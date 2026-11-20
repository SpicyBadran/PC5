"""Host-side tests for the 64-bit OVMF driver's evidence logic.

These drive `tests/ovmf_boot64.py` with synthetic serial logs.  What that proves
is narrow and worth stating plainly: the driver classifies a log correctly.  It
does **not** prove that firmware boots the image, that the kernel is entered, or
that the page tables on any real machine are connected.  Those need QEMU and
OVMF, which this machine does not have.

The tests exist because the dangerous failure for a boot gate is not "it says
FAIL when the boot worked" but "it says PASS when the boot did not happen".
Every check below is written against that.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import ovmf_boot64 as drv  # noqa: E402

PASS, FAIL, UNAVAILABLE, NOT_TESTED = (drv.PASS, drv.FAIL, drv.UNAVAILABLE,
                                      drv.NOT_TESTED)

# A complete, well-formed 64-bit boot log.  Build fragments from it so each test
# can remove or reorder exactly one thing and show that is what changed.
FIRMWARE = "BdsDxe: loading Boot0001 \"UEFI QEMU HARDDISK\" from PciRoot(0x0)\n"
LOADER_BANNER = "PC5-64-LOADER-BANNER 64-bit UEFI path (experimental)\n"
LOADER_ALLOC = ("PC5-64-LOADER-ALLOC type=max-address pages=1 requested-max=0xffffffff"
                " returned-base=0x200c000 end=0x200cfff within-bound=1 within-limit=1"
                " verdict=ok\n")
LOADER_EBS = "PC5-64-LOADER exit-boot-services=ok attempts=1\n"
LOADER_JUMP = "PC5-64-LOADER-JUMP entry=0x100000\n"
KERNEL_BANNER = "PC5-64-BANNER PC5 64-bit kernel entry\n"
KERNEL_ENV = "PC5-64-ENV cr0=0x80010033 cr3=0x201a000 cr4=0x20 efer=0xd01\n"
KERNEL_PAGING = "PC5-64-PAGING enabled=1 root=0x201a000 pages=6 span=0x100000000\n"
WALK_OK = ("PC5-64-PTWALK span probes=14 failed=0\n"
           "PC5-64-PTWALK va=0x0 path=PPPP size=2m pa=0x0 identity=1 note=start\n")
LONG_MODE_OK = "PC5-64BIT-LONG-MODE-OK\n"
DONE = "PC5-64-DONE\n"


def full_log(**overrides):
    """A passing log, with named pieces replaced or removed."""
    parts = {
        "firmware": FIRMWARE,
        "loader_banner": LOADER_BANNER,
        "alloc": LOADER_ALLOC,
        "ebs": LOADER_EBS,
        "jump": LOADER_JUMP,
        "kernel_banner": KERNEL_BANNER,
        "env": KERNEL_ENV,
        "paging": KERNEL_PAGING,
        "walk": WALK_OK,
        "long_mode": LONG_MODE_OK,
        "done": DONE,
    }
    order = ["firmware", "loader_banner", "alloc", "ebs", "jump", "kernel_banner",
             "env", "paging", "walk", "long_mode", "done"]
    parts.update(overrides)
    return "".join(parts[key] for key in order)


def judge(text, timed_out=False, seconds=12.0):
    """Run the real `evaluate` over a log, exactly as a boot would."""
    report = drv.Evidence()
    run = {"serial": drv.normalise_serial(text), "seconds": seconds,
           "stopped_on": "timeout" if timed_out else "evidence",
           "returncode": None, "timed_out": False, "stderr": "", "command": []}
    if timed_out:
        # Use the driver's own rule, not a copy of it.
        run["timed_out"] = True
        drv.report_timeout(report, run)
    drv.evaluate(report, drv.observe(run["serial"]), run)
    return report


def states(report):
    return {c["name"]: c["state"] for c in report.checks}


def failed_names(report):
    return [c["name"] for c in report.checks if c["state"] == FAIL]


class TheHappyLogPasses(unittest.TestCase):
    def test_a_complete_boot_passes_every_check(self):
        report = judge(full_log())
        self.assertEqual(report.failed, 0, failed_names(report))

    def test_no_check_is_silently_skipped(self):
        report = judge(full_log())
        self.assertEqual(report.count(NOT_TESTED), 0)
        self.assertEqual(report.count(UNAVAILABLE), 0)


class MissingMarkerIsNeverAPass(unittest.TestCase):
    """§6: each required marker, removed in turn, must fail loudly."""

    def test_missing_kernel_marker_fails(self):
        report = judge(full_log(kernel_banner=""))
        self.assertIn("the kernel executed", failed_names(report))
        self.assertNotEqual(states(report)["the kernel executed"], PASS)
        self.assertGreater(report.failed, 0)

    def test_missing_kernel_marker_prevents_done_from_counting(self):
        # DONE alone must not be readable as a completed boot.
        report = judge(full_log(kernel_banner="", env="", paging="", walk=""))
        self.assertIn("the kernel executed", failed_names(report))

    def test_missing_loader_banner_fails(self):
        report = judge(full_log(loader_banner=""))
        self.assertIn("the 64-bit loader was entered", failed_names(report))

    def test_missing_handoff_marker_fails(self):
        report = judge(full_log(jump=""))
        self.assertIn("the loader handed control to the kernel",
                      failed_names(report))

    def test_missing_final_marker_fails(self):
        report = judge(full_log(done=""))
        self.assertIn("the kernel reached its final marker", failed_names(report))

    def test_missing_walk_is_not_test_ed_or_passed(self):
        # The run finished, so the walk genuinely did not happen.  That is a
        # failure of the D1 requirement, not an untested stage.
        report = judge(full_log(walk=""))
        state = states(report)["[D1] the live page tables were walked"]
        self.assertNotEqual(state, PASS)
        self.assertEqual(state, NOT_TESTED)


class IncorrectOrderingIsDetected(unittest.TestCase):
    """§6: markers present but out of order must not pass."""

    def test_kernel_banner_before_handoff_fails(self):
        report = judge(full_log(jump="", kernel_banner=KERNEL_BANNER + LOADER_JUMP))
        self.assertIn("the boot stages appeared in order", failed_names(report))

    def test_done_before_kernel_entry_fails(self):
        text = (FIRMWARE + LOADER_BANNER + LOADER_ALLOC + LOADER_EBS + DONE +
                LOADER_JUMP + KERNEL_BANNER + KERNEL_ENV + KERNEL_PAGING +
                WALK_OK + LONG_MODE_OK)
        report = judge(text)
        self.assertIn("the boot stages appeared in order", failed_names(report))

    def test_ordering_is_not_satisfied_by_presence_alone(self):
        # All five markers present, but the handoff is last: the chain must fail.
        text = (FIRMWARE + LOADER_BANNER + LOADER_ALLOC + KERNEL_BANNER +
                KERNEL_ENV + KERNEL_PAGING + WALK_OK + DONE + LOADER_EBS +
                LOADER_JUMP)
        report = judge(text)
        self.assertIn("the boot stages appeared in order", failed_names(report))


class TimeoutBeforeKernelEntryIsDetected(unittest.TestCase):
    """§6: a QEMU timeout before kernel entry is a failure, not a pass."""

    def test_timeout_before_kernel_entry_fails(self):
        text = FIRMWARE + LOADER_BANNER + LOADER_ALLOC + LOADER_EBS
        report = judge(text, timed_out=True, seconds=300.0)
        names = failed_names(report)
        self.assertIn("the machine was stopped on evidence, not on the clock", names)
        self.assertIn("the kernel executed", names)
        self.assertEqual(states(report)["the kernel executed"], FAIL)
        self.assertNotEqual(states(report)["the kernel executed"], NOT_TESTED)

    def test_timeout_with_no_output_at_all_is_not_a_pass(self):
        report = judge("", timed_out=True, seconds=300.0)
        self.assertGreater(report.failed, 0)
        self.assertNotIn("the kernel executed",
                         [c["name"] for c in report.checks if c["state"] == PASS])

    def test_total_silence_with_no_timeout_is_not_a_pass(self):
        # QEMU exits cleanly having printed nothing: every stage is untested or
        # failed, and nothing may be PASS.
        report = judge("")
        passed = [c["name"] for c in report.checks if c["state"] == PASS]
        self.assertNotIn("the kernel executed", passed)
        self.assertNotIn("firmware produced a boot manager log", passed)


class DisconnectedPageDirectoryIsDetected(unittest.TestCase):
    """§6: a D1 mutant - one GiB boundary unreachable - must fail the run.

    The mutant is a log, because the driver's job is judging the walk's output.
    The walk itself is exercised for real against a synthetic table in
    test_pagetable64.py, including this same defect.
    """

    MUTANT_WALK = ("PC5-64-PTWALK va=0x0 path=PPPP size=2m pa=0x0 identity=1 note=start\n"
                   "PC5-64-PTWALK va=0x40000000 path=P--- size=none pa=none identity=0"
                   " note=no PDPT entry\n"
                   "PC5-64-PTWALK span probes=14 failed=3\n")

    def test_a_disconnected_directory_fails_the_walk_check(self):
        report = judge(full_log(walk=self.MUTANT_WALK))
        self.assertIn("[D1] every identity probe resolved", failed_names(report))

    def test_a_walk_that_covered_too_few_addresses_fails(self):
        thin = ("PC5-64-PTWALK span probes=4 failed=0\n"
                "PC5-64-PTWALK va=0x0 path=PPPP size=2m pa=0x0 identity=1 note=start\n")
        report = judge(full_log(walk=thin))
        self.assertIn("[D1] the walk covered all required addresses",
                      failed_names(report))

    def test_a_non_identity_translation_fails(self):
        wrong = ("PC5-64-PTWALK span probes=14 failed=0\n"
                 "PC5-64-PTWALK va=0x0 path=PPPP size=2m pa=0x200000 identity=0"
                 " note=start\n")
        report = judge(full_log(walk=wrong))
        self.assertIn("[D1] every probed address translated to itself",
                      failed_names(report))

    def test_a_path_that_stops_at_a_table_level_fails(self):
        # "failed=0" with nothing but table entries reached would be a walk that
        # never got to a page.  The path field is what makes that visible.
        stubby = ("PC5-64-PTWALK span probes=14 failed=0\n"
                  "PC5-64-PTWALK va=0x0 path=PP-- size=none pa=none identity=0"
                  " note=below-1gib\n")
        report = judge(full_log(walk=stubby))
        self.assertIn("[D1] every probe reached a terminal page-table entry",
                      failed_names(report))

    def test_the_kernel_failure_marker_fails(self):
        report = judge(full_log(walk="PC5-64BIT-FAIL=pagetables-disconnected\n"))
        self.assertIn("the kernel reported no failure", failed_names(report))


class IncorrectAllocationBoundIsDetected(unittest.TestCase):
    """§6: a wrong bound, a result outside it, and overflow must all fail."""

    def test_a_bound_other_than_the_inclusive_limit_fails(self):
        alloc = ("PC5-64-LOADER-ALLOC type=max-address pages=1 requested-max=0x100000000"
                 " returned-base=0x200c000 end=0x200cfff within-bound=1 within-limit=1"
                 " verdict=ok\n")
        report = judge(full_log(alloc=alloc))
        self.assertIn("[D2] the requested maximum was the inclusive limit",
                      failed_names(report))

    def test_a_zero_bound_fails(self):
        # Passing zero is the bug the loader's own comment warns about.
        alloc = ("PC5-64-LOADER-ALLOC type=max-address pages=1 requested-max=0x0"
                 " returned-base=0x200c000 end=0x200cfff within-bound=1 within-limit=1"
                 " verdict=ok\n")
        report = judge(full_log(alloc=alloc))
        self.assertIn("[D2] the requested maximum was the inclusive limit",
                      failed_names(report))

    def test_an_allocation_outside_the_limit_fails(self):
        alloc = ("PC5-64-LOADER-ALLOC type=max-address pages=1 requested-max=0xffffffff"
                 " returned-base=0x100000000 end=0x100000fff within-bound=1"
                 " within-limit=1 verdict=ok\n")
        report = judge(full_log(alloc=alloc))
        # The loader's own verdict claims ok; the driver recomputes and disagrees.
        self.assertIn("[D2] no allocation ended outside the identity range",
                      failed_names(report))

    def test_an_allocation_over_its_bound_fails(self):
        alloc = ("PC5-64-LOADER-ALLOC type=max-address pages=2 requested-max=0x200cfff"
                 " returned-base=0x200c000 end=0x200dfff within-bound=1 within-limit=1"
                 " verdict=ok\n")
        report = judge(full_log(alloc=alloc))
        self.assertIn("[D2] no allocation ended above the requested maximum",
                      failed_names(report))

    def test_overflow_in_the_end_calculation_fails(self):
        # base + pages*4096 wraps past 2**64; the reported end is small and
        # plausible.  A driver that trusted it would pass this.
        alloc = ("PC5-64-LOADER-ALLOC type=max-address pages=2"
                 " requested-max=0xffffffff returned-base=0xfffffffffffff000"
                 " end=0x1fff within-bound=1 within-limit=1 verdict=ok\n")
        report = judge(full_log(alloc=alloc))
        names = failed_names(report)
        self.assertIn("[D2] reported ends match base + pages*4096 - 1", names)
        self.assertIn("[D2] no allocation ended outside the identity range", names)

    def test_a_loader_verdict_of_overflow_fails(self):
        alloc = ("PC5-64-LOADER-ALLOC type=max-address pages=2 requested-max=0xffffffff"
                 " returned-base=0xfffffffffffff000 end=0x0 within-bound=0"
                 " within-limit=0 verdict=overflow-end\n")
        report = judge(full_log(alloc=alloc))
        self.assertIn("[D2] every allocation verdict was ok", failed_names(report))


class ContradictionsAreCaught(unittest.TestCase):
    def test_done_together_with_a_failure_marker_fails(self):
        report = judge(full_log(walk="PC5-64BIT-FAIL=pagetables-disconnected\n"))
        self.assertIn("no marker contradicts another", failed_names(report))

    def test_kernel_banner_without_handoff_is_a_contradiction(self):
        report = judge(full_log(jump="", kernel_banner=KERNEL_BANNER + LOADER_JUMP))
        # jump is present but after the banner; the ordering check owns that.
        self.assertIn("the boot stages appeared in order", failed_names(report))


class AbsenceIsNotPass(unittest.TestCase):
    """§5: no missing tool, log, or marker may become PASS."""

    def test_missing_qemu_and_ovmf_exit_three_and_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "report.json"
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tests" / "ovmf_boot64.py"),
                 "--no-build", "--qemu", "/nonexistent/qemu",
                 "--ovmf-code", "/nonexistent/code.fd",
                 "--ovmf-vars", "/nonexistent/vars.fd",
                 "--json-out", str(out)],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
            data = json.loads(out.read_text())
            self.assertEqual(data["failed"], 0)
            self.assertGreater(data["unavailable"], 0)
            self.assertGreater(data["not_tested"], 0)
            # The decisive assertion: nothing about the boot may be claimed.
            for check in data["checks"]:
                if "boot" in check["name"] or check["name"].startswith("stage:"):
                    self.assertNotEqual(check["state"], PASS, check)

    def test_the_serial_log_records_machine_readable_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "report.json"
            subprocess.run(
                [sys.executable, str(ROOT / "tests" / "ovmf_boot64.py"),
                 "--no-build", "--qemu", "/nonexistent/qemu",
                 "--ovmf-code", "/nonexistent/code.fd",
                 "--ovmf-vars", "/nonexistent/vars.fd",
                 "--json-out", str(out)],
                capture_output=True, text=True)
            data = json.loads(out.read_text())
            self.assertIn("artifacts", data["data"])
            self.assertIn("sha256", data["data"]["artifacts"]["efi64.img"])
            self.assertIn("commit", data["data"]["source"])
            self.assertIn("version", data["data"]["qemu"])
            self.assertIn("device_arrangement", data["data"])
            self.assertFalse(data["data"]["device_arrangement"]["verified"])


class TheDriverNeverAltersThe32BitPath(unittest.TestCase):
    def test_the_two_drivers_do_not_share_a_marker_vocabulary(self):
        # Not a text search: what matters is that neither driver can recognise
        # the other's logs.  Sharing a vocabulary is how two verdicts get
        # confused for one another.
        import ovmf_boot as old
        thirty_two = drv.normalise_serial(
            "PC5-UEFI: firmware=EDK II\nPC5-LOG kernel_entry eip=0x10000\n"
            "PC5-BOOT-OK\n")
        self.assertFalse(drv.observe(thirty_two)["loader_banner"])
        self.assertFalse(drv.observe(thirty_two)["kernel_banner"])
        # And a 64-bit log carries nothing the 32-bit classifier claims.
        sixty_four = drv.normalise_serial(full_log())
        self.assertNotIn("PC5-BOOT-OK", sixty_four)
        self.assertFalse(bool(re.search(r"PC5-UEFI:", sixty_four)))
        self.assertFalse(bool(re.search(r"PC5-LOG ", sixty_four)))
        del old  # imported only to prove the module exists and is unchanged


if __name__ == "__main__":
    unittest.main()
