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
 * consistent and the array is not empty. */
static inline int pc5_map_validate(const struct pc5_memdesc64 *descriptors, u64 entries)
{
    if (!descriptors || entries == 0)
        return 0;
    for (u64 index = 0; index < entries; index++) {
        if (!pc5_descriptor_valid(&descriptors[index]))
            return 0;
    }
    return 1;
}

#endif
