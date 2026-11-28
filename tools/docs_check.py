"""Check the documentation tree against itself.

Three things go stale in a documentation-heavy repository, and all three are cheap to
detect mechanically:

  * a relative link or image path that no longer resolves;
  * a `#anchor` pointing at a heading that was renamed;
  * a documented command (`make something`) for a target that does not exist;
  * and, for `docs/ABOUT.md`, the four things that make it safe to trust: the sections it
    promises, the generated blocks the numbers live in, the repository paths it cites, and
    the licence grant in `LICENSING.md` and the official text in `LICENSE` (`tools/roadmap.py`, `tools/completion.py`
    and `tools/presentation.py` own the milestone states, the progress figure and the
    generated blocks themselves — this tool does not recompute any of them, it checks the
    prose around them, which is the part no generator writes).

`tests/test_architecture.py` already checks the README's links. This tool checks every
Markdown file in the tree, so a document that links another document cannot rot just
because it is not the front page.

    python3 tools/docs_check.py            # exit 1 on any problem
    python3 tools/docs_check.py --list     # also print what was checked

Anchors are matched with GitHub's slug rule (lowercase, punctuation dropped, spaces to
hyphens, duplicates suffixed `-1`, `-2` …). That rule is an approximation of GitHub's
renderer, not a copy of it: it is deliberately strict about renamed headings and
deliberately tolerant about anything it cannot resolve, so it never blocks a document
for a reason the author cannot fix.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import licensing  # noqa: E402 -- same-directory sibling; one reader for one fact

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", "__pycache__", "build", ".venv"}

LINK = re.compile(r"!?\[[^\]]*\]\(\s*([^)\s]+?)(?:\s+\"[^\"]*\")?\s*\)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# `make BUILD_ID=...` is a variable assignment on the command line, not a target
MAKE = re.compile(r"`make\s+([a-zA-Z][\w.-]*)(?![\w.=])")
CODE_FENCE = re.compile(r"^```")


# ------------------------------------------------------------------ the About contract
#: Headings `docs/ABOUT.md` has to carry, in this order.  A page that quietly loses its
#: "Licence and attribution" section is worse than one that never had it, because a reader
#: cannot tell which happened — so the requirement is a gate, not a convention.
ABOUT_SECTIONS = (
    "1. Project identity",
    "2. Technical overview",
    "3. What actually works",
    "4. Engineering standards",
    "5. Roadmap and progress",
    "6. Licence and attribution",
    "7. Getting involved",
    "8. Sources this page was written from",
)

#: Generated blocks the About page must contain.  The percentage and the capability counts
#: live only inside these, which is what keeps the page consistent with the README.
ABOUT_BLOCKS = ("about-identity", "about-capabilities", "about-progress")

#: Documents whose licence statement has to agree with the notice's grant.  Dated records under
#: `docs/` are deliberately excluded: they quote a past state, and rewriting history to
#: satisfy a checker is the worse error.  `AGENTS.md` is included because it instructs every
#: future contributor, and a wrong duty stated there outlives any single pull request.
LICENCE_DOCUMENTS = ("README.md", "docs/ABOUT.md", "CONTRIBUTING.md", "docs/BRAND.md",
                     "AGENTS.md", "docs/SECURITY.md")

#: A licence stated **in words** rather than as an identifier: the form a README footer uses.
#: The SPDX pattern below cannot see this, and `2e5d39f`'s fix existed precisely because a
#: footer that said "version 2 only" while `LICENSING.md` granted `GPL-3.0-only` reached
#: `main` past an id-only check (debt D-4).  A mention of a *quotation* is not an assertion,
#: so quoted and inline-code spans are stripped before this is applied -- see
#: `strip_quotations`.
LICENCE_PROSE = re.compile(
    r"(?:GNU )?General Public License[^.\n]{0,24}?version\s+(\d+)|\bGPL\s+v(?:ersion)?\s*(\d+)",
    re.I)

#: Documents that speak *about* the tree rather than record what happened to it.  A cited
#: path in one of these is a claim about where something lives.
CITED_PATH_DOCUMENTS = (
    "README.md",
    "CONTRIBUTING.md",
    "docs/ABOUT.md",
    "docs/ARCHITECTURE.md",
    "docs/BUILDING.md",
    "docs/INSTALLATION.md",
    "docs/ROADMAP.md",
    "docs/TESTING_STRATEGY.md",
    "docs/PRESENTATION.md",
)

#: Generated artefacts and external source trees a document may name without holding a
#: copy: a citation of `build/pc5.iso` is a claim about a build, not about this checkout.
PATH_ALLOW_PREFIXES = ("build/", "MdeModulePkg/", "MdePkg/", "OvmfPkg/", "UefiCpuPkg/",
                       "SecurityPkg/", "NetworkPkg/", "StdLib/", "CryptoPkg/", "edk2/")

#: An SPDX-style licence identifier, as a document might assert it.
LICENCE_ID = re.compile(r"\bGPL-\d+(?:\.\d+)?(?:-(?:only|or-later))?\b"
                        r"|\bMIT\b|\bApache-\d(?:\.\d)?\b")

#: A repository path written as inline code: a separator plus a suffix, which is what
#: distinguishes a location claim from a bare file name.
CITED_PATH = re.compile(r"(?<![`\w/.])`((?:[\w.-]+/)+[\w.-]+\.(?:c|h|py|sh|json|md|ld|S|txt|"
                        r"yml|iso))`")

#: Phrases that assert a milestone's standing, with the states each one permits.  This is
#: not vocabulary policing: "verified" has a definition here (`complete`, criteria met,
#: evidence filed), and an About page that uses it loosely is exactly the overstated
#: progress the rest of the design exists to prevent.
STATUS_CLAIMS = (
    (re.compile(r"\b(?:verified|verifies|implemented|complete|completed)\b", re.I),
     ("complete",)),
    (re.compile(r"\b(?:in progress|in-progress|experimental|under way|open work)\b", re.I),
     ("in_progress", "blocked")),
    (re.compile(r"\b(?:not started|planned|unimplemented|not yet built)\b", re.I),
     ("not_started", "blocked")),
    (re.compile(r"\bblocked\b", re.I), ("blocked",)),
)

MILESTONE_ID = re.compile(r"`(M\d+[a-z]?)`")

#: Where a clause ends, for the status-claim rule: punctuation and the conjunctions that
#: join two claims into one sentence.  A predicate shared across a conjunction ("`M1` and
#: `M2` are verified") is therefore read per side and not flagged — a deliberate false
#: negative.  Guessing which noun a shared predicate belongs to is how a checker ends up
#: wrong in the direction that publishes a claim nobody verified, and this rule exists to
#: stop that, not to parse English.
CLAUSE = re.compile(r"[,;:.()\[\]]|\band\b|\bbut\b|\bwhile\b|\bthough\b", re.I)


def strip_generated_blocks(text: str) -> str:
    """A page's prose, with every `pc5:*` generated region removed.

    A generated block is true by construction — it is the model's own output, checked by
    `tools/presentation.py` — so the prose rules below must not read it.  Checking
    generated text against the data it came from is that tool's job; duplicating it here
    would be two validators for one fact.
    """
    return re.sub(r"<!-- pc5:[\w-]+:begin -->[\s\S]*?<!-- pc5:[\w-]+:end -->", "\n", text)


#: The licence rules live in `tools/licensing.py`, which the About renderer also calls, so
#: the gate and the page cannot disagree about what the repository grants — that
#: disagreement was the defect, and two parsers over two files is how it was produced.
#: What stays here is only the reporting: a documentation check lists every problem it
#: found instead of refusing, and it attributes a refusal to the file a reader can act on.
#: Two absences are tolerated as one: a tree with neither `LICENSE` nor `LICENSING.md` has
#: no licence claim to check, and the link rule already reports the missing text; either
#: half present without the other is a refusal, because then the tree does make a statement,
#: and it is an unverifiable one.


def strip_quotations(text: str) -> str:
    """Remove inline-code spans, quoted passages and blockquotes.

    A gate over licence *statements* has to distinguish asserting a thing from recording that
    somebody once asserted it. `docs/CHANGELOG.md`-style honesty ("the footer said \"version 2
    only\"; corrected here") must stay writable, so quoted material and code spans are dropped
    rather than the whole check being loosened.
    """
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            continue
        line = re.sub(r"`[^`]*`", " ", line)
        line = re.sub(r'\"[^\"]{0,300}?\"', " ", line)
        line = re.sub(r"“[^”]{0,300}?”", " ", line)
        kept.append(line)
    return "\n".join(kept)


def licence_grant(root: Path = ROOT) -> tuple:
    """`(spdx_id, note, file)` for what this repository's licence is.

    Either half of the first pair can be empty, and `file` names the ambiguous one when it
    is not: `LICENSING.md` when the project has not said what it grants, `LICENSE` when the
    official text has been edited, replaced, or does not match the notice.
    """
    result = licensing.read(root)
    if result["spdx"]:
        return result["spdx"], "", ""
    return None, result["problem"], result["file"]


def licence_from_file(root: Path = ROOT):
    """The SPDX id PC5 grants, or ``None`` when the tree does not state one verifiably."""
    return licence_grant(root)[0]


def roadmap_states(root: Path = ROOT) -> dict:
    """`{milestone id: state}` straight from the graph, or an empty map if it is unreadable.

    Read, never recomputed: `tools/roadmap.py` owns validity and `tools/completion.py`
    owns the arithmetic.  This function exists so a sentence cannot disagree with a field.
    """
    import json
    try:
        data = json.loads((root / "docs/architecture/roadmap.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(m.get("id")): str(m.get("state")) for m in data.get("milestones", [])}


def heading_titles(path: Path) -> list:
    """Headings in document order, fenced code excluded — the order is part of the contract."""
    titles, fenced = [], False
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if CODE_FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING.match(line)
        if match:
            titles.append(match.group(2).strip())
    return titles


def about_problems(about: Path, root: Path = ROOT) -> list:
    """Everything `docs/ABOUT.md` owes a first-time reader, checked in one pass.

    An absent page is not this function's problem: a document that links `docs/ABOUT.md`
    when it does not exist is already reported as a broken link, and inventing a second
    failure for the same fact would make every fixture tree without an About page fail for
    a reason its author cannot act on.  Whether *this* repository must have the page is a
    decision, so it is asserted in `tests/test_docs_check.py::RepositoryTest` rather than
    smuggled into a link checker.
    """
    if not about.exists():
        return []
    text = about.read_text(encoding="utf-8", errors="ignore")
    rel = about.relative_to(root) if about.is_relative_to(root) else about
    problems = []

    titles = heading_titles(about)
    positions = []
    for want in ABOUT_SECTIONS:
        found = [index for index, title in enumerate(titles) if title == want]
        if not found:
            problems.append(f"{rel}: required section is missing: {want!r}")
            continue
        positions.append(found[0])
    if len(positions) == len(ABOUT_SECTIONS) and positions != sorted(positions):
        problems.append(f"{rel}: the required sections are present but out of order")

    for name in ABOUT_BLOCKS:
        if f"<!-- pc5:{name}:begin -->" not in text:
            problems.append(f"{rel}: no `pc5:{name}` generated block — the page would be "
                            f"quoting the README instead of the model")

    prose = strip_generated_blocks(text)
    for pattern in (r"\b\d+\s+milestones\b", r"\b\d+\s+workstreams\b",
                    r"\b\d+(?:\.\d+)?\s*%(?!\w)"):
        for match in re.finditer(pattern, prose, re.I):
            problems.append(f"{rel}: {match.group(0)!r} is typed into the prose; generate it "
                            f"from the model or drop it")

    states = roadmap_states(root)
    for document in CITED_PATH_DOCUMENTS:
        path = root / document
        if not path.exists():
            continue
        body = re.sub(r"```[\s\S]*?```", "", strip_generated_blocks(
            path.read_text(encoding="utf-8", errors="ignore")))
        for match in CITED_PATH.finditer(body):
            cited = match.group(1)
            if cited.startswith(("http", "//", "~")) or cited.startswith(PATH_ALLOW_PREFIXES):
                continue
            # A path can be cited as history — "the workflow used to live at
            # `ci/build.yml`" is a true sentence about a file that no longer exists, and a
            # checker that forced it to be deleted would be forcing the record to be
            # rewritten.  Saying so is the exemption: a line that names itself historical is
            # believed, visibly, by whoever reads it.
            line = body[:match.start()].rsplit("\n", 1)[-1]
            if "historical" in line.lower():
                continue
            target = urllib.parse.unquote(cited.split("#", 1)[0])
            if (root / target).exists() or (path.parent / target).exists():
                continue
            problems.append(f"{document}: cited path does not exist: `{cited}`")

    granted, note, culprit = licence_grant(root)
    if note:
        # reported against one file, once: the ambiguity belongs to a specific document, and
        # pointing at four would send a reader to fix the wrong thing
        problems.append(f"{culprit}: {note}")
    if granted:
        granted_major = int(re.search(r"(\d+)", granted).group(1)) if granted else None
        for document in LICENCE_DOCUMENTS:
            path = root / document
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if granted_major is not None:
                for spoken in strip_quotations(strip_generated_blocks(text)).splitlines():
                    wording = LICENCE_PROSE.search(spoken)
                    if wording is None:
                        continue
                    stated = int(wording.group(1) or wording.group(2))
                    if stated != granted_major:
                        problems.append(
                            f"{document}: states the licence in words as version {stated}, but "
                            f"`LICENSING.md` grants {granted!r}; fix the sentence, not the grant "
                            f"(a quote or a code span is not an assertion and is ignored here)")
            for match in LICENCE_ID.finditer(text):
                stated = match.group(0)
                if stated == granted or re.fullmatch(r"GPL-\d+(?:\.\d+)?", stated):
                    # a bare family name is not a contradiction; nor is naming the version
                    # of the licence text a reader is about to open
                    continue
                problems.append(f"{document}: states {stated!r}, but `LICENSING.md` grants "
                                f"{granted!r}")

    if states:
        for line in prose.splitlines():
            # A claim binds to the milestone in its own clause, not to every id on the
            # line: "`M1` is verified, `M2` is experimental" is two sentences in one line
            # and is exactly the honest way to write a comparison.  Checking per clause is
            # what keeps this rule from forcing a page to avoid mentioning two milestones
            # in one breath — a constraint that would only make the prose worse.
            for clause in CLAUSE.split(line):
                ids = MILESTONE_ID.findall(clause)
                if not ids:
                    continue
                for pattern, allowed in STATUS_CLAIMS:
                    if not pattern.search(clause):
                        continue
                    for milestone in ids:
                        state = states.get(milestone)
                        if state and state not in allowed:
                            problems.append(
                                f"{rel}: `{milestone}` is described in a way the roadmap does "
                                f"not support (this clause asserts {', '.join(allowed)}; the "
                                f"recorded state is {state!r})")
    return problems


def slug(heading: str) -> str:
    """GitHub's heading anchor, close enough to be useful and strict enough to catch renames."""
    text = heading.strip().lower()
    text = re.sub(r"`|\*|_|~", "", text)
    text = re.sub(r"[^\w\s-]", "", text)
    # GitHub replaces each space with one hyphen, so a removed em dash leaves two:
    # collapsing runs here would silently accept a link GitHub cannot resolve
    return text.replace(" ", "-")


def markdown_files(root: Path = ROOT) -> list:
    found = []
    for path in sorted(root.rglob("*.md")):
        if SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        found.append(path)
    return found


def headings_of(path: Path) -> set:
    seen, anchors, fenced = set(), set(), False
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if CODE_FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING.match(line)
        if not match:
            continue
        base = slug(match.group(2))
        candidate, index = base, 1
        while candidate in seen:
            candidate = f"{base}-{index}"
            index += 1
        seen.add(candidate)
        anchors.add(candidate)
    return anchors


def make_targets(root: Path = ROOT) -> set:
    makefile = root / "Makefile"
    if not makefile.exists():
        return set()
    text = makefile.read_text(encoding="utf-8")
    targets = set(re.findall(r"^([A-Za-z][\w.-]*):(?!=)", text, re.M))
    targets.discard(".PHONY")
    return targets


def _resolve(parent: Path, path_part: str) -> Path | None:
    """``(parent / path_part)`` resolved, or ``None`` if resolving raised.

    ``Path.resolve()`` raises ``ValueError("embedded null byte")`` for a path
    containing NUL, which ``urllib.parse.unquote("a%00b.md")`` produces.  A
    checker whose job is to report malformed links must not die on one, so that
    narrow failure is turned into ``None`` and reported as a missing target.  A
    path that simply does not exist still resolves normally and is returned; the
    caller's ``exists()`` handles it.  Only these two exceptions are caught;
    anything else is a bug here and should still propagate.
    """
    try:
        return (parent / path_part).resolve()
    except (ValueError, OSError):
        return None


def check(root: Path = ROOT) -> list:
    problems = []
    files = markdown_files(root)
    targets = make_targets(root)
    heading_cache = {}

    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(root)
        for match in LINK.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:", "//", "#")):
                if not target.startswith("#"):
                    continue
                destination, fragment = path, target[1:]
            else:
                path_part, _, fragment = target.partition("#")
                # GitHub renders `[x](a%20b.md)` as the file `a b.md`, so a path
                # containing a space has to be percent-encoded to resolve there.
                # Decode before touching the filesystem, or the checker rejects a
                # link that GitHub follows.
                path_part = urllib.parse.unquote(path_part)
                destination = _resolve(path.parent, path_part)
                if destination is None:
                    # A decoded path the filesystem cannot even represent (an
                    # embedded NUL), or one naming no file.  Either way the link
                    # does not resolve, and that is a problem to report rather
                    # than an exception that stops the whole check.
                    problems.append(f"{rel}: link target missing: {target}")
                    continue
                if not path_part:
                    destination = path
                elif not destination.exists():
                    problems.append(f"{rel}: link target missing: {target}")
                    continue
            if not fragment or destination.suffix != ".md":
                continue
            if destination not in heading_cache:
                heading_cache[destination] = headings_of(destination)
            anchors = heading_cache[destination]
            if fragment.lower() not in anchors:
                problems.append(f"{rel}: anchor not found in "
                                f"{destination.relative_to(root) if destination.is_relative_to(root) else destination}: "
                                f"#{fragment}")

        for match in MAKE.finditer(text):
            name = match.group(1)
            if name not in targets:
                problems.append(f"{rel}: documented command `make {name}` has no target")

    problems += about_problems(root / "docs/ABOUT.md", root)
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check documentation links, anchors, commands and the About contract.")
    parser.add_argument("--list", action="store_true", help="print each file checked")
    parser.add_argument("--root", default=None,
                        help="check another tree (what the tests do; nothing here needs git)")
    parser.add_argument("--about-only", action="store_true",
                        help="run only the docs/ABOUT.md contract")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else ROOT
    files = markdown_files(root)
    if args.about_only:
        about = root / "docs/ABOUT.md"
        problems = ([f"{about.relative_to(root)}: missing"] if not about.exists()
                    else about_problems(about, root))
    else:
        problems = check(root)
    if args.list:
        for path in files:
            print(f"checked {path.relative_to(ROOT)}")
    for problem in problems:
        print(f"[FAIL] {problem}")
    if problems:
        print(f"{len(problems)} documentation problem(s) across {len(files)} files")
        return 1
    print(f"DOCS OK: {len(files)} Markdown files, "
          f"{len(make_targets())} make targets, links, anchors, commands, cited paths and "
          f"the About contract resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
