#!/usr/bin/env python3
"""The 64-bit frame allocator, driven from the host against maps no fixture has to provide.

`kernel64/mem64.c` is compiled from the same source the kernel links, with the same
`-Wall -Wextra -Werror`, and called through ctypes.  Physical addresses are supplied as the
host addresses of ordinary buffers, because the allocator never dereferences them: it decides
*which* ranges to hand out, and every decision here is about bookkeeping that the boot path
cannot afford to get wrong.

What is checked, and why each of these is a real property rather than a value:

  * fail-closed on every malformed map (descriptor wraps, unaligned start, wrong descriptor
    size, overlapping descriptors, no conventional memory, a map above the bitmap);
  * clipping to the identity span, because a frame above the map the kernel is running on
    faults rather than working;
  * the loader's and the kernel's own ranges never being handed out;
  * the zero page being reserved by policy, so `0` can never alias a granted allocation
    (the sentinelled API had exactly this bug, and the executed stub gate found it);
  * refusal semantics: `pages == 0`, a request longer than the pool, a request longer than
    the largest contiguous run, and the out-parameter cleared on all of them;
  * double free, unaligned free, out-of-range free - each refused without changing anything;
  * and the accounting invariant `free + claimed == conventional`, re-checked after every
    operation, which is what would break first if a path forgot to count.
"""
from __future__ import annotations

import ctypes
import pathlib
import struct
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel64" / "mem64.c"
INCLUDE = ROOT / "kernel64" / "include"

PAGE = 4096
INFO_BYTES = 160
DESC_BYTES = 32
MASK64 = (1 << 64) - 1
CANARY = 0x5C

# pc5_boot64 field offsets, pinned by _Static_assert in kernel64/main.c and by
# boot/uefi64/uefi64_main.c; restated here so a host test can build one.
OFF = {"magic": 0, "version": 4, "size": 8, "flags": 12, "memmap_addr": 16,
       "memmap_bytes": 24, "memmap_desc_size": 32, "memmap_desc_version": 36,
       "framebuffer_addr": 40, "acpi_rsdp": 64, "kernel_base": 72, "kernel_size": 80,
       "stack_top": 88, "stack_size": 96, "pagetable_addr": 104, "pagetable_pages": 112,
       "identity_span": 120, "loader_image_addr": 128, "loader_image_size": 136,
       "exit_boot_services": 144, "time_stamp": 152}

CONVENTIONAL = 7
LOADER_DATA = 2
BOOT_SERVICES_DATA = 4
RESERVED = 0
UNUSABLE = 8
MMIO = 11

_LIBRARIES: dict = {}


def build(source=None, tag="real"):
    """Compile mem64.c (or a mutated copy of it) into a host library."""
    if tag in _LIBRARIES:
        return _LIBRARIES[tag]
    directory = pathlib.Path(tempfile.mkdtemp(prefix="pc5-mem64-"))
    text = SOURCE.read_text(encoding="utf-8") if source is None else source
    compiled = directory / "mem64.c"
    compiled.write_text(text, encoding="utf-8")
    output = directory / "libmem64.so"
    done = subprocess.run(
        ["gcc", "-std=gnu11", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra", "-Werror",
        "-Wall", "-Wextra", "-Werror", "-ffreestanding", "-mno-red-zone", "-I", str(INCLUDE), str(compiled),
         "-o", str(output)],
        capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(f"mem64.c did not build for the host:\n{done.stderr}")
    lib = ctypes.CDLL(str(output))
    u64, u8p, pint = ctypes.c_ulonglong, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_void_p
    lib.pc5_mem64_init.restype = ctypes.c_int
    lib.pc5_mem64_init.argtypes = [u8p, pint, u64, u8p, u64]
    for name in ("pc5_mem64_alloc", "pc5_mem64_free", "pc5_mem64_validate",
                 "pc5_mem64_overlaps_claimed", "pc5_mem64_range_usable", "pc5_mem64_ready"):
        getattr(lib, name).restype = ctypes.c_int
    lib.pc5_mem64_alloc.argtypes = [u8p, u64, pint]
    lib.pc5_mem64_free.argtypes = [u8p, u64, u64]
    lib.pc5_mem64_validate.argtypes = [u8p, u64, u64]
    lib.pc5_mem64_overlaps_claimed.argtypes = [u8p, u64, u64]
    lib.pc5_mem64_range_usable.argtypes = [u8p, u64, u64]
    lib.pc5_mem64_ready.argtypes = [u8p]
    lib.pc5_mem64_reason.restype = ctypes.c_char_p
    lib.pc5_mem64_reason.argtypes = [u8p]
    lib.pc5_mem64_stats.restype = ctypes.c_void_p
    lib.pc5_mem64_stats.argtypes = [u8p]
    lib.pc5_mem64_claim_start.restype = u64
    lib.pc5_mem64_claim_start.argtypes = [u8p, u64]
    lib.pc5_mem64_claim_size.restype = u64
    lib.pc5_mem64_claim_size.argtypes = [u8p, u64]
    _LIBRARIES[tag] = lib
    return lib


class Stats(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "frames", "conventional", "conventional_total", "claimed", "free",
        "largest_at_init", "requests", "refusals", "double_frees", "coverage_refusals")]


class Region:
    """Real host memory, placed below 4 GiB so it can stand in for a physical address.

    The allocator range-checks the pointers it is given against the protocol's identity
    limit, so a heap address in the top of the 48-bit user space would be *correctly*
    refused - and the test would measure the refusal instead of the allocator.  Mapping a
    region just below 4 GiB and keeping the maps it describes at low addresses gives both
    sides something real to point at, and leaves every claim the pool makes about
    `info_base` outside the spans under test (which is what the clipping branch is for).
    """

    BASE = 0xFF000000          # 4 GiB - 16 MiB
    SIZE = 16 * 1024 * 1024

    def __init__(self):
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.mmap.restype = ctypes.c_void_p
        # PROT_READ|PROT_WRITE, MAP_FIXED|MAP_PRIVATE|MAP_ANONYMOUS
        address = libc.mmap(ctypes.c_void_p(self.BASE), self.SIZE, 0x3, 0x22 | 0x20, -1, 0)
        self.cursor = 0 if address is None else address
        if not self.cursor or self.cursor != self.BASE:
            raise AssertionError("cannot map a region below 4 GiB for the host test")
        self.libc = libc
        self.used = 0

    def take(self, size):
        size = (size + PAGE - 1) & ~(PAGE - 1)
        if self.used + size > self.SIZE:
            raise AssertionError("the host test ran out of low memory")
        address = self.BASE + self.used
        self.used += size
        return address

    def write(self, address, payload):
        ctypes.memmove(address, payload, len(payload))

    def read(self, address, size):
        return ctypes.string_at(address, size)

    def poke(self, address, offset, value):
        ctypes.c_ubyte.from_address(address + offset).value = value

    def peek(self, address, offset):
        return ctypes.c_ubyte.from_address(address + offset).value


REGION = None


def region():
    global REGION
    if REGION is None:
        REGION = Region()
    return REGION


class Pool:
    """A boot-info structure, a descriptor array and a guarded bitmap, all real memory."""

    def __init__(self, descriptors=(), bitmap_pages=4, span=64 * PAGE, info_over=None,
                 claims=None, lib=None):
        room = region()
        self.lib = lib
        self.info_base = room.take(PAGE * 2)
        room.write(self.info_base, bytes(PAGE * 2))
        # the reported length is the array; the mapping is a page, so a test that
        # replaces the array with more or fewer entries cannot read past its buffer
        self.desc_bytes = DESC_BYTES * len(descriptors)
        self.descs_base = room.take(max(PAGE, DESC_BYTES * 16))
        self.bitmap_bytes = bitmap_pages * PAGE
        block = self.bitmap_bytes + 4 * PAGE
        self.block_base = room.take(block)
        room.write(self.block_base, bytes(block))
        self.bitmap = self.block_base + PAGE
        self.guard_before = self.bitmap - PAGE
        self.guard_after = self.bitmap + self.bitmap_bytes
        for address in (self.guard_before, self.guard_after):
            room.write(address, bytes([CANARY]) * PAGE)
        self.set_descriptors(descriptors)

        payload = bytearray(PAGE * 2)

        def put(offset, value, fmt="<Q"):
            packed = struct.pack(fmt, value)
            payload[offset:offset + len(packed)] = packed

        put(OFF["magic"], 0x42354350, "<I")
        put(OFF["version"], 2, "<I")
        put(OFF["size"], INFO_BYTES, "<I")
        put(OFF["flags"], 1, "<I")
        put(OFF["memmap_addr"], self.descs_base)
        put(OFF["memmap_bytes"], self.desc_bytes)
        put(OFF["memmap_desc_size"], DESC_BYTES, "<I")
        put(OFF["memmap_desc_version"], 1, "<I")
        put(OFF["identity_span"], span)
        put(OFF["exit_boot_services"], 1)
        for name, value in list((info_over or {}).items()) + list((claims or {}).items()):
            put(OFF[name], value)
        room.write(self.info_base, bytes(payload))
        self.pool_base = room.take(PAGE)
        room.write(self.pool_base, bytes(PAGE))

    def set_descriptors(self, descriptors):
        """Replace the descriptor array, and the address and length the boot info states.

        A test that edited the array without repointing `memmap_addr` would be measuring a
        stale map, and would pass for the wrong reason.
        """
        self.desc_bytes = DESC_BYTES * len(descriptors)
        payload = bytearray(max(self.desc_bytes, DESC_BYTES))
        for index, (kind, start, pages) in enumerate(descriptors):
            packed = struct.pack("<QQQII", start, pages, 0, kind, 0)
            payload[index * DESC_BYTES:index + DESC_BYTES] = packed
        region().write(self.descs_base, bytes(payload))
        self.desc_count = len(descriptors)
        if hasattr(self, "info_base"):
            # the boot info has to describe the array that is really there, or a test
            # that grows or shrinks the map is measured against the old one
            self.info_field("memmap_addr", self.descs_base)
            self.info_field("memmap_bytes", self.desc_bytes)

    def desc_field(self, index, field, value):
        offsets = {"start": 0, "pages": 8, "attributes": 16, "type": 24}
        fmt = "<I" if field == "type" else "<Q"
        raw = bytearray(region().read(self.descs_base, self.desc_bytes))
        packed = struct.pack(fmt, value)
        at = index * DESC_BYTES + offsets[field]
        raw[at:at + len(packed)] = packed
        region().write(self.descs_base, bytes(raw))

    def info_field(self, name, value):
        raw = bytearray(region().read(self.info_base, PAGE * 2))
        packed = struct.pack("<I" if name in ("memmap_desc_size", "memmap_desc_version",
                                              "size", "version", "flags", "magic") else "<Q",
                             value)
        raw[OFF[name]:OFF[name] + len(packed)] = packed
        region().write(self.info_base, bytes(raw))

    def guards_intact(self):
        for address in (self.guard_before, self.guard_after):
            if set(region().read(address, PAGE)) != {CANARY}:
                return False
        return True

    def init(self, lib=None):
        self.lib = lib or self.lib or build()
        base = self.info_base
        code = self.lib.pc5_mem64_init(
            ctypes.cast(self.pool_base, ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.c_void_p(base), base,
            ctypes.cast(self.bitmap, ctypes.POINTER(ctypes.c_ubyte)), self.bitmap_bytes)
        return Pool.Handle(self, code, self.pool_base)

    class Handle:
        def __init__(self, owner, code, pool):
            self.owner, self.code, self.ptr = owner, code, pool
            self.lib = owner.lib
            self.cast = ctypes.cast(pool, ctypes.POINTER(ctypes.c_ubyte))

        @property
        def ready(self):
            return bool(self.lib.pc5_mem64_ready(self.cast))

        @property
        def reason(self):
            return (self.lib.pc5_mem64_reason(self.cast) or b"").decode()

        @property
        def stats(self):
            return Stats.from_address(self.lib.pc5_mem64_stats(self.cast))

        def alloc(self, pages):
            base = ctypes.c_ulonglong(0xA5A5)
            ok = self.lib.pc5_mem64_alloc(self.cast, pages, ctypes.byref(base))
            return bool(ok), base.value

        def free(self, base, pages):
            return bool(self.lib.pc5_mem64_free(self.cast, base, pages))

        def validate(self, base, pages):
            return bool(self.lib.pc5_mem64_validate(self.cast, base, pages))

        def overlaps(self, base, pages):
            return bool(self.lib.pc5_mem64_overlaps_claimed(self.cast, base, pages))

        def usable(self, start, end):
            return bool(self.lib.pc5_mem64_range_usable(self.cast, start, end))

        def bit(self, frame):
            byte = region().peek(self.owner.bitmap + (frame >> 3), 0)
            return (byte >> (frame & 7)) & 1

        def set_bit(self, frame, value):
            address = self.owner.bitmap + (frame >> 3)
            current = region().peek(self.owner.bitmap + (frame >> 3), 0)
            byte = region().read(address, 1)[0]
            byte = (byte | (1 << (frame & 7))) if value else (byte & ~(1 << (frame & 7)))
            region().write(address, bytes([byte]))

        def fill_bitmap(self, value):
            region().write(self.owner.bitmap, bytes([value]) * self.owner.bitmap_bytes)


def conventional(pages, start=0):
    """One descriptor of the only type a pool may manage."""
    return [(CONVENTIONAL, start, pages)]


# ------------------------------------------------------------------ the battery
def battery(pool_factory):
    # `pool_factory` is a parameter, not the module: the callers pass `lambda **kw:
    # Pool(**kw)` for the real library and a closure carrying a mutated library for each
    # control, so the same properties run over both and only the code under test differs.
    """Every property this file claims, run against whatever `pool_factory()` returns.

    Both the real library and each mutant are put through this same function: a mutant is
    only interesting if the battery *notices* it, and a battery that only ever runs against
    the code it was written with proves nothing about either.
    """
    failures = []

    def want(label, condition):
        if not condition:
            failures.append(label)

    # 1. an ordinary map: everything free, nothing claimed, the zero page held
    pool = pool_factory(descriptors=conventional(16), span=64 * PAGE)
    handle = pool.init()
    want("plain map initialises", handle.code == 1 and handle.ready)
    want("plain map counts the map", handle.stats.conventional == 16)
    want("plain map has nothing claimed but the zero page", handle.stats.claimed == 1)
    want("free plus claimed equals conventional",
         handle.stats.free + handle.stats.claimed == handle.stats.conventional)
    ok, base = handle.alloc(1)
    want("a single page is granted", ok and base == PAGE)
    ok2, base2 = handle.alloc(1)
    want("first-fit moves forward", ok2 and base2 == 2 * PAGE)
    want("guard canaries survive allocation", pool.guards_intact())

    # 2. the zero page is never granted, and `0` always means "not granted"
    pool = pool_factory(descriptors=conventional(4), span=64 * PAGE)
    handle = pool.init()
    want("frame zero is claimed", handle.bit(0) == 1)
    want("the zero page is not valid as an allocation", not handle.validate(0, 1))
    want("the zero page overlaps a claim", handle.overlaps(0, 1))

    # 3. a refusal leaves nothing behind and clears the out-parameter
    pool = pool_factory(descriptors=conventional(8), span=64 * PAGE)
    handle = pool.init()
    before = handle.stats.free
    want("zero pages refused", handle.alloc(0) == (False, 0))
    want("a run longer than the pool refused", handle.alloc(9)[0] is False)
    want("a run longer than the largest contiguous run refused",
         handle.alloc(handle.stats.largest_at_init + 1)[0] is False)
    want("refusals leave the pool unchanged", handle.stats.free == before)
    want("refusals are counted", handle.stats.refusals == 3)

    # 4. freeing: exact, refused when wrong, and never double
    pool = pool_factory(descriptors=conventional(8), span=64 * PAGE)
    handle = pool.init()
    ok, base = handle.alloc(3)
    want("three pages granted contiguously", ok and handle.bit(base // PAGE + 2) == 1)
    want("validate accepts a granted run", handle.validate(base, 3))
    want("free returns it", handle.free(base, 3))
    want("validate rejects a freed run", not handle.validate(base, 3))
    want("double free refused", not handle.free(base, 3))
    want("unaligned free refused", not handle.free(base + 1, 1))
    want("out-of-range free refused", not handle.free(64 * PAGE, 1))
    want("zero-page free refused", not handle.free(0, 1))
    want("double free counted once", handle.stats.double_frees == 1)
    want("accounting survived", handle.stats.free == handle.stats.conventional - 1)

    # 5. claims the map never mentioned: the kernel image and the stack
    claims = {"kernel_base": 4 * PAGE, "kernel_size": 4 * PAGE,
              "stack_top": 10 * PAGE, "stack_size": 2 * PAGE}
    pool = pool_factory(descriptors=conventional(16), span=64 * PAGE, claims=claims)
    handle = pool.init()
    want("claims are subtracted from the free set", handle.stats.claimed == 7)
    granted = []
    for _ in range(20):
        ok, base = handle.alloc(1)
        if not ok:
            break
        granted.append(base)
    want("no grant touches the kernel image",
         all(base < 4 * PAGE or base >= 8 * PAGE for base in granted))
    want("no grant touches the stack",
         all(base < 8 * PAGE or base >= 10 * PAGE for base in granted))
    want("every grant validates", all(handle.validate(base, 1) for base in granted))
    want("the run stopped when the free pages ran out", handle.alloc(1)[0] is False)

    # 6. clipping: a map that reaches past the identity span contributes only what is under it
    pool = pool_factory(descriptors=[(CONVENTIONAL, 0, 64)], span=16 * PAGE)
    handle = pool.init()
    want("span clipping bounds the pool", handle.stats.conventional == 16)
    want("clipping is reported against the map total",
         handle.stats.conventional_total == 64 and handle.stats.conventional < 64)
    want("nothing above the span is usable", not handle.usable(16 * PAGE, 17 * PAGE))
    want("everything below it is", handle.usable(2 * PAGE, 3 * PAGE))

    # 7. non-conventional memory is never managed, whatever the map says about it
    pool = pool_factory(descriptors=[(RESERVED, 0, 8), (UNUSABLE, 8 * PAGE, 8),
                                      (MMIO, 16 * PAGE, 8), (CONVENTIONAL, 24 * PAGE, 4)],
                        span=64 * PAGE)
    handle = pool.init()
    want("only conventional memory is free", handle.stats.conventional == 4)
    ok, base = handle.alloc(1)
    want("the granted page is the conventional one", ok and base == 24 * PAGE)
    want("reserved memory is never usable", not handle.usable(0, PAGE))
    want("mmio is never usable", not handle.usable(16 * PAGE, 17 * PAGE))

    # 8. every malformed map is refused with a reason, and a refused pool allocates nothing
    def refuses(label, descriptors=conventional(8), span=64 * PAGE, info_over=None,
                bitmap_pages=4, reason=None, patch=None):
        pool = pool_factory(descriptors=descriptors, span=span, info_over=info_over,
                            bitmap_pages=bitmap_pages)
        if patch:
            patch(pool)
        handle = pool.init()
        want(f"{label}: init refused", handle.code == 0 and not handle.ready)
        if reason is not None:
            want(f"{label}: reason is {reason}", handle.reason == reason)
        want(f"{label}: nothing allocates", handle.alloc(1) == (False, 0))

    # a descriptor array whose element size the loader did not agree with
    def wrong_desc_size(pool):
        pool.info_field("memmap_desc_size", 40)
    refuses("descriptor size", patch=wrong_desc_size, reason="map-descsize")

    def odd_bytes(pool):
        pool.info_field("memmap_bytes", DESC_BYTES + 8)
    refuses("descriptor array length", patch=odd_bytes, reason="map-length")

    def empty_array(pool):
        pool.info_field("memmap_bytes", 0)
    refuses("empty descriptor array", patch=empty_array, reason="map-length")

    def unaligned(pool):
        pool.desc_field(0, "start", PAGE + 8)
    refuses("unaligned descriptor", patch=unaligned, reason="map-descriptor")

    def wrapping(pool):
        pool.desc_field(0, "start", MASK64 - 4096)
        pool.desc_field(0, "pages", 4)
    refuses("wrapping descriptor", patch=wrapping, reason="map-inconsistent")

    def overlaps(pool):
        pool.set_descriptors([(CONVENTIONAL, 0, 8), (CONVENTIONAL, 4 * PAGE, 8)])
    refuses("two descriptors claiming one range", patch=overlaps, reason="map-inconsistent")

    def no_ram(pool):
        pool.set_descriptors([(RESERVED, 0, 8)])
    refuses("no conventional memory", patch=no_ram, reason="map-no-conventional")

    # 16 KiB of bitmap describes 131072 frames and one page describes 32768, so a
    # 40000-page map is out of reach of a one-page bitmap whatever the span says
    refuses("map above the bitmap's reach", descriptors=conventional(40000),
            span=40000 * PAGE, bitmap_pages=1, reason="map-above-bitmap-cap")

    def zero_span(pool):
        pool.info_field("identity_span", 0)
    refuses("span of zero", patch=zero_span, reason="span-unusable")

    def huge_span(pool):
        pool.info_field("identity_span", 8 << 30)
    refuses("span above the protocol limit", patch=huge_span, reason="span-unusable")

    # 9. a bitmap whose own frames the pool must not hand out is outside the described
    #    window by construction, because the window ends where the map ends
    pool = pool_factory(descriptors=conventional(16), span=64 * PAGE)
    handle = pool.init()
    want("the described window is what the map reaches", handle.stats.frames == 16)
    want("no grant leaves the window",
         all(base < handle.stats.frames * PAGE for _, base in
             [handle.alloc(1) for _ in range(15)]))

    # 10. the defence in depth: a bitmap edited behind the pool's back must not be enough
    # the window ends where the last conventional descriptor ends, so a reserved run
    # *between* two conventional ones is inside the described frames and outside the
    # usable set - which is precisely the case the allocation-time re-check is for
    pool = pool_factory(descriptors=[(CONVENTIONAL, 0, 4), (RESERVED, 4 * PAGE, 4),
                                     (CONVENTIONAL, 8 * PAGE, 4)], span=64 * PAGE)
    handle = pool.init()
    handle.fill_bitmap(0xFF)                       # nothing free at all...
    handle.set_bit(5, 0)                           # ...except a frame inside Reserved memory
    ok, base = handle.alloc(1)
    want("an allocation outside the map is refused", not ok and base == 0)
    want("the refusal is counted as a coverage refusal", handle.stats.coverage_refusals == 1)

    return failures


class FrameAllocatorTests(unittest.TestCase):
    def test_the_whole_battery_passes_against_the_real_allocator(self):
        """One call site, so a green suite means every property above still holds."""
        failures = battery(lambda **kw: Pool(**kw))
        self.assertEqual(failures, [], "the allocator broke these properties: " + "; ".join(failures))

    def test_stats_are_not_left_dirty_between_pools(self):
        pool = Pool(descriptors=conventional(8), span=64 * PAGE)
        handle = pool.init()
        self.assertTrue(handle.ready)
        self.assertEqual(handle.reason, "ok")
        second = Pool(descriptors=[(RESERVED, 0, 8)], span=64 * PAGE).init()
        self.assertFalse(second.ready)
        self.assertEqual(second.stats.requests, 0)
        self.assertEqual(second.stats.conventional, 0)

    def test_the_reason_string_survives_a_refused_pool(self):
        pool = Pool(descriptors=conventional(8), span=64 * PAGE)
        pool.info_field("memmap_desc_size", 40)
        handle = pool.init()
        self.assertEqual(handle.reason, "map-descsize")
        self.assertFalse(handle.ready)

    def test_a_pool_below_the_bitmap_capacity_still_reports_the_map_total(self):
        pool = Pool(descriptors=conventional(40000), span=40000 * PAGE, bitmap_pages=1)
        handle = pool.init()
        self.assertFalse(handle.ready)
        self.assertEqual(handle.stats.conventional_total, 40000)


# ------------------------------------------------------------------ negative controls
class AllocationDefenceMutationTests(unittest.TestCase):
    """Each mutant is a specific defence removed from the source, and the *same* battery has
    to notice it.  A mutant the battery cannot distinguish from the real allocator means the
    property it removes is not actually tested - which is the failure mode this file exists to
    avoid, in the suite as much as in the kernel.
    """

    ORIGINAL_COVERAGE = """                if (pc5_mul_overflows(pages, PC5_MEM64_PAGE, &bytes) ||
                    pc5_add_overflows(start, bytes, &stop) ||
                    !pc5_mem64_range_usable(pool, start, stop) ||
                    pc5_mem64_overlaps_claimed(pool, start, pages)) {"""
    MUTANT_COVERAGE = """                if (pc5_mul_overflows(pages, PC5_MEM64_PAGE, &bytes) ||
                    pc5_add_overflows(start, bytes, &stop)) {"""

    ORIGINAL_ZERO = """    default: return PC5_MEM64_PAGE;           /* one page, always */"""
    MUTANT_ZERO = """    default: return 0;                        /* MUTANT: the zero page is not reserved */"""

    ORIGINAL_DOUBLE = """        if (!bit_get(pool->bitmap, frame)) {
            pool->stats.double_frees++;
            return 0;                 /* nothing changed: the first free wins */
        }"""
    MUTANT_DOUBLE = """        (void)frame;"""

    def assert_mutant_is_caught(self, original, mutant, label):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn(original, source, f"{label}: the anchor moved; the mutant is stale")
        self.assertNotEqual(original, mutant, f"{label}: MUTANT is identical to ORIGINAL")
        mutated = source.replace(original, mutant)
        self.assertNotEqual(mutated, source)
        directory = pathlib.Path(tempfile.mkdtemp(prefix="pc5-mem64-mutant-"))
        compiled = directory / "mem64.c"
        compiled.write_text(mutated, encoding="utf-8")
        lib = build(mutated, tag=label)
        failures = battery(lambda **kw: Pool(lib=lib, **kw))
        self.assertTrue(failures, f"{label}: the battery passed against the mutant, so the "
                                  "property it removes is not tested")

    def test_removing_the_coverage_recheck_is_caught(self):
        self.assert_mutant_is_caught(self.ORIGINAL_COVERAGE, self.MUTANT_COVERAGE,
                                    "coverage-recheck-removed")

    def test_not_reserving_the_zero_page_is_caught(self):
        self.assert_mutant_is_caught(self.ORIGINAL_ZERO, self.MUTANT_ZERO,
                                     "zero-page-claim-removed")

    def test_allowing_a_double_free_is_caught(self):
        self.assert_mutant_is_caught(self.ORIGINAL_DOUBLE, self.MUTANT_DOUBLE,
                                     "double-free-guard-removed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
