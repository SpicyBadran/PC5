# Follow-up: evidence delivery and test-integrity remediation (2026-11-16)

**Author:** the PC5 implementer (Arena.ai agent). **Not an independent review.**
**Tested revision:** the commit containing this file; **parent:** `0a7eaa07222df5b6182050933c650e8b74efde94`.
**Evidence type:** implementer-reported, with every number below re-derived on demand by the
commands in `EVIDENCE_INDEX_2026-11-16.md` §3.

This follow-up responds to the verdict `DO NOT APPROVE — TECHNICAL FINDINGS REMAIN`. It does
**not** overturn that verdict as a statement about independence: the same author still wrote
everything here. What it can do is remove the *evidential* objections — missing artifacts,
a guard that could be defeated, a failure that was not investigated — so that a real reviewer
has something inspectable.

---

## 1. What the review found, and what changed

| Finding | Severity | Action | State |
|---|---|---|---|
| F2 guard defeatable by consistent thinning of `SPANS` + `EXPECTED_UNUSED` | Low (live) | Coverage anchored outside the mutable state; 8 adversarial controls added | **Fixed and demonstrated** |
| F2: emptying `SPANS` left the shipped tests vacuously green | Low | `setUp()` now refuses an empty collection; each shipped test fails on its own | **Fixed and demonstrated** |
| F1: docstring cited `alloc_pages_or_status()`, which does not exist | Cosmetic | Rewritten to name the two real service callers and `alloc_or_raise()` | **Fixed** |
| F1: three test helpers assumed `alloc()` cannot return `None` | Cosmetic | Routed through `scratch_slot()`; fails with pool state instead of writing to 0 | **Fixed** |
| F4/F5: corrected artifacts existed only in the author's sandbox | **High (process)** | Committed; titles that claimed independence corrected; tested SHA and evidence type added to each | **Fixed** |
| Cold-start `make -s check` failure unexplained | Low | Investigated; **root cause found** (§4) | **Explained, not fixed** |
| No CI, no branch protection, 0 reviews | High | Out of scope for a commit | **Unchanged blocker** |

## 2. F2 — the coverage design, and why each case exists

The requirement is now stated **once**, at module scope, in `REQUIRED_SHORT_SPANS`, with each
case justified by the branch of `pc5_pt_build_identity()` it exercises. It is deliberately not
derived from the collection it guards.

| # | Span | Leaves | Unused PD entries | Why this case is required |
|---|---|---|---|---|
| 1 | `0x201000` (2 MiB + 4 KiB) | 1 | 2047 | `span` is rounded **down** to whole 2 MiB pages. This is the only case that distinguishes round-down from round-up at the low end; a builder that rounded up would map an extra 2 MiB. |
| 2 | `0x200000` (2 MiB) | 1 | 2047 | The exact single-leaf boundary: no rounding at all. Paired with #1 to pin the behaviour on both sides. |
| 3 | `0x40000000` (1 GiB) | 512 | 1536 | The largest span needing exactly **one** PDPT entry — the low edge of the multi-region transition. |
| 4 | `0x40200000` (1 GiB + 2 MiB) | 513 | 1535 | The smallest span needing a **second** PDPT entry. Catches an off-by-one that would leave 513..1024 unreachable, which is the D1 failure mode in a different guise. |
| 5 | `0xFFE00000` (4 GiB − 2 MiB) | 2047 | 1 | Upper bound of `PC5_PT_MAX_INDEX` with a **non-empty** tail — the case the original empty loop pretended to cover. |
| 6 | `0xFFFFF000` (4 GiB − 4 KiB) | 2047 | 1 | Same leaf count by a different rounding path: proves rounding is by 2 MiB, not 4 KiB. |
| 7 | `0xC0000000` (3 GiB) | 1536 | 512 | A mid-range multi-region span: proves entries 0..2 are each installed, not just the first. |
| 8 | `0x100000000` (4 GiB) | 2048 | 0 | The production configuration. **Excluded from the iterated set** because it has no unused tail; covered by `test_the_full_production_span_still_maps_everything` and `IdentityHierarchyTests`. |

Cases 1/2, 3/4 and 5/6 are **pairs straddling a boundary**. Removing either half removes the
ability to distinguish "correct" from "off by one", which is why removal is not merely a
reduction in count.

Two independent statements of the expectation now exist and must agree:
`REQUIRED_UNUSED` (derived from the documented rounding rule) and `EXPECTED_UNUSED`
(hand-written). Editing one to fit a weakened collection leaves the other disagreeing —
`test_the_two_expectation_tables_agree` is the check.

### Adversarial controls and measured results

All eight run as ordinary tests (`AdversarialCoverageTests`), so the breakage is applied to
the shipped code path rather than to a copy of it.

| Control | Attack | Result |
|---|---|---|
| `test_emptying_spans_makes_the_suite_fail` | `SPANS = ()` | **3/3 shipped tests refuse**; whole suite **18 failures/errors** (was 0 before this follow-up) |
| `test_removing_one_required_span_is_detected` | drop the rounding case, the exact-leaf case, and the PDPT off-by-one case | each detected |
| `test_thinning_while_keeping_three_gibibyte_spans_is_detected` | the **exact review attack**: `(1,2,3) GiB` with consistent expectations | **detected** |
| `test_expectations_edited_to_match_the_weakened_collection` | rewrite `EXPECTED_UNUSED` to fit a thinned `SPANS` | **detected** |
| `test_a_no_op_mutant_is_rejected` | replace the mutant with the original source | identity replacement is **not** reported as a catch, and `ORIGINAL != MUTANT` is asserted |
| `test_the_original_d1_defect_is_still_detected` | reinstate a single PDPT entry | defect still returns 2048 while reaching only 1 GiB; the **shipped** hierarchy test rejects it |
| `test_the_l1_stale_tail_defect_is_still_detected` | reinstate the stale unused tail | **both** shipped tail tests catch it |
| `test_a_valid_physical_address_is_not_mistaken_for_a_canary` | canary-filled block | tail reads zero at word granularity; no false positive |

External checks (not the test's own assertions), on a separate tree:

| Scenario | Result |
|---|---|
| clean tree, `tests.test_pagetables64` | **64 tests OK** |
| consistent thinning, class attributes only | **3 failures** |
| `SPANS` emptied in class scope only | **18 failures/errors** |
| original D1 defect (`d0be617`'s `pagetables.c`) | **66 failures** |
| L1 stale-tail mutant | **2072 failures** (subTest expansion) |

The jump from 13 to 2072 on the L1 mutant is because the new adversarial controls re-run the
shipped tests per case; the number is larger but the discrimination is the same defect.

## 3. F1 — documentation and helper robustness

`alloc()`'s docstring said callers "bind the result with `alloc_pages_or_status()`". **No such
function exists.** It now names the two real service callers (`svc_allocate_pages`,
`svc_allocate_pool`) and `alloc_or_raise()`, and says what each does with a `None`.

Three test helpers used `machine.alloc(8)` and then wrote through the result. On a fresh
machine a `None` is impossible, but if one ever occurred the failure would have been a
`TypeError` writing to address 0 — silent-success-shaped. All three now use `scratch_slot()`,
which asserts with the pool state and the recorded exhaustions.

Re-verified after the change:

| Check | Result |
|---|---|
| `PoolExhaustionTests` | **4 tests OK** |
| full `test_boot64_stub` | **35 tests OK** |
| old raising stub restored, new tests kept | **7 errors** (was 4 before `scratch_slot`) — the helpers now fail specifically instead of opaquely |

## 4. The cold-start failure — investigated, not dismissed

**Reported:** one `make -s check` failure in a cold worktree, six later passes, test name not
captured.

**What was done.** Repeated runs at `0a7eaa0` in fresh detached worktrees, each fully logged:

| Series | Runs | Result |
|---|---|---|
| first-ever run in a cold worktree, `make -s check` | 1 | **pass** (697, 2 skips) |
| `python3 -m unittest discover` | 10 | **10/10 pass** |
| `make -s check` (the exact failing command form) | 5 | **5/5 pass** |
| **3 suites concurrently**, 3 rounds | 9 | **9/9 fail**, 40–48 failures each |
| **one** competing process in the same worktree, 3 offsets | 3 | **3/3 fail**, 19–33 failures |

**Root cause (reproduced, not guessed).** `tests/test_fat32_expand.py:152` sets
`cls.tmp = os.path.join(ROOT, "build", "test", "m1i")` — a **fixed** directory — and
`run_op()` writes fixed filenames `in.img`, `out.img`, `lba.txt` into it. Two processes running
the suite in the same worktree overwrite each other's images mid-operation, so the parity,
directory-growth and fault-injection tests see a corrupted volume. A single competing process
is enough; it is not a race that needs many.

**What this explains and what it does not.** It explains *a* class of intermittent failure —
anything else touching the same worktree, including an editor, a second test run, or a
pre-commit hook. It does **not** prove it was the cause of the original observation: that run
was single-process as far as I know, its name was not captured, and I could not reproduce a
failure in 16 further single-process runs. **The original failure remains unexplained.** I am
not claiming the concurrency bug *is* the cause.

**Not fixed here.** Changing the harness's working-directory strategy is outside this
follow-up's scope and touches the 32-bit test path. It is recorded as blocker B11.

## 5. F4 / F5 — evidence delivery

Committed and pushed with this branch (see `EVIDENCE_INDEX_2026-11-16.md` §1): the review
package, the post-remediation audit, the adversarial review, the final review, the landing
strategy, the remediation report, the evidence index and this file.

Corrections applied before committing, because publishing them as they stood would have
repeated the defect they describe:

- `ADVERSARIAL_REVIEW...md` — title was "Independent adversarial review"; corrected to
  "Author self-review" with a note saying why.
- `AUDIT_PR26_PR27_INDEPENDENT...md` — title claimed "Independent"; the claim is withdrawn at
  the head of the file. The filename is retained because other documents reference it.
- `REVIEW_PACKAGE...md` — "Independent" removed from the title.
- Every report now carries a **tested SHA**, an **evidence type**, a **historical/current**
  marker and an **author disclosure**.
- The pool address written as `0x1000000` in the adversarial review was the pool *size*, not
  its base; corrected to `0x02000000`–`0x03000000`.
- Rounded headroom figures (`≈152×`, `≈100×`) replaced with the measured `151.7×` and `99.9×`.

**F5 re-derived independently of the earlier reports** (`EVIDENCE_INDEX` §4). All seven values
confirm: 16 MiB capacity, 27 loader pages, 110,592 B, 41 pages / 167,936 B total, 151.7×,
99.9×. Repo-wide sweep: the only surviving `~470×` strings are inside correction text quoting
the error. **No tracked file ever carried the claim**, so `main` was never affected.

## 6. Regression at this revision

| Command | Exit | Result |
|---|---|---|
| `make -s all` | 0 | builds |
| `make -s check` | 0 | **713 tests OK**, 2 skips — *see the correction in §9 below: an earlier draft of this line read "0 real skips", which was wrong* |
| `make -s stubtest` | 0 | PASSED 58/58 |
| `make -s stubtest64` | 0 | 22/22 |
| `make -s verify` | 0 | `[PASS]` |
| `make -s roadmap` / `docs-check` / `brand-check` / `presentation-check` | 0 | pass |
| `make -s boot-test` | 2 (77) | **NOT RUN** — no `node_modules` |
| `make -s ovmf-test` | 2 | **NOT PASSED** — 22 required rows, 0 executed |

Test count 697 → 713: +9 integrity, +8 adversarial, −1 replaced empty-SPANS test.

## 7. Release isolation — measured

Pinned **both** inputs, because `BUILD_ID` otherwise defaults to `git rev-parse --short HEAD`,
so two builds of different revisions differ by construction. Every build below started from
`rm -rf build`; no artifact was reused.

Inputs: `SOURCE_DATE_EPOCH=1700000000`, `BUILD_ID=141f91f`.

| Artifact | `main` `8aab6d3` | PR #27 before `0a7eaa0` | this follow-up |
|---|---|---|---|
| `build/pc5.iso` | `7d756cb6125266e1…` | `7d756cb6125266e1…` | `7d756cb6125266e1…` |
| `build/kernel.bin` | `7e1552f0a30e127e…` | `7e1552f0a30e127e…` | `7e1552f0a30e127e…` |
| `build/mbr.bin` | `d07ec2ba3aeb06b2…` | `d07ec2ba3aeb06b2…` | `d07ec2ba3aeb06b2…` |
| `build/loader.bin` | `f121b34a7ebc0e7d…` | `f121b34a7ebc0e7d…` | `f121b34a7ebc0e7d…` |
| `build/efi.img` | `9106b561ea55c7f8…` | `9106b561ea55c7f8…` | `9106b561ea55c7f8…` |

Full digests (identical in all three columns):

```
pc5.iso    7d756cb6125266e14651371237723dcd19aef36e633d73eb31a8a57d98665042
kernel.bin 7e1552f0a30e127e991929b347872ca9b355c029444fe7b88b62a716e5896b9e
mbr.bin    d07ec2ba3aeb06b200ed8e918d9a14dfa18b730477f03cbd632dec06ab19d107
loader.bin f121b34a7ebc0e7dd6855e429496b7f3bbb636ac4249fad62542b8c6503da1bb
efi.img    9106b561ea55c7f8a6dd9d525ad7d56274765cd5bc6bda4ca9de4553c85afba1
```

**Interpretation, stated narrowly.** The shipping ISO, kernel, MBR, loader and EFI image are
**bit-identical** across `main`, `0a7eaa0` and this follow-up. That is the expected result of a
change confined to `tests/` and `*.md`, and it is also the limit of the claim: this shows the
follow-up does not alter the 32-bit release, **not** that the 64-bit path is correct or wired
in. `boot/uefi64/` and `kernel64/` are compiled and tested but are not part of the ISO.

## 8. What is still not true

- **This is not independent review.** Same author throughout.
- **No CI exists.** Every result is from one machine.
- **OVMF, QEMU, physical hardware and genuine Sony firmware remain untested.**
- **The concurrency bug in `test_fat32_expand.py` is real and unfixed.**
- **The original cold-start failure is still unexplained.**

---

## 9. Addendum 2026-11-16 (later the same day): shared-path races fixed

Appended, not rewritten. Everything above stands as written; this section records
what was done afterwards and one correction to it.

**Starting revision:** `eacba14`
**Final revision:** the commit that adds this section (parent `03daa4a`).

*Corrected 2026-11-16.* Both references above originally named the commits that
carried them before this branch was rebased onto PR #27, and a rebase rewrites every
commit id above it, so both had become unreadable pointers. The rewritten commits
are cited instead: `eacba14` is still the ancestor, and the commit that adds this
section still has its own parent, so the ordering this paragraph describes is
unchanged. Nothing else in §9 was altered.
### 9.1 Correction: the skip count

An earlier draft of §6 said the suite ran with **"0 real skips"** while the same
paragraph reported `2 skips`. That was sloppy, and it is corrected here rather
than quietly edited away. The truth, measured:

- The run reports **2 skipped tests**, and the "Ran N tests" count **includes**
  them. `713 tests` (now `727`) counts skips in the total.
- The two skips, by fully-qualified ID:

| Test ID | Reason reported by the runner | Optional? |
|---|---|---|
| `test_ovmf_harness.FirmwareAcceptanceTests.test_the_real_esp_is_not_firmware` | `build/efi.img is not built` | **Conditional** — a build artifact, not an environment capability |
| `test_tools.BuiltImageTests.test_built_iso_verifies` | `build/pc5.iso not built` | **Conditional** — same |

Neither hides required coverage: both run and pass as soon as `make all` has
produced the artifacts. Measured after `make -s all`:
`tests.test_ovmf_harness tests.test_tools` → **58 tests, OK, 0 skips**.

- **A third, larger skip set exists and is environment-dependent.** Without the
  `unicorn` package installed, **22 additional tests** in
  `test_boot64_stub.StubFirmware64Tests` skip with
  `'unicorn is not installed (pip install unicorn)'`, making the reported skip
  count **24, not 2**. The figure of 2 in §6 is therefore only true in an
  environment with `unicorn` present. Both numbers are recorded here so that
  neither can be mistaken for the other, and no skip was removed or altered to
  make the count look cleaner.

### 9.2 Three shared-path races, not one

The previous report named `test_fat32_expand.py` only. Two more were found by
inspecting every path the suite writes:

| # | Shared path | Failure mode | Measured before the fix |
|---|---|---|---|
| 1 | `build/test/m1i/{in,out}.img`, `lba.txt` | Two suite processes overwrite each other's images mid-run | 2 concurrent module runs → **41** and **33** failures |
| 2 | `$(BUILD)/kernel-unit` | One run links the binary while the other executes it → `ETXTBSY` | 2 concurrent `make unit` → exits **2** and **0**, `Text file busy … Error 127` |
| 3 | `build/manifest-test.json`, `build/manifest-cli.json` | Silent: both runs write identical bytes, so it passes by luck | 2 concurrent runs → OK, but only because the outputs are identical |

Defect 3 was not failing, which is why it is worth stating plainly: it was a
latent flake, not an observed failure. Any change making the two runs' manifests
differ would have made it intermittent.

### 9.3 The isolation fix

`tests/scratch.py` is new and states the model in its own docstring. Three
artifact kinds, three scopes:

| Artifact | Scope | Why |
|---|---|---|
| Compiled driver (`fat32-write-check`) | **per process** | Immutable once linked and safe to *execute* concurrently, but rebuilding it while another process executes it gives `ETXTBSY`. Per-process is the smallest safe boundary. |
| Mutable run files (`in.img`, `out.img`, `lba.txt`) | **per case** | Written and read on every `run_op()`. Per-process is not enough if tests ever run in parallel *within* one process, so each case gets its own directory. |
| Failure diagnostics | kept on failure | The images are the only evidence of what the driver did; a failing case retains its tree. |

Cleanup reliability: `addCleanup` / `addClassCleanup`, which `unittest` runs
after failed assertions and after `tearDown`. `atexit` covers the ordinary exit
path. A `SIGKILL`ed process leaves its directory behind — nothing can prevent
that — but those live under the system temp directory as
`pc5-tests-<pid>-*`, never in the checkout.

`Makefile`: the `unit` target now creates its binary with `mktemp` and removes it
via a `trap`. A `$$`-based name was tried first and **did not work** — make
expands it at parse time, so every invocation of the same make process shared the
name, and the concurrent runs still collided. That attempt is recorded because it
is the kind of thing that looks correct in a diff.

### 9.4 Regression tests (14 new)

`tests/test_scratch_isolation.py`. These assert isolation, not that a helper was
called.

| Test | What it binds |
|---|---|
| `test_the_root_is_stable_within_a_process` | one root per process |
| `test_the_root_is_outside_the_repository` | scratch cannot be committed or shared across checkouts |
| `test_distinct_cases_get_distinct_directories` | same fixed filename in two cases resolves to different files |
| `test_a_second_call_for_the_same_owner_still_gets_a_fresh_directory` | re-entry does not inherit stale images |
| `test_cleanup_removes_only_its_own_tree` | cleanup is scoped, does not delete another case's tree |
| `test_a_failing_case_keeps_its_directory` | diagnostics survive a failure |
| `test_a_competing_process_gets_a_different_root` | 3 subprocesses → 3 distinct roots, none equal to ours |
| `test_the_driver_does_not_use_the_old_fixed_directory` | the old path cannot return to `test_fat32_expand` |
| `test_the_write_driver_uses_per_case_storage` | same for `test_fat32_write` |
| `test_the_manifest_test_no_longer_writes_into_build` | same for `test_iso_manifest` |
| `test_the_unit_binary_is_not_a_single_shared_name` | `mktemp` present, shared name gone |
| `test_the_module_still_runs_its_real_operations` | coverage was isolated, not reduced |
| `test_a_shared_directory_corrupts_concurrent_runs` | **control**: the old scheme must collide |
| `test_the_isolated_scheme_does_not_corrupt_concurrent_runs` | the new scheme must not |

The control was itself verified to discriminate, in both directions:

- given two private directories, `test_a_shared_directory_corrupts_concurrent_runs`
  **fails** with *"the old fixed-path scheme did NOT collide in this control"*;
- given one shared directory, `test_the_isolated_scheme_does_not_corrupt_concurrent_runs`
  **fails** with `['WRONG:BBBB', 'OK']`.

An earlier draft of the control used the same directory for both processes and
passed even when inverted — it proved nothing. That draft was discarded; the
measurement above is from the corrected version.

### 9.5 Concurrent execution results

All from one checkout, all captured with exit codes.

| Run | Processes | Result |
|---|---|---|
| affected module, single | 1 | 50 tests OK |
| affected module, concurrent | 2 | **50 OK / 50 OK**, 0 failures |
| affected module, concurrent | 4 | **50 OK ×4**, 0 failures |
| full suite, `make -s check` | 1 | 727 OK, 2 skips |
| full suite, `make -s check`, concurrent | 2 | **727 OK / 727 OK**, 0 failures |
| … repeated 3 more rounds | 2 each | **8/8 process-runs OK**, 0 failures |

Zero scratch directories were left behind after any of these.

**This is not a proof of concurrency safety in general.** It is eight concurrent
full-suite process-pairs plus the module-level runs above, on one machine, with
one kernel. A race can be rare. What can be said precisely is that the three
shared paths identified are gone, and that the specific collisions previously
reproduced no longer reproduce.

### 9.6 The original unexplained failure — still unexplained

§4 reported one unreproduced single-process `make -s check` failure. **The fix in
this section does not retroactively explain it.** The concurrency defect is a
demonstrated mechanism by which *a* failure of that shape can occur, but the
original run was single-process as far as I know, its test name was never
captured, and 16 further single-process runs before this change were clean. That
observation is preserved unaltered in §4 and is **not** claimed as resolved.

### 9.7 Full-suite results after the fix

`727 tests, OK, 2 skips` (was 713 before the 14 regression tests were added).
No test was weakened, removed, skipped or relaxed. `make -s check`, `stubtest`,
`stubtest64`, `verify`, `roadmap`, `docs-check`, `brand-check` and
`presentation-check` all exit 0. `boot-test` and `ovmf-test` still do not run —
no `node_modules`, no OVMF/QEMU — and are reported as not run, not as passes.

### 9.8 Additional shared-path races discovered

Only the three in §9.2. Every other test module was checked for fixed paths
under `build/`; the remaining reads (`test_ovmf_harness` reading `build/efi.img`,
`test_tools` reading `build/pc5.iso`) are **read-only** and stay safe while
nothing writes those files concurrently. They are listed here so a reviewer can
decide whether that is good enough.

### 9.9 Remaining limitations

1. **This is implementer-authored evidence, not independent review.**
2. Eight concurrent full-suite process-pairs is not a proof of concurrency safety.
3. `test_ovmf_harness` and `test_tools` still *read* shared build artifacts; a
   concurrent `make all` would race with them in principle.
4. The original cold-start failure remains unexplained (§9.6).
5. No CI exists; these results are from one machine.
6. OVMF, QEMU, physical hardware and genuine Sony firmware remain untested.

## 10. Addendum: the cleanup decision was broken, and is now correct

This section corrects §9.3 and §5a of the index. The isolation model in §9.3 is
unchanged; what was wrong is the *cleanup decision* - whether a directory is
removed - and the tests that were supposed to bind it.

### 10.1 The defect

`tests/scratch.py:register_cleanup` decided whether to keep a directory by
reading `case._outcome.errors + case._outcome.failures`. `unittest.case._Outcome`
has neither attribute (measured on the project interpreter, Python 3.11.2):

    expectedFailure, expecting_failure, result, result_supports_subtests,
    success, testPartExecutor

So the `except AttributeError` branch fired on **every** test, `failed` was
always `True`, and every directory was kept. `note_failure()` then set a
process-wide flag that made `drop_class_tree` retain every class tree too. The
only thing that ever removed anything was the `atexit` hook at interpreter exit.

Measured at the moment the runner returned (before `atexit` could hide it), with
both FAT32 modules passing:

| | before | after |
|---|---|---|
| tests run | 88 | 88 |
| failures / errors | 0 / 0 | 0 / 0 |
| per-case directories remaining | **82** | 0 |
| class trees remaining | **84** | 0 |
| files remaining | 234 | 0 |

And with one failure injected into a 17-case class (`OverwriteTests`):

| | before | after |
|---|---|---|
| directories remaining | 37 | 4 |
| files remaining | 52 | 3 |

Before: every case directory and every class tree, all 17 tests' images. After:
the two owner directories (`fat32_expand`, `OverwriteTests`), the failing test's
directory, and its `case-*` holding `in.img`, `out.img`, `lba.txt`. The three
files belong to the failing case and to nothing else.

The "after" run retains exactly one case directory - `in.img`, `out.img`,
`lba.txt` - belonging to the failing test. That test uses `subTest`, so the two
failure entries are `_SubTest` objects; the fix unwraps them.

### 10.2 Why `outcome.success` is not the fix

The obvious repair - use `outcome.success` - is also wrong, and this was
measured rather than assumed. `TestCase.doCleanups` wraps every cleanup function
in `outcome.testPartExecutor(self)`, whose `__enter__` sets `success = True`.
So inside a cleanup body, `outcome.success` is **always** `True`, even after a
failure:

    in_test_before_fail    outcome.success=True  failures=0 errors=0
    cleanup_after_fail     outcome.success=True  failures=1 errors=0
    cleanup_after_error    outcome.success=True  failures=1 errors=1

The result lists, by contrast, are appended to *before* `doCleanups` runs
(`TestCase.run` order: `startTest` → `_outcome` → setUp/test/tearDown executors
→ `doCleanups()` → `if outcome.success` → `_outcome = None` → `stopTest`), so
scanning them is correct at every point a cleanup can observe.

### 10.3 The fix

`scratch.case_failed(case)` scans `result.failures`, `result.errors` and
`result.unexpectedSuccesses`, unwraps `_SubTest.test_case`, and compares by
identity to the case. Attribution is therefore per case: one failing test no
longer retains another test's directory. Anything unusual - no `_outcome`, no
`result` - keeps the tree, because losing a diagnostic artifact is worse than
leaving a directory.

`note_failure()` and the process-wide `_PROCESS_FAILURE` flag are removed; they
were only referenced from `scratch.py` and had no other caller.
`drop_class_tree` now removes the class tree unconditionally (subject to
`PC5_KEEP_TEST_SCRATCH`). The layout makes that safe, and the layout is worth
stating exactly because it is easy to get wrong from the directory names alone.
`setUpClass` builds `scratch.directory("fat32_expand", cls.__name__)`, which is
itself a `case-*` directory - that is what `drop_class_tree` is handed, and a
trace confirms it contains exactly one file, the compiled driver. Per-case
images live one level deeper, under
`fat32_expand/<Class>/<test_name>/case-*`. The two `case-*` levels are therefore
different things, and the class-level one is removed while the case-level
survivor keeps its evidence. The measurement above shows this: the retained
directory is the *inner* one, with 3 files. Empty owner directories left behind by removed case
directories are pruned, so a passing run leaves the scratch root empty rather
than a tree of empty shells.

### 10.4 Regression tests

`tests/test_scratch_isolation.py` previously faked `_outcome` with an object
exposing `errors`/`failures` lists - a double that does not exist in the real
runner. Those two tests are replaced by `CleanupLifecycleTest` (11 tests) that
run real `TestCase` classes through a real `TextTestRunner` and inspect the
scratch root when the runner returns: passing removes, failing keeps only its
own, erroring keeps, failing `subTest` keeps, pass-after-fail still cleans up,
class cleanup runs when `setUpClass` raises part-way, a `setUp` failure leaves
no case directory, `PC5_KEEP_TEST_SCRATCH=1` keeps everything, the root is
removed at interpreter exit (subprocess), and the two fail-safe branches of
`case_failed`.

The file now has 23 tests (was 14). The new lifecycle tests were run against the
old `scratch.py` as a control: **7 of 11 fail** (5 failures, 2 errors from the
missing `case_failed`). The 4 that pass against the old code are the "keeps"
cases, which the old code satisfied trivially by keeping everything; every
"removes" case fails. The control is not vacuous.

### 10.5 Full suite

With `unicorn` installed (2.1.4, in a venv; the system interpreter is
externally managed and has no `unicorn`):

    Ran 736 tests in 70.3s
    OK (skipped=2)

736 = the 727 of §9 plus the 9 net new tests. The 2 skips are the pre-existing
`test_ovmf_harness` / `test_tools` "not built" skips. Without `unicorn`, 69
tests error with `ModuleNotFoundError` / `StubError` - pre-existing, in
`test_kernel64` and `test_boot64_stub`, neither of which imports `scratch`.

Two concurrent runs of both FAT32 modules (88 tests each) both pass, and no
scratch root remains after either exits.

### 10.6 Corrections to earlier claims

* §9.3: "`addCleanup` / `addClassCleanup`, which `unittest` runs after failed
  assertions and after `tearDown`" - true, but the decision taken inside those
  cleanups was broken (§10.1). The reliability claim did not hold.
* Index §5a: "a passing run cleans up" - **not true** before this fix. A passing
  run left 82 case directories and 84 class trees behind; only `atexit` removed
  them, at process exit.
* §9.4's "14 new" tests: the two cleanup tests among them were vacuous (they
  tested a fake outcome). They are replaced by §10.4.

## 11. Addendum: unexpected successes, and what a cleanup can actually know

Dated 2026-11-16, after §10. §10 stands as written with one correction, stated at
the end of this section. This one is subtler than §10: §10's fix was right about
`failures` and `errors` and wrong about one of the three lists it consulted.

### 11.1 The defect

`case_failed()` consulted `result.unexpectedSuccesses`. Inside a cleanup that list
is **always empty**, because `TestCase.run` calls `doCleanups()` *before* it
records the outcome:

    self.doCleanups()

    if outcome.success:
        if expecting_failure:
            ... self._addUnexpectedSuccess(result)   # <-- after cleanups
        else:
            result.addSuccess(self)

So the clause could never fire. Worse, the other two `@expectedFailure` signals
that a cleanup *can* see both point the wrong way: `outcome.expecting_failure` has
already been reset to `False` at that point in `run()`, and `outcome.success` is
`True`. An unexpected success therefore looked exactly like a pass to
`case_failed()`, and its case directory was deleted.

That is the wrong direction: `unittest` counts an unexpected success as a
**failure** - the run exits non-zero - so it is precisely the case where the
images are the only record of what happened.

Measured on the project interpreter (Python 3.11.2), with a real `TestCase`
decorated `@unittest.expectedFailure` whose body returns normally, driven through
`TextTestRunner`, observed from a cleanup registered in `setUp`:

| Case | `expecting_marker` | `outcome.expectedFailure` set | already in result lists | `len(unexpectedSuccesses)` | Outcome |
|---|---|---|---|---|---|
| ordinary pass | False | False | False | 0 | pass |
| failure | False | False | True | 0 | fail |
| error | False | False | True | 0 | error |
| failing `subTest` | False | False | True | 0 | fail |
| **unexpected success** | **True** | **False** | **False** | **0** | **unexpected success** |
| genuine expected failure | True | True | False | 0 | expected failure |

The last two rows are the discriminating pair: same marker, same empty result
lists, and the only difference available to a cleanup is whether
`outcome.expectedFailure` was set - which `testPartExecutor` does only when an
`@expectedFailure` case actually raises.

Before the correction, measured end-to-end: one unexpected success, and **0**
directories surviving at the moment the runner returned. After: 1 directory,
holding the case's images.

### 11.2 The fix, and why `outcome.success` is usable here

`_unexpected_success()` returns true when the case carries the expected-failure
marker, `outcome.expectedFailure` is unset, and `outcome.success` is true.

Using `outcome.success` here does not contradict §10.2, and the distinction
matters: `testPartExecutor` sets `success = True` on entry, but on exit it does
`self.success = self.success and old_success`, restoring the value from before the
cleanup function ran. In §10.2 the observation was made *inside* a cleanup body -
where `success` is indeed always true - and that remains correct. What is read
here is the same attribute *after* the test body's executor has exited and *before*
any cleanup runs, where it holds the test's own outcome and `expecting_failure`
has already been cleared. Both statements are measured; they are about two
different instants.

`case_failed()` now reads `failures`, `errors` and `unexpectedSuccesses` from the
result (the first two are appended before `doCleanups`, the third matters only for
custom results that record it early), and falls back to `_unexpected_success()`
for the one case the standard result cannot yet describe. Non-standard results, a
missing `_outcome` or a missing `result` still keep the tree.

Policy, stated so implementation and tests agree: **a case retains its artifacts
iff the suite counts the run as failed** - failure, error, or unexpected success.
A met expectation is a pass and keeps nothing. That is a deliberate choice, not an
accident of the mechanism: an unexpected success means the test is no longer
testing what it claims, and the images are the evidence of what it did instead.

### 11.3 Regression tests, and proof that they discriminate

`tests/test_scratch_isolation.py` gains four real-lifecycle tests (23 -> 27):

| Test | Asserts |
|---|---|
| `test_unexpected_success_keeps_its_directory` | the runner reports an unexpected success, `wasSuccessful()` is False, and the case directory survives while the class tree does not |
| `test_a_genuine_expected_failure_removes_its_directory` | the inverse: a met expectation is a pass and leaves nothing |
| `test_keep_env_keeps_an_unexpected_success_too` | `PC5_KEEP_TEST_SCRATCH=1` retains it |
| `test_unexpected_success_does_not_retain_an_ordinary_pass` | attribution stays per case |

The genuine-expected-failure test exists so that over-retention fails too. Without
it, `return True` for every marked case would satisfy the retention test while
reintroducing the bug this module was fixed for.

Both directions were checked by running the tests against implementations that get
it wrong:

| Implementation under test | Result |
|---|---|
| `667afa0` (`unexpectedSuccesses` only) | **27 tests, FAILED (failures=2)** - `test_unexpected_success_keeps_its_directory`, `test_unexpected_success_does_not_retain_an_ordinary_pass` |
| mutation: retain *every* `@expectedFailure` case | **27 tests, FAILED (failures=1)** - `test_a_genuine_expected_failure_removes_its_directory` |
| current implementation | **27 tests, OK** |

So the tests are not vacuous in either direction: they fail for the exact defect a
reviewer would reintroduce, and they fail for its mirror image.

### 11.4 Correction to §10

§10.2 and §10.3 state that `case_failed()` "scans `result.failures` /
`.errors` / `.unexpectedSuccesses`" and present that as the complete answer. The
first two are right. The third was inert at cleanup time, as §11.1 measures. §10's
measurements, tables and the `success`-inside-cleanups finding are unaffected and
are not rewritten here.

### 11.5 What did not regress

Per-process roots, per-class driver isolation, per-case image isolation, cleanup of
passing cases, retention of failing artifacts, and the per-case attribution from
§10.3 are all still bound by the suite - the 23 tests §10 left behind still pass,
and this section adds 4 more. Full-suite and concurrency results are recorded in
the commit message and summarised in the PR, not duplicated here.

## 12. Addendum: the link checkers must report malformed paths, not crash on them

Dated 2026-11-16, after §11. This one is a defect introduced *by* §11's companion
change (the percent-decoding in the docs tooling), found by independent review
rather than by the suite.

### 12.1 The defect (H1)

Making the link checkers decode `%XX` so they follow what GitHub follows was
correct, but it moved a malformed link out of the "does not resolve" path and
into `Path.resolve()`, which raises on some decoded strings. `%00` is the one that
bites:

    urllib.parse.unquote("docs/a%00b.md")   -> "docs/a\0b.md"
    (root / "docs/a\0b.md").resolve()       -> ValueError: embedded null byte

Before the change the literal `docs/a%00b.md` simply did not exist and was
reported. After it, the same link caused an uncaught traceback that stopped the
whole documentation check. A tool whose purpose is to report bad links must never
die on one.

Measured on the project interpreter, before the fix:

The targets are listed rather than written as live links, because a literal
malformed link in this document is itself a broken link - the checker is right to
say so, and it did.

| Link target | Result |
|---|---|
| `docs/a%00b.md` | **CRASH** `ValueError: embedded null byte` |
| `docs/a%00b.md#frag` | **CRASH** |
| `docs/%zz.md`, `docs/a%2`, `a%2Fb.md`, `%ed%a0%80`, `%b0` | reported as missing (already safe) |
| `docs/%2e%2e/etc/passwd` | reported as missing (no file there, so nothing resolves) |

### 12.2 Scope: which of the three checkers actually crashed

Two of the three, measured individually rather than assumed. The third was left
alone because it is not affected.

| Site | Behaviour on `%00` | Action |
|---|---|---|
| `tools/docs_check.py` (`check`) | `ValueError` escaped | **fixed** |
| `tools/presentation.py` (`link_problems`) | `ValueError` escaped | **fixed** |
| `tests/test_architecture.py` (`markdown_links`) | yields the string; the caller uses `os.path.exists`, which returns `False` for NUL | unchanged |

`os.path.exists`/`Path.exists` answer `False` for a NUL path rather than raising;
only `resolve()` raises. That is why the third site needed nothing.

### 12.3 The fix

Both affected sites gain a tiny helper that resolves and returns "did not resolve"
instead of propagating, catching **only** `ValueError` and `OSError` - the two
failures a path the filesystem cannot represent can cause. Anything else is a bug
in the checker and still propagates. The `%00` case becomes an ordinary
`link target missing` problem, carrying the link as written.

No diagnostic was weakened, re-verified after the fix:

| Check | Result |
|---|---|
| real missing file | still caught |
| real anchor miss | still caught |
| real valid link + anchor | still passes |
| missing `make` target | still caught |
| malformed escapes (`%zz`, `%2`, `%2F`) | still reported |
| `%2e%2e` traversal | still reported when the target does not exist; **not contained** - see §14 |

### 12.4 Regression tests

Four added, each run against the original `3c4684d` code and against the fix:

| Test | `3c4684d` | fixed |
|---|---|---|
| `test_docs_check`: `..._nul_is_reported_not_raised` | **ERROR** (`ValueError`) | pass |
| `test_docs_check`: `..._nul_with_a_fragment_is_reported_not_raised` | **ERROR** | pass |
| `test_docs_check`: `other_malformed_escapes_are_still_reported` | pass | pass |
| `test_presentation`: `..._nul_link_is_caught_not_raised` | **ERROR** | pass |

The third test is the guard against over-correcting: it asserts that escapes
which were *already* reported stay reported, so "make it not crash" cannot be
satisfied by reporting nothing.

## 13. Addendum: cleanup ordering (M2), documented and pinned

Independent review noted that a scratch directory can be deleted before a later
cleanup raises, so a failing run can lose its evidence. Inspected, reproduced, and
recorded as a constraint rather than redesigned.

`addCleanup` runs functions in **reverse registration order**, and the retention
decision is taken when the scratch cleanup runs. So the same raising cleanup on
the same passing test body behaves differently depending only on registration
order - measured, five repeats each, deterministic:

| Registration order | Observed sequence | Errors | Evidence |
|---|---|---|---|
| scratch cleanup **first** | `boom` then scratch | 1 | **kept** |
| scratch cleanup **last** | scratch then `boom` | 1 | **removed** |

The rule, now in `register_cleanup`'s docstring and in the module overview: call
`register_cleanup` before adding any other cleanup that could raise. Registered
first, it runs last and observes everything.

It cannot be fixed inside the module: at the earlier instant the failure has not
happened yet, so there is nothing to read. All four current call sites
(`test_fat32_expand`, `test_fat32_write`, `test_iso_manifest` ×2) are the only
cleanup on their case, so none is affected today; this is a latent constraint.

`test_cleanup_ordering_decides_whether_evidence_survives` asserts both halves -
evidence retained in the safe order, removed in the hazardous one - because only
the pair shows that ordering is what decides. It deliberately does **not** claim
evidence is always retained.

## 14. Correction: link traversal is not confined to the repository

Dated 2026-11-16, after §13. This corrects a claim in §12; it does not change any
behaviour. Flagged by independent review, and reproduced here before being
written down.

### 14.1 What was wrong

§12.3's table asserted that `%2e%2e` traversal was "still reported; still
contained", and §12.1's parenthetical said "nothing escapes". **The containment
half of both statements is false.** Measured on this checkout:

| Link target | Target exists? | `docs_check` result |
|---|---|---|
| `docs/%2e%2e/README.md` | yes, one directory above `docs/` | **`[]` — accepted** |
| `docs/%2e%2e/etc/passwd` | no | reported as missing |

The first row is the point: a link that climbs out of the directory it is written
in resolves and passes. Only links whose target does not exist are reported, which
is why the second row looked like containment and is not.

### 14.2 What is and is not new

The distinction matters, and it is the reason this is a documentation defect
rather than a regression:

* **Plain, unencoded relative traversal already resolved before PR #28.** At
  `main` (`8aab6d3`), a link such as `../../…/etc/hostname` was accepted when the
  target existed. Encoding was not required.
* **The `%2e%2e` decoding behaviour was introduced earlier in PR #28's history**,
  by the percent-decoding change in `3c4684d` that made the checkers follow links
  the way GitHub does. Before it, `docs/%2e%2e/README.md` was reported as missing
  because the literal encoded string did not exist.
* **The H1/M2 remediation (§12-§13, commits `ac83252` and `ef876bb`) introduced no
  new traversal capability.** It changed how malformed links are reported, not
  which paths resolve.

So the accurate statement is: relative-path traversal is not confined to the
repository, and the encoded form became reachable earlier in this branch. Neither
is caused by the fix in §12.

*Citation note, 2026-11-16.* The commit ids in this section were rewritten by the
rebase onto PR #27 and have been replaced with the rewritten commits. For readers
working from an earlier copy of this document, the mapping is: the percent-decoding
commit formerly cited here as c34988c is now `3c4684d`, the malformed-link fix
formerly cited as 07337c2 is now `ac83252`, and the LIFO commit formerly cited as
3b47f9e is now `ef876bb`. The superseded ids are given without backticks because
they no longer name any object and must not be read as live citations. Commit
subjects and diffs are unchanged, so the argument above is unaffected; only the
pointers moved.

### 14.3 Status

**This is not fixed, and this document does not claim it is.** The documentation
checkers resolve links against the filesystem and do not confine them to the
repository. Adding containment would be a behaviour change to `tools/docs_check.py`
and `tools/presentation.py` — new restrictions on which links a document may use —
and is outside this correction, which is deliberately wording-only.

Practical impact is low: the checkers run over this repository's own tracked
Markdown, links are written by the project's own contributors, and a link that
climbs out of the tree is a documentation error rather than an attack surface.
That is an assessment of exposure, not a containment guarantee, and it should not
be cited as one.

### 14.4 Wording changes made

| Section | Before | After |
|---|---|---|
| §12.1 | reported as missing (no file there; nothing escapes) | reported as missing (no file there, so nothing resolves) |
| §12.3 | still reported; still contained | still reported when the target does not exist; **not contained** - see §14 |

Two phrases changed. No measurement, test, count or other finding in §12 or §13
was altered, and no behaviour or test was changed by this commit.
