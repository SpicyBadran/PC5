"""Read PC5's licence: the project's notice, the official text, and whether they agree.

Why this is a module rather than two parsers
--------------------------------------------
Three surfaces state PC5's licence — the README table, `docs/ABOUT.md` §1 and §6, and the
documentation gate that fails the build — and the defect this file exists to remove was
precisely those three disagreeing.  One file held the verbatim FSF text, a second held a
hand-written grant, and each document cited whichever it was written against, so the front
page could print an identifier the repository's canonical text did not.  A licence claim is
the one statement on an About page a distributor acts on, so there is now exactly one reader:
`tools/presentation.py` renders what it returns and `tools/docs_check.py` fails the build on
it.  Those two duplicate their *prose* rules deliberately (anchors, make targets) so a render
bug cannot certify itself; the licence is the opposite case — a disagreement between the
renderer and the gate *is* the failure mode, so they share the reader and the tests pin it.

What the reader refuses to do
-----------------------------
  * **Derive a grant from the official text.** The FSF's verbatim body contains "either version
    3 of this License, or (at your option) any later version", and its *How to Apply These Terms
    to Your New Programs* section hands that wording to every project as a template for applying
    the licence.  It is a sentence about how the licence is *used*, not a decision by PC5, so an
    id copied out of it would be a guess wearing somebody else's formatting — and it would be a
    wrong one, because the standard text never says "only" about a project's own release.  The
    grant is read from `LICENSING.md`, where this project speaks in its own person.
  * **Accept an edited official text.** A project may state its terms beside the GPL; it may
    not splice them into it.  `LICENSE` must therefore be byte-identical to the canonical text
    pinned below, and any edit — an inserted paragraph, one reworded clause, a stray space —
    is reported as a modification rather than tolerated because it looks harmless.
  * **Pick a side in a contradiction.** If the notice and the official text disagree about the
    version, or the notice contradicts itself, the answer is a `problem`, not the more
    convenient of the two readings.

The pinned digest and its provenance
------------------------------------
`GPL3_ONLY_SHA256` is the SHA-256 of the byte sequence this repository adopted as its canonical
`GPL-3.0-only` text: the file on `main` at `9e3fda61e420` (674 lines, 35,149 bytes), which is
the standard "Version 3, 29 June 2007" GNU GPL document.  It is pinned as *this repository's
adopted text*, which is the claim the check can support — not as an independently re-verified
copy of anything, since `spdx.org` and `gnu.org` are not reachable from this environment.
Re-adopting a different byte sequence is a deliberate act: change the constant, and every
document that prints the identifier moves with it, because each one is derived from this file.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Where PC5 states its own decision, and where the standard text lives.
NOTICE = "LICENSING.md"
OFFICIAL = "LICENSE"

#: The canonical `LICENSE` this repository adopted; see the module docstring.
GPL3_ONLY_SHA256 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"

#: A notice states a grant when a sentence names *this project* with a verb of licensing.
#: The sentence, not the file, is the unit: a notice that mentions PC5 in one paragraph and a
#: licence in another has described something without granting it.
GRANT = re.compile(
    r"\bPC5\b(?P<mid>[^.\n]{0,220}? )\b(?:licensed|distributed|released|offered)\b(?P<tail>[^.\n]*)",
    re.I)
NOT_LATER = re.compile(r"not\s+\"?\s*or any later version", re.I)
VERSION = re.compile(r"version\s+(\d+)", re.I)
ONLY = re.compile(r"version\s+(\d+)\s+only", re.I)
LATER = re.compile(r"or any later version", re.I)
#: Any SPDX-style identifier the notice asserts, including the bold one under its title.
DECLARED_ID = re.compile(
    r"(?<![A-Za-z0-9.+-])(GPL-(?:\d+\.\d+|1\.1)(?:-only|-or-later)?"
    r"|MIT|Apache-\d+(?:\.\d+)?|BSD-\d+(?:\.\d+)?(?:-[A-Za-z0-9.]+)*)")
#: The opening of the official document: its title line and the version it claims to be.
GPL_HEADER = re.compile(r"^\s*GNU GENERAL PUBLIC LICENSE\s*\n[^\n]*Version\s+(\d+)", re.I)
#: No vendoring is a build rule recorded in ADR-025; the notice restating it is a fact about
#: the notice, and the About row attributes it either way rather than implying the rule lives
#: inside the licence text.
NO_VENDORING = re.compile(r"vendors no third-party\s+source\s+archive", re.I)


def asserted_ids(notice: str) -> set:
    """Every SPDX-style identifier the notice asserts for itself, bare family names excluded.

    A bare `GPL-3.0` names the document a reader is about to open; it is not a second claim
    about the grant, and treating it as one would make an accurate sentence contradict itself.
    `tools/docs_check.py` applies the same leniency to the controlled documents, so "naming the
    version of the text" is not a failure anywhere in the tree, while "naming a different id" is
    always one.
    """
    out = set()
    for match in DECLARED_ID.finditer(notice):
        token = match.group(1).upper()
        if re.fullmatch(r"GPL-\d+(?:\.\d+)?", token):
            continue
        out.add(token)
    return out


def plain(text: str) -> str:
    """Markdown emphasis and links, reduced to the words a grant sentence is made of."""
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text).replace("*", "").replace("`", "")


def official_text_state(text: str) -> dict:
    """How `LICENSE` relates to the canonical text: verbatim, modified, or a different document.

    Three answers, in the order a reader would want them.  "verbatim" is the only pass.  A file
    that opens like the GPL but hashes differently is *modified*, and the wording says so —
    "the digest differs" names the symptom, not the act.  A file that is not the GPL at all is
    reported as the wrong document rather than as an alteration of the right one, because those
    call for different repairs: re-adopt the text, or fix a link.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    header = GPL_HEADER.search(text)
    if digest == GPL3_ONLY_SHA256:
        return {"status": "verbatim", "version": 3, "sha256": digest}
    if header is None:
        return {"status": "foreign", "version": None, "sha256": digest}
    return {"status": "modified", "version": int(header.group(1)), "sha256": digest}


def read(root: Path | None = None) -> dict:
    """What PC5's licence is, or why that cannot be answered yet.

    A refusal is a *value*, never an exception: `{"spdx": None, "problem": sentence, "file":
    which file is ambiguous}`.  Raising would make one unclear file crash every figure and
    every generated block — several false failures wrapped around one true statement, and the
    page would lose the chance to print that statement at all.  `tools/docs_check.py` is the
    consumer that turns a refusal into a build failure, once, against a file a reader can act
    on.
    """
    root = root or ROOT

    def refusing(problem: str, file: str, official=None) -> dict:
        """A refusal, phrased so the consumer can prefix it with the file it is about.

        The `problem` never repeats its own subject: `docs_check` prints `"<file>: <problem>"`
        and the About page prints "`<file>` <problem>", and one wording has to serve both.
        """
        return {"spdx": None, "major": None, "qualifier": "", "problem": problem,
                "file": file, "source": NOTICE, "grant": "",
                "no_vendored_third_party": False, "official": official}

    def short(digest: str) -> str:
        return digest[:12] + "…"

    notice_path, official_path = root / NOTICE, root / OFFICIAL
    if not notice_path.exists() and not official_path.exists():
        return refusing("neither the notice nor the official text is in the tree, so there is "
                        "nothing to read — and nothing to check either", "")
    try:
        notice = plain(notice_path.read_text(encoding="utf-8"))
    except OSError as exc:
        if not official_path.exists():
            return refusing(f"is missing, and so is `{OFFICIAL}`: the tree states no licence at "
                            f"all", NOTICE)
        return refusing(f"is missing while `{OFFICIAL}` holds the licence text: adopting a "
                        f"document is not the same as granting it, so no identifier can be "
                        f"stated (read it here, in this project's own person)", NOTICE)
    try:
        body = official_path.read_text(encoding="utf-8")
    except OSError as exc:
        return refusing(f"is unreadable ({exc}), so the grant in `{NOTICE}` cannot be verified "
                        f"against the canonical text", OFFICIAL)

    # The official document first: a notice can only be compared against a text that is itself
    # unambiguous, and an edited body would otherwise make a correct grant print a wrong id.
    official = official_text_state(body)
    if official["status"] == "foreign":
        return refusing(f"does not open with the GNU General Public License document, so it is "
                        f"not the canonical text this project adopted (sha256 "
                        f"{short(official['sha256'])}, expected {short(GPL3_ONLY_SHA256)})",
                        OFFICIAL, official)
    if official["status"] == "modified":
        if official["version"] == 3:
            return refusing(f"presents itself as the GNU GPL version 3 document but is not the "
                            f"canonical text: it has been edited (sha256 "
                            f"{short(official['sha256'])}, expected "
                            f"{short(GPL3_ONLY_SHA256)}). Project-specific wording belongs in "
                            f"`{NOTICE}`, never spliced into the licence body",
                            OFFICIAL, official)
        return refusing(f"is the GNU GPL version {official['version']} text while `{NOTICE}` "
                        f"grants version 3: the official document and this project's declaration "
                        f"contradict each other, and no page may pick a side",
                        OFFICIAL, official)

    grant = GRANT.search(notice)
    if not grant:
        return refusing("states no licence grant for PC5: the file describes a licence without "
                        "granting one, and an identifier cannot be derived from a description of "
                        "somebody else's wording", NOTICE, official)
    sentence = grant.group(0)
    clean = NOT_LATER.sub("", sentence)
    if re.search(r"\bMIT\b", clean):
        family = "MIT"
    elif "apache" in clean.lower():
        family = "Apache-2.0"
    elif "bsd" in clean.lower():
        family = "BSD"
    elif re.search(r"general public license|\bGPL\b", clean, re.I):
        family = "GPL"
    else:
        return refusing(f"names no licence family this tool can print ({sentence!r})", NOTICE,
                        official)
    if family != "GPL":
        return refusing(f"grants {family}, while the canonical text verified here is the GNU GPL "
                        f"version 3 document — the notice, `{OFFICIAL}` and `GPL3_ONLY_SHA256` "
                        f"have to be changed together", NOTICE, official)

    version = VERSION.search(clean)
    if not version:
        return refusing("names the GPL with no version number in its grant sentence; refusing to "
                        "print a guess", NOTICE, official)
    major = int(version.group(1))
    if LATER.search(clean):
        return refusing(f"grants GPL-{major}.0-or-later while the canonical text adopted here is "
                        f"`GPL-3.0-only`: a project may widen its own grant, and this tool will "
                        f"not do it on its behalf", NOTICE, official)
    if not ONLY.search(clean):
        return refusing(f"grants GPL version {major} without saying 'only' or 'any later "
                        f"version', so the identifier cannot be derived from it", NOTICE,
                        official)
    spdx = f"GPL-{major}.0-only"
    if major != 3:
        return refusing(f"grants version {major} while `{OFFICIAL}` holds the version 3 document: "
                        f"one of the two is stale", NOTICE, official)

    # Every identifier the notice asserts has to be the one its own grant implies, or the file
    # contradicts itself and no controlled document can be checked against either reading.
    for declared in sorted(asserted_ids(notice) - {spdx.upper()}):
            return refusing(f"asserts `{declared}` beside a grant sentence that implies "
                            f"`{spdx}`: the notice contradicts itself, and a controlled document "
                            f"cannot be checked against either reading", NOTICE, official)

    return {"spdx": spdx, "major": major, "qualifier": "only", "problem": "",
            "file": NOTICE, "source": NOTICE, "grant": sentence.strip(),
            "no_vendored_third_party": bool(NO_VENDORING.search(notice)),
            "official": official}


if __name__ == "__main__":  # pragma: no cover - a convenience for humans, not a gate
    result = read()
    if result["spdx"]:
        print(f"{result['spdx']} — grant read from {NOTICE}, {OFFICIAL} verified verbatim "
              f"(sha256 {GPL3_ONLY_SHA256[:12]}…)")
    else:
        print(f"NO IDENTIFIER — {result['file']}: {result['problem']}")
