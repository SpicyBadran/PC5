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
PC5_UC_BITS = 0x18          # PWT | PCD on a 2 MiB leaf

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


class IdentityHierarchyTests(unittest.TestCase):
    """The installed 4-level hierarchy, walked from PML4 - not the PD array alone.

    The defect these tests exist for is precisely the one a page-directory test
    cannot see: four PD pages were populated, but only PDPT[0] pointed at one of
    them, so three quarters of the "4 GiB map" was unreachable while
    pc5_pt_build_identity() still returned 2048.  Every assertion here therefore
    goes through pc5_pt_lookup(), which performs the real PML4 -> PDPT -> PD walk,
    or reads the PDPT entries themselves.
    """

    def setUp(self):
        self.lib = library()
        self.block = Block()
        # Poison, so a structure the builder forgets to initialise is caught rather
        # than happening to read as zero.
        ctypes.memset(self.block.base, CANARY, PT_PAGES * PAGE)
        self.mapped = self.lib.pc5_pt_build_identity(self.block.base, SPAN)

    # ---------------------------------------------------------- helpers ----
    def pml4(self, index=0):
        return ctypes.c_ulonglong.from_address(self.block.base + index * 8).value

    def pdpt(self, index):
        return ctypes.c_ulonglong.from_address(
            self.block.base + PAGE + index * 8).value

    def lookup(self, address):
        entry = ctypes.c_ulonglong(0)
        found = self.lib.pc5_pt_lookup(self.block.base, address,
                                       ctypes.byref(entry))
        return found, entry.value

    def assert_mapped(self, address, message=""):
        found, entry = self.lookup(address)
        self.assertEqual(found, 1, f"{address:#x} is not reachable through the "
                                  f"hierarchy {message}".strip())
        self.assertTrue(entry & 1, f"{address:#x} leaf not present")
        self.assertTrue(entry & 2, f"{address:#x} leaf not writable")
        self.assertTrue(entry & (1 << 7), f"{address:#x} leaf is not a 2 MiB page")
        return entry

    def assert_unmapped(self, address, message=""):
        found, _ = self.lookup(address)
        self.assertEqual(found, 0, f"{address:#x} must not be mapped {message}".strip())

    # ----------------------------------------------- hierarchy integrity ----
    def test_returned_count_is_what_the_hierarchy_actually_reaches(self):
        self.assertEqual(self.mapped, 2048)
        # Count the reachable 2 MiB pages by walking, independently of the return
        # value: one present leaf per 2 MiB with the correct physical address.
        reachable = 0
        for address in range(0, SPAN, TWO_MIB):
            found, entry = self.lookup(address)
            if found and entry & 1 and entry & (1 << 7):
                self.assertEqual(entry & ~0xFFF, address,
                                 f"leaf at {address:#x} points elsewhere")
                reachable += 1
        self.assertEqual(reachable, self.mapped,
                         f"returned {self.mapped} but the hierarchy reaches {reachable}")

    def test_every_pdpt_entry_points_at_its_own_page_directory(self):
        expected_pd = [self.block.base + PD_BASE_OFFSET + g * PAGE for g in range(4)]
        for g in range(4):
            entry = self.pdpt(g)
            self.assertTrue(entry & 1, f"PDPT[{g}] not present")
            self.assertTrue(entry & 2, f"PDPT[{g}] not writable")
            self.assertFalse(entry & (1 << 7),
                             f"PDPT[{g}] must be a table pointer, not a 1 GiB leaf")
            self.assertEqual(entry & 0x000FFFFFFFFFF000, expected_pd[g],
                             f"PDPT[{g}] points at {entry & 0x000FFFFFFFFFF000:#x}, "
                             f"expected the PD page at {expected_pd[g]:#x}")

    def test_pml4_points_at_the_pdpt_and_nowhere_else(self):
        entry = self.pml4()
        self.assertTrue(entry & 1)
        self.assertFalse(entry & (1 << 7))
        self.assertEqual(entry & 0x000FFFFFFFFFF000, self.block.base + PAGE)
        for index in range(1, 512):
            self.assertEqual(self.pml4(index), 0,
                             f"PML4[{index}] must be empty: only the low 4 GiB is mapped")

    def test_every_active_structure_is_zero_beyond_the_span(self):
        # No stale firmware value may survive in any page the walk can reach.
        self.assertEqual(self.pdpt(4), 0, "PDPT[4] must be zero")
        for index in range(5, 512):
            self.assertEqual(self.pdpt(index), 0, f"PDPT[{index}] must be zero")
        # The PD array is four pages of 512 entries.  At the full span all 2048
        # carry the map, so nothing is left over *here* - an `unused` bound has to
        # be derived from what this build actually mapped, not from a constant, or
        # the loop is empty and asserts nothing.  The short-span case, where the
        # tail is non-empty and must still be zero, is covered by
        # ShortSpanInitialisationTests.
        unused = (PT_PAGES - 2) * 512 - self.mapped
        for index in range(self.mapped, (PT_PAGES - 2) * 512):
            entry = self.block.entry(index)
            if entry:
                self.fail(f"PD entry {index} is {entry:#x}, expected 0")
        self.assertEqual(unused, 0,
                         "the full span should leave no unused PD entry")
        self.assertTrue(self.block.guards_intact())

    # --------------------------------------- translation at every 1 GiB ----
    def test_all_four_gibibyte_regions_are_reachable(self):
        # Immediately below, at, and immediately above each 1 GiB boundary, plus the
        # first and last byte of the whole span.
        probes = [
            0x00000000, 0x00100000,
            0x3FFFFFff,                     # last byte below 1 GiB
            0x40000000,                     # exactly 1 GiB
            0x40000001, 0x7FFFFFFF,         # first/last byte of region 1
            0x80000000, 0x80000001, 0xBFFFFFFF,
            0xC0000000, 0xC0000001, 0xFFFFFFFF,
        ]
        for address in probes:
            with self.subTest(address=hex(address)):
                self.assert_mapped(address)

    def test_the_last_two_mib_page_below_the_span_is_mapped(self):
        entry = self.assert_mapped(SPAN - TWO_MIB)
        self.assertEqual(entry & ~0xFFF, SPAN - TWO_MIB)
        # and the entry that covers the final byte
        self.assert_mapped(SPAN - 1)

    def test_the_span_boundary_is_exclusive(self):
        self.assert_unmapped(SPAN, "the first byte above the map")
        self.assert_unmapped(SPAN + 0x1000)
        self.assert_unmapped(1 << 32)
        self.assert_unmapped(MASK64)

    def test_lookup_disagrees_with_a_leaked_pd_entry(self):
        """pc5_pt_lookup must report reachability, not the existence of a PD entry.

        This is the exact false-positive the defect produced: PD entry 1024 was
        populated, but no PDPT entry referenced its page, so the address was not
        mapped.  Here the PDPT link is removed by hand after building and the
        lookup must stop reporting a mapping even though the leaf is still there.
        """
        self.assert_mapped(0x80000000)
        self.assertTrue(self.block.entry(1024) & 1,
                        "precondition: the leaf for 2 GiB exists in the PD array")
        # Break the hierarchy only.
        pdpt2 = ctypes.c_ulonglong.from_address(self.block.base + PAGE + 2 * 8)
        pdpt2.value = 0
        self.assertTrue(self.block.entry(1024) & 1,
                        "the leaf must still be populated - only the link is gone")
        self.assert_unmapped(0x80000000,
                             "an unreferenced PD page must not translate")
        self.assert_unmapped(0x90000000)

    # --------------------------------------------- uncached in every GiB ----
    def test_mark_uncached_reaches_entries_in_every_pdpt_region(self):
        for region in range(4):
            start = region * (1 << 30) + 0x40 * TWO_MIB
            with self.subTest(region=region):
                block_before = self.block.entry(start // TWO_MIB) & PC5_UC_BITS
                self.assertEqual(block_before, 0, "precondition: entry starts cached")
                changed = self.lib.pc5_pt_mark_uncached(
                    self.block.base, start, start + 2 * TWO_MIB)
                self.assertEqual(changed, 2, f"region {region}: expected 2 entries")
                found, entry = self.lookup(start)
                self.assertEqual(found, 1)
                self.assertEqual(entry & PC5_UC_BITS, PC5_UC_BITS,
                                 f"region {region}: reachable leaf not marked uncached")
                neighbours = self.lookup(start - TWO_MIB)[1]
                self.assertEqual(neighbours & PC5_UC_BITS, 0,
                                 f"region {region}: marking leaked to a neighbour")

    def test_marking_a_range_above_the_span_changes_nothing(self):
        before = [self.block.entry(i) for i in (0, 512, 1024, 1536, 2047)]
        self.assertEqual(self.lib.pc5_pt_mark_uncached(
            self.block.base, SPAN, SPAN + 0x400000), 0)
        after = [self.block.entry(i) for i in (0, 512, 1024, 1536, 2047)]
        self.assertEqual(before, after)
        self.assertTrue(self.block.guards_intact())

    # -------------------------------------------------- invalid inputs ----
    def test_short_spans_install_only_the_pdpt_entries_they_need(self):
        self.block2 = Block()
        ctypes.memset(self.block2.base, CANARY, PT_PAGES * PAGE)
        # 1 GiB needs exactly one PDPT entry; 2 GiB needs two.
        self.assertEqual(self.lib.pc5_pt_build_identity(self.block2.base, 1 << 30), 512)
        first = ctypes.c_ulonglong.from_address(self.block2.base + PAGE).value
        second = ctypes.c_ulonglong.from_address(self.block2.base + PAGE + 8).value
        self.assertTrue(first & 1, "PDPT[0] must point at the first PD")
        self.assertEqual(second, 0, "a 1 GiB map must not install PDPT[1]")
        probe = ctypes.c_ulonglong(0)
        self.assertEqual(
            self.lib.pc5_pt_lookup(self.block2.base, 1 << 30, ctypes.byref(probe)), 0,
            "with a 1 GiB span the first byte of region 1 must not translate")

    def test_invalid_inputs_leave_the_block_untouched(self):
        for pt, span in ((0, SPAN), (self.block.base + 4, SPAN),
                         (self.block.base, 0), (self.block.base, SPAN + 1),
                         (self.block.base, SPAN * 2)):
            fresh = Block()
            ctypes.memset(fresh.base, CANARY, PT_PAGES * PAGE)
            self.assertEqual(self.lib.pc5_pt_build_identity(pt, span), 0,
                             f"pt={pt:#x} span={span:#x} must be refused")
            self.assertTrue(fresh.guards_intact())


class ShortSpanInitialisationTests(unittest.TestCase):
    """Every page of the block must be initialised deterministically for *any* span.

    The builder documents that "every page of the block is zeroed by this
    function", and it accepts spans shorter than the full 4 GiB.  The production
    loader only ever passes PC5_PT_SPAN, so a short-span defect is invisible to
    the rest of this file: `test_every_active_structure_is_zero_beyond_the_span`
    iterates `range(2048, 4 * 512)`, which is empty because 2048 PD entries are
    exactly what the full span consumes.  A builder that cleared PML4 and the
    PDPT correctly but left the unused PD tail stale therefore passed all of it.

    These tests exercise the documented API rather than the current caller's
    preferred configuration.  Two details keep them honest:

      * assertions are at *word* granularity.  A present leaf encodes its physical
        address, so with a block at 0xAA000000 the payload contains 0xAA bytes; a
        byte-scan for the canary would report a false positive on a correct entry.
      * "deterministic" is tested as span-invariance.  Whatever the builder writes
        into a slot must depend only on the span, never on what was in the memory
        beforehand - so a block pre-filled with one pattern and a block pre-filled
        with another must come out identical.
    """

    SPANS = (
        1 << 30,                      # exactly one 1 GiB region
        3 << 30,                      # three regions: 512 unused PD entries
        (2 << 20) + 0x1000,           # rounds down to a single 2 MiB page
        (4 << 30) - 0x1000,           # rounds down to 2047 entries, not 2048
    )
    # What each span above must exercise, so the collection cannot be thinned out
    # without a failure.  Keyed by span; the value is the number of unused PD
    # entries the span leaves behind, which is the quantity these tests exist for.
    EXPECTED_UNUSED = {
        1 << 30: 1536,
        3 << 30: 512,
        (2 << 20) + 0x1000: 2047,     # rounds down to ONE 2 MiB entry
        (4 << 30) - 0x1000: 1,
    }

    def setUp(self):
        self.lib = library()

    # ------------------------------------------------------------ helpers ---
    @staticmethod
    def poisoned_block(pattern):
        """A block whose six pages are pre-filled with `pattern`."""
        block = Block()
        ctypes.memset(block.base, pattern, PT_PAGES * PAGE)
        return block

    def entries(self, block):
        """Every one of the 2048 PD entries, read as words."""
        return [block.entry(index) for index in range(PT_PAGES - 2)]

    def pdpt_used_for(self, span):
        total = span // TWO_MIB
        return (total + 511) // 512

    def total_for(self, span):
        return span // TWO_MIB

    # ------------------------------------------- unused PD tail is zero ----
    def test_unused_pd_entries_are_zero_for_every_short_span(self):
        """The defect: a stale PD tail below the full span must be cleared.

        This is the assertion the old `range(2048, 4 * 512)` loop meant to make
        and could not: with a short span the tail is non-empty, so the loop
        actually runs.
        """
        for span in self.SPANS:
            total = self.total_for(span)
            with self.subTest(span=hex(span), unused=2048 - total):
                block = self.poisoned_block(CANARY)
                self.assertEqual(
                    self.lib.pc5_pt_build_identity(block.base, span), total,
                    f"span {span:#x}: builder returned the wrong page count")
                for index in range(total, 2048):
                    self.assertEqual(
                        block.entry(index), 0,
                        f"span {span:#x}: PD entry {index} is "
                        f"{block.entry(index):#x}, expected 0 - a stale firmware "
                        f"value survived in an unused page-directory entry")

    def test_a_poisoned_tail_is_cleared_not_merely_ignored(self):
        """A tail pre-filled with present-looking entries must be cleared too.

        Poisoning with 0xAA makes every tail byte non-zero but leaves the present
        bit clear, so a builder that "skips" the tail still looks right to a
        check that only tests the present bit.  Filling the tail with
        present|writable|2 MiB leaves is the harder case: an installation that
        leaked those entries would claim mappings above the configured span.
        """
        leaked = (1 << 7) | 0x3
        for span in self.SPANS:
            total = self.total_for(span)
            with self.subTest(span=hex(span)):
                block = Block()
                ctypes.memset(block.base, 0, PT_PAGES * PAGE)
                for index in range(total, 2048):
                    ctypes.c_ulonglong.from_address(
                        block.base + PD_BASE_OFFSET + index * 8).value = leaked
                self.lib.pc5_pt_build_identity(block.base, span)
                for index in range(total, 2048):
                    self.assertEqual(
                        block.entry(index), 0,
                        f"span {span:#x}: PD entry {index} still claims a mapping "
                        f"above the configured span")
                # ... and the addresses those entries would have described must
                # not translate, which is the property that matters.
                probe = ctypes.c_ulonglong(0)
                self.assertEqual(
                    self.lib.pc5_pt_lookup(block.base, span, ctypes.byref(probe)), 0,
                    f"span {span:#x}: the first byte above the span translates")

    # --------------------------------------- unused PDPT entries are zero --
    def test_unused_pdpt_entries_are_zero_for_every_short_span(self):
        for span in self.SPANS:
            used = self.pdpt_used_for(span)
            with self.subTest(span=hex(span), pdpt_used=used):
                block = self.poisoned_block(CANARY)
                self.lib.pc5_pt_build_identity(block.base, span)
                for index in range(used, 512):
                    self.assertEqual(
                        ctypes.c_ulonglong.from_address(
                            block.base + PAGE + index * 8).value, 0,
                        f"span {span:#x}: PDPT[{index}] is not zero; only "
                        f"{used} entries belong to this span")

    def test_every_installed_pdpt_entry_points_inside_the_block(self):
        """No PDPT entry may reference a page outside the six-page block."""
        low = None
        high = None
        for span in self.SPANS:
            used = self.pdpt_used_for(span)
            with self.subTest(span=hex(span)):
                block = self.poisoned_block(CANARY)
                self.lib.pc5_pt_build_identity(block.base, span)
                for index in range(used):
                    entry = ctypes.c_ulonglong.from_address(
                        block.base + PAGE + index * 8).value
                    self.assertTrue(entry & 1, f"PDPT[{index}] not present")
                    self.assertFalse(entry & (1 << 7),
                                     f"PDPT[{index}] must be a table pointer")
                    target = entry & 0x000FFFFFFFFFF000
                    self.assertGreaterEqual(
                        target, block.base + PD_BASE_OFFSET,
                        f"PDPT[{index}] points below the PD array")
                    self.assertLess(
                        target, block.base + PT_PAGES * PAGE,
                        f"PDPT[{index}] points past the end of the block")

    # ------------------------------------------ reachable leaves are right --
    def test_every_reachable_leaf_is_initialised_for_every_short_span(self):
        for span in self.SPANS:
            total = self.total_for(span)
            with self.subTest(span=hex(span)):
                block = self.poisoned_block(CANARY)
                self.assertEqual(
                    self.lib.pc5_pt_build_identity(block.base, span), total)
                for index in range(total):
                    entry = block.entry(index)
                    self.assertEqual(
                        entry, (index * TWO_MIB) | 0x83,
                        f"span {span:#x}: reachable leaf {index} is {entry:#x}, "
                        f"expected {(index * TWO_MIB) | 0x83:#x}")

    def test_lookup_rejects_addresses_outside_the_configured_span(self):
        for span in self.SPANS:
            # The builder rounds the span down to whole 2 MiB pages, so the
            # effective boundary is the rounded span, not the requested one.
            effective = (span // TWO_MIB) * TWO_MIB
            with self.subTest(span=hex(span), effective=hex(effective)):
                block = self.poisoned_block(CANARY)
                self.lib.pc5_pt_build_identity(block.base, span)
                probe = ctypes.c_ulonglong(0)
                self.assertEqual(
                    self.lib.pc5_pt_lookup(block.base, effective - 1,
                                           ctypes.byref(probe)),
                    1, f"span {span:#x}: the last mapped byte must translate")
                self.assertEqual(
                    self.lib.pc5_pt_lookup(block.base, effective,
                                           ctypes.byref(probe)),
                    0, f"span {span:#x}: the first byte past the span translates")
                self.assertEqual(
                    self.lib.pc5_pt_lookup(block.base, effective + TWO_MIB,
                                           ctypes.byref(probe)),
                    0, f"span {span:#x}: a page past the span translates")

    # -------------------------------- all six pages initialised, no escape --
    def test_all_six_pages_are_initialised_deterministically(self):
        """Same span, different prior contents -> identical block.

        This is the property that makes "every page is zeroed" testable without
        guessing which bytes a correct entry happens to contain.
        """
        for span in self.SPANS + (SPAN,):
            with self.subTest(span=hex(span)):
                # One block, rewritten.  Separate blocks sit at different
                # addresses, so their pointer entries legitimately differ; the
                # property under test is that the *same* block comes out the same
                # whatever was in it beforehand.
                block = Block()
                results = {}
                for name, pattern in (("0xAA-filled", CANARY),
                                      ("0x5A-filled", 0x5A),
                                      ("zero-filled", 0x00)):
                    ctypes.memset(block.base, pattern, PT_PAGES * PAGE)
                    self.lib.pc5_pt_build_identity(block.base, span)
                    results[name] = ctypes.string_at(block.base, PT_PAGES * PAGE)
                reference = results["0xAA-filled"]
                for name in ("0x5A-filled", "zero-filled"):
                    self.assertEqual(
                        results[name], reference,
                        f"span {span:#x}: the {name} block produced different "
                        f"bytes; a page is not being fully initialised")

    def test_no_write_escapes_the_six_page_block(self):
        for span in self.SPANS + (SPAN,):
            with self.subTest(span=hex(span)):
                block = self.poisoned_block(CANARY)
                self.lib.pc5_pt_build_identity(block.base, span)
                self.assertTrue(block.guards_intact(),
                                f"span {span:#x}: a guard page was written")

    def test_invalid_spans_leave_the_block_completely_untouched(self):
        """A refused span must not partially initialise the block."""
        for span in (0, SPAN + 1, SPAN * 2, TWO_MIB - 1):
            with self.subTest(span=hex(span)):
                block = self.poisoned_block(CANARY)
                before = ctypes.string_at(block.base, PT_PAGES * PAGE)
                self.assertEqual(self.lib.pc5_pt_build_identity(block.base, span), 0)
                after = ctypes.string_at(block.base, PT_PAGES * PAGE)
                self.assertEqual(after, before,
                                 f"span {span:#x} was refused but still wrote to "
                                 f"the block")
                self.assertTrue(block.guards_intact())

    # ------------------------------------- the full production map is intact --
    def test_the_full_production_span_still_maps_everything(self):
        block = self.poisoned_block(CANARY)
        self.assertEqual(self.lib.pc5_pt_build_identity(block.base, SPAN), 2048)
        for index in range(2048):
            self.assertEqual(block.entry(index), (index * TWO_MIB) | 0x83)
        for region in range(4):
            probe = ctypes.c_ulonglong(0)
            self.assertEqual(
                self.lib.pc5_pt_lookup(block.base, region * (1 << 30),
                                       ctypes.byref(probe)), 1,
                f"region {region} is not reachable through the hierarchy")
        self.assertTrue(block.guards_intact())


class UnusedPdTailMutationTests(unittest.TestCase):
    """The short-span tests must actually be able to fail.

    `test_unused_pd_entries_are_zero_for_every_short_span` is the replacement for
    a vacuously-empty loop, so it is not enough for it to pass against the real
    builder - it has to fail against a builder that leaves only the unused PD
    tail stale.  This test compiles that mutant and runs the assertions above
    against it in a subprocess, so a future refactor cannot quietly turn the
    discrimination test back into a no-op.

    The mutant keeps PML4 and the PDPT structurally correct, so it is caught by
    the tail assertion alone and cannot be mistaken for the D1 defect.
    """

    ORIGINAL = (
        "    /* Clear all six pages: PML4, the whole PDPT, and every PD entry. */\n"
        "    for (unsigned i = 0; i < PC5_PT_PAGES * PC5_PT_ENTRIES_PER_PAGE; i++)\n"
        "        store(pt + (u64)i * 8ull, 0);\n"
    )
    MUTANT = (
        "    /* MUTANT: PML4 and the whole PDPT are cleared, but only the PD\n"
        "     * entries this span uses.  The unused tail keeps whatever was in\n"
        "     * memory, which is the stale-tail defect. */\n"
        "    for (unsigned i = 0; i < PC5_PT_ENTRIES_PER_PAGE; i++) {\n"
        "        store(pc5_pt_pml4(pt) + (u64)i * 8ull, 0);\n"
        "        store(pc5_pt_pdpt(pt) + (u64)i * 8ull, 0);\n"
        "    }\n"
        "    for (unsigned long i = 0; i < total; i++)\n"
        "        store(pc5_pt_pd_base(pt) + i * 8ull, 0);\n"
    )

    def test_the_mutant_anchor_is_present_and_unique(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertEqual(
            source.count(self.ORIGINAL), 1,
            "the builder's whole-block zeroing line has changed shape; update "
            "UnusedPdTailMutationTests.MUTANT so the blind spot stays guarded")

    def test_stale_pd_tail_is_detected(self):
        import tempfile

        directory = pathlib.Path(tempfile.mkdtemp(prefix="pc5-pd-tail-mutant-"))
        mutant = directory / "pagetables.c"
        source = SOURCE.read_text(encoding="utf-8")
        mutant.write_text(source.replace(self.ORIGINAL, self.MUTANT), encoding="utf-8")

        library_path = directory / "libpagetables_mutant.so"
        build = subprocess.run(
            ["gcc", "-std=gnu11", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra",
             "-Werror", "-I", str(SOURCE.parent), str(mutant), "-o", str(library_path)],
            capture_output=True, text=True)
        self.assertEqual(build.returncode, 0,
                         f"the mutant did not compile:\n{build.stderr}")

        lib = ctypes.CDLL(str(library_path))
        lib.pc5_pt_build_identity.restype = ctypes.c_ulong
        lib.pc5_pt_build_identity.argtypes = [ctypes.c_ulonglong, ctypes.c_ulonglong]

        # PML4 and PDPT stay correct in this mutant, so a short span still builds
        # and still reports the right count.  Only the tail is wrong.
        for span in ShortSpanInitialisationTests.SPANS:
            total = span // TWO_MIB
            with self.subTest(span=hex(span)):
                block = Block()
                ctypes.memset(block.base, CANARY, PT_PAGES * PAGE)
                self.assertEqual(
                    lib.pc5_pt_build_identity(block.base, span), total,
                    "precondition: the mutant still reports the right page count")
                stale = [index for index in range(total, 2048)
                         if block.entry(index) != 0]
                self.assertTrue(
                    stale,
                    f"span {span:#x}: the mutant left no stale tail, so this "
                    f"discrimination test proves nothing")
                self.assertEqual(
                    block.entry(stale[0]), CANARY * 0x0101010101010101,
                    f"span {span:#x}: expected the poisoned value to survive at "
                    f"PD entry {stale[0]}")

    def test_the_shipped_short_span_tests_reject_the_mutant(self):
        """The real shipped test must fail on the mutant - not a copy of it.

        test_stale_pd_tail_is_detected above asserts that the mutant leaves a
        stale tail.  That is a fact about the mutant, not about the suite.  This
        test runs the *actual* shipped test method against the mutant and
        requires it to fail, so the discrimination belongs to the shipped
        assertion rather than to a re-implementation of it.
        """
        import tempfile

        directory = pathlib.Path(tempfile.mkdtemp(prefix="pc5-mutant-shipped-"))
        mutant = directory / "pagetables.c"
        source = SOURCE.read_text(encoding="utf-8")
        mutant.write_text(source.replace(self.ORIGINAL, self.MUTANT), encoding="utf-8")
        library_path = directory / "libpagetables_mutant_shipped.so"
        build = subprocess.run(
            ["gcc", "-std=gnu11", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra",
             "-Werror", "-I", str(SOURCE.parent), str(mutant), "-o", str(library_path)],
            capture_output=True, text=True)
        self.assertEqual(build.returncode, 0, f"mutant did not compile:\n{build.stderr}")

        mutant_lib = ctypes.CDLL(str(library_path))
        mutant_lib.pc5_pt_build_identity.restype = ctypes.c_ulong
        mutant_lib.pc5_pt_build_identity.argtypes = [ctypes.c_ulonglong] * 2

        # Bind the shipped test class to the mutant library and run its methods.
        shipped = ShortSpanInitialisationTests
        saved = _LIBRARY.get("lib")
        _LIBRARY["lib"] = mutant_lib
        try:
            for name in ("test_unused_pd_entries_are_zero_for_every_short_span",
                         "test_a_poisoned_tail_is_cleared_not_merely_ignored"):
                with self.subTest(shipped_test=name):
                    case = shipped(name)
                    case.setUp()
                    with self.assertRaises(AssertionError,
                                           msg=f"the shipped test {name} passed "
                                               f"against the mutant, so it does not "
                                               f"actually guard the stale tail"):
                        getattr(case, name)()
        finally:
            if saved is None:
                _LIBRARY.pop("lib", None)
            else:
                _LIBRARY["lib"] = saved


class ShortSpanCoverageIntegrityTests(unittest.TestCase):
    """The short-span coverage must not be able to disappear quietly.

    Emptying or thinning ShortSpanInitialisationTests.SPANS previously left the
    suite green: every short-span test iterates that tuple, so an empty tuple
    made them all vacuously pass, and the mutation test's per-span loop stopped
    exercising anything.  These tests pin the collection's contents and prove
    that losing them is detected rather than tolerated.
    """

    def setUp(self):
        self.spans = ShortSpanInitialisationTests.SPANS
        self.expected = ShortSpanInitialisationTests.EXPECTED_UNUSED

    def test_the_span_collection_contains_the_cases_that_matter(self):
        self.assertTrue(self.spans, "SPANS is empty: the short-span tests would all "
                                    "pass without executing an assertion")
        self.assertEqual(
            set(self.spans), set(self.expected),
            f"SPANS changed: {sorted(map(hex, self.spans))} vs expected "
            f"{sorted(map(hex, self.expected))}. A span was added or removed, so "
            f"EXPECTED_UNUSED must be updated deliberately.")

    def test_every_span_leaves_a_non_empty_tail_to_check(self):
        """A span that consumes all 2048 entries cannot test the tail at all."""
        for span in self.spans:
            with self.subTest(span=hex(span)):
                total = span // TWO_MIB
                unused = (PT_PAGES - 2) * 512 - total
                self.assertEqual(
                    unused, self.expected[span],
                    f"span {span:#x} leaves {unused} unused PD entries, expected "
                    f"{self.expected[span]}")
                self.assertGreater(
                    unused, 0,
                    f"span {span:#x} leaves no unused PD entries, so it cannot "
                    f"exercise the stale-tail property these tests exist for")

    def test_the_collection_covers_more_than_one_gigabyte(self):
        regions = sorted({span // (1 << 30) for span in self.spans})
        self.assertGreaterEqual(
            len(regions), 3,
            f"the span collection only exercises {regions} GiB regions; it must "
            f"cross several PDPT entries to be meaningful")

    def test_empty_spans_would_be_detected(self):
        """The negative case: prove the guard fails when the coverage is removed."""
        saved = ShortSpanInitialisationTests.SPANS
        saved_expected = ShortSpanInitialisationTests.EXPECTED_UNUSED
        try:
            ShortSpanInitialisationTests.SPANS = ()
            ShortSpanInitialisationTests.EXPECTED_UNUSED = {}
            case = ShortSpanCoverageIntegrityTests(
                "test_the_span_collection_contains_the_cases_that_matter")
            case.setUp()          # bind self.spans/self.expected from the empties
            with self.assertRaises(AssertionError,
                                   msg="emptying SPANS must be detected"):
                case.test_the_span_collection_contains_the_cases_that_matter()
        finally:
            ShortSpanInitialisationTests.SPANS = saved
            ShortSpanInitialisationTests.EXPECTED_UNUSED = saved_expected
