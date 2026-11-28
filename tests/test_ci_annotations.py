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

    def test_a_clean_log_produces_nothing_rather_than_a_claim_of_health(self):
        self.assertEqual([], ci_annotations.annotate("Build succeeded.\n    0 Error(s)\n"))


class CLITests(unittest.TestCase):
    def run_tool(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(REPO / "tools" / "ci_annotations.py")] + list(args),
                              input=stdin, capture_output=True, text=True, timeout=60)

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

    def test_the_verdicts_come_from_the_tools_not_the_reporter(self):
        self.assertIn("exit $status", self.text, "the bash steps must exit with dotnet's code")
        self.assertIn("exit $LASTEXITCODE", self.text, "the pwsh steps must exit with dotnet's code")

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
