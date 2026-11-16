/* 64-bit entry.  The loader jumps here with RDI = physical address of pc5_boot64 and
 * paging already enabled, identity-mapping the image.  System V AMD64 ABI: the first
 * argument is in RDI, the stack must be 16-byte aligned at the call. */
    .section .text.entry, "ax"
    .globl _start64
    .type _start64, @function
_start64:
    cli
    /* Keep the boot-info pointer: the clear below borrows RDI for the destination. */
    movq %rdi, %rbx

    /* Run on the image's own stack until the loader's stack has been validated.
     * Taking RSP straight from the boot info would dereference a value nothing has
     * checked yet: a bad stack_top makes the very next push fault, and the check
     * that would have reported it never runs.  The .bss stack is part of the loaded
     * image, so it is mapped by definition. */
    leaq __stack_top(%rip), %rsp
    andq $-16, %rsp
    xorq %rbp, %rbp

    /* Clear .bss: the image is loaded from a file, so nothing may assume zeros.
     * This includes the stack area just adopted, which is why the pointer is taken
     * first and nothing is read from the stack in between. */
    leaq __bss_start(%rip), %rdi
    leaq __bss_end(%rip), %rcx
    subq %rdi, %rcx
    shrq $3, %rcx
    xorq %rax, %rax
    rep stosq

    movq %rbx, %rdi
    /* kmain validates the handover, adopts the loader's stack and continues.  It
     * does not return; if it ever did there would be no valid context to return
     * to, so stop rather than fall through into whatever follows. */
    call kmain
1:  cli
    hlt
    jmp 1b
    .size _start64, . - _start64

/* Continue on a validated stack.
 *   RDI = new stack top, RSI = continuation, RDX = argument
 *
 * Called only after the C code has established that the stack is inside the map
 * and page-aligned.  The old frame is abandoned deliberately: the continuation
 * builds a new one on the new stack, and nothing in it refers to the old stack.
 */
    .globl pc5_adopt_stack
    .type pc5_adopt_stack, @function
pc5_adopt_stack:
    movq %rdi, %rsp
    andq $-16, %rsp
    cld
    xorq %rbp, %rbp
    movq %rdx, %rdi
    call *%rsi
    /* A continuation that returns is a kernel bug; say nothing and stop. */
2:  cli
    hlt
    jmp 2b
    .size pc5_adopt_stack, . - pc5_adopt_stack

    .section .note.GNU-stack, "", @progbits
