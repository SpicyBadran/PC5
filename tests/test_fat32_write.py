#!/usr/bin/env python3
"""M1g: creating a file on a FAT32 volume, checked by an independent reader.

Every test here follows the same shape, because the question is not "did the
kernel say ok" but "did the disk end up in a state another implementation can
prove":

  1. build a deterministic FAT32 image and remember its SHA-256,
  2. let `kernel/fat32.c` (compiled as a host program) create one file,
  3. read the *resulting image* with `tests/fat32_images.py`, a second FAT32
     implementation that shares no code with the kernel,
  4. assert the new file's bytes, its chain, both FAT copies, the FSInfo block
     and every pre-existing file,
  5. assert the set of changed sectors is confined to what a create may touch.

The fault-injection tests do the same with a write or flush forced to fail, and
classify what the volume looks like afterwards: intact, leaked, partial or
inconsistent.  None of them is allowed to call a partial write a success.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fat32_images  # noqa: E402

SOURCES = ["tests/fat32_write_check.c", "kernel/fat32.c", "kernel/sha256.c",
           "kernel/lib.c", "kernel/mem.c", "kernel/chain.c"]

PART_LBA = 2048
PART_SECTORS = 66592
SECTOR = 512

# Every byte index a correct create is allowed to change, in absolute sectors:
# the data clusters it allocated, both FAT copies, the FSInfo block and its
# backup, and the root-directory sector holding the new entry.  Anything else
# changing would be a bug worth failing on.
def allowed_change_sectors(g: dict) -> set[int]:
    base = g["part_lba"]
    out = {base + g["fsinfo"], base + g["backup"] + 1}
    for i in range(g["nfats"]):
        for s in range(g["fat_sectors"]):
            out.add(base + g["reserved"] + i * g["fat_sectors"] + s)
    root = base + g["first_data"] + (g["root_cluster"] - 2) * g["spc"]
    for s in range(g["spc"]):
        out.add(root + s)
    return out


def set_fat(image: bytes, cluster: int, value: int, part_lba: int = PART_LBA) -> bytes:
    """Set one FAT entry in every copy (test scaffolding, not the code under test)."""
    out = bytearray(image)
    _set_fat_inplace(out, image, cluster, value, part_lba)
    return bytes(out)


def _set_fat_inplace(out: bytearray, image: bytes, cluster: int, value: int,
                     part_lba: int = PART_LBA) -> None:
    g = fat32_images.volume_geometry(image, part_lba)
    for i in range(g["nfats"]):
        off = (part_lba + g["reserved"] + i * g["fat_sectors"]) * SECTOR + cluster * 4
        struct.pack_into("<I", out, off, value & 0xFFFFFFFF)


def set_fsinfo(image: bytes, field: str, value: int, part_lba: int = PART_LBA) -> bytes:
    g = fat32_images.volume_geometry(image, part_lba)
    out = bytearray(image)
    offset = {"free_count": 488, "next_free": 492, "lead": 0, "struct": 484,
              "trail": 508}[field]
    struct.pack_into("<I", out, (part_lba + g["fsinfo"]) * SECTOR + offset, value & 0xFFFFFFFF)
    return bytes(out)


def leave_free_clusters(image: bytes, keep: int, part_lba: int = PART_LBA) -> bytes:
    """Mark every unreferenced cluster allocated except `keep` of them."""
    g = fat32_images.volume_geometry(image, part_lba)
    referenced = fat32_images.referenced_clusters(image, part_lba)
    out = bytearray(image)
    # one pass over the FAT, not one whole-image copy per cluster
    for i in range(g["nfats"]):
        start = (part_lba + g["reserved"] + i * g["fat_sectors"]) * SECTOR
        kept = 0
        for c in range(2, g["clusters"] + 2):
            if c in referenced:
                continue
            kept += 1
            if kept > keep:
                struct.pack_into("<I", out, start + c * 4, 0x0FFFFFFF)
    return bytes(out)


def close_directory(image: bytes, part_lba: int = PART_LBA) -> bytes:
    """Turn the fixture's deleted slot into a live entry: no free slot remains."""
    g = fat32_images.volume_geometry(image, part_lba)
    out = bytearray(image)
    root = (part_lba + g["first_data"] + (g["root_cluster"] - 2) * g["spc"]) * SECTOR
    # slot 1 is DELETED.TXT (0xE5 ...); reviving its first byte makes all 16
    # slots live and leaves the directory with no end marker and no free slot.
    assert out[root + 32] == 0xE5
    out[root + 32] = ord("D")
    return bytes(out)


def classify(before: bytes, after: bytes, name: str, part_lba: int = PART_LBA) -> dict:
    """What state did the volume end up in?  Used by the fault-injection tests."""
    expected = fat32_images.expected_files()
    info = {"size_ok": len(before) == len(after)}
    files = {}
    for fname, data in expected.items():
        try:
            files[fname] = fat32_images.read_file(after, fname, part_lba) == data
        except Exception:
            files[fname] = False
    info["originals_intact"] = all(files.values())
    info["broken_originals"] = sorted(k for k, v in files.items() if not v)
    try:
        got = fat32_images.read_file(after, name, part_lba)
        info["exists"] = True
        info["content"] = got
    except KeyError:
        info["exists"] = False
        info["content"] = None
    except Exception as exc:                      # unreadable entry is not "absent"
        info["exists"] = True
        info["content"] = None
        info["error"] = str(exc)
    g = fat32_images.volume_geometry(after, part_lba)
    fat = fat32_images.fat_entries(after, part_lba)
    info["fat_ok"] = all(c < g["clusters"] + 2 or fat[c] >= 0x0FFFFFF8
                         for c in range(2, g["clusters"] + 2))
    info["mirrors_equal"] = all(c == fat32_images.fat_copies(after, part_lba)[0]
                                for c in fat32_images.fat_copies(after, part_lba))
    info["orphans"] = sorted(fat32_images.orphan_clusters(after, part_lba))
    info["changed"] = fat32_images.changed_sectors(before, after)
    return info


class WriteDriver(unittest.TestCase):
    """Builds the host driver once; every test gets a fresh copy of an image."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("gcc"):
            raise unittest.SkipTest("gcc is required to build the host driver")
        cls.tmp = tempfile.mkdtemp(prefix="pc5-fat32w-")
        cls.binary = os.path.join(cls.tmp, "fat32-write-check")
        cmd = ["gcc", "-std=gnu11", "-O1", "-Wall", "-Wextra",
               "-Wno-unused-parameter", "-ffreestanding", "-fno-builtin",
               "-fno-stack-protector", "-DPC5_HOST_TEST",
               "-I", os.path.join(ROOT, "kernel", "include"),
               "-o", cls.binary] + [os.path.join(ROOT, s) for s in SOURCES]
        cls.build = subprocess.run(cmd, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def create(self, image, name="NEW.TXT", size=0, payload=(), options=(),
               part_sectors=PART_SECTORS):
        """Run one create and return (fields, after_image, stdout, returncode)."""
        if not os.path.exists(self.binary):
            self.fail("driver was not built:\n" + self.build.stderr[-3000:])
        src = os.path.join(self.tmp, "in.img")
        dst = os.path.join(self.tmp, "out.img")
        lba_log = os.path.join(self.tmp, "lba.txt")
        for path in (dst, lba_log):
            if os.path.exists(path):
                os.unlink(path)
        with open(src, "wb") as fh:
            fh.write(image)
        cmd = [self.binary, src, str(PART_LBA), str(part_sectors), name, str(size),
               "--out", dst, "--lba-log", lba_log] + list(payload) + list(options)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        after = image
        if os.path.exists(dst):
            with open(dst, "rb") as fh:
                after = fh.read()
        fields, lines = {}, []
        for line in proc.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] == "create":
                fields = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
            lines.append(line)
        writes = []
        if os.path.exists(lba_log):
            with open(lba_log, encoding="ascii") as fh:
                values = [int(v) for v in fh.read().split()]
            writes = [(values[i], values[i + 1]) for i in range(0, len(values), 2)]
        fields["_lbas"] = writes
        fields["_stdout"] = "\n".join(lines)
        return fields, after, proc.returncode

    # ---------------------------------------------------------- the matrix

    def test_creates_a_small_file_that_an_independent_reader_confirms(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "NEW.TXT", payload=["--text", "hello M1g"])
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["status"], "ok")
        self.assertEqual(fields["verified"], "1")
        self.assertEqual(fat32_images.read_file(after, "NEW.TXT"), b"hello M1g")
        self.assertEqual(int(fields["clusters"]), 1)
        self.assertEqual(int(fields["free_after"]), fat32_images.free_cluster_count(after))
        for name, data in fat32_images.expected_files().items():
            with self.subTest(name=name):
                self.assertEqual(fat32_images.read_file(after, name), data,
                                 f"{name} was damaged by creating another file")
        self.assertEqual(fat32_images.orphan_clusters(after), set())
        self.assertEqual(fat32_images.fat_copies(after)[0], fat32_images.fat_copies(after)[1])

    def test_changes_are_confined_to_the_regions_a_create_may_touch(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "NEW.TXT", payload=["--text", "hello M1g"])
        self.assertEqual(rc, 0, fields["_stdout"])
        g = fat32_images.volume_geometry(image)
        chain = fat32_images.cluster_chain(after, int(fields["first"]))
        data_sectors = {PART_LBA + g["first_data"] + (c - 2) * g["spc"] + s
                        for c in chain for s in range(g["spc"])}
        changed = fat32_images.changed_sectors(image, after)
        self.assertTrue(changed <= allowed_change_sectors(g) | data_sectors,
                        f"unexpected sectors changed: {sorted(changed - allowed_change_sectors(g) - data_sectors)}")
        self.assertLessEqual(len(changed), 8, "a one-cluster create touches little")

    def test_zero_length_file(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "EMPTY2.TXT", 0)
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["clusters"], "0")
        self.assertEqual(fields["first"], "0")
        self.assertEqual(fat32_images.read_file(after, "EMPTY2.TXT"), b"")
        entry = next(e for e in fat32_images.list_dir(after) if e["name"] == "EMPTY2.TXT")
        self.assertEqual(entry["first_cluster"], 0)
        self.assertEqual(entry["size"], 0)

    def test_file_smaller_than_a_cluster_is_not_padded_into_its_size(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "TINY.BIN", 100)
        self.assertEqual(rc, 0, fields["_stdout"])
        payload = bytes(((i * 17 + 5) & 0xFF) for i in range(100))
        self.assertEqual(fat32_images.read_file(after, "TINY.BIN"), payload)
        self.assertEqual(len(fat32_images.read_file(after, "TINY.BIN")), 100)

    def test_file_exactly_one_cluster(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "ONE.BIN", 512)
        self.assertEqual(rc, 0, fields["_stdout"])
        payload = bytes(((i * 17 + 5) & 0xFF) for i in range(512))
        self.assertEqual(fat32_images.read_file(after, "ONE.BIN"), payload)
        self.assertEqual(int(fields["clusters"]), 1)

    def test_multi_cluster_file_chain_is_consistent(self):
        image = fat32_images.build_fat32_image()
        size = 3000
        fields, after, rc = self.create(image, "MULTI2.BIN", size)
        self.assertEqual(rc, 0, fields["_stdout"])
        payload = bytes(((i * 17 + 5) & 0xFF) for i in range(size))
        self.assertEqual(fat32_images.read_file(after, "MULTI2.BIN"), payload)
        self.assertEqual(int(fields["clusters"]), 6)
        chain = fat32_images.cluster_chain(after, int(fields["first"]))
        self.assertEqual(len(chain), 6)
        fat = fat32_images.fat_entries(after)
        for i, c in enumerate(chain):
            self.assertEqual(fat[c], chain[i + 1] if i + 1 < len(chain) else 0x0FFFFFFF)
        self.assertEqual(fat32_images.fat_copies(after)[0], fat32_images.fat_copies(after)[1])

    def test_the_root_directory_cluster_is_never_allocated(self):
        # A damaged FAT that marks the root directory's own cluster free must
        # not turn the directory into an allocation target: writing a file there
        # would destroy every name on the volume.
        image = set_fat(fat32_images.build_fat32_image(), 2, 0)
        self.assertEqual(fat32_images.fat_entries(image)[2], 0)
        fields, after, rc = self.create(image, "SAFE.TXT", 0, ["--text", "x"])
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertNotEqual(fields["first"], "0")
        self.assertNotIn(int(fields["first"]), (2,),
                         "cluster 2 holds the root directory")
        # every original name is still readable after the create
        for name, data in fat32_images.expected_files().items():
            self.assertEqual(fat32_images.read_file(after, name), data)

    def test_fragmented_free_space_produces_a_split_chain(self):
        image = fat32_images.build_fat32_image(holes=2)
        fields, after, rc = self.create(image, "FRAG2.BIN", 2000)
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fat32_images.read_file(after, "FRAG2.BIN"),
                         bytes(((i * 17 + 5) & 0xFF) for i in range(2000)))
        chain = fat32_images.cluster_chain(after, int(fields["first"]))
        gaps = [b - a for a, b in zip(chain, chain[1:])]
        self.assertIn(2, gaps, f"expected a fragmented chain, got {chain}")
        self.assertEqual(len(set(chain)), len(chain), "a cluster was used twice")

    def test_allocation_in_a_nearly_full_volume(self):
        image = leave_free_clusters(fat32_images.build_fat32_image(), keep=2)
        self.assertEqual(fat32_images.free_cluster_count(image), 2)
        fields, after, rc = self.create(image, "LAST.BIN", 1024)  # needs 2 clusters
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fat32_images.free_cluster_count(after), 0)
        self.assertEqual(fat32_images.read_file(after, "LAST.BIN"),
                         bytes(((i * 17 + 5) & 0xFF) for i in range(1024)))

    def test_a_full_volume_is_refused_without_writing_anything(self):
        image = leave_free_clusters(fat32_images.build_fat32_image(), keep=0)
        fields, after, rc = self.create(image, "NO.BIN", 512)
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "full")
        self.assertEqual(fields["writes"], "0", "a refused create must not write")
        self.assertEqual(after, image, "the image changed on a refusal")

    def test_one_cluster_short_is_refused(self):
        image = leave_free_clusters(fat32_images.build_fat32_image(), keep=1)
        fields, after, rc = self.create(image, "TWO.BIN", 1024)
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "full")
        self.assertEqual(after, image)

    def test_an_existing_name_is_refused_and_the_file_is_untouched(self):
        image = fat32_images.build_fat32_image()
        before = fat32_images.read_file(image, "PC5TEST.TXT")
        fields, after, rc = self.create(image, "PC5TEST.TXT", 512)
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "exists")
        self.assertEqual(fields["writes"], "0")
        self.assertEqual(after, image)
        self.assertEqual(fat32_images.read_file(after, "PC5TEST.TXT"), before)

    def test_existing_name_is_matched_case_insensitively(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "pc5test.txt", 512)
        self.assertEqual(fields["status"], "exists")
        self.assertEqual(after, image)

    def test_a_deleted_slot_is_reused_and_stays_invisible(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "NEW.TXT", 0, ["--text", "x"])
        self.assertEqual(rc, 0, fields["_stdout"])
        # slot 1 is the deleted DELETED.TXT record: reusing it is correct, and
        # the old name must not come back to life
        self.assertEqual(fields["dir_offset"], "32")
        names = [e["name"] for e in fat32_images.list_dir(after)]
        self.assertNotIn("DELETED.TXT", names)
        self.assertIn("NEW.TXT", names)

    def test_a_deleted_slot_behind_a_long_name_record_is_skipped(self):
        # A long-name record immediately before the deleted slot would survive
        # the reuse and attach to the new 8.3 name.  Make slot 1 an LFN record
        # and slot 2 a deleted entry: the create must not use slot 2.
        image = bytearray(fat32_images.build_fat32_image())
        g = fat32_images.volume_geometry(image)
        root = (PART_LBA + g["first_data"] + (g["root_cluster"] - 2) * g["spc"]) * SECTOR
        image[root + 32] = 0x41               # slot 1: a live long-name record
        image[root + 32 + 11] = 0x0F
        image[root + 64] = 0xE5               # slot 2: a deleted entry behind it
        fields, after, rc = self.create(bytes(image), "NEW.TXT", 0, ["--text", "x"])
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertNotEqual(fields["dir_offset"], "64",
                            "the slot behind a long-name record must be left alone")
        self.assertEqual(fields["dir_offset"], "320", "the next free slot is the end marker")
        names = [e["name"] for e in fat32_images.list_dir(after)]
        self.assertIn("NEW.TXT", names)

    def test_a_full_root_directory_is_grown_by_one_cluster(self):
        """M1i replaces M1g's refusal here, with a stronger contract.

        M1g refused to create a file when the root directory had no free slot
        (`dir-full`, nothing written). M1i grows the directory by one cluster
        instead — a FAT32 directory is an ordinary cluster chain, so appending
        a zeroed cluster is legal and every existing entry is untouched. The
        old assertion ("refused") would now be a lie about the implementation,
        so it is replaced by the property that matters: the create succeeds,
        the directory grows by exactly one cluster, every previous entry is
        still readable, the new file reads back, both FAT copies agree, and the
        changed sectors are confined to the data, the FATs, FSInfo, the existing
        root cluster and the new directory cluster.
        """
        image = close_directory(fat32_images.build_fat32_image(fill_root=True))
        g = fat32_images.volume_geometry(image)
        root_chain_before = fat32_images.cluster_chain(image, g["root_cluster"])
        fields, after, _rc = self.create(image, "GROWN.BIN", 512)
        self.assertEqual(fields.get("status"), "ok", fields.get("_stdout"))
        self.assertEqual(fields.get("grew"), "1")
        self.assertNotEqual(fields.get("writes"), "0")

        g2 = fat32_images.volume_geometry(after)
        root_chain_after = fat32_images.cluster_chain(after, g2["root_cluster"])
        self.assertEqual(len(root_chain_after), len(root_chain_before) + 1)
        self.assertEqual(root_chain_after[:len(root_chain_before)], root_chain_before,
                         "growth must append, never re-link the existing chain")
        self.assertEqual(int(fields["grown"]), root_chain_after[-1])
        # the new cluster is allocated exactly once and holds the entry
        fat = fat32_images.fat_entries(after)
        self.assertGreaterEqual(fat[root_chain_after[-1]], 0x0FFFFFF8)
        for copy in fat32_images.fat_copies(after):
            self.assertEqual(copy, fat32_images.fat_copies(after)[0])

        # every original file survives byte for byte, and the new one reads back
        for name, data in fat32_images.expected_files().items():
            self.assertEqual(fat32_images.read_file(after, name), data, name)
        self.assertEqual(fat32_images.read_file(after, "GROWN.BIN"),
                         bytes(((i * 17 + 5) & 0xFF) for i in range(512)))
        self.assertIn("GROWN.BIN", [e["name"] for e in fat32_images.list_dir(after)])

        allowed = allowed_change_sectors(g2)
        allowed |= {g2["part_lba"] + g2["first_data"] + (c - 2) * g2["spc"] + s
                    for c in root_chain_after for s in range(g2["spc"])}
        allowed |= {g2["part_lba"] + g2["first_data"] + (c - 2) * g2["spc"] + s
                    for c in fat32_images.cluster_chain(after, int(fields["first"]))
                    for s in range(g2["spc"])}
        self.assertEqual(fat32_images.changed_sectors(image, after) - allowed, set())

    def test_a_directory_that_cannot_grow_is_refused_without_writing(self):
        """The other half of the new contract: no free cluster, no create."""
        image = close_directory(fat32_images.build_fat32_image(fill_root=True))
        starved = leave_free_clusters(image, keep=0)
        fields, after, rc = self.create(starved, "NOPE.BIN", 0)
        self.assertEqual(rc, 1)
        self.assertEqual(fields.get("status"), "full", fields.get("_stdout"))
        self.assertEqual(fields.get("writes"), "0")
        self.assertEqual(after, starved)

    def test_invalid_names_are_refused(self):
        image = fat32_images.build_fat32_image()
        for name in ("BAD*NAME.TXT", "TOOLONGNAME.TXT", "A.BBBB", "", "name?.txt"):
            with self.subTest(name=name):
                fields, after, rc = self.create(image, name, 10)
                self.assertEqual(fields.get("status"), "name", fields["_stdout"])
                self.assertEqual(after, image)

    def test_a_read_only_source_refuses_to_write(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "RO.TXT", 0, ["--text", "x"],
                                        options=["--read-only-io"])
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "read-only")
        self.assertEqual(after, image)

    def test_writes_stay_inside_the_partition(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "BOUND.TXT", 3000)
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertTrue(fields["_lbas"], "no writes were logged")
        for lba, count in fields["_lbas"]:
            self.assertGreaterEqual(lba, PART_LBA, "a write landed before the partition")
            self.assertLess(lba, PART_LBA + PART_SECTORS, "a write landed past the partition")
            self.assertEqual(count, 1, "sector writes are single-sector by design")

    def test_two_identical_creates_produce_identical_images(self):
        image = fat32_images.build_fat32_image()
        _, first, rc1 = self.create(image, "SAME.TXT", 700)
        _, second, rc2 = self.create(image, "SAME.TXT", 700)
        self.assertEqual((rc1, rc2), (0, 0))
        self.assertEqual(first, second, "the create is not deterministic")

    # ------------------------------------------------- FSInfo behaviour

    def test_fsinfo_free_count_is_recomputed_not_trusted(self):
        image = fat32_images.build_fat32_image()
        self.assertEqual(fat32_images.fsinfo_fields(image)["free_count"], 65504)
        actual = fat32_images.free_cluster_count(image)
        self.assertEqual(actual, 65510)
        fields, after, rc = self.create(image, "FS.TXT", 700)
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["fsinfo_claimed"], "65504")
        self.assertEqual(fat32_images.fsinfo_fields(after)["free_count"], actual - 2)
        self.assertEqual(fat32_images.fsinfo_fields(after)["free_count"],
                         fat32_images.free_cluster_count(after))
        self.assertEqual(fat32_images.fsinfo_fields(after, sector=7)["free_count"],
                         fat32_images.free_cluster_count(after),
                         "the backup FSInfo must be updated too")

    def test_an_implausible_fsinfo_is_not_trusted_and_not_rewritten(self):
        image = fat32_images.build_fat32_image()
        broken = set_fsinfo(image, "free_count", 0xFFFFFFFF)   # > cluster count
        before_block = broken[(PART_LBA + 1) * SECTOR:(PART_LBA + 2) * SECTOR]
        fields, after, rc = self.create(broken, "NFS.TXT", 100)
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["fsinfo_valid"], "0")
        self.assertEqual(fields["fsinfo_updated"], "0")
        self.assertEqual(after[(PART_LBA + 1) * SECTOR:(PART_LBA + 2) * SECTOR],
                         before_block, "an untrusted FSInfo block must be left alone")
        self.assertEqual(fat32_images.read_file(after, "NFS.TXT"),
                         bytes(((i * 17 + 5) & 0xFF) for i in range(100)))

    def test_a_missing_fsinfo_signature_is_survivable(self):
        image = fat32_images.build_fat32_image()
        broken = set_fsinfo(image, "trail", 0)
        fields, after, rc = self.create(broken, "SIG.TXT", 0, ["--text", "ok"])
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["fsinfo_valid"], "0")
        self.assertEqual(fat32_images.read_file(after, "SIG.TXT"), b"ok")

    # ------------------------------------------------- flush behaviour

    def test_a_missing_flush_callback_is_reported_not_assumed(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "NF.TXT", 0, ["--text", "x"],
                                        options=["--no-flush"])
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["flush"], "0", "an absent flush must not look durable")
        self.assertEqual(fat32_images.read_file(after, "NF.TXT"), b"x")

    def test_a_failed_flush_is_an_error_even_though_the_entry_exists(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "FF.TXT", 0, ["--text", "x"],
                                        options=["--fail-flush"])
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "flush")
        self.assertEqual(fields["verified"], "0")
        self.assertTrue(fields["_stdout"].count("status=flush") == 1)

    # ------------------------------------------------- fault injection

    def test_failure_on_the_first_data_write_writes_nothing(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "F1.BIN", 700, options=["--fail-write", "0"])
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "io")
        self.assertEqual(fields["stage"], "3")          # DATA
        self.assertEqual(after, image, "no sector may change when the first write fails")

    def test_failure_before_the_directory_entry_leaks_only_clusters(self):
        image = fat32_images.build_fat32_image()
        # 700 bytes = 2 clusters; write order is data(0,1), FAT copies for both
        # clusters (2,3,4,5), FSInfo (6), backup FSInfo (7), directory entry (8)
        fields, after, rc = self.create(image, "F2.BIN", 700, options=["--fail-write", "8"])
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "io")
        self.assertEqual(fields["stage"], "7")          # DIR in progress
        state = classify(image, after, "F2.BIN")
        self.assertFalse(state["exists"], "a half-created file must not appear")
        self.assertTrue(state["originals_intact"], state["broken_originals"])
        self.assertEqual(len(state["orphans"]), 2, "the only damage is a leaked chain")
        self.assertTrue(state["mirrors_equal"])
        self.assertEqual(fat32_images.fat_entries(after)[state["orphans"][0]],
                         state["orphans"][1])
        self.assertEqual(fat32_images.fat_entries(after)[state["orphans"][1]], 0x0FFFFFFF)

    def test_failure_between_fat_copies_is_reported_as_a_broken_mirror(self):
        image = fat32_images.build_fat32_image()
        # index 2 is the first FAT copy of the first cluster, 3 the second copy
        fields, after, rc = self.create(image, "F3.BIN", 700, options=["--fail-write", "3"])
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "io")
        self.assertEqual(fields["stage"], "4")          # FAT
        copies = fat32_images.fat_copies(after)
        self.assertNotEqual(copies[0], copies[1],
                            "the first FAT received the link, the second did not")
        state = classify(image, after, "F3.BIN")
        self.assertFalse(state["exists"])
        self.assertTrue(state["originals_intact"])
        self.assertTrue(state["fat_ok"], "all entries are still legal values")

    def test_corrupted_data_is_caught_by_the_read_back(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "F4.BIN", 700, options=["--corrupt-write", "0"])
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "verify")
        self.assertEqual(fields["verified"], "0")
        state = classify(image, after, "F4.BIN")
        self.assertTrue(state["exists"], "the entry was written before verification")
        self.assertNotEqual(state["content"], bytes(((i * 17 + 5) & 0xFF) for i in range(700)),
                            "a corrupted create must not read back as the intended bytes")
        self.assertTrue(state["originals_intact"])

    def test_every_write_failure_still_leaves_the_original_files_readable(self):
        image = fat32_images.build_fat32_image()
        for index in range(0, 7):
            with self.subTest(fail_write=index):
                fields, after, rc = self.create(
                    image, "FX.BIN", 700, options=["--fail-write", str(index)])
                self.assertEqual(rc, 1, fields["_stdout"])
                state = classify(image, after, "FX.BIN")
                self.assertTrue(state["originals_intact"], state["broken_originals"])
                self.assertFalse(state["exists"] or state.get("error"),
                                 "a failed create must not leave a readable entry")


class HarnessSafetyTest(unittest.TestCase):
    """The write path must be reachable only from the harness, with images we made."""

    def test_only_the_put_command_can_create_a_file(self):
        src = read("kernel/fat32.c")
        self.assertEqual(src.count("int fat32_create_mounted("), 1)
        calls = []
        for name in ("kernel/ui.c", "kernel/main.c", "kernel/storage_test.c"):
            body = read(name)
            if "fat32_create_mounted" in body:
                calls.append(name)
        self.assertEqual(calls, ["kernel/ui.c"],
                         "only the explicit console command may write to a filesystem")
        ui = read("kernel/ui.c")
        branch = ui.split('!strncmp(cmd, "put ", 4)')[1].split("} else if")[0]
        self.assertIn("fat32_create_mounted", branch)
        self.assertNotIn("fat32_create_mounted", read("kernel/main.c"),
                         "nothing on the boot path may create files")

    def test_the_boot_test_only_attaches_generated_images(self):
        src = read("tests/boot_test.py")
        # every disk handed to the emulator comes from the fixture builder and
        # lives in the run's output directory: never a host block device
        self.assertIn("fat32_images.build_fat32_image()", src)
        self.assertIn("os.path.join(args.out_dir", src)
        for forbidden in ("/dev/sd", "/dev/hd", "/dev/nvme", "/dev/disk"):
            self.assertNotIn(forbidden, src)


class ReadOnlyRegressionTest(unittest.TestCase):
    """The write path must stay a separate, explicitly requested operation."""

    def test_boot_mount_attaches_no_write_callbacks(self):
        src = read("kernel/fat32.c")
        mount = src.split("int fat32_mount_partition(int device)")[1]
        mount = mount.split("int fat32_list_mounted")[0]
        self.assertNotIn(".write", mount,
                         "the boot-time mount must never attach a write callback")
        self.assertNotIn("ata_sink", mount)

    def test_only_the_device_layer_touches_the_disk(self):
        src = read("kernel/fat32.c")
        host, device = src.split("#ifndef PC5_HOST_TEST", 1)
        self.assertNotIn("ata_write_sectors", host)
        self.assertNotIn("ata_flush(", host)
        self.assertIn("ata_write_sectors", device)
        self.assertIn("ata_flush(", device)
        self.assertNotIn("outb(", host, "the filesystem must not do port I/O")

    def test_creating_requires_an_explicit_call(self):
        src = read("kernel/fat32.c")
        # exactly one definition and one caller inside the module
        self.assertEqual(src.count("int fat32_create_file("), 1)
        self.assertEqual(src.count("fat32_create_file(&io"), 1)
        # the create entry point needs a write callback; there is no fallback
        body = src.split("int fat32_create_file(")[1]
        self.assertIn("if (!io->write)", body)
        self.assertIn("return FAT32_E_READ_ONLY", body)

    def test_flush_is_explicit_and_checked(self):
        src = read("kernel/ata.c")
        self.assertIn("int ata_flush(int device)", src)
        self.assertIn("0xe7", src)
        self.assertIn("BLK_E_READ_ONLY", src.split("int ata_flush(int device)")[1])

    def test_existing_read_tests_still_pass_unchanged(self):
        # `make check` runs them; this is the guard that they were not deleted.
        test = read("tests/test_fat32.py")
        self.assertIn("test_kernel_reads_every_fixture_file_correctly", test)
        self.assertIn("test_a_fat16_bpb_is_refused_not_guessed", test)


if __name__ == "__main__":
    unittest.main()
