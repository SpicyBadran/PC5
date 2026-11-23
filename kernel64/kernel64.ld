/* The experimental 64-bit kernel is linked as a flat image at 1 MiB, identity-mapped by
 * the loader.  Small code model is enough at this address, and no PIE/PLT is emitted. */
ENTRY(_start64)

SECTIONS
{
    . = 0x100000;
    __kernel_start = .;

    /* The image's own header (kernel64/kimage64_head.c), padded to a page so .text -- and
     * therefore every absolute address the linker resolved -- keeps the addresses it would
     * have had without the header.  KEEP: nothing references it, the *loader* does, and a
     * section the linker cannot see a reference to is a section `--gc-sections` deletes.
     * The padding is inside the output section rather than left to the next section's
     * ALIGN so that `objcopy -O binary` sees one contiguous loadable span: a gap between
     * sections is filled with zeros only in some objcopy versions, and a kernel image whose
     * header page was dropped is a refusal at boot, not a debugging exercise. */
    .head : ALIGN(4096) {
        KEEP(*(SORT_BY_ALIGNMENT(.head*)))
        . = ALIGN(4096);
    }

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
