# 01 — Live GitHub state, re-verified from scratch

Raw API responses for this file are in `raw/`, next to it. Nothing below is carried over from an
earlier report, a PR description, or a memory: every field was read from `gh api` at
**`2026-11-22T05:24:13Z`** (`raw/probed-at.txt`) and re-checked at 05:25–05:26 for the details.

## Commands

```sh
date -u
gh api repos/SpicyBadran/PC5/git/refs/heads/<ref>                       # raw/refs.txt
gh api repos/SpicyBadran/PC5/pulls/{37..41}                             # raw/pr{37..41}.json
gh api repos/SpicyBadran/PC5/branches/main/protection                   # raw/gh-probes.txt
gh api -X GET repos/SpicyBadran/PC5/branches/protection/enabled
gh api repos/SpicyBadran/PC5/contents/.github/workflows?ref=main
gh api repos/SpicyBadran/PC5/actions/workflows --jq .total_count
gh api repos/SpicyBadran/PC5/commits/<sha>/check-runs
gh api repos/SpicyBadran/PC5/commits/<sha>/check-suites                 # raw/cs41.json
gh api repos/SpicyBadran/PC5/issues/40/comments
gh api repos/SpicyBadran/PC5/pulls/40/reviews
gh api "repos/SpicyBadran/PC5/contents/docs/evidence/dashboard-reconciliation.json?ref=<ref>"
git fetch origin main feat/readme-progress-dashboard fix/dashboard-reconciliation-receipts
git merge-base / git rev-list --count                                     # raw/ahead-behind.txt
```

## Branches

| ref | full SHA |
|---|---|
| `main` | `52f682c8435956c8210b3189cad23ebea973d2de` |
| `feat/readme-progress-dashboard` (#40 head) | `8e3f0b642a92c2d2e27ba6d2a066803a70165697` |
| `fix/dashboard-reconciliation-receipts` (#41 head) | `5bc5d5d4353f2e2c520224fd48711556b0054fc4` |

`main` **has not moved** since the previous round's recorded value — verified, not assumed.

## Open pull requests — all five are drafts

| PR | title (verbatim, from the API) | head → base | ahead / behind | commits | files | lines |
|---|---|---|---|---|---|---|
| #37 | `docs: publish the M17 security-boundary analysis` | `d09949226c39052fa04827c2c9b440129735a312` → `main` | not re-derived | 4 | 9 | +1538/−0 |
| #38 | `docs(evidence): independent review of PR #37` | `c7571e4af4bf329daa0bbffdc6b4fd4ad97e5c46` → `main` | not re-derived | 1 | 4 | +929/−0 |
| #39 | `docs(evidence): M18 synthetic-package acceptance` | `003bbdcdef29bdeada06b029828487caa8f6b058` → `main` | not re-derived | 1 | 1 | +275/−0 |
| #40 | `feat(readme): generated progress matrix, and the rules for keeping it true` | `8e3f0b642a92c2d2e27ba6d2a066803a70165697` → `main` | **1 / 0** | 1 | 9 | +1944/−18 |
| #41 | `Fix #40's false evidence claim, make its post-merge duty testable, and close m2/m4/m6` | `5bc5d5d4353f2e2c520224fd48711556b0054fc4` → `feat/readme-progress-dashboard` | **9 / 0** | 9 | 13 | +2928/−320 |

Two corrections to what earlier reports in this session asserted:

- **#40 is a draft** (`"draft": true`). It was described as ready. It cannot be merged as-is; a
  human has to mark it ready first.
- **#37/#38/#39's titles are evidence/review documents, not the feature names** the session memory
  recorded for them. They are out of scope here except as context for the stack.

`mergeable` is `true` and `mergeable_state` is **`clean`** for all five, including #41. Read that
with the checks section below: `clean` here means *nothing is blocking the merge button*, and with
no required checks (protection not observable, see below) the button would work for anyone who can
push to `main`. It is not an endorsement of the code, and it is not evidence that anything was run.

## Git-verified relationship between #40 and #41

```
git merge-base 5bc5d5d… origin/feat/readme-progress-dashboard → 8e3f0b642a92c2d2e27ba6d2a066803a70165697
git rev-list --count 8e3f0b6…..5bc5d5d…  → 9      (#41's own commits)
git rev-list --count 5bc5d5d…..8e3f0b6…  → 0      (#41 contains all of #40)
git rev-list --count 52f682c…..8e3f0b6…  → 1      (#40 is one commit ahead of main)
git rev-list --count 8e3f0b6…..52f682c…  → 0      (#40 is not behind main)
```

So #41 is exactly #40 plus nine commits, and #40 is an up-to-date single commit off `main`. The
stack is sound at this instant; §6 of this audit is what breaks that.

## Checks

| probe | result | meaning |
|---|---|---|
| `.github/workflows` on `main` | **404 — does not exist** | no GitHub Actions CI in this repository |
| `actions/workflows.total_count` | `0` | no workflow has ever been configured |
| `actions/workflows` runs | `[]` | no run history |
| `commits/<head>/check-runs` for `8e3f0b6`, `5bc5d5d` | `0` for both | no check run completed for either head |
| `commits/<head>/check-suites` | **3 queued** at both heads: apps `netlify`, `vercel`, `freebuff-web`; `status: queued`, `conclusion: null`, `latest_execution_at: null` | third-party app suites that were created and never executed. They are not this project's CI, they produce no verdict, and they gate nothing |
| `commits/<head>/status.contexts` | `0`, combined `state: pending` | no commit statuses; "pending" with zero contexts is GitHub's default, not a running check |

**Verdict: there is no CI.** The only check activity on these commits is three external app suites
sitting in `queued` with no conclusion. Anyone who reads "3 check suites" as "CI ran" would be
wrong in the direction that matters — an unexecuted suite vouches for nothing.

## Reviews and comments

`#40`: 0 issue comments, 0 reviews, 0 review comments. `#41`: 0 / 0 / 0. Neither PR has been
reviewed by a human or a bot. No `reviewDecision` was returned for the drafts (the API reports
`REVIEW_REQUIRED` only for ready PRs; the earlier round's "reviewDecision=none" for #40 is not
reproducible now that #40 is a draft).

## Branch protection — **unknown, not absent**

```
gh api repos/SpicyBadran/PC5/branches/main/protection
  → 403 {"message":"Resource not accessible by integration", ...}
```

403 with a token that can read and write the repository means the *permission level* is
insufficient to answer. It does **not** mean `main` is unprotected. Recorded as unknown, per the
standing instruction. What I could verify independently:

- nothing in the tooling merges anything: `tools/dashboard_reconcile.py` only reads git and writes
  files inside the checkout;
- `AGENTS.md` §1 step 7 requires the reconciliation to arrive as a pull request "for a human to
  merge" and forbids committing straight to `main`;
- but no *machine* check prevents the author-agent from merging its own PR. That is a human control
  (a norm in `AGENTS.md`), not an enforced one. If protection is in fact absent, the honest
  statement is "the guarantee is procedural" — and this audit's central finding (F-1) is exactly
  what a self-certifying commit could do with a procedural-only control.

## The receipt

`docs/evidence/dashboard-reconciliation.json` was requested by path at `main`,
`feat/readme-progress-dashboard` and `fix/dashboard-reconciliation-receipts`: **404 on all three**.
No reconciliation receipt exists on any ref. That is consistent with the design — a receipt can only
reach a target through a merge, and #41's own receipt commits are, by construction, written after
the branch tip they vouch for — and it is the reason `make dashboard-reconcile` on a fresh clone
exits 1 with `OUTSTANDING` (reproduced in §7).
