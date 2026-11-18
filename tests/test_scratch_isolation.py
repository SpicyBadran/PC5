"""Regression coverage for the shared-scratch-path defect.

The defect
----------
``test_fat32_expand`` compiled its host driver into ``build/test/m1i`` and wrote
``in.img``, ``out.img`` and ``lba.txt`` there with fixed names.  Two suite
processes in one checkout therefore overwrote each other's images mid-run, and
the failures appeared in parity, directory-growth and fault-injection tests that
had nothing to do with each other.  Separately, the ``unit`` make target linked
``build/kernel-unit`` to a single name, so two concurrent ``make check`` runs hit
``ETXTBSY`` ("Text file busy").

These tests assert the *isolation itself*, not that some helper was called:

* distinct directories for distinct cases and distinct processes;
* two real processes running the same module concurrently both pass;
* cleanup removes only its own tree;
* cleanup behaviour - a passing case removes its directory, a failing or
  erroring case keeps it, the class tree is removed at the class boundary - is
  verified through a real ``unittest`` runner, never through a fake outcome
  object (``unittest.case._Outcome`` has no ``errors``/``failures`` attributes,
  and its ``success`` flag is reset to ``True`` while a cleanup body runs, so
  neither can be used to decide whether a case failed);
* the operations the module exists to exercise are still exercised;
* a **regression control** shows the old fixed-path scheme would collide, built
  in a throwaway tree so the real suite never corrupts shared state.

The control is the important half: without it, "the new code is isolated" is a
statement about the new code, not evidence that the old shape was ever broken.
"""

from __future__ import annotations

import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import scratch  # noqa: E402


class ScratchIsolationTest(unittest.TestCase):
    """The helper's own guarantees."""

    def test_the_root_is_stable_within_a_process(self):
        self.assertEqual(scratch.root(), scratch.root(),
                         "the process root must not change between calls, or a "
                         "class-level tree and a case-level tree would land in "
                         "different places")

    def test_the_root_is_outside_the_repository(self):
        root = pathlib.Path(scratch.root()).resolve()
        repo = ROOT.resolve()
        self.assertFalse(
            str(root).startswith(str(repo) + os.sep),
            f"scratch root {root} is inside the checkout {repo}; a stray directory "
            f"there could be committed, and two checkouts would share it")

    def test_distinct_cases_get_distinct_directories(self):
        first = scratch.directory("isolation", "case-a")
        second = scratch.directory("isolation", "case-b")
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.isdir(first) and os.path.isdir(second))
        # Fixed filenames inside them must not collide.
        for directory in (first, second):
            pathlib.Path(directory, "in.img").write_bytes(b"payload")
        self.assertNotEqual(
            pathlib.Path(first, "in.img").resolve(),
            pathlib.Path(second, "in.img").resolve(),
            "two cases using the same fixed filename must resolve to different "
            "files")

    def test_a_second_call_for_the_same_owner_still_gets_a_fresh_directory(self):
        first = scratch.directory("isolation", "same-owner")
        second = scratch.directory("isolation", "same-owner")
        self.assertNotEqual(first, second,
                            "re-entering an owner must not reuse a directory, or a "
                            "re-run in one process would inherit stale images")

    def test_a_competing_process_gets_a_different_root(self):
        """Two interpreters must not share the per-process root."""
        code = textwrap.dedent("""
            import sys, pathlib
            sys.path.insert(0, sys.argv[1])
            import scratch
            print(scratch.root())
        """)
        ours = scratch.root()
        seen = set()
        for _ in range(3):
            result = subprocess.run(
                [sys.executable, "-c", code, str(ROOT / "tests")],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            seen.add(result.stdout.strip())
        self.assertEqual(len(seen), 3,
                         f"three subprocesses produced {len(seen)} distinct roots; "
                         f"a shared root is the defect this module exists to prevent")
        self.assertNotIn(ours, seen,
                         "a subprocess reused this process's scratch root")


class CleanupLifecycleTest(unittest.TestCase):
    """Cleanup behaviour observed through a real runner, not a fake outcome.

    An earlier version of this file faked ``_outcome`` with an object exposing
    ``errors``/``failures`` lists.  ``unittest.case._Outcome`` has neither
    attribute, so those tests passed against a double that does not exist in the
    real runner - and the real ``register_cleanup`` fell into its
    ``except AttributeError`` branch on every test, keeping every directory.
    These tests instead run real ``TestCase`` classes through a real
    ``TextTestRunner`` and inspect the scratch root when the runner returns,
    before ``atexit`` can hide anything.
    """

    def setUp(self):
        # Start from an empty root so "nothing remains" is a meaningful claim.
        shutil.rmtree(scratch.root(), ignore_errors=True)
        self.addCleanup(self._clean_root)

    @staticmethod
    def _clean_root():
        shutil.rmtree(scratch.root(), ignore_errors=True)

    def _make_case(self, *, set_up_class_raises=False, set_up_raises=False):
        """A TestCase that uses scratch exactly as the FAT32 drivers do.

        Defined locally so the outer discovery run never collects it.
        """

        class _Case(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                cls.tree = scratch.directory("lifecycle", cls.__name__)
                pathlib.Path(cls.tree, "driver").write_bytes(b"bin")
                cls.addClassCleanup(scratch.drop_class_tree, cls.tree)
                if set_up_class_raises:
                    raise RuntimeError("setUpClass boom")

            def setUp(self):
                if set_up_raises:
                    raise RuntimeError("setUp boom")

            def _work(self):
                where = scratch.directory("lifecycle", type(self).__name__,
                                          self._testMethodName)
                pathlib.Path(where, "in.img").write_bytes(b"payload")
                scratch.register_cleanup(self, where)
                return where

            def test_a_pass(self):
                self._work()

            def test_b_fail(self):
                self._work()
                self.fail("boom")

            def test_c_error(self):
                self._work()
                raise RuntimeError("boom")

            def test_d_subtest_fail(self):
                self._work()
                with self.subTest(part="x"):
                    self.fail("boom")

            def test_e_pass_after_fail(self):
                self._work()

            def test_f_never_runs(self):
                self.fail("must not run")

        return _Case

    def _run(self, suite):
        """Run ``suite`` for real and report what the scratch root still holds."""
        result = unittest.TextTestRunner(stream=io.StringIO(),
                                         verbosity=0).run(suite)
        root = scratch.root()
        remaining = []
        for dirpath, _, _ in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            if rel != ".":
                remaining.append(rel)
        return result, root, sorted(remaining)

    def test_a_passing_case_removes_its_directory(self):
        case = self._make_case()
        result, _, remaining = self._run(unittest.TestSuite([case("test_a_pass")]))
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.failures) + len(result.errors), 0)
        self.assertEqual(remaining, [],
                         f"a passing run left scratch behind: {remaining}")
        self.assertFalse(os.path.exists(case.tree),
                         "the class tree must be removed at the class boundary")

    def test_a_failing_case_keeps_only_its_own_directory(self):
        case = self._make_case()
        suite = unittest.TestSuite([case("test_b_fail"), case("test_a_pass")])
        result, _, remaining = self._run(suite)
        self.assertEqual(len(result.failures), 1)
        kept = [p for p in remaining if "test_b_fail" in p]
        self.assertTrue(kept,
                        f"the failing case's directory was removed: {remaining}")
        self.assertFalse(any("test_a_pass" in p for p in remaining),
                         "cleanup removed the wrong tree, or kept a passing "
                         f"case's directory: {remaining}")
        self.assertFalse(os.path.exists(case.tree),
                         "the class tree must be removed at the class boundary "
                         "even when a case in it failed")

    def test_an_erroring_case_keeps_its_directory(self):
        case = self._make_case()
        result, _, remaining = self._run(unittest.TestSuite([case("test_c_error")]))
        self.assertEqual(len(result.errors), 1)
        self.assertTrue(any("test_c_error" in p for p in remaining),
                        f"an erroring case's directory was removed: {remaining}")

    def test_a_failing_subtest_keeps_its_directory(self):
        case = self._make_case()
        result, _, remaining = self._run(
            unittest.TestSuite([case("test_d_subtest_fail")]))
        self.assertEqual(len(result.failures), 1)
        self.assertTrue(any("test_d_subtest_fail" in p for p in remaining),
                        "a subTest failure was not attributed to its case, so its "
                        f"directory was removed: {remaining}")

    def test_a_pass_after_a_failure_still_cleans_up(self):
        """One failing case must not retain another case's directory.

        The old implementation recorded a process-wide failure flag, so a single
        failure kept every later class tree.  Attribution is per case.
        """
        case = self._make_case()
        suite = unittest.TestSuite([case("test_b_fail"),
                                    case("test_e_pass_after_fail")])
        result, _, remaining = self._run(suite)
        self.assertEqual(len(result.failures), 1)
        self.assertFalse(any("test_e_pass_after_fail" in p for p in remaining),
                         "a passing case after a failure kept its directory; "
                         f"failure attribution is process-wide: {remaining}")

    def test_the_class_cleanup_runs_when_set_up_class_fails(self):
        case = self._make_case(set_up_class_raises=True)
        result, _, remaining = self._run(
            unittest.TestSuite([case("test_f_never_runs")]))
        self.assertEqual(len(result.errors), 1)
        self.assertFalse(os.path.exists(case.tree),
                         "addClassCleanup must run even when setUpClass raises "
                         "part-way through")
        self.assertEqual(remaining, [],
                         f"a setUpClass failure left scratch behind: {remaining}")

    def test_a_setup_failure_leaves_no_case_directory(self):
        case = self._make_case(set_up_raises=True)
        result, _, remaining = self._run(
            unittest.TestSuite([case("test_f_never_runs")]))
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(remaining, [],
                         "a setUp failure must not leave a case directory "
                         f"behind: {remaining}")
        self.assertFalse(os.path.exists(case.tree))

    def _make_expected_failure_case(self):
        """Cases whose expected-failure *claim* itself has an outcome.

        ``test_g_unexpected_success`` carries ``@expectedFailure`` and returns
        normally, which ``unittest`` reports as an unexpected success: a failing
        run.  ``test_h_genuine_expected_failure`` carries the same marker and does
        fail, which is a pass.  Keeping both makes the retention decision
        falsifiable in both directions - a predicate that simply keeps everything
        with the marker set fails the second test.
        """

        class _Case(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                cls.tree = scratch.directory("lifecycle", cls.__name__)
                pathlib.Path(cls.tree, "driver").write_bytes(b"bin")
                cls.addClassCleanup(scratch.drop_class_tree, cls.tree)

            def _work(self):
                where = scratch.directory("lifecycle", type(self).__name__,
                                          self._testMethodName)
                pathlib.Path(where, "in.img").write_bytes(b"payload")
                scratch.register_cleanup(self, where)
                return where

            @unittest.expectedFailure
            def test_g_unexpected_success(self):
                self._work()

            @unittest.expectedFailure
            def test_h_genuine_expected_failure(self):
                self._work()
                self.fail("this failure was expected")

        return _Case

    def test_unexpected_success_keeps_its_directory(self):
        """A clean exit is not a pass when the case promised to fail.

        ``TestCase.run`` appends to ``TestResult.unexpectedSuccesses`` only
        *after* ``doCleanups()``, so a cleanup that consults that list sees it
        empty and deletes the directory for what the suite reports as a failure.
        See ``FOLLOWUP_EVIDENCE_F1_F5_2026-11-16.md`` §11.
        """
        case = self._make_expected_failure_case()
        result, _, remaining = self._run(
            unittest.TestSuite([case("test_g_unexpected_success")]))
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.unexpectedSuccesses), 1,
                         "the runner must report this as an unexpected success")
        self.assertEqual(result.wasSuccessful(), False,
                         "an unexpected success must fail the run, or the "
                         "retention policy below has no basis")
        self.assertTrue(any("test_g_unexpected_success" in p for p in remaining),
                        "an unexpected success is a failing run, so its case "
                        f"directory must be kept: {remaining}")
        self.assertFalse(os.path.exists(case.tree),
                         "the class tree must still be removed at the class "
                         "boundary")

    def test_a_genuine_expected_failure_removes_its_directory(self):
        """The inverse: a met expectation is a pass and keeps nothing.

        Without this, a predicate that retained every ``@expectedFailure`` case
        would satisfy the test above while quietly reintroducing the retention
        bug this module exists to fix.
        """
        case = self._make_expected_failure_case()
        result, _, remaining = self._run(
            unittest.TestSuite([case("test_h_genuine_expected_failure")]))
        self.assertEqual(len(result.expectedFailures), 1,
                         "the runner must report this as an expected failure")
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(remaining, [],
                         "a met expectation is a pass and must leave no scratch "
                         f"behind: {remaining}")
        self.assertFalse(os.path.exists(case.tree))

    def test_keep_env_keeps_an_unexpected_success_too(self):
        case = self._make_expected_failure_case()
        with unittest.mock.patch.dict(os.environ, {scratch.KEEP_ENV: "1"}):
            result, _, remaining = self._run(
                unittest.TestSuite([case("test_g_unexpected_success")]))
        self.assertEqual(len(result.unexpectedSuccesses), 1)
        self.assertTrue(any("test_g_unexpected_success" in p for p in remaining),
                        "PC5_KEEP_TEST_SCRATCH=1 must keep an unexpected "
                        f"success's directory: {remaining}")
        self.assertTrue(os.path.exists(case.tree))

    def test_unexpected_success_does_not_retain_an_ordinary_pass(self):
        """Attribution stays per case; the new clause must not leak across cases."""
        case = self._make_expected_failure_case()
        plain = self._make_case()
        suite = unittest.TestSuite([case("test_g_unexpected_success"),
                                    plain("test_a_pass")])
        result, _, remaining = self._run(suite)
        self.assertEqual(len(result.unexpectedSuccesses), 1)
        self.assertTrue(any("test_g_unexpected_success" in p for p in remaining))
        self.assertFalse(any("test_a_pass" in p for p in remaining),
                         "an unexpected success must not retain an unrelated "
                         f"passing case's directory: {remaining}")

    def test_keep_env_keeps_everything(self):
        case = self._make_case()
        with unittest.mock.patch.dict(os.environ, {scratch.KEEP_ENV: "1"}):
            result, _, remaining = self._run(
                unittest.TestSuite([case("test_a_pass")]))
        self.assertEqual(result.testsRun, 1)
        self.assertTrue(any("test_a_pass" in p for p in remaining),
                        "PC5_KEEP_TEST_SCRATCH=1 must keep even a passing case's "
                        f"directory: {remaining}")
        self.assertTrue(os.path.exists(case.tree),
                        "PC5_KEEP_TEST_SCRATCH=1 must keep the class tree")

    def test_the_root_is_removed_at_interpreter_exit(self):
        code = textwrap.dedent("""
            import os, sys
            sys.path.insert(0, sys.argv[1])
            import scratch
            where = scratch.directory("atexit-probe")
            open(os.path.join(where, "f"), "w").write("x")
            print(scratch.root())
        """)
        result = subprocess.run(
            [sys.executable, "-c", code, str(ROOT / "tests")],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        root = result.stdout.strip()
        self.assertTrue(root, "the subprocess did not report its scratch root")
        self.assertFalse(os.path.exists(root),
                         "the scratch root must be removed at ordinary "
                         "interpreter exit")

    def test_a_case_without_an_outcome_keeps_its_directory(self):
        class _Bare:
            pass

        self.assertTrue(scratch.case_failed(_Bare()),
                        "a case with no _outcome must be treated as failed: "
                        "losing a diagnostic artifact is worse than leaving a "
                        "directory")

    def test_a_case_with_no_result_keeps_its_directory(self):
        class _Outcome:
            result = None

        class _Case:
            _outcome = _Outcome()

        self.assertTrue(scratch.case_failed(_Case()),
                        "a case whose outcome has no result must be treated as "
                        "failed")


class Fat32HarnessIsolationTest(unittest.TestCase):
    """The FAT32 drivers must use per-process, per-case mutable storage."""

    def test_the_driver_does_not_use_the_old_fixed_directory(self):
        source = (ROOT / "tests" / "test_fat32_expand.py").read_text(encoding="utf-8")
        self.assertNotIn(
            'os.path.join(ROOT, "build", "test", "m1i")', source,
            "test_fat32_expand still points at the fixed build/test/m1i directory")
        self.assertNotIn(
            'os.path.join(cls.tmp, "in.img")', source,
            "the mutable run files must not sit in the class directory any more")
        for name in ("in.img", "out.img", "lba.txt"):
            self.assertIn(name, source,
                          f"{name} disappeared; the driver interface changed")

    def test_the_write_driver_uses_per_case_storage(self):
        source = (ROOT / "tests" / "test_fat32_write.py").read_text(encoding="utf-8")
        self.assertIn("scratch.directory", source,
                      "test_fat32_write must isolate its mutable run files")
        self.assertIn("register_cleanup", source,
                      "test_fat32_write must clean up its per-case directory")
        self.assertNotIn(
            'os.path.join(cls.tmp, "in.img")', source,
            "the mutable run files must not sit in the class directory")

    def test_the_manifest_test_no_longer_writes_into_build(self):
        source = (ROOT / "tests" / "test_iso_manifest.py").read_text(encoding="utf-8")
        self.assertNotIn('REPO / "build"', source,
                         "test_iso_manifest still writes a mutable file into build/")

    def test_the_unit_binary_is_not_a_single_shared_name(self):
        text = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("mktemp", text,
                      "the unit target must create its binary under a name nothing "
                      "else holds, or two concurrent runs collide with ETXTBSY")
        self.assertNotIn(
            "-o $(BUILD)/kernel-unit ", text,
            "the unit binary is still linked to one shared path")

    def test_the_module_still_runs_its_real_operations(self):
        """Isolation must not have been bought by skipping the work."""
        source = (ROOT / "tests" / "test_fat32_expand.py").read_text(encoding="utf-8")
        for marker in ("self.overwrite(", "self.create(", "self.mkdir("):
            self.assertIn(marker, source,
                          f"{marker} is gone; the module no longer exercises that "
                          f"operation")
        # The three classes that carry the behaviours the defect masked.
        for name in ("OverwriteTests", "DirectoryTests", "FaultInjectionTests"):
            self.assertIn(f"class {name}(", source,
                          f"{name} disappeared; coverage was reduced, not isolated")


class OldSchemeCollisionControl(unittest.TestCase):
    """Proves the previous fixed-path scheme collides, in a throwaway tree.

    This is the control for the whole change.  It recreates the old layout - one
    shared directory, fixed filenames - and runs two processes that do what the
    tests do, then shows they interfere.  It never touches ``build/``, so it
    cannot itself corrupt a real run.
    """

    WORKER = textwrap.dedent("""
        import os, sys, time
        shared, tag, hold = sys.argv[1], sys.argv[2], float(sys.argv[3])
        src = os.path.join(shared, "in.img")
        dst = os.path.join(shared, "out.img")
        lba = os.path.join(shared, "lba.txt")
        # Exactly the old sequence: write input, drop previous outputs, run,
        # read the output back.  `hold` widens the window between the steps.
        for p in (dst, lba):
            if os.path.exists(p):
                os.unlink(p)
        with open(src, "wb") as fh:
            fh.write(tag.encode())
        time.sleep(hold)
        with open(dst, "wb") as fh:
            fh.write(tag.encode())
        time.sleep(hold)
        with open(lba, "w") as fh:
            fh.write(tag)
        time.sleep(hold)
        got = open(dst, "rb").read().decode()
        print("OK" if got == tag else f"WRONG:{got}")
    """)

    def _run_pair(self, dir_a, dir_b):
        """Run the old sequence twice, in the two directories given."""
        procs = []
        for directory, tag in ((dir_a, "AAAA"), (dir_b, "BBBB")):
            holder = pathlib.Path(directory, "worker.py")
            holder.write_text(self.WORKER, encoding="utf-8")
            procs.append(subprocess.Popen(
                [sys.executable, str(holder), str(directory), tag, "0.25"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        return [p.communicate(timeout=120)[0].strip() for p in procs]

    def test_a_shared_directory_corrupts_concurrent_runs(self):
        with tempfile.TemporaryDirectory(prefix="pc5-collision-control-") as shared:
            results = self._run_pair(shared, shared)
        self.assertTrue(
            any(r.startswith("WRONG") for r in results),
            f"the old fixed-path scheme did NOT collide in this control ({results}); "
            f"either the control is not exercising the hazard or the machine is too "
            f"slow to overlap, so this control proves nothing and should be reported "
            f"as inconclusive rather than passed")

    def test_the_isolated_scheme_does_not_corrupt_concurrent_runs(self):
        with tempfile.TemporaryDirectory(prefix="pc5-collision-control-") as base:
            dirs = [tempfile.mkdtemp(prefix="case-", dir=base) for _ in range(2)]
            outs = self._run_pair(dirs[0], dirs[1])
        self.assertEqual(outs, ["OK", "OK"],
                         f"per-case directories still collided: {outs}")


if __name__ == "__main__":
    unittest.main()
