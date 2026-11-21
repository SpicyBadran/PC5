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

  * **A token of only decimal digits is a citation only if it resolves.** Build
    timestamps (`2026111618480500`) and placeholder examples (`123456789`) have a
    SHA's shape and are not objects, so they are skipped -- but "all digits" is not
    by itself evidence of not being an object id. About 3.7% of 7-character object
    ids are all decimal digits, so skipping that shape unconditionally would have
    silently stopped checking a real citation roughly one time in 27. The digit
    rule is therefore an exemption that must be *earned*: a digit-only token is
    dropped only when Git cannot hand it back, and a digit-only token that does
    resolve is checked like any other.

    **Known limitation: a dead all-digit object id is not reported.**  The two
    cases are genuinely indistinguishable from the token alone -- a build timestamp
    and an abbreviated id that no longer resolves look identical -- so the exemption
    has to resolve one way and this is the direction chosen.  The consequence is
    that writing a *dead* all-digit abbreviation (``1234567``) in a document does
    not produce a failure, while writing a dead non-digit one (``deadbee``) does.
    Skipping is not evidence that a digit-only token is a timestamp; it means only
    that Git could not resolve it.  Catching this case would need a second signal
    -- for example a register of known timestamp shapes, or a rule that only
    ``docs/TEST_RESULTS.md`` may contain digit-only tokens -- and neither is
    attempted here, because either would narrow what a document may legitimately
    cite.  The gap is bounded: it makes the guard less sensitive, never less
    correct about the citations it does check.
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

What a fresh clone can and cannot resolve
-----------------------------------------
Several documents cite the *head of a pull request* rather than a commit on `main` --
that is the honest way to record "the revision this review was written against", and
the head stays genuine history even when the branch is later rebased. But GitHub
publishes pull-request heads only under `refs/pull/N/head`, and a default clone (and
`actions/checkout`, even with `fetch-depth: 0`) does not fetch them. So in a fresh
clone those citations are **unresolvable without being wrong**, and the gate says so
while still failing, because a pass would be a claim it has not earned:

    make citation-refs     # fetch +refs/pull/*/head:refs/remotes/origin/pr/* from a remote
    make citations-check   # now every citation can actually be resolved

`citation-refs` is a provisioning step, not a gate: it changes what the next check is
able to see. The distinction matters because a repository cloned from a local path, or
checked out from a bundle, has no pull refs to fetch at all -- and that must read as
"this clone cannot verify those citations", never as "the citations are dead" and
never as "they are fine".

Exit status
-----------
    0  every citation resolved
    1  at least one citation names no object
    2  the check could not be performed -- not a Git repository, no evidence
       document matched, or no citation was found in any of them

Status 2 exists because "I checked and it passed" and "I could not check" must not
print the same thing.  Status 0 means every citation *that was examined* resolved;
it does not mean every digit-only token was examined, for the reason above.  A gate
whose document globbing has quietly stopped matching anything would otherwise report
success forever, and the protection it was written to provide would be absent exactly
when it was needed. Neither condition is a documentation defect, so neither is
reported as one; both simply fail.

    python3 tools/commit_citations.py            # exit 1 dead, 2 uncheckable
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: A backticked token wide enough to be an abbreviated or full SHA-1.
CITATION = re.compile(r"`([0-9a-f]{7,40})`")


def evidence_documents(root: Path | None = None) -> tuple[Path, ...]:
    """The documents that cite Git objects as evidence.

    The root-level dated evidence records, plus the architecture document, which
    records which commit corrected each design claim. `docs/TEST_RESULTS.md` is
    deliberately *not* here: it is a historical log of test runs performed against
    revisions that later history rewrote out of existence, and rewriting a log so a
    checker agrees with it would destroy the record rather than repair it.
    """
    root = ROOT if root is None else root
    documents = sorted(
        path for path in root.glob("*_2026-11-16.md") if path.is_file()
    )
    architecture = root / "docs" / "ARCHITECTURE.md"
    if architecture.is_file():
        documents.append(architecture)
    return tuple(documents)


def citations(text: str, *, resolves=None):
    """Yield ``(line_number, reference, line)`` for each citation in ``text``.

    ``resolves``, when given, is called with a reference and answers whether it
    names an object. It is what makes the digit-only exemption honest: a token of
    only decimal digits is dropped only if it does *not* resolve, because a real
    object id can be all digits. Without the callback the exemption cannot be
    applied safely, so digit-only tokens are yielded and the caller checks them --
    the cost of a spurious ``cat-file`` is one subprocess call.

    With ``resolves`` supplied, a digit-only token that does *not* resolve is
    therefore skipped, which also skips a dead all-digit object id.  That is the
    documented limitation described in the module docstring: the two are
    indistinguishable from the token, and this function resolves the ambiguity in
    favour of "timestamp".
    """
    for number, line in enumerate(text.splitlines(), start=1):
        for match in CITATION.finditer(line):
            reference = match.group(1)
            if reference.isdigit() and resolves is not None and not resolves(reference):
                # Could be a build timestamp or a placeholder, or a dead all-digit
                # abbreviated object id: the token alone cannot tell these apart.
                # The policy skips unresolved digit-only tokens so that timestamps
                # do not become false positives, accepting that a dead all-digit id
                # goes unreported. See the module docstring.
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


def pr_ref_count(root: Path | None = None) -> int:
    """How many pull-request head refs this clone can resolve through.

    Zero does not mean the repository has no pull requests and it does not mean the
    citations are dead: it means this clone never fetched `refs/pull/*`, so no
    PR-head object can be resolved here. The count is what lets the report name the
    difference between a stale citation and a clone that cannot check it, instead of
    collapsing both into "does not name any Git object".
    """
    root = ROOT if root is None else root
    result = subprocess.run(
        ["git", "-C", str(root), "for-each-ref", "--format=%(refname)",
         "refs/remotes/origin/pr/", "refs/pull/"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return 0
    return len([line for line in result.stdout.splitlines() if line.strip()])


def history_truncated(root: Path | None = None) -> str | None:
    """Why this clone may hold too few objects to check anything, if it does.

    A shallow or filtered clone cannot resolve a citation to a commit that its
    fetch depth excluded, and the failure looks *identical* to a dead citation:
    `git cat-file` answers "not found" either way. Naming the condition is the
    difference between telling a reader to repair the documents and telling them
    to repair the checkout, so the report has to know which one it is looking at.
    Returns a short reason string, or None when the history is complete.
    """
    root = ROOT if root is None else root
    if not is_repository(root):
        return None
    shallow = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
        capture_output=True, text=True,
    )
    if shallow.returncode == 0 and shallow.stdout.strip() == "true":
        return "shallow clone (.git/shallow present; history depth limited by `--depth`)"
    partial = subprocess.run(
        ["git", "-C", str(root), "config", "--get", "extensions.partialclone"],
        capture_output=True, text=True,
    )
    if partial.returncode == 0 and partial.stdout.strip():
        return f"partial clone (objects fetched on demand, promisor {partial.stdout.strip()!r})"
    return None


def problems(root: Path | None = None) -> list[str]:
    """Every citation that does not resolve, as an actionable line per problem."""
    root = ROOT if root is None else root
    if not is_repository(root):
        return []
    resolved: dict[str, str | None] = {}

    def resolves(reference: str) -> bool:
        if reference not in resolved:
            resolved[reference] = object_type(root, reference)
        return resolved[reference] is not None

    found = []
    for document in evidence_documents(root):
        text = document.read_text(encoding="utf-8", errors="ignore")
        for number, reference, line in citations(text, resolves=resolves):
            if resolves(reference):
                continue
            found.append(
                f"{document.relative_to(root)}:{number}: `{reference}` does not name any "
                f"Git object (commit citation is dead): {line}"
            )
    return found


#: Exit status meaning "the check could not be performed", as distinct from
#: "it ran and found dead citations".
EXIT_UNCHECKED = 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that commit references in the evidence documents resolve."
    )
    parser.add_argument("--list", action="store_true", help="print each document checked")
    arguments = parser.parse_args(argv)

    documents = evidence_documents()

    # "I checked and everything resolved" and "I could not check" must never print
    # the same thing. A gate that says OK having examined nothing is worse than no
    # gate: it converts an absent check into a positive claim. Both conditions below
    # are therefore failures of the check itself, not documentation defects, and both
    # exit non-zero.
    if not is_repository(ROOT):
        print(f"CITATIONS NOT CHECKED: {ROOT} is not a Git repository, so no commit "
              f"reference can be resolved. This is not a pass.")
        return EXIT_UNCHECKED
    if not documents:
        print(f"CITATIONS NOT CHECKED: no evidence documents matched in {ROOT}, so "
              f"nothing was examined. This is not a pass.")
        return EXIT_UNCHECKED

    found = problems()
    if arguments.list:
        for document in documents:
            print(f"checked {document.relative_to(ROOT)}")
    for problem in found:
        print(f"[FAIL] {problem}")
    if found:
        print(f"{len(found)} dead commit reference(s) across {len(documents)} documents")
        refs = pr_ref_count()
        if refs == 0:
            # Say what the clone could not see, without turning the failure into a
            # pass: the citations remain unresolved here until the refs are fetched,
            # and a reader who fetches and re-runs gets a real verdict either way.
            print("CITATIONS INCOMPLETE: this clone cannot resolve the references above. "
                  "That is not a pass, and it is not proof the citations are dead.")
            truncated = history_truncated(ROOT)
            if truncated:
                print(f"    history truncated: {truncated}. A commit excluded by the "
                      f"fetch depth is indistinguishable from a missing one here.")
                print("    repair the checkout, not the documents: `git fetch --unshallow` "
                      "or re-clone without --depth/--filter (CI: fetch-depth: 0), then "
                      "re-run `make citations-check`.")
            print("    this clone also holds no pull-request refs, so a citation to a PR "
                  "head (refs/pull/N/head on the remote) cannot resolve here even when the "
                  "object exists upstream: run `make citation-refs`, then re-run "
                  "`make citations-check`.")
            print("    If the count is still non-zero afterwards, the citations are dead "
                  "for real (a rewrite or a deleted branch), and the documents need an "
                  "addendum rather than an edit.")
        else:
            print(f"CITATION REFS PRESENT: {refs} pull-request ref(s) are fetched, so "
                  f"every unresolved reference above is genuinely unresolvable in this "
                  f"repository, not merely unfetched.")
        return 1

    checked = sum(
        len(list(citations(
            document.read_text(encoding="utf-8", errors="ignore"),
            resolves=lambda reference: object_type(ROOT, reference) is not None,
        )))
        for document in documents
    )
    if checked == 0:
        print(f"CITATIONS NOT CHECKED: {len(documents)} document(s) contained no "
              f"citations, so nothing was examined. This is not a pass.")
        return EXIT_UNCHECKED

    print(f"CITATIONS OK: {checked} commit reference(s) resolve across "
          f"{len(documents)} documents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
