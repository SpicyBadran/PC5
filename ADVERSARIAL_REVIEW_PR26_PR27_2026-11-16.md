# Author self-review — PR #26 + PR #27

> **Not an independent review.** The title of this document was "Independent adversarial
> review" when it was first written. That was wrong: the author of this document also wrote
> the D1/D2 fixes, the L1 correction and the F1–F5 remediation it examines. The title has
> been corrected rather than the content deleted, because the findings and the reproduced
> evidence are still useful to a real reviewer — they are simply not a second opinion.


| | |
|---|---|
| **Tested revision** | `141f91f0ca192ca72972ca9afdbee8c86e6896c5` (PR #27 head at the time) |
| **Evidence type** | Author-reproduced. Every command was re-run by the author; nothing was copied from a prior report. |
| **Historical?** | Yes — it examines `141f91f`, which is superseded by later commits. |
| **Author** | The PC5 implementer. **Not an independent review.** |

**Repository:** `SpicyBadran/pc5`
**Reviewed refs:** `main` `8aab6d38`, PR #26 `170e611`, PR #27 `141f91f`
**Date:** 2026-11-16
**Mode:** strictly read-only. No file, commit, branch, PR, or review package was modified. One temporary git worktree and scratch trees under `/tmp` were created and removed; `git status` shows no tracked modification and repository HEAD is unchanged at `141f91f`.

## Declaration of interest

I wrote the two prior audit documents **and** the `141f91f` commit under review. A review by the author is not independent in the social sense the brief asks for. I have compensated by treating every prior conclusion as a claim to be disproved, re-deriving D1 with a walker written fresh for this task, and actively trying to falsify the L1 fix. Two errors in my own earlier work came out of that process and are reported in §B (F4, F5). A human still needs to do the independent sign-off.

---

## A. Verified repository state

Measured at the start and re-checked at the end. **No drift.**

| Ref | SHA | Matches brief |
|---|---|---|
| `main` | `8aab6d3864bb2e867a70bba28e7bfb52f43d7e35` | yes |
| PR #26 head `arena/af939eb1-pc5` | `170e611dcdfa3fe12e9abe58c2e60ee323aa461c` | yes |
| PR #27 head `arena/17f0c4f8-pc5` | `141f91f0ca192ca72972ca9afdbee8c86e6896c5` | yes |

| Property | Value |
|---|---|
| PR #26 | OPEN, not draft, **not merged** (`mergedAt: null`), base `main`, 54 files, +4719/−80 |
| PR #27 | OPEN, not draft, **not merged**, base `arena/af939eb1-pc5`, 67 files, +3608/−216 |
| Reviews / comments / requested changes | **0 / 0 / 0** on both; `reviewDecision: ""` |
| Ancestry | linear: `main` → `170e611` → `141f91f`; merge-base(`main`, PR27) = `main` |
| Commit counts | `main..26` = 15, `26..27` = 15, `main..27` = 30 |
| Combined tree | `merge(PR26, PR27)` = `5b452e261743d7b4e2d447233dc5de49546f363e` = `141f91f^{tree}` — **equal**, 0 conflicts |
| Combined diff | 83 files, +8197/−166, **0 deleted files** |
| GitHub Actions workflows | **0** — no CI exists |
| Check-runs / status on `141f91f` | **0** |
| Branch protection | all three branches `protected: false`, `enforcement_level: "off"` |
| Rulesets | `[]` (none) |
| CODEOWNERS | none |

**UI/API note:** `GET /branches/{b}/protection` returns HTTP 403 for this token, so protection was read from `GET /branches/{b}`, which reports `protected` and the required-status-check enforcement level. That is authoritative for presence/absence, though it does not enumerate every rule.

---

## B. Findings

### F1 — Stub raises instead of returning `EFI_OUT_OF_RESOURCES` under memory pressure
**Severity: Low.** **Status: unresolved (inherited; previously documented as L2).**

`tests/uefi_stub_firmware.py:277-281` (`alloc`), reached from the `svc_allocate_pages` `AllocateAnyPages` branch with no guard.

`alloc()` raises `StubError("stub pool exhausted")` when `address + size > limit`. Real firmware returns `EFI_OUT_OF_RESOURCES`, and the loader's `die("...-allocate")` path runs. **Reproduced:** shrinking the pool so the first page allocation cannot fit yields `StubError(stub pool exhausted)` with `reached_kernel=False` at 64 KiB, 4 KiB, and 0 bytes remaining, versus a clean boot with the normal pool.

**Can it conceal a production error?** Only in a narrow sense. It does **not** let a broken loader pass — every constrained run fails. But it fails *for the harness's reason, not the loader's*, so the loader's own allocation-failure handling is never exercised under these conditions. A test asserting merely "the boot did not complete" would pass for the wrong reason. That is a gap in *test expressiveness*, not a hidden production defect.

**The headroom figure quoted in prior documents is exaggerated — see F5.**

### F2 — The L1 mutation test duplicates the assertion it claims to guard
**Severity: Low.** **Status: new (found this review) — a limitation of the fix, not a defect in it.**

`tests/test_pagetables64.py:782-790`.

`UnusedPdTailMutationTests.test_stale_pd_tail_is_detected` re-implements the tail check inline (`for index in range(total, 2048): self.assertEqual(block.entry(index), 0)`) inside `assertRaises`. It therefore proves that *an assertion of that shape* rejects the mutant — not that the **shipped** `test_unused_pd_entries_are_zero_for_every_short_span` does. The shipped test is never invoked against the mutant.

**Does this make the guard vacuous?** No, and I tried to make it so. The mutant is genuinely caught by the shipped tests: running the real suite against the stale-tail mutant produces **13 failures across 4 test methods**, including three from `ShortSpanInitialisationTests`:

```
ShortSpanInitialisationTests.test_a_poisoned_tail_is_cleared_not_merely_ignored
ShortSpanInitialisationTests.test_all_six_pages_are_initialised_deterministically
ShortSpanInitialisationTests.test_unused_pd_entries_are_zero_for_every_short_span
UnusedPdTailMutationTests.test_the_mutant_anchor_is_present_and_unique
```

So the discrimination is real; the mutation test is a *second, independent* check rather than a proof about the first. Defence in depth, slightly weaker than its docstring implies.

**Attack I ran:** emptying `ShortSpanInitialisationTests.SPANS` silently disables all ten short-span tests *and* the mutation test's per-span loop — the suite still reports `43 tests OK` against the correct builder. With the mutant also applied, however, the run fails via the anchor guard (`test_the_mutant_anchor_is_present_and_unique`: "the builder's whole-block zeroing line has changed shape"). So the anchor guard is what prevents a silent no-op. Worth knowing; not a blocker.

### F3 — Tracked audit report describes a superseded state and does not mention the L1 fix
**Severity: Low.** **Status: new (this review).**

`AUDIT_PR26_PR27_2026-11-16.md`, tracked since `a9b383d`, would land on `main`.

The file **does** carry an explicit supersession section (`## 11. Remediation status (appended 2026-11-16, later the same day)`, line 525) with a finding→status→commit table marking D1 and D2 fixed. Its §1–§10 heading still reads *"Verdicts: PR #26 — REQUEST CHANGES (1 critical)… NEITHER is mergeable"*, which is historically accurate and explicitly framed as the record of what was found.

The genuine gap: **it predates `141f91f`.** It contains no reference to `141f91f`, L1, the vacuous loop, short spans, or `ShortSpanInitialisationTests` (verified: 0 matches for any of those terms). At the head it would land on, L1 is closed and the suite is 688 tests; the document describes a 676-test state with L1 implicitly open. It is not *wrong* — it is a dated record pointing forward to §11 — but there is no pointer from it to the L1 resolution.

**Correction to my own earlier working note:** I initially recorded "no supersession marker". That was wrong, caused by grepping for the wrong vocabulary. §11 exists and is explicit. Retracting.

### F4 — Review package understates what the tracked audit document is
**Severity: Low.** **Status: new (this review).**

`REVIEW_PACKAGE_PR26_PR27_2026-11-16.md:58` calls `AUDIT_PR26_PR27_2026-11-16.md` simply *"an analysis document"*.

It is more consequential than that: a **pre-fix audit whose headline verdict is REQUEST CHANGES, with two CRITICAL findings**. Landing it on `main` publishes a document whose first lines say the change is not mergeable, alongside the fix that resolved the findings. §11 makes it defensible, but the reviewer's consent should rest on an accurate description, not on "an analysis document". Recommended (not executed): state this accurately, or exclude the file.

### F5 — Headroom figure "~470×" in prior documentation is wrong; measured 99.9×
**Severity: Low.** **Status: new (this review); error in my own prior documents.**

`AUDIT_PR26_PR27_INDEPENDENT_2026-11-16.md` §F and `LANDING_STRATEGY_D_READINESS_2026-11-16.md` state "~35 pages against a 16 MiB pool (≈470× headroom)".

**Measured** from a real run: pool = 16,777,216 B (`0x1000000` *is the size*; the pool spans `0x02000000`–`0x03000000`, and an earlier draft of this line wrongly printed the size as the base address); consumed = `0x29000` (167,936 B) = **1.0%**; page allocations = 5 requests / 27 pages (110,592 B). Headroom = **99.9×**, not 470×. The "~35 pages" figure also overstates demand (actual: 27 pages).

Both statements live in untracked working-tree documents, so this does not affect `main`. Corrected here. F1's severity is unaffected — 100× is still ample — but the number should not be quoted as-is.

### F6 — No CI and no branch protection on any branch
**Severity: Medium (process, not code).** **Status: known limitation, unresolved.**

0 workflows; 0 check-runs; all three branches unprotected; no rulesets; no CODEOWNERS.

Consequence: **there is no automated gate whatsoever on `main`.** Combined with F7, the only control protecting `main` is human review. This is not a defect in the change; it is a property of the repository that raises the stakes of every other finding.

### F7 — The suite cannot distinguish the defective revision from the fixed one at PR #26
**Severity: Medium.** **Status: inherited; mechanism re-verified this review.**

PR #26 at `170e611` is green (612 tests, OK) while carrying both D1 and D2. The D2 defect lives *in the stub itself* at that revision, so the suite agrees with the bug.

Consequence: **a green suite is not evidence for this subsystem, and "PR #26 passes" must not be used to justify landing it alone.**

---

## C. D1 / D2 / L1 / L2 verdicts

### D1 — page-table hierarchy: **CORRECT — independently verified**

Wrote a fresh 4-level walker (`/tmp/adv/d1_oracle.py`) that reads raw entry bytes and does **not** call `pc5_pt_lookup` (that function is itself under review).

| Check | Result |
|---|---|
| Oracle vs current head | **PASS, 0 failures** |
| Oracle vs pre-fix `d0be617` | **1038 failures** — the oracle discriminates |
| All four PDPT entries installed, distinct PD targets, inside block | pass |
| PDPT entries carry no PS bit and no `PWT|PCD` (reserved on a table) | pass |
| PML4[1..511] and PDPT[4..511] zero | pass |
| Walk reaches exactly the returned count (2048 at full span) | pass |
| Identity: every leaf's physical base == its address | pass |
| Boundary: `SPAN-1` mapped; `SPAN` and `1<<32` not | pass |
| Short/unaligned spans 1 GiB, 2, 3 GiB, `2 MiB+0x1234`, `SPAN-0x1000` | all correct; tails zero |
| Refused spans write nothing | pass |
| Rounding contract (`span/2MiB` floor) | correct for 5 boundary cases |
| `pdpt_used` never exceeds 4 PD pages | verified for totals 1…2048 |

### D2 — UEFI `AllocateMaxAddress`: **CORRECT — adversarially probed**

`boot/uefi64/uefi64_main.c:248-271`.

| Property | Result |
|---|---|
| Input maximum non-zero and valid | `PC5_IDENTITY_LIMIT - 1` = `0xFFFFFFFF` |
| Last legal range accepted (`LIMIT-PAGE`, 1 page) | pass |
| One page more rejected | pass |
| Range ending exactly at limit accepted; starting at limit rejected | pass |
| Alignment enforced | pass |
| `pages == 0` and excessive counts rejected **before** any multiply | pass (2^52 and 2^64−1 rejected) |
| Overflow of the `last_page` expression impossible | max value < 2^64 |
| Lying firmware (crossing, misaligned, zero, above limit) rejected | pass |
| All six call sites check failure | pass (`:497, 502, 517, 594, 610, 633`) |
| Boot-services calls after `ExitBootServices` | **0, instrumented** |
| Arithmetic probe | **PASS, 0 failures, 11 groups** |

**Stub fidelity:** the stub genuinely models the contract rather than returning a convenient address — all three `EFI_ALLOCATE_*` modes, zero bound → `EFI_OUT_OF_RESOURCES`, whole-range ceiling via `alloc(size, ceiling=…)`, `AllocateAddress` → `EFI_NOT_FOUND`, plus fault switches (`ignore_max_address`, `allocate_return`, `allocation_limit`). Its one real divergence is F1.

### L1 — mutation-test correction: **CLOSED, with a documented caveat (F2)**

| Requirement | Result |
|---|---|
| Former empty loop actually corrected | yes — bound derived from `self.mapped`, plus an explicit "nothing unused at full span" assertion |
| No assertion removed or weakened | **verified**: the 3 deleted lines are 2 comments + the loop header; **0** deleted lines contain `assert`/`self.fail` |
| No skip/xfail introduced | verified (the single regex hit is prose: "skips the tail") |
| Short-span tail tested | yes, 4 spans incl. 1 GiB / 3 GiB / 2 MiB+4 KiB / SPAN−4 KiB |
| Mutant leaves only the PD tail stale | verified: 1536 stale tail entries; `PML4[1..511]` and `PDPT[1..511]` both zero |
| New tests fail against the mutant | **reproduced**: 13 failures / 4 methods |
| Original D1 defect still detectable | **reproduced**: 40 failures / 17 methods |
| Asserts distinguish valid addresses from stale entries | yes — word-level; the guard byte-scan is outside the block only |
| Same block + rounded effective span used | yes (lines 646-665; 617-635) |

### L2 — stub exhaustion: **CONFIRMED, Low, does not block**

---

## D. Test and release evidence

**Independently reproduced by me this session** (real git worktree at `141f91f`; `SOURCE_DATE_EPOCH=1700000000` unless noted):

| Gate | Result |
|---|---|
| `make all` | exit 0 |
| `make -s check` | **exit 0 — `Ran 688 tests`, `OK`** |
| `make verify` | exit 0 — `VALID: build/pc5.iso sha256=793341cf…` |
| `make stubtest64` | exit 0 — `22/22`, `PC5-64-DONE` |
| `make stubtest` | exit 0 — `58/58` |
| `make boot-test` | exit 0 — **only from `/home/user/pc5`, which has `node_modules`**; exit 2 from a bare worktree (`v86-system` absent). Environment, not code. |
| `make roadmap` | exit 0 — 35 milestones |
| `make docs-check` | exit 0 — 47 files |
| `make brand-check` | exit 0 — 11 assets |
| `make presentation-check` | exit 0 — 6 figures |
| `test_pagetables64` | 43 tests OK |
| Post-`ExitBootServices` service calls | 0 (instrumented) |

**Not reproduced / not available — reported, not verified:**

| Item | Status |
|---|---|
| OVMF / QEMU execution | **NOT TESTED** — neither present; `ovmf-test` correctly `not-tested` (exit 2) |
| Physical hardware | **never run** |
| Genuine Sony firmware / system software | **never run** |
| GitHub CI | **does not exist** |

**Pinned-epoch artifact comparison (actual hashes):**

| Artifact | `d0be617` | `141f91f` | Result |
|---|---|---|---|
| `pc5.iso` | `793341cf100f7ed4…` | `793341cf100f7ed4…` | **BIT-IDENTICAL** |
| `KERNEL64.BIN` | `032183f8132dea26…` | `032183f8132dea26…` | **BIT-IDENTICAL** |
| `BOOTX64.EFI` | `5def86ef770bed48…` | `6cda14a3c0f93365…` | **DIFFERS** (the fixed loader) |

Release isolation confirmed: `release-assets` stages only `pc5.iso` + metadata; `BOOTX64.EFI` is **not** embedded in `pc5.iso` (verified by byte search). The 64-bit path remains outside the release artifact.

**Harness artifact I hit and corrected:** an initial `make -s check` in a bare tarball gave 6 errors — `git ls-files` exit 128, because there was no `.git`. Not a code defect; re-ran clean in a worktree.

---

## E. Mission and documentation alignment

| Check | Result |
|---|---|
| PC5 remains a PC-side platform/compatibility project, not a replacement OS or fake PS5 UI | consistent with README, roadmap, `docs/KNOWN_LIMITATIONS.md` |
| Engineering/recovery tools distinguished from Sony UI/services | yes — the one hardware-mentioning doc reads "IMPLEMENTED — **NOT TESTED on physical hardware**", correct labelling |
| PUP not misrepresented as analysed | **correct** — `PS5UPDATE.PUP` is a 135-byte Git LFS pointer; no document claims analysis |
| No unsupported genuine-Sony-execution claim | none found |
| Security/legal boundaries explicit | yes (`docs/SECURITY.md`; LFS pointer retained rather than fetching firmware) |
| Evidence labels distinguish implementation / tests / stubs / emulation / hardware / Sony | yes — validation records use `not-tested` rather than overclaiming |

**Stale or misleading documentation found:** F3 (audit report predates L1 closure), F4 (review package understates it), F5 (headroom figure wrong in two untracked documents).

---

## F. Landing recommendation

**Strategy D remains technically correct.** Re-derived independently: `merge(PR26 head, PR27 head)` yields exactly `141f91f^{tree}` (`5b452e26…`), 0 conflicts, so after step 1 PR #26's content is the audited tree with no re-derivation.

Sequence, unchanged, and mandatory in order:

1. `gh pr merge 27 --merge` (merge commit — squash would collapse 15 reviewed commits; rebase would rewrite the SHAs this review is anchored to).
2. **Verify** the base branch tree == `5b452e26…` and that PR #26 now shows 83 files / +8197 / −166 (**not** 54 / +4719 / −80).
3. `gh pr merge 26 --merge`.

**On the tracked audit report:** it should **not** be silently removed — it is an honest append-only record, and removing it would be the history-rewriting this project has consistently refused. But landing it unaltered publishes a REQUEST CHANGES / "NEITHER is mergeable" verdict on `main` with no forward pointer to the L1 fix. **Recommendation: retain it, but add a one-line forward reference to `141f91f` / L1 closure in §11 before landing.** That is a documentation change requiring a new commit — **not performed here**, and it is the reviewer's call. Excluding the file is also defensible. What is *not* defensible is landing it while describing it as merely "an analysis document".

**Blockers:** one, and it is not technical — **B1: an independent human approving review from an identity other than the author.** There are 0 reviews. Because there is no CI and no protection (F6), that review is the entire control. I cannot supply it: I authored the commit under review.

---

## G. Final verdict

# APPROVE WITH DOCUMENTED LOW-SEVERITY LIMITATIONS

**Basis, stated conservatively:**

- D1 and D2 are **independently verified correct**, with an oracle that discriminates (1038 failures on the pre-fix code, 0 on the head) and an adversarial arithmetic probe (0 failures across 11 groups).
- L1 is **genuinely closed** — reproduced, not inspected: 13 failures against the stale-tail mutant, 40 against the original D1 defect, no assertion removed.
- L2 is **confirmed Low** and does not conceal a production defect; reproduced under real pool pressure.
- Five **Low** findings (F1–F5) are documented above rather than suppressed, including two errors in my own prior work. None blocks landing.
- Two **Medium** findings (F6 no CI/protection; F7 green-suite blindness) are properties of the repository and the test strategy, not regressions introduced by this change. They raise the importance of human review; they do not argue against this code.

**I did not select `APPROVE FOR INDEPENDENT HUMAN SIGN-OFF`** because the low-severity limitations are real, reproducible, and remain open — approval should carry them explicitly, not implicitly. **I did not select `REVIEW INCOMPLETE`** because every critical claim the brief required was independently established by execution rather than accepted from the implementer.

**This is not a merge approval.** It is a technical assessment, written by the author of the change, that the change is sound and its documented limitations are bounded. Sign-off requires a human who did not write it.
