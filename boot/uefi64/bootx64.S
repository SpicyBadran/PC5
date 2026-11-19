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

/* Hand the machine to the kernel.
 *   RDI = physical address of PC5's PML4, RSI = kernel entry, RDX = boot info
 *
 * This is the only code that runs between installing PC5's page tables and
 * entering the kernel, and it is assembly so that "nothing else executes on the
 * new map" is a property of the instruction sequence rather than a comment on a C
 * function.  It never returns on success.
 *
 * Order matters:
 *   - the caller's interrupt state and firmware CR3 are saved first, so a failed
 *     switch can put the machine back exactly as it was and return -1;
 *   - interrupts are masked, because PC5 installs no IDT and an interrupt taken
 *     between the CR3 write and the kernel's first instruction would run a
 *     handler described by firmware's now-abandoned tables;
 *   - CR3 is read back and compared: a silently ignored write would otherwise
 *     leave the loader executing on firmware's map believing otherwise;
 *   - only then is RSP moved to the kernel stack.  Until that instruction, every
 *     memory access - including these - is on firmware's stack, which is still
 *     mapped because the map is an identity map that includes it.
 *
 * stack_top lives at offset 88 of struct pc5_boot64; that offset is asserted with
 * _Static_assert in kernel64/main.c and boot/uefi64/uefi64_main.c, so a layout
 * change breaks the build instead of this jump.
 *
 * `andq $-16` on the line below aligns RSP for a *jump*, so the kernel sees 0
 * mod 16 rather than the 8 mod 16 a `call` would leave.  See the comment at the
 * instruction; `_start64` reloads its own stack before it uses one.
 */
    .globl pc5_enter_kernel64
    .type pc5_enter_kernel64, @function
pc5_enter_kernel64:
    pushq %rbx
    pushq %r12
    pushq %r13
    pushfq
    popq %rbx                      /* caller's RFLAGS, restored on failure */
    movq %cr3, %r12                /* firmware's root, restored on failure */
    movq %rdi, %r13                /* PC5's root, the only value written */
    cli
    movq %r13, %cr3
    movq %cr3, %rax
    cmpq %r13, %rax
    jne .Lpc5_handoff_failed
    movq 88(%rdx), %rsp            /* struct pc5_boot64.stack_top */
    /* 16-byte alignment, but not the SysV *call* alignment.
     *
     * This is a `jmp`, not a `call`: no return address was pushed, so after
     * `andq $-16` RSP is congruent to 0 mod 16 at the kernel's first instruction.
     * The SysV AMD64 convention (3.2.2) is stated for function *entry via call*,
     * where RSP is 16-byte aligned immediately before the call and therefore 8
     * mod 16 once the return address is on the stack; code that assumes that
     * relationship would be wrong here.
     *
     * Nothing depends on it, because `_start64` does not run on this stack.  Its
     * first action is to load `__stack_top` from the image's own .bss and align
     * that, so the value set here is a starting point that is discarded before a
     * single push.  The alignment is done anyway so the register is never left
     * pointing at a misaligned address on the way in.
     *
     * The contract that does matter is enforced in C, not here: the kernel checks
     * that RSP is 16-byte aligned and inside [stack_top - stack_size, stack_top]
     * *after* it has adopted the loader's stack (kernel64/main.c), and that is the
     * stack every later frame uses. */
    andq $-16, %rsp
    cld
    xorl %ebp, %ebp                /* no caller frame for the kernel to unwind */
    movq %rdx, %rdi                /* SysV first argument: boot info */
    jmpq *%rsi                     /* still in long mode: no mode switch */
.Lpc5_handoff_failed:
    movq %r12, %cr3
    pushq %rbx
    popfq
    popq %r13
    popq %r12
    popq %rbx
    movl $-1, %eax
    ret
    .size pc5_enter_kernel64, . - pc5_enter_kernel64

    .section .note.GNU-stack, "", @progbits
