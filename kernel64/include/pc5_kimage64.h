/* PC5 64-bit kernel image format, version 1.
 *
 * WHY THIS EXISTS
 *
 * `KERNEL64.BIN` used to be a bare flat binary: the first bytes of the file were the
 * first instructions (`cli; movq %rdi,%rbx`, the opening of `kernel64/start.S`).  The
 * loader read the file into the address it was linked for and jumped to byte zero of it.
 * That is not a format, and the difference mattered: a negative control that corrupted
 * the kernel on the medium could not be written for the 64-bit path, because there was no
 * field to corrupt -- changing any byte changed the code, and the loader would have run
 * the changed code anyway.  The 32-bit path refuses a kernel whose `PC5K` signature is
 * wrong (an 8-byte header at the front of `KERNEL.BIN`, `kernel/include/pc5.h`); this is
 * the same idea, carried to the size a 64-bit handover actually needs.
 *
 * What the header makes checkable, and what each field is for:
 *
 *   magic            whose image this is, so a file with the right name and the wrong
 *                    contents is a refusal rather than a jump into unknown bytes;
 *   header_version   which layout the rest of the bytes follow;
 *   header_size      where the payload starts, so a future header can grow without
 *                    moving anything the loader has to guess about;
 *   image_size       the exact file length: a kernel that is 4 KiB short has lost its
 *                    stack, and one that is longer has bytes nothing will ever execute;
 *   load_base        the physical address this image was linked for -- the promise the
 *                    loader already makes by allocating that address, now written in the
 *                    artifact too, so the two can be *compared* rather than assumed;
 *   entry_offset     where execution begins, so the loader jumps at a declared
 *                    instruction boundary instead of at byte zero of whatever the file
 *                    turned out to start with;
 *   entry_align      how that offset is aligned: an instruction boundary at 0x1001 is as
 *                    wrong as one at 0x1000 with a byte removed;
 *   load_align       the same promise about the base address;
 *   arch, machine    what CPU this runs on, in two independent encodings (see below);
 *   bss_size         how much of the tail is `.bss` the file must carry (this image has
 *                    no zero-fill-on-demand: the stack lives in `.bss`, so a file shorter
 *                    than its own declaration has no stack);
 *   header_crc       the header validating itself, before 200 KiB of payload is hashed;
 *   checksum         the payload, so a bit flip in the middle of the file -- where there
 *                    is no signature and no magic to notice -- is a refusal;
 *   reserved         zero, all of it, so a field a future version fills in is a *rejection*
 *                    today rather than a silently ignored non-zero value.
 *
 * TWO IDENTITY FIELDS, DELIBERATELY
 *
 * `arch` (PC5's own enumeration) and `machine` (the ELF `e_machine` value, 62) say the
 * same thing twice from two vocabularies.  That is not redundancy for its own sake: a
 * build that produced an image for the wrong target would have to corrupt both fields to
 * look right, and the pair turns "we checked it is x86-64" from a claim about one number
 * into a claim about two that are produced by different parts of the toolchain's mind.
 *
 * OVERFLOW, AND WHY IT IS SPelled OUT
 *
 * Every arithmetic check here is written so that no addition can wrap.  `entry_offset <
 * image_size` is compared against the *file length the loader measured*, not against
 * `load_base + image_size`; `loaded_at + size` is only ever formed after `size <=
 * UINT64_MAX - loaded_at` has been established.  A header that says `image_size =
 * 0xFFFF...` is the kind of thing a truncated write or a mismatched `objcopy` produces,
 * and a loader that computes `base + size` before it validates is a loader that validates
 * the address of a wrap.
 *
 * COMPATIBILITY
 *
 * Version 1 is the only version, and the loader accepts exactly one: an unknown
 * `header_version` is a refusal, never a best-effort read.  Growing this header means (a)
 * `header_size` changes, (b) the version changes, and (c) the loader's accepted set
 * changes -- and `tests/test_kimage64.py` pins the layout offsets against
 * `tools/kimage64.py`, which parses them by the same numbers, so a change here that skips
 * one of the three shows up as a failing test rather than as an image that boots slightly
 * wrongly.  An older loader meeting a newer image fails on `header_size` (it requires the
 * value it knows) or on `checksum`, both of which are refusals at a decision point it can
 * name.  Nothing in this format is position-independent: `load_base` is a *requirement*,
 * and a relocatable kernel image would be a new `arch` value with its own rules, not a
 * loosening of these.
 *
 * The types (u8, u32, u64) come from the includer: `boot/uefi/efi.h` for the loader,
 * `kernel64/include/pc5_boot64.h` for the kernel, as everywhere else in this directory.
 */
#ifndef PC5_KIMAGE64_H
#define PC5_KIMAGE64_H

#define PC5_KIMAGE64_MAGIC "PC5K64IM"
#define PC5_KIMAGE64_MAGIC_BYTES 8u
#define PC5_KIMAGE64_VERSION 1u
#define PC5_KIMAGE64_HEADER_SIZE 128u

/* Field offsets, so the host-side stamper, the gate's Python reader and this file cannot
 * drift apart without a test noticing.  Sizes follow from the gaps. */
#define PC5_KIMAGE64_OFF_MAGIC 0u
#define PC5_KIMAGE64_OFF_HEADER_VERSION 8u
#define PC5_KIMAGE64_OFF_HEADER_SIZE 12u
#define PC5_KIMAGE64_OFF_IMAGE_SIZE 16u
#define PC5_KIMAGE64_OFF_LOAD_BASE 24u
#define PC5_KIMAGE64_OFF_ENTRY_OFFSET 32u
#define PC5_KIMAGE64_OFF_ENTRY_ALIGN 40u
#define PC5_KIMAGE64_OFF_LOAD_ALIGN 44u
#define PC5_KIMAGE64_OFF_ARCH 48u
#define PC5_KIMAGE64_OFF_MACHINE 52u
#define PC5_KIMAGE64_OFF_BSS_SIZE 56u
#define PC5_KIMAGE64_OFF_CHECKSUM 64u
#define PC5_KIMAGE64_OFF_RESERVED0 72u
#define PC5_KIMAGE64_OFF_RESERVED1 76u
#define PC5_KIMAGE64_OFF_HEADER_CRC 80u
#define PC5_KIMAGE64_OFF_RESERVED2 84u
#define PC5_KIMAGE64_RESERVED2_SIZE 44u

/* `arch`: PC5's own enumeration, small and never reused. */
#define PC5_KIMAGE64_ARCH_X86_64 1u
#define PC5_KIMAGE64_ARCH_X86_32 2u
/* `machine`: ELF `EM_X86_64`, from the object format the kernel is linked with. */
#define PC5_KIMAGE64_MACHINE_EM_X86_64 62u

/* The two alignment promises.  16 is what the ABI asks for at a call target and what
 * `-falign-functions=16` gives; 4096 is the page the loader allocates at, and the frame
 * granularity everything below it is measured in. */
#define PC5_KIMAGE64_ENTRY_ALIGN 16u
#define PC5_KIMAGE64_LOAD_ALIGN 4096u

/* The checksums, defined once so the stamper and the loader agree on what is hashed.
 *
 *   FNV-1a, 64-bit: offset basis 14695981039346656037, prime 1099511628211.
 *   payload checksum  = fnv1a64 over the whole file, bytes [64,72) (the checksum field
 *                       itself) and [80,84) (the header CRC field) treated as zero;
 *   header CRC        = fnv1a32 (basis 2166136261, same prime) over bytes [0,80), with
 *                       no field treated as zero -- it covers the payload-independent
 *                       half, including the 64-bit checksum field as *stamped*.
 *
 * So: `header_crc` says the description is intact, `checksum` says the bytes the
 * description covers are intact, and a stamper that filled one but not the other is
 * caught by the first, not silently accepted. */
#define PC5_FNV64_BASIS 14695981039346656037ull
#define PC5_FNV64_PRIME 1099511628211ull
#define PC5_FNV32_BASIS 2166136261u
#define PC5_FNV32_PRIME 16777619u

/* What a caller may act on once validation passed: the fields, already read and already
 * range-checked, so the jump site does not re-read the image (and so a bug cannot make it
 * re-read a byte that was never checked). */
struct pc5_kimage64 {
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
    u64 entry_address;            /* load_base + entry_offset, computed and re-checked */
};

/* Check `image` (the `size` bytes read from the medium, now living at `loaded_at`) against
 * its own header and against `out`'s expectations.  Returns 0 and fills `out` on success;
 * returns a named reason on refusal and leaves `out` zeroed.
 *
 * The reason strings are part of the boot contract, not debug text: the loader prints
 * `PC5-64-LOADER-FAIL=<reason>` and the firmware gate asserts that a mutant which corrupts
 * field X produces exactly the reason for field X.  Renaming one is a contract change. */
const char *pc5_kimage64_validate(const u8 *image, u64 size, u64 loaded_at,
                                  struct pc5_kimage64 *out);

/* RUNTIME USE: only the header half of the same rules, and the reason it is a separate entry
 * point is a property of the kernel, not of the format.
 *
 * The running kernel cannot recompute `checksum`: it covers the whole image, `.bss` travels
 * inside that image, and `_start64` adopts a stack from `.bss` and clears it before `kmain`
 * runs.  By the time any C code executes, part of the checked range is in use as a stack --
 * so a runtime payload sum would not be a check of the load, it would be a check of the
 * ordering of the boot code, and it would fail.  The header is the opposite case: 128 bytes
 * in `.head`, which nothing writes, so `header_crc` and every bound the format defines can
 * be re-derived after the jump with no assumption about what has run in between.
 *
 * What that leaves is still a real claim, and it is the one the loader alone could not make:
 * the header is the image's own account of its length, and `size` here is the *caller's*
 * account of the same thing.  The loader passes what it read from the medium; the kernel
 * passes what it was handed.  A handover that understates or overstates the image is refused
 * by the party the claim is about, in the same code, against the same field.
 */
const char *pc5_kimage64_validate_header(const u8 *image, u64 size, u64 loaded_at,
                                         struct pc5_kimage64 *out);

/* The payload checksum, exposed so the stamper's output can be checked by the same code
 * the loader runs rather than by a second implementation that is equal only by luck. */
u64 pc5_kimage64_payload_checksum(const u8 *image, u64 size);
u32 pc5_kimage64_header_crc(const u8 *image, u64 size);

#endif /* PC5_KIMAGE64_H */
