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

int main(void)
{
    test_format();
    test_block_rules();
    test_sha256();
    test_partitions();
    test_numbers();
    test_strings();
    test_memory_map();
    test_chain();
    printf("%s: %d checks, %d failures\n", failures ? "FAILED" : "PASSED", checks, failures);
    return failures ? 1 : 0;
}
