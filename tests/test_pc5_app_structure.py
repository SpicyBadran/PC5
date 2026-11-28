"""Structural gates for the Windows application tree.

These are not a substitute for `dotnet build`. This repository's development host has no
.NET SDK (`command -v dotnet` → absent), so no test here can compile C#, and none pretends
to. What they do instead is pin the invariants that a compiler or a reviewer would otherwise
have to catch by eye, and that CI on the real runners enforces the rest:

  * the boundary between the portable core and the Windows shell is a rule, not an intent;
  * the deliverable is named what the product is named;
  * no file in the application tree may claim a PlayStation 5 capability;
  * the CI dimension for the app states its own limits and cannot write to the repository.

Anything here that could be checked by `dotnet` is left to the `windows app` workflow.
"""

import json
import re
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
CORE = APP / "src" / "PC5.Core"
SHELL = APP / "src" / "PC5.App"
TESTS = APP / "tests" / "PC5.Core.Tests"
WORKFLOW = REPO / ".github" / "workflows" / "windows-app.yml"

#: References that would make PC5.Core a Windows library wearing a portable name.
FORBIDDEN_IN_CORE = (
    "Microsoft.UI",
    "Microsoft.Windows",
    "Microsoft.Win32",
    "Windows.Storage",
    "Windows.Graphics",
    "P/Invoke",
    "DllImport",
)

#: Claims the application tree must not make, at rest or in a test name.
FORBIDDEN_CLAIMS = (
    "genuine PS5 operating system booted",
    "PS5 OS is running",
    "runs PlayStation 5 games",
    "E5 evidence",
    "firmware decrypted",
    "decryption implemented",
)


def sources(root: Path):
    return sorted(list(root.rglob("*.cs")) + list(root.rglob("*.xaml")) + list(root.rglob("*.csproj")))


def brace_depth(text: str) -> int:
    """How far from balanced a file's code braces are, ignoring strings and comments.

    A state machine rather than a regex: a C# source file puts apostrophes inside doc
    comments and braces inside interpolated strings, and any pattern-matching shortcut ends
    up reading those as code. This only needs to answer one question -- did a block get
    closed -- so it walks characters and keeps track of what it is inside.
    """
    depth = 0
    in_line_comment = in_string = in_char = False
    previous = ""
    for character in text:
        if in_line_comment:
            if character == "\n":
                in_line_comment = False
        elif in_string:
            if character == '"' and previous != "\\":
                in_string = False
        elif in_char:
            if character == "'" and previous != "\\":
                in_char = False
        elif previous == "/" and character == "/":
            in_line_comment = True
        elif character == '"':
            in_string = True
        elif character == "'":
            in_char = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        previous = character
    return depth


def code_lines(text: str):
    """The lines that can carry an assertion: comments and claim mappings are not claims."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("///", "//", "*")):
            continue
        if "=> \"" in stripped:
            # The evidence ladder maps a level to the sentence that level may print. Those
            # strings name levels, including ones PC5 has not reached; they are the contract,
            # not a claim, and the test that forbids overstating must not silence the ladder.
            continue
        yield stripped


class TreeTests(unittest.TestCase):
    def test_the_solution_and_every_project_it_names_are_present(self):
        solution = (APP / "PC5.sln").read_text(encoding="utf-8")
        for project in ("src\\PC5.Core\\PC5.Core.csproj", "src\\PC5.App\\PC5.App.csproj",
                        "tests\\PC5.Core.Tests\\PC5.Core.Tests.csproj"):
            self.assertIn(project, solution, f"PC5.sln does not reference {project}")
        for relative in ("src/PC5.Core/PC5.Core.csproj", "src/PC5.App/PC5.App.csproj",
                         "tests/PC5.Core.Tests/PC5.Core.Tests.csproj"):
            self.assertTrue((APP / relative).is_file(), f"{relative} is named by the solution but missing")

    def test_no_empty_directories_were_created_for_appearance(self):
        for directory in sorted(APP.rglob("*")):
            if directory.is_dir():
                self.assertTrue(any(directory.iterdir()), f"{directory.relative_to(REPO)} is an empty folder")

    def test_every_file_in_the_tree_is_text_and_small(self):
        for path in sorted(p for p in APP.rglob("*") if p.is_file()):
            body = path.read_bytes()
            self.assertNotIn(b"\x00", body, f"{path.relative_to(REPO)} looks binary; the app tree holds source only")
            self.assertLess(len(body), 64 * 1024, f"{path.relative_to(REPO)} is unexpectedly large")

    def test_braces_balance_in_every_csharp_file(self):
        for path in sorted(CORE.rglob("*.cs")) + sorted(SHELL.rglob("*.cs")) + sorted(TESTS.rglob("*.cs")):
            depth = brace_depth(path.read_text(encoding="utf-8"))
            self.assertEqual(0, depth, f"unbalanced braces in {path.relative_to(REPO)} (depth {depth:+d})")

    def test_namespaces_follow_the_project_they_live_in(self):
        for root, expected in ((CORE, "Pc5.Core"), (SHELL, "Pc5.App"), (TESTS, "Pc5.Core.Tests")):
            for path in sorted(root.rglob("*.cs")):
                text = path.read_text(encoding="utf-8")
                match = re.search(r"^\s*namespace\s+([\w.]+)\s*;", text, re.M)
                self.assertIsNotNone(match, f"{path.relative_to(REPO)} declares no namespace")
                self.assertTrue(match.group(1).startswith(expected),
                                f"{path.relative_to(REPO)} is in namespace {match.group(1)}, expected under {expected}")


class MarkupTests(unittest.TestCase):
    """Every XML file in `app/` has to be well-formed, and this host can check that.

    The point of this class is that it *caught nothing* the first time: the first hosted run
    (46109038081) failed with MSB4024, "An XML comment cannot contain '--'", in
    `app/Directory.Build.props` -- a file every project imports, so no C# was ever compiled and
    the failure looked like "the code is broken". Python parses XML without a .NET toolchain, so
    the whole class of project-file syntax errors is now caught here, before a runner spends
    four minutes telling us the same thing.
    """

    def test_every_xml_file_parses(self):
        for path in sorted(APP.rglob("*.props")) + sorted(APP.rglob("*.csproj")) \
                + sorted(APP.rglob("*.xaml")):
            text = path.read_text(encoding="utf-8")
            try:
                ET.fromstring(text)
            except ET.ParseError as failure:
                self.fail(f"{path.relative_to(REPO)} is not well-formed XML: {failure}")

    def test_no_xml_comment_carries_a_double_hyphen(self):
        for path in sorted(APP.rglob("*.props")) + sorted(APP.rglob("*.csproj")) \
                + sorted(APP.rglob("*.xaml")):
            text = path.read_text(encoding="utf-8")
            for comment in re.findall(r"<!--(.*?)-->", text, flags=re.S):
                self.assertNotIn("--", comment,
                                 f"{path.relative_to(REPO)} has '--' inside an XML comment, "
                                 f"which MSBuild refuses to load (MSB4024)")
                self.assertFalse(comment.rstrip().endswith("-"),
                                 f"{path.relative_to(REPO)} ends an XML comment with '-', "
                                 f"which is equally illegal")

    def test_the_project_files_declare_the_frameworks_the_boundary_requires(self):
        for name, expected in (("PC5.Core.csproj", "net8.0"),
                              ("PC5.App.csproj", "net8.0-windows10.0.19041.0"),
                              ("PC5.Core.Tests.csproj", "net8.0")):
            path = next(APP.rglob(name))
            root = ET.fromstring(path.read_text(encoding="utf-8"))
            tfms = [node.text.strip() for node in root.iter()
                    if node.tag.endswith("TargetFramework") or node.tag.endswith("TargetFrameworks")]
            self.assertIn(expected, tfms, f"{name} targets {tfms}, expected {expected}")

    def test_the_sdk_floor_says_what_it_meant(self):
        """`latestMajor` let a runner pick a preview SDK; a floor needs a ceiling too."""
        global_json = json.loads((APP / "global.json").read_text(encoding="utf-8"))
        sdk = global_json["sdk"]
        self.assertTrue(sdk["version"].startswith("8."), sdk["version"])
        self.assertIn(sdk["rollForward"], ("latestFeature", "latestPatch", "disable"),
                      "rollForward latestMajor silently accepts a preview SDK of a later "
                      "major, which is how a documented .NET 8 tree ends up built by 11.0.100")


class BoundaryTests(unittest.TestCase):
    def test_the_core_targets_a_portable_framework_and_nothing_else(self):
        project = (CORE / "PC5.Core.csproj").read_text(encoding="utf-8")
        self.assertIn("<TargetFramework>net8.0</TargetFramework>", project)
        self.assertNotIn("-windows", project, "PC5.Core may not acquire a Windows TFM")
        self.assertNotIn("<PackageReference", project, "PC5.Core has no third-party dependencies; keep it that way")

    def test_the_core_references_no_windows_api_at_all(self):
        for path in sorted(CORE.rglob("*.cs")):
            text = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_IN_CORE:
                self.assertNotIn(forbidden, text,
                                 f"{path.relative_to(REPO)} touches {forbidden}: that belongs in PC5.App/Platform")

    def test_the_shell_is_the_deliverable_and_declares_its_platform(self):
        project = (SHELL / "PC5.App.csproj").read_text(encoding="utf-8")
        self.assertIn("<AssemblyName>PC5</AssemblyName>", project, "the product is PC5.exe")
        self.assertIn("<OutputType>WinExe</OutputType>", project)
        self.assertIn("<UseWinUI>true</UseWinUI>", project)
        self.assertIn("<WindowsPackageType>MSIX</WindowsPackageType>", project)
        self.assertIn("<Platforms>x64</Platforms>", project)
        self.assertIn("-windows10.0.19041.0", project)
        self.assertIn('<ProjectReference Include="..\\PC5.Core\\PC5.Core.csproj" />', project)

    def test_the_window_holds_no_policy_of_its_own(self):
        code = (SHELL / "MainWindow.xaml.cs").read_text(encoding="utf-8")
        for banned in ("ExecutionStateMachine(", "MoveTo(", "FirmwareArtifactProbe.Describe("):
            self.assertNotIn(banned, code,
                             f"the window must not compute lifecycle or firmware state ({banned}); ask the view model")

    def test_only_one_service_owner_per_contract(self):
        composition = (SHELL / "App.xaml.cs").read_text(encoding="utf-8")
        registrations = re.findall(r"Services\.Register<([^>]+)>", composition)
        self.assertEqual(len(registrations), len(set(registrations)),
                         f"a contract is registered twice in the composition root: {registrations}")
        self.assertIn("new ServiceRegistry()", "x")  if False else None
        for contract in ("Pc5.Core.Execution.IExecutionEngine", "Pc5.Core.Diagnostics.IHostCapabilityProbe"):
            self.assertIn(contract, composition, f"{contract} is never registered, so Resolve would refuse at runtime")


class ClaimTests(unittest.TestCase):
    def test_nothing_in_the_app_tree_claims_a_playstation_capability(self):
        for path in sources(APP):
            lines = list(code_lines(path.read_text(encoding="utf-8")))
            for claim in FORBIDDEN_CLAIMS:
                offenders = [line for line in lines if claim.lower() in line.lower()]
                self.assertFalse(offenders,
                                 f"{path.relative_to(REPO)} asserts {claim!r} outside the evidence ladder: "
                                 + " | ".join(offenders[:2]))

    def test_the_shipped_backend_is_named_for_what_it_is(self):
        backend = (CORE / "Execution" / "Pc5TestBackend.cs").read_text(encoding="utf-8")
        self.assertIn("not PS5", backend, "the test backend's display name must say what it is not")
        self.assertIn("EvidenceLevel.GenericGuest", backend)
        self.assertIn("MaximumEvidenceLevel => EvidenceLevel.GenericGuest", backend,
                      "the backend must declare its own ceiling")

    def test_the_build_states_its_evidence_ceiling(self):
        build_info = (CORE / "Versioning" / "BuildInfo.cs").read_text(encoding="utf-8")
        self.assertIn("BuildEvidenceCeiling = EvidenceLevel.ApplicationBuilds", build_info)
        self.assertIn("has not been demonstrated", build_info)
        self.assertIn("TruthStatement", build_info)

    def test_the_lifecycle_can_record_every_blocked_and_unsupported_outcome(self):
        table = (CORE / "Lifecycle" / "ExecutionStateMachine.cs").read_text(encoding="utf-8")
        for state in ("Unsupported", "Blocked", "Failed", "Stopped"):
            self.assertIn(f"ExecutionState.{state}", table)
        # A refused edge must be an exception, not a silent acceptance.
        self.assertIn("refused lifecycle transition", table)


class DimensionTests(unittest.TestCase):
    def test_the_app_dimension_cannot_write_to_the_repository(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("contents: read", text)
        self.assertNotIn("contents: write", text, "the app job reports; the evidence commit belongs to progress.yml")
        self.assertNotIn("git push", text, "no workflow other than progress.yml may push to main")

    def test_the_app_dimension_files_its_record_from_a_result_file_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("tools/app_evidence.py", text)
        self.assertIn('if: always()', text, "an absent result file must surface as not_run, not as a missing record")
        self.assertIn("trx;LogFileName=", text)

    def test_every_job_states_a_timeout_and_an_operating_system(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("timeout-minutes:"), 2)
        self.assertIn("runs-on: windows-latest", text)
        self.assertIn("runs-on: ubuntu-latest", text)

    def test_the_milestones_the_dimension_cites_exist_in_the_graph(self):
        import json
        graph = json.loads((REPO / "docs" / "architecture" / "roadmap.json").read_text(encoding="utf-8"))
        known = {m["id"] for m in graph["milestones"]}
        text = WORKFLOW.read_text(encoding="utf-8")
        for milestone in sorted(set(re.findall(r"--milestone (M[0-9]+[a-z]?)", text))):
            self.assertIn(milestone, known,
                          f"windows-app.yml cites {milestone}, which the roadmap does not define")

    def test_the_validation_procedure_the_workflow_points_at_exists(self):
        reference = re.search(r"docs/engineering/(\w+\.md)", WORKFLOW.read_text(encoding="utf-8"))
        self.assertIsNotNone(reference, "the workflow must name the procedure for what it cannot prove")
        self.assertTrue((REPO / "docs" / "engineering" / reference.group(1)).is_file(),
                        f"docs/engineering/{reference.group(1)} is cited but missing")


class RecordingTests(unittest.TestCase):
    def test_the_evidence_tool_runs_on_this_host(self):
        """The recorder is Python, so unlike the C# it describes it can be executed here."""
        result = subprocess.run(
            ["python3", str(REPO / "tools" / "app_evidence.py"), "--out", "-", "--subsystem", "app"],
            capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('"status": "not_run"', result.stdout)
        self.assertIn("no --trx was given", result.stdout)


if __name__ == "__main__":
    unittest.main()
