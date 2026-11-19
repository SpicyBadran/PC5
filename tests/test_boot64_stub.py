"""End-to-end checks of the experimental 64-bit path under stub firmware.

`make stubtest64` runs this from the command line; the tests here are the same
checks driven from the unit suite, so `make check` covers the 64-bit path too
whenever Unicorn is installed.  Without Unicorn these tests skip and say why -
they never quietly pass.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

MARKER = "PC5-64BIT-LONG-MODE-OK"

try:
    import unicorn  # noqa: F401
    HAVE_UNICORN = True
except ImportError:
    HAVE_UNICORN = False

import test_kernel64  # noqa: E402  (build helper, reused so both run once)


def artifacts():
    test_kernel64.build()
    return (pathlib.Path(test_kernel64.LOADER_EFI).read_bytes(),
            pathlib.Path(test_kernel64.KERNEL_BIN).read_bytes())


@unittest.skipUnless(HAVE_UNICORN, "unicorn is not installed (pip install unicorn)")
class StubFirmware64Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import boot64_stub
        import boot64_stubtest
        cls.module = boot64_stub
        cls.runner = boot64_stubtest
        cls.image, cls.kernel = artifacts()

    def run_once(self, **options):
        machine = self.module.StubFirmware64(
            image=self.image,
            esp_volume={"\\EFI\\BOOT\\BOOTX64.EFI": self.image,
                        "\\KERNEL64.BIN": self.kernel},
            kernel_bytes=self.kernel,
            **options)
        return machine, machine.run()

    def test_the_whole_boot_path_passes_every_check(self):
        _, trace = self.run_once()
        checks = self.runner.checks_for(trace)
        failed = [entry["name"] for entry in checks if not entry["passed"]]
        self.assertEqual(failed, [], trace.serial)
        self.assertIn(MARKER, trace.serial)
        self.assertTrue(trace.serial.rstrip().endswith("PC5-64-DONE"))

    def test_the_kernel_runs_in_long_mode_with_pc5_page_tables(self):
        machine, trace = self.run_once()
        self.assertIn("PC5-64-PAGING enabled=1", trace.serial)
        self.assertIn("PC5-64-ENV", trace.serial)
        # EFER.LMA is what "long mode executing" means at the architectural
        # level; the kernel prints the value it read.
        self.assertIn("efer=0x500", trace.serial)
        self.assertEqual(machine.handover64["protocol"], 2)
        self.assertEqual(machine.handover64["boot_info_size"], 160)
        self.assertEqual(machine.handover64["identity_span"], 4 << 30)

    def test_no_boot_service_is_used_after_exit_boot_services(self):
        machine, trace = self.run_once()
        self.assertEqual(machine.boot_service_calls_after_exit, [])
        self.assertTrue(trace.exited)

    def test_a_stale_memory_map_key_is_retried_not_fatal(self):
        machine, trace = self.run_once(race_map_key=1)
        self.assertIn("exit-boot-services=stale-key retry=1", trace.serial)
        self.assertIn("exit-boot-services=ok attempts=2", trace.serial)
        self.assertIn(MARKER, trace.serial)

    def test_a_permanently_stale_key_stops_the_boot_without_the_marker(self):
        machine, trace = self.run_once(race_map_key_forever=True)
        self.assertIn("PC5-64-LOADER-FAIL=exit-boot-services", trace.serial)
        self.assertNotIn(MARKER, trace.serial)

    # ------------------------------------------------------- placement ----
    # Firmware chooses where the application goes.  These runs move it near the top
    # of the identity map, and move the handoff regions there too, because the
    # loader must not assume either is low.  Unicorn translates through CR3 after
    # the switch, so a region the map does not cover faults the run rather than
    # quietly succeeding.

    def assert_full_boot(self, trace):
        checks = self.runner.checks_for(trace)
        failed = [entry["name"] for entry in checks if not entry["passed"]]
        self.assertEqual(failed, [], trace.serial)
        self.assertIn(MARKER, trace.serial)

    def test_an_application_loaded_at_the_top_of_the_map_still_hands_over(self):
        machine, trace = self.run_once(image_load=0x0FE00000)
        self.assertEqual(machine.image_base, 0x0FE00000)
        self.assertIn("PC5-64-LOADER image=0xfe00000-0xfe03000 below-span=1",
                      trace.serial)
        self.assertIn("PC5-64-LOADER image=0xfe00000 size=0x3000 range=ok",
                      trace.serial)
        self.assert_full_boot(trace)

    def test_handoff_regions_placed_just_below_the_span_limit(self):
        machine, trace = self.run_once(high_allocations=True)
        # The boot info, page tables and stack all landed in the last megabyte.
        self.assertIn("bootinfo=0xff00000", trace.serial)
        self.assertIn("PC5-64-LOADER pagetables=0xff01000", trace.serial)
        self.assertIn("PC5-64-LOADER stack=0xff07000-0xff0b000", trace.serial)
        self.assertIn("PC5-64-STACK rsp=0xff0", trace.serial)
        self.assert_full_boot(trace)
        # Every mandatory range was accepted because firmware retyped these pages as
        # LoaderData - the kernel must not demand Conventional memory for ranges the
        # loader owns.
        self.assertIn("range=ok", trace.serial)

    def test_the_loader_replaces_cr3_only_after_validating_its_own_placement(self):
        machine, trace = self.run_once(image_load=0x0FE00000)
        serial = trace.serial
        self.assertLess(serial.index("PC5-64-LOADER image="),
                        serial.index("PC5-64-LOADER cr3-plan="))
        self.assertLess(serial.index("PC5-64-LOADER cr3-plan="),
                        serial.index("PC5-64-LOADER-JUMP"))
        self.assertLess(serial.index("PC5-64-LOADER-JUMP"),
                        serial.index("PC5-64-PAGING enabled=1"))


if __name__ == "__main__":
    unittest.main()
