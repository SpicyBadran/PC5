/* Runtime page-table walker for the PC5 64-bit kernel.
 *
 * Why this exists: the loader reports the span it *intended* to map, and the
 * kernel checks that CR3 is the root the loader declared.  Neither of those is
 * evidence that the hierarchy actually reaches every page.  A page-directory
 * entry that was written but never linked from its PDPT is invisible to both:
 * the reported span is a constant, and CR3 is correct, yet the region is
 * unreachable and a dereference there faults.
 *
 * So this walks the real tables the CPU is using, from the real CR3, and asks
 * the hardware's own question for each address: which 4 KiB page does this
 * translate to?  The walker is deliberately free of PC5 policy - it takes a
 * root and an address and describes what it found.  The policy (which
 * addresses must be identity-mapped) lives in the caller, so the walker can be
 * unit-tested against synthetic tables without a CPU.
 */
#include "pagetable64.h"
#include "include/pc5_io.h"

/* An entry is present when bit 0 is set (Intel SDM Vol 3A 4.5 / AMD APM 5.3). */
#define PTE_PRESENT 0x1ull
/* PS (page size) means "this entry terminates the walk": 2 MiB at PD level,
 * 1 GiB at PDPT level.  Only meaningful on entries that do not point to a
 * further table. */
#define PTE_PS 0x80ull
/* Physical address bits architecturally defined for a 64-bit page-table entry.
 * Bits 52:12 hold the frame; the rest are flags or reserved. */
#define PTE_ADDR_MASK 0x000FFFFFFFFFF000ull

/* 4 KiB pages, 512 entries each.  A 2 MiB page covers 512 of them. */
#define PT_PAGE_SHIFT 12
#define PT_ENTRIES 512ull
#define PT_2MIB_SHIFT 21

static u64 entry_address(u64 entry)
{
    return entry & PTE_ADDR_MASK;
}

/* The index a virtual address supplies at each level. */
static u64 pml4_index(u64 va) { return (va >> 39) & 0x1FFull; }
static u64 pdpt_index(u64 va) { return (va >> 30) & 0x1FFull; }
static u64 pd_index(u64 va)   { return (va >> 21) & 0x1FFull; }
static u64 pt_index(u64 va)   { return (va >> 12) & 0x1FFull; }

/* Read one entry out of a table the CPU is walking.  `read_u64` is injected so
 * the same code runs against real memory and against a test's table. */
static u64 read_entry(pc5_read_u64_fn read_u64, u64 table, u64 index)
{
    return read_u64(table + index * 8ull);
}

enum pc5_page_size pc5_walk(struct pc5_walk_result *out,
                            pc5_read_u64_fn read_u64,
                            u64 root, u64 va)
{
    u64 pml4e, pdpte, pde, pte;

    out->virtual_address = va;
    out->physical_address = 0;
    out->page_size = PC5_PAGE_NONE;
    out->failure = PC5_WALK_OK;
    out->pml4_present = 0;
    out->pdpt_present = 0;
    out->pd_present = 0;
    out->pt_present = 0;

    if (root == 0 || (root & 0xFFFull)) {
        out->failure = PC5_WALK_BAD_ROOT;
        return PC5_PAGE_NONE;
    }

    pml4e = read_entry(read_u64, root, pml4_index(va));
    if (!(pml4e & PTE_PRESENT)) {
        out->failure = PC5_WALK_MISSING_PML4;
        return PC5_PAGE_NONE;
    }
    out->pml4_present = 1;
    /* At PML4, PS is reserved and must be zero; a set PS here means the table
     * is not a 4-level table this code can interpret. */
    if (pml4e & PTE_PS) {
        out->failure = PC5_WALK_RESERVED_PS;
        return PC5_PAGE_NONE;
    }

    pdpte = read_entry(read_u64, entry_address(pml4e), pdpt_index(va));
    if (!(pdpte & PTE_PRESENT)) {
        out->failure = PC5_WALK_MISSING_PDPT;
        return PC5_PAGE_NONE;
    }
    out->pdpt_present = 1;

    /* A 1 GiB page terminates the walk here. */
    if (pdpte & PTE_PS) {
        u64 base = entry_address(pdpte) & ~((1ull << 30) - 1);
        out->physical_address = base + (va & ((1ull << 30) - 1));
        out->page_size = PC5_PAGE_1GIB;
        return PC5_PAGE_1GIB;
    }

    pde = read_entry(read_u64, entry_address(pdpte), pd_index(va));
    if (!(pde & PTE_PRESENT)) {
        out->failure = PC5_WALK_MISSING_PD;
        return PC5_PAGE_NONE;
    }
    out->pd_present = 1;

    /* A 2 MiB page terminates the walk here.  This is the size the PC5 loader
     * uses for the identity map, so it is the expected terminus. */
    if (pde & PTE_PS) {
        u64 base = entry_address(pde) & ~((1ull << PT_2MIB_SHIFT) - 1);
        out->physical_address = base + (va & ((1ull << PT_2MIB_SHIFT) - 1));
        out->page_size = PC5_PAGE_2MIB;
        return PC5_PAGE_2MIB;
    }

    pte = read_entry(read_u64, entry_address(pde), pt_index(va));
    if (!(pte & PTE_PRESENT)) {
        out->failure = PC5_WALK_MISSING_PT;
        return PC5_PAGE_NONE;
    }
    out->pt_present = 1;
    out->physical_address = entry_address(pte) + (va & 0xFFFull);
    out->page_size = PC5_PAGE_4KIB;
    return PC5_PAGE_4KIB;
}

/* ------------------------------------------------------------------ policy */

int pc5_check_identity(struct pc5_identity_case *out,
                       pc5_read_u64_fn read_u64,
                       u64 root, u64 limit, u64 va)
{
    struct pc5_walk_result walk;

    out->virtual_address = va;
    out->present = 0;
    out->pml4_present = 0;
    out->pdpt_present = 0;
    out->pd_present = 0;
    out->pt_present = 0;
    out->page_size = PC5_PAGE_NONE;
    out->physical_address = 0;
    out->identity = 0;
    out->failure = 0;
    out->note = "";

    /* An address at or above the declared span is not required to be mapped by
     * the identity map, so asking about it is a caller error, not a defect. */
    if (va >= limit) {
        out->failure = 0;
        out->note = "outside the declared identity span";
        return 0;
    }

    pc5_walk(&walk, read_u64, root, va);
    out->page_size = walk.page_size;

    if (walk.failure != PC5_WALK_OK) {
        out->pml4_present = walk.pml4_present;
        out->pdpt_present = walk.pdpt_present;
        out->pd_present = walk.pd_present;
        out->pt_present = walk.pt_present;
        out->failure = walk.failure;
        out->note = pc5_walk_failure_name(walk.failure);
        return 0;
    }

    out->present = 1;
    out->pml4_present = walk.pml4_present;
    out->pdpt_present = walk.pdpt_present;
    out->pd_present = walk.pd_present;
    out->pt_present = walk.pt_present;
    out->physical_address = walk.physical_address;
    out->identity = (walk.physical_address == va);
    return out->identity;
}

const char *pc5_walk_failure_name(enum pc5_walk_failure failure)
{
    switch (failure) {
    case PC5_WALK_OK: return "ok";
    case PC5_WALK_BAD_ROOT: return "page-table root is null or misaligned";
    case PC5_WALK_RESERVED_PS: return "PML4 entry has the reserved PS bit set";
    case PC5_WALK_MISSING_PML4: return "no PML4 entry";
    case PC5_WALK_MISSING_PDPT: return "no PDPT entry";
    case PC5_WALK_MISSING_PD: return "no page-directory entry";
    case PC5_WALK_MISSING_PT: return "no page-table entry";
    }
    return "unknown";
}

u64 real_read_u64(u64 address)
{
    /* Volatile tells the compiler the pointee can change without this function
     * writing it - which is true of memory firmware described to us. */
    return *(const volatile u64 *)(u64)address;
}

const char *describe_page_size(enum pc5_page_size size)
{
    switch (size) {
    case PC5_PAGE_4KIB: return "4k";
    case PC5_PAGE_2MIB: return "2m";
    case PC5_PAGE_1GIB: return "1g";
    case PC5_PAGE_NONE:
    default:            return "none";
    }
}
