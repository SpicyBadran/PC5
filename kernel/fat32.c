/*
 * PC5 read-only FAT32.
 *
 * Scope: mount one FAT32 volume on a partition discovered by M1e, validate
 * its BPB and geometry, walk its FAT, enumerate its root directory, find an
 * 8.3 short name, and read that file through the sector source.  Nothing here
 * writes: there is no allocation, no directory update, no FAT update and no
 * flush.  The module does not even know a device index exists — it reads
 * through `struct fat32_io`, which lets the host tests mount an image held in
 * memory and lets the kernel mount a real partition.
 *
 * M1g added the first write operation, fat32_create_file().  M1i adds two more
 * and the path syntax they need:
 *
 *   * fat32_overwrite_file() — replace the content of an existing regular file,
 *     keeping its name and directory entry.  The replacement is built in new
 *     clusters and the old chain is freed only after the new one is written,
 *     flushed and named by one directory-entry write, so an interruption leaves
 *     the old content intact or the new content complete plus, at worst, a
 *     leaked chain that is reported and quantified.
 *   * fat32_create_dir() — create a directory with its "." and ".." entries,
 *     inside an existing parent.  A directory that has no free slot is grown by
 *     one cluster first (FAT32 directories are ordinary cluster chains; only
 *     FAT12/16 roots are fixed, and this code refuses anything that is not
 *     structurally FAT32).
 *
 * Every write path orders its steps the same way: content -> FAT -> FSInfo ->
 * flush -> directory entry -> flush -> read-back.  A crash can leak clusters
 * and can leave a stale FSInfo counter (both reported, both safe); it cannot
 * make a name refer to data that was not written first.
 *
 * Deliberately unsupported, and said out loud:
 *   * long file names (LFN entries are skipped, not guessed) and LFN creation,
 *   * sector sizes other than 512 bytes,
 *   * FAT12/FAT16 (refused structurally, not by convention),
 *   * "." / ".." navigation in a path, leading or trailing separators,
 *   * deleting, renaming, truncating in place, resizing an existing chain,
 *     attribute changes, timestamps from a clock, multiple writers, journal,
 *     repair or any form of atomic transaction.
 *
 * Path syntax: COMPONENT { "/" COMPONENT }, 8.3 components, case-insensitive,
 * at most FAT32_PATH_DEPTH levels and FAT32_PATH_MAX characters.  Anything
 * longer, deeper or otherwise unsupported is refused, never truncated.
 *
 * Bounds discipline: every sector number is checked against the partition
 * before it is read or written, every cluster against the volume, and every
 * chain walk is bounded by the cluster count so a cycle cannot loop forever.
 */
#include "pc5.h"

#define SEC 512u
#define FAT32_MASK 0x0fffffffu
#define FAT32_EOC_MIN 0x0ffffff8u
#define FAT32_BAD 0x0ffffff7u
#define FAT32_RESERVED_MIN 0x0ffffff0u
#define FAT32_EOC 1 /* internal: end of chain, never returned to callers */

static u8 sec[SEC];

static u32 rd16(const u8 *p) { return (u32)p[0] | ((u32)p[1] << 8); }

static u32 rd32(const u8 *p)
{
    return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}

const char *fat32_status_name(int status)
{
    switch (status) {
    case FAT32_OK: return "ok";
    case FAT32_E_IO: return "io";
    case FAT32_E_SIGNATURE: return "signature";
    case FAT32_E_BPB: return "bpb";
    case FAT32_E_NOT_FAT32: return "not-fat32";
    case FAT32_E_GEOMETRY: return "geometry";
    case FAT32_E_NAME: return "name";
    case FAT32_E_NO_FILE: return "no-file";
    case FAT32_E_IS_DIR: return "directory";
    case FAT32_E_SPACE: return "space";
    case FAT32_E_CHAIN: return "chain";
    case FAT32_E_FREE: return "free-cluster";
    case FAT32_E_BAD: return "bad-cluster";
    case FAT32_E_RESERVED: return "reserved-cluster";
    case FAT32_E_CLUSTER: return "cluster";
    case FAT32_E_LOOP: return "loop";
    case FAT32_E_CORRUPT: return "corrupt";
    case FAT32_E_READ_ONLY: return "read-only";
    case FAT32_E_EXISTS: return "exists";
    case FAT32_E_FULL: return "full";
    case FAT32_E_DIR_FULL: return "dir-full";
    case FAT32_E_VERIFY: return "verify";
    case FAT32_E_FLUSH: return "flush";
    case FAT32_E_TOO_BIG: return "too-big";
    case FAT32_E_NOT_DIR: return "not-dir";
    default: return "unknown";
    }
}

/* Read a partition-relative sector, refusing anything outside the partition. */
static int read_rel(const struct fat32_io *io, const struct fat32_volume *v,
                    u32 rel, void *buf)
{
    if (rel >= v->sectors)
        return FAT32_E_GEOMETRY;
    u32 lba = v->base_lba + rel;
    if (lba < v->base_lba) /* overflow */
        return FAT32_E_GEOMETRY;
    if (!io || !io->read)
        return FAT32_E_IO;
    if (io->read(io->ctx, lba, 1, buf))
        return FAT32_E_IO;
    return FAT32_OK;
}

/* Partition-relative sector holding `index` within `cluster`. */
static int cluster_sector(const struct fat32_volume *v, u32 cluster, u32 index, u32 *rel)
{
    if (cluster < 2 || cluster >= v->cluster_count + 2)
        return FAT32_E_CLUSTER;
    if (index >= v->sectors_per_cluster)
        return FAT32_E_GEOMETRY;
    u64 off = (u64)v->first_data_sector +
              (u64)(cluster - 2) * v->sectors_per_cluster + index;
    if (off + 1 > v->total_sectors || off + 1 > v->sectors)
        return FAT32_E_GEOMETRY;
    *rel = (u32)off;
    return FAT32_OK;
}

/* FAT entry for `cluster`, masked and classified.  Returns FAT32_OK with the
 * raw next value, FAT32_E_... for free/bad/reserved/out-of-range. */
static int fat_next_raw(const struct fat32_io *io, const struct fat32_volume *v,
                        u32 cluster, u32 *next)
{
    if (cluster < 2 || cluster >= v->cluster_count + 2)
        return FAT32_E_CLUSTER;
    u32 byte = cluster * 4u;
    u32 sector = byte / v->bytes_per_sector;
    u32 inner = byte % v->bytes_per_sector;
    if (sector >= v->fat_sectors)
        return FAT32_E_GEOMETRY;
    u32 rel = v->reserved_sectors + v->active_fat * v->fat_sectors + sector;
    int rc = read_rel(io, v, rel, sec);
    if (rc != FAT32_OK)
        return rc;
    *next = rd32(sec + inner) & FAT32_MASK;
    return FAT32_OK;
}

/* Follow one link of a cluster chain.  Returns FAT32_EOC at the end. */
static int next_cluster(const struct fat32_io *io, const struct fat32_volume *v,
                        u32 cluster, u32 *next)
{
    u32 raw;
    int rc = fat_next_raw(io, v, cluster, &raw);
    if (rc != FAT32_OK)
        return rc;
    if (raw >= FAT32_EOC_MIN) {
        *next = 0;
        return FAT32_EOC;
    }
    if (raw == 0)
        return FAT32_E_FREE;
    if (raw == FAT32_BAD)
        return FAT32_E_BAD;
    if (raw >= FAT32_RESERVED_MIN)
        return FAT32_E_RESERVED;
    if (raw < 2 || raw >= v->cluster_count + 2)
        return FAT32_E_CLUSTER;
    *next = raw;
    return FAT32_OK;
}

/* "NAME.EXT" -> the 11 bytes a directory entry stores.  Rejects anything that
 * is not a legal 8.3 short name instead of mangling it. */
static int name_to_83(const char *name, u8 out[11])
{
    if (!name || !name[0])
        return FAT32_E_NAME;
    for (int i = 0; i < 11; i++)
        out[i] = ' ';
    int i = 0, base = 0;
    for (; name[i] && name[i] != '.'; i++) {
        if (base >= 8)
            return FAT32_E_NAME;
        char c = name[i];
        if (c >= 'a' && c <= 'z')
            c = (char)(c - 'a' + 'A');
        int ok = (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' ||
                 c == '-' || c == '~' || c == '$' || c == '!' || c == '#';
        if (!ok)
            return FAT32_E_NAME;
        out[base++] = (u8)c;
    }
    if (base == 0)
        return FAT32_E_NAME;
    if (name[i] == '.') {
        i++;
        int ext = 0;
        for (; name[i]; i++) {
            if (ext >= 3)
                return FAT32_E_NAME;
            char c = name[i];
            if (c >= 'a' && c <= 'z')
                c = (char)(c - 'a' + 'A');
            int ok = (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                     c == '_' || c == '-' || c == '~';
            if (!ok)
                return FAT32_E_NAME;
            out[8 + ext++] = (u8)c;
        }
        if (ext == 0)
            return FAT32_E_NAME;
    }
    return FAT32_OK;
}

static void name_from_83(const u8 *raw, char out[13])
{
    int n = 0;
    for (int i = 0; i < 8 && raw[i] != ' '; i++)
        out[n++] = (char)raw[i];
    if (raw[8] != ' ') {
        out[n++] = '.';
        for (int i = 8; i < 11 && raw[i] != ' '; i++)
            out[n++] = (char)raw[i];
    }
    out[n] = 0;
}

int fat32_mount(const struct fat32_io *io, u32 base_lba, u32 sectors,
                struct fat32_volume *vol)
{
    if (!io || !io->read || !vol)
        return FAT32_E_IO;
    memset(vol, 0, sizeof(*vol));
    vol->base_lba = base_lba;
    vol->sectors = sectors;

    int rc = read_rel(io, vol, 0, sec);
    if (rc != FAT32_OK) {
        vol->error = rc;
        return rc;
    }

    if (sec[510] != 0x55 || sec[511] != 0xaa) {
        vol->error = FAT32_E_SIGNATURE;
        return FAT32_E_SIGNATURE;
    }

    u32 bps = rd16(sec + 11);
    if (bps != SEC) { /* only 512-byte sectors are implemented */
        vol->error = FAT32_E_BPB;
        return FAT32_E_BPB;
    }
    u32 spc = sec[13];
    if (spc == 0 || (spc & (spc - 1)) || spc > 128) {
        vol->error = FAT32_E_BPB;
        return FAT32_E_BPB;
    }
    u32 reserved = rd16(sec + 14);
    u32 fat_count = sec[16];
    u32 root_entries = rd16(sec + 17);
    u32 total16 = rd16(sec + 19);
    u32 media = sec[21];
    u32 fat16_sectors = rd16(sec + 22);
    u32 total32 = rd32(sec + 32);
    u32 fat32_sectors = rd32(sec + 36);
    u32 version = rd16(sec + 42);
    u32 root_cluster = rd32(sec + 44);
    u32 fsinfo = rd16(sec + 48);
    u32 backup = rd16(sec + 50);

    if (reserved == 0 || fat_count == 0 || fat_count > 4 || fat32_sectors == 0) {
        vol->error = FAT32_E_BPB;
        return FAT32_E_BPB;
    }
    /* Structural FAT32 markers.  FAT16 has a non-zero root-entry count and a
     * 16-bit FAT size; a volume carrying either is not FAT32 whatever the
     * BPB string says. */
    if (root_entries != 0 || fat16_sectors != 0) {
        vol->error = FAT32_E_NOT_FAT32;
        return FAT32_E_NOT_FAT32;
    }
    if (version != 0) {
        vol->error = FAT32_E_NOT_FAT32;
        return FAT32_E_NOT_FAT32;
    }
    if (media != 0xf0 && media < 0xf8) {
        vol->error = FAT32_E_BPB;
        return FAT32_E_BPB;
    }
    if (total16 != 0 && total32 != 0 && total16 != total32) {
        vol->error = FAT32_E_GEOMETRY;
        return FAT32_E_GEOMETRY;
    }
    u32 total = total32 ? total32 : total16;
    if (total == 0) {
        vol->error = FAT32_E_GEOMETRY;
        return FAT32_E_GEOMETRY;
    }
    if (total > sectors) { /* volume larger than the partition claims */
        vol->error = FAT32_E_GEOMETRY;
        return FAT32_E_GEOMETRY;
    }

    u64 first_data_64 = (u64)reserved + (u64)fat_count * fat32_sectors;
    if (first_data_64 + spc > total) {
        vol->error = FAT32_E_GEOMETRY;
        return FAT32_E_GEOMETRY;
    }
    u32 first_data = (u32)first_data_64;
    u32 clusters = (total - first_data) / spc;
    if (clusters < 1) {
        vol->error = FAT32_E_GEOMETRY;
        return FAT32_E_GEOMETRY;
    }
    /* The FAT must be able to describe every cluster plus the two reserved
     * entries; otherwise a valid-looking chain can point outside the table. */
    u64 fat_entries = (u64)fat32_sectors * (bps / 4);
    if (fat_entries < (u64)clusters + 2) {
        vol->error = FAT32_E_GEOMETRY;
        return FAT32_E_GEOMETRY;
    }
    if (root_cluster < 2 || root_cluster >= clusters + 2) {
        vol->error = FAT32_E_GEOMETRY;
        return FAT32_E_GEOMETRY;
    }
    if (fsinfo != 0 && fsinfo != 0xffff && fsinfo >= reserved) {
        vol->error = FAT32_E_BPB;
        return FAT32_E_BPB;
    }
    if (backup != 0 && backup != 0xffff && backup >= reserved) {
        vol->error = FAT32_E_BPB;
        return FAT32_E_BPB;
    }

    u32 flags = rd16(sec + 40);
    u32 active = (flags & 0x0080) ? (flags & 0x000f) : 0;
    if (active >= fat_count) {
        vol->error = FAT32_E_BPB;
        return FAT32_E_BPB;
    }

    vol->bytes_per_sector = bps;
    vol->sectors_per_cluster = spc;
    vol->reserved_sectors = reserved;
    vol->fat_count = fat_count;
    vol->fat_sectors = fat32_sectors;
    vol->active_fat = active;
    vol->total_sectors = total;
    vol->first_data_sector = first_data;
    vol->cluster_count = clusters;
    vol->root_cluster = root_cluster;
    vol->fsinfo_sector = (fsinfo == 0xffff) ? 0 : fsinfo;
    vol->backup_boot_sector = (backup == 0xffff) ? 0 : backup;

    int ft = 0;
    for (int i = 0; i < 8 && sec[82 + i]; i++)
        vol->fs_type[ft++] = (char)sec[82 + i];
    while (ft > 0 && vol->fs_type[ft - 1] == ' ')
        ft--;
    vol->fs_type[ft] = 0;
    if (vol->fs_type[0] && strncmp(vol->fs_type, "FAT32", 5) != 0) {
        vol->error = FAT32_E_NOT_FAT32;
        return FAT32_E_NOT_FAT32;
    }

    for (int i = 0; i < 11; i++)
        vol->label[i] = (char)sec[71 + i];
    vol->label[11] = 0;
    for (int i = 10; i >= 0 && (vol->label[i] == ' ' || vol->label[i] == 0); i--)
        vol->label[i] = 0;

    vol->error = FAT32_OK;
    return FAT32_OK;
}

struct dir_scan {
    const u8 *want;           /* 11 bytes, or NULL to list */
    struct fat32_entry *list;
    int max;
    int count;
    int found;
    u32 cluster;
    u32 size;
    u8 attr;
};

static int scan_dir(const struct fat32_io *io, const struct fat32_volume *v,
                    u32 start, struct dir_scan *s)
{
    if (start < 2 || start >= v->cluster_count + 2)
        return FAT32_E_CLUSTER;

    u32 cluster = start;
    u32 visited = 0;

    for (;;) {
        if (++visited > v->cluster_count)
            return FAT32_E_LOOP;
        for (u32 si = 0; si < v->sectors_per_cluster; si++) {
            u32 rel;
            int rc = cluster_sector(v, cluster, si, &rel);
            if (rc != FAT32_OK)
                return rc;
            rc = read_rel(io, v, rel, sec);
            if (rc != FAT32_OK)
                return rc;
            for (u32 e = 0; e < SEC / 32; e++) {
                const u8 *ent = sec + e * 32;
                if (ent[0] == 0x00)     /* end of directory */
                    return FAT32_OK;
                if (ent[0] == 0xe5)     /* deleted */
                    continue;
                u8 attr = ent[11];
                if (attr == 0x0f)       /* LFN: skipped by design */
                    continue;
                if (attr & 0x08)        /* volume label */
                    continue;
                u32 cluster_lo = rd16(ent + 26);
                u32 cluster_hi = rd16(ent + 20);
                u32 entry_cluster = (cluster_hi << 16) | cluster_lo;
                u32 size = rd32(ent + 28);

                if (s->want) {
                    if (memcmp(ent, s->want, 11) == 0) {
                        s->found = 1;
                        s->cluster = entry_cluster;
                        s->size = size;
                        s->attr = attr;
                        return FAT32_OK;
                    }
                    continue;
                }

                /* listing: skip "." and ".." */
                if (ent[0] == '.' && (ent[1] == ' ' || (ent[1] == '.' && ent[2] == ' ')))
                    continue;
                if (s->list && s->count < s->max) {
                    struct fat32_entry *out = &s->list[s->count];
                    name_from_83(ent, out->name);
                    out->size = size;
                    out->attr = attr;
                }
                s->count++;
            }
        }
        u32 next;
        int rc = next_cluster(io, v, cluster, &next);
        if (rc == FAT32_EOC)
            return FAT32_OK;
        if (rc != FAT32_OK)
            return rc;
        cluster = next;
    }
}

/* ------------------------------------------------------- paths (M1i) --- */
/*
 * The path grammar is deliberately tiny, and every rejection is explicit:
 *   COMPONENT { "/" COMPONENT }, each COMPONENT an 8.3 short name.
 * No leading or trailing "/", no empty component, no "." or "..", at most
 * FAT32_PATH_DEPTH components and FAT32_PATH_MAX characters in total.  A name
 * that would need a long-file-name record is refused with FAT32_E_NAME rather
 * than mangled or approximated.
 */
struct fat32_path {
    char buf[FAT32_PATH_MAX + 1];
    char *comp[FAT32_PATH_DEPTH + 1];
    int count;                /* components, at least 1                        */
    u8 name[11];              /* the final component as 8.3 bytes              */
};

static int path_split(const char *path, struct fat32_path *out)
{
    if (!path || !path[0])
        return FAT32_E_NAME;
    u32 len = strlen(path);
    if (len > FAT32_PATH_MAX)
        return FAT32_E_NAME;                 /* refuse, never truncate */
    memcpy(out->buf, path, len + 1);
    if (out->buf[0] == '/' || out->buf[len - 1] == '/')
        return FAT32_E_NAME;

    out->count = 0;
    char *p = out->buf;
    for (;;) {
        if (out->count >= FAT32_PATH_DEPTH)
            return FAT32_E_NAME;             /* too deep */
        out->comp[out->count++] = p;
        while (*p && *p != '/')
            p++;
        if (!*p)
            break;
        *p++ = 0;
        if (!*p)
            return FAT32_E_NAME;             /* empty component: "A//B" or "A/" */
    }
    for (int i = 0; i < out->count; i++) {
        if (!strcmp(out->comp[i], ".") || !strcmp(out->comp[i], ".."))
            return FAT32_E_NAME;             /* no relative navigation */
        if (name_to_83(out->comp[i], out->name) != FAT32_OK)
            return FAT32_E_NAME;
    }
    return FAT32_OK;
}

/* Walk the directory components of `path`, leaving in *dir_cluster the
 * directory that holds the final component and in parts->name that component.
 * Every intermediate component must exist and must be a directory. */
static int path_parent(const struct fat32_io *io, const struct fat32_volume *vol,
                       const char *path, struct fat32_path *parts, u32 *dir_cluster)
{
    int rc = path_split(path, parts);
    if (rc != FAT32_OK)
        return rc;

    u32 cur = vol->root_cluster;
    for (int i = 0; i + 1 < parts->count; i++) {
        u8 want[11];
        if (name_to_83(parts->comp[i], want) != FAT32_OK)
            return FAT32_E_NAME;
        struct dir_scan s;
        memset(&s, 0, sizeof(s));
        s.want = want;
        rc = scan_dir(io, vol, cur, &s);
        if (rc != FAT32_OK)
            return rc;
        if (!s.found)
            return FAT32_E_NO_FILE;          /* the parent does not exist */
        if (!(s.attr & 0x10))
            return FAT32_E_NOT_DIR;          /* a file in the middle of a path */
        if (s.cluster < 2 || s.cluster >= vol->cluster_count + 2)
            return FAT32_E_CLUSTER;
        cur = s.cluster;
    }
    *dir_cluster = cur;
    return FAT32_OK;
}

int fat32_find(const struct fat32_io *io, const struct fat32_volume *vol,
               const char *path, u32 *cluster, u32 *size, u8 *attr)
{
    if (!io || !vol || vol->error != FAT32_OK)
        return vol ? vol->error : FAT32_E_IO;

    struct fat32_path parts;
    u32 dir = 0;
    int rc = path_parent(io, vol, path, &parts, &dir);
    if (rc != FAT32_OK)
        return rc;

    struct dir_scan s;
    memset(&s, 0, sizeof(s));
    s.want = parts.name;
    rc = scan_dir(io, vol, dir, &s);
    if (rc != FAT32_OK)
        return rc;
    if (!s.found)
        return FAT32_E_NO_FILE;
    if (cluster)
        *cluster = s.cluster;
    if (size)
        *size = s.size;
    if (attr)
        *attr = s.attr;
    return FAT32_OK;
}

/* List one directory: "" is the root, anything else must resolve to a
 * directory.  Entries are reported as stored; "." and ".." are skipped. */
int fat32_list_dir(const struct fat32_io *io, const struct fat32_volume *vol,
                   const char *path, struct fat32_entry *out, int max, int *count)
{
    if (!io || !vol || vol->error != FAT32_OK)
        return vol ? vol->error : FAT32_E_IO;

    u32 dir = vol->root_cluster;
    if (path && path[0]) {
        u32 cluster = 0, size = 0;
        u8 attr = 0;
        int rc = fat32_find(io, vol, path, &cluster, &size, &attr);
        if (rc != FAT32_OK)
            return rc;
        if (!(attr & 0x10))
            return FAT32_E_NOT_DIR;
        if (cluster < 2 || cluster >= vol->cluster_count + 2)
            return FAT32_E_CLUSTER;
        dir = cluster;
    }

    struct dir_scan s;
    memset(&s, 0, sizeof(s));
    s.list = out;
    s.max = max;
    int rc = scan_dir(io, vol, dir, &s);
    if (rc != FAT32_OK)
        return rc;
    if (count)
        *count = s.count;
    return FAT32_OK;
}

int fat32_list_root(const struct fat32_io *io, const struct fat32_volume *vol,
                    struct fat32_entry *out, int max, int *count)
{
    return fat32_list_dir(io, vol, "", out, max, count);
}

/* Floyd cycle detection over the first `limit` links of a chain.  A chain that
 * ends, or goes exactly as far as the file needs, is fine; a chain that meets
 * itself is corrupt and is refused before a byte is copied.  O(1) memory, so a
 * volume of any size costs the kernel nothing. */
static int chain_cycle(const struct fat32_io *io, const struct fat32_volume *vol,
                       u32 start, u32 limit)
{
    u32 slow = start, fast = start;
    for (u32 step = 0; step <= limit; step++) {
        u32 n;
        int rc = next_cluster(io, vol, fast, &n);
        if (rc == FAT32_EOC)
            return FAT32_OK;
        if (rc != FAT32_OK)
            return rc;
        fast = n;
        rc = next_cluster(io, vol, fast, &n);
        if (rc == FAT32_EOC)
            return FAT32_OK;
        if (rc != FAT32_OK)
            return rc;
        fast = n;
        rc = next_cluster(io, vol, slow, &n);
        if (rc != FAT32_OK)
            return rc;
        slow = n;
        if (fast == slow)
            return FAT32_E_LOOP;
    }
    return FAT32_E_LOOP;
}

int fat32_read_file(const struct fat32_io *io, const struct fat32_volume *vol,
                    const char *name, void *buf, u32 max, u32 *out_len)
{
    if (!io || !vol || !buf || !out_len)
        return FAT32_E_IO;
    u32 cluster = 0, size = 0;
    u8 attr = 0;
    int rc = fat32_find(io, vol, name, &cluster, &size, &attr);
    if (rc != FAT32_OK)
        return rc;
    if (attr & 0x10)
        return FAT32_E_IS_DIR;
    if (size > max)
        return FAT32_E_SPACE; /* explicit refusal, never a silent truncation */
    if (size == 0) {
        *out_len = 0;
        return FAT32_OK;
    }
    if (cluster < 2 || cluster >= vol->cluster_count + 2)
        return FAT32_E_CLUSTER;

    u32 cluster_bytes = vol->sectors_per_cluster * SEC;
    u32 needed = (size + cluster_bytes - 1) / cluster_bytes;
    if (needed > 1) {
        rc = chain_cycle(io, vol, cluster, needed);
        if (rc != FAT32_OK)
            return rc;
    }

    u8 *dst = (u8 *)buf;
    u32 left = size, done = 0, cur = cluster, visited = 0;

    while (left > 0) {
        if (++visited > vol->cluster_count)
            return FAT32_E_LOOP;
        for (u32 si = 0; si < vol->sectors_per_cluster && left > 0; si++) {
            u32 rel;
            rc = cluster_sector(vol, cur, si, &rel);
            if (rc != FAT32_OK)
                return rc;
            rc = read_rel(io, vol, rel, sec);
            if (rc != FAT32_OK)
                return rc;
            u32 n = left < SEC ? left : SEC;
            memcpy(dst + done, sec, n);
            done += n;
            left -= n;
        }
        if (left == 0)
            break;
        u32 next;
        rc = next_cluster(io, vol, cur, &next);
        if (rc == FAT32_EOC)
            return FAT32_E_CHAIN; /* size promises more than the chain holds */
        if (rc != FAT32_OK)
            return rc;
        cur = next;
    }
    *out_len = done;
    return FAT32_OK;
}

/* ---------------------------------------------- write paths (M1g, M1i) --- */
/*
 * Three operations can change a volume, and only these three:
 *
 *   fat32_create_file()    new entry + new chain (M1g; M1i adds paths and
 *                          directory growth)
 *   fat32_overwrite_file() replacement chain, same entry (M1i)
 *   fat32_create_dir()     new directory cluster, "." and ".." (M1i)
 *
 * Their shared discipline, in the order the code applies it:
 *
 *   1. plan: resolve the path, refuse unsupported types, count the free
 *      clusters the whole operation needs, allocate every cluster in memory;
 *   2. build: write content into clusters nobody can name yet, then link them
 *      in every FAT copy and prove the copies agree;
 *   3. flush;
 *   4. name: one directory-entry write, which is the single instant at which
 *      the operation becomes visible;
 *   5. flush, then read back through the sector source and compare;
 *   6. (overwrite only) free the replaced chain, and verify the mirrors again.
 *
 * What that ordering buys, stated exactly: a failure before step 4 leaves the
 * volume as it was plus, at worst, an allocated-but-unreferenced chain (a leak,
 * counted and reported); a failure after step 4 leaves the new file complete
 * and, for an overwrite, the replaced chain leaked.  What it does not buy: any
 * claim about power-loss atomicity.  There is no journal, no repair and no
 * second copy of anything.  See docs/TEST_RESULTS.md §15 and ADR-015.
 */

static u8 wsec[SEC];

static void wr16(u8 *p, u32 v)
{
    p[0] = (u8)(v & 0xff);
    p[1] = (u8)((v >> 8) & 0xff);
}

static void wr32(u8 *p, u32 v)
{
    p[0] = (u8)(v & 0xff);
    p[1] = (u8)((v >> 8) & 0xff);
    p[2] = (u8)((v >> 16) & 0xff);
    p[3] = (u8)((v >> 24) & 0xff);
}

/* Partition-relative sector write, refusing anything outside the partition. */
static int write_rel(const struct fat32_io *io, const struct fat32_volume *v,
                     u32 rel, const void *buf, struct fat32_write_report *rep)
{
    if (rel >= v->sectors)
        return FAT32_E_GEOMETRY;
    u32 lba = v->base_lba + rel;
    if (lba < v->base_lba) /* overflow */
        return FAT32_E_GEOMETRY;
    if (!io || !io->write)
        return FAT32_E_READ_ONLY;
    if (io->write(io->ctx, lba, 1, buf))
        return FAT32_E_IO;
    if (rep)
        rep->writes++;
    return FAT32_OK;
}

/* Set one FAT entry in *every* FAT copy, preserving the four reserved high bits
 * exactly as the volume had them.  Writing the copies one at a time is the one
 * place the mirror can be interrupted; the caller re-reads all of them
 * afterwards and reports the answer instead of assuming it. */
static int fat_write_entry(const struct fat32_io *io, const struct fat32_volume *v,
                           u32 cluster, u32 value, struct fat32_write_report *rep)
{
    if (cluster < 2 || cluster >= v->cluster_count + 2)
        return FAT32_E_CLUSTER;
    u32 byte = cluster * 4u;
    u32 sector = byte / v->bytes_per_sector;
    u32 inner = byte % v->bytes_per_sector;
    if (sector >= v->fat_sectors)
        return FAT32_E_GEOMETRY;

    for (u32 copy = 0; copy < v->fat_count; copy++) {
        u32 rel = v->reserved_sectors + copy * v->fat_sectors + sector;
        int rc = read_rel(io, v, rel, wsec);
        if (rc != FAT32_OK)
            return rc;
        u32 old = rd32(wsec + inner);
        u32 now = (old & ~FAT32_MASK) | (value & FAT32_MASK);
        if (now == old)
            continue;
        wr32(wsec + inner, now);
        rc = write_rel(io, v, rel, wsec, rep);
        if (rc != FAT32_OK)
            return rc;
    }
    return FAT32_OK;
}

/* One entry as one specific FAT copy holds it (used to prove the mirrors). */
static int fat_entry_in_copy(const struct fat32_io *io, const struct fat32_volume *v,
                             u32 copy, u32 cluster, u32 *value)
{
    if (cluster < 2 || cluster >= v->cluster_count + 2)
        return FAT32_E_CLUSTER;
    if (copy >= v->fat_count)
        return FAT32_E_GEOMETRY;
    u32 byte = cluster * 4u;
    u32 sector = byte / v->bytes_per_sector;
    u32 inner = byte % v->bytes_per_sector;
    if (sector >= v->fat_sectors)
        return FAT32_E_GEOMETRY;
    u32 rel = v->reserved_sectors + copy * v->fat_sectors + sector;
    int rc = read_rel(io, v, rel, sec);
    if (rc != FAT32_OK)
        return rc;
    *value = rd32(sec + inner) & FAT32_MASK;
    return FAT32_OK;
}

/* Count free clusters by reading the FAT, one sector at a time.
 * Cost: one sector read per (bytes_per_sector / 4) clusters — 512 sectors for
 * the 65,534-cluster fixture.  This is deliberately preferred over trusting
 * FSInfo, which is a hint written by whoever formatted the volume. */
static int fat_count_free(const struct fat32_io *io, const struct fat32_volume *v,
                          u32 *free_out)
{
    u32 per_sector = v->bytes_per_sector / 4u;
    u32 limit = v->cluster_count + 2u;
    u32 free_count = 0;

    for (u32 cluster = 2; cluster < limit;) {
        u32 sector_index = (cluster * 4u) / v->bytes_per_sector;
        if (sector_index >= v->fat_sectors)
            return FAT32_E_GEOMETRY;
        u32 rel = v->reserved_sectors + v->active_fat * v->fat_sectors + sector_index;
        int rc = read_rel(io, v, rel, sec);
        if (rc != FAT32_OK)
            return rc;
        u32 first_entry = sector_index * per_sector;
        for (u32 e = cluster - first_entry; e < per_sector && cluster < limit; e++, cluster++) {
            /* The root directory's own cluster is never free space, whatever a
             * damaged FAT claims: counting it would both misreport the free
             * count and offer the directory itself as an allocation target. */
            if (cluster == v->root_cluster)
                continue;
            if ((rd32(sec + e * 4u) & FAT32_MASK) == 0)
                free_count++;
        }
    }
    *free_out = free_count;
    return FAT32_OK;
}

/* Choose `count` free clusters, lowest first from `hint`, wrapping once.  The
 * chain lives in the caller's array until the whole allocation is known, so a
 * volume that turns out to be full leaves nothing half-allocated. */
static int fat_alloc_chain(const struct fat32_io *io, const struct fat32_volume *v,
                           u32 count, u32 *chain, u32 hint)
{
    if (count == 0)
        return FAT32_OK;
    if (count > FAT32_MAX_CHAIN)
        return FAT32_E_TOO_BIG;

    u32 start = (hint >= 2 && hint < v->cluster_count + 2) ? hint : 2;
    u32 found = 0;

    for (int pass = 0; pass < 2 && found < count; pass++) {
        u32 from = pass ? 2 : start;
        u32 to = pass ? start : v->cluster_count + 2;
        for (u32 c = from; c < to && found < count; c++) {
            if (c == v->root_cluster)
                continue;      /* never hand out the directory's own cluster */
            u32 val;
            int rc = fat_next_raw(io, v, c, &val);
            if (rc != FAT32_OK)
                return rc;
            if (val != 0)      /* allocated, bad, reserved or end-of-chain */
                continue;
            chain[found++] = c;
        }
    }
    return found == count ? FAT32_OK : FAT32_E_FULL;
}

/* A directory entry, filled in with the fixed timestamp M1g established (the
 * kernel has no clock; a constant keeps two builds of the same write identical). */
static void dir_entry_fill(u8 *ent, const u8 name[11], u8 attr, u32 first, u32 size)
{
    memset(ent, 0, 32);
    memcpy(ent, name, 11);
    ent[11] = attr;
    wr16(ent + 14, 0x6000);            /* creation time 12:00:00 */
    wr16(ent + 16, 0x5d6f);            /* creation date 2026-11-15 */
    wr16(ent + 18, 0x5d6f);            /* access date */
    wr16(ent + 20, (u16)(first >> 16));
    wr16(ent + 22, 0x6000);            /* write time */
    wr16(ent + 24, 0x5d6f);            /* write date */
    wr16(ent + 26, (u16)(first & 0xffff));
    wr32(ent + 28, size);
}

/* What a directory scan found.  `exists` is the duplicate check; otherwise
 * sector/offset name the first free slot when there is one. */
struct dir_hit {
    int exists;
    int free_slot;
    u32 sector;
    u32 offset;
    u32 first_cluster;
    u32 size;
    u8 attr;
    u32 last_cluster;      /* last cluster of the directory's own chain */
};

/*
 * Scan one directory for `want` and for the first usable free slot.
 *
 * A slot is usable when it is 0x00 (end of directory) or 0xE5 (deleted) and
 * does not sit directly behind a long-name record, whose orphaned fragments
 * would otherwise attach to the new name.  Where the chain ends without a 0x00,
 * the directory has no free slot and the caller decides whether it may grow.
 */
static int dir_scan(const struct fat32_io *io, const struct fat32_volume *v,
                    u32 dir_cluster, const u8 want[11], struct dir_hit *hit)
{
    memset(hit, 0, sizeof(*hit));
    if (dir_cluster < 2 || dir_cluster >= v->cluster_count + 2)
        return FAT32_E_CLUSTER;

    u32 cluster = dir_cluster;
    u32 visited = 0;
    for (;;) {
        if (++visited > v->cluster_count)
            return FAT32_E_LOOP;
        hit->last_cluster = cluster;
        for (u32 si = 0; si < v->sectors_per_cluster; si++) {
            u32 rel;
            int rc = cluster_sector(v, cluster, si, &rel);
            if (rc != FAT32_OK)
                return rc;
            rc = read_rel(io, v, rel, sec);
            if (rc != FAT32_OK)
                return rc;
            for (u32 e = 0; e < SEC / 32u; e++) {
                const u8 *ent = sec + e * 32u;
                if (ent[0] == 0x00) {
                    if (!hit->free_slot) {
                        hit->sector = rel;
                        hit->offset = e * 32u;
                        hit->free_slot = 1;
                    }
                    return FAT32_OK;             /* end of directory */
                }
                if (ent[0] == 0xe5) {
                    int prev_lfn = e > 0 && sec[(e - 1) * 32u + 11] == 0x0f;
                    if (!hit->free_slot && !prev_lfn) {
                        hit->sector = rel;
                        hit->offset = e * 32u;
                        hit->free_slot = 1;
                    }
                    continue;
                }
                u8 attr = ent[11];
                if (attr == 0x0f || (attr & 0x08))
                    continue;
                if (memcmp(ent, want, 11) == 0) {
                    hit->exists = 1;
                    hit->sector = rel;
                    hit->offset = e * 32u;
                    hit->attr = attr;
                    hit->size = rd32(ent + 28);
                    hit->first_cluster = ((u32)rd16(ent + 20) << 16) | rd16(ent + 26);
                    return FAT32_OK;
                }
            }
        }
        u32 next;
        int rc = next_cluster(io, v, cluster, &next);
        if (rc == FAT32_EOC)
            return FAT32_OK;                     /* no free slot in this chain */
        if (rc != FAT32_OK)
            return rc;
        cluster = next;
    }
}

/* FSInfo: read it, and believe it only if its three signatures are intact and
 * its counters are inside the volume.  A volume can be perfectly healthy with a
 * nonsense FSInfo; the answer here is "do not trust it", not "refuse to work". */
static int fsinfo_read(const struct fat32_io *io, const struct fat32_volume *v,
                       u32 *free_count, u32 *next_free, int *valid)
{
    *valid = 0;
    *free_count = 0;
    *next_free = 0xffffffffu;
    if (v->fsinfo_sector == 0)
        return FAT32_OK;
    if (v->fsinfo_sector >= v->reserved_sectors && v->fsinfo_sector != 0)
        return FAT32_OK;
    int rc = read_rel(io, v, v->fsinfo_sector, sec);
    if (rc != FAT32_OK)
        return rc;
    if (rd32(sec) != 0x41615252u || rd32(sec + 484) != 0x61417272u ||
        rd32(sec + 508) != 0xaa550000u)
        return FAT32_OK;
    u32 claimed = rd32(sec + 488);
    u32 hint = rd32(sec + 492);
    if (claimed > v->cluster_count)
        return FAT32_OK;
    if (hint != 0xffffffffu && (hint < 2 || hint >= v->cluster_count + 2))
        return FAT32_OK;
    *free_count = claimed;
    *next_free = hint;
    *valid = 1;
    return FAT32_OK;
}

/* Rewrite the free count and hint of one FSInfo block, leaving any block whose
 * signatures are broken alone (it is not an FSInfo block we can trust). */
static int fsinfo_write_block(const struct fat32_io *io, const struct fat32_volume *v,
                              u32 sector, u32 free_count, u32 next_free,
                              struct fat32_write_report *rep)
{
    if (sector == 0 || sector >= v->reserved_sectors)
        return sector == 0 ? FAT32_OK : FAT32_E_GEOMETRY;
    int rc = read_rel(io, v, sector, wsec);
    if (rc != FAT32_OK)
        return rc;
    if (rd32(wsec) != 0x41615252u || rd32(wsec + 484) != 0x61417272u ||
        rd32(wsec + 508) != 0xaa550000u)
        return FAT32_OK;
    if (rd32(wsec + 488) == free_count && rd32(wsec + 492) == next_free)
        return FAT32_OK;
    wr32(wsec + 488, free_count);
    wr32(wsec + 492, next_free);
    return write_rel(io, v, sector, wsec, rep);
}

/* The lowest free cluster at or after `start` (wrapping to 2), or 0xffffffff. */
static int fat_next_free_hint(const struct fat32_io *io, const struct fat32_volume *v,
                              u32 start, u32 *hint)
{
    u32 limit = v->cluster_count + 2;
    for (int pass = 0; pass < 2; pass++) {
        u32 from = pass ? 2 : (start < 2 ? 2 : start);
        u32 to = pass ? (start < 2 ? 2 : start) : limit;
        for (u32 c = from; c < to; c++) {
            u32 val;
            int rc = fat_next_raw(io, v, c, &val);
            if (rc != FAT32_OK)
                return rc;
            if (val == 0) {
                *hint = c;
                return FAT32_OK;
            }
        }
    }
    *hint = 0xffffffffu;
    return FAT32_OK;
}

/* Write the free-cluster counters, counting the region honestly: only a block
 * whose signatures and counters were trusted is written, and a failure is
 * reported rather than swallowed. */
static int fsinfo_apply(const struct fat32_io *io, const struct fat32_volume *v,
                        int valid, u32 free_after, u32 hint_from,
                        struct fat32_write_report *rep)
{
    rep->free_after = free_after;
    if (!valid)
        return FAT32_OK;
    u32 hint = 0xffffffffu;
    int rc = fat_next_free_hint(io, v, hint_from, &hint);
    if (rc != FAT32_OK)
        return rc;
    rc = fsinfo_write_block(io, v, v->fsinfo_sector, free_after, hint, rep);
    if (rc != FAT32_OK) {
        rep->fsinfo_updated = -1;
        return rc;
    }
    if (v->backup_boot_sector != 0 && v->backup_boot_sector + 1 < v->reserved_sectors) {
        rc = fsinfo_write_block(io, v, v->backup_boot_sector + 1, free_after, hint, rep);
        if (rc != FAT32_OK) {
            rep->fsinfo_updated = -1;
            return rc;
        }
    }
    rep->fsinfo_updated = 1;
    return FAT32_OK;
}

/* Barrier.  A source without a flush callback is reported as such, never
 * assumed to have flushed. */
static int flush_now(const struct fat32_io *io, struct fat32_write_report *rep)
{
    if (!io->flush)
        return FAT32_OK;
    if (io->flush(io->ctx) != 0) {
        rep->flush_state = -1;
        return FAT32_E_FLUSH;
    }
    rep->flush_state = 1;
    return FAT32_OK;
}

/* Zero every sector of one cluster (used for new directories and for a
 * directory cluster added by growth, so no stale entry can appear in it). */
static int zero_cluster(const struct fat32_io *io, const struct fat32_volume *v,
                        u32 cluster, struct fat32_write_report *rep)
{
    memset(wsec, 0, sizeof(wsec));
    for (u32 si = 0; si < v->sectors_per_cluster; si++) {
        u32 rel;
        int rc = cluster_sector(v, cluster, si, &rel);
        if (rc != FAT32_OK)
            return rc;
        rc = write_rel(io, v, rel, wsec, rep);
        if (rc != FAT32_OK)
            return rc;
    }
    return FAT32_OK;
}

/* Write `size` bytes of `src` into `cluster`, starting at byte `offset` inside
 * the cluster.  Every sector of the cluster is written: the bytes outside the
 * slice are zeroed, so a replacement never leaves bytes of the previous
 * occupant behind the end of a short file.  Callers pass whole-cluster slices
 * in practice; the general form keeps the function honest. */
static int write_into_cluster(const struct fat32_io *io, const struct fat32_volume *v,
                              u32 cluster, const u8 *src, u32 offset, u32 size,
                              struct fat32_write_report *rep)
{
    u32 cluster_bytes = v->sectors_per_cluster * SEC;
    if (offset > cluster_bytes || size > cluster_bytes - offset)
        return FAT32_E_GEOMETRY;
    for (u32 si = 0; si < v->sectors_per_cluster; si++) {
        u32 sec_start = si * SEC;
        u32 sec_end = sec_start + SEC;
        u32 lo = offset > sec_start ? offset : sec_start;
        u32 hi = (offset + size) < sec_end ? (offset + size) : sec_end;
        memset(wsec, 0, sizeof(wsec));
        if (lo < hi)
            memcpy(wsec + (lo - sec_start), src + (lo - offset), hi - lo);
        u32 rel;
        int rc = cluster_sector(v, cluster, si, &rel);
        if (rc != FAT32_OK)
            return rc;
        rc = write_rel(io, v, rel, wsec, rep);
        if (rc != FAT32_OK)
            return rc;
    }
    return FAT32_OK;
}

/* Follow a chain to its end, counting the clusters.  Any anomaly is refused:
 * a chain with a free, bad, reserved or out-of-range link, or one that never
 * ends, is not something this code will free or measure. */
static int chain_walk(const struct fat32_io *io, const struct fat32_volume *v,
                      u32 first, u32 *count)
{
    *count = 0;
    if (first == 0)
        return FAT32_OK;                      /* an empty file has no chain */
    if (first < 2 || first >= v->cluster_count + 2)
        return FAT32_E_CLUSTER;

    u32 cur = first, walked = 0;
    for (;;) {
        u32 next;
        int rc = next_cluster(io, v, cur, &next);
        if (rc == FAT32_EOC) {
            *count = walked + 1;
            return FAT32_OK;
        }
        if (rc != FAT32_OK)
            return rc;
        if (++walked >= v->cluster_count)
            return FAT32_E_LOOP;
        cur = next;
    }
}

/* Return every cluster of a chain to free space, in every FAT copy.
 *
 * `first` must already have been validated by chain_walk(): this function
 * reads each link *before* clearing it, so a damaged chain cannot send it
 * anywhere.  A failure part-way leaves the remaining clusters allocated and
 * unreferenced — a leak, which the caller reports with the count reached. */
static int chain_free(const struct fat32_io *io, const struct fat32_volume *v,
                      u32 first, u32 *freed, struct fat32_write_report *rep)
{
    *freed = 0;
    if (first == 0)
        return FAT32_OK;

    u32 cur = first, walked = 0;
    for (;;) {
        if (++walked > v->cluster_count)
            return FAT32_E_LOOP;
        u32 next;
        int rc = fat_next_raw(io, v, cur, &next);
        if (rc != FAT32_OK)
            return rc;
        if (next == FAT32_BAD)
            return FAT32_E_BAD;
        if (next >= FAT32_RESERVED_MIN && next < FAT32_EOC_MIN)
            return FAT32_E_RESERVED;
        int end = next >= FAT32_EOC_MIN;
        if (!end && (next < 2 || next >= v->cluster_count + 2))
            return FAT32_E_CLUSTER;
        rc = fat_write_entry(io, v, cur, 0, rep);
        if (rc != FAT32_OK)
            return rc;
        (*freed)++;
        if (end)
            return FAT32_OK;
        cur = next;
    }
}

/* Prove every FAT copy holds `value` for `cluster`. */
static int mirrors_hold(const struct fat32_io *io, const struct fat32_volume *v,
                        u32 cluster, u32 value)
{
    for (u32 copy = 0; copy < v->fat_count; copy++) {
        u32 got = 0;
        int rc = fat_entry_in_copy(io, v, copy, cluster, &got);
        if (rc != FAT32_OK)
            return rc;
        if (got != value)
            return FAT32_E_CORRUPT;
    }
    return FAT32_OK;
}

/* Prove the mirrors agree for every cluster of a freshly written chain. */
static int mirrors_hold_chain(const struct fat32_io *io, const struct fat32_volume *v,
                              const u32 *chain, u32 count)
{
    for (u32 i = 0; i < count; i++) {
        u32 want_value = (i + 1 < count) ? chain[i + 1] : 0x0fffffffu;
        int rc = mirrors_hold(io, v, chain[i], want_value);
        if (rc != FAT32_OK)
            return rc;
    }
    return FAT32_OK;
}

/* Byte-for-byte comparison of what the volume now returns with what was
 * written.  This is the kernel's own read-back: it goes back through the sector
 * source, not through the buffer that was just used. */
static int verify_created(const struct fat32_io *io, const struct fat32_volume *v,
                          u32 first, const void *data, u32 size)
{
    if (size == 0)
        return FAT32_OK;
    if (first < 2 || first >= v->cluster_count + 2)
        return FAT32_E_CLUSTER;
    const u8 *src = (const u8 *)data;
    u32 cur = first, done = 0, visited = 0;

    while (done < size) {
        if (++visited > v->cluster_count)
            return FAT32_E_LOOP;
        for (u32 si = 0; si < v->sectors_per_cluster && done < size; si++) {
            u32 rel;
            int rc = cluster_sector(v, cur, si, &rel);
            if (rc != FAT32_OK)
                return rc;
            rc = read_rel(io, v, rel, sec);
            if (rc != FAT32_OK)
                return rc;
            u32 n = size - done < SEC ? size - done : SEC;
            if (memcmp(sec, src + done, n) != 0)
                return FAT32_E_VERIFY;
            done += n;
        }
        if (done >= size)
            break;
        u32 next;
        int rc = next_cluster(io, v, cur, &next);
        if (rc == FAT32_EOC)
            return FAT32_E_CHAIN;
        if (rc != FAT32_OK)
            return rc;
        cur = next;
    }
    return FAT32_OK;
}

/* Read the file back *through its name*: the entry must exist, must name the
 * size it was given, must start at the cluster that was allocated, and must
 * return the bytes that were written.  A stale entry or a reversed switch would
 * fail here even though the clusters themselves are fine. */
static int verify_named_file(const struct fat32_io *io, const struct fat32_volume *v,
                             const char *path, const void *data, u32 size, u32 first)
{
    u32 cluster = 0, got_size = 0;
    u8 attr = 0;
    int rc = fat32_find(io, v, path, &cluster, &got_size, &attr);
    if (rc != FAT32_OK)
        return rc;
    if (attr & 0x10)
        return FAT32_E_IS_DIR;
    if (got_size != size)
        return FAT32_E_VERIFY;
    if (size && cluster != first)
        return FAT32_E_VERIFY;
    return verify_created(io, v, cluster, data, size);
}

/* Grow one directory by the single cluster `new_cluster`, which the caller has
 * already reserved in its own allocation.  Passing the cluster in (rather than
 * allocating here) is what makes it impossible for the growth to pick a cluster
 * the caller had reserved but not yet linked: the FAT still shows those as free
 * until the content is written, so a second scan of the FAT could hand the same
 * cluster out twice.
 *
 * Order matters: the new cluster is given its end-of-chain marker *before* it
 * is linked from the old last cluster, so an interruption can only leave an
 * allocated, unreferenced cluster — never a directory whose chain passes
 * through a free cluster.  Existing entries are untouched: the new cluster is
 * zeroed and only appended. */
static int dir_grow(const struct fat32_io *io, const struct fat32_volume *v,
                    u32 dir_cluster, u32 new_cluster, struct fat32_write_report *rep,
                    u32 *grown)
{
    u32 count = 0;
    int rc = chain_walk(io, v, dir_cluster, &count);
    if (rc != FAT32_OK)
        return rc;

    /* last cluster of the directory chain */
    u32 last = dir_cluster;
    for (u32 i = 1; i < count; i++) {
        u32 next;
        rc = next_cluster(io, v, last, &next);
        if (rc != FAT32_OK)
            return rc;
        last = next;
    }

    if (new_cluster < 2 || new_cluster >= v->cluster_count + 2 ||
        new_cluster == dir_cluster)
        return FAT32_E_CLUSTER;

    rc = zero_cluster(io, v, new_cluster, rep);
    if (rc != FAT32_OK)
        return rc;
    rc = fat_write_entry(io, v, new_cluster, 0x0fffffffu, rep);
    if (rc != FAT32_OK)
        return rc;
    rc = mirrors_hold(io, v, new_cluster, 0x0fffffffu);
    if (rc != FAT32_OK)
        return FAT32_E_CORRUPT;
    rc = fat_write_entry(io, v, last, new_cluster, rep);
    if (rc != FAT32_OK)
        return rc;
    rc = mirrors_hold(io, v, last, new_cluster);
    if (rc != FAT32_OK)
        return FAT32_E_CORRUPT;

    rep->grew = 1;
    rep->grown_cluster = new_cluster;
    *grown = new_cluster;
    return FAT32_OK;
}

/* Fill the first sector of a new directory with "." and "..". */
static int dir_init_entries(const struct fat32_io *io, const struct fat32_volume *v,
                            u32 dir_cluster, u32 parent_cluster,
                            struct fat32_write_report *rep)
{
    u32 rel;
    int rc = cluster_sector(v, dir_cluster, 0, &rel);
    if (rc != FAT32_OK)
        return rc;
    rc = read_rel(io, v, rel, wsec);
    if (rc != FAT32_OK)
        return rc;

    u8 dot[11] = { '.', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ' };
    u8 dotdot[11] = { '.', '.', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ' };
    /* ".." points at the parent directory, or at cluster 0 when the parent is
     * the root: both conventions exist in the wild and 0 is what Windows,
     * Linux and this code read correctly. */
    u32 up = (parent_cluster == v->root_cluster) ? 0 : parent_cluster;
    dir_entry_fill(wsec + 0, dot, 0x10, dir_cluster, 0);
    dir_entry_fill(wsec + 32, dotdot, 0x10, up, 0);
    return write_rel(io, v, rel, wsec, rep);
}

/* Prove a new directory is what it claims: "." names itself, ".." names its
 * parent, and the parent's own entry names the directory. */
static int verify_dir(const struct fat32_io *io, const struct fat32_volume *v,
                      u32 dir_cluster, u32 parent_cluster, const u8 name[11],
                      u32 parent_sector, u32 parent_offset)
{
    u32 rel;
    int rc = cluster_sector(v, dir_cluster, 0, &rel);
    if (rc != FAT32_OK)
        return rc;
    rc = read_rel(io, v, rel, sec);
    if (rc != FAT32_OK)
        return rc;
    static const u8 dot[11] = { '.', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ' };
    static const u8 dotdot[11] = { '.', '.', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ' };
    if (memcmp(sec, dot, 11) != 0 || !(sec[11] & 0x10))
        return FAT32_E_VERIFY;
    u32 self = ((u32)rd16(sec + 20) << 16) | rd16(sec + 26);
    if (self != dir_cluster)
        return FAT32_E_VERIFY;
    if (memcmp(sec + 32, dotdot, 11) != 0 || !(sec[43] & 0x10))
        return FAT32_E_VERIFY;
    u32 up = ((u32)rd16(sec + 52) << 16) | rd16(sec + 58);
    u32 want_up = (parent_cluster == v->root_cluster) ? 0 : parent_cluster;
    if (up != want_up)
        return FAT32_E_VERIFY;

    rc = read_rel(io, v, parent_sector, sec);
    if (rc != FAT32_OK)
        return rc;
    const u8 *ent = sec + parent_offset;
    if (memcmp(ent, name, 11) != 0 || !(ent[11] & 0x10))
        return FAT32_E_VERIFY;
    u32 named = ((u32)rd16(ent + 20) << 16) | rd16(ent + 26);
    if (named != dir_cluster || rd32(ent + 28) != 0)
        return FAT32_E_VERIFY;
    return FAT32_OK;
}

/* ---------------------------------------------------------- create (M1g) */

/* Refuse, rather than guess, whenever the volume or the requested path is not
 * something these operations can do safely. */
int fat32_create_file(const struct fat32_io *io, struct fat32_volume *vol,
                      const char *path, const void *data, u32 size,
                      struct fat32_write_report *rep)
{
    struct fat32_write_report local;
    if (!rep)
        rep = &local;
    memset(rep, 0, sizeof(*rep));
    rep->stage = FAT32_STAGE_NONE;

    if (!io || !io->read || !vol || vol->error != FAT32_OK)
        return FAT32_E_IO;
    if (!data && size)
        return FAT32_E_IO;
    if (!io->write)
        return FAT32_E_READ_ONLY;      /* a read-only source stays read-only */
    if (vol->bytes_per_sector != SEC || vol->sectors_per_cluster == 0)
        return FAT32_E_BPB;

    rep->stage = FAT32_STAGE_CHECK;

    u32 dir = 0;
    struct fat32_path parts;
    int rc = path_parent(io, vol, path, &parts, &dir);
    if (rc != FAT32_OK)
        return rc;                     /* name / no-file / not-dir */

    u32 cluster_bytes = vol->sectors_per_cluster * SEC;
    if (size > (u32)FAT32_MAX_CHAIN * cluster_bytes)
        return FAT32_E_TOO_BIG;        /* no partial writes, no silent truncation */
    u32 needed = size ? (size + cluster_bytes - 1) / cluster_bytes : 0;

    struct dir_hit hit;
    rc = dir_scan(io, vol, dir, parts.name, &hit);
    if (rc != FAT32_OK)
        return rc;
    if (hit.exists)
        return FAT32_E_EXISTS;         /* never overwrite: use fat32_overwrite_file */

    u32 free_before = 0;
    rc = fat_count_free(io, vol, &free_before);
    if (rc != FAT32_OK)
        return rc;

    /* A directory with no free slot needs one more cluster for the entry. */
    int grow = !hit.free_slot;
    u32 demand = needed + (grow ? 1u : 0u);
    if (free_before < demand)
        return FAT32_E_FULL;           /* VOLUME FULL: nothing was written */

    u32 fsinfo_claimed = 0, next_hint = 0xffffffffu;
    int fsinfo_valid = 0;
    rc = fsinfo_read(io, vol, &fsinfo_claimed, &next_hint, &fsinfo_valid);
    if (rc != FAT32_OK)
        return rc;

    /* One allocation for everything the operation needs, including the cluster
     * a full directory would grow into: two separate scans of the FAT could
     * choose the same still-unlinked cluster twice. */
    u32 chain[FAT32_MAX_CHAIN + 1u];
    rc = fat_alloc_chain(io, vol, demand, chain, next_hint);
    if (rc != FAT32_OK)
        return rc;

    rep->clusters = needed;
    rep->bytes = size;
    rep->first_cluster = needed ? chain[0] : 0;
    rep->free_before = free_before;
    rep->fsinfo_claimed = fsinfo_claimed;
    rep->fsinfo_valid = fsinfo_valid;
    rep->stage = FAT32_STAGE_ALLOCATED;

    /* 0. if the directory is full, give it room before anything is named. */
    if (grow) {
        rep->stage = FAT32_STAGE_DIR_GROW;
        u32 grown = 0;
        rc = dir_grow(io, vol, dir, chain[needed], rep, &grown);
        if (rc != FAT32_OK)
            return rc;
        u32 rel;
        rc = cluster_sector(vol, grown, 0, &rel);
        if (rc != FAT32_OK)
            return rc;
        hit.sector = rel;
        hit.offset = 0;
        hit.free_slot = 1;
    }

    /* 1. content first, into clusters nobody references yet. */
    rep->stage = FAT32_STAGE_DATA;
    const u8 *src = (const u8 *)data;
    for (u32 i = 0; i < needed; i++) {
        u32 off = i * cluster_bytes;
        u32 n = size - off < cluster_bytes ? size - off : cluster_bytes;
        rc = write_into_cluster(io, vol, chain[i], src + off, 0, n, rep);
        if (rc != FAT32_OK)
            return rc;
    }

    /* 2. then the chain itself, in every FAT copy. */
    rep->stage = FAT32_STAGE_FAT;
    for (u32 i = 0; i < needed; i++) {
        u32 value = (i + 1 < needed) ? chain[i + 1] : 0x0fffffffu;
        rc = fat_write_entry(io, vol, chain[i], value, rep);
        if (rc != FAT32_OK)
            return rc;
    }

    /* 3. prove the copies agree, in every copy, entry by entry. */
    rep->mirrors_equal = 1;
    rc = mirrors_hold_chain(io, vol, chain, needed);
    if (rc != FAT32_OK) {
        rep->mirrors_equal = 0;
        return rc == FAT32_E_VERIFY ? FAT32_E_CORRUPT : rc;
    }

    /* 4. FSInfo: recomputed from the FAT, written only where it is trustworthy.
     * Doing it before the name exists means an interruption over-reports usage
     * (safe) rather than under-reporting free space (dangerous). */
    rep->stage = FAT32_STAGE_FSINFO;
    rc = fsinfo_apply(io, vol, fsinfo_valid, free_before - demand,
                      demand ? chain[demand - 1] + 1 : 2, rep);
    if (rc != FAT32_OK)
        return rc;

    /* 5. barrier before the new name is visible. */
    rep->stage = FAT32_STAGE_FLUSH;
    rc = flush_now(io, rep);
    if (rc != FAT32_OK)
        return rc;

    /* 6. the entry that makes the file exist. */
    rep->stage = FAT32_STAGE_DIR;
    rc = read_rel(io, vol, hit.sector, wsec);
    if (rc != FAT32_OK)
        return rc;
    u8 *ent = wsec + hit.offset;
    if (ent[0] != 0x00 && ent[0] != 0xe5)
        return FAT32_E_CORRUPT;        /* somebody wrote the slot under us */
    dir_entry_fill(ent, parts.name, 0x20, rep->first_cluster, size);
    rep->dir_sector = hit.sector;
    rep->dir_offset = hit.offset;
    rc = write_rel(io, vol, hit.sector, wsec, rep);
    if (rc != FAT32_OK)
        return rc;

    /* 7. flush again: the name is now on the medium, or the caller is told it
     * is not. */
    rc = flush_now(io, rep);
    if (rc != FAT32_OK)
        return rc;

    /* 8. read the file back through its own name and compare. */
    rc = verify_named_file(io, vol, path, data, size, rep->first_cluster);
    if (rc != FAT32_OK)
        return rc;
    rep->verified = 1;
    rep->stage = FAT32_STAGE_DONE;
    return FAT32_OK;
}

/* ------------------------------------------------------- overwrite (M1i) */

/*
 * Replace an existing file's content without touching its name.
 *
 * The failure model, stated before the code: the new content is built in
 * clusters that nothing references, the entry is switched with one sector
 * write, and only then is the old chain freed.  So at every instant the
 * volume's state is one of:
 *
 *   before the switch   old file complete; the new chain leaked (if any);
 *   after the switch    new file complete; the old chain leaked (until freed);
 *   mid-free            new file complete; part of the old chain leaked.
 *
 * There is no state in which a name refers to unwritten data, and no state in
 * which the old and new chains share a cluster.  The cost is transient space
 * for both versions, which is why a volume that cannot afford it is refused
 * before anything is written.  This is not a transaction: a power cut between
 * the switch and the free leaves clusters allocated to nothing, which is a leak
 * (reported here, repairable with fsck) and not a corruption.
 */
int fat32_overwrite_file(const struct fat32_io *io, struct fat32_volume *vol,
                         const char *path, const void *data, u32 size,
                         struct fat32_write_report *rep)
{
    struct fat32_write_report local;
    if (!rep)
        rep = &local;
    memset(rep, 0, sizeof(*rep));
    rep->stage = FAT32_STAGE_NONE;

    if (!io || !io->read || !vol || vol->error != FAT32_OK)
        return FAT32_E_IO;
    if (!data && size)
        return FAT32_E_IO;
    if (!io->write)
        return FAT32_E_READ_ONLY;
    if (vol->bytes_per_sector != SEC || vol->sectors_per_cluster == 0)
        return FAT32_E_BPB;

    rep->stage = FAT32_STAGE_CHECK;

    u32 dir = 0;
    struct fat32_path parts;
    int rc = path_parent(io, vol, path, &parts, &dir);
    if (rc != FAT32_OK)
        return rc;

    struct dir_hit hit;
    rc = dir_scan(io, vol, dir, parts.name, &hit);
    if (rc != FAT32_OK)
        return rc;
    if (!hit.exists)
        return FAT32_E_NO_FILE;
    if (hit.attr & 0x10)
        return FAT32_E_IS_DIR;         /* a directory is not a file */
    if (hit.attr & 0x01)
        return FAT32_E_READ_ONLY;      /* the entry itself says read-only */
    if (hit.attr & 0x08)
        return FAT32_E_CORRUPT;        /* a volume label is not a file */

    u32 cluster_bytes = vol->sectors_per_cluster * SEC;
    if (size > (u32)FAT32_MAX_CHAIN * cluster_bytes)
        return FAT32_E_TOO_BIG;
    u32 needed = size ? (size + cluster_bytes - 1) / cluster_bytes : 0;

    /* The chain being replaced must be coherent before anything may be freed. */
    u32 old_clusters = 0;
    if (hit.size > 0) {
        if (hit.first_cluster < 2)
            return FAT32_E_CORRUPT;    /* a non-empty file without a chain */
        rc = chain_walk(io, vol, hit.first_cluster, &old_clusters);
        if (rc != FAT32_OK)
            return rc;
    } else if (hit.first_cluster >= 2) {
        /* An empty file that owns a chain: ambiguous, so refuse and say why
         * rather than silently keeping or freeing it. */
        return FAT32_E_CORRUPT;
    }

    u32 free_before = 0;
    rc = fat_count_free(io, vol, &free_before);
    if (rc != FAT32_OK)
        return rc;
    if (free_before < needed)
        return FAT32_E_FULL;           /* no room for both versions: refuse */

    u32 fsinfo_claimed = 0, next_hint = 0xffffffffu;
    int fsinfo_valid = 0;
    rc = fsinfo_read(io, vol, &fsinfo_claimed, &next_hint, &fsinfo_valid);
    if (rc != FAT32_OK)
        return rc;

    u32 chain[FAT32_MAX_CHAIN];
    rc = fat_alloc_chain(io, vol, needed, chain, next_hint);
    if (rc != FAT32_OK)
        return rc;

    rep->clusters = needed;
    rep->bytes = size;
    rep->first_cluster = needed ? chain[0] : 0;
    rep->old_first_cluster = hit.first_cluster;
    rep->old_clusters = old_clusters;
    rep->free_before = free_before;
    rep->fsinfo_claimed = fsinfo_claimed;
    rep->fsinfo_valid = fsinfo_valid;
    rep->dir_sector = hit.sector;
    rep->dir_offset = hit.offset;
    rep->stage = FAT32_STAGE_ALLOCATED;

    /* 1. content into the new clusters. */
    rep->stage = FAT32_STAGE_DATA;
    const u8 *src = (const u8 *)data;
    for (u32 i = 0; i < needed; i++) {
        u32 off = i * cluster_bytes;
        u32 n = size - off < cluster_bytes ? size - off : cluster_bytes;
        rc = write_into_cluster(io, vol, chain[i], src + off, 0, n, rep);
        if (rc != FAT32_OK)
            return rc;
    }

    /* 2. link the new chain in every FAT copy and prove the mirrors. */
    rep->stage = FAT32_STAGE_FAT;
    for (u32 i = 0; i < needed; i++) {
        u32 value = (i + 1 < needed) ? chain[i + 1] : 0x0fffffffu;
        rc = fat_write_entry(io, vol, chain[i], value, rep);
        if (rc != FAT32_OK)
            return rc;
    }
    rep->mirrors_equal = 1;
    rc = mirrors_hold_chain(io, vol, chain, needed);
    if (rc != FAT32_OK) {
        rep->mirrors_equal = 0;
        return rc == FAT32_E_VERIFY ? FAT32_E_CORRUPT : rc;
    }

    /* 3. usage counters: both chains are allocated at this instant, so the
     * count over-reports usage if the operation stops here (safe direction). */
    rep->stage = FAT32_STAGE_FSINFO;
    rc = fsinfo_apply(io, vol, fsinfo_valid, free_before - needed,
                      needed ? chain[needed - 1] + 1 : 2, rep);
    if (rc != FAT32_OK)
        return rc;

    /* 4. barrier, then the single write that switches the file over. */
    rep->stage = FAT32_STAGE_FLUSH;
    rc = flush_now(io, rep);
    if (rc != FAT32_OK)
        return rc;

    rep->stage = FAT32_STAGE_DIR;
    rc = read_rel(io, vol, hit.sector, wsec);
    if (rc != FAT32_OK)
        return rc;
    u8 *ent = wsec + hit.offset;
    if (memcmp(ent, parts.name, 11) != 0)
        return FAT32_E_CORRUPT;        /* the entry moved under us */
    /* keep the name and the attributes; change only the chain and the size */
    wr16(ent + 20, (u16)(rep->first_cluster >> 16));
    wr16(ent + 26, (u16)(rep->first_cluster & 0xffff));
    wr32(ent + 28, size);
    wr16(ent + 22, 0x6000);
    wr16(ent + 24, 0x5d6f);
    rc = write_rel(io, vol, hit.sector, wsec, rep);
    if (rc != FAT32_OK)
        return rc;

    rc = flush_now(io, rep);
    if (rc != FAT32_OK)
        return rc;

    /* 5. only now may the replaced chain go back to free space.  A file that
     * had no chain (an empty file receiving its first content) has nothing to
     * free, and asking the FAT about cluster 0 is not a question worth asking. */
    rep->stage = FAT32_STAGE_OLD_FAT;
    if (old_clusters) {
        rc = chain_free(io, vol, hit.first_cluster, &rep->freed, rep);
        if (rc != FAT32_OK) {
            /* The free changed the FAT after the last proof, and a copy write
             * in it failed: the copies are no longer verified to agree, so the
             * report must not carry the 1 from before the switch. */
            rep->mirrors_equal = 0;
            return rc;
        }

        /* 6. the freed entries must agree in every copy too. */
        rc = mirrors_hold(io, vol, hit.first_cluster, 0);
        if (rc != FAT32_OK) {
            rep->mirrors_equal = 0;
            return rc == FAT32_E_VERIFY ? FAT32_E_CORRUPT : rc;
        }

        /* 7. counters again, now that the old chain is free. */
        rep->stage = FAT32_STAGE_FSINFO;
        rc = fsinfo_apply(io, vol, fsinfo_valid, free_before - needed + rep->freed,
                          2, rep);
        if (rc != FAT32_OK)
            return rc;
    }

    rc = flush_now(io, rep);
    if (rc != FAT32_OK)
        return rc;

    /* 8. read the file back through its own name and compare. */
    rc = verify_named_file(io, vol, path, data, size, rep->first_cluster);
    if (rc != FAT32_OK)
        return rc;
    rep->verified = 1;
    rep->stage = FAT32_STAGE_DONE;
    return FAT32_OK;
}

/* ---------------------------------------------------- directory (M1i) --- */

int fat32_create_dir(const struct fat32_io *io, struct fat32_volume *vol,
                     const char *path, struct fat32_write_report *rep)
{
    struct fat32_write_report local;
    if (!rep)
        rep = &local;
    memset(rep, 0, sizeof(*rep));
    rep->stage = FAT32_STAGE_NONE;

    if (!io || !io->read || !vol || vol->error != FAT32_OK)
        return FAT32_E_IO;
    if (!io->write)
        return FAT32_E_READ_ONLY;
    if (vol->bytes_per_sector != SEC || vol->sectors_per_cluster == 0)
        return FAT32_E_BPB;

    rep->stage = FAT32_STAGE_CHECK;

    u32 parent = 0;
    struct fat32_path parts;
    int rc = path_parent(io, vol, path, &parts, &parent);
    if (rc != FAT32_OK)
        return rc;

    struct dir_hit hit;
    rc = dir_scan(io, vol, parent, parts.name, &hit);
    if (rc != FAT32_OK)
        return rc;
    if (hit.exists)
        return FAT32_E_EXISTS;         /* file or directory: never both */

    u32 free_before = 0;
    rc = fat_count_free(io, vol, &free_before);
    if (rc != FAT32_OK)
        return rc;

    int grow = !hit.free_slot;
    u32 demand = 1u + (grow ? 1u : 0u);
    if (free_before < demand)
        return FAT32_E_FULL;

    u32 fsinfo_claimed = 0, next_hint = 0xffffffffu;
    int fsinfo_valid = 0;
    rc = fsinfo_read(io, vol, &fsinfo_claimed, &next_hint, &fsinfo_valid);
    if (rc != FAT32_OK)
        return rc;

    /* The directory's own cluster and, when the parent is full, the cluster the
     * parent will grow into: reserved together, never by two FAT scans. */
    u32 chain[2];
    rc = fat_alloc_chain(io, vol, demand, chain, next_hint);
    if (rc != FAT32_OK)
        return rc;
    u32 dir_cluster = chain[0];

    rep->clusters = 1;
    rep->first_cluster = dir_cluster;
    rep->free_before = free_before;
    rep->fsinfo_claimed = fsinfo_claimed;
    rep->fsinfo_valid = fsinfo_valid;
    rep->stage = FAT32_STAGE_ALLOCATED;

    /* 1. a zeroed cluster with "." and ".." — nothing can name it yet. */
    rep->stage = FAT32_STAGE_DIR_INIT;
    rc = zero_cluster(io, vol, dir_cluster, rep);
    if (rc != FAT32_OK)
        return rc;
    rc = dir_init_entries(io, vol, dir_cluster, parent, rep);
    if (rc != FAT32_OK)
        return rc;

    /* 2. its chain (one cluster) in every FAT copy. */
    rep->stage = FAT32_STAGE_FAT;
    rc = fat_write_entry(io, vol, dir_cluster, 0x0fffffffu, rep);
    if (rc != FAT32_OK)
        return rc;
    rep->mirrors_equal = 1;
    rc = mirrors_hold(io, vol, dir_cluster, 0x0fffffffu);
    if (rc != FAT32_OK) {
        rep->mirrors_equal = 0;
        return rc == FAT32_E_VERIFY ? FAT32_E_CORRUPT : rc;
    }

    /* 3. grow the parent if it has no free slot, then find the slot. */
    if (grow) {
        rep->stage = FAT32_STAGE_DIR_GROW;
        u32 grown = 0;
        rc = dir_grow(io, vol, parent, chain[1], rep, &grown);
        if (rc != FAT32_OK)
            return rc;
        u32 rel;
        rc = cluster_sector(vol, grown, 0, &rel);
        if (rc != FAT32_OK)
            return rc;
        hit.sector = rel;
        hit.offset = 0;
        hit.free_slot = 1;
    }

    /* 4. counters before the name exists (over-reporting usage if interrupted). */
    rep->stage = FAT32_STAGE_FSINFO;
    rc = fsinfo_apply(io, vol, fsinfo_valid, free_before - demand, dir_cluster + 1, rep);
    if (rc != FAT32_OK)
        return rc;

    rep->stage = FAT32_STAGE_FLUSH;
    rc = flush_now(io, rep);
    if (rc != FAT32_OK)
        return rc;

    /* 5. the entry in the parent: one write. */
    rep->stage = FAT32_STAGE_DIR;
    rc = read_rel(io, vol, hit.sector, wsec);
    if (rc != FAT32_OK)
        return rc;
    u8 *ent = wsec + hit.offset;
    if (ent[0] != 0x00 && ent[0] != 0xe5)
        return FAT32_E_CORRUPT;
    dir_entry_fill(ent, parts.name, 0x10, dir_cluster, 0);
    rep->dir_sector = hit.sector;
    rep->dir_offset = hit.offset;
    rc = write_rel(io, vol, hit.sector, wsec, rep);
    if (rc != FAT32_OK)
        return rc;

    rc = flush_now(io, rep);
    if (rc != FAT32_OK)
        return rc;

    /* 6. read the directory back through the medium and prove it is readable. */
    rc = verify_dir(io, vol, dir_cluster, parent, parts.name,
                    hit.sector, hit.offset);
    if (rc != FAT32_OK)
        return rc;
    int count = 0;
    struct fat32_entry list[4];
    rc = fat32_list_dir(io, vol, path, list, 4, &count);
    if (rc != FAT32_OK)
        return rc;
    if (count != 0)
        return FAT32_E_VERIFY;         /* a new directory holds nothing but . and .. */
    rep->verified = 1;
    rep->stage = FAT32_STAGE_DONE;
    return FAT32_OK;
}
#ifndef PC5_HOST_TEST
/* ------------------------------------------------------- device layer --- */

static struct fat32_volume mounted;
static int mounted_device = -1;
static int mounted_partition = -1;

static int ata_source(void *ctx, u32 lba, u32 count, void *buf)
{
    int device = *(int *)ctx;
    return ata_read_sectors(device, lba, count, buf) == BLK_OK ? 0 : -1;
}

/* The write side of the same source.  It is only attached to a mount that
 * actually asks for it (fat32_create_mounted); the boot-time mount is made with
 * a read-only source, so nothing during boot can write even by mistake. */
static int ata_sink(void *ctx, u32 lba, u32 count, const void *buf)
{
    int device = *(int *)ctx;
    return ata_write_sectors(device, lba, count, buf) == BLK_OK ? 0 : -1;
}

static int ata_flush_cb(void *ctx)
{
    int device = *(int *)ctx;
    return ata_flush(device) == BLK_OK ? 0 : -1;
}

const struct fat32_volume *fat32_mounted(void) { return &mounted; }
int fat32_mounted_device(void) { return mounted_device; }
int fat32_mounted_partition(void) { return mounted_partition; }

int fat32_mount_partition(int device)
{
    const struct part_report *pr = part_report();
    memset(&mounted, 0, sizeof(mounted));
    mounted.error = FAT32_E_IO;
    mounted_device = -1;
    mounted_partition = -1;

    if (device < 0 || pr->device != device || !pr->valid || pr->count <= 0)
        return FAT32_E_IO;

    struct fat32_io io = { .read = ata_source, .ctx = &device };
    int first_error = FAT32_OK;

    for (int i = 0; i < pr->count; i++) {
        const struct pc5_partition *p = &pr->parts[i];
        if (p->sectors == 0)
            continue;
        struct fat32_volume trial;
        int rc = fat32_mount(&io, p->first_lba, p->sectors, &trial);
        if (rc == FAT32_OK) {
            mounted = trial;
            mounted_device = device;
            mounted_partition = p->index;
            return FAT32_OK;
        }
        if (first_error == FAT32_OK)
            first_error = rc;
    }
    mounted.error = first_error ? first_error : FAT32_E_NO_FILE;
    return mounted.error;
}

int fat32_list_mounted(struct fat32_entry *out, int max, int *count)
{
    if (mounted_device < 0 || mounted.error != FAT32_OK)
        return mounted.error ? mounted.error : FAT32_E_IO;
    struct fat32_io io = { .read = ata_source, .ctx = &mounted_device };
    return fat32_list_root(&io, &mounted, out, max, count);
}

/* Listing of a subdirectory of the mounted volume; "" is the root. */
int fat32_list_path(const char *path, struct fat32_entry *out, int max, int *count)
{
    if (mounted_device < 0 || mounted.error != FAT32_OK)
        return mounted.error ? mounted.error : FAT32_E_IO;
    struct fat32_io io = { .read = ata_source, .ctx = &mounted_device };
    struct fat32_volume copy = mounted;
    return fat32_list_dir(&io, &copy, path, out, max, count);
}

int fat32_read_named(const char *path, void *buf, u32 max, u32 *out_len)
{
    if (mounted_device < 0)
        return FAT32_E_IO;
    struct fat32_io io = { .read = ata_source, .ctx = &mounted_device };
    /* The volume remembers its partition origin, but the source needs the
     * device index, which is stable for the mounted volume. */
    struct fat32_volume copy = mounted;
    return fat32_read_file(&io, &copy, path, buf, max, out_len);
}

/* Is the mounted volume on a device this kernel can write to at all?  ATAPI is
 * excluded here *and* refused again by ata_write_sectors; the answer is "yes"
 * only for a mounted, parsed volume on a real ATA disk. */
int fat32_mounted_writable(void)
{
    if (mounted_device < 0 || mounted.error != FAT32_OK)
        return 0;
    const struct ata_drive *d = ata_drive(mounted_device);
    if (!d || d->atapi || d->sectors == 0)
        return 0;
    return 1;
}

/* The writable source for the mounted volume: the only place write callbacks
 * are attached, which is exactly why every other path in this file is
 * structurally read-only. */
static int mounted_write_io(struct fat32_io *io)
{
    if (mounted_device < 0 || mounted.error != FAT32_OK)
        return mounted.error ? mounted.error : FAT32_E_IO;
    if (!fat32_mounted_writable())
        return FAT32_E_READ_ONLY;
    memset(io, 0, sizeof(*io));
    io->read = ata_source;
    io->write = ata_sink;
    io->flush = ata_flush_cb;
    io->ctx = &mounted_device;
    return FAT32_OK;
}

/* The explicit write operations.  Nothing calls these during boot: they exist
 * for the `put`, `overwrite` and `mkdir` console commands and for the tests. */
int fat32_create_mounted(const char *path, const void *data, u32 size,
                         struct fat32_write_report *rep)
{
    struct fat32_write_report local;
    if (!rep)
        rep = &local;
    memset(rep, 0, sizeof(*rep));
    rep->stage = FAT32_STAGE_NONE;

    struct fat32_io io;
    int rc = mounted_write_io(&io);
    if (rc != FAT32_OK)
        return rc;
    struct fat32_volume copy = mounted;
    rc = fat32_create_file(&io, &copy, path, data, size, rep);
    if (rc == FAT32_OK)
        mounted.error = FAT32_OK;
    return rc;
}

int fat32_overwrite_mounted(const char *path, const void *data, u32 size,
                            struct fat32_write_report *rep)
{
    struct fat32_write_report local;
    if (!rep)
        rep = &local;
    memset(rep, 0, sizeof(*rep));
    rep->stage = FAT32_STAGE_NONE;

    struct fat32_io io;
    int rc = mounted_write_io(&io);
    if (rc != FAT32_OK)
        return rc;
    struct fat32_volume copy = mounted;
    rc = fat32_overwrite_file(&io, &copy, path, data, size, rep);
    if (rc == FAT32_OK)
        mounted.error = FAT32_OK;
    return rc;
}

int fat32_mkdir_mounted(const char *path, struct fat32_write_report *rep)
{
    struct fat32_write_report local;
    if (!rep)
        rep = &local;
    memset(rep, 0, sizeof(*rep));
    rep->stage = FAT32_STAGE_NONE;

    struct fat32_io io;
    int rc = mounted_write_io(&io);
    if (rc != FAT32_OK)
        return rc;
    struct fat32_volume copy = mounted;
    rc = fat32_create_dir(&io, &copy, path, rep);
    if (rc == FAT32_OK)
        mounted.error = FAT32_OK;
    return rc;
}
#endif /* PC5_HOST_TEST */
