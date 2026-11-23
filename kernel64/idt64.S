/* One trampoline for every exception vector.
 *
 * The CPU's frame differs between vectors: some push an error code, most do not.
 * A stub that ignores the difference reads the saved RIP as the error code and the
 * saved CS as the RIP, producing a report that is plausible and wrong - the worst
 * kind of wrong. So the frame is normalised here, in assembly, and the C side never
 * has to know which vector it is looking at.
 *
 * Target frame at the top of the stack when the trampoline runs:
 *
 *     [rsp+0]   vector
 *     [rsp+8]   error code, or PC5_NOERR
 *     [rsp+16]  rip      (pushed by the CPU)
 *     [rsp+24]  cs       (pushed by the CPU)
 *     [rsp+32]  rflags   (pushed by the CPU)
 *
 * A stub only has to push the vector, and for no-error vectors also a sentinel
 * *under* it, so both flavours agree.
 *
 * Which vectors push an error code is architectural (Intel SDM vol. 3A, table 6-1):
 * 8, 10, 11, 12, 13, 14, 17, 21, 29, 30. Getting that list backwards is exactly the
 * silent-misreport bug above, so it appears once, here, and tests/test_idt64.py
 * parses this file to check the two stub groups partition 0..31.
 *
 * Only 0..31 get stubs. Vectors 32..255 are hardware-interrupt vectors and nothing
 * in this kernel has enabled interrupts, so a legitimate one cannot arrive; leaving
 * those entries not-present turns a stray one into a captured #GP (vector 13)
 * instead of silently accepting it.
 */
    .section .text.idt, "ax"

    .globl pc5_exception_entry
    .type pc5_exception_entry, @function
pc5_exception_entry:
    movq  0(%rsp), %rdi        /* vector, pushed by the stub */
    movq  8(%rsp), %rsi        /* error code, or the sentinel */
    movq 16(%rsp), %rdx        /* rip */
    movq 24(%rsp), %rcx        /* cs */
    movq 32(%rsp), %r8         /* rflags */

    /* A fault inside the reporter must not fault again. */
    cli
    andq $-16, %rsp
    call idt64_report

1:  cli                        /* idt64_report does not return */
    hlt
    jmp 1b
    .size pc5_exception_entry, . - pc5_exception_entry

/* A vector the CPU pushes an error code for: only the vector need be added. */
    .macro PC5_STUB_ERR n
    .globl pc5_vector_\n
pc5_vector_\n:
    pushq $\n
    jmp pc5_exception_entry
    .endm

/* A vector with no error code: fabricate the slot so the frame matches.
 *
 * `pushq $imm64` is not encodable - only an imm32 that sign-extends - so the
 * sentinel is materialised in %rax. %rax is saved and restored with `xchg`, which
 * puts the sentinel in the slot and the caller's value back in %rax in one step.
 * A fault handler that silently destroyed a register the faulting code was using
 * would make the report itself part of the bug.
 */
    .macro PC5_STUB_NOERR n
    .globl pc5_vector_\n
pc5_vector_\n:
    pushq %rax
    movq  $0x5A5A5A5A5A5A5A5A, %rax   /* PC5_NOERR; must match idt64.h */
    xchgq %rax, (%rsp)
    pushq $\n
    jmp pc5_exception_entry
    .endm

    /* Vectors that push an error code. */
    PC5_STUB_ERR   8
    PC5_STUB_ERR  10
    PC5_STUB_ERR  11
    PC5_STUB_ERR  12
    PC5_STUB_ERR  13
    PC5_STUB_ERR  14
    PC5_STUB_ERR  17
    PC5_STUB_ERR  21
    PC5_STUB_ERR  29
    PC5_STUB_ERR  30

    /* Every other vector in 0..31. */
    PC5_STUB_NOERR  0
    PC5_STUB_NOERR  1
    PC5_STUB_NOERR  2
    PC5_STUB_NOERR  3
    PC5_STUB_NOERR  4
    PC5_STUB_NOERR  5
    PC5_STUB_NOERR  6
    PC5_STUB_NOERR  7
    PC5_STUB_NOERR  9
    PC5_STUB_NOERR 15
    PC5_STUB_NOERR 16
    PC5_STUB_NOERR 18
    PC5_STUB_NOERR 19
    PC5_STUB_NOERR 20
    PC5_STUB_NOERR 22
    PC5_STUB_NOERR 23
    PC5_STUB_NOERR 24
    PC5_STUB_NOERR 25
    PC5_STUB_NOERR 26
    PC5_STUB_NOERR 27
    PC5_STUB_NOERR 28
    PC5_STUB_NOERR 31

/* The IST delivery probe's entry point, and the scratch pointer it is handed.
 *
 * This is scaffolding for a measurement, not a recovery path, and the difference matters.
 * `pc5_exception_entry` below prints and halts, because a fault this kernel has not been
 * shown to survive has no sane continuation.  A probe is different: the kernel deliberately
 * raised vector 8 with an IST field armed, and wants to know what the CPU did -- where it
 * put the frame, whether it switched stacks -- and then wants to carry on.  Returning from a
 * real #DF is not a thing an OS does (the stack it was running on is the suspect), so this
 * does not pretend to: the *faulting code* recorded its own RSP and resume address before
 * raising, and this entry copies the three words the CPU pushed, restores RFLAGS from the
 * frame (the CPU set DF on delivery; leaving it set would turn the next fault into a real
 * double fault), and jumps back to the recorded address.  Nothing is reconstructed or
 * guessed, and if the pointer is not armed it halts rather than inventing a return.
 *
 * The frame here is the CPU's own, with no vector number pushed: the #DF gate points straight
 * at this label for the duration of a probe, so the ordinary stub's extra push is absent and the
 * offsets below are one quadword lower than the ones `pc5_exception_entry` uses.  Nothing
 * dispatches here on a test of the armed pointer -- the ordinary entry keeps its own frame shape
 * and its report-and-halt behaviour, because a #PF that arrives while a probe is outstanding is
 * a real fault and must be reported as one, and because a handler that inferred its frame layout
 * from a flag is one stale flag away from reading a vector number as a saved RIP.  Getting these
 * four offsets right was not obvious: the push order is RFLAGS, RIP, CS, error code, so the
 * *lowest* address holds the last thing pushed (the error code) and CS sits at +8 where a first
 * reading would put RIP.  A probe that read the wrong slot printed `frame-rip=0x38` -- a code
 * selector, in the right shape to be believed.
 *
 * Nothing here reads the frame, and that is deliberate.  What the CPU pushes above the new RSP
 * depends on the path the vector arrived by: a soft `int $8` under QEMU leaves rip, cs, rflags,
 * rsp and ss (five quadwords, no error code), a real double fault leaves an error code first, and
 * a privilege change leaves two more.  The handler records one number that no version of that
 * argument can change -- the RSP it was entered with -- and the frame itself is read back later,
 * from memory, by the code that knows what it is looking for.  An earlier revision copied the
 * frame's RIP and RFLAGS out at fixed offsets here; on the soft-interrupt path the word at +16 is
 * not the RIP and the word at +24 is the *saved stack pointer*, so the probe reported a
 * mismatched RIP and installed a plausible-looking value as flags, and both survived only because
 * nothing checked them.  A handler that has to be right about the layout to be right at all is the
 * thing to avoid, not the thing to document.
 *
 * Slots in the context (see struct pc5_tss64_probe in tss64.h; the three offsets used here are
 * duplicated in the assembly and pinned by tests/test_tss64.py, because an asm/C disagreement
 * about a layout is the kind of bug that reports success):
 *
 *     0   rsp the probe recorded before raising     (written by the trigger, read here)
 *     8   resume address                            (written by the trigger, jumped to here)
 *    24   RSP as found on delivery                  (written here, and nothing else)
 */
    .bss
    .globl pc5_ist_probe_ctx
    .balign 64
pc5_ist_probe_ctx:
    .zero 64

    .section .text.idt, "ax"
    .globl pc5_ist_probe_entry
    .type pc5_ist_probe_entry, @function
pc5_ist_probe_entry:
    cli
    movq  pc5_ist_probe_ctx(%rip), %rdi
    testq %rdi, %rdi
    jz    9f
    movq  %rsp, 24(%rdi)               /* where the CPU put the frame */
    movq   0(%rdi), %rsp                /* back to the stack the probe recorded */
    pushfq
    andq  $~0x400, (%rsp)               /* clear DF in place; every other flag untouched */
    popfq
    movq   8(%rdi), %rdx                /* our recorded resume address, not the frame's */
    xorl  %ecx, %ecx
    movq  %rcx, pc5_ist_probe_ctx(%rip) /* one shot: a second delivery must not bounce */
    jmp   *%rdx
9:  cli
    hlt                                /* never armed: refuse to fabricate a return */
    jmp 9b
    .size pc5_ist_probe_entry, . - pc5_ist_probe_entry

    .section .note.GNU-stack, "", @progbits
