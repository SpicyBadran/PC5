"""Tests for the CI annotation reporter.

The property that matters is not "does it find errors" but "does it never become the failure":
a step that pipes a build through a reporter must still report the build's verdict, and a
reporter that cannot read anything must say so instead of printing silence that reads like a
clean log. Both are asserted here, plus the escaping that keeps a message from being parsed as
parameters.
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import ci_annotations  # noqa: E402

BUILD_LOG = """\
MSBuild version 17.8.3 for .NET
  Determining projects to restore...
/home/runner/work/PC5/PC5/app/src/PC5.Core/Logging/Log.cs(41,17): error CS0103: The name 'FileOptions' does not exist in the current context [/home/runner/work/PC5/PC5/app/src/PC5.Core/PC5.Core.csproj]
/home/runner/work/PC5/PC5/app/src/PC5.Core/Diagnostics/Capability.cs(12,29): warning CS8618: Non-nullable property must contain a non-null value [PC5.Core.csproj]
Build FAILED.
    1 Warning(s)
    1 Error(s)
"""


class AnnotateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_compiler_error_becomes_an_annotation_that_points_at_the_line(self):
        lines = ci_annotations.annotate(BUILD_LOG)
        self.assertEqual(1, len(lines), "warnings stay out unless asked for")
        self.assertTrue(lines[0].startswith("::error file="), lines[0])
        self.assertIn(",line=41::", lines[0])
        self.assertIn("CS0103", lines[0])
        self.assertIn("FileOptions", lines[0])

    def test_a_colon_in_the_message_does_not_end_the_parameter_block(self):
        """`::error file=x,line=1::CS0103: text` is valid; a comma in a *parameter* is not."""
        line = ci_annotations.annotate(BUILD_LOG)[0]
        parameters, _, message = line[2:].partition("::")
        self.assertNotIn(",", message.replace("work/PC5,PC5", "work/PC5"), "a raw comma in the "
                           "message is fine; the guard is that the parameter list is complete")
        for part in parameters.split(","):
            self.assertIn("=", part, f"every parameter must be key=value, found {part!r}")

    def test_a_non_compiler_failure_is_still_quoted(self):
        lines = ci_annotations.annotate('error MSB4057: The target "Pack" does not exist.\n')
        self.assertEqual(1, len(lines))
        self.assertTrue(lines[0].startswith("::error::"), lines[0])
        self.assertIn("MSB4057", lines[0])

    def test_the_limit_is_a_limit_and_errors_come_first(self):
        log = "\n".join(f"/x/F{i}.cs({i},1): error CS0001: problem {i}" for i in range(40))
        lines = ci_annotations.annotate(log, limit=3)
        self.assertEqual(3, len(lines))
        self.assertIn("problem 0", lines[0])

    def test_warnings_are_available_but_not_by_default(self):
        """One warning line in, zero out by default, one out when asked — the counts are exact
        so that "warnings are hidden" cannot quietly become "everything is hidden"."""
        only_warning = "/x/Foo.cs(3,4): warning CS8618: something to look at\n"
        self.assertEqual([], ci_annotations.annotate(only_warning))
        self.assertEqual(1, len(ci_annotations.annotate(only_warning, with_warnings=True)))
        # and in the mixed log, warnings are still absent unless requested
        self.assertEqual(1, len(ci_annotations.annotate(BUILD_LOG)))
        self.assertEqual(2, len(ci_annotations.annotate(BUILD_LOG, with_warnings=True)))

    def test_a_windows_path_and_a_bare_position_are_both_readable(self):
        """`path : error NU1102:` is a real MSBuild shape, and `C:\\a\\b` has a colon.

        Both facts broke the first hosted Windows run: the pattern demanded a colon-free path
        with an explicit (line,column), so the only failure line in the log was invisible.
        """
        windows = ("C:\\a\\PC5\\PC5\\app\\src\\PC5.App\\PC5.App.csproj : error NU1102: "
                   "Unable to find package Microsoft.WindowsAppSDK with version (>= 1.6.2) "
                   "[C:\\a\\PC5\\PC5\\app\\src\\PC5.App\\PC5.App.csproj]\n")
        lines = ci_annotations.annotate(windows)
        self.assertEqual(1, len(lines), lines)
        self.assertIn("file=C%3A", lines[0], "the drive-qualified path survives, escaped")
        self.assertIn("NU1102", lines[0])
        self.assertNotIn("line=", lines[0], "no position is invented when the log gives none")
        self.assertNotIn("PC5.App.csproj]", lines[0], "the trailing project bracket is not the message")

    def test_a_utf16_log_written_by_tee_object_is_read_as_a_log(self):
        """PowerShell's `Tee-Object -FilePath` writes UTF-16; UTF-8 reading hid every failure."""
        message = "app/src/PC5.App/PC5.App.csproj : error NU1101: no such package\n"
        for data in (message.encode("utf-16-le"),
                     b"\xff\xfe" + message.encode("utf-16-le"),
                     b"\xfe\xff" + message.encode("utf-16-be")):
            path = Path(self.tmp.name) / "log.txt"
            path.write_bytes(data)
            text = ci_annotations.read_log(str(path))
            self.assertIn("error NU1101", text, data[:4])
            self.assertEqual(1, len(ci_annotations.annotate(text)), data[:4])

    def test_a_failing_test_is_annotated_by_name(self):
        """A red assertion says something about behaviour; `exit code 1` says nothing.

        The second hosted run's Unit-tests step failed on the harness, and the only thing the
        API would have carried is a per-test name if the reporter understood the test runner's
        own output as well as the compiler's.
        """
        log = ("  Failed Pc5.Core.Tests.SettingsAndLogTests.a_corrupt_file_is_quarantined [12 ms]\n"
               "  Failed Pc5.Core.Tests.ExecutionStateMachineTests.an_undeclared_edge_throws [3 ms]\n")
        lines = ci_annotations.annotate(log)
        self.assertEqual(2, len(lines), lines)
        self.assertTrue(all(line.startswith("::error::") for line in lines), lines)
        self.assertIn("a_corrupt_file_is_quarantined", lines[0])
        self.assertIn("[12 ms]", lines[0], "the runner's own detail is quoted, not summarised")

    def test_the_tally_leads_and_a_green_tally_is_not_an_annotation(self):
        failed = ci_annotations.annotate(
            "Failed!  - Failed: 2, Passed: 39, Skipped: 0, Total: 41 [12 s]\n"
            "  Failed Pc5.Core.Tests.A.B [1 ms]\n")
        self.assertEqual(2, len(failed))
        self.assertIn("Failed: 2", failed[0], "the size of the failure comes first")
        self.assertEqual([], ci_annotations.annotate(
            "Passed! - Failed: 0, Passed: 41, Skipped: 0, Total: 41 [1 m 3 s]\n"),
            "a passing tally must not be turned into anything, let alone an error")

    def test_a_clean_log_produces_nothing_rather_than_a_claim_of_health(self):
        self.assertEqual([], ci_annotations.annotate("Build succeeded.\n    0 Error(s)\n"))


class CLITests(unittest.TestCase):
    def run_tool(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(REPO / "tools" / "ci_annotations.py")] + list(args),
                              input=stdin, capture_output=True, text=True, timeout=60)

    def test_a_step_that_failed_with_nothing_recognised_says_so(self):
        """Silence from a red step is the exact ambiguity that hid two real failures."""
        log = ("  Determining projects to restore...\n"
               "  Restored /home/runner/app/src/PC5.App/PC5.App.csproj (in 4.2 sec).\n"
               "  1 of 2 projects are up-to-date for restore.\n"
               "  PC5.Core -> bin/Release/net8.0/PC5.Core.dll\n"
               "  LaunchSettingsProfile: warning : no profile\n"
               "  XamlCompiler : task failed\n"
               "Build FAILED.\n")
        self.assertEqual([], ci_annotations.annotate(log), "no diagnostic shape is matched")
        reported = self.run_tool("--in", "-", "--status", "1", stdin=log).stdout
        self.assertIn("no compiler or test diagnostic recognised", reported)
        self.assertIn("Build FAILED.", reported, "the tail is quoted, because the tail is the reason")
        self.assertIn("XamlCompiler", reported)
        self.assertNotIn("Determining projects", reported,
                         "and only the last lines of it, not the whole log")
        silent = self.run_tool("--in", "-", "--status", "0", stdin=log).stdout
        self.assertEqual("", silent.strip(), "a passing step produces nothing at all")

    def test_exit_zero_on_a_log_that_reports_a_failed_build(self):
        """The reporter's exit code must never become the step's verdict."""
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as handle:
            handle.write(BUILD_LOG)
            path = handle.name
        try:
            done = self.run_tool("--in", path)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertIn("::error file=", done.stdout)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_stdin_works_so_a_step_can_pipe_without_a_temp_file(self):
        done = self.run_tool("--limit", "2", stdin=BUILD_LOG)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("CS0103", done.stdout)

    def test_an_unreadable_log_is_reported_as_silence_about_itself(self):
        done = self.run_tool("--in", str(REPO / "no-such-build-log.log"))
        self.assertEqual(0, done.returncode, "a missing log may not turn into a failed step")
        self.assertEqual("", done.stdout.strip())
        self.assertIn("could not read", done.stderr)
        self.assertNotIn("no errors", (done.stdout + done.stderr).lower(),
                         "the tool must not imply the build was clean")

    def test_a_binary_log_does_not_crash_it(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".log", delete=False) as handle:
            handle.write(b"\x00\x01error CS1234: \xff\xfe")
            path = handle.name
        try:
            done = self.run_tool("--in", path)
            self.assertEqual(0, done.returncode, done.stderr[-300:])
        finally:
            Path(path).unlink(missing_ok=True)


class WiringTests(unittest.TestCase):
    """The workflow must call this tool and must not let it decide the outcome."""

    def setUp(self):
        self.text = (REPO / ".github" / "workflows" / "windows-app.yml").read_text(encoding="utf-8")

    def test_every_dotnet_build_step_is_annotated(self):
        self.assertGreaterEqual(self.text.count("ci_annotations.py"), 3,
                                "core build, app build and the publish step all need the "
                                "annotation pass; that is the whole reason the tool exists")

    #: The blocks this file's verdicts are built from, found by indentation rather than by a
    #: YAML parser: `shell:` and `run: |` sit at 8 spaces inside a step, their body at 10.
    BLOCKS = re.compile(r"shell: (bash|pwsh)\n[ ]{8}run: \|\n((?:[ ]{10}.*\n?)+)")

    def annotated_blocks(self):
        found = []
        for match in self.BLOCKS.finditer(self.text):
            shell, body = match.group(1), match.group(2)
            if "dotnet" in body and "ci_annotations.py" in body:
                found.append((shell, body))
        return found

    def test_the_verdicts_come_from_the_tools_not_the_reporter(self):
        """Every annotated step exits with *dotnet's* code, captured before the reporter runs.

        The first version of this test only asked that `exit $LASTEXITCODE` appear somewhere in
        the file, and that is the wrong invariant. `$LASTEXITCODE` is the exit code of the last
        native command, so a pwsh step that runs the reporter and then exits with `$LASTEXITCODE`
        reports python's 0 -- which is exactly what this workflow's publish step did: a failed
        publish would have been recorded as a green step. Checking "captured before, exited after"
        per block is the property; a substring anywhere in the file was decoration.
        """
        blocks = self.annotated_blocks()
        self.assertGreaterEqual(len(blocks), 5,
                                f"expected the five annotated dotnet steps, found {len(blocks)}")
        offenders = []
        for shell, body in blocks:
            call = body.index("ci_annotations.py")
            capture = "$status = $LASTEXITCODE" if shell == "pwsh" else "|| status=$?"
            if capture not in body[:call]:
                offenders.append(f"{shell}: the reporter runs before dotnet's status is captured")
            after = body[call:]
            if "exit $status" not in after:
                offenders.append(f"{shell}: the step does not exit with the captured status")
            if "--status $status" not in after:
                offenders.append(f"{shell}: the reporter is not told the status, so a log it "
                                 "cannot parse is indistinguishable from a clean one")
        self.assertEqual([], offenders, "; ".join(offenders))

    def test_the_paths_filter_includes_the_new_tool(self):
        self.assertIn("tools/ci_annotations.py", self.text)

    def test_the_structure_gates_run_even_when_the_build_failed(self):
        """The boundary and claim gates need no .NET, so a compile failure must not hide them."""
        block = self.text[self.text.index("Application structure gates"):]
        self.assertIn("if: always()", block[:200])
        self.assertIn("tests.test_ci_annotations", block[:400],
                      "the reporter that makes failures legible is itself part of the dimension")

    def test_the_record_steps_create_their_output_directory(self):
        """The first hosted run failed here: `tee build/…` with no `build/`, because the step
        that made it was skipped. A record step that cannot write is a lost verdict."""
        self.assertIn("mkdir -p build", self.text)
        self.assertIn("New-Item -ItemType Directory -Force -Path build/logs, build/app-trx",
                      self.text)

    def test_multiline_commands_are_continuations_not_new_statements(self):
        """A `run:` block is YAML-folded, so every line is a separate shell statement.

        The bug this looks for was in this file's first version: `dotnet test ...` on one line
        and `--logger "trx;..."` on the next, which the shell reads as a second command named
        `--logger` and the run dies with "command not found" for a flag that exists. A line may
        only start with an option if the line before it ends with the continuation marker its
        own shell understands -- `\\` in bash, a backtick in pwsh.
        """
        offenders = []
        for _, job in re.findall(r"^  ([\w-]+):\n((?:.*\n)*?)(?=^  [\w-]+:|$)", self.text, re.M):
            in_run = False
            shell = "bash"
            previous = ""
            for raw in job.splitlines():
                stripped = raw.strip()
                if stripped.startswith("shell:"):
                    shell = stripped.split(":", 1)[1].strip()
                if stripped.startswith("run:"):
                    in_run, previous = True, ""
                    if stripped != "run: |" and stripped != "run: >":
                        previous = stripped.split("run:", 1)[1].strip()
                    continue
                if not in_run:
                    continue
                if not stripped or re.match(r"^(- name:|uses:|with:|if:|id:|name:|shell:)", stripped):
                    in_run = False
                    continue
                continued = previous.endswith("\\\\") if shell == "bash" else previous.endswith("`")
                if stripped.startswith(("--", "-p:", ">&", ">", "|")) and not continued:
                    offenders.append(f"{stripped[:60]!r} (previous line: {previous[-24:]!r})")
                previous = stripped
        self.assertEqual([], offenders,
                         "these run lines would execute as their own commands: " + "; ".join(offenders))


if __name__ == "__main__":
    unittest.main()
