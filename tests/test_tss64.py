#!/usr/bin/env python3
"""The TSS builder, driven from the host against the same frame pool the kernel uses.

`kernel64/tss64.c` is compiled together with `kernel64/mem64.c` - the sources the kernel
links, with the kernel's own `-Wall -Wextra -Werror` - and called through ctypes.  The
field offsets come from `__builtin_offsetof` in a file compiled against the real header,
so a layout change moves the test with it instead of silently reading the wrong word.

What this can reach and what it cannot is the design of the file:

  * every allocation decision is host-testable, because it is bookkeeping over the pool;
  * the descriptor encoding is host-testable, because it is built and decoded here;
  * installing the table is not: LGDT and LTR are privileged, so the host test checks the
    guard in front of them (a process runs at CPL 3 and `pc5_tss64_install` must refuse
    before it executes anything), and `make stubtest64` checks what the kernel does at
    CPL 0.  Nothing here claims to have loaded a task register.

`battery()` is the single list of properties.  It runs against the real code and against
mutants of it, and a mutant it cannot distinguish is reported as such: a property no
mutation can break is a property that is not tested.
"""
from __future__ import annotations

import ctypes
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ROOT = HERE.parents[0]
TSS_SOURCE = ROOT / "kernel64" / "tss64.c"
MEM_SOURCE = ROOT / "kernel64" / "mem64.c"
INCLUDE = ROOT / "kernel64" / "include"
KERNEL64 = ROOT / "kernel64"

from test_mem64 import (CONVENTIONAL, DESC_BYTES, INFO_BYTES, OFF,  # noqa: E402
                        Stats)

PAGE = 4096
TSS_SIZE = 104
IST1_OFFSET = 32            # the AMD64 TSS: IST1 is the first field after RSP0..2 + RSV
IOMAP_OFFSET = 100          # the I/O-map base, 4 bytes, ending the 104-byte structure
FRAMES = 3                  # TSS + IST1 + IST2

_OFFSETS = r"""#include "tss64.h"
#define OFF(f) ((unsigned long)__builtin_offsetof(struct pc5_tss64, f))
unsigned long pc5_tss_off_tss_base(void) { return OFF(tss_base); }
unsigned long pc5_tss_off_ist_base(void) { return OFF(ist_base); }
unsigned long pc5_tss_off_ist_top(void) { return OFF(ist_top); }
unsigned long pc5_tss_off_gdt_base(void) { return OFF(gdt_base); }
unsigned long pc5_tss_off_gdt_limit(void) { return OFF(gdt_limit); }
unsigned long pc5_tss_off_selector(void) { return OFF(selector); }
unsigned long pc5_tss_off_cpl(void) { return OFF(cpl); }
unsigned long pc5_tss_off_gdtr_base(void) { return OFF(gdtr_base); }
unsigned long pc5_tss_off_frames_taken(void) { return OFF(frames_taken); }
unsigned long pc5_tss_off_built(void) { return OFF(built); }
unsigned long pc5_tss_off_installed(void) { return OFF(installed); }
unsigned long pc5_tss_off_size(void) { return (unsigned long)sizeof(struct pc5_tss64); }
"""

_FUNCS = (
    ("pc5_mem64_init", ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulonglong,
                                      ctypes.c_void_p, ctypes.c_ulonglong]),
    ("pc5_mem64_ready", ctypes.c_int, [ctypes.c_void_p]),
    ("pc5_mem64_reason", ctypes.c_char_p, [ctypes.c_void_p]),
    ("pc5_mem64_stats", ctypes.c_void_p, [ctypes.c_void_p]),
    ("pc5_tss64_build", ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p]),
    ("pc5_tss64_install", ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p]),
    ("pc5_tss64_release", None, [ctypes.c_void_p, ctypes.c_void_p]),
    ("pc5_tss64_gdt_word", ctypes.c_ulonglong, [ctypes.c_ulonglong]),
    ("pc5_tss64_reason", ctypes.c_char_p, [ctypes.c_void_p]),
)

_CACHE: dict = {}


def build(source_text=None, tag="real"):
    """Compile tss64.c (optionally a mutation of it) with mem64.c into one host library."""
    if tag in _CACHE:
        return _CACHE[tag]
    directory = pathlib.Path(tempfile.mkdtemp(prefix="pc5-tss64-"))
    text = TSS_SOURCE.read_text(encoding="utf-8") if source_text is None else source_text
    tss = directory / "tss64.c"
    tss.write_text(text, encoding="utf-8")
    offsets = directory / "offsets.c"
    offsets.write_text(_OFFSETS, encoding="utf-8")
    out = directory / "libtss64.so"
    done = subprocess.run(
        ["gcc", "-std=gnu11", "-O1", "-fPIC", "-shared",
         "-Wall", "-Wextra", "-Werror", "-ffreestanding", "-mno-red-zone",
         "-fno-builtin", "-fno-stack-protector",
         "-I", str(INCLUDE), "-I", str(KERNEL64),
         str(tss), str(MEM_SOURCE), str(offsets), "-o", str(out)],
        capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(f"the host build of tss64.c failed:\n{done.stderr}")
    lib = ctypes.CDLL(str(out))
    for name, restype, argtypes in _FUNCS:
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes
    for name in ("tss_base", "ist_base", "ist_top", "gdt_base", "gdt_limit", "selector",
                 "cpl", "gdtr_base", "frames_taken", "built", "installed"):
        setattr(lib, f"off_{name}", getattr(lib, f"pc5_tss_off_{name}"))
        getattr(lib, f"off_{name}").restype = ctypes.c_ulonglong
    lib.off_size = getattr(lib, "pc5_tss_off_size")
    lib.off_size.restype = ctypes.c_ulonglong
    _CACHE[tag] = lib
    return lib


def mutate(old, new, tag):
    """One plausible edit to tss64.c, compiled as its own library."""
    source = TSS_SOURCE.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError(f"{tag}: the anchor occurs {source.count(old)} times, "
                             "so the mutant is stale")
    text = source.replace(old, new)
    if text == source:
        raise AssertionError(f"{tag}: the mutant is identical to the original")
    return build(text, tag=tag)


class Reader:
    """Typed reads out of a `struct pc5_tss64` buffer, by the header's own offsets."""

    def __init__(self, lib):
        self.lib = lib
        self.size = lib.off_size()
        self.buffer = (ctypes.c_ubyte * self.size)()
        ctypes.memset(self.buffer, 0, self.size)

    #: the widths the header actually declares; reading a wider word than the field
    #: would pick up its neighbour and turn a pass into an accident
    WIDTH = {"selector": 2, "built": 4, "installed": 4, "frames_taken": 8}

    def word(self, field, index=0, width=None):
        offset = getattr(self.lib, f"off_{field}")() + index * 8
        if width is None:
            width = self.WIDTH.get(field, 8)
        raw = bytes((ctypes.c_ubyte * width).from_buffer(self.buffer, offset))
        return int.from_bytes(raw, "little")


# --- the pool this test drives -------------------------------------------------
#
# A TSS is not merely described, it is *written*: the builder copies 104 bytes into the
# frame it was granted.  So the frames here have to be memory that exists, and they have
# to sit below `PC5_IDENTITY_LIMIT` (4 GiB) because that is the range the pool accepts for
# a map and for everything it hands out.  A `ctypes` buffer satisfies the first condition
# and not the second - its address depends on how much of the heap the process has already
# touched, which is how a fixture written that way passes on its own and fails inside a
# larger run - so libc is asked for a fixed address with `MAP_FIXED_NOREPLACE`.  If the
# mapping does not land exactly where it was asked, the fixture says so instead of
# measuring luck.
RAM_BASE = 1 << 30
RAM_PAGES = 256
# The window the map describes starts at the bottom of the slab, because the pool indexes
# its bitmap by *absolute* frame number: a page at 1 GiB is frame 262144, so the bitmap has
# to reach the top of the window however few frames the window holds.  The bookkeeping
# structures therefore live above it, unmentioned by the map, and two pages of bitmap is
# what covers this window: 1 GiB of frames is 32 KiB of bitmap, plus the window itself.
FRAMES_AT = RAM_BASE
BITMAP_AT = RAM_BASE + 64 * PAGE
INFO_AT = RAM_BASE + 80 * PAGE
POOL_AT = RAM_BASE + 81 * PAGE
DESCS_AT = RAM_BASE + 82 * PAGE
FRAMES_MAX = 64
BITMAP_BYTES = 9 * PAGE
PROT_READ_WRITE = 0x1 | 0x2
MAP_PRIVATE_ANONYMOUS_NOREPLACE = 0x2 | 0x20 | 0x100


def _slab(address, size):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.restype = ctypes.c_void_p
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_long]
    got = libc.mmap(ctypes.c_void_p(address), size, PROT_READ_WRITE,
                    MAP_PRIVATE_ANONYMOUS_NOREPLACE, -1, 0)
    if got != address:
        raise AssertionError(f"cannot place {size} bytes at {address:#x} for the host "
                             f"test (mmap returned {got!r})")


_slab(RAM_BASE, RAM_PAGES * PAGE)


def ram_write(address, payload):
    ctypes.memmove(address, payload, len(payload))


def ram_read(address, size):
    return ctypes.string_at(address, size)


class Pool:
    """One map, one pool struct, one bitmap, and `frames` real host pages to hand out.

    Nothing is pre-claimed: the window is deliberately bare, so that `free` after a build
    is exactly the number of frames the build took and the arithmetic in the battery cannot
    be satisfied by a coincidence of reservations.
    """

    def __init__(self, lib, frames=16, bitmap_bytes=BITMAP_BYTES, kind=CONVENTIONAL,
                 base=FRAMES_AT):
        self.lib = lib
        for address in (INFO_AT, POOL_AT, DESCS_AT):
            ram_write(address, bytes(PAGE))
        ram_write(BITMAP_AT, bytes(bitmap_bytes))
        info = bytearray(PAGE)
        fields = (("magic", 0x42354350, "<I"), ("version", 2, "<I"),
                  ("size", INFO_BYTES, "<I"), ("memmap_addr", DESCS_AT, "<Q"),
                  ("memmap_bytes", DESC_BYTES, "<Q"), ("memmap_desc_size", DESC_BYTES, "<I"),
                  ("memmap_desc_version", 1, "<I"),
                  ("identity_span", RAM_BASE + RAM_PAGES * PAGE, "<Q"))
        for name, value, fmt in fields:
            packed = struct.pack(fmt, value)
            info[OFF[name]:OFF[name] + len(packed)] = packed
        ram_write(INFO_AT, bytes(info))
        ram_write(DESCS_AT, struct.pack("<QQQI4x", base, frames, 0, kind))
        self.cast = POOL_AT
        self.base = base
        self.frames = frames
        self.code = lib.pc5_mem64_init(self.cast, INFO_AT, INFO_AT, BITMAP_AT,
                                      bitmap_bytes)

    @property
    def ready(self):
        return bool(self.lib.pc5_mem64_ready(self.cast))

    @property
    def reason(self):
        return (self.lib.pc5_mem64_reason(self.cast) or b"").decode()

    @property
    def stats(self):
        return Stats.from_address(self.lib.pc5_mem64_stats(self.cast))

    def contains(self, address):
        """Whether `address` is a page this map offers as conventional memory."""
        return self.base <= address < self.base + self.frames * PAGE


def battery(lib):
    """Every property the host can check.  Returns a list of problems; empty means good."""
    problems = []

    def want(label, condition, detail=""):
        if not condition:
            problems.append(f"{label}{': ' + detail if detail else ''}")

    # --- the successful build ------------------------------------------------
    pool = Pool(lib)
    free_before = pool.stats.free
    state = Reader(lib)
    ok = lib.pc5_tss64_build(pool.cast, state.buffer)
    want("the pool can satisfy a TSS and two stacks", ok == 1,
         f"build returned {ok}, reason={lib.pc5_tss64_reason(state.buffer).decode()}")
    if ok == 1:
        tss = state.word("tss_base")
        ist1 = state.word("ist_base", 1)
        ist2 = state.word("ist_base", 2)
        top1 = state.word("ist_top", 1)
        top2 = state.word("ist_top", 2)
        want("three frames are taken from the pool",
             pool.stats.free == free_before - FRAMES,
             f"free went {free_before} -> {pool.stats.free}")
        want("the state reports the three frames it took",
             state.word("frames_taken") == FRAMES, str(state.word("frames_taken")))
        want("no grant is the zero page", 0 not in (tss, ist1, ist2))
        want("the TSS and the two stacks are three distinct frames",
             len({tss, ist1, ist2}) == 3, f"tss={tss:#x} ist1={ist1:#x} ist2={ist2:#x}")
        want("each IST is the top of its own frame",
             (top1, top2) == (ist1 + PAGE, ist2 + PAGE),
             f"ist1={ist1:#x}/{top1:#x} ist2={ist2:#x}/{top2:#x}")
        want("an IST is 16-byte aligned or the CPU faults on entry",
             (top1 & 0xF) == 0 and (top2 & 0xF) == 0)
        want("the table is 47 bytes of limit for six slots",
             state.word("gdt_limit") == 6 * 8 - 1 and state.word("gdt_base") != 0)
        want("the selector a caller would hand to LTR is the slot the descriptor sits in",
             state.word("selector") == 0x28, hex(state.word("selector")))

        raw = ram_read(tss, TSS_SIZE)
        got1 = int.from_bytes(raw[IST1_OFFSET:IST1_OFFSET + 8], "little")
        got2 = int.from_bytes(raw[IST1_OFFSET + 8:IST1_OFFSET + 16], "little")
        want("the TSS advertises the IST1 the report claims", got1 == top1,
             f"tss={got1:#x} report={top1:#x}")
        want("the TSS advertises the IST2 the report claims", got2 == top2,
             f"tss={got2:#x} report={top2:#x}")
        want("the TSS is 104 bytes and the rest of it stays zero",
             raw[0:4] == bytes(4) and raw[4:IST1_OFFSET] == bytes(IST1_OFFSET - 4)
             and raw[IST1_OFFSET + 16:IOMAP_OFFSET] == bytes(IOMAP_OFFSET - IST1_OFFSET - 16),
             "a stale RSP0 or IST3..7 would be a switch onto memory nobody allocated")
        want("no I/O permission map is claimed",
             int.from_bytes(raw[IOMAP_OFFSET:IOMAP_OFFSET + 4], "little") == TSS_SIZE)

        low = lib.pc5_tss64_gdt_word(4)
        high = lib.pc5_tss64_gdt_word(5)
        base = (((low >> 16) & 0xFF) | (((low >> 24) & 0xFF) << 8)
                | (((low >> 32) & 0xFF) << 16) | (((low >> 56) & 0xFF) << 24)
                | ((high & 0xFFFFFFFF) << 32))
        limit = (low & 0xFFFF) | (((low >> 48) & 0xF) << 16)
        want("the descriptor points at the frame the pool granted", base == tss,
             f"descriptor={base:#x} grant={tss:#x}")
        want("the descriptor covers the whole TSS", limit == TSS_SIZE - 1, str(limit))
        want("the descriptor is a present, DPL0, available 64-bit TSS",
             (low >> 40) & 0xFF == 0x89 and (low >> 53) & 1 == 1,
             f"access={(low >> 40) & 0xFF:#x}")
        want("the code and data selectors the IDT names are in the table",
             (lib.pc5_tss64_gdt_word(1) >> 40 & 0xFF) == 0x9A
             and (lib.pc5_tss64_gdt_word(2) >> 40 & 0xFF) == 0x92,
             f"code={(lib.pc5_tss64_gdt_word(1) >> 40) & 0xFF:#x} "
             f"data={(lib.pc5_tss64_gdt_word(2) >> 40) & 0xFF:#x}")

        # --- the guard in front of the privileged instructions ---------------
        installed = lib.pc5_tss64_install(pool.cast, state.buffer)
        want("a host process at CPL 3 must be refused before LGDT runs", installed == 0,
             f"install returned {installed}")
        want("the refusal names the privilege level",
             lib.pc5_tss64_reason(state.buffer).decode() == "not-ring-0",
             lib.pc5_tss64_reason(state.buffer).decode())
        want("the refused install leaves installed clear", state.word("installed") == 0)
        want("the CPL the guard saw is the one the report prints",
             (state.word("cpl") & 3) == 3, str(state.word("cpl")))
        want("a refusal to install does not release the frames behind a built TSS",
             state.word("built") == 1)

        # --- release --------------------------------------------------------
        want("every granted frame is a page of the window the map offered",
             all(pool.contains(a) for a in (tss, ist1, ist2)),
             f"window={pool.base:#x}+{pool.frames * PAGE:#x} tss={tss:#x} "
             f"ist1={ist1:#x} ist2={ist2:#x}")

        # --- release --------------------------------------------------------
        lib.pc5_tss64_release(pool.cast, state.buffer)
        want("release hands every frame back", pool.stats.free == free_before,
             f"free={pool.stats.free} expected {free_before}")
        want("release clears what it handed back",
             state.word("tss_base") == 0 and state.word("ist_base", 1) == 0
             and state.word("ist_base", 2) == 0 and state.word("frames_taken") == 0)
        want("release leaves no descriptor naming memory that is on offer again",
             lib.pc5_tss64_gdt_word(4) == 0 and lib.pc5_tss64_gdt_word(5) == 0)

    # --- refusals -----------------------------------------------------------
    # The three frames are asked for in order - TSS, IST1, IST2 - so a window with one
    # free page fails at IST1 and a window with two fails at IST2.  Each refusal has to
    # name its own stage and give back what it already took: the difference between "no"
    # and "half", which is the difference between a clean boot and a leak at every fault.
    for frames, expected in ((1, "ist1-frame-refused"), (2, "ist2-frame-refused")):
        pool = Pool(lib, frames=frames)
        before = pool.stats.free
        state = Reader(lib)
        ok = lib.pc5_tss64_build(pool.cast, state.buffer)
        want(f"build refuses when a window of {frames} cannot hold three frames", ok == 0,
             f"build returned {ok}")
        want(f"the refusal is named {expected}",
             lib.pc5_tss64_reason(state.buffer).decode() == expected,
             lib.pc5_tss64_reason(state.buffer).decode())
        want(f"the refusal for {expected} takes nothing", pool.stats.free == before,
             f"free={pool.stats.free} vs {before}")
        want(f"{expected} leaves no TSS address behind", state.word("tss_base") == 0)
        want(f"{expected} leaves the descriptor slots empty",
             lib.pc5_tss64_gdt_word(4) == 0 and lib.pc5_tss64_gdt_word(5) == 0)

    # A map the pool cannot serve never becomes ready, and the builder's first check is
    # the one that fires: a bitmap too small for the window is refused inside
    # `pc5_mem64_init`, so the caller is told "pool-not-ready" rather than walking into an
    # allocator with no bitmap to mark.  Zero conventional memory is the same shape.
    pool = Pool(lib, frames=FRAMES_MAX, bitmap_bytes=8)
    want("a window above the bitmap cap is refused by the pool itself", not pool.ready,
         "the pool came up ready over a map it cannot track")
    state = Reader(lib)
    want("so the builder refuses before allocating",
         lib.pc5_tss64_build(pool.cast, state.buffer) == 0)
    want("and says so as an unready pool",
         lib.pc5_tss64_reason(state.buffer).decode() == "pool-not-ready",
         lib.pc5_tss64_reason(state.buffer).decode())
    want("with nothing taken", state.word("tss_base") == 0)
    pool = Pool(lib, frames=0)
    want("a map with no conventional memory at all is refused too", not pool.ready,
         "the pool came up ready over nothing")
    state = Reader(lib)
    want("and the builder refuses it before asking for a frame",
         lib.pc5_tss64_build(pool.cast, state.buffer) == 0)
    want("with no TSS address recorded", state.word("tss_base") == 0)
    return problems


class Tss64HostTests(unittest.TestCase):
    def test_the_battery_passes_against_the_real_source(self):
        problems = battery(build())
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_state_is_big_enough_for_what_the_kernel_prints(self):
        # Every field the boot line prints has to exist in the struct: a report that reads
        # past the end would print the neighbour's bytes and still look plausible.
        lib = build()
        self.assertGreaterEqual(lib.off_size(), TSS_SIZE)
        self.assertEqual(lib.off_ist_top() - lib.off_ist_base(), 8 * 8,
                         "ist_base and ist_top are seven-slot arrays of the same width")
        for field in ("tss_base", "ist_base", "ist_top", "gdt_base", "gdt_limit",
                      "selector", "cpl", "gdtr_base", "frames_taken", "built",
                      "installed"):
            self.assertGreaterEqual(getattr(lib, f"off_{field}")(), 0)
            self.assertLess(getattr(lib, f"off_{field}")(), lib.off_size(), field)


class Tss64MutationTests(unittest.TestCase):
    """Each mutant is one plausible edit; the battery above has to notice all of them."""

    def assert_caught(self, lib, label):
        problems = battery(lib)
        self.assertTrue(problems,
                        f"{label}: the battery passed against the mutant, so the property "
                        "it removes is not tested")

    def test_a_misaligned_interrupt_stack_is_caught(self):
        self.assert_caught(mutate(
            "    out->ist_top[PC5_TSS64_IST_DOUBLE_FAULT] = base + PC5_MEM64_PAGE;",
            "    out->ist_top[PC5_TSS64_IST_DOUBLE_FAULT] = base + PC5_MEM64_PAGE - 4ull;",
            "ist-top-unaligned"), "the -4 'red zone' on IST1")

    def test_ignoring_the_ready_flag_is_caught(self):
        self.assert_caught(mutate("    if (!pc5_mem64_ready(pool)) {", "    if (0) {",
                                  "ignore-ready"), "the ready check")

    def test_a_release_that_forgets_the_stacks_is_caught(self):
        self.assert_caught(mutate(
            "    for (i = 0; i < 8; i++) {\n        if (state->ist_base[i] != 0) {",
            "    for (i = 3; i < 8; i++) {\n        if (state->ist_base[i] != 0) {",
            "release-short"), "the IST release loop")

    def test_a_refusal_that_keeps_the_frames_is_caught(self):
        self.assert_caught(mutate(
            "    for (i = frames; i > 0; i--) {",
            "    for (i = 0; i > frames; i--) {",
            "no-unwind"), "the unwind on the refusal path")

    def test_a_wrong_descriptor_encoding_is_caught(self):
        self.assert_caught(mutate(
            "        | (((base >> 24) & 0xFFull) << 56);",
            "        | (((base >> 24) & 0xFFull) << 48);",
            "desc-shift"), "the descriptor's base[31:24] field")


if __name__ == "__main__":
    unittest.main(verbosity=2)
