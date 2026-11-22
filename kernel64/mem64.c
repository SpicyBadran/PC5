/* Frame allocation over the handover memory map.  See pc5_mem64.h for the policy
 * this file implements and for what it deliberately refuses to do.
 *
 * No libc, no globals, no writes into the frames it hands out: the same object code
 * is what the kernel runs and what tests/test_mem64.py drives from the host, so a
 * host test cannot drift from the boot path.
 */
#include "pc5_mem64.h"

/* Pinned because the host test reads these through ctypes: an offset that moves
 * silently turns a green suite into a test of the wrong bytes. */
_Static_assert(__builtin_offsetof(struct pc5_mem64, stats) == 32, "mem64 pool abi");
_Static_assert(sizeof(struct pc5_mem64_stats) == 80, "mem64 stats abi");

#define PAGESHIFT 12ull

/* The ranges a running kernel is standing on.  If the allocator handed one of
 * these out, the write would land in the image, the stack, the page tables, the
 * boot info, the map itself, or the loader's image - and the fault, if any, would
 * arrive long afterwards and somewhere else. */
#define CLAIM_COUNT PC5_MEM64_CLAIM_COUNT
#define CLAIM_ZERO_PAGE (CLAIM_COUNT - 1)

/* The frames a running kernel is standing on, plus the zero page.  The zero page is
 * not a claim the loader told us about; it is a policy reservation of this
 * allocator, so that `0` in an address field always means "not granted" and never
 * "the first page of RAM", which is the confusion the sentinelled API had. */

static void bit_set(u8 *bitmap, u64 frame)
{
    bitmap[frame >> 3] = (u8)(bitmap[frame >> 3] | (1u << (frame & 7ull)));
}

static void bit_clear(u8 *bitmap, u64 frame)
{
    bitmap[frame >> 3] = (u8)(bitmap[frame >> 3] & ~(1u << (frame & 7ull)));
}

static int bit_get(const u8 *bitmap, u64 frame)
{
    return (bitmap[frame >> 3] >> (frame & 7ull)) & 1u;
}

u64 pc5_mem64_claim_start(const struct pc5_mem64 *pool, u64 index)
{
    switch (index) {
    case 0: return pool->kernel_base;
    case 1: return pool->stack_base;
    case 2: return pool->pagetable_base;
    case 3: return pool->info_base;
    case 4: return pool->memmap_base;
    case 5: return pool->loader_base;
    default: return 0;                       /* the zero page */
    }
}

u64 pc5_mem64_claim_size(const struct pc5_mem64 *pool, u64 index)
{
    switch (index) {
    case 0: return pool->kernel_size;
    case 1: return pool->stack_size;
    case 2: return pool->pagetable_bytes;
    case 3: return pool->info_size;
    case 4: return pool->memmap_bytes;
    case 5: return pool->loader_size;
    default: return PC5_MEM64_PAGE;           /* one page, always */
    }
}

int pc5_mem64_overlaps_claimed(const struct pc5_mem64 *pool, u64 base, u64 pages)
{
    u64 end;
    if (pages == 0 || pc5_mul_overflows(pages, PC5_MEM64_PAGE, &end))
        return 1;                       /* an unanswerable extent is not a licence */
    if (pc5_add_overflows(base, end, &end))
        return 1;
    for (u64 index = 0; index < CLAIM_COUNT; index++) {
        u64 start = pc5_mem64_claim_start(pool, index);
        u64 size = pc5_mem64_claim_size(pool, index);
        u64 stop;
        if (size == 0)
            continue;
        if (pc5_add_overflows(start, size, &stop))
            stop = ~0ull;                /* a claim that wraps covers everything */
        if (pc5_ranges_overlap(base, end, start, stop))
            return 1;
    }
    return 0;
}

int pc5_mem64_range_usable(const struct pc5_mem64 *pool, u64 start, u64 end)
{
    if (end <= start)
        return 0;
    if (start >= pool->span || end > pool->span)
        return 0;
    return pc5_map_covers(&pool->map, start, end - start,
                          PC5_MMAP_BIT(PC5_MMAP_CONVENTIONAL));
}

static void pool_reset(struct pc5_mem64 *pool, const struct pc5_boot64 *info,
                       u8 *bitmap, u64 bitmap_frames)
{
    pool->bitmap = bitmap;
    pool->bitmap_frames = bitmap_frames;
    pool->map.descriptors = (const struct pc5_memdesc64 *)(unsigned long)info->memmap_addr;
    pool->map.entries = 0;
    pool->span = info->identity_span;
    pool->kernel_base = info->kernel_base;
    pool->kernel_size = info->kernel_size;
    pool->stack_size = info->stack_size;
    pool->stack_base = info->stack_top >= info->stack_size ? info->stack_top - info->stack_size : 0;
    pool->pagetable_base = info->pagetable_addr;
    pool->pagetable_bytes = info->pagetable_pages * PC5_MEM64_PAGE;
    pool->info_size = info->size;
    pool->loader_base = info->loader_image_addr;
    pool->loader_size = info->loader_image_size;
    pool->memmap_base = info->memmap_addr;
    pool->memmap_bytes = info->memmap_bytes;
    pool->stats.frames = 0;
    pool->stats.conventional = 0;
    pool->stats.conventional_total = 0;
    pool->stats.claimed = 0;
    pool->stats.free = 0;
    pool->stats.largest_at_init = 0;
    pool->stats.requests = 0;
    pool->stats.refusals = 0;
    pool->stats.double_frees = 0;
    pool->stats.coverage_refusals = 0;
    pool->ready = 0;
    pool->reason = "uninitialised";
}

/* The first frame past the last usable byte of conventional memory inside the
 * span.  Clipping at the span is the whole point: a descriptor that runs past the
 * identity map contributes the pages it has below the map and no more. */
static int map_extent(const struct pc5_mem64 *pool, u64 *frames, u64 *total, u64 *malformed)
{
    u64 highest = 0;
    *total = 0;
    *malformed = 0;
    for (u64 index = 0; index < pool->map.entries; index++) {
        const struct pc5_memdesc64 *descriptor = &pool->map.descriptors[index];
        if (!pc5_descriptor_valid(descriptor)) {
            *malformed = index + 1;
            return 0;
        }
        if (descriptor->physical_start & PC5_MEM64_PAGE_MASK) {
            *malformed = index + 1;
            return 0;
        }
        if (descriptor->type != PC5_MMAP_CONVENTIONAL)
            continue;
        /* The unclipped total, saturated: this is what the map itself states, and it
         * is the number the kernel's other count is compared against. */
        if (descriptor->page_count > ~0ull - *total)
            *total = ~0ull;
        else
            *total += descriptor->page_count;
        u64 end = pc5_descriptor_end(descriptor);
        if (end > pool->span)
            end = pool->span;               /* clipped, not rejected: the map is real */
        if (end <= descriptor->physical_start)
            continue;                       /* entirely above the span */
        if (end > highest)
            highest = end;
    }
    if (highest == 0)
        return 0;
    *frames = (highest + PC5_MEM64_PAGE - 1) >> PAGESHIFT;
    return 1;
}

int pc5_mem64_init(struct pc5_mem64 *pool, const struct pc5_boot64 *info, u64 info_base,
                   u8 *bitmap, u64 bitmap_bytes)
{
    if (!pool)
        return 0;
    if (!info || !bitmap || bitmap_bytes == 0 || bitmap_bytes > (~0ull / 8)) {
        pool->bitmap = 0;
        pool->bitmap_frames = 0;
        pool->span = 0;
        pool->map.descriptors = 0;
        pool->map.entries = 0;
        pool->ready = 0;
        pool->reason = !info ? "info-null" : (!bitmap ? "bitmap-null" : "bitmap-size");
        return 0;
    }
    u64 capacity = bitmap_bytes * 8ull;   /* one byte of bitmap describes eight frames */
    if (capacity > PC5_MEM64_MAX_FRAMES)
        capacity = PC5_MEM64_MAX_FRAMES;
    pool_reset(pool, info, bitmap, capacity);
    pool->info_base = info_base;

    /* Re-derived rather than inherited from the caller's validation, because the
     * host harness calls this function directly and a future second caller may
     * not have checked anything. */
    if (info->memmap_desc_size != (u32)sizeof(struct pc5_memdesc64)) {
        pool->reason = "map-descsize";
        return 0;
    }
    if (info->memmap_bytes == 0 || info->memmap_bytes % sizeof(struct pc5_memdesc64)) {
        pool->reason = "map-length";
        return 0;
    }
    u64 entries = info->memmap_bytes / (u64)sizeof(struct pc5_memdesc64);
    if (entries == 0 || entries > (1ull << 20)) {
        pool->reason = "map-entries";
        return 0;
    }
    if (!pc5_range_within(info->memmap_addr, info->memmap_bytes, PC5_IDENTITY_LIMIT)) {
        pool->reason = "map-range";
        return 0;
    }
    if (info->identity_span == 0 || info->identity_span > PC5_IDENTITY_LIMIT) {
        pool->reason = "span-unusable";
        return 0;
    }
    pool->map.entries = entries;
    if (!pc5_map_validate(pool->map.descriptors, entries)) {
        pool->reason = "map-inconsistent";
        return 0;
    }

    u64 frames = 0, total = 0, malformed = 0;
    if (!map_extent(pool, &frames, &total, &malformed)) {
        pool->reason = malformed ? "map-descriptor" : "map-no-conventional";
        return 0;
    }
    pool->stats.conventional_total = total;
    if (frames > capacity) {
        /* Refused, not truncated: a pool that quietly forgot the frames above its
         * bitmap would report them as unavailable, and the kernel would believe
         * the machine had less memory than the firmware said it had. */
        pool->reason = "map-above-bitmap-cap";
        pool->stats.frames = frames;
        return 0;
    }
    pool->stats.frames = frames;

    /* Everything starts unavailable, and only what the map proves is free is
     * opened up.  Filling the other way - free, then reserve - would hand out any
     * region the map failed to mention, which is exactly the class of bug a
     * fail-closed allocator exists to avoid. */
    for (u64 index = 0; index < bitmap_bytes; index++)
        bitmap[index] = 0xFF;

    for (u64 index = 0; index < entries; index++) {
        const struct pc5_memdesc64 *descriptor = &pool->map.descriptors[index];
        if (descriptor->type != PC5_MMAP_CONVENTIONAL)
            continue;
        u64 start = descriptor->physical_start >> PAGESHIFT;
        u64 end = pc5_descriptor_end(descriptor);
        if (end > pool->span)
            end = pool->span;
        if (end > frames << PAGESHIFT)
            end = frames << PAGESHIFT;
        for (u64 frame = start; frame < (end >> PAGESHIFT); frame++) {
            if (bit_get(bitmap, frame)) {
                bit_clear(bitmap, frame);
                pool->stats.conventional++;
                pool->stats.free++;
            }
        }
    }

    u64 largest = 0, run = 0;
    for (u64 frame = 0; frame <= frames; frame++) {
        if (frame < frames && !bit_get(bitmap, frame)) {
            run++;
            if (run > largest)
                largest = run;
            continue;
        }
        run = 0;
    }

    for (u64 index = 0; index < CLAIM_COUNT; index++) {
        u64 start = pc5_mem64_claim_start(pool, index);
        u64 size = pc5_mem64_claim_size(pool, index);
        u64 stop;
        if (size == 0 || pc5_add_overflows(start, size, &stop))
            continue;
        if (start >= pool->span)
            continue;
        if (stop > pool->span)
            stop = pool->span;
        for (u64 frame = start >> PAGESHIFT; frame < (stop >> PAGESHIFT) && frame < frames;
             frame++) {
            if (!bit_get(bitmap, frame)) {
                bit_set(bitmap, frame);
                pool->stats.claimed++;
                pool->stats.free--;
            }
        }
    }
    /* The zero-page claim is not part of the map's conventional set on every
     * machine, so `conventional` counts what the map offered and `claimed` counts
     * what was taken from it - including frame 0 when the map offered that too.
     * The invariant below holds either way. */
    pool->stats.largest_at_init = largest;

    if (pool->stats.free == 0) {
        pool->reason = "no-free-frames";
        return 0;
    }
    /* The construction above has exactly two outcomes for a conventional frame: it
     * is free, or it is claimed.  Anything else means the bitmap and the counts
     * disagree, and a pool that disagrees with itself must not allocate.  This is
     * checked before `ready` is set, so the contradiction cannot be used. */
    if (pool->stats.free + pool->stats.claimed != pool->stats.conventional) {
        pool->ready = 0;
        pool->stats.free = 0;
        pool->reason = "pool-inconsistent";
        return 0;
    }
    pool->ready = 1;
    pool->reason = "ok";
    return 1;
}

int pc5_mem64_alloc(struct pc5_mem64 *pool, u64 pages, u64 *base)
{
    pool->stats.requests++;
    if (base)
        *base = 0;
    if (!pool->ready || !base || pages == 0 || pages > pool->stats.frames) {
        pool->stats.refusals++;
        return 0;
    }
    u64 run = 0;
    u64 frames = pool->stats.frames;
    for (u64 frame = 0; frame <= frames; frame++) {
        if (frame < frames && !bit_get(pool->bitmap, frame)) {
            run++;
            if (run == pages) {
                u64 first = frame + 1 - pages;
                u64 start = first << PAGESHIFT;
                /* Re-derive both properties the scan assumed: the map must still
                 * cover the run, and it must not touch anything claimed.  Neither
                 * is expensive, and either one being wrong is a fault later. */
                u64 bytes = 0, stop = 0;
                if (pc5_mul_overflows(pages, PC5_MEM64_PAGE, &bytes) ||
                    pc5_add_overflows(start, bytes, &stop) ||
                    !pc5_mem64_range_usable(pool, start, stop) ||
                    pc5_mem64_overlaps_claimed(pool, start, pages)) {
                    pool->stats.coverage_refusals++;
                    pool->stats.refusals++;
                    return 0;
                }
                for (u64 taken = first; taken < first + pages; taken++)
                    bit_set(pool->bitmap, taken);
                pool->stats.free -= pages;
                *base = start;
                return 1;
            }
            continue;
        }
        run = 0;
    }
    pool->stats.refusals++;
    return 0;
}

int pc5_mem64_free(struct pc5_mem64 *pool, u64 base, u64 pages)
{
    if (!pool->ready || pages == 0 || base & PC5_MEM64_PAGE_MASK)
        return 0;
    if (base >= pool->stats.frames << PAGESHIFT)
        return 0;
    u64 first = base >> PAGESHIFT;
    if (pages > pool->stats.frames - first)
        return 0;
    /* A range the pool never granted cannot be handed back.  Without this, freeing
     * the zero page, the kernel image, the stack or the page tables would mark them
     * free, and the next allocation would hand the kernel a page it is standing on -
     * the one failure mode an allocator must not have. */
    if (pc5_mem64_overlaps_claimed(pool, base, pages))
        return 0;
    for (u64 frame = first; frame < first + pages; frame++) {
        if (!bit_get(pool->bitmap, frame)) {
            pool->stats.double_frees++;
            return 0;                 /* nothing changed: the first free wins */
        }
    }
    for (u64 frame = first; frame < first + pages; frame++)
        bit_clear(pool->bitmap, frame);
    pool->stats.free += pages;
    return 1;
}

int pc5_mem64_validate(const struct pc5_mem64 *pool, u64 base, u64 pages)
{
    if (pages == 0 || base & PC5_MEM64_PAGE_MASK)
        return 0;
    if (base >= pool->stats.frames << PAGESHIFT)
        return 0;
    u64 first = base >> PAGESHIFT;
    if (pages > pool->stats.frames - first)
        return 0;
    for (u64 frame = first; frame < first + pages; frame++) {
        if (!bit_get(pool->bitmap, frame))
            return 0;
    }
    if (pc5_mem64_overlaps_claimed(pool, base, pages))
        return 0;
    return pc5_mem64_range_usable(pool, base, base + (pages * PC5_MEM64_PAGE));
}

const char *pc5_mem64_reason(const struct pc5_mem64 *pool)
{
    return pool->reason ? pool->reason : "unreported";
}

int pc5_mem64_ready(const struct pc5_mem64 *pool)
{
    return pool->ready;
}

const struct pc5_mem64_stats *pc5_mem64_stats(const struct pc5_mem64 *pool)
{
    return &pool->stats;
}
