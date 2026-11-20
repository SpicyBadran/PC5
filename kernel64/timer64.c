#include "timer64.h"
#include "serial64.h"
#include "include/pc5_io.h"

/* PIT channel 2, polled. The same sequence the 32-bit kernel's `pit_wait_1ms()`
 * uses, so the two kernels agree on what a millisecond is.
 *
 * Channel 2 rather than channel 0 because channel 0 raises IRQ0 and nothing here is
 * ready to take an interrupt; polling the gate output on port 0x61 uses the PIT
 * without enabling anything.
 *
 * The spin cap is not cosmetic. Without it, a machine whose PIT is absent or whose
 * gate never rises hangs the kernel during calibration, and "boots on some hardware,
 * hangs on others" is the failure that costs the most to diagnose. Hitting the cap
 * leaves calibration unset, which is reported.
 */
#define PC5_PIT_DIVISOR_1MS  1193u     /* 1193182 / 1193 ~= 1000.2 Hz */
/* Total polls allowed across all samples, not per sample.
 *
 * A per-sample cap multiplies: 20 samples x 2,000,000 polls is minutes under
 * emulation and perceptible on real hardware, and it happens precisely when the PIT
 * is absent - i.e. the case that is supposed to fail fast. A single budget makes an
 * absent PIT cost the same few milliseconds however many samples were attempted.
 * 40,000 is far more than a working PIT needs (it rises within a handful of polls)
 * and small enough that giving up is quick. */
#define PC5_PIT_POLL_BUDGET  40000u
#define PC5_PIT_SAMPLES      20u

static u32 pit_wait_1ms(const struct pc5_timer64_io *io, u32 budget)
{
    u32 spin;
    u8 gate = io->inb(0x61);

    io->outb(0x61, (u8)((gate | 0x01) & ~0x02));  /* gate on, speaker off */
    io->outb(0x43, 0xb0);                          /* channel 2, mode 0, lobyte/hibyte */
    io->outb(0x42, (u8)(PC5_PIT_DIVISOR_1MS & 0xffu));
    io->outb(0x42, (u8)((PC5_PIT_DIVISOR_1MS >> 8) & 0xffu));

    for (spin = 0; spin < budget; spin++)
        if (io->inb(0x61) & 0x20)                  /* output high: count expired */
            return spin;
    return spin;
}

void timer64_init_with(struct pc5_timer64 *out, const struct pc5_timer64_io *io)
{
    u64 first, last;
    u32 i, spins = 0;
    u32 budget = PC5_PIT_POLL_BUDGET;

    if (out == 0)
        return;

    out->calibrated = 0;
    out->pit_present = 0;
    out->invariant_tsc = 0;
    out->tsc_per_ms = 0;
    out->tsc_per_us = 0;
    out->boot_tsc = 0;
    out->spins = 0;

    if (io == 0 || io->inb == 0 || io->outb == 0 || io->rdtsc == 0)
        return;

    first = io->rdtsc();
    for (i = 0; i < PC5_PIT_SAMPLES; i++) {
        u32 s = pit_wait_1ms(io, budget);
        spins += s;
        budget = (s < budget) ? (budget - s) : 0u;
        if (s >= PC5_PIT_POLL_BUDGET || budget == 0u) {
            /* The gate never rose, or the budget is spent. Report and stop: more
             * samples would only multiply an already-proven failure. */
            out->spins = s;
            out->boot_tsc = first;
            return;
        }
    }
    last = io->rdtsc();

    out->spins = spins;
    out->boot_tsc = first;
    out->pit_present = 1;

    /* Average over every sample: one sample would be dominated by the cost of the
     * port writes themselves. */
    out->tsc_per_ms = (u32)((last - first) / PC5_PIT_SAMPLES);
    if (out->tsc_per_ms != 0) {
        out->tsc_per_us = out->tsc_per_ms / 1000u;
        out->calibrated = 1;
    }

    if (io->invariant_tsc != 0)
        out->invariant_tsc = io->invariant_tsc();
}

u64 timer64_ms(const struct pc5_timer64 *timer, u64 tsc_now)
{
    if (timer == 0 || !timer->calibrated || timer->tsc_per_ms == 0)
        return 0;
    if (tsc_now < timer->boot_tsc)
        return 0;
    return (tsc_now - timer->boot_tsc) / (u64)timer->tsc_per_ms;
}

static void field(const char *name, u64 value)
{
    serial64_write("PC5-64-TIMER ");
    serial64_write(name);
    serial64_write("=");
    serial64_dec(value);
    serial64_write("\n");
}

void timer64_print(const struct pc5_timer64 *timer)
{
    if (timer == 0)
        return;

    field("calibrated", timer->calibrated);
    field("pit_present", timer->pit_present);
    field("invariant_tsc", timer->invariant_tsc);
    field("tsc_per_ms", timer->tsc_per_ms);
    field("tsc_per_us", timer->tsc_per_us);
    field("spins", timer->spins);
    field("since_boot_ms", timer64_ms(timer, timer64_rdtsc()));
}

/* --- the production primitives, and the entry point the kernel calls ---------- */

static u8  hardware_inb(u16 port) { return inb(port); }
static void hardware_outb(u16 port, u8 value) { outb(port, value); }
static u64 hardware_rdtsc(void) { return timer64_rdtsc(); }

static u32 hardware_invariant_tsc(void)
{
    u32 regs[4];
    /* CPUID.80000007H:EDX[8] is only valid when the leaf exists, so the maximum
     * extended leaf is checked first. Without that test, a CPU that does not
     * implement the leaf returns unrelated bits in EDX and the kernel would record
     * a confident, meaningless "invariant" claim. */
    __asm__ volatile ("cpuid"
                      : "=a"(regs[0]), "=b"(regs[1]), "=c"(regs[2]), "=d"(regs[3])
                      : "a"(0x80000000u), "c"(0u));
    if (regs[0] < 0x80000007u)
        return 0;
    __asm__ volatile ("cpuid"
                      : "=a"(regs[0]), "=b"(regs[1]), "=c"(regs[2]), "=d"(regs[3])
                      : "a"(0x80000007u), "c"(0u));
    return (regs[3] >> 8) & 1u;
}

static const struct pc5_timer64_io g_hardware_io = {
    hardware_inb, hardware_outb, hardware_rdtsc, hardware_invariant_tsc
};

const struct pc5_timer64_io *timer64_hardware_io(void)
{
    return &g_hardware_io;
}

void timer64_init(struct pc5_timer64 *out)
{
    timer64_init_with(out, &g_hardware_io);
}
