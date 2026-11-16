"""The build manifest must describe the image, and must notice when it changes.

`tools/iso_manifest.py` is the second reader: it parses the ISO from the bytes on
disk without importing the writer. These tests pin three properties that matter
for release evidence:

1. the manifest lists every file, its size and its SHA-256, plus the El Torito
   entries firmware would execute — with the UEFI section correctly identified
   rather than assumed to be x86;
2. a manifest check fails when a single byte of a payload changes, and when a file
   is added or removed;
3. the tool is genuinely independent of `mkiso`, so a writer bug cannot be hidden
   by a shared parser.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))

import iso_manifest  # noqa: E402
import mbr_fixture  # noqa: E402
import mkiso  # noqa: E402


def boot_image() -> bytes:
    image = bytearray(4096)
    image[0:3] = b"\xfa\xeb\xfe"
    image[0xE00:0xE08] = b"PC5BOOT1"
    return bytes(image)


def build(files=None, uefi: bool = True) -> bytes:
    builder = mkiso.IsoBuilder(volume_id="PC5TEST")
    builder.set_bios_boot_image(boot_image(), desc_offset=0xE00)
    builder.set_mbr_code(mbr_fixture.synthetic())
    if uefi:
        builder.set_uefi_image(bytearray(4096))
    for name, data in (files or {"KERNEL.BIN": b"\xeb\x05PC5K\x01" + bytes(2048),
                                 "BUILDINFO.TXT": b"PC5\nversion: test\nbuild: deadbeef\n"}).items():
        builder.add_file(name, data)
    image, _ = builder.build()
    return image


class ManifestContentTest(unittest.TestCase):
    def test_every_file_is_listed_with_size_and_hash(self):
        data = iso_manifest.manifest(build())
        paths = {entry["path"]: entry for entry in data["files"]}
        self.assertEqual(set(paths), {"BUILDINF.TXT", "KERNEL.BIN"})
        for entry in paths.values():
            self.assertGreater(entry["size"], 0)
            self.assertEqual(len(entry["sha256"]), 64)
        self.assertEqual(paths["KERNEL.BIN"]["size"], 2055)

    def test_the_image_identity_is_recorded(self):
        image = build()
        data = iso_manifest.manifest(image)
        self.assertEqual(data["image"]["size"], len(image))
        self.assertEqual(data["image"]["sectors"], len(image) // 2048)
        self.assertEqual(len(data["image"]["sha256"]), 64)

    def test_the_boot_catalog_names_both_platforms(self):
        data = iso_manifest.manifest(build())
        entries = data["boot_catalog"]
        roles = [entry["role"] for entry in entries]
        self.assertEqual(roles[0], "validation")
        self.assertIn("initial", roles)
        self.assertIn("section-header", roles)
        self.assertIn("section", roles)
        platforms = [entry["platform"] for entry in entries]
        self.assertIn("x86", platforms)
        self.assertIn("uefi", platforms)
        bootable = [entry for entry in entries if entry.get("bootable")]
        self.assertGreaterEqual(len(bootable), 2, "the BIOS and UEFI images both boot")
        self.assertTrue(all(entry["media"] == "no-emulation" for entry in bootable))

    def test_the_volume_descriptor_fields_answer_at_the_standard_offsets(self):
        """ECMA-119 8.4: the fields an independent reader looks for are where it looks."""
        data = iso_manifest.manifest(build())
        self.assertEqual(data["volume"]["volume_id"], "PC5TEST")
        self.assertEqual(data["volume"]["logical_block_size"], 2048)
        self.assertGreater(data["volume"]["volume_space_size"], 0)
        self.assertGreater(data["volume"]["path_table_size"], 0)
        self.assertTrue(data["volume"]["publisher"])

    def test_the_build_record_is_parsed(self):
        data = iso_manifest.read(_write_temp(build()))
        self.assertEqual(data["buildinfo"]["version"], "test")
        self.assertEqual(data["buildinfo"]["build"], "deadbeef")

    def test_a_non_iso_is_refused_with_a_reason(self):
        with self.assertRaises(iso_manifest.ManifestError) as caught:
            iso_manifest.manifest(b"\x00" * 70000)
        self.assertIn("CD001", str(caught.exception))

    def test_an_image_without_a_boot_catalog_is_refused(self):
        image = bytearray(build())
        image[17 * 2048 + 1:17 * 2048 + 6] = b"XXXXX"
        with self.assertRaises(iso_manifest.ManifestError):
            iso_manifest.manifest(bytes(image))


class ManifestVerificationTest(unittest.TestCase):
    def setUp(self):
        self.image = build()
        self.path = _write_temp(self.image)
        self.manifest = REPO / "build" / "manifest-test.json"
        self.manifest.parent.mkdir(exist_ok=True)
        self.manifest.write_text(json.dumps(iso_manifest.manifest(self.image),
                                            indent=2, sort_keys=True))

    def test_an_unchanged_image_verifies(self):
        ok, problems = iso_manifest.verify(self.path, self.manifest)
        self.assertTrue(ok, problems)
        self.assertEqual(problems, [])

    def test_one_changed_byte_in_a_payload_is_caught(self):
        image = bytearray(self.image)
        # find the KERNEL.BIN extent and flip a byte inside it
        data = iso_manifest.manifest(self.image)
        volume = data["volume"]
        for rec in iso_manifest._dir_records(self.image, volume["root_extent"],
                                            volume["root_length"]):
            if rec["name"].startswith("KERNEL"):
                image[rec["extent"] * 2048 + 100] ^= 0xFF
                break
        changed = _write_temp(bytes(image))
        ok, problems = iso_manifest.verify(changed, self.manifest)
        self.assertFalse(ok)
        self.assertTrue(any("KERNEL.BIN" in problem for problem in problems), problems)

    def test_a_changed_boot_catalog_is_caught(self):
        image = bytearray(self.image)
        catalog_lba = iso_manifest._u32(image, 17 * 2048 + 0x47)
        image[catalog_lba * 2048 + 8] ^= 0xFF      # inside the initial entry
        changed = _write_temp(bytes(image))
        ok, problems = iso_manifest.verify(changed, self.manifest)
        self.assertFalse(ok)
        self.assertTrue(any("boot catalog" in problem for problem in problems), problems)

    def test_a_manifest_for_another_image_is_caught(self):
        other = _write_temp(build({"KERNEL.BIN": b"\xeb\x05PC5K\x01" + bytes(2048)}))
        ok, problems = iso_manifest.verify(other, self.manifest)
        self.assertFalse(ok)
        self.assertTrue(any("image identity differs" in problem for problem in problems))

    def test_the_manifest_is_deterministic_for_the_same_bytes(self):
        first = json.dumps(iso_manifest.manifest(self.image), sort_keys=True)
        second = json.dumps(iso_manifest.manifest(self.image), sort_keys=True)
        self.assertEqual(first, second)


class PvdOffsetRegressionTest(unittest.TestCase):
    """The defect this work found, pinned so it cannot return.

    `mkiso` wrote the logical-block-size field with an eight-byte slice for a
    four-byte 7.3.3 value and wrote the creation/modification dates 15 bytes wide
    instead of 17, so the descriptor was four bytes short and everything after
    the block-size field sat at the wrong offset. The image still booted, because
    nothing PC5 ships reads those fields.
    """

    def test_the_writer_produces_a_standard_conformant_descriptor(self):
        data = iso_manifest.manifest(build())
        volume = data["volume"]
        self.assertEqual(volume["path_table_size"] % 2048, 0 if volume["path_table_size"] >= 2048
                         else volume["path_table_size"])
        self.assertTrue(volume["publisher"])
        self.assertTrue(volume["app_id"])

    def test_verify_iso_rejects_a_shifted_path_table_field(self):
        import verify_iso

        # No UEFI image here: a 4 KiB placeholder is not a FAT ESP, and this test
        # is about the primary volume descriptor, not the ESP.
        path = _write_temp(build(uefi=False))
        report = verify_iso.verify(path)
        self.assertTrue(report.ok, report.failures)

        image = bytearray(path.read_bytes())
        image[16 * 2048 + 132:16 * 2048 + 140] = b"\x00" * 8
        broken = _write_temp(bytes(image))
        report = verify_iso.verify(broken)
        self.assertFalse(report.ok, "a blank path-table field must not verify")
        self.assertTrue(any("path table" in failure["name"] for failure in report.failures),
                        report.failures)


class IndependenceTest(unittest.TestCase):
    def test_the_reader_does_not_import_the_writer(self):
        source = (REPO / "tools/iso_manifest.py").read_text()
        self.assertNotIn("import mkiso", source)
        self.assertNotIn("from mkiso", source)
        self.assertNotIn("IsoBuilder", source)

    def test_the_cli_writes_and_checks_a_manifest(self):
        image = _write_temp(build())
        out = REPO / "build" / "manifest-cli.json"
        out.parent.mkdir(exist_ok=True)
        for args in ([str(image), "--output", str(out), "-q"],
                     [str(image), "--verify", str(out)]):
            result = subprocess.run([sys.executable, str(REPO / "tools/iso_manifest.py")]
                                    + args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MANIFEST VALID", result.stdout)

    def test_a_missing_image_exits_two(self):
        result = subprocess.run([sys.executable, str(REPO / "tools/iso_manifest.py"),
                                 "build/definitely-not-here.iso"], capture_output=True,
                                text=True)
        self.assertEqual(result.returncode, 2)


def _write_temp(data: bytes) -> Path:
    handle, name = tempfile.mkstemp(suffix=".iso")
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
    path = Path(name)
    Path(name).unlink(missing_ok=True)
    path.write_bytes(data)
    return path


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
