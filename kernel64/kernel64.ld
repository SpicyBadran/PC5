/* The experimental 64-bit kernel is linked as a flat image at 1 MiB, identity-mapped by
 * the loader.  Small code model is enough at this address, and no PIE/PLT is emitted. */
ENTRY(_start64)

SECTIONS
{
    . = 0x100000;
    __kernel_start = .;

    .text : ALIGN(4096) {
        *(.text.entry)
        *(.text*)
    }

    .rodata : ALIGN(4096) { *(.rodata*) }
    .data   : ALIGN(4096) { *(.data*) }

    .bss : ALIGN(4096) {
        __bss_start = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(16);
        . += 32768;              /* the initial kernel stack */
        __stack_top = .;
        . = ALIGN(4096);
        __bss_end = .;
    }

    __kernel_end = .;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
