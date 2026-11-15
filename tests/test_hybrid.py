"""Hybrid-MBR tests (M1h).

Two independent layers:

  * the bootstrap source, assembled exactly the way the Makefile does it, is
    inspected byte by byte — this is the only test that knows what the 440
    bytes mean;
  * the image the builder produces is inspected by tools/verify_iso.py, then
    deliberately corrupted in every way a wrong layout could go wrong, and the
    verifier must reject each corruption.  A verifier that passes everything is
    not a verifier.

Nothing here consults tools/mkiso.py when *checking* a layout: the expectation
is recomputed from the ISO geometry in the test itself.
"""

import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mbr_fixture  # noqa: E402
import mkiso  # noqa: E402
import verify_iso  # noqa: E402

SECTOR = 2048


def assemble_mbr(dest_dir):
    """boot/mbr.S, assembled exactly the way the Makefile does."""
    return mbr_fixture.assemble(dest_dir)


class HybridMbrSourceTests(unittest.TestCase):
    """What the 440 bytes handed to the builder are and are not."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.blob = assemble_mbr(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_bootstrap_is_exactly_440_bytes(self):
        # 440 is the MBR layout: the disk signature starts at 0x1B8 and the
        # builder owns 440..511.
        self.assertEqual(len(self.blob), mkiso.MBR_CODE_BYTES)

    def test_the_bootstrap_reads_the_image_through_int13h(self):
        self.assertIn(b"\xcd\x13", self.blob, "no INT 13h in the MBR bootstrap")
        self.assertIn(b"\x42", self.blob, "no extended-read function number")

    def test_the_bootstrap_hands_control_to_the_stage1_loader(self):
        # A far jump to 0000:7C00 is the hand-off; without it the loader never
        # runs on the hard-disk path.
        self.assertIn(b"\xea\x00\x7c\x00\x00", self.blob)

    def test_both_patch_words_are_zero_in_the_source(self):
        # mkiso.py patches them from the real layout; a non-zero default would
        # be a silent, wrong fallback.
        self.assertEqual(self.blob[0x1B0:0x1B8], bytes(8))

    def test_the_bootstrap_addresses_the_patch_words_absolutely(self):
        # The sector is executed from 0000:7C00, so file offset 0x1B0 must be
        # read at 0x7DB0.  These are the two instructions that do it:
        #   66 a1 b0 7d   mov eax, [0x7db0]
        #   66 8b 0e b4 7d mov ecx, [0x7db4]
        self.assertIn(b"\x66\xa1\xb0\x7d", self.blob)
        self.assertIn(b"\x66\x8b\x0e\xb4\x7d", self.blob)
        self.assertEqual(struct.unpack("<I", self.blob[mkiso.MBR_PATCH_LBA:
                                                        mkiso.MBR_PATCH_LBA + 4])[0], 0)

    def test_the_bootstrap_reloads_itself_above_the_loader_window(self):
        # It must move its own code (with the retry counter and the disk address
        # packet) somewhere that the incoming loader does not overwrite.
        self.assertIn(b"\xbf\x00\x05", self.blob)   # mov di, 0x0500
        self.assertIn(b"\xbe\x22\x7c", self.blob)   # mov si, 0x7c22 (copy source)
        # At 0x18: b9 c0 00 -> "mov cx, <relay length in bytes>"
        self.assertEqual(self.blob[0x18], 0xB9)
        relay_len = struct.unpack("<H", self.blob[0x19:0x1B])[0]
        self.assertGreater(relay_len, 32)
        self.assertLess(relay_len, 512 - 0x22, "relay would not stay in one sector")

    def test_the_bootstrap_owns_no_disk_signature_or_table(self):
        # If the source carried 55AA or a partition table, the builder and the
        # source could disagree about the layout.
        self.assertNotIn(b"\x55\xaa", self.blob)

    def test_the_makefile_builds_and_pins_the_bootstrap(self):
        makefile = open(os.path.join(ROOT, "Makefile"), encoding="utf-8").read()
        self.assertIn("$(BUILD)/mbr.bin: $(OBJ)/mbr.o", makefile)
        self.assertIn("--mbr-code $(BUILD)/mbr.bin", makefile)
        self.assertIn("must be exactly 440 bytes", makefile)
        self.assertIn("$(BUILD)/mbr.bin", makefile.split("$(ISO):", 1)[1].split("\n")[0])


def build_small_image(mbr_code):
    """A minimal ISO: fake 4096-byte boot image with a valid PC5 descriptor."""
    boot = bytearray(4096)
    boot[0:3] = b"\xfa\xeb\xfe"
    boot[0xE00:0xE08] = b"PC5BOOT1"
    kernel = b"\xeb\x05PC5K\x01" + bytes(2048)
    builder = mkiso.IsoBuilder(volume_id="PC5TEST")
    builder.set_bios_boot_image(bytes(boot), desc_offset=0xE00)
    builder.set_mbr_code(mbr_code)
    builder.add_file("KERNEL.BIN", kernel)
    return builder.build()


class HybridMbrLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mbr_code = assemble_mbr(cls.tmp.name)
        cls.image, cls.info = build_small_image(cls.mbr_code)
        cls.path = os.path.join(cls.tmp.name, "hybrid.iso")
        with open(cls.path, "wb") as fh:
            fh.write(cls.image)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def partition(self, index=0):
        off = 446 + 16 * index
        return {
            "status": self.image[off],
            "start": struct.unpack("<I", self.image[off + 8:off + 12])[0],
            "sectors": struct.unpack("<I", self.image[off + 12:off + 16])[0],
            "type": self.image[off + 4],
        }

    def test_the_builder_writes_a_signed_sector_at_offset_zero(self):
        self.assertEqual(self.image[510:512], b"\x55\xaa")
        # The first 0x1B0 bytes are the code verbatim; 0x1B0..0x1B7 are the two
        # patched words, which live inside the 440-byte region by design.
        self.assertEqual(self.image[:0x1B0], self.mbr_code[:0x1B0])
        self.assertEqual(self.image[0x1B8:440], self.mbr_code[0x1B8:440])
        self.assertNotEqual(self.image[440:444], bytes(4), "no disk signature")

    def test_the_partition_describes_this_image(self):
        total = len(self.image) // 512
        part = self.partition()
        self.assertEqual(part["status"], 0x80)
        self.assertEqual(part["type"], 0x17)
        self.assertEqual(part["start"], 0)
        self.assertEqual(part["sectors"], total, "the entry must cover the image")
        self.assertEqual(total * 512, len(self.image))

    def test_the_other_three_entries_are_empty(self):
        for index in (1, 2, 3):
            self.assertEqual(self.image[446 + 16 * index:462 + 16 * index], bytes(16))

    def test_the_patch_words_point_at_the_el_torito_boot_image(self):
        patch_lba, patch_count = struct.unpack("<II", self.image[0x1B0:0x1B8])
        layout = self.info["layout"]
        self.assertEqual(patch_lba, layout["bios_boot_image_lba"] * 4)
        self.assertEqual(patch_count, layout["bios_boot_virtual_sectors"])
        # ...and what those words address really is the stage-1 loader.
        loaded = self.image[patch_lba * 512:patch_lba * 512 + patch_count * 512]
        self.assertEqual(len(loaded), 4096)
        self.assertEqual(loaded[:3], b"\xfa\xeb\xfe")
        self.assertEqual(loaded[0xE00:0xE08], b"PC5BOOT1")

    def test_the_verifier_accepts_the_image(self):
        rep = verify_iso.verify(self.path)
        self.assertTrue(rep.ok, [c for c in rep.checks if not c["ok"]])

    def test_the_verifier_reports_every_hybrid_check(self):
        rep = verify_iso.verify(self.path)
        names = [c["name"] for c in rep.checks if c["name"].startswith("hybrid MBR")]
        self.assertGreaterEqual(len(names), 16, names)
        self.assertTrue(all(c["ok"] for c in rep.checks if c["name"].startswith("hybrid MBR")),
                        [c for c in rep.checks if not c["ok"]])
        # The signature of the whole 512-byte contract, not just the table.
        self.assertIn("hybrid MBR signature 55AA at offset 510", names)
        self.assertIn("hybrid MBR partition type is 0x17", names)

    def test_builds_are_byte_identical(self):
        again, info = build_small_image(self.mbr_code)
        self.assertEqual(again, self.image)
        self.assertEqual(info["sha256"], self.info["sha256"])
        self.assertEqual(info["hybrid_mbr"]["sectors_512"], len(self.image) // 512)

    def test_the_build_metadata_records_the_table(self):
        mbr = self.info["hybrid_mbr"]
        self.assertTrue(mbr["present"])
        self.assertEqual(mbr["start_lba_512"], 0)
        self.assertEqual(mbr["sectors_512"], len(self.image) // 512)
        self.assertEqual(mbr["partition_type"], "0x17")
        self.assertEqual(mbr["boot_flag"], "0x80")

    def test_the_generator_refuses_a_wrong_sized_bootstrap(self):
        with self.assertRaises(SystemExit):
            build_small_image(self.mbr_code[:439])
        with self.assertRaises(SystemExit):
            build_small_image(self.mbr_code + b"\x00")

    def test_the_generator_refuses_to_build_without_a_bootstrap(self):
        builder = mkiso.IsoBuilder(volume_id="PC5TEST")
        boot = bytearray(4096)
        boot[0xE00:0xE08] = b"PC5BOOT1"
        builder.set_bios_boot_image(bytes(boot), desc_offset=0xE00)
        builder.add_file("KERNEL.BIN", b"\xeb\x05PC5K\x01" + bytes(2048))
        with self.assertRaises(SystemExit):
            builder.build()

    def test_the_partition_helper_rejects_an_empty_partition(self):
        with self.assertRaises(SystemExit):
            mkiso.mbr_partition_entry(0x80, 0x17, 0, 0)

    def test_chs_saturates_instead_of_wrapping(self):
        self.assertEqual(mkiso.mbr_chs(0), b"\x00\x01\x00")
        self.assertEqual(mkiso.mbr_chs(63), b"\x01\x01\x00")
        saturated = mkiso.mbr_chs((1024 * 255 * 63) + 1000)
        self.assertEqual(saturated, b"\xfe\xff\xff")


class HybridMbrRejectionTests(unittest.TestCase):
    """Every way the MBR can lie about the image must be rejected."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mbr_code = assemble_mbr(cls.tmp.name)
        cls.image, _ = build_small_image(cls.mbr_code)
        with open(os.path.join(cls.tmp.name, "base.iso"), "wb") as fh:
            fh.write(cls.image)
        base = verify_iso.verify(os.path.join(cls.tmp.name, "base.iso"))
        assert base.ok, [c for c in base.checks if not c["ok"]]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def corrupt(self, patches):
        data = bytearray(self.image)
        for offset, blob in patches:
            data[offset:offset + len(blob)] = blob
        path = os.path.join(self.tmp.name, "broken.iso")
        with open(path, "wb") as fh:
            fh.write(data)
        return verify_iso.verify(path)

    def assert_rejected(self, patches, failing):
        rep = self.corrupt(patches)
        self.assertFalse(rep.ok, "corrupted image was accepted as VALID")
        failed = [c["name"] for c in rep.checks if not c["ok"]]
        self.assertIn(failing, failed, failed)

    def test_the_missing_signature_is_rejected(self):
        self.assert_rejected([(510, b"\x00\x00")], "hybrid MBR signature 55AA at offset 510")

    def test_a_wiped_system_area_is_rejected(self):
        self.assert_rejected([(0, bytes(512))], "hybrid MBR signature 55AA at offset 510")

    def test_a_missing_bootstrap_is_rejected(self):
        self.assert_rejected([(0, bytes(440))], "hybrid MBR carries a bootstrap")

    def test_a_zero_disk_signature_is_rejected(self):
        self.assert_rejected([(440, bytes(4))], "hybrid MBR disk signature is set")

    def test_non_zero_reserved_bytes_are_rejected(self):
        self.assert_rejected([(444, b"\x01\x00")], "hybrid MBR reserved bytes are zero")

    def test_a_cleared_boot_flag_is_rejected(self):
        self.assert_rejected([(446, b"\x00")], "hybrid MBR boot indicator is 0x80")

    def test_a_foreign_partition_type_is_rejected(self):
        self.assert_rejected([(450, b"\x83")], "hybrid MBR partition type is 0x17")

    def test_a_partition_that_starts_late_is_rejected(self):
        self.assert_rejected([(454, struct.pack("<I", 1))],
                             "hybrid MBR partition covers every sector of the image")

    def test_a_partition_past_the_end_of_the_image_is_rejected(self):
        total = len(self.image) // 512
        self.assert_rejected([(458, struct.pack("<I", total + 1))],
                             "hybrid MBR partition does not run past the end of the image")

    def test_a_short_partition_is_rejected(self):
        total = len(self.image) // 512
        self.assert_rejected([(458, struct.pack("<I", total - 1))],
                             "hybrid MBR partition ends on the last image sector")

    def test_a_stale_chs_address_is_rejected(self):
        self.assert_rejected([(447, b"\x05\x01\x00")],
                             "hybrid MBR CHS start address is consistent with its LBA")

    def test_a_second_partition_is_rejected(self):
        self.assert_rejected([(462, bytes([0x00]) + b"\x00\x01\x00" + bytes([0x17])
                                   + b"\x00\x01\x00" + struct.pack("<II", 0, 64))],
                             "hybrid MBR declares exactly one partition")

    def test_an_overlapping_second_partition_is_rejected(self):
        # Two entries that both cover sector 0: the classic overlapping table.
        entry = bytes([0x00]) + b"\x00\x01\x00" + bytes([0x17]) + b"\x00\x01\x00" \
            + struct.pack("<II", 0, len(self.image) // 512)
        self.assert_rejected([(462, entry)], "hybrid MBR declares exactly one partition")

    def test_a_stale_boot_image_lba_is_rejected(self):
        self.assert_rejected([(0x1B0, struct.pack("<I", 100))],
                             "hybrid MBR names the El Torito boot image LBA")

    def test_a_short_boot_image_length_is_rejected(self):
        self.assert_rejected([(0x1B4, struct.pack("<I", 4))],
                             "hybrid MBR names the El Torito boot image length")

    def test_a_boot_image_beyond_the_partition_is_rejected(self):
        far = len(self.image) // 512 + 100
        self.assert_rejected([(0x1B0, struct.pack("<I", far))],
                             "hybrid MBR boot image lies inside the partition")

    def test_the_verifier_rejects_a_table_that_no_longer_matches_the_image(self):
        # Truncating one sector must not silently keep the old sector count.
        data = self.image[:-512]
        path = os.path.join(self.tmp.name, "short.iso")
        with open(path, "wb") as fh:
            fh.write(data)
        rep = verify_iso.verify(path)
        self.assertFalse(rep.ok)


if __name__ == "__main__":
    unittest.main()
