# Remediation report — findings F1–F5


| | |
|---|---|
| **Tested revision** | `0a7eaa07222df5b6182050933c650e8b74efde94` |
| **Evidence type** | Implementer-reported, with each claimed result re-derived by the author. |
| **Historical?** | Yes — describes the F1–F5 pass; superseded by the follow-up commit for F1 docstring/F2 integrity. |
| **Author** | The PC5 implementer. **Not an independent verification.** |

**Date:** 2026-11-16 · **Branch:** `arena/17f0c4f8-pc5` · **Commit:** `0a7eaa0`

| | SHA |
|---|---|
| Starting (verified, byte-identical to working tree) | `141f91f0ca192ca72972ca9afdbee8c86e6896c5` |
| Final (pushed) | `0a7eaa07222df5b6182050933c650e8b74efde94` |
| `main` (unchanged) | `8aab6d3864bb2e867a70bba28e7bfb52f43d7e35` |
| PR #26 head (unchanged) | `170e611dcdfa3fe12e9abe58c2e60ee323aa461c` |

Both PRs remain **OPEN**, `mergedAt: null`, **0 reviews, 0 comments**. Nothing was merged,
approved, or commented on. `main` was not touched. The only change is one commit on the
existing session branch, which PR #27 already tracks.

## Files changed

| File | Why |
|---|---|
| `tests/uefi_stub_firmware.py` | F1 — `alloc()` returns `None`; services return `EFI_OUT_OF_RESOURCES`; `alloc_or_raise()` added for scaffolding |
| `tests/boot64_stub.py` | F1 — 5 scaffolding sites converted; `pool_reserve` option added |
| `tests/test_boot64_stub.py` | F1 — `PoolExhaustionTests` (4 tests) |
| `tests/test_pagetables64.py` | F2 — `EXPECTED_UNUSED`, `ShortSpanCoverageIntegrityTests` (4 tests), shipped-logic mutation test |
| `AUDIT_PR26_PR27_2026-11-16.md` | F3 — §12 dated forward pointer, appended |

**No production file changed.** `git diff --name-only` filtered to non-`tests/`, non-`.md`
is empty. `boot/`, `kernel/`, `Makefile` and the entire 32-bit shipping path are
byte-identical to `141f91f`. The 64-bit path (`boot/uefi64/`) is likewise untouched —
F1 is a harness-fidelity fix, not a product fix.

## Resolution table

| # | Finding | Status | Evidence |
|---|---|---|---|
| F1 | Stub raised a Python exception where firmware returns a status | **Fixed** | 4/4 tests error on old stub, pass on new; loader reaches its own fail path |
| F2 | Short-span coverage could vanish silently; mutation test duplicated its assertion | **Fixed** | Emptying `SPANS` now yields 4 failures (was green); mutation test runs shipped methods |
| F3 | Tracked audit predates L1 closure, no forward pointer | **Fixed** | §12 appended, 44 insertions / 0 deletions |
| F4 | Review package called the audit "an analysis document" | **Fixed** | Replaced with an accurate description |
| F5 | "~470×" headroom claim wrong in two untracked reports | **Fixed** | Corrected to measured 151.7× / 99.9× with method stated |

### F1 detail

`alloc()` now returns `None` and appends to `self.exhaustions`. `svc_allocate_pages`
(both `AllocateAnyPages` and `AllocateMaxAddress` branches) and `svc_allocate_pool`
return `EFI_OUT_OF_RESOURCES`. All 10 harness-scaffolding call sites — 5 in
`uefi_stub_firmware.py`, 5 in `boot64_stub.py` — use `alloc_or_raise()`, preserving the
original diagnostic text ("wanted N bytes, limit X, next free Y"). Production allocation
behaviour was not altered to accommodate the stub.

The discriminating test is `test_the_loader_takes_its_own_failure_path_under_real_exhaustion`.
It constructs a machine with `pool_reserve=0`, applied *after* `_install_firmware_paging()`
so the firmware's own structures exist, then boots. The loader must reach
`PC5-64-LOADER-FAIL=bootinfo-allocate` — its intended error path — rather than dying on a
Python exception. **This marker was confirmed from actual test output, not assumed.**

**Discrimination, measured:** reverting only `uefi_stub_firmware.py` and `boot64_stub.py`
to `141f91f` while keeping the new tests → **4 errors / 4 tests**. With the fix → **4 pass**.

### F2 detail

Two distinct defects were reported and both are addressed.

*Silent disappearance.* Every short-span test iterated `SPANS`, so an empty tuple made them
all vacuously pass. `EXPECTED_UNUSED` now pins, per span, how many unused PD entries the span
must leave (1536 / 512 / 2047 / 1), and `ShortSpanCoverageIntegrityTests` asserts the tuple is
non-empty, matches expectations exactly, crosses ≥3 GiB regions, and that every span leaves a
non-empty tail. **Attack re-run:** emptying `SPANS` on the current code → **4 failures**
(2 integrity tests plus 2 subTests of the shipped-logic mutation test); on `141f91f` the same
attack left the suite green.

*Duplicated assertion.* The old mutation test re-implemented the tail comparison inline. The
replacement compiles the mutant, binds `ShortSpanInitialisationTests` to it via the module's
library cache, and calls the **shipped** test methods, requiring them to raise. If the shipped
assertion is ever weakened, this test fails — which is exactly what happens in the
empty-`SPANS` attack above. Word-level checks (`block.entry(index)`) are preserved unchanged.

### F3 / F4 detail

The audit's §1–§11 are untouched. §12 states the sections must be read in the past tense,
names `141f91f` and the L1 test-only correction, lists where remediation evidence lives, and
says explicitly: *"It does not withdraw the verdicts in the header of this document: at
`d0be617` those verdicts were correct."* R2/R4 remain NOT TESTED.

The review package now describes the file as a historical pre-remediation audit with an
original REQUEST CHANGES verdict and two critical findings, notes §11's supersession, and
records that §12 resolves the L1 predating.

### F5 detail

**Method:** instrument a real stub boot, read `pool_next - POOL_BASE` at completion, and
separate the loader's own requests (recorded in `machine.allocations`) from harness scaffolding.

| Quantity | Measured |
|---|---|
| Pool (`0x02000000`–`0x03000000`) | 16,777,216 B = 4,096 × 4 KiB pages |
| Loader requests | 5 requests, **27 pages**, 110,592 B |
| Harness scaffolding | 14 pages, 57,344 B |
| Total consumed | 41 pages, 167,936 B = 1.00 % of pool |
| Headroom, loader demand | **151.7×** |
| Headroom, total consumption | **99.9×** |

The reported "~35 pages (≈470×)" is both mismeasured and arithmetically impossible:
470 × 35 = 16,450 pages exceeds the pool's entire 4,096-page theoretical capacity.
Measured demand is now stated separately from theoretical capacity in all three occurrences.
**No tracked file carried this claim** — it existed only in untracked reports, so `main` needs
no correction.

## Tests added (9)

| Test | Defect it discriminates |
|---|---|
| `PoolExhaustionTests` ×4 | Stub raising instead of returning a status; loader's own failure path |
| `ShortSpanCoverageIntegrityTests` ×4 | Vacuous short-span coverage; silent `SPANS` thinning |
| `UnusedPdTailMutationTests.test_the_shipped_short_span_tests_reject_the_mutant` | Mutation test that guarded a copy rather than the shipped assertion |

No assertion was weakened. No test was skipped to make a suite pass.

## Regression

All commands run at `0a7eaa0`, clean tree, exit codes as shown.

| Command | Exit | Result |
|---|---|---|
| `make all` | 0 | builds |
| `make -s check` | 0 | **Ran 697 tests — OK**, 0 failures, 0 real skips |
| `make -s stubtest` | 0 | PASSED: 58/58 against the stub firmware |
| `make -s stubtest64` | 0 | pass |
| `make -s verify` | 0 | `[PASS]` BUILDINF.TXT matches build info |
| `make -s roadmap` | 0 | pass |
| `make -s docs-check` | 0 | pass |
| `make -s brand-check` | 0 | pass |
| `make -s presentation-check` | 0 | pass |
| `make -s boot-test` | 2 (skip 77) | **NOT RUN** — `node_modules` absent; pre-existing |

Test count moved 688 → 697 (+9), matching the tests added exactly.

## Re-verification of prior defects

| Check | Result |
|---|---|
| D1 original defect (`d0be617` pagetables.c) | **41 failures** — still loudly rejected (was 40; one more from the new integrity tests) |
| L1 stale-tail mutant (shipped mutant definition) | **13 failures / 4 methods** — reproduces the prior result exactly |
| F2 empty-`SPANS` attack | **4 failures** (previously green) |
| F1 old-stub revert | **4 errors / 4 tests** |

## Release isolation

Cleaned `build/` and rebuilt both a pristine `141f91f` tree and the edited tree with
`SOURCE_DATE_EPOCH=1700000000 BUILD_ID=141f91f` (both must be pinned — `BUILD_ID` defaults to
`git rev-parse --short HEAD`, which differs in a tree without `.git`):

```
pristine:  7d756cb6125266e14651371237723dcd19aef36e633d73eb31a8a57d98665042
edits:     7d756cb6125266e14651371237723dcd19aef36e633d73eb31a8a57d98665042
```

Per-member: `BUILDINF.TXT`, `KERNEL.BIN`, `README.TXT` all IDENTICAL. **The release artifact is
unaffected by this pass**, which is the expected outcome of a tests-and-docs-only change.

## Remaining limitations

1. **The review was not independent.** I wrote the prior audits, the `141f91f` commit, and now
   its remediation. The adversarial review named this and it still holds. A human still needs
   to do the independent sign-off.
2. **`ovmf-test` never runs here.** It exits 3 (22 required matrix rows, none executed) because
   OVMF and QEMU are absent; on the pristine tree it exits 2. The UEFI integration gate is
   **NOT passed**.
3. **`boot-test` did not run** — `node_modules` absent.
4. **F1 is proven at stub level only.** It shows the loader handles a status the stub returns.
   It does not show behaviour under real firmware, OVMF, QEMU, or hardware.
5. **`pool_reserve` is a harness affordance.** It models "firmware has nothing left" without a
   16 MiB allocation. It is a test hook, not a claim about firmware.
6. **No CI exists** in this repository, so none of the above was machine-verified by anyone else.
7. **F5's numbers are one machine's measurement** of one boot configuration.
8. Three of the four review reports remain **untracked**, so their corrections live only in the
   working tree unless the user chooses to commit them.

## State

Branch `arena/17f0c4f8-pc5` = `0a7eaa0`, pushed, in sync with the remote.
PR #27 head = `0a7eaa0`, base `arena/af939eb1-pc5`, OPEN, not draft, 0 reviews, 0 comments.
PR #26 unchanged at `170e611`, OPEN, 0 reviews, 0 comments. `main` unchanged at `8aab6d3`.
No finding was left unresolved; no PR was merged, approved, or commented on.
