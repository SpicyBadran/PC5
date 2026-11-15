#!/usr/bin/env python3
"""M1i: overwriting a file, creating a directory, and the paths that reach them.

Every test has the same shape as M1g's, for the same reason: the question is
never "did the kernel say ok", it is "did the disk end up in a state a second
implementation can prove".  So each test

  1. builds a deterministic FAT32 image,
  2. lets `kernel/fat32.c` (compiled as a host program) perform one operation,
  3. reads the *resulting image* with `tests/fat32_images.py`, which shares no
     code with the kernel and now walks directories too,
  4. asserts content, chains, both FAT copies, FSInfo, "." and "..", and that
     every unrelated file is byte-identical,
  5. for the fault-injection tests, fails each write in turn and classifies what
     the volume looks like afterwards.

The two properties this file exists to defend, in the order the milestone ranks
them:

  * data integrity — no failure may ever damage an unrelated file, and no
    cluster may ever belong to two objects;
  * the failure model — before the single directory-entry write the old file is
    intact, after it the new content is complete, and the only possible damage
    is a leaked chain, which is counted.

Power-loss atomicity is *not* claimed anywhere here: a failing write call is not
a power cut, and the tests say so where it matters.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import sys  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fat32_images  # noqa: E402

SOURCES = ["tests/fat32_write_check.c", "kernel/fat32.c", "kernel/sha256.c",
           "kernel/lib.c", "kernel/mem.c", "kernel/chain.c"]

PART_LBA = 2048
PART_SECTORS = 66592
SECTOR = 512


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def pattern(size: int, seed: int = 5) -> bytes:
    """The driver's deterministic payload (tests/fat32_write_check.c)."""
    return bytes(((i * 17 + seed) & 0xFF) for i in range(size))


def expected_root_files() -> dict:
    return fat32_images.expected_files()


def expected_subdir_files() -> dict:
    inner = bytes((i * 11 + 7) & 0xFF for i in range(700))
    return {"SUBDIR/INNER.TXT": inner}


def region_of(lba: int, g: dict, dir_clusters: set[int]) -> str:
    """Which part of the volume one written sector belongs to."""
    rel = lba - g["part_lba"]
    if rel in (g["fsinfo"], g["backup"] + 1):
        return "fsinfo"
    if g["reserved"] <= rel < g["reserved"] + g["nfats"] * g["fat_sectors"]:
        return f"fat{(rel - g['reserved']) // g['fat_sectors']}"
    if rel >= g["first_data"]:
        cluster = 2 + (rel - g["first_data"]) // g["spc"]
        return f"dir{cluster}" if cluster in dir_clusters else "data"
    return "meta"


def classify(before: bytes, after: bytes, path: str, part_lba: int = PART_LBA) -> dict:
    """What state did the volume end up in?  Pure observation, no assertions.

    The "unrelated files" oracle is the *before* image read by the independent
    reader, not a list hard-coded in this file: a fixture change then cannot
    quietly weaken the check, and a test on a volume that holds subdirectories
    compares exactly the files that volume had.
    """
    info = {}
    originals = fat32_images.all_files(before, part_lba)
    originals.pop(path, None)          # the target is what the operation changes
    broken = []
    for name, data in originals.items():
        try:
            ok = fat32_images.read_path(after, name, part_lba) == data
        except Exception:
            ok = False
        if not ok:
            broken.append(name)
    info["originals_intact"] = not broken
    info["broken_originals"] = broken
    info["originals"] = sorted(originals)
    info["after"] = after

    try:
        info["target"] = fat32_images.read_path(after, path, part_lba)
        info["target_exists"] = True
    except KeyError:
        info["target"] = None
        info["target_exists"] = False
    except Exception as exc:                       # unreadable is not "absent"
        info["target"] = None
        info["target_exists"] = True
        info["target_error"] = str(exc)

    info["orphans"] = sorted(fat32_images.orphan_clusters(after, part_lba))
    info["double"] = sorted(fat32_images.double_allocated(after, part_lba))
    g = fat32_images.volume_geometry(after, part_lba)
    fat = fat32_images.fat_entries(after, part_lba)
    info["fat_valid"] = all(c < g["clusters"] + 2 or fat[c] >= 0x0FFFFFF8
                            for c in range(2, g["clusters"] + 2))
    copies = fat32_images.fat_copies(after, part_lba)
    info["mirrors_equal"] = all(c == copies[0] for c in copies)
    info["changed"] = fat32_images.changed_sectors(before, after)
    info["allocated"] = fat32_images.allocated_clusters(after, part_lba)
    return info


def allowed_change_sectors(g: dict, extra_chains=()) -> set[int]:
    """Every sector a create/overwrite/mkdir may legitimately touch."""
    base = g["part_lba"]
    out = {base + g["fsinfo"], base + g["backup"] + 1}
    for i in range(g["nfats"]):
        for s in range(g["fat_sectors"]):
            out.add(base + g["reserved"] + i * g["fat_sectors"] + s)
    for chain in extra_chains:
        for c in chain:
            for s in range(g["spc"]):
                out.add(base + g["first_data"] + (c - 2) * g["spc"] + s)
    return out


class ExpandDriver(unittest.TestCase):
    """Builds the host driver once; each test works on its own image."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("gcc"):
            raise unittest.SkipTest("gcc is required to build the host driver")
        cls.tmp = os.path.join(ROOT, "build", "test", "m1i")
        os.makedirs(cls.tmp, exist_ok=True)
        cls.binary = os.path.join(cls.tmp, "fat32-write-check")
        cmd = ["gcc", "-std=gnu11", "-O1", "-Wall", "-Wextra",
               "-Wno-unused-parameter", "-ffreestanding", "-fno-builtin",
               "-fno-stack-protector", "-DPC5_HOST_TEST",
               "-I", os.path.join(ROOT, "kernel", "include"),
               "-o", cls.binary] + [os.path.join(ROOT, s) for s in SOURCES]
        cls.build = subprocess.run(cmd, capture_output=True, text=True)

    def run_op(self, image, path, size=0, op="create", payload=(), options=(),
               part_sectors=PART_SECTORS):
        """Run one operation; return (report fields, resulting image, exit code)."""
        if not os.path.exists(self.binary):
            self.fail("driver was not built:\n" + self.build.stderr[-3000:])
        src = os.path.join(self.tmp, "in.img")
        dst = os.path.join(self.tmp, "out.img")
        lba_log = os.path.join(self.tmp, "lba.txt")
        for p in (dst, lba_log):
            if os.path.exists(p):
                os.unlink(p)
        with open(src, "wb") as fh:
            fh.write(image)
        cmd = [self.binary, src, str(PART_LBA), str(part_sectors), path, str(size),
               "--out", dst, "--lba-log", lba_log]
        if op == "overwrite":
            cmd.append("--overwrite")
        elif op == "mkdir":
            cmd.append("--mkdir")
        elif op == "read":
            cmd.append("--read")
        cmd += list(payload) + list(options)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        after = image
        if os.path.exists(dst):
            with open(dst, "rb") as fh:
                after = fh.read()
        fields = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] in ("create", "overwrite", "mkdir", "read"):
                fields = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        writes = []
        if os.path.exists(lba_log):
            with open(lba_log, encoding="ascii") as fh:
                values = [int(v) for v in fh.read().split()]
            writes = [(values[i], values[i + 1]) for i in range(0, len(values), 2)]
        fields["_lbas"] = writes
        fields["_stdout"] = proc.stdout
        return fields, after, proc.returncode

    # ---- convenience wrappers ------------------------------------------

    def create(self, image, path, size=0, **kw):
        return self.run_op(image, path, size, "create", **kw)

    def overwrite(self, image, path, size=0, **kw):
        return self.run_op(image, path, size, "overwrite", **kw)

    def mkdir(self, image, path, **kw):
        return self.run_op(image, path, 0, "mkdir", **kw)


# --------------------------------------------------------------------------
# Feature A: overwrite
# --------------------------------------------------------------------------

class OverwriteTests(ExpandDriver):
    def test_shorter_replacement_shrinks_the_chain_and_frees_the_rest(self):
        image = fat32_images.build_fat32_image()
        before_chain = fat32_images.cluster_chain(image, 3)   # PC5TEST.TXT, 6 clusters
        fields, after, rc = self.overwrite(image, "PC5TEST.TXT", 100,
                                           payload=["--pattern"])
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["status"], "ok")
        self.assertEqual(fields["clusters"], "1")
        self.assertEqual(fields["old_clusters"], str(len(before_chain)))
        self.assertEqual(fields["freed"], str(len(before_chain)))
        self.assertEqual(fat32_images.read_path(after, "PC5TEST.TXT"), pattern(100))
        # every cluster of the old chain is free again
        fat = fat32_images.fat_entries(after)
        for c in before_chain:
            self.assertEqual(fat[c], 0, f"cluster {c} was not returned to free space")
        self.assertEqual(fat32_images.double_allocated(after), set())

    def test_longer_replacement_grows_the_chain(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.overwrite(image, "SMALL.BIN", 1500)
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["clusters"], "3")
        self.assertEqual(fields["old_clusters"], "1")
        self.assertEqual(fat32_images.read_path(after, "SMALL.BIN"), pattern(1500))
        self.assertEqual(len(fat32_images.cluster_chain(after, int(fields["first"]))), 3)
        self.assertEqual(fat32_images.orphan_clusters(after), set())

    def test_same_size_replacement_changes_content_only(self):
        image = fat32_images.build_fat32_image()
        old = fat32_images.read_path(image, "EXACT.BIN")
        fields, after, rc = self.overwrite(image, "EXACT.BIN", 512)
        self.assertEqual(rc, 0, fields["_stdout"])
        new = fat32_images.read_path(after, "EXACT.BIN")
        self.assertEqual(new, pattern(512))
        self.assertNotEqual(new, old)
        self.assertEqual(fat32_images.list_path(after, "") != [], True)

    def test_empty_replacement_frees_the_whole_chain(self):
        image = fat32_images.build_fat32_image()
        old_first = next(e["first_cluster"] for e in fat32_images.list_path(image, "")
                         if e["name"] == "PC5TEST.TXT")
        old_chain = fat32_images.cluster_chain(image, old_first)
        self.assertEqual(len(old_chain), 6)
        fields, after, rc = self.overwrite(image, "PC5TEST.TXT", 0)
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["clusters"], "0")
        self.assertEqual(fields["first"], "0")
        self.assertEqual(fields["freed"], str(len(old_chain)))
        self.assertEqual(fat32_images.read_path(after, "PC5TEST.TXT"), b"")
        fat = fat32_images.fat_entries(after)
        for c in old_chain:
            self.assertEqual(fat[c], 0)

    def test_an_empty_file_can_receive_content(self):
        image = fat32_images.build_fat32_image()
        self.assertEqual(fat32_images.read_path(image, "EMPTY.TXT"), b"")
        fields, after, rc = self.overwrite(image, "EMPTY.TXT", 700)
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["old_clusters"], "0")
        self.assertEqual(fat32_images.read_path(after, "EMPTY.TXT"), pattern(700))

    def test_a_fragmented_chain_is_reclaimed_completely(self):
        image = fat32_images.build_fat32_image()
        chain = fat32_images.cluster_chain(image, 21)          # FRAG.BIN
        self.assertEqual(chain, sorted(chain, key=lambda c: [21, 23, 22, 24].index(c)))
        fields, after, rc = self.overwrite(image, "FRAG.BIN", 8, payload=["--text", "frag-m1i"])
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["old_clusters"], "4")
        self.assertEqual(fields["freed"], "4")
        fat = fat32_images.fat_entries(after)
        for c in chain:
            self.assertEqual(fat[c], 0, f"fragmented cluster {c} leaked")
        self.assertEqual(fat32_images.read_path(after, "FRAG.BIN"), b"frag-m1i")

    def test_the_entry_keeps_its_name_slot_and_attributes(self):
        image = fat32_images.build_fat32_image()
        before = next(e for e in fat32_images._dir_entries(image, 2) if e["name"] == "MULTI.BIN")
        fields, after, rc = self.overwrite(image, "MULTI.BIN", 600)
        self.assertEqual(rc, 0, fields["_stdout"])
        after_ent = next(e for e in fat32_images._dir_entries(after, 2)
                         if e["name"] == "MULTI.BIN")
        self.assertEqual(after_ent["slot_lba"], before["slot_lba"])
        self.assertEqual(after_ent["slot_offset"], before["slot_offset"])
        self.assertEqual(after_ent["attr"], before["attr"])
        self.assertEqual(after_ent["size"], 600)
        self.assertEqual(len(fat32_images.list_path(after, "")),
                         len(fat32_images.list_path(image, "")))

    def test_unrelated_files_and_their_clusters_are_untouched(self):
        image = fat32_images.build_fat32_image()
        g = fat32_images.volume_geometry(image)
        fields, after, rc = self.overwrite(image, "MULTI.BIN", 1200)
        self.assertEqual(rc, 0, fields["_stdout"])
        new_chain = fat32_images.cluster_chain(after, int(fields["first"]))
        allowed = allowed_change_sectors(g, extra_chains=[new_chain] +
                                         [[2], [3, 4, 5, 6, 7, 8], [9], [10], [21, 23, 22, 24], [25]])
        changed = fat32_images.changed_sectors(image, after)
        self.assertEqual(changed - allowed, set(),
                         f"unexpected sectors changed: {sorted(changed - allowed)}")

    def test_fsinfo_matches_the_fat_after_an_overwrite(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.overwrite(image, "MULTI.BIN", 400)
        self.assertEqual(rc, 0, fields["_stdout"])
        claimed = fat32_images.fsinfo_fields(after)["free_count"]
        actual = fat32_images.free_cluster_count(after)
        self.assertEqual(claimed, actual)

    # ---- refusals -------------------------------------------------------

    def test_a_missing_file_is_refused_without_writing(self):
        image = fat32_images.build_fat32_image()
        for path in ("NOPE.TXT", "MISSING/X.TXT"):
            with self.subTest(path=path):
                fields, after, rc = self.overwrite(image, path, 10)
                self.assertEqual(rc, 1)
                self.assertEqual(fields["status"], "no-file", fields["_stdout"])
                self.assertEqual(fields["writes"], "0")
                self.assertEqual(after, image)

    def test_a_directory_is_not_a_file(self):
        image = fat32_images.build_fat32_image(valid_subdir=True)
        for path in ("SUBDIR", "SUBDIR/"):
            with self.subTest(path=path):
                fields, after, rc = self.overwrite(image, path, 10)
                self.assertEqual(rc, 1)
                self.assertIn(fields["status"], ("directory", "name"))
                self.assertEqual(fields["writes"], "0")
                self.assertEqual(after, image)

    def test_a_read_only_entry_is_refused(self):
        image = bytearray(fat32_images.build_fat32_image())
        g = fat32_images.volume_geometry(bytes(image))
        # set the read-only attribute on SMALL.BIN's entry
        for ent in fat32_images._dir_entries(bytes(image), g["root_cluster"]):
            if ent["name"] == "SMALL.BIN":
                off = ent["slot_lba"] * SECTOR + ent["slot_offset"] + 11
                image[off] |= 0x01
                break
        else:
            self.fail("SMALL.BIN not found in the fixture")
        fields, after, rc = self.overwrite(bytes(image), "SMALL.BIN", 10)
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "read-only", fields["_stdout"])
        self.assertEqual(fields["writes"], "0")
        self.assertEqual(after, bytes(image))

    def test_a_replacement_too_large_for_the_chain_limit_is_refused(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.overwrite(image, "SMALL.BIN", 128 * 512 + 1)
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "too-big")
        self.assertEqual(fields["writes"], "0")
        self.assertEqual(after, image)

    def test_a_volume_without_room_for_both_versions_is_refused(self):
        """Overwrite trades space for integrity: it needs both chains at once."""
        image = fat32_images.build_fat32_image()
        starved = fat32_images_leave_free(image, keep=2)   # enough for one chain only
        fields, after, rc = self.overwrite(starved, "MULTI.BIN", 1536)  # 3 clusters
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "full", fields["_stdout"])
        self.assertEqual(fields["writes"], "0")
        self.assertEqual(after, starved)
        self.assertEqual(fat32_images.read_path(after, "MULTI.BIN"),
                         fat32_images.expected_files()["MULTI.BIN"])

    def test_a_read_only_source_refuses_an_overwrite(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.overwrite(image, "SMALL.BIN", 10,
                                           options=["--read-only-io"])
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "read-only")
        self.assertEqual(after, image)

    def test_two_identical_overwrites_produce_identical_images(self):
        image = fat32_images.build_fat32_image()
        _, a, _ = self.overwrite(image, "PC5TEST.TXT", 800)
        _, b, _ = self.overwrite(image, "PC5TEST.TXT", 800)
        self.assertEqual(a, b)

    def test_the_written_sectors_are_inside_the_partition(self):
        image = fat32_images.build_fat32_image()
        fields, _after, _rc = self.overwrite(image, "PC5TEST.TXT", 800)
        g = fat32_images.volume_geometry(image)
        for lba, _count in fields["_lbas"]:
            self.assertGreaterEqual(lba, g["part_lba"])
            self.assertLess(lba, g["part_lba"] + PART_SECTORS)


def fat32_images_leave_free(image: bytes, keep: int) -> bytes:
    """Mark every free cluster allocated except `keep` of them."""
    import struct
    g = fat32_images.volume_geometry(image)
    referenced = fat32_images.referenced_clusters(image)
    out = bytearray(image)
    for i in range(g["nfats"]):
        start = (g["part_lba"] + g["reserved"] + i * g["fat_sectors"]) * SECTOR
        kept = 0
        for c in range(2, g["clusters"] + 2):
            if c in referenced:
                continue
            kept += 1
            if kept > keep:
                struct.pack_into("<I", out, start + c * 4, 0x0FFFFFFF)
    return bytes(out)


# --------------------------------------------------------------------------
# Feature B: directories
# --------------------------------------------------------------------------

class DirectoryTests(ExpandDriver):
    def test_creating_a_directory_at_the_root(self):
        image = fat32_images.build_fat32_image()
        free_before = fat32_images.free_cluster_count(image)
        fields, after, rc = self.mkdir(image, "NEWDIR")
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["status"], "ok")
        self.assertEqual(fields["clusters"], "1")
        self.assertEqual(fields["grew"], "0")
        self.assertEqual(fields["verified"], "1")

        info = fat32_images.check_directory(after, "NEWDIR")
        self.assertTrue(info["dot_ok"], "'.' must name the directory itself")
        self.assertTrue(info["dotdot_ok"], "'..' must name the parent (root = cluster 0)")
        self.assertEqual(info["dotdot"], 0)
        self.assertEqual(info["duplicates"], [])
        self.assertEqual(info["live"], [], "a new directory holds nothing but . and ..")
        self.assertEqual(len(info["chain"]), 1)

        self.assertEqual(fat32_images.free_cluster_count(after), free_before - 1)
        self.assertEqual(fat32_images.double_allocated(after), set())
        self.assertEqual(fat32_images.orphan_clusters(after), set())
        self.assertEqual(fat32_images.fsinfo_fields(after)["free_count"],
                         fat32_images.free_cluster_count(after))
        for name, data in expected_root_files().items():
            self.assertEqual(fat32_images.read_path(after, name), data, name)

    def test_creating_a_directory_inside_an_existing_one(self):
        image = fat32_images.build_fat32_image(valid_subdir=True)
        parent = fat32_images.resolve_dir(image, "SUBDIR")
        fields, after, rc = self.mkdir(image, "SUBDIR/DEEP")
        self.assertEqual(rc, 0, fields["_stdout"])
        info = fat32_images.check_directory(after, "SUBDIR/DEEP")
        self.assertTrue(info["dot_ok"])
        self.assertTrue(info["dotdot_ok"])
        self.assertEqual(info["dotdot"], parent, "'..' must name SUBDIR")
        self.assertEqual(fat32_images.read_path(after, "SUBDIR/INNER.TXT"),
                         expected_subdir_files()["SUBDIR/INNER.TXT"])

    def test_a_new_directory_is_readable_and_accepts_a_file_immediately(self):
        image = fat32_images.build_fat32_image()
        fields, after, _rc = self.mkdir(image, "NEWDIR")
        self.assertEqual(fields["status"], "ok")
        self.assertEqual(fat32_images.list_path(after, "NEWDIR"), [])
        fields, after2, rc = self.create(after, "NEWDIR/FILE.TXT", 42)
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fat32_images.read_path(after2, "NEWDIR/FILE.TXT"), pattern(42))
        info = fat32_images.check_directory(after2, "NEWDIR")
        self.assertTrue(info["dot_ok"] and info["dotdot_ok"])
        self.assertEqual([e["name"] for e in info["live"]], ["FILE.TXT"])

    def test_nested_directories_three_levels_deep(self):
        image = fat32_images.build_fat32_image(valid_subdir=True)
        fields, img1, _ = self.mkdir(image, "SUBDIR/A")
        self.assertEqual(fields["status"], "ok", fields["_stdout"])
        fields, img2, _ = self.mkdir(img1, "SUBDIR/A/B")
        self.assertEqual(fields["status"], "ok", fields["_stdout"])
        fields, img3, rc = self.create(img2, "SUBDIR/A/B/C.TXT", 20)
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fat32_images.read_path(img3, "SUBDIR/A/B/C.TXT"), pattern(20))
        deep = fat32_images.check_directory(img3, "SUBDIR/A/B")
        self.assertEqual(deep["dotdot"], fat32_images.resolve_dir(img3, "SUBDIR/A"))
        self.assertEqual(fat32_images.double_allocated(img3), set())
        self.assertEqual(fat32_images.orphan_clusters(img3), set())

    def test_a_full_root_directory_grows_to_hold_a_new_directory(self):
        image = _closed_full_root()
        g = fat32_images.volume_geometry(image)
        root_before = fat32_images.cluster_chain(image, g["root_cluster"])
        fields, after, rc = self.mkdir(image, "GROWNDIR")
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["grew"], "1")
        g2 = fat32_images.volume_geometry(after)
        root_after = fat32_images.cluster_chain(after, g2["root_cluster"])
        self.assertEqual(root_after[:len(root_before)], root_before)
        self.assertEqual(len(root_after), len(root_before) + 1)
        info = fat32_images.check_directory(after, "GROWNDIR")
        self.assertTrue(info["dot_ok"] and info["dotdot_ok"])
        # every pre-existing entry is still there and still correct
        names = [e["name"] for e in fat32_images.list_path(after, "")]
        self.assertIn("GROWNDIR", names)
        for name in ("PC5TEST.TXT", "SMALL.BIN", "EXACT.BIN", "EMPTY.TXT",
                     "MULTI.BIN", "FRAG.BIN", "SUBDIR"):
            self.assertIn(name, names)
        self.assertEqual(fat32_images.read_path(after, "PC5TEST.TXT"),
                         expected_root_files()["PC5TEST.TXT"])

    def test_a_directory_with_no_free_slot_grows_when_a_file_is_created_in_it(self):
        image = _closed_full_root()
        fields, after, rc = self.create(image, "MANY.TXT", 5, payload=["--text", "hello"])
        self.assertEqual(rc, 0, fields["_stdout"])
        self.assertEqual(fields["grew"], "1")
        self.assertEqual(fat32_images.read_path(after, "MANY.TXT"), b"hello")

    def test_a_parent_that_cannot_grow_refuses_the_directory(self):
        image = fat32_images_leave_free(_closed_full_root(), keep=1)  # 1 free: the dir itself
        fields, after, rc = self.mkdir(image, "NOPE")
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "full", fields["_stdout"])
        self.assertEqual(fields["writes"], "0")
        self.assertEqual(after, image)

    def test_directory_refusals(self):
        image = fat32_images.build_fat32_image(valid_subdir=True)
        cases = {
            "SUBDIR": "exists",
            "PC5TEST.TXT": "exists",
            "MISSING/X": "no-file",
            "PC5TEST.TXT/X": "not-dir",
            "BAD*NAME": "name",
            "TOOLONGNAME": "name",
            ".": "name",
            "..": "name",
            "A/": "name",
            "/A": "name",
            "A//B": "name",
            "A/B/C/D/E/F/G/H/I": "name",
        }
        for path, status in cases.items():
            with self.subTest(path=path):
                fields, after, rc = self.mkdir(image, path)
                self.assertEqual(rc, 1)
                self.assertEqual(fields["status"], status, fields["_stdout"])
                self.assertEqual(fields["writes"], "0")
                self.assertEqual(after, image)

    def test_a_directory_that_grew_keeps_the_end_of_directory_marker(self):
        image = _closed_full_root()
        fields, after, _ = self.mkdir(image, "GROWNDIR")
        self.assertEqual(fields["status"], "ok")
        g = fat32_images.volume_geometry(after)
        new_cluster = fat32_images.cluster_chain(after, g["root_cluster"])[-1]
        start = (g["part_lba"] + g["first_data"] + (new_cluster - 2) * g["spc"]) * SECTOR
        block = after[start:start + g["spc"] * SECTOR]
        # the entry we added is the first slot; the rest must be a zeroed end marker
        self.assertEqual(block[32:], bytes(len(block) - 32))


def _diverged_clusters(image: bytes, clusters) -> set[int]:
    """Clusters whose FAT entry is not identical in every FAT copy."""
    copies = fat32_images.fat_copies(image)
    out = set()
    for c in clusters:
        entries = {copy[c * 4:c * 4 + 4] for copy in copies}
        if len(entries) > 1:
            out.add(c)
    return out


def _closed_full_root() -> bytes:
    """The fixture's root directory with every slot live (no free slot, no 0x00)."""
    import struct
    image = bytearray(fat32_images.build_fat32_image(fill_root=True))
    g = fat32_images.volume_geometry(bytes(image))
    for ent in fat32_images._dir_entries(bytes(image), g["root_cluster"]):
        if ent["deleted"]:
            off = ent["slot_lba"] * SECTOR + ent["slot_offset"]
            image[off] = ord("D")          # revive it: the slot is taken now
            break
    return bytes(image)


# --------------------------------------------------------------------------
# Feature C: paths
# --------------------------------------------------------------------------

class PathTests(ExpandDriver):
    def test_lookup_is_case_insensitive_in_every_component(self):
        image = fat32_images.build_fat32_image(valid_subdir=True)
        fields, after, rc = self.create(image, "subdir/New.txt", 3, payload=["--text", "abc"])
        self.assertEqual(rc, 0, fields["_stdout"])
        for spelling in ("SUBDIR/NEW.TXT", "subdir/new.txt", "SubDir/New.Txt"):
            self.assertEqual(fat32_images.read_path(after, spelling), b"abc", spelling)
        # ... and the name is stored upper case, not as typed
        names = [e["name"] for e in fat32_images.list_path(after, "SUBDIR")]
        self.assertIn("NEW.TXT", names)

    def test_path_rejections_in_the_read_and_write_paths(self):
        image = fat32_images.build_fat32_image(valid_subdir=True)
        bad = ["/SUBDIR", "SUBDIR/", "SUBDIR//X", "a" * 70, "SUBDIR/./X", "SUBDIR/../X"]
        for path in bad:
            with self.subTest(path=path):
                fields, after, rc = self.create(image, path, 1, payload=["--text", "x"])
                self.assertEqual(rc, 1)
                self.assertEqual(fields["status"], "name", fields["_stdout"])
                self.assertEqual(after, image)
        fields, after, rc = self.read_probe(image, "SUBDIR/../PC5TEST.TXT")
        self.assertEqual(fields["status"], "name")
        self.assertEqual(after, image)

    def read_probe(self, image, path):
        """Read through the kernel's own read path (`--read`, no write callback)."""
        return self.run_op(image, path, 0, "read")

    def test_eight_levels_are_allowed_and_nine_are_not(self):
        image = fat32_images.build_fat32_image()
        img = image
        path = ""
        for i in range(8):
            path = f"{path}/D{i}" if path else f"D{i}"
            fields, img, rc = self.mkdir(img, path)
            self.assertEqual(rc, 0, f"level {i + 1}: {fields['_stdout']}")
        fields, after, rc = self.mkdir(img, path + "/D8")
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "name")
        self.assertEqual(after, img)

    def test_a_missing_intermediate_directory_is_not_created_implicitly(self):
        image = fat32_images.build_fat32_image()
        fields, after, rc = self.create(image, "A/B/C.TXT", 1, payload=["--text", "x"])
        self.assertEqual(rc, 1)
        self.assertEqual(fields["status"], "no-file")
        self.assertEqual(fields["writes"], "0")
        self.assertEqual(after, image)
        self.assertEqual(fat32_images.list_path(after, ""),
                         fat32_images.list_path(image, ""))

    def test_reading_a_directory_as_a_file_is_refused(self):
        image = fat32_images.build_fat32_image(valid_subdir=True)
        # the kernel's read path is exercised in the boot test; here the
        # independent reader is the oracle for what a directory is
        with self.assertRaises(IsADirectoryError):
            fat32_images.read_path(image, "SUBDIR")
        self.assertEqual(fat32_images.list_path(image, "SUBDIR")[0]["name"], "INNER.TXT")


# --------------------------------------------------------------------------
# Fault injection and interruption classification
# --------------------------------------------------------------------------

class FaultInjectionTests(ExpandDriver):
    """Fail every write of an operation in turn and classify the result.

    The rule the implementation promises, and these tests enforce:

      * failing *before* the directory-entry write leaves the target exactly as
        it was (old content for an overwrite, absent for a create/mkdir) and can
        only leak the clusters allocated so far;
      * failing *after* it leaves the new state complete and can only leak the
        replaced chain;
      * no failure ever damages an unrelated file, makes a cluster belong to two
        objects, or produces an invalid FAT reference.

    Which write is "the switch" is read from the successful run (the write to
    the sector holding the new directory entry), not guessed from a count.
    """

    def sweep(self, image, path, size, op, payload=(), old_content=None,
              new_content=None):
        """Run once for real, then fail each write in turn."""
        g = fat32_images.volume_geometry(image)
        dir_clusters = {g["root_cluster"]}
        for p, chain in fat32_images.chains(image).items():
            if p != "/" and len(chain) and any(
                    e["attr"] & fat32_images.ATTR_DIRECTORY and e["name"] in p
                    for e in fat32_images.list_path(image, "")):
                dir_clusters.update(chain)
        dir_clusters.update(fat32_images.cluster_chain(image, g["root_cluster"]))

        fields, after_ok, rc_ok = self.run_op(image, path, size, op, payload=payload)
        self.assertEqual(rc_ok, 0, f"the baseline run failed: {fields['_stdout']}")
        switch_sector = g["part_lba"] + int(fields["dir_sector"])
        writes = fields["_lbas"]
        self.assertTrue(writes)
        switch = next(i for i, (lba, _c) in enumerate(writes) if lba == switch_sector)

        results = []
        for index in range(len(writes)):
            options = ["--fail-write", str(index)]
            f, after, rc = self.run_op(image, path, size, op, payload=payload,
                                       options=options)
            info = classify(image, after, path)
            results.append({
                "index": index,
                "region": region_of(writes[index][0], g, dir_clusters),
                "before_switch": index <= switch,
                "fields": f,
                "rc": rc,
                "info": info,
            })
            # invariants that hold for every single failure
            self.assertNotEqual(rc, 0, f"write {index} failed but the call returned ok")
            self.assertNotEqual(f["status"], "ok")
            self.assertTrue(info["originals_intact"],
                            f"write {index} damaged {info['broken_originals']}")
            self.assertEqual(info["double"], [],
                             f"write {index} left clusters in two chains")
            self.assertTrue(info["fat_valid"], f"write {index} wrote an invalid FAT entry")
        return fields, results, old_content, new_content

    def assert_model(self, results, old_content, new_content, mkdir=False, path=None):
        for r in results:
            before, info, fields = r["before_switch"], r["info"], r["fields"]
            if mkdir:
                if before:
                    self.assertFalse(info["target_exists"],
                                     f"write {r['index']} ({r['region']}) named the "
                                     f"directory before its entry was written")
                else:
                    self.assertTrue(info["target_exists"],
                                    f"write {r['index']} ({r['region']}) failed after the "
                                    f"entry was written but lost the directory")
                    dirinfo = fat32_images.check_directory(info["after"], path)
                    self.assertTrue(dirinfo["dot_ok"] and dirinfo["dotdot_ok"])
                continue
            if before:
                self.assertEqual(info["target"], old_content,
                                 f"write {r['index']} ({r['region']}) damaged the file "
                                 f"it was replacing")
            else:
                self.assertEqual(info["target"], new_content,
                                 f"write {r['index']} ({r['region']}) failed after the "
                                 f"switch but lost the new content")
            self.assertIn(fields["status"],
                          ("io", "flush", "verify", "corrupt", "full", "chain"),
                          f"write {r['index']}: unexpected status {fields['status']}")

    # ---- overwrite -------------------------------------------------------

    def test_every_write_failure_during_an_overwrite_is_classified(self):
        image = fat32_images.build_fat32_image()
        old = fat32_images.read_path(image, "PC5TEST.TXT")
        new = pattern(1500)
        _, results, _, _ = self.sweep(image, "PC5TEST.TXT", 1500, "overwrite")
        self.assert_model(results, old, new, path="PC5TEST.TXT")
        # the switch is a single write: at least one failure lands on each side
        self.assertTrue(any(r["before_switch"] for r in results))
        self.assertTrue(any(not r["before_switch"] for r in results))
        # every failure is attributed to a region of the volume, so the report
        # says *where* the operation stopped, not merely that it did
        self.assertEqual({r["region"] for r in results} - {"data", "fat0", "fat1",
                                                           "dir2", "fsinfo"}, set())

    def test_a_failed_switch_leaves_the_old_file_completely_intact(self):
        image = fat32_images.build_fat32_image()
        old = fat32_images.read_path(image, "PC5TEST.TXT")
        fields, _after, _rc = self.overwrite(image, "PC5TEST.TXT", 1500)
        switch = next(i for i, (lba, _c) in enumerate(fields["_lbas"])
                      if lba == fat32_images.volume_geometry(image)["part_lba"]
                      + int(fields["dir_sector"]))
        f, after, rc = self.overwrite(image, "PC5TEST.TXT", 1500,
                                      options=["--fail-write", str(switch)])
        self.assertEqual(rc, 1)
        self.assertEqual(f["status"], "io")
        self.assertEqual(fat32_images.read_path(after, "PC5TEST.TXT"), old)
        self.assertEqual(fat32_images.read_path(after, "SMALL.BIN"),
                         expected_root_files()["SMALL.BIN"])
        # the new chain is leaked, and the leak is the only damage
        self.assertTrue(fat32_images.orphan_clusters(after))
        self.assertEqual(fat32_images.double_allocated(after), set())

    def test_a_failed_reclamation_leaks_the_old_chain_but_keeps_the_new_file(self):
        image = fat32_images.build_fat32_image()
        new = pattern(1500)
        old_first = next(e["first_cluster"] for e in fat32_images.list_path(image, "")
                         if e["name"] == "PC5TEST.TXT")
        old_chain = fat32_images.cluster_chain(image, old_first)
        fields, _after, _rc = self.overwrite(image, "PC5TEST.TXT", 1500)
        g = fat32_images.volume_geometry(image)
        writes = fields["_lbas"]
        switch = next(i for i, (lba, _c) in enumerate(writes)
                      if lba == g["part_lba"] + int(fields["dir_sector"]))
        # every write after the switch belongs to the barrier, the reclamation
        # or the counters; whichever it is, the file has already been replaced
        saw_partial = False
        for index in range(switch + 1, len(writes)):
            with self.subTest(index=index):
                f, after, rc = self.overwrite(image, "PC5TEST.TXT", 1500,
                                              options=["--fail-write", str(index)])
                self.assertEqual(rc, 1)
                self.assertNotEqual(f["status"], "ok")
                self.assertEqual(fat32_images.read_path(after, "PC5TEST.TXT"), new)
                freed, old_clusters = int(f["freed"]), int(f["old_clusters"])
                self.assertEqual(old_clusters, 6)
                self.assertLessEqual(freed, old_clusters)
                self.assertEqual(fat32_images.double_allocated(after), set())
                leaked = fat32_images.orphan_clusters(after)
                diverged = _diverged_clusters(after, old_chain)
                # every cluster of the replaced chain is in exactly one of three
                # states: freed, still allocated and unreachable (a leak), or
                # freed in one FAT copy only (a divergence the report must own)
                self.assertEqual(freed + len(leaked) + len(diverged), old_clusters,
                                 f"write {index}: freed {freed} + leaked {sorted(leaked)} "
                                 f"+ diverged {sorted(diverged)} != {old_clusters}")
                # `mirrors` means "verified equal after this operation": a free
                # that did not finish may have left the copies disagreeing, so
                # it must never be reported as 1.
                self.assertEqual(f["mirrors"], "1" if freed == old_clusters else "0",
                                 f"write {index}: mirrors={f['mirrors']} after freeing "
                                 f"{freed}/{old_clusters} clusters")
                if freed < old_clusters:
                    saw_partial = True
                    self.assertTrue(leaked or diverged,
                                    f"write {index} stopped the free after {freed} of "
                                    f"{old_clusters} clusters but left nothing behind")
                else:
                    self.assertEqual(leaked, set(),
                                     f"write {index} landed after the last free but "
                                     f"leaked something")
        self.assertTrue(saw_partial, "no failure landed inside the reclamation")

    def test_failing_one_fat_mirror_is_reported_not_hidden(self):
        image = fat32_images.build_fat32_image()
        for copy in (0, 1):
            with self.subTest(copy=copy):
                f, after, rc = self.overwrite(image, "PC5TEST.TXT", 1500,
                                              options=["--fail-fat-copy", str(copy)])
                self.assertEqual(rc, 1)
                self.assertNotEqual(f["status"], "ok")
                self.assertEqual(f["mirrors"], "0")
                info = classify(image, after, "PC5TEST.TXT")
                self.assertTrue(info["originals_intact"])
                self.assertEqual(fat32_images.read_path(after, "PC5TEST.TXT"),
                                 fat32_images.read_path(image, "PC5TEST.TXT"))

    def test_corrupted_replacement_data_is_caught_by_the_read_back(self):
        image = fat32_images.build_fat32_image()
        f, after, rc = self.overwrite(image, "PC5TEST.TXT", 1500,
                                      options=["--corrupt-write", "0"])
        self.assertEqual(rc, 1)
        self.assertIn(f["status"], ("verify", "io"))
        self.assertEqual(f["verified"], "0")

    def test_a_failed_flush_during_an_overwrite_is_an_error(self):
        image = fat32_images.build_fat32_image()
        f, after, rc = self.overwrite(image, "PC5TEST.TXT", 1500,
                                      options=["--fail-flush"])
        self.assertEqual(rc, 1)
        self.assertEqual(f["status"], "flush")
        self.assertEqual(f["flush"], "-1")
        self.assertTrue(classify(image, after, "PC5TEST.TXT")["originals_intact"])

    def test_a_missing_flush_callback_is_reported_not_assumed(self):
        image = fat32_images.build_fat32_image()
        f, _after, rc = self.overwrite(image, "PC5TEST.TXT", 100,
                                       options=["--no-flush"])
        self.assertEqual(rc, 0)
        self.assertEqual(f["flush"], "0")

    # ---- mkdir -----------------------------------------------------------

    def test_every_write_failure_during_a_directory_create_is_classified(self):
        image = fat32_images.build_fat32_image()
        _, results, _, _ = self.sweep(image, "NEWDIR", 0, "mkdir")
        self.assert_model(results, None, None, mkdir=True, path="NEWDIR")
        self.assertTrue(any(r["before_switch"] for r in results))
        # the parent-entry write is the last write of a mkdir (only a flush
        # follows it), so no failure can land strictly after the switch
        self.assertFalse(any(not r["before_switch"] for r in results))

    def test_a_failed_parent_entry_leaves_the_volume_with_a_clean_leak(self):
        image = fat32_images.build_fat32_image()
        fields, _after, _rc = self.mkdir(image, "NEWDIR")
        g = fat32_images.volume_geometry(image)
        switch = next(i for i, (lba, _c) in enumerate(fields["_lbas"])
                      if lba == g["part_lba"] + int(fields["dir_sector"]))
        f, after, rc = self.mkdir(image, "NEWDIR", options=["--fail-write", str(switch)])
        self.assertEqual(rc, 1)
        self.assertEqual(f["status"], "io")
        self.assertNotIn("NEWDIR", [e["name"] for e in fat32_images.list_path(after, "")])
        self.assertEqual(len(fat32_images.orphan_clusters(after)), 1,
                         "exactly the initialised directory cluster is leaked")
        self.assertEqual(fat32_images.double_allocated(after), set())
        for name, data in expected_root_files().items():
            self.assertEqual(fat32_images.read_path(after, name), data)

    def test_a_failed_directory_growth_leaves_the_parent_unchanged(self):
        image = _closed_full_root()
        g = fat32_images.volume_geometry(image)
        root = fat32_images.cluster_chain(image, g["root_cluster"])
        fields, _after, _rc = self.mkdir(image, "NEWDIR")
        self.assertEqual(fields["status"], "ok", fields["_stdout"])
        self.assertEqual(fields["grew"], "1")
        # the first write into the *new* cluster of the root chain is the growth
        grown = int(fields["grown"])
        grow_sector = g["part_lba"] + g["first_data"] + (grown - 2) * g["spc"]
        writes = fields["_lbas"]
        index = next(i for i, (lba, _c) in enumerate(writes) if lba == grow_sector)
        f, after, rc = self.mkdir(image, "NEWDIR", options=["--fail-write", str(index)])
        self.assertEqual(rc, 1)
        self.assertEqual(f["status"], "io", f["_stdout"])
        # the growth did not finish, so the report must not claim it did
        self.assertEqual(f["grew"], "0")
        self.assertEqual(fat32_images.cluster_chain(after, g["root_cluster"]), root,
                         "the parent chain must be unchanged when the growth failed")
        self.assertNotIn("NEWDIR", [e["name"] for e in fat32_images.list_path(after, "")])
        self.assertEqual(fat32_images.double_allocated(after), set())
        # the new directory cluster was already initialised before the growth,
        # so it is now allocated and unnamed: exactly one leaked cluster, and it
        # is still a well-formed empty directory
        leaked = fat32_images.orphan_clusters(after)
        self.assertEqual(len(leaked), 1, f"leaked {sorted(leaked)}")
        orphan = next(iter(leaked))
        entries = [e for e in fat32_images._dir_entries(after, orphan) if not e["deleted"]]
        self.assertEqual([e["name"] for e in entries[:2]], [".", ".."])
        self.assertEqual(entries[0]["first_cluster"], orphan)
        self.assertEqual(entries[1]["first_cluster"], 0,
                         "'..' of a directory in the root is cluster 0")


# --------------------------------------------------------------------------
# Invariants that must hold for a successful operation too
# --------------------------------------------------------------------------

class InvariantTests(ExpandDriver):
    def test_the_root_directory_cluster_is_never_allocated_as_data(self):
        image = fat32_images.build_fat32_image()
        g = fat32_images.volume_geometry(image)
        for op, path, size in (("create", "A.TXT", 500),
                               ("overwrite", "SMALL.BIN", 2000),
                               ("mkdir", "D", 0)):
            with self.subTest(op=op):
                f, after, rc = self.run_op(image, path, size, op)
                self.assertEqual(rc, 0, f["_stdout"])
                chains = fat32_images.chains(after)
                self.assertIn(g["root_cluster"], chains["/"])
                for name, chain in chains.items():
                    if name == "/":
                        continue
                    self.assertNotIn(g["root_cluster"], chain,
                                     f"{op} handed out the root cluster to {name}")

    def test_no_cluster_is_ever_claimed_twice_after_a_mixed_sequence(self):
        image = fat32_images.build_fat32_image(valid_subdir=True)
        seq = [("mkdir", "NEW", 0), ("create", "NEW/A.TXT", 700),
               ("overwrite", "MULTI.BIN", 300), ("create", "B.TXT", 1536),
               ("overwrite", "NEW/A.TXT", 40), ("mkdir", "NEW/SUB", 0),
               ("create", "NEW/SUB/C.TXT", 2000)]
        img = image
        for op, path, size in seq:
            f, img, rc = self.run_op(img, path, size, op)
            self.assertEqual(rc, 0, f"{op} {path}: {f['_stdout']}")
            self.assertEqual(fat32_images.double_allocated(img), set(), f"{op} {path}")
            self.assertTrue(classify(img, img, path)["fat_valid"])
        # everything written is readable, every original survives
        self.assertEqual(fat32_images.read_path(img, "NEW/A.TXT"), pattern(40))
        self.assertEqual(fat32_images.read_path(img, "NEW/SUB/C.TXT"), pattern(2000))
        self.assertEqual(fat32_images.read_path(img, "SUBDIR/INNER.TXT"),
                         expected_subdir_files()["SUBDIR/INNER.TXT"])
        for name, data in expected_root_files().items():
            if name == "MULTI.BIN":
                continue                      # deliberately replaced above
            self.assertEqual(fat32_images.read_path(img, name), data, name)
        for path in ("NEW", "NEW/SUB"):
            info = fat32_images.check_directory(img, path)
            self.assertTrue(info["dot_ok"], path)
            self.assertTrue(info["dotdot_ok"], path)
        self.assertEqual(fat32_images.orphan_clusters(img), set())

    def test_fsinfo_stays_consistent_across_a_mixed_sequence(self):
        image = fat32_images.build_fat32_image()
        img = image
        for op, path, size in (("mkdir", "DIR", 0), ("create", "DIR/F", 900),
                               ("overwrite", "PC5TEST.TXT", 100),
                               ("overwrite", "EMPTY.TXT", 1200)):
            f, img, rc = self.run_op(img, path, size, op)
            self.assertEqual(rc, 0, f["_stdout"])
            self.assertEqual(fat32_images.fsinfo_fields(img)["free_count"],
                             fat32_images.free_cluster_count(img),
                             f"FSInfo drifted after {op} {path}")

    def test_a_hostile_fsinfo_is_neither_trusted_nor_rewritten(self):
        image = fat32_images.build_fat32_image()
        broken = bytearray(image)
        g = fat32_images.volume_geometry(image)
        off = (g["part_lba"] + g["fsinfo"]) * SECTOR
        broken[off:off + 4] = b"\x00\x00\x00\x00"          # destroy the lead signature
        broken = bytes(broken)
        f, after, rc = self.mkdir(broken, "NDIR")
        self.assertEqual(rc, 0, f["_stdout"])
        self.assertEqual(f["fsinfo_valid"], "0")
        self.assertEqual(f["fsinfo_updated"], "0")
        self.assertEqual(after[off:off + 4], b"\x00\x00\x00\x00",
                         "an untrusted FSInfo block must not be written")
        self.assertTrue(fat32_images.check_directory(after, "NDIR")["dot_ok"])

    def test_operations_never_write_outside_the_partition(self):
        image = fat32_images.build_fat32_image()
        g = fat32_images.volume_geometry(image)
        for op, path, size in (("create", "A.TXT", 900), ("overwrite", "SMALL.BIN", 3000),
                               ("mkdir", "D", 0)):
            with self.subTest(op=op):
                f, _after, rc = self.run_op(image, path, size, op)
                self.assertEqual(rc, 0, f["_stdout"])
                for lba, count in f["_lbas"]:
                    self.assertGreaterEqual(lba, g["part_lba"])
                    self.assertLess(lba + count * 0 + 1, g["part_lba"] + g["total"])
                    self.assertEqual(count, 1)


# --------------------------------------------------------------------------
# The same harness-safety checks M1g has, extended to the new entry points
# --------------------------------------------------------------------------

class HarnessSafetyTests(unittest.TestCase):
    def read(self, path):
        with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
            return fh.read()

    def test_no_boot_path_calls_a_write(self):
        main = self.read("kernel/main.c") + self.read("kernel/storage.c") \
            if os.path.exists(os.path.join(ROOT, "kernel/storage.c")) else self.read("kernel/main.c")
        for call in ("fat32_create_mounted", "fat32_overwrite_mounted", "fat32_mkdir_mounted"):
            self.assertNotIn(call, main, f"{call} must never run during boot")

    def test_only_the_console_commands_reach_the_new_operations(self):
        ui = self.read("kernel/ui.c")
        for call in ("fat32_overwrite_mounted", "fat32_mkdir_mounted"):
            self.assertIn(call, ui, f"{call} should be reachable from the console")

    def test_the_write_entry_points_are_declared_once(self):
        header = self.read("kernel/include/pc5.h")
        for fn in ("fat32_overwrite_file", "fat32_create_dir"):
            self.assertEqual(header.count(fn), 2, fn)   # one comment, one prototype

    def test_a_read_only_mount_still_refuses_every_write(self):
        source = self.read("kernel/fat32.c")
        self.assertIn("if (!io->write)", source)
        self.assertIn("FAT32_E_READ_ONLY", source)


if __name__ == "__main__":
    unittest.main()
