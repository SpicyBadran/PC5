#include "madt64.h"
#include "serial64.h"
#include "include/pc5_map.h"

/* ACPI structure layout used here, all little-endian, all fields at fixed offsets:
 *
 *   RSDP (rev 2+)   0  signature "RSD PTR "   8 bytes
 *                   9  revision               1 byte
 *                  20  length                 4 bytes
 *                  24  XSDT address           8 bytes
 *
 *   SDT header       0  signature             4 bytes
 *                    4  length                4 bytes
 *                    8  revision              1 byte
 *                   36  (MADT) local APIC address 4 bytes
 *
 *   MADT record      0  type                  1 byte   (0 = processor local APIC)
 *                    1  length                1 byte
 *                    2  ACPI processor id     1 byte
 *                    3  APIC id               1 byte
 *                    4  flags                 4 bytes  (bit 0 = enabled)
 *
 *   x2APIC record    type 9, same shape with a 4-byte apic id at offset 4.
 */
#define RSDP_XSDT_OFFSET      24u
#define RSDP_LEGACY_LENGTH    20u
#define RSDP_V2_MIN_LENGTH    36u
#define SDT_HEADER_BYTES      36u
#define SDT_LENGTH_OFFSET      4u
#define MADT_LAPIC_OFFSET     36u
#define MADT_HEADER_BYTES     44u
#define MAX_TABLES            64u
#define MAX_TABLE_BYTES   (1024u * 1024u)   /* a bigger ACPI table is not credible */

#define MADT_TYPE_LOCAL_APIC   0u
#define MADT_TYPE_X2APIC       9u

static u32 read_u32(const u8 *p)
{
    return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}

static u64 read_u64(const u8 *p)
{
    return (u64)read_u32(p) | ((u64)read_u32(p + 4) << 32);
}

static int signature_is(const u8 *p, const char *want)
{
    u32 i;
    for (i = 0; i < 4u; i++)
        if (p[i] != (u8)want[i])
            return 0;
    return 1;
}

/* Find the table with `signature` among the firmware's pointer array.
 *
 * Every candidate address is checked against the memory map before being
 * dereferenced: a firmware pointer array is untrusted input, and following one into
 * unmapped memory faults the kernel during what is supposed to be enumeration. */
static const u8 *find_table(u64 root_address, u32 root_bytes, u32 entry_bytes,
                            const char *signature,
                            const struct pc5_map_view *map)
{
    const u8 *root;
    u32 offset;

    if (!pc5_map_covers(map, root_address, root_bytes, PC5_MMAP_FIRMWARE_DATA_TYPES))
        return 0;

    root = (const u8 *)(unsigned long)root_address;
    /* The pointer array starts at offset 36, immediately after the SDT header
     * (ACPI 6.x §5.2.7/§5.2.8). An earlier version started at offset 0/8 and still
     * found the table, because byte 40 happened to align with the first pointer - it
     * was reading a signature and length field as if they were addresses. Only the
     * signature and map checks stopped that from being a fault, and the walk was
     * silently dependent on table ordering. */
    for (offset = SDT_HEADER_BYTES; offset + entry_bytes <= root_bytes;
         offset += entry_bytes) {
        u64 table_address = (entry_bytes == 8u) ? read_u64(root + offset)
                                                : (u64)read_u32(root + offset);
        const u8 *table;

        if (table_address == 0)
            continue;
        /* The SDT header must be readable before its length can be trusted. */
        if (!pc5_map_covers(map, table_address, SDT_HEADER_BYTES,
                            PC5_MMAP_FIRMWARE_DATA_TYPES))
            continue;
        table = (const u8 *)(unsigned long)table_address;
        if (!signature_is(table, signature))
            continue;
        return table;
    }
    return 0;
}

const char *madt64_scan(struct pc5_madt64 *out, u64 rsdp_address,
                        const struct pc5_map_view *map)
{
    const u8 *rsdp;
    const u8 *madt;
    u32 rsdp_length;
    u32 root_address;
    u64 root_address64;
    u32 root_length;
    u32 madt_bytes;
    u32 offset;

    if (out == 0)
        return "no output structure";
    if (map == 0)
        return "no memory map";

    out->parsed = 0;
    out->acpi_revision = 0;
    out->used_xsdt = 0;
    out->local_apic_address = 0;
    out->cpu_count = 0;
    out->cpu_total = 0;
    out->x2apic_total = 0;
    out->truncated = 0;
    out->table_bytes = 0;

    if (rsdp_address == 0)
        return "no RSDP in the boot info";

    /* The RSDP itself was already checksum-validated before this is called; here it
     * only has to be readable. */
    if (!pc5_map_covers(map, rsdp_address, RSDP_LEGACY_LENGTH,
                        PC5_MMAP_FIRMWARE_DATA_TYPES))
        return "RSDP outside firmware memory";
    rsdp = (const u8 *)(unsigned long)rsdp_address;

    out->acpi_revision = rsdp[9];

    /* Revision 2+ publishes the XSDT at a fixed offset. A revision-0 RSDP has no
     * such field, so reading it would take bytes that mean something else - hence
     * the length and revision gate before the read. */
    root_address = read_u32(rsdp + 16);      /* RSDT address, always present */
    root_address64 = 0;
    if (rsdp[9] >= 2u) {
        if (!pc5_map_covers(map, rsdp_address, RSDP_V2_MIN_LENGTH,
                            PC5_MMAP_FIRMWARE_DATA_TYPES))
            return "RSDP revision claims 2 but not 36 bytes are readable";
        rsdp_length = read_u32(rsdp + 20);
        if (rsdp_length < RSDP_V2_MIN_LENGTH)
            return "RSDP length below the revision-2 minimum";
        root_address64 = read_u64(rsdp + RSDP_XSDT_OFFSET);
    }

    /* Prefer the XSDT when present: it is the only way to reach a table above 4 GiB. */
    madt = 0;
    if (root_address64 != 0) {
        u32 header_map_ok = pc5_map_covers(map, root_address64, SDT_HEADER_BYTES,
                                           PC5_MMAP_FIRMWARE_DATA_TYPES);
        if (header_map_ok) {
            root_length = read_u32((const u8 *)(unsigned long)root_address64
                                   + SDT_LENGTH_OFFSET);
            if (root_length >= SDT_HEADER_BYTES && root_length <= MAX_TABLE_BYTES) {
                madt = find_table(root_address64, root_length, 8u, "APIC", map);
                if (madt != 0)
                    out->used_xsdt = 1;
            }
        }
    }
    if (madt == 0 && root_address != 0) {
        if (pc5_map_covers(map, root_address, SDT_HEADER_BYTES,
                           PC5_MMAP_FIRMWARE_DATA_TYPES)) {
            root_length = read_u32((const u8 *)(unsigned long)root_address
                                   + SDT_LENGTH_OFFSET);
            if (root_length >= SDT_HEADER_BYTES && root_length <= MAX_TABLE_BYTES)
                madt = find_table(root_address, root_length, 4u, "APIC", map);
        }
    }
    if (madt == 0)
        return "no MADT reachable from the RSDP";

    /* The MADT's own length is the authority for how far its records extend, and it
     * must fit inside mapped memory before a single record is read. */
    madt_bytes = read_u32(madt + SDT_LENGTH_OFFSET);
    out->table_bytes = madt_bytes;
    if (madt_bytes < MADT_HEADER_BYTES)
        return "MADT shorter than its header";
    if (madt_bytes > MAX_TABLE_BYTES)
        return "MADT length beyond any credible table";
    if (!pc5_map_covers(map, (u64)(unsigned long)madt, madt_bytes,
                        PC5_MMAP_FIRMWARE_DATA_TYPES))
        return "MADT extends past mapped firmware memory";

    out->local_apic_address = read_u32(madt + MADT_LAPIC_OFFSET);
    out->parsed = 1;

    /* Walk the records. Each has its own length byte, which must advance by at least
     * the fixed part: a zero length would spin forever, and a short one would let a
     * later record start in the middle of this one. */
    offset = MADT_HEADER_BYTES;
    while (offset + 2u <= madt_bytes) {
        u8 type = madt[offset];
        u8 record_bytes = madt[offset + 1u];

        if (record_bytes < 2u)
            break;                                  /* malformed; stop, do not spin */
        if (offset + record_bytes > madt_bytes)
            break;                                  /* runs past the table; stop */

        if (type == MADT_TYPE_LOCAL_APIC && record_bytes >= 8u) {
            u32 flags = read_u32(madt + offset + 4u);
            out->cpu_total++;
            if (flags & 0x1u) {
                if (out->cpu_count < PC5_MADT_MAX_CPUS) {
                    struct pc5_cpu_entry *entry = &out->cpus[out->cpu_count];
                    entry->acpi_processor_id = madt[offset + 2u];
                    entry->apic_id = madt[offset + 3u];
                    entry->enabled = 1;
                    out->cpu_count++;
                } else {
                    out->truncated = 1;
                }
            }
        } else if (type == MADT_TYPE_X2APIC && record_bytes >= 16u) {
            /* Counted, not folded into the local-APIC list: a 32-bit x2APIC id does
             * not fit the structure, and silently truncating it would produce a
             * plausible wrong id. */
            out->x2apic_total++;
        }
        offset += record_bytes;
    }

    return 0;
}

static void field(const char *name, u64 value)
{
    serial64_write("PC5-64-MADT ");
    serial64_write(name);
    serial64_write("=");
    serial64_dec(value);
    serial64_write("\n");
}

void madt64_print(const struct pc5_madt64 *madt)
{
    u32 i;

    if (madt == 0)
        return;

    field("parsed", madt->parsed);
    field("acpi_revision", madt->acpi_revision);
    field("used_xsdt", madt->used_xsdt);
    field("table_bytes", madt->table_bytes);
    field("local_apic_address", madt->local_apic_address);
    field("cpu_count", madt->cpu_count);
    field("cpu_total", madt->cpu_total);
    field("x2apic_total", madt->x2apic_total);
    field("truncated", madt->truncated);

    for (i = 0; i < madt->cpu_count; i++) {
        serial64_write("PC5-64-MADT cpu apic_id=");
        serial64_dec(madt->cpus[i].apic_id);
        serial64_write(" acpi_id=");
        serial64_dec(madt->cpus[i].acpi_processor_id);
        serial64_write("\n");
    }
}
