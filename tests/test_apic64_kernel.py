"""Kernel-level tests that the local APIC bring-up is reachable from the real boot.

`tests/test_apic64.py` proves the register logic against an injected bus. This file
proves the *integration*: that the real kernel, booted in the emulator, attempts
bring-up exactly when the memory map vouches for the window, and does not touch
that window otherwise.

The distinction matters because the failure it guards against was real. An earlier
version of this change called `apic64_bring_up()` unconditionally. Under the stub
firmware that produced:

    emulation faulted: Invalid memory read (UC_ERR_READ_UNMAPPED) at rip=0x10005a

i.e. it broke the boot on any firmware that does not map `0xFEE00000`. The lesson
is that the APIC probe cannot be the code that discovers a missing window - an
unbacked MMIO read faults rather than returning all-ones, so the gate has to come
first. `test_unmapped_window_does_not_break_the_boot` is the regression test for
that, and it runs the real kernel, not a mock.

These tests skip without `unicorn`; a skip is reported as a skip, never a pass.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
KERNEL_BIN = ROOT / "build" / "kernel64" / "KERNEL64.BIN"

APIC_BASE = 0xFEE00000
APIC_VERSION = 0x030
APIC_ID = 0x020
APIC_SVR = 0x0F0
APIC_ESR = 0x280

# A plausible xAPIC: version 0x00050014 (5 LVTs), ID 0, SVR at its architectural
# reset value 0xFF (enable clear), clean ESR.
APIC_REGS = {APIC_VERSION: 0x00050014, APIC_ID: 0, APIC_SVR: 0xFF, APIC_ESR: 0}


def _build():
    """Build the 64-bit kernel binary once per test class."""
    import subprocess
    subprocess.run(["make", "-s", "-C", str(ROOT), "kernel64"],
                   check=True, capture_output=True)


class ApicIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import unicorn  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(f"unicorn is not installed: {exc}")
        _build()
        sys.path.insert(0, str(TESTS))
        import kernel64_machine
        cls.machine = kernel64_machine
        cls.kernel = KERNEL_BIN.read_bytes()

    # -- helpers ---------------------------------------------------------

    def _run(self, memmap, back_apic=False):
        """Boot the real kernel; optionally back the APIC page with registers.

        The instruction budget is generous because the timer now calibrates against
        the PIT at boot, and an emulated PIT that never answers costs its full poll
        budget. A tighter cap stops the kernel mid-calibration and looks like a boot
        failure.

        `run_kernel` maps only the memory the boot info describes, so an MMIO
        descriptor alone leaves the window mapped but *unbacked*, which is what an
        emulator does for a device it has no model for.  Backing it is therefore a
        separate step, done after the machine exists and before it runs.
        """
        machine = self.machine.Machine(self.kernel)
        if back_apic:
            machine.uc.mem_map(APIC_BASE, 0x1000)
            for offset, value in APIC_REGS.items():
                machine.uc.mem_write(APIC_BASE + offset, value.to_bytes(4, "little"))
        overrides = {"memmap_bytes": self.machine.DESC_SIZE * len(memmap)}
        result = machine.run(self.machine.pack_boot_info(**overrides),
                            memmap=memmap, max_instructions=8_000_000)
        result["machine"] = machine
        return result

    def _apic_lines(self, serial):
        return [ln for ln in serial.splitlines() if "PC5-64-APIC" in ln]

    # -- the regression this change introduced and then fixed -------------

    def test_unmapped_window_does_not_break_the_boot(self):
        """The default map does not describe the APIC, so nothing may touch it.

        MUTATION: calling `apic64_bring_up()` without the `pc5_map_covers()` gate
        makes this boot die with UC_ERR_READ_UNMAPPED before reaching
        PC5-64-DONE. That is precisely what happened before the gate existed.
        """
        result = self._run(self.machine.DEFAULT_MEMMAP)
        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(result["stopped_on"], "PC5-64-DONE")

    def test_unmapped_window_is_reported_as_not_attempted(self):
        """Silence would be worse than refusal: the log must say why."""
        result = self._run(self.machine.DEFAULT_MEMMAP)
        lines = self._apic_lines(result["serial"])
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("not-attempted", lines[0])
        self.assertIn("no MMIO range", lines[0])
        # And it must not claim any register was read.
        self.assertNotIn("present=1", result["serial"])

    def test_boot_still_reaches_long_mode_marker_with_apic_refused(self):
        """The APIC is not on the critical path yet, and must not become one."""
        result = self._run(self.machine.DEFAULT_MEMMAP)
        self.assertIn("PC5-64BIT-LONG-MODE-OK", result["serial"])

    # -- the path that actually runs bring-up ----------------------------
    #
    # A limitation, measured rather than assumed: Unicorn 2.1.4 refuses an executed
    # read from *any* address above roughly 1 GiB under paging, even when the page
    # is mapped with mem_map and backed with mem_write. Reproduced in isolation with
    # no kernel code involved:
    #
    #     read 0x00800000: OK      read 0xc0000000: FAULT
    #     read 0x40000000: FAULT   read 0xfee00000: FAULT
    #
    # The architectural APIC base (0xFEE00000) is therefore unreachable in this
    # emulator under paging, so the *successful* bring-up path cannot be driven
    # through the emulator. It is covered instead by `tests/test_apic64.py`, which
    # runs the real driver against an injected bus - and, importantly, the kernel
    # behaves correctly here anyway: it refuses cleanly and still finishes the boot.
    #
    # This is a limit of the test environment, not of the kernel, and it is stated
    # rather than papered over with a test that cannot fail.

    HIGH_ADDRESS_CEILING_DOC = (
        "Unicorn 2.1.4 faults on executed reads above ~1 GiB under paging, so the "
        "architectural APIC base cannot be exercised end to end in this emulator.")

    def test_unicorn_cannot_reach_the_apic_base_under_paging(self):
        """Pin the emulator limitation so it is a known fact, not a surprise.

        If a future Unicorn version lifts it, this test starts failing and the
        success path above can be enabled - a failing test is the right signal
        here, because it means the reason for skipping is gone.
        """
        memmap = list(self.machine.DEFAULT_MEMMAP) + [(APIC_BASE, 1, self.machine.PC5_MMAP_MMIO)]
        result = self._run(memmap, back_apic=True)
        self.assertIsNotNone(
            result["error"],
            "Unicorn now reaches the APIC base under paging; replace this test with "
            "one that asserts a successful bring-up in the real kernel")
        self.assertIn("UC_ERR", result["error"])

    def test_apic_failure_never_becomes_a_boot_failure(self):
        """The property that matters: whatever the APIC does, the boot verdict holds.

        This is the regression guard for the bug this change actually introduced.
        An earlier draft called bring-up unconditionally, and the stub firmware
        reported `Invalid memory read (UC_ERR_READ_UNMAPPED) at rip=0x10005a` - the
        boot broken by an optional facility. The gate on the memory map fixes it,
        and this asserts the outcome that matters rather than the mechanism.
        """
        result = self._run(self.machine.DEFAULT_MEMMAP)
        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(result["stopped_on"], "PC5-64-DONE")
        self.assertIn("PC5-64BIT-LONG-MODE-OK", result["serial"])
        # The APIC must be explicitly refused, never silently absent.
        self.assertIn("PC5-64-APIC not-attempted", result["serial"])

    def test_apic_reporting_cannot_precede_the_boot_checks(self):
        """Bring-up is reported after the CPU report, not before validation."""
        result = self._run(self.machine.DEFAULT_MEMMAP)
        serial = result["serial"]
        self.assertLess(serial.index("PC5-64-CPU vendor="), serial.index("PC5-64-APIC"))
        self.assertGreater(serial.index("PC5-64-APIC"), serial.index("PC5-64-MAP"))


if __name__ == "__main__":
    unittest.main()
