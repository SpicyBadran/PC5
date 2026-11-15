#!/usr/bin/env python3
"""Small, valid disk images for the PC5 boot test.

The boot test needs a disk whose partition table the *kernel* can parse, and
it must be built here rather than downloaded, so the exact bytes are visible
and reproducible.

Two builders:

  mbr_image(size, parts)  four primary slots, 55 AA signature
  gpt_image(size, parts)  protective MBR, primary header at LBA 1, entry array
                          at LBA 2, backup entry array and backup header at the
                          end of the disk (as the GPT specification requires)

Only the fields the kernel actually validates are meaningful here; every other
byte is zero.  These images are test fixtures, not a partitioning tool, and
nothing in PC5 writes them.
"""

from __future__ import annotations

import struct
import zlib

SECTOR = 512
GPT_ENTRIES = 128
GPT_ENTRY_SIZE = 128
FIRST_USABLE_LBA = 34

# Type GUIDs are stored as raw bytes; PC5 reports them raw rather than
# reinterpreting endianness it has not verified.
TYPE_LINUX_FS = bytes.fromhex("af3dc60f838472478e793d69d8477de4")
TYPE_EFI_SYSTEM = bytes.fromhex("c12a7328f81f11d2ba4b00a0c93ec93b")


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _put32(buf: bytearray, off: int, value: int) -> None:
    struct.pack_into("<I", buf, off, value & 0xFFFFFFFF)


def _put64(buf: bytearray, off: int, value: int) -> None:
    struct.pack_into("<Q", buf, off, value & 0xFFFFFFFFFFFFFFFF)


def _utf16_name(name: str) -> bytes:
    raw = name.encode("utf-16-le")[:72]
    return raw + bytes(72 - len(raw))


def protective_mbr(disk_sectors: int) -> bytes:
    mbr = bytearray(SECTOR)
    e = 446
    mbr[e + 4] = 0xEE
    _put32(mbr, e + 8, 1)
    _put32(mbr, e + 12, min(disk_sectors - 1, 0xFFFFFFFF))
    mbr[510] = 0x55
    mbr[511] = 0xAA
    return bytes(mbr)


def mbr_image(size_bytes: int, parts) -> bytes:
    """parts: iterable of (type_code, first_lba, sectors). Maximum four."""
    parts = list(parts)
    if len(parts) > 4:
        raise ValueError("an MBR has four primary slots")
    if size_bytes % SECTOR:
        raise ValueError("size must be a whole number of sectors")
    img = bytearray(size_bytes)
    for i, (type_code, first, count) in enumerate(parts):
        if first < 1 or count < 1 or first + count > size_bytes // SECTOR:
            raise ValueError("partition outside the image")
        e = 446 + i * 16
        img[e + 4] = type_code
        _put32(img, e + 8, first)
        _put32(img, e + 12, count)
    img[510] = 0x55
    img[511] = 0xAA
    return bytes(img)


def gpt_image(size_bytes: int, parts, disk_guid: bytes | None = None) -> bytes:
    """parts: iterable of (name, first_lba, sectors, type_guid)."""
    if size_bytes % SECTOR:
        raise ValueError("size must be a whole number of sectors")
    total = size_bytes // SECTOR
    parts = list(parts)
    if len(parts) > GPT_ENTRIES:
        raise ValueError("too many partitions for one entry array")

    entries = bytearray(GPT_ENTRIES * GPT_ENTRY_SIZE)
    for i, (name, first, count, type_guid) in enumerate(parts):
        if len(type_guid) != 16:
            raise ValueError("type GUID must be 16 bytes")
        if first < FIRST_USABLE_LBA or count < 1 or first + count > total - 33:
            raise ValueError("partition outside the usable area")
        e = i * GPT_ENTRY_SIZE
        entries[e:e + 16] = type_guid
        # A unique GUID per entry; content is irrelevant to the parser but a
        # real table never has a zero unique GUID.
        entries[e + 16:e + 32] = bytes([(i + 1) & 0xFF] * 16)
        struct.pack_into("<Q", entries, e + 32, first)
        struct.pack_into("<Q", entries, e + 40, first + count - 1)
        entries[e + 56:e + 128] = _utf16_name(name)

    entries_crc = crc32(entries)
    disk_guid = disk_guid or bytes.fromhex("5a1b2c3d4e5f60718293a4b5c6d7e8f9")
    backup_entries_lba = total - 33

    def header(current_lba: int, backup_lba: int, entries_lba: int) -> bytes:
        h = bytearray(SECTOR)
        h[0:8] = b"EFI PART"
        _put32(h, 8, 0x00010000)
        _put32(h, 12, 92)
        _put32(h, 16, 0)
        _put64(h, 24, current_lba)
        _put64(h, 32, backup_lba)
        _put64(h, 40, FIRST_USABLE_LBA)
        _put64(h, 48, total - 34)
        h[56:72] = disk_guid
        _put64(h, 72, entries_lba)
        _put32(h, 80, GPT_ENTRIES)
        _put32(h, 84, GPT_ENTRY_SIZE)
        _put32(h, 88, entries_crc)
        _put32(h, 16, crc32(h[:92]))
        return bytes(h)

    img = bytearray(size_bytes)
    img[0:SECTOR] = protective_mbr(total)
    img[SECTOR:2 * SECTOR] = header(1, total - 1, 2)
    img[2 * SECTOR:2 * SECTOR + len(entries)] = entries
    img[backup_entries_lba * SECTOR:backup_entries_lba * SECTOR + len(entries)] = entries
    img[(total - 1) * SECTOR:(total - 1) * SECTOR + SECTOR] = header(total - 1, 1, backup_entries_lba)
    return bytes(img)


def corrupt_entries_crc(image: bytes) -> bytes:
    """Flip one byte of the primary entry array so its CRC no longer matches.

    This produces the exact failure a damaged disk would show: a valid header
    pointing at a table that does not verify.
    """
    bad = bytearray(image)
    bad[2 * SECTOR + 56] ^= 1
    return bytes(bad)
