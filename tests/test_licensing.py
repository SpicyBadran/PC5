"""PC5's licence has two files and one reader, and each of them has a way to be wrong.

The rule these tests hold is narrow on purpose: `LICENSING.md` states what this project
grants, `LICENSE` holds the official text verbatim, and `tools/licensing.py::read` is the only
thing allowed to combine them.  Every case below is a way that combination could quietly become
a lie — a declaration nobody made, an edit to the standard text, a contradiction between the two
files, a generated page left behind by a licence change — and each one has to end in a *named
refusal*, never in the more convenient of the two readings.

Fixtures that need a file the checker will accept as verbatim copy the repository's own
`LICENSE`.  That is not laziness: the check is a digest comparison, so a hand-written
"GNU GENERAL PUBLIC LICENSE / Version 3" stub would pass nothing and prove nothing — it would
test whether the parser is tolerant of near-misses, which is precisely the property under attack.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import docs_check  # noqa: E402
import licensing  # noqa: E402
import presentation  # noqa: E402

NOTICE = licensing.NOTICE
OFFICIAL = licensing.OFFICIAL


def tree(**files) -> Path:
    """A minimal repository: a `Makefile`, the licence pair, and whatever else is named.

    Only `docs_check`'s licence rules need this much.  A `Makefile` and one `docs/` page keep
    the other rules quiet, so a test that expects one problem gets exactly the one it caused.
    """
    root = Path(tempfile.mkdtemp())
    (root / "Makefile").write_text("check:\n\t@true\n")
    (root / "docs").mkdir()
    (root / "docs" / "target.md").write_text("# Real heading\n\nText.\n")
    grant = files.pop("notice", None)
    body = files.pop("licence", "verbatim")
    if grant is not None:
        (root / NOTICE).write_text(grant, encoding="utf-8")
    if body == "verbatim":
        shutil.copyfile(REPO / OFFICIAL, root / OFFICIAL)
    elif body is not None:
        (root / OFFICIAL).write_text(body, encoding="utf-8")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def canonical() -> str:
    """The repository's own adopted text, which is the only thing that hashes to the pin."""
    return (REPO / OFFICIAL).read_text(encoding="utf-8")


GOOD_GRANT = ("# PC5's licence\n\n"
              "PC5 is licensed under the GNU General Public License version 3 only "
              "(`GPL-3.0-only`).\n"
              "PC5 vendors no third-party source archive.\n")


class ReaderTest(unittest.TestCase):
    def tearDown(self):
        if getattr(self, "root", None):
            shutil.rmtree(self.root, ignore_errors=True)

    def test_the_repository_itself_resolves_to_one_identifier(self):
        """The real tree answers, and answers the same way through every consumer."""
        result = licensing.read(REPO)
        self.assertEqual(result["problem"], "", result["problem"])
        self.assertEqual(result["spdx"], "GPL-3.0-only")
        self.assertEqual(result["official"]["status"], "verbatim")
        self.assertTrue(result["no_vendored_third_party"])
        self.assertEqual(docs_check.licence_from_file(REPO), "GPL-3.0-only")
        self.assertEqual(presentation.licence_identity(REPO)["spdx"], "GPL-3.0-only")

    def test_a_missing_declaration_is_refused_rather_than_inferred_from_the_text(self):
        """(a) A tree with the official document and no notice has adopted a licence, not granted one."""
        self.root = tree(licence="verbatim")  # no LICENSING.md at all
        result = licensing.read(self.root)
        self.assertIsNone(result["spdx"])
        self.assertEqual(result["file"], NOTICE)
        self.assertIn("is missing", result["problem"])
        self.assertIn(OFFICIAL, result["problem"])
        # and a notice that is present but never grants anything is the same kind of gap
        self.root = tree(notice="# PC5's licence\n\nThe GNU General Public License is copyleft.\n")
        result = licensing.read(self.root)
        self.assertIsNone(result["spdx"])
        self.assertIn("states no licence grant for PC5", result["problem"])
        # neither file at all is not a contradiction to resolve, and is reported as nothing
        self.root = tree(licence=None)
        self.assertIsNone(licensing.read(self.root)["spdx"])
        self.assertEqual(docs_check.check(self.root), [],
                         "an absent licence is the link rule's problem, not this rule's")

    def test_contradictory_identifiers_inside_the_notice_are_refused(self):
        """(b) The notice cannot hold two ids, one asserted and one granted."""
        self.root = tree(notice="# PC5's licence\n\n"
                                "PC5 is licensed under the GNU General Public License version 3 "
                                "only (**GPL-3.0-or-later**).\n",
                         licence="verbatim")
        result = licensing.read(self.root)
        self.assertIsNone(result["spdx"])
        self.assertEqual(result["file"], NOTICE)
        self.assertIn("GPL-3.0-OR-LATER", result["problem"])
        self.assertIn("contradicts itself", result["problem"])

    def test_a_gpl2_body_under_a_gpl3_declaration_is_a_conflict_not_a_preference(self):
        """(c) When the standard document and the notice disagree, neither one wins by default."""
        self.root = tree(notice=GOOD_GRANT,
                         licence="                    GNU GENERAL PUBLIC LICENSE\n"
                                 "                       Version 2, June 1991\n\n"
                                 "   TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND "
                                 "MODIFICATION\n")
        result = licensing.read(self.root)
        self.assertIsNone(result["spdx"])
        self.assertEqual(result["file"], OFFICIAL)
        self.assertIn("version 2 text", result["problem"])
        self.assertIn(f"`{NOTICE}` grants version 3", result["problem"])
        self.assertEqual(result["official"]["version"], 2)
        self.assertEqual(result["official"]["status"], "modified")

    def test_a_later_version_claim_is_refused_and_never_printed(self):
        """(d) An *or later* grant is a different licence; nothing here adopts it quietly."""
        for wording in ("PC5 is distributed under the GNU General Public License version 3, "
                        "or any later version.",
                        "PC5 is distributed under the GNU General Public License version 3 "
                        "only, or any later version."):
            self.root = tree(notice=f"# PC5's licence\n\n{wording}\n", licence="verbatim")
            result = licensing.read(self.root)
            self.assertIsNone(result["spdx"], wording)
            self.assertIn("or-later", result["problem"], wording)
            self.assertNotEqual(result["spdx"], "GPL-3.0-or-later")
            self.assertNotIn("**GPL-3.0-or-later**", presentation.licence_row(result),
                             "a refused grant must not reach the page as *the* identifier")
            self.assertIn("no identifier is printed", presentation.licence_row(result))

    def test_a_grant_that_omits_the_version_or_the_clause_is_refused(self):
        self.root = tree(notice="# PC5's licence\n\nPC5 is distributed under the GNU General "
                                "Public License.\n", licence="verbatim")
        self.assertIn("no version number", licensing.read(self.root)["problem"])
        self.root = tree(notice="# PC5's licence\n\nPC5 is released under the GNU General Public "
                                "License version 3.\n", licence="verbatim")
        result = licensing.read(self.root)
        self.assertIsNone(result["spdx"])
        self.assertIn("without saying 'only' or 'any later version'", result["problem"])

    def test_the_boilerplate_is_not_a_grant(self):
        """The FSF's own text contains the wording this tool refuses to read as a decision.

        Two facts this refusal rests on, pinned here so the refusal cannot be "simplified" into
        parsing the boilerplate: the standard body does carry a later-version sentence, wrapped
        across lines by its own reflowing; and it says nothing about PC5, so no grant can be read
        out of it at all.
        """
        wrapped = re.sub(r"\s+", " ", canonical())
        self.assertIn("either version 3 of the License, or (at your option) any later version",
                      wrapped, "the assumption this refusal depends on")
        self.assertIn("How to Apply These Terms to Your New Programs", wrapped)
        self.assertNotIn("PC5", wrapped)
        self.root = tree(licence=canonical())  # the adopted text, with no notice to grant it
        self.assertIsNone(licensing.read(self.root)["spdx"])

    def test_a_modified_official_text_is_refused_in_every_shape(self):
        """(e) Verbatim means byte-identical: an insertion, a rewording and a whitespace edit all fail."""
        base = canonical()
        mutations = {
            "a grant spliced into the body": base + "\nPC5 is licensed under GPL version 3 only.\n",
            "a clause reworded inside the body": base.replace("TERMS AND CONDITIONS",
                                                               "TERMS AND CONDITIONS OF USE", 1),
            "one stray space": base.replace("Version 3, 29 June 2007", "Version 3, 29 June 2007 "),
            "a blank line added at the end": base + "\n",
        }
        for label, text in mutations.items():
            with self.subTest(label):
                self.assertNotEqual(hashlib.sha256(text.encode()).hexdigest(),
                                    licensing.GPL3_ONLY_SHA256)
                # the refusal must say what happened, not just that the hash moved
                self.root = tree(notice=GOOD_GRANT, licence=text)
                result = licensing.read(self.root)
                self.assertIsNone(result["spdx"], result)
                self.assertEqual(result["file"], OFFICIAL)
                self.assertIn("has been edited", result["problem"])
                self.assertIn(f"`{NOTICE}`", result["problem"])
        self.root = tree(notice=GOOD_GRANT, licence=base)
        self.assertEqual(licensing.read(self.root)["spdx"], "GPL-3.0-only",
                         "the unmutated text has to pass, or the four refusals prove nothing")

    def test_a_third_party_notice_replaced_in_the_body_is_caught(self):
        """(f) Notices that are not PC5's live in this file; overwriting one is an edit.

        The mechanism is the digest, which is what makes this case unskippable: the reader has
        no list of third-party blocks to compare, so it refuses *any* difference from the adopted
        text. The point of the fixture is that a change which looks like housekeeping — putting
        somebody else's notice in a comment, deleting a duplicate — still fails closed.
        """
        base = canonical()
        self.assertIn("Free Software Foundation", base, "the notice this case overwrites")
        replaced = base.replace("Free Software Foundation", "PC5 Contributors", 1)
        self.root = tree(notice=GOOD_GRANT, licence=replaced)
        result = licensing.read(self.root)
        self.assertIsNone(result["spdx"])
        self.assertIn("has been edited", result["problem"])
        # a foreign notice appended is equally refused
        self.root = tree(notice=GOOD_GRANT,
                         licence=base + "\nCopyright (c) somebody else, under terms not reviewed "
                                        "here.\n")
        self.assertIn("has been edited", licensing.read(self.root)["problem"])

    def test_a_file_that_is_not_the_gpl_at_all_is_the_wrong_document_not_an_edit(self):
        self.root = tree(notice=GOOD_GRANT, licence="# Licensing\n\nEverything here is public "
                                                    "domain.\n")
        result = licensing.read(self.root)
        self.assertEqual(result["official"]["status"], "foreign")
        self.assertIn("does not open with the GNU General Public License", result["problem"])

    def test_an_unreadable_official_text_refuses_instead_of_crashing(self):
        self.root = tree(notice=GOOD_GRANT)
        (self.root / OFFICIAL).unlink()
        (self.root / OFFICIAL).mkdir()
        result = licensing.read(self.root)
        self.assertIsNone(result["spdx"])
        self.assertIn("is unreadable", result["problem"])
        self.assertEqual(result["file"], OFFICIAL)

    def test_a_non_gpl_family_needs_its_own_canonical_text(self):
        self.root = tree(notice="# PC5's licence\n\nPC5 is released under the MIT License.\n",
                         licence="verbatim")
        result = licensing.read(self.root)
        self.assertIsNone(result["spdx"])
        self.assertIn("MIT", result["problem"])
        self.assertIn("changed together", result["problem"])


class GenerationAndValidationTest(unittest.TestCase):
    """(g) and (h): the row is generated, and the page and the gate refuse the same way."""

    def tearDown(self):
        if getattr(self, "root", None):
            shutil.rmtree(self.root, ignore_errors=True)

    def test_the_committed_row_matches_the_notice_it_was_generated_from(self):
        """A licence change that does not regenerate the page is stale, and says so here."""
        row = presentation.licence_row(licensing.read(REPO))
        about = (REPO / "docs/ABOUT.md").read_text(encoding="utf-8")
        self.assertIn(row, about, "docs/ABOUT.md §1's licence row is generated; run "
                                  "`make presentation` after changing either licence file")
        # the README's summary line is hand-written, so its identifier is pinned to the reader
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        line = next(line for line in readme.splitlines() if line.startswith("| **Licence** |"))
        self.assertIn("**Licence**", line)
        self.assertIn("GPL-3.0-only", line, line)
        self.assertIn("LICENSING.md", line, "the front page has to point at the notice")

    def test_the_generated_row_is_the_only_place_the_page_derives_the_id(self):
        """The section explains the mechanism without restating the grant in prose."""
        about = (REPO / "docs/ABOUT.md").read_text(encoding="utf-8")
        section = about.split("## 6. Licence and attribution", 1)[1].split("\n## 7.", 1)[0]
        for phrase in ("PC5 is licensed under", "PC5 is distributed under", "PC5 is released under"):
            self.assertNotIn(phrase, section,
                             f"the notice is the one place that grants; §6 must not ({phrase}…)")
        self.assertIn("LICENSING.md", section)
        self.assertIn("ADR-027", section)
        self.assertIn("verbatim", section)

    def test_a_refusal_reaches_the_page_as_a_sentence_and_the_gate_as_one_problem(self):
        """The renderer and the gate read one file, so they can only agree."""
        broken = {
            "notice grants nothing": {"notice": "# licence\n\nThe GPL is copyleft.\n"},
            "notice contradicts itself": {"notice": "PC5 is licensed under GPL version 3 only "
                                                     "(**GPL-3.0-or-later**).\n"},
            "body is edited": {"notice": GOOD_GRANT, "licence": canonical() + "x\n"},
            "body is another version": {"notice": GOOD_GRANT,
                                        "licence": "GNU GENERAL PUBLIC LICENSE\n"
                                                   "Version 2, June 1991\n\nterms\n"},
        }
        for label, files in broken.items():
            with self.subTest(label):
                self.root = tree(**files)
                result = licensing.read(self.root)
                self.assertIsNone(result["spdx"])
                row = presentation.licence_row(result)
                self.assertIn("no identifier is printed", row)
                self.assertIn(f"`{result['file']}`", row)
                self.assertIn(result["problem"], row)
                granted, note, culprit = docs_check.licence_grant(self.root)
                self.assertEqual((granted, culprit), (None, result["file"]))
                problems = [f"{culprit}: {note}"]
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(result["problem"], problems[0])
                self.assertTrue(problems[0].startswith(f"{result['file']}: "), problems[0])

    def test_a_valid_pair_produces_no_licence_problem_and_a_row_with_the_id(self):
        self.root = tree(notice=GOOD_GRANT, licence="verbatim")
        self.assertEqual(docs_check.check(self.root), [])
        result = licensing.read(self.root)
        self.assertEqual(result["spdx"], "GPL-3.0-only")
        self.assertIn("**GPL-3.0-only**", presentation.licence_row(result))
        self.assertEqual([p for p in docs_check.check(self.root) if "LICENS" in p.upper()], [])


class IndependentChecksTest(unittest.TestCase):
    """The reader is not the only thing that has to hold; the documents are checked against it."""

    def tearDown(self):
        if getattr(self, "root", None):
            shutil.rmtree(self.root, ignore_errors=True)

    def test_the_gate_and_the_reader_report_the_same_identifier(self):
        """`docs_check` delegates, so a passing reader is a passing gate and vice versa."""
        self.root = tree(notice=GOOD_GRANT, licence="verbatim")
        (self.root / "docs" / "ABOUT.md").write_text("# About PC5\n", encoding="utf-8")
        self.assertEqual(docs_check.licence_grant(self.root), ("GPL-3.0-only", "", ""))
        (self.root / "docs" / "ABOUT.md").write_text("# About PC5\n", encoding="utf-8")
        self.root = tree(notice=GOOD_GRANT, licence=canonical() + "x\n")
        (self.root / "docs" / "ABOUT.md").write_text("# About PC5\n", encoding="utf-8")
        granted, note, culprit = docs_check.licence_grant(self.root)
        self.assertIsNone(granted)
        self.assertEqual(culprit, OFFICIAL)
        # the rule is reached through the About contract, so the fixture carries a page; the
        # section rules of that contract add their own noise, which is why this filters by prefix
        problems = [p for p in docs_check.about_problems(self.root / "docs/ABOUT.md", self.root)
                    if p.startswith(f"{OFFICIAL}: ")]
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(note, problems[0])

    def test_the_notice_itself_holds_no_stale_identifier(self):
        """Whatever the tree says today, the two root files have to agree today."""
        notice = (REPO / NOTICE).read_text(encoding="utf-8")
        self.assertEqual(licensing.asserted_ids(licensing.plain(notice)), {"GPL-3.0-ONLY"})
        self.assertEqual(licensing.read(REPO)["spdx"], "GPL-3.0-only")

    def test_the_official_text_carries_no_project_wording_of_its_own(self):
        body = canonical()
        self.assertNotIn("PC5", body)
        self.assertNotIn("vendored", body)
        self.assertIn("GNU GENERAL PUBLIC LICENSE", body)
        self.assertIn("Version 3, 29 June 2007", body)
        self.assertEqual(len(body.splitlines()), 674, "the adopted document's shape is part of "
                                                      "what the pin asserts")

    def test_the_pinned_digest_is_the_adopted_file_not_an_invented_constant(self):
        """If this fails, someone changed `LICENSE` or the pin, and both are deliberate acts."""
        digest = hashlib.sha256(canonical().encode("utf-8")).hexdigest()
        self.assertEqual(digest, licensing.GPL3_ONLY_SHA256)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
