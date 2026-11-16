"""Independent ISO9660 reader that produces, and checks, a build manifest.

Why this exists: `make verify` proves the image is structurally sound, but a
release also has to answer "does the image contain exactly the files we meant to
ship, and can somebody else confirm that without trusting our writer?" That is
`BUILD-VERIFIED` acceptance level 1 in `docs/TEST_RESULTS.md` §8 of the release
plan, and it needs a second reader.

This module therefore **does not import `mkiso`**. It parses the image from the
bytes on disk — primary volume descriptor, directory records, file extents — and
the El Torito boot catalog, so a bug in the writer cannot hide behind a shared
parser. `tests/test_iso_manifest.py` asserts the independence by checking that
this file contains no import of the writer.

Manifest contents: the image's own size and SHA-256, the volume identity, every
file with its size and SHA-256, the boot catalog entries (system type, bootable
flag, load address, sector count), and the `BUILDINFO.TXT` fields when present.
Sorted, stable and free of the wall clock, so two runs over the same bytes produce
identical output.

    python3 tools/iso_manifest.py build/pc5.iso --output build/pc5.iso.manifest.json
    python3 tools/iso_manifest.py build/pc5.iso --verify build/pc5.iso.manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

SECTOR = 2048
TOOL = "iso_manifest/1"
PVD = 16          # primary volume descriptor sector
BOOT_RECORD = 17  # El Torito boot record sector


class ManifestError(Exception):
    """The image cannot be read as an ISO9660 image with a boot catalog."""


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def _u16(b: bytes, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]


def _both_u32(b: bytes, off: int) -> int:
    """A 733-format field: little-endian first, big-endian copy second."""
    le = _u32(b, off)
    be = struct.unpack_from(">I", b, off + 4)[0]
    if le != be:
        raise ManifestError(f"733 field at {off} disagrees with itself ({le} != {be})")
    return le


def _both_u16(b: bytes, off: int) -> int:
    le = _u16(b, off)
    be = struct.unpack_from(">H", b, off + 2)[0]
    if le != be:
        raise ManifestError(f"723 field at {off} disagrees with itself ({le} != {be})")
    return le


def primary_volume(image: bytes) -> dict:
    off = PVD * SECTOR
    if len(image) < off + SECTOR:
        raise ManifestError("image is too small to contain a volume descriptor set")
    if image[off + 1:off + 6] != b"CD001":
        raise ManifestError("sector 16 is not a primary volume descriptor (no CD001)")
    if image[off] != 1:
        raise ManifestError(f"sector 16 is type {image[off]}, not a primary volume descriptor")
    root = off + 156
    if image[root] != 34:
        raise ManifestError("root directory record is malformed")
    return {
        "volume_id": image[off + 40:off + 72].rstrip(b" \x00").decode("ascii", "replace"),
        "volume_space_size": _both_u32(image, off + 80),
        "path_table_size": _both_u32(image, off + 132),
        "root_extent": _both_u32(image, root + 2),
        "root_length": _both_u32(image, root + 10),
        "logical_block_size": _both_u16(image, off + 128),
        "publisher": image[off + 318:off + 446].rstrip(b" \x00").decode("ascii", "replace"),
        "app_id": image[off + 574:off + 702].rstrip(b" \x00").decode("ascii", "replace"),
    }


def _dir_records(image: bytes, lba: int, length: int) -> list[dict]:
    start = lba * SECTOR
    blob = image[start:start + length]
    if len(blob) < length:
        raise ManifestError(f"directory extent {lba} runs past the end of the image")
    out: list[dict] = []
    pos = 0
    while pos < len(blob):
        size = blob[pos]
        if size == 0:
            pos = (pos // SECTOR + 1) * SECTOR
            continue
        rec = blob[pos:pos + size]
        if len(rec) < 34:
            raise ManifestError(f"truncated directory record at {lba}:{pos}")
        name_len = rec[32]
        name = rec[33:33 + name_len]
        if name in (b"\x00", b"\x01"):
            pos += size
            continue
        flags = rec[25]
        out.append({
            "name": name.decode("ascii", "replace"),
            "is_dir": bool(flags & 0x02),
            "extent": _both_u32(rec, 2),
            "size": _both_u32(rec, 10),
            "recorded_at": rec[18:25].hex(),
        })
        pos += size
    return out


def walk(image: bytes, lba: int, length: int, prefix: str = "") -> list[dict]:
    """Every file in the tree, depth-first, with its ISO path."""
    files: list[dict] = []
    for entry in sorted(_dir_records(image, lba, length), key=lambda e: e["name"]):
        name = entry["name"].split(";")[0]
        path = f"{prefix}{name}"
        if entry["is_dir"]:
            files.extend(walk(image, entry["extent"], entry["size"], path + "/"))
            continue
        start = entry["extent"] * SECTOR
        data = image[start:start + entry["size"]]
        if len(data) != entry["size"]:
            raise ManifestError(f"{path}: extent runs past the end of the image")
        files.append({
            "path": path,
            "size": entry["size"],
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    return files


PLATFORMS = {0: "x86", 1: "powerpc", 2: "mac", 0xEF: "uefi"}
MEDIA = {0: "no-emulation", 1: "1.2MB", 2: "1.44MB", 3: "2.88MB", 4: "hard-disk"}


def _catalog_entry(raw: bytes, platform: str, role: str) -> dict:
    return {
        "role": role,
        "bootable": raw[0] == 0x88,
        "platform": platform,
        "media": MEDIA.get(raw[1], "unknown"),
        "load_segment": _u16(raw, 2),
        "system_type": raw[4],
        "sector_count": _u16(raw, 6),
        "load_rba": _u32(raw, 8),
    }


def boot_catalog(image: bytes) -> list[dict]:
    """El Torito: what firmware will actually execute, and for which platform.

    The initial entry at offset 32 is the BIOS/x86 default. Everything after it is
    either a section header (``0x90``/``0x91``) introducing entries for another
    platform -- which is how the UEFI image is announced -- an entry extension
    (``0x44``), or a further default entry. A reader that ignores section headers
    mislabels the UEFI entry as x86 and reports one entry too many, which is
    exactly what an earlier draft of this tool did.
    """
    off = BOOT_RECORD * SECTOR
    if image[off + 1:off + 6] != b"CD001" or image[off] != 0:
        raise ManifestError("sector 17 is not an El Torito boot record")
    catalog_lba = _u32(image, off + 0x47)
    cat = image[catalog_lba * SECTOR:(catalog_lba + 1) * SECTOR]
    if len(cat) < SECTOR:
        raise ManifestError("boot catalog runs past the end of the image")
    if cat[0:] == b"\x00" * SECTOR:
        raise ManifestError("boot catalog is empty")
    if cat[0] != 1:
        raise ManifestError(f"boot catalog header is version {cat[0]}, not 1")
    if cat[30:32] != b"\x55\xaa":
        raise ManifestError("boot catalog validation entry has no 55 AA key")
    platform = PLATFORMS.get(cat[1], "unknown")
    entries: list[dict] = [{
        "role": "validation",
        "platform": platform,
        "id": cat[4:28].rstrip(b"\x00 ").decode("ascii", "replace"),
    }]
    if cat[32] in (0x88, 0x00) and any(cat[32:64]):
        entries.append(_catalog_entry(cat[32:64], platform, "initial"))

    index = 64
    while index + 32 <= len(cat):
        head = cat[index]
        if head == 0:
            break
        if head in (0x90, 0x91):
            platform = PLATFORMS.get(cat[index + 1], "unknown")
            count = _u16(cat, index + 2)
            entries.append({
                "role": "section-header",
                "platform": platform,
                "final": head == 0x90,
                "entry_count": count,
                "id": cat[index + 4:index + 32].rstrip(b"\x00 ").decode("ascii", "replace"),
            })
            index += 32
            for _ in range(count):
                if index + 32 > len(cat):
                    raise ManifestError("section header promises entries the catalog lacks")
                entries.append(_catalog_entry(cat[index:index + 32], platform, "section"))
                index += 32
            continue
        if head == 0x44:
            index += 32
            continue
        raise ManifestError(f"unexpected catalog byte {head:#04x} at offset {index}")
    if not any(e.get("bootable") for e in entries):
        raise ManifestError("the boot catalog contains no bootable entry")
    return entries


#: The build record ships under its 8.3 name; `iso_name()` in the writer folds
#: BUILDINFO.TXT to BUILDINF.TXT, and a reader that assumes the long name finds
#: nothing at all. Accept either spelling.
def manifest(image: bytes) -> dict:
    """The complete description of an image, in a stable order."""
    volume = primary_volume(image)
    files = walk(image, volume["root_extent"], volume["root_length"])
    return {
        "tool": TOOL,
        "image": {
            "size": len(image),
            "sha256": hashlib.sha256(image).hexdigest(),
            "sectors": len(image) // SECTOR,
        },
        "volume": volume,
        "boot_catalog": boot_catalog(image),
        "files": files,
    }


BUILDINFO_NAMES = ("BUILDINF.TXT", "BUILDINFO.TXT")


def buildinfo_fields(image: bytes, files: list[dict]) -> dict:
    """Parse the `key: value` build record shipped in the image, if any."""
    entry = next((f for f in files if f["path"] in BUILDINFO_NAMES), None)
    if entry is None:
        return {}
    start = None
    volume = primary_volume(image)
    for rec in _dir_records(image, volume["root_extent"], volume["root_length"]):
        if rec["name"].split(";")[0] in BUILDINFO_NAMES:
            start = rec["extent"] * SECTOR
            size = rec["size"]
            break
    if start is None:
        return {}
    text = image[start:start + size].decode("utf-8", "replace")
    fields = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().lower().replace(" ", "_")] = value.strip()
    return fields


def read(path: Path) -> dict:
    data = path.read_bytes()
    manifest_data = manifest(data)
    fields = buildinfo_fields(data, manifest_data["files"])
    if fields:
        manifest_data["buildinfo"] = fields
    return manifest_data


def differences(expected: dict, actual: dict) -> list[str]:
    """Every way two manifests disagree, as sentences."""
    found: list[str] = []
    if expected.get("image") != actual.get("image"):
        found.append(f"image identity differs: {expected.get('image')} != {actual.get('image')}")
    if expected.get("volume") != actual.get("volume"):
        found.append("volume descriptor fields differ")
    if expected.get("boot_catalog") != actual.get("boot_catalog"):
        found.append("boot catalog entries differ")
    want = {f["path"]: f for f in expected.get("files", [])}
    have = {f["path"]: f for f in actual.get("files", [])}
    for path in sorted(set(want) - set(have)):
        found.append(f"missing from the image: {path}")
    for path in sorted(set(have) - set(want)):
        found.append(f"not in the manifest: {path}")
    for path in sorted(set(want) & set(have)):
        if want[path] != have[path]:
            found.append(f"{path}: expected {want[path]['size']} B "
                         f"{want[path]['sha256'][:16]}, found {have[path]['size']} B "
                         f"{have[path]['sha256'][:16]}")
    if expected.get("buildinfo") and expected.get("buildinfo") != actual.get("buildinfo"):
        found.append(f"BUILDINFO differs: {expected.get('buildinfo')} != "
                     f"{actual.get('buildinfo')}")
    return found


def verify(path: Path, manifest_path: Path) -> tuple[bool, list[str]]:
    expected = json.loads(manifest_path.read_text())
    try:
        actual = read(path)
    except ManifestError as exc:
        return False, [f"{path}: {exc}"]
    return (not differences(expected, actual)), differences(expected, actual)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Independent ISO9660 manifest and verifier.")
    ap.add_argument("image")
    ap.add_argument("--output", help="write the manifest JSON here")
    ap.add_argument("--verify", metavar="MANIFEST",
                    help="check the image against a previously written manifest")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    image = Path(args.image)
    if not image.exists():
        print(f"iso-manifest: {image}: no such file", file=sys.stderr)
        return 2

    if args.verify:
        ok, problems = verify(image, Path(args.verify))
        for problem in problems:
            print(f"[FAIL] {problem}")
        if ok:
            print(f"MANIFEST VALID: {image} matches {args.verify}")
            return 0
        return 1 if problems else 2

    try:
        data = read(image)
    except ManifestError as exc:
        print(f"iso-manifest: {image}: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text)
        if not args.quiet:
            print(f"manifest: {args.output} "
                  f"({len(data['files'])} files, {len(data['boot_catalog'])} boot entries, "
                  f"sha256 {data['image']['sha256'][:16]})")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
