# PC5 — Human review package

> **The word "Independent" has been removed from this title.** This package was assembled by
> the implementer. It is a reading guide for a human reviewer, not an independent review, and
> nothing in it constitutes approval.


| | |
|---|---|
| **Tested revision** | `141f91f0ca192ca72972ca9afdbee8c86e6896c5`, with later sections describing `0a7eaa0` |
| **Evidence type** | Implementer-assembled reading guide. Gate results quoted from recorded evidence. |
| **Historical?** | Partly — the F4 correction added later refers to `0a7eaa0`. |
| **Author** | The PC5 implementer. **Not an independent review, and not approval.** |

**Subject:** combined landing of PR #26 (`170e611`) + PR #27 (`141f91f`) into `main` under Strategy D
**Repository:** `SpicyBadran/pc5`
**Prepared:** 2026-11-16
**Prepared by:** the agent that authored the change under review
**Status of this document:** a *review request*, not an approval. Nothing has been merged, approved, or commented on GitHub.

> **Read this first.** The author of the change and the author of the two prior audit documents are both AI agents. Every conclusion below is offered as a *claim to be independently checked*, not as evidence in itself. §8 lists the specific things you must verify by reading the code yourself. §9 is yours to fill in. If you cannot verify something, say so rather than approving on the strength of this document.

---

## 1. Project purpose and scope

PC5 is a from-scratch, freestanding bootable operating-system foundation targeting PS5-class x86-64 hardware, built as a BIOS/MBR legacy-boot image plus an experimental UEFI 64-bit path. It is not a Sony product and contains no Sony code, firmware, or keys.

**What this combined change is:** the first 64-bit long-mode boot path (UEFI application → 4-level paging → flat 64-bit kernel handover), plus the corrections that two rounds of review found in it.

**What it is not:** it does not boot a PS5, does not run any Sony software, and has never executed on physical hardware. It has been exercised only under an emulated stub firmware. Do not let any phrasing in this package suggest otherwise.

**Scope boundary that matters for risk:** the 64-bit path is deliberately **excluded from the release artifact**. `Makefile:107-111` states it is "not part of `all`, `iso` or the release artifact", and `release-assets` stages only `pc5.iso` and its metadata. The BIOS ISO is byte-identical before and after this change. So the practical blast radius of any defect here is the experimental path only — but that is a reason to review it carefully, not a reason to skip review.

---

## 2. Exact PR and commit references

Verified live at the start and the end of preparation; **no state change detected**.

| Ref | SHA | Notes |
|---|---|---|
| `main` | `8aab6d3864bb2e867a70bba28e7bfb52f43d7e35` | base |
| PR #26 head `arena/af939eb1-pc5` | `170e611dcdfa3fe12e9abe58c2e60ee323aa461c` | base = `main` |
| PR #27 head `arena/17f0c4f8-pc5` | `141f91f0ca192ca72972ca9afdbee8c86e6896c5` | base = PR #26 head |

| Property | Value |
|---|---|
| Ancestry | strictly linear: `main` → `170e611` → `141f91f` |
| Commit counts | `main..26` = 15, `26..27` = 14, `main..27` = **29** |
| Merge base (`main`, PR #27) | `8aab6d386` (= `main`; no divergence) |
| Both PRs | OPEN, not draft, **not merged** (`mergedAt: null`, `mergeCommit: null`) |
| Mergeability | PR #26 `MERGEABLE`/`CLEAN`, PR #27 `MERGEABLE`/`CLEAN` |
| Author of both | `SpicyBadran` (Adham Badran) |
| Reviews / comments | **0 / 0 on both**; `reviewDecision: ""`; 0 review requests |
| Same-repo branches | yes (`cross_repo = false`; not forks) |

**Fixes by commit:**

| Commit | Role |
|---|---|
| `ed9c6a3` | **D1 fix** — four PDPT entries so all 4 GiB are reachable |
| `ec16c65` | **D2 fix** — satisfiable `AllocateMaxAddress` bound + conforming stub |
| `a9b383d` | documentation: *supersedes* the earlier "4 GiB map" verdict rather than rewriting it |
| `9d4a79d` | evidence: re-records every gate at `a9b383d` on a clean tree |
| `141f91f` | **L1 closed** — page-table tests now cover short spans, and the vacuous loop is replaced |

None of these four appear in `main..PR26` (each has count 0 there). **The remediation exists only in PR #27's 14 commits.**

**One content item you should consciously consent to.** The diff adds one file that is not code: `AUDIT_PR26_PR27_2026-11-16.md` (601 lines, added by `a9b383d`). If you do not want it on `main`, that is a legitimate review objection and it should be raised *before* merge, not after.

What that file actually is, stated plainly, because calling it "an analysis document" understates it:

- It is a **historical, pre-remediation audit.** It was performed at `d0be617` and its headline verdict is **REQUEST CHANGES on both PRs** — *"NEITHER is mergeable while the 4 GiB identity map is a 1 GiB map."* The header says so and has not been edited.
- It records **two critical findings** (D1, the 1 GiB identity map; D2, the zero `AllocateMaxAddress` bound), both real at the SHA audited, both since fixed (`de9c6a3`, `ec16c65`).
- Its **§11 remediation section supersedes the original technical status** for exactly the findings it names — it is explicit about which, and leaves R2/R4 as NOT TESTED.
- It **predated the L1 closure** in `141f91f` when first written. §12, appended in the same pass as this correction, resolves that with a dated forward pointer rather than by rewriting §1–§11.

So the document distinguishes historical from current findings itself: §1–§10 are the record of what was found at `d0be617`, §11–§12 are the remediation status. It will land describing a defect state that no longer exists at the head it lands on — that is the point of an append-only audit, and it is why §12 exists.

---

## 3. Combined change summary

What would land on `main` is `main..141f91f`:

```
83 files changed, 8197 insertions(+), 166 deletions(-)
0 deleted files · 45 added · 38 modified
```

**PR #26 (15 commits) introduces:** the experimental 64-bit boot path — `boot/uefi64/*` (entry stub, page tables, loader), `kernel64/*` (flat 64-bit kernel, CPUID, map helpers), `tests/*64*` (three independent test levels), the brand assets, roadmap/version unification, and the gate records.

**PR #27 (14 commits) corrects and adds:** the two defects below, the conforming stub firmware, the loader's placement and handoff validation, the framebuffer geometry validation, CPUID extended-leaf gating, brand/mission/document corrections, and the superseding audit record.

**Why PR #26 must not land independently:** at `170e611` both defects are present, and — this is the part that matters — **the test suite is green there** (`Ran 612 tests`, `OK`). I verified that the defective and the fixed `pagetables.c` *both* pass PR #26's own 19 D1 tests. The D2 defect is worse: at `170e611` the stub firmware itself ignores the allocation type, so the suite agrees with the bug. A green suite at `170e611` is therefore not evidence of correctness.

**Why Strategy D preserves the audited tree:** merging PR #27 into its base yields a tree identical to the audited head. Verified locally with `git merge-tree`:

| Tree | SHA |
|---|---|
| PR #27 head `141f91f` (audited) | `5b452e261743d7b4e2d447233dc5de49546f363e` |
| `merge(PR26 head, PR27 head)` | `5b452e261743d7b4e2d447233dc5de49546f363e` |
| `main` today | `6d76d3f7b73d22aea1ba19a37885f19b988ef0f1` |

So after step 1, PR #26's content is **byte-for-byte the tree that was audited**. No content is re-derived, re-generated, or re-reviewed between audit and merge.

**Most important files for review** (in priority order):

| File | Why it matters | Lines |
|---|---|---|
| `boot/uefi64/pagetables.c` | D1 lives here; the whole paging builder, lookup, uncached marking | 130 |
| `boot/uefi64/pagetables.h` | constants, `PC5_PT_TABLE_FLAGS` vs leaf flags | 82 |
| `boot/uefi64/uefi64_main.c` | D2 lives here; `alloc_below_4g` + all 6 call sites, handover construction | 686 |
| `kernel64/include/pc5_map.h` | kernel-side span/width validation | 230 |
| `kernel64/main.c` | boot-info validation, environment report, `PC5-64-DONE` | 459 |
| `tests/uefi_stub_firmware.py` | the emulated firmware — judge how faithful the *model* is | 148/+21 |
| `tests/test_pagetables64.py` | D1 tests; L1 vacuous loop fixed, short-span coverage added | 790 |
| `tests/test_boot64_stub.py` | D2 A/B tests | 429 |
| `boot/uefi64/bootx64.S` | CR3 write + readback before `jmpq` | 90 |

---

## 4. D1 — the 4 GiB identity map

### The defect

A 4-level x86-64 walk is `PML4 → PDPT → PD → PT`, and a 2 MiB page stops at the PD. One PDPT entry is consumed by a **page-directory pointer**, and a single page directory holds 512 × 2 MiB = exactly **1 GiB**. So covering 4 GiB requires **four PDPT entries**, each pointing at a *different* PD page.

The original `pc5_pt_build_identity` (`170e611`) did this:

```c
store(pc5_pt_pml4(pt), pc5_pt_pdpt(pt) | 1ull | 2ull);
store(pc5_pt_pdpt(pt), pc5_pt_pd_base(pt) | 1ull | 2ull);   /* <-- only PDPT[0] */
...
for (unsigned long index = 0; index < total; index++)
    store(pc5_pt_pd_base(pt) + index * 8ul, (index * PC5_PT_2MIB) | PC5_PT_FLAGS);
```

It also zeroed only the **first** PD page (`PC5_PT_PD_PAGES * ENTRIES_PER_PAGE` from `pc5_pt_pd_base`, i.e. 512 entries = 4 KiB), leaving PD pages 1–3 with whatever firmware had left in them.

### Why filling 2048 leaf entries did not map 4 GiB

It wrote all 2048 leaf entries — but they are physically contiguous across four PD pages, and only the first PD page was ever referenced by a PDPT entry. The four-level walk for any address ≥ 1 GiB consumes `PDPT[1]`, `[2]`, or `[3]`, all of which were **zero**. A not-present PDPT entry terminates the walk: the CPU takes a page fault on the truncated path. No amount of correct leaf data in an unreachable page directory can fix that.

Worse, the function **returned 2048** and the loader's success test (`mapped != PC5_PT_SPAN / PC5_PT_2MIB`) passed, so the failure was silent. The consequences were real but latent: a framebuffer anywhere in `[1 GiB, 4 GiB)` passed every kernel check and was reported `usability=ok` with no mapping behind it; `pc5_pt_mark_uncached()` wrote `PWT|PCD` into page directories no PDPT entry referenced. Nothing dereferenced the framebuffer yet, so it was a **false report rather than a fault** — it becomes a fault on the first write.

### The fix (`ed9c6a3`, `boot/uefi64/pagetables.c:35-73`)

1. **Zero all six pages first** — `for (i = 0; i < PC5_PT_PAGES * PC5_PT_ENTRIES_PER_PAGE; i++)` (line 47). The whole PDPT is wiped, so a stale firmware present-bit can never become a phantom mapping above the span.
2. **One PDPT entry per gigabyte**, count derived from the span (`pdpt_used`, lines 58-63), each pointing at its own PD page inside the block: `pc5_pt_pd_base(pt) + g * PC5_PT_PAGE`.
3. **`PC5_PT_TABLE_FLAGS` (present|writable, no page-size bit)** separates a table pointer from a 2 MiB leaf. Using leaf flags with `PWT|PCD` on a table pointer would set reserved bits — a `#GP`, not a mapping.
4. **Span validated before anything is written**, so a refused call cannot leave a half-built hierarchy active.

Because `pdpt_used` is derived from the span, a shorter map installs fewer PDPT entries and every installed entry still points at a PD inside the block — so the short-span case is structural, not an afterthought.

### Verification claimed, and its limits

- An independent oracle (written by the auditing agent, **not** the implementation, and not reusing `pc5_pt_lookup`) walks the produced tables and checks 100 properties: **100 pass / 0 fail** on the fixed source, **55 pass / 36 fail** on the original. Failures include `PDPT[1] missing`, `reachable 512 != 2048`, and retained stale bytes in `PDPT[4]/[5]/[511]`.
- Computed indices for `0x0, 0x3FFFFFFF, 0x40000000, 0x7FFFFFFF, 0x80000000, 0xBFFFFFFF, 0xC0000000, 0xFFFFFFFF` are PML4 0 and PDPT 0/1/2/3 respectively; `0x100000000` falls outside the span. Highest byte touched is 24575 of a 24576-byte block — no OOB write.
- **These are still the auditor's claims.** §8 tells you how to reproduce them.

### L1 — the vacuous test weakness — CLOSED in `141f91f`

The original test, `tests/test_pagetables64.py:347`, read:

```python
for index in range(2048, 4 * 512):     # len(range(2048, 2048)) == 0
```

The loop was **vacuously empty**: the full 4 GiB span consumes exactly 2048 PD
entries, so the clause could never execute.  A builder that cleared PML4 and the
PDPT correctly but left the unused PD tail stale passed all 31 D1 tests — I
confirmed that with a surgical mutant before changing anything.

That is fixed now, in two ways:

1. **The loop itself** derives its bound from what the build actually mapped
   (`self.mapped`) instead of a constant, and asserts that at the full span there
   is nothing unused.  A constant bound was the original mistake; a different
   constant would be the same mistake.
2. **`ShortSpanInitialisationTests`** exercises the documented API rather than the
   current caller's configuration: 1 GiB, 3 GiB, one 2 MiB page, and
   `(4 GiB - 4 KiB)`.  Per span it checks that every reachable leaf carries its
   correct 2 MiB address and `0x83`; every unused PD entry is zero; a tail
   pre-filled with *present* entries is cleared rather than ignored (so nothing
   above the span translates); unused PDPT entries are zero and installed ones
   point inside the block; lookup translates the last mapped byte and refuses the
   first byte past the span (after the documented rounding); the block is
   byte-identical whatever was in that memory before; guard pages survive; and a
   refused span does not partially write.

`UnusedPdTailMutationTests` compiles the surgical mutant and runs the assertions
against it, so the replacement cannot rot back into a no-op.  Results:

| Implementation | Result against the final D1 suite |
|---|---|
| correct builder | 43 tests, **OK** |
| surgical stale-tail mutant | **13 failures across 4 test methods** (was 31/31 green) |
| original 4 GiB defect (`d0be617`) | **40 failures across 17 test methods** — discrimination intact |

Two traps worth knowing about, because the obvious versions of these tests are
wrong and would have passed vacuously in a different way.  Assertions are
**word-level, not byte-level**: a present leaf encodes its physical address, so a
block placed at `0xAA000000` contains `0xAA` bytes inside *correct* entries and a
canary byte-scan reports a false positive there.  And "deterministic
initialisation" compares **the same block rewritten with different patterns**;
comparing two different blocks compares their addresses, which legitimately
differ.

**No production code changed.**  The builder already initialised the short-span
block correctly — only the tests were blind to it.

---

## 5. D2 — UEFI `AllocateMaxAddress`

### Why passing zero was invalid

UEFI 2.9A §7.2.1 defines the `EFI_PHYSICAL_ADDRESS` pointed to by `Memory` as an **input** for `AllocateMaxAddress`: *"allocate any available range of pages whose uppermost address is less than or equal to the address pointed to by Memory on input."*

The original code (`170e611`) did:

```c
u64 address = 0;                                    /* <-- input */
efi_status status = g_bs->allocate_pages(EFI_ALLOCATE_MAX_ADDRESS,
                                         EFI_LOADER_DATA, pages, &address);
if (EFI_ERROR(status) || address == 0 || address > LOAD_LIMIT) return 0;
```

Zero describes a range ending at or below zero. No page allocation can satisfy that. edk2's `CoreInternalAllocatePages` does exactly `Start = *Memory` and searches below it, rejecting `ProposedStart + ProposedSize - 1 > MaxAddress`; Linux's `efi_high_alloc` guards with `if (!max_addr) status = EFI_NOT_FOUND`. So on any conforming firmware **the loader's first allocation fails**.

It was invisible because the stub ignored `alloc_type` entirely — it took no `EFI_ALLOCATE_*` branch, always carved from a fixed low pool, and returned a plausible address for a request no real firmware honours. `stubtest64` 22/22 and the `PC5-64BIT-LONG-MODE-OK` marker were resting on that stub.

### The corrected bound and validation (`ec16c65`, `boot/uefi64/uefi64_main.c:248-272`)

```c
if (pages == 0 || pages > (PC5_IDENTITY_LIMIT / 4096ull)) return 0;   /* wrap site first */
u64 address = PC5_IDENTITY_LIMIT - 1ull;        /* input: last byte the map covers */
... allocate_pages(EFI_ALLOCATE_MAX_ADDRESS, EFI_LOADER_DATA, pages, &address) ...
u64 last_page = address / 4096ull + pages - 1ull;
if ((address & 0xFFFull) != 0 || last_page >= PC5_IDENTITY_LIMIT / 4096ull) return 0;
if (address > LOAD_LIMIT) return 0;
```

- **`PC5_IDENTITY_LIMIT - 1` is the correct input**: `0xFFFFFFFF` is the last byte a mapped address may occupy. A conforming firmware returning `base=0xFFFFF000` for one page ends exactly at `0xFFFFFFFF` < 4 GiB. Checked numerically.
- **Whole-range, not base-only**: `last_page` must stay strictly below the limit, so a range that *starts* below 4 GiB but *crosses* it is rejected.
- **Alignment**: `(address & 0xFFF) != 0` rejects a misaligned result.
- **Overflow**: `pages` is bounds-checked *before* `pages * 4096` is ever formed; `pages = 2^52` would otherwise wrap to zero. Max legal count is 2^20 (1 GiB of pages).
- **Malformed firmware**: a firmware that ignores the bound, wraps, or straddles the limit causes a hard boot failure rather than handing the kernel an undereferenceable pointer.
- **ABI**: `efi_status(EFIAPI *allocate_pages)(u32, u32, u64, u64 *)`, `EFIAPI = __attribute__((ms_abi))`, `efi_status = u64`, `EFI_ALLOCATE_MAX_ADDRESS = 1` (`boot/uefi/efi.h:191-192, 32, 37, 164`).

### The conforming stub and the A/B reproduction

`tests/uefi_stub_firmware.py::svc_allocate_pages` now implements all three modes with their real contracts: `AllocateAnyPages` (input ignored), `AllocateMaxAddress` (zero → `EFI_OUT_OF_RESOURCES`; `ceiling = min(requested+1, pool_ceiling)`, applied to the **whole range** via `alloc(size, ceiling=ceiling)`), `AllocateAddress` (exact base or `EFI_NOT_FOUND`). Fault switches — `ignore_max_address`, `allocate_return`, `allocation_limit` — let a test model firmware that lies or has no memory in range.

**The A/B, firmware model held constant and only the loader varied** (this is the correct design; comparing each revision against its *own* stub measures the stub, not the loader):

| Loader | Stub | Result |
|---|---|---|
| original `d0be617` | conforming (`9d4a79d`) | fails: `PC5-64-LOADER-FAIL=bootinfo-allocate` |
| fixed `9d4a79d` | conforming (`9d4a79d`) | reaches the kernel |

### Remaining low-severity harness weakness (L2) — still open

`tests/uefi_stub_firmware.py:277-281`: the stub's `alloc()` **raises `StubError("stub pool exhausted")`** instead of returning `EFI_OUT_OF_RESOURCES`. `AllocateAnyPages` has no pre-check, so it raises directly; a subsequent `AllocateMaxAddress` can also raise when `pool_next` already exceeds the caller's ceiling. Real firmware returns a status the loader must survive; the harness would crash with a Python exception, so a test asserting "the boot fails cleanly" would pass for the wrong reason. **Low** severity because loader demand is, measured, **27 pages / 110,592 B** against a 16 MiB pool (151.7× headroom for the loader's own requests; 99.9× counting the 14 pages of harness scaffolding that share the pool) and the path is unrelated to D1/D2. *(Corrected 2026-11-16: this read "~35 pages (~470× headroom)", which is arithmetically impossible — 470 × 35 = 16,450 pages exceeds the pool's 4,096-page capacity. Demand was measured by instrumenting a real stub boot and reading `pool_next - POOL_BASE` at completion.)*

### Evidence level — read carefully

The stub is an **emulator**, not firmware. It is a faithful *model* of the three allocation modes and the strongest evidence available in this environment. It is **not** OVMF, not QEMU-with-real-firmware, not hardware, and not Sony execution. No claim in this package exceeds the emulator level.

---

## 6. Test evidence and reproduction instructions

### Environment

`gcc`/`binutils` (C11, `-m64`), `make`, `python3` ≥ 3.11, `node` ≥ 20. Python packages: `unicorn` 2.1.4 (the long-mode emulator — many 64-bit tests **error out** without it, they do not skip), `resvg_py`. Node package: `v86-system` (`npm install --no-save v86-system`). Optional: QEMU + OVMF, `capstone`, `nasm` — absent here.

```bash
python3 -m pip install --break-system-packages unicorn resvg_py
npm install --no-save v86-system
```

**Warning about skips:** without `unicorn`, the D2 suites raise `StubError("the 'unicorn' module is not installed")` and the run reports dozens of *errors*, not skips. If you see that, it is a missing dependency, **not** a regression. I hit exactly this during preparation and re-ran cleanly after installing it.

### Commands — all marked with what I have actually run

Set a pinned epoch for any artifact comparison: `export SOURCE_DATE_EPOCH=1700000000`.

| # | Purpose | Command | Status |
|---|---|---|---|
| 1 | Build the BIOS/ISO artifact | `make all` | **executed, exit 0** |
| 2 | Full suite (canonical) | `make -s check` | **executed, exit 0 — `Ran 688 tests`, `OK`, 0 skips** |
| 3 | Build 64-bit artifacts | `make -s kernel64 uefi64` | **executed, exit 0** |
| 4 | D1 page-table tests | `python3 -m unittest tests.test_pagetables64 -v` | **executed — 31 tests, OK** |
| 5 | D1 map/kernel helpers | `python3 -m unittest tests.test_map64 -v` | **executed — 12 tests, OK** |
| 6 | D2 allocation tests | `python3 -m unittest tests.test_boot64_stub -v` | **executed — 31 tests, OK** |
| 7 | D2 end-to-end stub boot | `make -s stubtest64` | **executed — `64-BIT STUB FIRMWARE OK: 22/22`, `PC5-64-DONE`** |
| 8 | ISO structure + manifest | `make -s verify` | **executed previously, exit 0** |
| 9 | Release-artifact isolation | build at `d0be617` and `9d4a79d` with the pinned epoch, compare `sha256sum build/pc5.iso build/kernel64/KERNEL64.BIN` | **executed previously — ISO and kernel bit-identical; only `BOOTX64.EFI` differs (that is the fixed code)** |
| 10 | OVMF / real-firmware boot | `make ovmf-test` | **NOT run — no OVMF or QEMU available.** Expect exit 2 `not-tested` |
| 11 | Physical hardware | — | **NEVER run** |

**Commands that still need genuinely independent execution by you:** everything in the table is reproducible, but *I* ran them. The ones that matter most for your decision are #4, #6, #7 (the defect-specific evidence) and the A/B reconstruction in §4 and §5, which I built in `/tmp` and which is not committed to the repository. If you want the A/B, rebuild it: check out `170e611`, copy `tests/uefi_stub_firmware.py` and `tests/boot64_stub.py` from `9d4a79d`, and run the loader against the conforming stub.

**Reproducing L1 for yourself** (do this — it is the most useful 5 minutes in this review): apply the six-page zeroing correctly but leave PD entries ≥ `total` stale, then run `python3 -m unittest tests.test_pagetables64`. It will pass. That is the blind spot.

---

## 7. Remaining limitations

Stated plainly, without being converted into claims of failure:

1. **No GitHub Actions CI.** `total_count = 0` workflows; `.github/workflows/` does not exist on any branch. `ci/build.yml` is dormant and cannot be activated: the GitHub App lacks the `Workflows` permission and GitHub rejected that exact push (documented in `ci/README.md` and `README.md:199`, milestone `M1b`).
2. **No branch protection.** All three branches: `protected: false`, `protection.enabled: false`, `required_status_checks.enforcement_level: "off"`. No repository rulesets (`[]`).
3. **No enforced independent approval.** 0 reviews, 0 comments, 0 review requests on both PRs. Both are authored by `SpicyBadran`, which is also the authenticated identity — so GitHub would reject a self-approval. Nothing automated stands between this change and `main`; **your review is the entire control.**
4. **OVMF/QEMU not tested.** `make ovmf-test` is `not-tested` (exit 2). No real UEFI firmware has executed this loader.
5. **No physical hardware validation.** Never booted on any PS5 or PC.
6. **No genuine Sony firmware execution.** No Sony code, keys, or system software is present or run.
7. **Two low-severity test-harness weaknesses.** L1 (vacuous PD-tail loop) was **closed in `141f91f`** — the tests now fail against the surgical stale-tail mutant, see §4. L2 (stub raises `StubError` instead of returning `EFI_OUT_OF_RESOURCES`) **remains open** on the current head; it is a harness-fidelity gap with 151.7× headroom on measured demand (27 pages of the pool's 4,096), not a production defect. *(Headroom corrected 2026-11-16 from "~470×".)*

None of these is a code defect, and none should be inflated into one. Equally, none should be quietly dropped from the record.

---

## 8. Your technical checklist

Answer each of these **from the code**, not from this document. Tick only what you personally verified.

**D1 — paging**
- [ ] Trace the walk for `0x0`, `0x40000000`, `0x80000000`, `0xC0000000` and confirm each consumes a *different* PDPT entry that is present and points at a *distinct* PD page.
- [ ] Confirm `PC5_PT_TABLE_FLAGS` has no page-size bit and that leaf entries use `PC5_PT_FLAGS` with `PWT|PCD` only at the leaf level.
- [ ] Confirm the zeroing loop covers all six pages, including the whole PDPT.
- [ ] Confirm `pdpt_used` is derived from the span and cannot point outside the block for any accepted span.
- [ ] Confirm `pc5_pt_lookup` rejects a PS-bit (1 GiB) leaf at the PDPT level rather than misreading it as a table.
- [ ] Confirm the highest byte written is within the 6-page block for the maximum accepted span.
- [ ] Reproduce L1's closure yourself: apply the mutation in `UnusedPdTailMutationTests.MUTANT`, run the suite, and confirm it now fails (it passed before `141f91f`).

**D2 — allocation**
- [ ] Confirm `PC5_IDENTITY_LIMIT - 1` is the last mappable byte, and that a conforming firmware returning `0xFFFFF000` for one page is accepted.
- [ ] Confirm the post-call check validates the **whole range**, not the base address.
- [ ] Confirm `pages` is bounded **before** `pages * 4096` can wrap, and that zero pages is refused.
- [ ] Confirm a firmware that ignores the bound causes a boot failure rather than a bad handover.
- [ ] Confirm the ABI: `ms_abi`, `u64` page count, pointer-to-physical-address as the in/out parameter.
- [ ] Walk all six `alloc_below_4g` call sites (`uefi64_main.c:497, 502, 517, 594, 610, 633`) and confirm each checks failure.

**Fidelity and regressions**
- [ ] Judge the stub yourself: does `svc_allocate_pages` model UEFI faithfully, and could it hide a real firmware failure?
- [ ] Confirm no test was weakened or deleted by `ed9c6a3`/`ec16c65` (`git diff d0be617..9d4a79d -- tests/`, look for removed assertions).
- [ ] Confirm the run you perform reproduces 688 tests with 0 skips **with artifacts built**, and understand why 2 tests skip without them.
- [ ] Confirm the ISO and kernel are bit-identical across the fix at `SOURCE_DATE_EPOCH=1700000000`.

**Landing**
- [ ] Confirm `git merge-tree <PR26 head> <PR27 head>` yields tree `5b452e26…`, identical to `141f91f^{tree}`.
- [ ] Confirm PR #26's combined diff against `main` is 83 files / +8197 / −166 with **0 deletions**.
- [ ] Confirm the audit document `AUDIT_PR26_PR27_2026-11-16.md` landing on `main` is acceptable to you.
- [ ] Confirm you accept that the merge sequence must be PR #27 → base, verified, **then** PR #26 → `main`.

---

## 9. Reviewer decision

Fill this in yourself. Do not merge before this section is complete.

```
Reviewer name:        ______________________________
Date:                 ______________________________
Head verified at:     PR #26 170e611…   yes / no
                      PR #27 141f91f…   yes / no

D1 paging traced independently:        yes / no
D2 allocation verified independently:  yes / no
Stub fidelity judged adequate:         yes / no
A/B reproduced independently:          yes / no
L1 closure verified (mutant now caught): yes / no
L2 must be fixed before merge:         yes / no

Combined tree 5b452e26… verified:      yes / no
Zero-deletion combined diff verified:  yes / no
Audit document on main acceptable:     yes / no

RECOMMENDATION:   [ ] APPROVE for Strategy D landing
                  [ ] REJECT — reason(s):
                  ________________________________________________
                  [ ] APPROVE only after specific changes:
                  ________________________________________________

Signature:            ______________________________
```

**A rejection on technical grounds is a successful review.** If something in §4–§6 does not hold up when you check it, reject it and say why — that outcome is worth more than an approval that merely echoes this document.

---

## Amendment — 2026-11-16, L1 closed in `141f91f`

This package was first written against PR #27 head `9d4a79d`.  One commit has been
added since, and every fact it changes has been updated in place above.  Nothing
else moved: `main` is still `8aab6d38…`, PR #26 is still `170e611…`, and PR #27
still targets `arena/af939eb1-pc5`.

| Item | Before | Now |
|---|---|---|
| PR #27 head | `9d4a79d` | **`141f91f`** |
| Commits `main..PR27` | 29 | **30** |
| Combined diff | 83 files, +7857/−166 | **83 files, +8197/−166**, still 0 deletions |
| Combined tree (`merge(PR26, PR27 head)`) | `d63b04c4…` | **`5b452e26…`** |
| Full suite | 676 tests | **688 tests, 0 skips** |
| L1 | open | **closed** |
| L2 | open | open (unchanged) |

**What changed in the commit:** `tests/test_pagetables64.py` only, 343 insertions
and 3 deletions *within that file*.  No production code, no other test, no
evidence record, no release artifact.  The 3 deleted lines are comment and
loop-header lines from the vacuous loop; no assertion was removed.

**Verification re-run at `141f91f`:**

| Check | Result |
|---|---|
| `make all` | exit 0 |
| `make -s check` | exit 0 — `Ran 688 tests`, `OK`, 0 skips |
| `python3 -m unittest tests.test_pagetables64` | 43 tests, OK |
| `python3 -m unittest tests.test_map64` | 12 tests, OK |
| `python3 -m unittest tests.test_boot64_stub` | 31 tests, OK |
| `make -s stubtest64` | `22/22`, `PC5-64-DONE` |
| ISO / kernel vs `d0be617` at `SOURCE_DATE_EPOCH=1700000000` | bit-identical (`793341cf…`, `032183f8…`) |

**Strategy D is unchanged and still valid**, on the same argument: merging PR #27
into its base yields a tree identical to the audited head (`5b452e26…`), so no
content is re-derived between audit and merge.  The merge sequence, the ordering
gate, and both blockers (B1 independent human approval; B2 optional CI/protection
hardening) are as stated in the landing-readiness document.

The reviewer's checklist in §8 has been updated to match, including an item to
reproduce L1's closure by running the mutant and confirming it now fails.
