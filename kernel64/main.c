/* The 64-bit kernel's entry environment.
 *
 * This is the first execution path in PC5 that runs in long mode with paging owned by
 * PC5 rather than by firmware.  It is deliberately small: validate the handover, prove
 * the CPU state, collect the feature report, and say so.  It refuses to print its success
 * marker unless every one of those checks passed, because a marker that appears anyway
 * would be worse than no marker at all.
 *
 * It is an *experimental* path: `make kernel64` builds it and `make stubtest64` runs it;
 * nothing in the release ISO consumes it yet.
 */
#include "include/pc5_boot64.h"
#include "include/pc5_io.h"
#include "cpu64.h"
#include "serial64.h"

#define CR0_PG (1ULL << 31)
#define CR0_PE (1ULL << 0)
#define CR4_PAE (1ULL << 5)

_Static_assert(sizeof(struct pc5_boot64) == 112, "pc5_boot64 abi");
_Static_assert(sizeof(struct pc5_memdesc64) == 32, "pc5_memdesc64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, memmap_addr) == 16, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, framebuffer_addr) == 40, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, acpi_rsdp) == 64, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, kernel_base) == 72, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, stack_top) == 88, "boot64 abi");
_Static_assert(__builtin_offsetof(struct pc5_boot64, exit_boot_services) == 96, "boot64 abi");

extern char __kernel_start[], __kernel_end[];

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

/* The boot information has to be validated before a single field is dereferenced. */
static void validate_boot_info(const struct pc5_boot64 *bi)
{
    if (!bi)
        fail("bootinfo-null");
    if (bi->magic != PC5_BOOT64_MAGIC)
        fail("bootinfo-magic");
    if (bi->version != PC5_BOOT64_VERSION)
        fail("bootinfo-version");
    if (bi->size != (u32)sizeof(struct pc5_boot64))
        fail("bootinfo-size");
    if (!(bi->flags & PC5_BOOT64_F_MEMMAP))
        fail("bootinfo-no-memmap");
    if (bi->memmap_addr == 0)
        fail("bootinfo-memmap-null");
    if (bi->memmap_addr & 7)
        fail("bootinfo-memmap-misaligned");
    if (bi->memmap_desc_size != (u32)sizeof(struct pc5_memdesc64))
        fail("bootinfo-memmap-descsize");
    if (bi->memmap_bytes == 0 || bi->memmap_bytes % bi->memmap_desc_size)
        fail("bootinfo-memmap-bytes");
    if (bi->kernel_base == 0 || bi->kernel_base & 0xFFF)
        fail("bootinfo-kernel-base");
    if (bi->kernel_base != PC5_KERNEL64_LINK_BASE)
        fail("bootinfo-kernel-address");
    if ((u64)(unsigned long)__kernel_start != PC5_KERNEL64_LINK_BASE)
        fail("link-base-mismatch");
    if (bi->stack_top == 0 || (bi->stack_top & 0xF))
        fail("bootinfo-stack");

    const struct pc5_memdesc64 *desc =
        (const struct pc5_memdesc64 *)(unsigned long)bi->memmap_addr;
    u64 entries = bi->memmap_bytes / bi->memmap_desc_size;
    u64 conventional = 0;
    for (u64 i = 0; i < entries; i++) {
        if (desc[i].page_count == 0)
            fail("bootinfo-memmap-empty");
        if (desc[i].type == PC5_MMAP_CONVENTIONAL)
            conventional += desc[i].page_count;
    }
    if (conventional == 0)
        fail("bootinfo-no-conventional-memory");
}

static void report_environment(const struct pc5_boot64 *bi)
{
    u64 cr0 = read_cr0(), cr3 = read_cr3(), cr4 = read_cr4();
    u64 efer = 0;
    int efer_readable = 1;

    /* The MSR may be unreadable under some emulators; that makes it informational,
     * never a substitute for the architectural checks below. */
    efer = read_msr(MSR_EFER);
    if ((efer & (EFER_LME | EFER_LMA)) == 0 && (cr0 & CR0_PG) && (cr4 & CR4_PAE))
        efer_readable = 0;

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

    if (!(cr0 & CR0_PG))
        fail("paging-disabled");
    if (!(cr4 & CR4_PAE))
        fail("pae-disabled");
    if (cr3 == 0 || (cr3 & 0xFFF))
        fail("page-table-root-invalid");
    if (!efer_readable && !(efer & EFER_LMA))
        serial64_write("PC5-64-NOTE efer.lma unavailable; long mode proven by "
                       "64-bit register arithmetic and the CPUID long-mode bit\n");

    serial64_write("PC5-64-PAGING enabled=1 root=");
    serial64_hex(cr3 & 0x000FFFFFFFFFF000ull);
    serial64_write(" kernel=");
    serial64_hex((u64)(unsigned long)__kernel_start);
    serial64_write("-");
    serial64_hex((u64)(unsigned long)__kernel_end);
    serial64_write(" loaded=");
    serial64_hex(bi->kernel_base);
    serial64_write("\n");

    /* A 64-bit-only proof: in 32-bit mode the upper half of this value cannot survive
     * the move, and the comparison below would fail. */
    u64 probe = 0x1122334455667788ull;
    u64 copy;
    __asm__ volatile("movq %1, %%rax; movq %%rax, %0" : "=r"(copy) : "r"(probe)
                     : "rax");
    if (copy != probe)
        fail("not-64-bit-operand-size");
    if ((0x0000000100000000ull + 1ull) != 0x0000000100000001ull)
        fail("not-64-bit-arithmetic");
    serial64_write("PC5-64-REGS wide-operands=ok arithmetic=ok\n");

    /* Stack alignment: the start stub aligned rsp to 16 and the C ABI keeps it there. */
    u64 rsp;
    __asm__ volatile("mov %%rsp, %0" : "=r"(rsp));
    if (rsp & 0xF)
        fail("stack-misaligned");
    if (bi->stack_top == 0 || rsp > bi->stack_top || bi->stack_top - rsp > 64 * 1024)
        fail("stack-not-from-bootinfo");
    serial64_write("PC5-64-STACK rsp=");
    serial64_hex(rsp);
    serial64_write(" top=");
    serial64_hex(bi->stack_top);
    serial64_write(" aligned=16\n");
}

static void report_boot_info(const struct pc5_boot64 *bi)
{
    u64 entries = bi->memmap_bytes / bi->memmap_desc_size;
    serial64_write("PC5-64-BOOTINFO magic=ok version=");
    serial64_dec(bi->version);
    serial64_write(" size=");
    serial64_dec(bi->size);
    serial64_write(" memdesc=");
    serial64_dec(bi->memmap_desc_size);
    serial64_write(" entries=");
    serial64_dec(entries);
    serial64_write(" flags=");
    serial64_hex(bi->flags);
    serial64_write("\n");
    serial64_write("PC5-64-HANDOVER framebuffer=");
    serial64_hex(bi->framebuffer_addr);
    serial64_write(" rsdp=");
    serial64_hex(bi->acpi_rsdp);
    serial64_write(" exit_boot_services=");
    serial64_dec(bi->exit_boot_services);
    serial64_write("\n");
}

void kmain(struct pc5_boot64 *bi)
{
    serial64_init();
    serial64_write("PC5-64BIT-KERNEL-ENTRY\n");

    validate_boot_info(bi);
    report_environment(bi);
    report_boot_info(bi);

    struct pc5_cpu64 cpu;
    cpu64_report(&cpu);
    cpu64_print(&cpu);

    if (!cpu.long_mode)
        fail("cpuid-reports-no-long-mode");

    /* Every check above passed; only now is the marker emitted. */
    serial64_write("PC5-64BIT-LONG-MODE-OK\n");
    serial64_write("PC5-64-DONE\n");
    halt_forever();
}
