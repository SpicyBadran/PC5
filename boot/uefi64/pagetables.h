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
 */
#ifndef PC5_PAGETABLES64_H
#define PC5_PAGETABLES64_H

typedef unsigned long long u64;

#define PC5_PT_PAGE 4096u
#define PC5_PT_PAGES 6u           /* 1 PML4 + 1 PDPT + 4 PD */
#define PC5_PT_SPAN (4ull << 30)  /* identity map below 4 GiB */
#define PC5_PT_2MIB (2ull << 20)

/* Present | writable | page-size (2 MiB leaf). */
#define PC5_PT_FLAGS (1ull | 2ull | (1ull << 7))
/* PWT | PCD: strong uncached, which is what a framebuffer needs. */
#define PC5_PT_UC (1ull << 3) | (1ull << 4)

/* Build the identity map.  `pt` must point at PC5_PT_PAGES contiguous, zeroed,
 * 4 KiB-aligned pages.  Returns the number of 2 MiB pages mapped (0 on a bad
 * argument), so a caller can refuse to switch CR3 on a partial map. */
unsigned long pc5_pt_build_identity(u64 pt, u64 span);

/* Make every 2 MiB page that overlaps [start, end) uncached.  Returns the
 * number of entries changed; entries that are not mapped are skipped. */
unsigned long pc5_pt_mark_uncached(u64 pt, u64 start, u64 end);

/* Read back a mapping, for validation and for tests.  Returns 1 when the
 * address is mapped, filling in the leaf entry, 0 otherwise. */
int pc5_pt_lookup(u64 pt, u64 address, u64 *entry);

/* Physical address of the PML4 / PDPT / PD array inside the block. */
static inline u64 pc5_pt_pml4(u64 pt) { return pt; }
static inline u64 pc5_pt_pdpt(u64 pt) { return pt + PC5_PT_PAGE; }
static inline u64 pc5_pt_pd_base(u64 pt) { return pt + 2 * PC5_PT_PAGE; }

#endif
