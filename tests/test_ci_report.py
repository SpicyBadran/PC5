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


#: The ovmf job's log, as run `45338046314` left it: the harness raised outside `unittest`,
#: so there is no `FAIL:` block, and the two lines the old reporter kept -- the `Traceback`
#: header and the `make:` status -- name neither the error nor its site.  Everything between
#: them is what the annotation channel was missing.
CRASH_LOG = (
    "python3 tests/ovmf_boot.py --iso build/pc5.iso --efi-image build/pc5-esp.img "
    "--kernel build/kernel64.bin --secure-boot\n"
    "Traceback (most recent call last):\n"
    '  File "/home/runner/work/pc5/pc5/tests/ovmf_boot.py", line 1729, in _make_medium\n'
    "    subprocess.run(command, check=True, capture_output=True)\n"
    '  File "/usr/lib/python3.12/subprocess.py", line 571, in run\n'
    "    raise CalledProcessError(retcode, process.args,\n"
    "subprocess.CalledProcessError: Command '['python3', 'tools/mkiso.py']' returned "
    "non-zero exit status 1.\n"
    "make: *** [Makefile:637: ovmf-test] Error 1\n"
)

#: The same crash with the exception line missing -- what a log truncated by a signal looks
#: like.  There is a traceback and no cause, and the reporter must say so instead of
#: promoting the `make:` line into the thing that raised.
STUMPED_LOG = ("Traceback (most recent call last):\n"
               '  File "tests/ovmf_boot.py", line 1907, in main\n'
               "    shutil.copyfile(found[\"vars\"], cold_vars)\n"
               "make: *** [Makefile:637: ovmf-test] Error 2\n")


class TracebackCauseTests(unittest.TestCase):
    """The cause of a harness crash has to survive into the one channel that is reachable.

    `extract()` keeps lines that *start* with a known prefix, which is how a Python
    traceback lost every frame and its exception line: the header (`Traceback ...`) and the
    make status (`make: ...`) are prefixed, `subprocess.CalledProcessError: ...` is not.  So
    a red firmware job arrived as "Traceback / Error 1" and no endpoint in reach could say
    more.  These tests pin the fix and, just as important, pin the honesty limits: a log that
    does not contain an exception line must not be given one.
    """

    def test_the_exception_line_and_its_site_reach_the_annotation(self):
        annotations = ci_report.annotations_for("build/logs/ovmf-test.log", CRASH_LOG)
        self.assertTrue(annotations, "a crash log must not be silent")
        first = annotations[0]
        self.assertIn("subprocess.CalledProcessError", first,
                      "the exception type is the cause; it must lead the annotation")
        self.assertIn("returned non-zero exit status 1", first)
        self.assertIn("tests/ovmf_boot.py", first.replace("/home/runner/work/pc5/pc5/", ""),
                      "the innermost frame says where it was raised")
        self.assertIn("make: *** [Makefile:637: ovmf-test] Error 1", first,
                      "and the exit status still travels with it")

    def test_the_crash_annotation_is_added_to_the_marker_line_not_substituted(self):
        """A new channel may not become the only channel: nothing already reported vanishes."""
        before = [l for l in CRASH_LOG.splitlines() if l.startswith(ci_report.MARK_PREFIXES)]
        annotations = ci_report.annotations_for("build/logs/ovmf-test.log", CRASH_LOG)
        self.assertGreaterEqual(len(annotations), 2,
                                "the marker scan that existed before must still be there")
        joined = "\n".join(annotations)
        for line in before:
            self.assertIn(line, joined, "%r was dropped" % line[:40])

    def test_a_traceback_already_inside_a_failure_block_is_not_quoted_twice(self):
        """`FAIL:` blocks carry their own exception line; duplicating it is not evidence."""
        annotations = ci_report.annotations_for("build/logs/host.log", HOST_LOG)
        self.assertEqual(len(annotations), 1,
                         "HOST_LOG's AssertionError sits in the block that quotes it, so the "
                         "traceback path must add a second copy of nothing")
        self.assertEqual(annotations[0].count("AssertionError"), 1)
        self.assertEqual(ci_report.unquoted_tracebacks(HOST_LOG, ci_report.extract(HOST_LOG)[0]),
                         [], "the block already quotes that AssertionError, so the traceback "
                            "path must not add a second copy of it")

    def test_a_truncated_traceback_says_it_has_no_exception_rather_than_inventing_one(self):
        found = ci_report.tracebacks(STUMPED_LOG)
        self.assertEqual(len(found), 1)
        exception, frame = found[0]
        self.assertIn("no exception line", exception,
                      "the honest reading is that the log ends there: %r" % exception)
        self.assertNotIn("Error 2", exception,
                         "`make: ... Error 2` is an exit status, not the exception")
        self.assertIn("shutil.copyfile", frame,
                      "the statement that raised is the most useful half of the frame")
        self.assertIn("tests/ovmf_boot.py", frame, "and the harness's own file, not a stdlib one")
        annotation = ci_report.annotations_for("build/logs/ovmf-test.log", STUMPED_LOG)[0]
        self.assertIn("no exception line", annotation)
        self.assertIn("Error 2", annotation, "and the exit status stays, unadorned")

    def test_the_live_exception_of_a_chained_traceback_is_the_last_one(self):
        text = ("Traceback (most recent call last):\n"
                '  File "a.py", line 1, in f\n    x()\n'
                "FileNotFoundError: no qemu on PATH\n"
                "During handling of the above exception, another exception occurred:\n"
                "\n"
                "Traceback (most recent call last):\n"
                '  File "b.py", line 9, in g\n    y()\n'
                "RuntimeError: firmware did not start\n")
        self.assertEqual([e for e, _f in ci_report.tracebacks(text)],
                         ["FileNotFoundError: no qemu on PATH",
                          "RuntimeError: firmware did not start"])
        self.assertIn("RuntimeError: firmware did not start",
                      ci_report.annotations_for("build/logs/x.log", text)[0])

    def test_a_unittest_block_heading_is_not_reported_as_an_exception(self):
        text = ("Traceback (most recent call last):\n"
                '  File "t.py", line 3, in t\n    z()\n'
                "ERROR: test_t (tests.test_x.C.test_t)\n")
        exception = ci_report.tracebacks(text)[0][0]
        self.assertIn("no exception line", exception,
                      "a block heading beneath a traceback is structure, not a cause")

    def test_malformed_input_cannot_raise_in_the_reporter(self):
        """The step describes a failure; a reporter that crashes is a second failure."""
        for junk in ("", "\n", "Traceback (most recent call last):\n",
                     "\x00\x01binary\x02", "Traceback (most recent call last)",
                     "  File \"unterminated.py\", line 9,\n",
                     "Traceback:\n:\n:\n:\n", CRASH_LOG.replace("\n", "\r\n"),
                     "  " * 400 + "\n"):
            with self.subTest(text=repr(junk[:24])):
                out = ci_report.annotations_for("build/logs/j.log", junk)
                self.assertIsInstance(out, list)
                for line in out:
                    self.assertTrue(line.startswith("::error title="))
                    self.assertLessEqual(len(line), 8 + ci_report.ANNOTATION_LIMIT + 64)
                self.assertIsInstance(ci_report.report_for("build/logs/j.log", junk), str)

    def test_an_annotation_about_a_crash_is_still_one_physical_line_and_capped(self):
        huge = CRASH_LOG.replace("non-zero exit status 1.", "x" * 20000)
        for line in ci_report.annotations_for("build/logs/ovmf-test.log", huge):
            self.assertNotIn("\n", line)
            self.assertNotIn("\r", line)
            self.assertLessEqual(len(line), 8 + ci_report.ANNOTATION_LIMIT + 64)
            self.assertEqual(line.count("::error title="), 1)

    def test_the_summary_puts_the_raising_frame_on_its_own_line(self):
        body = ci_report.report_for("build/logs/ovmf-test.log", CRASH_LOG)
        self.assertIn("Traceback(s):", body)
        self.assertIn("subprocess.CalledProcessError", body)
        # `SEPARATOR` folds annotation bodies onto one line; the summary is markdown and
        # keeps the frames separate, which is the only place the shape survives.
        self.assertIn("\n  subprocess.CalledProcessError", body)

    def test_the_frame_quoted_is_the_deepest_one_in_the_project_not_the_entry_point(self):
        """`sys.exit(main())` is where the process died; the bug is three frames down.

        The outermost frame is the harness's entry point and the innermost is usually CPython
        raising inside `shutil` on the library's behalf.  Neither tells a reader which line
        of this repository to open, so the reporter names the deepest frame whose path is
        under the working directory and counts what it skipped past.
        """
        here = os.getcwd()
        text = ("Traceback (most recent call last):\n"
                '  File "%s/tests/ovmf_boot.py", line 2496, in <module>\n'
                "    sys.exit(main())\n"
                '  File "%s/tests/ovmf_boot.py", line 1970, in main\n'
                "    secure_boot_run(report, args)\n"
                '  File "/usr/lib/python3.12/shutil.py", line 235, in copyfile\n'
                "    if _samefile(src, dst):\n"
                '  File "<frozen genericpath>", line 100, in samefile\n'
                "TypeError: stat: path should be string, not Profile\n") % (here, here)
        exception, site = ci_report.tracebacks(text)[0]
        self.assertEqual(exception, "TypeError: stat: path should be string, not Profile")
        self.assertIn("line 1970, in main", site, "the deepest frame in the project's files")
        self.assertIn("secure_boot_run(report, args)", site)
        self.assertNotIn("line 2496", site, "the entry point is not where the bug is")
        self.assertIn("[+2 frame(s) below]", site,
                      "and the reader is told how much of the stack was skipped")

    def test_a_passing_gate_with_no_crash_still_reports_nothing(self):
        text = "test_a ... ok\nRan 1154 tests in 400.0s\n\nOK (skipped=1)\n"
        self.assertEqual(ci_report.annotations_for("build/logs/unit.log", text), [])
        self.assertEqual(ci_report.report_for("build/logs/unit.log", text), "")


class WorkflowFlagTests(unittest.TestCase):
    """Every flag the workflow passes to a helper must be a flag that helper accepts.

    `6293d65` added `--only ovmf` to the firmware job's triage step and `--only` did not
    exist in `tools/ci_triage.py`.  The helper printed one warning line to stderr, exited 0,
    and walked all 47 modules -- in a job that installs no virtualenv, so six of them failed
    on import.  A reviewer read "6 did not return 0" as six defects.  Nothing caught it
    because nothing asked the two files whether they agreed.
    """

    def test_the_triage_flags_the_workflow_passes_are_accepted(self):
        wf = load_workflow(self)
        seen = 0
        for name in ("build", "ovmf"):
            for step in wf["jobs"][name]["steps"]:
                body = str(step.get("run", ""))
                if "tools/ci_triage.py" not in body:
                    continue
                flags = [t for t in body.replace("\\", " ").split() if t.startswith("--")]
                self.assertTrue(flags, "%s's triage step passes no flags?" % name)
                seen += 1
                with tempfile.TemporaryDirectory() as tmp:
                    tests = os.path.join(tmp, "tests")
                    os.makedirs(tests)
                    with open(os.path.join(tests, "test_probe.py"), "w",
                              encoding="utf-8") as handle:
                        handle.write("import unittest\n\n\nclass T(unittest.TestCase):\n"
                                     "    def test_a(self):\n        pass\n")
                    # the workflow's own flags, against a one-file tree: only `--root` and
                    # `--json-out` values are redirected, so the flag names are the live ones
                    # only the helper's own arguments: `set -uo pipefail` and the
                    # interpreter line are shell, and feeding them to the tool would test
                    # the shell rather than the flag names
                    toks = body.replace("\\", " ").split()
                    start = toks.index([t for t in toks if t.endswith("ci_triage.py")][0]) + 1
                    cmd = [sys.executable, os.path.join(ROOT, "tools", "ci_triage.py")]
                    skip_next = False
                    for tok in toks[start:]:
                        if skip_next:
                            skip_next = False
                            continue
                        if tok in ("--root", "--json-out"):
                            skip_next = True      # drop the flag too: it is re-added below
                            continue
                        if tok == "--python":
                            cmd += ["--python", sys.executable]
                            skip_next = True
                            continue
                        cmd.append(tok)
                    cmd += ["--root", tests,
                            "--json-out", os.path.join(tmp, "t.json")]
                    done = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True,
                                          timeout=300)
                self.assertEqual(done.returncode, 0, done.stderr[-400:])
                self.assertNotIn("ignoring unknown argument", done.stderr,
                                 "%s passes flags the helper does not know: %s"
                                 % (name, " ".join(flags)))

    def test_the_ovmf_job_narrows_the_triage_and_the_build_job_does_not(self):
        """The narrowing is the point: a job without the venv must not be judged by it."""
        wf = load_workflow(self)
        bodies = {}
        for name in ("build", "ovmf"):
            for step in wf["jobs"][name]["steps"]:
                if "tools/ci_triage.py" in str(step.get("run", "")):
                    bodies[name] = str(step["run"])
        self.assertIn("--only ovmf", bodies.get("ovmf", ""),
                      "the firmware job should diagnose the modules it can actually run")
        self.assertNotIn("--only", bodies.get("build", ""),
                         "the host job installs the venv and is supposed to walk everything")


class AnnotationProtocolTests(unittest.TestCase):
    def test_an_annotation_is_one_line_and_log_content_cannot_forge_another(self):
        hostile = (HOST_LOG + "::error title=pwned::this line was written by the log\n"
                   "::notice title=also-pwned::and this one too\n")
        annotations = ci_report.annotations_for("build/logs/host.log", hostile)
        self.assertTrue(annotations)
        for line in annotations:
            self.assertNotIn("\n", line, "a workflow command must be exactly one line")
            self.assertNotIn("\r", line, "and it must not hide its body behind a line break")
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
        self.assertIn("FAIL: test_moving_head", body)
        self.assertIn("AssertionError", body)
        # GitHub shows only the first line of a message that contains real line breaks,
        # so a *readable* annotation is a single-line one; the block's shape is traded
        # away deliberately and the shape survives in the log and the job summary.
        self.assertEqual(body.count("\n"), 0, "an annotation must be one physical line")
        self.assertNotIn("\r", body, "a \r-separated body renders as its first line only")
        self.assertEqual(ci_report.SEPARATOR, " ")

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
            self.assertIn("No gate log held a verdict", done.stdout)
            self.assertIn("::error title=no gate log::", done.stdout)
            # the fallback must name what it looked at: an absent log and a log truncated by a
            # signal are different diagnoses, and the annotation is all a restricted caller gets
            self.assertIn("none found", done.stdout)
            self.assertIn("Interpreter:", done.stdout)

    def test_a_truncated_log_is_distinguished_from_an_absent_one(self):
        """`host.log=41B` in the fallback is the difference between two failure kinds.

        Run 4 died with `exit 139` and the reporter said "no gate log was written" while a
        41-byte log sat on disk: the size is what tells a segfault's truncated output apart
        from a step that never wrote anything, so it has to be in the message.
        """
        with tempfile.TemporaryDirectory() as tmp:
            logs = os.path.join(tmp, "build", "logs")
            os.makedirs(logs)
            with open(os.path.join(logs, "host.log"), "w", encoding="utf-8") as handle:
                handle.write("test_a ... ok" + chr(10) + "test_b ... ")
            done = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ci_report.py")],
                                  cwd=tmp, capture_output=True, text=True, timeout=600)
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            self.assertIn("host.log=", done.stdout)
            self.assertIn("B)", done.stdout, "the size must be quoted with a unit")
            self.assertNotIn("Traceback", done.stdout)

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

    def test_both_failing_jobs_call_the_triage_helper(self):
        """`discover` cannot describe a dead interpreter; the per-file triage can.

        Three properties, each something a future edit breaks from the other side of the
        file: exactly one triage step per failing job, `if: failure()` so a green job does
        not spend its budget being diagnosed, and *after* the reporter — the gate's own
        words lead the summary, the per-file tally is the deeper layer.
        """
        for name in ("build", "ovmf"):
            steps = self.yaml["jobs"][name]["steps"]
            hits = [i for i, st in enumerate(steps)
                    if "tools/ci_triage.py" in str(st.get("run", ""))]
            self.assertEqual(len(hits), 1, "%s should carry exactly one triage step" % name)
            self.assertEqual(steps[hits[0]].get("if"), "failure()",
                             "%s's triage must never run on a green job" % name)
            reporter = [i for i, st in enumerate(steps)
                        if "ci_report.py" in str(st.get("run", ""))]
            self.assertTrue(reporter, "%s lost its reporter" % name)
            self.assertGreater(hits[0], min(reporter),
                               "%s: the triage should follow the reporter" % name)

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
                body = str(later.get("run", "")) + str(later.get("name", ""))
                allowed = ("ci_triage.py" in body or later.get("if") == "always()")
                self.assertTrue(
                    allowed,
                    "%s: only the triage diagnostic and artifact uploads may follow the "
                    "reporter. A gate after it would be quoted as passed or absent before it "
                    "ran, and a step without `if:` would make a green job pay for a diagnosis "
                    "it does not need" % name)

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
