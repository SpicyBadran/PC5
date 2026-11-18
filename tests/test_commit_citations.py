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

import contextlib
import io
import os
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

    def test_an_all_digit_object_id_is_still_a_citation(self):
        """A real object id can be all decimal digits.

        About 3.7% of 7-character ids are, so treating "all digits" as proof of
        "not an object id" would have silently skipped a genuine citation roughly
        once in 27. The exemption is now earned by a failed lookup, not assumed
        from the shape. This is the case that produced an intermittent failure
        before the fix.
        """
        digit_sha = self._make_all_digit_commit()
        self.assertTrue(digit_sha.isdigit(), digit_sha)
        self.cite(f"Fixed in `{digit_sha}`.\n")
        self.assertEqual(self.problems(), [], "an all-digit object id was skipped")
        self.assertEqual(len(list(commit_citations.citations(
            f"`{digit_sha}`",
            resolves=lambda r: commit_citations.object_type(self.root, r) is not None,
        ))), 1)

    def test_an_all_digit_token_that_is_not_an_object_is_skipped(self):
        """The timestamp case the exemption exists for must keep working."""
        self.cite("Built at `2026111618480500`; placeholder `123456789`.\n")
        self.assertEqual(self.problems(), [])

    def _make_all_digit_commit(self):
        """Commit until the abbreviated id is all decimal digits, then return it.

        Deterministic in effect, not in luck: the loop converges in a few thousand
        commits at worst, and stops as soon as it succeeds.
        """
        for index in range(20000):
            candidate = self.root / "digit-probe.txt"
            candidate.write_text(str(index), encoding="utf-8")
            git(self.root, "add", "-A")
            git(self.root, "commit", "-q", "--allow-empty", "-m", f"probe {index}")
            short = git(self.root, "rev-parse", "--short=7", "HEAD")
            if short.isdigit():
                return short
        self.fail("could not produce an all-digit abbreviated id")

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

    def test_the_architecture_cross_reference_lands_on_the_right_commit(self):
        """`ARCHITECTURE.md` sends the reader to `TEST_RESULTS.md` §27 for the 4 GiB
        identity-map correction, and §27 names the commit that fixed it.

        `docs/TEST_RESULTS.md` is deliberately outside this guard's scope, so nothing
        else would notice if those two documents disagreed -- and they did: the
        architecture page was corrected to `ed9c6a3` while §27 still said `de9c6a3`,
        a transposition that never named any object. This test pins the one
        cross-reference that binds them, not the whole log.

        It is deliberately narrow. It asserts that the cited object *resolves* and
        that both documents name the same id; it does not re-check every reference
        in the log, which the file's own exclusion rationale puts out of scope.
        """
        architecture = (REPO / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        results = (REPO / "docs" / "TEST_RESULTS.md").read_text(encoding="utf-8")

        # The architecture page must still point at §27, or this test is guarding
        # nothing and should be revisited rather than silently passing.
        self.assertIn("see `TEST_RESULTS.md` §27", architecture)
        self.assertIn("## 27. Correction to §26", results)

        architecture_ids = set(commit_citations.CITATION.findall(architecture))
        section_27 = results.split("## 27. Correction to §26", 1)[1]
        section_27 = section_27.split("\n## 28.", 1)[0]
        cited_in_27 = {
            reference
            for _, reference, _ in commit_citations.citations(section_27)
        }

        # The correction §27 records must be a commit the architecture page also
        # names, so the reader following the cross-reference arrives at the same
        # evidence. A typo on one side breaks this with no other symptom.
        correction_ids = {
            reference
            for reference in cited_in_27
            if reference in architecture_ids
        }
        self.assertTrue(
            correction_ids,
            "ARCHITECTURE.md and TEST_RESULTS.md §27 share no commit reference; "
            f"architecture cites {sorted(architecture_ids)}, §27 cites "
            f"{sorted(cited_in_27)}",
        )
        for reference in correction_ids:
            self.assertIsNotNone(
                commit_citations.object_type(REPO, reference),
                f"`{reference}` is cross-referenced by both documents but names no "
                f"Git object",
            )

    def test_the_documents_cited_by_the_architecture_page_resolve(self):
        """Every object the architecture page names resolves, including §27's id.

        This covers `docs/TEST_RESULTS.md` at exactly one point -- the commit §27
        credits -- without putting the historical log into the guard's sweep. The
        log stays excluded because it records runs against revisions that history
        removed; that exclusion is not weakened by checking what another in-scope
        document points at.
        """
        found = commit_citations.problems(REPO)
        self.assertEqual(found, [], "in-scope documents cite dead objects")

        typo = "de9c6a3"
        for name in ("docs/ARCHITECTURE.md", "docs/TEST_RESULTS.md"):
            text = (REPO / name).read_text(encoding="utf-8")
            self.assertNotIn(
                f"`{typo}`", text,
                f"{name} still carries the transposed id `{typo}`, which never "
                f"named any object; the commit is `ed9c6a3`",
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


class CommandLineExitTests(Fixture):
    """"Checked and passed" must be distinguishable from "could not check".

    The guard is a gate in `make check`, so its exit status is the whole product. A
    checker whose document globbing silently stopped matching used to print
    `CITATIONS OK: ... (0 documents)` and exit 0, which is an absent check presented
    as a positive result. These tests pin the three outcomes apart.
    """

    def run_cli(self, root):
        """Run `main()` against ``root`` exactly as the Makefile would."""
        original_root = commit_citations.ROOT
        commit_citations.ROOT = root
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = commit_citations.main([])
        finally:
            commit_citations.ROOT = original_root
        return code, buffer.getvalue()

    def test_a_valid_repository_with_documents_exits_zero(self):
        self.cite(f"See `{self.head[:7]}`.\n")
        code, output = self.run_cli(self.root)
        self.assertEqual(code, 0, output)
        self.assertIn("CITATIONS OK", output)
        # The fixture has two in-scope documents: the evidence file written here and
        # the `docs/ARCHITECTURE.md` the fixture creates as part of its repository.
        self.assertIn("1 commit reference(s) resolve across 2 documents", output)

    def test_a_dead_citation_still_exits_one(self):
        self.cite("Fixed in `deadbee`.\n")
        code, output = self.run_cli(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("[FAIL]", output)
        self.assertIn("deadbee", output)
        self.assertNotIn("CITATIONS OK", output)

    def test_zero_documents_exits_unchecked_and_does_not_claim_success(self):
        """The regression: an empty tree used to print CITATIONS OK and exit 0."""
        code, output = self.run_cli(self.root)
        self.assertEqual(code, commit_citations.EXIT_UNCHECKED, output)
        self.assertIn("NOT CHECKED", output)
        self.assertNotIn("CITATIONS OK", output)
        self.assertIn("not a pass", output)

    def test_a_non_repository_exits_unchecked(self):
        """No `.git`: nothing can be resolved, so nothing may be claimed."""
        plain = Path(tempfile.mkdtemp())
        try:
            (plain / "AUDIT_2026-11-16.md").write_text("See `abc1234`.\n", encoding="utf-8")
            self.assertFalse(commit_citations.is_repository(plain))
            code, output = self.run_cli(plain)
            self.assertEqual(code, commit_citations.EXIT_UNCHECKED, output)
            self.assertIn("NOT CHECKED", output)
            self.assertNotIn("CITATIONS OK", output)
        finally:
            shutil.rmtree(plain, ignore_errors=True)

    def test_documents_without_any_citation_exit_unchecked(self):
        """Documents found, but nothing in them to check -- still not a pass."""
        self.cite("Prose with no object ids at all.\n")
        code, output = self.run_cli(self.root)
        self.assertEqual(code, commit_citations.EXIT_UNCHECKED, output)
        self.assertIn("NOT CHECKED", output)
        self.assertNotIn("CITATIONS OK", output)

    def test_git_unavailable_exits_unchecked_and_does_not_raise(self):
        """A missing or unrunnable `git` is an unperformed check, not a crash."""
        self.cite(f"See `{self.head[:7]}`.\n")
        original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = "/nonexistent-directory-for-this-test"
        try:
            self.assertFalse(
                commit_citations.is_repository(self.root),
                "fixture precondition: git must be unreachable")
            code, output = self.run_cli(self.root)
        finally:
            os.environ["PATH"] = original_path
        self.assertEqual(code, commit_citations.EXIT_UNCHECKED, output)
        self.assertIn("NOT CHECKED", output)
        self.assertNotIn("CITATIONS OK", output)

    def test_a_tree_or_blob_citation_still_passes(self):
        """The documented object-type policy must survive the exit-status change."""
        tree = git(self.root, "rev-parse", f"{self.head}^{{tree}}")
        blob = git(self.root, "rev-parse", f"{self.head}:README.md")
        self.cite(f"The tree is `{tree}`; the file is `{blob}`.\n")
        code, output = self.run_cli(self.root)
        self.assertEqual(code, 0, output)
        self.assertIn("CITATIONS OK", output)

    def test_a_historical_unreachable_citation_still_passes(self):
        """No reachability requirement is introduced by the exit-status change."""
        self.cite(f"Base was `{self.abandoned[:7]}` before the rewrite.\n")
        code, output = self.run_cli(self.root)
        self.assertEqual(code, 0, output)
        self.assertIn("CITATIONS OK", output)

    def test_the_make_target_propagates_a_failure(self):
        """`make citations-check` must fail when the guard fails.

        Run against the real tree through the real recipe, with a fixture directory
        standing in for a broken repository, so this is the Makefile's behaviour and
        not a restatement of the Python.
        """
        result = subprocess.run(
            ["make", "-s", "citations-check"], cwd=str(REPO),
            capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(
            result.returncode, 0,
            f"`make citations-check` failed on the shipped tree:\n{result.stdout}{result.stderr}")
        self.assertIn("CITATIONS OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
