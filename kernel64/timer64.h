/* Time in the 64-bit kernel: a PIT-calibrated TSC.
 *
 * What this is. The PIT (channel 2, polled through port 0x61) is used exactly once,
 * at boot, to measure how many TSC ticks pass in 1 ms. After that every measurement
 * is a TSC delta. That is the same design the 32-bit kernel uses
 * (`kernel/timer.c`), reused deliberately: two kernels measuring time two different
 * ways would be a bug waiting to happen, and the TSC is the only monotonic source
 * this platform offers without programming an APIC timer.
 *
 * What this is not. There is no interrupt-driven timer and no scheduler. Nothing
 * here preempts anything. A "high-resolution timer" in the `M2a` sense - one that
 * produces an interrupt at a deadline - needs the APIC timer or a corrected PIT-
 * ticked counter, and neither is here.
 *
 * Why a calibration instead of the CPUID TSC frequency. CPUID leaves 0x15 and 0x16
 * are the architectural way to learn the TSC rate, but they are absent on many
 * parts and lie on some virtualised ones. Measuring against the PIT costs one
 * millisecond of boot and works wherever the PIT works, which is everywhere this
 * kernel runs. The CPUID figure, when present, is recorded alongside so a
 * disagreement between the two is visible in the log rather than silent.
 *
 * The measurement's error, stated so nobody over-reads it: the PIT's own accuracy is
 * about 0.1%, and the calibration averages over 20 ms to keep scheduling jitter from
 * dominating. Anything that needs better than ~1% must not use this.
 */
#ifndef PC5_TIMER64_H
#define PC5_TIMER64_H

#include "include/pc5_boot64.h"

struct pc5_timer64 {
    u32 calibrated;        /* 1 when tsc_per_ms is a measurement, not a fallback */
    u32 pit_present;       /* 1 when the PIT responded to calibration */
    u32 invariant_tsc;     /* CPUID.80000007H:EDX[8] */
    u32 tsc_per_ms;        /* measured TSC ticks per millisecond */
    u32 tsc_per_us;        /* measured TSC ticks per microsecond (may round to 0) */
    u64 boot_tsc;          /* TSC at the moment of calibration */
    u32 spins;             /* polls spent waiting for the PIT: 0 means it never fired */
};

/* Hardware access, injected. Same idiom as `apic64`'s MMIO accessors and
 * `cpu64_report_with()`'s CPUID callback: the real entry point passes the port/TSC
 * primitives, a test passes its own, and the logic under test is the code the kernel
 * runs. Without this a host test cannot call `timer64_init()` at all - `inb` on a
 * test process touches whatever the machine has on that port. */
struct pc5_timer64_io {
    u8  (*inb)(u16 port);
    void (*outb)(u16 port, u8 value);
    u64 (*rdtsc)(void);
    u32 (*invariant_tsc)(void);   /* 0 when it cannot be determined */
};

/* The real primitives, for the production call site. */
const struct pc5_timer64_io *timer64_hardware_io(void);

/* Read the TSC. Architectural on every x86-64 part; not serialising, so it measures
 * roughly where it is called rather than exactly. */
static inline u64 timer64_rdtsc(void)
{
    u32 lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((u64)hi << 32) | lo;
}

/* Calibrate against the PIT and fill `out`. Always returns. A PIT that never
 * responds leaves `calibrated` clear and `spins` at its cap, which the caller
 * reports rather than treating as a zero-length millisecond. */
void timer64_init_with(struct pc5_timer64 *out, const struct pc5_timer64_io *io);
void timer64_init(struct pc5_timer64 *out);

/* Milliseconds since calibration. Zero when the TSC rate is unknown, because a
 * made-up rate would produce a confident wrong number and a zero is visible. */
u64 timer64_ms(const struct pc5_timer64 *timer, u64 tsc_now);

void timer64_print(const struct pc5_timer64 *timer);

#endif
