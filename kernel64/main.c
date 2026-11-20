/* The 64-bit kernel's entry environment.
 *
 * This is the first execution path in PC5 that runs in long mode with paging owned by
 * PC5 rather than by firmware.  It is deliberately small: validate the handover, prove
 * the CPU state, collect the feature report, and say so.  It refuses to print its
 * success marker unless every mandatory check passed, because a marker that appears
 * anyway would be worse than no marker at all.
 *
 * Validation order matters and is the point of this file (see pc5_boot64.h):
 *   address arithmetic on the structure -> header -> memory map -> everything else,
 *   with each remaining pointer checked against the map for range and memory type
 *   before it is dereferenced or reported as usable.
 *
 * It is an *experimental* path: `make kernel64` builds it and `make stubtest64` runs
 * it; nothing in the release ISO consumes it yet.
 */
#include "pc5_boot64.h"
#include "pc5_map.h"
#include "pc5_io.h"
#include "cpu64.h"
#include "apic64.h"
#include "serial64.h"

#define CR0_PG (1ULL << 31)
#define CR0_PE (1ULL << 0)
#define CR4_PAE (1ULL << 5)

#define KERNEL_MAX_BYTES (64ULL << 20)
#define STACK_MAX_BYTES (1ULL << 20)
#define MEMMAP_MAX_BYTES (1024ULL * 32ULL)
#define MEMMAP_MAX_ENTRIES 1024ULL
#define RSDP_SIGNATURE "RSD PTR "

/* ------------------------------------------------------------- ACPI RSDP ----
 * The two structures the specification defines, and nothing beyond them:
 *
 *   revision 0  struct acpi_rsdp_legacy  20 bytes, one checksum over all 20
 *   revision 2+ struct acpi_rsdp_v2      the legacy 20 plus a pointer to the
 *                                        XSDT, with its own checksum over the
 *                                        *declared* length (>= 36)
 *
 * A revision-0 table has no `length` field and its bytes 20..35 belong to
 * whatever follows it, so they are never read or summed.  `length` in a
 * revision-2 table is the only thing that says how much of that structure is
 * real, so it is validated before a single byte past offset 36 is touched.
 */
#define RSDP_LEGACY_BYTES 20u
#define RSDP_V2_MIN_BYTES 36u
#define RSDP_MAX_BYTES 4096u          /* a structure this large is not an RSDP */
#define RSDP_REVISION_V2 2u

_Static_assert(sizeof(struct pc5_boot64) == 160, "pc5_boot64 abi");
_Static_assert(sizeof(struct pc5_memdesc64) == 32, "pc5_memdesc64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, memmap_addr) == 16, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, framebuffer_addr) == 40, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, acpi_rsdp) == 64, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, kernel_base) == 72, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, stack_top) == 88, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, stack_size) == 96, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, pagetable_addr) == 104, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, identity_span) == 120, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, loader_image_addr) == 128, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, exit_boot_services) == 144, "boot64 abi");

extern char __kernel_start[], __kernel_end[];

/* Arithmetic, descriptor and coverage helpers live in pc5_map.h, shared with
 * the loader so both sides apply exactly one rule. */

static void halt_forever(void)
{
    for (;;)
        __asm__ volatile("cli; hlt");
}

static void fail(const char *reason)
{
    serial64_write("PC5-64BIT-FAIL=");
    serial64_write(reason);
    serial64_write("\n");
    halt_forever();
}

/* Stage 1: nothing is dereferenced here that has not been checked arithmetically. */
static void validate_structure_address(const struct pc5_boot64 *bi)
{
    if (!bi)
        fail("bootinfo-null");
    u64 address = (u64)(unsigned long)bi;
    if (address & 7)
        fail("bootinfo-misaligned");
    if (!pc5_range_within(address, sizeof(struct pc5_boot64), PC5_IDENTITY_LIMIT))
        fail("bootinfo-address");
}

static void validate_header(const struct pc5_boot64 *bi)
{
    if (bi->magic != PC5_BOOT64_MAGIC)
        fail("bootinfo-magic");
    if (bi->version != PC5_BOOT64_VERSION)
        fail("bootinfo-version");
    if (bi->size != (u32)sizeof(struct pc5_boot64))
        fail("bootinfo-size");
    if (!(bi->flags & PC5_BOOT64_F_MEMMAP))
        fail("bootinfo-no-memmap");
}

/* Stage 2: the map's address is checked arithmetically, then it is read and every
 * descriptor is checked for internal consistency before anyone trusts a bound. */
static struct pc5_map_view validate_memmap(const struct pc5_boot64 *bi)
{
    struct pc5_map_view map = {0, 0};
    if (bi->memmap_addr == 0)
        fail("bootinfo-memmap-null");
    if (bi->memmap_addr & 7)
        fail("bootinfo-memmap-misaligned");
    if (bi->memmap_desc_size != (u32)sizeof(struct pc5_memdesc64))
        fail("bootinfo-memmap-descsize");
    if (bi->memmap_bytes == 0 || bi->memmap_bytes % bi->memmap_desc_size)
        fail("bootinfo-memmap-bytes");
    if (bi->memmap_bytes > MEMMAP_MAX_BYTES)
        fail("bootinfo-memmap-too-large");
    if (!pc5_range_within(bi->memmap_addr, bi->memmap_bytes, PC5_IDENTITY_LIMIT))
        fail("bootinfo-memmap-range");

    map.descriptors = (const struct pc5_memdesc64 *)(unsigned long)bi->memmap_addr;
    map.entries = bi->memmap_bytes / bi->memmap_desc_size;
    if (map.entries > MEMMAP_MAX_ENTRIES)
        fail("bootinfo-memmap-entries");

    u64 conventional_pages = 0;
    for (u64 index = 0; index < map.entries; index++) {
        if (!pc5_descriptor_valid(&map.descriptors[index]))
            fail("bootinfo-memmap-descriptor");
        if (map.descriptors[index].type == PC5_MMAP_CONVENTIONAL)
            conventional_pages += map.descriptors[index].page_count;
    }
    if (conventional_pages == 0)
        fail("bootinfo-no-conventional-memory");
    /* Every descriptor is self-consistent at this point; what is left is the
     * property of the set.  Two descriptors that claim the same physical bytes
     * would make every later coverage answer ambiguous, so the whole set is
     * validated before any range - kernel, stack, page tables, framebuffer, RSDP -
     * is checked against it. */
    if (!pc5_map_validate(map.descriptors, map.entries))
        fail("bootinfo-memmap-overlap");

    serial64_write("PC5-64-MAP entries=");
    serial64_dec(map.entries);
    serial64_write(" conventional_pages=");
    serial64_dec(conventional_pages);
    serial64_write(" bytes=");
    serial64_hex(bi->memmap_bytes);
    serial64_write("\n");
    return map;
}

/* Stage 3: every mandatory pointer, validated against the map before use. */
static void validate_mandatory_ranges(const struct pc5_boot64 *bi,
                                      const struct pc5_map_view *map)
{
    if (bi->kernel_base == 0 || bi->kernel_base & 0xFFF)
        fail("bootinfo-kernel-base");
    if (bi->kernel_base != PC5_KERNEL64_LINK_BASE)
        fail("bootinfo-kernel-address");
    if ((u64)(unsigned long)__kernel_start != PC5_KERNEL64_LINK_BASE)
        fail("link-base-mismatch");
    if (bi->kernel_size == 0 || bi->kernel_size > KERNEL_MAX_BYTES)
        fail("bootinfo-kernel-size");
    if (!pc5_range_within(bi->kernel_base, bi->kernel_size, PC5_IDENTITY_LIMIT))
        fail("bootinfo-kernel-span");
    if (!pc5_map_covers(map, bi->kernel_base, bi->kernel_size, PC5_MMAP_LOADER_OWNED_TYPES))
        fail("bootinfo-kernel-range");
    serial64_write("PC5-64-IMAGE base=");
    serial64_hex(bi->kernel_base);
    serial64_write(" size=");
    serial64_hex(bi->kernel_size);
    serial64_write(" range=ok\n");

    if (bi->stack_top == 0 || (bi->stack_top & 0xF))
        fail("bootinfo-stack");
    if (bi->stack_size < 4096 || bi->stack_size > STACK_MAX_BYTES ||
        (bi->stack_size & 0xFFF))
        fail("bootinfo-stack-size");
    if (bi->stack_top < bi->stack_size)
        fail("bootinfo-stack-underflow");
    u64 stack_base = bi->stack_top - bi->stack_size;
    if (!pc5_range_within(stack_base, bi->stack_size, PC5_IDENTITY_LIMIT))
        fail("bootinfo-stack-span");
    if (!pc5_map_covers(map, stack_base, bi->stack_size, PC5_MMAP_LOADER_OWNED_TYPES))
        fail("bootinfo-stack-range");

    if (bi->pagetable_addr == 0 || (bi->pagetable_addr & 0xFFF))
        fail("bootinfo-pagetable");
    if (bi->pagetable_pages < 3 || bi->pagetable_pages > PC5_PAGETABLE_MAX_PAGES)
        fail("bootinfo-pagetable-pages");
    /* The table block's *size* is pages * 4096; the sum is only needed to prove the
     * block does not wrap.  Passing the sum where the size belongs would validate a
     * range many times too large (and reject a correct handover), so the two are
     * kept in separate variables on purpose. */
    u64 table_bytes = bi->pagetable_pages * 4096ull;
    u64 table_end = 0;
    if (pc5_add_overflows(bi->pagetable_addr, table_bytes, &table_end))
        fail("bootinfo-pagetable-overflow");
    if (table_end > PC5_IDENTITY_LIMIT)
        fail("bootinfo-pagetable-span");
    if (!pc5_map_covers(map, bi->pagetable_addr, table_bytes,
                        PC5_MMAP_LOADER_OWNED_TYPES))
        fail("bootinfo-pagetable-range");
    /* The declared span must be exactly what a 1 PML4 + 1 PDPT + (pages-2) PD block
     * maps with 2 MiB pages; anything else would make "inside the span" a guess. */
    if (bi->identity_span != (bi->pagetable_pages - 2ull) * 512ull * (2ull << 20))
        fail("bootinfo-pagetable-span-mismatch");
    if (bi->identity_span == 0 || bi->identity_span > PC5_IDENTITY_LIMIT)
        fail("bootinfo-span-limit");

    if (bi->loader_image_addr == 0 || bi->loader_image_size == 0)
        fail("bootinfo-loader");
    if (!pc5_range_within(bi->loader_image_addr, bi->loader_image_size,
                      PC5_IDENTITY_LIMIT))
        fail("bootinfo-loader-span");
    if (!pc5_map_covers(map, bi->loader_image_addr, bi->loader_image_size,
                    PC5_MMAP_LOADER_OWNED_TYPES))
        fail("bootinfo-loader-range");
}

/* Sum `length` bytes and report whether they add up to zero, which is what the
 * ACPI checksum rule asks for.  The bytes are read only after the caller has
 * proven the whole range is inside the identity span and covered by
 * firmware-owned memory: this function dereferences, so it does not decide. */
static int rsdp_checksum_holds(u64 address, u32 length)
{
    const unsigned char *bytes = (const unsigned char *)(unsigned long)address;
    u8 sum = 0;
    for (u32 i = 0; i < length; i++)
        sum = (u8)(sum + bytes[i]);
    return sum == 0;
}

/* Why an RSDP is not usable, as a stable token for the log.  `0` means every
 * check this kernel implements passed - nothing here implies the referenced
 * XSDT, its tables, or their checksums were validated. */
static const char *rsdp_reject_reason(u64 address, u64 span,
                                      const struct pc5_map_view *map)
{
    if (address == 0)
        return "null";

    /* The legacy header is the smallest thing that can be checked, so it is the
     * first range validated - and no field beyond it is read before this passes. */
    if (!pc5_range_within(address, RSDP_LEGACY_BYTES, span))
        return "outside-span";
    if (!pc5_map_covers(map, address, RSDP_LEGACY_BYTES, PC5_MMAP_FIRMWARE_DATA_TYPES))
        return "not-firmware-memory";

    const unsigned char *base = (const unsigned char *)(unsigned long)address;
    for (unsigned i = 0; i < 8; i++) {
        if (base[i] != (unsigned char)RSDP_SIGNATURE[i])
            return "signature";
    }
    if (!rsdp_checksum_holds(address, RSDP_LEGACY_BYTES))
        return "legacy-checksum";

    u8 revision = base[15];
    /* Exactly two revisions are defined: 0 (ACPI 1.0) and 2 (ACPI 2.0 and later).
     * Revision 1 is not one of them, and a revision above 2 describes a layout
     * this kernel has not been told about - treating either as "probably like
     * revision 2" would be guessing at the size of a structure being summed. */
    if (revision == 0)
        return 0;                   /* legacy: 20 bytes, both already checked */
    if (revision != RSDP_REVISION_V2)
        return "revision";

    /* Revision 2 and later.  `length` is the only claim about how many bytes the
     * structure occupies, so it is validated before offset 20 is read at all. */
    u32 length = 0;
    for (unsigned i = 0; i < 4; i++)
        length |= (u32)base[20 + i] << (8 * i);
    if (length < RSDP_V2_MIN_BYTES)
        return "length";            /* too short to contain its own checksum */
    if (length > RSDP_MAX_BYTES)
        return "length";
    /* The whole declared structure, not merely its header, must be mapped and
     * firmware-owned before the extended checksum reads all of it. */
    if (!pc5_range_within(address, length, span))
        return "outside-span";
    if (!pc5_map_covers(map, address, length, PC5_MMAP_FIRMWARE_DATA_TYPES))
        return "not-firmware-memory";
    if (!rsdp_checksum_holds(address, length))
        return "extended-checksum";
    return 0;
}

/* Stage 4: optional resources are validated too, but a failure here is recorded
 * rather than fatal: the kernel simply must not report an unusable resource as
 * usable.  Neither is dereferenced before it passes. */
static void report_optional_resources(const struct pc5_boot64 *bi,
                                      const struct pc5_map_view *map)
{
    if (bi->flags & PC5_BOOT64_F_FRAMEBUFFER) {
        const char *reason = 0;
        u64 bytes = 0;
        if (bi->framebuffer_addr == 0)
            reason = "null";
        else if (!pc5_pixel_format_supported(bi->framebuffer_format))
            reason = "unsupported-format";
        else if (bi->framebuffer_width == 0 || bi->framebuffer_height == 0)
            reason = "empty";
        else if (bi->framebuffer_pitch < (u64)bi->framebuffer_width * 4ull)
            reason = "pitch-too-small";
        else if (!pc5_framebuffer_bytes(bi->framebuffer_width, bi->framebuffer_height,
                                        bi->framebuffer_pitch, &bytes))
            reason = "size-overflow";
        else if (!pc5_range_within(bi->framebuffer_addr, bytes, bi->identity_span))
            reason = "outside-span";
        else if (!pc5_map_covers(map, bi->framebuffer_addr, bytes,
                             PC5_MMAP_FRAMEBUFFER_TYPES))
            reason = "not-device-memory";
        serial64_write("PC5-64-FB addr=");
        serial64_hex(bi->framebuffer_addr);
        serial64_write(" width=");
        serial64_dec(bi->framebuffer_width);
        serial64_write(" height=");
        serial64_dec(bi->framebuffer_height);
        serial64_write(" pitch=");
        serial64_dec(bi->framebuffer_pitch);
        if (reason) {
            serial64_write(" usability=unsupported reason=");
            serial64_write(reason);
            serial64_write("\n");
        } else {
            serial64_write(" usability=ok\n");
        }
    } else if (bi->flags & PC5_BOOT64_F_FRAMEBUFFER_UNSUPPORTED) {
        serial64_write("PC5-64-FB usability=unsupported reason=unsupported-by-loader\n");
    } else {
        serial64_write(bi->flags & PC5_BOOT64_F_UNMAPPED_FRAMEBUFFER
                           ? "PC5-64-FB usability=unsupported reason=unmapped-by-loader\n"
                           : "PC5-64-FB usability=absent\n");
    }

    if (bi->flags & PC5_BOOT64_F_RSDP) {
        /* An RSDP that fails is *invalid*, not merely unsupported: `ok` here means
         * the signature and both checksums this kernel implements held.  It still
         * says nothing about the XSDT or any table it points at. */
        const char *reason = rsdp_reject_reason(bi->acpi_rsdp, bi->identity_span,
                                                map);
        serial64_write("PC5-64-RSDP addr=");
        serial64_hex(bi->acpi_rsdp);
        if (reason) {
            serial64_write(" usability=invalid reason=");
            serial64_write(reason);
            serial64_write(" checks=none-passed\n");
        } else {
            serial64_write(" usability=ok checks=");
            serial64_write("signature,legacy-checksum");
            if (*(const unsigned char *)(unsigned long)(bi->acpi_rsdp + 15)
                >= RSDP_REVISION_V2)
                serial64_write(",length,extended-checksum");
            serial64_write(" tables=not-validated\n");
        }
    } else {
        serial64_write(bi->flags & PC5_BOOT64_F_UNMAPPED_RSDP
                           ? "PC5-64-RSDP usability=unsupported reason=unmapped-by-loader\n"
                           : "PC5-64-RSDP usability=absent\n");
    }
}

/* ------------------------------------------------------------- environment */
static void report_environment(const struct pc5_boot64 *bi, u32 physical_bits)
{
    u64 cr0 = read_cr0(), cr3 = read_cr3(), cr4 = read_cr4();
    int efer_readable;

    /* The MSR may be unreadable under some emulators; that makes it informational,
     * never a substitute for the architectural checks below. */
    u64 efer = read_msr(MSR_EFER);
    efer_readable = !((efer & (EFER_LME | EFER_LMA)) == 0 &&
                      (cr0 & CR0_PG) && (cr4 & CR4_PAE));
    if (!(cr0 & CR0_PG))
        fail("paging-disabled");
    if (!(cr4 & CR4_PAE))
        fail("pae-disabled");
    if (cr3 == 0 || (cr3 & 0xFFF))
        fail("page-table-root-invalid");

    serial64_write("PC5-64-BANNER PC5 64-bit kernel entry\n");
    serial64_write("PC5-64-ENV cr0=");
    serial64_hex(cr0);
    serial64_write(" cr3=");
    serial64_hex(cr3);
    serial64_write(" cr4=");
    serial64_hex(cr4);
    serial64_write(" efer=");
    serial64_hex(efer);
    serial64_write(efer_readable ? "\n" : " (not reported by this environment)\n");
    if (!efer_readable)
        serial64_write("PC5-64-NOTE efer.lma unavailable; long mode proven by "
                       "64-bit register arithmetic and the CPUID long-mode bit\n");

    /* The kernel must be running on the page tables the loader declared, or the
     * whole "PC5 owns the map" claim is a story about a different table. */
    if ((cr3 & 0x000FFFFFFFFFF000ull) != bi->pagetable_addr)
        fail("pagetable-root-mismatch");

    serial64_write("PC5-64-PAGING enabled=1 root=");
    serial64_hex(bi->pagetable_addr);
    serial64_write(" pages=");
    serial64_dec(bi->pagetable_pages);
    serial64_write(" span=");
    serial64_hex(bi->identity_span);
    serial64_write(" kernel=");
    serial64_hex((u64)(unsigned long)__kernel_start);
    serial64_write("-");
    serial64_hex((u64)(unsigned long)__kernel_end);
    serial64_write(" loaded=");
    serial64_hex(bi->kernel_base);
    serial64_write("\n");

    /* A 64-bit-only proof: in 32-bit mode the upper half of this value cannot
     * survive the move, and the comparison below would fail. */
    u64 probe = 0x1122334455667788ull;
    u64 copy;
    __asm__ volatile("movq %1, %%rax; movq %%rax, %0" : "=r"(copy) : "r"(probe)
                     : "rax");
    if (copy != probe)
        fail("not-64-bit-operand-size");
    if ((0x0000000100000000ull + 1ull) != 0x0000000100000001ull)
        fail("not-64-bit-arithmetic");
    serial64_write("PC5-64-REGS wide-operands=ok arithmetic=ok\n");

    /* Stack: the loader says where it is and how big; the kernel checks both the
     * architectural alignment and that RSP is really inside that range. */
    u64 rsp;
    __asm__ volatile("mov %%rsp, %0" : "=r"(rsp));
    if (rsp & 0xF)
        fail("stack-misaligned");
    if (rsp > bi->stack_top || bi->stack_top - rsp >= bi->stack_size)
        fail("stack-not-from-bootinfo");
    serial64_write("PC5-64-STACK rsp=");
    serial64_hex(rsp);
    serial64_write(" top=");
    serial64_hex(bi->stack_top);
    serial64_write(" size=");
    serial64_dec(bi->stack_size);
    serial64_write(" aligned=16 range=ok\n");

    serial64_write("PC5-64-LOADER image=");
    serial64_hex(bi->loader_image_addr);
    serial64_write(" size=");
    serial64_hex(bi->loader_image_size);
    serial64_write(" range=ok\n");

    /* Physical address width, when CPUID reports it: a pointer that does not fit
     * the CPU's physical address is not a pointer, whatever the map says.  A zero
     * or missing CPUID result is *not* read as "every address fits": the kernel
     * says the check did not run, so the log cannot be mistaken for a validation
     * that never happened. */
    if (pc5_physical_width_reported(physical_bits)) {
        const u64 checked[] = {bi->kernel_base, bi->kernel_base + bi->kernel_size,
                               bi->stack_top, bi->pagetable_addr,
                               bi->loader_image_addr + bi->loader_image_size,
                               bi->memmap_addr + bi->memmap_bytes};
        for (unsigned i = 0; i < sizeof(checked) / sizeof(checked[0]); i++) {
            if (!pc5_address_fits_width(checked[i], physical_bits))
                fail("address-width");
        }
        serial64_write("PC5-64-ENV physical-address-width=");
        serial64_dec(physical_bits);
        serial64_write(" checked=1\n");
    } else {
        serial64_write("PC5-64-ENV physical-address-width=not-reported checked=0\n");
    }
}

static void report_handover_flags(const struct pc5_boot64 *bi)
{
    serial64_write("PC5-64-BOOTINFO magic=ok version=");
    serial64_dec(bi->version);
    serial64_write(" size=");
    serial64_dec(bi->size);
    serial64_write(" memdesc=");
    serial64_dec(bi->memmap_desc_size);
    serial64_write(" flags=");
    serial64_hex(bi->flags);
    serial64_write(" exit_boot_services=");
    serial64_dec(bi->exit_boot_services);
    serial64_write("\n");
}

/* Validated memory map, kept for the half of the boot that runs after the stack
 * switch: it is read-only after validate_memmap() and re-parsing it would only add
 * another chance to disagree with the checks already made. */
static struct pc5_map_view g_map;

/* kernel64/start.S: switches RSP to a validated stack and calls the continuation
 * there.  It never returns. */
void pc5_adopt_stack(u64 stack_top, void (*continuation)(void *), void *argument)
    __attribute__((noreturn));

static void continue_on_handed_stack(void *argument);

void kmain(struct pc5_boot64 *bi)
{
    serial64_init();
    serial64_write("PC5-64BIT-KERNEL-ENTRY\n");

    validate_structure_address(bi);
    validate_header(bi);
    g_map = validate_memmap(bi);
    validate_mandatory_ranges(bi, &g_map);

    /* The loader's stack is now known to be inside the map, page-aligned and
     * inside a range the map calls loader-owned.  Adopting it is the first moment it
     * is legal to use it; until now every frame lived on the image's own stack. */
    pc5_adopt_stack(bi->stack_top, continue_on_handed_stack, (void *)bi);

    /* pc5_adopt_stack does not return. */
    fail("stack-adopt-returned");
}

/* The architectural local-APIC MMIO base: where the huge majority of x86-64
 * machines decode it.  A default, not a guarantee - firmware may move it. */
#define PC5_APIC_MMIO_BASE 0xFEE00000ULL
#define PC5_APIC_MMIO_SIZE 0x1000ULL

static u32 apic_mmio_read(void *context, u32 offset)
{
    (void)context;
    return *(volatile u32 *)(PC5_APIC_MMIO_BASE + offset);
}

static void apic_mmio_write(void *context, u32 offset, u32 value)
{
    (void)context;
    *(volatile u32 *)(PC5_APIC_MMIO_BASE + offset) = value;
}

/* Bring-up is *reported*, never fatal, and it is attempted only when the memory
 * map vouches for the window.
 *
 * Both halves of that sentence are load-bearing.  The gate exists because an APIC
 * MMIO read that nothing backs does not return all-ones, it raises a fault - so
 * `apic64_bring_up()`'s own probe cannot be the thing that discovers a missing
 * window, since it would die before it could report.  Reading only after
 * `pc5_map_covers()` says MMIO is there is what makes the read defined behaviour.
 *
 * Reported rather than fatal because nothing in this kernel consumes interrupts
 * yet: a machine that boots and says "APIC not enabled, here is why" is more useful
 * than one that refuses to boot over a facility no code depends on.  When timer or
 * SMP work lands, this becomes a hard requirement and the failure moves to fail().
 */
static void report_local_apic(void)
{
    struct pc5_apic64 apic;
    const char *why;

    if (!pc5_map_covers(&g_map, PC5_APIC_MMIO_BASE, PC5_APIC_MMIO_SIZE,
                        PC5_MMAP_BIT(PC5_MMAP_MMIO))) {
        serial64_write("PC5-64-APIC not-attempted reason=\"no MMIO range in the "
                       "memory map covers the architectural APIC base\"\n");
        return;
    }

    why = apic64_bring_up(&apic, apic_mmio_read, apic_mmio_write, 0);
    if (why != 0) {
        serial64_write("PC5-64-APIC not-brought-up reason=\"");
        serial64_write(why);
        serial64_write("\"\n");
        apic64_print(&apic);
        return;
    }
    apic64_print(&apic);
}

static void continue_on_handed_stack(void *argument)
{
    const struct pc5_boot64 *bi = (const struct pc5_boot64 *)argument;
    struct pc5_cpu64 cpu;
    cpu64_report(&cpu);
    if (!cpu.long_mode)
        fail("cpuid-reports-no-long-mode");

    report_environment(bi, cpu.phys_addr_bits);
    report_handover_flags(bi);
    report_optional_resources(bi, &g_map);
    cpu64_print(&cpu);
    report_local_apic();

    /* Every mandatory check passed; only now is the marker emitted. */
    serial64_write("PC5-64BIT-LONG-MODE-OK\n");
    serial64_write("PC5-64-DONE\n");
    halt_forever();
}
