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
# The 64-bit TSS, field by field, transcribed from Intel SDM vol. 3A figure 7-2 ("64-Bit TSS
# Format") and table 7-1's note on the I/O map.  This table is deliberately *not* read out of
# kernel64/tss64.h or kernel64/tss64.c: the point of an independent restatement is that it can
# disagree with the code, and here it did.
#
# It used to begin `IST1_OFFSET = 32  # the first field after RSP0..2 + RSV`, which is what the
# builder believed, so the assertions below compared the builder's output with the builder's own
# idea of where the fields go and passed -- while every CPU in the world read IST1 at 36 and found
# the high half of that value welded to the low half of IST2.  The kernel's TSS therefore named
# `0x00004000_00000000` as the double-fault stack, and the mistake was invisible from inside: 32
# is where RSP0..RSP2 *would* leave the next free byte if the link field were a quadword, and it is
# eight bytes, so nothing looked unaligned or out of range.  What found it was the delivery probe,
# which asked a real CPU to use the field and took a not-present write fault at the instruction
# that asked.
#
# So: RSP0 at 4, and the IST slots start at 36, because the reserved quadword at 28 is real, and
# the I/O map base is a *word* at 102 -- a dword there would read the two reserved bytes before it.
TSS_FIELDS = (
    ("link", 0, 4),
    ("rsp0", 4, 8),
    ("rsp1", 12, 8),
    ("rsp2", 20, 8),
    ("reserved-rsp", 28, 8),
    ("ist1", 36, 8),
    ("ist2", 44, 8),
    ("ist3", 52, 8),
    ("ist4", 60, 8),
    ("ist5", 68, 8),
    ("ist6", 76, 8),
    ("ist7", 84, 8),
    ("reserved-ist", 92, 2),
    ("iomap-base", 102, 2),
)
TSS_FIELD = {name: (offset, width) for name, offset, width in TSS_FIELDS}
IST1_OFFSET = TSS_FIELD["ist1"][0]
IOMAP_OFFSET = TSS_FIELD["iomap-base"][0]
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
unsigned long pc5_tss_off_gdtr_limit(void) { return OFF(gdtr_limit); }
unsigned long pc5_tss_off_desc_base(void) { return OFF(desc_base); }
unsigned long pc5_tss_off_desc_limit(void) { return OFF(desc_limit); }
unsigned long pc5_tss_off_desc_type_before(void) { return OFF(desc_type_before); }
unsigned long pc5_tss_off_desc_type_after(void) { return OFF(desc_type_after); }
unsigned long pc5_tss_off_tr_selector(void) { return OFF(tr_selector); }
unsigned long pc5_tss_off_tr_armed(void) { return OFF(tr_armed); }
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
    ("pc5_tss64_check_loaded", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
    ("pc5_tss64_probe_verdict", ctypes.c_int,
     [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]),
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
                 "cpl", "gdtr_base", "frames_taken", "built", "installed", "gdtr_limit",
                 "desc_base", "desc_limit", "desc_type_before", "desc_type_after",
                 "tr_selector", "tr_armed"):
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


def built_tss(lib):
    """Run the real builder against the real pool and return (body, ist1_top, ist2_top).

    Split out so that a *mutant* of the builder can be asked the same question the shipped
    builder is asked: the assertions that follow read the bytes at the offsets the architecture
    names, which a check routed through `pc5_tss64_check_loaded` cannot do -- that function reads
    through the very offset table under test, so a builder and a checker that move together stay
    perfectly consistent and report `ok`.  That agreement is how the wrong IST1 offset survived
    three milestones of green tests.
    """
    pool = Pool(lib)
    state = Reader(lib)
    if not lib.pc5_tss64_build(pool.cast, state.buffer):
        raise AssertionError(f"the builder refused the pool: {reason_of(lib, state)}")
    body = bytes(ram_read(state.word("tss_base"), TSS_SIZE))
    return (body, state.word("ist_top", 1), state.word("ist_top", 2))


def reason_of(lib, state):
    return lib.pc5_tss64_reason(state.buffer).decode()


class Reader:
    """Typed reads out of a `struct pc5_tss64` buffer, by the header's own offsets.

    `poke` is here because half of what the activation checks are about is *state the kernel
    cannot reach on the host*: GDTR is written by LGDT, and there is no ring 0 here.  A control
    that needs GDTR to name a different table therefore sets the two numbers LGDT would have
    loaded, and says so -- which is also why these are the host's half of the evidence and the
    stub plus the firmware gate are the executed half."""

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

    def poke(self, field, value, index=0, width=None):
        offset = getattr(self.lib, f"off_{field}")() + index * 8
        if width is None:
            width = self.WIDTH.get(field, 8)
        ctypes.memmove(ctypes.addressof(self.buffer) + offset,
                       int(value).to_bytes(width, "little"), width)


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
             state.word("selector") == 0x20, hex(state.word("selector")))

        raw = ram_read(tss, TSS_SIZE)
        got1 = int.from_bytes(raw[IST1_OFFSET:IST1_OFFSET + 8], "little")
        got2 = int.from_bytes(raw[IST1_OFFSET + 8:IST1_OFFSET + 16], "little")
        want("the TSS advertises the IST1 the report claims", got1 == top1,
             f"tss={got1:#x} report={top1:#x}")
        want("the TSS advertises the IST2 the report claims", got2 == top2,
             f"tss={got2:#x} report={top2:#x}")
        # Every field the kernel does not use must be zero *at its own architectural offset*.  The
        # reserved quadword at 28 is the one that matters: it is the slot the builder used to write
        # IST1 into, so "28..35 are zero" is the sentence that separates the two layouts instead of
        # accepting either.
        for name, offset, width in TSS_FIELDS:
            if name in ("ist1", "ist2", "iomap-base"):
                continue
            want(f"the {name} field of the TSS is zero",
                 raw[offset:offset + width] == bytes(width),
                 f"{name}@{offset} holds {raw[offset:offset + width].hex()}")
        want("no I/O permission map is claimed",
             int.from_bytes(raw[IOMAP_OFFSET:IOMAP_OFFSET + 2], "little") == TSS_SIZE,
             "the field is a word at 102, and 104 means past the limit")

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


# --- the activation checks: the descriptor, the TSS, and what TR has to agree with ---
#
# `pc5_tss64_check_loaded` is the half of TR arming that needs no instruction, so the host can
# drive it over doctored tables.  That is what makes these negative controls possible at all:
# every one of them is "the CPU would fault, and the kernel refuses first", and a control that
# could only be produced by faulting a real CPU would be a control for the environment rather
# than for the code.
PROBE_WORDS = 11
TSS_SELECTOR = 0x20          # slot 4, the first qword of the 16-byte descriptor
SCRATCH_AT = RAM_BASE + 120 * PAGE


def hand_installed(state):
    """The two numbers LGDT would have left in GDTR.

    Deliberately a copy of the builder's own table address rather than a fresh one: the point
    of the first control below is to show the check reads through GDTR, and that is only
    visible when the two can be told apart.
    """
    state.poke("installed", 1, width=4)
    state.poke("gdtr_base", state.word("gdt_base"))
    state.poke("gdtr_limit", state.word("gdt_limit"), width=2)


def arm_battery(lib):
    problems = []

    def want(label, condition, detail=""):
        if not condition:
            problems.append(f"{label}{': ' + detail if detail else ''}")

    def reason(state):
        return lib.pc5_tss64_reason(state.buffer).decode()

    pool = Pool(lib)
    state = Reader(lib)
    if not lib.pc5_tss64_build(pool.cast, state.buffer):
        problems.append("the builder refused the pool, so nothing here can be checked")
        return problems
    hand_installed(state)
    gdt = state.word("gdt_base")
    tss = state.word("tss_base")
    desc_at = gdt + TSS_SELECTOR

    def check(after=False):
        return lib.pc5_tss64_check_loaded(state.buffer, 1 if after else 0)

    def controls(label, *, expect, label_edit, after=False, detail=""):
        """Snapshot, break, expect the named refusal, restore.

        The snapshot is the part that matters: a battery that leaves one control's damage in
        place reports the same reason for every later label and reads as a passing table.
        """
        low = bytes(ram_read(desc_at, 16))
        body = bytes(ram_read(tss, TSS_SIZE))
        # The whole state, not just the memory: several controls poke a field (GDTR's limit,
        # the selector, the TSS address), and a snapshot of only the bytes the kernel writes
        # would leave a poked field behind -- which is how one control's damage was reported as
        # the *next* one's refusal, and the battery would then be measuring its own leftovers.
        saved = bytes(bytearray(state.buffer))
        label_edit()
        ok = check(after)
        got = reason(state)
        want(f"{label} is refused as {expect}", ok == 0 and got == expect,
             f"returned {ok} with {got!r}{(' ' + detail) if detail else ''}")
        ram_write(desc_at, low)
        ram_write(tss, body)
        ctypes.memmove(state.buffer, saved, len(saved))

    # The undamaged case, both sides of the instruction.
    want("an available descriptor passes the pre-load check", check() == 1, reason(state))
    want("and the post-load check accepts it unchanged, because a CPU that does not mark the"
         " descriptor busy is a different fact from one that refused to load it",
         check(after=True) == 1, reason(state))
    want("so the decoded descriptor names the granted frame",
         state.word("desc_base") == tss and state.word("desc_limit") == TSS_SIZE - 1,
         f"{state.word('desc_base'):#x}/{state.word('desc_limit')}")

    # A busy TSS is not loadable a second time: #TS on a real CPU, refused here first.
    def label_edit():
        ram_write(desc_at + 5, bytes([0x8B]))
    controls("a descriptor already marked busy", expect="check-descriptor-type",
             label_edit=label_edit)

    # After the load the tolerated set is exactly {busy, available}, and `available` is in it for
    # one reason only: TCG does not mark a TSS descriptor busy, so a host that leaves it alone is a
    # different fact from a descriptor that changed into something else.  Nothing here pinned that
    # boundary -- loosening the comparison to `type <= AVAIL` left all twenty checks green, which
    # is how a post-load check that accepts a 16-bit code segment as a loaded TSS would have
    # shipped.  The control is the narrow one: a type that is neither of the two.
    def label_edit():
        ram_write(desc_at + 5, bytes([0x83]))         # P set, type 3: neither busy nor available
    controls("a descriptor of some other type after the load",
             expect="check-descriptor-type-after", label_edit=label_edit, after=True)

    def label_edit():
        ram_write(desc_at + 5, bytes([0x09]))         # P cleared
    controls("a descriptor that is not present", expect="check-descriptor-not-present",
             label_edit=label_edit)

    def label_edit():
        ram_write(desc_at + 5, bytes([0x99]))         # S set: a code/data descriptor
    controls("a descriptor with the system bit cleared", expect="check-descriptor-is-code-or-data",
             label_edit=label_edit)

    def label_edit():
        ram_write(desc_at + 5, bytes([0xE9]))         # DPL 3
    controls("a ring-3 TSS descriptor", expect="check-descriptor-dpl", label_edit=label_edit)

    def label_edit():
        ram_write(desc_at + 6, bytes([0x00]))          # L cleared: a 32-bit TSS
    controls("a descriptor without the 64-bit TSS flag", expect="check-descriptor-l-bit",
             label_edit=label_edit)

    def label_edit():
        ram_write(desc_at + 6, bytes([0xA0]))          # G set: byte limit would be scaled
    controls("a descriptor with 4 KiB granularity", expect="check-descriptor-granularity",
             label_edit=label_edit)

    def label_edit():
        ram_write(desc_at + 2, bytes([0x00, 0x20, 0x00]))   # base[23:0] -> 0x2000...
    controls("a descriptor whose base is not the granted frame",
             expect="check-descriptor-base", label_edit=label_edit)

    def label_edit():
        ram_write(desc_at, bytes([0xFF, 0xFF]))        # limit 65535
    controls("a descriptor with the wrong limit", expect="check-descriptor-limit",
             label_edit=label_edit)

    def label_edit():
        ram_write(desc_at, bytes([0x67, 0x00]))        # limit 103 is what SIZE-1 encodes
        ram_write(desc_at + 6, bytes([0x00]))
    # (limit 0x67 with L and G clear is the *correct* pair; this only re-checks the encoder,
    # so it must PASS -- a control that refuses a good value is as useless as one that accepts
    # a bad one, and the reason this one is here is that it was the shape of the bug that made
    # the selector wrong for three milestones.)
    low = bytes(ram_read(desc_at, 16))
    want("limit 103 with G clear is the accepted encoding", check() == 1, reason(state))
    ram_write(desc_at, low)

    # The TSS's own contents, reached through the descriptor rather than through the builder.
    def label_edit():
        ram_write(tss + 32, bytes(8))                  # IST1 -> 0
    controls("an unwritten IST1 field", expect="check-ist1-field", label_edit=label_edit)

    def label_edit():
        ram_write(tss + 40, bytes(8))                  # IST2 -> 0
    controls("an unwritten IST2 field", expect="check-ist2-field", label_edit=label_edit)

    def label_edit():
        ram_write(tss + 100, bytes(4))                 # I/O-map base -> 0, inside the limit
    controls("an I/O-map base of zero", expect="check-iomap-field", label_edit=label_edit)

    def label_edit():
        ram_write(tss + 100, (TSS_SIZE - 8).to_bytes(4, "little"))
    controls("an I/O-map base inside the TSS", expect="check-iomap-field", label_edit=label_edit)

    def label_edit():
        ram_write(tss + 4, b"\x01" + bytes(7))         # RSP0 -> 1
    controls("a privilege stack pointer that is not zero", expect="check-rsp-fields",
             label_edit=label_edit)

    # Selector-level refusals.  The constant check comes first, so a selector that is not the
    # one this kernel builds -- including one with the table bit set -- is refused as
    # unexpected rather than out-of-range: which of the two fires is a property of the order,
    # and the order is asserted here rather than inferred from a comment.
    def label_edit():
        state.poke("selector", 0x23, width=2)          # RPL 3
    controls("a selector at the wrong privilege level", expect="check-selector-rpl",
             label_edit=label_edit)

    def label_edit():
        state.poke("selector", 0x28, width=2)          # the second half of the pair: the bug
    controls("the second half of the descriptor pair as a selector",
             expect="check-selector-unexpected", label_edit=label_edit)

    def label_edit():
        state.poke("selector", 0x24, width=2)          # table bit set: an LDT selector
    controls("an LDT selector", expect="check-selector-table-bit", label_edit=label_edit)

    def label_edit():
        state.poke("tss_base", tss + 4)
    controls("a TSS that is not 8-byte aligned", expect="check-tss-unaligned",
             label_edit=label_edit)

    def label_edit():
        state.poke("gdtr_limit", 31, width=2)          # table ends before the descriptor pair
    controls("a GDTR limit that does not cover the descriptor",
             expect="check-descriptor-outside-gdt", label_edit=label_edit)

    # The discriminating one: the table the CPU reads is GDTR's, not the one the builder kept.
    # Zero the descriptor in a *copy*, point GDTR at the copy, and the good descriptor still
    # readable through `pc5_tss64_gdt_word(4)` must not save it.
    table = bytes(ram_read(gdt, 48))
    ram_write(SCRATCH_AT, table[:TSS_SELECTOR] + bytes(16))
    low = bytes(ram_read(desc_at, 16))
    body = bytes(ram_read(tss, TSS_SIZE))
    state.poke("gdtr_base", SCRATCH_AT)
    ok = lib.pc5_tss64_check_loaded(state.buffer, 0)
    want("a descriptor the builder wrote but GDTR does not name is refused",
         ok == 0 and reason(state) == "check-descriptor-not-present", reason(state))
    want("...while the builder's own copy still looks perfect, which is why this test exists",
         lib.pc5_tss64_gdt_word(4) != 0)
    state.poke("gdtr_base", gdt)
    ram_write(SCRATCH_AT, bytes(48))
    return problems


class Tss64ActivationTests(unittest.TestCase):
    """`pc5_tss64_check_loaded`, driven over every way the table or the TSS can disagree."""

    def test_the_battery_passes_against_the_real_source(self):
        self.assertEqual(arm_battery(build()), [], "the activation checks disagreed")

    def test_the_selector_is_the_first_qword_of_the_descriptor(self):
        """0x20, not 0x28 -- and the reason this is a test rather than a comment.

        A GDT selector is the descriptor's byte offset divided by 8, so a 16-byte descriptor
        that begins at byte 32 is named by 0x20.  This kernel said 0x28, naming the descriptor's
        *high half*, whose first bytes are base[63:32] and which therefore reads as a
        not-present descriptor of type 0.  Nothing noticed, because nothing executed LTR: the
        builder and its own decoder agreed with each other, the way a pair of arithmetic
        mistakes agrees.  The constant is asserted here against the layout the GDT array
        actually has, and `arm_battery` above refuses the old value by name.
        """
        text = TSS_SOURCE.read_text(encoding="utf-8")
        slot = text.index("g_gdt[4] = tss_desc_build_low")
        self.assertIn("PC5_TSS64_SELECTOR 0x20u",
                      (ROOT / "kernel64" / "tss64.h").read_text(encoding="utf-8"),
                      "the selector no longer names the low half of the pair built at g_gdt[4]")
        self.assertLess(text.index("g_gdt[4]"), text.index("g_gdt[5]"),
                        "the pair is written high-first, so the selector would be 0x28")
        lib = build()
        pool = Pool(lib)
        state = Reader(lib)
        self.assertEqual(lib.pc5_tss64_build(pool.cast, state.buffer), 1)
        self.assertEqual(state.word("selector"), TSS_SELECTOR)
        # And the pair the selector names: byte 32 and byte 40 of the table.
        self.assertEqual(TSS_SELECTOR, 32 // 8 * 8,
                         "a selector is an offset divided by 8, times 8")

    def test_tr_armed_stays_zero_until_the_install_has_happened(self):
        """Arming before LGDT is refused, so TR can never be loaded from a table the CPU
        is not using -- and `report_tss`'s gate rule reads that flag and nothing else."""
        lib = build()
        pool = Pool(lib)
        state = Reader(lib)
        self.assertEqual(lib.pc5_tss64_build(pool.cast, state.buffer), 1)
        self.assertEqual(state.word("installed"), 0)
        self.assertEqual(state.word("tr_armed", width=4), 0)


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


class Tss64ProbeTests(unittest.TestCase):
    """The delivery probe's own judgements, driven on the host where no CPU is required.

    `pc5_tss64_probe_verdict` is the function that decides whether a #DF was delivered on the
    interrupt stack, and it is pure arithmetic over a filled-in struct -- which means every one of
    its refusals can be produced here by handing it a frame that *looks* right in one way and wrong
    in another.  The executed half (does the CPU really switch) is the firmware gate's row
    `a #DF taken by the CPU was delivered on the IST1 stack`, and the two halves are written to
    fail for different reasons on purpose: the host refuses fabricated verdicts, the gate refuses
    an unverified claim.
    """

    #: the shape a good delivery has, taken from the numbers a real boot printed rather than from
    #: an idealisation -- `rsp` 40 bytes below the IST top, the resume address as the first word of
    #: the frame, CS and SS as QEMU leaves them
    GOOD = {
        "rsp_before": 0xE1F9C70,
        "resume": 0x10245C,
        "gate_ist": 1,
        "rsp_delivery": 0x2FD8,
        "ist_top": 0x3000,
        "flags_after": 0x10002,
        "frame": (0x10245C, 0x38, 0x2, 0xE1F9C70, 0x30),
    }

    def verdict(self, lib, **overrides):
        state = Reader(lib)
        state.poke("built", 1)
        state.poke("installed", 1)
        state.poke("tr_armed", 1, width=4)
        values = dict(self.GOOD)
        values.update(overrides)
        # Built by *slot number*, in the order the struct declares them, so this fixture is a
        # second description of the layout rather than a reader of the first: if a field moves in
        # tss64.h and the assembly is not updated with it, the `_Static_assert`s in tss64.c catch
        # that pair, and a verdict computed from the wrong words shows up here as a refusal.
        probe = (ctypes.c_ulonglong * PROBE_WORDS)(
            values["rsp_before"], values["resume"], values["gate_ist"],
            values["rsp_delivery"], values["ist_top"], values["flags_after"],
            *values["frame"])
        got = ctypes.c_char_p()
        ok = lib.pc5_tss64_probe_verdict(state.buffer, probe, ctypes.byref(got))
        return bool(ok), (got.value or b"").decode()

    def test_a_delivery_on_the_interrupt_stack_is_accepted(self):
        lib = build()
        ok, reason = self.verdict(lib)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_every_way_a_delivery_can_be_wrong_has_its_own_name(self):
        """One field at a time, each refusal reached by damaging exactly one fact.

        `probe-stack-unchanged` is the one that matters most: a CPU that ignores the gate's IST
        field pushes the frame onto the stack that just overflowed, and every other number on the
        line still reads `ok`.  `probe-frame-rip-missing` is the one that keeps a *fabricated*
        delivery honest: a stack pointer alone is a number, and the frame is the evidence.
        """
        lib = build()
        for label, expect, overrides in (
            ("no IST slot was ever granted", "probe-ist-top-missing", {"ist_top": 0}),
            ("the handler never ran", "probe-not-delivered", {"rsp_delivery": 0}),
            ("the fault stayed on its own stack", "probe-stack-unchanged",
             {"rsp_delivery": self.GOOD["rsp_before"]}),
            ("the frame is outside the granted page", "probe-frame-outside-ist-page",
             {"rsp_delivery": 0x9FD8}),
            ("the frame holds no return address", "probe-frame-rip-missing",
             {"frame": (0xDEADBEEF, 0x38, 0x2, self.GOOD["rsp_before"], 0x30)}),
            ("the gate names a different slot", "probe-gate-ist-not-one", {"gate_ist": 2}),
            ("DF was left set", "probe-df-flag-left-set",
             {"flags_after": self.GOOD["flags_after"] | 0x400}),
        ):
            ok, reason = self.verdict(lib, **overrides)
            self.assertFalse(ok, f"{label}: the probe accepted it")
            self.assertEqual(reason, expect, f"{label}: refused as {reason}")

    def test_the_frame_is_found_wherever_the_cpu_put_it(self):
        """The saved RIP may be any of the frame's words, and the verdict must not care.

        Which quadword a CPU pushes the return address into depends on whether an error code went
        first and on whether SS and RSP were saved with it -- a soft `int $8` and a real double
        fault do not agree, and neither is the thing under test.  Pinning the *search* rather than
        an offset is what lets this probe run on a machine that lays the frame out differently,
        without letting it pass on a frame that contains nothing at all.
        """
        lib = build()
        for index in range(5):
            frame = [0] * 5
            frame[index] = self.GOOD["resume"]
            ok, reason = self.verdict(lib, frame=tuple(frame))
            self.assertTrue(ok, f"resume at frame[{index}]: {reason}")

    def test_the_state_is_read_and_not_written(self):
        """A verdict about a probe must not change what the TSS claims.

        The refusal names live in the caller's variable, not in `state->reason`, and that is the
        contract that lets `report_tss` print `reason=` for the install and a separate `reason=`
        for the probe on the next line.  A probe that wrote into the state would let a failed
        measurement quietly rewrite the boot's own verdict.
        """
        lib = build()
        state = Reader(lib)
        state.poke("built", 1)
        state.poke("tr_armed", 1, width=4)
        # `reason` is left exactly as a fresh state has it (a null pointer): the buffer is
        # zeroed by `Reader`, and a verdict that wrote a refusal into the *state* instead of the
        # caller's out-parameter would show up as a difference below.
        before = bytes(state.buffer)
        probe = (ctypes.c_ulonglong * PROBE_WORDS)(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        got = ctypes.c_char_p()
        ok = lib.pc5_tss64_probe_verdict(state.buffer, probe, ctypes.byref(got))
        self.assertEqual(ok, 0, "an all-zero probe was accepted")
        self.assertEqual(bytes(state.buffer), before,
                         "the verdict wrote into the state it was handed")

class Tss64ActivationMutationTests(unittest.TestCase):
    """The activation battery has to notice the checks being removed, one `if` at a time."""

    def assert_caught(self, lib, label):
        problems = arm_battery(lib)
        if problems:
            return
        self.fail(f"{label}: the activation battery passed against the mutant, so the property "
                  "it is meant to enforce is not enforced by the code")

    def test_widening_the_after_load_type_tolerance_is_caught(self):
        """Accept any type up to `available` after `ltr` and the battery must notice.

        The shipped rule tolerates one extra type after the load, for the TCG reason above.  A
        comparison written as `type <= PC5_DESC_TYPE_TSS_AVAIL` reads like a harmless slip and turns
        the post-load check into a no-op for the very field `ltr` is supposed to have set; before
        the control existed, this mutant survived every check in this file.
        """
        self.assert_caught(mutate(
            "        && !(after_load && type == PC5_DESC_TYPE_TSS_AVAIL)) {",
            "        && !(after_load && type <= PC5_DESC_TYPE_TSS_AVAIL)) {",
            "after-load-type-widened"), "the after-load descriptor type")

    def test_losing_the_bounds_test_on_the_table_is_caught(self):
        """A bound so wide it never trips, written so the parameter stays used.

        `if (0)` would not compile here -- the host build carries -Werror=unused-parameter, and
        the argument exists only for this test -- which is the right outcome for a mutant, but
        for the wrong reason.  Relaxing the comparison instead proves the *check* is what the
        control depends on.
        """
        self.assert_caught(mutate(
            "    if (byte_off + 16ull > gdtr_limit + 1ull)   /* a 16-byte descriptor has to fit */",
            "    if (byte_off + 16ull > gdtr_limit + 0xFFFFull)   /* a 16-byte descriptor has to fit */",
            "gdtr-bounds-removed"), "a GDTR limit that cuts the descriptor in half")

    def test_accepting_a_busy_descriptor_before_loading_is_caught(self):
        """Say the acceptable pre-load type is "busy" and the good table stops passing.

        The constant is the mutation's subject rather than an incidental: which type a
        *fresh* descriptor must carry is exactly the fact the check encodes, and a kernel that
        demanded 11 before loading would refuse its own GDT and then load nothing."""
        self.assert_caught(mutate("#define PC5_DESC_TYPE_TSS_AVAIL 9ull",
                                  "#define PC5_DESC_TYPE_TSS_AVAIL 11ull",
                                  "avail-type-is-busy"),
                           "a descriptor whose type is already busy before the load")

    def test_trusting_the_builder_about_the_iomap_field_is_caught(self):
        self.assert_caught(mutate(
            "    if (tss_word(base, tss_iomap_offset()) != PC5_TSS64_SIZE) {",
            "    if (0) {",
            "iomap-check-removed"), "an I/O-map base that was never written")

    def test_reading_the_iomap_field_four_bytes_wide_is_caught(self):
        """A dword at 100 finds `0x00680000`, not 104, and the check has to say so.

        The I/O-map base is a *word* at 102: reading four bytes from 100 picks up the two reserved
        bytes in front of it and compares a number no CPU would ever use.  Before the layout was
        corrected, both the write and the read were dwords at 100, so they agreed with each other
        and the field's width was never tested by anything.
        """
        lib = build()
        body, _, _ = built_tss(lib)
        self.assertEqual(int.from_bytes(body[102:104], "little"), TSS_SIZE,
                         "the shipped builder no longer writes the word at 102, so this test has "
                         "stopped meaning what it says")
        mutant = mutate("    return 104ull - 2ull;", "    return 104ull - 4ull;",
                        "iomap-offset-is-dword")
        bad, _, _ = built_tss(mutant)
        self.assertEqual(int.from_bytes(bad[100:104], "little"), TSS_SIZE,
                         "the mutant did not move the field, so it proves nothing")
        self.assertNotEqual(int.from_bytes(bad[102:104], "little"), TSS_SIZE,
                            "a builder that writes 104 at offset 100 still leaves 104 at 102: "
                            "the decode is not independent of the builder")

    def test_the_ist_slots_are_checked_against_the_architecture_not_the_builder(self):
        """Move IST1 back to offset 32 and only the independent decode notices.

        This is the regression test for the bug the delivery probe found, and it is written to
        make the point rather than to assert the number: the mutant is *self-consistent* -- the
        builder writes at 32 and `pc5_tss64_check_loaded` reads through the same helper, so the
        activation battery above keeps passing.  What fails is a test that reads the bytes at the
        offset the SDM names, which is the only kind of check that could have caught this while the
        kernel still believed otherwise.
        """
        lib = build()
        body, top1, top2 = built_tss(lib)
        self.assertEqual(int.from_bytes(body[36:44], "little"), top1,
                         "the shipped builder no longer puts IST1 at the architectural offset, "
                         "so this test has stopped meaning what it says")
        mutant = mutate("    return 36ull + (index - 1ull) * 8ull;",
                        "    return 32ull + (index - 1ull) * 8ull;",
                        "ist1-at-32")
        bad_body, bad_top1, bad_top2 = built_tss(mutant)
        self.assertEqual(int.from_bytes(bad_body[32:40], "little"), bad_top1,
                         "the mutant did not move IST1, so it proves nothing")
        self.assertNotEqual(int.from_bytes(bad_body[36:44], "little"), bad_top1,
                            "a TSS whose IST1 sits at offset 32 still reads correctly at 36: "
                            "the decode is not independent of the builder")
        # And what a CPU would have used, asserted as a value rather than as a difference: the
        # high half of IST1 (zero, for a stack below 4 GiB) welded to the low half of IST2, which
        # for the pair this kernel writes is exactly `0x0000400000000000` -- a 47-bit address no
        # page table maps, and the number that produced the not-present write fault the probe was
        # written to catch.  Pinning the shape of the garbage is what stops this test passing for
        # a mutant that merely leaves the slot empty.
        garbage = int.from_bytes(bad_body[36:44], "little")
        self.assertEqual(garbage & 0xFFFFFFFF, 0, "the low half should be IST1's zero extension")
        self.assertEqual(garbage >> 32, bad_top2 & 0xFFFFFFFF,
                         "the high half should be IST2's low half: %016x" % garbage)
        self.assertNotIn(garbage, (bad_top1, bad_top2),
                         "the garbage happens to name a real stack, which would make this the "
                         "wrong negative control")
        self.assertNotEqual(garbage, 0, "an empty slot is a different bug than a shifted one")

    def test_reading_the_builders_array_instead_of_gdtr_is_caught(self):
        """Replace the read-through-GDTR with the builder's own words and the copy control
        above stops distinguishing them -- which is the whole reason the read is written the
        way it is."""
        self.assert_caught(mutate(
            "    if (!gdt_descriptor_at(state->gdtr_base, state->gdtr_limit, selector, &low, &high)) {",
            "    if (!gdt_descriptor_at(state->gdt_base, state->gdt_limit, selector, &low, &high)) {",
            "reads-builder-array"), "a descriptor read from the wrong table")


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
