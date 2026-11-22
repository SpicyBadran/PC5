/* Host-side probe for kernel64/pagetable64.c and kernel64/alloc64.c.
 *
 * These compile and execute the real walker against a synthetic page table in
 * ordinary host memory.  That exercises the index arithmetic, the large-page
 * handling and the overflow logic - the parts most likely to be wrong - without
 * claiming anything about real firmware or real CPU page tables.
 *
 * Each case prints one line: "<name> <observed>".  tests/test_pagetable64.py
 * compares that against what the case must produce.
 */
#include <stdio.h>
#include <string.h>

#include "pagetable64.h"
#include "alloc64.h"

/* A tiny synthetic table space: enough for one PML4, one PDPT, four PDs. */
#define MAX_ENTRIES 4096
static u64 table[MAX_ENTRIES];
static unsigned table_used;

/* Base of the synthetic table, so "physical" addresses look real. */
#define TABLE_BASE 0x40000ull

static u64 read_u64(u64 address)
{
    u64 index;
    if (address < TABLE_BASE)
        return 0;
    index = (address - TABLE_BASE) / 8ull;
    if (index >= MAX_ENTRIES)
        return 0;
    return table[index];
}

static u64 at(u64 address)
{
    return (address - TABLE_BASE) / 8ull;
}

static void reset_table(void)
{
    memset(table, 0, sizeof(table));
    table_used = 0;
}

/* Lay out an identity map the way the loader does when it is correct: one
 * PDPT entry per GiB, each pointing at its own PD, PD entries 2 MiB pages. */
static u64 build_identity(u64 span)
{
    u64 root = TABLE_BASE;
    u64 pdpt = TABLE_BASE + 0x1000;
    u64 pd_base = TABLE_BASE + 0x2000;
    u64 gib_count = span >> 30;
    u64 g;

    reset_table();
    table[at(root)] = pdpt | 0x1 | 0x2;                   /* PML4 -> PDPT */
    for (g = 0; g < gib_count; g++) {
        u64 pd = pd_base + g * 0x1000;
        u64 i;
        table[at(pdpt + g * 8)] = pd | 0x1 | 0x2;         /* PDPT -> own PD */
        for (i = 0; i < 512; i++) {
            u64 pa = (g << 30) + (i << 21);
            table[at(pd + i * 8)] = pa | 0x1 | 0x2 | 0x80; /* 2 MiB, PS set */
        }
    }
    return root;
}

static void walk_case(const char *name, u64 root, u64 va, u64 limit)
{
    struct pc5_identity_case c;
    int ok = pc5_check_identity(&c, read_u64, root, limit, va);
    printf("walk %s ok=%d present=%u path=%c%c%c%c size=%d pa=%llx ident=%d fail=%s\n",
           name, ok, c.present,
           c.pml4_present ? 'P' : '-', c.pdpt_present ? 'P' : '-',
           c.pd_present ? 'P' : '-', c.pt_present ? 'P' : '-',
           (int)c.page_size,
           (unsigned long long)c.physical_address, c.identity,
           pc5_walk_failure_name(c.failure));
}

int main(void)
{
    const u64 limit = 4ull << 30;
    u64 root;

    /* ---------------------------------------------------- correct map (D1 fixed) */
    root = build_identity(limit);
    /* start, just below/above each 1 GiB boundary, inside each GiB, upper end */
    walk_case("addr0", root, 0ull, limit);
    walk_case("below1g", root, 0x3FFFF000ull, limit);
    walk_case("at1g", root, 0x40000000ull, limit);
    walk_case("above1g", root, 0x40001000ull, limit);
    walk_case("in1g", root, 0x50000000ull, limit);
    walk_case("below2g", root, 0x7FFFF000ull, limit);
    walk_case("at2g", root, 0x80000000ull, limit);
    walk_case("in2g", root, 0x90000000ull, limit);
    walk_case("below3g", root, 0xBFFFF000ull, limit);
    walk_case("at3g", root, 0xC0000000ull, limit);
    walk_case("in3g", root, 0xD0000000ull, limit);
    walk_case("below4g", root, 0xFFFFFFFfull, limit);
    walk_case("at4g", root, limit, limit);              /* outside span */

    /* ------------------------------------------------- D1 mutant: one PDPT entry
     * The defect PR #26 shipped: only PDPT[0] is linked, so the upper three PDs
     * exist but nothing points at them.  Every address below 1 GiB still works,
     * which is why the loader's reported span looked right. */
    root = build_identity(limit);
    table[at(TABLE_BASE + 0x1000) + 1] = 0;             /* disconnect 1-2 GiB */
    table[at(TABLE_BASE + 0x1000) + 2] = 0;             /* disconnect 2-3 GiB */
    table[at(TABLE_BASE + 0x1000) + 3] = 0;             /* disconnect 3-4 GiB */
    walk_case("mutant_low", root, 0x00001000ull, limit);
    walk_case("mutant_at1g", root, 0x40000000ull, limit);
    walk_case("mutant_in2g", root, 0x90000000ull, limit);
    walk_case("mutant_at3g", root, 0xC0000000ull, limit);

    /* ------------------------------------------------ a 1 GiB page terminates */
    root = build_identity(limit);
    {
        u64 pdpt = TABLE_BASE + 0x1000;
        table[at(pdpt)] = (0ull) | 0x1 | 0x2 | 0x80;     /* 1 GiB page, PS set */
    }
    walk_case("huge1g", root, 0x12345000ull, limit);

    /* -------------------------------------------- present PDPT, absent PD entry */
    root = build_identity(limit);
    table[at(TABLE_BASE + 0x2000) + 3] = 0;              /* drop a 2 MiB entry */
    walk_case("hole_pd", root, 0x00600000ull, limit);

    /* ------------------------------------------------------- malformed roots */
    walk_case("root_zero", 0ull, 0x1000ull, limit);
    walk_case("root_misaligned", TABLE_BASE + 3, 0x1000ull, limit);

    /* ================================================== alloc64 arithmetic */
    {
        struct pc5_alloc_check a;

        pc5_alloc_check(&a, 0xFFFFFFFFull, 1, 0x1000, limit);
        printf("alloc ok %s end=%llx base=%llx\n", pc5_alloc_verdict(&a),
               (unsigned long long)a.end_address,
               (unsigned long long)a.returned_base);

        /* inclusive: the extent may end exactly on the bound */
        pc5_alloc_check(&a, 0x1FFFfull, 16, 0x10000, limit);
        printf("alloc inclusive %s end=%llx\n", pc5_alloc_verdict(&a), (unsigned long long)a.end_address);

        /* one byte over the bound must fail */
        pc5_alloc_check(&a, 0x1FFFEull, 16, 0x10000, limit);
        printf("alloc over_bound %s\n", pc5_alloc_verdict(&a));

        /* zero base */
        pc5_alloc_check(&a, 0xFFFFFFFFull, 1, 0, limit);
        printf("alloc null %s\n", pc5_alloc_verdict(&a));

        /* pages * 4096 wraps */
        pc5_alloc_check(&a, 0xFFFFFFFFull, 0x4000000000000001ull, 0x1000, limit);
        printf("alloc overflow_size %s\n", pc5_alloc_verdict(&a));

        /* base + bytes wraps : base near top, 2 pages */
        pc5_alloc_check(&a, ~0ull, 2, 0xFFFFFFFFFFFFF000ull, limit);
        printf("alloc overflow_end %s\n", pc5_alloc_verdict(&a));

        /* inside the requested bound but past the identity limit: 4 GiB exactly.
         * requested_max is deliberately set high so within_bound passes and only
         * the limit test can catch this. */
        pc5_alloc_check(&a, 0xFFFFFFFFFull, 1, 0x100000000ull, 0xFFFFFFFFull);
        printf("alloc over_limit %s\n", pc5_alloc_verdict(&a));

        /* unaligned base */
        pc5_alloc_check(&a, 0xFFFFFFFFull, 1, 0x1001, limit);
        printf("alloc unaligned %s\n", pc5_alloc_verdict(&a));

        /* zero pages */
        pc5_alloc_check(&a, 0xFFFFFFFFull, 0, 0x1000, limit);
        printf("alloc zero_pages %s note=\"%s\"\n",
               pc5_alloc_verdict(&a), a.note);
    }

    return 0;
}
