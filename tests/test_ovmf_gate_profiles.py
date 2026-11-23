"""The 64-bit gate's own vocabulary, and the binding between matrix rows and checks.

`tests/test_ovmf_harness.py` covers the serial-log *parser* (what each `PC5-UEFI:`
line means) and the ISO-level guards.  This file covers what decides whether a 64-bit
boot counts: which words the 64-bit path prints, whether the gate reads them, and
whether every row of the required matrix is backed by a check the run can emit.

Those three questions are the difference between "the 64-bit ISO was booted" and
"the 64-bit ISO was booted *and judged*".  The defect this file pins is the judging:
before the gate knew the `PC5-64-*` markers it read a successful 64-bit log as
"no `PC5-BOOT-OK`", i.e. a failed boot, and a firmware-only log looked the same --
so the harness could not tell a broken release from a working one, and the case that
must never pass (firmware initialised, PC5 never ran) was not distinguishable from it.

The two fixtures are real EDK II serial transcripts, captured by `make ovmf-test`
against QEMU 11.0.2 and the shipped OVMF images, with the lines this harness does not
read removed.  They are kept as literals, not generated, because the point of the test
is what the machine actually prints: `build/ovmf-serial/serial-cold.log` is the source
of both, and `docs/evidence/` carries a full copy of the 64-bit one.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ovmf_boot as ovmf  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

X64_GOOD_LOG = """\
BdsDxe: loading Boot0001 "UEFI QEMU DVD-ROM QM00005 " from PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x2,0xFFFF,0x0)
BdsDxe: starting Boot0001 "UEFI QEMU DVD-ROM QM00005 " from PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x2,0xFFFF,0x0)
PC5-64-LOADER-BANNER 64-bit UEFI path (experimental)
PC5-64-LOADER firmware=EDK II revision=0x2 table-rev=0x10000
PC5-64-LOADER watchdog=disabled
PC5-64-LOADER kernel-volume=image
PC5-64-LOADER kernel at=0x100000 bytes=208896
PC5-64-LOADER kernel-header version=1 header=128 image=208896 entry=0x1000 bss=172032 arch=x86-64 machine=62 checksum=ok
PC5-64-LOADER rsdp=0xf77e000
PC5-64-LOADER pagetables=0xe1eb000 pages=6 mapped_2mib=2048 span=0x100000000
PC5-64-LOADER stack=0xe1f6000-0xe1fa000
PC5-64-LOADER image=0xe1f1000-0xe1f5190 below-span=1
PC5-64-LOADER exit-boot-services=ok attempts=1 descriptors=98
PC5-64-LOADER cr3-plan=0xe1eb000 cr0=0x80010033 cr4=0x668 long-mode=1
PC5-64-LOADER-JUMP entry=0x101000 bootinfo=0xe5b6000 stack=0xe1fa000
PC5-64BIT-KERNEL-ENTRY
PC5-64-IDT installed=256 present=32
PC5-64-MAP entries=98 conventional_pages=53751 bytes=0xc40
PC5-64-IMAGE base=0x100000 size=0x33000 range=ok
PC5-64-PAGING enabled=1 root=0xe1eb000 pages=6 span=0x100000000 kernel=0x100000-0x133000 loaded=0x100000
PC5-64-BOOTINFO magic=ok version=2 size=160 memdesc=32 flags=0xd exit_boot_services=1
PC5-64-CPU vendor=AuthenticAMD
PC5-64-CPU long_mode=1
PC5-64-MEM status=ok reason=ok frames=65094 conventional=53751 conventional_total=53751 claimed=1 free=53750 largest=41964 bitmap=0x20000 span=0x100000000
PC5-64-MEM-SELFTEST one=0x1000 three=0x2000 grant-in-map=1 bitmap-lie-refused=1 result=ok
PC5-64-TSS status=ok arm-rules=1 tss=0x1000 gdt=0x129280 limit=47 gdtr=0x129280 cpl=0 ist1=0x3000 ist2=0x4000 ist-df=0 ist-nmi=0 frames=3 free-after=53747 tr=not-loaded reason=ltr-unverifiable-here
PC5-64BIT-LONG-MODE-OK
PC5-64-DONE
"""

IA32_GOOD_LOG = """\
BdsDxe: loading Boot0001 "UEFI QEMU DVD-ROM QM00005 " from PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x2,0xFFFF,0x0)
BdsDxe: starting Boot0001 "UEFI QEMU DVD-ROM QM00005 " from PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x2,0xFFFF,0x0)
PC5 UEFI LOADER uefi-0.2.0
PC5-UEFI: firmware=EDK II
PC5-UEFI: kernel_volume=image
PC5-UEFI: kernel_bytes_read=67028
PC5-UEFI: boot_services_exited
PC5-UEFI: ExitBootServices_retries=0
PC5-UEFI: memory_map_entries=20
PC5-UEFI: handover_window_checked=1
PC5-UEFI: kernel_handoff_verified=1
PC5-UEFI: handoff_bytes=67028
PC5-UEFI: memory_conventional_mib=210
PC5-UEFI: watchdog_disabled
PC5-LOG kernel_entry eip=0x10000
PC5-LOG protocol=1 magic=ok
PC5-LOG memory_mib=254 entries=20 top_mib=4096
PC5-BOOT-OK
"""

# What EDK II prints when the medium is unreadable and nothing of ours ever ran: a
# genuine firmware banner, a genuine refusal, and no PC5 line at all.  This is the
# input that must never produce a pass, and it is the one the old harness could not
# tell apart from a working 64-bit boot.
FIRMWARE_ONLY_LOG = """\
BdsDxe: loading Boot0001 "UEFI QEMU DVD-ROM QM00005 " from PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x2,0xFFFF,0x0)
BdsDxe: failed to load Boot0001 "UEFI QEMU DVD-ROM QM00005 ": Not Found
BdsDxe: no bootable option or specified boot option found.
"""

#: The rows a firmware-only boot is allowed to satisfy: every one of them is a fact
#: about the firmware, which really did run and really did refuse.  Everything else
#: needs a `PC5-64-*` line.
FIRMWARE_ONLY_ALLOWANCE = (
    "genuine firmware produced a boot manager log",
    "the machine booted UEFI, not SeaBIOS",
    "firmware started the El Torito UEFI image",
    "the machine was stopped on evidence, not on the clock",
)


def run_of(text, stopped_on="PC5-64-DONE"):
    return {"serial": text, "raw_serial": text, "seconds": 3.5, "killed": False,
            "stopped_on": stopped_on, "returncode": -9, "command": ["qemu"],
            "stderr": ""}


class ClassifyX64(unittest.TestCase):
    def test_the_64bit_markers_are_read_as_stages(self):
        seen = ovmf.classify(run_of(X64_GOOD_LOG), ovmf.X64)
        self.assertTrue(seen["loader_banner"])
        self.assertTrue(seen["loader_firmware_edk2"])
        self.assertTrue(seen["kernel_volume_image"])
        self.assertEqual(seen["kernel_bytes_read"], 208896)
        self.assertEqual(seen["memory_map_entries"], 98)
        self.assertEqual(seen["exit_retries"], 1)
        self.assertEqual(seen["watchdog"], "disabled")
        self.assertTrue(seen["boot_ok"])
        self.assertEqual(seen["failure_codes"], [])

    def test_the_handover_is_verified_by_the_kernel_not_by_the_loader(self):
        """`range=ok` is the kernel's verdict on the copy it was given.

        It comes from `PC5-64-IMAGE`, and `handoff_bytes` is that line's hex `size=`
        field -- read as hex, because reading `0x33000` as a decimal is a comparison
        that silently never matches anything.
        """
        seen = ovmf.classify(run_of(X64_GOOD_LOG), ovmf.X64)
        self.assertTrue(seen["handoff_verified"])
        self.assertEqual(seen["handoff_bytes"], 0x33000)
        self.assertEqual(seen["handoff_bytes"], 208896)

    def test_the_kernel_agrees_with_the_loader_on_map_and_page_tables(self):
        seen = ovmf.classify(run_of(X64_GOOD_LOG), ovmf.X64)
        self.assertEqual(seen["kernel_memory_entries"], seen["memory_map_entries"])
        self.assertEqual(seen["paging_root"], seen["loader_pt_root"])
        self.assertEqual(seen["paging_pages"], seen["loader_pt_pages"])
        self.assertEqual(seen["idt_installed"], 256)
        self.assertEqual(seen["mem_conventional"], 53751)
        self.assertEqual(seen["cpu_long_mode"], 1)

    def test_tr_state_is_recorded_as_measured(self):
        """The log says `tr=not-loaded`; the gate reads that and does not round up."""
        self.assertEqual(ovmf.classify(run_of(X64_GOOD_LOG), ovmf.X64)["tr_state"],
                         "not-loaded")

    def test_watchdog_refusal_is_a_reported_attempt_not_a_missing_stage(self):
        text = X64_GOOD_LOG.replace("PC5-64-LOADER watchdog=disabled",
                                    "PC5-64-LOADER watchdog=disable-refused=0x2")
        self.assertEqual(ovmf.classify(run_of(text), ovmf.X64)["watchdog"], "refused")

    def test_a_missing_watchdog_line_is_not_a_pass(self):
        text = "\n".join(line for line in X64_GOOD_LOG.splitlines()
                         if "watchdog" not in line)
        self.assertIsNone(ovmf.classify(run_of(text), ovmf.X64)["watchdog"])


class FirmwareOnlyNeverPasses(unittest.TestCase):
    """The requirement, written as code."""

    def setUp(self):
        self.seen = ovmf.classify(run_of(FIRMWARE_ONLY_LOG, stopped_on=None), ovmf.X64)

    def test_firmware_is_recognised_and_nothing_of_ours_is(self):
        self.assertTrue(self.seen["firmware_banner"])
        self.assertTrue(self.seen["firmware_refused"])
        self.assertFalse(self.seen["loader_banner"])
        self.assertFalse(self.seen["boot_ok"])

    def test_no_64bit_stage_has_evidence(self):
        for key in ("loader_firmware_edk2", "kernel_volume_image", "kernel_read",
                    "boot_services_exited", "handoff_verified", "kernel_entry",
                    "kernel_protocol", "idt_installed", "paging_root", "paging_pages",
                    "paging_enabled", "mem_conventional", "mem_free", "mem_selftest_ok",
                    "cpu_long_mode", "tr_state", "handoff_bytes", "exit_retries",
                    "memory_map_entries", "kernel_memory_entries", "kernel_bytes_read"):
            self.assertIn(self.seen[key], (None, 0, False),
                          "%s claims %r from a log with no PC5 line in it"
                          % (key, self.seen[key]))

    def test_every_64bit_row_fails_on_a_firmware_only_log(self):
        report = ovmf.Report()
        ovmf.check_boot(report, run_of(FIRMWARE_ONLY_LOG, stopped_on=None), "cold",
                        208896, ovmf.X64, 256)
        passed = {check["name"] for check in report.checks if check["state"] == "pass"}
        for stage in ovmf.x64_stages():
            if stage.check in FIRMWARE_ONLY_ALLOWANCE:
                continue
            self.assertNotIn("[cold] " + stage.check, passed,
                             "row %r passed on a log with no PC5 output"
                             % stage.requirement)
        # And the run cannot be called a pass by the matrix either.
        rows = ovmf.build_matrix(report, ovmf.X64)
        cold = [row for row in rows if row["source"].startswith("OVMF cold")]
        self.assertTrue(all(row["state"] != "verified" for row in cold
                             if row["check"].split("] ", 1)[1]
                             not in FIRMWARE_ONLY_ALLOWANCE))
        ovmf.matrix_failures(report, rows)
        summary = [check for check in report.checks if "matrix row" in check["name"]]
        self.assertTrue(summary and summary[0]["state"] == "fail",
                        "a firmware-only boot must not satisfy the loader rows, and "
                        "the run's own status has to say so")
        self.assertIn("no loader failure was reported", summary[0]["detail"])


class ProfilesDoNotBorrow(unittest.TestCase):
    def test_the_32bit_log_does_not_satisfy_the_64bit_profile(self):
        seen = ovmf.classify(run_of(IA32_GOOD_LOG), ovmf.X64)
        self.assertFalse(seen["boot_ok"])
        self.assertFalse(seen["handoff_verified"])
        self.assertIsNone(seen["idt_installed"])

    def test_the_64bit_log_does_not_satisfy_the_32bit_profile(self):
        """The inversion, which is the bug this gate used to have in reverse.

        The 32-bit profile looked only for `PC5-BOOT-OK`, so a perfectly good 64-bit
        log read as a failed boot.  The harness now takes its vocabulary from the
        medium, so this cannot be arranged by passing a flag.
        """
        seen = ovmf.classify(run_of(X64_GOOD_LOG), ovmf.IA32)
        self.assertFalse(seen["boot_ok"])
        self.assertEqual(seen["failure_codes"], [])

    def test_an_explicit_failure_vetoes_the_success_markers(self):
        """Order-independent: a log that says both is read as a failure.

        A loader that printed a failure code *and* reached its success marker is
        contradicting itself, and the safe reading of a contradiction is that the boot
        did not succeed.  Both paths are covered because both had a success marker that
        could be trusted on its own.
        """
        x64 = X64_GOOD_LOG.replace("PC5-64-DONE",
                                   "PC5-64-LOADER-FAIL=handover-outside-map\nPC5-64-DONE")
        seen = ovmf.classify(run_of(x64), ovmf.X64)
        self.assertEqual(seen["failure_codes"], ["handover-outside-map"])
        self.assertFalse(seen["boot_ok"])
        ia32 = ovmf.classify(
            run_of(IA32_GOOD_LOG + "PC5-UEFI-FAILURE E15\n"), ovmf.IA32)
        self.assertEqual(ia32["failure_codes"], ["E15"])
        self.assertFalse(ia32["boot_ok"])

    def test_stop_markers_are_the_ones_that_end_a_run(self):
        """The machine is stopped on the profile's own evidence.

        `PC5-BOOT-OK` is never printed on the 64-bit path and `PC5-64-LOADER-FAIL`
        never on the 32-bit one, so a shared stop list meant one path always ended on
        the clock -- and a timeout cannot tell a wedge from a boot that finished.
        """
        self.assertEqual(ovmf.X64.stop, ("PC5-64-DONE", "PC5-64-LOADER-FAIL"))
        self.assertIn("PC5-BOOT-OK", ovmf.IA32.stop)
        self.assertNotIn("PC5-64-DONE", ovmf.IA32.stop)
        self.assertEqual(ovmf.X64.success,
                         ("PC5-64BIT-LONG-MODE-OK", "PC5-64-DONE"))

    def test_each_profile_owns_its_kernel_file_name(self):
        """The control that removes "the" kernel must remove it from *this* path."""
        self.assertEqual(ovmf.IA32.kernel_name, "KERNEL.BIN")
        self.assertEqual(ovmf.X64.kernel_name, "KERNEL64.BIN")
        self.assertEqual(ovmf.X64.kernel_fat, "\\KERNEL64.BIN")
        self.assertEqual(ovmf.X64.no_kernel_code, "kernel-not-found")
        self.assertEqual(ovmf.IA32.no_kernel_code, "E2")
        self.assertIn("KERNEL64.BIN", ovmf.X64.no_kernel_check())
        self.assertNotIn("KERNEL.BIN", ovmf.X64.no_kernel_check().replace(
            "KERNEL64.BIN", ""))


class DetectProfile(unittest.TestCase):
    """Which artifact this is comes from the medium, not from a flag."""

    def setUp(self):
        self.iso = os.path.join(ROOT, "build", "pc5.iso")

    def test_the_release_iso_is_read_the_way_the_loader_reads_it(self):
        if not os.path.isfile(self.iso):
            self.skipTest("no build/pc5.iso; run `make iso` first")
        names = ovmf.esp_directory(self.iso)
        self.assertIn("EFI/BOOT/BOOTX64.EFI", names)
        profile, note = ovmf.detect_profile(self.iso)
        self.assertIn(profile.kernel_name, names)
        self.assertIn("ESP root directory", note)
        # One of the two, never both: the release ESP carries the kernel its loader
        # looks for and not its sibling, which is what makes the medium decisive.
        self.assertTrue(("KERNEL.BIN" in names) ^ ("KERNEL64.BIN" in names),
                        "ESP holds %s" % sorted(names))

    def test_the_harness_survives_a_medium_it_cannot_read(self):
        """A gate that crashes reports nothing; a gate that fails reports why.

        Run with the emulator variables removed, for two reasons.  The gate is
        supposed to reach a verdict about the medium *before* it needs QEMU, so a
        run with no emulator is the shortest path to that code; and leaving the
        variables in place would let it boot the garbage ISO for real, which is a
        five-minute wait for a verdict this test already has -- a failure it observed
        while the machine it was pointed at sat in firmware.
        """
        import json
        import subprocess
        import tempfile
        env = {key: value for key, value in os.environ.items()
               if key not in ("PC5_QEMU", "PC5_OVMF_CODE", "PC5_OVMF_VARS")}
        with tempfile.TemporaryDirectory() as tmp:
            bogus = os.path.join(tmp, "pc5.iso")
            with open(bogus, "wb") as handle:
                handle.write(bytes(4096))
            out = os.path.join(tmp, "report.json")
            result = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tests", "ovmf_boot.py"),
                 "--iso", bogus, "--json-out", out, "--timeout", "5"],
                cwd=ROOT, capture_output=True, text=True, timeout=180, env=env)
            self.assertNotEqual(result.returncode, 1, result.stderr[-800:])
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("the boot profile can be read from the medium", result.stdout)
            with open(out) as handle:
                report = json.load(handle)
        failed = [check["name"] for check in report["checks"]
                  if check["state"] == "fail"]
        self.assertTrue(any("boot profile" in name for name in failed), failed)
        # And the unreadable medium has to be a *stated* reason, not a bare verdict:
        # "no El Torito boot catalog" is what a reader needs to tell "this ISO is
        # junk" from "the reader is broken".
        reason = next(check["detail"] for check in report["checks"]
                      if "boot profile" in check["name"] and check["state"] == "fail")
        self.assertTrue(reason.strip(), "the failed profile-read check carries no reason")

    def test_the_iso9660_root_alone_cannot_decide_it(self):
        """Why the detector reads the ESP and not the ISO9660 directory.

        Both flavors carry a flat `KERNEL.BIN;1` in the ISO9660 root -- it is the copy
        the BIOS path uses -- so a detector that looked there would call the 64-bit
        release a 32-bit boot.
        """
        if not os.path.isfile(self.iso):
            self.skipTest("no build/pc5.iso; run `make iso` first")
        with open(self.iso, "rb") as handle:
            blob = handle.read()
        # The ISO9660 identifier field holds `KERNEL.BIN;1` -- the name the BIOS path
        # reads -- and it is in *both* flavors of the release image.
        self.assertIn(b"KERNEL.BIN;1", blob)
        profile, _note = ovmf.detect_profile(self.iso)
        self.assertIn("KERNEL64.BIN" if profile is ovmf.X64 else "KERNEL.BIN",
                      ovmf.esp_directory(self.iso))

    def test_a_medium_we_cannot_walk_is_reported_and_never_guessed(self):
        """Detection may fail, but it may not lie, and it may not crash the gate.

        Three properties, all of them load-bearing:
        * `esp_directory` raises -- it has read nothing;
        * `detect_profile` turns that into `(None, why)`, so the caller has to say
          what it could not read rather than pick a vocabulary;
        * the harness records it as a failed check and still exits with its own
          verdict, instead of dying with a traceback and exit 1, which would look to
          a log reader like an infrastructure accident.
        """
        bogus = os.path.join(ROOT, "Makefile")
        self.assertRaises(ValueError, ovmf.esp_directory, bogus)
        profile, note = ovmf.detect_profile(bogus)
        self.assertIsNone(profile)
        self.assertIn("cannot read the ESP", note)

    def test_the_profile_vocabulary_follows_the_key(self):
        self.assertIs(ovmf.profile_by_key("x64"), ovmf.X64)
        self.assertIs(ovmf.profile_by_key("ia32"), ovmf.IA32)
        self.assertRaises(SystemExit, ovmf.profile_by_key, "arm64")


class MatrixRowsAreBound(unittest.TestCase):
    """Every required row names a check that the run can actually emit."""

    def _run(self, profile, log, size):
        report = ovmf.Report()
        ovmf.check_boot(report, run_of(log, "PC5-BOOT-OK" if profile is ovmf.IA32
                                       else "PC5-64-DONE"), "cold", size, profile, 256)
        return report, {check["name"] for check in report.checks}

    def test_every_cold_row_names_an_emitted_check(self):
        for profile, log, size in ((ovmf.X64, X64_GOOD_LOG, 208896),
                                   (ovmf.IA32, IA32_GOOD_LOG, 67028)):
            _report, emitted = self._run(profile, log, size)
            for requirement, source, check_name in ovmf.required_rows(profile):
                if not source.startswith("OVMF cold boot"):
                    continue
                self.assertIn(check_name, emitted,
                              "%s: row %r names %r, which no check emits"
                              % (profile.key, requirement, check_name))

    def test_a_good_run_verifies_every_cold_row(self):
        """...and a row is `verified` only because its own check passed.

        The old `build_matrix` judged all the cold rows with one blanket predicate
        (`boot_ok and loader_banner and kernel_entry`), so a row could read VERIFIED
        while the check behind it had failed, and renaming a stage left its row
        satisfied by an unrelated green check.
        """
        for profile, log, size, stages in ((ovmf.X64, X64_GOOD_LOG, 208896,
                                            ovmf.x64_stages()),
                                           (ovmf.IA32, IA32_GOOD_LOG, 67028,
                                            ovmf.ia32_stages())):
            report, _emitted = self._run(profile, log, size)
            rows = ovmf.build_matrix(report, profile)
            cold = [row for row in rows if row["source"].startswith("OVMF cold boot")]
            self.assertEqual(len(cold), len(stages))
            self.assertEqual([row["state"] for row in cold],
                             ["verified"] * len(stages),
                             [(row["requirement"], row["state"], row.get("note"))
                              for row in cold if row["state"] != "verified"])

    def test_a_failed_stage_fails_its_row_and_the_run(self):
        """The blanket-predicate bug, pinned.

        Dropping `PC5-64-MAP` leaves a boot that still prints `boot_ok`,
        `loader_banner` and `kernel_entry` -- so the old matrix called all twenty-plus
        cold rows verified while the map-agreement check had quietly failed.  Now that
        one row reads `failed`, and `matrix_failures` turns the row into a failed run,
        which is what keeps a green-looking summary from disagreeing with
        `ovmf-matrix.json`.
        """
        crippled = "\n".join(line for line in X64_GOOD_LOG.splitlines()
                             if not line.startswith("PC5-64-MAP"))
        report = ovmf.Report()
        ovmf.check_boot(report, run_of(crippled), "cold", 208896, ovmf.X64, 256)
        rows = ovmf.build_matrix(report, ovmf.X64)
        failed = [row["requirement"] for row in rows if row["state"] == "failed"]
        self.assertIn("the kernel's own map agrees with the loader's count", failed)
        self.assertNotIn("the kernel reaches its 64-bit success marker", failed)
        ovmf.matrix_failures(report, rows)
        self.assertTrue(any(check["state"] == "fail" and
                            "matrix rows failed" in check["detail"]
                            for check in report.checks),
                        "a failed row must be visible in the run's own status")

    def test_the_rows_that_did_not_run_are_not_tested(self):
        """The warm row and the controls, when this run skipped the machines."""
        report, _emitted = self._run(ovmf.X64, X64_GOOD_LOG, 208896)
        rows = ovmf.build_matrix(report, ovmf.X64)
        for row in rows:
            if row["source"].startswith("OVMF cold boot") or row["state"] == "external":
                continue
            self.assertEqual(row["state"], "not-tested",
                             "%r claims %r without running" % (row["requirement"],
                                                                row["state"]))

    def test_a_stage_without_its_file_is_not_applicable_not_passed(self):
        """No kernel file to compare sizes with -> the row says so.

        A `verified` for a row whose evidence was never available is the same defect
        as a `failed` for a stage that is fine: it makes the matrix disagree with the
        run.  `NOT_APPLICABLE` keeps the question visible, and exit code 3 keeps the
        run from being called complete.
        """
        report = ovmf.Report()
        ovmf.check_boot(report, run_of(X64_GOOD_LOG), "cold", None, ovmf.X64, 256)
        sizes = [check for check in report.checks if "real size" in check["name"]
                 or "exactly the kernel file" in check["name"]]
        self.assertEqual(len(sizes), 2)
        for check in sizes:
            self.assertEqual(check["state"], "skip", check["name"])

    def test_the_64bit_rows_are_not_the_32bit_rows(self):
        ia32 = {row[0] for row in ovmf.required_rows(ovmf.IA32)}
        x64 = {row[0] for row in ovmf.required_rows(ovmf.X64)}
        for requirement in ("the kernel runs on the page tables the loader built",
                            "the IDT is installed and complete before anything can fault",
                            "the frame allocator is initialised from the real memory map",
                            "the kernel reads real CPU state",
                            "the TSS/IST state is reported as measured, never as assumed",
                            "the kernel reaches its 64-bit success marker",
                            "the handover was verified by the kernel that received it"):
            self.assertIn(requirement, x64)
            self.assertNotIn(requirement, ia32)
        self.assertGreater(len(x64), len(ia32))
        # The 32-bit path keeps the rows it always had: nothing was dropped to make
        # room for the 64-bit ones.
        for requirement in ("firmware recognises the PC5 boot medium",
                            "the EFI boot entry is discovered",
                            "the PC5 EFI loader executes",
                            "required files are located and loaded",
                            "memory allocation and ownership are correct",
                            "the firmware watchdog is disarmed before handover",
                            "ExitBootServices succeeds with a current map key",
                            "the kernel handoff succeeds",
                            "the kernel reaches its expected initialisation point",
                            "serial output proves the execution path",
                            "rebooting the same machine boots identically"):
            self.assertIn(requirement, ia32)

    def test_the_64bit_corrupt_kernel_rows_are_enforced_by_the_image_format(self):
        """The rewrite of a test that documented a gap, kept as the guard on the closure.

        This row used to assert that `KERNEL64.BIN` had *no* header, and that the matrix said
        so -- the honest shape of an unfinished control.  `kernel64/include/pc5_kimage64.h`
        closed it, so the same expectations are now inverted, and the third assertion is the
        one that keeps the inversion from being permanent: the NOT TESTED wording is derived
        from the format, so a path with no header still gets it, and a path that loses its
        header re-opens the gap in the matrix instead of silently passing.
        """
        rows = [row for row in ovmf.required_rows(ovmf.X64) if "corrupted" in row[0]]
        self.assertEqual(len(rows), 1)
        self.assertIn("kimage64-magic", rows[0][0])
        self.assertEqual(ovmf.X64.bad_signature_code, "kimage64-magic")
        self.assertEqual(ovmf.IA32.bad_signature_code, "E4")
        self.assertIn("E4", ovmf.IA32.bad_signature_check())
        # Every field the profile says it refuses on has a row of its own, and the row names
        # the mutant it was built from -- which is how `build_mutant_iso` is asked for it.
        for mutation, code in ovmf.X64.kernel_refusals:
            wanted = ovmf.X64.kernel_refusal_check(mutation, code)
            self.assertIn((wanted, "OVMF negative run (kernel-%s)" % mutation, wanted),
                          ovmf.required_rows(ovmf.X64))
        bare = ovmf.Profile(key="test", label="test", kernel_name="KERNEL.TEST",
                            kernel_fat="\\KERNEL.TEST", success=(), stop=(),
                            failure_pattern=r"nope=([a-z]+)", protocol_version=9,
                            no_kernel_code="x", bad_signature_code=None, uefi_path="32")
        self.assertIn("no header field to corrupt yet", bare.bad_signature_check())
        self.assertEqual(bare.kernel_refusals, ())


class ReleaseEspIdentity(unittest.TestCase):
    """The gate must refuse to grade an ISO it was not given the ESP for.

    This is the failure the 64-bit gate actually hit on its first full run: `build/pc5.iso`
    still held the 32-bit ESP from the previous `PC5_UEFI=32` build while the Makefile
    handed the gate `build/efi64-release.img` and `build/kernel64/KERNEL64.BIN`.  The
    profile detection read the medium correctly, and then six rows failed for reasons
    that each looked like a defect in the boot -- a kernel size mismatch, two controls
    that refused nothing -- when one line about the pairing would have said it.  A gate
    that cannot name a mismatched artifact cannot be trusted to name anything else.
    """

    def _report_for(self, iso_bytes, esp_path):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            iso = os.path.join(tmp, "pc5.iso")
            with open(iso, "wb") as handle:
                handle.write(iso_bytes)
            report = ovmf.Report()
            ovmf.check_release_esp(report, iso, esp_path)
            return report

    def test_the_pairing_is_recorded_whether_or_not_it_matches(self):
        """The verdict follows the bytes, and the identity is recorded either way.

        Which mode built `build/pc5.iso` last is a property of the workspace, not of
        this test, so the assertion is about the *reporting*: the check passes exactly
        when the two digests agree, and `report.data` carries both digests even on a
        pass -- that is what lets a saved report say which artifact a boot was of
        (milestone 7 priority 4), rather than only that something matched.
        """
        import hashlib
        iso = os.path.join(ROOT, "build", "pc5.iso")
        esp = os.path.join(ROOT, "build", "efi64-release.img")
        if not (os.path.isfile(iso) and os.path.isfile(esp)):
            self.skipTest("no build/pc5.iso and build/efi64-release.img to compare")
        with open(iso, "rb") as handle:
            blob = handle.read()
        with open(esp, "rb") as handle:
            built = handle.read()
        report = self._report_for(blob, esp)
        check = report.checks[0]
        data = report.data["release_esp"]
        self.assertEqual(data["identical"],
                         hashlib.sha256(ovmf.esp_image_in_iso(blob)).hexdigest()
                         == hashlib.sha256(built).hexdigest())
        self.assertEqual(check["state"], "pass" if data["identical"] else "fail")
        self.assertEqual(data["built_sha256"], hashlib.sha256(built).hexdigest())
        self.assertEqual(data["iso_sha256"],
                         hashlib.sha256(ovmf.esp_image_in_iso(blob)).hexdigest())
        # A pass carries no detail: `Report.check` reserves details for failures, so
        # the digests above are the only place a passing pairing is recorded.  If that
        # ever changes, this assertion is the one that notices.
        self.assertEqual(check["detail"], "" if data["identical"] else check["detail"])
        self.assertIn("sha256=", check["detail"]) if not data["identical"] else None

    def test_a_mismatched_pair_names_the_pairing_as_the_fault(self):
        """A stale ISO against the other mode's ESP: one clear failure, digests shown."""
        iso = os.path.join(ROOT, "build", "pc5.iso")
        other = os.path.join(ROOT, "build", "efi.img")
        if not (os.path.isfile(iso) and os.path.isfile(other)):
            self.skipTest("need both build/pc5.iso and build/efi.img")
        with open(iso, "rb") as handle:
            blob = handle.read()
        esp = ovmf.esp_image_in_iso(blob)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            wrong = os.path.join(tmp, "other-esp.img")
            with open(wrong, "wb") as handle:
                handle.write(esp + b"\x00")     # same content, one byte longer
            report = self._report_for(blob, wrong)
        check = report.checks[0]
        self.assertEqual(check["state"], "fail")
        self.assertIn("booted a different image than the gate was pointed at",
                      check["detail"])
        self.assertFalse(report.data["release_esp"]["identical"])

    def test_a_medium_with_no_uefi_entry_is_reported_not_raised(self):
        report = self._report_for(bytes(4096), os.path.join(ROOT, "Makefile"))
        self.assertEqual(report.checks[0]["state"], "fail")
        self.assertIn("cannot be read", report.checks[0]["detail"])

    def test_the_esp_length_comes_from_512_byte_sectors(self):
        """El Torito sizes a boot image in 512-byte sectors, not 2048-byte ones.

        Reading `sector_count` as 2048-blocks asks for four times the volume: the slice
        runs off the end of the ISO, and a truncated slice walks like an empty FAT
        root -- which would read as "this ESP has no kernel", not as a parser bug.
        """
        iso = os.path.join(ROOT, "build", "pc5.iso")
        if not os.path.isfile(iso):
            self.skipTest("no build/pc5.iso; run `make iso` first")
        with open(iso, "rb") as handle:
            blob = handle.read()
        esp = ovmf.esp_image_in_iso(blob)
        self.assertEqual(len(esp), 1440 * 1024)   # tools/mkefiimg.py --size-kb 1440
        # The 2048-byte reading of the same count would run past this medium, which is
        # how the mistake shows up in a log: not as a wrong number, as no FAT at all.
        self.assertGreater(len(esp) * 4, len(blob) - 26 * 2048)


class MemoryCountsAreBounded(unittest.TestCase):
    """The E15 lesson, kept as an assertion that does not pin a bug's output.

    The old row demanded `conventional_mib == 1`, which was the value the bug
    produced; the fix (a loader that sums the real map) made the gate red.  These
    rows bound the count by the machine instead, so a hardcoded or zeroed total fails
    and an honest number -- whatever the guest size -- passes.
    """

    def test_both_counts_are_within_the_machine(self):
        seen_ia32 = ovmf.classify(run_of(IA32_GOOD_LOG), ovmf.IA32)
        self.assertEqual(seen_ia32["conventional_mib"], 210)
        self.assertEqual(seen_ia32["kernel_memory_mib"], 254)
        report = ovmf.Report()
        ovmf.check_boot(report, run_of(IA32_GOOD_LOG), "cold", 67028, ovmf.IA32, 256)
        names = [check["name"] for check in report.checks if check["state"] == "pass"]
        self.assertIn("[cold] the loader's conventional-memory total is a real count "
                      "within the memory the machine was given", names)

    def test_a_count_bigger_than_the_machine_fails(self):
        inflated = IA32_GOOD_LOG.replace("memory_conventional_mib=210",
                                         "memory_conventional_mib=4096")
        report = ovmf.Report()
        ovmf.check_boot(report, run_of(inflated), "cold", 67028, ovmf.IA32, 256)
        failed = [check["name"] for check in report.checks if check["state"] == "fail"]
        self.assertIn("[cold] the loader's conventional-memory total is a real count "
                      "within the memory the machine was given", failed)

    def test_a_zero_count_fails(self):
        zeroed = IA32_GOOD_LOG.replace("memory_conventional_mib=210",
                                       "memory_conventional_mib=0")
        report = ovmf.Report()
        ovmf.check_boot(report, run_of(zeroed), "cold", 67028, ovmf.IA32, 256)
        failed = [check["name"] for check in report.checks if check["state"] == "fail"]
        self.assertIn("[cold] the loader's conventional-memory total is a real count "
                      "within the memory the machine was given", failed)


class ArtifactDefaults(unittest.TestCase):
    def test_the_64bit_gate_is_not_handed_the_32bit_artifacts(self):
        """`tools/build_release.py` hardcodes `efi.img`/`mbr.bin`; the gate must not.

        The 64-bit release embeds `build/efi64-release.img`.  `build/efi64.img` is the
        bare `make uefi64` image the stub gate runs, which has no README.TXT, so it is
        not the ESP the release carries: comparing the ISO's ESP against the wrong one
        would fail for a reason that has nothing to do with the boot, and comparing it
        against `efi.img` would compare the wrong loader entirely.
        """
        self.assertEqual(ovmf.ARTIFACTS["x64"]["efi_image"], "efi64-release.img")
        self.assertEqual(ovmf.ARTIFACTS["ia32"]["efi_image"], "efi.img")
        # Both flavors are written to `build/pc5.iso`: same name, different contents.
        # That is exactly why the profile is detected from the ESP.
        self.assertEqual(ovmf.ARTIFACTS["x64"]["iso"], ovmf.ARTIFACTS["ia32"]["iso"])

    def test_the_64bit_kernel_path_is_the_one_the_makefile_builds(self):
        self.assertEqual(ovmf._build_kernel_path(ovmf.X64),
                         os.path.join(ovmf.BUILD, "kernel64", "KERNEL64.BIN"))
        self.assertEqual(ovmf._build_kernel_path(ovmf.IA32),
                         os.path.join(ovmf.BUILD, "kernel.bin"))


if __name__ == "__main__":
    unittest.main()


class HeaderVocabulary(unittest.TestCase):
    """The header facts the gate derives its negative controls from.

    `kernel_has_header` decides whether the corrupted-header control is enforced or
    reported as not testable, so a wrong offset here is a control that quietly never
    runs (or a mutant that corrupts an instruction nobody reads).  The numbers are
    therefore bound to the kernel's own header file rather than trusted.
    """

    def test_the_gates_header_offsets_are_the_kernels(self):
        path = os.path.join(ROOT, "kernel", "include", "pc5.h")
        if not os.path.isfile(path):
            self.skipTest("kernel/include/pc5.h is not in this checkout")
        with open(path, encoding="utf-8") as handle:
            header = handle.read()

        def define(name):
            match = re.search(r"#define\s+%s\s+(\S+)" % name, header)
            self.assertIsNotNone(match, "%s is not defined in pc5.h" % name)
            return match.group(1)

        self.assertEqual(define("PC5_KERNEL_SIG").strip('"'),
                         ovmf.PC5_KERNEL_SIG.decode("ascii"))
        self.assertEqual(int(define("PC5_KERNEL_SIG_OFFSET")),
                         ovmf.PC5_KERNEL_SIG_OFFSET)
        self.assertEqual(int(define("PC5_KERNEL_VERSION_OFFSET")),
                         ovmf.PC5_KERNEL_VERSION_OFFSET)
        self.assertEqual(int(define("PC5_KERNEL_HEADER_SIZE")),
                         ovmf.PC5_KERNEL_HEADER_SIZE)

    def test_an_image_with_the_signature_at_its_real_offset_has_a_header(self):
        image = b"\xeb\x00" + ovmf.PC5_KERNEL_SIG + bytes(64)
        self.assertTrue(ovmf.kernel_has_header(image))

    def test_an_instruction_stream_is_not_a_header(self):
        # The first bytes of build/kernel64/KERNEL64.BIN: `cli; mov rbx, rdi; lea ...`
        # -- what `kernel64/start.S` emits, because the 64-bit image format has no
        # header at all.  The gate must say so instead of corrupting code.
        self.assertFalse(ovmf.kernel_has_header(
            b"\xfa\x48\x89\xfb\x48\x8d\x25\xa5" + bytes(64)))

    def test_an_image_shorter_than_the_header_is_not_a_header(self):
        self.assertFalse(ovmf.kernel_has_header(b"\xeb\x00PC5"))
        self.assertFalse(ovmf.kernel_has_header(b""))

    def test_a_refusable_code_is_declared_only_where_there_is_a_header(self):
        """Each profile's claim has to agree with its kernel image.

        This is the same rule the gate asserts at run time through
        HEADER_VOCABULARY_CHECK; here it holds on the profiles alone, so the pairing
        is pinned even when no build artifacts exist to boot.
        """
        self.assertEqual(ovmf.IA32.bad_signature_code, "E4")
        self.assertEqual(ovmf.IA32.kernel_header_kind, "bios8")
        self.assertEqual(ovmf.X64.bad_signature_code, "kimage64-magic")
        self.assertEqual(ovmf.X64.kernel_header_kind, "kimage64")
        self.assertTrue(ovmf.kernel_has_header(
            b"\xeb\x00" + ovmf.PC5_KERNEL_SIG + bytes(64)))
        self.assertFalse(ovmf.kernel_has_header(b"\xfa\x48\x89\xfb" + bytes(64)))
        # Each format is recognised as *itself*, not merely as "a header": the gate corrupts
        # the field the profile's loader reads, and a 64-bit mutant built by scribbling at
        # offset 2 -- where the BIOS signature lives -- would be a control testing nothing.
        self.assertEqual(ovmf.kernel_header_kind(b"\xeb\x00" + ovmf.PC5_KERNEL_SIG
                                                 + bytes(64)), "bios8")
        kimage = bytearray(b"PC5K64IM" + bytes(120))
        kimage[16:24] = (128).to_bytes(8, "little")
        self.assertEqual(ovmf.kernel_header_kind(bytes(kimage)), "kimage64")
        self.assertEqual(ovmf.kernel_header_kind(b"\xfa\x48\x89\xfb\x48\x8d\x25\xa5"
                                                 + bytes(256)), None)

    def test_a_profile_can_find_the_code_it_promises_to_report(self):
        """A control that expects a code the profile's pattern cannot capture is dead."""
        for profile, line in ((ovmf.IA32, "PC5-UEFI-FAILURE E2 cannot read kernel"),
                              (ovmf.X64, "PC5-64-LOADER-FAIL=kernel-not-found")):
            match = re.search(profile.failure_pattern, line)
            self.assertIsNotNone(match,
                                 "%s cannot parse %r" % (profile.key, line))
            self.assertEqual(match.group(1), profile.no_kernel_code)


class BuildInfoAttribution(unittest.TestCase):
    """The image's own label, graded against the image.

    `check_buildinfo` is what makes a saved boot report re-derivable: the commit the
    artifact names is recorded in the report, and a record that contradicts the medium
    it sits in fails the gate.  Both halves are exercised here against synthetic
    media, because the point is the *judgement*, not the state of `build/`.
    """

    def setUp(self):
        self.real_buildinfo = ovmf.iso_buildinfo
        self.real_directory = ovmf.esp_directory
        self.iso = os.path.join(ROOT, "build", "pc5.iso")

    def tearDown(self):
        ovmf.iso_buildinfo = self.real_buildinfo
        ovmf.esp_directory = self.real_directory

    def _run(self, buildinfo, files, profile=ovmf.X64, iso="/nonexistent.iso"):
        ovmf.iso_buildinfo = lambda path: dict(buildinfo)
        ovmf.esp_directory = lambda path: dict(files)
        report = ovmf.Report()
        ovmf.check_buildinfo(report, iso, profile)
        return report

    def test_a_consistent_record_is_a_pass_and_is_copied_into_the_report(self):
        loader = b"MZ\x90\x00fake-efi"
        import hashlib
        digest = hashlib.sha256(loader).hexdigest()[:16]
        report = self._run(
            {"build": "abc1234-dirty", "commit": "0" * 39 + "a",
             "uefi-path": "64", "esp-loader": digest},
            {"EFI/BOOT/BOOTX64.EFI": loader, "KERNEL64.BIN": b"\xfa\x48"})
        check = report.checks[0]
        self.assertEqual(check["state"], "pass", check["detail"])
        self.assertEqual(report.data["environment"]["buildinfo"]["commit"],
                         "0" * 39 + "a")

    def test_a_record_naming_the_other_architecture_fails(self):
        loader = b"MZ\x90\x00"
        import hashlib
        report = self._run(
            {"build": "abc1234", "commit": "0" * 40, "uefi-path": "32",
             "esp-loader": hashlib.sha256(loader).hexdigest()[:16]},
            {"EFI/BOOT/BOOTX64.EFI": loader, "KERNEL64.BIN": b"\xfa\x48"})
        check = report.checks[0]
        self.assertEqual(check["state"], "fail")
        self.assertIn("uefi-path=32", check["detail"])
        self.assertIn("KERNEL64.BIN", check["detail"])

    def test_a_record_quoting_a_loader_that_is_not_on_the_volume_fails(self):
        """A label from a previous build, which is what staleness looks like here."""
        report = self._run({"build": "abc1234", "commit": "0" * 40,
                             "uefi-path": "64", "esp-loader": "0" * 16},
                            {"EFI/BOOT/BOOTX64.EFI": b"MZ\x90\x00",
                             "KERNEL64.BIN": b"\xfa\x48"})
        check = report.checks[0]
        self.assertEqual(check["state"], "fail")
        self.assertIn("is not the loader on the volume", check["detail"])

    def test_a_record_that_names_no_commit_fails(self):
        report = self._run({"version": "0.2.0", "uefi-path": "64",
                            "esp-loader": "abc"},
                           {"EFI/BOOT/BOOTX64.EFI": b"MZ", "KERNEL64.BIN": b"\xfa"})
        check = report.checks[0]
        self.assertEqual(check["state"], "fail")
        self.assertIn("does not name a source commit", check["detail"])

    def test_a_short_commit_field_fails(self):
        """`commit:` means the full object id; a short id belongs in `build:`."""
        import hashlib
        loader = b"MZ"
        report = self._run({"build": "abc1234", "commit": "abc1234",
                            "uefi-path": "64",
                            "esp-loader": hashlib.sha256(loader).hexdigest()[:16]},
                           {"EFI/BOOT/BOOTX64.EFI": loader, "KERNEL64.BIN": b"\xfa"})
        check = report.checks[0]
        self.assertEqual(check["state"], "fail")
        self.assertIn("not a full object id", check["detail"])

    def test_an_unreadable_medium_is_reported_not_raised(self):
        def boom(path):
            raise ValueError("cannot read the ESP of /nope: no boot catalog")
        ovmf.iso_buildinfo = boom
        report = ovmf.Report()
        ovmf.check_buildinfo(report, "/nope", ovmf.X64)
        check = report.checks[0]
        self.assertEqual(check["state"], "skip")
        self.assertIn("no boot catalog", check["detail"])
        self.assertIsNone(report.data["environment"]["buildinfo"])
        self.assertEqual(report.fail_count(), 0,
                         "an unreadable medium is not the boot's fault to count twice")

    def test_the_shipped_image_carries_a_record_that_matches_its_medium(self):
        """The same judgement, on the artifact the release target produces."""
        if not os.path.isfile(self.iso):
            self.skipTest("no build/pc5.iso; run `make iso` first")
        profile, note = ovmf.detect_profile(self.iso)
        if profile is None:
            self.skipTest("the built medium cannot be read: %s" % note)
        report = ovmf.Report()
        self.real_buildinfo.__name__          # attribute exists -- guard against a rename
        ovmf.check_buildinfo(report, self.iso, profile)
        check = report.checks[0]
        self.assertEqual(check["state"], "pass", check["detail"])
        info = report.data["environment"]["buildinfo"]
        self.assertEqual(info["uefi-path"], profile.uefi_path)
        # The label has to say `-dirty` unless the tree is clean; the build cannot
        # promise more than that, and neither can this test.
        self.assertRegex(info.get("build", ""), r"^[0-9a-f]{7,40}(-dirty)?$")
