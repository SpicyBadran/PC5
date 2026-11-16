/*
 * PC5 UEFI loader.
 *
 * Booted by firmware as \EFI\BOOT\BOOTX64.EFI from the FAT image that
 * tools/mkiso.py places in the El Torito UEFI entry.  It does for UEFI what
 * boot/loader.S does for BIOS: describe the machine, load the kernel, and
 * enter it in 32-bit protected mode with EBX pointing at a valid
 * struct pc5_boot_info.
 *
 * What this loader does *not* do is invent hardware.  Everything it reports
 * comes from firmware and is labelled as such:
 *
 *   memory   the UEFI memory map, converted to the E820 layout the kernel
 *            already understands (conventional memory -> type 1, everything
 *            else -> type 2 reserved).  Loader data is deliberately *not*
 *            called usable: it holds this application and the kernel buffer.
 *   display  GOP.  A synthetic VBE mode-info block describes the linear
 *            framebuffer so the kernel takes its framebuffer path.  font_addr
 *            is 0 because firmware hands over no VGA font, and the kernel
 *            must not draw glyphs it does not have.
 *   medium   the boot volume is opened through EFI_SIMPLE_FILE_SYSTEM and a
 *            known file is read back as proof the medium is readable.  The
 *            ISO9660 descriptor itself is *not* reachable through the file
 *            protocol, so pvd_ok stays 0 here; the kernel reads the
 *            descriptor itself when it probes storage.  Saying otherwise
 *            would be a lie that the next stage could not back up.
 *   kernel   \KERNEL.BIN read through the file protocol, signature checked,
 *            copied to 0x10000, entered.
 *
 * Every failure is a distinct code on screen and on the serial port followed
 * by a halt: this loader never falls through pretending a step worked.
 */
#include "efi.h"

/* The boot contract is the kernel's own header: if the BIOS loader, the
 * kernel and this loader ever disagree about an offset, this file stops
 * compiling instead of corrupting a boot at run time. */
#include "pc5.h"

/* ---------------------------------------------------------------- layout */
/* The kernel is linked at 1 MiB - 960 KiB and the BIOS loader loads it to the
 * same address, so the handover address is part of the contract. */
#define KERNEL_ADDR 0x10000u
#define KERNEL_LIMIT 0x8F000u          /* below the 0x9F000 handover stack */
#define BOOT_INFO_ADDR 0x4000u         /* same address the BIOS loader uses */
#define E820_BUF_ADDR 0x2000u
#define E820_MAX 40
#define VBE_MODE_ADDR 0x2800u
#define PVD_BUF_ADDR 0x3A00u
/* Everything the loader writes after firmware is gone lives in this window:
 * E820 at 0x2000, VBE, the PVD buffer, boot info at 0x4000, the kernel from
 * 0x10000, and the handover stack growing down from 0x9F000. */
#define HANDOVER_BASE 0x2000u
#define HANDOVER_LIMIT 0x9F000u

#define MAX_FILE_BYTES (4u * 1024u * 1024u)
#define LOADER_VERSION "uefi-0.2.0"

/* Offsets that must agree with kernel/include/pc5.h and boot/loader.S. */
_Static_assert(__builtin_offsetof(struct pc5_boot_info, boot_drive) == 8, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, e820_addr) == 12, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, e820_count) == 16, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, vbe_mode_addr) == 20, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, vbe_ok) == 24, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, font_addr) == 28, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, pvd_addr) == 36, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, kernel_bytes) == 44, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, kernel_lba) == 48, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, volume_id) == 52, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, iso_sectors) == 76, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, loader_version) == 80, "abi");
_Static_assert(__builtin_offsetof(struct pc5_boot_info, flags) == 100, "abi");
_Static_assert(sizeof(struct pc5_boot_info) == 104, "abi");
_Static_assert(__builtin_offsetof(struct vbe_mode_info, pitch) == 0x10, "vbe abi");
_Static_assert(__builtin_offsetof(struct vbe_mode_info, phys_base) == 0x28, "vbe abi");

/* The structures below are walked by the numbers the UEFI specification
 * assigns, so the offsets this loader relies on are pinned.  A wrong slot here
 * does not misbehave gently: it jumps into the middle of some other service. */
_Static_assert(__builtin_offsetof(struct efi_boot_services, handle_protocol) == 152, "boot services abi");
_Static_assert(__builtin_offsetof(struct efi_boot_services, locate_handle) == 176, "boot services abi");
_Static_assert(__builtin_offsetof(struct efi_boot_services, locate_device_path) == 184, "boot services abi");
_Static_assert(__builtin_offsetof(struct efi_boot_services, exit_boot_services) == 232, "boot services abi");
_Static_assert(__builtin_offsetof(struct efi_boot_services, set_watchdog_timer) == 256, "boot services abi");
_Static_assert(__builtin_offsetof(struct efi_system_table, con_out) == 64, "system table abi");
_Static_assert(__builtin_offsetof(struct efi_system_table, boot_services) == 96, "system table abi");
_Static_assert(__builtin_offsetof(struct efi_file, open) == 8, "file protocol abi");
_Static_assert(__builtin_offsetof(struct efi_file, read) == 32, "file protocol abi");
_Static_assert(sizeof(struct efi_loaded_image) == 96, "loaded image abi");
_Static_assert(__builtin_offsetof(struct efi_loaded_image, device_handle) == 24, "loaded image abi");
_Static_assert(__builtin_offsetof(struct efi_simple_file_system, open_volume) == 8, "fs abi");
_Static_assert(__builtin_offsetof(struct efi_gop, mode) == 24, "gop abi");
_Static_assert(__builtin_offsetof(struct efi_gop_mode, frame_buffer_base) == 24, "gop abi");

#define BOOT_INFO ((struct pc5_boot_info *)BOOT_INFO_ADDR)
#define E820 ((struct e820_entry *)E820_BUF_ADDR)
#define VBE ((struct vbe_mode_info *)VBE_MODE_ADDR)

/* provided by bootx64.S; both take ordinary SysV arguments */
void pc5_uefi_bridge(u64 entry, u64 boot_info) __attribute__((noreturn));
void pc5_uefi_halt(u64 status) __attribute__((noreturn));

/* ------------------------------------------------------- freestanding libc */
/* gcc must never reach for a hosted library, but it is allowed to turn a
 * memcpy loop into a call to memcpy, so the three symbols it can synthesise
 * are defined here.  PC5_SIZE is u32 in this build, matching pc5.h. */
void *memset(void *dst, int c, PC5_SIZE n)
{
    u8 *p = (u8 *)dst;
    while (n--)
        *p++ = (u8)c;
    return dst;
}

void *memcpy(void *dst, const void *src, PC5_SIZE n)
{
    u8 *d = (u8 *)dst;
    const u8 *s = (const u8 *)src;
    while (n--)
        *d++ = *s++;
    return dst;
}

int memcmp(const void *a, const void *b, PC5_SIZE n)
{
    const u8 *pa = (const u8 *)a, *pb = (const u8 *)b;
    while (n--) {
        if (*pa != *pb)
            return (int)*pa - (int)*pb;
        pa++;
        pb++;
    }
    return 0;
}

/* ---------------------------------------------------------------- serial */
#define COM1 0x3F8
static int serial_present;

/* A serial port is a nicety, not a requirement: probe the scratch register a
 * 16550 has and a floating port does not. */
static void uefi_serial_init(void)
{
    outb(COM1 + 1, 0x00);
    outb(COM1 + 3, 0x80);
    outb(COM1 + 0, 0x01);
    outb(COM1 + 1, 0x00);
    outb(COM1 + 3, 0x03);
    outb(COM1 + 2, 0xC7);
    outb(COM1 + 4, 0x03);
    outb(COM1 + 7, 0x5A);
    serial_present = inb(COM1 + 7) == 0x5A;
    outb(COM1 + 7, 0x00);
}

static void uefi_serial_putc(char c)
{
    int guard = 100000;
    if (!serial_present)
        return;
    while (!(inb(COM1 + 5) & 0x20) && guard--)
        ;
    outb(COM1, (u8)c);
}

static void uefi_serial_puts(const char *s)
{
    if (!serial_present)
        return;
    while (*s)
        uefi_serial_putc(*s++);
}

static void uefi_serial_put_dec(u64 value)
{
    char buf[21];
    int i = 20;
    buf[i] = 0;
    do {
        buf[--i] = (char)('0' + (u8)(value % 10));
        value /= 10;
    } while (value);
    uefi_serial_puts(&buf[i]);
}


/* -------------------------------------------------------------- console */
static struct efi_simple_text_output *conout;

/* Set once ExitBootServices has succeeded: from that moment the console
 * protocol is firmware memory the loader no longer owns and must not call. */
static int console_closed;

/* One write per sink per string.
 *
 * Firmware is free to build its console out of the same 16550 PC5 also writes
 * to - OVMF's ConSplitter does exactly that on a QEMU serial port - so a
 * character-at-a-time echo produced every character twice on the wire
 * ("PPCC55--UUEEFFII").  Writing the whole string to each sink keeps the log
 * readable: the line appears twice, in order, instead of interleaved. */
static void uefi_puts(const char *s)
{
    uefi_serial_puts(s);
    if (conout && !console_closed) {
        u16 glyph[64];
        while (*s) {
            u32 n = 0;
            while (*s && n < 63)
                glyph[n++] = (u16)(u8)*s++;
            glyph[n] = 0;
            conout->output_string(conout, glyph);
        }
    }
}

/* Trace lines are assembled in one buffer and emitted with a single write to
 * each sink, so firmware that mirrors its console to the same 16550 PC5 logs to
 * puts two complete, identical lines on the wire instead of two interleaved
 * character streams.  A machine-readable log is worth the 192 bytes. */
static char line_buf[192];
static u32 line_len;

static void line_reset(void)
{
    line_len = 0;
    line_buf[0] = 0;
}

static void line_add(const char *s)
{
    while (*s && line_len + 1 < sizeof(line_buf))
        line_buf[line_len++] = *s++;
    line_buf[line_len] = 0;
}

static void line_add_dec(u64 value)
{
    char buf[21];
    int i = 20;
    buf[i] = 0;
    do {
        buf[--i] = (char)('0' + (u8)(value % 10));
        value /= 10;
    } while (value);
    line_add(&buf[i]);
}

static void line_add_hex(u64 value, int digits)
{
    static const char hex[] = "0123456789abcdef";
    char buf[17];
    if (digits < 1 || digits > 16)
        digits = 16;
    for (int i = digits - 1; i >= 0; i--) {
        buf[i] = hex[value & 0xF];
        value >>= 4;
    }
    buf[digits] = 0;
    line_add(buf);
}

static void line_add_prefix(void)
{
    line_add("PC5-UEFI: ");
}

/* Every trace line is prefixed so it can be grepped in a screenshot and in
 * the serial log. */
static void trace(const char *label)
{
    line_reset();
    line_add_prefix();
    line_add(label);
    line_add("\r\n");
    uefi_puts(line_buf);
}

static void trace_dec(const char *label, u64 value)
{
    line_reset();
    line_add_prefix();
    line_add(label);
    line_add("=");
    line_add_dec(value);
    line_add("\r\n");
    uefi_puts(line_buf);
}

static void trace_hex(const char *label, u64 value)
{
    line_reset();
    line_add_prefix();
    line_add(label);
    line_add("=0x");
    line_add_hex(value, 8);
    line_add("\r\n");
    uefi_puts(line_buf);
}

/* Failure: a distinct code, a sentence a human can act on, then a halt. */
static void fail(const char *code, const char *reason, u64 status)
    __attribute__((noreturn));

static void fail(const char *code, const char *reason, u64 status)
{
    line_reset();
    line_add("\r\nPC5-UEFI FAILURE ");
    line_add(code);
    line_add(": ");
    line_add(reason);
    line_add(" (status=0x");
    line_add_hex(status, 16);
    line_add(")\r\n");
    uefi_puts(line_buf);
    uefi_serial_puts("PC5-UEFI-FAILURE ");
    uefi_serial_puts(code);
    uefi_serial_puts("\r\n");
    pc5_uefi_halt(status);
}

/* --------------------------------------------------------------- display */
static u32 frame_buffer;
static u32 frame_width, frame_height, frame_pitch;
static int frame_ok;

/* Only 32-bit GOP modes are accepted: a palettised mode cannot be described by
 * a VBE direct-colour block, and a 24-bit mode would need conversion the
 * kernel does not do.  GOP mode 0 (RGB) is therefore the supported path. */
static int gop_init(struct efi_system_table *st)
{
    struct efi_gop *gop = 0;
    if (EFI_ERROR(st->boot_services->handle_protocol(
            st->console_out_handle,
            (struct efi_guid *)&EFI_GRAPHICS_OUTPUT_PROTOCOL_GUID, (void **)&gop)))
        return 0;
    if (!gop || !gop->mode || !gop->mode->info)
        return 0;

    struct efi_gop_mode_info *info = gop->mode->info;
    if (info->pixel_format != 0 && info->pixel_format != 1)
        return 0;
    if (!info->horizontal_resolution || !info->vertical_resolution)
        return 0;
    if (!info->pixels_per_scan_line)
        return 0;
    if (!gop->mode->frame_buffer_base || !gop->mode->frame_buffer_size)
        return 0;
    /* the kernel addresses the framebuffer as a 32-bit physical address */
    if (gop->mode->frame_buffer_base > 0xFFFFFFFFULL)
        return 0;

    frame_buffer = (u32)gop->mode->frame_buffer_base;
    frame_width = info->horizontal_resolution;
    frame_height = info->vertical_resolution;
    frame_pitch = info->pixels_per_scan_line * 4;
    frame_ok = 1;

    /* Synthesise the VBE mode-info block the kernel console reads.  VBE has no
     * pixel-format field and the kernel always writes 0x00RRGGBB, which is
     * exactly GOP format 0.  For format 1 (BGR) the channel positions are
     * reported honestly; the kernel still draws, with red and blue swapped,
     * and that limitation is stated in the docs rather than papered over. */
    struct vbe_mode_info *vbe = VBE;
    memset(vbe, 0, sizeof(*vbe));
    vbe->attributes = 0x0081;          /* supported + linear framebuffer    */
    vbe->win_size = 64;
    vbe->pitch = (u16)(frame_pitch > 0xFFFF ? 0xFFFF : frame_pitch);
    vbe->width = (u16)(frame_width > 0xFFFF ? 0xFFFF : frame_width);
    vbe->height = (u16)(frame_height > 0xFFFF ? 0xFFFF : frame_height);
    vbe->char_w = 8;
    vbe->char_h = 16;
    vbe->planes = 1;
    vbe->bpp = 32;
    vbe->banks = 1;
    vbe->model = 6;                    /* direct colour                     */
    vbe->image_pages = 1;
    vbe->direct_color_mode = 1;
    if (info->pixel_format == 1) {
        vbe->red_mask_size = 8; vbe->red_field_pos = 16;
        vbe->green_mask_size = 8; vbe->green_field_pos = 8;
        vbe->blue_mask_size = 8; vbe->blue_field_pos = 0;
    } else {
        vbe->red_mask_size = 8; vbe->red_field_pos = 0;
        vbe->green_mask_size = 8; vbe->green_field_pos = 8;
        vbe->blue_mask_size = 8; vbe->blue_field_pos = 16;
    }
    vbe->phys_base = frame_buffer;

    trace_dec("display_width", frame_width);
    trace_dec("display_height", frame_height);
    trace_dec("display_pitch", frame_pitch);
    trace_dec("display_pixel_format", info->pixel_format);
    return 1;
}

/* ---------------------------------------------------------------- memory */
/* Firmware memory map -> E820 entries.  The mapping is stated in one place so
 * that the kernel, the tests and the docs can be checked against it.
 *
 * Two properties matter and both used to be silent:
 *
 *  - descriptors are merged when they are adjacent and of the same class, so a
 *    firmware that reports memory in many small pieces still yields the memory
 *    it actually has;
 *  - a map that does not fit is a failure, not a truncation.  Dropping entries
 *    would under-report usable memory and could hide a reserved region the
 *    kernel is about to write into.
 */
static u32 build_e820(u64 count, u32 desc_size, const u8 *map, int *overflow)
{
    u32 entries = 0;
    *overflow = 0;
    for (u64 i = 0; i < count; i++) {
        const struct efi_memory_descriptor *d =
            (const struct efi_memory_descriptor *)(map + i * desc_size);
        u64 length = d->number_of_pages * 4096u;
        if (!length)
            continue;
        u32 type = (d->type == EFI_CONVENTIONAL_MEMORY) ? 1u : 2u;
        if (entries && type == E820[entries - 1].type &&
            E820[entries - 1].base + E820[entries - 1].length == d->physical_start) {
            E820[entries - 1].length += length;
            continue;
        }
        if (entries == E820_MAX) {
            *overflow = 1;
            return entries;
        }
        E820[entries].base = d->physical_start;
        E820[entries].length = length;
        E820[entries].type = type;
        E820[entries].acpi_ext = 0;
        entries++;
    }
    return entries;
}

/* Is [base, base + length) entirely inside conventional memory?
 *
 * The loader hands the kernel a fixed load address, a fixed boot-info address
 * and a fixed stack, all in the first megabyte, and it copies the kernel there
 * *after* firmware has been given up.  Nothing checked that range against the
 * map firmware reported, so on a machine whose low memory is partly reserved
 * the copy would land in firmware memory and the fault would appear as a kernel
 * crash a long way from its cause.  Descriptors are not required to be sorted,
 * so the walk advances to the furthest covering descriptor each time. */
static int window_is_conventional(u64 count, u32 desc_size, const u8 *map,
                                  u64 base, u64 length)
{
    u64 end = base + length;
    u64 cursor = base;
    while (cursor < end) {
        u64 best = cursor;
        for (u64 i = 0; i < count; i++) {
            const struct efi_memory_descriptor *d =
                (const struct efi_memory_descriptor *)(map + i * desc_size);
            if (d->type != EFI_CONVENTIONAL_MEMORY)
                continue;
            u64 start = d->physical_start;
            u64 stop = start + d->number_of_pages * 4096u;
            if (start <= cursor && stop > cursor && stop > best)
                best = stop;
        }
        if (best == cursor)
            return 0;
        cursor = best;
    }
    return 1;
}

static u64 conventional_mib(u32 entries)
{
    u64 bytes = 0;
    for (u32 i = 0; i < entries; i++)
        if (E820[i].type == 1)
            bytes += E820[i].length;
    return bytes >> 20;
}

/* ----------------------------------------------------------------- files */
struct file_buffer {
    u8 *data;
    u64 size;
};

/* Learn a file's size from the firmware.  EFI_FILE_INFO is authoritative: a
 * SetPosition past EOF is accepted and the position is reported back exactly as
 * it was set, so "seek to the end to learn the size" returns the constant it
 * asked for rather than the file size.  OVMF does exactly that, which is how
 * this loader read a 65 KiB kernel as a 4 MiB file.  The seek trick survives
 * only as a fallback for firmware that does not implement GetInfo. */
static int file_size_of(struct efi_boot_services *bs, struct efi_file *file, u64 *size)
{
    u64 info_size = 0;
    efi_status status = file->get_info(file, (struct efi_guid *)&EFI_FILE_INFO_GUID,
                                       &info_size, 0);
    if (status == EFI_BUFFER_TOO_SMALL && info_size >= sizeof(struct efi_file_info) &&
        info_size <= 0x1000) {
        struct efi_file_info *info = 0;
        if (!EFI_ERROR(bs->allocate_pool(EFI_LOADER_DATA, info_size, (void **)&info)) && info) {
            status = file->get_info(file, (struct efi_guid *)&EFI_FILE_INFO_GUID,
                                    &info_size, info);
            if (!EFI_ERROR(status) && !(info->attribute & EFI_FILE_DIRECTORY)) {
                *size = info->file_size;
                bs->free_pool(info);
                return 0;
            }
            bs->free_pool(info);
        }
    }
    u64 position = 0;
    if (EFI_ERROR(file->set_position(file, MAX_FILE_BYTES)))
        return 2;
    if (EFI_ERROR(file->get_position(file, &position)))
        return 3;
    *size = position;
    return 0;
}

/* Read a whole file from the volume the application was loaded from.  The
 * buffer is EFI_LOADER_DATA, so the memory map tells the kernel it is in use;
 * it is never freed, because the kernel runs with boot services gone. */
static int read_file(struct efi_system_table *st, struct efi_file *root,
                     u16 *name, struct file_buffer *out)
{
    struct efi_file *file = 0;
    efi_status status = root->open(root, &file, name, EFI_FILE_MODE_READ, 0);
    if (EFI_ERROR(status) || !file)
        return 1;

    u64 size = 0;
    if (file_size_of(st->boot_services, file, &size) != 0) {
        root->close(file);
        return 2;
    }
    if (!size || size > MAX_FILE_BYTES) {
        trace_hex("read_size", size);
        root->close(file);
        return 4;
    }
    status = file->set_position(file, 0);
    if (EFI_ERROR(status)) {
        root->close(file);
        return 5;
    }

    void *buffer = 0;
    status = st->boot_services->allocate_pool(EFI_LOADER_DATA, size, &buffer);
    if (EFI_ERROR(status) || !buffer) {
        root->close(file);
        return 6;
    }
    /* Firmware may hand a file over in pieces; keep reading until the size
     * GetInfo reported has arrived. */
    u64 total = 0;
    while (total < size) {
        u64 chunk = size - total;
        status = file->read(file, &chunk, (u8 *)buffer + total);
        if (EFI_ERROR(status) || !chunk) {
            trace_hex("read_refused_at", total);
            trace_hex("read_status", status);
            root->close(file);
            st->boot_services->free_pool(buffer);
            return 7;
        }
        total += chunk;
    }
    root->close(file);
    out->data = (u8 *)buffer;
    out->size = size;
    return 0;
}

/* Walk a device path and report what it describes, so the trace names the
 * device the kernel was actually loaded from. */
static const char *path_kind(const struct efi_device_path *node)
{
    if (!node)
        return "none";
    if (node->type == EFI_DEVICE_PATH_TYPE_MEDIA) {
        if (node->sub_type == EFI_DEVICE_PATH_SUBTYPE_MEDIA_CDROM)
            return "cdrom";
        return "media";
    }
    if (node->type == EFI_DEVICE_PATH_TYPE_HARDWARE)
        return "hardware";
    if (node->type == EFI_DEVICE_PATH_TYPE_MESSAGING)
        return "messaging";
    if (node->type == EFI_DEVICE_PATH_TYPE_ACPI)
        return "acpi";
    return "other";
}

/* Does this device carry a file system whose root holds \KERNEL.BIN?  On yes
 * the volume is reopened for the caller, so the caller never re-tests and never
 * guesses: the volume returned is the one that satisfied the test. */
static int volume_has_kernel(struct efi_boot_services *bs, efi_handle device,
                             struct efi_file **kernel_root)
{
    struct efi_simple_file_system *fs = 0;
    if (EFI_ERROR(bs->handle_protocol(device,
            (struct efi_guid *)&EFI_SIMPLE_FILE_SYSTEM_PROTOCOL_GUID, (void **)&fs)) || !fs)
        return 0;                   /* this handle has no file system */
    struct efi_file *root = 0;
    if (EFI_ERROR(fs->open_volume(fs, &root)) || !root)
        return 0;                   /* a volume firmware will not let us read */
    struct efi_file *file = 0;
    efi_status status = root->open(root, &file, u"\\KERNEL.BIN", EFI_FILE_MODE_READ, 0);
    root->close(root);              /* also closes a failed Open's handle set */
    if (EFI_ERROR(status) || !file)
        return 0;
    file->close(file);
    struct efi_file *chosen_root = 0;
    if (EFI_ERROR(fs->open_volume(fs, &chosen_root)) || !chosen_root)
        return 0;
    *kernel_root = chosen_root;
    return 1;
}

/* Find the volume that holds \KERNEL.BIN.
 *
 * The volume the application was booted from is tried first, because that is
 * where the kernel is meant to live: on a disk-style ESP boot it is the ESP
 * itself, and on an El Torito UEFI boot it is the firmware's RAM-disk copy of
 * the same ESP image.  Firmware offers no ISO9660 driver at all (OVMF has
 * none), so the ISO9660 volume the BIOS path reads is *not* reachable through
 * EFI_SIMPLE_FILE_SYSTEM_PROTOCOL - which is exactly why the ESP carries a
 * copy of the kernel next to BOOTX64.EFI.
 *
 * Only when that volume has no kernel does the scan fall back to every file
 * system on the machine, so a machine that keeps the kernel on another
 * partition still boots without hard-coded LBA arithmetic.
 *
 * Returns the file system handle, or 0 when no volume could provide the file. */
static efi_handle find_kernel_volume(struct efi_system_table *st, efi_handle image_handle,
                                     struct efi_file **kernel_root)
{
    struct efi_boot_services *bs = st->boot_services;
    efi_handle image_device = 0;

    /* The device we were loaded from, for the trace and for the first try. */
    struct efi_loaded_image *loaded = 0;
    if (!EFI_ERROR(bs->handle_protocol(image_handle,
                                       (struct efi_guid *)&EFI_LOADED_IMAGE_PROTOCOL_GUID,
                                       (void **)&loaded)) && loaded) {
        image_device = loaded->device_handle;
        struct efi_device_path *device_path = 0;
        if (!EFI_ERROR(bs->handle_protocol(image_device,
                                           (struct efi_guid *)&EFI_DEVICE_PATH_PROTOCOL_GUID,
                                           (void **)&device_path)) && device_path) {
            line_reset();
            line_add_prefix();
            line_add("image_device=");
            line_add(path_kind(device_path));
            line_add("\r\n");
            uefi_puts(line_buf);
        }
    }

    if (image_device && volume_has_kernel(bs, image_device, kernel_root)) {
        trace("kernel_volume=image");
        return image_device;
    }

    /* Enumerate every handle with a file system on it. */
    u64 size = 0;
    efi_status status = bs->locate_handle(&size, EFI_LOCATE_BY_PROTOCOL, 0, 0);
    if (status != EFI_BUFFER_TOO_SMALL || !size) {
        trace("kernel_volume=none");
        return 0;
    }
    size += 4 * sizeof(efi_handle);       /* handles can appear between calls */
    void *buffer = 0;
    if (EFI_ERROR(bs->allocate_pool(EFI_LOADER_DATA, size, &buffer)) || !buffer) {
        trace("kernel_volume=none");
        return 0;
    }
    status = bs->locate_handle(&size, EFI_LOCATE_BY_PROTOCOL, 0, (efi_handle *)buffer);
    if (EFI_ERROR(status)) {
        bs->free_pool(buffer);
        trace("kernel_volume=none");
        return 0;
    }
    u64 count = size / sizeof(efi_handle);

    efi_handle chosen = 0;
    u32 candidates = 0;
    for (u64 i = 0; i < count && !chosen; i++) {
        efi_handle device = ((efi_handle *)buffer)[i];
        struct efi_simple_file_system *probe = 0;
        if (EFI_ERROR(bs->handle_protocol(device,
                (struct efi_guid *)&EFI_SIMPLE_FILE_SYSTEM_PROTOCOL_GUID, (void **)&probe)) || !probe)
            continue;               /* this handle has no file system */
        candidates++;
        if (device == image_device)
            continue;               /* already tried, and it had no kernel */
        if (volume_has_kernel(bs, device, kernel_root))
            chosen = device;
    }
    bs->free_pool(buffer);
    trace_dec("filesystem_volumes", candidates);
    if (chosen)
        trace("kernel_volume=scan");
    else
        trace("kernel_volume=none");
    return chosen;
}

/* UTF-16 -> printable ASCII.  Anything outside ASCII becomes '?', so a stray
 * high byte can never end up in a char[] the kernel prints or compares. */
static void utf16_to_ascii(const u16 *src, char *dst, u32 max)
{
    u32 i = 0;
    while (src[i] && i + 1 < max) {
        u16 c = src[i];
        dst[i] = (char)((c >= 0x20 && c < 0x7F) ? c : '?');
        i++;
    }
    dst[i] = 0;
}

/* ------------------------------------------------------------------ main */
efi_status EFIAPI efi_main(efi_handle image_handle, struct efi_system_table *st)
{
    uefi_serial_init();
    uefi_serial_puts("\r\nPC5 UEFI LOADER " LOADER_VERSION "\r\n");

    if (!st || !st->boot_services || !st->con_out)
        return EFI_LOAD_ERROR;

    conout = st->con_out;
    uefi_puts("\r\nPC5 UEFI loader " LOADER_VERSION "\r\n");
    trace("firmware_started");
    trace(serial_present ? "serial_probe=16550" : "serial_probe=absent");

    if (st->firmware_vendor && st->firmware_vendor[0]) {
        char vendor[32];
        utf16_to_ascii(st->firmware_vendor, vendor, sizeof(vendor));
        line_reset();
        line_add_prefix();
        line_add("firmware=");
        line_add(vendor);
        line_add("\r\n");
        uefi_puts(line_buf);
    }
    trace_dec("firmware_revision", st->firmware_revision);

    /* 1. display: text mode is a valid boot, so this never fails the load */
    if (!gop_init(st))
        trace("display=gop_unavailable,text_fallback");

    /* 2. the volume that holds the kernel (not necessarily the one we booted
     *    from: on El Torito the ESP is a separate device) */
    struct efi_file *root = 0;
    efi_handle volume = find_kernel_volume(st, image_handle, &root);
    if (!volume || !root)
        fail("E2", "no volume on this machine holds \\KERNEL.BIN", 0);
    trace("kernel_volume_opened");

    /* Proof that the medium is readable, without pretending to have read the
     * ISO9660 descriptor (the file protocol cannot expose it). */
    struct file_buffer probe;
    if (read_file(st, root, u"\\README.TXT", &probe) == 0) {
        trace_dec("medium_readme_bytes", probe.size);
        st->boot_services->free_pool(probe.data);
    } else {
        trace("medium_readme_unreadable");
    }

    /* 3. the kernel */
    struct file_buffer kernel;
    int err = read_file(st, root, u"\\KERNEL.BIN", &kernel);
    if (err)
        fail("E3", "cannot read \\KERNEL.BIN from the boot volume", (u64)err);
    trace_dec("kernel_bytes_read", kernel.size);

    /* The file layout is fixed by kernel/start.S and exposed as
     * PC5_KERNEL_SIG_OFFSET in kernel/include/pc5.h: byte 0 is a two-byte jump
     * over the header, then "PC5K" and the protocol version.  Reading the
     * signature at offset 0 would reject every kernel the BIOS path boots. */
    if (kernel.size < PC5_KERNEL_HEADER_SIZE ||
        memcmp(kernel.data + PC5_KERNEL_SIG_OFFSET, PC5_KERNEL_SIG, 4) != 0)
        fail("E4", "kernel image does not carry the PC5K signature", 0);
    u8 protocol = kernel.data[PC5_KERNEL_VERSION_OFFSET];
    trace_dec("kernel_protocol_version", protocol);
    if (protocol != PC5_PROTOCOL_VERSION)
        fail("E5", "kernel boot protocol version mismatch", protocol);
    if (kernel.size > KERNEL_LIMIT - KERNEL_ADDR)
        fail("E6", "kernel image is larger than the load window", kernel.size);

    /* Firmware may have armed a watchdog timer, and the load plus the map walk
     * can outlast it on a slow machine; a reset in the middle of a handover
     * looks exactly like a hardware fault.  Disable it while boot services are
     * still callable, and say whether firmware agreed. */
    {
        efi_status wd = st->boot_services->set_watchdog_timer(0, 0, 0, 0);
        if (EFI_ERROR(wd))
            trace_dec("watchdog_disable_refused", wd);
        else
            trace("watchdog_disabled");
    }

    /* 4 + 7. the memory map and leaving boot services belong together: the map
     * key is only valid until the next firmware allocation, so anything that
     * can allocate - printing to the console included - invalidates it.  The
     * map is therefore re-read and ExitBootServices retried while firmware
     * keeps moving it, and nothing runs between the keyed GetMemoryMap and the
     * ExitBootServices call itself. */
    struct efi_boot_services *bs = st->boot_services;
    efi_status status = EFI_INVALID_PARAMETER;
    u32 e820_count = 0;
    void *map = 0;
    u64 map_capacity = 0;
    u32 retries = 0;
    int exited = 0;
    int handover_checked = 0;
    for (u32 attempt = 0; attempt < 8 && !exited; attempt++) {
        u64 map_size = 0, map_key = 0, desc_size = 0;
        u32 desc_version = 0;
        efi_status probe = bs->get_memory_map(&map_size, 0, &map_key, &desc_size,
                                             &desc_version);
        if (probe != EFI_BUFFER_TOO_SMALL || !map_size || !desc_size)
            fail("E8", "firmware did not report a memory map size", probe);
        /* Slack: the descriptor count may grow between the size probe and the
         * real query, and a grown map is not a reason to give up. */
        u64 needed = map_size + (u64)4 * desc_size;
        if (needed > map_capacity) {
            if (map)
                bs->free_pool(map);
            map = 0;
            efi_status alloc = bs->allocate_pool(EFI_LOADER_DATA, needed, &map);
            if (EFI_ERROR(alloc) || !map)
                fail("E9", "cannot allocate the memory map buffer", alloc);
            map_capacity = needed;
        }
        map_size = map_capacity;
        status = bs->get_memory_map(&map_size, (struct efi_memory_descriptor *)map,
                                    &map_key, &desc_size, &desc_version);
        if (status == EFI_BUFFER_TOO_SMALL) {
            map_capacity = 0;          /* grew again: size it once more */
            continue;
        }
        if (EFI_ERROR(status))
            fail("E10", "firmware memory map query failed", status);
        /* Descriptors are walked with the stride firmware reports, never with
         * sizeof(struct efi_memory_descriptor). */
        if (desc_size < sizeof(struct efi_memory_descriptor) || desc_size > 0x1000)
            fail("E11", "unexpected memory descriptor size", desc_size);
        u64 count = map_size / desc_size;
        int overflow = 0;
        e820_count = build_e820(count, (u32)desc_size, (const u8 *)map, &overflow);
        if (!e820_count)
            fail("E12", "the firmware memory map was empty", 0);
        if (overflow)
            fail("E16", "the memory map does not fit in the E820 buffer", count);

        /* The load window has to be memory the kernel may own. */
        if (!window_is_conventional(count, (u32)desc_size, (const u8 *)map,
                                    HANDOVER_BASE, HANDOVER_LIMIT - HANDOVER_BASE))
            fail("E15", "the kernel handover window is not conventional memory",
                 HANDOVER_BASE);
        handover_checked = 1;

        status = bs->exit_boot_services(image_handle, map_key);
        if (!EFI_ERROR(status)) {
            exited = 1;
            break;
        }
        if (status != EFI_INVALID_PARAMETER)
            fail("E13", "ExitBootServices was refused", status);
        retries++;                     /* the map moved under us: read it again */
    }
    if (!exited)
        fail("E13", "ExitBootServices was refused with a fresh map key", status);

    /* Firmware is gone from here on: the console protocol must not be called
     * again, only the port PC5 owns. */
    console_closed = 1;
    uefi_serial_puts("PC5-UEFI: boot_services_exited\r\n");
    uefi_serial_puts("PC5-UEFI: memory_map_entries=");
    uefi_serial_put_dec(e820_count);
    uefi_serial_puts("\r\n");
    uefi_serial_puts("PC5-UEFI: ExitBootServices_retries=");
    uefi_serial_put_dec(retries);
    uefi_serial_puts("\r\n");
    uefi_serial_puts("PC5-UEFI: handover_window_checked=");
    uefi_serial_put_dec(handover_checked);
    uefi_serial_puts("\r\n");
    uefi_serial_puts("PC5-UEFI: memory_conventional_mib=");
    uefi_serial_put_dec(conventional_mib(e820_count));
    uefi_serial_puts("\r\n");

    /* 5. the boot contract.  Plain memory writes: no firmware call is needed
     * to publish a table the kernel reads after boot services are gone. */
    struct pc5_boot_info *bi = BOOT_INFO;
    memset(bi, 0, sizeof(*bi));
    bi->magic = PC5_BOOT_MAGIC;
    bi->version = PC5_PROTOCOL_VERSION;
    bi->boot_drive = 0xFFFFFFFFu;   /* there is no BIOS drive on this path */
    bi->e820_addr = E820_BUF_ADDR;
    bi->e820_count = e820_count;
    bi->vbe_mode_addr = VBE_MODE_ADDR;
    bi->vbe_ok = frame_ok ? 1u : 0u;
    bi->font_addr = 0;              /* firmware hands over no VGA font */
    bi->font_bytes_per_char = 0;
    bi->pvd_addr = PVD_BUF_ADDR;
    bi->pvd_ok = 0;                 /* not reachable through the file protocol */
    bi->kernel_bytes = (u32)kernel.size;
    bi->kernel_lba = 0;             /* the file protocol hides the LBA */
    bi->volume_id[0] = 0;           /* not exposed by the file protocol either */
    bi->iso_sectors = 0;
    memcpy(bi->loader_version, LOADER_VERSION, sizeof(bi->loader_version));
    bi->desc_crc = 0;
    /* bit 0: booted by the UEFI loader.  Bits 1 and 2 say what could not be
     * reported, so the kernel can print an honest medium line. */
    bi->flags = 0x1u | 0x2u /* no ISO LBA */ | 0x4u /* no volume id */;

    /* 6. kernel image into the handover address.  The copy is read back: a
     * handover that jumps to bytes which are not the file would fail inside the
     * kernel, where the cause is invisible. */
    memcpy((void *)KERNEL_ADDR, kernel.data, kernel.size);
    if (memcmp((const void *)KERNEL_ADDR, kernel.data, kernel.size) != 0)
        fail("E17", "the kernel copy at the handover address does not match the file", 0);
    uefi_serial_puts("PC5-UEFI: kernel_handoff_verified=1\r\n");
    uefi_serial_puts("PC5-UEFI: handoff_bytes=");
    uefi_serial_put_dec(kernel.size);
    uefi_serial_puts("\r\n");

    /* 8. 32-bit protected mode, then the kernel never returns */
    if (KERNEL_ADDR != 0x10000u)
        fail("E14", "kernel entry address is not the protocol address", KERNEL_ADDR);
    uefi_serial_puts("PC5-UEFI: kernel_loaded_at=0x00010000\r\n");
    uefi_serial_puts("PC5-UEFI: entering 32-bit kernel\r\n");
    pc5_uefi_bridge(KERNEL_ADDR, BOOT_INFO_ADDR);
}
