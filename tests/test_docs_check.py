"""The documentation checker has to catch the three ways documentation rots.

A link to a file that was renamed, an anchor to a heading that was reworded, and a
`make` command whose target was removed all look fine to a human skimming a diff, which
is exactly why they survive. These tests give each one a fixture that must fail, and
then check the real tree — including the contributor templates, which are documentation
that GitHub renders.
"""

from __future__ import annotations

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


class RepositoryTest(unittest.TestCase):
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
