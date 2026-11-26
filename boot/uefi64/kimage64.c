/* Validation of the PC5 64-bit kernel image header (see
 * kernel64/include/pc5_kimage64.h for the format and why it exists).
 *
 * This file is deliberately free of firmware calls: it reads bytes and returns a reason.
 * That is what lets the *same* code be the loader's gatekeeper and the subject of a host
 * battery (`tests/test_kimage64.py`), where every malformed header in the brief -- bad
 * magic, wrong version, wrong architecture, truncation, a corrupted signature, an entry
 * outside the image, a declared size that is not the real one, an unaligned entry, a
 * non-power-of-two alignment, a `.bss` bigger than the file, a dirty reserved field, a
 * header whose own CRC is wrong -- is fed to the implementation that ships and expected to
 * refuse by name.
 *
 * Two properties the code is written to keep, because they are the ones a reader cannot
 * see from a green test:
 *
 *   * Nothing is trusted before it is bounded.  Every field is read from inside
 *     `[0, PC5_KIMAGE64_HEADER_SIZE)` of a buffer already established to be that long, so
 *     no check can read past what was read from the medium;
 *   * Nothing wraps.  The only sum formed is `loaded_at + entry_offset`, and it is formed
 *     after both `entry_offset < size` and `size <= UINT64_MAX - loaded_at` hold -- which
 *     is also why `entry_address` is reported rather than recomputed by each caller.
 *
 * The order of the checks is the order of the reasons, and both are load-bearing: a mutant
 * that corrupts one field has to be refused *for that field*, so an earlier check must not
 * fire first on the same input.  `tests/test_kimage64.py` asserts the pairing per case,
 * and the firmware gate asserts it for the three corruptions that survive being booted.
 */
/* The types only, from the firmware header: u8/u32/u64 are the same identical typedefs
 * every other file in this directory uses, and nothing here calls a firmware service.  A
 * validator that needs `EFI_BOOT_SERVICES` to compile is a validator the host battery cannot
 * run, and the battery is the reason the reasons are strings. */
#include "../uefi/efi.h"
#include "pc5_kimage64.h"

static u64 le64(const u8 *p)
{
    return (u64)p[0] | ((u64)p[1] << 8) | ((u64)p[2] << 16) | ((u64)p[3] << 24)
        | ((u64)p[4] << 32) | ((u64)p[5] << 40) | ((u64)p[6] << 48) | ((u64)p[7] << 56);
}

static u32 le32(const u8 *p)
{
    return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}

static u32 fnv32(u32 hash, const u8 *p, u64 n)
{
    u64 i;

    for (i = 0; i < n; i++) {
        hash ^= (u32)p[i];
        hash *= PC5_FNV32_PRIME;
    }
    return hash;
}

u64 pc5_kimage64_payload_checksum(const u8 *image, u64 size)
{
    /* Two zeroed windows, in ascending order and non-overlapping, because that is what
     * the format comment defines: the checksum field, then the header CRC field.  A
     * single "skip the first 84 bytes" would be wrong in a way that both sides agree on,
     * which is the worst kind of wrong -- it stamps and validates the same mistake. */
    u64 hash = PC5_FNV64_BASIS;
    u64 i;

    for (i = 0; i < size; i++) {
        u8 byte = image[i];

        if ((i >= PC5_KIMAGE64_OFF_CHECKSUM
             && i < PC5_KIMAGE64_OFF_CHECKSUM + 8u) ||
            (i >= PC5_KIMAGE64_OFF_HEADER_CRC
             && i < PC5_KIMAGE64_OFF_HEADER_CRC + 4u))
            byte = 0u;
        hash ^= (u64)byte;
        hash *= PC5_FNV64_PRIME;
    }
    return hash;
}

u32 pc5_kimage64_header_crc(const u8 *image, u64 size)
{
    u64 n = size < PC5_KIMAGE64_OFF_HEADER_CRC ? size : PC5_KIMAGE64_OFF_HEADER_CRC;

    return fnv32(PC5_FNV32_BASIS, image, n);
}

static int is_power_of_two(u32 v)
{
    return v != 0u && (v & (v - 1u)) == 0u;
}

/* Everything about the *description*, and nothing about the bytes it describes.
 *
 * Split out of `pc5_kimage64_validate` rather than copied, for the reason the format
 * documents: a second implementation that agrees with the first only by luck is how this
 * milestone's first two bugs survived.  The running kernel calls this one, because the
 * payload sum it would need in order to use the full validator is not computable by it --
 * see the `RUNTIME USE` note in `pc5_kimage64.h`.
 *
 * `size` is not "the length of the buffer" here, though it is that too: it is *the claim
 * being checked against the header*.  The loader passes the number of bytes it read from the
 * medium; the kernel passes the `kernel_size` it was handed.  Both are then compared against
 * `image_size`, which the image carries itself, so a disagreement is a refusal at whichever
 * decision point noticed -- and neither caller is comparing a value with itself.
 */
const char *pc5_kimage64_validate_header(const u8 *image, u64 size, u64 loaded_at,
                                        struct pc5_kimage64 *out)
{
    u64 image_size;
    u64 entry;
    u32 header_size;

    /* Byte-wise on purpose: the struct has padding after the u32 fields, and "the caller
     * may only read what was checked" is only true if nothing is left holding the
     * caller's previous stack contents. */
    {
        u8 *bytes = (u8 *)out;

        for (u64 i = 0; i < (u64)sizeof(*out); i++)
            bytes[i] = 0u;
    }

    if (image == 0 || size < PC5_KIMAGE64_HEADER_SIZE)
        return "kimage64-truncated";
    if (image[0] != 'P' || image[1] != 'C' || image[2] != '5' || image[3] != 'K'
        || image[4] != '6' || image[5] != '4' || image[6] != 'I' || image[7] != 'M')
        return "kimage64-magic";
    if (le32(image + PC5_KIMAGE64_OFF_HEADER_VERSION) != PC5_KIMAGE64_VERSION)
        return "kimage64-version";

    header_size = le32(image + PC5_KIMAGE64_OFF_HEADER_SIZE);
    if (header_size != PC5_KIMAGE64_HEADER_SIZE)
        return "kimage64-header-size";

    /* The header's own integrity first: everything below trusts a number out of it, so a
     * header that decodes to nonsense must not be allowed to reach the payload hash. */
    if (le32(image + PC5_KIMAGE64_OFF_HEADER_CRC) != pc5_kimage64_header_crc(image, size))
        return "kimage64-header-crc";

    image_size = le64(image + PC5_KIMAGE64_OFF_IMAGE_SIZE);
    if (image_size != size)
        return "kimage64-image-size";
    if (le64(image + PC5_KIMAGE64_OFF_LOAD_BASE) != loaded_at)
        return "kimage64-load-base";
    if (le32(image + PC5_KIMAGE64_OFF_ARCH) != PC5_KIMAGE64_ARCH_X86_64)
        return "kimage64-arch";
    if (le32(image + PC5_KIMAGE64_OFF_MACHINE) != PC5_KIMAGE64_MACHINE_EM_X86_64)
        return "kimage64-machine";

    if (!is_power_of_two(le32(image + PC5_KIMAGE64_OFF_ENTRY_ALIGN)))
        return "kimage64-entry-align";
    if (!is_power_of_two(le32(image + PC5_KIMAGE64_OFF_LOAD_ALIGN)))
        return "kimage64-load-align";
    if ((loaded_at % le32(image + PC5_KIMAGE64_OFF_LOAD_ALIGN)) != 0u)
        return "kimage64-load-align";

    /* `.bss` travels in this image (there is no zero-fill elsewhere), and the initial
     * kernel stack is inside it: a declaration bigger than the file is a file that lost
     * its stack, and the first push after `_start64` would fault on memory nobody owns. */
    if (le64(image + PC5_KIMAGE64_OFF_BSS_SIZE) > image_size)
        return "kimage64-bss-size";

    entry = le64(image + PC5_KIMAGE64_OFF_ENTRY_OFFSET);
    if (entry < header_size || entry >= image_size)
        return "kimage64-entry-range";
    if ((entry % le32(image + PC5_KIMAGE64_OFF_ENTRY_ALIGN)) != 0u)
        return "kimage64-entry-align";
    /* The two bounds above make this safe to form -- and that is the only reason it is
     * formed here: the caller jumps at the result, so it has to be a value that cannot
     * have wrapped into a low address. */
    if (image_size > 0xFFFFFFFFFFFFFFFFull - loaded_at)
        return "kimage64-load-base";

    if (le32(image + PC5_KIMAGE64_OFF_RESERVED0) != 0u
        || le32(image + PC5_KIMAGE64_OFF_RESERVED1) != 0u)
        return "kimage64-reserved";
    for (u64 i = 0; i < (u64)PC5_KIMAGE64_RESERVED2_SIZE; i++) {
        if (image[PC5_KIMAGE64_OFF_RESERVED2 + i] != 0u)
            return "kimage64-reserved";
    }

    out->header_version = PC5_KIMAGE64_VERSION;
    out->header_size = header_size;
    out->image_size = image_size;
    out->load_base = loaded_at;
    out->entry_offset = entry;
    out->entry_align = le32(image + PC5_KIMAGE64_OFF_ENTRY_ALIGN);
    out->load_align = le32(image + PC5_KIMAGE64_OFF_LOAD_ALIGN);
    out->arch = PC5_KIMAGE64_ARCH_X86_64;
    out->machine = PC5_KIMAGE64_MACHINE_EM_X86_64;
    out->bss_size = le64(image + PC5_KIMAGE64_OFF_BSS_SIZE);
    out->checksum = le64(image + PC5_KIMAGE64_OFF_CHECKSUM);
    out->entry_address = loaded_at + entry;
    return 0;
}

const char *pc5_kimage64_validate(const u8 *image, u64 size, u64 loaded_at,
                                  struct pc5_kimage64 *out)
{
    const char *reason = pc5_kimage64_validate_header(image, size, loaded_at, out);

    if (reason)
        return reason;
    /* Last, and only over a header that has already been bounded and CRC-checked: this is
     * the one read that walks the whole buffer, and a payload sum computed over a length
     * out of a corrupt header is a sum of nothing. */
    if (le64(image + PC5_KIMAGE64_OFF_CHECKSUM) != pc5_kimage64_payload_checksum(image, size)) {
        /* A refusal has to leave nothing usable behind, and the header half has already
         * filled `out` -- so it is cleared again here rather than left holding a valid
         * description of an image that failed its own checksum. */
        u8 *bytes = (u8 *)out;

        for (u64 i = 0; i < (u64)sizeof(*out); i++)
            bytes[i] = 0u;
        return "kimage64-checksum";
    }
    return 0;
}
