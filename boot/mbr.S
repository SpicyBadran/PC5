/*
 * PC5 hybrid-MBR bootstrap (the first 512 bytes of the image).
 *
 * The ISO boots two ways with one boot image:
 *
 *   CD / El Torito   firmware loads the whole 4096-byte stage-1 loader
 *                    (build/loader.bin, ISO block 24) at 07C0:0000 and jumps
 *                    to it.  This sector is not involved at all.
 *
 *   USB / hard disk  firmware loads *this* 512-byte sector at 0000:7C00, sees
 *                    55AA and jumps to it.  It must therefore load the same
 *                    stage-1 loader from the same ISO block and jump to it, so
 *                    both paths run identical code from that point on.
 *
 * tools/mkiso.py patches two 32-bit words into this sector before writing it:
 *
 *   +0x1B0  LBA of the loader in 512-byte units  (bios_boot_image_lba * 4)
 *   +0x1B4  loader length in 512-byte sectors     (4096 / 512)
 *
 * and then appends the disk signature, the partition table and 55AA, which is
 * why this file must be exactly 440 bytes: bytes 440..511 of the sector belong
 * to the builder, per the MBR layout every BIOS expects.
 *
 * The sector may not load the loader directly at 0x7C00 while executing from
 * 0x7C00, so it copies a small relay to 0x0500 and jumps there.  The relay owns
 * all of its mutable state outside the region it overwrites:
 *
 *   0x0500..  relay code (copied)
 *   0x05F0    boot drive number from DL
 *   0x05F1    retry counter
 *   0x0600    disk address packet (16 bytes)
 *
 * A failure prints one line and halts; it never jumps into unknown memory.
 * The message says MBR because that is what failed, not the loader.
 */

    .code16
    .section .text
    .globl _start

    .set DAP,         0x0600
    .set DRIVE,       0x05f0
    .set RETRIES,     0x05f1
    .set RELAY,       0x0500
    .set LOAD_ADDR,   0x7c00
    .set PATCH_LBA,   0x1b0
    .set PATCH_COUNT, 0x1b4
    /* The patch words are addressed absolutely, exactly as the CPU will see
     * them: the sector is loaded at 0000:7C00, so file offset 0x1B0 lives at
     * 0x7DB0.  A label would be relocated by the linker to the same address,
     * but a literal keeps the instruction self-explanatory and byte-checkable
     * (tests/test_hybrid.py asserts these bytes). */
    .set PATCH_WORDS, 0x7db0
    .set MSG_FAIL_RELAY, msg_fail - relay + RELAY
    .set MBR_CODE_BYTES, 440

_start:
    cli
    xorw %ax, %ax
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %ss
    movw $LOAD_ADDR, %sp
    sti
    cld

    movb %dl, DRIVE              /* firmware hands us the boot drive here */

    /* relocate the relay out of the area the loader will occupy */
    movw $relay, %si
    movw $RELAY, %di
    movw $(relay_end - relay), %cx
    rep movsb
    ljmp $0, $RELAY

/* ------------------------------------------------------------------ relay */
relay:
    movw $16, DAP                /* packet size                      */
    movw $1, DAP + 2             /* one 512-byte sector per call     */
    movw $LOAD_ADDR, DAP + 4     /* destination offset               */
    movw $0, DAP + 6             /* destination segment (0000:7C00)  */
    movl $0, DAP + 12            /* LBA bits 63..32                  */

    movl PATCH_WORDS, %eax       /* patched by tools/mkiso.py        */
    movl %eax, DAP + 8           /* LBA bits 31..0                   */
    movl PATCH_WORDS + 4, %ecx   /* loader length in 512-byte sectors */
    testl %ecx, %ecx
    jz fail

read_loop:
    movw $3, RETRIES
retry:
    movb DRIVE, %dl
    movw $DAP, %si
    movw $0x4200, %ax            /* INT 13h: extended read sectors   */
    int $0x13
    jnc read_ok
    movb DRIVE, %dl              /* reset the drive and try again    */
    xorw %ax, %ax
    int $0x13
    decw RETRIES
    jnz retry
    jmp fail

read_ok:
    addw $512, DAP + 4           /* next sector goes 512 bytes higher */
    addl $1, DAP + 8             /* LBA + 1, carrying into the high word */
    adcl $0, DAP + 12
    decl %ecx
    jnz read_loop

    movb DRIVE, %dl              /* the loader expects the boot drive  */
    ljmp $0, $LOAD_ADDR

fail:
    movw $MSG_FAIL_RELAY, %si    /* the copy at 0x0500, not the original */
1:  lodsb
    testb %al, %al
    jz 2f
    movb $0x0e, %ah              /* INT 10h teletype output          */
    movw $0x0007, %bx
    int $0x10
    jmp 1b
2:  cli
    hlt
    jmp 2b

msg_fail:
    .asciz "PC5: MBR cannot read the boot image from this disk\r\n"

relay_end:

    /* pad to the patch words, then to the byte the builder takes over */
    .space PATCH_LBA - (. - _start), 0x90
patch_lba:
    .long 0
patch_count:
    .long 0
    .space MBR_CODE_BYTES - (. - _start), 0
