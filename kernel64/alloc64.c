/* Overflow-safe judging of an AllocateMaxAddress result.  See alloc64.h for why
 * each term is checked separately rather than as one combined expression. */
#include "alloc64.h"

/* x * y without wrapping.  Returns 0 and leaves *out untouched on overflow. */
static int mul_ok(u64 x, u64 y, u64 *out)
{
    if (x != 0 && y > (~0ull) / x)
        return 0;
    *out = x * y;
    return 1;
}

/* x + y without wrapping. */
static int add_ok(u64 x, u64 y, u64 *out)
{
    if (y > (~0ull) - x)
        return 0;
    *out = x + y;
    return 1;
}

void pc5_alloc_check(struct pc5_alloc_check *out,
                     u64 requested_max, u64 pages, u64 returned_base,
                     u64 identity_limit)
{
    out->requested_max = requested_max;
    out->pages = pages;
    out->returned_base = returned_base;
    out->bytes = 0;
    out->end_address = 0;
    out->size_ok = 0;
    out->zero_pages = 0;
    out->end_ok = 0;
    out->aligned = 0;
    out->within_bound = 0;
    out->within_limit = 0;
    out->note = "";

    /* A base of zero means firmware could not satisfy the request: alloc_below_4g
     * returns 0 in that case, and UEFI's own zero is not a usable page. */
    if (returned_base == 0) {
        out->note = "firmware returned a null base";
        return;
    }

    out->aligned = (returned_base % PC5_PAGE_BYTES) == 0;

    if (!mul_ok(pages, PC5_PAGE_BYTES, &out->bytes)) {
        out->note = "page count times page size overflows 64 bits";
        return;
    }
    out->size_ok = 1;

    if (pages == 0) {
        out->note = "zero pages requested";
        out->zero_pages = 1;
        return;
    }

    /* end = base + bytes - 1, computed in two checked steps so a base near the
     * top of the address space cannot wrap into a small, plausible-looking end. */
    {
        u64 beyond;
        if (!add_ok(returned_base, out->bytes, &beyond)) {
            out->note = "base plus size overflows 64 bits";
            /* Not a bound violation: the extent is unknowable, which is a
             * different and worse thing than exceeding a limit.  Leaving the
             * flags clear lets pc5_alloc_verdict report the overflow. */
            return;
        }
        out->end_address = beyond - 1;
        out->end_ok = 1;
    }

    /* Inclusive semantics: the last byte may equal the requested maximum. */
    out->within_bound = out->end_address <= requested_max;
    out->within_limit = out->end_address < identity_limit;

    if (!out->aligned)
        out->note = "base is not 4 KiB aligned";
    else if (!out->within_bound)
        out->note = "extent ends above the requested maximum";
    else if (!out->within_limit)
        out->note = "extent ends at or above the identity limit";
}

const char *pc5_alloc_verdict(const struct pc5_alloc_check *check)
{
    if (check->returned_base == 0)
        return "null";
    if (!check->size_ok)
        return "overflow";
    if (check->zero_pages)
        return "zero-pages";
    if (!check->end_ok)
        return "overflow-end";
    if (!check->aligned)
        return "unaligned";
    if (!check->within_bound)
        return "over-bound";
    if (!check->within_limit)
        return "over-limit";
    return "ok";
}
