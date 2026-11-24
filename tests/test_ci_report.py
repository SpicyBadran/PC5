"""Tests for the CI failure reporter, and for its place in the pipeline.

`tools/ci_report.py` exists because a run that fails where nobody can read it is not
a diagnosis: GitHub serves raw job logs and uploaded artifacts from hosts outside this
repository's control, and a sandboxed environment may reach none of them, while the
Checks API annotations endpoint answers for any repository token.  The script turns a
gate's own log into annotations and into the job summary.

Three of its properties are what these tests are really about:

* it must never become a second failure -- the first version of this step did, with a
  `grep` that matched nothing under `pipefail`, and the job then reported two red steps
  while the real verdict stayed unread;
* it must quote the failure *block*, because the tally line says only that something
  broke, and it must not report a *passing* gate's tally as an error;
* a workflow command is a one-line protocol, so log content must not be able to forge
  a second annotation.

The wiring assertions parse the YAML rather than grepping it: "the reporter follows the
gate" is a statement about one job's step list, and both jobs share a file.
"""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ci_report  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "build.yml")
DORMANT = os.path.join(ROOT, "ci", "build.yml")

HOST_LOG = (
    "test_alpha ... ok\n"
    "test_beta ... ok\n"
    + "=" * 71 + "\n"
    "FAIL: test_moving_head (test_release_esp.ReleaseAttributionRebuild.test_moving_head)\n"
    + "-" * 70 + "\n"
    "Traceback (most recent call last):\n"
    '  File "/runner/tests/test_release_esp.py", line 700,\n'
    "    self.assertIn(needle, second.stdout)\n"
    "AssertionError: 'build record now names' is not found in : the image kept a label\n"
    + "-" * 70 + "\n"
    "Ran 1154 tests in 763.261s\n"
    "\n"
    "FAILED (failures=1)\n"
)


def pipeline_path():
    return WORKFLOW if os.path.exists(WORKFLOW) else DORMANT


def pipeline_text():
    path = pipeline_path()
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def load_workflow(test):
    if not os.path.exists(pipeline_path()):
        test.skipTest("no pipeline file in the tree")
    try:
        import yaml
    except ImportError:
        test.skipTest("PyYAML is not installed; these assertions need the workflow parsed "
                      "as YAML, not grepped, because a gate and its reporter can share a "
                      "file while belonging to different jobs")
    with open(pipeline_path(), encoding="utf-8") as handle:
        return yaml.safe_load(handle)


class ExtractionTests(unittest.TestCase):
    def test_the_failure_block_includes_the_assertion_not_just_its_heading(self):
        blocks, tally, marks = ci_report.extract(HOST_LOG)
        self.assertEqual(len(blocks), 1, "the failure block was not found at all")
        self.assertIn("FAIL: test_moving_head", blocks[0])
        self.assertIn("AssertionError", blocks[0])
        self.assertIn("Ran 1154 tests", blocks[0] + "\n".join(tally))
        # the tally is carried alongside, because `Ran N tests` is what distinguishes a
        # suite that ran from one that barely did
        self.assertEqual(tally, ["Ran 1154 tests in 763.261s", "FAILED (failures=1)"])
        self.assertTrue(marks)

    def test_a_passing_log_produces_nothing_and_no_annotation(self):
        text = "test_a ... ok\nRan 1154 tests in 400.0s\n\nOK (skipped=1)\n"
        blocks, tally, marks = ci_report.extract(text)
        self.assertEqual(blocks, [])
        self.assertEqual(marks, [])
        # A lone `OK` must not be reported as an error: this step runs after a *failed*
        # job, and the culprit is usually a step whose log looks fine, so pointing at a
        # green gate would send a reviewer to the wrong place.
        self.assertEqual(ci_report.annotations_for("build/logs/unit.log", text), [])
        self.assertEqual(ci_report.report_for("build/logs/unit.log", text), "")

    def test_a_make_failure_without_unittest_structure_still_reports(self):
        text = ("gcc -static -o build/kernel.elf\n"
                "make: *** [Makefile:210: build/kernel.elf] Error 1\n")
        blocks, tally, marks = ci_report.extract(text)
        self.assertEqual(blocks, [], "there is no unittest block here")
        self.assertEqual(len(marks), 1)
        annotations = ci_report.annotations_for("build/logs/iso.log", text)
        self.assertEqual(len(annotations), 1)
        self.assertIn("make: ***", annotations[0])

    def test_a_bare_failed_tally_is_still_a_verdict(self):
        text = "Ran 3 tests in 0.01s\n\nFAILED (errors=3)\n"
        annotations = ci_report.annotations_for("build/logs/psm.log", text)
        self.assertEqual(len(annotations), 1, "a `FAILED` tally is a verdict, not noise")
        self.assertIn("FAILED (errors=3)", annotations[0])


class AnnotationProtocolTests(unittest.TestCase):
    def test_an_annotation_is_one_line_and_log_content_cannot_forge_another(self):
        hostile = (HOST_LOG + "::error title=pwned::this line was written by the log\n"
                   "::notice title=also-pwned::and this one too\n")
        annotations = ci_report.annotations_for("build/logs/host.log", hostile)
        self.assertTrue(annotations)
        for line in annotations:
            self.assertNotIn("\n", line, "a workflow command must be exactly one line")
            self.assertLessEqual(len(line), 8 + ci_report.ANNOTATION_LIMIT + 64)
            self.assertEqual(line.count("::error title="), 1,
                             "one annotation carries a forged second command")
        # nothing beyond the failure block is quoted at all, so the injected lines stay
        # in the log on GitHub's side, where they are text and not commands
        report = ci_report.report_for("build/logs/host.log", hostile)
        self.assertNotIn("title=pwned", report)
        self.assertEqual(report.count("::error"), 0,
                         "a summary body must never carry a workflow command")

    def test_the_failure_block_survives_into_the_annotation(self):
        annotations = ci_report.annotations_for("build/logs/host.log", HOST_LOG)
        self.assertEqual(len(annotations), 1)
        body = annotations[0].split("::", 2)[-1]
        folded = body.replace("\r", "\n")
        self.assertIn("FAIL: test_moving_head", folded)
        self.assertIn("AssertionError", folded)
        self.assertEqual(body.count("\n"), 0, "carriage returns, not newlines")

    def test_annotations_are_capped(self):
        big = ("FAIL: test_x (t.T.test_x)\n" + ("assertion noise\n" * 4000)
               + "Ran 1 tests in 0.00s\n\nFAILED (failures=1)\n")
        annotations = ci_report.annotations_for("build/logs/host.log", big)
        self.assertTrue(annotations)
        for line in annotations:
            self.assertLessEqual(len(line), 8 + ci_report.ANNOTATION_LIMIT + 64)


class AlwaysSucceedsTests(unittest.TestCase):
    """The step describes a failure; it must never add one."""

    def _run(self, cwd, summary_path=None):
        env = dict(os.environ)
        env.pop("GITHUB_STEP_SUMMARY", None)
        if summary_path:
            env["GITHUB_STEP_SUMMARY"] = summary_path
        return subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ci_report.py")],
                              cwd=cwd, env=env, capture_output=True, text=True, timeout=600)

    def test_exit_zero_with_no_logs_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = self._run(tmp)
            self.assertEqual(done.returncode, 0, done.stderr[-500:])
            self.assertIn("No gate log was written", done.stdout)
            self.assertIn("::error title=no gate log::", done.stdout)

    def test_exit_zero_when_a_log_is_empty_a_directory_or_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "build", "logs")
            os.makedirs(logs)
            with open(os.path.join(logs, "empty.log"), "w", encoding="utf-8"):
                pass
            os.makedirs(os.path.join(logs, "adir.log"))
            with open(os.path.join(logs, "binary.log"), "wb") as handle:
                handle.write(b"\x00\x01\xff FAIL: not text \xfe")
            done = self._run(tmp)
            self.assertEqual(done.returncode, 0, done.stderr[-500:])

    def test_exit_zero_when_the_summary_is_unwritable(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "build", "logs")
            os.makedirs(logs)
            with open(os.path.join(logs, "host.log"), "w", encoding="utf-8") as handle:
                handle.write(HOST_LOG)
            locked = os.path.join(tmp, "locked")
            os.makedirs(locked)  # a directory cannot be opened for append
            done = self._run(tmp, summary_path=locked)
            self.assertEqual(done.returncode, 0, done.stderr[-500:])

    def test_exit_zero_when_a_log_is_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "build", "logs")
            os.makedirs(logs)
            blocked = os.path.join(logs, "blocked.log")
            with open(blocked, "w", encoding="utf-8") as handle:
                handle.write(HOST_LOG)
            os.chmod(blocked, 0)
            try:
                done = self._run(tmp)
            finally:
                os.chmod(blocked, 0o600)
            self.assertEqual(done.returncode, 0, done.stderr[-500:])
            # root can read a 0600-less file, so the assertion is about the exit code,
            # which is the property under test; a non-zero exit is the bug
            self.assertNotIn("Traceback (most recent call last)", done.stdout.split(
                "### failed gate")[-1] if "### failed gate" in done.stdout else done.stdout)

    def test_the_summary_is_appended_to_not_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "build", "logs")
            os.makedirs(logs)
            with open(os.path.join(logs, "host.log"), "w", encoding="utf-8") as handle:
                handle.write(HOST_LOG)
            summary = os.path.join(tmp, "sum.md")
            with open(summary, "w", encoding="utf-8") as handle:
                handle.write("### Genuine OVMF boot\n- written by the gate itself\n")
            done = self._run(tmp, summary_path=summary)
            self.assertEqual(done.returncode, 0, done.stderr[-500:])
            with open(summary, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("written by the gate itself", text,
                          "the reporter replaced the summary the job had already written")
            self.assertIn("failed gate:", text)
            self.assertIn("AssertionError", text, "the summary carries the tally but no reason")
            self.assertIn("runner environment", text)


class PipelineWiringTests(unittest.TestCase):
    """The script is only useful if the pipeline is wired to it."""

    def setUp(self):
        self.text = pipeline_text()
        if not self.text:
            self.skipTest("no pipeline file in the tree")
        self.yaml = load_workflow(self)

    def test_both_failing_jobs_call_the_reporter(self):
        calls = {}
        for name, job in self.yaml["jobs"].items():
            hits = [i for i, st in enumerate(job.get("steps", []))
                    if "tools/ci_report.py" in str(st.get("run", ""))]
            if hits:
                calls[name] = hits
        self.assertEqual(sorted(calls), ["build", "ovmf"],
                         "the reporter must run in the build job and in the firmware job, "
                         "and nowhere a green job would execute it")
        for name, hits in calls.items():
            self.assertEqual(len(hits), 1, "%s should carry exactly one reporter step" % name)

    def test_the_reporter_is_guarded_by_failure_and_only_artifacts_follow_it(self):
        for name in ("build", "ovmf"):
            steps = self.yaml["jobs"][name]["steps"]
            idx = [i for i, st in enumerate(steps)
                   if "tools/ci_report.py" in str(st.get("run", ""))][0]
            self.assertEqual(steps[idx].get("if"), "failure()",
                             "%s's reporter must run only once a step has already failed" % name)
            for later in steps[idx + 1:]:
                self.assertEqual(later.get("if"), "always()",
                                 "%s: only artifact uploads may follow the reporter, so that it "
                                 "sees every gate log and no gate sees this one" % name)

    def test_every_gate_is_followed_by_the_reporter_inside_its_own_job(self):
        """A reporter placed before a gate, or in another job, would have nothing to read."""
        gates = ("make unit", "unittest discover", "make roadmap", "make psm",
                 "make docs-check", "reproducible", "make ovmf-test")
        seen = 0
        for name, job in self.yaml["jobs"].items():
            steps = [str(st.get("run", "")) + " " + str(st.get("name", ""))
                     for st in job.get("steps", [])]
            reporter = [i for i, body in enumerate(steps) if "tools/ci_report.py" in body]
            for i, body in enumerate(steps):
                if not reporter or i >= max(reporter):
                    continue
                if not any(g in body for g in gates):
                    continue
                seen += 1
                self.assertTrue([r for r in reporter if r > i],
                                "job %s runs a gate with no reporter after it" % name)
        self.assertGreaterEqual(seen, 7,
                                "the walk matched %d gates, which is too few to be a real "
                                "check -- did the step bodies stop containing their make calls?"
                                % seen)

    def test_the_reporter_writes_no_log_a_gate_might_need(self):
        with open(os.path.join(ROOT, "tools", "ci_report.py"), encoding="utf-8") as handle:
            script = handle.read()
        for forbidden in ('open("build/', "open('build/", "> build/", "mkdir", "shutil", "rm -"):
            self.assertNotIn(forbidden, script,
                             "the reporter touches build/, which could disturb a later gate")

    def test_probes_do_not_interpolate_runner_state_into_a_shell(self):
        """The probes are shell, so they may not contain what the shell would expand.

        A literal `;` is fine and unavoidable -- one probe passes a small program to
        `python3 -c` -- because everything here is a constant written in this
        repository.  What must never appear is a construct whose *value* comes from the
        runner: `$` (parameter, command or arithmetic expansion) and backticks.
        """
        for cmd in ci_report.PROBES:
            self.assertNotIn("$", cmd, "a probe must not interpolate runner state into a shell")
            self.assertNotIn("`", cmd)
            self.assertTrue(cmd.split()[0] in ("python3", "make", "gcc", "ld", "git", "ls",
                                                "uname", "node"), cmd)

    def test_the_script_is_used_from_a_step_that_needs_no_extra_dependency(self):
        """`python3 tools/ci_report.py`, stdlib only: the step must work on a runner
        before any `pip install`, or a provisioning failure would hide the diagnosis."""
        with open(os.path.join(ROOT, "tools", "ci_report.py"), encoding="utf-8") as handle:
            script = handle.read()
        for line in script.splitlines():
            if line.startswith("import ") or line.startswith("from "):
                module = line.split()[1].split(".")[0]
                self.assertIn(module, ("glob", "os", "re", "sys", "json"),
                              "the reporter imports %r, which is not in the standard library"
                              % module)


if __name__ == "__main__":
    unittest.main(verbosity=2)
