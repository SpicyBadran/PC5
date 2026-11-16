"""The M1 record validator has to reject what M1 must not claim.

Physical boot is the one gate this repository cannot run, so its evidence format
is the only thing standing between "somebody will test it" and a document that
quietly suggests it was tested. Each test here is a way a record could lie or a way
it could be filed too early; the validator has to catch all of them.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import hw_record  # noqa: E402

TEMPLATE = REPO / "docs/evidence/M1_RECORD_TEMPLATE.json"


def filled() -> dict:
    """A complete, honest record: a physical UEFI boot with a log."""
    return {
        "date": "2026-11-20",
        "operator": "tester",
        "level": "physical-uefi",
        "outcome": "booted",
        "machine": {
            "vendor": "Example", "model": "X570-Pro", "cpu": "Ryzen 5 3600",
            "ram_gb": 16, "board": "X570-Pro", "chipset": "X570",
            "gpu": "RX 6600", "display": "HDMI 1080p", "storage": "NVMe 500 GB",
        },
        "firmware": {
            "vendor": "Example", "version": "1.2.3", "date": "2026-01-05",
            "secure_boot": "disabled", "boot_mode": "uefi", "csm": "absent",
        },
        "medium": {"kind": "usb", "capacity_gb": 8},
        "image": {
            "commit": "0" * 40,
            "source_date_epoch": 1763251200,
            "sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "medium_sha256": "a" * 64,
        },
        "serial": {
            "capture": "file", "log": "serial.log", "baud": 115200,
            "final_line": "PC5-BOOT-OK",
        },
        "observations": ["boot log reached PC5-BOOT-OK on COM1"],
        "blockers": [],
    }


class RecordValidationTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "serial.log").write_text("PC5-BOOT-OK\n")

    def check(self, record: dict) -> list[str]:
        return hw_record.problems(record, base=self.dir)

    def test_a_complete_record_validates(self):
        self.assertEqual(self.check(filled()), [])

    def test_the_shipped_template_does_not_validate(self):
        """An unfilled template must never pass as evidence."""
        self.assertNotEqual(hw_record.problems(json.loads(TEMPLATE.read_text()),
                                               base=TEMPLATE.parent), [])
        self.assertEqual(hw_record.template_problems(TEMPLATE), [])

    def test_a_tbd_field_is_rejected(self):
        record = filled()
        record["machine"]["gpu"] = "TBD"
        self.assertTrue(any("machine.gpu" in problem for problem in self.check(record)))

    def test_a_physical_boot_without_a_log_is_rejected(self):
        record = filled()
        record["serial"] = {"capture": "none", "log": "", "baud": 115200, "final_line": ""}
        problems = self.check(record)
        self.assertTrue(any("without a log" in problem for problem in problems), problems)

    def test_a_missing_log_file_is_rejected(self):
        record = filled()
        record["serial"]["log"] = "not-there.log"
        self.assertTrue(any("does not exist" in problem for problem in self.check(record)))

    def test_an_emulator_run_cannot_be_filed_as_a_physical_record(self):
        for level in ("emulator", "ovmf", "structure"):
            record = filled()
            record["level"] = level
            problems = self.check(record)
            self.assertTrue(any("level" in problem for problem in problems), (level, problems))

    def test_a_bad_sha256_is_rejected(self):
        record = filled()
        record["image"]["sha256"] = "not-a-hash"
        self.assertTrue(any("SHA-256" in problem for problem in self.check(record)))

    def test_medium_and_image_must_match(self):
        record = filled()
        record["image"]["medium_sha256"] = "c" * 64
        self.assertTrue(any("medium does not hold" in problem
                            for problem in self.check(record)))

    def test_booted_with_blockers_is_rejected(self):
        record = filled()
        record["blockers"] = ["no serial adapter"]
        self.assertTrue(any("cannot be 'booted' while listing blockers" in problem
                            for problem in self.check(record)))

    def test_a_missing_observation_is_rejected(self):
        record = filled()
        record["observations"] = []
        self.assertTrue(any("observations" in problem for problem in self.check(record)))

    def test_bad_enumerations_are_rejected(self):
        for key, value in (("boot_mode", "uefi-csm"), ("secure_boot", "sometimes"),
                           ("csm", "maybe")):
            record = filled()
            record["firmware"][key] = value
            self.assertTrue(any(key in problem for problem in self.check(record)), key)

    def test_a_failed_attempt_is_valid_evidence(self):
        """A failed boot is a result; only dishonesty is rejected."""
        record = filled()
        record["outcome"] = "failed"
        record["observations"] = ["no output on COM1 after the medium was selected"]
        record["blockers"] = ["medium not offered as a boot target"]
        self.assertEqual(self.check(record), [])

    def test_the_cli_reports_both_ways(self):
        good = self.dir / "good.json"
        good.write_text(json.dumps(filled()))
        result = subprocess.run([sys.executable, str(REPO / "tools/hw_record.py"),
                                 str(good)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("RECORD VALID", result.stdout)

        bad = self.dir / "bad.json"
        broken = copy.deepcopy(filled())
        broken["machine"]["vendor"] = ""
        bad.write_text(json.dumps(broken))
        result = subprocess.run([sys.executable, str(REPO / "tools/hw_record.py"),
                                 str(bad)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL]", result.stdout)

    def test_the_template_check_cli_passes(self):
        result = subprocess.run([sys.executable, str(REPO / "tools/hw_record.py"),
                                 "--check-template", str(TEMPLATE)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
