/* Memory-map helpers shared by the loader and the kernel, in one place.
 *
 * Both sides must agree on what "this range is covered by acceptable memory"
 * means; if the loader applied one rule and the kernel another, a resource could
 * pass one check and fail the other, and the disagreement would only be visible
 * as a crash.  So the arithmetic lives here and nowhere else.
 *
 * Every function is defined for all 64-bit inputs: the ranges come from firmware
 * and can wrap, can be empty, and can describe memory that does not exist.
 */
#ifndef PC5_MAP_H
#define PC5_MAP_H

#include "pc5_boot64.h"

struct pc5_map_view {
    const struct pc5_memdesc64 *descriptors;
    u64 entries;
};

/* 1 when left + right wraps the 64-bit space. */
static inline int pc5_add_overflows(u64 left, u64 right, u64 *out)
{
    if (left > ~0ull - right)
        return 1;
    if (out)
        *out = left + right;
    return 0;
}

/* 1 when left * right wraps the 64-bit space.  Multiplication cannot be checked
 * after the fact the way addition can: the wrapped product is already wrong by
 * the time a caller would test it, so the check has to happen before the multiply.
 * This is the helper that makes `pitch * height` safe; the loader used to compute
 * the product first and then pass it to pc5_add_overflows(), which cannot see the
 * wrap. */
static inline int pc5_mul_overflows(u64 left, u64 right, u64 *out)
{
    if (right != 0 && left > ~0ull / right)
        return 1;
    if (out)
        *out = left * right;
    return 0;
}

/* 1 when [start, start+size) is non-empty, does not wrap, and ends at or below
 * `limit`. */
static inline int pc5_range_within(u64 start, u64 size, u64 limit)
{
    if (size == 0)
        return 0;
    if (start >= limit)
        return 0;
    return size <= limit - start;
}

static inline int pc5_descriptor_valid(const struct pc5_memdesc64 *descriptor)
{
    if (descriptor->page_count == 0)
        return 0;
    if (descriptor->page_count > (~0ull >> 12))
        return 0;
    if (descriptor->physical_start > ~0ull - (descriptor->page_count << 12))
        return 0;
    if (descriptor->type > PC5_MMAP_TYPE_MAX)
        return 0;
    return 1;
}

static inline u64 pc5_descriptor_end(const struct pc5_memdesc64 *descriptor)
{
    return descriptor->physical_start + (descriptor->page_count << 12);
}

/* ------------------------------------------------------- framebuffer geometry */
/* The boot-info protocol carries a 32-bit pitch and 32-bit dimensions.  The
 * loader measures the firmware's geometry and the kernel re-derives the same
 * byte count before it calls a framebuffer usable, so both sides use these two
 * functions rather than each writing the arithmetic.  A framebuffer that cannot
 * be described exactly - zero dimensions, a pitch that does not fit the protocol
 * field, or a byte count that wraps 64 bits - is not usable, and saying so here
 * is what stops a wrapped size from being handed over as a small one. */

/* Convert PixelsPerScanLine to the protocol's byte pitch.  0 on a zero count, a
 * multiply that wraps, or a pitch the 32-bit field cannot hold. */
static inline int pc5_framebuffer_line(u32 pixels_per_scan_line, u32 *pitch)
{
    if (pixels_per_scan_line == 0)
        return 0;
    u64 line = 0;
    if (pc5_mul_overflows(pixels_per_scan_line, 4ull, &line))
        return 0;
    if (line > 0xFFFFFFFFull)
        return 0;
    if (pitch)
        *pitch = (u32)line;
    return 1;
}

/* Byte length of [height] scan lines of [pitch] bytes, for a [width]-pixel image.
 * 0 on zero dimensions, a pitch shorter than one visible line, or a product that
 * wraps.  `pitch` is already a 32-bit field value, so the multiply is the only
 * place a wrap can be introduced. */
static inline int pc5_framebuffer_bytes(u32 width, u32 height, u32 pitch, u64 *bytes)
{
    if (width == 0 || height == 0 || pitch == 0)
        return 0;
    if (pitch < (u64)width * 4ull)
        return 0;
    return !pc5_mul_overflows((u64)pitch, height, bytes);
}

/* UEFI GOP pixel formats.  The protocol records the firmware's value, and only
 * the two direct-colour layouts are interpretable from the structure alone: a
 * bit-mask format needs per-channel masks the boot info does not carry, and
 * PixelBltOnly has no linear framebuffer at all. */
#define PC5_PIXEL_FORMAT_RGB 0u
#define PC5_PIXEL_FORMAT_BGR 1u
#define PC5_PIXEL_FORMAT_BITMASK 2u
#define PC5_PIXEL_FORMAT_BLT_ONLY 3u
#define PC5_PIXEL_FORMAT_MAX 3u

static inline int pc5_pixel_format_supported(u32 format)
{
    return format == PC5_PIXEL_FORMAT_RGB || format == PC5_PIXEL_FORMAT_BGR;
}

/* ------------------------------------------------------------- address width */
/* CPUID.80000008H reports the physical-address width in EAX[7:0].  A zero there
 * means the leaf was not queried or the CPU did not report a width; it is not a
 * licence to treat every address as valid.  The kernel therefore distinguishes
 * "reported and checked" from "not reported", and the helper below is what makes
 * that distinction testable. */
static inline int pc5_physical_width_reported(u32 bits)
{
    return bits >= 32 && bits <= 52;
}

static inline int pc5_address_fits_width(u64 value, u32 physical_bits)
{
    if (!pc5_physical_width_reported(physical_bits))
        return 0;
    /* A width of N bits addresses [0, 2^N): 2^N itself is the first address that
     * does not exist, so the comparison is strict. */
    return value < (1ull << physical_bits);
}

/* ------------------------------------------------------- descriptor overlaps */
/* 1 when [a_start, a_end) and [b_start, b_end) share at least one byte.  Both
 * ends must already be known not to wrap (pc5_descriptor_valid guarantees that).
 * Adjacent ranges (a_end == b_start) do not overlap. */
static inline int pc5_ranges_overlap(u64 a_start, u64 a_end, u64 b_start, u64 b_end)
{
    return a_start < b_end && b_start < a_end;
}

/* 1 when any two descriptors in the array describe the same physical bytes.  The
 * comparison is pairwise and order-independent, so an unsorted map is validated
 * exactly like a sorted one and an adjacent pair is accepted. */
static inline int pc5_map_overlaps(const struct pc5_memdesc64 *descriptors, u64 entries)
{
    for (u64 left = 0; left < entries; left++) {
        u64 left_start = descriptors[left].physical_start;
        u64 left_end = pc5_descriptor_end(&descriptors[left]);
        for (u64 right = left + 1; right < entries; right++) {
            if (pc5_ranges_overlap(left_start, left_end,
                                   descriptors[right].physical_start,
                                   pc5_descriptor_end(&descriptors[right])))
                return 1;
        }
    }
    return 0;
}

/* 1 when every byte of [start, start+size) is covered by descriptors of an
 * acceptable type.  Descriptors may arrive in any order and may be adjacent; the
 * loop walks the range forwards taking the furthest end at each step, so a gap of
 * one byte fails and overlapping coverage succeeds.  Every descriptor must have
 * passed pc5_descriptor_valid() first, so no bound here can wrap. */
static inline int pc5_map_covers(const struct pc5_map_view *map, u64 start, u64 size,
                                 u32 types)
{
    u64 end;
    if (size == 0 || pc5_add_overflows(start, size, &end))
        return 0;
    u64 cursor = start;
    for (u64 steps = 0; steps <= map->entries; steps++) {
        if (cursor >= end)
            return 1;
        u64 furthest = 0;
        int found = 0;
        for (u64 index = 0; index < map->entries; index++) {
            const struct pc5_memdesc64 *descriptor = &map->descriptors[index];
            if (!(types & PC5_MMAP_BIT(descriptor->type)))
                continue;
            if (descriptor->physical_start > cursor)
                continue;
            u64 candidate = pc5_descriptor_end(descriptor);
            if (candidate <= cursor)
                continue;
            if (!found || candidate > furthest) {
                furthest = candidate;
                found = 1;
            }
        }
        if (!found)
            return 0;
        cursor = furthest;
    }
    return 0;
}

/* Validate a descriptor array in place: 1 when every entry is internally
 * consistent, the array is not empty, and no two descriptors claim the same
 * physical bytes.  Overlap is rejected rather than resolved: a map that assigns
 * one address to two owners cannot be used to decide what is safe, and picking
 * whichever descriptor is convenient would hide the contradiction instead of
 * reporting it. */
static inline int pc5_map_validate(const struct pc5_memdesc64 *descriptors, u64 entries)
{
    if (!descriptors || entries == 0)
        return 0;
    for (u64 index = 0; index < entries; index++) {
        if (!pc5_descriptor_valid(&descriptors[index]))
            return 0;
    }
    return !pc5_map_overlaps(descriptors, entries);
}

#endif
