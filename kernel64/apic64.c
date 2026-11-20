#include "apic64.h"
#include "serial64.h"

/* Bring the local APIC up in the only order that works.
 *
 * Every step is ordered for a reason, and the reasons are the bugs:
 *
 *  1. Read ID/VERSION *first*.  If the MMIO window is not backed, those reads are
 *     the cheapest way to find out - and the call site only reaches here after the
 *     memory map has vouched for the window, because on real hardware an unbacked
 *     read raises a fault rather than returning a value.
 *  2. A VERSION of 0 or all-ones means the window did not answer.  Neither is a
 *     real APIC; all-ones is exactly what an unbacked read returns where the fault
 *     is masked.
 *  3. Clear TPR.  Reset value is 0, but firmware could hand over with something
 *     else and silently mask every priority below it.
 *  4. Clear ESR by writing back what was read, masked.  A stale ESR left set is a
 *     permanent error indicator that makes every later diagnosis lie.
 *  5. Set SVR enable *last*, once everything above is known good.
 *  6. Read SVR back and refuse to report ready unless the bit stuck.  A write that
 *     did not take must fail here, not surface later as an interrupt that never
 *     arrives.
 */
const char *apic64_bring_up(struct pc5_apic64 *out,
                            pc5_apic_read_fn read_fn,
                            pc5_apic_write_fn write_fn,
                            void *context)
{
    u32 version;
    u32 svr;

    if (out == 0)
        return "no output structure";
    if (read_fn == 0 || write_fn == 0)
        return "no MMIO accessors";

    out->present = 0;
    out->id = 0;
    out->version = 0;
    out->max_lvt = 0;
    out->enabled = 0;
    out->svr = 0;
    out->error_status = 0;

    version = read_fn(context, PC5_APIC_VERSION);
    out->version = version;

    if (version == 0u || version == 0xFFFFFFFFu)
        return "the local APIC MMIO window did not answer";

    out->present = 1;
    out->id = read_fn(context, PC5_APIC_ID) >> 24;
    out->max_lvt = ((version >> 16) & 0xFFu) + 1u;

    write_fn(context, PC5_APIC_TPR, 0u);

    out->error_status = read_fn(context, PC5_APIC_ESR) & PC5_APIC_ESR_MASK;
    if (out->error_status != 0u)
        write_fn(context, PC5_APIC_ESR, out->error_status);

    write_fn(context, PC5_APIC_SVR, PC5_APIC_SVR_RESET | PC5_APIC_SVR_ENABLE);

    svr = read_fn(context, PC5_APIC_SVR);
    out->svr = svr;
    if ((svr & PC5_APIC_SVR_ENABLE) == 0u)
        return "the spurious-vector enable bit did not take";

    out->enabled = 1;
    return 0;
}

static void field(const char *name, u64 value)
{
    serial64_write("PC5-64-APIC ");
    serial64_write(name);
    serial64_write("=");
    serial64_dec(value);
    serial64_write("\n");
}

void apic64_print(const struct pc5_apic64 *apic)
{
    if (apic == 0)
        return;

    field("present", apic->present);
    field("enabled", apic->enabled);
    field("id", apic->id);
    field("version", apic->version);
    field("max_lvt", apic->max_lvt);
    field("svr", apic->svr);
    field("error_status", apic->error_status);
}
