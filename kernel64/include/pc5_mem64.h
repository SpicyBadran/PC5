/* The 64-bit kernel's frame allocator: one bitmap over the handover memory map.
 *
 * Version 2 of the boot-information protocol maps exactly one region - the
 * identity span the loader built - so a frame is *usable by this kernel* only when
 * it is (a) `EfiConventionalMemory`, (b) entirely inside that span, and (c) not
 * claimed by something the loader or this kernel is already standing on.  All three
 * conditions come from firmware-supplied data, so all three are re-derived here
 * rather than trusted from the loader: the loader's checks decide what it handed
 * over, and this file decides what the kernel may write to afterwards.
 *
 * What this allocator deliberately does NOT do:
 *
 *   * it never allocates above the identity span, and it never *partially*
 *     allocates a descriptor: a conventional region whose end passes the span end
 *     contributes only its pages below the span, because the pages above it have
 *     no mapping and a write there faults;
 *   * it does not coalesce lazily, does not support `PAGE_SIZE < 4 KiB`, and has no
 *     per-owner accounting: a page is either free or the kernel's;
 *   * it keeps no heap metadata in the allocated pages, so a returned range is
 *     exactly what it claims and nothing is stored beside it;
 *   * it refuses to manage a map it cannot describe: if the map reaches past the
 *     bitmap, init fails instead of silently forgetting the frames above the cap.
 *     A pool that ignores the top of memory is worse than no pool, because the
 *     kernel would then believe those frames were unavailable when they merely
 *     were not measured.
 *
 * Every entry point is pure bookkeeping over caller-supplied buffers: no I/O, no
 * global state, nothing that writes to the frames it hands out.  That is what makes
 * the same object testable twice - as a host library against synthetic maps, and
 * as the code the kernel really runs - without either test re-implementing the rule
 * it is checking.
 */
#ifndef PC5_MEM64_H
#define PC5_MEM64_H

#include "pc5_map.h"

#define PC5_MEM64_PAGE 4096ull
#define PC5_MEM64_PAGE_MASK 0xFFFull

/* Frames a pool can describe with its bitmap - derived from the protocol's own
 * limit rather than picked, so that no map which the boot information is allowed to
 * state can outgrow the pool.  4 GiB of identity span is 1048576 frames, which is
 * 128 KiB of bitmap; that much `.bss` is what makes the cap a real invariant instead
 * of a fixture-dependent refusal, because `.bss` travels with the image and the
 * kernel-image claim therefore covers the bitmap's own bytes.
 *
 * `bitmap_bytes` is still a parameter, and a caller that hands over less than
 * `PC5_MEM64_MAX_FRAMES / 8` bytes gets `map-above-bitmap-cap`: refusing to manage a
 * map partly is the point of the check, not an artefact of the size. */
#define PC5_MEM64_MAX_FRAMES (PC5_IDENTITY_LIMIT / PC5_MEM64_PAGE)

struct pc5_mem64_stats {
    u64 frames;            /* frames the bitmap describes */
    u64 conventional;      /* frames the map calls EfiConventionalMemory, in the span */
    /* The same total with no clipping at all: what the map claims.  The kernel
     * counts it once while validating the descriptors and once here, and the
     * executed gate compares the two, so a pool that quietly loses part of the map
     * is a check failure rather than a smaller heap. */
    u64 conventional_total;
    u64 claimed;           /* of those, frames the loader or kernel is standing on */
    u64 free;              /* frames on offer right now */
    u64 largest_at_init;   /* longest free run when init finished; not tracked after */
    u64 requests;          /* alloc calls, granted or not */
    u64 refusals;          /* alloc calls that returned 0 */
    u64 double_frees;      /* free calls refused because a frame was already free */
    u64 coverage_refusals; /* alloc refused because the map no longer covers the run */
};

struct pc5_mem64 {
    struct pc5_map_view map;
    u8 *bitmap;                    /* one bit per frame; set means "not free" */
    u64 bitmap_frames;             /* frames the caller's buffer can hold */
    struct pc5_mem64_stats stats;
    u64 span;                      /* the identity span: nothing above it is mappable */
    /* The ranges that must never be handed out, kept so every later allocation can
     * be checked against them and so a caller can ask the question too. */
    u64 kernel_base, kernel_size;
    u64 stack_base, stack_size;
    u64 pagetable_base, pagetable_bytes;
    u64 info_base, info_size;
    u64 memmap_base, memmap_bytes;
    u64 loader_base, loader_size;
    int ready;                     /* 1 when the pool may be used */
    const char *reason;            /* why not, when it is not */
};

/* Build the pool from a boot-info structure the caller has already validated.
 * `info_base` is where that structure physically lives, which the structure
 * itself cannot say.  Returns 1 when the pool is usable, 0 with `pool->reason`
 * set; a refusal leaves the pool marked not-ready, so an ignored return value
 * still cannot yield an allocation. */
int pc5_mem64_init(struct pc5_mem64 *pool, const struct pc5_boot64 *info, u64 info_base,
                   u8 *bitmap, u64 bitmap_bytes);

/* Grant a run of `pages` free frames, writing their physical base to `*base`.
 * Returns 1 when granted and 0 when refused (pool not ready, zero pages, a request
 * larger than the pool, or a run the map no longer covers); a refusal leaves
 * `*base` zero and the pool unchanged.
 *
 * Success and address are reported separately on purpose.  The first free frame of
 * a machine can be frame 0, and an allocator that returns 0 for "nothing" then
 * cannot return it for "the first page" - a bug the executed boot self-test caught
 * the first time this ran.  The zero page is also claimed by the pool (see
 * `pool_claim`), so an address field left zeroed by a caller can never be mistaken
 * for a granted allocation. */
int pc5_mem64_alloc(struct pc5_mem64 *pool, u64 pages, u64 *base);

/* Hand `pages` frames at `base` back.  0 when refused: unaligned, outside the
 * described window, larger than the pool, already free, or a range that overlaps
 * something the loader or the kernel owns - a caller cannot return memory the pool
 * never granted, which is what would otherwise let `free(0, 1)` put the zero page
 * or the kernel image back into circulation.  A refusal changes nothing, so a
 * double free cannot make the accounting go negative. */
int pc5_mem64_free(struct pc5_mem64 *pool, u64 base, u64 pages);

/* 1 when [base, base + pages*4096) is inside the window, covered by conventional
 * memory, entirely marked used, and outside every claimed range. */
int pc5_mem64_validate(const struct pc5_mem64 *pool, u64 base, u64 pages);

/* 1 when [base, base + pages*4096) overlaps a range the loader or this kernel owns,
 * or the zero page the pool always reserves.  An extent that cannot be computed
 * (zero pages, a multiply or add that wraps) counts as overlapping: an unanswerable
 * question is not a licence to hand the range out. */
int pc5_mem64_overlaps_claimed(const struct pc5_mem64 *pool, u64 base, u64 pages);

/* The physical extent of every claim the pool honours, for a caller that wants to
 * check a range against the map and the claims itself.  Index 6 is the zero page. */
u64 pc5_mem64_claim_start(const struct pc5_mem64 *pool, u64 index);
u64 pc5_mem64_claim_size(const struct pc5_mem64 *pool, u64 index);
#define PC5_MEM64_CLAIM_COUNT 7u

/* 1 when [start, end) is covered by a descriptor of an acceptable type *and*
 * lies inside the identity span.  The span test is what the map helpers do not do:
 * coverage says firmware called the bytes RAM, the span says this kernel can
 * touch them without building a mapping. */
int pc5_mem64_range_usable(const struct pc5_mem64 *pool, u64 start, u64 end);

static inline void pc5_mem64_snapshot(const struct pc5_mem64 *pool,
                                     struct pc5_mem64_stats *out)
{
    *out = pool->stats;
}

/* Read-only views, exported so a caller (and the host test) can ask what the pool
 * decided without mirroring this structure's layout. */
const char *pc5_mem64_reason(const struct pc5_mem64 *pool);
int pc5_mem64_ready(const struct pc5_mem64 *pool);
const struct pc5_mem64_stats *pc5_mem64_stats(const struct pc5_mem64 *pool);

#endif
