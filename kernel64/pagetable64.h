/* Runtime page-table walker: asks the hardware's own question of the tables the
 * CPU is actually using, so "the identity map reaches this address" is observed
 * rather than asserted. */
#ifndef PC5_PAGETABLE64_H
#define PC5_PAGETABLE64_H

#include "include/pc5_boot64.h"

enum pc5_page_size {
    PC5_PAGE_NONE = 0,
    PC5_PAGE_4KIB,
    PC5_PAGE_2MIB,
    PC5_PAGE_1GIB,
};

enum pc5_walk_failure {
    PC5_WALK_OK = 0,
    PC5_WALK_BAD_ROOT,
    PC5_WALK_RESERVED_PS,
    PC5_WALK_MISSING_PML4,
    PC5_WALK_MISSING_PDPT,
    PC5_WALK_MISSING_PD,
    PC5_WALK_MISSING_PT,
};

/* Reading an entry is injected so the identical walk runs against real memory
 * in the kernel and against a synthetic table in a host test. */
typedef u64 (*pc5_read_u64_fn)(u64 address);

struct pc5_walk_result {
    u64 virtual_address;
    u64 physical_address;      /* valid only when page_size != PC5_PAGE_NONE */
    enum pc5_page_size page_size;
    enum pc5_walk_failure failure;
    unsigned pml4_present;
    unsigned pdpt_present;
    unsigned pd_present;
    unsigned pt_present;
};

/* Walk from `root` for `va`.  Returns the terminating page size, or
 * PC5_PAGE_NONE with `failure` set.  Large pages terminate the walk early. */
enum pc5_page_size pc5_walk(struct pc5_walk_result *out,
                            pc5_read_u64_fn read_u64,
                            u64 root, u64 va);

struct pc5_identity_case {
    u64 virtual_address;
    unsigned present;
    /* Which table levels the walk reached.  Reported separately from `present`
     * because "the PML4 entry is there" and "the address translates" are
     * different facts, and a reader diagnosing a broken span needs both. */
    unsigned pml4_present;
    unsigned pdpt_present;
    unsigned pd_present;
    unsigned pt_present;
    enum pc5_page_size page_size;
    u64 physical_address;
    unsigned identity;         /* translation equals the virtual address */
    enum pc5_walk_failure failure;
    const char *note;
};

/* Walk `va` and judge it as an identity-map member.  Addresses at or above
 * `limit` are reported as outside the span rather than as failures: the map is
 * not required to cover them. */
int pc5_check_identity(struct pc5_identity_case *out,
                       pc5_read_u64_fn read_u64,
                       u64 root, u64 limit, u64 va);

const char *pc5_walk_failure_name(enum pc5_walk_failure failure);
const char *describe_page_size(enum pc5_page_size size);

/* Read a u64 from physical memory with the identity map still active.  Used as
 * the walker's reader in the kernel; the host probe substitutes its own. */
u64 real_read_u64(u64 address);

/* D1 policy: probe the executing CPU's tables across `limit` and emit
 * PC5-64-PTWALK lines.  Returns nonzero only if every probe is a clean
 * identity translation, and on failure emits PC5-64BIT-FAIL. */
int pc5_report_identity_span(pc5_read_u64_fn read_u64, u64 root, u64 limit);

#endif
