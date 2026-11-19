"""Check the documentation tree against itself.

Three things go stale in a documentation-heavy repository, and all three are cheap to
detect mechanically:

  * a relative link or image path that no longer resolves;
  * a `#anchor` pointing at a heading that was renamed;
  * a documented command (`make something`) for a target that does not exist.

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

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", "__pycache__", "build", ".venv"}

LINK = re.compile(r"!?\[[^\]]*\]\(\s*([^)\s]+?)(?:\s+\"[^\"]*\")?\s*\)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# `make BUILD_ID=...` is a variable assignment on the command line, not a target
MAKE = re.compile(r"`make\s+([a-zA-Z][\w.-]*)(?![\w.=])")
CODE_FENCE = re.compile(r"^```")


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

    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check documentation links, anchors and commands.")
    parser.add_argument("--list", action="store_true", help="print each file checked")
    args = parser.parse_args(argv)

    files = markdown_files()
    problems = check()
    if args.list:
        for path in files:
            print(f"checked {path.relative_to(ROOT)}")
    for problem in problems:
        print(f"[FAIL] {problem}")
    if problems:
        print(f"{len(problems)} documentation problem(s) across {len(files)} files")
        return 1
    print(f"DOCS OK: {len(files)} Markdown files, "
          f"{len(make_targets())} make targets, links, anchors and commands resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
