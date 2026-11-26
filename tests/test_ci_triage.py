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

import io
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

    def _run(self, cwd, root="tests", json_out=None, timeout=900, only=None):
        argv = [sys.executable, os.path.join(ROOT, "tools", "ci_triage.py"), "--root", root]
        if only:
            argv += ["--only", only]
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
            data = json.load(io.open(os.path.join(tmp, "triage.json"), encoding="utf-8"))
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
            data = json.load(io.open(os.path.join(tmp, "t.json"), encoding="utf-8"))
            self.assertTrue(data["stopped_at"].startswith("test_slow_"),
                            "a truncated walk must say where it stopped: %r" % data)
            self.assertLess(data["files_run"], data["files_total"])
            self.assertIn("budget exhausted", data["note"])


class OnlyFilterTests(unittest.TestCase):
    """`--only` narrows the walk to the modules a *given job* could have broken.

    The firmware job installs no virtualenv.  Before the filter existed it walked all 47
    modules anyway, every unicorn-based one failed on import, and the run reported "6 did
    not return 0" next to a real firmware failure that needed naming.  A triage that cannot
    tell "not applicable here" from "broken" is worse than no triage, so the selection is
    explicit in the note and in the JSON.
    """

    FILES = {
        "test_ovmf_harness.py": "import unittest\n\n\nclass A(unittest.TestCase):\n"
                                "    def test_a(self):\n        pass\n",
        "test_ovmf_gate_profiles.py": "import unittest\n\n\nclass B(unittest.TestCase):\n"
                                      "    def test_b(self):\n        pass\n",
        "test_unicorn_soft_int.py": "import unittest\n\n\nclass C(unittest.TestCase):\n"
                                    "    def test_c(self):\n        self.assertEqual(1, 2)\n",
    }

    def _run(self, cwd, root="tests", json_out=None, timeout=900, only=None):
        """The same invocation `RunTests` uses, with `--only` added.

        Duplicated rather than shared: a `TestCase` must not reach into a sibling class's
        private helper, which is the mistake that once made this file's runner die on its
        own `_outcome` attribute.
        """
        argv = [sys.executable, os.path.join(ROOT, "tools", "ci_triage.py"), "--root", root]
        if only:
            argv += ["--only", only]
        if json_out:
            argv += ["--json-out", json_out]
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)

    def _tree(self, tmp, bodies=None):
        tests = os.path.join(tmp, "tests")
        os.makedirs(tests)
        for name, body in (bodies or self.FILES).items():
            with open(os.path.join(tests, name), "w", encoding="utf-8") as handle:
                handle.write(body)
        return tests

    def test_the_filter_selects_by_substring_and_keeps_discovery_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tests = self._tree(tmp)
            self.assertEqual(ci_triage.test_files(tests),
                             ["test_ovmf_gate_profiles.py", "test_ovmf_harness.py",
                              "test_unicorn_soft_int.py"], "no filter means the whole set")
            self.assertEqual(ci_triage.test_files(tests, "ovmf"),
                             ["test_ovmf_gate_profiles.py", "test_ovmf_harness.py"],
                             "`--only ovmf` must keep only the ovmf modules, in the same "
                             "order discovery would use")
            self.assertEqual(ci_triage.test_files(tests, "ovmf_harness"),
                             ["test_ovmf_harness.py"], "a longer substring narrows further")
            self.assertEqual(ci_triage.test_files(tests, "unicorn,ovmf_harness"),
                             ["test_ovmf_harness.py", "test_unicorn_soft_int.py"],
                             "a comma-separated list is a union, still in order")

    def test_an_empty_filter_is_the_whole_set_not_the_empty_set(self):
        """`--only ""` or `--only ,` means "no filter", because that is what a caller means."""
        with tempfile.TemporaryDirectory() as tmp:
            tests = self._tree(tmp)
            every = ci_triage.test_files(tests)
            for blank in ("", " ", ",", " ,, "):
                self.assertEqual(ci_triage.test_files(tests, blank), every,
                                 "%r must not silently select nothing" % blank)

    def test_a_filtered_out_failure_is_reported_as_unexamined_not_as_a_pass(self):
        """The dangerous misreading is an empty triage on a red job."""
        with tempfile.TemporaryDirectory() as tmp:
            self._tree(tmp)
            done = self._run(tmp, json_out=os.path.join(tmp, "t.json"), only="ovmf")
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            with io.open(os.path.join(tmp, "t.json"), encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["nonzero"], 0, "the failing module was filtered out")
            self.assertEqual(data["files_run"], 2)
            self.assertEqual(data["files_found"], 3, "the note must reveal that a file "
                                                      "existed which was not walked")
            self.assertEqual(data["only"], "ovmf")
            self.assertIn("--only 'ovmf' matched 2 of 3", data["note"],
                          "an empty result is only honest next to the selection that made "
                          "it empty")
            self.assertNotIn("0 did not return 0\n", data["note"] + "\n")

    def test_a_filter_that_matches_nothing_runs_nothing_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._tree(tmp)
            done = self._run(tmp, json_out=os.path.join(tmp, "t.json"), only="no_such_module")
            self.assertEqual(done.returncode, 0,
                             "a mistyped selector must not become a second failure")
            self.assertIn("matched 0 of 3", done.stdout)
            self.assertIn("so nothing ran", done.stdout)
            with io.open(os.path.join(tmp, "t.json"), encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["results"], [])
            self.assertEqual(data["files_total"], 0)
            self.assertEqual(data["nonzero"], 0, "and it must not be dressed up as a verdict")

    def test_the_workflow_and_the_helper_agree_on_every_flag(self):
        """`--only` was passed by the workflow for one whole commit before it existed.

        The helper ignored it, exited 0, and printed one warning to a stderr line nobody
        can reach from the annotations endpoint.  Nothing caught that because nothing asked
        whether the two files agree, so this reads the flags out of the real workflow and
        checks them against the names `parse()` actually accepts.
        """
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed; the workflow would have to be grepped, "
                          "and a grep that misses a continuation line is what let the "
                          "mismatch through in the first place")
        wf = os.path.join(ROOT, ".github", "workflows", "build.yml")
        if not os.path.exists(wf):
            self.skipTest("no pipeline file in the tree")
        with open(wf, encoding="utf-8") as handle:
            doc = yaml.safe_load(handle)
        accepted = ci_triage.FLAGS | {"-h", "--help"}
        seen = 0
        for job_name, job in doc["jobs"].items():
            for step in job.get("steps", []):
                body = str(step.get("run", ""))
                if "tools/ci_triage.py" not in body:
                    continue
                toks = body.replace("\\", " ").split()
                start = toks.index([t for t in toks if t.endswith("ci_triage.py")][0]) + 1
                flags = [t for t in toks[start:] if t.startswith("--")]
                self.assertTrue(flags, "a triage invocation with no flags at all?")
                for flag in flags:
                    self.assertIn(flag, accepted,
                                  "%s passes %s, which ci_triage.parse() does not know"
                                  % (job_name, flag))
                seen += 1
        self.assertGreaterEqual(seen, 2,
                                "the walk found %d triage steps; a check that matches "
                                "nothing is not a check" % seen)


if __name__ == "__main__":
    unittest.main(verbosity=2)
