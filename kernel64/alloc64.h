/* Arithmetic for judging an EFI_ALLOCATE_MAX_ADDRESS result.
 *
 * The loader asks firmware for pages whose uppermost byte is at or below an
 * inclusive maximum.  Two things can go wrong that a "did it succeed" check
 * cannot see:
 *
 *   1. The bound itself is wrong.  Handled in the loader and pinned by
 *      tests/test_boot64_stub.py; this module reports the value so the serial
 *      log carries it.
 *   2. The returned extent does not fit the bound, or arithmetic on it wraps.
 *      A base near the top of the 64-bit space plus a page count overflows
 *      `base + pages*4096` and can make a bad allocation look good.
 *
 * Everything here is deliberately free of UEFI types so a host test can drive
 * it with arbitrary 64-bit inputs, including the wrap cases.
 */
#ifndef PC5_ALLOC64_H
#define PC5_ALLOC64_H

#include "include/pc5_boot64.h"

#define PC5_PAGE_BYTES 4096ull

struct pc5_alloc_check {
    u64 requested_max;      /* inclusive uppermost byte asked of firmware */
    u64 pages;
    u64 returned_base;      /* what firmware wrote back */
    u64 bytes;
    u64 end_address;        /* last byte of the returned extent */
    int size_ok;            /* pages * 4096 did not wrap */
    int zero_pages;         /* firmware was asked for no pages at all */
    int end_ok;             /* end_address was computed without wrapping */
    int aligned;            /* base is 4 KiB aligned */
    int within_bound;       /* end_address <= requested_max */
    int within_limit;       /* end_address < identity limit */
    const char *note;
};

/* Judge one completed allocation.  `identity_limit` is PC5_IDENTITY_LIMIT.
 * A null base is reported through `note` and never counts as usable. */
void pc5_alloc_check(struct pc5_alloc_check *out,
                     u64 requested_max, u64 pages, u64 returned_base,
                     u64 identity_limit);

const char *pc5_alloc_verdict(const struct pc5_alloc_check *check);

#endif
