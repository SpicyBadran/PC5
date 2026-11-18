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

**Starting revision:** `d6f6c7ff22376fb04a9578323cf2495d708a2564`
**Final revision:** the commit that adds this section (parent `995730d`).

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
