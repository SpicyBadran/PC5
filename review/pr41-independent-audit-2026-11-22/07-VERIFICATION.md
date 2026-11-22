# 07 — Verification: fresh clone, negative controls, and the fix branch

Every exit code below is the code the command really returned in this sandbox. `raw/verify1.log`,
`raw/fix-check.log`, `raw/fix-check3.log`, `raw/mutation.log` and `raw/mutation-results.json` are the
untrimmed outputs.

## 1. Fresh clone of #41's head, from GitHub

```sh
git clone --quiet https://github.com/SpicyBadran/PC5.git /tmp/audit41/verify1
cd /tmp/audit41/verify1
git fetch -q origin pull/41/head && git checkout -q FETCH_HEAD
git log -1 --format="%H"            # 5bc5d5d4353f2e2c520224fd48711556b0054fc4
TMPDIR=/tmp make -j8 check          # raw/verify1.log, 1353 lines
```

| result | value |
|---|---|
| `make -j8 check` | **exit 0** |
| unittest summary | `Ran 1037 tests in 104.066s` / `OK (skipped=110)` |
| gate commands seen in the same run | `python3 -m psm.contract`, `tools/docs_check.py`, `tools/commit_citations.py`, `tools/presentation.py check`, `tools/brand.py check` (all part of `check`, none failed) |

The 110 skips are the hardware/target-dependent suites (OVMF, physical boot, USB) — they skip for
absence of hardware, which is the correct behaviour and is not a pass.

Then the reconciler, run as an operator would on that clone:

```
$ python3 tools/dashboard_reconcile.py --repo /tmp/audit41/verify1
OUTSTANDING — docs/evidence/dashboard-reconciliation.json does not exist on
52f682c8435956c8210b3189cad23ebea973d2de yet: nothing has been recorded as inspected.
Bootstrap it with `python3 tools/dashboard_reconcile.py --record --bootstrap
--boundary <full SHA> --inspect <full SHA>`
  run: make post-merge
                                              exit 1   ✓ correct: it does not read green
$ python3 tools/dashboard_reconcile.py --repo /tmp/audit41/verify1 --record
ERROR — no usable receipt at 52f682c8…: `--record` only extends an existing boundary. Bootstrap
it explicitly with --record --boundary <full SHA> --inspect <sha>… so the receipt states what it
looked at.
                                              exit 2   ✓ correct: no invented baseline
$ python3 tools/dashboard_reconcile.py --repo /tmp/audit41/verify1 --json | python3 -c "import json,sys; json.load(sys.stdin)"
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
                                              exit 1   ✗ F-8: prose where --json promised an object
```

Two of the three answers are exactly what the design promises, and the third is the one defect that
was found **only** by running the tool on a clean clone — not from the diff, not from the tests, not
from the PR description. That is the argument for this step existing.

## 2. Negative controls (the mutation battery)

`raw/mutate.py` — each control copies the tree to `/tmp/audit41/mut/<n>`, applies **one** edit, then
asks two independent questions: does a gate notice (`make presentation-check`, `make docs-check`)
and does the suite notice (the named `unittest` modules). Where a control edits the generator, the
run is repeated after `tools/presentation.py sync`, because only then do the committed bytes match the
mutated render — which is the only state in which a presentation-layer lie could actually be merged.
The tree is reset with `git checkout -- .` *before* patching, on committed bytes only.

**14 controls, 14 caught, 0 missed, 0 needles failed to apply** (`battery exit=0`):

| # | control | `presentation-check` | `docs-check` | tests | caught by |
|---|---|---|---|---|---|
| 1 | force every ladder cell green (the original M1 class) | 0 | 0 | 1 | tests |
| 2 | `not_started` rendered with the `complete` colour | 0 | 0 | 1 | tests |
| 3 | the blocked marker removed, so blocked reads as not started | 0 | 0 | 1 | tests |
| 3b | a legend label softened to a neutral word | **2** | 0 | 1 | both |
| 4 | inflate the milestones-verified metric in the README table | 0 | 0 | 1 | tests |
| 5 | count a citation as a filed evidence record | 0 | 0 | 1 | tests |
| 6 | skip the filesystem check of cited paths | 0 | 0 | 1 | tests |
| 7 | an SVG text collision introduced into the committed asset | **2** | 0 | 1 | both |
| 8 | a truncated label (ellipsis) in the committed figure | **2** | 0 | 1 | both |
| 9 | the dashboard asset deleted | **2** | **2** | 1 | all three |
| 10 | the generator itself made to fail | **2** | 0 | 0 | presentation-check only |
| 11 | a Git subprocess fails and the answer is swallowed | 0 | 0 | 1 | tests |
| 12 | verification removed from the record path altogether | 0 | 0 | 1 | tests |
| 13 | generated output no longer exempted, so the loop never closes | 0 | 0 | 1 | tests |

Controls 1–10 are the list from the request (the three presentation-layer ones split into 3/3b, 7, 8,
9, 10); 11–13 are the reconciliation-layer controls this audit added because #41's substance is the
reconciler. The reading that matters: **the presentation layer is defended by measurement, not by
trust** — six of the fourteen are caught by a *gate* rather than by a test, and control 10 is caught by
a gate alone, which is what you want from a check that must work even when someone edits the tests.

**Re-run on the fixed tree** (`raw/mutation-fix.log`, `raw/mutation-results-fix.json`, against
`git archive` of the fix branch's final commit): **14/14 caught, 0 missed, 0 needles failed to
apply**, and every control's *signature* — `(presentation-check, docs-check, tests, verdict)` — is
byte-identical to the pre-fix run, including control 10's `(2, 0, 0)` (caught by the gate alone) and
control 12's patch applying to the record path after `verify()` was moved above the early return. No
test was weakened to accommodate the fix and no control became invisible; the fix adds catchers
(s39–s45) without removing one.

## 3. The fix branch, `fix/reconcile-exemption-is-verified`

Four commits on top of `5bc5d5d4353f2e2c520224fd48711556b0054fc4`:

```
52c6f95  docs: say what the receipt certifies, and that the exemption is tested rather than granted
d2c5e43  fix: `--json` answers with an object when the answer is a refusal, and tests s39–s45 land
20874e9  fix: test the exemption instead of granting it, and stop clearing what was never reviewed
dfb9aaa  test: six lifecycle scenarios the audit found the tool could not fail
```

**Test-first, proved rather than asserted.** With `dfb9aaa`'s tests applied to `5bc5d5d`'s tool
(`/tmp/audit41/before`, a copy of the audit tree):

```
$ python3 -m unittest tests.test_dashboard_reconcile          # original tool + new tests
FAIL test_s39 … AssertionError: 0 != 1 : a hand-written figure was reported as CURRENT
FAIL test_s40 … AssertionError: 0 != 2 : `--record` accepted a stale figure
FAIL test_s41 … AssertionError: 0 != 1 : a no-op merge was waved through
FAIL test_s42 … AssertionError: 0 != 1 : the naming forgery: repository …
FAIL test_s43 … AssertionError: 0 != 2 : --boundary without --bootstrap was ignored
FAIL test_s44 … AssertionError: '<sha>' not found in []
FAIL test_the_three_exit_codes_are_the_documented_ones        (the structural test pinned the old
                                                               `report()` expression; rewritten to
                                                               require the union)
Ran 52 tests … FAILED (failures=7, skipped=1)
```

`s45` was written against the fresh-clone observation and was checked the same way
(`FAIL … --json on an unbootstrapped target is not parseable`).

| after the fix | result |
|---|---|
| `python3 -m unittest tests.test_dashboard_reconcile` | `Ran 53 tests … OK (skipped=1)` — 46 → 53, the one skip still s36's, whose dead assertion is now covered by s44 with no skip available |
| `python3 -m unittest tests.test_progress_matrix tests.test_presentation` (docs↔test coupling) | `Ran 122 … OK`, then `Ran 73 … OK` for the `PostMergePolicyTest` + presentation pair after the wording edits |
| `make presentation-check docs-check` | **exit 0** — no asset drift, on both the pre- and post-fix trees |
| `TMPDIR=/tmp make -j8 check` | **exit 0**, `Ran 1044 tests in 104.197s`, `OK (skipped=110)` |
| 1044 vs the clone's 1037 | +7 = exactly the seven scenarios added; no test removed, no assertion weakened, no state changed |

Nothing in the fix touches a metric, a milestone state, an evidence classification or the artwork:
`M17`–`M21` remain `blocked`, `M19` remains the blocked destination, and `docs/assets/*` is byte
unchanged on this branch (the two doc-file changes are prose; `presentation-check` compares the
generated blocks and passes).
