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
 * What is NOT claimed: no task switching (there is nothing to switch to), no I/O permission
 * map, no per-CPU TSS (only the boot CPU exists until SMP).  What *is* claimed, and measured:
 * TR is loaded and read back with STR; the descriptor is decoded through GDTR rather than out of
 * the builder's own array; the CPU's busy transition is seen in memory; and a double fault raised
 * by the kernel itself is delivered onto the IST1 stack, which `pc5_tss64_probe_delivery`
 * establishes by reading where its own handler woke up.  The last is the only one of the four
 * that asks the machine to *do* something, and it is the one that found the layout bug: the IST
 * slots were written four bytes low -- one dword, the width of the link field -- so the CPU had
 * been handed `0x0000400000000000` as a
 * stack - a value the builder and its own checker agreed about, in three files, for as long as
 * nobody tried to land a frame on it.
 *
 * Every failure path refuses rather than half-loads: a grant the pool cannot re-derive, a pool
 * that is not ready, an SGDT readback that disagrees with what was written, or an LTR whose STR
 * readback does not match leaves the TSS frames handed back and the IDT with its IST fields
 * cleared, so a fault keeps reporting on the ordinary path instead of running on a stack that
 * does not exist.  Refusing to arm a gate is the safety property here, not a shortfall: an armed
 * gate whose stack the CPU cannot resolve turns a reportable fault into a lost one.
 *
 * Evidence: docs/evidence/2026-11-22-tss-and-ist/ (the wiring, and the erratum on why LTR was
 * never executed) and docs/evidence/2026-11-24-tss-delivery/ (TR loaded, the IST layout, and the
 * delivered double fault).
 */
#ifndef PC5_TSS64_H
#define PC5_TSS64_H

#include "include/pc5_mem64.h"

/* One frame, spelled out here as well as in mem64.c, because the delivery probe bounds a
 * stack pointer against the page it was granted and must not import the allocator to do it. */
#define PC5_TSS64_IST_PAGE 0x1000ull
#define PC5_TSS64_SIZE 104u
#define PC5_TSS64_GDT_SLOTS 6u
#define PC5_TSS64_SELECTOR 0x20u
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
    u64 desc_type_before;               /* descriptor type byte field, read through GDTR */
    u64 desc_type_after;                /* ... and again after LTR: the CPU marks it busy */
    u64 tr_selector;                    /* what STR reads back after LTR */
    int tr_armed;                       /* 1 when TR names this TSS and the checks agree */
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
 * Loading TR is `pc5_tss64_arm`'s job, on purpose: the table has to be live before the
 * descriptor in it can be handed to the CPU, and the two steps fail for different reasons.  A
 * caller that wants to arm an IST field checks `tr_armed`, which only that second step sets,
 * and never `installed` -- GDTR naming the table is not the same fact as TR naming the TSS, and
 * the difference is the whole content of `report_tss`'s pairing rule. */
int pc5_tss64_install(struct pc5_mem64 *pool, struct pc5_tss64 *state);

/* Load TR with the selector the build prepared, and *prove it took*: STR's readback, the
 * descriptor read back through GDTR rather than out of the builder's own array, the TSS
 * fields read from memory, and the busy transition the CPU performs on the descriptor type.
 * Returns 1 only when all of it agrees; `reason` says which part did not.
 *
 * This is the step that makes the IST stacks live, so it is also the step whose failure has
 * to be visible: an environment that refuses LTR ends up here with `tr_armed = 0`, and the
 * caller that arms the IDT gates is required to look at this flag and no other. */
int pc5_tss64_arm(struct pc5_tss64 *state);

/* The half of that proof that needs no instruction: the descriptor through GDTR and the TSS
 * through that descriptor.  `after_load` expects a busy descriptor; without it, an available
 * one.  Returns 1 and leaves `reason` at "ok", or names the first disagreement -- and it is
 * callable with TR untouched, which is what lets the host battery drive every refusal in it. */
int pc5_tss64_check_loaded(struct pc5_tss64 *state, int after_load);

/* The state of one deliberately-raised #DF, used to find out whether the CPU really switches
 * to IST1.
 *
 * The first six words are the probe's own bookkeeping and only three of them are written by the
 * handler: an interrupt handler runs on the stack it is being asked about, must not assume
 * anything about the stack it left, and returns to an address the *faulting code* recorded
 * rather than to the frame's own RIP -- the frame is the thing under test, so it is not also
 * allowed to be the control flow.  `frame[]` is read back afterwards, from the delivery stack
 * itself, because which quadword holds the saved RIP depends on whether the CPU pushed an error
 * code and on whether it saved SS and RSP: a soft `int $8` and a genuine double fault do not
 * agree, and a probe that compared one fixed offset would be measuring its own assumption.  What
 * does not depend on the layout is that *somewhere in those five words* is the address the trap
 * was taken at, and a delivery that did not really happen has no such word to offer.
 *
 * Slots 0, 8, 16 and 48 are filled by whoever raises the vector; 24 and 56 only by the handler
 * and the code that resumes.  A probe whose delivery slot is still zero is a probe nobody
 * delivered, and `pc5_tss64_probe_verdict` says so. */
struct pc5_tss64_probe {
    u64 rsp_before;                     /* 0  the stack the fault happened on */
    u64 resume;                         /* 8  where that code wants to go back to */
    u64 gate_ist;                       /* 16 the IDT gate's IST field, as C read it */
    u64 rsp_delivery;                   /* 24 RSP as the handler found it */
    u64 ist_top;                        /* 32 the IST top the loaded TSS names */
    u64 flags_after;                    /* 40 RFLAGS once the probe had returned */
    u64 frame[5];                       /* 48 the CPU's own frame, read back off the IST stack */
};

#define PC5_TSS64_PROBE_WORDS 11

/* Decide what a filled probe proves, without reference to who filled it.
 *
 * Seven checks, in the order that makes a wrong answer diagnosable, and each with its own
 * refusal name: an IST top that was never granted; a handler that never ran; a delivery that
 * stayed on the faulting stack (the interesting one -- a CPU that ignores the IST field); a frame
 * outside the page that was granted for it; a frame with no word equal to the address the trap
 * was taken at, which is what separates a real delivery from a stack pointer that merely looks
 * right; a gate whose IST field is not the slot the TSS was built for; and a DF flag the probe
 * left set.
 *
 * The last is not pedantry.  A double fault is delivered with DF already set, and the flag is the
 * handler's to clear: leave it and the *next* fault is a real double fault, so a probe that
 * measured a switch and then left DF set would have made the machine less able to report a fault
 * than it was before the measurement.  This is why the handler clears it in place out of RFLAGS
 * itself rather than reinstalling a word it read out of the frame: on the soft-interrupt path the
 * frame's third quadword is the *saved stack pointer*, and installing that as flags is only
 * survivable by accident.
 *
 * Returns 1 and sets `*which` to "ok" when every check holds, or 0 with the first failure named.
 * It writes nothing to `state`: a verdict about a probe is not a reason to change what the TSS
 * claims. */
int pc5_tss64_probe_verdict(const struct pc5_tss64 *state, const struct pc5_tss64_probe *probe,
                           const char **which);

/* Hand the frames back after an install that turned out to be unusable, and report what
 * was released.  Called only from the failure path in main.c. */
void pc5_tss64_release(struct pc5_mem64 *pool, struct pc5_tss64 *state);

/* The descriptor words, so a test can decode them without re-implementing the
 * encoding.  Index 4 is the low half of the TSS descriptor, index 5 the high half. */
u64 pc5_tss64_gdt_word(u64 index);

const char *pc5_tss64_reason(const struct pc5_tss64 *state);

/* The descriptor type transition LTR performs in memory, as words: "available-to-busy" is a
 * load, "still-available" is a TR that was set without the CPU touching the table, and
 * "unreadable" is an attempt that never got far enough to read it. */
const char *pc5_tss64_busy_transition(const struct pc5_tss64 *state);

/* One IST slot as the loaded TSS holds it, read through the descriptor's base rather than out
 * of `ist_top[]`.  The difference is who is being quoted: `ist_top[]` is what the kernel decided
 * to write, and this is what a CPU taking a fault would actually use.  Returns 0 for a slot that
 * was never granted or a TSS that was never built. */
u64 pc5_tss64_ist_live(const struct pc5_tss64 *state, u64 index);

/* The TSS's I/O-map-base field, read back through the descriptor's base. */
u64 pc5_tss64_iomap_base(const struct pc5_tss64 *state);

#endif
