/* Building the identity map is the only thing that has to be exactly right, so it is
 * written once, without firmware dependencies, and unit-tested on the host against
 * real entry values and real out-of-bounds canaries (tests/test_pagetables64.py). */
#include "pagetables.h"

static void store(u64 address, u64 value)
{
    *(volatile u64 *)(unsigned long)address = value;
}

static u64 load(u64 address)
{
    return *(volatile u64 *)(unsigned long)address;
}

static int block_ok(u64 pt)
{
    /* The block must be page-aligned and must not wrap the 64-bit address space
     * when its six pages are addressed. */
    return pt && !(pt & (PC5_PT_PAGE - 1)) &&
           pt <= ~0ull - (u64)PC5_PT_PAGES * PC5_PT_PAGE;
}

unsigned long pc5_pt_build_identity(u64 pt, u64 span)
{
    if (!block_ok(pt) || span == 0 || span > PC5_PT_SPAN)
        return 0;

    store(pc5_pt_pml4(pt), 0);
    store(pc5_pt_pdpt(pt), 0);
    for (unsigned i = 0; i < PC5_PT_PD_PAGES * PC5_PT_ENTRIES_PER_PAGE; i++)
        store(pc5_pt_pd_base(pt) + (u64)i * 8ull, 0);

    store(pc5_pt_pml4(pt), pc5_pt_pdpt(pt) | 1ull | 2ull);   /* present, writable */
    store(pc5_pt_pdpt(pt), pc5_pt_pd_base(pt) | 1ull | 2ull);

    unsigned long total = (unsigned long)(span / PC5_PT_2MIB);
    if (total == 0 || total > (unsigned long)PC5_PT_MAX_INDEX + 1ul)
        return 0;
    for (unsigned long index = 0; index < total; index++) {
        store(pc5_pt_pd_base(pt) + index * 8ul,
              (index * PC5_PT_2MIB) | PC5_PT_FLAGS);
    }
    return total;
}

int pc5_pt_range_supported(u64 start, u64 size)
{
    if (size == 0)
        return 0;
    if (start >= PC5_PT_SPAN)
        return 0;
    return size <= PC5_PT_SPAN - start;      /* overflow-free by construction */
}

unsigned long pc5_pt_mark_uncached(u64 pt, u64 start, u64 end)
{
    if (!block_ok(pt) || end <= start)
        return 0;
    if (start >= PC5_PT_SPAN)
        return 0;                             /* fully above the map: nothing to do */
    if (end > PC5_PT_SPAN)
        end = PC5_PT_SPAN;                    /* clip: never index past the PD block */

    unsigned long first = (unsigned long)(start / PC5_PT_2MIB);
    unsigned long last = (unsigned long)((end - 1) / PC5_PT_2MIB);
    if (last > (unsigned long)PC5_PT_MAX_INDEX)
        last = (unsigned long)PC5_PT_MAX_INDEX;   /* defensive; the clip above holds */

    unsigned long changed = 0;
    for (unsigned long index = first; index <= last; index++) {
        u64 slot = pc5_pt_pd_base(pt) + index * 8ul;
        u64 entry = load(slot);
        if (!(entry & 1ull))
            continue;
        if (entry & PC5_PT_UC)
            continue;                          /* already uncached: not a change */
        store(slot, entry | PC5_PT_UC);
        changed++;
    }
    return changed;
}

int pc5_pt_lookup(u64 pt, u64 address, u64 *entry)
{
    if (!block_ok(pt) || address >= PC5_PT_SPAN)
        return 0;
    u64 pml4e = load(pc5_pt_pml4(pt) + ((address >> 39) & 0x1FF) * 8ull);
    if (!(pml4e & 1ull))
        return 0;
    u64 pdpte = load((pml4e & ~0xFFFull) + ((address >> 30) & 0x1FF) * 8ull);
    if (!(pdpte & 1ull))
        return 0;
    if (pdpte & (1ull << 7))
        return 0;                              /* 1 GiB leaf: not built here */
    u64 pde = load((pdpte & ~0xFFFull) + ((address >> 21) & 0x1FF) * 8ull);
    if (!(pde & 1ull))
        return 0;
    if (entry)
        *entry = pde;
    return 1;
}
