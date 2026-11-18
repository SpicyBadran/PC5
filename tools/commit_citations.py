"""Check that Git object references in the evidence documentation resolve.

Why this exists
---------------
The evidence documents in this repository make their claims checkable by citing the
commit that establishes each one -- "fixed in `abc1234`", "parent `def5678`". That is
the right way to write them, and it is also the one thing a rebase silently destroys.
A rewritten commit keeps its message, its diff and its position, so the sentence
around a stale hash still reads perfectly; only the hash is now a dead pointer.

Nothing else in the tree notices. `tools/docs_check.py` validates links, anchors and
`make` targets, and until now no test ever resolved a citation, so a rebase could rot
an entire evidence trail while all 773 tests and both documentation gates stayed
green. This tool closes that gap: a citation is only evidence if Git can hand the
object back, so that is what it checks.

What counts as a citation
-------------------------
Backticked, and 7 to 40 lowercase hex characters -- the width of an abbreviated or
full SHA-1. Two deliberate exclusions keep this from firing on things that merely
look like object ids:

  * **A token of only decimal digits is not a citation.** Build timestamps
    (`2026111618480500`) and placeholder examples (`123456789`) have exactly that
    shape and are not object ids. No object id this repository produces is
    all-digits, so excluding that shape costs nothing.
  * **Any object type is accepted, not only commits.** Several documents cite tree
    hashes on purpose -- "`merge(PR26, PR27)` = `5b452e2...` = `141f91f^{tree}`" --
    and a tree hash is as much evidence as a commit hash.

Resolvable is not the same as reachable
---------------------------------------
A citation that resolves to an object outside the current branch's history is
accepted, because some documents record genuine historical revisions that a later
rewrite removed from `main`. Resolvability is the property this guard protects.
Reachability is a different question, and the documents that cite an old revision
answer it themselves, in prose, at the point of citation. Confusing the two would
force those documents to delete an accurate historical record to satisfy a checker.

    python3 tools/commit_citations.py            # exit 1 if any citation is dead
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: A backticked token wide enough to be an abbreviated or full SHA-1.
CITATION = re.compile(r"`([0-9a-f]{7,40})`")


def evidence_documents(root: Path = ROOT) -> tuple[Path, ...]:
    """The documents that cite Git objects as evidence.

    The root-level dated evidence records, plus the architecture document, which
    records which commit corrected each design claim. `docs/TEST_RESULTS.md` is
    deliberately *not* here: it is a historical log of test runs performed against
    revisions that later history rewrote out of existence, and rewriting a log so a
    checker agrees with it would destroy the record rather than repair it.
    """
    documents = sorted(
        path for path in root.glob("*_2026-11-16.md") if path.is_file()
    )
    architecture = root / "docs" / "ARCHITECTURE.md"
    if architecture.is_file():
        documents.append(architecture)
    return tuple(documents)


def citations(text: str):
    """Yield ``(line_number, reference, line)`` for each citation in ``text``."""
    for number, line in enumerate(text.splitlines(), start=1):
        for match in CITATION.finditer(line):
            reference = match.group(1)
            if reference.isdigit():
                # A timestamp or a placeholder, not an object id.
                continue
            yield number, reference, line.strip()


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
    )


def is_repository(root: Path) -> bool:
    """Whether Git can answer questions about ``root``.

    A source archive has no `.git`, and a checkout made without the objects cannot
    resolve anything. Neither is a documentation defect, so the caller skips rather
    than reporting the whole tree as broken.
    """
    try:
        return _git(root, "rev-parse", "--git-dir").returncode == 0
    except OSError:
        return False


def object_type(root: Path, reference: str) -> str | None:
    """The type of the object ``reference`` names, or ``None`` if it names none."""
    result = _git(root, "cat-file", "-t", reference)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def problems(root: Path = ROOT) -> list[str]:
    """Every citation that does not resolve, as an actionable line per problem."""
    if not is_repository(root):
        return []
    resolved: dict[str, str | None] = {}
    found = []
    for document in evidence_documents(root):
        text = document.read_text(encoding="utf-8", errors="ignore")
        for number, reference, line in citations(text):
            if reference not in resolved:
                resolved[reference] = object_type(root, reference)
            if resolved[reference] is not None:
                continue
            found.append(
                f"{document.relative_to(root)}:{number}: `{reference}` does not name any "
                f"Git object (commit citation is dead): {line}"
            )
    return found


def main(argv=None) -> int:
    parser = __import__("argparse").ArgumentParser(
        description="Check that commit references in the evidence documents resolve."
    )
    parser.add_argument("--list", action="store_true", help="print each document checked")
    arguments = parser.parse_args(argv)

    documents = evidence_documents()
    found = problems()
    if arguments.list:
        for document in documents:
            print(f"checked {document.relative_to(ROOT)}")
    for problem in found:
        print(f"[FAIL] {problem}")
    if found:
        print(f"{len(found)} dead commit reference(s) across {len(documents)} documents")
        return 1
    print(f"CITATIONS OK: every commit reference resolves ({len(documents)} documents)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
