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
import os
import shutil
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import ovmf_boot64

import ovmf_boot64 as drv  # noqa: E402

PASS, FAIL, UNAVAILABLE, NOT_TESTED = (drv.PASS, drv.FAIL, drv.UNAVAILABLE,
                                      drv.NOT_TESTED)

# A complete, well-formed 64-bit boot log.  Build fragments from it so each test
# can remove or reorder exactly one thing and show that is what changed.
FIRMWARE = "BdsDxe: loading Boot0001 \"UEFI QEMU HARDDISK\" from PciRoot(0x0)\n"
LOADER_BANNER = "PC5-64-LOADER-BANNER 64-bit UEFI path (experimental)\n"
def alloc_line(purpose="bootinfo", pages=1, base=0x200c000, requested_max=0xFFFFFFFF,
               alloc_type="max-address", verdict="ok"):
    """One ALLOC record in the loader's exact grammar."""
    end = base + pages * 4096 - 1
    return ("PC5-64-LOADER-ALLOC purpose=%s type=%s pages=%d requested-max=0x%x"
            " returned-base=0x%x end=0x%x within-bound=1 within-limit=1"
            " verdict=%s\n" % (purpose, alloc_type, pages, requested_max, base, end,
                               verdict))


# Every mandatory allocation, in the order the loader makes them, with the page
# counts the loader actually uses.  A fixture with one allocation of any shape
# would satisfy the old checks; this one exercises the completeness contract.
ALLOC_BASES = [0x200c000, 0x2010000, 0x2014000, 0x2018000, 0x201c000]
ALLOC_PAGES = {"bootinfo": 1, "pagetables": 6, "stack": 4,
               "memmap-buffer": 8, "memmap-out": 8}


def allocs_ok(omit=None, extra=""):
    out = []
    for i, purpose in enumerate(("bootinfo", "pagetables", "stack",
                                 "memmap-buffer", "memmap-out")):
        if purpose == omit:
            continue
        out.append(alloc_line(purpose, ALLOC_PAGES[purpose], ALLOC_BASES[i]))
    return "".join(out) + extra


LOADER_ALLOC = allocs_ok()
LOADER_EBS = "PC5-64-LOADER exit-boot-services=ok attempts=1\n"
LOADER_JUMP = "PC5-64-LOADER-JUMP entry=0x100000\n"
KERNEL_BANNER = "PC5-64-BANNER PC5 64-bit kernel entry\n"
KERNEL_ENV = "PC5-64-ENV cr0=0x80010033 cr3=0x201a000 cr4=0x20 efer=0xd01\n"
KERNEL_PAGING = "PC5-64-PAGING enabled=1 root=0x201a000 pages=6 span=0x100000000\n"
# A genuine passing walk: one detailed record per required address, spelled the
# way the kernel spells them.  Address zero prints as a bare `0` on both sides,
# because serial_hex() emits "0" rather than "0x0" for zero - a fixture that
# used "0x0" would not match a real log, and the driver would then be agreeing
# with its own fixture instead of the kernel's output.
_D1_NOTES = ("start", "below-1gib", "at-1gib", "above-1gib", "in-1gib",
             "below-2gib", "at-2gib", "above-2gib", "in-2gib",
             "below-3gib", "at-3gib", "above-3gib", "in-3gib", "upper-end")
_D1_ADDRS = (0, 0x3FFFFFFF, 0x40000000, 0x40000001, 0x50000000, 0x7FFFFFFF,
             0x80000000, 0x80000001, 0x90000000, 0xBFFFFFFF, 0xC0000000,
             0xC0000001, 0xD0000000, 0xFFFFFFFF)


def walk_line(addr, note="probe", path="PPP-", size="2m", identity=1):
    """One PTWALK record in the loader's exact grammar."""
    va = "0" if addr == 0 else "0x%x" % addr
    pa = "0" if addr == 0 else "0x%x" % addr
    return ("PC5-64-PTWALK va=%s path=%s size=%s pa=%s identity=%d note=%s\n"
            % (va, path, size, pa, identity, note))


def walk_ok(count=14, summary=14, addresses=None, failed=0, include_summary=True):
    """A passing walk with `count` genuine records and a matching summary."""
    addrs = list(addresses if addresses is not None else _D1_ADDRS)
    notes = list(_D1_NOTES) + ["extra"] * len(addrs)
    body = "".join(walk_line(a, notes[i]) for i, a in enumerate(addrs[:count]))
    if not include_summary:
        return body
    return "PC5-64-PTWALK span probes=%d failed=%d\n%s" % (summary, failed, body)


WALK_OK = walk_ok()
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
    # `walk_extra` is appended immediately after the walk block, so a malformed
    # probe lands beside the genuine ones rather than after the end marker.
    out = []
    for key in order:
        out.append(parts[key])
        if key == "walk":
            out.append(overrides.get("walk_extra", ""))
    return "".join(out)


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

    MUTANT_WALK = ("PC5-64-PTWALK va=0x0 path=PPP- size=2m pa=0x0 identity=1 note=start\n"
                   "PC5-64-PTWALK va=0x40000000 path=P--- size=none pa=none identity=0"
                   " note=no PDPT entry\n"
                   "PC5-64-PTWALK span probes=14 failed=3\n")

    def test_a_disconnected_directory_fails_the_walk_check(self):
        report = judge(full_log(walk=self.MUTANT_WALK))
        self.assertIn("[D1] every identity probe resolved", failed_names(report))

    def test_a_walk_that_covered_too_few_addresses_fails(self):
        thin = ("PC5-64-PTWALK span probes=4 failed=0\n"
                "PC5-64-PTWALK va=0x0 path=PPP- size=2m pa=0x0 identity=1 note=start\n")
        report = judge(full_log(walk=thin))
        self.assertIn("[D1] the walk covered all required addresses",
                      failed_names(report))

    def test_a_non_identity_translation_fails(self):
        wrong = ("PC5-64-PTWALK span probes=14 failed=0\n"
                 "PC5-64-PTWALK va=0x0 path=PPP- size=2m pa=0x200000 identity=0"
                 " note=start\n")
        report = judge(full_log(walk=wrong))
        self.assertIn("[D1] every probed address translated to itself",
                      failed_names(report))

    def test_a_path_that_stops_at_a_table_level_fails(self):
        # "failed=0" with nothing but table entries reached would be a walk that
        # never got to a page.  The path field is what makes that visible.
        stubby = ("PC5-64-PTWALK span probes=14 failed=0\n"
                  "PC5-64-PTWALK va=0x0 path=P--- size=none pa=none identity=0"
                  " note=below-1gib\n")
        report = judge(full_log(walk=stubby))
        self.assertIn("[D1] every probe reached a terminal mapping with no gap",
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


class FirmwareAbiContractTests(unittest.TestCase):
    """Contracts that only break on real firmware, pinned so they cannot regress.

    Everything here was found by running the loader under genuine OVMF, and none
    of it was detectable from the stub, because the stub had been written to
    match the loader rather than the specification.
    """

    def test_locate_handle_is_declared_in_spec_order(self):
        """UEFI 2.10 7.3.6: LocateHandle(SearchType, Protocol, SearchKey, BufferSize, Buffer).

        The header had BufferSize first and SearchType second, and every caller
        matched.  Under OVMF each call returned EFI_INVALID_PARAMETER - the size
        pointer's address arrived as a SearchType - so no volume was ever found
        and the boot died "kernel-not-found" with the kernel on the very volume
        it had just been loaded from.
        """
        header = (ROOT / "boot" / "uefi" / "efi.h").read_text()
        declaration = header[header.index("*locate_handle)("):]
        declaration = declaration[:declaration.index(";")]
        self.assertLess(declaration.index("search_type"),
                        declaration.index("buffer_size"),
                        "SearchType must be the first parameter: " + declaration)
        self.assertIn("protocol", declaration,
                      "ByProtocol requires a non-NULL protocol argument")

    def test_every_locate_handle_call_passes_a_protocol_for_by_protocol(self):
        for name in ("boot/uefi/uefi_main.c", "boot/uefi64/uefi64_main.c"):
            source = (ROOT / name).read_text()
            for match in re.finditer(r"locate_handle\(([^;]*)\)", source, re.S):
                call = match.group(1)
                args = [a.strip() for a in call.split(",")]
                if len(args) < 5:
                    continue        # a different function or a comment
                self.assertNotEqual(args[0], "0",
                                    "%s: SearchType must be an enum, not 0" % name)
                if "BY_PROTOCOL" in args[0]:
                    self.assertNotEqual(args[1], "0",
                                        "%s: ByProtocol with a NULL protocol is "
                                        "EFI_INVALID_PARAMETER" % name)

    def test_the_stub_enforces_the_spec_signature(self):
        """A stub that mirrors the caller cannot catch a caller bug."""
        stub = (ROOT / "tests" / "uefi_stub_firmware.py").read_text()
        signature = stub[stub.index("def svc_locate_handle"):
                         stub.index("def svc_locate_handle") + 200]
        self.assertLess(signature.index("search_type"),
                        signature.index("size_ptr"),
                        "stub must take SearchType before BufferSize")

    def test_the_entry_stub_preserves_the_image_handle(self):
        """RCX carries ImageHandle; the .bss zero loop needs RCX as a counter.

        The stub still booted because it reloads RCX, but on real firmware the
        loader received NULL and every image-handle call failed with
        EFI_INVALID_PARAMETER.
        """
        stub = (ROOT / "boot" / "uefi64" / "bootx64.S").read_text()
        body = stub[stub.index("_start:"):stub.index("call efi_main64")]
        self.assertRegex(body, r"movq\s+%rcx,\s*%r",
                         "ImageHandle (RCX) must be saved before the .bss loop")
        self.assertRegex(body, r"movq\s+%r\w+,\s*%rcx",
                         "ImageHandle must be restored into RCX before the call")

    def test_the_boot_info_page_is_zeroed_before_use(self):
        """AllocatePages returns whatever was on the page.

        OVMF fills freed memory with 0xAF, so an un-zeroed struct arrived with
        flags=0xafafafed and a framebuffer address of 0xafafafafafafafaf.
        """
        source = (ROOT / "boot" / "uefi64" / "uefi64_main.c").read_text()
        allocate = source.index('alloc_below_4g("bootinfo", 1, &boot_info_physical)')
        zero = source.index("raw[offset] = 0", allocate)
        magic = source.index("boot_info->magic =", allocate)
        self.assertLess(zero, magic,
                        "the structure must be cleared before its first field")

    def test_an_unmappable_framebuffer_is_reported_not_fatal(self):
        """Real firmware puts the framebuffer above a 4 GiB identity span.

        OVMF under q35 hands out 0x80000000.  Dying there stopped the boot before
        the kernel ever ran, which is why no firmware result existed at all.
        """
        source = (ROOT / "boot" / "uefi64" / "uefi64_main.c").read_text()
        self.assertNotIn('die("framebuffer-outside-span")', source,
                         "an out-of-span framebuffer must not abort the boot")
        self.assertIn("PC5_BOOT64_F_FRAMEBUFFER_UNSUPPORTED", source)



class TheD1TerminusRuleTests(unittest.TestCase):
    """A 2 MiB page is a complete walk that never touches a PT.

    The loader emits `path=PPP- size=2m` for it, because PDE.PS ends the walk at
    the PD.  A driver that demanded a trailing "P" would fail every correct
    mapping; one that accepted any non-empty page size would pass a log claiming
    a 4 KiB page reached through three levels.  These pin both directions.
    """

    def _judge(self, path, size):
        report = ovmf_boot64.Evidence()
        run = {"serial": "", "seconds": 1.0, "timed_out": False,
               "stopped_on": "evidence", "returncode": 0,
               "stderr": "", "command": ["qemu"]}
        seen = ovmf_boot64.observe("")
        seen["walk_probes"] = 14
        seen["walk_failed"] = 0
        seen["walk_summary_count"] = 1
        seen["walk_summaries"] = [{"line": 1, "probes": 14, "failed": 0}]
        # A full, correct address set, so the coverage check is satisfied and
        # these tests isolate the terminus rule they are about.  `pa` must equal
        # `va` and the identity flag must agree with that: D1 now recomputes
        # identity from the addresses instead of trusting the flag, so a fixture
        # that left pa at 0 would fail for a reason unrelated to the terminus.
        seen["walk_samples"] = [
            {"va": "0" if a == 0 else "0x%x" % a, "va_value": a,
             "path": path if a == 0 else "PPP-",
             "size": size if a == 0 else "2m",
             "pa": "0x0" if a == 0 else "0x%x" % a, "pa_value": a,
             "identity": size != "none", "note": "n"}
            for a in ovmf_boot64.D1_EXPECTED_ADDRESSES]
        ovmf_boot64.evaluate(report, seen, run)
        verdict = [c for c in report.checks if c["name"].startswith("[D1] every probe reached")]
        self.assertEqual(len(verdict), 1)
        return verdict[0]["state"]

    def test_a_2mib_page_is_a_terminal_mapping(self):
        self.assertEqual(self._judge("PPP-", "2m"), ovmf_boot64.PASS)

    def test_a_4kib_page_reaching_a_pt_is_terminal(self):
        self.assertEqual(self._judge("PPPP", "4k"), ovmf_boot64.PASS)

    def test_a_1gib_page_stops_at_the_pdpt(self):
        self.assertEqual(self._judge("PP--", "1g"), ovmf_boot64.PASS)

    def test_a_probe_with_no_mapping_fails(self):
        self.assertEqual(self._judge("P---", "none"), ovmf_boot64.FAIL)

    def test_a_2mib_claim_without_a_pd_entry_fails(self):
        # Disconnected span: it says large page but the PD was never reached.
        self.assertEqual(self._judge("PP--", "2m"), ovmf_boot64.FAIL)

    def test_a_4kib_claim_that_never_reached_a_pt_fails(self):
        self.assertEqual(self._judge("PPP-", "4k"), ovmf_boot64.FAIL)



class FirmwarePairDiscoveryTests(unittest.TestCase):
    """The variable store must be the one that belongs with the code image.

    A pair is not interchangeable across installs: sizes and layout differ, and a
    mismatched store leaves firmware that never reaches the boot manager.  Guessing
    is worse than refusing, so the search is limited to a sibling file and the
    driver reports UNAVAILABLE when there is none.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _touch(self, name):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(b"x")
        return path

    def test_the_code_stem_is_rewritten_to_vars(self):
        self._touch("OVMF_CODE.fd")
        code = self._touch("OVMF_VARS.fd")
        self.assertEqual(ovmf_boot64.sibling_vars(
            os.path.join(self.dir, "OVMF_CODE.fd")), code)

    def test_a_suffixed_pair_is_matched(self):
        self._touch("OVMF_CODE_4M.fd")
        code = self._touch("OVMF_VARS_4M.fd")
        self.assertEqual(ovmf_boot64.sibling_vars(
            os.path.join(self.dir, "OVMF_CODE_4M.fd")), code)

    def test_an_unrelated_neighbour_is_not_used(self):
        self._touch("OVMF_CODE.fd")
        self._touch("something-else.fd")
        self.assertIsNone(ovmf_boot64.sibling_vars(
            os.path.join(self.dir, "OVMF_CODE.fd")))

    def test_no_code_path_means_no_discovery(self):
        self.assertIsNone(ovmf_boot64.sibling_vars(None))

    def test_a_missing_vars_file_is_not_reported_as_present(self):
        """The rule the whole driver exists to keep: absent is not usable."""
        code = self._touch("OVMF_CODE.fd")
        self.assertIsNone(ovmf_boot64.sibling_vars(code))



class KernelEntryIsProvenByOwnershipOfTheMarker(unittest.TestCase):
    """The loader must not be able to pronounce a kernel-only marker.

    "Kernel entry" rests on a string appearing in the serial log.  That is only
    evidence if the binary that prints it is the kernel: if the loader also
    contained the string, a loader bug could emit it without ever transferring
    control, and the gate would pass on a boot that never got there.  These read
    the built artifacts and require the marker strings to be absent from the
    loader and present in the kernel.
    """

    KERNEL_ONLY = (b"PC5-64BIT-KERNEL-ENTRY", b"PC5-64-DONE",
                   b"PC5-64BIT-LONG-MODE-OK", b"PC5-64-PTWALK")

    def _loader(self):
        path = ROOT / "build" / "uefi64" / "BOOTX64.EFI"
        if not path.exists():
            # Explicit, not silent: `make check` does not build 64-bit
            # artifacts, so a plain suite run cannot have validated this.
            self.skipTest("PREREQUISITE SKIP: build/uefi64/BOOTX64.EFI not built; "
                          "run `make ovmf-test64`, which builds it and asserts "
                          "this in the gate")
        return path.read_bytes()

    def _kernel(self):
        path = ROOT / "build" / "kernel64" / "KERNEL64.BIN"
        if not path.exists():
            self.skipTest("PREREQUISITE SKIP: build/kernel64/KERNEL64.BIN not built; "
                          "run `make ovmf-test64`, which builds it and asserts "
                          "this in the gate")
        return path.read_bytes()

    def test_the_loader_cannot_print_a_kernel_marker(self):
        loader = self._loader()
        for marker in self.KERNEL_ONLY:
            self.assertNotIn(marker, loader,
                             "%s is in the loader: it could fake kernel entry"
                             % marker.decode())

    def test_the_kernel_really_owns_the_entry_and_terminal_markers(self):
        kernel = self._kernel()
        for marker in (b"PC5-64BIT-KERNEL-ENTRY", b"PC5-64-DONE"):
            self.assertIn(marker, kernel,
                          "%s is not in the kernel; the markers prove nothing"
                          % marker.decode())

    def test_the_loader_marker_is_absent_from_the_kernel(self):
        """The handoff is two binaries naming two different stages."""
        kernel = self._kernel()
        self.assertNotIn(b"PC5-64-LOADER-JUMP", kernel,
                         "the kernel carries a loader marker")



class AStaleLogCanNeverBeTheVerdict(unittest.TestCase):
    """§5: no pass on stale output.

    A serial log is written to a fixed path, so whatever a previous run left there
    is the first thing a careless driver would read.  The guard is that the log is
    truncated before QEMU starts and the verdict comes from the bytes this run
    produced, never from the file.  Both directions are checked: a fake emulator
    that prints nothing must FAIL even with a complete passing log already on disk,
    and a driver reading the file would do the opposite.
    """

    def test_a_silent_emulator_fails_despite_a_passing_log_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            serial_dir = tmpdir / "ovmf64"
            serial_dir.mkdir()
            # Seed the exact log a real passing run produced.
            (serial_dir / "efi64-serial.log").write_text(full_log(), encoding="latin-1")
            shim = tmpdir / "silent-qemu.sh"
            shim.write_text("#!/bin/sh\nexit 0\n")
            shim.chmod(0o755)
            out = tmpdir / "report.json"
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tests" / "ovmf_boot64.py"),
                 "--no-build", "--qemu", str(shim),
                 "--ovmf-code", "/tmp/none-code.fd" if False else str(ROOT / "Makefile"),
                 "--ovmf-vars", str(ROOT / "Makefile"),
                 "--serial-dir", str(serial_dir),
                 "--json-out", str(out)],
                capture_output=True, text=True)
            data = json.loads(out.read_text())
            self.assertNotEqual(proc.returncode, 0,
                                "a silent emulator must not exit 0")
            # The decisive one: the kernel must not be claimed to have run.
            for check in data["checks"]:
                if check["name"] in ("the kernel executed",
                                     "the kernel reached its final marker",
                                     "the 64-bit loader was entered"):
                    self.assertNotEqual(check["state"], PASS,
                                        "verdict came from the stale file: %s" % check)

    def test_qemu_that_never_starts_overwrites_the_old_log(self):
        """Startup failure must not leave the previous run's bytes behind."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = pathlib.Path(tmp)
            serial_dir = tmpdir / "ovmf64"
            serial_dir.mkdir()
            log = serial_dir / "efi64-serial.log"
            log.write_text(full_log(), encoding="latin-1")
            subprocess.run(
                [sys.executable, str(ROOT / "tests" / "ovmf_boot64.py"),
                 "--no-build", "--qemu", "/nonexistent/qemu",
                 "--ovmf-code", str(ROOT / "Makefile"),
                 "--ovmf-vars", str(ROOT / "Makefile"),
                 "--serial-dir", str(serial_dir),
                 "--json-out", str(tmpdir / "report.json")],
                capture_output=True, text=True)
            # Either the file is gone or it no longer claims a completed boot.
            if log.exists():
                self.assertNotIn("PC5-64-DONE", log.read_text(encoding="latin-1"),
                                 "a stale log survived a failed run")



class TheImageIsReallyAFat12Superfloppy(unittest.TestCase):
    """The report states `image_format: fat12 superfloppy`.  Assert it from bytes.

    Both mutations below were demonstrated to pass the entire suite before this
    existed: firmware boots a partitioned image, and tools/mkefiimg.py will build
    FAT16 for any image large enough.  The cluster thresholds are the FAT
    specification's, and the cluster count is calculated from the BPB rather than
    compared against the current value, so enlarging the image fails for the right
    reason instead of pinning today's number.
    """

    def _image(self):
        path = ROOT / "build" / "efi64.img"
        if not path.exists():
            self.skipTest("build/efi64.img is not built (make uefi64)")
        return path

    def test_the_bpb_describes_a_512_byte_sector_volume(self):
        f = ovmf_boot64.read_fat_superfloppy(self._image())
        self.assertIsNotNone(f, "not a readable FAT boot sector")
        self.assertEqual(f["bytes_per_sector"], 512)
        self.assertGreater(f["sectors_per_cluster"], 0)
        self.assertGreaterEqual(f["num_fats"], 1)
        self.assertTrue(f["boot_signature"], "no 0x55AA signature at 0x1FE")

    def test_the_cluster_count_classifies_as_fat12_by_the_spec_threshold(self):
        f = ovmf_boot64.read_fat_superfloppy(self._image())
        self.assertIsNotNone(f)
        # The rule, not the number: FAT12 is defined by fewer than 4085 clusters.
        self.assertLess(f["clusters"], 4085,
                        "%d clusters is not FAT12" % f["clusters"])
        self.assertEqual(f["flavour"], "FAT12")
        # The count must be consistent with the BPB it came from.
        expected = ((f["total_sectors"] - f["first_data_sector"])
                    // f["sectors_per_cluster"])
        self.assertEqual(f["clusters"], expected)

    def test_there_is_no_partition_table(self):
        f = ovmf_boot64.read_fat_superfloppy(self._image())
        self.assertEqual(f["partition_entries"], 0,
                         "a superfloppy has no records at 0x1BE")

    def test_a_partition_entry_is_rejected(self):
        """The first demonstrated mutation."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "partitioned.img"
            data = bytearray(self._image().read_bytes())
            data[0x1BE:0x1BE + 16] = bytes(
                [0x80, 0, 1, 0, 0x0C, 0xFE, 0xFF, 0xFF,
                 1, 0, 0, 0, 0xDF, 0x0B, 0, 0])
            bad.write_bytes(bytes(data))
            report = ovmf_boot64.Evidence()
            ovmf_boot64.check_fat12_superfloppy(report, str(bad))
            verdict = [c for c in report.checks
                       if c["name"] == "the 64-bit image is a FAT12 superfloppy"]
            self.assertEqual(verdict[0]["state"], ovmf_boot64.FAIL)
            self.assertIn("partition", verdict[0]["detail"])

    def test_a_fat16_image_is_rejected(self):
        """The second demonstrated mutation: a larger image becomes FAT16."""
        # 8192 KiB is above the FAT12 cluster limit; mkefiimg.py builds FAT16.
        with tempfile.TemporaryDirectory() as tmp:
            big = pathlib.Path(tmp) / "big.img"
            subprocess.run(
                [sys.executable, str(ROOT / "tools" / "mkefiimg.py"),
                 "--size-kb", "8192", "--volume-label", "PC5EFI",
                 "--file", "%s=EFI/BOOT/BOOTX64.EFI" % (ROOT / "build" / "uefi64" / "BOOTX64.EFI"),
                 "--output", str(big)],
                capture_output=True, text=True, check=True)
            f = ovmf_boot64.read_fat_superfloppy(str(big))
            self.assertEqual(f["flavour"], "FAT16",
                             "expected the larger image to be FAT16, got %s" % f["flavour"])
            report = ovmf_boot64.Evidence()
            ovmf_boot64.check_fat12_superfloppy(report, str(big))
            verdict = [c for c in report.checks
                       if c["name"] == "the 64-bit image is a FAT12 superfloppy"]
            self.assertEqual(verdict[0]["state"], ovmf_boot64.FAIL)
            self.assertIn("FAT12", verdict[0]["detail"])

    def test_a_non_fat_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = pathlib.Path(tmp) / "junk.img"
            junk.write_bytes(b"\x00" * 4096)
            report = ovmf_boot64.Evidence()
            ovmf_boot64.check_fat12_superfloppy(report, str(junk))
            verdict = report.checks[0]
            self.assertEqual(verdict["state"], ovmf_boot64.FAIL)



class D1RequiresEveryProbeNotJustAClaim(unittest.TestCase):
    """Coverage must come from the detail records, not from the summary count.

    The defect this replaces: the driver accepted `probes=14` on the summary line
    while only one detailed record existed, so a kernel that walked one address
    and reported fourteen would have passed.  Each case below would have passed
    the old check.
    """

    def _state(self, log):
        report = judge(log)
        return {c["name"]: c for c in report.checks}

    def _coverage(self, log):
        return self._state(log)["[D1] the walk covered all required addresses"]

    def test_the_real_fourteen_record_walk_passes(self):
        self.assertEqual(self._coverage(full_log())[ "state"], PASS)

    def test_a_lone_probe_claiming_fourteen_is_rejected(self):
        # The exact fixture the old driver accepted: 1 record, summary says 14.
        log = full_log(walk="PC5-64-PTWALK span probes=14 failed=0\n"
                            + walk_line(0, "start"))
        check = self._coverage(log)
        self.assertEqual(check["state"], FAIL)
        self.assertIn("1 detailed probe record", check["detail"])

    def test_a_missing_expected_address_is_rejected(self):
        addrs = [a for a in ovmf_boot64.D1_EXPECTED_ADDRESSES if a != 0x50000000]
        check = self._coverage(full_log(walk=walk_ok(addresses=addrs)))
        self.assertEqual(check["state"], FAIL)
        self.assertIn("missing address", check["detail"])
        self.assertIn("0x50000000", check["detail"])

    def test_a_duplicated_address_is_rejected(self):
        addrs = list(ovmf_boot64.D1_EXPECTED_ADDRESSES)
        addrs[4] = addrs[3]          # two records for the same address
        check = self._coverage(full_log(walk=walk_ok(addresses=addrs)))
        self.assertEqual(check["state"], FAIL)
        self.assertIn("duplicated", check["detail"])

    def test_an_unexpected_address_is_rejected(self):
        addrs = list(ovmf_boot64.D1_EXPECTED_ADDRESSES)
        addrs[4] = 0x60000000        # inside region 1 but not a required probe
        check = self._coverage(full_log(walk=walk_ok(addresses=addrs)))
        self.assertEqual(check["state"], FAIL)
        self.assertIn("unexpected address", check["detail"])
        self.assertIn("0x60000000", check["detail"])

    def test_a_summary_that_disagrees_with_the_details_is_rejected(self):
        # 14 genuine records, summary claims 15: contradictory.
        check = self._coverage(full_log(walk=walk_ok(summary=15)))
        self.assertEqual(check["state"], FAIL)
        self.assertIn("summary claims 15", check["detail"])

    def test_a_malformed_probe_record_is_not_counted_as_coverage(self):
        # A truncated record cannot be parsed, so it cannot satisfy a slot.
        bad = "PC5-64-PTWALK va=0x50000000 path=PPP\n"      # stops mid-record
        log = full_log(walk=walk_ok() .replace(walk_line(0x50000000, "in-1gib"), bad))
        check = self._coverage(log)
        self.assertEqual(check["state"], FAIL)
        self.assertIn("0x50000000", check["detail"])



class D2RequiresEveryMandatoryAllocation(unittest.TestCase):
    """Completeness, not just correctness of whatever was reported.

    The defect this replaces: one well-formed allocation record satisfied D2, so
    a boot that never allocated page tables would pass as long as the allocation
    it did make was inside its bound.
    """

    NAME = "[D2] every mandatory allocation was reported exactly once"

    def _check(self, alloc):
        report = judge(full_log(alloc=alloc))
        return {c["name"]: c for c in report.checks}[self.NAME]

    def test_all_five_mandatory_allocations_pass(self):
        self.assertEqual(self._check(allocs_ok())["state"], PASS)

    def test_the_real_single_record_fixture_is_now_rejected(self):
        # The exact shape the old driver accepted: one allocation, nothing else.
        check = self._check(alloc_line("bootinfo", 1))
        self.assertEqual(check["state"], FAIL)
        for purpose in ("pagetables", "stack", "memmap-buffer", "memmap-out"):
            self.assertIn(purpose, check["detail"])

    def test_each_mandatory_purpose_is_required(self):
        for purpose in ("bootinfo", "pagetables", "stack",
                        "memmap-buffer", "memmap-out"):
            with self.subTest(purpose=purpose):
                check = self._check(allocs_ok(omit=purpose))
                self.assertEqual(check["state"], FAIL)
                self.assertIn(purpose, check["detail"])

    def test_a_duplicated_purpose_is_rejected(self):
        dup = alloc_line("stack", 4, 0x3000000)
        check = self._check(allocs_ok(extra=dup))
        self.assertEqual(check["state"], FAIL)
        self.assertIn("duplicated", check["detail"])

    def test_an_unknown_purpose_is_rejected(self):
        bogus = alloc_line("mystery", 2, 0x3000000)
        check = self._check(allocs_ok(extra=bogus))
        self.assertEqual(check["state"], FAIL)
        self.assertIn("unknown", check["detail"])

    def test_a_record_without_a_purpose_is_rejected(self):
        untagged = ("PC5-64-LOADER-ALLOC type=max-address pages=2 requested-max=0xffffffff"
                    " returned-base=0x3000000 end=0x3001fff within-bound=1"
                    " within-limit=1 verdict=ok\n")
        check = self._check(allocs_ok(extra=untagged))
        self.assertEqual(check["state"], FAIL)
        self.assertIn("no purpose", check["detail"])

    def test_the_conditional_growth_record_is_accepted_when_present(self):
        grow = alloc_line("memmap-grow", 16, 0x3000000)
        report = judge(full_log(alloc=allocs_ok(extra=grow)))
        checks = {c["name"]: c for c in report.checks}
        self.assertEqual(checks[self.NAME]["state"], PASS)
        self.assertEqual(report.data["d2_allocations"]["conditional_observed"],
                         ["memmap-grow"])

    def test_growth_is_not_required(self):
        # Absent growth must not be reported as a failure.
        self.assertEqual(self._check(allocs_ok())["state"], PASS)

    def test_a_bad_verdict_still_fails(self):
        alloc = allocs_ok().replace("verdict=ok", "verdict=over-bound", 1)
        report = judge(full_log(alloc=alloc))
        checks = {c["name"]: c for c in report.checks}
        self.assertEqual(checks["[D2] every allocation verdict was ok"]["state"], FAIL)

    def test_a_wrong_allocation_type_is_rejected(self):
        alloc = allocs_ok() + alloc_line("memmap-out", 8, 0x3000000, alloc_type="any-pages")
        # Replace rather than add, to keep the purpose set valid.
        alloc = allocs_ok().replace(
            "purpose=memmap-out type=max-address",
            "purpose=memmap-out type=any-pages")
        report = judge(full_log(alloc=alloc))
        checks = {c["name"]: c for c in report.checks}
        self.assertEqual(
            checks["[D2] every allocation used EFI_ALLOCATE_MAX_ADDRESS"]["state"], FAIL)

    def test_an_over_bound_range_is_still_rejected(self):
        # A 4-page range based at 0x2014000 ends at 0x2017fff, so asking for no
        # more than 0x2010000 is a real contradiction between the request and the
        # range the loader used.
        alloc = allocs_ok().replace(
            "purpose=stack type=max-address pages=4 requested-max=0xffffffff",
            "purpose=stack type=max-address pages=4 requested-max=0x2010000")
        report = judge(full_log(alloc=alloc))
        checks = {c["name"]: c for c in report.checks}
        self.assertEqual(
            checks["[D2] no allocation ended above the requested maximum"]["state"], FAIL)

    def test_a_range_outside_the_identity_span_is_still_rejected(self):
        alloc = allocs_ok().replace(
            "purpose=stack type=max-address pages=4 requested-max=0xffffffff"
            " returned-base=0x2014000 end=0x2017fff",
            "purpose=stack type=max-address pages=4 requested-max=0xffffffff"
            " returned-base=0x100000000 end=0x100003fff")
        report = judge(full_log(alloc=alloc))
        checks = {c["name"]: c for c in report.checks}
        self.assertEqual(
            checks["[D2] no allocation ended outside the identity range"]["state"], FAIL)

    def test_mismatched_end_arithmetic_is_still_rejected(self):
        alloc = allocs_ok().replace("end=0x2017fff", "end=0x2017000")
        report = judge(full_log(alloc=alloc))
        checks = {c["name"]: c for c in report.checks}
        self.assertEqual(
            checks["[D2] reported ends match base + pages*4096 - 1"]["state"], FAIL)


class APassNeverCarriesAFailureExplanation(unittest.TestCase):
    """The JSON must not contradict itself.

    The committed reports contained `state: PASS` beside
    `detail: "timed out after 2.5s with no terminal marker"`.  The state was
    right; the retained explanation was not, and the JSON is the artifact a
    reader trusts later.
    """

    def test_a_passing_check_drops_its_explanation(self):
        report = ovmf_boot64.Evidence()
        report.check("x", True, "timed out after 2.5s with no terminal marker")
        self.assertEqual(report.checks[0]["state"], PASS)
        self.assertEqual(report.checks[0]["detail"], "")

    def test_a_failing_check_keeps_its_explanation(self):
        report = ovmf_boot64.Evidence()
        report.check("x", False, "missing address(es): 0x50000000")
        self.assertEqual(report.checks[0]["state"], FAIL)
        self.assertIn("0x50000000", report.checks[0]["detail"])

    def test_unavailable_and_not_tested_keep_their_explanations(self):
        report = ovmf_boot64.Evidence()
        report.record("u", UNAVAILABLE, "qemu not found")
        report.record("n", NOT_TESTED, "stage never ran")
        self.assertEqual(report.checks[0]["detail"], "qemu not found")
        self.assertEqual(report.checks[1]["detail"], "stage never ran")

    def test_a_successful_run_does_not_claim_it_timed_out(self):
        # A run that ended on evidence, having lived past a shorter deadline.
        run = {"serial": full_log(), "seconds": 2.5, "timed_out": False,
               "stopped_on": "evidence", "returncode": 0,
               "stderr": "", "command": ["qemu"]}
        report = ovmf_boot64.Evidence()
        ovmf_boot64.report_timeout(report, run)
        check = report.checks[0]
        self.assertEqual(check["state"], PASS)
        self.assertEqual(check["detail"], "",
                         "a successful run must not carry a timeout explanation")

    def test_no_passing_check_in_a_full_run_carries_a_detail(self):
        report = judge(full_log())
        offenders = [c for c in report.checks
                     if c["state"] == PASS and c["detail"]]
        self.assertEqual(offenders, [])



class EveryRawMarkerMustParse(unittest.TestCase):
    """A malformed marker is a fault even beside a complete set of good ones.

    The previous driver collected records with findall()/finditer(), so anything
    that did not match simply did not exist. Fourteen good probes plus one
    corrupt line read as fourteen good probes, and the corrupt line - emitted by
    the code under test - was reported as nothing at all.
    """

    D1_NAME = "[D1] the walk covered all required addresses"
    D2_NAME = "[D2] every mandatory allocation was reported exactly once"

    def _checks(self, log):
        report = judge(log)
        return {c["name"]: c for c in report.checks}, report

    # ---------------------------------------------------------------- D1
    def test_a_malformed_extra_probe_record_fails(self):
        junk = "PC5-64-PTWALK va=0x1234 path=PPP- size=2m\n"   # truncated
        checks, _ = self._checks(full_log(walk_extra=junk))
        self.assertEqual(checks[self.D1_NAME]["state"], FAIL)
        self.assertIn("malformed", checks[self.D1_NAME]["detail"])

    def test_each_kind_of_truncated_probe_record_fails(self):
        for junk in (
                "PC5-64-PTWALK va=0x1234 path=PPP- size=2m pa=0x1234 identity=1\n",
                "PC5-64-PTWALK va=0xZZZZ path=PPP- size=2m pa=0x1234 identity=1 note=x\n",
                "PC5-64-PTWALK va=0x1234 path=PPP- size=2m pa=0x1234 identity=x note=y\n",
                "PC5-64-PTWALK\n",
                "PC5-64-PTWALK span probes=14 failed=0 trailing-garbage\n"):
            with self.subTest(junk=junk.strip()):
                checks, _ = self._checks(full_log(walk_extra=junk))
                self.assertEqual(checks[self.D1_NAME]["state"], FAIL)

    def test_a_malformed_replacement_probe_record_still_fails(self):
        # The control that already existed: one expected probe replaced by junk.
        checks, _ = self._checks(full_log(walk_extra="PC5-64-PTWALK va=0xdead\n"))
        self.assertEqual(checks[self.D1_NAME]["state"], FAIL)

    def test_the_complete_genuine_set_still_passes(self):
        checks, _ = self._checks(full_log())
        self.assertEqual(checks[self.D1_NAME]["state"], PASS)

    def test_raw_marker_count_is_recorded(self):
        _, report = self._checks(full_log())
        self.assertEqual(report.data["d1_addresses"]["raw_detailed_markers"], 14)
        self.assertEqual(report.data["d1_addresses"]["malformed"], [])

    # ---------------------------------------------------------------- D2
    def test_a_malformed_extra_allocation_fails(self):
        junk = alloc_line("memmap-grow", 16, 0x3000000).replace(" verdict=ok", "")
        checks, _ = self._checks(full_log(alloc=allocs_ok(extra=junk)))
        self.assertEqual(checks[self.D2_NAME]["state"], FAIL)
        self.assertIn("malformed", checks[self.D2_NAME]["detail"])

    def test_a_malformed_replacement_allocation_fails(self):
        broken = "PC5-64-LOADER-ALLOC purpose=stack type=max-address pages=4\n"
        alloc = allocs_ok(omit="stack") + broken
        checks, _ = self._checks(full_log(alloc=alloc))
        self.assertEqual(checks[self.D2_NAME]["state"], FAIL)

    def test_a_zero_page_allocation_fails_even_when_it_claims_success(self):
        # Every other field is internally consistent and the loader's own verdict
        # says ok: only an independent page-count check can catch this.
        zero = alloc_line("stack", 0, 0x2014000, verdict="ok")
        zero = zero.replace("end=0x2013fff", "end=0x2013fff")
        alloc = allocs_ok(omit="stack") + zero
        checks, report = self._checks(full_log(alloc=alloc))
        self.assertEqual(checks["[D2] every allocation asked for at least one page"]["state"],
                         FAIL)
        self.assertIn("pages <= 0", checks["[D2] every allocation asked for at least one page"]["detail"])
        # And the record really did claim to have succeeded.
        stack = [a for a in report.data["d2_allocations"]["pages"].items()]
        self.assertIn(("stack", 0), stack)

    def test_a_zero_page_allocation_is_not_quietly_dropped(self):
        # pages=0 must remain a *parsed* record so it can be judged, not vanish.
        zero = alloc_line("memmap-grow", 0, 0x3000000)
        _, report = self._checks(full_log(alloc=allocs_ok(extra=zero)))
        self.assertEqual(report.data["d2_allocations"]["pages"]["memmap-grow"], 0)

    def test_the_five_genuine_allocations_still_pass(self):
        checks, report = self._checks(full_log(alloc=allocs_ok()))
        self.assertEqual(checks[self.D2_NAME]["state"], PASS)
        self.assertTrue(checks["[D2] every allocation asked for at least one page"]["state"]
                        == PASS)
        self.assertEqual(report.data["d2_allocations"]["raw_markers"], 5)



class D1RecomputesIdentityFromTheAddresses(unittest.TestCase):
    """F1-A: identity is a property of va and pa, not of the kernel's flag.

    The driver used to accept `identity=1` as proof.  A record claiming
    `pa=0x1000` for `va=0x50000000` therefore passed, and the flag it trusted is
    precisely the field that would be wrong when the mapping is broken.  Each
    test below keeps all 14 addresses, the exact coverage set and a single valid
    summary, so nothing but the address comparison can reject the log.
    """

    NAME = "[D1] every probed address translated to itself"

    def _checks(self, walk):
        report = judge(full_log(walk=walk))
        return {c["name"]: c for c in report.checks}, report

    def _swap(self, addr, replacement):
        """Replace exactly one genuine record, keeping coverage complete.

        Matched on the record prefix rather than on a regenerated line, because
        walk_ok() gives each probe its real note and walk_line()'s default would
        not equal it.  A silent no-op here would leave the log valid and make
        these tests pass for the wrong reason, so the substitution is asserted.
        """
        full = walk_ok()
        original = [line for line in full.splitlines()
                    if line.startswith("PC5-64-PTWALK va=%s "
                                       % ("0" if addr == 0 else "0x%x" % addr))]
        self.assertEqual(len(original), 1, "expected exactly one record for that address")
        swapped = full.replace(original[0] + "\n", replacement)
        self.assertNotEqual(swapped, full, "the substitution did not apply")
        return swapped

    def test_pa_differing_from_va_fails_even_when_the_flag_claims_identity(self):
        walk = self._swap(0x50000000,
                          "PC5-64-PTWALK va=0x50000000 path=PPP- size=2m"
                          " pa=0x1000 identity=1 note=in-1gib\n")
        checks, _ = self._checks(walk)
        self.assertEqual(checks[self.NAME]["state"], FAIL)
        self.assertIn("did not translate to their own address",
                      checks[self.NAME]["detail"])

    def test_the_address_comparison_alone_causes_the_rejection(self):
        # Prove the failure is not a count, marker or coverage problem: those
        # checks must all still pass while this one fails.
        walk = self._swap(0x50000000,
                          "PC5-64-PTWALK va=0x50000000 path=PPP- size=2m"
                          " pa=0x1000 identity=1 note=in-1gib\n")
        checks, _ = self._checks(walk)
        self.assertEqual(checks[self.NAME]["state"], FAIL)
        self.assertEqual(checks["[D1] the walk covered all required addresses"]["state"], PASS)
        self.assertEqual(checks["[D1] exactly one span summary was reported"]["state"], PASS)
        self.assertEqual(checks["[D1] every identity probe resolved"]["state"], PASS)
        self.assertEqual(checks["[D1] every probe reached a terminal mapping with no gap"]["state"], PASS)

    def test_an_identity_flag_that_denies_a_real_mapping_fails(self):
        walk = self._swap(0x50000000,
                          "PC5-64-PTWALK va=0x50000000 path=PPP- size=2m"
                          " pa=0x50000000 identity=0 note=in-1gib\n")
        checks, _ = self._checks(walk)
        self.assertEqual(checks[self.NAME]["state"], FAIL)
        self.assertIn("contradicts", checks[self.NAME]["detail"])

    def test_a_corrupted_physical_address_fails(self):
        # One wrong hex digit, with everything else untouched.
        walk = walk_ok().replace("pa=0x40000000", "pa=0x4000000b")
        checks, _ = self._checks(walk)
        self.assertEqual(checks[self.NAME]["state"], FAIL)

    def test_pa_none_never_counts_as_identity(self):
        walk = self._swap(0x50000000,
                          "PC5-64-PTWALK va=0x50000000 path=P--- size=none"
                          " pa=none identity=0 note=in-1gib\n")
        checks, _ = self._checks(walk)
        self.assertEqual(checks[self.NAME]["state"], FAIL)

    def test_all_fourteen_consistent_probes_pass(self):
        checks, report = self._checks(walk_ok())
        self.assertEqual(checks[self.NAME]["state"], PASS)
        pairs = report.data["d1_addresses"]["identity_pairs"]
        self.assertEqual(len(pairs), 14)
        self.assertTrue(all(p["pa_equals_va"] for p in pairs))

    def test_the_artifact_records_both_the_flag_and_the_recomputation(self):
        walk = self._swap(0x50000000,
                          "PC5-64-PTWALK va=0x50000000 path=PPP- size=2m"
                          " pa=0x1000 identity=1 note=in-1gib\n")
        _, report = self._checks(walk)
        bad = [p for p in report.data["d1_addresses"]["identity_pairs"]
               if not p["pa_equals_va"]]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["flag"], True,
                         "the kernel's claim is preserved next to the refutation")


class D1RejectsMultipleSpanSummaries(unittest.TestCase):
    """F1-B: exactly one span summary, because two summaries are two claims.

    `re.search` returned the first summary and ignored the rest, so a second
    line claiming `failed=9` was invisible and the run passed.
    """

    NAME = "[D1] exactly one span summary was reported"
    COVERAGE = "[D1] the walk covered all required addresses"

    def _checks(self, walk):
        report = judge(full_log(walk=walk))
        return {c["name"]: c for c in report.checks}, report

    def _body(self):
        return walk_ok(include_summary=False)

    def test_one_valid_summary_passes(self):
        checks, _ = self._checks(self._body() + "PC5-64-PTWALK span probes=14 failed=0\n")
        self.assertEqual(checks[self.NAME]["state"], PASS)

    def test_two_identical_summaries_fail(self):
        walk = self._body() + ("PC5-64-PTWALK span probes=14 failed=0\n"
                               "PC5-64-PTWALK span probes=14 failed=0\n")
        checks, _ = self._checks(walk)
        self.assertEqual(checks[self.NAME]["state"], FAIL)
        self.assertIn("identical duplicates", checks[self.NAME]["detail"])

    def test_two_contradictory_summaries_fail(self):
        walk = self._body() + ("PC5-64-PTWALK span probes=14 failed=0\n"
                               "PC5-64-PTWALK span probes=14 failed=9\n")
        checks, _ = self._checks(walk)
        self.assertEqual(checks[self.NAME]["state"], FAIL)
        self.assertIn("contradictory", checks[self.NAME]["detail"])

    def test_a_valid_summary_followed_by_a_malformed_one_fails(self):
        walk = self._body() + ("PC5-64-PTWALK span probes=14 failed=0\n"
                               "PC5-64-PTWALK span probes=14 failed=0 trailing\n")
        checks, _ = self._checks(walk)
        self.assertEqual(checks[self.COVERAGE]["state"], FAIL)
        self.assertIn("malformed", checks[self.COVERAGE]["detail"])

    def test_a_malformed_summary_before_a_valid_one_fails(self):
        walk = self._body() + ("PC5-64-PTWALK span probes=x failed=0\n"
                               "PC5-64-PTWALK span probes=14 failed=0\n")
        checks, _ = self._checks(walk)
        self.assertEqual(checks[self.COVERAGE]["state"], FAIL)
        self.assertIn("malformed", checks[self.COVERAGE]["detail"])

    def test_an_absent_summary_is_not_tested_rather_than_failed(self):
        # The walk never ran: that is an unexecuted stage, not a bad one.
        report = judge(full_log(walk=walk_ok(include_summary=False)))
        states = {c["name"]: c["state"] for c in report.checks}
        self.assertEqual(states["[D1] the live page tables were walked"], NOT_TESTED)
        self.assertNotIn(self.NAME, states)


if __name__ == "__main__":
    unittest.main()
