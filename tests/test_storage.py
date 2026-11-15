#!/usr/bin/env python3
"""Tests for the ATA write path's contracts and harness safety.

The kernel's own behaviour is proven by `make unit` (request rules, SHA-256
vectors) and by `make boot-test` (a real write and read-back on an emulated
disk). These tests protect the seams that neither can see:

  * the scratch window in `tests/boot_test.py` is the same window the kernel
    uses, so a test cannot verify the wrong sectors;
  * the host-side pattern and the kernel pattern produce the same digest;
  * the write test is explicit — no boot path calls it automatically;
  * the harness only ever attaches a file it created, never a host device.
"""

from __future__ import annotations

import hashlib
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def header_constant(name):
    text = read("kernel", "include", "pc5.h")
    match = re.search(rf"#define\s+{name}\s+([0-9]+)([uUlL]*)", text)
    if not match:
        raise AssertionError(f"{name} is not defined in kernel/include/pc5.h")
    return int(match.group(1))


def boot_test_constant(name):
    text = read("tests", "boot_test.py")
    match = re.search(rf"^{name}\s*=\s*([0-9]+)", text, flags=re.MULTILINE)
    if not match:
        raise AssertionError(f"{name} is not defined in tests/boot_test.py")
    return int(match.group(1))


def kernel_pattern(n=4096):
    return bytes(((i * 7 + 0x5A) & 0xFF) for i in range(n))


class ScratchWindowTests(unittest.TestCase):
    def test_scratch_window_matches_between_kernel_and_test(self):
        self.assertEqual(header_constant("WTEST_SCRATCH_LBA"),
                         boot_test_constant("WTEST_SCRATCH_LBA"))
        self.assertEqual(header_constant("WTEST_SCRATCH_SECTORS"),
                         boot_test_constant("WTEST_SCRATCH_SECTORS"))

    def test_window_is_4k_of_512_byte_sectors(self):
        self.assertEqual(header_constant("PC5_SECTOR_SIZE"), 512)
        self.assertEqual(header_constant("PC5_BLOCK_SIZE"), 2048)
        sectors = header_constant("WTEST_SCRATCH_SECTORS")
        self.assertEqual(sectors * header_constant("PC5_SECTOR_SIZE"), 4096)

    def test_window_starts_far_enough_into_the_disk(self):
        # 1 MiB in, so it cannot overlap a partition table, an MBR, or anything
        # a future boot loader would place at the start of the disk.
        self.assertGreaterEqual(header_constant("WTEST_SCRATCH_LBA") * 512, 1024 * 1024)

    def test_test_disk_is_disposable_and_bigger_than_the_window(self):
        text = read("tests", "boot_test.py")
        self.assertIn("WTEST_HDD_BYTES = 8388608", text)
        window_end = (boot_test_constant("WTEST_SCRATCH_LBA")
                      + boot_test_constant("WTEST_SCRATCH_SECTORS")) * 512
        self.assertGreater(boot_test_constant("WTEST_HDD_BYTES"), window_end)

    def test_host_pattern_matches_the_kernel_vector(self):
        # The same digest is asserted against kernel/sha256.c in
        # tests/kernel_unit.c and against this Python computation in the boot
        # test, so a divergent pattern fails one of the two.
        self.assertEqual(hashlib.sha256(kernel_pattern()).hexdigest(),
                         "80fdc12208ec4af1410125cbe146549182100a6b1e0cf67e382aa9aefdbafa3d")


class ExplicitnessTests(unittest.TestCase):
    def test_no_boot_path_runs_the_write_test(self):
        for name in ("kernel/main.c", "kernel/start.S", "kernel/chain.c"):
            source = read(*name.split("/"))
            self.assertNotIn("storage_write_selftest", source,
                             f"{name} must not start a write test during boot")

    def test_the_write_test_is_reachable_only_from_the_console(self):
        callers = []
        for name in os.listdir(os.path.join(ROOT, "kernel")):
            if not name.endswith(".c"):
                continue
            if "storage_write_selftest" in read("kernel", name):
                callers.append(name)
        self.assertEqual(sorted(callers), ["storage_test.c", "ui.c"])

    def test_serial_help_lists_the_command(self):
        self.assertIn("wtest", read("kernel", "ui.c"))

    def test_harness_can_only_attach_a_file_it_was_given(self):
        runner = read("tests", "v86_runner.mjs")
        self.assertIn("--hda-buffer", runner)
        self.assertIn("--hda-out", runner)
        boot = read("tests", "boot_test.py")
        self.assertIn('os.path.join(args.out_dir, "wtest-hda.img")', boot)
        self.assertIn("hda_buffer=disk_in", boot)
        # A boot test must never touch a host block device.
        self.assertNotIn("/dev/", boot)

    def test_runner_writes_the_copy_not_the_input(self):
        runner = read("tests", "v86_runner.mjs")
        self.assertIn('fs.readFileSync(path.resolve(args["hda-buffer"]))', runner)
        self.assertIn('fs.writeFileSync(path.resolve(args["hda-out"]), Buffer.from(hdaBuffer))', runner)


class KernelWiringTests(unittest.TestCase):
    def test_unit_target_compiles_the_host_testable_units(self):
        makefile = read("Makefile")
        self.assertIn("kernel/block.c", makefile)
        self.assertIn("kernel/sha256.c", makefile)

    def test_ata_write_requires_an_explicit_device_index(self):
        source = read("kernel", "ata.c")
        self.assertIn("int ata_write_sectors(int device, u32 lba, u32 count, const void *buf)", source)
        # The 2048-byte boot-medium reader must not have grown a write sibling.
        self.assertNotIn("ata_write_blocks", source)

    def test_atapi_is_refused_before_any_range_check(self):
        source = read("kernel", "block.c")
        atapi = source.index("if (atapi && writable)")
        count = source.index("if (count == 0)")
        self.assertLess(atapi, count, "device type must be decided before request shape")

    def test_optical_and_disk_units_are_documented_as_distinct(self):
        header = read("kernel", "include", "pc5.h")
        self.assertIn("PC5_SECTOR_SIZE 512", header)
        self.assertIn("PC5_BLOCK_SIZE 2048", header)
        self.assertIn("ADR-013", header)


if __name__ == "__main__":
    unittest.main()
