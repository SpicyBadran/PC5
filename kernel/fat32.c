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
 * Deliberately unsupported, and said out loud:
 *   * long file names (LFN entries are skipped, not guessed),
 *   * sector sizes other than 512 bytes,
 *   * FAT12/FAT16 (refused structurally, not by convention),
 *   * subdirectories below the root (only the root is enumerated; a directory
 *     entry is reported as a directory, not descended),
 *   * anything that writes.
 *
 * Bounds discipline: every sector number is checked against the partition
 * before it is read, every cluster against the volume, and every chain walk is
 * bounded by the cluster count so a cycle cannot loop forever.
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

int fat32_find(const struct fat32_io *io, const struct fat32_volume *vol,
               const char *name, u32 *cluster, u32 *size, u8 *attr)
{
    if (!io || !vol || vol->error != FAT32_OK)
        return vol ? vol->error : FAT32_E_IO;
    u8 want[11];
    int rc = name_to_83(name, want);
    if (rc != FAT32_OK)
        return rc;

    struct dir_scan s;
    memset(&s, 0, sizeof(s));
    s.want = want;
    rc = scan_dir(io, vol, vol->root_cluster, &s);
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

int fat32_list_root(const struct fat32_io *io, const struct fat32_volume *vol,
                    struct fat32_entry *out, int max, int *count)
{
    if (!io || !vol || vol->error != FAT32_OK)
        return vol ? vol->error : FAT32_E_IO;
    struct dir_scan s;
    memset(&s, 0, sizeof(s));
    s.list = out;
    s.max = max;
    int rc = scan_dir(io, vol, vol->root_cluster, &s);
    if (rc != FAT32_OK)
        return rc;
    if (count)
        *count = s.count;
    return FAT32_OK;
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

    struct fat32_io io = { ata_source, &device };
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
    struct fat32_io io = { ata_source, &mounted_device };
    return fat32_list_root(&io, &mounted, out, max, count);
}

int fat32_read_named(const char *name, void *buf, u32 max, u32 *out_len)
{
    if (mounted_device < 0)
        return FAT32_E_IO;
    struct fat32_io io = { ata_source, &mounted_device };
    /* The volume remembers its partition origin, but the source needs the
     * device index, which is stable for the mounted volume. */
    struct fat32_volume copy = mounted;
    return fat32_read_file(&io, &copy, name, buf, max, out_len);
}
#endif /* PC5_HOST_TEST */
