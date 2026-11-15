/*
 * PC5 partition discovery — MBR and GPT, read-only.
 *
 * Why this file exists: after the write path (M1d) the next thing an installer
 * needs is to know what is already on a disk.  It needs that answer before it
 * is allowed to write anything, and it needs to be correct on a disk it did
 * not create.
 *
 * Design rules:
 *   * Read only.  Nothing here writes, formats or mounts.  The only I/O is
 *     ata_read_sectors(), which takes an explicit device index.
 *   * Fail closed.  A table that does not validate is reported as invalid and
 *     no partition is offered as a target.  "Probably fine" is not a state.
 *   * Pure where it matters.  Both parsers work on buffers, so the host tests
 *     exercise malformed tables, CRC failures and overlaps without a machine.
 *   * Bounded.  Only the first 512-byte sector, the GPT header and up to
 *     16 KiB of entry array are ever read; nothing loops on device values.
 *
 * Scope, stated plainly: this reads the four MBR primary slots and a GPT
 * entry array.  It does not walk extended/logical partitions and does not
 * interpret filesystems.  Both are later tasks (docs/ROADMAP.md M1e/M2).
 */
#include "pc5.h"

#define SECTOR_SIZE_ 512u
#define GPT_HEADER_LBA 1u
#define GPT_MAX_ENTRIES 256u
#define GPT_MAX_ENTRY_SIZE 256u
#define GPT_MIN_ENTRY_SIZE 128u
#define GPT_MIN_HEADER_SIZE 92u
#define GPT_MAX_TABLE_BYTES 16384u /* 128 entries x 128 bytes, the usual size */

#define MBR_SIG_OFFSET 510
#define MBR_PART_OFFSET 446
#define MBR_PART_SIZE 16

static struct part_report report;

static u32 rd32(const u8 *p)
{
    return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}

static u64 rd64(const u8 *p)
{
    return (u64)rd32(p) | ((u64)rd32(p + 4) << 32);
}

u32 part_crc32(const void *data, u32 len)
{
    const u8 *p = (const u8 *)data;
    u32 crc = 0xffffffffu;
    for (u32 i = 0; i < len; i++) {
        crc ^= p[i];
        for (int b = 0; b < 8; b++)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

void part_guid_hex(const u8 guid[16], char out[PART_GUID_HEX_LEN])
{
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 16; i++) {
        out[i * 2] = hex[guid[i] >> 4];
        out[i * 2 + 1] = hex[guid[i] & 0x0f];
    }
    out[32] = 0;
}

const char *part_scheme_name(int scheme)
{
    switch (scheme) {
    case PART_SCHEME_MBR: return "mbr";
    case PART_SCHEME_GPT: return "gpt";
    default: return "none";
    }
}

const char *part_status_name(int status)
{
    switch (status) {
    case PART_OK: return "ok";
    case PART_E_NO_DEVICE: return "no-device";
    case PART_E_ATAPI: return "atapi";
    case PART_E_IO: return "io";
    case PART_E_SIGNATURE: return "signature";
    case PART_E_CRC: return "crc";
    case PART_E_TABLE: return "table";
    case PART_E_RANGE: return "range";
    case PART_E_OVERLAP: return "overlap";
    default: return "unknown";
    }
}

static void reset(struct part_report *out, u32 capacity)
{
    memset(out, 0, sizeof(*out));
    out->device = -1;
    out->capacity = capacity;
    out->scheme = PART_SCHEME_NONE;
}

/* Every recorded partition must be non-empty, inside the device, and must not
 * overlap another.  A table that fails this is not offered as usable. */
static int check_ranges(struct part_report *out)
{
    for (int i = 0; i < out->count; i++) {
        const struct pc5_partition *a = &out->parts[i];
        if (a->sectors == 0)
            return PART_E_RANGE;
        u64 end = (u64)a->first_lba + a->sectors;
        if (a->first_lba == 0 || end > out->capacity)
            return PART_E_RANGE;
        for (int j = 0; j < i; j++) {
            const struct pc5_partition *b = &out->parts[j];
            u64 b_end = (u64)b->first_lba + b->sectors;
            if (a->first_lba < b_end && b->first_lba < end)
                return PART_E_OVERLAP;
        }
    }
    return PART_OK;
}

/* UTF-16LE GPT name -> printable ASCII.  Characters outside printable ASCII
 * become '?': the kernel console cannot render them and pretending otherwise
 * would put unverified text in the log. */
static void gpt_name(const u8 *entry, char *out)
{
    int n = 0;
    for (int i = 0; i < 36; i++) {
        u16 ch = (u16)(entry[56 + i * 2] | (entry[56 + i * 2 + 1] << 8));
        if (ch == 0)
            break;
        out[n++] = (ch >= 0x20 && ch < 0x7f) ? (char)ch : '?';
    }
    out[n] = 0;
}

static int guid_is_zero(const u8 guid[16])
{
    for (int i = 0; i < 16; i++)
        if (guid[i])
            return 0;
    return 1;
}

/* Validate the GPT header and report where the entry array lives.  Used both
 * by part_scan() (to size its read) and by part_parse_gpt(). */
static int gpt_geometry(const u8 *header, u32 header_bytes, u32 *entries_lba,
                        u32 *entries_count, u32 *entry_size)
{
    if (header_bytes < GPT_MIN_HEADER_SIZE)
        return PART_E_TABLE;
    if (memcmp(header, "EFI PART", 8) != 0)
        return PART_E_SIGNATURE;

    u32 revision = rd32(header + 8);
    if ((revision >> 16) != 1) /* major version must be 1 */
        return PART_E_TABLE;

    u32 size = rd32(header + 12);
    if (size < GPT_MIN_HEADER_SIZE || size > header_bytes)
        return PART_E_TABLE;

    u32 stored_crc = rd32(header + 16);
    u8 copy[512];
    if (size > sizeof(copy))
        return PART_E_TABLE;
    memcpy(copy, header, size);
    copy[16] = copy[17] = copy[18] = copy[19] = 0;
    if (part_crc32(copy, size) != stored_crc)
        return PART_E_CRC;

    u64 lba = rd64(header + 72);
    u32 count = rd32(header + 80);
    u32 esize = rd32(header + 84);

    if (lba == 0 || lba > 0xffffffffull)
        return PART_E_TABLE;
    if (count == 0 || count > GPT_MAX_ENTRIES)
        return PART_E_TABLE;
    if (esize < GPT_MIN_ENTRY_SIZE || esize > GPT_MAX_ENTRY_SIZE || (esize & 7u))
        return PART_E_TABLE;
    if ((u64)count * esize > GPT_MAX_TABLE_BYTES)
        return PART_E_TABLE;

    *entries_lba = (u32)lba;
    *entries_count = count;
    *entry_size = esize;
    return PART_OK;
}

int part_parse_mbr(const u8 sector[512], u32 capacity, struct part_report *out)
{
    if (!sector || !out)
        return PART_E_TABLE;
    reset(out, capacity);

    if (sector[MBR_SIG_OFFSET] != 0x55 || sector[MBR_SIG_OFFSET + 1] != 0xaa) {
        out->error = PART_E_SIGNATURE;
        return PART_E_SIGNATURE;
    }

    /* A protective MBR is the outermost layer of a GPT disk.  It says "the
     * real table is GPT" and carries nothing usable as a partition itself. */
    for (int i = 0; i < 4; i++) {
        const u8 *e = sector + MBR_PART_OFFSET + i * MBR_PART_SIZE;
        if (e[4] == 0xee && rd32(e + 8) == 1) {
            out->scheme = PART_SCHEME_GPT;
            out->valid = 0;
            out->error = PART_OK;
            return PART_OK;
        }
    }

    out->scheme = PART_SCHEME_MBR;
    for (int i = 0; i < 4; i++) {
        const u8 *e = sector + MBR_PART_OFFSET + i * MBR_PART_SIZE;
        u8 type = e[4];
        u32 first = rd32(e + 8);
        u32 sectors = rd32(e + 12);
        if (type == 0 && first == 0 && sectors == 0)
            continue;
        if (out->count >= PART_MAX) {
            out->error = PART_E_TABLE;
            return PART_E_TABLE;
        }
        struct pc5_partition *p = &out->parts[out->count];
        p->scheme = PART_SCHEME_MBR;
        p->index = (u8)(out->count + 1);
        p->type = type;
        p->first_lba = first;
        p->sectors = sectors;
        /* An extended partition is a container for logical partitions that
         * this code does not walk.  Say so instead of inventing a size. */
        if (type == 0x05 || type == 0x0f || type == 0x85)
            str_copy(p->name, "extended (not walked)", sizeof(p->name));
        out->count++;
    }

    int rc = check_ranges(out);
    out->error = rc;
    out->valid = rc == PART_OK;
    return rc;
}

int part_parse_gpt(const u8 *header, u32 header_bytes, const u8 *entries,
                   u32 entries_bytes, u32 capacity, struct part_report *out)
{
    if (!header || !entries || !out)
        return PART_E_TABLE;
    reset(out, capacity);
    out->scheme = PART_SCHEME_GPT;

    u32 entries_lba = 0, count = 0, esize = 0;
    int rc = gpt_geometry(header, header_bytes, &entries_lba, &count, &esize);
    if (rc != PART_OK) {
        out->error = rc;
        return rc;
    }

    u32 table_bytes = count * esize;
    if (entries_bytes < table_bytes) {
        out->error = PART_E_TABLE;
        return PART_E_TABLE;
    }
    if (part_crc32(entries, table_bytes) != rd32(header + 88)) {
        out->error = PART_E_CRC;
        return PART_E_CRC;
    }

    for (u32 i = 0; i < count; i++) {
        const u8 *e = entries + i * esize;
        if (guid_is_zero(e))
            continue;
        u64 first = rd64(e + 32);
        u64 last = rd64(e + 40);
        if (first < 2 || last < first) {
            out->error = PART_E_RANGE;
            return PART_E_RANGE;
        }
        if (out->count >= PART_MAX) {
            out->error = PART_E_TABLE;
            return PART_E_TABLE;
        }
        struct pc5_partition *p = &out->parts[out->count];
        p->scheme = PART_SCHEME_GPT;
        p->index = (u8)(out->count + 1);
        p->type = 0;
        memcpy(p->type_guid, e, 16);
        p->first_lba = (u32)first;
        p->sectors = (u32)(last - first + 1);
        gpt_name(e, p->name);
        out->count++;
    }

    rc = check_ranges(out);
    out->error = rc;
    out->valid = rc == PART_OK;
    return rc;
}

#ifndef PC5_HOST_TEST
static u8 mbr_sector[SECTOR_SIZE_];
static u8 gpt_header[SECTOR_SIZE_];
static u8 gpt_entries[GPT_MAX_TABLE_BYTES];

/*
 * Device layer.  Reads sector 0, and for a GPT disk the header and the entry
 * array, through the explicit-device sector API.  Nothing here can reach the
 * boot medium by accident: the device index comes from the caller, and an
 * ATAPI device is refused outright.
 */
const struct part_report *part_scan(int device)
{
    memset(&report, 0, sizeof(report));
    report.device = device;
    report.scheme = PART_SCHEME_NONE;

    const struct ata_drive *d = ata_drive(device);
    if (device < 0 || !d || !d->present) {
        report.device = -1;
        report.error = PART_E_NO_DEVICE;
        return &report;
    }
    if (d->atapi) {
        report.error = PART_E_ATAPI;
        return &report;
    }
    if (d->sectors == 0) {
        report.error = PART_E_TABLE; /* unknown capacity: nothing can be validated */
        return &report;
    }
    report.capacity = d->sectors;

    int rc = ata_read_sectors(device, 0, 1, mbr_sector);
    if (rc != BLK_OK) {
        report.error = PART_E_IO;
        return &report;
    }
    rc = part_parse_mbr(mbr_sector, report.capacity, &report);
    report.device = device;
    if (rc != PART_OK || report.scheme != PART_SCHEME_GPT)
        return &report;

    /* Protective MBR says the real table is GPT.  Validate that claim before
     * reporting any partition: a protective MBR with a broken GPT is a
     * damaged disk, not an empty one. */
    rc = ata_read_sectors(device, GPT_HEADER_LBA, 1, gpt_header);
    if (rc != BLK_OK) {
        report.error = PART_E_IO;
        return &report;
    }

    u32 entries_lba = 0, count = 0, esize = 0;
    rc = gpt_geometry(gpt_header, SECTOR_SIZE_, &entries_lba, &count, &esize);
    if (rc != PART_OK) {
        report.error = rc;
        return &report;
    }

    u32 table_bytes = count * esize;
    u32 table_sectors = (table_bytes + SECTOR_SIZE_ - 1) / SECTOR_SIZE_;
    if ((u64)entries_lba + table_sectors > report.capacity) {
        report.error = PART_E_RANGE;
        return &report;
    }

    u32 done = 0;
    while (done < table_bytes) {
        u32 chunk = table_bytes - done;
        if (chunk > 32 * SECTOR_SIZE_) /* 32-sector bound in the ATA driver */
            chunk = 32 * SECTOR_SIZE_;
        u32 sectors = (chunk + SECTOR_SIZE_ - 1) / SECTOR_SIZE_;
        rc = ata_read_sectors(device, entries_lba + done / SECTOR_SIZE_, sectors,
                              gpt_entries + done);
        if (rc != BLK_OK) {
            report.error = PART_E_IO;
            return &report;
        }
        done += sectors * SECTOR_SIZE_;
    }

    part_parse_gpt(gpt_header, SECTOR_SIZE_, gpt_entries, table_bytes,
                   report.capacity, &report);
    report.device = device;
    return &report;
}
#endif /* PC5_HOST_TEST */

const struct part_report *part_report(void)
{
    return &report;
}
