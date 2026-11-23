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
#include "pc5_mem64.h"
#include "cpu64.h"
#include "apic64.h"
#include "idt64.h"
#include "timer64.h"
#include "madt64.h"
#include "tss64.h"
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

/* ------------------------------------------------------------------ frame allocator
 * The bitmap describes the whole conventional window the loader's map reaches, and
 * it lives in .bss - which travels with the image, so the kernel-image claim the
 * allocator makes on `[kernel_base, kernel_size)` already covers the bitmap's own
 * bytes.  No heap metadata is written outside the pool.
 */
#define MEM64_BITMAP_BYTES (PC5_MEM64_MAX_FRAMES / 8ull)
static u8 g_mem64_bitmap[MEM64_BITMAP_BYTES];
static struct pc5_mem64 g_mem64;

/* One boot-time measurement, printed for the log and asserted by the gate that can
 * execute it (`make stubtest64`).  A pool the map cannot support is reported and
 * left alone - nothing in this kernel allocates yet, so refusing to manage memory
 * must not stop a machine that would otherwise boot - but it is never reported as
 * success, and the stub gate requires status=ok, so a real regression is a build
 * failure rather than a footnote. */
/* The pool's first consumer: the frames a task-state segment and its two interrupt
 * stacks live in.  Reported the way the pool itself is reported - the fields printed are
 * what the executed gate asserts, and a refusal is printed as a refusal rather than
 * folded into a silence. */
static struct pc5_tss64 g_tss64;

static void report_tss(void)
{
    int tr_ok = 0;

    serial64_write("PC5-64-TSS status=");

    if (!pc5_tss64_build(&g_mem64, &g_tss64)) {
        serial64_write("unavailable reason=");
        serial64_write(pc5_tss64_reason(&g_tss64));
        serial64_write("\n");
        return;
    }
    if (!pc5_tss64_install(&g_mem64, &g_tss64)) {
        serial64_write("failed reason=");
        serial64_write(pc5_tss64_reason(&g_tss64));
        serial64_write(" frames=0\n");
        pc5_tss64_release(&g_mem64, &g_tss64);
        return;
    }
    /* Arming the routing itself, then taking it away again, on the live table: the rule
     * being tested is the one that keeps a fault honest - a vector the kernel has a
     * handler for accepts an IST index of 1..7, a vector it has none for accepts nothing,
     * and 0 (which means "stay on the current stack", the thing a caller trying to be safe
     * never wants) is refused.  Nothing here claims a double fault was delivered onto the
     * stack: it claims the writer and the reader agree, on the table the CPU is actually
     * using.  The presence rule is folded into the range rule because this kernel installs
     * a present gate on every vector 0..31, so no in-range vector is absent to try. */
    {
        int out_of_range = idt64_set_ist(40, 1);    /* handler installed only for 0..31 */
        int zero = idt64_set_ist(PC5_VECTOR_DOUBLE_FAULT, 0);
        int too_big = idt64_set_ist(PC5_VECTOR_DOUBLE_FAULT, 8);
        int armed = idt64_set_ist(PC5_VECTOR_DOUBLE_FAULT, PC5_TSS64_IST_DOUBLE_FAULT);
        int reads_back = idt64_gate_ist(PC5_VECTOR_DOUBLE_FAULT) == PC5_TSS64_IST_DOUBLE_FAULT;
        idt64_clear_ist(PC5_VECTOR_DOUBLE_FAULT);
        int cleared = idt64_gate_ist(PC5_VECTOR_DOUBLE_FAULT) == 0;
        if (out_of_range || zero || too_big || !armed || !reads_back || !cleared) {
            pc5_tss64_release(&g_mem64, &g_tss64);
            serial64_write("failed reason=ist-rule-broken frames=0\n");
            return;
        }
        serial64_write("ok arm-rules=1");
    }
    /* TR.  The instruction is executed here, on the boot path, by the kernel that owns the
     * TSS -- not by a harness on its behalf.  `pc5_tss64_arm` is what decides whether the
     * gates below may be armed, and it decides from three readbacks (STR, the descriptor
     * through GDTR, the TSS through that descriptor) rather than from the fact that the
     * instruction assembled. */
    tr_ok = pc5_tss64_arm(&g_tss64);

    /* Arming the interrupt stack routing, and the rule that makes it safe: a gate with a
     * non-zero IST field makes fault delivery read `ISTn` out of *whatever TSS TR points at*,
     * so the gates may name an IST slot if and only if TR names this TSS.  That is the same
     * rule this file had before LTR was executed -- when it could only ever leave TR
     * unloaded, which is why `ist-df` and `ist-nmi` read 0 -- stated as a two-way agreement
     * now that both halves of it can be true.  A one-way rule would let a future edit arm the
     * gates while the readbacks were failing and still print a clean line. */
    if (tr_ok) {
        if (!idt64_set_ist(PC5_VECTOR_DOUBLE_FAULT, PC5_TSS64_IST_DOUBLE_FAULT)
            || !idt64_set_ist(PC5_VECTOR_NMI, PC5_TSS64_IST_NMI)) {
            serial64_write("failed reason=ist-arm-refused frames=0\n");
            return;
        }
        if (idt64_gate_ist(PC5_VECTOR_DOUBLE_FAULT) != PC5_TSS64_IST_DOUBLE_FAULT
            || idt64_gate_ist(PC5_VECTOR_NMI) != PC5_TSS64_IST_NMI) {
            idt64_clear_ist(PC5_VECTOR_DOUBLE_FAULT);
            idt64_clear_ist(PC5_VECTOR_NMI);
            pc5_tss64_release(&g_mem64, &g_tss64);
            serial64_write("failed reason=ist-readback-mismatch frames=0\n");
            return;
        }
    } else if (idt64_gate_ist(PC5_VECTOR_DOUBLE_FAULT) != 0
               || idt64_gate_ist(PC5_VECTOR_NMI) != 0) {
        idt64_clear_ist(PC5_VECTOR_DOUBLE_FAULT);
        idt64_clear_ist(PC5_VECTOR_NMI);
        pc5_tss64_release(&g_mem64, &g_tss64);
        serial64_write("failed reason=ist-armed-without-tr frames=0\n");
        return;
    }
    serial64_write(" tss=");
    serial64_hex(g_tss64.tss_base);
    serial64_write(" gdt=");
    serial64_hex(g_tss64.gdt_base);
    serial64_write(" limit=");
    serial64_dec(g_tss64.gdt_limit);
    serial64_write(" gdtr=");
    serial64_hex(g_tss64.gdtr_base);
    serial64_write(" cpl=");
    serial64_dec(g_tss64.cpl);
    serial64_write(" ist1=");
    serial64_hex(g_tss64.ist_top[PC5_TSS64_IST_DOUBLE_FAULT]);
    serial64_write(" ist2=");
    serial64_hex(g_tss64.ist_top[PC5_TSS64_IST_NMI]);
    serial64_write(" ist-df=");
    serial64_dec(idt64_gate_ist(PC5_VECTOR_DOUBLE_FAULT));
    serial64_write(" ist-nmi=");
    serial64_dec(idt64_gate_ist(PC5_VECTOR_NMI));
    serial64_write(" frames=");
    serial64_dec(g_tss64.frames_taken);
    serial64_write(" free-after=");
    serial64_dec(pc5_mem64_stats(&g_mem64)->free);
    /* The fields that make the claim checkable from outside.  `str=` is what the CPU
     * reports as its own task register, `busy=` is the descriptor type transition the load
     * performs in memory, `desc=` is the base and limit decoded out of the table GDTR names,
     * and `iomap=` is the TSS's own last field read back through that base.  A gate that can
     * read all four can tell "TR holds 0x20 because the CPU loaded it" from "TR holds 0x20
     * because something wrote the same number into a log". */
    serial64_write(" tr=");
    serial64_write(g_tss64.tr_armed ? "loaded" : "not-loaded");
    serial64_write(" str=");
    serial64_hex(g_tss64.tr_selector);
    serial64_write(" desc=");
    serial64_hex(g_tss64.desc_base);
    serial64_write("/");
    serial64_dec(g_tss64.desc_limit);
    serial64_write(" busy=");
    serial64_write(pc5_tss64_busy_transition(&g_tss64));
    serial64_write(" iomap=");
    serial64_dec(pc5_tss64_iomap_base(&g_tss64));
    if (!g_tss64.tr_armed) {
        serial64_write(" reason=");
        serial64_write(pc5_tss64_reason(&g_tss64));
    }
    serial64_write("\n");
}

/* Raise a double fault on purpose, and ask what the CPU did with the stack.
 *
 * Everything `report_tss` prints is a *precondition*: an armed gate, a loaded TR, a TSS whose
 * IST1 field names a granted page.  What it cannot show is the consequence those exist for --
 * that a fault arriving on a broken stack is delivered somewhere safe.  This raises vector 8
 * (a `#DF`, from `int $8`, which the CPU delivers through the gate exactly as it would a real
 * one), and reads back where the handler found itself.  Two outcomes are proof and one is a
 * measurement: the frame lands inside the IST1 page, which the ordinary stack is not, and the
 * kernel is still running afterwards with DF clear.
 *
 * The mechanism is in idt64.S and it is scaffolding, not recovery: the IDT's #DF gate is
 * pointed at a handler that records three words, restores RFLAGS (the CPU sets DF while
 * delivering, and leaving it set would turn the next fault into a real double fault -- a probe
 * that made the machine worse to measure it would be a bad trade), and jumps back to an address
 * this function recorded itself.  A real #DF is not returnable, and nothing here pretends
 * otherwise: this returns because the fault was invited and its stack was known good, which is
 * precisely the situation a handler must never assume on its own.
 *
 * It runs only when TR was loaded, because an IST field on a gate is read out of *whatever TSS
 * TR names*: with TR pointing elsewhere, this would either deliver onto a stranger's stack or
 * fault while handling a fault, and both of those look like a passing test in the log. */
/* idt64.S: the probe's entry point and the one-word mailbox it reads.  The pointer is a plain
 * global rather than a function argument because the handler is entered through the IDT, with no
 * caller to hand it anything, and it is declared here rather than in a header because the only
 * legitimate user is the function below -- `struct pc5_tss64_probe`'s layout is asserted against
 * the assembly in tss64.c, and the offsets the mailbox is written at are the same ones.  They are
 * not in a header on purpose: the only legitimate caller is the function below, and a declaration
 * in `tss64.h` would advertise a way to arm the mailbox without arming the gate that reads it. */
extern void pc5_ist_probe_entry(void);
extern u64 pc5_ist_probe_ctx;

static void report_tss_ist_probe(void)
{
    struct pc5_tss64_probe probe;
    const char *which = "unreported";
    volatile u64 canary = 0xC0FFEE1234567890ull;
    u64 i;
    int delivered = 0;

    serial64_write("PC5-64-TSS-DF ");
    if (!g_tss64.tr_armed) {
        serial64_write("status=skipped reason=tr-not-loaded\n");
        return;
    }
    /* Arm the gate, then the gate's target.  Both are read back afterwards rather than
     * assumed, and the IST field is checked *before* the redirect because the redirect is what
     * could plausibly lose it: `idt64_set_gate_target` moves only the offset, and if it moved
     * more than that the number printed below would say so. */
    if (idt64_gate_ist(PC5_VECTOR_DOUBLE_FAULT) != PC5_TSS64_IST_DOUBLE_FAULT) {
        serial64_write("status=refused reason=gate-not-armed\n");
        return;
    }
    probe.rsp_before = 0;
    probe.resume = 0;
    probe.gate_ist = idt64_gate_ist(PC5_VECTOR_DOUBLE_FAULT);
    probe.rsp_delivery = 0;
    /* The bound the frame has to land inside is the IST1 the *loaded TSS* names, read back
     * through the descriptor -- not `ist_top[]`, which is only the kernel's memory of writing
     * it.  Quoting the builder here would let a TSS whose field was overwritten after the build
     * pass the window check against a page nobody is using. */
    probe.ist_top = pc5_tss64_ist_live(&g_tss64, PC5_TSS64_IST_DOUBLE_FAULT);
    if (probe.ist_top == 0ull || probe.ist_top != g_tss64.ist_top[PC5_TSS64_IST_DOUBLE_FAULT]) {
        serial64_write("status=refused reason=ist-live-readback\n");
        return;
    }
    /* Touch the page before asking the CPU to.  A faulting store here reports a #PF at a rip the
     * reader can identify, which localises the problem to the mapping; a #PF whose rip is the
     * `int` itself can only mean the delivery, and the two have very different fixes -- one is
     * the pool handing out a page the platform will not let anyone write, the other is the TSS
     * field or the descriptor.  Without this line, the pair is indistinguishable in the log. */
    {
        volatile u64 *sentinel = (volatile u64 *)(unsigned long)(probe.ist_top - 256ull);
        *sentinel = 0xA5A5A5A500000000ull | probe.ist_top;
        if (*sentinel != (0xA5A5A5A500000000ull | probe.ist_top)) {
            serial64_write("status=refused reason=ist-page-not-writable\n");
            return;
        }
    }
    probe.flags_after = 0;
    if (!idt64_set_gate_target(PC5_VECTOR_DOUBLE_FAULT, (u64)(unsigned long)&pc5_ist_probe_entry)) {
        serial64_write("status=refused reason=gate-redirect-refused\n");
        return;
    }
    pc5_ist_probe_ctx = (u64)(unsigned long)&probe;
    /* One-shot on purpose: the handler clears the pointer as it returns, so a second delivery
     * -- a #DF raised by the probe itself going wrong -- halts on the ordinary path rather than
     * bouncing off the same recorded RSP twice, which would corrupt the stack silently and keep
     * running. */
    __asm__ volatile(
        "leaq 1f(%%rip), %%rax\n\t"
        "movq %%rax, 8(%[p])\n\t"
        "movq %%rsp, 0(%[p])\n\t"
        "int $8\n\t"
        "1:\n\t"
        "pushfq\n\t"
        "popq 40(%[p])\n\t"
        :
        : [p] "r"((u64)(unsigned long)&probe)
        : "rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11", "memory", "cc");
    /* The frame, read back off the stack the CPU switched to.  Nothing has used that page since
     * the handler left it, and the handler itself deliberately read nothing from it -- so this is
     * the CPU's own writing, seen from outside the layout argument. */
    if (probe.rsp_delivery != 0ull) {
        const volatile u64 *frame = (const volatile u64 *)(unsigned long)probe.rsp_delivery;
        u64 i;

        for (i = 0; i < PC5_TSS64_PROBE_WORDS - 6ull; i++)
            probe.frame[i] = frame[i];
    }
    delivered = pc5_tss64_probe_verdict(&g_tss64, &probe, &which);
    idt64_set_gate_target(PC5_VECTOR_DOUBLE_FAULT, 0u);
    serial64_write("status=");
    serial64_write(delivered ? "ok" : "failed");
    serial64_write(" reason=");
    serial64_write(which);
    serial64_write(" rsp=");
    serial64_hex(probe.rsp_delivery);
    serial64_write(" rsp-before=");
    serial64_hex(probe.rsp_before);
    serial64_write(" ist1=");
    serial64_hex(probe.ist_top);
    serial64_write(" resume=");
    serial64_hex(probe.resume);
    serial64_write(" gate-ist=");
    serial64_dec(probe.gate_ist);
    serial64_write(" df=");
    serial64_dec((probe.flags_after & 0x400ull) != 0ull);
    serial64_write(" frame=");
    for (i = 0; i < PC5_TSS64_PROBE_WORDS - 6ull; i++) {
        serial64_write(i == 0ull ? "" : ",");
        serial64_hex(probe.frame[i]);
    }
    /* The canary is the negative control for the claim that matters most.  A delivery that
     * silently used the ordinary stack would still show a plausible RSP if the IST were wired
     * to it; it would also, for a frame of 40 bytes, be unlikely to touch this word -- so the
     * proof of "the interrupt stack is a different stack" is that the fault did *not* arrive on
     * the one we were using.  Kept volatile so the compiler cannot fold the comparison away or
     * keep the value in a register the handler never touched. */
    serial64_write(" stack-intact=");
    serial64_write(canary == 0xC0FFEE1234567890ull ? "1" : "0");
    /* The frame the CPU left behind, dumped as it sits in memory.  The handler above records
     * three of these words through fixed offsets, and the offsets are exactly the kind of thing
     * two files can each get self-consistently wrong about -- so the raw quads go in the log too,
     * with `resume=` as the needle: whichever word equals the address after the `int` is the RIP,
     * and a report of the frame's layout that disagrees with the dump is a bug in the dump. */
    serial64_write(" gate-restored=");
    serial64_write(idt64_gate_uses_default_stub(PC5_VECTOR_DOUBLE_FAULT) ? "1" : "0");
    serial64_write("\n");
}

static void report_memory(const struct pc5_boot64 *bi)
{
    const char *reason = "unreported";
    int selftest_ok = 1;
    int ok = pc5_mem64_init(&g_mem64, bi, (u64)(unsigned long)bi,
                            g_mem64_bitmap, MEM64_BITMAP_BYTES);
    struct pc5_mem64_stats stats;
    pc5_mem64_snapshot(&g_mem64, &stats);

    serial64_write("PC5-64-MEM status=");
    serial64_write(ok ? "ok" : "unavailable");
    serial64_write(" reason=");
    serial64_write(ok ? "ok" : (g_mem64.reason ? g_mem64.reason : reason));
    serial64_write(" frames=");
    serial64_dec(stats.frames);
    serial64_write(" conventional=");
    serial64_dec(stats.conventional);
    serial64_write(" conventional_total=");
    serial64_dec(stats.conventional_total);
    serial64_write(" claimed=");
    serial64_dec(stats.claimed);
    serial64_write(" free=");
    serial64_dec(stats.free);
    serial64_write(" largest=");
    serial64_dec(stats.largest_at_init);
    serial64_write(" bitmap=");
    serial64_hex(MEM64_BITMAP_BYTES);
    serial64_write(" span=");
    serial64_hex(bi->identity_span);
    serial64_write("\n");
    if (!ok)
        return;

    /* The self-test is the part that can only be done here, with the map this machine
     * actually reported.  Each number printed below is a property of this allocator,
     * not a constant: `largest` comes from the map the loader was handed, so a fixture
     * with a different map changes the values and the checks still mean what they say.
     *
     * It needs a pool with room to be interesting - a one-page grant, a three-page
     * grant, and two eight-page runs that must not touch.  A sparse map is not a broken
     * allocator, so a pool too small for the battery is reported as `skipped`, not as
     * `failed`; the stub firmware's map always clears the bar, so `ok` is what the
     * executed gate expects. */
    int roomy = g_mem64.stats.free >= 32 && g_mem64.stats.largest_at_init >= 8;
    if (!roomy) {
        serial64_write("PC5-64-MEM-SELFTEST result=skipped reason=pool-too-small free=");
        serial64_dec(g_mem64.stats.free);
        serial64_write(" largest=");
        serial64_dec(g_mem64.stats.largest_at_init);
        serial64_write("\n");
        return;
    }

    u64 free_before = g_mem64.stats.free;
    u64 one = 0, three = 0, sentinel = 0xDEAD;
    int got_one = pc5_mem64_alloc(&g_mem64, 1, &one);
    int got_three = pc5_mem64_alloc(&g_mem64, 3, &three);
    int zero_refused = pc5_mem64_alloc(&g_mem64, 0, &sentinel);
    int oversized_refused = pc5_mem64_alloc(&g_mem64, g_mem64.stats.frames + 1, &sentinel);
    /* A refusal has to clear the out-parameter, or a caller reusing a variable
     * would read the previous grant as the result of the failed request. */
    int sentinel_cleared = sentinel == 0;

    /* Deliberately not a write.  Dereferencing a grant here is only defined on a
     * machine that has mapped what its map describes, and this function runs before the
     * kernel has anything of its own living in an allocated page - so a write would
     * test the fixture's memory layout rather than the allocator.  What is asserted
     * instead is the property the write was standing in for: the run is conventional
     * memory inside the identity span, which is a statement about the pool's inputs and
     * needs no memory reference at all. */
    int grant_in_map = (!got_one || pc5_mem64_range_usable(&g_mem64, one,
                                                            one + PC5_MEM64_PAGE)) &&
        (!got_three || pc5_mem64_range_usable(&g_mem64, three,
                                              three + 3 * PC5_MEM64_PAGE));

    /* The largest run init measured, plus one page, must be refused: a scan that
     * stopped early or wrapped its arithmetic would hand out a run that is not
     * free.  Asking for "every remaining page" would prove nothing, because a map
     * with claims in the middle has no such contiguous run - and the pool refuses
     * that request, which is the right answer for the wrong question. */
    u64 too_big = 1;
    int over_largest_refused = !pc5_mem64_alloc(&g_mem64, stats.largest_at_init + 1,
                                                &too_big) && too_big == 0;

    int disjoint = got_one && got_three &&
        !pc5_ranges_overlap(one, one + PC5_MEM64_PAGE,
                            three, three + 3 * PC5_MEM64_PAGE);
    int validated = pc5_mem64_validate(&g_mem64, one, 1) &&
        pc5_mem64_validate(&g_mem64, three, 3);
    int claims_clean = !pc5_mem64_overlaps_claimed(&g_mem64, one, 1) &&
        !pc5_mem64_overlaps_claimed(&g_mem64, three, 3);
    /* The zero page is the one frame the pool reserves by policy rather than by
     * map: a caller that leaves an address field at zero must not have it alias a
     * real allocation. */
    int zero_page_held = !pc5_mem64_validate(&g_mem64, 0, 1) &&
        pc5_mem64_overlaps_claimed(&g_mem64, 0, 1);
    int refused = zero_refused == 0 && oversized_refused == 0 && sentinel_cleared;

    int freed_one = pc5_mem64_free(&g_mem64, one, 1);
    int refused_again = pc5_mem64_free(&g_mem64, one, 1);       /* the double free */
    int unaligned = pc5_mem64_free(&g_mem64, three + 1, 1);     /* not a frame edge */
    pc5_mem64_free(&g_mem64, three, 3);

    /* First-fit is a documented property, so it is asserted rather than assumed:
     * the page just freed is the first page the next request finds. */
    u64 reused = 0;
    int got_reuse = pc5_mem64_alloc(&g_mem64, 1, &reused) && reused == one;
    pc5_mem64_free(&g_mem64, reused, 1);

    /* Defence in depth, and the only part of this battery that a *running* kernel can
     * observe: frame 0 is claimed by policy rather than by the map, so the bitmap alone
     * must never be able to grant it.  Clearing the bit here is safe for exactly one
     * reason - nothing else is allocating while this runs - and the bit goes straight
     * back, so the probe cannot leak into the pool the kernel later uses. */
    u64 refusals_before = g_mem64.stats.refusals;
    g_mem64_bitmap[0] = (u8)(g_mem64_bitmap[0] & ~1u);
    u64 probe = 0xA5A5;
    int probe_refused = !pc5_mem64_alloc(&g_mem64, 1, &probe) && probe == 0;
    g_mem64_bitmap[0] = (u8)(g_mem64_bitmap[0] | 1u);
    int probe_counted = g_mem64.stats.refusals == refusals_before + 1;
    if (probe)
        pc5_mem64_free(&g_mem64, probe, 1);   /* only reachable on a broken pool */

    u64 block_a = 0, block_b = 0;
    int got_a = pc5_mem64_alloc(&g_mem64, 8, &block_a);
    int got_b = pc5_mem64_alloc(&g_mem64, 8, &block_b);
    int blocks_apart = got_a && got_b &&
        !pc5_ranges_overlap(block_a, block_a + (8 * PC5_MEM64_PAGE),
                            block_b, block_b + (8 * PC5_MEM64_PAGE));
    pc5_mem64_free(&g_mem64, block_a, 8);
    pc5_mem64_free(&g_mem64, block_b, 8);
    pc5_mem64_snapshot(&g_mem64, &stats);

    if (!got_one || !got_three || !grant_in_map || !zero_page_held ||
        !probe_refused || !probe_counted ||
        !over_largest_refused || !disjoint || !validated || !claims_clean ||
        !refused || !freed_one || refused_again || unaligned || !got_reuse ||
        !blocks_apart || stats.free != free_before)
        selftest_ok = 0;

    serial64_write("PC5-64-MEM-SELFTEST one=");
    serial64_hex(one);
    serial64_write(" three=");
    serial64_hex(three);
    serial64_write(" grant-in-map=");
    serial64_write(grant_in_map ? "1" : "0");
    serial64_write(" bitmap-lie-refused=");
    serial64_write(probe_refused ? "1" : "0");
    serial64_write(" refusal-counted=");
    serial64_write(probe_counted ? "1" : "0");
    serial64_write(" disjoint=");
    serial64_write(disjoint ? "1" : "0");
    serial64_write(" validated=");
    serial64_write(validated ? "1" : "0");
    serial64_write(" claims-clean=");
    serial64_write(claims_clean ? "1" : "0");
    serial64_write(" zero-page-reserved=");
    serial64_write(zero_page_held ? "1" : "0");
    serial64_write(" zero-refused=");
    serial64_write(zero_refused == 0 ? "1" : "0");
    serial64_write(" oversized-refused=");
    serial64_write(oversized_refused == 0 ? "1" : "0");
    serial64_write(" refusal-clears-base=");
    serial64_write(sentinel_cleared ? "1" : "0");
    serial64_write(" over-largest-refused=");
    serial64_write(over_largest_refused ? "1" : "0");
    serial64_write(" double-free-refused=");
    serial64_write(refused_again == 0 ? "1" : "0");
    serial64_write(" unaligned-free-refused=");
    serial64_write(unaligned == 0 ? "1" : "0");
    serial64_write(" first-fit-reuse=");
    serial64_write(got_reuse ? "1" : "0");
    serial64_write(" blocks-apart=");
    serial64_write(blocks_apart ? "1" : "0");
    serial64_write(" free-restored=");
    serial64_write(stats.free == free_before ? "1" : "0");
    serial64_write(" result=");
    serial64_write(selftest_ok ? "ok" : "failed");
    serial64_write("\n");
    if (!selftest_ok) {
        /* Not `fail()`.  Nothing in this kernel has allocated from the pool yet, so a
         * bookkeeping disagreement is not a reason to stop a machine that would
         * otherwise boot.  It is a reason to make the pool unusable, which is what
         * `ready = 0` does - every entry point refuses when the pool is not ready -
         * and it is reported on the serial line, where the executed gate turns it into
         * a build failure. */
        g_mem64.ready = 0;
        g_mem64.reason = "mem64-selftest-failed";
        serial64_write("PC5-64-MEM status=unavailable reason=mem64-selftest-failed\n");
    }
}

/* kernel64/start.S: switches RSP to a validated stack and calls the continuation
 * there.  It never returns. */
void pc5_adopt_stack(u64 stack_top, void (*continuation)(void *), void *argument)
    __attribute__((noreturn));


/* kernel64/start.S: switches RSP to a validated stack and calls the continuation
 * there.  It never returns. */
void pc5_adopt_stack(u64 stack_top, void (*continuation)(void *), void *argument)
    __attribute__((noreturn));

static void continue_on_handed_stack(void *argument);

void kmain(struct pc5_boot64 *bi)
{
    serial64_init();
    serial64_write("PC5-64BIT-KERNEL-ENTRY\n");

    /* Install exception capture before anything that can fault: until this runs,
     * a bad pointer is a silent triple-fault reset with no evidence at all. */
    idt64_install();
    serial64_write("PC5-64-IDT installed=256 present=32\n");

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

/* Time, calibrated once against the PIT and then measured as TSC deltas. Reported
 * rather than required: a machine whose PIT does not answer still boots, and the log
 * says the clock is uncalibrated instead of presenting a zero rate as a working one. */
static void report_timer(void)
{
    struct pc5_timer64 timer;

    timer64_init(&timer);
    timer64_print(&timer);
    if (!timer.calibrated) {
        serial64_write("PC5-64-TIMER not-calibrated reason=\"the PIT did not "
                       "measure a TSC rate\"\n");
    }
}

/* How many CPUs exist, from firmware. `CPUID` can only describe the core it is
 * running on, so the MADT is the only firmware-published source for this.
 *
 * Reported, not acted on: starting a secondary processor needs an INIT-SIPI sequence
 * and a low-memory trampoline, neither of which this kernel has. So `M2a`'s "second
 * CPU brought up or its absence reported" is answered with the second option, and the
 * log says so rather than implying the cores are usable. */
static void report_topology(const struct pc5_boot64 *boot)
{
    struct pc5_madt64 madt;
    const char *why;

    why = madt64_scan(&madt, boot->acpi_rsdp, &g_map);
    if (why != 0) {
        serial64_write("PC5-64-TOPOLOGY not-parsed reason=\"");
        serial64_write(why);
        serial64_write("\"\n");
        return;
    }

    madt64_print(&madt);
    if (madt.cpu_count <= 1) {
        serial64_write("PC5-64-TOPOLOGY single-cpu=1 usable=1 bringup=not-attempted\n");
    } else {
        serial64_write("PC5-64-TOPOLOGY single-cpu=0 usable=1 present=");
        serial64_dec(madt.cpu_count);
        serial64_write(" bringup=not-attempted reason=\"no INIT-SIPI trampoline\"\n");
    }
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
    report_memory(bi);
    report_tss();
    report_local_apic();
    report_timer();
    report_topology(bi);

    /* Every mandatory check passed; only now is the marker emitted. */
    serial64_write("PC5-64BIT-LONG-MODE-OK\n");
    serial64_write("PC5-64-DONE\n");

    /* Last, and after the marker on purpose.  The probe below halts the CPU if it cannot
     * complete, and an environment that cannot deliver a software interrupt at all -- which is
     * what the emulator in `make stubtest64` turns out to be, proven by
     * tests/test_unicorn_soft_int.py rather than asserted -- would otherwise take every check
     * that follows down with it and report a broken boot.  Emitting DONE first says which of the
     * two happened: a boot that reached the marker and then printed a failed probe line is a
     * machine whose IST delivery was measured and did not match, and a run that stops at the
     * marker with no probe line is a harness that never got that far. */
    report_tss_ist_probe();
    halt_forever();
}
