/*
 * PC5 kernel: shared types, boot contract and module interfaces.
 *
 * The boot contract is produced by boot/loader.S and patched by
 * tools/mkiso.py; every field here has a matching comment in those files.
 */
#ifndef PC5_H
#define PC5_H

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef signed int s32;

/* Length type for the memory primitives: u32 matches size_t in the 32-bit
 * kernel, and host unit tests build the same code with the host size_t. */
#ifdef PC5_HOST_TEST
#include <stddef.h>
#define PC5_SIZE size_t
#else
#define PC5_SIZE u32
#endif

#define PC5_BOOT_MAGIC 0x42354350u /* 'P','C','5','B' */
#define PC5_PROTOCOL_VERSION 1

/* Fixed header at the very start of the kernel file, written by
 * kernel/start.S and checked by every loader and by tools/verify_iso.py:
 *   0  short jump over the header   (the kernel's first instruction)
 *   2  "PC5K" signature
 *   6  protocol version
 * The signature therefore sits at offset 2, not offset 0. */
#define PC5_KERNEL_SIG "PC5K"
#define PC5_KERNEL_SIG_OFFSET 2
#define PC5_KERNEL_VERSION_OFFSET 6
#define PC5_KERNEL_HEADER_SIZE 8

#define NULL ((void *)0)

/* ---------------------------------------------------------------- boot */
struct pc5_boot_info {
    u32 magic;             /* +0   PC5_BOOT_MAGIC                        */
    u32 version;           /* +4   protocol version                      */
    u32 boot_drive;        /* +8   BIOS drive number                     */
    u32 e820_addr;         /* +12  physical address of the E820 array    */
    u32 e820_count;        /* +16  number of 24-byte entries             */
    u32 vbe_mode_addr;     /* +20  physical address of the VBE mode info */
    u32 vbe_ok;            /* +24  1 when a linear framebuffer is active */
    u32 font_addr;         /* +28  256-glyph 8x16 font, 0 when absent    */
    u32 font_bytes_per_char; /* +32                                     */
    u32 pvd_addr;          /* +36  ISO9660 primary volume descriptor     */
    u32 pvd_ok;            /* +40  1 when the descriptor was readable    */
    u32 kernel_bytes;      /* +44  size of this kernel image             */
    u32 kernel_lba;        /* +48  ISO LBA of the kernel file            */
    char volume_id[24];    /* +52  ISO volume identifier                 */
    u32 iso_sectors;       /* +76  medium size in 2048-byte sectors      */
    char loader_version[16]; /* +80                                      */
    u32 desc_crc;          /* +96  boot descriptor CRC32                 */
    u32 flags;             /* +100 reserved                              */
};

/* The loader writes this structure at a fixed physical address; a layout
 * change without changing boot/loader.S would corrupt the boot silently, so
 * the offsets are checked at compile time. */
_Static_assert(__builtin_offsetof(struct pc5_boot_info, volume_id) == 52, "boot info layout");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, iso_sectors) == 76, "boot info layout");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, desc_crc) == 96, "boot info layout");
_Static_assert(sizeof(struct pc5_boot_info) == 104, "boot info layout");

/* VBE mode information block fields used here (VBE 2.0+ layout) */
struct vbe_mode_info {
    u16 attributes;      /* 0x00 */
    u8 win_a, win_b;     /* 0x02 */
    u16 granularity;     /* 0x04 */
    u16 win_size;        /* 0x06 */
    u16 win_aseg, win_bseg; /* 0x08 */
    u32 win_func;        /* 0x0c */
    u16 pitch;           /* 0x10 bytes per scan line */
    u16 width;           /* 0x12 */
    u16 height;          /* 0x14 */
    u8 char_w, char_h;   /* 0x16 */
    u8 planes, bpp;      /* 0x18, 0x19 */
    u8 banks;            /* 0x1a */
    u8 model;            /* 0x1b */
    u8 bank_size;        /* 0x1c */
    u8 image_pages;      /* 0x1d */
    u8 reserved0;        /* 0x1e */
    u8 red_mask_size, red_field_pos;      /* 0x1f, 0x20 */
    u8 green_mask_size, green_field_pos;  /* 0x21, 0x22 */
    u8 blue_mask_size, blue_field_pos;    /* 0x23, 0x24 */
    u8 reserved_mask_size, reserved_field_pos; /* 0x25, 0x26 */
    u8 direct_color_mode;                 /* 0x27 */
    u32 phys_base;                        /* 0x28 linear framebuffer */
} __attribute__((packed));

struct e820_entry {
    u64 base;
    u64 length;
    u32 type;
    u32 acpi_ext;
} __attribute__((packed));

/* --------------------------------------------------------------- utils */
void *memset(void *dst, int c, PC5_SIZE n);
void *memcpy(void *dst, const void *src, PC5_SIZE n);
int strcmp(const char *a, const char *b);
int memcmp(const void *a, const void *b, PC5_SIZE n);
int strncmp(const char *a, const char *b, PC5_SIZE n);
PC5_SIZE strlen(const char *s);
int str_contains(const char *hay, const char *needle);
void str_copy(char *dst, const char *src, u32 max);
void str_append(char *dst, const char *src, u32 max);
void u32_to_str(u32 value, char *out, int base);
void u64_to_str(u64 value, char *out, u32 base);
void u32_to_hex(u32 value, char *out, int digits);
int pc5_snprintf(char *buf, u32 max, const char *fmt, ...);
int pc5_vsnprintf(char *buf, u32 max, const char *fmt, __builtin_va_list ap);

static inline void outb(u16 port, u8 value)
{
    __asm__ volatile("outb %0, %1" : : "a"(value), "Nd"(port));
}
static inline u8 inb(u16 port)
{
    u8 v;
    __asm__ volatile("inb %1, %0" : "=a"(v) : "Nd"(port));
    return v;
}
/* 16-bit port access.  The ATA data register is a 16-bit register at the base
 * port; reading or writing it as two byte accesses hits the neighbouring
 * error/features register instead (this is exactly the bug that made the first
 * ATAPI read return garbage). */
static inline void outw(u16 port, u16 value)
{
    __asm__ volatile("outw %0, %1" : : "a"(value), "Nd"(port));
}
static inline u16 inw(u16 port)
{
    u16 v;
    __asm__ volatile("inw %1, %0" : "=a"(v) : "Nd"(port));
    return v;
}

static inline void io_wait(void) { outb(0x80, 0); }
static inline void cli(void) { __asm__ volatile("cli"); }
static inline void sti(void) { __asm__ volatile("sti"); }
static inline void hlt(void) { __asm__ volatile("hlt"); }

/* ------------------------------------------------------------- console */
#define COL_BLACK 0
#define COL_BLUE 1
#define COL_GREEN 2
#define COL_CYAN 3
#define COL_RED 4
#define COL_MAGENTA 5
#define COL_BROWN 6
#define COL_LGREY 7
#define COL_DGREY 8
#define COL_LBLUE 9
#define COL_LGREEN 10
#define COL_LCYAN 11
#define COL_LRED 12
#define COL_LMAGENTA 13
#define COL_YELLOW 14
#define COL_WHITE 15

struct console {
    int fb;          /* 1 = framebuffer, 0 = VGA text */
    u32 phys;        /* framebuffer physical address */
    u32 width, height, pitch;
    u32 bpp;
    u8 *font;        /* 8x16 glyphs */
    u32 font_bpc;
    int cols, rows;
    int x, y;
    u8 fg, bg;
    int serial_mirror;
    int wrap;        /* 0 = clip at the right edge instead of wrapping */
};
extern struct console con;

void console_init(const struct pc5_boot_info *bi);
void con_clear(void);
void con_putc(char c);
void con_puts(const char *s);
void con_printf(const char *fmt, ...);
void con_attr(u8 fg, u8 bg);
void con_gotoxy(int x, int y);
void con_wrap(int enable);
void con_fill(int x, int y, int w, int h, u8 color);
void con_hline(int x, int y, int w, u8 color, const char *text);
void con_panic(const char *msg);
void con_snapshot(char *buf, u32 max, int *rows_out, int *cols_out);
int con_framebuffer_selftest(void);

/* -------------------------------------------------------------- serial */
void serial_init(void);
void serial_putc(char c);
char serial_getc_nonblock(void);
void serial_puts(const char *s);

/* ----------------------------------------------------------- inventory */
struct cpu_info {
    char vendor[13];
    char brand[49];
    u32 family, model, stepping;
    u32 logical;
    u32 features_ecx, features_edx, ext_edx;
    u32 phys_bits, virt_bits;
    u32 tsc_hz_guess;
};
void cpu_detect(struct cpu_info *out);
void cpu_report(const struct cpu_info *ci, char *out, u32 max);

struct mem_info {
    u64 total_bytes;
    u64 usable_bytes;
    u64 upper_bytes;
    u32 entries;
    u32 top_address_mb;
};
void mem_detect(const struct pc5_boot_info *bi, struct mem_info *out);
const struct e820_entry *mem_entries(void);

struct pci_device {
    u8 bus, slot, func;
    u16 vendor, device;
    u8 class_code, subclass, prog_if, revision;
    u16 subsystem_vendor, subsystem_device;
};
#define PCI_MAX_DEVICES 96
struct pci_report {
    struct pci_device dev[PCI_MAX_DEVICES];
    u32 count;
    u32 gpu_count, network_count, storage_count, audio_count, usb_count;
    char gpu_desc[64];
    char net_desc[64];
};
void pci_scan(struct pci_report *out);
const char *pci_class_name(u8 class_code, u8 subclass);

struct smbios_info {
    int present;
    int version_major, version_minor;
    char bios_vendor[32];
    char bios_version[32];
    char bios_date[16];
    char sys_vendor[32];
    char product[48];
    char board[32];
    char cpu_version[48];
    u32 cpu_cores, cpu_threads;
    u32 memory_devices;
    u64 memory_total_mb;
};
void smbios_scan(struct smbios_info *out);

struct acpi_info {
    int present;
    u8 revision;
    char oem_id[7];
    char oem_table_id[9];
    u32 table_count;
    u32 rsdt_addr;
    u16 fadt_length;
    char table_names[16][5];
};
void acpi_scan(struct acpi_info *out);

/* ------------------------------------------------------------- storage */
/* Two transfer granularities, deliberately distinct (ADR-013):
 *
 *   PC5_SECTOR_SIZE  512-byte disk sectors  -> ATA READ/WRITE SECTORS
 *   PC5_BLOCK_SIZE   2048-byte optical blocks -> ATAPI READ(10) and ISO9660
 *
 * Mixing them silently is how a driver corrupts a disk.  Every function below
 * names which one it uses: `*_sectors` is always 512 bytes, `*_blocks` is
 * always 2048 bytes and only ever talks to the boot medium.
 */
#define PC5_SECTOR_SIZE 512
#define PC5_BLOCK_SIZE 2048
#define ATA_MAX_DRIVES 4
/* Bound the work between two polls: the ATA sector-count register is 8-bit
 * (0 would mean 256), and a small chunk keeps timeouts meaningful. */
#define ATA_MAX_SECTORS_PER_CMD 32

enum blk_status {
    BLK_OK = 0,
    BLK_E_NO_DEVICE = -1,  /* index out of range, or nothing on that port      */
    BLK_E_READ_ONLY = -2,  /* ATAPI packet device: never a write target        */
    BLK_E_RANGE = -3,      /* capacity unknown, LBA outside the device, overflow */
    BLK_E_COUNT = -4,      /* zero or nonsensical sector count                 */
    BLK_E_ALIGN = -5,      /* NULL or odd-address buffer                       */
    BLK_E_IO = -6,         /* device reported ERR/DF                           */
    BLK_E_TIMEOUT = -7,    /* bounded polling expired                          */
    BLK_E_VERIFY = -8      /* read-back did not match what was written         */
};
const char *blk_status_name(int status);

/* Pure request validation (host unit-tested).  A write to an ATAPI device is
 * refused before anything else is considered, and an unknown capacity fails
 * closed rather than being guessed. */
int blk_check_disk_request(u32 lba, u32 count, u32 capacity, int writable, int atapi);
u32 blk_chunk_sectors(u32 remaining, u32 max_per_command);

struct storage_report {
    int drives;          /* drives found on the legacy ATA channels */
    int selected;        /* index of the drive used, -1 if none */
    int atapi;           /* selected drive is an ATAPI packet device */
    char model[41];
    int mounted;         /* ISO9660 volume mounted through the kernel driver */
    char volume[33];
    u32 blocks;
    int file_ok;         /* the kernel read a file back from the medium */
    u32 file_bytes;
    char file_name[16];
    int file_signature;  /* that file carries the expected signature */
    int error;           /* first failing step, for diagnostics */
};
struct ata_drive {
    int present;
    int atapi;
    u16 io;
    u16 ctl;
    u8 drive;
    u32 sectors;
    char model[41];
};
int ata_init(void);
int ata_selected(void);
const struct ata_drive *ata_drive(int index);
const struct storage_report *storage_report(void);
int ata_read_blocks(u32 lba, u32 count, void *buf);

/* Disk-sector interface (512-byte).  `device` is an explicit index from
 * ata_drive(); nothing here falls back to the selected boot device. */
int ata_read_sectors(int device, u32 lba, u32 count, void *buf);
int ata_write_sectors(int device, u32 lba, u32 count, const void *buf);
int ata_writable_device(void);

/* Kernel write self-test, invoked explicitly (serial command `wtest`): it
 * writes a deterministic pattern to a reserved scratch window on the first
 * writable ATA disk, reads it back and verifies it.  It is never run on an
 * ordinary boot path. */
#define WTEST_SCRATCH_LBA 2048u
#define WTEST_SCRATCH_SECTORS 8u
#define WTEST_BYTES (WTEST_SCRATCH_SECTORS * PC5_SECTOR_SIZE)
int storage_write_selftest(void);
int storage_write_test_region_ok(u32 lba, u32 count);
int storage_selftest_state(void);          /* 0 not run, 1 ok, -1 failed */
const char *storage_selftest_sha256(void); /* 64 hex chars, or "" */
int storage_selftest_device(void);         /* index or -1 */

/* SHA-256 (FIPS 180-4), used for write-verification evidence and, later, for
 * update manifests. */
struct sha256_ctx {
    u32 state[8];
    u64 bytes;
    u8 block[64];
    u32 used;
};
void sha256_init(struct sha256_ctx *ctx);
void sha256_update(struct sha256_ctx *ctx, const void *data, u32 len);
void sha256_final(struct sha256_ctx *ctx, u8 out[32]);
void sha256_hex(const u8 digest[32], char out[65]);

/* ------------------------------------------------------- partitions */
/* Read-only discovery of MBR and GPT partition tables on a disk.  The
 * parsers are pure functions over buffers, so the host tests can prove them
 * against synthetic tables; only part_scan() touches a device, and it only
 * ever *reads* 512-byte sectors through ata_read_sectors().  Nothing here
 * mounts, writes or formats anything (see docs/ROADMAP.md M1e). */
#define PART_MAX 16
#define PART_NAME_LEN 37
#define PART_GUID_HEX_LEN 33

enum part_scheme {
    PART_SCHEME_NONE = 0,
    PART_SCHEME_MBR = 1,
    PART_SCHEME_GPT = 2
};

enum part_status {
    PART_OK = 0,
    PART_E_NO_DEVICE = -1, /* no such drive index                             */
    PART_E_ATAPI = -2,     /* optical device: no partition table to read      */
    PART_E_IO = -3,        /* the device refused the read                     */
    PART_E_SIGNATURE = -4, /* neither 55 AA nor "EFI PART" where required     */
    PART_E_CRC = -5,       /* GPT header or entry-array CRC mismatch          */
    PART_E_TABLE = -6,     /* geometry outside the spec bounds                */
    PART_E_RANGE = -7,     /* partition extends past the device or overlaps   */
    PART_E_OVERLAP = -8
};

struct pc5_partition {
    u8 scheme;                /* enum part_scheme                            */
    u8 index;                 /* 1-based, as the table numbers it            */
    u8 type;                  /* MBR type code (0 for GPT)                   */
    u8 type_guid[16];         /* raw GPT type GUID bytes                     */
    u32 first_lba;            /* 512-byte sectors                            */
    u32 sectors;
    char name[PART_NAME_LEN]; /* GPT UTF-16LE name reduced to ASCII, or ""    */
};

struct part_report {
    int device;               /* disk index scanned, -1 if none              */
    int scheme;               /* enum part_scheme                            */
    int count;                /* partitions recorded                         */
    int valid;                /* 1 only if the whole table parsed cleanly     */
    int error;                /* enum part_status of the first failure       */
    u32 capacity;             /* device capacity in 512-byte sectors         */
    struct pc5_partition parts[PART_MAX];
};

/* Pure parsers (host unit-tested).  `capacity` is in 512-byte sectors. */
int part_parse_mbr(const u8 sector[512], u32 capacity, struct part_report *out);
int part_parse_gpt(const u8 *header, u32 header_bytes, const u8 *entries,
                   u32 entries_bytes, u32 capacity, struct part_report *out);
u32 part_crc32(const void *data, u32 len);
void part_guid_hex(const u8 guid[16], char out[PART_GUID_HEX_LEN]);

/* Device layer: reads the table from `device` and caches the result. */
const struct part_report *part_scan(int device);
const struct part_report *part_report(void);
const char *part_scheme_name(int scheme);
const char *part_status_name(int status);

int iso_mount(void);
int iso_mounted(void);
const char *iso_volume_id(void);
u32 iso_block_count(void);
int iso_root_find(const char *name, u32 *lba_out, u32 *size_out);
u32 iso_read_file(const char *name, void *buf, u32 max);

/* firmware probe ------------------------------------------------------ */
struct fw_report {
    int supplied;
    int identified;
    u32 size_bytes;
    char kind[32];
    char note[128];
    u32 sha256_prefix;
};

/* ------------------------------------------------------- compatibility */
enum chain_state {
    CHAIN_OK = 0,
    CHAIN_PARTIAL = 1,
    CHAIN_BLOCKED = 2,
    CHAIN_NOT_STARTED = 3,
    CHAIN_NOT_APPLICABLE = 4,
};

struct chain_stage {
    const char *name;
    enum chain_state state;
    const char *evidence;
};

#define CHAIN_STAGE_COUNT 13
void chain_evaluate(const struct pc5_boot_info *bi, const struct cpu_info *cpu,
                    const struct mem_info *mem, const struct pci_report *pci,
                    const struct smbios_info *smb, const struct acpi_info *acpi,
                    const struct fw_report *fw, int kbd_ok,
                    const struct storage_report *storage);
const struct chain_stage *chain_stages(void);
const char *chain_state_name(enum chain_state s);
u8 chain_state_colour(enum chain_state s);
int chain_first_blocked(void);

/* ------------------------------------------------------------- drivers */
int kbd_init(void);
int kbd_read(u8 *code, int *extended);
int kbd_wait(u32 timeout_ticks, u8 *code, int *extended);
u32 pit_ticks(void);
u32 pc5_ms_since(u32 start);
void pit_init(void);
u32 rdtsc_low(void);

/* ------------------------------------------------------------------ ui */
void ui_run(const struct pc5_boot_info *bi, struct cpu_info *cpu, struct mem_info *mem,
            struct pci_report *pci, struct smbios_info *smb, struct acpi_info *acpi,
            struct fw_report *fw, int kbd_ok, int kbd_error);
void pc5_log(const char *fmt, ...);

void fw_probe(const struct pc5_boot_info *bi, struct fw_report *out);

/* build identity, provided by the Makefile */
#ifndef PC5_VERSION
#define PC5_VERSION "0.1.0-dev"
#endif
#ifndef PC5_BUILD_ID
#define PC5_BUILD_ID "local"
#endif
#ifndef PC5_BUILD_DATE
#define PC5_BUILD_DATE "unknown"
#endif

#endif /* PC5_H */
