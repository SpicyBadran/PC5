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

    .section .note.GNU-stack, "", @progbits
