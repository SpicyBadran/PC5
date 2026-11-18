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


class ContainmentTest(unittest.TestCase):
    """A scratch directory must never land outside the scratch root.

    Cleanup and ``atexit`` remove the root only.  Before containment was enforced,
    ``scratch.directory("..", "elsewhere")`` built a path beside the root, created
    it, and left it there for the lifetime of the machine: no test failed, nothing
    reported it, and ``PC5_KEEP_TEST_SCRATCH`` could not account for it.  These
    tests exist because that failure is silent by construction.

    The rejected inputs are checked by kind, not by one lucky string, and the
    accepted ones are here so that "reject traversal" cannot be satisfied by
    rejecting everything.
    """

    def setUp(self):
        self.root = pathlib.Path(scratch.root()).resolve()

    def assertInsideRoot(self, path):
        resolved = pathlib.Path(path).resolve()
        self.assertTrue(
            resolved.is_relative_to(self.root),
            f"{path} resolves to {resolved}, outside the scratch root {self.root}")

    def test_traversal_components_are_rejected(self):
        for parts in (
            ("..",),
            ("..", "escaped"),
            ("..", "..", "escaped"),
            ("owner", "..", "escaped"),
            ("../escaped",),
            ("owner/../../escaped",),
            ("./relative",),
            ("",),
        ):
            with self.subTest(parts=parts):
                with self.assertRaises(ValueError, msg=f"{parts!r} was accepted"):
                    scratch.directory(*parts)

    def test_absolute_and_drive_paths_are_rejected(self):
        for parts in (("/tmp/absolute",), (os.sep + "absolute",)):
            with self.subTest(parts=parts):
                with self.assertRaises(ValueError, msg=f"{parts!r} was accepted"):
                    scratch.directory(*parts)

    def test_a_nul_component_is_rejected(self):
        with self.assertRaises(ValueError):
            scratch.directory("a\x00b")

    def test_non_string_components_are_rejected(self):
        with self.assertRaises(TypeError):
            scratch.directory(b"bytes")

    def test_a_rejected_traversal_creates_nothing(self):
        """The refusal must happen before anything is created on disk."""
        sibling = self.root.parent / f"{self.root.name}-sibling-probe"
        self.assertFalse(sibling.exists(), "fixture precondition")
        try:
            with self.assertRaises(ValueError):
                scratch.directory("..", sibling.name)
        finally:
            self.assertFalse(
                sibling.exists(),
                f"rejecting the traversal still created {sibling}")
        self.assertFalse(
            sibling.exists(), "a directory was created outside the scratch root")

    def test_repeated_rejection_never_creates_an_outside_directory(self):
        """Ten attempts in a row, since a guard that fires once may not fire again."""
        # Names are unique to this process, so debris left by an unrelated run (or
        # by a previous negative-control experiment) cannot make this fail.
        marker = f"escape-probe-{os.getpid()}-"
        for index in range(10):
            with self.assertRaises(ValueError):
                scratch.directory("..", f"{marker}{index}")
        outside = [p for p in self.root.parent.iterdir()
                   if p.name.startswith(marker)]
        self.assertEqual(outside, [], f"escaped directories were created: {outside}")

    def test_a_normal_prefix_still_works(self):
        """``prefix`` is public API; guarding it must not remove it."""
        path = scratch.directory("containment-prefix", prefix="case-")
        self.assertInsideRoot(path)
        self.assertEqual(
            pathlib.Path(path).parent.resolve(),
            (self.root / "containment-prefix").resolve(),
            "the prefix must name the leaf, not a directory")
        self.assertTrue(pathlib.Path(path).name.startswith("case-"),
                        "the prefix was not honoured")
        self.assertTrue(os.path.isdir(path))

    def test_a_default_prefix_still_works(self):
        path = scratch.directory("containment-prefix")
        self.assertInsideRoot(path)
        self.assertTrue(pathlib.Path(path).name.startswith("case-"))

    def test_traversal_prefixes_are_rejected(self):
        """The prefix is joined onto the parent exactly as a part is."""
        for prefix in ("..", "../", "../escaped-", "../../escaped-",
                       "..{}..{}escaped-".format(os.sep, os.sep),
                       ".", "./escaped-"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError, msg=f"{prefix!r} was accepted"):
                    scratch.directory("containment-prefix", prefix=prefix)

    def test_a_deeply_nested_parent_cannot_be_escaped_by_a_prefix(self):
        """Enough traversal to climb out of a nested parent, not just the root.

        With parts three levels deep, a single ``..`` only reaches a directory that
        is still inside the root, so the test uses enough components to clear the
        root regardless of depth.  A guard that only counted one level would pass
        the shallower case and fail this one.
        """
        parts = ("containment-deep", "level-a", "level-b", "level-c")
        depth = len(parts) + 4
        prefix = (".." + os.sep) * depth + "escaped-deep-"
        with self.assertRaises(ValueError):
            scratch.directory(*parts, prefix=prefix)

    def test_absolute_prefixes_are_rejected(self):
        for prefix in (os.sep + "absolute-", os.path.abspath(os.sep)):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError, msg=f"{prefix!r} was accepted"):
                    scratch.directory("containment-prefix", prefix=prefix)

    def test_embedded_separators_in_a_prefix_are_rejected(self):
        separators = {os.sep} | ({os.altsep} if os.altsep else set())
        for separator in sorted(separators):
            for prefix in (f"a{separator}b-", f"a{separator}..{separator}b-"):
                with self.subTest(prefix=prefix):
                    with self.assertRaises(ValueError, msg=f"{prefix!r} was accepted"):
                        scratch.directory("containment-prefix", prefix=prefix)

    def test_a_nul_or_non_string_prefix_is_rejected(self):
        with self.assertRaises(ValueError):
            scratch.directory("containment-prefix", prefix="a\x00b-")
        with self.assertRaises(TypeError):
            scratch.directory("containment-prefix", prefix=b"bytes-")

    def test_a_rejected_prefix_creates_nothing(self):
        """Refusal must happen before ``mkdtemp``, not after it.

        This is the whole point of the finding: a post-creation check would raise
        the same exception and still leave the escaped directory on disk.
        """
        marker = f"prefix-probe-{os.getpid()}-"
        beside = self.root.parent / f"{marker}sibling"
        absolute = self.root.parent / f"{marker}absolute"
        self.assertFalse(beside.exists(), "fixture precondition")
        self.assertFalse(absolute.exists(), "fixture precondition")
        try:
            with self.assertRaises(ValueError):
                scratch.directory("containment-prefix", prefix=f"..{os.sep}{beside.name}-")
            with self.assertRaises(ValueError):
                scratch.directory("containment-prefix", prefix=str(absolute) + os.sep)
            self.assertFalse(beside.exists(),
                             f"rejecting the prefix still created {beside}")
            self.assertEqual(
                [p for p in self.root.parent.iterdir() if p.name.startswith(marker)], [],
                "rejecting the prefix created a directory outside the scratch root")
        finally:
            shutil.rmtree(beside, ignore_errors=True)
            shutil.rmtree(absolute, ignore_errors=True)

    def test_repeated_rejected_prefixes_never_create_anything(self):
        """Ten attempts, since a guard that fires once may not fire again."""
        marker = f"prefix-repeat-{os.getpid()}-"
        for index in range(10):
            with self.assertRaises(ValueError):
                scratch.directory("containment-prefix", prefix=f"..{os.sep}{marker}{index}-")
        outside = [p for p in self.root.parent.iterdir() if p.name.startswith(marker)]
        self.assertEqual(outside, [], f"escaped directories were created: {outside}")

    def test_nested_parts_with_a_prefix_still_work(self):
        path = scratch.directory("containment", "NestedTests", "test_case",
                                 prefix="nested-")
        self.assertInsideRoot(path)
        self.assertEqual(
            pathlib.Path(path).parent.resolve(),
            (self.root / "containment" / "NestedTests" / "test_case").resolve(),
            "the legitimate layout must be preserved exactly")
        self.assertTrue(pathlib.Path(path).name.startswith("nested-"))

    def test_nested_owners_still_work(self):
        path = scratch.directory("containment", "NestedTests", "test_case")
        self.assertInsideRoot(path)
        self.assertEqual(
            pathlib.Path(path).parent.resolve(),
            (self.root / "containment" / "NestedTests" / "test_case").resolve(),
            "the legitimate layout must be preserved exactly")
        self.assertTrue(os.path.isdir(path))

    def test_dotted_names_that_are_not_traversal_still_work(self):
        """``a..b`` and ``..a`` are ordinary names; only ``..`` is traversal."""
        for name in ("a..b", "..a", "a.", "with space", "unicode-üñï"):
            with self.subTest(name=name):
                path = scratch.directory("containment-dotted", name)
                self.assertInsideRoot(path)

    def test_every_created_directory_is_inside_the_root(self):
        made = [scratch.directory("containment", f"case-{i}") for i in range(5)]
        for path in made:
            self.assertInsideRoot(path)

    @unittest.skipUnless(hasattr(os, "symlink"), "platform has no symlinks")
    def test_a_symlinked_component_cannot_be_used_to_escape(self):
        """Containment is checked on resolved paths, so a symlink cannot defeat it.

        A string-prefix check would pass here: the path *text* starts with the root
        even though the write lands outside it.
        """
        outside = self.root.parent / f"{self.root.name}-symlink-target"
        outside.mkdir()
        link = self.root / "linked"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"cannot create symlinks here: {error}")
        try:
            with self.assertRaises(ValueError):
                scratch.directory("linked", "escaped-through-symlink")
            self.assertEqual(
                list(outside.iterdir()), [],
                "a write escaped the root through a symlinked component")
        finally:
            link.unlink(missing_ok=True)
            shutil.rmtree(outside, ignore_errors=True)


class DescriptorRelativeCreationTest(unittest.TestCase):
    """E1: the create-time race, exercised deterministically rather than by timing.

    The vulnerable shape was ``check -> create-by-path``, so a component replaced
    *between* the two made the create land outside the root.  These tests drive the
    creation boundary through a hook, so they fail against that shape every time and
    pass against descriptor-relative creation every time.  Nothing here sleeps or
    races threads: a probabilistic test would prove nothing and flake.
    """

    def setUp(self):
        self.root = pathlib.Path(scratch.root()).resolve()
        self.outside = self.root.parent / f"{self.root.name}-e1-target"
        self.outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, self.outside, True)

    def _swap_component(self, name="owner"):
        """Replace a scratch component with a symlink to the outside target."""
        victim = self.root / name
        if victim.is_symlink() or victim.is_file():
            victim.unlink()
        elif victim.is_dir():
            shutil.rmtree(victim)
        os.symlink(self.outside, victim)

    def _remove_component(self, name):
        """Remove a component whether it is a symlink, a file or a directory."""
        victim = self.root / name
        if victim.is_symlink() or victim.is_file():
            victim.unlink()
        elif victim.is_dir():
            shutil.rmtree(victim, ignore_errors=True)

    def test_support_probe_is_true_here(self):
        """The premise of every test below: this platform can do it safely."""
        self.assertTrue(
            scratch._secure_creation_supported(),
            "descriptor-relative creation is reported unavailable on this platform")

    def test_support_probe_survives_a_patched_os_mkdir(self):
        """The probe must answer about the platform, not about a test double.

        ``os.supports_dir_fd`` compares function objects by identity, so a naive
        probe reports "unsupported" as soon as ``os.mkdir`` is replaced - which
        would silently disable the containment path inside these very tests.
        """
        original = os.mkdir
        try:
            os.mkdir = lambda *a, **k: original(*a, **k)
            self.assertTrue(scratch._secure_creation_supported())
        finally:
            os.mkdir = original

    def test_a_component_swapped_at_the_creation_boundary_cannot_escape(self):
        """E1: swap the parent at the leaf ``mkdir``, which is the exact window."""
        original = os.mkdir
        fired = []

        def hook(path, mode=0o777, *, dir_fd=None):
            if dir_fd is not None and str(path).startswith("case-") and not fired:
                fired.append(path)
                self._swap_component("owner")
            return original(path, mode, dir_fd=dir_fd)

        os.mkdir = hook
        try:
            with self.assertRaises((OSError, ValueError)):
                scratch.directory("owner")
        finally:
            os.mkdir = original

        self.assertTrue(fired, "the hook never reached the creation boundary")
        self.assertEqual(
            list(self.outside.iterdir()), [],
            "E1: a directory was created outside the scratch root")

    def test_a_component_swapped_before_the_walk_is_refused(self):
        """A symlinked component is refused, and the outside target stays empty."""
        self._swap_component("owner")
        with self.assertRaises(ValueError, msg="a symlinked component was followed"):
            scratch.directory("owner")
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_symlinked_component_is_refused_for_nested_parts_too(self):
        (self.root / "a").mkdir(exist_ok=True)
        self._swap_component("a")
        with self.assertRaises(ValueError):
            scratch.directory("a", "b", "c")
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_normal_nested_creation_still_works(self):
        path = scratch.directory("drt", "Nested", "case", prefix="nested-")
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(pathlib.Path(path).name.startswith("nested-"))
        self.assertEqual(
            pathlib.Path(path).parent.resolve(),
            (self.root / "drt" / "Nested" / "case").resolve())

    def test_repeated_creation_under_one_owner_is_distinct(self):
        first = scratch.directory("drt", "Same")
        second = scratch.directory("drt", "Same")
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.isdir(first) and os.path.isdir(second))

    def test_descriptors_are_not_leaked(self):
        """A rejected call must close what it opened (``finally`` on every path).

        Runs in its own owner name and removes the injected symlink afterwards, so a
        failure here cannot poison the shared scratch root for later cases.
        """
        if not os.path.isdir("/proc/self/fd"):
            self.skipTest("no /proc/self/fd on this platform")
        owner = "leak-probe"
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(20):
            scratch.directory(owner, "ok")
        self._swap_component(owner)
        self.addCleanup(self._remove_component, owner)
        for _ in range(20):
            with self.assertRaises(ValueError):
                scratch.directory(owner)
        after = len(os.listdir("/proc/self/fd"))
        self.assertLessEqual(
            after, before + 4,
            f"descriptor count grew from {before} to {after}: a descriptor leaked")

    def test_unexpected_errors_are_not_masked_as_containment_rejections(self):
        """``PermissionError`` is a real problem and must surface as itself.

        Translating every ``OSError`` into ``ValueError`` would hide an unwritable
        scratch root behind a message about symlinks.
        """
        target = self.root / "unwritable"
        target.mkdir(exist_ok=True)
        os.chmod(target, 0o500)
        self.addCleanup(os.chmod, target, 0o700)
        if os.geteuid() == 0:
            self.skipTest("root bypasses permission bits")
        with self.assertRaises(PermissionError, msg="an OS error was masked"):
            scratch.directory("unwritable", "child")

    def test_the_refusal_message_names_the_missing_capability(self):
        """If the platform cannot do this safely, it says so instead of degrading."""
        original = scratch._secure_creation_supported
        scratch._secure_creation_supported = lambda: False
        try:
            with self.assertRaises(RuntimeError) as caught:
                scratch.directory("anything")
        finally:
            scratch._secure_creation_supported = original
        self.assertIn("descriptor-relative", str(caught.exception))
        self.assertFalse(
            (self.root / "anything").exists(),
            "a refused call created a directory anyway")


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

    def test_cleanup_ordering_decides_whether_evidence_survives(self):
        """The LIFO hazard documented on ``register_cleanup``.

        ``addCleanup`` runs functions in reverse registration order, and the
        retention decision is taken when the scratch cleanup runs.  So the same
        raising cleanup on the same passing test body retains evidence or
        destroys it depending only on registration order:

        * scratch cleanup registered **first** -> runs last -> sees the error the
          other cleanup recorded -> retains;
        * scratch cleanup registered **last** -> runs first -> sees a pass ->
          removes, and the error is recorded afterwards.

        Both halves are asserted, because only the pair shows that the ordering
        is what decides.  This is a documentation and guard test: it does not
        claim that evidence is always retained, it pins the requirement that
        callers register the scratch cleanup first.
        """

        def make_case(register_scratch_first):
            # Distinct owners per half: the first half deliberately leaves its
            # evidence behind, and the second half must not be able to mistake it
            # for its own.
            owner = "safe_order" if register_scratch_first else "hazardous_order"

            class _Case(unittest.TestCase):
                def test_body_passes(self):
                    self.work = scratch.directory("lifecycle", owner,
                                                  "test_body_passes")
                    pathlib.Path(self.work, "in.img").write_bytes(b"payload")

                    def raising_cleanup():
                        raise RuntimeError("cleanup boom")

                    if register_scratch_first:
                        scratch.register_cleanup(self, self.work)
                        self.addCleanup(raising_cleanup)
                    else:
                        self.addCleanup(raising_cleanup)
                        scratch.register_cleanup(self, self.work)

            return _Case

        # Safe order: scratch cleanup registered first, so it runs last.
        case = make_case(True)
        result, _, remaining = self._run(
            unittest.TestSuite([case("test_body_passes")]))
        self.assertEqual(len(result.errors), 1,
                         "the raising cleanup must be recorded as an error")
        self.assertTrue(any("safe_order" in p for p in remaining),
                        "registered first, the scratch cleanup runs last and "
                        f"must retain the evidence: {remaining}")

        # Hazardous order: scratch cleanup registered last, so it runs first.
        case = make_case(False)
        result, _, remaining = self._run(
            unittest.TestSuite([case("test_body_passes")]))
        self.assertEqual(len(result.errors), 1,
                         "the raising cleanup must still be recorded as an error")
        self.assertFalse(any("hazardous_order" in p for p in remaining),
                         "registered last, the scratch cleanup runs before the "
                         "raising one; this test records that known limitation "
                         f"rather than pretending it is fixed: {remaining}")

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
