# §2 live state · §3 F-1…F-8 re-verified against both heads

## 2. Live remote state, re-read twice

| item | value |
|---|---|
| `main` | `52f682c8435956c8210b3189cad23ebea973d2de` ("Merge pull request #35 …") |
| #40 | head `8e3f0b642a92c2d2e27ba6d2a066803a70165697`, base `main`, open, **draft**, mergeable `true/clean`, 1 commit, 9 files, +1944/−18 |
| #41 | head `5bc5d5d4353f2e2c520224fd48711556b0054fc4`, base `feat/readme-progress-dashboard` (#40's branch), open, draft, `true/clean`, 9 commits, 13 files, +2928/−320 |
| #42 | head `709581450f24d61a23f4068c9b9df51e8729fc76`, base `fix/dashboard-reconciliation-receipts` (#41's branch), open, draft, `true/clean`, 6 commits, 48 files, +8780/−34 |
| stacking | `refs/pull/42/head` == `7095814…`; #42's `base.sha` == #41's `head.sha` exactly; #41's `base.sha` == #40's `head.sha` exactly. The claimed stack is real |
| attention | 0 issue comments, 0 reviews, 0 review comments on #40, #41 and #42 |
| CI | 0 check-runs on all three heads; 3 check-suites (`netlify`, `vercel`, `freebuff-web`) all `queued`/`conclusion: null`; `actions/workflows` total_count 0; `actions/runs` total_count 0; `.github/workflows` absent from every ref |
| protection | `GET /branches/main/protection` → HTTP 403 "Resource not accessible by integration" → **unknown**, not "absent" and not "satisfied" |
| open PRs | 9 (not 5, as an earlier report stated). The three non-draft ones are #36 (`aed099d4fbf73c57c294ae017c48801a38b7512d`, 2 commits, 6 files, +649/−12), #34 (`547ebd5…`, based on the old main `56c58521580e88c4339ceb6eb40ce724a2bb533b`) and #31 (`ac17c5d…`, based on `46a9fda`). None of them touches any file in #40→#42's 48-file set |
| receipts | `docs/evidence/dashboard-reconciliation.json` is **absent** from `main`, #40, #41 and #42 (`git cat-file -e` on each). `tools/dashboard_reconcile.py` is absent from `main`, present from #40 onwards |

Every SHA the previous report quoted is still current. `review/pr41-independent-audit-2026-11-22/`
exists only on #42's branch (`review/` does not exist on `main`).

Two claims in the earlier report were wrong and are corrected here: the count of open PRs (9), and
`--target` semantics (F-6 below). A third — that a truncated committed asset makes
`make dashboard-reconcile` exit with an undocumented code — was my own measurement artifact, see F-3.

## 3. F-1…F-8, re-reproduced and then re-run, from the command line

Method. Every row is `python3 tools/dashboard_reconcile.py …` (or a `make` target) executed against
a disposable repository built by `raw/lib.py`: a real bare `origin`, real commits, real merges,
`tools/presentation.py sync` where AGENTS.md tells an operator to run it. The tool under test is the
one checked out at the head being audited — `raw/probe.py` ran the whole batch twice, once per tree,
and recorded exit code, stdout, `git status` and whether the receipt bytes changed. No row rests on
a helper function's return value.

| id | attack | #41 `5bc5d5d` | #42 `7095814` | verdict on #42 |
|---|---|---|---|---|
| F-1 | forged receipt (`reconciled_at` = HEAD) while a committed figure is stale | `0 CURRENT` | `1 OUTSTANDING` | **fixed, verified** |
| F-1b | self-consistent forgery: work commit, regenerate, and a receipt whose `reconciled_at` *is* that commit and which names it | `0 CURRENT` | `0 CURRENT` | **open, and unclosable in code** — see below |
| F-2 | hand-typed `\| Complete \| 10 \|` inside a generated README block, then `--record` | `0 nothing to record` | `2 refusing to record` | **fixed, verified** |
| F-3 | committed asset truncated (regeneration not run), read via `make -s dashboard-reconcile` | `0` | `2` — but that 2 is *make's* recipe status; the tool itself exits `1` on both trees when invoked directly | **fixed**; my earlier "undocumented code" note was a harness artifact |
| F-3b | committed asset **deleted** | `0 CURRENT` | `1 OUTSTANDING` | **fixed, verified** |
| F-3c | attribution: is a *stale asset* wrongly blamed on an unrelated commit? | `1` with `"outstanding": []` | `1` with `"outstanding": []` | **correct on both**: the stale output is its own finding |
| F-4 | a `--no-ff` merge of an already-contained branch (no-op merge) | `0 CURRENT` | `1 OUTSTANDING — … no-op merge … recorded as reviewed` | **fixed, verified** |
| F-5 | `--record --boundary X --inspect Y` without `--bootstrap` | `0` | `2 refused` | **partially closed**: `--note` without `--record` and `--boundary` on a read run are also refused (broader than claimed), but `--bootstrap` alone, `--dry-run` alone and `--here`+`--target` are still silent no-ops |
| F-6 | `--target <short sha>`, `--target HEAD`, `--target refs/heads/nope` | `0 / 0 / 2` | `0 / 0 / 2` | **not a code defect**: the earlier report's "cannot point at an arbitrary commit" was wrong; #41's own claim was accurate |
| F-7 | does the state ever *say* a merge was cleared by reviewed content? | `merges_cleared_by_reviewed_content: []` | non-empty under a real PR-shaped flow | **fixed, verified** |
| F-8 | `--json` when the answer is a refusal | `1`, stdout not parseable | `1`, `{"status":"outstanding",…}` parses | **fixed on the read path**; `--record --json` still prints prose (see FINDINGS R-5) |

### What `generated_output_agreement()` does now, and where it is still wrong

`inspect()` calls `generated_output_agrees(repo)` only when the range is non-empty *and* nothing is
outstanding — i.e. exactly when the generated-output exemption (or a receipt name) is what makes the
answer CURRENT. It calls `verify(repo, …)`, which runs **both** `VERIFICATIONS` in order
(`tools/presentation.py check`, then `tools/docs_check.py`) and raises on the first non-zero exit;
so a receipt-named commit cannot walk away green while the figures disagree with their data, and the
receipt's own `verification` array is not consulted by this decision (no self-certification: the
array is *displayed*, the gates are *re-run* — `raw/probe.py` F-1's second half, and
`tests/test_dashboard_reconcile.py::test_s42_*` on #42 cover it).

Two inaccuracies survive on #42, both reproduced verbatim in `raw/r2-pr42.json`:

1. `verify()` raises one exception class for both "a gate refused" and "a gate is not there", and
   `generated_output_agrees()` catches it, so a deleted `tools/docs_check.py` prints
   `OUTSTANDING — … that output is not what its data produces: its generated output no longer
   matches the data it renders (verifier tools/docs_check.py is missing; refusing to record a
   receipt over an unverifiable tree)` at **exit 1**. Nothing was measured, so "an inspection is
   due" is the wrong category (the write path answers 2 for the same tree), and the sentence
   asserts a mismatch nobody established.
2. A *documentation* failure reaches the same header: a broken link in `docs/BROKEN-LINK.md` with a
   clean figure set prints "the range is confined to generated output, and that output is not what
   its data produces … documentation links, anchors and commands resolve failed" — blaming the
   figures for a link problem and pointing the operator at `make presentation`, which cannot fix it.

Both are wording/category defects, not false greens: the tree still refuses to read as CURRENT, the
receipt is not written, and `make post-merge` surfaces the real cause. `FINDINGS.md` R-1/R-2 record
them; `fix/reconcile-premise-and-flags` closes them with `s46`/`s47`.

### F-1b, the residual that code cannot close

A commit's hash covers its own tree, which includes the receipt, so a receipt can never name the
commit that carries it — "self-certification by self-naming" is arithmetically impossible, and the
two forgeries that *are* possible (name yourself, or move the boundary past the commit) are now both
caught, because the exemption's premise is re-measured. What remains is a receipt that is *internally
consistent and false*: an author who regenerates the figures, commits them, and writes a receipt
naming that commit has produced a tree in which every claim is true as far as any file can tell. The
tool says CURRENT; the inspection it attests did not necessarily happen. That is governance (review,
`commit_citations.py`, and the fact that a receipt is a normal diff a human can read), not code, and
this review does not claim otherwise. It is listed as an **accepted residual**, not as a #42
regression — #42 neither creates nor worsens it, and #41's test for it was vacuous.
