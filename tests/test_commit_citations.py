"""A citation is only evidence while Git can hand the object back.

The evidence documents justify their claims by citing commit ids. A rebase rewrites
those ids silently: the sentence still reads correctly, the diff it describes is still
there, and the hash is now a dead pointer. Before this guard existed nothing checked
it -- `tools/docs_check.py` validates links, anchors and `make` targets, and no test
resolved a citation -- so an entire evidence trail could rot while every gate stayed
green. That is exactly what happened when PR #28 was rebased onto PR #27.

The fixtures below build throwaway repositories, because a test that only ran against
this checkout could not show that the guard *fails*: the checkout is (after repair)
clean, and the interesting case is precisely the broken one. Every negative case here
is a citation that must be reported, and the positive cases pin the three ways a
naive checker would be wrong -- reporting timestamps, rejecting tree hashes, or
demanding reachability instead of resolvability.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import commit_citations  # noqa: E402


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class Fixture(unittest.TestCase):
    """A tiny repository with one real commit and one abandoned historical commit."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "audit@example.invalid")
        git(self.root, "config", "user.name", "Audit")
        git(self.root, "config", "commit.gpgsign", "false")

        tracked = self.root / "README.md"
        tracked.write_text("# Base\n", encoding="utf-8")
        (self.root / "*.md").write_text("", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        self.base = git(self.root, "rev-parse", "HEAD")

        (self.root / "docs").mkdir()
        (self.root / "docs" / "ARCHITECTURE.md").write_text("# Arch\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "docs directory")
        self.head = git(self.root, "rev-parse", "HEAD")

        # A commit that is then rewritten away, so its object stays present but is
        # not an ancestor of HEAD. This is the "historical, intentionally cited" shape.
        git(self.root, "checkout", "-q", "-b", "abandoned", self.base)
        abandoned = self.root / "abandoned.md"
        abandoned.write_text("old\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "abandoned work")
        self.abandoned = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-q", "master")

        self.evidence = self.root / "AUDIT_2026-11-16.md"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def cite(self, text: str, name: str = "AUDIT_2026-11-16.md") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def problems(self) -> list:
        return commit_citations.problems(self.root)


class ResolvingCitationTests(Fixture):
    def test_abbreviated_and_full_commit_citations_resolve(self):
        self.cite(
            f"Fixed in `{self.head[:7]}`; audited at `{self.base}`.\n"
        )
        self.assertEqual(self.problems(), [])

    def test_a_historical_revision_is_accepted_when_the_object_still_exists(self):
        """Resolvable is the property, not reachable.

        `self.abandoned` is not an ancestor of HEAD -- a rewrite removed it from the
        current line, exactly like the real `main` rewrites this repository records.
        A document that deliberately cites it is giving accurate evidence, so the
        guard must accept it rather than demand it be deleted.
        """
        self.assertNotEqual(git(self.root, "rev-parse", "HEAD"), self.abandoned)
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.root, "merge-base", "--is-ancestor", self.abandoned, "HEAD")
        self.cite(f"Base was `{self.abandoned[:7]}` before the rewrite.\n")
        self.assertEqual(self.problems(), [])

    def test_a_tree_hash_is_accepted_as_evidence(self):
        """Documents cite tree hashes on purpose ("= `141f91f^{tree}`")."""
        tree = git(self.root, "rev-parse", f"{self.head}^{{tree}}")
        self.cite(f"The combined tree is `{tree}`.\n")
        self.assertEqual(self.problems(), [])

    def test_timestamps_and_placeholders_are_not_treated_as_citations(self):
        """All-decimal tokens have a SHA's width and are not object ids."""
        self.cite(
            "Built at `2026111618480500`; a placeholder `123456789` follows.\n"
        )
        self.assertEqual(self.problems(), [])

    def test_tokens_shorter_than_an_abbreviation_are_ignored(self):
        self.cite("Version `abc123` is six characters, one short of an abbreviation.\n")
        self.assertEqual(self.problems(), [])


class DeadCitationTests(Fixture):
    def test_a_dangling_commit_citation_is_reported(self):
        self.cite("Fixed in `deadbee` — which no longer exists.\n")
        found = self.problems()
        self.assertEqual(len(found), 1, found)
        self.assertIn("deadbee", found[0])
        self.assertIn("AUDIT_2026-11-16.md:1", found[0])

    def test_the_report_names_the_file_line_and_context(self):
        self.cite(
            "Intro line.\n"
            "\n"
            "| finding | commit |\n"
            "| --- | --- |\n"
            "| D1 | `0badc0de` |\n"
        )
        found = self.problems()
        self.assertEqual(len(found), 1, found)
        self.assertIn("AUDIT_2026-11-16.md:5", found[0])
        self.assertIn("| D1 |", found[0])

    def test_every_occurrence_is_reported_not_only_the_first(self):
        self.cite("First `badc0de`, second `badc0de`.\n")
        self.assertEqual(len(self.problems()), 2)

    def test_documents_outside_the_evidence_scope_are_not_checked(self):
        """`docs/TEST_RESULTS.md` is a log of runs against revisions history removed."""
        self.cite("Base: `main` `af726d2`.\n", name="docs/TEST_RESULTS.md")
        self.assertEqual(self.problems(), [])

    def test_a_clean_checkout_reports_nothing(self):
        self.cite(f"See `{self.head[:7]}`.\n")
        self.assertEqual(self.problems(), [])


class RealTreeTests(unittest.TestCase):
    """The documents this repository actually ships."""

    def test_every_citation_in_the_shipped_evidence_documents_resolves(self):
        found = commit_citations.problems(REPO)
        self.assertEqual(
            found, [],
            "A dead commit citation was shipped:\n" + "\n".join(found),
        )

    def test_the_guard_actually_looks_at_the_shipped_documents(self):
        """A guard that silently checked nothing would pass the test above."""
        documents = commit_citations.evidence_documents(REPO)
        self.assertGreaterEqual(len(documents), 10, [d.name for d in documents])
        checked = 0
        for document in documents:
            text = document.read_text(encoding="utf-8", errors="ignore")
            checked += len(list(commit_citations.citations(text)))
        self.assertGreater(checked, 50, f"only {checked} citations parsed")

    def test_the_shipped_documents_do_not_cite_the_rebase_invalidated_ids(self):
        """The five ids the PR #28 rebase destroyed must not reappear."""
        stale = ("07337c2", "3b47f9e", "c34988c", "995730d", "201a96f", "de9c6a3")
        for document in commit_citations.evidence_documents(REPO):
            text = document.read_text(encoding="utf-8", errors="ignore")
            for reference in stale:
                self.assertNotIn(
                    f"`{reference}`", text,
                    f"{document.name} still cites the invalid id `{reference}`",
                )

    def test_the_guard_reports_a_dead_citation_in_the_real_tree(self):
        """Negative control against the shipped tree, then the fixture is restored.

        Patching a real document is the only way to show that the guard is wired to
        the real tree rather than only to fixtures. The file is restored from its
        original bytes, and the restoration is asserted, not assumed.
        """
        document = REPO / "AUDIT_PR26_PR27_2026-11-16.md"
        original = document.read_bytes()
        try:
            text = original.decode("utf-8")
            self.assertIn("ed9c6a3", text)
            document.write_text(text.replace("ed9c6a3", "deadbee"), encoding="utf-8")
            found = commit_citations.problems(REPO)
            self.assertTrue(
                any("deadbee" in problem for problem in found),
                f"guard did not notice the injected dead citation: {found}",
            )
        finally:
            document.write_bytes(original)
        self.assertEqual(commit_citations.problems(REPO), [])


if __name__ == "__main__":
    unittest.main()
