# Findings register

Six classifications, used consistently:

* **closed in #42, verified** — the defect was in #41, #42 fixes it, and this review reproduced both
  the failure and the fix by running the public command against each head;
* **open on #42** — still wrong at `7095814`; a fix and a scenario exist on
  `fix/reconcile-premise-and-flags`;
* **partially closed on #42** — the class is narrowed but not exhausted;
* **report inaccuracy** — the *report* was wrong, the code was not (or the finding is mine);
* **accepted residual** — real, but not closable by this tool; needs a human control;
* **not verified** — outside what this environment can establish.

| id | class | severity | finding | evidence | fix |
|---|---|---|---|---|---|
| F-1 | closed in #42, verified | Critical (was) | a hand-written receipt naming HEAD read as CURRENT with no gate run | `raw/probe-results-*.json` row 1; `s39`/`s42` | #42: exemption premise re-measured in `inspect()` |
| F-1b | accepted residual | Medium | a receipt that is internally consistent *and* false still reads CURRENT: no file can prove an inspection happened, and a commit hash covers the receipt, so self-naming is impossible by construction | `raw/probe.py` `CERT-*`; same result on #41, #42 and the corrective branch | not code: review the receipt as a diff, `make citations-check`, AGENTS.md §1. **This review does not claim #42 causes it** |
| F-2 | closed in #42, verified | High (was) | `--record` on a hand-typed generated README block returned 0 "nothing to record" before running the gates | probe row 2 | #42: `verify()` moved above the early return (`s40`) |
| F-3 | closed in #42, verified | High (was) | a truncated or deleted `docs/assets/**` figure read as CURRENT | probe rows 3–4, `raw/probe3-*` MV-3 | #42: content check, not path check |
| F-3m | report inaccuracy | Informational | I first recorded F-3 as "exit 2 instead of the documented 1". `make -s dashboard-reconcile` returns 2 for *any* non-zero recipe status (`Makefile:324`); the tool returns 1. Measured side by side on `raw/probe3-results-pr42.json` | corrected here; see R-7 for the real, related defect | — |
| F-4 | closed in #42, verified | High (was) | a `--no-ff` merge of an already-contained branch self-approved the duty | probe row 6; `s41` | #42: no-op merges are outstanding until recorded |
| F-5 | **partially closed on #42** | Medium | #42 refuses `--boundary`/`--inspect` without `--bootstrap` and `--note` without `--record`. `--bootstrap` alone, `--dry-run` alone, and `--here` with `--target` are still accepted and then ignored, and the run answers the ordinary read at exit 0 | `raw/probe3-results-{pr41,pr42}.json` matrix rows, identical on both trees | corrective branch: refuse all three (`s48`) |
| F-6 | report inaccuracy | Informational | the previous report said the tool "cannot point at a commit" via `--target`; `--target <short-sha>` and `--target HEAD` both exit 0 on #41 *and* #42, and an unresolvable ref exits 2. #41's own claim was accurate | probe row 11; `raw/probe-results-*.json` | — (documentation is correct) |
| F-7 | closed in #42, verified | Medium | `recorded_in_receipt` and `merges_cleared_by_reviewed_content` were always empty, so the report could not say *why* a range was clear | probe rows 14, 21; `s44` | #42: classification order fixed |
| F-8 | closed in #42 (read path), verified | Low | `--json` printed prose when the answer was a refusal, so `json.load` failed identically for "duty due" and "tool broken" | probe row 24 | #42: `Outstanding`/`ReconcileError` answered as objects (`s45`) |
| R-1 | **open on #42** | Medium | a verifier that cannot run (e.g. `tools/docs_check.py` deleted) is reported on the read path as `OUTSTANDING … its generated output no longer matches the data it renders (verifier … is missing)` at exit 1 — a category error (nothing was measured) and a claim about content that no check supports. `--record` answers 2 for the same tree | `raw/r2-pr42.json` (exit 1, `outstanding: []`) vs `raw/r2-pr41.json` (exit 0 CURRENT on #41 — #42 improved it, but not to the documented category) | corrective branch: `VerifierMissing` propagates → exit 2 (`s46`) |
| R-2 | **open on #42** | Low | a documentation-gate failure is attributed to generated output ("that output is not what its data produces"), sending the operator to `make presentation` for a broken link | `raw/probe3-results-pr42.json` MV-2, verbatim in `raw/r2-pr42.json` | corrective branch: the report names the gate that refused; only the presentation gate may claim a data mismatch (`s47`) |
| R-3 | **open on #42** | Medium | `test_s36_a_merge_of_reviewed_content_clears_the_moment_it_lands` is **skipped in this environment** (`skipped=1` in the module, `skipped=110` in the suite) because `git merge --no-ff` declines to merge an already-contained branch. Its two assertions never run. Worse, they cannot pass: for that topology #42's own rule (s41) makes the merge *outstanding*, and the fixture also leaves `main moves on` unrecorded. A scenario that can only ever skip is a false green in the coverage count | `raw/fix-suite.log` vs `#42` (`1044 … skipped=110`) and the reconstructed `commit-tree` run in this branch, which fails against #42's tool for exactly the reason stated | corrective branch: build the merge with `commit-tree` and assert #42's documented behaviour (refusal + repair). Suite is now `1048 … skipped=109`, module `57 tests, no skip` |
| R-4 | **open on #42** | Low | `s39` contains `self.assert_current(fx.read()) if False else None` — an assertion written but disabled. Harmless (the neighbours cover it) but it is an assertion in the diff that asserts nothing, which is what this whole audit is about | `tests/test_dashboard_reconcile.py` line 984 at `7095814` | deleted on the corrective branch |
| R-5 | **open on #42** | Low | `--record --json` prints log prose on success (only refusals became JSON), so a caller that asked for the machine interface on the write path gets text; "a receipt was written" and "there was nothing to write" are not distinguishable without string matching | `raw/probe3-results-pr42.json` G-3-style row; the error case *is* JSON (stderr), which is correct | corrective branch: `{"status":"written"\|"nothing-to-record"\|"dry-run","exit":…,"lines":[…]}`, status from the writer (`s49`) |
| R-6 | **open on #42** | Informational | the post-merge duty is stated in all four documents *and* the tool's output, but not in `--help`, and the Makefile's own comment does not say that nothing runs either target | `raw/live-final.txt` (0 workflows, 0 runs) + the greps in `README-VISUAL-REVIEW.md` | corrective branch: module docstring + `--help` + Makefile comment |
| R-7 | **open on #42** | Low | the README contract table attributes the exit-code pair to `make dashboard-reconcile` ("exit 1 while one is unreconciled, exit 2 when the history cannot be read"), which `make` cannot express — a wrapper that branches on 1-vs-2 through `make` reads a permanent 2 | measured on #41 and #42 trees in `raw/sim.log` §A: tool `1`, `make` `2`, same state | corrective branch: README note under the row |
| R-8 | **not verified** | — | branch protection / required checks on `main` (HTTP 403), any CI actually running these gates (none exists: 0 workflows, 0 runs), physical-hardware claims in `docs/evidence/validation`, and the reference screenshot in §6 (the image is not present as a file in this workspace) | `raw/live-final.txt` | n/a — reported as unknown, never as satisfied |

## What this means for the three pull requests

* **#42 fixes all eight findings it claims to fix** (F-1…F-8), verified at the command level on both
  heads, and its seven new scenarios are discriminating: run against #41's tool they fail 8/8, run
  against #42's tool the four *new* scenarios on this branch fail while nothing else does.
* **#42 introduces no regression** in the artefacts it does not touch: `tools/presentation.py`, all
  four SVGs and `README.md` are byte-identical between #41 and #42 (`HASHES.txt`), and it changes no
  generated block — so the cosmetic work stayed in #40/#41 as it should.
* **What remains open on #42 is not false-green material**: R-1/R-2 mislabel a state that is already
  being refused; R-3/R-4/R-5 are test and interface hygiene; R-6/R-7 are documentation. Every one of
  them fails *safe*. That is the difference between "mergeable with reservations" and "not ready".
