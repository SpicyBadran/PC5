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

    def assert_hierarchy_maps(self, machine, address):
        """Prove `address` translates through the page tables the loader installed.

        The audit finding was that a framebuffer above 1 GiB was reported usable
        while no PDPT entry referenced its page directory.  Reading the bot info or
        the span is not enough: the question is whether the *installed hierarchy*
        reaches the address, so this walks PML4 -> PDPT -> PD in the emulated
        memory, using the CR3 root the loader recorded.
        """
        # The loader prints the CR3 root it installed; the kernel independently
        # re-reads it from CR3 and fails the boot if the two disagree, so this value
        # is confirmed by the run that just happened rather than assumed.
        marker = "PC5-64-LOADER-JUMP entry="
        line = next((l for l in machine.trace.serial.splitlines()
                     if marker in l), None)
        self.assertIsNotNone(line, "loader never reached the handover")
        token = next(t for t in line.split() if t.startswith("bootinfo="))
        boot_info = int(token.split("=", 1)[1], 16)
        # struct pc5_boot64: pagetable_addr is at offset 104 (pinned both sides).
        root = machine.read_u64(boot_info + 104)
        pml4e = machine.read_u64(root + ((address >> 39) & 0x1FF) * 8)
        self.assertTrue(pml4e & 1, f"{address:#x}: PML4 entry not present")
        pdpte = machine.read_u64((pml4e & 0x000FFFFFFFFFF000)
                                 + ((address >> 30) & 0x1FF) * 8)
        self.assertTrue(pdpte & 1,
                        f"{address:#x}: PDPT entry for the 1 GiB region is not present"
                        f" - the region is outside the installed hierarchy")
        self.assertFalse(pdpte & (1 << 7),
                         f"{address:#x}: PDPT entry is a 1 GiB leaf, unexpected here")
        pde = machine.read_u64((pdpte & 0x000FFFFFFFFFF000)
                               + ((address >> 21) & 0x1FF) * 8)
        self.assertTrue(pde & 1, f"{address:#x}: PD entry not present")
        self.assertTrue(pde & (1 << 7), f"{address:#x}: PD entry is not a 2 MiB page")
        self.assertEqual(pde & ~0x1FFFFF, address & ~0x1FFFFF,
                         f"{address:#x}: leaf maps the wrong physical page")

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

    # ------------------------------------------------------- framebuffer ----
    # The loader measures the firmware's GOP mode and must refuse a framebuffer it
    # cannot describe exactly.  Each case below is a boundary of that arithmetic,
    # driven through the real loader under the stub firmware.

    def test_an_extreme_scan_line_is_refused_for_its_pitch(self):
        _machine, trace = self.run_once(gop={"pixels_per_scan_line": 0x40000000})
        self.assertIn("unusable=invalid-geometry", trace.serial)

    def test_a_pitch_above_the_protocol_field_is_refused(self):
        # 4 * (2^30 + 1) = 2^32 + 4: the pitch does not fit the 32-bit field.  The
        # old code truncated it into the field and then multiplied, so the wrapped
        # product could look like a small, mappable framebuffer.
        _machine, trace = self.run_once(
            gop={"pixels_per_scan_line": 0x40000001, "height": 0xFFFFFFFD,
                 "base": 0x1000})
        self.assertIn("unusable=invalid-geometry", trace.serial)

    def test_a_multiplication_that_wraps_is_refused(self):
        _machine, trace = self.run_once(
            gop={"pixels_per_scan_line": 0x40000001, "height": 0xFFFFFFFF,
                 "base": 0x1000})
        self.assertIn("unusable=invalid-geometry", trace.serial)

    def test_an_extreme_vertical_resolution_is_refused(self):
        _machine, trace = self.run_once(gop={"height": 0xFFFFFFFF})
        self.assertIn("unusable=outside-identity-span", trace.serial)

    def test_zero_dimensions_are_refused(self):
        for options in ({"width": 0}, {"height": 0}, {"pixels_per_scan_line": 0}):
            with self.subTest(**options):
                _machine, trace = self.run_once(gop=options)
                self.assertIn("unusable=invalid-geometry", trace.serial)

    def test_a_framebuffer_ending_exactly_at_the_span_is_accepted(self):
        span = 4 << 30
        base = span - (4096 * 768)
        machine, trace = self.run_once(gop={"base": base})
        self.assertNotIn("unusable=", trace.serial)
        self.assertIn("PC5-64-LOADER framebuffer=0xffd00000", trace.serial)
        # Accepting it is only correct if the address is actually reachable through
        # the hierarchy the loader just built.  0xffd00000 sits in the last 1 GiB
        # region, which is the region a PDPT[0]-only map leaves unmapped - so this
        # assertion failed against the pre-fix loader even though the two above it
        # passed.  Without it the test blessed an unmapped framebuffer as usable.
        self.assert_hierarchy_maps(machine, 0xffd00000)

    def test_a_framebuffer_in_every_gibibyte_region_is_reachable(self):
        # A framebuffer is only usable if the loader's own page tables map it.  Each
        # of the four 1 GiB regions is exercised, because a map that installs only
        # PDPT[0] still reports every region as inside the span.
        span = 4 << 30
        for region in range(4):
            base = region * (1 << 30) + 0x08000000
            with self.subTest(region=region, base=hex(base)):
                machine, trace = self.run_once(gop={"base": base})
                self.assertNotIn("unusable=", trace.serial)
                self.assert_hierarchy_maps(machine, base)
                self.assertIn("usability=ok", trace.serial)

    def test_a_framebuffer_crossing_the_span_is_refused(self):
        span = 4 << 30
        _machine, trace = self.run_once(gop={"base": span - 0x1000})
        self.assertIn("unusable=outside-identity-span", trace.serial)

    def test_both_direct_colour_formats_are_accepted(self):
        for fmt in (0, 1):
            with self.subTest(format=fmt):
                _machine, trace = self.run_once(gop={"format": fmt})
                self.assertNotIn("unusable=", trace.serial)
                self.assertIn("PC5-64-FB addr=0x4000000", trace.serial)
                self.assertIn("usability=ok", trace.serial)

    def test_a_bitmask_format_is_classified_unsupported(self):
        _machine, trace = self.run_once(gop={"format": 2})
        self.assertIn("unusable=unsupported-format", trace.serial)
        self.assertIn("PC5-64-FB usability=unsupported reason=unsupported-by-loader",
                      trace.serial)

    def test_a_blt_only_format_is_classified_unsupported(self):
        _machine, trace = self.run_once(gop={"format": 3})
        self.assertIn("unusable=unsupported-format", trace.serial)
        self.assertIn("PC5-64-FB usability=unsupported reason=unsupported-by-loader",
                      trace.serial)

    def test_an_unknown_format_is_classified_unsupported(self):
        _machine, trace = self.run_once(gop={"format": 9})
        self.assertIn("unusable=unsupported-format", trace.serial)

    # ------------------------------------------------------ memory-map set ----
    # The loader validates the descriptor set it is about to hand over, not just
    # each descriptor on its own.

    def test_the_loader_refuses_a_map_with_overlapping_descriptors(self):
        machine = self.module.StubFirmware64(
            image=self.image,
            esp_volume={"\\EFI\\BOOT\\BOOTX64.EFI": self.image,
                        "\\KERNEL64.BIN": self.kernel},
            kernel_bytes=self.kernel)
        original = machine.memory_descriptors

        def overlapping():
            descriptors = list(original())
            # (type, physical_start, attributes, pages): conventional bytes
            # inside the kernel window, which the map already claims as loader data.
            descriptors.append((7, 0x180000, 0x0, 0x10))
            return descriptors

        machine.memory_descriptors = overlapping
        trace = machine.run()
        self.assertIn("PC5-64-LOADER-FAIL=memory-map-overlap", trace.serial)
        self.assertNotIn("PC5-64-LOADER-JUMP", trace.serial)

    def test_the_loader_accepts_a_valid_unsorted_map(self):
        machine = self.module.StubFirmware64(
            image=self.image,
            esp_volume={"\\EFI\\BOOT\\BOOTX64.EFI": self.image,
                        "\\KERNEL64.BIN": self.kernel},
            kernel_bytes=self.kernel)
        original = machine.memory_descriptors

        def unsorted():
            return list(reversed(original()))

        machine.memory_descriptors = unsorted
        trace = machine.run()
        self.assertIn("PC5-64-LOADER-JUMP", trace.serial)
        self.assert_full_boot(trace)


if __name__ == "__main__":
    unittest.main()


class AllocateMaxAddressTests(unittest.TestCase):
    """EFI_ALLOCATE_MAX_ADDRESS: the bound the loader passes, and what it accepts.

    The defect this class exists for: alloc_below_4g() passed 0 as the *input*
    maximum address.  UEFI 2.9A 7.2.1 defines that input as "the address pointed to
    by Memory on input" such that the allocated range's uppermost address is <= it,
    so 0 describes a range that ends at or below zero and no allocation can satisfy
    it.  The old stub ignored the allocation type entirely and always returned a low
    pool address, which is why the whole 64-bit suite passed over a request no real
    firmware can honour.
    """

    @classmethod
    def setUpClass(cls):
        import boot64_stub
        import boot64_stubtest
        cls.module = boot64_stub
        cls.runner = boot64_stubtest
        cls.image, cls.kernel = artifacts()

    def machine(self, **options):
        return self.module.StubFirmware64(
            image=self.image,
            esp_volume={"\\EFI\\BOOT\\BOOTX64.EFI": self.image,
                        "\\KERNEL64.BIN": self.kernel},
            kernel_bytes=self.kernel, **options)

    # ------------------------------------------- what the loader asks for ----
    def test_the_loader_passes_a_maximum_that_can_actually_be_satisfied(self):
        machine = self.machine()
        trace = machine.run()
        self.assertIn(MARKER, trace.serial)
        # Every MaxAddress request the loader made must have carried a usable bound.
        max_requests = [a for a in machine.allocations
                        if a[0] == self.module.EFI_ALLOCATE_MAX_ADDRESS]
        self.assertGreaterEqual(len(max_requests), 3,
                                "expected boot info, page tables and stack")
        for alloc_type, pages, base in max_requests:
            self.assertNotEqual(base, 0)
            self.assertLess(base + pages * 4096, 1 << 32,
                            "allocation must end below the 4 GiB identity limit")

    def test_a_zero_maximum_verifies_as_unsatisfiable(self):
        """The contract the old code violated, asserted directly on the stub."""
        import ctypes
        machine = self.machine()
        # Ask the stub itself, with a zero bound, and require refusal.
        status = machine.svc_allocate_pages(
            self.module.EFI_ALLOCATE_MAX_ADDRESS, 2, 1, machine.alloc(8))
        self.assertEqual(status, self.module.EFI_OUT_OF_RESOURCES,
                         "a zero AllocateMaxAddress bound must not allocate")

    def test_a_maximum_below_the_page_count_cannot_succeed(self):
        machine = self.machine()
        slot = machine.alloc(8)
        machine.write_u64(slot, 0x2000)          # 8 KiB bound, 3 pages requested
        status = machine.svc_allocate_pages(
            self.module.EFI_ALLOCATE_MAX_ADDRESS, 2, 3, slot)
        self.assertNotEqual(status, 0,
                            "a 3-page request cannot fit below a 8 KiB bound")

    # ------------------------- the loader must reject a lying firmware ----
    def test_firmware_that_ignores_the_bound_is_rejected(self):
        """A range whose last byte crosses 4 GiB must stop the boot.

        `ignore_max_address` models firmware that allocates without honouring the
        input bound, and `allocation_limit` tells it to hand out pages from the very
        top of the 4 GiB space.  The loader must check what it was given instead of
        trusting the status code: the boot info, page tables or stack landing at the
        boundary would leave a structure straddling the identity limit.
        """
        machine = self.machine(ignore_max_address=True, allocations_at_limit=True)
        trace = machine.run()
        self.assertNotIn(MARKER, trace.serial,
                         "a range crossing the identity limit must fail the boot")
        self.assertIn("PC5-64-LOADER-FAIL=", trace.serial)

    def test_a_failed_allocation_stops_the_boot_before_the_handover(self):
        machine = self.machine(allocate_return=self.module.EFI_OUT_OF_RESOURCES)
        trace = machine.run()
        self.assertNotIn(MARKER, trace.serial)
        self.assertNotIn("PC5-64BIT-KERNEL-ENTRY", trace.serial,
                         "the kernel must never be entered")
        self.assertIn("PC5-64-LOADER-FAIL=", trace.serial)

    def test_allocation_failure_happens_before_exit_boot_services(self):
        """Every allocation precedes ExitBootServices, so failure is reportable."""
        machine = self.machine()
        trace = machine.run()
        self.assertIn(MARKER, trace.serial)
        # The serial log orders the two: allocations print, then exit-boot-services.
        serial = trace.serial
        self.assertLess(serial.index("PC5-64-LOADER pagetables="),
                        serial.index("exit-boot-services=ok"))

    # --------------------------------- A/B: the audit's experiment in code ----
    def test_conforming_firmware_can_satisfy_the_loader(self):
        """A, part 1: with a real MaxAddress bound, a correct loader succeeds."""
        machine = self.machine()
        trace = machine.run()
        self.assertIn(MARKER, trace.serial)
        self.assertIn("PC5-64-DONE", trace.serial)

    def test_conforming_firmware_with_no_memory_in_range_fails_safely(self):
        """A, part 2: an honest bound with no memory under it is refused.

        Exercised at the service boundary rather than by booting: the loader's first
        allocation is the boot info, so a firmware with nothing below the bound fails
        that call, and the loader's own failure path is covered by
        test_a_failed_allocation_stops_the_boot_before_the_handover.
        """
        machine = self.machine(allocation_limit=0x2000)
        slot = machine.alloc(8)
        machine.write_u64(slot, (1 << 32) - 1)   # an honest, generous bound
        status = machine.svc_allocate_pages(
            self.module.EFI_ALLOCATE_MAX_ADDRESS, 2, 4, slot)
        self.assertNotEqual(status, 0,
                            "no memory below the bound must be refused, not granted")

    def test_the_high_allocation_window_is_inside_the_bound(self):
        """Firmware may place the handover regions anywhere up to the bound."""
        machine = self.machine(high_allocations=True)
        trace = machine.run()
        self.assertIn(MARKER, trace.serial,
                      "allocations just below the caller's bound must be accepted")
        used = [a for a in machine.allocations if a[2] >= 0x0FF00000]
        self.assertTrue(used, "the high window should have been used")
