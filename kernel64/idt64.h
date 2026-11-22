/* Exception capture for the 64-bit path.
 *
 * The kernel installed no IDT at all. That sounds like "interrupts are not enabled
 * yet", which would be harmless, but it is worse: with no IDT the first exception
 * finds a null IDT base and the CPU triple-faults and resets. The operator sees a
 * machine that rebooted with no message, so every bring-up bug presents the same
 * way and none of them is diagnosable.
 *
 * This installs an IDT whose entries point at per-vector stubs. A stub records the
 * vector and error code, the reporter prints them, and the kernel halts. It does
 * not attempt recovery: nothing here has a scheduler to return to, so "recovering"
 * would only convert one fault into a loop.
 *
 * Not in scope: no interrupt delivery, no page-fault fixup, no user/kernel split.
 * It turns a silent reset into a line of text, which is the entire claim.
 */
#ifndef PC5_IDT64_H
#define PC5_IDT64_H

#include "include/pc5_boot64.h"

#define PC5_IDT_ENTRIES 256

struct pc5_idt_ptr {
    u16 limit;
    u64 base;
} __attribute__((packed));

/* Install the table and load IDTR. After this returns, any exception prints
 * `PC5-64-EXCEPTION` and halts instead of triple-faulting. */
void idt64_install(void);

/* Called by the assembly trampoline with the frame already normalised. */
void idt64_report(u64 vector, u64 error_code, u64 rip, u64 cs, u64 rflags);

/* Route one exception vector onto an interrupt stack (IST1..IST7 in the TSS), or take
 * the routing away again.  Returns 0 and changes nothing when the vector is not one of
 * the 32 the kernel installs a handler for, or the index is not 1..7 - a gate that is
 * not present has no business naming a stack, and an IST index of 0 means "use the
 * current stack", which is exactly what a caller trying to be safe does not want.
 *
 * The table is live when this runs, so the write is the install: no fault can be in
 * progress (the kernel runs with IF clear and every handler here halts), and an
 * interrupted fault would have been a fault during fault handling, which is what the
 * routing is for. */
int idt64_set_ist(u64 vector, u64 ist);
int idt64_clear_ist(u64 vector);

/* The IST field as installed, 0 when the vector is out of range.  Reported so the
 * executed gate can compare what the TSS advertises with what the gates say. */
u64 idt64_gate_ist(u64 vector);

/* The sentinel a stub passes for vectors the CPU pushes no error code for. Not a
 * value any real error code can take; compared for equality only. */
#define PC5_NOERR 0x5A5A5A5A5A5A5A5AULL

#endif
