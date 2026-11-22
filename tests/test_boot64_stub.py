"""End-to-end checks of the experimental 64-bit path under stub firmware.

`make stubtest64` runs this from the command line; the tests here are the same
checks driven from the unit suite, so `make check` covers the 64-bit path too
whenever Unicorn is installed.  Without Unicorn these tests skip and say why -
they never quietly pass.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
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


#: Why a decorator and not a `setUpClass` guard: a skip raised from `setUpClass`
#: replaces the whole class with a single summary entry, so the suite *shrinks* when
#: the dependency is missing and a reader counting tests sees fewer tests run rather
#: than the same tests reported as skipped.  Decorators keep the count stable and the
#: reason attached to each test -- "skipped", never "passed", never "errored".
REQUIRE_UNICORN = unittest.skipUnless(
    HAVE_UNICORN, "unicorn is not installed (pip install -r requirements-harness.txt)")


def _header_constant(name):
    """Evaluate a `#define` from the real kernel header by compiling it.

    The tests already reach for the genuine header this way (`test_map64`,
    `test_kernel64`) rather than restating constants in Python.  Keeping that
    convention means a change to `pc5_boot64.h` cannot leave a test asserting a
    value the firmware no longer uses.
    """
    header = ROOT / "kernel64" / "include"
    source = ('#include "pc5_boot64.h"\n'
              "#include <stdio.h>\n"
              f"int main(void){{printf(\"%llu\\n\", (unsigned long long)({name}));}}\n")
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "probe.c"
        path.write_text(source)
        binary = pathlib.Path(directory) / "probe"
        compiled = subprocess.run(
            ["gcc", "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror",
             "-I", str(header), str(path), "-o", str(binary)],
            capture_output=True, text=True)
        assert compiled.returncode == 0, compiled.stderr
        done = subprocess.run([str(binary)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    return int(done.stdout.strip())


def artifacts():
    test_kernel64.build()
    return (pathlib.Path(test_kernel64.LOADER_EFI).read_bytes(),
            pathlib.Path(test_kernel64.KERNEL_BIN).read_bytes())


@unittest.skipUnless(HAVE_UNICORN, "unicorn is not installed (pip install unicorn)")
@REQUIRE_UNICORN
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

    # ------------------------------------------- D1 / D2 runtime assertions ----
    # These read the markers the real kernel and the real loader emit, from a
    # run of the real binaries.  The walk and the allocation both happen in
    # this run; nothing here is a model of them.

    def test_the_kernel_walks_the_live_page_tables_at_every_required_address(self):
        _, trace = self.run_once()
        serial = trace.serial
        self.assertIn("PC5-64-PTWALK span probes=14 failed=0", serial)
        # Beginning of the range, both sides of each 1 GiB boundary, inside each
        # of the four regions, and the last byte of the span.
        for note in ("start", "below-1gib", "at-1gib", "above-1gib", "in-1gib",
                     "below-2gib", "at-2gib", "above-2gib", "in-2gib",
                     "below-3gib", "at-3gib", "above-3gib", "in-3gib",
                     "upper-end"):
            self.assertIn("note=%s" % note, serial)
        # Every probe must translate to its own address, not merely be present.
        for line in serial.splitlines():
            if line.startswith("PC5-64-PTWALK va="):
                self.assertIn("identity=1", line, line)
                self.assertNotIn("pa=none", line, line)

    def test_the_walk_reports_the_page_size_it_actually_terminated_at(self):
        # The requirement is that page size is not assumed.  The loader builds
        # 2 MiB leaves, so that is what the walk must say - and it must never
        # claim 4 KiB, which would mean it walked past the leaf into a table
        # that does not exist.
        _, trace = self.run_once()
        sizes = {line.split("size=")[1].split()[0]
                 for line in trace.serial.splitlines()
                 if line.startswith("PC5-64-PTWALK va=")}
        self.assertEqual(sizes, {"2m"}, sizes)

    def test_the_loader_reports_the_allocation_firmware_actually_performed(self):
        _, trace = self.run_once()
        serial = trace.serial
        # The record must name the allocation's purpose: without it a reader (and
        # the driver) can only count allocations, not identify them.
        self.assertIn("PC5-64-LOADER-ALLOC purpose=bootinfo type=max-address", serial)
        for purpose in ("bootinfo", "pagetables", "stack",
                        "memmap-buffer", "memmap-out"):
            self.assertEqual(
                serial.count("PC5-64-LOADER-ALLOC purpose=%s " % purpose), 1,
                "each mandatory allocation appears exactly once: %s" % purpose)
        # The bound must be the last mappable byte of the span, not the span
        # length and not zero.
        self.assertIn("requested-max=0xffffffff", serial)
        self.assertNotIn("requested-max=0x100000000", serial)
        self.assertNotIn("requested-max=0x0 ", serial)
        for line in serial.splitlines():
            if line.startswith("PC5-64-LOADER-ALLOC"):
                self.assertIn("verdict=ok", line, line)
                self.assertIn("within-bound=1", line, line)
                self.assertIn("within-limit=1", line, line)

    def test_every_reported_allocation_end_is_inside_the_span(self):
        # Recomputed from the printed base and page count rather than trusting
        # the printed end, which is the value under test.
        _, trace = self.run_once()
        seen = 0
        for line in trace.serial.splitlines():
            if not line.startswith("PC5-64-LOADER-ALLOC"):
                continue
            fields = dict(f.split("=", 1) for f in line.split() if "=" in f)
            pages = int(fields["pages"])
            base = int(fields["returned-base"], 16)
            end = int(fields["end"], 16)
            self.assertEqual(end, base + pages * 4096 - 1, line)
            self.assertLess(end, 4 << 30, line)
            seen += 1
        self.assertGreater(seen, 0, "no allocation was reported")

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
        # The loader's own end and size are compared against what the stub
        # firmware said the image was, not against a literal.  A hardcoded size
        # would fail the moment the loader grew by a function - which is what
        # happened when the allocation report was added - and would have taught
        # nothing about placement, which is what this test is for.  The
        # property under test is that the loader reports the span firmware
        # gave it and that the span is inside the map.
        end = 0x0FE00000 + machine.image_size
        self.assertIn("PC5-64-LOADER image=0x%x-0x%x below-span=1"
                      % (0x0FE00000, end), trace.serial)
        self.assertIn("PC5-64-LOADER image=0xfe00000 size=0x%x range=ok"
                      % machine.image_size, trace.serial)
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




@REQUIRE_UNICORN
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
    def scratch_slot(self, machine, size=8):
        """A pool slot for writing a UEFI pointer argument, or a clear failure.

        `machine.alloc()` returns None when the pool cannot satisfy a request.
        On a freshly built machine that cannot happen, so a None here means the
        harness itself is wrong - not that the test should continue and write a
        value to address 0.  Asserting keeps the failure specific and keeps an
        allocation failure from being mistaken for a passing check.
        """
        address = machine.alloc(size)
        self.assertIsNotNone(
            address,
            f"the stub pool could not supply {size} bytes for a test argument; "
            f"exhaustions={machine.exhaustions}, "
            f"next free={machine.pool_next:#x}")
        return address

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

    def test_every_maximum_request_carries_the_identity_limit_as_its_bound(self):
        """The bound itself, not just a plausible allocation that came back.

        `test_the_loader_passes_a_maximum_that_can_actually_be_satisfied` judges the
        *returned* base, so it pins "some usable address came back" rather than "the
        right question was asked".  A bound that is too low still yields a base below
        it; a bound above the span still yields a base the loader's own post-checks
        accept or reject.  Neither is the contract.

        The contract is the value written into the Memory cell *before* the call:
        UEFI 2.9A 7.2.1 makes that the inclusive uppermost address the allocation may
        reach, so the loader must ask for exactly PC5_IDENTITY_LIMIT - 1 - the last
        byte that the identity map it builds above actually covers.

        The expected value is taken from the real header rather than repeated here.
        A duplicated constant would let `PC5_IDENTITY_LIMIT` move while this test
        stayed green, which is the same class of blindness this test exists to end.
        """
        expected = _header_constant("PC5_IDENTITY_LIMIT") - 1

        machine = self.machine()
        trace = machine.run()
        self.assertIn(MARKER, trace.serial)
        self.assertGreaterEqual(len(machine.max_request_bounds), 3,
                                "expected boot info, page tables and stack")
        for bound in machine.max_request_bounds:
            self.assertEqual(
                bound, expected,
                f"loader asked for a maximum of {bound:#x}; the identity map it "
                f"builds covers [0, {expected + 1:#x}), whose last address is "
                f"{expected:#x}")

    def test_the_high_allocation_path_also_reports_the_same_bound(self):
        """The bound is recorded on the branch that honours it, not just the default.

        `high_allocations` refuses any request whose range would exceed the caller's
        bound, so it is the one path where a wrong bound changes behaviour.  If the
        harness only observed the default path, a regression there would be silent.
        """
        expected = _header_constant("PC5_IDENTITY_LIMIT") - 1

        machine = self.machine(high_allocations=True)
        trace = machine.run()
        self.assertIn(MARKER, trace.serial)
        self.assertTrue(machine.max_request_bounds,
                        "the high-allocation path recorded no MaxAddress bound")
        for bound in machine.max_request_bounds:
            self.assertEqual(bound, expected)

    def test_a_zero_maximum_verifies_as_unsatisfiable(self):
        """The contract the old code violated, asserted directly on the stub."""
        import ctypes
        machine = self.machine()
        # Ask the stub itself, with a zero bound, and require refusal.
        status = machine.svc_allocate_pages(
            self.module.EFI_ALLOCATE_MAX_ADDRESS, 2, 1,
            self.scratch_slot(machine))
        self.assertEqual(status, self.module.EFI_OUT_OF_RESOURCES,
                         "a zero AllocateMaxAddress bound must not allocate")

    def test_a_maximum_below_the_page_count_cannot_succeed(self):
        machine = self.machine()
        slot = self.scratch_slot(machine)
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
        slot = self.scratch_slot(machine)
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


@REQUIRE_UNICORN
class PoolExhaustionTests(unittest.TestCase):
    """A firmware that runs out of memory must answer, not raise.

    Real firmware returns EFI_OUT_OF_RESOURCES.  The stub used to raise
    StubError("stub pool exhausted") from alloc(), so a loader that mishandled
    that status would still have failed the test - but for the harness's reason
    rather than the loader's, and the loader's own failure path never ran.  These
    tests drive genuine exhaustion through both allocation services and require
    the loader to take its own path.
    """

    @classmethod
    def setUpClass(cls):
        import boot64_stub
        cls.module = boot64_stub
        cls.image, cls.kernel = artifacts()

    def machine(self, **options):
        return self.module.StubFirmware64(
            image=self.image,
            esp_volume={"\\EFI\\BOOT\\BOOTX64.EFI": self.image,
                        "\\KERNEL64.BIN": self.kernel},
            kernel_bytes=self.kernel, **options)
    def scratch_slot(self, machine, size=8):
        """A pool slot for writing a UEFI pointer argument, or a clear failure.

        `machine.alloc()` returns None when the pool cannot satisfy a request.
        On a freshly built machine that cannot happen, so a None here means the
        harness itself is wrong - not that the test should continue and write a
        value to address 0.  Asserting keeps the failure specific and keeps an
        allocation failure from being mistaken for a passing check.
        """
        address = machine.alloc(size)
        self.assertIsNotNone(
            address,
            f"the stub pool could not supply {size} bytes for a test argument; "
            f"exhaustions={machine.exhaustions}, "
            f"next free={machine.pool_next:#x}")
        return address

    def starved(self, reserve=0, **options):
        """A machine whose firmware has `reserve` bytes left for the loader.

        `reserve=0` models conforming firmware with no memory to give.  The
        reserve is applied after firmware builds its own structures, so this
        models the loader's situation rather than breaking the harness.
        """
        return self.machine(pool_reserve=reserve, **options)

    def test_allocate_pages_returns_out_of_resources_when_the_pool_is_empty(self):
        """The service answer is a status, and it is the documented one."""
        machine = self.starved()
        slot = machine.alloc_or_raise(8)      # scratch slot, before starvation
        machine.pool_next = machine.pool_ceiling()   # firmware has given it all
        machine.write_u64(slot, (1 << 32) - 1)
        status = machine.svc_allocate_pages(
            self.module.EFI_ALLOCATE_ANY_PAGES, 2, 1, slot)
        self.assertEqual(
            status, self.module.EFI_OUT_OF_RESOURCES,
            "an exhausted pool must answer EFI_OUT_OF_RESOURCES, not raise")
        self.assertTrue(machine.exhaustions,
                        "the stub must record why it could not satisfy the request")

    def test_allocate_pool_returns_out_of_resources_when_the_pool_is_empty(self):
        machine = self.machine()
        slot = machine.alloc_or_raise(8)
        machine.pool_next = machine.pool_ceiling()
        status = machine.svc_allocate_pool(2, 4096, slot)
        self.assertEqual(status, self.module.EFI_OUT_OF_RESOURCES,
                         "AllocatePool must report exhaustion as a status")

    def test_the_loader_takes_its_own_failure_path_under_real_exhaustion(self):
        """The point of the fix: the loader's die() path actually runs.

        No allocate_return override is used - the firmware is honest and simply
        has no memory, exactly as a real one would.  The loader must name the
        allocation that failed rather than the harness dying first.
        """
        machine = self.starved(reserve=0)
        trace = machine.run()
        self.assertNotIn(MARKER, trace.serial,
                         "the boot must not reach the kernel entry")
        self.assertNotIn("PC5-64BIT-KERNEL-ENTRY", trace.serial)
        failures = [line.strip() for line in trace.serial.splitlines()
                    if "PC5-64-LOADER-FAIL=" in line]
        self.assertTrue(
            failures,
            "the loader must report its own failure, not die inside the harness")
        self.assertIn(
            "PC5-64-LOADER-FAIL=bootinfo-allocate", failures,
            f"expected the first allocation to be the one that fails; saw {failures}")
        self.assertTrue(machine.exhaustions,
                        "the stub must have recorded an exhaustion, not a lie")

    def test_exhaustion_is_reported_as_a_status_not_an_exception(self):
        """Guard against a regression to StubError on the service path."""
        machine = self.machine()
        slot = machine.alloc_or_raise(8)
        machine.pool_next = machine.pool_ceiling()
        machine.write_u64(slot, (1 << 32) - 1)
        for service, args in (
            (machine.svc_allocate_pages,
             (self.module.EFI_ALLOCATE_ANY_PAGES, 2, 4, slot)),
            (machine.svc_allocate_pool, (2, 65536, slot)),
        ):
            with self.subTest(service=service.__name__):
                try:
                    status = service(*args)
                except Exception as exc:            # noqa: BLE001 - the assertion
                    self.fail(f"{service.__name__} raised {exc!r} on exhaustion; "
                              f"firmware returns a status")
                self.assertNotEqual(status, 0,
                                    f"{service.__name__} reported success while "
                                    f"the pool was empty")

@REQUIRE_UNICORN
class MemoryMapGrowthTests(unittest.TestCase):
    """The GetMemoryMap growth loop: what it frees, and which key it uses.

    `EFI_BUFFER_TOO_SMALL` is answered by allocating a larger buffer and asking
    again.  The previous buffer is dead at that moment, and leaving it allocated
    is a leak on a path that can run three times.  Releasing it changes the memory
    map, so the loop must take a *fresh* key for every ExitBootServices attempt
    and must never reuse the key from the call that was rejected.

    Limitation stated rather than papered over: the stub's pool is contiguous, so
    its natural map (a few hundred bytes of descriptors) always fits the 32 KiB
    first-pass buffer and BUFFER_TOO_SMALL is otherwise unreachable here.
    `map_small_buffer_calls` forces it, which exercises the loader's growth loop
    but cannot reproduce a real firmware's particular descriptor count.
    """

    @classmethod
    def setUpClass(cls):
        import boot64_stub
        cls.module = boot64_stub
        cls.image, cls.kernel = artifacts()

    def run_once(self, **options):
        machine = self.module.StubFirmware64(
            image=self.image,
            esp_volume={"\\EFI\\BOOT\\BOOTX64.EFI": self.image,
                        "\\KERNEL64.BIN": self.kernel},
            kernel_bytes=self.kernel, **options)
        return machine, machine.run()

    # ------------------------------------------------ 1. buffer sufficient ----
    def test_a_sufficient_buffer_never_grows_and_never_frees(self):
        machine, trace = self.run_once()
        self.assertEqual(machine.free_pages_calls, [],
                         "the first buffer was sufficient; nothing should be freed")
        self.assertEqual(machine.map_small_buffer_forced, 0)
        self.assertIn("exit-boot-services=ok attempts=1", trace.serial)
        self.assertIn(MARKER, trace.serial)

    # --------------------------------------------- 2. one growth retry --------
    def test_one_buffer_too_small_grows_once_and_frees_the_old_buffer(self):
        machine, trace = self.run_once(map_small_buffer_calls=1)
        self.assertEqual(machine.map_small_buffer_forced, 1,
                         "the forced growth must have happened exactly once")
        self.assertEqual(len(machine.free_pages_calls), 1,
                         "the abandoned buffer must be released exactly once")
        address, pages = machine.free_pages_calls[0]
        self.assertEqual(pages, 8,
                         "the released buffer is the 8-page first-pass buffer")
        self.assertEqual(address % 4096, 0, "freed addresses are page-aligned")
        self.assertIn("exit-boot-services=ok attempts=2", trace.serial)
        self.assertIn(MARKER, trace.serial)

    # ------------------------------------------ 3. several growth retries -----
    def test_three_buffer_too_small_answers_free_each_abandoned_buffer(self):
        machine, trace = self.run_once(map_small_buffer_calls=3)
        self.assertEqual(machine.map_small_buffer_forced, 3)
        self.assertEqual([pages for _address, pages in machine.free_pages_calls],
                         [8, 16, 32],
                         "each growth step must release the buffer it replaced")
        addresses = [address for address, _pages in machine.free_pages_calls]
        self.assertEqual(len(set(addresses)), 3,
                         "the freed buffers must be three distinct allocations")
        for address in addresses:
            self.assertEqual(address % 4096, 0)
        # Three growth steps occupy attempts 1-3, so the exit succeeds on the fourth.
        self.assertIn("exit-boot-services=ok attempts=4", trace.serial)
        self.assertIn(MARKER, trace.serial)

    # ------------------------------------------- 4. allocation failure -------
    def test_a_failed_growth_allocation_stops_the_boot_and_frees_nothing(self):
        """Growth fails before the old buffer is released.

        Order matters: the replacement is allocated first, so a firmware that has
        no memory left leaves the loop with a valid buffer to read into rather
        than freeing the only one it has and then failing.  The boot must stop
        with the loader's own reason, before any handover.
        """
        # Only the 16-page request fails: the first pass asks for 8 pages and
        # succeeds, the growth asks for 16.  This isolates the growth step instead
        # of starving the loader before it starts.
        machine, trace = self.run_once(map_small_buffer_calls=1,
                                       fail_allocate_pages_pages={16})
        self.assertIn("PC5-64-LOADER-FAIL=memmap-grow", trace.serial,
                      trace.serial)
        self.assertNotIn(MARKER, trace.serial)
        self.assertEqual(machine.free_pages_calls, [],
                         "the old buffer must not be freed if the new one failed")

    def test_a_firmware_without_free_pages_still_completes_the_growth(self):
        """An older boot-services table may leave FreePages null.

        The loader must test the pointer rather than calling through it.  The
        growth still succeeds; the abandoned buffer simply stays loader-owned,
        which is the bounded cost the protocol already tolerates.
        """
        machine, trace = self.run_once(map_small_buffer_calls=1,
                                       free_pages_available=False)
        self.assertEqual(machine.free_pages_calls, [])
        self.assertEqual(machine.map_small_buffer_forced, 1)
        self.assertIn("exit-boot-services=ok attempts=2", trace.serial)
        self.assertIn(MARKER, trace.serial)

    # ------------------------------------- 5. a fresh key for every attempt ---
    def test_every_exit_attempt_uses_the_key_from_its_own_get_memory_map(self):
        """No ExitBootServices may be given a key that a free has invalidated.

        The stub advances the map generation on every allocation, every free and
        every ExitBootServices mismatch, and GetMemoryMap reports the current
        generation.  So if the loader reused a key across a growth step, the key
        it passed would be *below* the generation the free created, and the call
        would be rejected as stale.  A single growth plus a single successful exit
        therefore proves the key was re-read: the free happened between the two.
        """
        machine, trace = self.run_once(map_small_buffer_calls=1)
        self.assertEqual(len(machine.free_pages_calls), 1)
        exits = [event for event in machine.trace.events
                 if event["event"] == "exit_boot_services"]
        stale = [event for event in machine.trace.events
                 if event["event"] == "exit_boot_services_stale_key"]
        self.assertEqual(len(exits), 1,
                         "one successful ExitBootServices and no rejected retry")
        self.assertEqual(stale, [],
                         "a key invalidated by the free must never be passed")
        self.assertIn("exit-boot-services=ok attempts=2", trace.serial)

    def test_growth_then_a_stale_key_retries_with_another_fresh_key(self):
        """Growth and a firmware-side key race in the same boot.

        The retry after a stale key must re-read the map, and it must not
        allocate or free anything: boot services may already be gone.
        """
        machine, trace = self.run_once(map_small_buffer_calls=1, race_map_key=1)
        self.assertEqual(len(machine.free_pages_calls), 1,
                         "only the growth step frees; the stale-key retry must not")
        # Attempt 1 was consumed by the rejected growth call, so the stale key
        # is retried on attempt 2 and the exit succeeds on attempt 3.
        self.assertIn("exit-boot-services=stale-key retry=2", trace.serial)
        self.assertIn("exit-boot-services=ok attempts=3", trace.serial)
        self.assertEqual(machine.boot_service_calls_after_exit, [])
        self.assertIn(MARKER, trace.serial)

@REQUIRE_UNICORN
class DescriptorVersionTests(unittest.TestCase):
    """What memmap_desc_version means, and what the loader does with it.

    UEFI 2.x defines exactly one EFI_MEMORY_DESCRIPTOR.Version, 1.  The field in
    the boot info is that firmware-reported value - provenance for the input the
    loader accepted - not a version of the pc5_memdesc64 array it writes instead.
    An unknown value means the descriptor layout the loader is reading is not the
    one it was built against, so the map is refused rather than reinterpreted.
    """

    @classmethod
    def setUpClass(cls):
        import boot64_stub
        cls.module = boot64_stub
        cls.image, cls.kernel = artifacts()

    def run_once(self, **options):
        machine = self.module.StubFirmware64(
            image=self.image,
            esp_volume={"\\EFI\\BOOT\\BOOTX64.EFI": self.image,
                        "\\KERNEL64.BIN": self.kernel},
            kernel_bytes=self.kernel, **options)
        return machine, machine.run()

    def boot_info_desc_version(self, machine):
        """Read memmap_desc_version out of the boot info the loader built."""
        marker = "PC5-64-LOADER-JUMP entry="
        line = next((l for l in machine.trace.serial.splitlines()
                     if marker in l), None)
        self.assertIsNotNone(line, "loader never reached the handover")
        token = next(t for t in line.split() if t.startswith("bootinfo="))
        boot_info = int(token.split("=", 1)[1], 16)
        # Offset 36 in struct pc5_boot64: magic=0, version=4, size=8, flags=12,
        # memmap_addr=16, memmap_bytes=24, memmap_desc_size=32,
        # memmap_desc_version=36, framebuffer_addr=40.  The kernel pins 16 and 40
        # with _Static_assert, so reading between them is safe by construction.
        return machine.read_u32(boot_info + 36)

    def test_supported_descriptor_version_is_accepted_and_recorded(self):
        machine, trace = self.run_once()
        self.assertIn(MARKER, trace.serial)
        self.assertEqual(self.boot_info_desc_version(machine), 1,
                         "the accepted firmware version must be recorded, not invented")

    def test_an_unsupported_descriptor_version_stops_the_boot(self):
        machine, trace = self.run_once(describe_version=2)
        self.assertIn("PC5-64-LOADER-FAIL=memory-descriptor-version", trace.serial)
        self.assertNotIn(MARKER, trace.serial)
        self.assertNotIn("PC5-64-LOADER-JUMP", trace.serial)

    def test_a_zero_descriptor_version_is_also_refused(self):
        # Zero is not "unset" in this protocol: it is simply not version 1.
        machine, trace = self.run_once(describe_version=0)
        self.assertIn("PC5-64-LOADER-FAIL=memory-descriptor-version", trace.serial)
        self.assertNotIn(MARKER, trace.serial)

    def test_the_refusal_happens_before_exit_boot_services(self):
        # A refused map must not leave firmware's services already gone: the
        # decision is made while there is still something to report to.
        machine, trace = self.run_once(describe_version=2)
        self.assertFalse(trace.exited,
                         "the loader must not call ExitBootServices on a map it refused")
        self.assertEqual(machine.boot_service_calls_after_exit, [])


if __name__ == "__main__":
    unittest.main()
