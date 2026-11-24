"""Tests for the per-file CI triage helper.

`tools/ci_triage.py` exists for one failure mode that `unittest discover` cannot
describe: an interpreter that *dies*.  A segfault leaves no `FAIL:` block, no tally and
no traceback -- only a shell exit code of 139 -- so the only way to say which module
broke is to run them separately and report the first death.

The property that makes it safe to put in a pipeline is that it cannot change the
pipeline's verdict.  These tests assert that directly, with a fixture that segfaults,
because a helper written to explain a failure became a second failure once already
(`tools/ci_report.py` documents how).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ci_triage  # noqa: E402

FIXTURES = {
    "test_passes.py": "import unittest\n"
                      "class P(unittest.TestCase):\n"
                      "    def test_a(self):\n"
                      "        pass\n",
    "test_fails.py": "import unittest\n"
                     "class F(unittest.TestCase):\n"
                     "    def test_no(self):\n"
                     "        self.assertEqual(1, 2)\n",
    "test_segv.py": "import os, signal, unittest\n"
                    "class S(unittest.TestCase):\n"
                    "    def test_boom(self):\n"
                    "        os.kill(os.getpid(), signal.SIGSEGV)\n",
    "not_a_test.py": "raise RuntimeError('discovery would never load this')\n",
    "test_skipped.py": "import unittest\n"
                       "class K(unittest.TestCase):\n"
                       "    @unittest.skip('deliberately not run')\n"
                       "    def test_x(self):\n"
                       "        pass\n",
}


class SelectionTests(unittest.TestCase):
    def test_only_test_modules_are_collected_in_discovery_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in FIXTURES.items():
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                    handle.write(body)
            self.assertEqual(ci_triage.test_files(tmp),
                             ["test_fails.py", "test_passes.py", "test_segv.py", "test_skipped.py"],
                             "`not_a_test.py` must not be loaded, and the rest must run in the "
                             "order `unittest discover` uses")

    def test_signal_exits_are_named_not_numbered(self):
        # named `_name_of`, never `_outcome`: `unittest.TestCase` keeps its own private
        # `_outcome` attribute, and shadowing it here breaks the test runner instead of
        # the code under test -- which is exactly what this line used to do
        name = ci_triage.outcome_name
        self.assertEqual(name(-11), "SIGSEGV")
        self.assertEqual(name(139), "SIGSEGV (128+11)")
        self.assertEqual(name(137), "SIGKILL (128+9)")
        self.assertEqual(name(0), "passed")
        # an unnamed non-zero must still be reported, not silently bucketed as a signal
        self.assertEqual(name(7), "exit 7")


class RunTests(unittest.TestCase):
    def _write_fixtures(self, tmp):
        tests = os.path.join(tmp, "tests")
        os.makedirs(tests)
        for name, body in FIXTURES.items():
            with open(os.path.join(tests, name), "w", encoding="utf-8") as handle:
                handle.write(body)
        return tests

    def _run(self, cwd, root="tests", json_out=None, timeout=900):
        argv = [sys.executable, os.path.join(ROOT, "tools", "ci_triage.py"), "--root", root]
        if json_out:
            argv += ["--json-out", json_out]
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)

    def test_a_missing_root_is_reported_and_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = self._run(tmp, root=os.path.join(tmp, "absent"))
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            self.assertIn("no test files", done.stdout)

    def test_a_crashing_file_is_named_and_the_helper_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_fixtures(tmp)
            done = self._run(tmp, json_out=os.path.join(tmp, "triage.json"))
            self.assertEqual(done.returncode, 0, done.stderr[-600:])
            self.assertIn("test_segv.py", done.stdout)
            self.assertIn("SIGSEGV", done.stdout)
            self.assertIn("test_fails.py", done.stdout)
            self.assertNotIn("not_a_test", done.stdout)
            data = json.load(open(os.path.join(tmp, "triage.json"), encoding="utf-8"))
            self.assertEqual(sorted(r["file"] for r in data["results"]),
                             ["test_fails.py", "test_segv.py"])
            self.assertEqual(data["files_total"], 4)
            self.assertEqual(data["nonzero"], 2)
            # `stopped_at` is the honest "this budget ran out" field: complete => empty
            self.assertEqual(data["stopped_at"], "")

    def test_a_skip_is_not_reported_as_a_failure(self):
        """Exit 0 with skips inside is a pass; the suite's own tally owns that nuance."""
        with tempfile.TemporaryDirectory() as tmp:
            tests = os.path.join(tmp, "tests")
            os.makedirs(tests)
            with open(os.path.join(tests, "test_skipped.py"), "w", encoding="utf-8") as handle:
                handle.write(FIXTURES["test_skipped.py"])
            done = self._run(tmp)
            self.assertEqual(done.returncode, 0)
            self.assertIn("0 did not return 0", done.stdout)

    def test_an_unwritable_json_target_is_a_warning_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_fixtures(tmp)
            locked = os.path.join(tmp, "locked")
            os.makedirs(locked)
            done = self._run(tmp, json_out=locked)
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            self.assertIn("could not write", done.stdout + done.stderr)

    def test_the_triage_is_read_only_with_respect_to_the_tree(self):
        """It runs tests in subprocesses; it must not write where a gate would look."""
        with open(os.path.join(ROOT, "tools", "ci_triage.py"), encoding="utf-8") as handle:
            script = handle.read()
        for forbidden in ('open("build/', "open('build/", "> build/", "shutil.rmtree",
                          "os.remove", "mkdir", "git "):
            self.assertNotIn(forbidden, script,
                             "the triage writes or deletes something a gate may depend on")

    def test_annotations_are_one_physical_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_fixtures(tmp)
            done = self._run(tmp)
            for line in done.stdout.splitlines():
                if line.startswith("::"):
                    self.assertNotIn("\r", line, "a folded annotation renders as its first "
                                                 "line only; use spaces")

    def test_the_budget_field_is_set_when_files_are_left_over(self):
        """`stopped_at` must distinguish 'all files were clean' from 'we never got there'.

        Without it, an empty `results` after a truncated walk would read as a pass,
        which is the exact class of false green this repository keeps refusing.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tests = os.path.join(tmp, "tests")
            os.makedirs(tests)
            for i in range(4):
                with open(os.path.join(tests, "test_slow_%d.py" % i), "w", encoding="utf-8") as h:
                    h.write("import time, unittest\n"
                            "class S(unittest.TestCase):\n"
                            "    def test_wait(self):\n"
                            "        time.sleep(6)\n")
            # the budget is passed to the child, not patched here: the walk runs in a
            # subprocess, so a value set on this process's module would never reach it
            argv = [sys.executable, os.path.join(ROOT, "tools", "ci_triage.py"),
                    "--root", "tests", "--budget", "8",
                    "--json-out", os.path.join(tmp, "t.json")]
            done = subprocess.run(argv, cwd=tmp, capture_output=True, text=True, timeout=600)
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            data = json.load(open(os.path.join(tmp, "t.json"), encoding="utf-8"))
            self.assertTrue(data["stopped_at"].startswith("test_slow_"),
                            "a truncated walk must say where it stopped: %r" % data)
            self.assertLess(data["files_run"], data["files_total"])
            self.assertIn("budget exhausted", data["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
