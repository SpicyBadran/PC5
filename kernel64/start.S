/* 64-bit entry.  The loader jumps here with RDI = physical address of pc5_boot64 and
 * paging already enabled, identity-mapping the image.  System V AMD64 ABI: the first
 * argument is in RDI, the stack must be 16-byte aligned at the call. */
    .section .text.entry, "ax"
    .globl _start64
    .type _start64, @function
_start64:
    cli
    movq %rdi, %rbx                 /* keep the boot-info pointer across the setup */

    /* The handover requires the loader to provide the stack, so take it from the
     * boot info.  A zero or absent value is a broken loader, not a reason to
     * borrow firmware's stack: fall back to the image's own .bss stack and let
     * the C code report the missing stack, so the failure is visible instead of
     * silent. */
    movq 88(%rdi), %rsp             /* struct pc5_boot64.stack_top */
    testq %rsp, %rsp
    jnz 1f
    leaq __stack_top(%rip), %rsp
1:  andq $-16, %rsp
    xorq %rbp, %rbp

    /* Clear .bss: the image is loaded from a file, so nothing may assume zeros. */
    leaq __bss_start(%rip), %rdi
    leaq __bss_end(%rip), %rcx
    subq %rdi, %rcx
    shrq $3, %rcx
    xorq %rax, %rax
    rep stosq

    movq %rbx, %rdi
    call kmain
1:  cli
    hlt
    jmp 1b
    .size _start64, . - _start64

    .section .note.GNU-stack, "", @progbits
