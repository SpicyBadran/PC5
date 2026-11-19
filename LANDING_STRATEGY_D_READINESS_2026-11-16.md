# Strategy D — final landing readiness and merge preparation


| | |
|---|---|
| **Tested revision** | `170e611dcdfa3fe12e9abe58c2e60ee323aa461c` (PR #26 head) |
| **Evidence type** | Implementer-assessed landing readiness, based on recorded gate evidence. |
| **Historical?** | Yes — predates `0a7eaa0` and this follow-up. |
| **Author** | The PC5 implementer. **Not an independent assessment.** |

**Task:** read-only merge preparation for the PR #26 + PR #27 stack on `SpicyBadran/pc5`
**Date:** 2026-11-16
**Prior audit:** `AUDIT_PR26_PR27_INDEPENDENT_2026-11-16.md` (sha256 `d2cd1136…`, 280 lines)
**Mode:** read-only. Nothing was merged, approved, reviewed, commented, pushed, retargeted, rebased, squashed, force-pushed, or modified. Branch protection was not changed and no repository file was edited. The only file written is this report.

---

## Verdict

# READY FOR HUMAN APPROVAL

**Strategy D remains valid and is structurally exact — not merely workable.** All three landing-blocker categories are clear: topology is linear, content is verified, and the merge is a guaranteed no-content-change fast-forward. What remains is **one non-technical requirement that I cannot satisfy myself**: a human approving review from an identity other than the author.

Two blockers are recorded below as **B1** and **B2**. Neither is a code or topology defect. B1 is a hard requirement of the review process; B2 is an optional hardening step.

**The repository is unprotected and has no CI running on GitHub.** That is not permission to skip review — the prior audit proved the test suite cannot distinguish the defective revision from the fixed one, so the green-suite signal is worthless here and human judgement is the only real gate.

---

## 1. Exact live PR heads and bases

Re-verified at the **start** and again at the **end** of this task; both readings identical.

| Item | Value | Expected | Match |
|---|---|---|---|
| `main` | `8aab6d3864bb2e867a70bba28e7bfb52f43d7e35` | `8aab6d38…` | yes |
| PR #26 head SHA | `170e611dcdfa3fe12e9abe58c2e60ee323aa461c` | `170e611d…` | yes |
| PR #26 head branch | `arena/af939eb1-pc5` | same | yes |
| PR #26 base branch | `main` | `main` | yes |
| PR #27 head SHA | `9d4a79db2e078638bc10c7a8c54ed6182d55f115` | `9d4a79db…` | yes |
| PR #27 head branch | `arena/17f0c4f8-pc5` | same | yes |
| PR #27 base branch | `arena/af939eb1-pc5` | same | yes |
| PR #26 state | OPEN, not draft, unmerged | — | yes |
| PR #27 state | OPEN, not draft, unmerged | — | yes |
| PR #26 mergeable | `MERGEABLE` / `CLEAN` | — | yes |
| PR #27 mergeable | `MERGEABLE` / `CLEAN` | — | yes |
| Both PRs same-repo | `cross_repo=false` (branches in `SpicyBadran/pc5`, not forks) | — | yes |

**Neither PR has changed. The stop condition did not trigger.**

`mergeable` is reported only as observation; no eligibility claim in this report rests on it (see §3).

## 2. Current branch topology

Strictly linear — verified by ancestry, not by inspection of SHAs:

```
main  8aab6d386
  │  (15 commits: the 64-bit foundation and the PR #26 review fixes)
  ▼
arena/af939eb1-pc5  170e611dc   ← PR #26 head, base = main
  │  (14 commits: ed9c6a3, ec16c65, a9b383d, 9d4a79d + 10 earlier)
  ▼
arena/17f0c4f8-pc5  9d4a79db2  ← PR #27 head, base = PR #26 head
```

| Check | Result |
|---|---|
| `main` is ancestor of PR #26 head | **yes** |
| PR #26 head is ancestor of PR #27 head | **yes** |
| merge-base(`main`, PR #27 head) | `8aab6d386` = `main` |
| `main..PR26` | 15 commits |
| `PR26..PR27` | 14 commits |
| `main..PR27` | **29 commits** |
| Divergence / merge conflict | **none** — no rebase needed |

**The D1/D2 remediation exists only in PR #27's 14 commits.** Verified by commit-presence count in each range:

| Commit | In `main..PR26` | In `main..PR27` |
|---|---|---|
| `ed9c6a3` (D1: 4 GiB map) | **0** | 1 |
| `ec16c65` (D2: AllocateMaxAddress) | **0** | 1 |
| `a9b383d` (supersede docs) | **0** | 1 |
| `9d4a79d` (records) | **0** | 1 |

PR #26 at `170e611` is therefore **still the defective intermediate** and must not reach `main` alone.

## 3. Branch-protection and approval requirements

Branch protection could **not** be read via `GET /branches/{branch}/protection` (HTTP 403 — the GitHub App installation token lacks the administration scope). Rather than infer it from `mergeable`, I obtained it authoritatively from a different endpoint:

| Branch | `protected` | `protection.enabled` | `required_status_checks.enforcement_level` | Checks/contexts |
|---|---|---|---|---|
| `main` | **false** | **false** | `off` | `[]` / `[]` |
| `arena/af939eb1-pc5` | **false** | **false** | `off` | `[]` / `[]` |
| `arena/17f0c4f8-pc5` | **false** | **false** | `off` | `[]` / `[]` |

| Requirement | Status |
|---|---|
| Approving review required? | **No — not enforced.** No protection rule exists on any branch. |
| Approval from someone other than author? | Not enforced. **But see B1 — recommended regardless.** |
| CODEOWNERS approval required? | **No.** No `CODEOWNERS` file exists anywhere on `main` (checked `CODEOWNERS`, `.github/CODEOWNERS`, `docs/CODEOWNERS`). |
| Required status checks satisfied? | **N/A — zero required checks configured.** `enforcement_level: off`. |
| Stale / dismissed approvals? | **N/A — zero approvals exist** (see below). |
| Repository rulesets? | `[]` — none. |
| Merge settings | merge commit, squash, and rebase all **allowed**; auto-merge **disabled**; `delete_branch_on_merge: false`. |
| May a PR target another PR's head branch? | **Yes** — PR #27 already does, with `cross_repo=false`. |

**Review state re-verified (as required), still zero:**

| | reviews | latestReviews | reviewRequests | comments | reviewDecision |
|---|---|---|---|---|---|
| PR #26 | **0** | 0 | 0 | **0** | `""` |
| PR #27 | **0** | 0 | 0 | **0** | `""` |

**Author identity matters here.** Both PRs are authored by `SpicyBadran`, and the authenticated `gh` identity is **also** `SpicyBadran`. GitHub forbids approving your own PR. This is exactly why my standing constraint (never approve my own work) and this task's read-only boundary coincide — but it also means **an independent human reviewer must be the approving identity**. See B1.

## 4. CI / status-check results for both PRs

**There is no CI running on GitHub at all.** This is not a scoping artifact — it was verified from three independent directions:

| Check | Result |
|---|---|
| Actions workflows defined | **`total_count = 0`** |
| Workflow runs (any branch) | **none** |
| `.github/workflows/` directory | **does not exist** on `main`, on PR #26, or on PR #27 |
| `.github/` contents | 4 files only: `ISSUE_TEMPLATE/{bug_report,config,feature_request}.yml`, `PULL_REQUEST_TEMPLATE.md` |
| Check-runs on `9d4a79d` | `total_count = 0` |
| Combined commit status on `9d4a79d` | `state = pending`, `total_count = 0` |
| `statusCheckRollup` on both PRs | `[]` |

`ci/build.yml` exists in the repository but is **not** a GitHub workflow — it is a dormant file awaiting activation. The repository documents both the intent and the exact reason for the failure, in `README.md:199` (milestone `M1b`) and `ci/README.md`: activating it requires the `git mv ci/build.yml .github/workflows/build.yml` operation, and GitHub rejected exactly that push because the App installation lacks the **Workflows** permission. The documented remote rejection is:

```
 ! [remote rejected] … (refusing to allow a GitHub App to create or update
   workflow `.github/workflows/build.yml` without `workflows` permission)
```

The repository's own reading of the consequence matches mine exactly: `gh api repos/SpicyBadran/pc5/actions/workflows --jq '.total_count'` → `0`.

**Consequences for this landing:**
- No status check can be "failing" or "pending" in a blocking sense. `statusCheckRollup: []` reflects an absence of CI, **not** a passing build.
- `.github/` is byte-identical between `main` and PR #27's head — verified `git diff` is empty across `main..PR27`. The landing neither adds nor removes workflow configuration.
- **CI green is not available as evidence, and the prior audit proved it would be misleading anyway:** PR #26 at `170e611` is fully green (612 tests) while carrying both defects. Verification for this landing rests on the independent engineering evidence in §5 and the prior audit, not on GitHub checks.

## 5. Does Strategy D remain valid?

**Yes — and it is structurally exact.** All six validation criteria hold:

**(a) The merge preserves history.** Merging PR #27 into `arena/af939eb1-pc5` adds the 14 commits as a fast-forward; PR #26 head becomes PR #27 head. No commit is rewritten, reordered, or duplicated.

**(b) The merge is proven to change no content.** I computed the merge tree locally and compared trees:

| Tree | SHA |
|---|---|
| PR #27 head `9d4a79d` tree (the audited tree) | `d63b04c4a36c13a41d8a0b0d74b27ccbd4cacb28` |
| `merge-tree(PR26 head, PR27 head)` | `d63b04c4a36c13a41d8a0b0d74b27ccbd4cacb28` |
| `main` tree today | `6d76d3f7b73d22aea1ba19a37885f19b988ef0f1` |

**Merged tree == audited tree exactly.** After step 1, PR #26's head content is *byte-for-byte the tree the independent audit verified (100/100 D1 oracle, D2 A/B reproduced, 676/676 tests)*. This is the single strongest property of the whole plan: the artifact being approved is provably identical to the artifact that was audited.

**(c) PR #26 then represents the complete combined stack against `main`.** The combined `main..PR27` diff is **83 files changed, 7857 insertions(+), 166 deletions(-)**, and contains both fixes:

- D1 — `pagetables.c`: full-block zeroing `for (i = 0; i < PC5_PT_PAGES * PC5_PT_ENTRIES_PER_PAGE; i++)` plus the span-derived `pdpt_used` loop.
- D2 — `uefi64_main.c`: `u64 address = PC5_IDENTITY_LIMIT - 1ull;` and `if ((address & 0xFFFull) != 0 || last_page >= PC5_IDENTITY_LIMIT / 4096ull)`.
- Stub conformance — `uefi_stub_firmware.py`: `ignore_max_address` flag, `elif alloc_type == EFI_ALLOCATE_MAX_ADDRESS and not self.ignore_max_address:`, `ceiling = min(requested + 1, self.pool_ceiling())`.

**(d) No unrelated commits or unexpected files entered either branch.** The combined diff contains **zero deletions** (`git diff --name-status main..PR27 | grep '^D'` is empty) across all 83 files — nothing was removed from `main`. No `.pup`, `.zip`, `.tar`, `.bin`, `.exe`, `.iso`, or `vendor` path appears in the combined diff. `PS5UPDATE.PUP` remains the untouched 135-byte LFS pointer. No Sony firmware or proprietary payload is added.

**(e) The defective intermediate cannot reach `main`.** PR #26's `main`-facing content becomes the audited tree only after step 1. Before step 1, merging PR #26 would publish `170e611`, which the prior audit showed is green-but-defective. The sequence below makes that window impossible to enter accidentally, and §7 flags it as the one catastrophic misstep.

**(f) Engineering state still reproduces.** Not taken on trust — re-executed this task on a fresh detached worktree at `9d4a79d`, `SOURCE_DATE_EPOCH=1700000000`:

| Check | Result |
|---|---|
| `make all` | exit **0** |
| `make -s check` | exit **0** — `Ran 676 tests` — **`OK`** |
| Tracked files modified by the canonical sequence | **0** |
| Pinned-epoch ISO sha256 | `6b5b24ceea8d453dc6d19d879f7d565a84d39fd5f4608365b5281c66e9ac00ea` |

**One self-correction, recorded rather than hidden.** My first run reported `FAILED (errors=65, skipped=22)`. That was **not** a code or regression problem: `unicorn` (and 22 skip conditions) were absent from the freshly re-materialized sandbox. After restoring the documented dependencies (`unicorn` 2.1.4, `resvg_py`, `v86-system`), the identical tree produced exit 0 / 676 / OK. I report this because an unexplained 65-error result would otherwise look like a regression, and because it demonstrates the suite genuinely exercises the emulator rather than passing vacuously.

Per the task's §4, the following were **not** changed: code, tests, evidence records, documentation, release artifacts.

**Preserved engineering claims, unchanged and not re-litigated:** D1 and D2 independently reproduced and fixed; 676 tests, zero skips under `make all && make check`; no tracked files modified by that sequence; ISO and kernel release artifacts bit-identical across the remediation; **OVMF/QEMU not tested**; **no physical hardware and no genuine Sony execution**; two Low-severity test-harness weaknesses documented (`L1` vacuous PD-tail loop at `tests/test_pagetables64.py:348`; `L2` stub raising `StubError` instead of returning `EFI_OUT_OF_RESOURCES`); zero Critical, High, or Medium findings.

## 6. Exact proposed merge sequence

Nothing here has been executed. These are the commands a human executes **after** granting an independent approving review.

```bash
# 0. Confirm nothing moved since this report (both must match §1).
git ls-remote origin refs/heads/main \
                     refs/heads/arena/af939eb1-pc5 \
                     refs/heads/arena/17f0c4f8-pc5

# 1. Merge PR #27 into its existing base (PR #26's head branch).
#    Merge commit, not squash/rebase: squash would collapse the 14 commits and
#    rebase would rewrite them. Both destroy the reviewed history.
gh pr merge 27 --merge

# 2. VERIFY the base branch now carries the full stack before touching main.
#    Expected: PR26 head == 9d4a79d's tree == d63b04c4a36c13a41d8a0b0d74b27ccbd4cacb28
gh api repos/SpicyBadran/pc5/git/ref/heads/arena/af939eb1-pc5 --jq .object.sha
git fetch origin arena/af939eb1-pc5
git rev-parse FETCH_HEAD^{tree}          # must be d63b04c4…

# 3. Confirm PR #26 now shows the complete combined change against main.
gh pr view 26 --json headRefOid,changedFiles,additions,deletions
#    Expected: 83 files, +7857 / -166 (matching §5c). NOT 54 files / +4719 / -80.

# 4. Re-check mergeability and review state on the NEW head.
gh pr view 26 --json mergeable,mergeStateStatus,reviewDecision
gh pr view 26 --json reviews --jq '.reviews | length'

# 5. Only then merge PR #26 into main.
gh pr merge 26 --merge
```

**Ordering is mandatory and non-substitutable.** Step 1 must complete and step 2 must be verified **before** step 5. Step 5 at the original `170e611` would land both defects on `main`.

**Merge strategy for both steps: merge commit (`--merge`).** Squash is allowed by repo settings but would collapse 14 reviewed commits into one and erase the D1/D2 fix commits as discrete units; rebase would rewrite the 14 SHAs, invalidating the review that was performed against `9d4a79d` and breaking the tree-equality proof in §5b. Auto-merge is disabled repo-wide, so no `--auto` path exists.

## 7. Remaining blockers

### B1 — No independent approving review exists (HARD, non-technical, must be cleared before step 1)

Both PRs have **0 reviews, 0 comments, 0 review requests, `reviewDecision: ""`**, and both are authored by `SpicyBadran` — the same identity `gh` is authenticated as. GitHub will not permit that identity to approve its own PR.

This is a **process** blocker, not a technical one. It must not be waved through on the grounds that branches are unprotected: the repository *permits* merging with zero approvals, but the prior audit established that **the test suite cannot distinguish the defective revision from the fixed one** (the defective `pagetables.c` passes PR #26's 19 D1 tests; so does the fixed one; and at `170e611` the stub itself encodes the D2 bug). "No required checks" and "no required approvals" together mean **nothing automated stands between a defect and `main`**. The human review is the entire control.

A reviewer of record should read `AUDIT_PR26_PR27_INDEPENDENT_2026-11-16.md` §C, §E, §F, §H and the two Low findings, plus §5b of this report (tree equality). I cannot supply this approval: this task is read-only, and my standing constraint forbids approving my own work.

### B2 — No CI on GitHub; branch protection off (informational for this landing, recommended hardening afterwards)

There is no automated gate on `main` and none can be created from here (the App lacks the `Workflows` permission; this is documented in `ci/README.md` as milestone `M1b`). Recommended, **separately and after landing**:
- Enable protection on `main` requiring at least one approving review.
- Activate `ci/build.yml` as `.github/workflows/build.yml` once a token with `workflows` scope is available.

**Neither B1 nor B2 is a code, content, or topology defect, and neither invalidates Strategy D.** B1 must be cleared by a human before step 1; B2 may follow the landing.

### Non-blockers explicitly cleared

| Item | Status |
|---|---|
| Merge conflicts | None — linear ancestry, `merge-tree` succeeds |
| Rebasing / force-push needed | **No** — task's escape clause not invoked |
| Duplicated commits | None — 15 + 14 = 29, no overlap |
| Loss of PR #26 work | None — all 15 commits preserved; zero deletions |
| Loss of PR #27 work | None — all 14 commits preserved |
| Unexpected files | None — no archives, binaries, firmware, or vendor paths |
| Diff accuracy | Confirmed against the exact SHAs |
| Repository file modifications from this task | None |
| Release artifacts touched | None |

## 8. Final independent landing check

| Requirement | Result |
|---|---|
| Both PRs still contain the expected commits | **PASS** — 15 / 14 / 29, ancestry linear |
| Combined diff contains the D1/D2 remediation | **PASS** — all three fix sites present in `main..PR27` |
| No unrelated commits or unexpected files | **PASS** — 83 files, 0 deletions, no binaries/PUP/vendor |
| No required checks failing or pending | **PASS (vacuous)** — zero required checks exist; no CI to fail |
| PR #26 diff against `main` = complete intended stack | **PASS after step 1** — tree equals `d63b04c4…` |
| Sequence cannot expose the defective intermediate | **PASS** — step 2 gate + step 5 ordering |
| All repository-required human approvals present | **FAIL — B1.** Zero reviews; none required by config, but the only real control |

---

## Verdict

# READY FOR HUMAN APPROVAL

Strategy D is valid, structurally exact, and provably content-preserving: merging PR #27 into `arena/af939eb1-pc5` yields a tree **identical to the independently audited tree**, and all 29 commits are preserved with zero deletions and no rewrite.

The single outstanding item is **B1**: an independent human approving review. It is a process requirement, not a technical defect — and because the repository has no branch protection and no CI, that review is the *only* thing that would stop the defective `170e611` from reaching `main` if the merge order were ever mistaken.

**Do not treat `mergeable=true` as merge-readiness.** GitHub says `MERGEABLE` for PR #26 *right now*, at the defective `170e611`, with zero reviews and zero CI. That field measures conflict-freedom only. The readiness above rests on tree equality with the audited artifact, the commit-level presence of both fixes, and the ordered gate at step 2.
