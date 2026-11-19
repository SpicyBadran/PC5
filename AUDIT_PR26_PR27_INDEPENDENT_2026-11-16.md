# Author post-remediation audit — PR #26 and PR #27

> **Not an independent audit.** The title and the filename say "independent". They are not:
> the author of this audit also authored the remediation it audits. The filename is retained
> because other documents reference it by name; the claim in the title has been withdrawn.


| | |
|---|---|
| **Tested revision** | `170e611dcdfa3fe12e9abe58c2e60ee323aa461c` (PR #26) and `141f91f0ca192ca72972ca9afdbee8c86e6896c5` (PR #27) |
| **Evidence type** | Author-reproduced. |
| **Historical?** | Yes — post-remediation at `141f91f`, before the F1–F5 work. |
| **Author** | The PC5 implementer. **Not an independent audit.** |

**Auditor:** independent senior systems engineering review (second pass, post-remediation)
**Date:** 2026-11-16
**Scope:** PR #26 `170e611` and PR #27 `9d4a79d`, read-only
**Mode:** strictly read-only. No commit, push, merge, close, retarget, rebase, or milestone change was made. All experiments ran in disposable trees under `/tmp` or in temporary git worktrees that were removed afterwards.

---

## Verdict

# APPROVE FOR MERGE

**as Strategy D only — merge PR #27 into PR #26's branch, then merge PR #26 (`170e611 + 9d4a79d`) to `main` as one integrated change, never as a two-step A/B landing.**

No Critical or High findings. The two remediated defects are independently confirmed real and independently confirmed fixed. Both defects are confined to the 64-bit path, which is deliberately excluded from the release artifact. The verdict is conditional on the landing strategy: under Strategy A (merge #26 first) this PR must **not** be approved, because #26 alone puts both defects on `main`.

**Highest evidence level demonstrated: emulator (Unicorn stub firmware).** No OVMF, no QEMU, no hardware, no genuine Sony execution was performed or claimed. See §9.

---

## A. Live state verification (§1) — PASS

Measured before any other work and re-measured at the end; both readings identical.

| Ref | Expected | Measured | Match |
|---|---|---|---|
| `main` | `8aab6d3864bb2e867a70bba28e7bfb52f43d7e35` | same | yes |
| PR #26 head | `170e611dcdfa3fe12e9abe58c2e60ee323aa461c` | same | yes |
| PR #27 head | `9d4a79db2e078638bc10c7a8c54ed6182d55f115` | same | yes |
| PR #26 base | `main` | `main` | yes |
| PR #27 base | `arena/af939eb1-pc5` | `arena/af939eb1-pc5` = `170e611` | yes |
| PR #26 state | OPEN, MERGEABLE/CLEAN | OPEN, CLEAN, `reviewDecision=""` | yes |
| PR #27 state | OPEN, MERGEABLE/CLEAN | OPEN, CLEAN, `reviewDecision=""` | yes |

**Reviews: 0 on both PRs. Comments: 0 on both PRs. No approval exists on either.** Ancestry is linear: `main → 170e611 → 9d4a79d`; commit counts `main..26` = 15, `26..27` = 14, `main..27` = 29.

Because heads and bases matched the expected values exactly, the mandated stop-and-report condition did not trigger and the audit proceeded.

## B. Review environment (§2) — PASS

`git worktree add --detach /tmp/review 9d4a79d`, clean at creation. Correspondence to the live head was proved by hash, not by trust:

- **208 of 208 tracked blobs byte-identical** to the pushed head; 0 missing, 0 differing.
- Audit file `/home/user/preserved2/AUDIT_PR26_PR27_2026-11-16.md` preserved, sha256 `d09f69deab6723ac87cc20e38553a3c04bcc3d018a181c356bbfc72d26bc6319`, re-verified at the end of the audit.
- The pre-existing stale checkout `/home/user/pc5` remains at `8aab6d3` with its dirty/untracked files **untouched**, as instructed.
- Temporary worktrees created for reproduction (`/tmp/genuine`, `/tmp/nobuild`, `/tmp/pr26`) were removed; `git worktree list` is back to the original two entries.

## C. D1 — the 4 GiB identity map traced from source (§3) — PASS

I re-derived the walk from the C, then built an independent oracle (`/tmp/indep/walk.py`) that walks the page tables the implementation actually produced, rather than trusting `pc5_pt_lookup` or any comment.

The real walk is 4-level: `pml4[0] → pdpt[(a>>30)&0x1FF] → pd[(a>>21)&0x1FF]` with the PS bit set at the PD level. Requested indices, computed independently:

| Address | PML4 | PDPT | PD | Leaf offset | Region |
|---|---|---|---|---|---|
| `0x0` | 0 | 0 | 0 | 0 | 1st GiB |
| `0x3FFFFFFF` | 0 | 0 | 511 | `0x1FFFFF` | 1st GiB |
| `0x40000000` | 0 | 1 | 0 | 0 | 2nd GiB |
| `0x7FFFFFFF` | 0 | 1 | 511 | `0x1FFFFF` | 2nd GiB |
| `0x80000000` | 0 | 2 | 0 | 0 | 3rd GiB |
| `0xBFFFFFFF` | 0 | 2 | 511 | `0x1FFFFF` | 3rd GiB |
| `0xC0000000` | 0 | 3 | 0 | 0 | 4th GiB |
| `0xFFFFFFFF` | 0 | 3 | 511 | `0x1FFFFF` | 4th GiB |
| `0x100000000` | 0 | 4 | 0 | — | **outside span** |

PML4 index is 0 for every address below 512 GiB. All four PDPT slots (0–3) are required, which is exactly the defect: the original builder only made `PDPT[0]` reachable, so 3 of 4 GiB were dead. Verified all 12 enumerated properties at source level, including: PML4[0] present with flags `0x3`; each of the four PDPT entries points at a distinct PD page inside the 6-page block; all 2048 leaves carry `0x83` and the correct 2 MiB-aligned physical base; PDPT[4..511] and PML4[1..511] are zero; the `PS` bit is rejected at the PDPT level by `pc5_pt_lookup` so a 1 GiB leaf cannot be mistaken for a table.

**Block geometry / OOB:** `PC5_PT_PAGES = 6` ⇒ 6 × 4096 = 24576 bytes. Highest byte touched is the last leaf at offset 24575 — **no out-of-bounds write.** PDPT[3] sits at byte 4120–4127, inside the PDPT page.

**Address-width and PA-mask assumptions (§3 sub-question) — informational finding, see L1.** The tables store physical addresses below 4 GiB only, so the 52-bit PA mask in the oracle is never exercised at its limit. The loader performs **no CPUID feature check** before building the hierarchy or entering long mode (no `cpuid`, no PAE/NX/MSR probe anywhere in `boot/uefi64/`). The kernel checks physical address width only *after* handover (`kernel64/include/pc5_map.h:139`, `kernel64/main.c:379-393`), reporting `checked=0` when firmware does not report a width. This is a pre-existing design property of the 64-bit path, not introduced by the remediation, and it does not affect the two remediated defects.

**Independent A/B of the D1 fix** (`/tmp/indep/d1_verify.py`, 100 assertions against the real compiled C):

- fixed source `9d4a79d`: **PASS 100 / FAIL 0**
- original source `d0be617`: **PASS 55 / FAIL 36**, including `span 0x80000000: PDPT[1] missing`, `reachable 512 != 2048`, and retained stale bytes in `PDPT[4]/[5]/[511]`

Both the defect and the fix are real and independently confirmed.

## D. D1 tests, adversarially (§4) — PASS with one Low finding

The tests genuinely do what they claim in the important places: they compile the real implementation and read the hierarchy through `entry()`/`pml4()`/`pdpt()` accessors (`tests/test_pagetables64.py:81,102-108`), rather than taking `pc5_pt_lookup`'s return value as proof. They cover all four 1 GiB regions, both exclusive boundaries, stale-entry zeroing, the uncached marking at `0x18`, and OOB rejection.

Mutations were used to test the tests, each isolated surgically:

- Truncated span mutant, stale PDPT tail → **caught.**
- Mutant writing PML4[0] before zeroing the block → **caught** by the PML4-emptiness test.
- **Fully surgical mutant** — PML4 and PDPT correctly zeroed, but PD entries ≥ `total` left stale → **NOT caught, rc=0.** Confirmed in both the shared-block and short-span configurations.

**Root cause (L1 in §L):** `tests/test_pagetables64.py:348` reads

```python
for index in range(2048, 4 * 512):
```

`range(2048, 2048)` is **empty**. All 2048 PD entries are consumed by the full 4 GiB span, so the loop can never execute a single iteration and the `test_every_active_structure_is_zero_beyond_the_span` test's PD-tail clause is vacuous. A stale PD tail under a **short span** — precisely the configuration the builder explicitly supports and documents — is invisible to the suite.

This is **Low**, not High, because: the production call site (`boot/uefi64/uefi64_main.c:613`) always passes `PC5_PT_SPAN` (full 4 GiB) and asserts `PC5_PT_SPAN == PC5_IDENTITY_LIMIT` at `uefi64_main.c:183`, so no shipping path can reach the short-span configuration; the primary D1 claims are covered by non-vacuous assertions elsewhere in the same file; and the defect it would hide is a defence-in-depth property, not the remediated defect. It is a genuine latent hole in a test whose name claims otherwise.

## E. D2 — `alloc_below_4g` against the UEFI spec (§5) — PASS

Verified against the specification text, not against the stub. UEFI 2.9A §7.2.1: for `AllocateMaxAddress`, the `EFI_PHYSICAL_ADDRESS` pointed to by `Memory` is an **in** parameter — "allocate any available range of pages whose uppermost address is less than or equal to the address pointed to by `Memory` on input." The original code passed its zero-filled out-parameter in, describing a range ending at or below zero, which no firmware can satisfy. Independently corroborated against edk2 `MdeModulePkg/Core/Dxe/Mem/Page.c` (`Start = *Memory;`, `MaxAddress = Start`, `ProposedStart + ProposedSize - 1 > MaxAddress` rejected) and against Linux `efi_high_alloc`/`if (!max_addr) status = EFI_NOT_FOUND;`.

Checked, in `boot/uefi64/uefi64_main.c:248-272`:

| Property | Result |
|---|---|
| Input max = `PC5_IDENTITY_LIMIT - 1` | **Correct.** `0xFFFFFFFF` is the last byte a mapped address can occupy; a conforming firmware returning `base=0xFFFFF000` for 1 page ends exactly at `0xFFFFFFFF` < 4 GiB. Verified numerically. |
| Whole range below 4 GiB, not just base | **Correct.** `last_page = address/4096 + pages - 1; last_page >= LIMIT/4096 → reject`. Arithmetic in page units, so no multiply can wrap. |
| Alignment | Rejected: `(address & 0xFFF) != 0`. |
| Overflow | `pages > PC5_IDENTITY_LIMIT/4096` rejected **before** `pages*4096` is ever formed. `pages = 2^52` would otherwise wrap to 0; pre-check rejects it. Max legal count is 2^20. |
| Zero page count | Rejected pre-call. |
| `address == 0` | Rejected (a page-zero allocation is never acceptable here). |
| `LOAD_LIMIT` | Enforced (`address > LOAD_LIMIT → reject`). |
| Malformed firmware | A firmware that ignores the bound, returns a wrapped or misaligned range, or crosses the limit causes a hard boot failure, not a kernel handed an undereferenceable pointer. |
| Inclusive/exclusive boundary | Correct at both ends: exactly-ending range accepted, one-page-past rejected. |
| `*physical` out-parameter | Written only on success; callers get either a valid range or `0`. |

**ABI declarations** (`boot/uefi/efi.h:191-192`): `efi_status(EFIAPI *allocate_pages)(u32 type, u32 memory_type, u64 pages, u64 *memory)`. `EFIAPI` is `__attribute__((ms_abi))` (`efi.h:37`) — correct for UEFI on x64. `efi_status` is `u64` (`efi.h:32`), matching `EFI_STATUS` (64-bit). `EFI_ALLOCATE_MAX_ADDRESS = 1` (`efi.h:164`) matches the spec. `EFI_ERROR` semantics are honoured. `pages` is correctly `u64`, not `unsigned long` — no 32-bit truncation. The physical address is passed by pointer as the spec requires.

## F. Stub fidelity (§6) — PASS with one Low finding

`tests/uefi_stub_firmware.py::svc_allocate_pages` implements all three modes:

- `AllocateAnyPages` — input ignored, allocates from the pool. Correct.
- `AllocateMaxAddress` — `requested == 0 → EFI_OUT_OF_RESOURCES`; else `ceiling = min(requested+1, pool_ceiling)`, `size > ceiling → EFI_OUT_OF_RESOURCES`, then `alloc(size, align=4096, ceiling=ceiling)`. The `ceiling` bound is applied to the **whole range**, not the base: `alloc()` enforces `address + size > limit → fail`. **Whole-range semantics, correct.**
- `AllocateAddress` — rejects misaligned, rejects `requested + size > pool_ceiling`, returns `EFI_NOT_FOUND`. Correct.

Bound inclusivity is handled correctly (`requested + 1` ceiling, so a range ending exactly at `requested` is permitted). Invalid requests return `EFI_INVALID_PARAMETER` rather than raising.

**Can it hide a real UEFI failure? Not for the defect under review.** I reproduced the A/B myself with the firmware model held constant and only the loader varied — this is the correct experimental design, and it corrects a flaw in my own first attempt, where I initially compared both revisions using each one's *own* shipped stub (which measured the stub, not the loader):

| Loader | Stub | Result |
|---|---|---|
| original `d0be617` | conforming (from `9d4a79d`) | **fails**: `PC5-64-LOADER-FAIL=bootinfo-allocate` |
| fixed `9d4a79d` | conforming (from `9d4a79d`) | **reaches kernel** |

**L2 in §L — a latent harness gap.** The stub's `alloc()` **raises** `StubError` on exhaustion rather than returning `EFI_OUT_OF_RESOURCES`. `AllocateAnyPages` has no pre-check at all, so it raises directly (demonstrated: `pool_next=0x2fc1000` against `POOL_END=0x3000000`). A subsequent `AllocateMaxAddress` with a ceiling below `pool_next` can then also raise, because the `size > ceiling` guard passes while `pool_next + size > ceiling`. This is a fidelity gap: real firmware returns a status the loader must survive, whereas the harness would crash with a Python exception — and a test expecting "boot fails cleanly" would pass either way. Graded **Low**: the loader's total demand is **27 pages / 110,592 B** against a 16 MiB pool, so no realistic run reaches exhaustion — the correct headroom is **151.7×** for the loader's own requests (and 99.9× counting the 14 pages of harness scaffolding that share the pool). *Correction, 2026-11-16:* this line originally said "~35 pages (≈470×)", which is both mismeasured and arithmetically impossible — 470 × 35 = 16,450 pages exceeds the pool's 4,096-page theoretical capacity. The figure was measured by instrumenting a real stub boot (5 `AllocateAnyPages`/`AllocateMaxAddress` requests) and reading `pool_next - POOL_BASE` at completion. The path does not touch either remediated defect. Recommended fix for a later change, not a blocker.

**Evidence-level honesty:** the stub is an *emulator*, not firmware. It is a faithful *model* of the three allocation modes and it is the strongest evidence available in this environment, but it is not OVMF and not hardware. This audit makes no claim beyond the emulator level.

## G. Retained-allocation inventory (§7) — PASS

Every retained allocation traced through a real run, with the **whole range** checked for identity coverage (not merely the base address) by walking the tables the loader actually installed:

| # | Object | Call site | Type | Pages | Max in | Returned range | Whole range mapped | Validated | Handed over | Kernel checks |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | boot info | `uefi64_main.c:594` | MaxAddress | 1 | `0xFFFFFFFF` | `0x0200C000`–`0x0200CFFF` | yes | yes | yes | yes (span/flags) |
| 2 | page tables | `uefi64_main.c:610` | MaxAddress | 6 | `0xFFFFFFFF` | `0x0200E000`–`0x02013FFF` | yes | yes | yes (CR3 + boot info) | yes (`pagetable-span-mismatch`) |
| 3 | kernel stack | `uefi64_main.c:633` | MaxAddress | 4 | `0xFFFFFFFF` | `0x02014000`–`0x02017FFF` | yes | yes | yes | via stack pointer |
| 4 | MemMap buffer (scratch) | `uefi64_main.c:497` | MaxAddress | 8 | `0xFFFFFFFF` | `0x02019000`–`0x02020FFF` | yes | yes | **no — not retained** | n/a |
| 5 | MemMap copy (out) | `uefi64_main.c:502` | MaxAddress | 8 | `0xFFFFFFFF` | `0x02021000`–`0x02028FFF` | yes | yes | yes | yes (`memmap_addr/bytes`) |
| 6 | loader image | firmware-loaded | n/a | — | — | `0x00400000`–`0x00402FFF` | yes | n/a | recorded | via boot info |

Result: **all retained ranges fully mapped: true.** The kernel's span check recomputes `identity_span == (pagetable_pages-2)*512*2 MiB`, so the loader and kernel cannot silently disagree about the map; a wrong page count is rejected as `bootinfo-pagetable-span-mismatch`.

**Boot services after `ExitBootServices`: 0.** Verified by instrumenting the run — no allocation, memory-map, or other boot-service call occurs after the exit. `CR3` handling was verified in `boot/uefi64/bootx64.S`: `pc5_enter_kernel64` writes CR3, **reads it back and compares** before the `jmpq`, and restores the firmware CR3 on failure.

## H. Regression integrity (§8) — PASS

Four commits between `d0be617` and `9d4a79d`: `ed9c6a3`, `ec16c65`, `a9b383d`, `9d4a79d`.

- **No removed or weakened assertions:** zero deleted lines matching `assert|def test_|CHECK(` across the whole range.
- **No skips or xfails added:** zero `skip`/`xfail`/`expectedFailure` lines added under `tests/`.
- **Honest recording:** the docs commit `a9b383d` **supersedes** the earlier "4 GiB map" verdict rather than rewriting it — historical records are not silently falsified.
- **Record honesty:** all records carry `dirty=false`, `source_date_epoch=1763251200`, `commit=a9b383d`, and `ovmf-test` correctly stays `not-tested`/`exit=2`.

**Full suite reproduced (exact commands, real repository):**

| Sequence | Exit | Result |
|---|---|---|
| `make all && make check` (SOURCE_DATE_EPOCH=1700000000) | **0** | `Ran 676 tests` — `OK`, 0 skips, 0 files modified |
| `make check` alone, no prebuilt artifacts | **0** | `Ran 676 tests` — `OK (skipped=2)` |
| `make clean && make iso && make check` (CI's sequence) | **0** | `Ran 676 tests` — `OK` |
| `make verify` | **0** | `MANIFEST VALID` |
| `make check` at PR #26 `170e611` | **0** | `Ran 612 tests` — `OK (skipped=2)` |

**Two false alarms of my own, corrected rather than reported as findings** — recorded here because they matter to the audit's credibility:

1. I first measured `make all` as dirtying the tracked `docs/evidence/validation/ovmf-test.json`. That was **my own contamination** (an earlier `validation.py run` in that tree), not the build. Re-tested on a pristine export: **208/208 tracked files unmodified.** No finding.
2. I then measured 6 errors in `make check`, and separately at base — both were **`git ls-files` failing with exit 128** because my extraction was a bare tarball with no repository, not a code defect. Re-run in a real worktree: **clean pass.**

The 2 skips are legitimate and environment-dependent: `test_the_real_esp_is_not_firmware` ("build/efi.img is not built") and `test_built_iso_verifies` ("build/pc5.iso not built"). They disappear once artifacts are built, which the `make all && make check` run confirms. They are not suppressed tests.

**Mutation testing:** mutants fail for the intended reason, and the one survivor is the genuine blind spot documented in §D/L1.

## I. OVMF / QEMU (§9) — NOT AVAILABLE, outstanding gap

`make ovmf-test` → `not-tested`, exit **2**, with the explicit message that genuine OVMF firmware and/or QEMU is not present. No QEMU, OVMF, capstone or nasm is installed in this environment. I did **not** install arbitrary unpinned firmware to manufacture a pass.

**Highest evidence level actually demonstrated: emulator (Unicorn long-mode stub firmware).** Nothing in this report demonstrates OVMF execution, real firmware behaviour, hardware behaviour, or genuine Sony software execution. The UEFI contract findings in §E rest on the specification text plus the edk2 and Linux reference implementations — spec-and-reference evidence, which is strong for a semantic question like this one, but it is **not** execution on firmware. This gap is pre-existing and correctly recorded in the repository as `not-tested` rather than being papered over.

## J. Release isolation (§10) — PASS

Reproduced rather than trusted, with `SOURCE_DATE_EPOCH=1700000000`:

| Artifact | `d0be617` (pre-fix) | `9d4a79d` (PR head) | Identical |
|---|---|---|---|
| `build/pc5.iso` | `793341cf100f7ed4…` | `793341cf100f7ed4…` | **yes** |
| `build/kernel64/KERNEL64.BIN` | `032183f8132dea26…` | `032183f8132dea26…` | **yes** |
| `build/uefi64/BOOTX64.EFI` | `5def86ef770bed4…` | `6cda14a3c0f93365…` | **no — expected** |

The UEFI application differs because **that is the artifact the remediation changed**; identical size (10275 bytes), differing only where the loader changed. The ISO and kernel are bit-identical across the fix, which independently confirms zero blast radius.

This is consistent, not contradictory, because the 64-bit path is **deliberately excluded from the release**: `Makefile:107-111` states the long-mode image is "not part of `all`, `iso` or the release artifact", and `release-assets` (`Makefile:408-416`) stages only `pc5.iso`, its sha256, `build-info.json` and the manifest. I verified the UEFI binary is genuinely **not embedded** in `pc5.iso`. The dependency graph (`all: iso`, unchanged between base and head) and `ci/build.yml` (no `make all`; explicit `make clean && make iso`, `make verify`, `make unit`, `make stubtest`, `make boot-test`, `make ovmf-test`) are consistent with that isolation.

**Therefore: no shipped artifact changes. The remediation is confined to a path that does not ship.** This is the single most important fact for the §K decision.

## K. Landing strategies (§11) — analysed, none executed

**Topology (verified, not assumed):** PR #27's base branch `arena/af939eb1-pc5` **is** PR #26's head `170e611`. Ancestry `main → 170e611 → 9d4a79d` is strictly linear; there is no divergence, no merge conflict, and no rebase required. `170e611` contains no remediation commits at all — the fix arrives strictly later, at `ed9c6a3`.

**The decisive empirical fact:** PR #26 at `170e611` is **fully green — `Ran 612 tests`, `OK`, exit 0 — while carrying both defects.** Moreover I proved the suite cannot distinguish the two states: the *defective* `pagetables.c` passes PR #26's 19 D1 tests (`OK`), and the *fixed* `pagetables.c` also passes those same 19 tests (`OK`). The D2 side is worse — at `170e611` the stub itself has the defect, so the suite agrees with the bug. **PR #26's green CI carries zero information about either defect.** Any landing strategy that relies on "CI is green" to justify accepting #26 alone is unsound.

| Strategy | Outcome | Assessment |
|---|---|---|
| **A** — merge #26 to `main` first, then #27 | Both defects land on `main` in `170e611`, green-CI-blessed. The dead 3 GiB map and the unsatisfiable allocation bound ship to `main`, then get fixed by a later PR. | **Rejected.** Puts a known-bad intermediate on `main`; the defective commit would then be permanent history reachable from `main`, with a green CI record implying it was correct. |
| **B** — close #27, fold its commits into #26 | Duplicates history; requires rewriting #26's head (a force-push on someone else's branch) and destroys the separate review record. | **Rejected.** History rewrite plus loss of review provenance. |
| **C** — merge #27 straight into `main`, skip #26 | GitHub would carry all 29 commits, but #26's 15 commits would be duplicated/superseded, and #26 would be left open and unmergeable without conflict. Loses #26's work as a reviewable unit. | **Rejected.** Orphans #26 and misrepresents authorship of the integrated change. |
| **D** — merge #27 → `arena/af939eb1-pc5` (= #26 head), then `#26 (170e611 + 9d4a79d)` → `main` as one change | Exactly the current topology. No intermediate defective state ever reaches `main`. All 29 commits preserved once, in order, with no duplication. #26's review record stays valid (it reviews `170e611`; the additions are a strict superset, already reviewed here). Diff accuracy preserved. | **RECOMMENDED.** |

**Recommendation: Strategy D.** Merge PR #27 into `arena/af939eb1-pc5`, then merge PR #26 to `main`. The combined landing brings `main` from 612 to 676 tests and from the broken to the correct 4 GiB map in a single step, with no window in which `main` is knowingly wrong.

**Caveats the merger must respect:**
- **No approval exists on either PR** (`reviewDecision=""` on both). This audit is a technical assessment, not a GitHub approval; a human reviewer of record still needs to approve. My standing constraint forbids me from approving my own work, and that constraint is respected here.
- **0 reviews, 0 comments** on both PRs. If branch protection requires an approving review, it must be obtained before merging.
- CI green on `9d4a79d` is necessary but, as §K shows, genuinely *not* sufficient evidence on its own — the green suite is blind to both original defects. The approval must rest on the source-level and A/B evidence in §C–§H.
- Do not merge #26 to `main` at `170e611` before #27 lands (Strategy A). That single mistake converts this approval into a defect shipment.

## L. Findings by severity

### Critical
None.

### High
None.

### Medium
None.

### Low

**L1 — Vacuous assertion leaves the PD tail untested.**
`tests/test_pagetables64.py:348`,
`test_every_active_structure_is_zero_beyond_the_span()`:
`for index in range(2048, 4 * 512)` — `range(2048, 2048)` is empty, so the PD-tail clause never executes. All 2048 entries are consumed by the full span, and a stale PD tail under a short span is undetected by the entire suite (proved with a surgical mutant: rc=0, not caught). *Impact:* latent test hole in a test whose name claims otherwise. *Mitigation:* unreachable from production, which always passes the full `PC5_PT_SPAN` (`uefi64_main.c:613`, pinned by `_Static_assert` at `:183`); the remediated defect itself is covered by other non-vacuous assertions. *Suggested fix:* make the tail check span-relative (`range(total, PC5_PT_PAGES * 512)`), or parameterise it over a short span.

**L2 — Stub raises instead of returning a status on pool exhaustion.**
`tests/uefi_stub_firmware.py:281` (`alloc`), reached from `svc_allocate_pages` without a guard in `AllocateAnyPages`, and reachable in `AllocateMaxAddress` when `pool_next` already exceeds the caller's ceiling. *Impact:* a real firmware answer (`EFI_OUT_OF_RESOURCES`) becomes a Python exception, so a test asserting "the loader fails cleanly" would pass for the wrong reason. *Mitigation:* **151.7× headroom** — 27 pages (110,592 B) measured demand vs a 16 MiB pool holding 4,096 pages; 99.9× if harness scaffolding is counted. Unrelated to either remediated defect. *(Corrected 2026-11-16: this read "~470× (≈35 pages)", which overstates the headroom and does not follow from its own numbers; demand was measured by instrumenting a real stub boot (5 `AllocateAnyPages`/`AllocateMaxAddress` requests) and reading `pool_next - POOL_BASE` at completion.)* *Suggested fix:* return `EFI_OUT_OF_RESOURCES` rather than raising.

### Informational

**I1 — No pre-map CPU feature check.** `boot/uefi64/` contains no `cpuid`/PAE/NX/MSR probe before building the hierarchy or entering long mode; physical-address-width validation happens only after handover (`kernel64/main.c:379-393`) and reports `checked=0` when unsupported. Pre-existing design property of the 64-bit path, not introduced by the remediation.

**I2 — Green CI is not evidence here.** Demonstrated empirically: `170e611` is green (612 tests) with both defects present, and the defective and fixed `pagetables.c` both pass PR #26's 19 D1 tests. Future reviewers should not treat a green suite as validation of this subsystem; the A/B and source-level oracle in this report are what establish correctness.

**I3 — Two audit self-corrections.** My initial `make all`-dirties-a-record and `make check`-has-6-errors results were both artefacts of my own test method (contaminated tree; tarball without `.git` causing `git ls-files` exit 128). Both were corrected against a real repository before being reported. They are recorded so the audit trail is complete and not silently cleaned up.

**I4 — Evidence level.** Highest level reached: emulator (Unicorn stub). OVMF/QEMU unavailable; `ovmf-test` correctly `not-tested`/exit 2. No hardware, no genuine firmware, no Sony execution claimed.

---

## Summary

| § | Area | Result |
|---|---|---|
| A | Live state | PASS — matches expected exactly, 0 reviews on both |
| B | Review environment | PASS — 208/208 blobs identical, preserved |
| C | D1 source / walk | PASS — 100/0 fixed vs 55/36 original |
| D | D1 tests | PASS — with L1 (vacuous PD-tail loop) |
| E | D2 vs UEFI spec | PASS — bound, alignment, overflow, LOAD_LIMIT all correct |
| F | Stub fidelity | PASS — whole-range ceiling correct; L2 latent gap |
| G | Allocation inventory | PASS — all retained ranges fully mapped, 0 post-EBS calls |
| H | Regression integrity | PASS — 676/676, 0 skips with artifacts, no weakened assertions |
| I | OVMF/QEMU | NOT AVAILABLE — emulator is the highest level |
| J | Release isolation | PASS — ISO and kernel bit-identical; 64-bit path does not ship |
| K | Landing strategies | Strategy D recommended; A/B/C rejected |
| L | Findings | 0 Critical, 0 High, 0 Medium, 2 Low, 4 Informational |

**Verdict: APPROVE FOR MERGE — under Strategy D only (PR #27 into `arena/af939eb1-pc5`, then PR #26 to `main` as one integrated change). A human approving review is still required; this document is a technical assessment, not an approval.**
