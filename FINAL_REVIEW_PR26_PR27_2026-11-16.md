# Final adversarial review — PR #26 + PR #27


| | |
|---|---|
| **Tested revision** | `0a7eaa07222df5b6182050933c650e8b74efde94` |
| **Evidence type** | Author-reproduced review. Self-discloses that it is not independent. |
| **Historical?** | Yes — its verdict drove the follow-up branch. |
| **Author** | The PC5 implementer. **Not an independent review.** |

**Date:** 2026-11-16 · **Reviewed revision:** `0a7eaa07222df5b6182050933c650e8b74efde94`

## Independence disclosure — read this first

**I am not an independent reviewer of this stack.** I authored the F1–F5 remediation under
review (`0a7eaa0`), the remediation report, and — acting on prior instructions — the D1/D2
fixes, the L1 correction, and the earlier audits. In this session I also *wrote* the five
review documents that F3/F4/F5 refer to.

What that means concretely:

- Everything below that I verified was **reproduced from source and executed by me**, not
  taken from the remediation report. Where I re-ran the implementer's own claimed results,
  I show the commands and markers.
- That protects against **careless or false claims**. It does **not** provide the
  independent judgment the brief asks for, because the same author chose what to test,
  what to mutate, and what "fixed" means.
- Several findings below are findings **against my own work**. That is the limited value
  this document can offer.

**This review cannot substitute for human sign-off, and I do not offer it as one.**

---

## 1. Live repository state (verified via `gh` and `git ls-remote`)

| | Value |
|---|---|
| `main` | `8aab6d3864bb2e867a70bba28e7bfb52f43d7e35` |
| PR #26 head | `170e611dcdfa3fe12e9abe58c2e60ee323aa461c`, base `main` |
| PR #27 head | `0a7eaa07222df5b6182050933c650e8b74efde94`, base `arena/af939eb1-pc5` |
| Both PRs | **OPEN**, not draft, `mergedAt: null`, **0 reviews, 0 comments** |

All three reported SHAs are **correct**. `mergeable=MERGEABLE / CLEAN` on both — which is a
statement about conflict-free merging, **not technical approval**.

**Ancestry** (verified with `git merge-base --is-ancestor`):
`8aab6d3` → `170e611` → `0a7eaa0`. Linear, no divergence, no force-push, no rewritten history.

**Combined diff `main..PR27`:** 83 files, +8512 −178.

**Governance:**

| Check | Result |
|---|---|
| GitHub Actions workflows | **0** (`.github/workflows/` does not exist at head) |
| Check-runs on PR27 head | **0** |
| Combined commit status | `pending` (i.e. nothing reported) |
| Branch protection | **`false` on all three branches** |

There is **no CI and no branch protection**. Every "gate passed" claim resting on this stack
is a self-reported local run. Nothing in this review was machine-verified by a third party.

**Method note:** my sandbox working tree was stale (at `main` with 64 dirty entries). Rather
than reset it — the task forbids edits — I reviewed `0a7eaa0` from git objects, in a
`git archive` export (`/tmp/rev`) and detached worktrees (`/tmp/wt`, `/tmp/wt2`) at the exact
SHA. No file in the repository was modified, no commit, no push, no PR action.

---

## 2. D1 — 4 GiB identity map — **PASS**

I did not rely on `pc5_pt_lookup`. I compiled `boot/uefi64/pagetables.c` standalone and wrote
an independent walker that follows **PML4 → PDPT → PD using absolute physical addresses**,
resolving each pointer as `physical − block_base` and verifying it lands at the expected page
offset inside the 6-page block. The block was pre-filled with `0xA5` so any uncleared byte
would surface.

| Case | Returned | Expected | Structure |
|---|---|---|---|
| full 4 GiB | 2048 | 2048 | PASS — 4/4 PDPT entries present, all 2048 leaves = `(i*2MiB)\|0x83` |
| 3 GiB | 1536 | 1536 | PASS — 3 PDPT entries, 4th zero |
| 1 GiB | 512 | 512 | PASS — 1 PDPT entry, 3 zero |
| exactly 1 page | 1 | 1 | PASS |
| 1 page + 1 byte | 1 | 1 (rounds **down**) | PASS |
| 4 GiB − 1 | 2047 | 2047 | PASS — leaf 2047 stale-tail-free, no entry past 2047 |
| 511 / 512 / 513 pages | 511 / 512 / 513 | same | PASS — PDPT crossing correct at 513 |
| 2 pages | 2 | 2 | PASS |

**Deterministic initialisation:** confirmed — every span leaves zero non-zero bytes anywhere
in the block beyond the installed leaves, starting from an `0xA5`-poisoned buffer. This is
verified byte-wise across the whole 24 KiB, not just at entry granularity.

**Invalid inputs fail safely and write nothing:**

| Input | Returned | Block untouched |
|---|---|---|
| `span=0`, `4GiB+1`, `5GiB` | 0 | yes |
| base `+1`, `+0x800`, `+0xfff`, null | 0 | yes |

Two early runs of my own walker reported FAIL. **Both were bugs in my harness**, not the code:
first a non-page-aligned buffer (correctly rejected with `ret=0`), then treating physical
addresses as block-relative offsets. I record this because it cuts both ways — the code
survived two different wrong harnesses, but a careless reviewer would have published a false
positive.

## 3. D2 — `AllocateMaxAddress` semantics — **PASS**

- The bound is written **before** the call as `PC5_IDENTITY_LIMIT − 1` (input parameter is
  the highest permitted byte), not passed as `0`. `0` is correctly treated as unsatisfiable.
- `pages == 0 || pages > PC5_IDENTITY_LIMIT/4096` is rejected **before** any multiply, closing
  the `pages * 4096` wrap site.
- The returned range is re-validated on the way out: 4 KiB alignment, `last_page` computed in
  **page units** so the check itself cannot wrap, plus a `LOAD_LIMIT` (0xFFFFFFFF) cap.
- All five allocation sites handle failure (`bootinfo`, `pagetable`, `stack`, `memmap`,
  `memmap-copy`) and each has a distinct `die()` marker.
- `alloc_pool()`'s only caller checks for null and `continue`s to the next handle.

**No boot-services call after `ExitBootServices`:** verified by enumerating every `g_bs->`
use against the `exit_boot_services` call site. `memmap_and_exit` copies the map and calls
`ExitBootServices` **last**; all `g_bs->` uses are at lines 211–511, before it. The one
apparent post-exit reference (line 588, `set_watchdog_timer`) is inside `efi_main64`
(entry at 579), which runs *before* the map acquisition — a line-number artifact, not an
ordering bug.

## 4. L1 / F2 — test integrity — **PASS, with one hardening gap**

Independently reproduced, then extended with mutations the implementer did not report.

| Attack | Result |
|---|---|
| `SPANS = ()` (empty) | **4 failures** — reproduces the report |
| `SPANS` thinned to one span | **2 failures** |
| `EXPECTED_UNUSED` wrong for one span | **1 failure** |
| Both `SPANS` and `EXPECTED_UNUSED` thinned consistently | **1 failure** |
| **My own:** drop both rounding-boundary spans, keep 3 GiB regions, keep maps consistent | **OK — not caught** |

**The mutant is genuinely tail-only.** I compiled it and compared against the fixed library at
4 GiB / 1 GiB / 2 MiB+4 KiB: PDPT structure is **identical** (4/4, 1/4, 1/4) and return values
match; only unused-tail entries differ (0 vs 1536 vs 2047). It cannot be confused with D1.

**Shipped logic really rejects it.** Applying the mutant to `pagetables.c` and running the
class suite gives **13 failures across 4 methods** — including
`test_unused_pd_entries_are_zero_for_every_short_span` and the poisoned-tail test. The
retargeted mutation test binds `ShortSpanInitialisationTests` to a freshly compiled mutant and
requires the **shipped** methods to raise, so it fails if the shipped assertion is weakened.

**D1 remains detectable:** the shipped suite against `d0be617`'s `pagetables.c` yields
**41 failures**.

**Canary cannot be confused with a valid PA:** the tail check compares `block.entry(index)`
— an 8-byte **word** — against `0`, so a physical address that incidentally contains `0xA5`
bytes in one of its bytes is not misclassified. Word granularity is preserved.

### Gap (low severity, real)

`test_the_collection_covers_more_than_one_gigabyte` requires ≥3 distinct GiB regions but does
not require the two spans that exercise **rounding and tail boundaries**
(`(2<<20)+0x1000`, `(4<<30)-0x1000`). If a future change thins both `SPANS` and
`EXPECTED_UNUSED` consistently and keeps three whole-GiB spans, the suite stays green.

I tested whether this is exploitable: I applied a **round-up-instead-of-round-down** mutant to
`pagetables.c` and compared. Full span set → **12 failures**; thinned set → **2 failures**.
So the mutant is *still caught* (the poisoned-tail and determinism tests do the work), but
discriminating power drops by 10 failures. **This is a hardening weakness, not a live defect.**
Recommended fix: pin the required span set as a literal, or assert that a rounding span and a
tail-1 span are both present.

## 5. F1 — UEFI exhaustion semantics — **PASS**

- Both real service boundaries return `EFI_OUT_OF_RESOURCES`: `svc_allocate_pages`
  (`AllocateMaxAddress` and the any-pages branch, with an explicit `requested == 0` guard) and
  `svc_allocate_pool`. `alloc()` returns `None` and records `(size, limit, next_free)` in
  `self.exhaustions`.
- Harness scaffolding is separated: `alloc_or_raise()` is used only for the stub's own fixed
  structures and re-raises `StubError` with the original diagnostic.
- **Every `alloc()` caller handles `None`** — enumerated all four in
  `uefi_stub_firmware.py` (lines 307, 785, 789, 895). No caller dereferences a `None`.
- **The marker comes from real loader execution.** Booting with `pool_reserve=0` produced, from
  the actual serial trace:
  `PC5-64-LOADER-BANNER …` then **`PC5-64-LOADER-FAIL=bootinfo-allocate`**,
  `trace.exited = False`, `exhaustions = 1`, kernel entry never reached. The test asserts the
  marker is present *in the loader's own output*, not that an exception occurred.
- **The old stub fails the new tests:** reverting `uefi_stub_firmware.py` + `boot64_stub.py` to
  `141f91f` while keeping the new tests gives **4 errors / 4 tests**; the other 31 tests still
  pass, proving the revert was valid and the discrimination is specific.
- No test bypasses the service interface: the exhaustion tests call `svc_allocate_pages` /
  `svc_allocate_pool` directly.

### Nits found

1. `alloc()`'s docstring references a function `alloc_pages_or_status()` **that does not
   exist**. Stale documentation inside the fix.
2. Three test helpers call `machine.alloc(8)` and assume non-`None`
   (`test_boot64_stub.py` 351, 357, 415). They cannot fail in practice because the machine is
   fresh, but they would produce a confusing `TypeError` rather than a clear assertion if a
   future change reused a starved machine. Cosmetic.

## 6. F3 — tracked audit — **PASS**

`AUDIT_PR26_PR27_2026-11-16.md` **is tracked** at the head. §12 is present at line 561; the
committing diff is **+44 / −0**, so §1–§11 are untouched. The original verdict
(*"NEITHER is mergeable…"*) and the two critical findings remain. §12 names `141f91f` three
times, states the audit describes an earlier state, and explicitly says it **"does not
withdraw the verdicts"** — the historical findings are not implied to have been invalid.

## 7. F4 / F5 — **DOCUMENTED BUT NOT DELIVERED**

**This is the most consequential finding of this review.**

| Document | Status at `0a7eaa0` |
|---|---|
| `AUDIT_PR26_PR27_2026-11-16.md` | **TRACKED** |
| `REVIEW_PACKAGE_PR26_PR27_2026-11-16.md` | **ABSENT** — local untracked file only |
| `AUDIT_PR26_PR27_INDEPENDENT_2026-11-16.md` | **ABSENT** |
| `LANDING_STRATEGY_D_READINESS_2026-11-16.md` | **ABSENT** |
| `ADVERSARIAL_REVIEW_PR26_PR27_2026-11-16.md` | **ABSENT** |
| `REMEDIATION_REPORT_F1_F5_2026-11-16.md` | **ABSENT** |

The F4-corrected review package and both F5-corrected reports **exist only in my sandbox**.
A reviewer fetching this branch sees none of them and **cannot verify F4 or F5 at all**. The
remediation report — the document that explains what F1–F5 were and how each was closed — is
likewise invisible.

**F5's arithmetic is correct**, and I verified it independently rather than copying it:

| Quantity | My measurement |
|---|---|
| Pool `0x02000000`–`0x03000000` | 16,777,216 B = 16 MiB = 4,096 pages |
| Loader requests | 5 requests, **27 pages**, 110,592 B |
| `run()` scaffolding | 57,344 B |
| Total consumed | 167,936 B = **41.00 pages** = 1.00 % |
| Headroom (loader demand) | **151.7×** |
| Headroom (total) | **99.9×** |

All four figures reproduce exactly. The original "~35 pages (≈470×)" is arithmetically
impossible — 470 × 35 = 16,450 pages exceeds the 4,096-page capacity.

**But:** the corrections state the numbers as **≈152× / ≈100×** (roundings), not 151.7 × /
99.9 ×; the raw "~470×" string still appears **inside the correction text** in both documents
(quoted as the thing being corrected — defensible, but it means a naive grep still finds it);
and `LANDING_STRATEGY_D_READINESS` **never contained the claim** (0 matches in the preserved
copy), so the review's statement that it did is itself inaccurate.

**No tracked file carries the headroom claim**, so `main` is not polluted. The defect is
confined to documents that were never delivered.

## 8. Regression and release isolation (reproduced at the exact SHA)

Run in a detached worktree at `0a7eaa0`:

| Command | Exit | Result |
|---|---|---|
| `make all` | 0 | builds |
| `make -s check` | 0 | **Ran 697 — OK (skipped=2)**, reproduced 6× |
| `make -s stubtest` | 0 | PASSED: 58/58 |
| `make -s stubtest64` | 0 | 64-BIT STUB FIRMWARE OK: 22/22 |
| `make -s verify` | 0 | `[PASS]` |
| `make -s roadmap` | 0 | pass |
| `make -s docs-check` | 0 | pass |
| `make -s brand-check` | 0 | pass |
| `make -s presentation-check` | 0 | pass |
| `make -s boot-test` | 2 (77) | **NOT RUN** — `node_modules` absent |
| `make -s ovmf-test` | 3 | **NOT PASSED** — 22 required rows, none executed; no OVMF/QEMU |

**Explicit skips (2):** `test_the_real_esp_is_not_firmware` (`build/efi.img not built`) and
`test_built_iso_verifies` (`build/pc5.iso not built`). Both are build-artifact guards, not
suppressed assertions.

**Test-count attribution verified:** F1 contributes 4, `ShortSpanCoverageIntegrityTests` 4, and
the mutation class went 2 → 3 (+1) = **+9**, i.e. 688 → 697. The arithmetic holds.

### One unexplained observation

My **very first** `make -s check` in a cold worktree failed with
`FAILED (failures=1, skipped=2)`. Six subsequent runs — including two fresh cold worktrees and
`make`-driven runs — passed. I could not reproduce it and did not capture the failing test name.
**I am recording it rather than explaining it away.** A single unreproducible failure across
seven runs is consistent with a flaky or environment-sensitive test, and that possibility has
not been eliminated.

### Release isolation — PASS, with inputs explicitly controlled

`BUILD_ID` defaults to `git rev-parse --short HEAD`, so comparing builds without pinning it
compares different inputs. With **both** `SOURCE_DATE_EPOCH=1700000000` and
`BUILD_ID=141f91f` pinned, clean builds of `main`, `141f91f`, and `0a7eaa0` all produce:

```
pc5.iso    7d756cb6125266e14651371237723dcd19aef36e633d73eb31a8a57d98665042
mbr.bin    d07ec2ba3aeb06b2…
loader.bin f121b34a7ebc0e7d…
kernel.bin 7e1552f0a30e127e…
efi.img    9106b561ea55c7f8…
```

All five artifacts identical across all three trees. **The 64-bit path changes no shipping
binary** — consistent with it being a new, not-yet-wired directory (`boot/uefi64`, `kernel64`,
+2261 lines with no `Makefile` integration into the ISO).

## 9. Mission alignment — PASS

No tracked file claims a replacement OS, a Sony UI/shell, a simulated setup experience, genuine
Sony firmware execution, PUP payload analysis, or physical/OVMF verification. The only grep hit
for a replacement-OS claim is `tests/test_product_identity.py:99`, which **asserts the absence**
of such a claim. Explicit non-claims are present in `README.md`, `docs/GATES.md`,
`ci/README.md`, and the audit.

## 10. Remaining blockers

| # | Blocker | Severity |
|---|---|---|
| B1 | **F4/F5 and the remediation report are undelivered** — no reviewer can verify them | **High for the review process** |
| B2 | **No CI, no branch protection, 0 check-runs** — every gate claim is self-reported | **High** |
| B3 | **No independent human sign-off exists** — 0 reviews on both PRs | **High** |
| B4 | `ovmf-test` never executed (no OVMF/QEMU) — UEFI integration gate NOT passed | Medium |
| B5 | `boot-test` never executed (no `node_modules`) | Medium |
| B6 | Never run on physical hardware or genuine Sony firmware | Medium (acknowledged) |
| B7 | F2 span-set guard does not pin boundary spans (hardening gap, §4) | Low |
| B8 | Stale `alloc_pages_or_status()` docstring; 3 test helpers assume non-`None` | Low |
| B9 | One unreproducible `make check` failure (§8) | Low, unexplained |
| B10 | Dropping `SPANS` boundary spans goes undetected if maps are edited consistently | Low |

## 11. Landing strategy (Strategy D) — still valid, **not executed**

1. Merge PR #27 into `arena/af939eb1-pc5` (PR #26's base) — ancestry already linear, so this is
   mechanically clean. `--merge` preserves the 16 commits.
2. Re-verify the resulting PR #26 tree (expect 83 files, the 697-test suite, and the same ISO).
3. Merge PR #26 into `main`.

**I merged nothing.** Strategy D remains sound in shape, but executing it before **B1–B3** are
resolved would publish a REQUEST CHANGES audit and an unverifiable remediation claim while
neither CI nor a human has signed off.

---

## 12. Verdict

**`DO NOT APPROVE — TECHNICAL FINDINGS REMAIN`**

Not because the code is wrong. On the evidence I could reproduce, **D1, D2, L1, F1, F2, and F3
all hold up**, and F5's arithmetic is correct. The blockers are findings about the *delivery*,
not the implementation:

1. **F4 and F5 are not delivered.** Their corrected artifacts are absent from the pushed
   revision. The brief says not to mark them closed when unavailable to reviewers — and they
   are unavailable. A remediation that cannot be inspected has not been shared.
2. **The remediation report itself is undelivered**, so the F1–F5 work is undocumented in the
   repository that is being merged.
3. **There is no CI, no branch protection, and no third-party verification.** Every green
   result in this document came from my machine, and I am the author of the code under review.
4. **My own review is not independent.** Same author for the fixes, the audit, the remediation,
   and this review. The five documents F3/F4/F5 reference were written by the same hand.

The technical core looks sound. The **evidence trail needed for human sign-off is not in the
repository**, and that gap cannot be closed by my re-running my own tests.

### Path to `APPROVE WITH DOCUMENTED LOW-SEVERITY LIMITATIONS`

1. **Commit the review documents** (or state explicitly that they are intentionally excluded
   and correct F4's claim that the audit is a diff-visible consent item).
2. Fix the F2 span-set guard to pin boundary spans (B7), and the stale docstring (B8).
3. Investigate the unreproducible failure (B9) or document it in `KNOWN_LIMITATIONS`.
4. Add CI — even a single job running `make -s check` — so gate claims stop being self-reported.
5. Obtain sign-off from someone who did **not** write D1, D2, L1, the audit, or F1–F5.

Until then this revision is, at best, **technically plausible but evidentially incomplete**.
