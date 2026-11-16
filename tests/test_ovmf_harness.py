#!/usr/bin/env python3
"""Tests for the OVMF harness itself - not for firmware.

`tests/ovmf_boot.py` makes a very strong claim when it prints
`PC5-OVMF-BOOT-OK`, so the machinery that produces that claim has to be tested
like any other code: what it accepts as firmware, what it refuses, how it
de-duplicates a firmware console mirror, how it tells firmware failures from
bootloader failures, and - most importantly - that "not tested" can never come
out as a pass.

None of this executes firmware. It is a unit test of the harness; the verdict on
a real boot comes only from `make ovmf-test` on a machine that has OVMF.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

SPEC = importlib.util.spec_from_file_location(
    "ovmf_boot", os.path.join(ROOT, "tests", "ovmf_boot.py"))
ovmf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ovmf)


def write(path, data):
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def fake_firmware(path, size=1024 * 1024, signature=True, banner=b""):
    """A file shaped like an EDK II firmware volume (header only)."""
    blob = bytearray(size)
    if signature:
        blob[0x28:0x2C] = b"_FVH"
        blob[0x20:0x28] = (size).to_bytes(8, "little")
    blob[0x40:0x40 + len(banner)] = banner
    return write(path, bytes(blob))


class FirmwareAcceptanceTests(unittest.TestCase):
    """A file is only firmware if it can be told apart from everything else."""

    def test_accepts_a_firmware_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fake_firmware(os.path.join(tmp, "OVMF_CODE.fd"))
            info = ovmf.describe_firmware(path)
        self.assertTrue(info["ok"], info["problems"])
        self.assertTrue(info["is_firmware_volume"])
        self.assertEqual(len(info["sha256"]), 64)

    def test_refuses_a_file_without_a_volume_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(os.path.join(tmp, "not-firmware.fd"), bytes(1024 * 1024))
            info = ovmf.describe_firmware(path)
        self.assertFalse(info["ok"])
        self.assertIn("_FVH", " ".join(info["problems"]))

    def test_refuses_a_file_that_is_really_the_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fake_firmware(os.path.join(tmp, "sneaky.fd"),
                                 banner=b"PC5 UEFI LOADER uefi-0.2.0")
            info = ovmf.describe_firmware(path)
        self.assertFalse(info["ok"])
        self.assertTrue(any("loader banner" in problem for problem in info["problems"]))

    def test_refuses_an_implausibly_sized_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fake_firmware(os.path.join(tmp, "tiny.fd"), size=4096)
            info = ovmf.describe_firmware(path)
        self.assertFalse(info["ok"])
        self.assertTrue(any("size" in problem for problem in info["problems"]))

    def test_the_real_esp_is_not_firmware(self):
        esp = os.path.join(ROOT, "build", "efi.img")
        if not os.path.isfile(esp):
            self.skipTest("build/efi.img is not built")
        info = ovmf.describe_firmware(esp)
        self.assertFalse(info["ok"], "the ESP was accepted as firmware")


class SerialLogTests(unittest.TestCase):
    """The firmware console mirror is firmware behaviour, not loader output."""

    def test_consecutive_duplicates_are_collapsed(self):
        raw = ("PC5-UEFI: firmware_started\r\n"
               "PC5-UEFI: firmware_started\r\n"
               "PC5-UEFI: kernel_volume=image\r\n")
        self.assertEqual(ovmf.normalise_serial(raw),
                         "PC5-UEFI: firmware_started\nPC5-UEFI: kernel_volume=image")

    def test_repeated_lines_that_are_not_adjacent_survive(self):
        raw = "A\r\nB\r\nA\r\n"
        self.assertEqual(ovmf.normalise_serial(raw), "A\nB\nA")

    def test_character_doubling_is_not_silently_repaired(self):
        # A character-doubled line is a loader bug (one write per character to
        # two sinks); the harness must not "fix" it and pretend the log was fine.
        self.assertEqual(ovmf.normalise_serial("PPCC55--UUEEFFII\r\n"),
                         "PPCC55--UUEEFFII")


class ClassificationTests(unittest.TestCase):
    def run_of(self, text):
        return {"serial": ovmf.normalise_serial(text)}

    def test_a_uefi_boot_is_recognised(self):
        seen = ovmf.classify(self.run_of(
            "BdsDxe: starting Boot0001\r\n"
            "PC5 UEFI loader uefi-0.2.0\r\n"
            "PC5-UEFI: firmware=EDK II\r\n"
            "PC5-UEFI: kernel_volume=image\r\n"
            "PC5-UEFI: kernel_bytes_read=66836\r\n"
            "PC5-UEFI: boot_services_exited\r\n"
            "PC5-UEFI: memory_map_entries=40\r\n"
            "PC5-LOG kernel_entry eip=0x10000\r\n"
            "PC5-LOG protocol=1 magic=ok\r\n"
            "PC5-BOOT-OK\r\n"))
        for key in ("firmware_banner", "loader_banner", "kernel_volume_image",
                    "kernel_read", "boot_services_exited", "kernel_entry",
                    "kernel_protocol", "boot_ok"):
            self.assertTrue(seen[key], key)
        self.assertEqual(seen["memory_map_entries"], 40)

    def test_a_seabios_boot_is_not_uefi(self):
        seen = ovmf.classify(self.run_of(
            "SeaBIOS (version rel-1.16)\r\nPC5 loader\r\nPC5-BOOT-OK\r\n"))
        self.assertTrue(seen["seabios"])
        self.assertFalse(seen["firmware_banner"])

    def test_a_loader_failure_code_is_extracted(self):
        seen = ovmf.classify(self.run_of("PC5-UEFI-FAILURE E2\r\n"))
        self.assertEqual(seen["failure_codes"], ["E2"])
        self.assertFalse(seen["boot_ok"])

    def test_firmware_refusal_text_is_recognised(self):
        seen = ovmf.classify(self.run_of(
            "BdsDxe: No bootable option or device was found.\r\n"))
        self.assertTrue(seen["firmware_refused"])


class ReportTests(unittest.TestCase):
    """Three verdicts: a path that was not tested is not a pass."""

    def test_not_applicable_is_neither_pass_nor_fail(self):
        report = ovmf.Report()
        report.check("a real check", True)
        report.check("disk-style boot", False, "no ESP partition", applicable=False)
        self.assertEqual(report.fail_count(), 0)
        self.assertEqual(report.skip_count(), 1)
        states = [check["state"] for check in report.checks]
        self.assertEqual(states, ["pass", "skip"])
        self.assertTrue(report.as_dict()["passed"])
        self.assertEqual(report.as_dict()["not_applicable"], 1)

    def test_a_failure_fails_the_report(self):
        report = ovmf.Report()
        report.check("a real check", False, "it broke")
        self.assertEqual(report.fail_count(), 1)
        self.assertFalse(report.as_dict()["passed"])

    def test_skipped_checks_never_carry_a_pass_state(self):
        report = ovmf.Report()
        report.check("anything", True, applicable=False)
        self.assertEqual(report.checks[0]["state"], "skip")


class ExitCodeTests(unittest.TestCase):
    """The gate's most important property: absence is never success."""

    def run_harness(self, *extra, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            iso = write(os.path.join(tmp, "pc5.iso"), bytes(4096))
            out = os.path.join(tmp, "report.json")
            environment = dict(os.environ)
            for key in ("PC5_QEMU", "PC5_OVMF_CODE", "PC5_OVMF_VARS"):
                environment.pop(key, None)
            environment.update(env or {})
            result = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tests", "ovmf_boot.py"),
                 "--iso", iso, "--json-out", out] + list(extra),
                cwd=ROOT, capture_output=True, text=True, env=environment,
                timeout=300)
            report = None
            if os.path.isfile(out):
                with open(out) as handle:
                    report = json.load(handle)
            return result, report

    def test_missing_firmware_is_not_tested_not_passed(self):
        result, report = self.run_harness()
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn("NOT TESTED", result.stdout)
        self.assertEqual(report["data"]["status"], "not-tested")
        self.assertTrue(report["data"]["blockers"])
        self.assertIsNone(report["success_marker"])

    def test_a_bogus_firmware_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bogus = write(os.path.join(tmp, "code.fd"), bytes(1024 * 1024))
            result, report = self.run_harness("--ovmf-code", bogus,
                                              "--ovmf-vars", bogus)
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertTrue(any("not usable OVMF" in blocker
                            for blocker in report["data"]["blockers"]))


class IsolationTests(unittest.TestCase):
    def test_nothing_in_the_repository_is_a_firmware_image(self):
        # The harness refuses "firmware" that is not a volume; this checks the
        # other direction: no firmware is carried in the source tree.
        for name in ("boot", "kernel", "tools", "tests", "docs"):
            for base, _dirs, files in os.walk(os.path.join(ROOT, name)):
                for entry in files:
                    self.assertFalse(entry.lower().endswith((".fd", ".fv", ".rom")),
                                     os.path.join(base, entry))

    def test_the_gate_is_separate_from_the_other_gates(self):
        with open(os.path.join(ROOT, "Makefile")) as handle:
            makefile = handle.read()
        self.assertIn("ovmf-test:", makefile)
        # `make test` must not silently absorb a gate that most machines cannot
        # run; it stays its own target with its own exit codes.
        self.assertNotIn("test: check boot-test stubtest ovmf-test", makefile)
        self.assertIn("test: check boot-test stubtest", makefile)


GOOD_SERIAL = """\r\nPC5 UEFI LOADER uefi-0.2.0\r\n
PC5-UEFI: firmware_started\r\n
PC5-UEFI: kernel_bytes_read=66836\r\n
PC5-UEFI: watchdog_disabled\r\n
PC5-UEFI: boot_services_exited\r\n
PC5-UEFI: memory_map_entries=6\r\n
PC5-UEFI: ExitBootServices_retries=1\r\n
PC5-UEFI: handover_window_checked=1\r\n
PC5-UEFI: memory_conventional_mib=127\r\n
PC5-UEFI: kernel_handoff_verified=1\r\n
PC5-UEFI: handoff_bytes=66836\r\n
PC5-LOG kernel_entry eip=0x10000\r\n
PC5-LOG protocol=1 magic=ok\r\n
PC5-LOG memory_mib=127 entries=6 top_mib=1024\r\n
PC5-BOOT-OK\r\n"""


class BootContractMarkerTests(unittest.TestCase):
    """The harness must read what the loader and the kernel actually printed.

    Every one of these markers is a claim about a boot transition, so each is
    parsed from the serial log and none of them is inferred from another.
    """

    def setUp(self):
        self.seen = ovmf.classify({"serial": GOOD_SERIAL})

    def test_handoff_evidence_is_read(self):
        self.assertTrue(self.seen["handoff_verified"])
        self.assertEqual(self.seen["handoff_bytes"], 66836)
        self.assertEqual(self.seen["kernel_bytes_read"], 66836)

    def test_memory_map_evidence_is_read(self):
        self.assertEqual(self.seen["memory_map_entries"], 6)
        self.assertEqual(self.seen["conventional_mib"], 127)
        self.assertEqual(self.seen["handover_window_checked"], 1)

    def test_exit_boot_services_evidence_is_read(self):
        self.assertTrue(self.seen["boot_services_exited"])
        self.assertEqual(self.seen["exit_retries"], 1)

    def test_watchdog_evidence_is_read(self):
        self.assertEqual(self.seen["watchdog"], "disabled")
        refused = ovmf.classify({"serial": "PC5-UEFI: watchdog_disable_refused=0x8000000000000003"})
        self.assertEqual(refused["watchdog"], "refused")
        self.assertIsNone(ovmf.classify({"serial": "nothing here"})["watchdog"])

    def test_the_kernels_own_memory_view_is_read_separately(self):
        self.assertEqual(self.seen["kernel_memory_entries"], 6)
        self.assertEqual(self.seen["kernel_memory_mib"], 127)

    def test_a_missing_marker_is_none_not_zero(self):
        seen = ovmf.classify({"serial": "PC5-BOOT-OK"})
        self.assertIsNone(seen["memory_map_entries"])
        self.assertIsNone(seen["handoff_bytes"])
        self.assertFalse(seen["handoff_verified"])


class SecureBootTests(unittest.TestCase):
    """Secure Boot is judged by the refusal, because PC5 signs nothing."""

    def test_a_boot_with_the_policy_on_is_a_failure(self):
        ok, detail = ovmf.secure_boot_verdict({"boot_ok": True})
        self.assertFalse(ok)
        self.assertIn("not in force", detail)

    def test_a_refused_image_is_the_expected_result(self):
        ok, _ = ovmf.secure_boot_verdict({"boot_ok": False, "firmware_refused": True})
        self.assertTrue(ok)


class MatrixTests(unittest.TestCase):
    """The required matrix must never claim a row that no run produced."""

    def make_report(self):
        report = ovmf.Report()
        return report

    def test_without_runs_every_executed_row_is_not_tested(self):
        report = self.make_report()
        rows = ovmf.build_matrix(report)
        states = {row["state"] for row in rows}
        self.assertIn("not-tested", states)
        for row in rows:
            self.assertIn(row["state"], ("not-tested", "external", "verified", "failed"))
        executed = [r for r in rows if r["state"] == "verified"]
        self.assertEqual(executed, [])

    def test_external_rows_name_another_gate_and_never_say_verified(self):
        rows = ovmf.build_matrix(self.make_report())
        external = [r for r in rows if r["state"] == "external"]
        self.assertTrue(external)
        for row in external:
            self.assertNotEqual(row["source"], "OVMF cold boot")
            self.assertIn("(", row["source"])

    def test_a_good_cold_boot_marks_the_boot_rows_verified(self):
        report = self.make_report()
        report.data["cold"] = {"markers": ovmf.classify({"serial": GOOD_SERIAL})}
        rows = {r["requirement"]: r for r in ovmf.build_matrix(report)}
        self.assertEqual(rows["the PC5 EFI loader executes"]["state"], "verified")
        self.assertEqual(rows["the kernel handoff succeeds"]["state"], "verified")

    def test_a_failed_cold_boot_marks_them_not_tested(self):
        report = self.make_report()
        report.data["cold"] = {"markers": ovmf.classify(
            {"serial": "PC5-UEFI FAILURE E15: window\r\nPC5-UEFI-FAILURE E15\r\n"})}
        rows = {r["requirement"]: r for r in ovmf.build_matrix(report)}
        self.assertEqual(rows["the kernel handoff succeeds"]["state"], "not-tested")

    def test_a_negative_run_that_booted_is_a_failure_row(self):
        report = self.make_report()
        report.data["negative"] = {"no-kernel": {"markers": ovmf.classify(
            {"serial": "PC5 UEFI loader\r\nPC5-BOOT-OK\r\n"})}}
        rows = {r["requirement"]: r for r in ovmf.build_matrix(report)}
        self.assertEqual(rows["a missing kernel is an explicit failure"]["state"], "failed")

    def test_the_low_memory_row_exists(self):
        rows = {r["requirement"] for r in ovmf.build_matrix(self.make_report())}
        self.assertIn("insufficient memory fails safely", rows)


if __name__ == "__main__":
    unittest.main()
