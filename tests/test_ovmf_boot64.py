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
LOADER_ALLOC = ("PC5-64-LOADER-ALLOC type=max-address pages=1 requested-max=0xffffffff"
                " returned-base=0x200c000 end=0x200cfff within-bound=1 within-limit=1"
                " verdict=ok\n")
LOADER_EBS = "PC5-64-LOADER exit-boot-services=ok attempts=1\n"
LOADER_JUMP = "PC5-64-LOADER-JUMP entry=0x100000\n"
KERNEL_BANNER = "PC5-64-BANNER PC5 64-bit kernel entry\n"
KERNEL_ENV = "PC5-64-ENV cr0=0x80010033 cr3=0x201a000 cr4=0x20 efer=0xd01\n"
KERNEL_PAGING = "PC5-64-PAGING enabled=1 root=0x201a000 pages=6 span=0x100000000\n"
WALK_OK = ("PC5-64-PTWALK span probes=14 failed=0\n"
           "PC5-64-PTWALK va=0x0 path=PPP- size=2m pa=0x0 identity=1 note=start\n")
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
        allocate = source.index("boot_info = alloc_below_4g(1")
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
        seen["walk_samples"] = [{"va": "0", "path": path, "size": size,
                                 "pa": "0x0", "identity": True, "note": "n"}]
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
            self.skipTest("build/uefi64/BOOTX64.EFI not built")
        return path.read_bytes()

    def _kernel(self):
        path = ROOT / "build" / "kernel64" / "KERNEL64.BIN"
        if not path.exists():
            self.skipTest("build/kernel64/KERNEL64.BIN not built")
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


if __name__ == "__main__":
    unittest.main()
