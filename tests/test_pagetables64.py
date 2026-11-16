"""Page-table construction and bounds, verified on the host against real bytes.

`boot/uefi64/pagetables.c` runs during the handover, before any kernel exists to
prove anything, and it is handed firmware-supplied physical addresses: a
framebuffer can sit anywhere in the 64-bit physical space, including above the
map, at the boundary, or in a range whose end overflows.  So every entry of the
API is checked here for two properties at once:

  * the *semantics* — which entries end up mapped or uncached, and what the
    return value says about it;
  * the *bounds* — the page-table block sits between two guard pages filled with
    a canary, and every call must leave both guards byte-for-byte intact.

The library is compiled from the same source the loader uses, with the same
flags (`-Wall -Wextra -Werror`), so a host test cannot drift from the loader.
"""
from __future__ import annotations

import ctypes
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "boot" / "uefi64" / "pagetables.c"

PAGE = 4096
MASK64 = (1 << 64) - 1
PT_PAGES = 6
SPAN = 4 << 30
TWO_MIB = 2 << 20
PD_BASE_OFFSET = 2 * PAGE
CANARY = 0xAA

_LIBRARY = {}


def library():
    """Build pagetables.c for the host once per test run."""
    if "lib" in _LIBRARY:
        return _LIBRARY["lib"]
    directory = tempfile.mkdtemp(prefix="pc5-pagetables-")
    output = pathlib.Path(directory) / "libpagetables.so"
    result = subprocess.run(
        ["gcc", "-std=gnu11", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra",
         "-Werror", "-I", str(SOURCE.parent), str(SOURCE), "-o", str(output)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"pagetables.c did not build for the host:\n{result.stderr}")
    lib = ctypes.CDLL(str(output))
    lib.pc5_pt_build_identity.restype = ctypes.c_ulong
    lib.pc5_pt_build_identity.argtypes = [ctypes.c_ulonglong, ctypes.c_ulonglong]
    lib.pc5_pt_range_supported.restype = ctypes.c_int
    lib.pc5_pt_range_supported.argtypes = [ctypes.c_ulonglong, ctypes.c_ulonglong]
    lib.pc5_pt_mark_uncached.restype = ctypes.c_ulong
    lib.pc5_pt_mark_uncached.argtypes = [ctypes.c_ulonglong] * 3
    lib.pc5_pt_lookup.restype = ctypes.c_int
    lib.pc5_pt_lookup.argtypes = [ctypes.c_ulonglong, ctypes.c_ulonglong,
                                  ctypes.POINTER(ctypes.c_ulonglong)]
    _LIBRARY["lib"] = lib
    return lib


class Block:
    """A page-aligned page-table block with a canary page before and after it."""

    def __init__(self):
        # One extra page lets the six-page block be aligned inside the buffer
        # while still leaving a whole guard page on each side.
        self.buffer = ctypes.create_string_buffer((PT_PAGES + 2) * PAGE + PAGE)
        ctypes.memset(self.buffer, CANARY, len(self.buffer))
        raw = ctypes.addressof(self.buffer)
        self.base = (raw + PAGE - 1) & ~(PAGE - 1)
        while self.base - raw < PAGE:
            self.base += PAGE
        self.guard_before = self.base - PAGE
        self.guard_after = self.base + PT_PAGES * PAGE

    def entry(self, index):
        return ctypes.c_ulonglong.from_address(
            self.base + PD_BASE_OFFSET + index * 8).value

    def guards_intact(self):
        before = ctypes.string_at(self.guard_before, PAGE)
        after = ctypes.string_at(self.guard_after, PAGE)
        return (set(before) == {CANARY}) and (set(after) == {CANARY})

    def bytes_after_block(self):
        return ctypes.string_at(self.guard_after, PAGE)


class IdentityMapTests(unittest.TestCase):
    def setUp(self):
        self.lib = library()
        self.block = Block()

    def test_identity_map_covers_four_gib_with_2mib_pages(self):
        mapped = self.lib.pc5_pt_build_identity(self.block.base, SPAN)
        self.assertEqual(mapped, 2048)
        pml4 = ctypes.c_ulonglong.from_address(self.block.base).value
        pdpt = ctypes.c_ulonglong.from_address(self.block.base + PAGE).value
        self.assertEqual(pml4 & 0xFFF, 0x3)
        self.assertEqual(pdpt & 0xFFF, 0x3)
        self.assertEqual(self.block.entry(0), 0x83)
        self.assertEqual(self.block.entry(511), (511 << 21) | 0x83)
        self.assertEqual(self.block.entry(2047), (2047 << 21) | 0x83)
        self.assertTrue(self.block.guards_intact())

    def test_every_mapped_page_is_present_writable_and_2mib(self):
        self.lib.pc5_pt_build_identity(self.block.base, SPAN)
        for index in (0, 1, 300, 1024, 2047):
            entry = self.block.entry(index)
            self.assertTrue(entry & 1, f"page {index} not present")
            self.assertTrue(entry & 2, f"page {index} not writable")
            self.assertTrue(entry & (1 << 7), f"page {index} is not a 2 MiB leaf")
            self.assertEqual(entry & 0x1FF000, 0,
                             f"page {index} has reserved bits 12-20 set")

    def test_span_is_rounded_down_to_whole_2mib_pages(self):
        self.assertEqual(self.lib.pc5_pt_build_identity(self.block.base,
                                                       TWO_MIB + 4096), 1)
        self.assertEqual(self.lib.pc5_pt_build_identity(self.block.base,
                                                       3 * TWO_MIB + 4096), 3)
        self.assertEqual(self.lib.pc5_pt_build_identity(self.block.base,
                                                       TWO_MIB - 1), 0,
                         "less than one 2 MiB page maps nothing")

    def test_invalid_bases_are_refused_without_touching_memory(self):
        for base in (0, self.block.base + 1, self.block.base + PAGE // 2,
                     MASK64 - PAGE):
            self.assertEqual(self.lib.pc5_pt_build_identity(base, SPAN), 0, hex(base))
        self.assertTrue(self.block.guards_intact())

    def test_oversized_or_empty_spans_are_refused(self):
        for span in (0, SPAN + 1, SPAN * 2, MASK64):
            self.assertEqual(self.lib.pc5_pt_build_identity(self.block.base, span), 0)
        self.assertTrue(self.block.guards_intact())

    def test_lookup_finds_the_leaf_and_rejects_unmapped_space(self):
        self.lib.pc5_pt_build_identity(self.block.base, SPAN)
        entry = ctypes.c_ulonglong(0)
        self.assertEqual(self.lib.pc5_pt_lookup(self.block.base, 0x100000,
                                                ctypes.byref(entry)), 1)
        self.assertEqual(entry.value, 0x83)
        self.assertEqual(self.lib.pc5_pt_lookup(self.block.base, SPAN,
                                                ctypes.byref(entry)), 0)
        self.assertEqual(self.lib.pc5_pt_lookup(self.block.base, MASK64,
                                                ctypes.byref(entry)), 0)
        self.assertEqual(self.lib.pc5_pt_lookup(0, 0x100000, ctypes.byref(entry)), 0)
        self.assertEqual(self.lib.pc5_pt_lookup(self.block.base + 4, 0x100000,
                                                ctypes.byref(entry)), 0)


class RangeSupportedTests(unittest.TestCase):
    def setUp(self):
        self.lib = library()

    def test_ranges_inside_the_span_are_supported(self):
        self.assertTrue(self.lib.pc5_pt_range_supported(0, 1))
        self.assertTrue(self.lib.pc5_pt_range_supported(0, SPAN))
        self.assertTrue(self.lib.pc5_pt_range_supported(SPAN - 1, 1))
        self.assertTrue(self.lib.pc5_pt_range_supported(0x3F000000, 0x100000))

    def test_ranges_outside_the_span_are_not_supported(self):
        self.assertFalse(self.lib.pc5_pt_range_supported(SPAN, 1),
                         "the first byte above the map is not mapped")
        self.assertFalse(self.lib.pc5_pt_range_supported(SPAN - 1, 2))
        self.assertFalse(self.lib.pc5_pt_range_supported(1 << 32, 1))

    def test_empty_and_overflowing_ranges_are_not_supported(self):
        self.assertFalse(self.lib.pc5_pt_range_supported(0x1000, 0))
        self.assertFalse(self.lib.pc5_pt_range_supported(MASK64 - 8, 16),
                         "a range whose end wraps must be refused, not clipped")
        self.assertFalse(self.lib.pc5_pt_range_supported(MASK64, MASK64))


class UncachedBoundaryTests(unittest.TestCase):
    """Every boundary the framebuffer can land on, including above the map."""

    def setUp(self):
        self.lib = library()
        self.block = Block()
        self.lib.pc5_pt_build_identity(self.block.base, SPAN)

    def mark(self, start, end):
        changed = self.lib.pc5_pt_mark_uncached(self.block.base, start, end)
        self.assertTrue(self.block.guards_intact(),
                        f"mark_uncached({start:#x}, {end:#x}) wrote outside the block")
        return changed

    def test_framebuffer_entirely_below_the_span(self):
        self.assertEqual(self.mark(0x04000000, 0x04000000 + 0x200000), 1)
        self.assertEqual(self.block.entry(32) & 0x18, 0x18)
        self.assertEqual(self.block.entry(31) & 0x18, 0)
        self.assertEqual(self.block.entry(33) & 0x18, 0)

    def test_framebuffer_ending_exactly_at_the_span(self):
        self.assertEqual(self.mark(SPAN - TWO_MIB, SPAN), 1)
        self.assertEqual(self.block.entry(2047) & 0x18, 0x18)
        self.assertEqual(self.block.entry(2046) & 0x18, 0)

    def test_framebuffer_crossing_the_span_marks_only_the_mapped_part(self):
        changed = self.mark(SPAN - TWO_MIB, SPAN + TWO_MIB)
        self.assertEqual(changed, 1, "only the page inside the map can change")
        self.assertEqual(self.block.entry(2047) & 0x18, 0x18)
        self.assertTrue(self.block.guards_intact())

    def test_framebuffer_entirely_above_the_span_changes_nothing(self):
        for start, size in ((SPAN, TWO_MIB), (1 << 32, 0x1000000),
                            (0xFFFF_FFFF_0000_0000, 0x1000)):
            self.assertEqual(self.mark(start, start + size), 0, hex(start))
        self.assertEqual(self.block.entry(2047) & 0x18, 0,
                         "no entry may be marked for a range above the map")

    def test_unaligned_start_and_end_mark_every_overlapping_page(self):
        self.assertEqual(self.mark(TWO_MIB - 8, TWO_MIB + 8), 2)
        self.assertEqual(self.block.entry(0) & 0x18, 0x18)
        self.assertEqual(self.block.entry(1) & 0x18, 0x18)
        self.assertEqual(self.block.entry(2) & 0x18, 0)

    def test_empty_inverted_and_overflowing_ranges_change_nothing(self):
        for start, end in ((0x1000, 0x1000), (0x2000, 0x1000), (MASK64, 0),
                           (MASK64 - 8, 8)):
            self.assertEqual(self.mark(start, end), 0, f"{start:#x}-{end:#x}")

    def test_invalid_bases_are_refused_without_touching_the_canary(self):
        for base in (0, self.block.base + 8, self.block.base + PAGE // 2):
            self.assertEqual(
                self.lib.pc5_pt_mark_uncached(base, 0, TWO_MIB), 0, hex(base))
        self.assertTrue(self.block.guards_intact())

    def test_the_return_value_counts_what_changed_not_what_was_asked(self):
        self.assertEqual(self.mark(0, TWO_MIB), 1)
        self.assertEqual(self.mark(0, TWO_MIB), 0,
                         "an entry that is already uncached is not a change")
        self.assertEqual(self.mark(0, 3 * TWO_MIB), 2)

    def test_marking_an_unmapped_span_reports_zero(self):
        empty = Block()
        # A block with no identity map built: every slot is zero, so nothing is
        # present and nothing may be reported as changed.
        self.assertEqual(self.lib.pc5_pt_mark_uncached(empty.base, 0, TWO_MIB), 0)
        self.assertTrue(empty.guards_intact())

    def test_the_guard_page_after_the_block_is_untouched(self):
        # The last PD slot that belongs to the block is index 2047; anything
        # beyond it is the canary page.  Prove the canary is still exactly the
        # pattern, not merely "not obviously corrupt".
        self.assertEqual(set(self.block.bytes_after_block()), {CANARY})


if __name__ == "__main__":
    unittest.main()
