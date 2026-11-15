#!/usr/bin/env python3
"""FAT32 tests that do not need the emulator.

Three independent views of the same fixture must agree:

  1. the bytes the fixture was generated from (`expected_files`),
  2. a second FAT32 reader written in Python (tests/fat32_images.py),
  3. the PC5 kernel module, compiled as a host program (tests/fat32_check.c).

Then the contracts that are easy to regress are pinned: the module contains no
write path, both build systems compile it, the mount API takes explicit base
LBA/sector counts, and a corrupted BPB is refused.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fat32_images  # noqa: E402

SOURCES = ["tests/fat32_check.c", "kernel/fat32.c", "kernel/sha256.c",
           "kernel/lib.c", "kernel/mem.c", "kernel/chain.c"]


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


class Fat32FixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = fat32_images.build_fat32_image()

    def test_partition_table_points_at_the_volume(self):
        mbr = self.image[446:462]
        self.assertEqual(mbr[4], 0x0C)
        self.assertEqual(int.from_bytes(mbr[8:12], "little"), 2048)
        self.assertEqual(int.from_bytes(mbr[12:16], "little"), 66592)
        self.assertEqual(self.image[510:512], b"\x55\xaa")

    def test_bpb_is_structurally_fat32(self):
        boot = self.image[2048 * 512:2049 * 512]
        self.assertEqual(boot[510:512], b"\x55\xaa")
        self.assertEqual(int.from_bytes(boot[11:13], "little"), 512)
        self.assertEqual(boot[13], 1)               # sectors per cluster
        self.assertEqual(int.from_bytes(boot[17:19], "little"), 0)  # root entries
        self.assertEqual(int.from_bytes(boot[22:24], "little"), 0)  # FAT16 size
        self.assertNotEqual(int.from_bytes(boot[36:40], "little"), 0)  # FAT32 size
        self.assertEqual(boot[82:90], b"FAT32   ")

    def test_cluster_count_is_a_conforming_fat32_volume(self):
        boot = self.image[2048 * 512:2049 * 512]
        total = int.from_bytes(boot[32:36], "little")
        reserved = int.from_bytes(boot[14:16], "little")
        fats = boot[16]
        fat_sectors = int.from_bytes(boot[36:40], "little")
        spc = boot[13]
        clusters = (total - reserved - fats * fat_sectors) // spc
        self.assertGreaterEqual(clusters, 65525,
                                "the fixture must be a spec-conforming FAT32 volume")

    def test_independent_reader_matches_generated_bytes(self):
        for name, data in fat32_images.expected_files().items():
            with self.subTest(name=name):
                self.assertEqual(fat32_images.read_file(self.image, name), data)

    def test_reader_ignores_deleted_entries_and_labels(self):
        for missing in ("DELETED.TXT", "PC5FS"):
            with self.subTest(name=missing):
                with self.assertRaises(KeyError):
                    fat32_images.read_file(self.image, missing)

    def test_reader_refuses_a_directory(self):
        with self.assertRaises(IsADirectoryError):
            fat32_images.read_file(self.image, "SUBDIR")

    def test_fragmented_chain_is_really_fragmented(self):
        boot = self.image[2048 * 512:2049 * 512]
        reserved = int.from_bytes(boot[14:16], "little")
        fats = boot[16]
        fat_sectors = int.from_bytes(boot[36:40], "little")
        fat = self.image[(2048 + reserved) * 512:
                         (2048 + reserved + fat_sectors) * 512]
        # FRAG.BIN was built as 4 clusters out of order; the chain must still
        # terminate and must not be four consecutive numbers.
        name83 = b"FRAG    BIN"
        root = self.image[(2048 + reserved + fats * fat_sectors) * 512:
                          (2048 + reserved + fats * fat_sectors + 1) * 512]
        start = None
        for off in range(0, len(root), 32):
            if root[off:off + 11] == name83:
                start = (int.from_bytes(root[off + 20:off + 22], "little") << 16) \
                        | int.from_bytes(root[off + 26:off + 28], "little")
        self.assertIsNotNone(start)
        chain = []
        cur = start
        while True:
            chain.append(cur)
            nxt = int.from_bytes(fat[cur * 4:cur * 4 + 4], "little") & 0x0FFFFFFF
            if nxt >= 0x0FFFFFF8:
                break
            cur = nxt
        self.assertEqual(len(chain), 4)
        self.assertNotEqual(chain, list(range(chain[0], chain[0] + 4)))


class KernelModuleTest(unittest.TestCase):
    """Compile kernel/fat32.c as a host program and compare its answers."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("gcc"):
            raise unittest.SkipTest("gcc is required to build the host driver")
        cls.tmp = tempfile.mkdtemp(prefix="pc5-fat32-")
        cls.image_path = os.path.join(cls.tmp, "fat32.img")
        cls.image = fat32_images.build_fat32_image()
        with open(cls.image_path, "wb") as fh:
            fh.write(cls.image)
        cls.image_sha = hashlib.sha256(cls.image).hexdigest()

        cls.binary = os.path.join(cls.tmp, "fat32-check")
        cmd = ["gcc", "-std=gnu11", "-O1", "-Wall", "-Wextra",
               "-Wno-unused-parameter", "-ffreestanding", "-fno-builtin",
               "-fno-stack-protector", "-DPC5_HOST_TEST",
               "-I", os.path.join(ROOT, "kernel", "include"),
               "-o", cls.binary] + [os.path.join(ROOT, s) for s in SOURCES]
        cls.build = subprocess.run(cmd, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_check(self, image_path, names):
        proc = subprocess.run(
            [self.binary, image_path, "2048", "66592"] + list(names),
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        fields = {"mount": {}, "files": {}, "entries": []}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if not parts:
                continue
            kv = {}
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    kv[k] = v
            if parts[0] == "mount":
                fields["mount"] = kv
            elif parts[0] == "file":
                fields["files"][kv["name"]] = kv
            elif parts[0] == "entry":
                fields["entries"].append(kv)
        return fields, proc.stdout

    def test_compiles_without_warnings(self):
        self.assertEqual(self.build.returncode, 0, self.build.stderr[-3000:])
        # Only the FAT32 module and its driver must be warning-free; mem.c's
        # long-standing 32-bit pointer cast is unrelated to this milestone.
        own = [ln for ln in self.build.stderr.splitlines()
               if ("fat32" in ln) and "warning:" in ln]
        self.assertFalse(own, "\n".join(own))

    def test_kernel_reads_every_fixture_file_correctly(self):
        expected = fat32_images.expected_files()
        result, stdout = self.run_check(self.image_path, expected.keys())
        self.assertEqual(result["mount"].get("status"), "ok", stdout)
        self.assertEqual(result["mount"].get("label"), "PC5FS", stdout)
        self.assertEqual(result["mount"].get("fs"), "FAT32", stdout)
        for name, data in expected.items():
            with self.subTest(name=name):
                got = result["files"].get(name)
                self.assertIsNotNone(got, stdout)
                self.assertEqual(got["status"], "ok", stdout)
                self.assertEqual(int(got["size"]), len(data))
                self.assertEqual(got["sha256"],
                                 hashlib.sha256(data).hexdigest(),
                                 f"{name}: kernel digest != fixture digest")

    def test_kernel_and_python_reader_agree_on_digests(self):
        expected = fat32_images.expected_files()
        result, stdout = self.run_check(self.image_path, expected.keys())
        for name in expected:
            with self.subTest(name=name):
                py = hashlib.sha256(fat32_images.read_file(self.image, name)).hexdigest()
                self.assertEqual(result["files"][name]["sha256"], py, stdout)

    def test_listing_skips_labels_deleted_entries_and_lfn_records(self):
        result, stdout = self.run_check(self.image_path, ["SMALL.BIN"])
        names = sorted(e["name"] for e in result["entries"])
        self.assertEqual(names, ["EMPTY.TXT", "EXACT.BIN", "FRAG.BIN", "MULTI.BIN",
                                 "PC5TEST.TXT", "SMALL.BIN", "SUBDIR"], stdout)

    def test_directory_and_missing_entries_are_refused(self):
        result, stdout = self.run_check(self.image_path,
                                        ["SUBDIR", "DELETED.TXT", "NOPE.TXT"])
        self.assertEqual(result["files"]["SUBDIR"]["status"], "directory", stdout)
        self.assertEqual(result["files"]["DELETED.TXT"]["status"], "no-file", stdout)
        self.assertEqual(result["files"]["NOPE.TXT"]["status"], "no-file", stdout)

    def test_the_image_is_byte_identical_after_reading(self):
        expected = fat32_images.expected_files()
        self.run_check(self.image_path, expected.keys())
        with open(self.image_path, "rb") as fh:
            after = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(after, self.image_sha,
                         "FAT32 reading must not modify the image")

    def test_a_fat16_bpb_is_refused_not_guessed(self):
        image = fat32_images.build_fat32_image()
        bad = fat32_images.corrupt_bpb(image, 17, 512)  # root entry count
        path = os.path.join(self.tmp, "fat16-bpb.img")
        with open(path, "wb") as fh:
            fh.write(bad)
        proc = subprocess.run([self.binary, path, "2048", "66592", "SMALL.BIN"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("mount status=not-fat32", proc.stdout)

    def test_a_missing_boot_signature_is_refused(self):
        image = fat32_images.build_fat32_image()
        bad = bytearray(image)
        base = 2048 * 512
        bad[base + 510] = 0
        path = os.path.join(self.tmp, "nosig.img")
        with open(path, "wb") as fh:
            fh.write(bad)
        proc = subprocess.run([self.binary, path, "2048", "66592"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("mount status=signature", proc.stdout)


class WiringTest(unittest.TestCase):
    def test_module_has_no_write_path(self):
        src = read("kernel/fat32.c")
        for forbidden in ("ata_write", "outb(", "WRITE SECTORS", "0xe7",
                          "ata_writable_device", "storage_write"):
            self.assertNotIn(forbidden, src,
                             f"the FAT32 module must not touch the device directly ({forbidden})")

    def test_device_layer_is_excluded_from_host_builds(self):
        src = read("kernel/fat32.c")
        self.assertIn("#ifndef PC5_HOST_TEST", src)
        self.assertIn("ata_read_sectors", src)  # only inside the device layer
        self.assertGreater(src.index("#ifndef PC5_HOST_TEST"),
                           src.index("int fat32_read_file"))

    def test_mount_takes_explicit_geometry(self):
        header = read("kernel/include/pc5.h")
        self.assertIn("int fat32_mount(const struct fat32_io *io, u32 base_lba, u32 sectors,", header)
        self.assertIn("int fat32_mount_partition(int device);", header)

    def test_both_build_systems_compile_the_module(self):
        mk = read("Makefile")
        unit = mk.split("\nunit:")[1].split("\n\n")[0]
        self.assertIn("kernel/fat32.c", unit)
        self.assertIn("$(wildcard kernel/*.c)", mk)
        tools = read("tests/test_tools.py")
        self.assertIn('"fat32.c"', tools)

    def test_status_names_are_stable(self):
        src = read("kernel/fat32.c")
        for name in ("ok", "signature", "bpb", "not-fat32", "geometry", "no-file",
                     "directory", "space", "chain", "loop", "corrupt"):
            self.assertIn(f'"{name}"', src)


if __name__ == "__main__":
    unittest.main()
