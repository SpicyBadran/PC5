/* The experimental 64-bit UEFI boot path.
 *
 * What this loader does that the 32-bit one does not:
 *
 *   - it never leaves long mode, and never touches the compatibility mode the
 *     other loader uses to reach a 32-bit kernel;
 *   - it builds PC5-owned 4-level page tables and switches CR3 to them, so the
 *     kernel runs on mappings PC5 can describe;
 *   - it hands over a versioned, size-checked boot-info structure
 *     (`struct pc5_boot64`, protocol version 2) rather than a flat 32-bit one;
 *   - it calls ExitBootServices with the memory-map key and retries when
 *     firmware invalidates that key, which real firmware does routinely;
 *   - it gives the kernel its own stack instead of firmware's;
 *   - it says what it did on COM1, so the execution can be checked without a
 *     screen.
 *
 * Placement is validated, not assumed.  Version 2 maps one identity region and the
 * kernel refuses to run outside it, so the loader:
 *
 *   - asks firmware where it loaded this application (Loaded Image Protocol) and
 *     refuses to continue if the image is outside the region, instead of switching
 *     CR3 and executing code nothing maps;
 *   - requests every allocation below the region (EFI_ALLOCATE_MAX_ADDRESS) and
 *     asserts the result is inside it;
 *   - records a framebuffer or RSDP that lies outside it as *unmapped* - the
 *     address is zeroed and the flag says why - rather than passing a physical
 *     address to the kernel that the kernel would dereference and fault on.
 *
 * The switch itself is in bootx64.S: the only code between installing CR3 and
 * entering the kernel is an instruction sequence that cannot be interrupted, and
 * that puts firmware's root back if the write does not take effect.
 */
#include "../uefi/efi.h"
#include "pc5_boot64.h"
#include "pc5_map.h"
#include "handover64.h"
#include "pagetables.h"

#define LOAD_LIMIT 0xFFFFFFFFull
static u16 KERNEL_NAME[] = {'\\', 'K', 'E', 'R', 'N', 'E', 'L',
                                   '6', '4', '.', 'B', 'I', 'N', 0};
#define MEMMAP_BUFFER_PAGES 8ull   /* 32 KiB: hundreds of descriptors */
#define MEMMAP_ENTRIES_MAX 1024ull
#define KERNEL_STACK_PAGES 4ull
#define EXIT_RETRIES 8

/* Published for the image test and the stub harness: the 64-bit loader's own
 * protocol record, not the 32-bit handover descriptor. */
struct pc5_handover64 pc5_handover64
    __attribute__((section(".data"), used)) = {
    .magic = {'P', 'C', '5', 'H', 'N', 'D', '0', '2'},
    .protocol = PC5_BOOT64_VERSION,
    .boot_info_size = sizeof(struct pc5_boot64),
    .page_table_pages = PC5_PT_PAGES,
    .identity_span = PC5_PT_SPAN,
};

struct acpi_config_entry {
    struct efi_guid guid;
    void *table;
};

/* 8868e871-e4f1-11d3-bc22-0080c73c8881: ACPI 2.0+ RSDP. */
static const struct efi_guid ACPI2_GUID =
    GUID(8868e871, e4f1, 11d3, bc, 22, 00, 80, c7, 3c, 88, 81);
/* eb9d2d30-2d88-11d3-9a16-0090273fc14d: the ACPI 1.0 table, older firmware. */
static const struct efi_guid ACPI1_GUID =
    GUID(eb9d2d30, 2d88, 11d3, 9a, 16, 00, 90, 27, 3f, c1, 4d);

static struct efi_system_table *g_st;
static struct efi_boot_services *g_bs;

/* ------------------------------------------------------------------ serial */
static void outb(unsigned short port, unsigned char value)
{
    __asm__ volatile("outb %0, %1" : : "a"(value), "Nd"(port));
}

static unsigned char inb(unsigned short port)
{
    unsigned char value;
    __asm__ volatile("inb %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}

static void serial_init(void)
{
    outb(0x3F9, 0x00);
    outb(0x3FB, 0x80);
    outb(0x3F8, 0x01);
    outb(0x3F9, 0x00);
    outb(0x3FB, 0x03);
    outb(0x3FA, 0xC7);
    outb(0x3FC, 0x0B);
}

static void serial_put_raw(char c);

static void serial_write(const char *text)
{
    while (*text) {
        if (*text == '\n')
            serial_put_raw('\r');
        serial_put_raw(*text++);
    }
}

void serial_put_raw(char c)
{
    unsigned guard = 0;
    while ((inb(0x3FD) & 0x20) == 0) {
        if (++guard > 1000000u)
            return;
    }
    outb(0x3F8, (unsigned char)c);
}

static void serial_hex(u64 value)
{
    static const char digits[] = "0123456789abcdef";
    char buffer[19];
    int index = 18;
    buffer[index--] = '\0';
    if (!value) {
        serial_write("0");
        return;
    }
    while (value && index >= 0) {
        buffer[index--] = digits[value & 0xF];
        value >>= 4;
    }
    serial_write("0x");
    serial_write(&buffer[index + 1]);
}

static void serial_dec(u64 value)
{
    char buffer[21];
    int index = 20;
    buffer[index--] = '\0';
    if (!value) {
        serial_write("0");
        return;
    }
    while (value && index > 0) {
        buffer[index--] = (char)('0' + (value % 10));
        value /= 10;
    }
    serial_write(&buffer[index + 1]);
}

static void die(const char *why) __attribute__((noreturn));

static void die(const char *why)
{
    serial_write("PC5-64-LOADER-FAIL=");
    serial_write(why);
    serial_write("\n");
    for (;;)
        __asm__ volatile("cli; hlt");
}

/* ------------------------------------------------------------- registers */
static u64 read_cr0(void) { u64 v; __asm__ volatile("mov %%cr0, %0" : "=r"(v)); return v; }
static u64 read_cr4(void) { u64 v; __asm__ volatile("mov %%cr4, %0" : "=r"(v)); return v; }

/* Installs CR3, verifies it reads back, switches to the kernel stack and jumps in
 * long mode.  Defined in bootx64.S: it is the only code that runs between the map
 * switch and the kernel, and it is written in assembly so that "nothing else runs
 * on the new map" is a property of the instruction sequence, not a promise. */
int pc5_enter_kernel64(u64 pagetable, u64 entry, struct pc5_boot64 *boot_info);

/* The assembly handoff loads the kernel stack with `movq 88(%rdx), %rsp`.  If the
 * structure ever moves, that instruction becomes a stack switch to the wrong
 * address - the kind of failure that is invisible until a machine happens to have
 * something writable there.  Fail the build instead. */
_Static_assert(__builtin_offsetof(struct pc5_boot64, stack_top) == 88,
               "bootx64.S loads stack_top from offset 88");
_Static_assert(sizeof(struct pc5_boot64) == 160, "boot info ABI is 160 bytes");
/* The map spans PC5_IDENTITY_LIMIT and nothing else; the loader's placement policy
 * and the kernel's range checks must use the same limit or a region could pass one
 * and fail the other. */
_Static_assert(PC5_PT_SPAN == PC5_IDENTITY_LIMIT, "map span differs from the ABI limit");

/* ------------------------------------------------------- placement policy */
/* Every address the loader will touch after the map switch must be inside the
 * map, and the map is all the loader can build in version 2.  These helpers are
 * the single place that decides what "inside" means, so the loader and the kernel
 * cannot disagree about it. */
static void require_below_span(const char *what, u64 address, u64 size)
{
    if (!pc5_range_within(address, size, PC5_PT_SPAN)) {
        serial_write("PC5-64-LOADER-REJECT ");
        serial_write(what);
        serial_write("=");
        serial_hex(address);
        serial_write(" size=");
        serial_hex(size);
        serial_write(" above identity span\n");
        die("above-identity-map");
    }
}

/* Records the EFI application's own placement and refuses to continue if firmware
 * put it where PC5's map cannot reach it.  The alternative - assuming firmware
 * loads applications low - is exactly the assumption that crashes on the first
 * machine that does not. */
static void validate_loader_placement(efi_handle image, struct pc5_boot64 *boot_info)
{
    struct efi_loaded_image *loaded = 0;
    efi_status status = g_bs->handle_protocol(
        image, (struct efi_guid *)&EFI_LOADED_IMAGE_PROTOCOL_GUID, (void **)&loaded);
    if (EFI_ERROR(status) || !loaded)
        die("no-loaded-image");
    u64 base = (u64)(unsigned long)loaded->image_base;
    u64 size = loaded->image_size;
    if (!base || !size)
        die("loaded-image-empty");
    u64 end = 0;
    if (pc5_add_overflows(base, size, &end))
        die("loader-image-overflow");
    require_below_span("loader-image", base, size);
    boot_info->loader_image_addr = base;
    boot_info->loader_image_size = size;
    serial_write("PC5-64-LOADER image=");
    serial_hex(base);
    serial_write("-");
    serial_hex(end);
    serial_write(" below-span=1\n");
}

/* --------------------------------------------------------------- helpers */
static void *alloc_below_4g(u64 pages, u64 *physical)
{
    u64 address = 0;
    efi_status status = g_bs->allocate_pages(EFI_ALLOCATE_MAX_ADDRESS,
                                            EFI_LOADER_DATA, pages, &address);
    if (EFI_ERROR(status) || address == 0 || address > LOAD_LIMIT)
        return 0;
    if (physical)
        *physical = address;
    return (void *)(unsigned long)address;
}

static void *alloc_pool(u64 bytes)
{
    void *buffer = 0;
    if (EFI_ERROR(g_bs->allocate_pool(EFI_LOADER_DATA, bytes, &buffer)))
        return 0;
    return buffer;
}

/* ---------------------------------------------------------- kernel image */
static efi_status open_kernel(struct efi_file **out_file, u64 *out_size)
{
    u64 size = 0;
    efi_handle handles[16];
    u64 handles_size = sizeof(handles);
    efi_status status = g_bs->locate_handle(&handles_size, EFI_LOCATE_BY_PROTOCOL,
                                            (void *)&EFI_SIMPLE_FILE_SYSTEM_PROTOCOL_GUID,
                                            handles);
    if (EFI_ERROR(status))
        return status;
    u64 count = handles_size / sizeof(efi_handle);

    for (u64 index = 0; index < count; index++) {
        struct efi_simple_file_system *fs = 0;
        if (EFI_ERROR(g_bs->handle_protocol(handles[index],
                                            (struct efi_guid *)&EFI_SIMPLE_FILE_SYSTEM_PROTOCOL_GUID,
                                            (void **)&fs)) || !fs)
            continue;
        struct efi_file *root = 0;
        if (EFI_ERROR(fs->open_volume(fs, &root)) || !root)
            continue;
        struct efi_file *file = 0;
        if (EFI_ERROR(root->open(root, &file, KERNEL_NAME, EFI_FILE_MODE_READ, 0)) || !file)
            continue;

        /* EFI_FILE_INFO is the only trustworthy source for the size: firmware
         * accepts SetPosition past EOF and echoes the position back. */
        u64 info_size = 0;
        if (root->open && file->get_info) {
            struct efi_guid info_guid = EFI_FILE_INFO_GUID;
            efi_status info_status =
                file->get_info(file, &info_guid, &info_size, 0);
            if (info_status != EFI_BUFFER_TOO_SMALL || info_size == 0)
                continue;
        } else {
            continue;
        }
        struct efi_file_info *info = alloc_pool(info_size);
        if (!info)
            continue;
        struct efi_guid info_guid = EFI_FILE_INFO_GUID;
        status = file->get_info(file, &info_guid, &info_size, info);
        if (EFI_ERROR(status) || info->file_size == 0) {
            g_bs->free_pool(info);
            continue;
        }
        size = info->file_size;
        g_bs->free_pool(info);
        *out_file = file;
        *out_size = size;
        return EFI_SUCCESS;
    }
    return EFI_NOT_FOUND;
}

static void load_kernel(struct pc5_boot64 *boot_info)
{
    struct efi_file *file = 0;
    u64 size = 0;
    if (EFI_ERROR(open_kernel(&file, &size)))
        die("kernel-not-found");

    /* The kernel is a flat, non-relocatable image: it has to be loaded at the
     * address it was linked for.  Requesting exactly that address turns a
     * mismatched build into a boot failure here instead of a wild jump later. */
    u64 pages = (size + 4095) / 4096;
    u64 physical = PC5_KERNEL64_LINK_BASE;
    efi_status allocated = g_bs->allocate_pages(EFI_ALLOCATE_ADDRESS, EFI_LOADER_DATA,
                                                pages, &physical);
    if (EFI_ERROR(allocated) || physical != PC5_KERNEL64_LINK_BASE)
        die("kernel-window-busy");
    void *buffer = (void *)(unsigned long)physical;

    u64 read_size = size;
    efi_status status = file->read(file, &read_size, buffer);
    if (EFI_ERROR(status) || read_size != size)
        die("kernel-read");
    file->close(file);

    boot_info->kernel_base = physical;
    boot_info->kernel_size = size;
    serial_write("PC5-64-LOADER kernel at=");
    serial_hex(physical);
    serial_write(" bytes=");
    serial_dec(size);
    serial_write("\n");
}

/* ------------------------------------------------------------ framebuffer */
static void collect_framebuffer(struct pc5_boot64 *boot_info, u64 *fb_end)
{
    struct efi_gop *gop = 0;
    efi_handle handles[8];
    u64 handles_size = sizeof(handles);
    if (EFI_ERROR(g_bs->locate_handle(&handles_size, EFI_LOCATE_BY_PROTOCOL,
                                      (void *)&EFI_GRAPHICS_OUTPUT_PROTOCOL_GUID,
                                      handles)))
        return;
    if (EFI_ERROR(g_bs->handle_protocol(handles[0],
                                        (struct efi_guid *)&EFI_GRAPHICS_OUTPUT_PROTOCOL_GUID,
                                        (void **)&gop)) || !gop || !gop->mode || !gop->mode->info)
        return;

    struct efi_gop_mode_info *info = gop->mode->info;
    /* A zero base means firmware did not report a linear framebuffer at all, so
     * there is nothing to record.  A zero pixel count, a zero dimension or an
     * unrepresentable pitch is different: the firmware *did* offer one, and it is
     * classified unusable rather than silently dropped. */
    if (!gop->mode->frame_buffer_base)
        return;

    u64 base = gop->mode->frame_buffer_base;
    /* The geometry is validated before it is stored.  A pitch that does not fit
     * the protocol's 32-bit field, a byte count that wraps, or a zero dimension
     * would otherwise be truncated or wrapped into a plausible-looking number and
     * handed to the kernel as usable. */
    u32 pitch = 0;
    u64 bytes = 0;
    if (!pc5_framebuffer_line(info->pixels_per_scan_line, &pitch) ||
        !pc5_framebuffer_bytes(info->horizontal_resolution,
                               info->vertical_resolution, pitch, &bytes)) {
        serial_write("PC5-64-LOADER framebuffer=");
        serial_hex(base);
        serial_write(" unusable=invalid-geometry\n");
        boot_info->framebuffer_addr = 0;
        boot_info->flags |= PC5_BOOT64_F_UNMAPPED_FRAMEBUFFER;
        return;
    }
    /* Only the two direct-colour layouts are interpretable from the boot info.
     * A bit-mask format needs masks the structure does not carry and PixelBltOnly
     * has no linear buffer, so both are classified unusable rather than trusted. */
    if (!pc5_pixel_format_supported(info->pixel_format)) {
        serial_write("PC5-64-LOADER framebuffer=");
        serial_hex(base);
        serial_write(" unusable=unsupported-format\n");
        boot_info->framebuffer_addr = 0;
        boot_info->flags |= PC5_BOOT64_F_FRAMEBUFFER_UNSUPPORTED;
        return;
    }
    u64 end = 0;
    if (pc5_add_overflows(base, bytes, &end) || end > PC5_PT_SPAN) {
        /* Firmware offered a framebuffer outside the single region version 2 can
         * map.  Say so, zero the address, and let the kernel record it as
         * unsupported: a 64-bit address that cannot be dereferenced must never be
         * handed over as if it could be. */
        serial_write("PC5-64-LOADER framebuffer=");
        serial_hex(base);
        serial_write(" unusable=outside-identity-span\n");
        boot_info->framebuffer_addr = 0;
        boot_info->flags |= PC5_BOOT64_F_UNMAPPED_FRAMEBUFFER;
        return;
    }
    boot_info->framebuffer_addr = base;
    boot_info->framebuffer_width = info->horizontal_resolution;
    boot_info->framebuffer_height = info->vertical_resolution;
    boot_info->framebuffer_pitch = pitch;
    boot_info->framebuffer_format = info->pixel_format;
    boot_info->flags |= PC5_BOOT64_F_FRAMEBUFFER;
    *fb_end = end;
    serial_write("PC5-64-LOADER framebuffer=");
    serial_hex(boot_info->framebuffer_addr);
    serial_write(" ");
    serial_dec(boot_info->framebuffer_width);
    serial_write("x");
    serial_dec(boot_info->framebuffer_height);
    serial_write(" pitch=");
    serial_dec(boot_info->framebuffer_pitch);
    serial_write("\n");
}

static int guid_equal(const struct efi_guid *left, const struct efi_guid *right)
{
    const unsigned char *a = (const unsigned char *)left;
    const unsigned char *b = (const unsigned char *)right;
    for (unsigned i = 0; i < 16; i++) {
        if (a[i] != b[i])
            return 0;
    }
    return 1;
}

static void collect_rsdp(struct pc5_boot64 *boot_info)
{
    u64 entries = g_st->number_of_table_entries;
    struct acpi_config_entry *table = g_st->configuration_table;
    if (!entries || !table)
        return;
    for (u64 index = 0; index < entries; index++) {
        if (!table[index].table)
            continue;
        if (!guid_equal(&table[index].guid, &ACPI2_GUID) &&
            !guid_equal(&table[index].guid, &ACPI1_GUID))
            continue;
        u64 rsdp = (u64)(unsigned long)table[index].table;
        if (!pc5_range_within(rsdp, 36, PC5_PT_SPAN)) {
            boot_info->flags |= PC5_BOOT64_F_UNMAPPED_RSDP;
            boot_info->acpi_rsdp = 0;
            serial_write("PC5-64-LOADER rsdp=");
            serial_hex(rsdp);
            serial_write(" unusable=outside-identity-span\n");
            return;
        }
        boot_info->acpi_rsdp = rsdp;
        boot_info->flags |= PC5_BOOT64_F_RSDP;
        serial_write("PC5-64-LOADER rsdp=");
        serial_hex(rsdp);
        serial_write("\n");
        return;
    }
}

/* --------------------------------------------------------- memory map/EBS */
static struct pc5_memdesc64 *memmap_and_exit(efi_handle image, u64 *out_entries)
{
    u64 buffer_pages = MEMMAP_BUFFER_PAGES;
    void *buffer = alloc_below_4g(buffer_pages, 0);
    if (!buffer)
        die("memmap-allocate");
    u64 out_pages = (MEMMAP_ENTRIES_MAX * sizeof(struct pc5_memdesc64) + 4095) / 4096;
    u64 out_physical = 0;
    struct pc5_memdesc64 *out = alloc_below_4g(out_pages, &out_physical);
    if (!out)
        die("memmap-copy-allocate");

    for (unsigned attempt = 0; attempt < EXIT_RETRIES; attempt++) {
        u64 size = buffer_pages * 4096;
        u64 key = 0;
        u64 descriptor_size = 0;
        u32 descriptor_version = 0;
        efi_status status = g_bs->get_memory_map(&size, buffer, &key,
                                                &descriptor_size, &descriptor_version);
        if (status == EFI_BUFFER_TOO_SMALL) {
            if (buffer_pages >= 64)
                die("memmap-too-large");
            buffer_pages *= 2;
            buffer = alloc_below_4g(buffer_pages, 0);
            if (!buffer)
                die("memmap-grow");
            continue;
        }
        if (EFI_ERROR(status))
            die("get-memory-map");
        if (descriptor_size < 40 || descriptor_size > 4096)
            die("memory-descriptor-size");

        u64 entries = size / descriptor_size;
        if (entries > MEMMAP_ENTRIES_MAX)
            die("memmap-entries");
        struct efi_memory_descriptor *source = buffer;
        u64 kept = 0;
        for (u64 index = 0; index < entries; index++) {
            struct efi_memory_descriptor *entry =
                (struct efi_memory_descriptor *)((unsigned char *)source +
                                                 index * descriptor_size);
            /* Firmware sizes the map with one spare descriptor so a caller can
             * call ExitBootServices without reallocating; the spare arrives
             * zeroed.  It is padding, not memory: dropping it here is what
             * keeps the kernel from seeing a zero-page region. */
            if (entry->number_of_pages == 0)
                continue;
            out[kept].physical_start = entry->physical_start;
            out[kept].page_count = entry->number_of_pages;
            out[kept].attributes = entry->attribute;
            out[kept].type = entry->type;
            out[kept].reserved = 0;
            kept++;
        }
        /* The map is validated as a set before it is handed over, not only entry by
         * entry.  Two firmware descriptors that claim the same physical bytes would
         * make the kernel's coverage decisions ambiguous, so the loader refuses the
         * map rather than passing a contradiction on. */
        if (kept == 0)
            die("memory-map-empty");
        if (!pc5_map_validate(out, kept))
            die("memory-map-overlap");

        status = g_bs->exit_boot_services(image, key);
        if (status == EFI_SUCCESS) {
            *out_entries = kept;
            serial_write("PC5-64-LOADER exit-boot-services=ok attempts=");
            serial_dec(attempt + 1);
            serial_write(" descriptors=");
            serial_dec(kept);
            serial_write("\n");
            return out;
        }
        /* The map key went stale between the call above and this one: firmware
         * allocated something itself.  Re-read the map, convert again, retry. */
        serial_write("PC5-64-LOADER exit-boot-services=stale-key retry=");
        serial_dec(attempt + 1);
        serial_write("\n");
    }
    die("exit-boot-services");
    return 0;
}

/* --------------------------------------------------------------- entry */
efi_status EFIAPI efi_main64(efi_handle image, struct efi_system_table *system_table)
{
    serial_init();
    serial_write("PC5-64-LOADER-BANNER 64-bit UEFI path (experimental)\n");

    if (!system_table || !system_table->boot_services)
        die("no-boot-services");
    g_st = system_table;
    g_bs = system_table->boot_services;
    g_bs->set_watchdog_timer(0, 0, 0, 0);

    /* Everything PC5 allocates lives below the identity span, because version 2
     * maps exactly one region and the kernel will validate every pointer against
     * it.  Each requirement is asserted rather than assumed. */
    u64 boot_info_physical = 0;
    struct pc5_boot64 *boot_info = alloc_below_4g(1, &boot_info_physical);
    if (!boot_info)
        die("bootinfo-allocate");
    require_below_span("bootinfo", boot_info_physical, sizeof(struct pc5_boot64));
    boot_info->magic = PC5_BOOT64_MAGIC;
    boot_info->version = PC5_BOOT64_VERSION;
    boot_info->size = sizeof(struct pc5_boot64);

    load_kernel(boot_info);
    require_below_span("kernel", boot_info->kernel_base, boot_info->kernel_size);

    u64 fb_end = 0;
    collect_framebuffer(boot_info, &fb_end);
    collect_rsdp(boot_info);

    u64 pt_physical = 0;
    if (!alloc_below_4g(PC5_PT_PAGES, &pt_physical))
        die("pagetable-allocate");
    require_below_span("pagetable", pt_physical, PC5_PT_PAGES * PC5_PT_PAGE);
    unsigned long mapped = pc5_pt_build_identity(pt_physical, PC5_PT_SPAN);
    if (mapped != (unsigned long)(PC5_PT_SPAN / PC5_PT_2MIB))
        die("pagetable-incomplete");
    if ((boot_info->flags & PC5_BOOT64_F_FRAMEBUFFER) &&
        !pc5_range_within(boot_info->framebuffer_addr,
                          fb_end - boot_info->framebuffer_addr, PC5_PT_SPAN))
        die("framebuffer-outside-span");
    if (boot_info->flags & PC5_BOOT64_F_FRAMEBUFFER)
        pc5_pt_mark_uncached(pt_physical, boot_info->framebuffer_addr, fb_end);
    serial_write("PC5-64-LOADER pagetables=");
    serial_hex(pt_physical);
    serial_write(" pages=");
    serial_dec(PC5_PT_PAGES);
    serial_write(" mapped_2mib=");
    serial_dec(mapped);
    serial_write(" span=");
    serial_hex(PC5_PT_SPAN);
    serial_write("\n");

    u64 stack_physical = 0;
    if (!alloc_below_4g(KERNEL_STACK_PAGES, &stack_physical))
        die("stack-allocate");
    require_below_span("stack", stack_physical, KERNEL_STACK_PAGES * 4096);
    boot_info->stack_top = stack_physical + KERNEL_STACK_PAGES * 4096;
    boot_info->stack_size = KERNEL_STACK_PAGES * 4096;
    serial_write("PC5-64-LOADER stack=");
    serial_hex(stack_physical);
    serial_write("-");
    serial_hex(boot_info->stack_top);
    serial_write("\n");

    validate_loader_placement(image, boot_info);

    u64 entries = 0;
    struct pc5_memdesc64 *memmap = memmap_and_exit(image, &entries);
    boot_info->memmap_addr = (u64)(unsigned long)memmap;
    boot_info->memmap_bytes = entries * sizeof(struct pc5_memdesc64);
    boot_info->memmap_desc_size = sizeof(struct pc5_memdesc64);
    boot_info->memmap_desc_version = 1;
    boot_info->pagetable_addr = pt_physical;
    boot_info->pagetable_pages = PC5_PT_PAGES;
    boot_info->identity_span = PC5_PT_SPAN;
    boot_info->flags |= PC5_BOOT64_F_MEMMAP | PC5_BOOT64_F_EXIT_BOOT_SERVICES;
    boot_info->exit_boot_services = 1;
    require_below_span("memmap", boot_info->memmap_addr, boot_info->memmap_bytes);

    /* Everything the loader needs after the switch is now inside the map, so the
     * switch itself is the last thing this code does.  Firmware's stack and
     * firmware's tables are abandoned here; the kernel gets PC5's stack. */
    u64 cr0 = read_cr0();
    u64 cr4 = read_cr4();
    if (!(cr0 & (1ull << 31)))
        die("paging-off");
    if (!(cr4 & (1ull << 5)))
        die("pae-off");
    serial_write("PC5-64-LOADER cr3-plan=");
    serial_hex(pt_physical);
    serial_write(" cr0=");
    serial_hex(cr0);
    serial_write(" cr4=");
    serial_hex(cr4);
    serial_write(" long-mode=1\n");
    serial_write("PC5-64-LOADER-JUMP entry=");
    serial_hex(boot_info->kernel_base);
    serial_write(" bootinfo=");
    serial_hex(boot_info_physical);
    serial_write(" stack=");
    serial_hex(boot_info->stack_top);
    serial_write("\n");

    if (pc5_enter_kernel64(pt_physical, boot_info->kernel_base, boot_info) != 0)
        die("cr3-not-installed");
    die("handoff-returned");
}
