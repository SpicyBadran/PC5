"""The documentation checker has to catch the three ways documentation rots.

A link to a file that was renamed, an anchor to a heading that was reworded, and a
`make` command whose target was removed all look fine to a human skimming a diff, which
is exactly why they survive. These tests give each one a fixture that must fail, and
then check the real tree — including the contributor templates, which are documentation
that GitHub renders.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import docs_check  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "Makefile").write_text("check:\n\t@true\n\npresentation-check:\n\t@true\n")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "target.md").write_text("# Real heading\n\nText.\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def problems(self) -> list:
        return docs_check.check(self.root)


class DetectionTest(Fixture):
    def test_a_clean_tree_passes(self):
        self.write("README.md", "# PC5\n\nSee [the target](docs/target.md#real-heading).\n"
                                 "Run `make check`.\n")
        self.assertEqual(self.problems(), [])

    def test_a_missing_file_is_caught(self):
        self.write("README.md", "See [gone](docs/gone.md).\n")
        self.assertTrue(any("link target missing" in p for p in self.problems()))

    def test_a_percent_encoded_path_resolves(self):
        """GitHub follows `[x](a%20b.md)` to the file `a b.md`; so must this."""
        self.write("docs/a b.md", "# Spaced\n\nText.\n")
        self.write("README.md", "See [spaced](docs/a%20b.md) and "
                                 "[its anchor](docs/a%20b.md#spaced).\n")
        self.assertEqual(self.problems(), [])

    def test_a_percent_encoded_path_to_nowhere_is_still_caught(self):
        self.write("README.md", "See [gone](docs/no%20such%20file.md).\n")
        self.assertTrue(any("link target missing" in p for p in self.problems()))

    def test_a_percent_encoded_nul_is_reported_not_raised(self):
        """`a%00b.md` decodes to a path with an embedded NUL.

        `Path.resolve()` raises `ValueError: embedded null byte` for that, which
        used to escape as a traceback and stop the whole check - turning a
        diagnostic tool into a crash on exactly the malformed input it exists to
        report. It must be an ordinary "link target missing".
        """
        self.write("README.md", "See [nul](docs/a%00b.md).\n")
        problems = self.problems()
        self.assertTrue(any("link target missing" in p for p in problems), problems)

    def test_a_percent_encoded_nul_with_a_fragment_is_reported_not_raised(self):
        self.write("README.md", "See [nul](docs/a%00b.md#frag).\n")
        problems = self.problems()
        self.assertTrue(any("link target missing" in p for p in problems), problems)

    def test_other_malformed_escapes_are_still_reported(self):
        """The fix must not have widened into "anything odd is silently fine"."""
        for target in ("docs/%zz.md", "docs/a%2", "docs/a%2Fb.md"):
            with self.subTest(target=target):
                self.write("README.md", f"See [bad]({target}).\n")
                problems = self.problems()
                self.assertTrue(any("link target missing" in p for p in problems),
                                (target, problems))

    def test_a_renamed_heading_is_caught(self):
        self.write("README.md", "See [the target](docs/target.md#old-heading).\n")
        problems = self.problems()
        self.assertTrue(any("anchor not found" in problem for problem in problems), problems)

    def test_an_anchor_into_the_same_file_is_checked(self):
        self.write("README.md", "# Top\n\n## A section\n\n[Jump](#a-section) and [miss](#nope)\n")
        problems = self.problems()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("#nope", problems[0])

    def test_a_removed_target_is_caught(self):
        self.write("README.md", "Run `make publish`.\n")
        self.assertTrue(any("make publish" in p for p in self.problems()))

    def test_a_variable_assignment_is_not_a_target(self):
        self.write("README.md", "Run `make BUILD_ID=dev iso`.\n")
        self.assertEqual(self.problems(), [])

    def test_external_links_are_not_resolved(self):
        self.write("README.md", "[upstream](https://example.invalid/x) and [mail](mailto:a@b)\n")
        self.assertEqual(self.problems(), [])

    def test_a_fenced_code_block_is_not_parsed_for_headings(self):
        self.write("README.md", "# Real\n\n```\n# not a heading\n```\n\n[ok](#real)\n")
        self.assertEqual(self.problems(), [])

    def test_duplicate_headings_get_numeric_suffixes(self):
        self.write("docs/target.md", "# Same\n\n# Same\n")
        self.write("README.md", "[second](docs/target.md#same-1)\n")
        self.assertEqual(self.problems(), [])

    def test_the_slug_rule_is_github_like(self):
        self.assertEqual(docs_check.slug("M1 — Physical boot: the *procedure*"),
                         "m1--physical-boot-the-procedure")


class AboutFixture(Fixture):
    """A tree with a real-shaped `docs/ABOUT.md`, one rule broken at a time.

    The fixture carries its own `LICENSE`, roadmap and `Makefile`, so each test changes
    one property of the page and nothing else can be blamed for the result.
    """

    IDENTITY = ("An independent engineering project that builds PC-side boot, hardware and "
                "compatibility infrastructure toward running genuine Sony PlayStation 5 "
                "system software on compatible PC hardware.")

    def setUp(self):
        super().setUp()
        # The licence arrives as a *pair*: the official text verbatim, and a notice that grants
        # it. The fixture copies the repository's own `LICENSE` rather than writing a
        # GPL-shaped stub, because the check it exercises is a digest comparison — a stub would
        # fail for the wrong reason and the test would pass for the wrong reason.
        shutil.copyfile(REPO / "LICENSE", self.root / "LICENSE")
        (self.root / "LICENSING.md").write_text(
            "# PC5's licence\n\n"
            "PC5 is licensed under the GNU General Public License version 3 only "
            "(`GPL-3.0-only`).\n"
            "PC5 vendors no third-party source archive.\n")
        (self.root / "README.md").write_text("# PC5\n\nGPL-3.0-only.\n")
        (self.root / "docs" / "architecture").mkdir(parents=True, exist_ok=True)
        (self.root / "docs/architecture/roadmap.json").write_text(json.dumps({
            "document_version": "test", "updated": "2026-01-01",
            "authority": "docs/architecture/SPEC.md",
            "product": {"identity": self.IDENTITY},
            "workstreams": [{"id": "WS0", "name": "n", "purpose": "p"}],
            "milestones": [
                {"id": "M1", "state": "complete", "title": "t", "workstream": "WS0",
                 "acceptance": ["a"], "evidence": ["docs/target.md"]},
                {"id": "M2", "state": "in_progress", "title": "t", "workstream": "WS0",
                 "acceptance": ["a", "b"]},
                {"id": "M3", "state": "blocked", "title": "t", "workstream": "WS0",
                 "acceptance": ["a"]},
                {"id": "M4", "state": "not_started", "title": "t", "workstream": "WS0",
                 "acceptance": ["a"]},
            ],
            "foundations": [],
        }))

    def page(self, **overrides) -> str:
        body = [self.IDENTITY, ""]
        for title in docs_check.ABOUT_SECTIONS:
            body += [f"## {title}", ""]
        for name in docs_check.ABOUT_BLOCKS:
            body += [f"<!-- pc5:{name}:begin -->", f"generated for {name}",
                     f"<!-- pc5:{name}:end -->", ""]
        body += ["The real path is `Makefile`, which exists.", ""]
        text = "\n".join(body)
        for _key, replacement in overrides.items():
            text = text + replacement
        return text

    def write_page(self, **overrides) -> None:
        """Write the page; with no overrides, insist the baseline is clean first.

        The clean-check only applies to the untouched fixture.  A test that supplies an
        override is supplying the violation, so requiring the baseline to be dirty as
        well would make the failure ambiguous about which rule fired.
        """
        self.write("docs/ABOUT.md", self.page(**overrides))
        if not overrides:
            self.assertEqual(self.about_problems(), [],
                             "the fixture is not clean before the rule is applied")

    def about_problems(self) -> list:
        return docs_check.about_problems(self.root / "docs/ABOUT.md", self.root)

    def test_a_well_formed_page_passes(self):
        self.write_page()
        self.assertEqual(self.about_problems(), [])

    def test_a_missing_section_is_caught(self):
        self.write_page()
        text = (self.root / "docs/ABOUT.md").read_text()
        (self.root / "docs/ABOUT.md").write_text(
            text.replace("## 6. Licence and attribution", "## 6. Notes"), encoding="utf-8")
        self.assertTrue(any("required section is missing" in problem
                            for problem in self.about_problems()), "a dropped licence section must fail")

    def test_sections_out_of_order_are_caught(self):
        self.write_page()
        text = (self.root / "docs/ABOUT.md").read_text()
        text = text.replace("## 3. What actually works", "## TEMP", 1)
        text = text.replace("## 8. Sources this page was written from",
                            "## 3. What actually works", 1)
        text = text.replace("## TEMP", "## 8. Sources this page was written from", 1)
        (self.root / "docs/ABOUT.md").write_text(text, encoding="utf-8")
        self.assertTrue(any("out of order" in problem for problem in self.about_problems()),
                        "swapping the sections must fail")

    def test_a_missing_generated_block_is_caught(self):
        self.write_page()
        text = (self.root / "docs/ABOUT.md").read_text()
        text = text.replace("<!-- pc5:about-progress:begin -->\ngenerated for about-progress\n"
                            "<!-- pc5:about-progress:end -->",
                            "**Overall completion** is 71.4% and 4 of 12 milestones are done.")
        (self.root / "docs/ABOUT.md").write_text(text, encoding="utf-8")
        problems = self.about_problems()
        self.assertTrue(any("no `pc5:about-progress` generated block" in problem for problem in problems))
        self.assertTrue(any("'71.4%'" in problem for problem in problems),
                        "a typed percentage must also be reported as typed")
        self.assertTrue(any("'4 of 12 milestones'" in problem or "milestones" in problem
                            for problem in problems), problems)

    def test_a_generated_number_is_not_reported_as_typed(self):
        """The rule reads prose. A block is the model's output and is checked elsewhere."""
        self.write_page()
        text = (self.root / "docs/ABOUT.md").read_text()
        text = text.replace("generated for about-progress",
                            "| **Overall completion** | **26.7%** |\n| **Milestones** | 9 of 35 |")
        (self.root / "docs/ABOUT.md").write_text(text, encoding="utf-8")
        self.assertEqual(self.about_problems(), [])

    def test_a_wrong_licence_is_caught(self):
        self.write_page(licence="\nPC5 is MIT licensed.\n")
        problems = self.about_problems()
        self.assertTrue(any("docs/ABOUT.md" in problem and "'MIT'" in problem
                            and "GPL-3.0-only" in problem for problem in problems), problems)

    def test_a_bare_family_name_is_not_a_contradiction(self):
        self.write_page(licence="\nPC5 is GPL-2.0 software, and the text is the GPL.\n")
        self.assertEqual(self.about_problems(), [])

    def test_the_licence_rule_also_reads_the_readme(self):
        self.write_page()
        (self.root / "README.md").write_text("# PC5\n\n**Licence**: MIT.\n", encoding="utf-8")
        self.assertTrue(any("README.md" in problem and "MIT" in problem
                            for problem in self.about_problems()),
                        "the front page's licence line is the one most readers see")

    def test_a_licence_stated_in_words_is_caught_even_without_an_spdx_id(self):
        """Debt D-4, as a gate.

        `README.md` on `main` once said "GNU General Public License version 2 only" while
        `LICENSING.md` granted `GPL-3.0-only`. The id pattern could not see it: the sentence
        spells the version out, and the fix that removed the sentence left the class of error
        open. This test is the difference between those two states — a footer in prose is a
        licence statement, and it has to agree with the grant.
        """
        self.write_page()
        (self.root / "README.md").write_text(
            "# PC5\n\nLicense\n-------\n\nPC5 is licensed under the GNU General Public "
            "License version 2 only.\n", encoding="utf-8")
        problems = [problem for problem in self.about_problems()
                    if "README.md" in problem and "version 2" in problem]
        self.assertTrue(problems, "a prose licence statement that contradicts the grant must fail")
        self.assertIn("fix the sentence, not the grant", problems[0])

    def test_a_quoted_or_coded_licence_mention_is_not_an_assertion(self):
        """Recording a corrected mistake must stay writable, or the gate gets worked around.

        A changelog line — the footer said "version 2 only"; fixed here — is a fact about a
        past state, not a present grant. Quotations and inline code are therefore excluded,
        and this test is what keeps that exemption from being quietly widened to whole files.
        """
        self.write_page()
        (self.root / "README.md").write_text(
            "# PC5\n\nThe footer once read \"GNU General Public License version 2 only\"; "
            "it was corrected at `2e5d39f`.\n\n`GPL version 2` is not this project's licence.\n",
            encoding="utf-8")
        self.assertEqual([problem for problem in self.about_problems()
                          if "in words as version" in problem], [])

    def test_the_prose_rule_reads_the_documents_that_instruct_contributors(self):
        """`AGENTS.md` tells every future agent what the licence is; it cannot be allowed to drift."""
        self.write_page()
        (self.root / "AGENTS.md").write_text(
            "# Instructions\n\nPC5 is licensed under the GNU General Public License "
            "version 2 only.\n", encoding="utf-8")
        self.assertTrue(any("AGENTS.md" in problem and "version 2" in problem
                            for problem in self.about_problems()),
                        "AGENTS.md is inside the checked set precisely because it is copied "
                        "forward into every future session")

    def test_a_notice_without_the_official_text_is_refused(self):
        """The grant needs the document it grants: a notice alone is an unverifiable claim."""
        self.write_page()
        (self.root / "LICENSE").unlink()
        self.assertIsNone(docs_check.licence_from_file(self.root))
        problems = [p for p in self.about_problems() if p.startswith("LICENSE: ")]
        self.assertEqual(len(problems), 1, self.about_problems())
        self.assertIn("cannot be verified", problems[0])

    def test_neither_file_is_not_guessed_at(self):
        """No notice and no text: nothing to compare, and no second failure for a broken link."""
        self.write_page()
        (self.root / "LICENSE").unlink()
        (self.root / "LICENSING.md").unlink()
        self.assertIsNone(docs_check.licence_from_file(self.root))
        self.assertEqual([p for p in self.about_problems()
                          if "LICENSE" in p or "LICENSING" in p], [],
                         "an absent licence is the link rule's problem, not this rule's")
        (self.root / "README.md").write_text("# PC5\n\n[Licence](LICENSE)\n", encoding="utf-8")
        self.assertTrue(any("link target missing: LICENSE" in problem
                            for problem in self.problems()),
                        "and it is reported, exactly once, where the reader would click")

    def test_an_unreadable_licence_file_is_reported_against_the_file(self):
        self.write_page()
        licence = self.root / "LICENSE"
        licence.unlink()
        licence.mkdir()  # a directory where the file should be: readable as nothing
        self.assertTrue(any("LICENSE: is unreadable" in problem
                            for problem in self.about_problems()))

    def test_an_edited_official_text_is_reported_once_against_the_file(self):
        """A grant pasted into the GPL makes the file not-the-GPL: one problem, against `LICENSE`."""
        self.write_page()
        body = (self.root / "LICENSE").read_text(encoding="utf-8")
        (self.root / "LICENSE").write_text(
            body + "\nPC5 is distributed under GPL version 2 only.\n", encoding="utf-8")
        (self.root / "README.md").write_text("# PC5\n\n**Licence**: GPL-2.0-only.\n",
                                             encoding="utf-8")
        problems = [p for p in self.about_problems() if "LICENSE" in p or "states" in p]
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("LICENSE", problems[0])
        self.assertIn("has been edited", problems[0])
        self.assertIn("LICENSING.md", problems[0],
                      "and it names the file the wording actually belongs in")

    def test_a_notice_that_grants_nothing_is_reported_once_against_the_notice(self):
        """The other half of the same rule: a perfect text with no grant is not a licence claim."""
        self.write_page()
        (self.root / "LICENSING.md").write_text(
            "# PC5's licence\n\nThe GNU General Public License is a copyleft licence.\n",
            encoding="utf-8")
        problems = [p for p in self.about_problems()
                    if p.startswith("LICENSING.md: ") or "states" in p]
        self.assertEqual(len(problems), 1, self.about_problems())
        self.assertIn("states no licence grant for PC5", problems[0])

    def test_a_body_of_the_other_version_and_the_notice_are_a_contradiction(self):
        self.write_page()
        (self.root / "LICENSE").write_text(
            "                    GNU GENERAL PUBLIC LICENSE\n"
            "                       Version 2, June 1991\n\n"
            "   TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND MODIFICATION\n",
            encoding="utf-8")
        problems = self.about_problems()
        self.assertEqual([p for p in problems if "contradict each other" in p], problems,
                         "the disagreement is the only reportable fact here")
        self.assertTrue(problems[0].startswith("LICENSE: "), problems[0])

    def test_a_cited_path_that_does_not_exist_is_caught(self):
        self.write_page(paths="\nSee `docs/nowhere.md` for the record.\n")
        self.assertTrue(any("cited path does not exist" in problem and "docs/nowhere.md" in problem
                            for problem in self.about_problems()), problems_hint())

    def test_a_generated_artefact_path_is_allowed(self):
        self.write_page(paths="\nThe image is written to `build/pc5.iso` by `make iso`.\n")
        self.assertEqual(self.about_problems(), [])

    def test_a_path_cited_as_history_is_allowed(self):
        """Records are not rewritten to satisfy a checker; saying so is the exemption."""
        self.write_page(paths="\nThe pipeline used to live at the historical path "
                              "`ci/build.yml`, now `.github/workflows/build.yml`.\n")
        self.assertEqual(self.about_problems(), [])

    def test_a_milestone_claimed_as_verified_must_be_complete(self):
        self.write_page(status="\n`M2` is implemented and verified.\n")
        problems = self.about_problems()
        self.assertTrue(any("`M2`" in problem and "recorded state is 'in_progress'" in problem
                            for problem in problems), problems)

    def test_a_consistent_milestone_claim_passes(self):
        self.write_page(status="\n`M1` is verified, `M2` is experimental, `M3` is blocked and "
                                "`M4` is planned.\n")
        self.assertEqual(self.about_problems(), [])

    def test_a_claim_does_not_leak_across_clauses(self):
        """Two milestones in one sentence, each with its own standing, is fine prose."""
        self.write_page(status="\n`M1` is verified while `M2` is experimental and `M4` is "
                                "planned, though `M3` is blocked.\n")
        self.assertEqual(self.about_problems(), [])

    def test_a_planned_milestone_described_as_blocked_is_caught(self):
        self.write_page(status="\n`M4` is blocked by a missing cable.\n")
        self.assertTrue(any("`M4`" in problem for problem in self.about_problems()))

    def test_the_rule_ignores_an_absent_roadmap_rather_than_inventing_states(self):
        self.write_page()
        (self.root / "docs/architecture/roadmap.json").unlink()
        problems = self.about_problems()
        self.assertEqual([p for p in problems if "does not support" in p], [])

    def test_an_absent_page_is_not_a_link_checkers_problem(self):
        """The broken *link* is the reportable fact; a second failure adds nothing."""
        self.assertEqual(docs_check.about_problems(self.root / "docs/ABOUT.md", self.root), [])
        (self.root / "README.md").write_text("# PC5\n\n[About](docs/ABOUT.md)\n", encoding="utf-8")
        self.assertTrue(any("link target missing: docs/ABOUT.md" in problem
                            for problem in self.problems()),
                        "the link rule is what reports an absent page")


def problems_hint():
    return "a path that is not in the tree has to be named"


class RepositoryTest(unittest.TestCase):
    def test_this_repository_has_the_about_page(self):
        """Existence is a decision about the project, so it is asserted here."""
        about = REPO / "docs/ABOUT.md"
        self.assertTrue(about.exists(), "docs/ABOUT.md is the page a newcomer is sent to")
        self.assertTrue((REPO / "README.md").read_text(encoding="utf-8")
                        .count("docs/ABOUT.md") >= 1, "the README must link it")

    def test_the_about_page_satisfies_its_own_contract(self):
        problems = docs_check.about_problems(REPO / "docs/ABOUT.md", REPO)
        self.assertEqual(problems, [], "\n".join(problems))
    def test_the_real_tree_passes(self):
        self.assertEqual(docs_check.check(REPO), [])

    def test_every_make_command_in_the_documents_exists(self):
        targets = docs_check.make_targets(REPO)
        missing = []
        for path in docs_check.markdown_files(REPO):
            for match in docs_check.MAKE.finditer(path.read_text(encoding="utf-8")):
                if match.group(1) not in targets:
                    missing.append(f"{path.relative_to(REPO)}: make {match.group(1)}")
        self.assertEqual(missing, [])

    def test_the_contributor_templates_reference_real_documents(self):
        templates = sorted((REPO / ".github").rglob("*.yml")) + \
            sorted((REPO / ".github").rglob("*.md"))
        self.assertTrue(templates, "no contributor templates")
        for path in templates:
            for match in docs_check.LINK.finditer(path.read_text(encoding="utf-8")):
                target = match.group(1)
                if target.startswith(("http", "mailto", "#")):
                    continue
                self.assertTrue((path.parent / target).resolve().exists(),
                                f"{path.name} links a missing file: {target}")

    def test_issue_forms_have_the_fields_github_requires(self):
        for name in ("bug_report.yml", "feature_request.yml"):
            text = (REPO / ".github/ISSUE_TEMPLATE" / name).read_text(encoding="utf-8")
            for field in ("name:", "description:", "body:", "type: input",
                          "validations:", "required: true"):
                self.assertIn(field, text, f"{name} is missing {field}")

    def test_the_pull_request_template_keeps_the_levels_apart(self):
        text = (REPO / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
        for level in ("Implemented", "Unit tested", "Build verified", "Emulator executed",
                      "Stub firmware executed", "Real OVMF executed",
                      "Physical hardware tested", "Genuine Sony system software executed"):
            self.assertIn(level, text)
        self.assertIn("NOT TESTED", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
