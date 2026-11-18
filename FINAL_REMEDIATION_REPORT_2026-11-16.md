# Final evidence and test-integrity remediation — report

**Author:** the PC5 implementer. **This work does not satisfy independent review** — the author
of the code remains the author of the remediation and of this report. See §11.

## 1. Starting and final SHAs

| | SHA | Verified by |
|---|---|---|
| `main` | `8aab6d3864bb2e867a70bba28e7bfb52f43d7e35` | `git ls-remote`, `gh pr view` |
| PR #26 head | `170e611dcdfa3fe12e9abe58c2e60ee323aa461c` | `git ls-remote`, `gh pr view` |
| PR #27 head (start) | `0a7eaa07222df5b6182050933c650e8b74efde94` | `git ls-remote`, `gh pr view` |
| **Follow-up head (final)** | **`28aa4bb5635f2f5c9ac2ee1420c9bda1f9ba3ef6`** | pushed, re-fetched, `git ls-remote` agrees |

Ancestry re-proven with `git merge-base --is-ancestor`: `main → 170e611 → 0a7eaa0 → 28aa4bb`.
**Stack preserved; no history rewritten, no force-push, no merge, no PR comment or approval.**

## 2. Working-tree preservation

The starting tree was **not** clobbered. It was:

- **Proven**: all 208 files of `0a7eaa0` byte-identical (`cmp` each, 0 differing, 0 missing).
- **Proven**: exactly 6 files present that were not in `0a7eaa0` — the six undelivered reports.
- **Preserved**: all six copied to `/home/user/preserved3/` with SHA-256 recorded, plus 11 run
  logs from the cold-start investigation.
- **Ref aligned only after that proof**: local branch sat at `8aab6d3` while the remote was
  `0a7eaa0`. `git update-ref` moved the ref, then `git reset` refreshed the index. **No file
  was written, reset, cleaned or discarded** — verified by re-running the 208-file `cmp` after
  the move (0 differing).
- **New branch** `arena/followup-evidence-integrity` created from `0a7eaa0`.

## 3. Files changed

| File | Reason |
|---|---|
| `tests/test_pagetables64.py` | F2: independent coverage anchor, 8 required cases, non-vacuous `setUp`, `AdversarialCoverageTests` (8 controls) |
| `tests/uefi_stub_firmware.py` | F1: docstring named a function that does not exist |
| `tests/test_boot64_stub.py` | F1: 3 helpers that assumed `alloc()` ≠ `None` now use `scratch_slot()` |
| 6 × review/audit/landing `.md` | F4/F5 delivery; independence claims withdrawn; tested SHA + evidence type added |
| `EVIDENCE_INDEX_2026-11-16.md` | New. Index of every artifact, what it is worth, what did not run, how to reproduce |
| `FOLLOWUP_EVIDENCE_F1_F5_2026-11-16.md` | New. This work, with each number re-derivable |

**No production file changed.** `git diff --name-only 0a7eaa0..28aa4bb` contains only `tests/`
and `*.md`. The 32-bit shipping path and `boot/uefi64/` are untouched.

## 4. F2 — coverage design

Eight required cases, each justified by the branch of `pc5_pt_build_identity()` it exercises:
round-down (`0x201000`), exact leaf (`0x200000`), full PDPT entry (`0x40000000`), one leaf past
a PDPT entry (`0x40200000`), max − one leaf (`0xFFE00000`), max − 4 KiB (`0xFFFFF000`),
three entries (`0xC0000000`), and the maximum (`0x100000000`, handled separately because it has
no unused tail). Cases 1/2, 3/4 and 5/6 are boundary pairs; removing either half removes the
ability to distinguish correct from off-by-one.

The requirement lives at **module scope**, outside the mutable class state, so it cannot be
edited by the same act that edits what it guards. The expectation is stated **twice**: derived
(`REQUIRED_UNUSED`) and hand-written (`EXPECTED_UNUSED`), and a test requires them to agree.

| Adversarial control | Result |
|---|---|
| empty `SPANS` | **18 failures/errors** (was 0 before this branch) |
| remove one required case (×3 different cases) | detected each time |
| **the review's consistent-thinning attack** | **3 failures** — previously passed |
| `EXPECTED_UNUSED` rewritten to fit a thinned `SPANS` | detected |
| no-op mutant | identity replacement not reported as a catch; `ORIGINAL != MUTANT` asserted |
| original D1 defect reintroduced | caught by the **shipped** hierarchy test (returns 2048, reaches 1 GiB) |
| L1 stale-tail reintroduced | caught by **both** shipped tail tests |
| canary collision | no false positive at word granularity |

Independent external verification (separate trees, not the tests' own assertions):

| Scenario | Result |
|---|---|
| clean | **64 OK** |
| consistent thinning | **3 failures** |
| empty `SPANS` | **18 failures/errors** |
| D1 defect | **66 failures** |
| L1 mutant | **2072 failures** |

**One honest note.** The empty-`SPANS` result only became 18 because I first wrote a control
that *failed*: emptying `SPANS` left the shipped tests green, so I added a `setUp()` refusal
rather than adjusting the control. The control found a real gap.

## 5. F1 corrections

- Docstring cited `alloc_pages_or_status()`, **which does not exist**; now names
  `svc_allocate_pages`, `svc_allocate_pool` and `alloc_or_raise()`. Verified absent from a
  fresh clone: 0 occurrences.
- Three helpers assumed `alloc()` ≠ `None`; all now use `scratch_slot()`, which fails with the
  pool state rather than writing through `None`. No silent success introduced.
- `PoolExhaustionTests` **4 OK**; full `test_boot64_stub` **35 OK**; against the pre-fix raising
  stub the suite now produces **7 specific errors** rather than 4.

## 6. F4/F5 delivery

All eight documents are **tracked and pushed**, confirmed by cloning the branch:

| Document | Tested SHA | Evidence type |
|---|---|---|
| `AUDIT_PR26_PR27_2026-11-16.md` | `d0be617`, §12 → `141f91f` | author-reported (historical) |
| `REVIEW_PACKAGE...` | `141f91f` / `0a7eaa0` | implementer-assembled |
| `REMEDIATION_REPORT_F1_F5...` | `0a7eaa0` | implementer-reported |
| `AUDIT_PR26_PR27_INDEPENDENT...` | `170e611`, `141f91f` | author-reproduced (claim withdrawn) |
| `ADVERSARIAL_REVIEW...` | `141f91f` | author-reproduced (retitled) |
| `FINAL_REVIEW...` | `0a7eaa0` | author-reproduced, self-disclosed |
| `LANDING_STRATEGY_D...` | `170e611` | implementer-assessed |
| `EVIDENCE_INDEX...`, `FOLLOWUP_EVIDENCE...` | this commit | implementer-assembled |

**Corrections applied before committing**, because publishing them unchanged would have
repeated the defect: three titles claimed "Independent" and were authored by me — withdrawn;
the pool base was printed as its size (`0x1000000`) — corrected to `0x02000000`–`0x03000000`;
rounded headroom figures replaced with measured values.

**F5 re-derived, not copied:** pool 16,777,216 B (16 MiB, 4096 pages); loader 27 pages /
110,592 B over 5 requests; scaffolding 57,344 B; total 167,936 B = 41.00 pages = 1.00 %;
loader-only headroom **151.7×**; combined **99.9×**. All seven confirm. Repo-wide sweep: the
only surviving `~470×` strings sit inside correction text.

## 7. Cold-start failure investigation

Reproduced as a **class of bug**, not as the original instance.

| Series | Runs | Result |
|---|---|---|
| first-ever cold-worktree `make -s check` | 1 | pass |
| `unittest discover` | 10 | 10/10 pass |
| `make -s check` | 5 | 5/5 pass |
| 3 concurrent suites × 3 rounds | 9 | **9/9 fail**, 40–48 failures |
| 1 competing process × 3 offsets | 3 | **3/3 fail**, 19–33 failures |

**Root cause:** `tests/test_fat32_expand.py:152` uses a fixed directory
`build/test/m1i` with fixed filenames `in.img`, `out.img`, `lba.txt`. Any second process in the
same worktree corrupts the images; one competitor suffices. This is pre-existing and unrelated
to the 64-bit path.

**It does not prove the original cause.** That run was single-process as far as I know, its
name was never captured, and 16 further single-process runs were clean. **The original failure
remains unexplained.** Not fixed here: it is the 32-bit test path and outside this scope.

## 8. Regression

| Command | Exit | Result |
|---|---|---|
| `make -s all` | 0 | builds |
| `make -s check` | 0 | **713 tests OK**, 0 failures, 2 skips |
| `make -s stubtest` | 0 | 58/58 |
| `make -s stubtest64` | 0 | 22/22 |
| `make -s verify` | 0 | `[PASS]` |
| `make -s roadmap`, `docs-check`, `brand-check`, `presentation-check` | 0 | pass |
| `make -s boot-test` | 2 (77) | **NOT RUN** — no `node_modules` |
| `make -s ovmf-test` | 2 | **NOT PASSED** — 22 rows, 0 executed |

Also verified in a **fresh clone** of the pushed branch: 713 OK, 2 skips.

Skips: `test_the_real_esp_is_not_firmware` (`build/efi.img not built`) and
`test_built_iso_verifies` (`build/pc5.iso not built`) — build-artifact guards.

## 9. Release isolation

Both inputs pinned: `SOURCE_DATE_EPOCH=1700000000`, `BUILD_ID=141f91f`. Every build from
`rm -rf build`.

| Artifact | `main` | `0a7eaa0` | follow-up |
|---|---|---|---|
| `pc5.iso` | `7d756cb6…` | `7d756cb6…` | `7d756cb6…` |
| `kernel.bin` | `7e1552f0…` | `7e1552f0…` | `7e1552f0…` |
| `mbr.bin` | `d07ec2ba…` | `d07ec2ba…` | `d07ec2ba…` |
| `loader.bin` | `f121b34a…` | `f121b34a…` | `f121b34a…` |
| `efi.img` | `9106b561…` | `9106b561…` | `9106b561…` |

**Claimed narrowly:** the 32-bit release is unchanged by this branch. This is **not** a claim
that the 64-bit path is correct — it is compiled and tested but is not part of the ISO.

## 10. Remaining limitations

| # | Limitation | Label |
|---|---|---|
| L1 | No CI, no branch protection, 0 check-runs | **Blocked** |
| L2 | 0 reviews, 0 approvals on both PRs | **Blocked** |
| L3 | OVMF / QEMU never executed | **Untested** |
| L4 | `boot-test` never executed | **Untested** |
| L5 | No physical hardware, no genuine Sony firmware | **Untested** |
| L6 | `test_fat32_expand.py` not concurrency-safe | **Known defect, unfixed** |
| L7 | Original cold-start failure still unexplained | **Unexplained** |
| L8 | No artifact in the repository is independently reproduced | **Structural** |
| L9 | 64-bit path not wired into the shipping artifact | **By design, unproven** |

## 11. Evidence labels for this work

| Item | Label |
|---|---|
| F2 coverage redesign | **Implemented**, **Test-verified**, **Build-verified** |
| 8 adversarial controls | **Test-verified** |
| F1 docstring + helper corrections | **Implemented**, **Test-verified** |
| F4/F5 delivery | **Implemented**, **Test-verified** (present in a fresh clone) |
| Release isolation | **Build-verified** (byte comparison) |
| Cold-start root cause | **Reproduced** (mechanism), **NOT** the original cause |
| D1 / D2 / L1 / F1 core fixes | **Reported but not independently reproduced** — verified by the author only |
| OVMF / QEMU / hardware / Sony | **Untested** |
| CI, branch protection, human sign-off | **Blocked** |

## 12. PR and branch state

| | |
|---|---|
| Follow-up branch | `arena/followup-evidence-integrity` @ `28aa4bb5635f2f5c9ac2ee1420c9bda1f9ba3ef6` |
| Remote | in sync (`git ls-remote` agrees) |
| PR #26 | OPEN, `mergedAt: null`, head `170e611`, 0 reviews, 0 comments — **untouched** |
| PR #27 | OPEN, `mergedAt: null`, head `0a7eaa0`, 0 reviews, 0 comments — **untouched** |
| `main` | `8aab6d3` — **untouched** |
| Pull request for the follow-up | **Not opened.** Creating one would be a PR action; the brief forbids opening/approving/commenting, so the branch is pushed and left for you to open if you want it. |

**What is still not true:** this is not independent review, no CI exists, and the 64-bit path
has never run under OVMF, on hardware, or against genuine Sony firmware.
