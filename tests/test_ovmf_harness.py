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

import ast
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
from types import SimpleNamespace
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

    def test_the_vars_template_is_copied_to_the_work_dir_before_qemu_starts(self):
        """The whole function, driven with the types its signature asks for.

        `secure_boot_run(report, args, qemu, data_dir, work, iso, code, profile,
        vars_template)` was called with the last two arguments the other way round, so
        `shutil.copyfile(vars_template, ...)` was handed a `Profile` and raised
        `TypeError: stat: path should be string, bytes, os.PathLike or integer, not
        Profile`.  That is the first statement in the function: the gate died before QEMU
        was ever launched, and the Secure Boot assertion was never graded.  This pins what
        the *function* promises -- argument nine is a path, argument eight is a profile --
        which is half the story; the other half, that the call site honours it, is
        `HarnessCallShapeTests`, because a contract test cannot see the caller.
        """
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "work")
            os.makedirs(work)
            vars_template = write(os.path.join(tmp, "OVMF_VARS.ms.fd"), b"VARS-TEMPLATE")
            code = fake_firmware(os.path.join(tmp, "OVMF_CODE.secboot.fd"))
            iso = write(os.path.join(tmp, "pc5.iso"), b"")
            calls = []
            original = ovmf.run_qemu

            def fake_run_qemu(qemu, data_dir, media, vars_path, serial_path, timeout,
                              memory_mb, secure=False, stop_markers=None):
                calls.append({"media": media, "vars_path": vars_path, "secure": secure,
                              "stop_markers": stop_markers})
                return {"command": ["true"], "serial": "", "seconds": 0.0,
                        "stopped_on": "exit"}

            ovmf.run_qemu = fake_run_qemu
            try:
                seen = ovmf.secure_boot_run(
                    ovmf.Report(), SimpleNamespace(timeout=10, memory_mb=256), "qemu",
                    os.path.join(tmp, "data"), work, iso, code, ovmf.IA32, vars_template)
            finally:
                ovmf.run_qemu = original
            with open(os.path.join(work, "vars-secure.fd"), "rb") as handle:
                copied = handle.read()
            self.assertEqual(copied, b"VARS-TEMPLATE",
                             "the writable copy of the vars template is what QEMU boots "
                             "from; this is the line that raised")
            self.assertEqual(len(calls), 1, "run_qemu was never reached -- the crash again")
            self.assertEqual(calls[0]["media"]["code"], code,
                              "`code_path` must be the firmware, not the vars template")
            self.assertEqual(calls[0]["vars_path"], os.path.join(work, "vars-secure.fd"))
            self.assertTrue(calls[0]["secure"], "this is the Secure Boot run; the flag is "
                                                "what makes the policy in force")
            self.assertEqual(calls[0]["stop_markers"], ovmf.IA32.stop,
                              "`profile` reached the function that uses it")
            self.assertFalse(seen["boot_ok"])
            self.assertTrue(seen["firmware_refused"] or not seen["kernel_entry"],
                            "an unsigned image with the policy on must read as a refusal")

    def test_a_passing_object_for_the_vars_template_is_still_a_type_error(self):
        """The inverse: a `Profile` in the path slot must fail, not silently skip."""
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "work")
            os.makedirs(work)
            with self.assertRaises(TypeError):
                ovmf.secure_boot_run(ovmf.Report(), SimpleNamespace(timeout=1, memory_mb=256),
                                     "qemu", tmp, work, os.path.join(tmp, "pc5.iso"),
                                     os.path.join(tmp, "code.fd"), ovmf.IA32, ovmf.IA32)


class HarnessCallShapeTests(unittest.TestCase):
    """Every internal call in the harness, bound against its own definition.

    Eleven hosted runs died on the transposition below and no local run could see it,
    because the branch needs *both* Secure Boot firmware files and a developer machine has
    either neither or (rarely) both.  A checker that reads the two names side by side finds
    it in either case, on any machine, with no firmware installed.
    """

    #: Names that describe *what* an argument is, so a mismatch means a swap rather than a
    #: rename.  `work`, `args`, `report` and `out` are excluded by requiring the parameter
    #: to carry a role token too.
    ROLES = frozenset(("profile", "vars", "code", "iso", "image", "template", "serial"))

    def _calls(self):
        with open(os.path.join(ROOT, "tests", "ovmf_boot.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        defs = {n.name: [a.arg for a in n.args.args]
                for n in tree.body if isinstance(n, ast.FunctionDef)}
        return tree, defs

    def test_no_positional_call_hands_a_role_to_the_wrong_parameter(self):
        tree, defs = self._calls()
        offenders = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            params = defs.get(node.func.id)
            if not params or len(params) < 4 or len(node.args) != len(params):
                continue          # different arity is a TypeError, which Python already sees
            for arg, param in zip(node.args, params):
                if not isinstance(arg, ast.Name):
                    continue
                at = set(arg.id.split("_"))
                pt = set(param.split("_"))
                if not (at & self.ROLES) or not (pt & self.ROLES):
                    continue      # neither side is role-shaped; nothing to compare
                if not (at & pt):
                    offenders.append("%d: %s(%s=%s)" % (node.lineno, node.func.id, param,
                                                         arg.id))
        self.assertEqual(offenders, [],
                         "a positional argument whose role name matches no parameter is a "
                         "swap waiting to happen (or, if it has one, the bug):\n"
                         + "\n".join(offenders))

    def test_the_secure_boot_call_site_names_the_arguments_it_cannot_be_trusted_to_order(self):
        """The fix itself: three same-shaped arguments, each bound by name.

        Asserted against the *source*, because a call that uses keywords cannot be
        transposed by the next edit and a call that uses positions can -- which is how this
        file lost eleven runs.  `vars_template` must be the vars file and `profile` must be
        the profile; those two are the pair that was swapped.
        """
        with open(os.path.join(ROOT, "tests", "ovmf_boot.py"), encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        params = [a.arg for a in defs["secure_boot_run"].args.args]
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "secure_boot_run"]
        self.assertEqual(len(calls), 1, "the harness should call it from exactly one place")
        call = calls[0]
        bound = {}
        for i, arg in enumerate(call.args):
            self.assertLess(i, len(params), "one positional argument too many")
            bound[params[i]] = ast.get_source_segment(source, arg)
        for kw in call.keywords:
            self.assertIn(kw.arg, params, "%s is not a parameter of secure_boot_run" % kw.arg)
            self.assertNotIn(kw.arg, bound, "%s was passed twice" % kw.arg)
            bound[kw.arg] = ast.get_source_segment(source, kw.value)
        self.assertEqual(sorted(bound), sorted(params),
                         "every parameter must be bound exactly once: %s" % sorted(bound))
        for name, want in (("profile", "profile"),
                          ("vars_template", 'found["secure_vars"]'),
                          ("code_path", 'found["secure_code"]')):
            self.assertEqual(bound[name], want,
                             "`%s` should be %s, and the call gives it %s"
                             % (name, want, bound[name]))
        for kw in call.keywords:
            self.assertIn(kw.arg, ("code_path", "profile", "vars_template"),
                          "the three role-shaped arguments are named; a positional one "
                          "could be transposed again without any checker noticing")


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

    def test_a_good_cold_boot_verifies_the_rows_it_claims(self):
        """Rows come from the run's own checks now, not from a shared predicate.

        The bug this replaced: `build_matrix` judged every cold row with one blanket
        expression (`boot_ok and loader_banner and kernel_entry`), so a row could read
        VERIFIED while the check that is supposed to answer it had failed -- and a row
        could be renamed out of existence without the matrix noticing.  A row is now
        verified exactly when its own check passed, so this test has to feed the
        harness a complete, real log.
        """
        from test_ovmf_gate_profiles import IA32_GOOD_LOG
        report = self.make_report()
        ovmf.check_boot(report, {"serial": IA32_GOOD_LOG, "raw_serial": IA32_GOOD_LOG,
                                 "seconds": 4.0, "killed": False,
                                 "stopped_on": "PC5-BOOT-OK", "returncode": -9,
                                 "command": [], "stderr": ""},
                        "cold", 67028, ovmf.IA32, 256)
        rows = {row["requirement"]: row for row in ovmf.build_matrix(report, ovmf.IA32)}
        cold = [row for row in rows.values()
                if row["source"] == "OVMF cold boot"]
        self.assertEqual(len(cold), len(ovmf.ia32_stages()))
        self.assertEqual([row["state"] for row in cold], ["verified"] * len(cold),
                         [(row["requirement"], row.get("note")) for row in cold
                          if row["state"] != "verified"])

    def test_a_row_that_names_no_check_is_not_tested(self):
        """The guard against prose that drifted away from the code.

        A row whose check name nobody emits cannot be satisfied by some other green
        check, and must not be reported as failed either -- the honest answer is that
        this run did not answer the question.
        """
        report = self.make_report()
        rows = ovmf.build_matrix(report, ovmf.X64)
        invented = {"requirement": "an invented requirement",
                    "source": "OVMF cold boot", "check": "[cold] nothing of the sort",
                    "state": "not-tested"}
        rows.append(invented)
        self.assertEqual(invented["state"], "not-tested")
        # And with no run at all, no row may claim to be verified.
        self.assertFalse([row for row in rows if row["state"] == "verified"])

    def test_a_negative_row_follows_its_own_check(self):
        """`deadline` and `small-memory` succeed without refusing anything.

        They used to be judged by "did it refuse?", which reported a `failed` row on a
        run whose checks had all passed.  The check is now the only judge, for every
        control, so a row reads verified exactly when the control's own check did.
        """
        report = self.make_report()
        report.check(ovmf.DEADLINE_CHECK, True)
        rows = {row["requirement"]: row for row in ovmf.build_matrix(report, ovmf.IA32)}
        self.assertEqual(rows[ovmf.DEADLINE_CHECK]["state"], "verified")
        report = self.make_report()
        report.check(ovmf.DEADLINE_CHECK, False, "the boot finished inside the budget")
        rows = {row["requirement"]: row for row in ovmf.build_matrix(report, ovmf.IA32)}
        self.assertEqual(rows[ovmf.DEADLINE_CHECK]["state"], "failed")
        self.assertIn("the boot finished inside the budget",
                      rows[ovmf.DEADLINE_CHECK]["note"])

    def test_the_control_rows_are_row_and_check_in_one_name(self):
        """One name per control, for both profiles -- including the 64-bit one.

        The bug this pins: a matrix row worded differently from the check that drove
        it, so the lookup missed and the generic "did it refuse?" rule applied, and
        rows read `failed` while every check passed.  `required_rows` now *returns* the
        check name, so there is no second spelling to drift.
        """
        for profile in (ovmf.IA32, ovmf.X64):
            rows = {requirement: (source, check)
                    for requirement, source, check in ovmf.required_rows(profile)}
            for name in (ovmf.DEADLINE_CHECK, ovmf.SMALL_MEMORY_CHECK,
                         ovmf.BAD_PE_CHECK, ovmf.NO_BOOT_APP_CHECK,
                         ovmf.TRUNCATED_CHECK, profile.no_kernel_check()):
                self.assertIn(name, rows, "%s: %r is not a row" % (profile.key, name))
                source, check = rows[name]
                self.assertEqual(check, name)
                self.assertTrue(source.startswith("OVMF negative run"), source)
        source_text = pathlib.Path(ROOT, "tests", "ovmf_boot.py").read_text()
        for name in (ovmf.DEADLINE_CHECK, ovmf.SMALL_MEMORY_CHECK, ovmf.BAD_PE_CHECK):
            # The sentence appears once in the file: as the constant's own
            # definition.  Anywhere else it would be a duplicated literal free to
            # drift, which is the bug this guards.
            self.assertEqual(source_text.count('"%s' % name[:34]), 1,
                             "%r is spelled out somewhere besides its definition"
                             % name[:20])

    def test_one_broken_stage_fails_one_row(self):
        """The precision the milestone asked for, on the row level.

        Dropping the read-back line must fail the handoff row alone: not the boot's
        whole stage set, and -- the old bug -- not the handoff row *not* at all.
        """
        from test_ovmf_gate_profiles import X64_GOOD_LOG
        crippled = "\n".join(
            line for line in X64_GOOD_LOG.splitlines()
            if "the kernel copy was read back" not in line and "range=ok" not in line
            and not line.startswith("PC5-64-IMAGE"))
        report = self.make_report()
        ovmf.check_boot(report, {"serial": crippled, "raw_serial": crippled,
                                 "seconds": 4.0, "killed": False,
                                 "stopped_on": "PC5-64-DONE", "returncode": -9,
                                 "command": [], "stderr": ""},
                        "cold", 204800, ovmf.X64, 256)
        rows = {row["requirement"]: row for row in ovmf.build_matrix(report, ovmf.X64)}
        self.assertEqual(rows["the kernel handoff succeeds"]["state"], "failed")
        self.assertEqual(rows["the kernel reaches its 64-bit success marker"]["state"],
                         "verified")

    def test_a_failed_matrix_row_makes_the_run_fail(self):
        """The exit status and the matrix cannot disagree."""
        report = ovmf.Report()
        ovmf.build_matrix(report)
        rows = report.data["matrix"]
        rows.append({"requirement": "invented", "source": "x", "state": "failed"})
        before = report.fail_count()
        ovmf.matrix_failures(report, rows)
        self.assertEqual(report.fail_count(), before + 1)

    def test_the_small_memory_row_exists(self):
        """Renamed with the control: the row is now about a *small* machine, because
        the starved one (-m 24) never reaches firmware far enough to say anything
        about PC5 -- see tests/ovmf_boot.py's comment on the check."""
        rows = {r["requirement"] for r in ovmf.build_matrix(self.make_report())}
        self.assertIn("a small machine stops with a diagnostic or boots with its "
                      "own memory", rows)
        self.assertNotIn("insufficient memory fails safely", rows)

    def test_a_negative_without_its_check_is_never_verified(self):
        """A refusal in the log is not the verdict; the control's check is.

        `build_matrix` used to fall back to "did this run refuse or print a failure
        code?" for any negative row whose check name it could not find.  That is how a
        row came to read VERIFIED because the emulator happened to stop early: a mutant
        that was never built, never booted, or was starved by firmware before PC5 ran
        looks exactly like a control that worked.  Now a row is verified only when its
        own check ran, and a row whose check is missing says NOT TESTED -- including
        this case, where the markers do show a refusal.
        """
        report = self.make_report()
        report.data.setdefault("negative", {})[
            ovmf.IA32.no_kernel_check()] = {
                "markers": {"boot_ok": False, "failure_codes": ["E2"]}}
        rows = {row["requirement"]: row
                for row in ovmf.build_matrix(report, ovmf.IA32)}
        self.assertEqual(rows[ovmf.IA32.no_kernel_check()]["state"], "not-tested")
        # And with the check emitted, the same row follows it in both directions.
        report.check(ovmf.IA32.no_kernel_check(), True)
        rows = {row["requirement"]: row
                for row in ovmf.build_matrix(report, ovmf.IA32)}
        self.assertEqual(rows[ovmf.IA32.no_kernel_check()]["state"], "verified")
        report = self.make_report()
        report.check(ovmf.IA32.no_kernel_check(), False, "the mutant booted anyway")
        rows = {row["requirement"]: row
                for row in ovmf.build_matrix(report, ovmf.IA32)}
        self.assertEqual(rows[ovmf.IA32.no_kernel_check()]["state"], "failed")

    def test_a_duplicated_control_name_is_a_defect(self):
        """One name per control, defined once, for both profiles.

        The bug this pins: the matrix row was worded differently from the check that
        drove it, so the lookup missed, the fallback rule judged the row, and both
        `deadline` and `small-memory` read `failed` on a run whose checks all passed.
        """
        for profile in (ovmf.IA32, ovmf.X64):
            rows = {requirement: (source, check) for requirement, source, check
                    in ovmf.required_rows(profile)}
            for name in (ovmf.DEADLINE_CHECK, ovmf.SMALL_MEMORY_CHECK,
                         ovmf.BAD_PE_CHECK, ovmf.NO_BOOT_APP_CHECK,
                         ovmf.TRUNCATED_CHECK, profile.no_kernel_check()):
                self.assertIn(name, rows, "%s: %r is not a row" % (profile.key, name))
                source, check = rows[name]
                self.assertEqual(check, name)
                self.assertTrue(source.startswith("OVMF negative run"), source)
        source_text = pathlib.Path(ROOT, "tests", "ovmf_boot.py").read_text()
        for name in (ovmf.DEADLINE_CHECK, ovmf.SMALL_MEMORY_CHECK,
                     ovmf.BAD_PE_CHECK, ovmf.NO_BOOT_APP_CHECK, ovmf.TRUNCATED_CHECK):
            # Each control's wording appears exactly once in the file -- in the
            # constant.  A second occurrence anywhere (a check call, a comment that
            # restates it) is a second spelling, and the second spelling is what drifts
            # and silently unbinds a row from its check.
            fragment = name[:20]
            self.assertEqual(source_text.count(fragment), 1,
                             "%r is spelled out somewhere besides its definition"
                             % fragment)

    def test_the_boot_rows_come_from_the_stage_table(self):
        """The rows the milestone lists are the rows the harness judges.

        A stage added to the table has to appear as a row, and a row has to name a
        check; neither list is maintained by hand any more, so this asserts the
        derivation for both profiles and the profiles' distinctness.
        """
        for profile, stages in ((ovmf.IA32, ovmf.ia32_stages()),
                                (ovmf.X64, ovmf.x64_stages())):
            rows = [(requirement, source, check)
                    for requirement, source, check in ovmf.required_rows(profile)
                    if source == "OVMF cold boot"]
            self.assertEqual([requirement for requirement, _s, _c in rows],
                             [stage.requirement for stage in stages])
            self.assertEqual([check for _r, _s, check in rows],
                             ["[cold] " + stage.check for stage in stages])
class FirmwareEvidenceTests(unittest.TestCase):
    """The row that fails on the firmware's *name* must say which name it saw.

    Run 7's firmware job reached this row for real and reported it as
    `loader_firmware_edk2=False`.  That is a true statement and an useless one: three
    different defects produce it -- the firmware told the loader something else, the loader
    printed it with different spacing, or nothing was printed at all -- and the serial log
    already distinguishes them.  Quoting the observed string is the half of §39's option 1
    that changes no contract; the other half (whether the row should require the literal
    `EDK II` at all) is a decision about what the gate *means* and stays open.
    """

    #: (the line the loader prints, the profile that reads it).  `classify` asks the
    #: *profile* which words to look for -- the two paths use different prefixes on
    #: purpose -- so a 64-bit line classified as IA32 proves nothing either way, and each
    #: case below has to say which path it is speaking about.
    IA32 = ("PC5-UEFI: firmware=%s\r\n", "IA32")
    SIXTY4 = ("PC5-64-LOADER firmware=%s\r\n", "X64")

    def _seen(self, shape, path):
        template, profile = path
        return ovmf.classify({"serial": template % shape}, profile=getattr(ovmf, profile))

    def _row(self, shape, path):
        return ovmf._firmware_named("loader_firmware_edk2", "loader_firmware_said")(
            self._seen(shape, path), {})

    def test_the_required_name_still_passes_and_says_nothing(self):
        ok, detail = self._row("EDK II", self.IA32)
        self.assertTrue(ok, "the predicate is unchanged: a pass must stay a pass")
        self.assertEqual(detail, "")

    def test_a_different_name_is_quoted_not_summarised(self):
        ok, detail = self._row("EFI Development Kit II", self.IA32)
        self.assertFalse(ok)
        self.assertIn("loader_firmware_edk2=False", detail,
                      "the old wording survives inside the new one; anything reading the "
                      "detail for the key must still find it")
        self.assertIn("EFI Development Kit II", detail)

    def test_an_absent_line_says_it_is_absent(self):
        seen = ovmf.classify({"serial": "PC5-UEFI: watchdog_disable_refused=3"})
        ok, detail = ovmf._firmware_named("loader_firmware_edk2", "loader_firmware_said")(seen, {})
        self.assertFalse(ok)
        self.assertIn("no `firmware=` line at all", detail)
        self.assertNotIn("firmware=''", detail, "an absent string and an empty one are "
                                                "different facts; only the second may be quoted")

    def test_the_64_bit_path_carries_the_same_evidence(self):
        ok, detail = self._row("EDK Entropy", self.SIXTY4)
        self.assertFalse(ok)
        self.assertIn("EDK Entropy", detail,
                      "the two paths print the line with different prefixes, and the hosted "
                      "failure could come from either")

    def test_quoting_can_never_move_the_verdict(self):
        # The strongest form of the claim: for every serial shape, the boolean this proof
        # returns is the boolean `_seen` returns.  A diagnostic that could flip a row would
        # be a new gate, which is what the whole `if: failure()` discipline is there to stop.
        shapes = ["EDK II", "edk ii", "EDK II  ", "EDK", "", "SeaBIOS",
                  "EDK II (ACCID=0x6564)", "0x6564 EDK II"]
        for path in (self.IA32, self.SIXTY4):
            for shape in shapes:
                with self.subTest(shape=shape, profile=path[1]):
                    seen = self._seen(shape, path)
                    plain = ovmf._seen("loader_firmware_edk2")(seen, {})[0]
                    quoted = ovmf._firmware_named("loader_firmware_edk2",
                                                  "loader_firmware_said")(seen, {})[0]
                    self.assertEqual(plain, quoted)


class FirmwareIdentityContractTests(unittest.TestCase):
    """ADR-026: the row asserts an *implementation* and records a *distribution*.

    The hosted runner's OVMF prints `Ubuntu distribution of EDK II`, because Ubuntu
    overrides edk2's build-time `PcdFirmwareVendor` (upstream value: the literal
    `EDK II`, which is what all sixteen genuine serial logs under `docs/evidence/`
    contain).  The predicate that was here before was a substring test, so it was loose at
    one end and strict at the other: it accepted `EDK II-beta` and refused the Ubuntu
    build.  The contract below is anchored, keeps the firmware's own words verbatim, and
    refuses anything it cannot name -- including this repository's own stub, which sets
    the same system-table field to `PC5 stub firmware` and must never certify a real boot.

    Both profiles are exercised because the two loaders print the field differently: the
    32-bit line ends at the vendor string and prints nothing at all when the field is
    missing, while the 64-bit line continues with ` revision=`/` table-rev=` and spells a
    NULL pointer `absent` and a zero-length string `empty`.
    """

    #: (what the firmware printed, accepted?, implementation, distribution).  Every row is
    #: checked on both profiles, so the contract is one rule and not two dialects.
    MATRIX = [
        ("EDK II", True, ovmf.EDK2, "upstream default"),
        ("Ubuntu distribution of EDK II", True, ovmf.EDK2, "Ubuntu distribution"),
        ("Fedora distribution of EDK II", True, ovmf.EDK2, "Fedora distribution"),
        ("  EDK II  ", True, ovmf.EDK2, "upstream default"),
        # D3: the tightening.  The old substring test let all four of these through.
        ("EDK II (ACCID=0x6564)", False, "UNKNOWN", None),
        ("EDK IIx", False, "UNKNOWN", None),
        ("edk ii", False, "UNKNOWN", None),
        ("EDK II-like", False, "UNKNOWN", None),
        # D2: plausible but unevidenced.  Refused as UNKNOWN, and named, so adding one later
        # is a decision taken against a captured log rather than a widening typed in now.
        ("TianoCore", False, "UNKNOWN", None),
        ("EFI Development Kit II", False, "UNKNOWN", None),
        ("OVMF", False, "UNKNOWN", None),
        # The stub, and a stub that renames itself to fit the grammar.
        ("PC5 stub firmware", False, None, None),
        ("PC5 of EDK II", False, None, None),
        # D4: three distinct absences, never one.
        ("absent", False, None, None),
        ("empty", False, None, None),
        # Row 11 (>= the profile's print limit) and row 12 (non-printable mangling).
        ("Ubuntu distribution of EDK II fir", False, "UNKNOWN", None),
        ("EDK II?", False, "UNKNOWN", None),
    ]

    LINES = {"ia32": "PC5-UEFI: firmware=%s\r\n",
             "x64": "PC5-64-LOADER firmware=%s revision=0x2 table-rev=0x10000\r\n"}

    def _ident(self, shape, key):
        return ovmf.classify({"serial": self.LINES[key] % shape},
                             profile=ovmf.IA32 if key == "ia32" else ovmf.X64)

    def test_every_row_grades_the_same_way_on_both_profiles(self):
        for shape, accepted, impl, dist in self.MATRIX:
            verdicts = {}
            for key in ("ia32", "x64"):
                seen = self._ident(shape, key)
                with self.subTest(shape=shape, profile=key):
                    self.assertEqual(seen["loader_firmware_edk2"], accepted)
                    self.assertEqual(seen["loader_firmware_impl"], impl)
                    self.assertEqual(seen["loader_firmware_distribution"], dist)
                    self.assertIsInstance(seen["loader_firmware_edk2"], bool,
                                          "the row's key must stay a bool: the matrix, the "
                                          "gate-profile tests and `--secure-boot` all read it")
                    self.assertEqual(bool(seen["loader_firmware_refusal"]), not accepted,
                                     "a refusal carries a reason, a pass carries none")
                verdicts[key] = accepted
            self.assertEqual(verdicts["ia32"], verdicts["x64"],
                             "one contract, not a dialect per profile")

    def test_the_raw_string_is_preserved_verbatim(self):
        # Nothing canonicalises the firmware's own words: the case, the padding and the
        # junk are the evidence, and a report that stored only the classification would
        # make ADR-026 unauditable.
        for key in ("ia32", "x64"):
            shape = "  Ubuntu distribution of EDK II  "
            raw = self.LINES[key] % shape
            seen = ovmf.classify({"serial": raw}, profile=ovmf.IA32 if key == "ia32" else ovmf.X64)
            with self.subTest(profile=key):
                self.assertEqual(seen["loader_firmware_said"], shape.strip(),
                                 "`_said` trims the line for comparison; the *content* is the "
                                 "firmware's own, unaltered")
                self.assertNotIn("EDK IIx", seen["loader_firmware_said"])
                self.assertEqual(ovmf.identity_record(seen)["raw"], shape.strip())

    def test_a_self_issued_label_can_never_certify_a_boot(self):
        # The stub firmware sets `SystemTable->FirmwareVendor` to `PC5 stub firmware`
        # itself.  Refusal is by shape (`SELF_ISSUED_MARK`) rather than by that literal, so
        # a stub that renamed itself into the accepted grammar is still refused -- which is
        # the one way this row could be satisfied without genuine firmware.
        self.assertTrue(ovmf.SELF_ISSUED_MARK in "PC5 stub firmware")
        for shape in ("PC5 stub firmware", "PC5 of EDK II", "EDK II by PC5"):
            for key in ("ia32", "x64"):
                with self.subTest(shape=shape, profile=key):
                    self.assertFalse(self._ident(shape, key)["loader_firmware_edk2"])
        self.assertIn("cannot certify genuine firmware",
                      self._ident("PC5 of EDK II", "ia32")["loader_firmware_refusal"])

    def test_absence_is_three_distinct_refusals(self):
        no_line = ovmf.classify({"serial": "PC5-UEFI: kernel_volume=image\r\n"})
        absent = self._ident("absent", "x64")
        empty = self._ident("empty", "x64")
        reasons = [no_line["loader_firmware_refusal"], absent["loader_firmware_refusal"],
                   empty["loader_firmware_refusal"]]
        for reason in reasons:
            self.assertTrue(reason, "every refusal must say which absence it is")
        self.assertEqual(len(set(reasons)), 3,
                         "NULL pointer, zero-length string and no line at all are three facts "
                         "about the machine; collapsing them loses the only signal naming which")
        self.assertIn("NULL", absent["loader_firmware_refusal"])
        self.assertIn("zero-length", empty["loader_firmware_refusal"])
        self.assertIn("no `firmware=` line", no_line["loader_firmware_refusal"])

    def test_truncation_is_ambiguous_rather_than_wrong(self):
        # `utf16_to_ascii` into a 32-byte buffer stops at 31 characters, so a firmware that
        # calls itself something longer cannot be told apart from one whose name merely got
        # cut.  Both are refused, but only the first may say "truncated" -- and a short
        # mismatch must not claim it.
        long_so_far = "Ubuntu distribution of EDK II fir"
        self.assertGreaterEqual(len(long_so_far), ovmf.PRINT_LIMIT["ia32"])
        self.assertIn("may be truncated", self._ident(long_so_far, "ia32")["loader_firmware_refusal"])
        short = "EDK IIx"
        self.assertLess(len(short), ovmf.PRINT_LIMIT["ia32"])
        self.assertNotIn("truncated", self._ident(short, "ia32")["loader_firmware_refusal"])
        self.assertIn("not the EDK II implementation token",
                      self._ident(short, "ia32")["loader_firmware_refusal"])

    def test_a_mangled_string_is_reported_as_mangled(self):
        refusal = self._ident("EDK II?", "ia32")["loader_firmware_refusal"]
        self.assertIn("non-printable", refusal,
                      "the loader substitutes `?`, so a refusal should say the encoding was "
                      "lost rather than leave a reader to spot the question mark")

    def test_the_distribution_is_recorded_and_nothing_gates_on_it(self):
        # D1.  The distributor goes into the report and the refusal detail, and no row keys
        # on it -- because anyone building EDK II can write any distributor name into that
        # PCD, so a gate on it would be a gate on a claim the string cannot support, and it
        # would go red on the next distro upgrade for a reason that is not a defect.  Checked
        # against the stage table itself, which is where a future widening would appear.
        for shape in ("Ubuntu distribution of EDK II", "Fedora distribution of EDK II"):
            seen = self._ident(shape, "ia32")
            self.assertTrue(seen["loader_firmware_edk2"])
            # Verbatim: the clause before ` of EDK II`, exactly as printed.  Trimming it to
            # `Ubuntu` would be interpreting the distribution -- the same overreach D1 rules
            # out, in the other direction.
            self.assertEqual(seen["loader_firmware_distribution"],
                             shape.split(" of EDK II")[0])
        for profile in (ovmf.IA32, ovmf.X64):
            for stage in ovmf.profile_stages(profile):
                text = " ".join((stage.requirement, stage.check, stage.name("cold"))).lower()
                self.assertNotIn("distribution", text,
                                 "a stage named for the distributor would assert what D1 "
                                 "forbids: %r" % text)
                self.assertNotIn("ubuntu", text)

    def test_the_64bit_marker_does_not_swallow_the_revision_fields(self):
        # The 64-bit loader continues the same line with ` revision=` and ` table-rev=`.  A
        # pattern that captured to end of line would read the identity as
        # `EDK II revision=0x2 table-rev=0x10000`, refuse it, and turn sixteen committed
        # genuine boots red -- the one way this change could have looked safe in isolation
        # and been wrong everywhere.
        seen = ovmf.classify({"serial":
                              "PC5-64-LOADER firmware=EDK II revision=0x2 table-rev=0x10000\r\n"},
                             profile=ovmf.X64)
        self.assertEqual(seen["loader_firmware_said"], "EDK II")
        self.assertTrue(seen["loader_firmware_edk2"])
        ubuntu = ovmf.classify({"serial":
                                "PC5-64-LOADER firmware=Ubuntu distribution of EDK II "
                                "revision=0x2 table-rev=0x10000\r\n"}, profile=ovmf.X64)
        self.assertTrue(ubuntu["loader_firmware_edk2"])
        self.assertEqual(ubuntu["loader_firmware_distribution"], "Ubuntu distribution")

    def test_the_row_verdict_is_the_classifiers_verdict(self):
        # The row and the marker must not be able to disagree: one predicate, read twice.
        proof = ovmf._firmware_named("loader_firmware_edk2", "loader_firmware_said")
        for shape, accepted, _impl, _dist in self.MATRIX:
            for key in ("ia32", "x64"):
                seen = self._ident(shape, key)
                with self.subTest(shape=shape, profile=key):
                    self.assertEqual(proof(seen, {})[0], accepted)

    def test_the_report_carries_the_identity_block(self):
        # The artifact, not just the annotation: an accepted boot records the raw string,
        # the implementation and the distribution; a refused one records all three too, with
        # the refusal, so a reader of `build/ovmf-report.json` can audit the verdict.
        run = {"serial": self.LINES["ia32"] % "Ubuntu distribution of EDK II",
               "killed": False, "stopped_on": "marker", "seconds": 1.0, "returncode": 0,
               "nmi": None}
        report = ovmf.Report()
        ovmf.check_boot(report, run, "cold")
        identity = report.data["cold"]["firmware_identity"]
        self.assertEqual(identity["raw"], "Ubuntu distribution of EDK II")
        self.assertEqual(identity["implementation"], "EDK II")
        self.assertEqual(identity["distribution"], "Ubuntu distribution")
        self.assertTrue(identity["accepted"])
        self.assertEqual(identity["refusal"], "")

        refused = dict(run, serial=self.LINES["ia32"] % "PC5 stub firmware")
        report = ovmf.Report()
        ovmf.check_boot(report, refused, "cold")
        identity = report.data["cold"]["firmware_identity"]
        self.assertFalse(identity["accepted"])
        self.assertIsNone(identity["implementation"])
        self.assertIn("cannot certify", identity["refusal"])
        self.assertEqual(identity["raw"], "PC5 stub firmware")


class MutantBuildFailureTests(unittest.TestCase):
    """A failed `mkiso.py` must arrive with its own complaint, not just an exit status.

    `build_mutant_iso` ran the repository's ISO tool with `check=True, capture_output=True`.
    The child died, said why, and every word of it landed in the exception's `stdout`/
    `.stderr` attributes, which nothing prints on the way out: the traceback that reaches
    the shell ends with `Command '[...]' returned non-zero exit status 1`, which names no
    cause.  That is the same hole `tools/ci_report.py` was just widened for, one level down
    -- the reporter can now carry an exception line, but here there was no exception *line*
    worth carrying.  Unlike the transposed `secure_boot_run` arguments, this one reproduces
    with no QEMU and no OVMF at all, because the child never gets as far as the ISO.
    """

    def _build_and_fail(self, tmp):
        """The one build that must die, with the harness's output captured and its `BUILD`
        redirected into `tmp` so nothing is written to the repository's own tree."""
        err, out = io.StringIO(), io.StringIO()
        saved, ovmf.BUILD = ovmf.BUILD, tmp
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    ovmf.build_mutant_iso("pc5-no-input", os.path.join(tmp, "absent.esp"))
        finally:
            ovmf.BUILD = saved
        return caught.exception, err.getvalue()

    def test_the_childs_reason_reaches_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            exc, text = self._build_and_fail(tmp)
            self.assertEqual(exc.returncode, 1, "the same non-zero verdict `check=True` gave")
            self.assertIn("mkiso.py failed (exit 1)", text)
            self.assertIn("FileNotFoundError", text,
                          "the missing input is the whole reason; it must not stay inside an "
                          "exception attribute that nothing prints")
            self.assertIn("loader.bin", text, "named by the child, not inferred by us")

    def test_naming_the_childs_reason_does_not_move_the_verdict(self):
        # The exception is what the gate dies on.  Same type, same status, same command: the
        # added output is a witness statement, not a second verdict, and a mutant that builds
        # cleanly still returns a path rather than a printout.
        with tempfile.TemporaryDirectory() as tmp:
            exc, _text = self._build_and_fail(tmp)
            self.assertIsInstance(exc, subprocess.CalledProcessError)
            self.assertEqual(exc.returncode, 1)
            self.assertTrue(any("mkiso.py" in part for part in exc.cmd), exc.cmd)

    def test_the_output_is_written_when_there_is_a_reason_to_write(self):
        # The inverse control: a child that succeeds prints nothing through this path, so the
        # harness cannot manufacture an error line on a green build -- and the gate profiles,
        # which call `build_mutant_iso` six times per run, would otherwise be annotated six
        # times over with nothing wrong.
        src = pathlib.Path(ROOT, "tests", "ovmf_boot.py").read_text()
        window = src[src.index("def build_mutant_iso"):src.index("# ---------------------------------------------------------------- main")]
        self.assertIn("if built.returncode != 0:", window,
                      "the child's output must be printed only when the child failed")
        self.assertIn("check_returncode", window,
                      "and the raise must be the same `CalledProcessError` `check=True` gave, "
                      "not a new exit path")
        self.assertNotIn("run(command, check=True", window,
                         "asserted against the call, not the word: this function's comment "
                         "explains why `check=True` was dropped, and a test that matched that "
                         "prose would fail for describing the fix")


if __name__ == "__main__":
    unittest.main()
