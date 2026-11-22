/* The task-state segment and its interrupt stacks.
 *
 * This is the first thing in the kernel that takes memory from the frame pool
 * (`kernel64/mem64.c`) for a reason other than measuring it.  It needs frames because
 * an exception stack cannot share the stack the fault happened on: a #DF is by
 * definition raised while the delivery of another exception was already failing, so by
 * the time it is taken the original stack is untrustworthy.  That is what IST is for,
 * and IST only works if a TSS is loaded, and a TSS has to live in memory somebody
 * allocated.
 *
 * What is claimed, in order, and nothing more:
 *   * one frame for the TSS itself;
 *   * one frame for IST1, the double-fault stack;
 *   * one frame for IST2, the NMI stack;
 *   * a GDT in the kernel's own `.bss` (the image's claim already covers it), holding
 *     the code and data selectors the IDT gates name plus the TSS descriptor.
 *
 * What is NOT claimed: no task switching (there is nothing to switch to), no I/O
 * permission map, no per-CPU TSS (only the boot CPU exists until SMP), no loaded task
 * register, and no proof that a real CPU delivers a double fault onto the IST1 stack -
 * no machine this project can run has taken a double fault.  Loading TR is what would
 * make the stacks above actually be used, and `pc5_tss64_install` deliberately does not
 * attempt it: under the emulator this repository can run, executing LTR raises an
 * exception, for reasons that could not be narrowed down further - the measurement, and
 * the hypotheses ruled out one at a time, are recorded in the dated evidence directory
 * named below.  What the executed gate proves is therefore the wiring and nothing more:
 * GDTR points at the table the kernel wrote, the descriptor decodes to the frame the
 * pool granted, the handover ran at CPL 0, the arming rules hold on the live IDT, and TR
 * is reported unloaded with the gates agreeing that they are not armed.
 *
 * Every failure path refuses rather than half-loads: a grant the pool cannot re-derive,
 * a pool that is not ready, or an SGDT readback that disagrees with what was written
 * leaves the TSS frames handed back and the IDT with its IST fields cleared, so a fault
 * keeps reporting on the ordinary path instead of running on a stack that does not
 * exist.  Refusing to arm a gate is the safety property here, not a shortfall: an armed
 * gate whose stack the CPU cannot resolve turns a reportable fault into a lost one.
 *
 * Evidence: docs/evidence/2026-11-22-tss-and-ist/ (measured, with negative controls).
 */
#ifndef PC5_TSS64_H
#define PC5_TSS64_H

#include "include/pc5_mem64.h"

#define PC5_TSS64_SIZE 104u
#define PC5_TSS64_GDT_SLOTS 6u
#define PC5_TSS64_SELECTOR 0x28u
#define PC5_TSS64_STACK_PAGES 1ull
#define PC5_TSS64_FRAMES 3ull          /* TSS + IST1 + IST2 */

/* Which vector routes to which IST slot, in both directions: the IDT is written from
 * here and the TSS fields are named from there, and a mistake in either is a fault
 * that corrupts the stack it was meant to escape. */
#define PC5_TSS64_IST_DOUBLE_FAULT 1u
#define PC5_VECTOR_DOUBLE_FAULT 8u
#define PC5_TSS64_IST_NMI 2u
#define PC5_VECTOR_NMI 2u

/* Filled by pc5_tss64_build and, after a successful install, by pc5_tss64_install.
 * Everything in it is printed on the boot line, so the log is the evidence rather than
 * a side effect: a field that the gate can read is a field that can be wrong in public. */
struct pc5_tss64 {
    u64 tss_base;                       /* the frame holding the TSS */
    u64 ist_base[8];                    /* 1..7: the frame behind each IST slot */
    u64 ist_top[8];                     /* 1..7: the value written into the TSS */
    u64 gdt_base;
    u64 gdt_limit;
    u16 selector;                       /* the selector a caller would hand to LTR */
    u64 cpl;                            /* privilege level the install ran at */
    u64 gdtr_base;                      /* what SGDT reports afterwards */
    u16 gdtr_limit;
    u64 desc_base;                      /* the TSS descriptor decoded */
    u64 desc_limit;
    u64 frames_taken;                   /* granted and kept */
    int built;                          /* 1 when the TSS and GDT are written */
    int installed;                      /* 1 when GDTR agrees with the table built */
    const char *reason;                 /* "ok", or why it refused */
};

/* Grant the frames, write the TSS and the GDT.  Pure C: nothing here executes a
 * privileged instruction, so the whole refusal surface is testable on the host.
 * Returns 1 on success; on failure the pool is left exactly as it was found, because
 * every frame taken is handed back before returning. */
int pc5_tss64_build(struct pc5_mem64 *pool, struct pc5_tss64 *out);

/* Switch the CPU to the table `pc5_tss64_build` prepared: a privilege check, LGDT, and
 * an SGDT readback that has to agree.  Returns 1 when GDTR says what the kernel wrote.
 *
 * It does not load TR, and therefore does not make IST live; see the comment on the
 * function.  `installed` is the flag a caller of `idt64_set_ist` has to have set. */
int pc5_tss64_install(struct pc5_mem64 *pool, struct pc5_tss64 *state);

/* Hand the frames back after an install that turned out to be unusable, and report what
 * was released.  Called only from the failure path in main.c. */
void pc5_tss64_release(struct pc5_mem64 *pool, struct pc5_tss64 *state);

/* The descriptor words, so a test can decode them without re-implementing the
 * encoding.  Index 4 is the low half of the TSS descriptor, index 5 the high half. */
u64 pc5_tss64_gdt_word(u64 index);

const char *pc5_tss64_reason(const struct pc5_tss64 *state);

#endif
