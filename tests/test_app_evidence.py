"""The app dimension's recorder is held to the same rule as everything else here: it may
report what a result file says, and it must refuse the flattering reading. These tests run on
any host, so the refusal is verified even where no .NET SDK exists to produce a TRX."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "app_evidence.py"

TRX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<TestRun id="pc5" name="pc5" xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Results>
{results}
  </Results>
</TestRun>
"""


def trx(outcomes):
    return TRX_TEMPLATE.format(results="".join(
        f'    <UnitTestResult testName="t{n}" outcome="{o}" duration="00:00:00.010" />'
        for n, o in enumerate(outcomes)))


def run(*argv):
    return subprocess.run([sys.executable, str(TOOL), *argv], capture_output=True, text=True)


class AppEvidenceTests(unittest.TestCase):
    def write(self, root, text):
        path = Path(root) / "tests.trx"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_green_result_file_becomes_a_passed_record(self):
        with tempfile.TemporaryDirectory() as root:
            result = run("--trx", str(self.write(root, trx(["Passed"] * 7 + ["NotExecuted"]))),
                         "--out", "-", "--subsystem", "app", "--commit", "abc123", "--run", "42")
            self.assertEqual(0, result.returncode, result.stderr)
            record = json.loads(result.stdout)
            self.assertEqual("pc5.app-evidence/1", record["schema"])
            self.assertEqual({"executed": 8, "passed": 7, "failed": 0, "skipped": 1}, record["tests"])
            self.assertEqual("passed", record["status"])
            self.assertEqual("7 of 8 tests passed (1 skipped)", record["status_reason"])
            self.assertEqual("abc123", record["commit"])
            self.assertEqual("app", record["subsystem"])
            self.assertEqual("tools/app_evidence.py", record["recorded_by"])

    def test_a_failure_cannot_be_reported_as_anything_else(self):
        with tempfile.TemporaryDirectory() as root:
            result = run("--trx", str(self.write(root, trx(["Passed", "Failed", "Failed"]))), "--out", "-")
            record = json.loads(result.stdout)
            self.assertEqual("failed", record["status"])
            self.assertEqual(2, record["tests"]["failed"])
            self.assertEqual(1, result.returncode, "a failed dimension must fail the step")

    def test_an_absent_result_file_is_not_run_rather_than_a_zero(self):
        with tempfile.TemporaryDirectory() as root:
            result = run("--trx", str(Path(root) / "missing.trx"), "--out", "-")
            record = json.loads(result.stdout)
            self.assertEqual("not_run", record["status"])
            self.assertEqual(0, record["tests"]["executed"])
            self.assertIn("no result file", record["status_reason"])
            self.assertEqual(0, result.returncode, "not_run is a fact, not a failure")

    def test_an_unparseable_result_file_is_a_stated_inability_to_read_it(self):
        with tempfile.TemporaryDirectory() as root:
            result = run("--trx", str(self.write(root, "<TestRun")), "--out", "-")
            record = json.loads(result.stdout)
            self.assertEqual("not_run", record["status"])
            self.assertTrue(record["status_reason"].startswith("unreadable TRX"))

    def test_a_result_file_full_of_skips_proves_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            record = json.loads(run("--trx", str(self.write(root, trx(["NotExecuted"] * 4))),
                                    "--out", "-").stdout)
            self.assertEqual("unknown", record["status"])
            self.assertEqual(0, record["tests"]["passed"])

    def test_a_supplied_status_is_checked_and_a_green_no_op_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            refused = run("--trx", str(Path(root) / "missing.trx"), "--status", "passed", "--out", "-")
            self.assertEqual(1, refused.returncode)
            self.assertIn("zero executed tests", refused.stderr)
            invented = run("--trx", str(self.write(root, trx(["Passed"]))), "--status", "wat", "--out", "-")
            self.assertIn("--status must be one of", invented.stderr)

    def test_the_record_can_never_be_talked_into_a_guest_execution_claim(self):
        with tempfile.TemporaryDirectory() as root:
            record = json.loads(run("--trx", str(self.write(root, trx(["Passed"] * 3))),
                                   "--out", "-", "--milestone", "M23", "--milestone", "M22",
                                   "--artifact", "pc5-app-msix").stdout)
            joined = json.dumps(record)
            for forbidden in ("E5", "E6", "genuine PS5 operating system booted", "ps5-os-executed"):
                self.assertNotIn(forbidden, joined)
            self.assertIn("no guest of any kind ran here", joined)
            self.assertEqual(["M22", "M23"], record["milestones"])
            self.assertEqual(["pc5-app-msix"], record["artifacts"])

    def test_another_dimension_is_echoed_only_from_its_own_record(self):
        with tempfile.TemporaryDirectory() as root:
            legacy = Path(root) / "legacy.json"
            legacy.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
            record = json.loads(run("--trx", str(self.write(root, trx(["Passed"]))), "--out", "-",
                                    "--dimensions", f"legacy-kernel={legacy}",
                                    "--dimensions", "research={}".format(Path(root) / "none.json")).stdout)
            echo = record["other_dimensions_read_only"]
            self.assertEqual("passed", echo["legacy-kernel"])
            self.assertEqual("not_run", echo["research"])


if __name__ == "__main__":
    unittest.main()
