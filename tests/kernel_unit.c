#define PC5_HOST_TEST 1
/*
 * Host-side unit tests for the parts of the PC5 kernel that are pure logic:
 * string/number formatting, E820 accounting, SMBIOS record walking and the
 * compatibility chain state machine.  These run on the build host with the
 * host compiler, which makes formatting bugs visible without booting anything.
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/mman.h>

#define PC5_HOST_TEST 1

#include "pc5.h"

static int failures;
static int checks;

static void check_str(const char *what, const char *got, const char *want)
{
    checks++;
    if (strcmp(got, want) != 0) {
        failures++;
        printf("FAIL %s: got \"%s\" want \"%s\"\n", what, got, want);
    }
}

static void check_int(const char *what, long got, long want)
{
    checks++;
    if (got != want) {
        failures++;
        printf("FAIL %s: got %ld want %ld\n", what, got, want);
    }
}

static void test_format(void)
{
    char buf[128];
    pc5_snprintf(buf, sizeof(buf), "%s", "hello");
    check_str("plain string", buf, "hello");
    pc5_snprintf(buf, sizeof(buf), "[%s]", "ab");
    check_str("bracketed", buf, "[ab]");
    pc5_snprintf(buf, sizeof(buf), "%-4s|", "ab");
    check_str("left pad", buf, "ab  |");
    pc5_snprintf(buf, sizeof(buf), "%4s|", "ab");
    check_str("right pad", buf, "  ab|");
    pc5_snprintf(buf, sizeof(buf), "%02x:%02x.%x", 0, 5, 2);
    check_str("pci id", buf, "00:05.2");
    pc5_snprintf(buf, sizeof(buf), "%u-%d-%x", 42, -7, 0xbeef);
    check_str("numbers", buf, "42--7-beef");
    pc5_snprintf(buf, sizeof(buf), "%08x", 0x1234);
    check_str("hex width", buf, "00001234");
    pc5_snprintf(buf, sizeof(buf), "100%%");
    check_str("percent", buf, "100%");
    pc5_snprintf(buf, sizeof(buf), "%u", 0);
    check_str("zero", buf, "0");
    pc5_snprintf(buf, sizeof(buf), "%llu", (u64)4294967296ull);
    check_str("u64", buf, "4294967296");
    pc5_snprintf(buf, sizeof(buf), "%s:%s", "8086", "1237");
    check_str("two strings", buf, "8086:1237");
}

static void test_numbers(void)
{
    char buf[40];
    u32_to_str(1234, buf, 10);
    check_str("u32 dec", buf, "1234");
    u32_to_str(0xdead, buf, 16);
    check_str("u32 hex", buf, "dead");
    u64_to_str(9007199254740993ull, buf, 10);
    check_str("u64 big", buf, "9007199254740993");
    u32_to_hex(0x1f, buf, 4);
    check_str("hex digits", buf, "001f");
}

static void test_strings(void)
{
    char buf[16];
    check_int("memcmp eq", memcmp("abc", "abc", 3), 0);
    check_int("memcmp ne", memcmp("abc", "abd", 3) < 0, 1);
    check_int("contains", str_contains("PC5 BOOT ENVIRONMENT", "BOOT"), 1);
    check_int("contains missing", str_contains("PC5", "BOOT"), 0);
    str_copy(buf, "truncate-me", 5);
    check_str("str_copy", buf, "trun");
    str_copy(buf, "ab", sizeof(buf));
    str_append(buf, "cd", sizeof(buf));
    check_str("append", buf, "abcd");
}

static void test_memory_map(void)
{
    /* the kernel addresses the map through a 32-bit physical address, so the
       host test allocates below 4 GiB */
    struct e820_entry *entries = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT, -1, 0);
    if (entries == MAP_FAILED) {
        printf("SKIP memory map test: no low memory available\n");
        return;
    }
    struct pc5_boot_info bi;
    struct mem_info info;
    memset(&bi, 0, sizeof(bi));
    memset(entries, 0, 4096);
    entries[0].base = 0;
    entries[0].length = 0x9fc00;
    entries[0].type = 1;
    entries[1].base = 0x9fc00;
    entries[1].length = 0x400;
    entries[1].type = 2;
    entries[2].base = 0x100000;
    entries[2].length = 0x7ee0000;
    entries[2].type = 1;
    bi.e820_addr = (u32)(unsigned long)entries;
    bi.e820_count = 3;
    mem_detect(&bi, &info);
    check_int("e820 entries", (long)info.entries, 3);
    check_int("usable MiB", (long)(info.usable_bytes >> 20), 127);
    check_int("top MiB", (long)info.top_address_mb, 127);
}

static void test_boot_contract(void)
{
    /* The kernel must refuse boot information it cannot trust, and must say
       *which* part was wrong: a version mismatch and a corrupt block are
       different bugs in different places.  This is the same predicate kmain
       runs, compiled into the host binary. */
    struct pc5_boot_info bi;
    memset(&bi, 0, sizeof(bi));

    bi.magic = PC5_BOOT_MAGIC;
    bi.version = PC5_PROTOCOL_VERSION;
    check_int("boot info accepted", pc5_boot_info_problem(&bi), 0);

    bi.magic = 0xDEADBEEFu;
    bi.version = PC5_PROTOCOL_VERSION;
    check_int("bad magic rejected", pc5_boot_info_problem(&bi), 1);

    bi.magic = PC5_BOOT_MAGIC;
    bi.version = PC5_PROTOCOL_VERSION + 1;
    check_int("version mismatch rejected", pc5_boot_info_problem(&bi), 2);

    bi.magic = 0;
    bi.version = 0;
    check_int("empty block rejected", pc5_boot_info_problem(&bi), 1);
    check_int("null block rejected", pc5_boot_info_problem(NULL), 1);
}

static void test_chain(void)
{
    struct pc5_boot_info bi;
    struct cpu_info cpu;
    struct mem_info mem;
    struct pci_report pci;
    struct smbios_info smb;
    struct acpi_info acpi;
    struct fw_report fw;
    memset(&bi, 0, sizeof(bi));
    memset(&cpu, 0, sizeof(cpu));
    memset(&mem, 0, sizeof(mem));
    memset(&pci, 0, sizeof(pci));
    memset(&smb, 0, sizeof(smb));
    memset(&acpi, 0, sizeof(acpi));
    memset(&fw, 0, sizeof(fw));
    str_copy(cpu.vendor, "GenuineIntel", sizeof(cpu.vendor));
    mem.total_bytes = 128ull << 20;
    pci.count = 5;
    struct storage_report sr;
    memset(&sr, 0, sizeof(sr));
    sr.selected = -1;
    chain_evaluate(&bi, &cpu, &mem, &pci, &smb, &acpi, &fw, 1, &sr);
    const struct chain_stage *st = chain_stages();
    check_int("chain ready", st != NULL, 1);
    check_int("stage 1 ok", st[0].state == CHAIN_OK, 1);
    check_int("stage 3 partial", st[2].state == CHAIN_PARTIAL, 1);
    check_int("stage 4 blocked without storage", st[3].state == CHAIN_BLOCKED, 1);
    check_int("stage 5 blocked", st[4].state == CHAIN_BLOCKED, 1);
    check_int("first blocked", chain_first_blocked(), 3);
    check_int("never claims execution", st[8].state != CHAIN_OK, 1);
    fw.supplied = 1;
    fw.identified = 1;
    sr.drives = 1;
    sr.selected = 0;
    sr.mounted = 1;
    sr.file_ok = 1;
    sr.file_signature = 1;
    sr.file_bytes = 30908;
    str_copy(sr.volume, "PC5", sizeof(sr.volume));
    str_copy(sr.file_name, "KERNEL.BIN", sizeof(sr.file_name));
    chain_evaluate(&bi, &cpu, &mem, &pci, &smb, &acpi, &fw, 1, &sr);
    st = chain_stages();
    check_int("stage 4 evidence names the volume", strstr(st[3].evidence, "PC5") != NULL, 1);
    check_int("stage 4 stays partial (no installer)", st[3].state == CHAIN_PARTIAL, 1);
    check_int("stage 5 ok with firmware", st[4].state == CHAIN_OK, 1);
    check_int("stage 7 still blocked", st[6].state == CHAIN_BLOCKED, 1);
}

/* ---- block request rules (kernel/block.c) ---- */
static void test_block_rules(void)
{
    /* dev capacity 16384 sectors, disk (not ATAPI) */
    check_int("valid read", blk_check_disk_request(2048, 8, 16384, 0, 0), BLK_OK);
    check_int("valid write", blk_check_disk_request(2048, 8, 16384, 1, 0), BLK_OK);
    check_int("whole disk", blk_check_disk_request(0, 16384, 16384, 1, 0), BLK_OK);
    check_int("zero count", blk_check_disk_request(2048, 0, 16384, 1, 0), BLK_E_COUNT);
    check_int("lba at capacity", blk_check_disk_request(16384, 1, 16384, 1, 0), BLK_E_RANGE);
    check_int("one past end", blk_check_disk_request(16383, 2, 16384, 1, 0), BLK_E_RANGE);
    check_int("unknown capacity", blk_check_disk_request(0, 1, 0, 1, 0), BLK_E_RANGE);
    check_int("lba overflow", blk_check_disk_request(0xffffffffu, 1, 16384, 1, 0), BLK_E_RANGE);
    check_int("count wrap", blk_check_disk_request(16380, 0xfffffff0u, 16384, 1, 0), BLK_E_RANGE);
    check_int("atapi write refused", blk_check_disk_request(0, 1, 0, 1, 1), BLK_E_READ_ONLY);
    /* the device type is decided before the range: an ATAPI write is refused
     * whatever the LBA and count say */
    check_int("atapi write refused even at zero", blk_check_disk_request(0, 0, 0, 1, 1), BLK_E_READ_ONLY);
    /* optical reads do not use this validator (they go through ata_read_blocks,
     * which knows 2048-byte blocks); here an ATAPI read with no capacity must
     * still fail closed rather than be mistaken for a write refusal */
    check_int("atapi read, unknown capacity", blk_check_disk_request(16, 1, 0, 0, 1), BLK_E_RANGE);

    check_int("chunk exact", blk_chunk_sectors(8, 32), 8);
    check_int("chunk capped", blk_chunk_sectors(100, 32), 32);
    check_int("chunk zero remaining", blk_chunk_sectors(0, 32), 0);
    check_int("chunk zero max", blk_chunk_sectors(8, 0), 0);

    check_str("status ok", blk_status_name(BLK_OK), "ok");
    check_str("status read-only", blk_status_name(BLK_E_READ_ONLY), "read-only");
    check_str("status range", blk_status_name(BLK_E_RANGE), "range");
    check_str("status timeout", blk_status_name(BLK_E_TIMEOUT), "timeout");
}

/* ---- SHA-256 known-answer tests (kernel/sha256.c) ---- */
static void sha_of(const u8 *data, u32 len, char out[65])
{
    struct sha256_ctx ctx;
    u8 digest[32];
    sha256_init(&ctx);
    if (len && data)
        sha256_update(&ctx, data, len);
    sha256_final(&ctx, digest);
    sha256_hex(digest, out);
}

static void test_sha256(void)
{
    char hex[65];
    u8 pattern[4096];

    sha_of(NULL, 0, hex);
    check_str("sha256 empty", hex,
              "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    sha_of((const u8 *)"abc", 3, hex);
    check_str("sha256 abc", hex,
              "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    sha_of((const u8 *)"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", 56, hex);
    check_str("sha256 56-byte vector", hex,
              "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");

    memset(pattern, 'a', sizeof(pattern));
    sha_of(pattern, 112, hex);
    check_str("sha256 112-byte vector", hex,
              "f54353008a2553262ecdc4a34749563ba0950e8b0fc8652780b0a614b99683c1");

    /* the write self-test pattern, identical to tests/boot_test.py */
    for (u32 i = 0; i < sizeof(pattern); i++)
        pattern[i] = (u8)((i * 7u + 0x5au) & 0xffu);
    sha_of(pattern, sizeof(pattern), hex);
    check_str("sha256 write-test pattern", hex,
              "80fdc12208ec4af1410125cbe146549182100a6b1e0cf67e382aa9aefdbafa3d");

    /* split updates must equal a single update */
    struct sha256_ctx a, b;
    u8 da[32], db[32];
    sha256_init(&a);
    sha256_update(&a, pattern, 100);
    sha256_update(&a, pattern + 100, sizeof(pattern) - 100);
    sha256_final(&a, da);
    sha256_init(&b);
    sha256_update(&b, pattern, sizeof(pattern));
    sha256_final(&b, db);
    check_int("sha256 streaming equals one-shot", memcmp(da, db, sizeof(da)), 0);
}


/* ---------------------------------------------------------- partitions */

static void put32(u8 *p, u32 v)
{
    p[0] = (u8)v; p[1] = (u8)(v >> 8); p[2] = (u8)(v >> 16); p[3] = (u8)(v >> 24);
}

static void put64(u8 *p, u64 v)
{
    put32(p, (u32)v);
    put32(p + 4, (u32)(v >> 32));
}

/* Build a plain MBR with up to four entries: type, first LBA, sector count. */
static void build_mbr(u8 *s, const u8 types[4], const u32 first[4], const u32 count[4])
{
    memset(s, 0, 512);
    for (int i = 0; i < 4; i++) {
        u8 *e = s + 446 + i * 16;
        e[4] = types[i];
        put32(e + 8, first[i]);
        put32(e + 12, count[i]);
    }
    s[510] = 0x55;
    s[511] = 0xaa;
}

static void gpt_name_into(u8 *entry, const char *name)
{
    for (int i = 0; name[i] && i < 35; i++) {
        entry[56 + i * 2] = (u8)name[i];
        entry[56 + i * 2 + 1] = 0;
    }
}

/* Build a GPT header plus its entry array.  `corrupt_header_crc` and
 * `corrupt_entries_crc` flip the stored checksum so the refusal path can be
 * tested; the caller passes the capacity to keep the header coherent. */
static void build_gpt(u8 *header, u8 *entries, u32 capacity, u32 num_entries,
                      u32 entry_size, int corrupt_header_crc, int corrupt_entries_crc)
{
    memset(header, 0, 512);
    memcpy(header, "EFI PART", 8);
    put32(header + 8, 0x00010000u);
    put32(header + 12, 92);
    put64(header + 24, 1);
    put64(header + 32, capacity - 1);
    put64(header + 40, 34);
    put64(header + 48, capacity - 34);
    put64(header + 72, 2);
    put32(header + 80, num_entries);
    put32(header + 84, entry_size);

    memset(entries, 0, num_entries * entry_size);
    put32(header + 88, part_crc32(entries, num_entries * entry_size) ^
                           (corrupt_entries_crc ? 1u : 0u));

    put32(header + 16, 0);
    u32 crc = part_crc32(header, 92);
    put32(header + 16, corrupt_header_crc ? crc ^ 1u : crc);
}

static void gpt_entry(u8 *entries, u32 entry_size, u32 i, u8 type_byte,
                      u64 first, u64 last, const char *name)
{
    u8 *e = entries + i * entry_size;
    memset(e, 0, entry_size);
    e[0] = type_byte;      /* first byte of the type GUID is enough to be "used" */
    e[15] = 0x11;
    put64(e + 32, first);
    put64(e + 40, last);
    if (name)
        gpt_name_into(e, name);
}

static void test_partitions(void)
{
    u8 mbr[512];
    u8 header[512];
    u8 entries[128 * 128];
    struct part_report r;
    const u8 no_types[4] = {0, 0, 0, 0};
    const u32 zeros[4] = {0, 0, 0, 0};

    check_int("crc32 check vector", (long)part_crc32("123456789", 9), 0xcbf43926L);

    /* ---- MBR ---- */
    memset(mbr, 0, sizeof(mbr));
    check_int("mbr without signature", part_parse_mbr(mbr, 16384, &r), PART_E_SIGNATURE);

    u8 types[4] = {0x83, 0x07, 0, 0};
    u32 first[4] = {2048, 8192, 0, 0};
    u32 count[4] = {2048, 2048, 0, 0};
    build_mbr(mbr, types, first, count);
    check_int("mbr parses", part_parse_mbr(mbr, 16384, &r), PART_OK);
    check_int("mbr scheme", r.scheme, PART_SCHEME_MBR);
    check_int("mbr count", r.count, 2);
    check_int("mbr valid", r.valid, 1);
    check_int("mbr first lba", (long)r.parts[0].first_lba, 2048);
    check_int("mbr first type", r.parts[0].type, 0x83);
    check_int("mbr second type", r.parts[1].type, 0x07);

    types[0] = 0x83; first[0] = 2048; count[0] = 4096;
    types[1] = 0x83; first[1] = 4096; count[1] = 2048;
    build_mbr(mbr, types, first, count);
    check_int("mbr overlap refused", part_parse_mbr(mbr, 16384, &r), PART_E_OVERLAP);
    check_int("mbr overlap not valid", r.valid, 0);

    types[0] = 0x83; first[0] = 16380; count[0] = 16;
    types[1] = 0; count[1] = 0;
    build_mbr(mbr, types, first, count);
    check_int("mbr past end refused", part_parse_mbr(mbr, 16384, &r), PART_E_RANGE);

    types[0] = 0x0f; first[0] = 4096; count[0] = 4096;
    types[1] = 0; first[1] = 0; count[1] = 0;
    build_mbr(mbr, types, first, count);
    check_int("mbr extended parses", part_parse_mbr(mbr, 16384, &r), PART_OK);
    check_str("mbr extended labelled", r.parts[0].name, "extended (not walked)");

    /* protective MBR: the GPT layer must decide */
    build_mbr(mbr, no_types, zeros, zeros);
    mbr[446 + 4] = 0xee;
    put32(mbr + 446 + 8, 1);
    put32(mbr + 446 + 12, 0xffffffffu);
    check_int("protective mbr recognised", part_parse_mbr(mbr, 16384, &r), PART_OK);
    check_int("protective mbr means gpt", r.scheme, PART_SCHEME_GPT);
    check_int("protective mbr carries no partition", r.count, 0);

    /* ---- GPT ---- */
    build_gpt(header, entries, 16384, 128, 128, 0, 0);
    gpt_entry(entries, 128, 0, 0xa2, 2048, 4095, "PC5 ROOT");
    gpt_entry(entries, 128, 1, 0xa2, 4096, 6143, "PC5 DATA");
    /* the entry CRC was computed before the entries existed: rebuild it */
    put32(header + 88, part_crc32(entries, 128 * 128));
    put32(header + 16, 0);
    put32(header + 16, part_crc32(header, 92));

    check_int("gpt parses", part_parse_gpt(header, 512, entries, 128 * 128, 16384, &r), PART_OK);
    check_int("gpt scheme", r.scheme, PART_SCHEME_GPT);
    check_int("gpt count", r.count, 2);
    check_int("gpt valid", r.valid, 1);
    check_str("gpt name 1", r.parts[0].name, "PC5 ROOT");
    check_str("gpt name 2", r.parts[1].name, "PC5 DATA");
    check_int("gpt first lba", (long)r.parts[0].first_lba, 2048);
    check_int("gpt sectors", (long)r.parts[0].sectors, 2048);

    u8 type_guid[16];
    memset(type_guid, 0, sizeof(type_guid));
    type_guid[0] = 0xa2;
    type_guid[15] = 0x11;
    char hex[PART_GUID_HEX_LEN];
    part_guid_hex(type_guid, hex);
    check_str("guid hex", hex, "a2000000000000000000000000000011");

    /* corrupt header CRC */
    u8 bad[512];
    memcpy(bad, header, 512);
    bad[16] ^= 1;
    check_int("gpt header crc refused", part_parse_gpt(bad, 512, entries, 128 * 128, 16384, &r), PART_E_CRC);

    /* corrupt entry CRC */
    u8 bad_entries[128 * 128];
    memcpy(bad_entries, entries, sizeof(bad_entries));
    bad_entries[128 + 56] ^= 1;
    check_int("gpt entry crc refused", part_parse_gpt(header, 512, bad_entries, 128 * 128, 16384, &r), PART_E_CRC);

    /* entry past the device */
    build_gpt(header, entries, 16384, 128, 128, 0, 0);
    gpt_entry(entries, 128, 0, 0xa2, 16000, 17000, "TOO BIG");
    put32(header + 88, part_crc32(entries, 128 * 128));
    put32(header + 16, 0);
    put32(header + 16, part_crc32(header, 92));
    check_int("gpt past end refused", part_parse_gpt(header, 512, entries, 128 * 128, 16384, &r), PART_E_RANGE);

    /* overlapping entries */
    build_gpt(header, entries, 16384, 128, 128, 0, 0);
    gpt_entry(entries, 128, 0, 0xa2, 2048, 4095, "A");
    gpt_entry(entries, 128, 1, 0xa2, 3000, 5000, "B");
    put32(header + 88, part_crc32(entries, 128 * 128));
    put32(header + 16, 0);
    put32(header + 16, part_crc32(header, 92));
    check_int("gpt overlap refused", part_parse_gpt(header, 512, entries, 128 * 128, 16384, &r), PART_E_OVERLAP);
    check_int("gpt overlap not valid", r.valid, 0);

    /* empty table is valid and empty */
    build_gpt(header, entries, 16384, 128, 128, 0, 0);
    put32(header + 16, 0);
    put32(header + 16, part_crc32(header, 92));
    check_int("empty gpt parses", part_parse_gpt(header, 512, entries, 128 * 128, 16384, &r), PART_OK);
    check_int("empty gpt count", r.count, 0);
    check_int("empty gpt valid", r.valid, 1);

    check_str("scheme mbr", part_scheme_name(PART_SCHEME_MBR), "mbr");
    check_str("scheme gpt", part_scheme_name(PART_SCHEME_GPT), "gpt");
    check_str("part status signature", part_status_name(PART_E_SIGNATURE), "signature");
    check_str("part status overlap", part_status_name(PART_E_OVERLAP), "overlap");
}


/* ------------------------------------------------------------- fat32 */

#define TDISK_SECTORS 32u

struct tdisk {
    u8 *data;
    u32 sectors;
    u32 read_limit;   /* refuse reads at or beyond this sector (0 = no limit) */
};

static int tdisk_read(void *ctx, u32 lba, u32 count, void *buf)
{
    struct tdisk *d = (struct tdisk *)ctx;
    if (lba + count > d->sectors)
        return -1;
    if (d->read_limit && lba + count > d->read_limit)
        return -1;
    memcpy(buf, d->data + lba * 512u, count * 512u);
    return 0;
}

static void w16(u8 *p, u32 v) { p[0] = (u8)v; p[1] = (u8)(v >> 8); }
static void w32(u8 *p, u32 v) { w16(p, v); w16(p + 2, v >> 16); }

/* A minimal but structurally valid FAT32 volume: 32 sectors, 4 reserved,
 * two 1-sector FATs, 1 sector per cluster, root cluster 2, 26 data clusters.
 * Deliberately below the 65,525-cluster advisory; the parser is structural,
 * which is exactly what these tests pin down. */
static void tdisk_init(struct tdisk *d, u32 sectors)
{
    memset(d, 0, sizeof(*d));
    d->sectors = sectors;
    d->data = (u8 *)calloc(sectors, 512u);
    u8 *bs = d->data;
    bs[510] = 0x55;
    bs[511] = 0xaa;
    w16(bs + 11, 512);
    bs[13] = 1;
    w16(bs + 14, 4);
    bs[16] = 2;
    w16(bs + 17, 0);
    w16(bs + 19, 0);
    bs[21] = 0xf8;
    w16(bs + 22, 0);
    w32(bs + 32, sectors);
    w32(bs + 36, 1);
    w16(bs + 40, 0);
    w16(bs + 42, 0);
    w32(bs + 44, 2);
    w16(bs + 48, 1);
    w16(bs + 50, 0);
    bs[66] = 0x29;
    memcpy(bs + 71, "PC5FS      ", 11);
    memcpy(bs + 82, "FAT32   ", 8);
}

static void fat_set(struct tdisk *d, u32 cluster, u32 value)
{
    for (u32 copy = 0; copy < 2; copy++)
        w32(d->data + (4u + copy) * 512u + cluster * 4u, value);
}

static void tfree(struct tdisk *d)
{
    free(d->data);
    d->data = NULL;
}

/* Every test starts from the pristine volume; the struct holds a pointer, so a
 * plain assignment would share (and let one test corrupt) the same buffer. */
static void tclone(struct tdisk *dst, const struct tdisk *src)
{
    dst->sectors = src->sectors;
    dst->read_limit = src->read_limit;
    dst->data = (u8 *)malloc(src->sectors * 512u);
    memcpy(dst->data, src->data, src->sectors * 512u);
}

static u8 *tdir(struct tdisk *d) { return d->data + 6u * 512u; }

static void tdir_entry(struct tdisk *d, int index, const char name[11], u8 attr,
                       u32 cluster, u32 size)
{
    u8 *e = tdir(d) + index * 32;
    memcpy(e, name, 11);
    e[11] = attr;
    w16(e + 20, (u16)(cluster >> 16));
    w16(e + 26, (u16)cluster);
    w32(e + 28, size);
}

static u8 *tcluster(struct tdisk *d, u32 cluster)
{
    return d->data + (6u + (cluster - 2u)) * 512u;
}

static void test_fat32(void)
{
    struct tdisk d = {0};
    struct fat32_io io;
    struct fat32_volume v;
    u8 buf[4096];
    u32 len = 0;

    io.read = tdisk_read;
    io.ctx = &d;

    /* ---- a valid mount ---- */
    tdisk_init(&d, TDISK_SECTORS);
    fat_set(&d, 0, 0x0ffffff8u);
    fat_set(&d, 1, 0x0fffffffu);
    check_int("fat32 mount", fat32_mount(&io, 0, d.sectors, &v), FAT32_OK);
    check_int("fat32 cluster count", (long)v.cluster_count, 26);
    check_int("fat32 first data sector", (long)v.first_data_sector, 6);
    check_int("fat32 root cluster", (long)v.root_cluster, 2);
    check_int("fat32 bytes per sector", (long)v.bytes_per_sector, 512);
    check_str("fat32 label", v.label, "PC5FS");
    check_str("fat32 fs type", v.fs_type, "FAT32");

    struct tdisk base;
    tdisk_init(&base, TDISK_SECTORS);
    fat_set(&base, 0, 0x0ffffff8u);
    fat_set(&base, 1, 0x0fffffffu);

    /* ---- BPB and geometry refusals ---- */
    tfree(&d);
    tclone(&d, &base);
    d.data[510] = 0;
    check_int("fat32 missing signature", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_SIGNATURE);

    tfree(&d);
    tclone(&d, &base);
    w16(d.data + 11, 1024);
    check_int("fat32 1024-byte sectors refused", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_BPB);

    tfree(&d);
    tclone(&d, &base);
    d.data[13] = 3;
    check_int("fat32 non-power-of-two cluster", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_BPB);

    tfree(&d);
    tclone(&d, &base);
    w16(d.data + 14, 0);
    check_int("fat32 zero reserved sectors", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_BPB);

    tfree(&d);
    tclone(&d, &base);
    d.data[16] = 5;
    check_int("fat32 five FATs refused", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_BPB);

    tfree(&d);
    tclone(&d, &base);
    d.data[21] = 0;
    check_int("fat32 bad media byte", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_BPB);

    tfree(&d);
    tclone(&d, &base);
    w16(d.data + 42, 0x0100);
    check_int("fat32 version 1.0 refused", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_NOT_FAT32);

    tfree(&d);
    tclone(&d, &base);
    w16(d.data + 17, 512);
    check_int("fat16 root entries not fat32", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_NOT_FAT32);

    tfree(&d);
    tclone(&d, &base);
    w16(d.data + 22, 1);
    check_int("fat16 size field not fat32", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_NOT_FAT32);

    tfree(&d);
    tclone(&d, &base);
    memcpy(d.data + 82, "FAT16   ", 8);
    check_int("fat16 type string refused", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_NOT_FAT32);

    tfree(&d);
    tclone(&d, &base);
    w32(d.data + 32, 33);
    check_int("fat32 larger than partition", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_GEOMETRY);

    tfree(&d);
    tclone(&d, &base);
    w32(d.data + 44, 0);
    check_int("fat32 root cluster zero", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_GEOMETRY);

    tfree(&d);
    tclone(&d, &base);
    w32(d.data + 44, 28);
    check_int("fat32 root cluster past end", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_GEOMETRY);

    tfree(&d);
    tclone(&d, &base);
    w16(d.data + 48, 4);
    check_int("fat32 fsinfo outside reserved", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_BPB);

    tfree(&d);
    tclone(&d, &base);
    w16(d.data + 50, 4);
    check_int("fat32 backup outside reserved", fat32_mount(&io, 0, d.sectors, &v), FAT32_E_BPB);

    /* FAT too small to describe the clusters it claims */
    struct tdisk big;
    tdisk_init(&big, 300);
    struct fat32_io io_big = { .read = tdisk_read, .ctx = &big };
    check_int("fat32 FAT capacity enforced", fat32_mount(&io_big, 0, big.sectors, &v), FAT32_E_GEOMETRY);
    free(big.data);

    /* ---- directory and file reads ---- */
    tfree(&d);
    tclone(&d, &base);
    tdir_entry(&d, 0, "PC5FS      ", 0x08, 0, 0);           /* volume label  */
    tdir_entry(&d, 1, "TEST    TXT", 0x20, 3, 100);         /* small file     */
    tdir_entry(&d, 2, "SUBDIR     ", 0x10, 5, 0);           /* directory      */
    tdir_entry(&d, 3, "DELETED TXT", 0x20, 6, 10);
    tdir(&d)[3 * 32] = 0xe5;                                /* deleted        */
    tdir(&d)[4 * 32 + 11] = 0x0f;                           /* LFN record     */
    memcpy(tdir(&d) + 4 * 32, "LONGNA~1TXT", 11);
    tdir_entry(&d, 5, "EXACT   BIN", 0x20, 7, 512);
    tdir_entry(&d, 6, "MULTI   BIN", 0x20, 8, 1500);
    tdir_entry(&d, 7, "EMPTY   TXT", 0x20, 0, 0);
    tdir_entry(&d, 8, "BIG     BIN", 0x20, 9, 4000);

    check_int("fat32 remount with entries", fat32_mount(&io, 0, d.sectors, &v), FAT32_OK);

    for (u32 i = 0; i < 100; i++)
        tcluster(&d, 3)[i] = (u8)(i + 1);
    check_int("fat32 read small file", fat32_read_file(&io, &v, "TEST.TXT", buf, sizeof(buf), &len), FAT32_OK);
    check_int("fat32 small length", (long)len, 100);
    check_int("fat32 small first byte", buf[0], 1);
    check_int("fat32 small last byte", buf[99], 100);

    check_int("fat32 lookup is case-insensitive", fat32_find(&io, &v, "test.txt", NULL, NULL, NULL), FAT32_OK);
    check_int("fat32 missing file", fat32_read_file(&io, &v, "NOPE.TXT", buf, sizeof(buf), &len), FAT32_E_NO_FILE);
    check_int("fat32 long base name refused", fat32_find(&io, &v, "TOOLONGNAME.TXT", NULL, NULL, NULL), FAT32_E_NAME);
    check_int("fat32 illegal character refused", fat32_find(&io, &v, "A?.TXT", NULL, NULL, NULL), FAT32_E_NAME);
    check_int("fat32 directory is not a file", fat32_read_file(&io, &v, "SUBDIR", buf, sizeof(buf), &len), FAT32_E_IS_DIR);
    check_int("fat32 deleted entry ignored", fat32_read_file(&io, &v, "DELETED.TXT", buf, sizeof(buf), &len), FAT32_E_NO_FILE);
    check_int("fat32 long-name record is not a file",
              fat32_read_file(&io, &v, "LONGNA~1.TXT", buf, sizeof(buf), &len), FAT32_E_NO_FILE);

    for (u32 i = 0; i < 512; i++)
        tcluster(&d, 7)[i] = (u8)i;
    check_int("fat32 read exact cluster", fat32_read_file(&io, &v, "EXACT.BIN", buf, sizeof(buf), &len), FAT32_OK);
    check_int("fat32 exact length", (long)len, 512);
    check_int("fat32 exact last byte", buf[511], 255);

    /* 1500 bytes over clusters 8, 10, 11 (a gap makes the chain non-contiguous) */
    fat_set(&d, 8, 10);
    fat_set(&d, 10, 11);
    fat_set(&d, 11, 0x0fffffffu);
    u32 multi_clusters[3] = {8, 10, 11};
    for (u32 c = 0; c < 3; c++)
        for (u32 i = 0; i < 512; i++)
            tcluster(&d, multi_clusters[c])[i] = (u8)(c * 512 + i);
    check_int("fat32 multi-cluster read", fat32_read_file(&io, &v, "MULTI.BIN", buf, sizeof(buf), &len), FAT32_OK);
    check_int("fat32 multi length", (long)len, 1500);
    check_int("fat32 multi boundary byte", buf[512], 0);  /* second cluster, first byte */
    check_int("fat32 multi tail byte", buf[1499], (u8)(1499 - 1024));

    check_int("fat32 zero-length file", fat32_read_file(&io, &v, "EMPTY.TXT", buf, sizeof(buf), &len), FAT32_OK);
    check_int("fat32 zero-length size", (long)len, 0);

    check_int("fat32 buffer too small is refused",
              fat32_read_file(&io, &v, "BIG.BIN", buf, 1024, &len), FAT32_E_SPACE);

    /* ---- chain corruption ---- */
    tfree(&d);
    tclone(&d, &base);
    tdir_entry(&d, 0, "BROKEN  BIN", 0x20, 3, 3000);
    fat32_mount(&io, 0, d.sectors, &v);

    fat_set(&d, 3, 0);
    check_int("fat32 free cluster in chain", fat32_read_file(&io, &v, "BROKEN.BIN", buf, sizeof(buf), &len), FAT32_E_FREE);

    fat_set(&d, 3, 0x0ffffff7u);
    check_int("fat32 bad cluster in chain", fat32_read_file(&io, &v, "BROKEN.BIN", buf, sizeof(buf), &len), FAT32_E_BAD);

    fat_set(&d, 3, 0x0ffffff0u);
    check_int("fat32 reserved cluster in chain", fat32_read_file(&io, &v, "BROKEN.BIN", buf, sizeof(buf), &len), FAT32_E_RESERVED);

    fat_set(&d, 3, 30);
    check_int("fat32 cluster past volume", fat32_read_file(&io, &v, "BROKEN.BIN", buf, sizeof(buf), &len), FAT32_E_CLUSTER);

    /* 3 -> 4 -> 3: a two-cluster cycle */
    fat_set(&d, 3, 4);
    fat_set(&d, 4, 3);
    check_int("fat32 cyclic chain", fat32_read_file(&io, &v, "BROKEN.BIN", buf, sizeof(buf), &len), FAT32_E_LOOP);

    fat_set(&d, 3, 0x0fffffffu); /* chain ends, but the file claims 1500 bytes */
    check_int("fat32 short chain", fat32_read_file(&io, &v, "BROKEN.BIN", buf, sizeof(buf), &len), FAT32_E_CHAIN);

    /* entry with a size but no cluster */
    tfree(&d);
    tclone(&d, &base);
    tdir_entry(&d, 0, "NOSPACE BIN", 0x20, 0, 100);
    fat32_mount(&io, 0, d.sectors, &v);
    check_int("fat32 file with no cluster", fat32_read_file(&io, &v, "NOSPACE.BIN", buf, sizeof(buf), &len), FAT32_E_CLUSTER);

    /* truncated image: the directory sector itself is unreadable */
    tfree(&d);
    tclone(&d, &base);
    tdir_entry(&d, 0, "TEST    TXT", 0x20, 3, 100);
    fat32_mount(&io, 0, d.sectors, &v);
    d.read_limit = 6; /* sectors 0..5 readable, cluster data (6+) is not */
    check_int("fat32 truncated image", fat32_read_file(&io, &v, "TEST.TXT", buf, sizeof(buf), &len), FAT32_E_IO);
    d.read_limit = 0;

    /* ---- listing ---- */
    tfree(&d);
    tclone(&d, &base);
    tdir_entry(&d, 0, "PC5FS      ", 0x08, 0, 0);
    tdir_entry(&d, 1, "TEST    TXT", 0x20, 3, 100);
    tdir_entry(&d, 2, "DELETED TXT", 0x20, 6, 10);
    tdir(&d)[2 * 32] = 0xe5;
    tdir(&d)[3 * 32] = 0x41; /* LFN ordinal */
    tdir(&d)[3 * 32 + 11] = 0x0f;
    memcpy(tdir(&d) + 3 * 32 + 1, "O T H E R", 10);
    tdir_entry(&d, 4, "OTHER   BIN", 0x20, 4, 10);
    fat32_mount(&io, 0, d.sectors, &v);
    struct fat32_entry list[8];
    int count = 0;
    check_int("fat32 list root", fat32_list_root(&io, &v, list, 8, &count), FAT32_OK);
    check_int("fat32 list count", count, 2);
    check_str("fat32 list first name", list[0].name, "TEST.TXT");
    check_int("fat32 list first size", (long)list[0].size, 100);
    check_str("fat32 list second name", list[1].name, "OTHER.BIN");

    tfree(&d);
    free(base.data);

    check_str("fat32 status ok", fat32_status_name(FAT32_OK), "ok");
    check_str("fat32 status loop", fat32_status_name(FAT32_E_LOOP), "loop");
    check_str("fat32 status space", fat32_status_name(FAT32_E_SPACE), "space");
}

int main(void)
{
    test_format();
    test_block_rules();
    test_sha256();
    test_partitions();
    test_fat32();
    test_numbers();
    test_strings();
    test_memory_map();
    test_boot_contract();
    test_chain();
    printf("%s: %d checks, %d failures\n", failures ? "FAILED" : "PASSED", checks, failures);
    return failures ? 1 : 0;
}
