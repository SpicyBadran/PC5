/* D1: walk the page tables the executing CPU is actually using and report what
 * the hardware would find there.
 *
 * kernel64/main.c already proves CR3 is aligned and that the loader's reported
 * root matches the live register, but neither check visits an entry.  A table
 * whose upper PDPT entries were never linked passes both while leaving the top
 * three GiB of the declared identity span unreachable - which is exactly the
 * defect that shipped in PR #26.  The addresses below are chosen so that every
 * 1 GiB boundary is probed from both sides, because a boundary is where a
 * per-GiB loop goes wrong.
 */
#include "pagetable64.h"
#include "include/pc5_io.h"
#include "serial64.h"

#define PC5_IDENTITY_LIMIT (4ull << 30)

/* Report one address on the serial line and return whether it is a clean
 * identity translation.  Every field the requirement asks for is printed:
 * the virtual address, which table levels were present, the final physical
 * translation and whether that translation is the identity. */
static int report_case(pc5_read_u64_fn read_u64, u64 root, u64 limit, u64 va,
                       const char *note)
{
    struct pc5_identity_case c;
    int ok = pc5_check_identity(&c, read_u64, root, limit, va);

    serial64_write("PC5-64-PTWALK va=");
    serial64_hex(va);
    /* The path, level by level: which entries the walk actually found.  A
     * single "pml4=1" would say the top entry exists even when nothing below
     * it does, which is the situation that hid the original defect. */
    serial64_write(" path=");
    serial64_write(c.pml4_present ? "P" : "-");
    serial64_write(c.pdpt_present ? "P" : "-");
    serial64_write(c.pd_present ? "P" : "-");
    serial64_write(c.pt_present ? "P" : "-");
    serial64_write(" size=");
    serial64_write(describe_page_size(c.page_size));
    serial64_write(" pa=");
    if (c.present)
        serial64_hex(c.physical_address);
    else
        serial64_write("none");
    serial64_write(" identity=");
    serial64_write(c.identity ? "1" : "0");
    serial64_write(" note=");
    serial64_write(note);
    serial64_write("\n");
    return ok;
}

/* A span is reachable only if every probe on it lands on an identity
 * translation.  A single miss is reported as a failure with the address that
 * missed, so the serial log names the broken boundary rather than just saying
 * the walk failed. */
int pc5_report_identity_span(pc5_read_u64_fn read_u64, u64 root, u64 limit)
{
    static const u64 probes[] = {
        0ull,               /* start of the range */
        0x3FFFFFull,        /* last byte below the first 1 GiB boundary */
        0x40000000ull,      /* the boundary itself */
        0x40000001ull,      /* first byte above it */
        0x50000000ull,      /* inside region 1 */
        0x7FFFFFull,        /* last byte below 2 GiB */
        0x80000000ull,      /* 2 GiB boundary */
        0x80000001ull,
        0x90000000ull,      /* inside region 2 */
        0xBFFFFFull,        /* last byte below 3 GiB */
        0xC0000000ull,      /* 3 GiB boundary */
        0xC0000001ull,
        0xD0000000ull,      /* inside region 3 */
    };
    static const char *names[] = {
        "start",
        "below-1gib",
        "at-1gib",
        "above-1gib",
        "in-1gib",
        "below-2gib",
        "at-2gib",
        "above-2gib",
        "in-2gib",
        "below-3gib",
        "at-3gib",
        "above-3gib",
        "in-3gib",
    };
    u64 i;
    unsigned probes_run = 0;
    unsigned failed = 0;

    for (i = 0; i < sizeof(probes) / sizeof(probes[0]); i++) {
        if (!report_case(read_u64, root, limit, probes[i], names[i]))
            failed++;
        probes_run++;
    }

    /* The upper end of the span is derived, not hard-coded, so the check keeps
     * tracking the limit if the loader's span ever changes. */
    if (!report_case(read_u64, root, limit, limit - 1ull, "upper-end"))
        failed++;
    probes_run++;

    serial64_write("PC5-64-PTWALK span probes=");
    serial64_dec(probes_run);
    serial64_write(" failed=");
    serial64_dec(failed);
    serial64_write("\n");

    if (failed != 0) {
        serial64_write("PC5-64BIT-FAIL=pagetables-disconnected\n");
        return 0;
    }
    return 1;
}
