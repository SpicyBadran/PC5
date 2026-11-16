/* 64-bit UEFI entry for the experimental PC5 64-bit boot path.
 *
 * Firmware calls here in long mode with the Microsoft x64 convention:
 *   RCX = ImageHandle, RDX = SystemTable
 * and with its own page tables, stack and GDT.  Nothing here switches back to
 * 32-bit: the loader replaces CR3 with PC5's own tables and the kernel is
 * entered in long mode, which is the whole point of this path.
 */
    .section .text
    .globl _start
_start:
    /* .bss is NOLOAD, so the image does not carry it and firmware will not
     * zero it: do that before any C code runs. */
    leaq __bss_start(%rip), %rdi
    leaq __bss_end(%rip), %rcx
    subq %rdi, %rcx
    shrq $3, %rcx
    xorq %rax, %rax
    rep stosq

    /* ms_abi call: keep the stack 16-byte aligned and reserve 32 bytes of
     * shadow space.  Arguments are already in RCX/RDX. */
    subq $0x28, %rsp
    call efi_main64
    addq $0x28, %rsp

    /* efi_main64 does not return on success.  If it returns, the boot failed
     * and it has already said why on the serial port; stop here rather than
     * returning into firmware with a half-built machine. */
1:  cli
    hlt
    jmp 1b

/* Hand the machine to the kernel: RDI = entry point, RSI = boot info.
 * The kernel gets PC5's own stack, taken from the boot-info structure, so the
 * firmware's stack can be abandoned with the rest of boot services. */
    .globl pc5_enter_kernel64
    .type pc5_enter_kernel64, @function
pc5_enter_kernel64:
    movq %rdi, %rax                /* entry point */
    movq 88(%rsi), %rsp            /* struct pc5_boot64.stack_top */
    andq $-16, %rsp
    movq %rsi, %rdi                /* first argument: boot info */
    jmp *%rax                      /* still in long mode: no mode switch */
    .size pc5_enter_kernel64, . - pc5_enter_kernel64

    .section .note.GNU-stack, "", @progbits
