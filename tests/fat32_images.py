#!/usr/bin/env python3
"""Deterministic FAT32 fixtures and an *independent* FAT32 reader.

Two jobs, both deliberately separate from `kernel/fat32.c`:

  build_fat32_image()  writes a complete disk: MBR partition table, FAT32 boot
                       sector, FSInfo, two mirrored FATs, a root directory with
                       short names, a long-name record, a volume label, a
                       deleted entry, a subdirectory, and files with known
                       contents (including a fragmented chain).

  read_file()          a second FAT32 implementation, written here from the
                       specification, used to prove that the kernel read the
                       right clusters.  It shares no code with the kernel.

Every fixture is byte-deterministic: fixed timestamps, fixed allocation order,
zero fill elsewhere.
"""

from __future__ import annotations

import struct
import zlib

SECTOR = 512
RESERVED_SECTORS = 32
FAT_COUNT = 2
EOC = 0x0FFFFFFF
ATTR_ARCHIVE = 0x20
ATTR_DIRECTORY = 0x10
ATTR_VOLUME_ID = 0x08
ATTR_LFN = 0x0F
FIXED_DATE = ((2026 - 1980) << 9) | (11 << 5) | 15  # 2026-11-15


def _p16(buf, off, value):
    struct.pack_into("<H", buf, off, value & 0xFFFF)


def _p32(buf, off, value):
    struct.pack_into("<I", buf, off, value & 0xFFFFFFFF)


def _name83(name: str) -> bytes:
    """'NAME.EXT' -> 11 bytes, uppercase, space padded. Test fixture helper."""
    name = name.upper()
    base, _, ext = name.partition(".")
    if len(base) > 8 or len(ext) > 3:
        raise ValueError(f"not an 8.3 name: {name}")
    return base.ljust(8).encode("ascii") + ext.ljust(3).encode("ascii")


def _fat_geometry(partition_sectors: int, spc: int) -> tuple[int, int, int]:
    """Choose a FAT size that can describe every cluster, then return
    (fat_sectors, first_data_sector, cluster_count)."""
    fat = 1
    while True:
        first_data = RESERVED_SECTORS + FAT_COUNT * fat
        data_sectors = partition_sectors - first_data
        if data_sectors < spc * 4:
            raise ValueError("partition too small for a FAT32 volume")
        clusters = data_sectors // spc
        if fat * SECTOR // 4 >= clusters + 2:
            return fat, first_data, clusters
        fat += 1


def _dirent(name83: bytes, attr: int, cluster: int, size: int) -> bytes:
    e = bytearray(32)
    e[0:11] = name83
    e[11] = attr
    e[13] = 0  # creation tenths
    _p16(e, 14, 0x6000)  # creation time 12:00:00
    _p16(e, 16, FIXED_DATE)
    _p16(e, 18, FIXED_DATE)
    _p16(e, 20, (cluster >> 16) & 0xFFFF)  # high word of first cluster
    _p16(e, 22, 0x6000)  # write time
    _p16(e, 24, FIXED_DATE)
    _p16(e, 26, cluster & 0xFFFF)  # low word
    _p32(e, 28, size)
    return bytes(e)


def _lfn_checksum(name83: bytes) -> int:
    s = 0
    for b in name83:
        s = (((s & 1) << 7) + (s >> 1) + b) & 0xFF
    return s


def _lfn_record(long_name: str, name83: bytes, ordinal: int = 1, last: bool = True) -> bytes:
    """One long-file-name slot.  The kernel skips these; the fixture contains a
    real one so that skipping is exercised rather than assumed."""
    chars = long_name.encode("utf-16-le")
    chars += b"\x00\x00"
    chars = chars.ljust(26, b"\xff")
    e = bytearray(32)
    e[0] = ordinal | (0x40 if last else 0)
    e[1:11] = chars[0:10]
    e[11] = ATTR_LFN
    e[12] = 0
    e[13] = _lfn_checksum(name83)
    e[14:26] = chars[10:22]
    e[26:28] = b"\x00\x00"
    e[28:32] = chars[22:26]
    return bytes(e)


def build_fat32_image(partition_lba: int = 2048,
                      partition_sectors: int = 66592,
                      sectors_per_cluster: int = 1,
                      label: str = "PC5FS",
                      holes: int = 0,
                      fill_root: bool = False) -> bytes:
    """A complete disk image with one FAT32 partition and deterministic files.

    The file set is fixed so tests can assert exact sizes and digests:
      PC5TEST.TXT  3000 bytes  (6 clusters, has a long-name record before it)
      SMALL.BIN     100 bytes  (one partial cluster)
      EXACT.BIN     512 bytes  (exactly one sector/cluster)
      EMPTY.TXT       0 bytes  (cluster 0)
      MULTI.BIN    5000 bytes  (10 clusters)
      FRAG.BIN     2000 bytes  (4 clusters, deliberately out of order)
      SUBDIR/                   (directory entry; not descended)
      DELETED.TXT               (marked 0xE5: must be ignored)

    `holes` marks that many clusters as allocated-with-no-owner, one cluster
    apart, so the next allocation by a writer lands on non-contiguous clusters.
    `fill_root` consumes every remaining root-directory slot with empty files,
    so a create has nowhere to put its entry.
    """
    fat_sectors, first_data, clusters = _fat_geometry(partition_sectors, sectors_per_cluster)
    total = partition_sectors
    image = bytearray((partition_lba + total) * SECTOR)

    # ---- MBR ----
    mbr = 446
    image[mbr + 4] = 0x0C  # FAT32 LBA
    _p32(image, mbr + 8, partition_lba)
    _p32(image, mbr + 12, total)
    image[510] = 0x55
    image[511] = 0xAA

    base = partition_lba * SECTOR
    part = memoryview(image)[base:base + total * SECTOR]

    # ---- boot sector ----
    bs = bytearray(SECTOR)
    bs[0:3] = b"\xeb\x58\x90"
    bs[3:11] = b"PC5FAT32"
    _p16(bs, 11, SECTOR)
    bs[13] = sectors_per_cluster
    _p16(bs, 14, RESERVED_SECTORS)
    bs[16] = FAT_COUNT
    _p16(bs, 17, 0)          # root entries: 0 means FAT32
    _p16(bs, 19, 0)          # 16-bit total: 0
    bs[21] = 0xF8            # fixed disk
    _p16(bs, 22, 0)          # 16-bit FAT size: 0 means FAT32
    _p16(bs, 24, 63)
    _p16(bs, 26, 255)
    _p32(bs, 28, partition_lba)
    _p32(bs, 32, total)
    _p32(bs, 36, fat_sectors)
    _p16(bs, 40, 0)          # mirrored FATs
    _p16(bs, 42, 0)          # version 0.0
    _p32(bs, 44, 2)          # root cluster
    _p16(bs, 48, 1)          # FSInfo sector
    _p16(bs, 50, 6)          # backup boot sector
    bs[64] = 0x80
    bs[66] = 0x29
    _p32(bs, 67, 0x5A1B2C3D)
    bs[71:82] = label.ljust(11).encode("ascii")
    bs[82:90] = b"FAT32   "
    bs[510] = 0x55
    bs[511] = 0xAA
    part[0:SECTOR] = bs

    # ---- FSInfo (sector 1) and its backup (sector 7) ----
    fsinfo = bytearray(SECTOR)
    _p32(fsinfo, 0, 0x41615252)
    _p32(fsinfo, 484, 0x61417272)
    _p32(fsinfo, 488, max(0, clusters - 30))
    _p32(fsinfo, 492, 3)
    _p32(fsinfo, 508, 0xAA550000)
    part[SECTOR:2 * SECTOR] = fsinfo
    part[7 * SECTOR:8 * SECTOR] = fsinfo
    # backup boot sector (sector 6, FSInfo backup at 7)
    part[6 * SECTOR:7 * SECTOR] = bs

    # ---- directories ----
    root_dir = bytearray(sectors_per_cluster * SECTOR)
    dirs = []
    root_entries = bytearray()

    root_entries += _dirent(b"PC5FS      ", ATTR_VOLUME_ID, 0, 0)
    root_entries += _dirent(b"DELETED TXT", ATTR_ARCHIVE, 0, 0)
    root_entries[-32] = 0xE5  # deleted: the kernel must ignore it

    files = [
        ("PC5TEST.TXT", bytes((i * 13 + 7) & 0xFF for i in range(3000)), None, True),
        ("SMALL.BIN", bytes((i * 3 + 1) & 0xFF for i in range(100)), None, False),
        ("EXACT.BIN", bytes((0xA5 ^ i) & 0xFF for i in range(512)), None, False),
        ("EMPTY.TXT", b"", None, False),
        ("MULTI.BIN", bytes((i * 7 + 11) & 0xFF for i in range(5000)), None, False),
    ]

    next_cluster = 3
    payload = {}  # cluster -> bytes

    def allocate(chain_len):
        nonlocal next_cluster
        chain = list(range(next_cluster, next_cluster + chain_len))
        next_cluster += chain_len
        if next_cluster > clusters + 1:
            raise ValueError("fixture does not fit")
        return chain

    manifest = []
    for name, data, _chain, with_lfn in files:
        nclusters = (len(data) + sectors_per_cluster * SECTOR - 1) // (sectors_per_cluster * SECTOR)
        chain = allocate(nclusters)
        for i, cl in enumerate(chain):
            chunk = data[i * sectors_per_cluster * SECTOR:(i + 1) * sectors_per_cluster * SECTOR]
            payload[cl] = chunk
        name83 = _name83(name)
        if with_lfn:
            root_entries += _lfn_record(name, name83)
        first = chain[0] if chain else 0
        root_entries += _dirent(name83, ATTR_ARCHIVE, first, len(data))
        manifest.append({"name": name, "size": len(data), "chain": chain,
                         "sha256": __import__("hashlib").sha256(data).hexdigest()})
        dirs.append((chain, None))

    # fragmented file: four clusters, out of order, written explicitly
    frag = bytes((i * 5 + 3) & 0xFF for i in range(2000))
    frag_chain = [allocate(1)[0], allocate(1)[0], allocate(1)[0], allocate(1)[0]]
    frag_chain = [frag_chain[0], frag_chain[2], frag_chain[1], frag_chain[3]]
    for i, cl in enumerate(frag_chain):
        payload[cl] = frag[i * SECTOR:(i + 1) * SECTOR]
    root_entries += _dirent(_name83("FRAG.BIN"), ATTR_ARCHIVE, frag_chain[0], len(frag))
    manifest.append({"name": "FRAG.BIN", "size": len(frag), "chain": frag_chain,
                     "sha256": __import__("hashlib").sha256(frag).hexdigest()})

    # a subdirectory (root lists it; the reader must refuse to read it as a file)
    sub_cluster = allocate(1)[0]
    payload[sub_cluster] = bytes(SECTOR)
    root_entries += _dirent(_name83("SUBDIR"), ATTR_DIRECTORY, sub_cluster, 0)

    # fragmentation holes: allocated clusters nothing points at, one apart, so
    # the next two-cluster allocation is physically non-contiguous
    hole_clusters = []
    for _ in range(holes):
        hole_clusters.append(allocate(1)[0])
        allocate(1)  # left free, so the free space is a comb, not a run

    # fill the root directory: 16 slots per 512-byte sector, and the fixture
    # uses slots 0..10, so five dummy entries make the directory exactly full
    if fill_root:
        free_slots = (sectors_per_cluster * SECTOR // 32) - len(root_entries) // 32
        for i in range(free_slots):
            root_entries += _dirent(_name83(f"FULL{i}"), ATTR_ARCHIVE, 0, 0)

    if len(root_entries) > sectors_per_cluster * SECTOR:
        raise ValueError("root directory does not fit in one cluster")

    # root directory lives in cluster 2
    payload[2] = bytes(root_entries).ljust(sectors_per_cluster * SECTOR, b"\x00")
    root_dir[:] = payload[2]
    part[first_data * SECTOR:(first_data + sectors_per_cluster) * SECTOR] = root_dir

    # ---- data clusters + FAT ----
    fat = bytearray(fat_sectors * SECTOR)
    _p32(fat, 0, 0x0FFFFFF8)
    _p32(fat, 4, EOC)
    for name, data, _chain, _lfn in files:
        entry = next(m for m in manifest if m["name"] == name)
        chain = entry["chain"]
        for i, cl in enumerate(chain):
            nxt = chain[i + 1] if i + 1 < len(chain) else EOC
            _p32(fat, cl * 4, nxt)
    for i, cl in enumerate(frag_chain):
        _p32(fat, cl * 4, frag_chain[i + 1] if i + 1 < len(frag_chain) else EOC)
    _p32(fat, sub_cluster * 4, EOC)
    _p32(fat, 2 * 4, EOC)  # the root directory cluster is allocated
    for cl in hole_clusters:
        _p32(fat, cl * 4, EOC)

    for cl, chunk in payload.items():
        start = (first_data + (cl - 2) * sectors_per_cluster) * SECTOR
        part[start:start + len(chunk)] = chunk

    for i in range(FAT_COUNT):
        off = (RESERVED_SECTORS + i * fat_sectors) * SECTOR
        part[off:off + len(fat)] = fat

    return bytes(image)


# --------------------------------------------------------------------------
# Independent reader: a second FAT32 implementation, used only by the tests.
# --------------------------------------------------------------------------

def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def read_file(image: bytes, name: str, part_lba: int = 2048) -> bytes:
    """Read `name` (8.3, case-insensitive) from the FAT32 partition at `part_lba`.

    This deliberately re-derives everything from the bytes: BPB, FAT, directory
    walk and cluster chain.  It shares no code with kernel/fat32.c.
    """
    boot = image[part_lba * SECTOR:(part_lba + 1) * SECTOR]
    if boot[510:512] != b"\x55\xaa":
        raise ValueError("no boot signature")
    if _u16(boot, 11) != SECTOR:
        raise ValueError("unsupported sector size")
    spc = boot[13]
    reserved = _u16(boot, 14)
    nfats = boot[16]
    fat_sectors = _u32(boot, 36)
    root_cluster = _u32(boot, 44)
    total = _u32(boot, 32)
    first_data = reserved + nfats * fat_sectors

    def sector(rel):
        return image[(part_lba + rel) * SECTOR:(part_lba + rel + 1) * SECTOR]

    def fat_entry(cluster):
        byte = cluster * 4
        sec = sector(reserved + byte // SECTOR)
        return _u32(sec, byte % SECTOR) & 0x0FFFFFFF

    def cluster_bytes(cluster):
        start = (part_lba + first_data + (cluster - 2) * spc) * SECTOR
        return image[start:start + spc * SECTOR]

    want = _name83(name)
    cluster = root_cluster
    seen = 0
    while True:
        seen += 1
        if seen > total:
            raise ValueError("directory loop")
        block = cluster_bytes(cluster)
        for off in range(0, len(block), 32):
            ent = block[off:off + 32]
            if ent[0] == 0x00:
                raise KeyError(name)
            if ent[0] == 0xE5 or ent[11] == ATTR_LFN or ent[11] & ATTR_VOLUME_ID:
                continue
            if ent[11] & ATTR_DIRECTORY and ent[0:11] == want:
                raise IsADirectoryError(name)
            if ent[0:11] == want:
                size = _u32(ent, 28)
                first = (_u16(ent, 20) << 16) | _u16(ent, 26)
                if size == 0:
                    return b""
                out = bytearray()
                cur = first
                while len(out) < size:
                    out += cluster_bytes(cur)
                    if len(out) >= size:
                        break
                    cur = fat_entry(cur)
                    if cur >= 0x0FFFFFF8:
                        raise ValueError("chain shorter than file size")
                return bytes(out[:size])
        nxt = fat_entry(cluster)
        if nxt >= 0x0FFFFFF8:
            raise KeyError(name)
        cluster = nxt


def expected_files() -> dict:
    """The bytes each fixture file must contain, regenerated independently of
    the image layout.  Tests compare three things: these bytes, the independent
    reader's output and the kernel module's output."""
    return {
        "PC5TEST.TXT": bytes((i * 13 + 7) & 0xFF for i in range(3000)),
        "SMALL.BIN": bytes((i * 3 + 1) & 0xFF for i in range(100)),
        "EXACT.BIN": bytes((0xA5 ^ i) & 0xFF for i in range(512)),
        "EMPTY.TXT": b"",
        "MULTI.BIN": bytes((i * 7 + 11) & 0xFF for i in range(5000)),
        "FRAG.BIN": bytes((i * 5 + 3) & 0xFF for i in range(2000)),
    }


def corrupt_bpb(image: bytes, offset: int, value: int, width: int = 2) -> bytes:
    """Return a copy with one BPB field replaced (used for refusal tests)."""
    bad = bytearray(image)
    base = 2048 * SECTOR
    if width == 1:
        bad[base + offset] = value & 0xFF
    elif width == 2:
        struct.pack_into("<H", bad, base + offset, value & 0xFFFF)
    else:
        struct.pack_into("<I", bad, base + offset, value & 0xFFFFFFFF)
    return bytes(bad)


def entry_sha256(image: bytes, name: str) -> str:
    return __import__("hashlib").sha256(read_file(image, name)).hexdigest()


if __name__ == "__main__":  # tiny self-check, useful when editing fixtures
    img = build_fat32_image()
    for n in ("PC5TEST.TXT", "SMALL.BIN", "EXACT.BIN", "EMPTY.TXT", "MULTI.BIN", "FRAG.BIN"):
        data = read_file(img, n)
        print(f"{n:12s} {len(data):6d} {zlib.crc32(data) & 0xFFFFFFFF:08x}")


# --------------------------------------------------------------------------
# Structural helpers used to check what a *writer* did.  A test that only asks
# "can the file be read back" cannot tell a shared chain from an orphaned one,
# so these read the volume's structures directly.
# --------------------------------------------------------------------------

def volume_geometry(image: bytes, part_lba: int = 2048) -> dict:
    """Everything the checks below need, derived from the boot sector."""
    boot = image[part_lba * SECTOR:(part_lba + 1) * SECTOR]
    if boot[510:512] != b"\x55\xaa":
        raise ValueError("no boot signature")
    spc = boot[13]
    reserved = _u16(boot, 14)
    nfats = boot[16]
    fat_sectors = _u32(boot, 36)
    total = _u32(boot, 32)
    first_data = reserved + nfats * fat_sectors
    clusters = (total - first_data) // spc if total > first_data else 0
    return {
        "part_lba": part_lba,
        "spc": spc,
        "reserved": reserved,
        "nfats": nfats,
        "fat_sectors": fat_sectors,
        "total": total,
        "first_data": first_data,
        "clusters": clusters,
        "root_cluster": _u32(boot, 44),
        "fsinfo": _u16(boot, 48),
        "backup": _u16(boot, 50),
        "sector": SECTOR,
    }


def fat_copies(image: bytes, part_lba: int = 2048) -> list[bytes]:
    """Every FAT copy, as raw bytes.  Mirrored volumes must have identical copies."""
    g = volume_geometry(image, part_lba)
    start = (part_lba + g["reserved"]) * SECTOR
    size = g["fat_sectors"] * SECTOR
    return [image[start + i * size:start + (i + 1) * size] for i in range(g["nfats"])]


def fat_entries(image: bytes, part_lba: int = 2048, copy: int = 0) -> list[int]:
    """The FAT as a list of masked 32-bit entries (entry N describes cluster N)."""
    g = volume_geometry(image, part_lba)
    raw = fat_copies(image, part_lba)[copy]
    count = g["clusters"] + 2
    return [struct.unpack_from("<I", raw, i * 4)[0] & 0x0FFFFFFF for i in range(count)]


def list_dir(image: bytes, part_lba: int = 2048) -> list[dict]:
    """Every live short-name entry in the root directory, in slot order."""
    g = volume_geometry(image, part_lba)
    entries = []
    cluster = g["root_cluster"]
    fat = fat_entries(image, part_lba)
    seen = 0
    while True:
        seen += 1
        if seen > g["clusters"] + 2:
            raise ValueError("directory loop")
        start = (part_lba + g["first_data"] + (cluster - 2) * g["spc"]) * SECTOR
        block = image[start:start + g["spc"] * SECTOR]
        for off in range(0, len(block), 32):
            ent = block[off:off + 32]
            if ent[0] == 0x00:
                return entries
            if ent[0] == 0xE5 or ent[11] == ATTR_LFN or ent[11] & ATTR_VOLUME_ID:
                continue
            name = ent[0:8].decode("ascii", "replace").rstrip()
            ext = ent[8:11].decode("ascii", "replace").rstrip()
            entries.append({
                "name": f"{name}.{ext}" if ext else name,
                "attr": ent[11],
                "size": _u32(ent, 28),
                "first_cluster": (_u16(ent, 20) << 16) | _u16(ent, 26),
                "slot_sector": None,
                "slot_offset": off,
            })
        nxt = fat[cluster]
        if nxt >= 0x0FFFFFF8:
            return entries
        cluster = nxt


def cluster_chain(image: bytes, first: int, part_lba: int = 2048,
                  limit: int | None = None) -> list[int]:
    fat = fat_entries(image, part_lba)
    g = volume_geometry(image, part_lba)
    chain = []
    cur = first
    while True:
        if len(chain) > (limit if limit is not None else g["clusters"] + 2):
            raise ValueError("chain loop")
        chain.append(cur)
        nxt = fat[cur] if cur < len(fat) else 0x0FFFFFFF
        if nxt >= 0x0FFFFFF8:
            return chain
        if nxt == 0:
            raise ValueError("chain runs into a free cluster")
        cur = nxt


def allocated_clusters(image: bytes, part_lba: int = 2048) -> set[int]:
    """Clusters the FAT marks as in use (anything that is not free)."""
    fat = fat_entries(image, part_lba)
    return {c for c in range(2, len(fat)) if fat[c] != 0}


def referenced_clusters(image: bytes, part_lba: int = 2048) -> set[int]:
    """Clusters reachable from a live root-directory entry, plus the root
    directory itself (which no directory entry points at)."""
    used = {volume_geometry(image, part_lba)["root_cluster"]}
    for ent in list_dir(image, part_lba):
        if ent["size"] == 0 or ent["first_cluster"] < 2:
            if ent["attr"] & ATTR_DIRECTORY and ent["first_cluster"] >= 2:
                used.add(ent["first_cluster"])
            continue
        used.update(cluster_chain(image, ent["first_cluster"], part_lba))
    return used


def orphan_clusters(image: bytes, part_lba: int = 2048) -> set[int]:
    """Allocated but unreachable: a leak, which is the worst a failed create may do."""
    return allocated_clusters(image, part_lba) - referenced_clusters(image, part_lba)


def free_cluster_count(image: bytes, part_lba: int = 2048) -> int:
    fat = fat_entries(image, part_lba)
    return sum(1 for c in range(2, len(fat)) if fat[c] == 0)


def fsinfo_fields(image: bytes, part_lba: int = 2048, sector: int | None = None) -> dict:
    g = volume_geometry(image, part_lba)
    sec = g["fsinfo"] if sector is None else sector
    block = image[(part_lba + sec) * SECTOR:(part_lba + sec + 1) * SECTOR]
    return {
        "sector": sec,
        "lead": _u32(block, 0),
        "struct": _u32(block, 484),
        "free_count": _u32(block, 488),
        "next_free": _u32(block, 492),
        "trail": _u32(block, 508),
        "signatures_ok": (_u32(block, 0) == 0x41615252
                          and _u32(block, 484) == 0x61417272
                          and _u32(block, 508) == 0xAA550000),
    }


def changed_sectors(before: bytes, after: bytes) -> set[int]:
    """Sector indices whose 512-byte content differs.  A write test asserts this
    set is confined to the regions a create is allowed to touch."""
    if len(before) != len(after):
        raise ValueError("images differ in size")
    out = set()
    for i in range(0, len(before), SECTOR):
        if before[i:i + SECTOR] != after[i:i + SECTOR]:
            out.add(i // SECTOR)
    return out


def volume_files(image: bytes, part_lba: int = 2048) -> dict[str, bytes]:
    """Every regular file in the root directory, read by the independent reader.
    Raises if any of them cannot be read, which is itself a corruption signal."""
    out = {}
    for ent in list_dir(image, part_lba):
        if ent["attr"] & ATTR_DIRECTORY:
            continue
        out[ent["name"]] = read_file(image, ent["name"], part_lba)
    return out
