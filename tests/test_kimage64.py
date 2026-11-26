"""The 64-bit kernel image header: layout, refusal battery, and the tool that stamps it.

Three things are being held together here, and the test is written around the fact that each
of them could drift from the other two without any of them noticing:

  * `kernel64/include/pc5_kimage64.h` documents the byte layout,
  * `kernel64/kimage64_head.c` emits the statically-known half of it,
  * `boot/uefi64/kimage64.c` decides at boot whether an image may be jumped into,
  * `tools/kimage64.py` stamps the dynamic half and is what `make verify` and the firmware
    gate read the medium with.

So the first class below pins the offsets in all four places.  The second is the substance:
it compiles the *loader's own* validator on the host and feeds it every malformed header the
brief asks about -- bad magic, wrong version, wrong architecture, truncation, a corrupted
signature, an out-of-range entry point, a declared size that is not the real one, an
unaligned entry, a `.bss` bigger than the file, a dirty reserved field -- plus the arithmetic
cases a hostile or merely broken file can produce, where a loader that computes before it
checks has already wrapped.  Each case asserts the *name* of the refusal, because "it
refused" is satisfied by refusing for the wrong reason, and a control that refuses for the
wrong reason is a control that has already been broken by a refactor.

The C implementation and the Python one are cross-run over the same corpus: they must agree
on every input, so `make verify`'s verdict and the loader's verdict cannot describe
different rules.

`unicorn` is not needed here; `make stubtest64` and `tests/test_boot64_stub.py` are what
execute the same validator inside a booted loader.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "kernel64" / "include" / "pc5_kimage64.h"
VALIDATOR = ROOT / "boot" / "uefi64" / "kimage64.c"
EMITTER = ROOT / "kernel64" / "kimage64_head.c"
LINKER_SCRIPT = ROOT / "kernel64" / "kernel64.ld"
MAKEFILE = ROOT / "Makefile"
TOOL = ROOT / "tools" / "kimage64.py"

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import kimage64  # noqa: E402

HEADER_SIZE = 128
LINK_BASE = 0x100000
FMT = "<8sIIQQQIIIIQQIII"

_PROBE = r"""
/* The loader's validator, host-side.  No firmware types beyond the typedefs, no services:
 * `kimage64.c` is compiled exactly as the release builds it, so a refusal seen here is a
 * refusal the loader would reach -- which is the only reason this file is a test of the
 * boot path rather than a test of a reimplementation of it. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "efi.h"
#include "pc5_kimage64.h"

int main(int argc, char **argv)
{
    struct pc5_kimage64 info;
    unsigned long long loaded_at, claimed;
    int header_only;
    const char *reason;
    unsigned char *data;
    long length;
    FILE *handle;

    if (argc != 3 && argc != 4 && argc != 5) {
        fprintf(stderr,
                "usage: probe <image> <loaded-at> [full|header] [size-the-caller-claims]\n");
        return 2;
    }
    handle = fopen(argv[1], "rb");
    if (!handle)
        return 2;
    fseek(handle, 0, SEEK_END);
    length = ftell(handle);
    fseek(handle, 0, SEEK_SET);
    data = malloc(length > 0 ? (size_t)length : 1u);
    if (!data || fread(data, 1, (size_t)length, handle) != (size_t)length)
        return 2;
    fclose(handle);
    loaded_at = strtoull(argv[2], 0, 0);
    /* Two entry points, one binary, so "the header half agrees with the full validator" is a
     * comparison between the two callers' code paths rather than between this file's prose.
     *
     * `claimed` defaults to the file length, which is what the loader passes (the bytes it read).
     * Overriding it is how the *kernel's* call is reproduced: it passes the `kernel_size` it was
     * handed, so the value under test is a claim rather than a measurement. */
    header_only = argc > 3 && strcmp(argv[3], "header") == 0;
    claimed = argc > 4 ? strtoull(argv[4], 0, 0) : (unsigned long long)length;
    reason = header_only
        ? pc5_kimage64_validate_header(data, (u64)claimed, (u64)loaded_at, &info)
        : pc5_kimage64_validate(data, (u64)claimed, (u64)loaded_at, &info);
    if (reason) {
        printf("refuse %s mode=%s\n", reason, header_only ? "header" : "full");
        return 1;
    }
    printf("accept mode=%s claimed=%llx entry=%llx entry_address=%llx image=%llx bss=%llx\n",
           header_only ? "header" : "full", claimed,
           (unsigned long long)info.entry_offset,
           (unsigned long long)info.entry_address,
           (unsigned long long)info.image_size,
           (unsigned long long)info.bss_size);
    return 0;
}
"""


def _c_int(text: str) -> int:
    """Read a C integer literal the way the header writes them (`128u`, `...ull`, `0x10`)."""
    return int(text.rstrip("uUlL"), 0)


def _offsets_from_header() -> dict:
    """The `#define PC5_KIMAGE64_OFF_*` table, read from the format header itself."""
    table = {}
    for line in HEADER.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1].startswith("PC5_KIMAGE64_OFF_"):
            table[parts[1][len("PC5_KIMAGE64_OFF_"):]] = _c_int(parts[2])
    return table


def _make_image(size: int = HEADER_SIZE + 4096, entry: int = 4096, bss: int = 2048) -> bytes:
    """A valid image of the given shape, stamped by the tool that ships.

    Built through `kimage64.stamp`'s field writes rather than by hand, so the corpus the C
    and Python validators are compared over is made of images one of them could actually have
    produced -- and the independent oracle below (`test_the_checksums_have_an_independent_
    implementation`) is what notices if the *tool's* idea of a checksum is wrong.
    """
    data = bytearray(size)
    struct.pack_into(FMT, data, 0, b"PC5K64IM", 1, HEADER_SIZE, size, LINK_BASE, entry,
                     16, 4096, 1, 62, bss, 0, 0, 0, 0)
    for i in range(HEADER_SIZE, size):
        data[i] = (i * 37 + 11) & 0xFF
    kimage64.set_field(data, "checksum", kimage64.payload_checksum(bytes(data)))
    kimage64.set_field(data, "header_crc", kimage64.header_crc(bytes(data)))
    return bytes(data)


class KImage64Layout(unittest.TestCase):
    """The four descriptions of the same bytes have to be one description."""

    def test_the_documented_offsets_are_the_fields_in_order(self):
        offsets = _offsets_from_header()
        # `reserved2` is a *run* of bytes rather than a scalar field, so the tool carries it
        # as a (offset, length) pair instead of a struct format -- compared separately below
        # rather than excluded silently, because "excluded" is how a field goes unwatched.
        self.assertEqual(sorted(k.lower() for k in offsets if k != "RESERVED2"), sorted(
            name.lower() for name, _o, _f in kimage64.FIELDS),
            "the C header and tools/kimage64.py disagree about which fields exist")
        self.assertEqual(offsets["RESERVED2"], kimage64.RESERVED2[0])
        self.assertEqual(HEADER.read_text(encoding="utf-8").count(
            "PC5_KIMAGE64_RESERVED2_SIZE 44u"), 1)
        sizes = {"MAGIC": 8, "HEADER_VERSION": 4, "HEADER_SIZE": 4, "IMAGE_SIZE": 8,
                 "LOAD_BASE": 8, "ENTRY_OFFSET": 8, "ENTRY_ALIGN": 4, "LOAD_ALIGN": 4,
                 "ARCH": 4, "MACHINE": 4, "BSS_SIZE": 8, "CHECKSUM": 8, "RESERVED0": 4,
                 "RESERVED1": 4, "HEADER_CRC": 4}
        previous_end = 0
        for name, offset, fmt in kimage64.FIELDS:
            key = name.upper()
            self.assertEqual(offset, offsets[key],
                             "%s: the tool reads it at %d, the header says %d"
                             % (name, offset, offsets[key]))
            self.assertEqual(offset, previous_end,
                             "%s is not where the field before it ends -- so either a field "
                             "is unaccounted for or two overlap" % name)
            previous_end = offset + struct.calcsize(fmt)
            self.assertLessEqual(previous_end, HEADER_SIZE)
        self.assertEqual(offsets["RESERVED2"] + kimage64.RESERVED2[1], HEADER_SIZE,
                         "the reserved tail does not run to the header's declared size")
        self.assertEqual(sizes["MAGIC"], 8)

    def test_the_emitter_and_the_linker_script_agree_with_the_format(self):
        text = EMITTER.read_text(encoding="utf-8")
        self.assertIn("section(\".head\")", text)
        self.assertIn("PC5_KIMAGE64_ASSERT(magic, PC5_KIMAGE64_OFF_MAGIC)", text)
        ld = LINKER_SCRIPT.read_text(encoding="utf-8")
        self.assertLess(ld.index(".head :"), ld.index(".text :"),
                        "the header is not placed before the code it describes")
        self.assertIn("KEEP(*(SORT_BY_ALIGNMENT(.head*)))", ld,
                      ".head is not KEEP'd: a section nothing references is a section that "
                      "can be garbage-collected out of the image")
        makefile = MAKEFILE.read_text(encoding="utf-8")
        self.assertIn("tools/kimage64.py --stamp", makefile,
                      "the build does not stamp the image, so the dynamic fields are zero "
                      "and every boot would be refused")
        self.assertIn("kernel64/kimage64_head.c", makefile.split("KERNEL64_SRC :=", 1)[1]
                      .split("\n\n", 1)[0], "the header object is not linked into the kernel")

    def test_the_tool_and_the_header_agree_on_the_constants(self):
        text = HEADER.read_text(encoding="utf-8")

        def constant(name):
            for line in text.splitlines():
                parts = line.split()
                if len(parts) == 3 and parts[0] == "#define" and parts[1] == name:
                    return parts[2]
            raise AssertionError("%s is not defined in %s" % (name, HEADER))

        self.assertEqual(_c_int(constant("PC5_KIMAGE64_VERSION")), kimage64.VERSION)
        self.assertEqual(_c_int(constant("PC5_KIMAGE64_HEADER_SIZE")), HEADER_SIZE)
        self.assertEqual(constant("PC5_KIMAGE64_MAGIC"), '"PC5K64IM"')
        self.assertEqual(kimage64.MAGIC, b"PC5K64IM")
        self.assertEqual(_c_int(constant("PC5_KIMAGE64_ARCH_X86_64")), kimage64.ARCH_X86_64)
        self.assertEqual(_c_int(constant("PC5_KIMAGE64_MACHINE_EM_X86_64")),
                         kimage64.MACHINE_EM_X86_64)
        self.assertEqual(_c_int(constant("PC5_FNV64_BASIS")), kimage64.FNV64_BASIS)
        self.assertEqual(_c_int(constant("PC5_FNV64_PRIME")), kimage64.FNV64_PRIME)
        self.assertEqual(_c_int(constant("PC5_FNV32_BASIS")), kimage64.FNV32_BASIS)
        self.assertEqual(_c_int(constant("PC5_KIMAGE64_ENTRY_ALIGN")), kimage64.ENTRY_ALIGN)
        self.assertEqual(_c_int(constant("PC5_KIMAGE64_LOAD_ALIGN")), kimage64.LOAD_ALIGN)

    def test_the_checksums_have_an_independent_implementation(self):
        """Recompute both digests from the format comment, not from the tool's own loop."""
        data = bytearray(_make_image())
        basis, prime, mask = 14695981039346656037, 1099511628211, (1 << 64) - 1
        h = basis
        for i in range(len(data)):
            byte = 0 if 64 <= i < 72 or 80 <= i < 84 else data[i]
            h = ((h ^ byte) * prime) & mask
        self.assertEqual(h, struct.unpack_from("<Q", data, 64)[0],
                         "the stamped payload checksum is not FNV-1a-64 over the file with "
                         "its own two fields zeroed")
        h32 = 2166136261
        for i in range(80):
            h32 = ((h32 ^ data[i]) * 16777619) & 0xFFFFFFFF
        self.assertEqual(h32, struct.unpack_from("<I", data, 80)[0])
        self.assertEqual(kimage64.payload_checksum(bytes(data)), h)
        self.assertEqual(kimage64.header_crc(bytes(data)), h32)


@unittest.skipUnless(shutil.which("gcc"), "the refusal battery compiles the loader's C")
class KImage64Refusals(unittest.TestCase):
    """Every malformed header, refused by the code that runs in the loader."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="pc5-kimage64-")
        src = os.path.join(cls.tmp.name, "probe.c")
        with open(src, "w", encoding="utf-8") as handle:
            handle.write(_PROBE)
        cls.binary = os.path.join(cls.tmp.name, "probe")
        built = subprocess.run(
            ["gcc", "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror",
             "-I", str(ROOT / "kernel64" / "include"), "-I", str(ROOT / "boot" / "uefi"),
             "-o", cls.binary, src, str(VALIDATOR)],
            capture_output=True, text=True, timeout=300)
        if built.returncode != 0:
            raise unittest.SkipTest("probe did not build: " + built.stderr[-1500:])
        cls.images = os.path.join(cls.tmp.name, "images")
        os.makedirs(cls.images)
        cls._n = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def c_verdict(self, data: bytes, loaded_at: int = LINK_BASE, mode: str = "full",
                  size: int | None = None) -> tuple:
        """(exit code, verdict) where verdict is `accept` or `refuse <reason>`.

        The detail after the verdict is returned separately, because a test that compares
        whole lines drifts the moment the reported fields are reordered, and the *refusal
        name* is the contract here -- not the spacing around it.
        """
        code, out = self.c_run(data, loaded_at, mode=mode, size=size)
        words = out.split()
        verdict = " ".join(words[:2]) if words and words[0] == "refuse" else "accept"
        return code, verdict, out

    def c_run(self, data: bytes, loaded_at: int = LINK_BASE, mode: str = "full",
              size: int | None = None) -> tuple:
        self._n += 1
        path = os.path.join(self.images, "image-%d.bin" % self._n)
        with open(path, "wb") as handle:
            handle.write(data)
        argv = [self.binary, path, hex(loaded_at)]
        if mode != "full" or size is not None:
            argv.append(mode)
        if size is not None:
            argv.append(hex(size))
        run = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        return run.returncode, run.stdout.strip()

    def assert_refused_by_c(self, data, reason, loaded_at=LINK_BASE, note="",
                            mode="full", size=None):
        code, verdict, out = self.c_verdict(data, loaded_at, mode=mode, size=size)
        self.assertEqual(code, 1, "the C validator accepted a %s image: %s %s"
                         % (reason, out, note))
        self.assertEqual(verdict, "refuse %s" % reason,
                          "expected the refusal to be named %r so a control can assert on "
                          "it%s; got %r" % (reason, note, out))
        return out

    # ------------------------------------------------------- the header half ----
    #
    # `pc5_kimage64_validate_header` exists because the running kernel cannot recompute the
    # payload: `.bss` is inside the checksummed range and `_start64` has already used part of it
    # as a stack by the time any C runs (see `RUNTIME USE` in the format header).  These four
    # tests are what stops that reasoning from being a comment: the header half has to refuse
    # every header mutation the full validator refuses, accept exactly the payload mutation it
    # cannot see, and treat the caller's size as a claim rather than as a measurement.

    #: The mutants a header-only validation cannot see.  One entry, and it is the payload.
    HEADER_BLIND = ("checksum",)

    def test_the_header_half_refuses_everything_the_full_validator_refuses_about_the_header(self):
        for kind, reason in sorted(kimage64.EXPECTED_REFUSAL.items()):
            if kind == "_no_op":
                continue
            with self.subTest(kind=kind):
                data = kimage64.mutate_bytes(_make_image(), kind)
                want = "accept" if kind in self.HEADER_BLIND else "refuse %s" % reason
                code, verdict, out = self.c_verdict(data, mode="header")
                if want == "accept":
                    self.assertEqual(code, 0, "%s must not be refused by the header half: %s"
                                     % (kind, out))
                else:
                    self.assertEqual(verdict, want,
                                     "the kernel re-checks the header with this code, so %s has "
                                     "to be refused *for that field* by the header half too, not "
                                     "by the payload step the kernel cannot run; got %r"
                                     % (kind, out))

    def test_the_header_half_accepts_a_payload_the_full_validator_refuses(self):
        """The boundary, stated as an assertion: a mutated body passes a header-only check.

        This is the case the kernel *cannot* catch, and the test exists so that the fact is
        pinned rather than remembered.  If `validate_header` ever starts refusing it, the split
        has moved and the format comment is out of date.
        """
        data = bytearray(_make_image())
        data[0x200] ^= 0xFF                 # inside .head's padding: never executed, in the sum
        kimage64.set_field(data, "header_crc", kimage64.header_crc(bytes(data)))
        self.assert_refused_by_c(bytes(data), "kimage64-checksum",
                                 note="the full validator must still see a mutated body")
        code, verdict, out = self.c_verdict(bytes(data), mode="header")
        self.assertEqual(code, 0,
                         "a header-only validator that somehow refuses a body edit is not "
                         "header-only: %s" % out)

    def test_the_claimed_size_is_compared_rather_than_trusted_by_both_halves(self):
        """`size` is the caller's *claim*, and both callers are checked against the header.

        The loader passes the bytes it read; the kernel passes `bootinfo.kernel_size`.  Feeding
        the same image with a claimed size that is one page short is how the second case is
        exercised without a loader that misreads -- and it must be refused *by the header half*,
        because that is the half the kernel runs.
        """
        data = _make_image()
        for mode in ("full", "header"):
            with self.subTest(mode=mode):
                self.assert_refused_by_c(data, "kimage64-image-size", mode=mode,
                                         size=len(data) - 4096,
                                         note=" under %s validation" % mode)
                code, out = self.c_run(data, mode=mode, size=len(data))
                self.assertEqual(code, 0, "the same image at its true length must accept: %s"
                                 % out)

    def test_the_kernel_and_the_loader_link_the_same_validator_object(self):
        """One implementation, two boot-time callers -- asserted, not trusted.

        §30 and §31 of `docs/TEST_RESULTS.md` are both stories about a builder and a checker that
        agreed with each other because they were the same code.  Sharing this validator is the
        *opposite* case and needs to stay that way: the kernel re-running the loader's rules is
        only evidence if they are literally the same rules.  So the link line is checked, and a
        Makefile that gave the kernel a second copy would fail here rather than in a boot log.
        """
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        kernel_link = makefile.split("$(BUILD)/kernel64/pc5-64.elf:", 1)[1].split("\n\t", 1)[0]
        self.assertIn("$(BUILD)/kernel64/kimage64.o", kernel_link,
                      "the kernel must link boot/uefi64/kimage64.o rather than carry its own "
                      "copy of the header rules")
        loader_link = makefile.split("$(BUILD)/uefi64/BOOTX64.EFI:", 1)[1].split("\n\n", 1)[0]
        self.assertIn("$(BUILD)/kernel64/kimage64.o", loader_link)
        self.assertIn("kernel64/include/pc5_kimage64.h", makefile.split("KERNEL64_HDR :=", 1)[1]
                      .split("\n\n", 1)[0],
                      "the format header is a dependency of every kernel64 object; dropping it "
                      "means a format edit can leave a stale object in build/")

    # ---------------------------------------------------------------- accept ----

    def test_a_valid_image_is_accepted_and_its_entry_is_reported(self):
        data = _make_image(entry=4096)
        code, verdict, out = self.c_verdict(data)
        self.assertEqual(code, 0, out)
        self.assertEqual(verdict, "accept")
        self.assertIn("entry=1000", out)
        self.assertIn("entry_address=101000", out,
                      "the loader jumps at entry_address, so it has to be the load address "
                      "plus the declared offset -- computed here, not by the caller")
        self.assertIn("image=%x" % len(data), out)

    def test_python_and_c_agree_over_the_whole_corpus(self):
        """`make verify` and the boot must not be able to describe different rules."""
        corpus = [_make_image(), _make_image(size=HEADER_SIZE), _make_image(entry=HEADER_SIZE)]
        for kind in sorted(kimage64.MUTATIONS):
            corpus.append(kimage64.mutate_bytes(_make_image(), kind))
        for mutation in (lambda d: struct.pack_into("<Q", d, 16, 1 << 63),
                         lambda d: struct.pack_into("<Q", d, 32, (1 << 64) - 8),
                         lambda d: struct.pack_into("<I", d, 40, 0),
                         lambda d: struct.pack_into("<I", d, 40, 3),
                         lambda d: struct.pack_into("<I", d, 44, 0),
                         lambda d: struct.pack_into("<Q", d, 56, 1 << 62),
                         lambda d: struct.pack_into("<I", d, 8, 0xFFFFFFFF),
                         lambda d: struct.pack_into("<I", d, 12, 1 << 30)):
            data = bytearray(_make_image())
            mutation(data)
            kimage64.set_field(data, "header_crc", kimage64.header_crc(bytes(data)))
            corpus.append(bytes(data))
        corpus.append(b"")
        corpus.append(b"\x00" * 127)
        corpus.append(b"\x00" * 128)
        for data in corpus:
            reason, _fields = kimage64.verify(data)
            expect = "accept" if reason is None else "refuse %s" % reason
            code, verdict, out = self.c_verdict(data)
            self.assertEqual(verdict, expect,
                             "the tool says %r about a corpus image and the loader's code "
                             "says %r" % (reason, out))
            self.assertEqual(code, 0 if reason is None else 1)

    # --------------------------------------------------------------- refusals ----

    def test_every_documented_mutation_is_refused_by_name_in_both_implementations(self):
        for kind in sorted(kimage64.MUTATIONS):
            with self.subTest(mutation=kind):
                data = kimage64.mutate_bytes(_make_image(), kind)
                reason, _fields = kimage64.verify(data)
                self.assertEqual(reason, kimage64.EXPECTED_REFUSAL[kind])
                self.assert_refused_by_c(data, reason)

    def test_short_files_truncate_before_any_field_is_read(self):
        for size in (0, 1, 7, 8, 127):
            with self.subTest(size=size):
                self.assert_refused_by_c(b"PC5K64IM" + b"\x00" * max(0, size - 8),
                                         "kimage64-truncated")

    def test_a_wide_load_address_does_not_make_the_entry_wrap(self):
        """The overflow case the format comment promises.

        `image_size` is huge, so `load_base + size` would wrap; the validator must refuse on
        a *named* property rather than report an `entry_address` that is smaller than the
        base it was computed from.  A loader that then jumped there would be jumping into the
        first megabyte, which is where firmware lives.
        """
        for declared in (0xFFFFFFFFFFFFFFFF, 1 << 63, (1 << 64) - 8):
            with self.subTest(declared=declared):
                data = bytearray(_make_image())
                struct.pack_into("<Q", data, 16, declared)
                kimage64.set_field(data, "header_crc", kimage64.header_crc(bytes(data)))
                # The declared length no longer matches the file, and that is checked first:
                # the point of this case is that the validator never reaches the addition.
                self.assert_refused_by_c(bytes(data), "kimage64-image-size")
        # The sum itself, at both ends of whether it fits.
        #
        # `top` is the address the image claims -- and is checked against -- to live at.  At
        # `2**64 - 4096` there is no room below it for a 4224-byte image, so an entry up
        # there must be refused *before* `top + entry` is ever formed; at `2**64 - 65536`
        # there is room, and the same arithmetic has to produce a real address rather than a
        # wrapped one.  Refusing in the first case and accepting in the second is what lets
        # the format comment say "nothing wraps" instead of merely hoping.
        for top, expect in (((1 << 64) - 4096, "refuse kimage64-load-base"),
                            ((1 << 64) - 65536, "accept")):
            with self.subTest(top=hex(top)):
                data = bytearray(_make_image())
                struct.pack_into("<Q", data, 24, top)
                struct.pack_into("<Q", data, 32, 2048)
                struct.pack_into("<Q", data, 56, 1024)
                # The stamper's order, for the stamper's reason: the CRC covers the bytes
                # the checksum is written into, so the checksum has to exist first.
                kimage64.set_field(data, "checksum", kimage64.payload_checksum(bytes(data)))
                kimage64.set_field(data, "header_crc", kimage64.header_crc(bytes(data)))
                code, verdict, out = self.c_verdict(bytes(data), top)
                self.assertEqual(verdict, expect, out)
                if expect == "accept":
                    self.assertIn("entry_address=%x" % (top + 2048), out,
                                  "the reported entry is not the base plus the offset")
                    self.assertIsNone(kimage64.verify(bytes(data), top)[0],
                                      "the tool and the loader's code disagree at the top of "
                                      "the address space")
                else:
                    self.assertEqual(kimage64.verify(bytes(data), top)[0],
                                     "kimage64-load-base")

    def test_the_load_base_must_equal_the_address_the_image_lives_at(self):
        data = _make_image()
        reason, _f = kimage64.verify(data, loaded_at=0x200000)
        self.assertEqual(reason, "kimage64-load-base")
        self.assert_refused_by_c(data, "kimage64-load-base", loaded_at=0x200000)

    def test_an_unaligned_load_address_is_refused_even_when_it_matches(self):
        data = bytearray(_make_image())
        struct.pack_into("<Q", data, 24, LINK_BASE + 8)
        kimage64.set_field(data, "header_crc", kimage64.header_crc(bytes(data)))
        self.assert_refused_by_c(bytes(data), "kimage64-load-base",
                                 note=" -- the base is what it is, so the alignment promise "
                                      "is checked against the address, not the field")

    def test_a_header_that_lies_about_only_the_checksum_is_still_caught(self):
        """The last check, and the only one that sees the payload.

        Everything above is about the 128-byte description.  This is the case that matters
        for a bit flip at offset 100 000: there is no signature out there, and the release
        control named "corrupted kernel" is this row.
        """
        # Big enough that the flipped byte is far from the header -- 100 KiB into the
        # payload, where nothing but the checksum reaches.
        data = bytearray(_make_image(size=HEADER_SIZE + 131072))
        data[100000] ^= 0x01
        self.assert_refused_by_c(bytes(data), "kimage64-checksum")

    def test_an_old_header_is_refused_rather_than_interpreted(self):
        data = bytearray(_make_image())
        struct.pack_into("<I", data, 8, 0)
        kimage64.set_field(data, "header_crc", kimage64.header_crc(bytes(data)))
        self.assert_refused_by_c(bytes(data), "kimage64-version")

    def test_a_reserved_field_a_later_version_would_use_is_refused_today(self):
        data = bytearray(_make_image())
        data[100] = 0x01
        kimage64.set_field(data, "header_crc", kimage64.header_crc(bytes(data)))
        self.assert_refused_by_c(bytes(data), "kimage64-reserved")


class KImage64Tool(unittest.TestCase):
    """The stamper and the reader the release path actually invokes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pc5-kimagetool-")
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_verify_reports_a_bad_image_on_stderr_and_exits_one(self):
        path = self.write("KERNEL64.BIN", kimage64.mutate_bytes(_make_image(), "arch"))
        run = subprocess.run([sys.executable, str(TOOL), "--bin", path],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("kimage64-arch", run.stderr)

    def test_json_output_carries_the_fields_a_verifier_needs(self):
        path = self.write("KERNEL64.BIN", _make_image())
        run = subprocess.run([sys.executable, str(TOOL), "--bin", path, "--verify",
                              "--json"], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        import json
        payload = json.loads(run.stdout)
        self.assertIsNone(payload["refusal"])
        self.assertEqual(payload["magic"], b"PC5K64IM".hex())
        self.assertEqual(payload["image_size"], HEADER_SIZE + 4096)
        self.assertEqual(payload["entry_offset"], 4096)

    def test_stamping_is_idempotent(self):
        """A second stamp must produce the same bytes, or `make` is not reproducible.

        The Makefile stamps unconditionally, so `make iso` twice would otherwise produce two
        ISOs with different digests from one commit -- and this repository records ISO
        digests as evidence, so a stamp that moves on every build would make every run
        contradict the last one.
        """
        # `stamp()` needs a linked ELF, so drive the field arithmetic it performs: an
        # image re-stamped from the same numbers has to come back byte-identical.
        once = _make_image()
        data = bytearray(once)
        kimage64.set_field(data, "checksum", 0)
        kimage64.set_field(data, "header_crc", 0)
        kimage64.set_field(data, "checksum", kimage64.payload_checksum(bytes(data)))
        kimage64.set_field(data, "header_crc", kimage64.header_crc(bytes(data)))
        self.assertEqual(bytes(data), once)

    def test_a_mutant_that_still_verifies_is_an_error_not_a_control(self):
        """`mutate_bytes` refuses to return a file the validator would accept."""
        def no_op(data):
            return data
        kimage64.MUTATIONS["_no_op"] = no_op
        kimage64.EXPECTED_REFUSAL["_no_op"] = "kimage64-magic"
        try:
            with self.assertRaises(SystemExit) as caught:
                kimage64.mutate_bytes(_make_image(), "_no_op")
            self.assertIn("would not be testing what it is named after",
                          str(caught.exception))
        finally:
            del kimage64.MUTATIONS["_no_op"]
            del kimage64.EXPECTED_REFUSAL["_no_op"]

    def test_the_stamper_refuses_to_write_a_header_it_cannot_re_read(self):
        source = (ROOT / "tools" / "kimage64.py").read_text(encoding="utf-8")
        block = source.split("def stamp(", 1)[1]
        self.assertIn('if reason:\n        raise SystemExit(', block,
                      "stamp() must validate what it wrote: a stamper that writes and "
                      "hopes is how a format bug reaches a release")


class KImage64ReleaseImage(unittest.TestCase):
    """The artifact the release actually ships, verified from the bytes on disk."""

    KERNEL = ROOT / "build" / "kernel64" / "KERNEL64.BIN"
    ELF = ROOT / "build" / "kernel64" / "pc5-64.elf"

    @classmethod
    def setUpClass(cls):
        if cls.KERNEL.exists() and cls.ELF.exists():
            return
        subprocess.run(["make", "-s", "kernel64"], cwd=str(ROOT), capture_output=True,
                       timeout=900)

    def test_the_shipped_kernel_image_verifies_against_its_own_header(self):
        if not self.KERNEL.exists():
            self.skipTest("no kernel image and no toolchain to build one")
        data = self.KERNEL.read_bytes()
        reason, fields = kimage64.verify(data)
        self.assertIsNone(reason, "the kernel this tree builds is refused by its own format: "
                                  "%s (%s)" % (reason, fields))
        self.assertEqual(fields["load_base"], LINK_BASE)
        self.assertEqual(fields["image_size"], len(data))
        self.assertEqual(fields["entry_offset"] % fields["entry_align"], 0)

    def test_the_declared_entry_is_where_the_code_really_starts(self):
        if not (self.KERNEL.exists() and self.ELF.exists()):
            self.skipTest("no kernel image to compare against")
        nm = subprocess.run(["nm", "-n", str(self.ELF)], capture_output=True, text=True,
                            timeout=120)
        self.assertEqual(nm.returncode, 0, nm.stderr)
        symbols = {}
        for line in nm.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3:
                symbols[parts[2]] = int(parts[0], 16)
        fields = kimage64.verify(self.KERNEL.read_bytes())[1]
        self.assertEqual(symbols["__kernel_start"] + fields["entry_offset"],
                         symbols["_start64"],
                         "the header points somewhere other than the kernel's entry point")
        self.assertLessEqual(symbols["__bss_end"] - symbols["__kernel_start"],
                             fields["image_size"],
                         "the image does not carry the .bss it claims to")


if __name__ == "__main__":
    unittest.main()
