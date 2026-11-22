# 06 — The stacked-PR strategy, evaluated

Evaluated only. **No branch was changed, no PR was merged, retargeted, closed or commented on**,
and nothing in this section is a claim that anything was done — it is the order I would recommend and
what each alternative costs.

## The topology, from Git (not from the PR descriptions)

```
main                                52f682c8435956c8210b3189cad23ebea973d2de
  └─ #40  feat/readme-progress-dashboard   8e3f0b642a92c2d2e27ba6d2a066803a70165697   (1 commit, +1944/−18)
       └─ #41  fix/dashboard-reconciliation-receipts  5bc5d5d4353f2e2c520224fd48711556b0054fc4  (9 commits, +2928/−320)
            └─ this audit's fix  fix/reconcile-exemption-is-verified  (4 commits, see §07)
```

`git merge-base 5bc5d5d origin/feat/readme-progress-dashboard` → `8e3f0b6`;
`git rev-list --count 8e3f0b6..5bc5d5d` → 9; `…5bc5d5d..8e3f0b6` → 0. So #41 contains **all of #40**
plus exactly its own nine commits, and #40 is one commit ahead of `main`, zero behind.

The structural fact that decides everything below: **#41's base ref *is* #40's head ref.**
`feat/readme-progress-dashboard` is simultaneously "the branch #40 will merge from" and "the branch
#41 will merge into". That is what makes the stack cheap to review and fragile to touch.

## Merge order

**Recommended: #40, then #41, then this fix, then the bootstrap PR — with one bookkeeping step.**

1. **Mark #40 ready and merge it.** It is a draft (`"draft": true` from the API at 05:24 UTC), which
   an earlier report in this session recorded as "ready"; that is wrong and it blocks step 1 until a
   human changes it. `main` is 0 behind #40, so any merge method lands the same content; a
   `Merge commit` keeps `8e3f0b6` as an ancestor, and **that is what the stack needs**.
2. **Merge #41 into `feat/readme-progress-dashboard`.** #41 is 9 ahead / 0 behind its base, so this
   is a clean fast-forward-able merge and the branch tip becomes `5bc5d5d` (or a merge commit above
   it).
3. **Retarget a follow-up at `main`, or open one.** Step 2 did **not** put anything on `main` — a PR
   merged into a feature branch stays a feature-branch event. Someone must now bring
   `feat/readme-progress-dashboard` (now containing #41) to `main`: either retarget #41's base to
   `main` in the GitHub UI (its diff recomputes to the same 13 files, because #40 is already in
   `main` after step 1) or merge the base branch forward. Retargeting is the smaller action and it
   keeps the review history attached to the commits that were reviewed.
4. **Then the fix branch.** `fix/reconcile-exemption-is-verified` (4 commits, §07) is currently
   stacked on #41's head; after step 3 its content is a strict superset-of-nothing and applies
   cleanly.

**Why not merge #41 first.** It would be *allowed* — GitHub permits merging a PR whose base is a
branch, and the merge is clean — but `main` would then have no #40, the base branch would carry both
while `main` carried neither, and step 3 becomes "merge a 10-commit branch containing someone else's
unmerged PR". Reviewing #41 without #40 also means reading a diff that **rewrites every file #40 adds** — measured,
not estimated:

```
git diff --name-only 52f682c..8e3f0b6 | sort            # #40: 9 files
git diff --name-only 8e3f0b6..5bc5d5d | sort           # #41: 13 files
comm -12 <(#40) <(#41)                                  # shared: all 9 of them
comm -13 <(#40) <(#41)                                  # #41-only: 4
```

Shared: `AGENTS.md`, `CONTRIBUTING.md`, `Makefile`, `README.md`, `docs/PRESENTATION.md`,
`docs/assets/pc5-progress-matrix.svg`, `tests/test_progress_matrix.py`,
`tools/dashboard_reconcile.py`, `tools/presentation.py` — i.e. #41 rewrote *the whole of #40*,
including the reconciler it had just introduced. #41-only: `.github/PULL_REQUEST_TEMPLATE.md`,
`docs/TEST_RESULTS.md`, `tests/test_dashboard_reconcile.py`, `tests/test_presentation.py`. **Order exists to make the second review cheap; the reverse order makes it impossible.**

## What breaks if #40 changes

| change to #40 | consequence for #41 |
|---|---|
| a **new commit pushed** on top of `feat/readme-progress-dashboard` | #41 becomes "N+1 ahead" of a base that now has an unreconciled tip; #41's diff stays valid (it is computed against the new base), and the merge stays clean unless the new commit edits one of the 9 shared files — where it *will* conflict, because #41 rewrote those files wholesale |
| an **amend / force-push** (reword, rebase, `git commit --amend`) | `merge-base(5bc5d5d, new-head)` stops being `8e3f0b6` and becomes `52f682c8` (main). #41's diff against its base then **re-adds all of #40's content as if it were new**, and GitHub shows #41 as a 10-commit PR with 13 files whose hunks duplicate the amended ones. Reviewers see a false, much larger change; the merge develops conflicts in every shared file |
| **#40 squash-merged** into `main` | same class of breakage permanently: the squashed SHA is not `8e3f0b6`, so #41's base branch and #41's commits share no tip; #41 must be rebased onto `main` and re-diffed before it can be reviewed again |
| **#40 rebase-merged** | every one of its commits is rewritten; #41's nine commits are on top of the old ones, so the whole stack needs `git rebase --onto` before it is reviewable |
| **#40 merged with a merge commit** | nothing breaks. `8e3f0b6` remains an ancestor of `main`, `merge-base` stays `8e3f0b6`, and #41's diff against its base is unchanged |

So the constraint on the maintainer is a one-liner: **#40 may be merged, not amended, squashed or
rebased, while #41 is open; and #41 may not be merged while #40 is unmerged.** (The second half is
not a Git limitation — it is the "review cheaply" argument above, and it also protects the receipt
chain in step 4 below.) The corollary the user asked about, stated plainly: **stacking buys review
granularity at the price of making the base branch immutable while the child is open.** With one
reviewer and one author, that price is low — and it is also why this audit recommends the fix arrive
as a *stacked third* PR rather than as a push to #41's branch.

## Can the bootstrap receipt land through this sequence?

**Yes — in two merges, and only in two.** Verified against disposable clones, not argued:

- No receipt exists anywhere today: `docs/evidence/dashboard-reconciliation.json` is 404 on `main`,
  on `feat/readme-progress-dashboard` and on `fix/dashboard-reconciliation-receipts`.
- `--record` **refuses** without an existing boundary on the target: it exits 2 with
  `no usable receipt at <target>: --record only extends an existing boundary. Bootstrap it
  explicitly with --record --boundary <full SHA> --inspect <sha>…` (scenario 1, exit 1 OUTSTANDING on
  a bare fresh clone of `5bc5d5d`; the ERROR text is the same class, verified on the fix tree too).
- A bootstrap written **on a feature branch** cannot be honoured from `main`:
  `validate_receipt()` raises `the receipt's boundary <sha> is not an ancestor of <target>: the
  receipt describes a different line of history` — scenarios 24/24b confirm a feature-branch receipt
  never makes `origin/main` read CURRENT. **So the first receipt cannot arrive by being committed on
  the PR that introduces the tool and left there: the tool reads the receipt from the target, and the
  target is `main`.**
- `--bootstrap` itself **refuses while a gate is red** (verified: exit 2,
  `ERROR — refusing to record: presentation figures and README blocks are current failed (exit 1): 1 presentation problem(s)`)
  — so the bootstrap cannot be used to launder an unreconciled state, which was the design claim and
  it holds.
- A receipt-only commit **is** exempt, so the loop closes: scenario 2 (bootstrap lands → `CURRENT`)
  and scenario 33 (a *second* merge after a legitimate record is still named) show both halves — the
  record commits, and the next real merge still owes a duty rather than being waved through.

Concretely, after step 3 above, `main` has the tool and no receipt. The fourth PR is then:
run `python3 tools/dashboard_reconcile.py --record --bootstrap --boundary <main tip> --inspect <the
#40 SHA> --inspect <the #41 merge SHA> --note "adopts the dashboard line"`, commit the single file it
writes, open the PR, and merge it. That merge is generated-output+receipt only → exempt → `CURRENT`.
**The duty and the tool therefore become live on `main` in exactly one extra merge, and the audit
verifies there is no way to skip that merge and no way to fake it from a branch.**

## What the strategy does *not* get you

- **No enforcement.** Protection on `main` is **unknown** (403 = the token cannot see it, not absent),
  there is no CI, no workflow file, no hook. A stacked sequence of PRs is reviewed by the same person
  who wrote it unless someone else reads it. That is a fact about the repository, not a criticism of
  the ordering.
- **No free ride for the fix.** If #40 or #41 is amended after this fix branch is opened, the fix
  has to be rebased over the same rewriting mess described above; the fix's tests are tied to
  #41's `report()` wording, deliberately, so a reword of the report line will fail a test rather than
  drift silently.
