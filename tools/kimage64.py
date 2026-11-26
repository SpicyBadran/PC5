#!/usr/bin/env python3
"""Stamp, verify, and deliberately corrupt PC5 64-bit kernel images.

`KERNEL64.BIN` is a flat, non-relocatable image that the loader jumps into at an address it
chose because a header told it to.  This file is the write side of that agreement, and the
host-side read side: the format's rules have to be checkable *about* an artifact, not only
from inside the artifact's own boot, or a release could ship an image whose header was never
stamped and nobody would notice until the boot refused it.

Three modes:

  --stamp --elf E --bin B     fill the header's dynamic fields (image size, entry offset,
                              .bss size, both checksums) in `B`, reading the addresses from
                              `E`'s symbols.  Refuses to write a header it cannot re-read.
  --verify B [--json]         re-check a file against its own header and print what it says.
                              Same field order, same reason names as the loader: a release
                              that verifies here and boots there is checked twice by code
                              that has been compared to each other (`tests/test_kimage64.py`
                              cross-runs the two over ~200 malformed inputs).
  --mutate FIELD --out C      write a copy of `B` with exactly one field broken, for the
                              negative controls.  One field, never two: a control that
                              corrupts the entry point *and* the length proves nothing about
                              either, because the loader could have refused for the other
                              reason and the test would still be green.

Why the offsets are literals here rather than generated from the C header: nothing in the
release path runs a compiler against the loader's sources, so the gate and this tool parse
the bytes directly.  The duplication is closed by `tests/test_kimage64.py`, which reads the
`#define PC5_KIMAGE64_OFF_*` values out of `kernel64/include/pc5_kimage64.h` and fails if
the table below disagrees, and which then runs both implementations over the same corpus and
requires the same reason for the same input.

Exit codes: 0 ok, 1 refused (with the reason), 2 usage or I/O error.
"""
from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys

MAGIC = b"PC5K64IM"
VERSION = 1
HEADER_SIZE = 128

#: (name, offset, struct format).  The order is the loader's order of checking, and it is
#: what makes a single-field mutant fail for its own field: `image_size` is checked before
#: `arch` precisely so that a control which breaks `arch` is not accidentally diagnosed as a
#: length problem.
FIELDS = (
    ("magic", 0, "8s"),
    ("header_version", 8, "<I"),
    ("header_size", 12, "<I"),
    ("image_size", 16, "<Q"),
    ("load_base", 24, "<Q"),
    ("entry_offset", 32, "<Q"),
    ("entry_align", 40, "<I"),
    ("load_align", 44, "<I"),
    ("arch", 48, "<I"),
    ("machine", 52, "<I"),
    ("bss_size", 56, "<Q"),
    ("checksum", 64, "<Q"),
    ("reserved0", 72, "<I"),
    ("reserved1", 76, "<I"),
    ("header_crc", 80, "<I"),
)
RESERVED2 = (84, 44)

ARCH_X86_64 = 1
MACHINE_EM_X86_64 = 62
ENTRY_ALIGN = 16
LOAD_ALIGN = 4096
LINK_BASE = 0x100000

FNV64_BASIS = 14695981039346656037
FNV64_PRIME = 1099511628211
FNV32_BASIS = 2166136261
FNV32_PRIME = 16777619
MASK64 = (1 << 64) - 1
MASK32 = (1 << 32) - 1

CHECKSUM_WINDOW = (64, 72)
HEADER_CRC_WINDOW = (80, 84)


def payload_checksum(data: bytes) -> int:
    """FNV-1a-64 over the whole file, with the two stamped fields treated as zero."""
    h = FNV64_BASIS
    for i, byte in enumerate(data):
        if CHECKSUM_WINDOW[0] <= i < CHECKSUM_WINDOW[1] or \
           HEADER_CRC_WINDOW[0] <= i < HEADER_CRC_WINDOW[1]:
            byte = 0
        h = ((h ^ byte) * FNV64_PRIME) & MASK64
    return h


def header_crc(data: bytes) -> int:
    """FNV-1a-32 over bytes [0, 80): the description, without either of its own fields."""
    h = FNV32_BASIS
    for byte in data[:HEADER_CRC_WINDOW[0]]:
        h = ((h ^ byte) * FNV32_PRIME) & MASK32
    return h


def field(data: bytes, name: str):
    for fname, offset, fmt in FIELDS:
        if fname == name:
            return struct.unpack_from(fmt, data, offset)[0]
    raise KeyError(name)


def set_field(data: bytearray, name: str, value) -> None:
    for fname, offset, fmt in FIELDS:
        if fname == name:
            struct.pack_into(fmt, data, offset, value)
            return
    raise KeyError(name)


def read_fields(data: bytes) -> dict:
    out = {}
    for name, _offset, fmt in FIELDS:
        value = field(data, name)
        out[name] = value
    out["magic"] = data[0:8]
    return out


def verify(data: bytes, loaded_at: int = LINK_BASE) -> tuple[str | None, dict]:
    """Return (reason, fields).  `reason` is None when the image is acceptable.

    The reason strings are the boot contract: `pc5_kimage64_validate` in
    `boot/uefi64/kimage64.c` names them, the loader prints `PC5-64-LOADER-FAIL=<reason>`,
    and the firmware gate asserts a mutant produces exactly the reason for the field it
    broke.  Changing one here without changing it there is a cross-checked test failure.
    """
    size = len(data)
    if size < HEADER_SIZE:
        return "kimage64-truncated", {"image_bytes": size}
    f = read_fields(data)
    if f["magic"] != MAGIC:
        return "kimage64-magic", f
    if f["header_version"] != VERSION:
        return "kimage64-version", f
    if f["header_size"] != HEADER_SIZE:
        return "kimage64-header-size", f
    if f["header_crc"] != header_crc(data):
        return "kimage64-header-crc", f
    if f["image_size"] != size:
        return "kimage64-image-size", f
    if f["load_base"] != loaded_at:
        return "kimage64-load-base", f
    if f["arch"] != ARCH_X86_64:
        return "kimage64-arch", f
    if f["machine"] != MACHINE_EM_X86_64:
        return "kimage64-machine", f
    if not _power_of_two(f["entry_align"]):
        return "kimage64-entry-align", f
    if not _power_of_two(f["load_align"]):
        return "kimage64-load-align", f
    if loaded_at % f["load_align"]:
        return "kimage64-load-align", f
    if f["bss_size"] > f["image_size"]:
        return "kimage64-bss-size", f
    if not (f["header_size"] <= f["entry_offset"] < f["image_size"]):
        return "kimage64-entry-range", f
    if f["entry_offset"] % f["entry_align"]:
        return "kimage64-entry-align", f
    if f["image_size"] > MASK64 - loaded_at:
        return "kimage64-load-base", f
    if f["reserved0"] or f["reserved1"] or any(
            data[RESERVED2[0] + i] for i in range(RESERVED2[1])):
        return "kimage64-reserved", f
    if f["checksum"] != payload_checksum(data):
        return "kimage64-checksum", f
    return None, f


def _power_of_two(value: int) -> bool:
    return value != 0 and (value & (value - 1)) == 0


def _symbols(elf: str) -> dict:
    out = subprocess.run(["nm", "-n", "--format=bsd", elf], capture_output=True, text=True,
                         timeout=120)
    if out.returncode != 0:
        raise SystemExit("kimage64: nm failed on %s: %s" % (elf, out.stderr.strip()))
    table = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            table[parts[2]] = int(parts[0], 16)
    return table


def stamp(elf: str, binary: str, load_base: int = LINK_BASE) -> dict:
    """Fill the dynamic fields from the ELF's symbols, then re-read what was written."""
    symbols = _symbols(elf)
    try:
        kernel_start = symbols["__kernel_start"]
        entry = symbols["_start64"]
        bss_start = symbols["__bss_start"]
        bss_end = symbols["__bss_end"]
    except KeyError as exc:
        raise SystemExit("kimage64: %s does not define %s, so the image header cannot be "
                         "stamped from it -- the linker script provides these four symbols"
                         % (elf, exc.args[0]))
    data = bytearray(open(binary, "rb").read())
    size = len(data)
    if size < HEADER_SIZE:
        raise SystemExit("kimage64: %s is %d bytes, shorter than the %d-byte header, so "
                         "objcopy dropped .head instead of writing it first"
                         % (binary, size, HEADER_SIZE))
    if entry < kernel_start or entry - kernel_start >= size:
        raise SystemExit("kimage64: _start64 (%#x) is not inside %s, which means the image "
                         "does not start where the linker script says it does"
                         % (entry, binary))
    if bss_end < bss_start or bss_end - kernel_start > size:
        raise SystemExit("kimage64: the declared .bss (%#x..%#x) is not inside the %d bytes "
                         "of file -- the kernel stack lives in .bss, so this image has no "
                         "stack" % (bss_start, bss_end, size))

    set_field(data, "magic", MAGIC)
    set_field(data, "header_version", VERSION)
    set_field(data, "header_size", HEADER_SIZE)
    set_field(data, "load_base", load_base)
    set_field(data, "entry_align", ENTRY_ALIGN)
    set_field(data, "load_align", LOAD_ALIGN)
    set_field(data, "arch", ARCH_X86_64)
    set_field(data, "machine", MACHINE_EM_X86_64)
    set_field(data, "image_size", size)
    set_field(data, "entry_offset", entry - kernel_start)
    set_field(data, "bss_size", bss_end - bss_start)
    # Zero both checksum fields, then derive them in the order the validator reads them:
    # the CRC covers the header as it will be written (including a zeroed CRC field), and
    # the payload checksum treats both fields as zero, which is the only way a value inside
    # the hashed range can be defined at all.
    set_field(data, "checksum", 0)
    set_field(data, "header_crc", 0)
    # Order matters, and this is the whole reason the payload checksum ignores both fields:
    # `header_crc` covers the bytes up to and *including* the checksum field, so the
    # checksum has to exist before the CRC is taken.  Computing them the other way round
    # stamps a CRC over a header whose checksum byte then changes underneath it -- which is
    # exactly the `kimage64-header-crc` refusal this function refuses to leave on disk.
    set_field(data, "checksum", payload_checksum(bytes(data)))
    set_field(data, "header_crc", header_crc(bytes(data)))

    reason, fields = verify(bytes(data), load_base)
    if reason:
        raise SystemExit("kimage64: the header just stamped does not verify (%s): %r\n"
                         "This is a bug in the stamper or the format, not in the kernel: fix "
                         "this before writing the file." % (reason, fields))
    with open(binary, "r+b") as handle:
        handle.seek(0)
        handle.write(data)
    return fields


#: The mutations the negative controls use.  Each one breaks exactly one property and says
#: which reason it must produce, so a control cannot pass by tripping a different check.
#:
#: Every mutation re-derives `header_crc` before returning -- except the one whose whole
#: subject is a stale CRC.  Without that, a mutation of `arch` or `image_size` would be
#: diagnosed as `kimage64-header-crc` (the CRC covers those bytes and is checked first), and
#: the control named after the field would be measuring the CRC instead.  The payload
#: checksum deliberately stays stale for every mutation: it covers the whole file, it is the
#: last check, so the field's own check fires first, and "the loader noticed the corruption
#: twice, in two independent ways" is a property worth having in the artifact.
MUTATIONS = {
    "magic": lambda d: _set(d, "magic", b"PC5K64XX"),
    "version": lambda d: _set(d, "header_version", 2),
    "header-size": lambda d: _set(d, "header_size", 64),
    "image-size": lambda d: _set(d, "image_size", len(d) - 4096),
    "load-base": lambda d: _set(d, "load_base", LINK_BASE + 0x100000),
    "arch": lambda d: _set(d, "arch", 2),
    "machine": lambda d: _set(d, "machine", 3),
    "entry-range": lambda d: _set(d, "entry_offset", len(d) + 16),
    "entry-align": lambda d: _set(d, "entry_offset",
                                  field(bytes(d), "entry_offset") + 1),
    "bss-size": lambda d: _set(d, "bss_size", len(d) + 4096),
    "reserved": lambda d: _set(d, "reserved0", 1),
    "header-crc": lambda d: _set(d, "arch", ARCH_X86_64 ^ 0x10),
    # Inside the code, not in the header's padding: the point of this control is that a bit
    # flip out where there is no magic number and no version field is still a refusal.  At
    # `entry + 64` the flipped byte is in the first instruction stream the kernel runs, which
    # is the class of corruption this whole format exists to notice.
    "checksum": lambda d: _flip(d, field(bytes(d), "entry_offset") + 64),
    "truncate": lambda d: d[:max(HEADER_SIZE, len(d) - 8192)],
}


def _set(data, name, value):
    set_field(data, name, value)
    return data


def _flip(data, index):
    data[index] ^= 0xFF
    return data


#: The reason each mutation must produce, spelled the way the loader names it.  Kept beside
#: the mutations so adding one without saying what it must fail on is visible in review.
EXPECTED_REFUSAL = {
    "magic": "kimage64-magic",
    "version": "kimage64-version",
    "header-size": "kimage64-header-size",
    "image-size": "kimage64-image-size",
    "load-base": "kimage64-load-base",
    "arch": "kimage64-arch",
    "machine": "kimage64-machine",
    "entry-range": "kimage64-entry-range",
    "entry-align": "kimage64-entry-align",
    "bss-size": "kimage64-bss-size",
    "reserved": "kimage64-reserved",
    "header-crc": "kimage64-header-crc",
    "checksum": "kimage64-checksum",
    "truncate": "kimage64-image-size",
}


def mutate_bytes(data: bytes, kind: str) -> bytes:
    """Return a copy of `data` broken in exactly the way `kind` says, verified to be broken.

    `truncate` is the one case where the *length check* is the subject, so the declared
    `image_size` is left describing the original file; every other case re-derives the CRC
    as described above.
    """
    broken = bytearray(MUTATIONS[kind](bytearray(data)))
    if kind != "header-crc":
        if len(broken) >= HEADER_CRC_WINDOW[1]:
            set_field(broken, "header_crc", header_crc(bytes(broken)))
    reason, _fields = verify(bytes(broken))
    want = EXPECTED_REFUSAL[kind]
    if reason != want:
        raise SystemExit(
            "kimage64: the %s mutation was expected to be refused as %s but reported %r; a "
            "control built from it would not be testing what it is named after"
            % (kind, want, reason))
    return bytes(broken)


def mutate(binary: str, out_path: str, kind: str) -> str:
    data = open(binary, "rb").read()
    broken = mutate_bytes(data, kind)
    with open(out_path, "wb") as handle:
        handle.write(broken)
    return EXPECTED_REFUSAL[kind]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kimage64", description=__doc__.splitlines()[0])
    ap.add_argument("--elf", help="linked ELF to read symbol addresses from (--stamp)")
    ap.add_argument("--bin", help="flat kernel image to stamp, verify or mutate")
    ap.add_argument("--loaded-at", default=hex(LINK_BASE),
                    help="address the image is required to be loaded at (default 0x100000)")
    ap.add_argument("--stamp", action="store_true", help="write the dynamic fields")
    ap.add_argument("--verify", action="store_true", help="re-check a file against itself")
    ap.add_argument("--mutate", choices=sorted(MUTATIONS),
                    help="write one broken field to --out")
    ap.add_argument("--out", help="where --mutate writes")
    ap.add_argument("--json", action="store_true", help="print the decoded header as JSON")
    args = ap.parse_args(argv)

    loaded_at = int(args.loaded_at, 0)
    if args.stamp:
        if not args.elf or not args.bin:
            ap.error("--stamp needs --elf and --bin")
        fields = stamp(args.elf, args.bin, loaded_at)
        print("KERNEL64 header stamped: version=%d header=%d image=%d entry=%#x bss=%d "
              "checksum=%#x" % (fields["header_version"], fields["header_size"],
                                fields["image_size"], fields["entry_offset"],
                                fields["bss_size"], fields["checksum"]))
        return 0
    target = args.bin
    if not target:
        ap.error("--bin is required for --verify and --mutate")
    if args.mutate:
        if not args.out:
            ap.error("--mutate needs --out")
        reason = mutate(target, args.out, args.mutate)
        print("kimage64: %s -> %s must be refused as %s" % (args.mutate, args.out, reason))
        return 0
    data = open(target, "rb").read()
    reason, fields = verify(data, loaded_at)
    if args.json:
        printable = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in fields.items()}
        printable["refusal"] = reason
        printable["path"] = target
        print(json.dumps(printable, sort_keys=True))
    elif reason:
        print("kimage64: %s is not a valid kernel image: %s" % (target, reason),
              file=sys.stderr)
    else:
        print("kimage64: %s is a valid PC5 64-bit kernel image (v%d, %d bytes, entry %#x)"
              % (target, fields["header_version"], fields["image_size"],
                 fields["entry_offset"]))
    return 1 if reason else 0


if __name__ == "__main__":
    sys.exit(main())
