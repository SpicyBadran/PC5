/* Local APIC bring-up for the 64-bit path.
 *
 * Scope, stated here so nothing downstream over-reads it:
 *   - xAPIC (MMIO) only.  x2APIC is *detected* by the CPU report but not enabled;
 *     enabling it rewrites IA32_APIC_BASE, and a kernel that flips MSRs with no way
 *     back turns a bring-up bug into an unrecoverable fault.  Separate change,
 *     separate evidence.
 *   - The MMIO window is injected, the same idiom `cpu64_report_with()` established
 *     for CPUID, so the register logic is host-testable without QEMU or OVMF.
 *   - This does not enable global interrupts, does not program a timer, and does not
 *     start a secondary processor.  It establishes the controller's identity and
 *     state machine, which the interrupt, timer and SMP work all depend on.
 *
 * The failure this is built around: the spurious-interrupt vector register (SVR) is
 * *disabled* out of reset, bit 8 clear.  Code that writes TPR or reads the ID
 * register before setting SVR bit 8 is talking to a controller that will never
 * deliver an interrupt, and "my timer handler never fires" is an expensive bug to
 * find.  So the order below is: enable last, and refuse to report ready until SVR
 * reads back with the enable bit set.
 */
#ifndef PC5_APIC64_H
#define PC5_APIC64_H

#include "include/pc5_boot64.h"

/* Register offsets in the 4 KiB MMIO page.  Architectural: Intel SDM vol. 3A
 * §10.4.1, AMD APM vol. 2 §16.4.1. */
#define PC5_APIC_ID          0x020u
#define PC5_APIC_VERSION     0x030u
#define PC5_APIC_TPR         0x080u
#define PC5_APIC_EOI         0x0B0u
#define PC5_APIC_SVR         0x0F0u
#define PC5_APIC_ESR         0x280u
#define PC5_APIC_LVT_TIMER   0x320u
#define PC5_APIC_TIMER_INIT  0x380u
#define PC5_APIC_TIMER_CUR   0x390u
#define PC5_APIC_TIMER_DIV   0x3E0u

#define PC5_APIC_SVR_ENABLE  (1u << 8)

/* The architectural reset SVR value: bits 0..7 are the spurious vector (0xFF) and
 * bit 8, the enable bit, is CLEAR.  Writing 0x1FF here would set the enable bit by
 * accident and defeat the read-back below - a test for "the enable did not stick"
 * is what caught that value. */
#define PC5_APIC_SVR_RESET   0x000000FFu

/* ESR is write-one-to-clear; only bits 0..2 and 5..7 are meaningful.  Writing the
 * read value back unmasked can clear a sticky bit we wanted to keep. */
#define PC5_APIC_ESR_MASK    0x000000EFu

/* Injected MMIO accessors.  `context` lets a test carry its own register file. */
typedef u32 (*pc5_apic_read_fn)(void *context, u32 offset);
typedef void (*pc5_apic_write_fn)(void *context, u32 offset, u32 value);

struct pc5_apic64 {
    u32 present;
    u32 id;
    u32 version;
    u32 max_lvt;      /* VERSION[23:16] + 1 */
    u32 enabled;      /* SVR[8] read back set */
    u32 svr;
    u32 error_status; /* ESR after the write-one-to-clear */
};

/* Why a bring-up attempt was refused, as text: the kernel's serial log should say
 * what went wrong, not a number the reader has to look up.  Returns 0 on success. */
const char *apic64_bring_up(struct pc5_apic64 *out,
                           pc5_apic_read_fn read_fn,
                           pc5_apic_write_fn write_fn,
                           void *context);

void apic64_print(const struct pc5_apic64 *apic);

#endif
