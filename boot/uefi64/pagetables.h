/* PC5-owned 4-level page tables for the 64-bit boot path.
 *
 * The kernel must not run on firmware's page tables: firmware may unmap them,
 * may place them above 4 GiB, and does not tell the kernel which pages are
 * mapped.  The loader therefore builds a map it owns and can describe:
 *
 *   1 GiB of address space needs 1 PDPT entry and 512 PD entries;
 *   4 GiB needs 4 PDPT entries and 2048 PD entries, i.e. 4 PD pages.
 *
 * So the whole map below 4 GiB is 6 pages: one PML4, one PDPT, four PDs.  It
 * uses 2 MiB pages, which every x86-64 CPU that can enter long mode supports,
 * so the loader does not depend on 1 GiB pages (CPUID reports whether the CPU
 * has them, and the kernel prints that, but the boot map does not need them).
 *
 * Bounds policy — every entry of this API is defined for *any* 64-bit input,
 * because every one of them can be handed a firmware address:
 *
 *   - a base address that is null or not 4 KiB aligned is rejected;
 *   - a range is clipped to the identity span; entries outside the span are
 *     never read and never written, so no call can touch memory outside the
 *     PC5_PT_PAGES block or the 4 GiB of entries inside it;
 *   - a range whose end is not representable (start + size overflows) is
 *     refused by pc5_pt_range_supported() and by the callers that need the
 *     whole range;
 *   - pc5_pt_mark_uncached() reports how many entries it actually changed, so
 *     "0" means "nothing in this range is mapped" rather than "success".
 *
 * A caller that needs the *whole* range to end up uncached (the framebuffer, for
 * instance) must check pc5_pt_range_supported() first: clipping is safe, but it
 * is not the same thing as mapping.
 */
#ifndef PC5_PAGETABLES64_H
#define PC5_PAGETABLES64_H

typedef unsigned long long u64;

#define PC5_PT_PAGE 4096u
#define PC5_PT_PAGES 6u           /* 1 PML4 + 1 PDPT + 4 PD */
#define PC5_PT_SPAN (4ull << 30)  /* identity map below 4 GiB */
#define PC5_PT_2MIB (2ull << 20)
#define PC5_PT_ENTRIES_PER_PAGE 512u
#define PC5_PT_PD_PAGES (PC5_PT_PAGES - 2u)
#define PC5_PT_MAX_INDEX (PC5_PT_SPAN / PC5_PT_2MIB - 1u)

/* Present | writable | page-size (2 MiB leaf). */
#define PC5_PT_FLAGS (1ull | 2ull | (1ull << 7))
/* PWT | PCD: strong uncached, which is what a framebuffer needs. */
#define PC5_PT_UC ((1ull << 3) | (1ull << 4))

/* Build the identity map.  `pt` must point at PC5_PT_PAGES contiguous, zeroed,
 * 4 KiB-aligned pages.  `span` is rounded down to a whole number of 2 MiB pages.
 * Returns the number of 2 MiB pages mapped, or 0 when an argument is invalid:
 * null or unaligned base, zero span, or a span the table block cannot hold. */
unsigned long pc5_pt_build_identity(u64 pt, u64 span);

/* 1 when [start, start + size) is non-empty, free of 64-bit overflow, and lies
 * entirely inside the identity span — i.e. when every page of it is mapped and
 * can be marked.  0 otherwise, including every overflow case. */
int pc5_pt_range_supported(u64 start, u64 size);

/* Make every 2 MiB page that overlaps [start, end) uncached, clipped to the
 * span.  Returns the number of entries changed; 0 for a null or unaligned base,
 * an empty or inverted range, or a range with nothing mapped inside the span. */
unsigned long pc5_pt_mark_uncached(u64 pt, u64 start, u64 end);

/* Read back a mapping, for validation and for tests.  Returns 1 when the
 * address is mapped and fills in the leaf entry, 0 otherwise (including a null
 * or unaligned base, or an address outside the span). */
int pc5_pt_lookup(u64 pt, u64 address, u64 *entry);

/* Physical address of the PML4 / PDPT / PD array inside the block. */
static inline u64 pc5_pt_pml4(u64 pt) { return pt; }
static inline u64 pc5_pt_pdpt(u64 pt) { return pt + PC5_PT_PAGE; }
static inline u64 pc5_pt_pd_base(u64 pt) { return pt + 2 * PC5_PT_PAGE; }

#endif
