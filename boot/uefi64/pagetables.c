/* Building the identity map is the only thing that has to be exactly right, so it is
 * written once, without firmware dependencies, and unit-tested on the host against
 * real entry values (see tests/test_pagetables64.py). */
#include "pagetables.h"

static void store(u64 address, u64 value)
{
    *(volatile u64 *)(unsigned long)address = value;
}

static u64 load(u64 address)
{
    return *(volatile u64 *)(unsigned long)address;
}

unsigned long pc5_pt_build_identity(u64 pt, u64 span)
{
    if (!pt || (pt & (PC5_PT_PAGE - 1)) || span == 0 || span > PC5_PT_SPAN)
        return 0;

    store(pc5_pt_pml4(pt), 0);
    store(pc5_pt_pdpt(pt), 0);
    for (unsigned i = 0; i < (PC5_PT_PAGES - 2u) * 512u; i++)
        store(pc5_pt_pd_base(pt) + (u64)i * 8u, 0);

    store(pc5_pt_pml4(pt), pc5_pt_pdpt(pt) | 1ull | 2ull);   /* present, writable */
    store(pc5_pt_pdpt(pt), pc5_pt_pd_base(pt) | 1ull | 2ull);

    unsigned long mapped = 0;
    unsigned long total = (unsigned long)(span / PC5_PT_2MIB);
    unsigned long pd_pages = PC5_PT_PAGES - 2u;
    for (unsigned long index = 0; index < total; index++) {
        unsigned long pd_page = index / 512u;
        if (pd_page >= pd_pages)
            return 0;
        store(pc5_pt_pd_base(pt) + index * 8u,
              (index * PC5_PT_2MIB) | PC5_PT_FLAGS);
        mapped++;
    }
    return mapped;
}

unsigned long pc5_pt_mark_uncached(u64 pt, u64 start, u64 end)
{
    if (!pt || end <= start)
        return 0;
    unsigned long first = (unsigned long)(start / PC5_PT_2MIB);
    unsigned long last = (unsigned long)((end - 1) / PC5_PT_2MIB);
    unsigned long changed = 0;
    for (unsigned long index = first; index <= last; index++) {
        u64 slot = pc5_pt_pd_base(pt) + index * 8u;
        u64 entry = load(slot);
        if (!(entry & 1ull))
            continue;
        store(slot, entry | PC5_PT_UC);
        changed++;
    }
    return changed;
}

int pc5_pt_lookup(u64 pt, u64 address, u64 *entry)
{
    if (!pt || address >= PC5_PT_SPAN)
        return 0;
    u64 pml4e = load(pc5_pt_pml4(pt) + ((address >> 39) & 0x1FF) * 8u);
    if (!(pml4e & 1ull))
        return 0;
    u64 pdpte = load(((pml4e & ~0xFFFull)) + ((address >> 30) & 0x1FF) * 8u);
    if (!(pdpte & 1ull))
        return 0;
    u64 pde = load(((pdpte & ~0xFFFull)) + ((address >> 21) & 0x1FF) * 8u);
    if (!(pde & 1ull))
        return 0;
    if (entry)
        *entry = pde;
    return 1;
}
