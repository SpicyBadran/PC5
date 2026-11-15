#!/usr/bin/env python3
"""Contract tests for the partition-discovery code (M1e).

These do not boot anything. They protect the seams that the emulated boot test
cannot see cheaply:

  * the fixture images really are valid GPT/MBR per the specification fields the
    kernel checks (signature, header CRC, entry CRC, backup header),
  * the kernel module is read-only: it never references a write path,
  * the device layer refuses optical media before it reads anything,
  * the declared bounds in the header match what the parser enforces,
  * both build systems actually compile the unit.
"""

from __future__ import annotations

import os
import re
import struct
import sys
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import disk_images  # noqa: E402


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


def header_constant(name):
    text = read("kernel/include/pc5.h")
    m = re.search(rf"#define\s+{name}\s+([0-9]+)", text)
    if not m:
        raise AssertionError(f"{name} is not defined in kernel/include/pc5.h")
    return int(m.group(1))


class FixtureTests(unittest.TestCase):
    """The images the boot test attaches must be well-formed."""

    def setUp(self):
        self.size = 8 * 1024 * 1024
        self.image = disk_images.gpt_image(self.size, [
            ("PC5 ROOT", 2048, 2048, disk_images.TYPE_LINUX_FS),
            ("PC5 DATA", 4096, 2048, disk_images.TYPE_LINUX_FS),
        ])
        self.total = self.size // 512

    def test_protective_mbr_is_a_real_mbr(self):
        mbr = self.image[:512]
        self.assertEqual(mbr[510:512], b"\x55\xaa")
        self.assertEqual(mbr[446 + 4], 0xEE)
        self.assertEqual(struct.unpack_from("<I", mbr, 446 + 8)[0], 1)

    def test_primary_header_crc_verifies(self):
        hdr = bytearray(self.image[512:1024])
        self.assertEqual(hdr[0:8], b"EFI PART")
        stored = struct.unpack_from("<I", hdr, 16)[0]
        hdr[16:20] = b"\0\0\0\0"
        self.assertEqual(zlib.crc32(hdr[:92]) & 0xFFFFFFFF, stored)
        self.assertEqual(struct.unpack_from("<Q", hdr, 72)[0], 2)  # entries at LBA 2

    def test_entry_array_crc_verifies_and_names_are_utf16le(self):
        hdr = self.image[512:1024]
        entries_lba = struct.unpack_from("<Q", hdr, 72)[0]
        count = struct.unpack_from("<I", hdr, 80)[0]
        esize = struct.unpack_from("<I", hdr, 84)[0]
        stored = struct.unpack_from("<I", hdr, 88)[0]
        start = entries_lba * 512
        entries = self.image[start:start + count * esize]
        self.assertEqual(zlib.crc32(entries) & 0xFFFFFFFF, stored)
        name = entries[56:128].decode("utf-16-le").split("\0", 1)[0]
        self.assertEqual(name, "PC5 ROOT")
        first, last = struct.unpack_from("<QQ", entries, 32)
        self.assertEqual((first, last), (2048, 4095))

    def test_backup_header_is_at_the_last_sector(self):
        last = self.image[self.size - 512:]
        self.assertEqual(last[0:8], b"EFI PART")
        self.assertEqual(struct.unpack_from("<Q", last, 24)[0], self.total - 1)
        backup_entries = struct.unpack_from("<Q", last, 72)[0]
        self.assertEqual(backup_entries, self.total - 33)

    def test_corruption_only_touches_the_entry_array(self):
        bad = disk_images.corrupt_entries_crc(self.image)
        self.assertEqual(len(bad), len(self.image))
        diff = [i for i in range(len(bad)) if bad[i] != self.image[i]]
        self.assertEqual(len(diff), 1)
        self.assertGreaterEqual(diff[0], 2 * 512)  # inside the primary entry array
        self.assertLess(diff[0], 2 * 512 + 128 * 128)

    def test_mbr_fixture(self):
        img = disk_images.mbr_image(8 * 1024 * 1024, [(0x83, 2048, 2048), (0x07, 4096, 2048)])
        self.assertEqual(img[510:512], b"\x55\xaa")
        self.assertEqual(img[446 + 4], 0x83)
        self.assertEqual(struct.unpack_from("<I", img, 446 + 8)[0], 2048)
        self.assertEqual(struct.unpack_from("<I", img, 446 + 16 + 12)[0], 2048)


class KernelWiringTests(unittest.TestCase):
    def test_bounds_in_header_are_the_ones_the_parser_enforces(self):
        self.assertEqual(header_constant("PART_MAX"), 16)
        self.assertEqual(header_constant("PART_NAME_LEN"), 37)
        part = read("kernel/part.c")
        self.assertIn("#define GPT_MAX_TABLE_BYTES 16384u", part)
        self.assertIn("#define GPT_MIN_ENTRY_SIZE 128u", part)
        self.assertIn("out->count >= PART_MAX", part)

    def test_partition_module_is_read_only(self):
        part = read("kernel/part.c")
        self.assertIn("ata_read_sectors", part)
        for forbidden in ("ata_write_sectors", "ata_write_chunk", "0x30", "0xe7", "outb("):
            self.assertNotIn(forbidden, part,
                             f"partition discovery must never write ({forbidden})")

    def test_optical_devices_are_refused_before_any_read(self):
        part = read("kernel/part.c")
        scan = part.split("const struct part_report *part_scan(int device)")[1]
        atapi = scan.index("PART_E_ATAPI")
        first_read = scan.index("ata_read_sectors")
        self.assertLess(atapi, first_read,
                        "an ATAPI device must be refused before a sector is read")

    def test_scan_takes_an_explicit_device_index(self):
        header = read("kernel/include/pc5.h")
        self.assertIn("const struct part_report *part_scan(int device);", header)
        self.assertNotIn("part_scan(void)", header)

    def test_boot_calls_the_scan_without_making_it_fatal(self):
        main = read("kernel/main.c")
        self.assertIn("part_scan(disk)", main)
        # the result is reported, never turned into a panic or an error return
        self.assertNotIn("con_panic(\"partition", main)

    def test_unit_target_compiles_the_partition_parser(self):
        mk = read("Makefile")
        unit = mk.split("\nunit:")[1].split("\n\n")[0]
        self.assertIn("kernel/part.c", unit)
        # the kernel image is built from every kernel/*.c, so part.c is in it
        self.assertIn("$(wildcard kernel/*.c)", mk)

    def test_boot_test_uses_the_shared_fixture_builder(self):
        bt = read("tests/boot_test.py")
        self.assertIn("import disk_images", bt)
        self.assertIn("disk_images.gpt_image(", bt)
        self.assertIn("disk_images.corrupt_entries_crc(", bt)
        self.assertIn("status=crc", bt)


if __name__ == "__main__":
    unittest.main()
