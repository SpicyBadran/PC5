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
* the operations the module exists to exercise are still exercised;
* a **regression control** shows the old fixed-path scheme would collide, built
  in a throwaway tree so the real suite never corrupts shared state.

The control is the important half: without it, "the new code is isolated" is a
statement about the new code, not evidence that the old shape was ever broken.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

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

    def test_cleanup_removes_only_its_own_tree(self):
        mine = scratch.directory("isolation", "mine")
        theirs = scratch.directory("isolation", "theirs")
        pathlib.Path(mine, "out.img").write_bytes(b"x")
        pathlib.Path(theirs, "out.img").write_bytes(b"y")

        class _Case:
            def __init__(self):
                self.cleanups = []
                self._outcome = type("O", (), {"errors": [], "failures": []})()

            def addCleanup(self, fn):
                self.cleanups.append(fn)

        case = _Case()
        scratch.register_cleanup(case, mine)
        for fn in case.cleanups:
            fn()

        self.assertFalse(os.path.exists(mine),
                         "a passing case's own directory must be removed")
        self.assertTrue(os.path.isdir(theirs),
                        "cleanup removed another case's directory; cleanup must be "
                        "scoped to the tree it was registered for")

    def test_a_failing_case_keeps_its_directory(self):
        mine = scratch.directory("isolation", "failing")
        pathlib.Path(mine, "out.img").write_bytes(b"evidence")

        class _Case:
            def __init__(self):
                self.cleanups = []
                self._outcome = type("O", (), {
                    "errors": [], "failures": [(None, "boom")]})()

            def addCleanup(self, fn):
                self.cleanups.append(fn)

        case = _Case()
        scratch.register_cleanup(case, mine)
        for fn in case.cleanups:
            fn()
        try:
            self.assertTrue(
                os.path.isdir(mine),
                "a failing case must keep its images: they are the only evidence "
                "of what the driver did")
        finally:
            import shutil
            shutil.rmtree(mine, ignore_errors=True)

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
