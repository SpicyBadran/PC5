/* The first 128 bytes of `KERNEL64.BIN`: the image's own description of itself.
 *
 * Written by the linker, finished by `tools/kimage64.py`, read by the loader.  The split
 * follows what each of them can know:
 *
 *   * what is fixed by the *format* -- the magic, the version, the header size, the
 *     alignment requirements, the architecture pair, and the load address the linker
 *     script was told to use -- is compiled in here, so a change to the format is a change
 *     to a file that gets reviewed;
 *   * what only exists after the image has been linked -- the exact file length, the
 *     offset of `_start64`, the size of `.bss`, and the two checksums that depend on all
 *     three -- is stamped by the tool, which reads those values out of the ELF's own
 *     symbols (`__kernel_start`, `_start64`, `__bss_start`, `__bss_end`) rather than
 *     recomputing them from the binary.  Nothing here hard-codes a size, which is how a
 *     hand-typed number goes stale the first time a line is added to the kernel.
 *
 * The bytes this object does *not* fill in are zero, and validation requires the reserved
 * fields to be zero and the declared length to equal the file's -- so a build that skipped
 * the stamping step is a refusal at boot (`kimage64-image-size`, because no real kernel is
 * 0 bytes long), not an image that quietly carries a header full of zeros.
 *
 * The entry point's alignment is not asserted here, and cannot be: `_start64` lives in
 * another translation unit.  It is guaranteed by the linker script, which starts `.text`
 * on a page boundary, and it is *checked* by everything that consumes the image -- the
 * stamper refuses to write a header that would not validate, the loader validates before
 * it jumps, and the release verifier validates again from the bytes on the medium.
 *
 * The section is `".head"`, placed first by `kernel64/kernel64.ld` and padded to a page
 * there, so the payload starts at 0x101000 and every instruction keeps the address the
 * linker gave it: this image is not relocatable, so putting a header in front of it is
 * only safe because the space was the linker's to give.
 */
#include "include/pc5_boot64.h"
#include "include/pc5_kimage64.h"

struct pc5_kimage64_head {
    u8 magic[8];
    u32 header_version;
    u32 header_size;
    u64 image_size;
    u64 load_base;
    u64 entry_offset;
    u32 entry_align;
    u32 load_align;
    u32 arch;
    u32 machine;
    u64 bss_size;
    u64 checksum;
    u32 reserved0;
    u32 reserved1;
    u32 header_crc;
    u8 reserved2[PC5_KIMAGE64_RESERVED2_SIZE];
};

#define PC5_KIMAGE64_ASSERT(field, offset)                              \
    _Static_assert(__builtin_offsetof(struct pc5_kimage64_head, field)  \
                   == (offset), #field " is not at the offset the format documents")

/* The format header and this struct are the same promise written twice, which is the
 * point: `tools/kimage64.py` and the firmware gate parse the *offsets*, so a field moved
 * in the struct without a matching change to the offsets fails to compile, and one changed
 * in the offsets without a matching change here fails `tests/test_kimage64.py`. */
PC5_KIMAGE64_ASSERT(magic, PC5_KIMAGE64_OFF_MAGIC);
PC5_KIMAGE64_ASSERT(header_version, PC5_KIMAGE64_OFF_HEADER_VERSION);
PC5_KIMAGE64_ASSERT(header_size, PC5_KIMAGE64_OFF_HEADER_SIZE);
PC5_KIMAGE64_ASSERT(image_size, PC5_KIMAGE64_OFF_IMAGE_SIZE);
PC5_KIMAGE64_ASSERT(load_base, PC5_KIMAGE64_OFF_LOAD_BASE);
PC5_KIMAGE64_ASSERT(entry_offset, PC5_KIMAGE64_OFF_ENTRY_OFFSET);
PC5_KIMAGE64_ASSERT(entry_align, PC5_KIMAGE64_OFF_ENTRY_ALIGN);
PC5_KIMAGE64_ASSERT(load_align, PC5_KIMAGE64_OFF_LOAD_ALIGN);
PC5_KIMAGE64_ASSERT(arch, PC5_KIMAGE64_OFF_ARCH);
PC5_KIMAGE64_ASSERT(machine, PC5_KIMAGE64_OFF_MACHINE);
PC5_KIMAGE64_ASSERT(bss_size, PC5_KIMAGE64_OFF_BSS_SIZE);
PC5_KIMAGE64_ASSERT(checksum, PC5_KIMAGE64_OFF_CHECKSUM);
PC5_KIMAGE64_ASSERT(reserved0, PC5_KIMAGE64_OFF_RESERVED0);
PC5_KIMAGE64_ASSERT(reserved1, PC5_KIMAGE64_OFF_RESERVED1);
PC5_KIMAGE64_ASSERT(header_crc, PC5_KIMAGE64_OFF_HEADER_CRC);
PC5_KIMAGE64_ASSERT(reserved2, PC5_KIMAGE64_OFF_RESERVED2);
_Static_assert(sizeof(struct pc5_kimage64_head) == PC5_KIMAGE64_HEADER_SIZE,
               "the kernel image header is not the size the format documents");
_Static_assert(PC5_KERNEL64_LINK_BASE % PC5_KIMAGE64_LOAD_ALIGN == 0,
               "the link address does not satisfy the header's own alignment promise");

__attribute__((section(".head"), used, aligned(16)))
const struct pc5_kimage64_head pc5_kimage64_head = {
    .magic = { 'P', 'C', '5', 'K', '6', '4', 'I', 'M' },
    .header_version = PC5_KIMAGE64_VERSION,
    .header_size = PC5_KIMAGE64_HEADER_SIZE,
    .image_size = 0,              /* stamped */
    .load_base = PC5_KERNEL64_LINK_BASE,
    .entry_offset = 0,            /* stamped */
    .entry_align = PC5_KIMAGE64_ENTRY_ALIGN,
    .load_align = PC5_KIMAGE64_LOAD_ALIGN,
    .arch = PC5_KIMAGE64_ARCH_X86_64,
    .machine = PC5_KIMAGE64_MACHINE_EM_X86_64,
    .bss_size = 0,                /* stamped */
    .checksum = 0,                /* stamped */
    .reserved0 = 0,
    .reserved1 = 0,
    .header_crc = 0,              /* stamped */
    .reserved2 = { 0 },
};
