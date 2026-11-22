# 02 — File-by-file review of #41's final diff

Scope: `git diff 8e3f0b642a92c2d2e27ba6d2a066803a70165697..5bc5d5d4353f2e2c520224fd48711556b0054fc4`
— 13 files, +2928/−320, 9 commits, verified as #41's complete diff against its base
(`git merge-base` = `8e3f0b6`, `rev-list --count` = 9). No file contents were taken from PR
descriptions; everything below was read out of the tree at `5bc5d5d` (detached worktree) and
re-derived where a claim was testable.

| file | ± | verdict |
|---|---|---|
| `tools/dashboard_reconcile.py` | +843/−… | the substance; correct mechanism, four defects (F-1…F-5) |
| `tests/test_dashboard_reconcile.py` | +1052 | 46 scenarios, well built; one assertion structurally unreachable (F-7) |
| `tests/test_progress_matrix.py` | +384 | geometry + artwork-vs-data checks; sound, measured not decorative |
| `tests/test_presentation.py` | +86/−… | byte-exactness and denominator rules; sound |
| `tools/presentation.py` | +172 | M5 (length) not addressed by design; the measured-geometry helpers are real |
| `docs/assets/pc5-progress-matrix.svg` | +289/−289 | regenerated; every displayed number independently reproduced (§04/05) |
| `AGENTS.md` | +100/−… | the standing duty, 9 steps; accurate, one sentence now needs the fix's wording |
| `CONTRIBUTING.md` | +27/−… | discoverable from the human path; states "no CI, no hook" explicitly |
| `docs/PRESENTATION.md` | +107/−… | receipt fields, terminology table, drawing guarantees; the strongest part |
| `docs/TEST_RESULTS.md` | +151 | §28 narrative; accurate about what was wrong, silent about what still was |
| `README.md` | +18/−… | three table rows corrected; evidence row now distinguishes cited vs filed |
| `Makefile` | +16/−… | the two targets answer two questions; `post-merge` is the write path |
| `.github/PULL_REQUEST_TEMPLATE.md` | +2/−1 | counts corrected to seven figures/six blocks, receipt checkbox added |

## `tools/dashboard_reconcile.py`

Read in full. What it does, as verified by execution rather than by reading:

- **Boundary from a receipt, not from history.** `SCHEMA = "pc5.dashboard-reconciliation/1"`,
  receipt at `docs/evidence/dashboard-reconciliation.json`, read **from the target ref**
  (`git show <target>:<path>`), so a local dirty copy cannot advance it. `DEFAULT_TARGET_REFS =
  ("origin/main", "origin/HEAD")`; `--here` opts into the checked-out branch and records that
  choice in the receipt. Reproduced: §04 scenarios 22, 22b, 24, 24b.
- **The range is first-parent only**: `rev-list --reverse --first-parent boundary..target`, so
  `outside_generated_blocks()` can take a single `base` per commit and `git diff <merge>^1 <merge>`
  is exactly "what the branch brought". Verified for squash, rebase-then-ff, ff, direct commit,
  octopus and reverse-oriented merges (33, 34, 35).
- **Classification is content-based**: a commit needs inspection if any changed path falls
  outside `GENERATED_OUTPUTS = ("docs/assets/",)` / `GENERATED_README = "README.md"` spans, using
  `--unified=0` hunks and new lines against `sha`, old lines against `parents[-1]`. Multi-parent
  commits additionally consult `reviewed_ancestry()`. Verified 1, 2, 4b–4e, 5, 6, 14.
- **Exactly two recorded gates**: `VERIFICATIONS = (presentation.py check, docs_check.py)`.
  `verify()` raises on a non-zero exit, on missing verifier files, and `record()` refuses to write
  while a non-generated file is dirty. Verified 17 (exit 2 naming `documentation links`), 18.
- **Exit codes are load-bearing**: 0 CURRENT, 1 OUTSTANDING, 2 cannot-answer. `range_commits()`
  deliberately has no `try`, so an unreadable repository never reads as an empty range. Verified
  21, 23, 31, 32 (missing `git` → exit 2, no traceback).
- **`bootstrap()` refuses** when the gates are red (verified: exit 2,
  `ERROR — refusing to record: presentation figures and README blocks are current failed (exit 1): 1 presentation problem(s)`),
  so a fresh fork cannot mint a clean bill of health.
- **Repeated reconciliation is idempotent**: re-running `--record` over the same range produces a
  byte-identical receipt and mutates no tracked file (25, 26).

Four defects, each reproduced with an exit code, are in `03-FINDINGS.md`. Two of them (F-1, F-3)
share one root cause: the exemption is granted by path and never re-proved.

## `tests/test_dashboard_reconcile.py`

46 scenarios over a disposable fixture (own bare origin, stub generator, stub docs-check, its own
Makefile) — the same structure I rebuilt independently in `/tmp/audit41/lifecycle_lib.py`, which
is why I could check the claims against a second implementation. `Ran 46 … OK (skipped=1)` on
`5bc5d5d`. Two problems found:

- **F-7 (dead assertion).** `test_s36` asserts `merge in state["merges_cleared_by_reviewed_content"]`,
  but `assess()` fills that list from commits that failed the inspection test first, so the list
  can only ever contain commits `classify()` labelled `covered`, which `assess()` routes to
  `exempt_generated_only` — so the list is *always empty* and the same for `recorded_in_receipt`.
  s36 dodges a false pass only because git declines to merge an already-contained branch and the
  test skips. This is why my harness had to reach scenario 33 by a different route.
- **No scenario for the paths the fix closes** (self-certification, red gates on the
  "nothing to record" path, no-op merge). That is what §05 adds.

## `tests/test_progress_matrix.py`, `tools/presentation.py`, the SVG

The geometry claims are real and measured, not asserted: `measure()` builds a throwaway SVG and
asks the layout guard for the string's box; `fit`/`fit_lines` shorten with `…`; `room_for` returns
the width a region heading has left after its right-aligned figure; `collisions()` asks whether two
labels on one baseline overlap; `svg_height` is fixed once at 1000 so a later region shrinking
cannot lift the canvas and leave a dead band. Every one of these is called *before* the row is
drawn, and the test module fails on any `…` appearing in the committed figure, which makes clipping
a last resort rather than a hiding place.

I did not accept `progress_metrics()` as truth for the review. §04 recomputes all twelve README
rows and the ladder from `roadmap.json`, the contract tables, `psm/registry.py`,
`docs/evidence/**` and the SVG — 35 checks, 35 reproduced, including that all 16 fractions in the
README appear verbatim in the SVG and that no `<text>` contains an ellipsis.

`presentation.py` stays the file M5 complained about (line count). The round-1 decision not to
split it for a number was re-checked against the only concrete argument in favour — testability —
and stands: the parts the audit needed to test are tested through the committed bytes, which is the
stricter route. Splitting it would have churned 1700 lines to move a number off a dashboard.

## The documentation files

`AGENTS.md` §1 is the standing duty in 9 numbered steps, each naming the command that proves it,
with the explicit denial of an always-on service; `CONTRIBUTING.md` gives a human the two commands
and the same denial; the README's `## Dashboard` paragraph now points at the receipt and says
"There is no CI watching this"; `docs/PRESENTATION.md` documents the receipt's fields and the
four-word table (cited / filed / validation / physical) that stops `evidence` meaning three things.

One sentence is now false in a way the fix must chase, in three files:
`AGENTS.md` step 7 and `CONTRIBUTING.md` both present the generated-output exemption as a pure
path rule that "keeps the loop closed", and `docs/PRESENTATION.md` describes `verification` as
"each command … the exit code it really returned". All three are accurate only once the exemption
is *tested*; the fix commits change the wording to match, and the docs↔test coupling in
`test_progress_matrix.py` (`PostMergePolicyTest`) was re-run to confirm it still holds.
