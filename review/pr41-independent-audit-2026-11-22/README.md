# Independent audit of PR #41 — and the reconciliation fix it required

**Date:** 2026-11-22 (UTC). **Audited head:** `5bc5d5d4353f2e2c520224fd48711556b0054fc4`
(#41, `fix/dashboard-reconciliation-receipts`, a draft against `feat/readme-progress-dashboard` =
#40's `8e3f0b642a92c2d2e27ba6d2a066803a70165697`). **Nothing was merged, approved, closed, commented
on, retargeted or rebased** — the merge decision is the maintainer's, and no existing branch was
touched. Corrective work lives on a new branch, `fix/reconcile-exemption-is-verified`, whose four
commits sit on top of #41's head and are proposed as a stacked draft PR.

| file | what it is |
|---|---|
| `01-GITHUB-STATE.md` | live state, re-read from the API at 05:24 UTC: five draft PRs, full SHAs, ahead/behind from Git, checks, protection, receipts. Includes the two ways this session's earlier reports were wrong (#40 is a draft; three external check suites are queued and never ran) |
| `02-DIFF-REVIEW.md` | #41's 13-file diff, file by file, with what was read in full versus measured by execution |
| `03-FINDINGS.md` | **F-1 … F-8**, each with the scenario that reproduced it, the exit codes, the mechanism and what the fix does and does not close |
| `04-LIFECYCLE-TEST-MATRIX.md` | 54 scenario checks against disposable clones: 50 behaved as documented, 4 diverged; plus the coverage map against the requested scenario list |
| `05-README-VISUAL-REVIEW.md` | the reference dashboard image measured against `README.md` + `pc5-progress-matrix.svg`: what to copy, what must not be copied, and why a literal transplant would be a defect |
| `06-STACKED-STRATEGY.md` | merge order, the exact ways #40 can break #41, and whether the bootstrap receipt can land through this sequence (it can, in two merges — with the refusals that prove it) |
| `07-VERIFICATION.md` | fresh clone of #41's head (`make -j8 check` → exit 0, 1037 tests, 110 skips), the 14 negative controls before and after the fix, and the fix branch's real exit codes |
| `HASHES.txt` | full SHAs, per-file sha256 before/after (artwork identical on purpose), and the evidence-file register |
| `raw/` | untrimmed outputs: harness scripts, lifecycle JSON, logs, API dumps |

## The one-paragraph result

#41's mechanism is sound and its presentation layer is defended by measurement rather than by trust:
every displayed number was reproduced from `roadmap.json`, the contract tables, `psm/registry.py` and
`docs/evidence/**` without calling `progress_metrics()` (35/35), and 14 deliberately introduced lies
in that layer were all caught. The reconciliation layer is where it falls short, in one theme with
three shapes: **the generated-output exemption grants `CURRENT` on a premise the tool never
re-tests** — which lets a hand-written, damaged or deleted figure read as reconciled (F-3), lets a
commit clear itself by editing the receipt (F-1, security-relevant), and lets `--record` answer 0 over
red gates because verification sits below the "nothing to record" return (F-2). Three smaller ones
(no-op merge cleared, flags silently ignored, `--json` printing prose on a refusal) came from the same
class, and one assertion in the PR's own test suite turned out to be structurally dead: a report line
that could only ever read `0 already named in the receipt` (F-7). All seven fixes are on
`fix/reconcile-exemption-is-verified`, each pinned by a scenario that **fails on #41 and passes on
the fix** — and the fix adds 7 tests without weakening one.
