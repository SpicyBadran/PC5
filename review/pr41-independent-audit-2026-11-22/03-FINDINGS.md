# 03 — Findings register

Severity is about what a reader of the dashboard could be misled into believing, not about how much
code has to change. "Reproduced" means a disposable-repository scenario with the exit code the
command really returned; the raw assertions are in `raw/lifecycle-tallies.txt`, from
`/tmp/audit41/lifecycle-{a,b,c}-results*.json`. None of these were found by reading the source
only — every one was reproduced first.

---

## F-1 · HIGH · self-certification: a commit inside the range can clear itself

**Reproduced** — scenario 27 on `5bc5d5d`: a range commit whose only non-generated edit is to
`docs/evidence/dashboard-reconciliation.json`, naming its own SHA as inspected, gives reconciler
exit **0** with

```
CURRENT — every first-parent change since the receipt's boundary is either inspected
           and recorded or confined to generated output
```

while `python3 tools/presentation.py check` on the same tree exits **1**.

**Mechanism, three halves that only work together.**

1. `is_generated_output_only()` (line 286) returns True when every changed path is
   `docs/assets/**`, or `RECEIPT`, or `README.md` inside a generated span — so **a commit whose only
   change is the receipt is exempt from inspection**, with no question asked about who wrote that
   receipt.
2. `validate_receipt()` (line 380) checks each claimed SHA is *an ancestor of the target* —
   which is the right test for staleness and no test at all for self-reference: when the receipt edit
   is itself the tip, `sha == target`, and the claim is honoured.
3. The receipt's own `verification` array is checked only for `"exit_code": 0` as *written in the
   file*. The read path never re-runs a gate, so that field is self-reported and, for a
   hand-written receipt, free.

Its own docstring states the property that fails: *"exempting these paths cannot hide an unreviewed
change — while exempting by commit SHA or by message would."* A receipt-only commit hides an
unreviewed change precisely because exempting it means accepting its list of names. `CONTRIBUTING.md`
puts the rule bluntly — "it is a rule about file paths, not about who wrote the commit" — which is
accurate, and is the exploitable property rather than a defence against it.

**Why HIGH.** This is the one place where the tool's own protection story ("a receipt cannot be
written over a red gate") is false, and it is reachable by the person the duty is addressed to. It
does not fake a *metric* — the numbers are generated from `roadmap.json` — but it fakes the claim
"someone reconciled this", which is the claim the receipt exists to make.

**Fix** — the exemption's premise is now *tested*: `generated_output_agreement()` runs the same two
verifiers whenever a clear answer would rest on generated output (or on a name in the receipt, which
rests on the same premise) and a disagreement forces exit 1 in both output modes. `tests/test_dashboard_reconcile.py::test_s42`
pins both forgery variants (name it, and move the boundary). **Residual:** a hand-written receipt
whose figures *do* match is still a human claim — the tool cannot see an inspection that never
happened. `AGENTS.md` says the receipt is auditable rather than trusted, and this audit says out
loud that this half is governance, not code.

---

## F-2 · HIGH · a red gate does not block the "nothing to record" path

**Reproduced** — scenario 16: a hand-edited digit inside a `<!-- pc5:stats -->` block committed to
main, then `--record` → exit **0**, `nothing to record: every change since the boundary is either
named in the receipt or confined to generated output`.

**Mechanism** — in `record()`, `if not unrecorded: return 0, [...]` sits *above* `verify()`.
`verify()` therefore runs only when something is about to be written. Note the same tree with a
*docs-check* failure and some real work in range does refuse (scenario 17: exit **2**,
`refusing to record: documentation links, anchors and commands resolve failed`), so the gate is
wired correctly and merely unreachable on this path — which is why reading the source would not
have found it.

**Why HIGH.** "Nothing to record" and "I checked the tree" are different statements, and the
receipt's `verification` field claims the second. An operator following AGENTS.md step 6 gets exit
0 and no receipt update on a tree whose figures are stale.

**Fix** — verification is unconditional; the refusal writes nothing (s40 asserts the receipt on main
is byte-unchanged and `git status` shows the receipt not modified).

---

## F-3 · MEDIUM · `CURRENT` certifies "nothing is owed", not "the artwork is intact"

**Reproduced** — scenarios 15b and 28: a committed **truncated** `docs/assets/pc5-progress-matrix.svg`
(15b) and a committed **deleted** one (28) both report CURRENT, exit **0**, while
`presentation-check` exits **1** on the same tree.

**Mechanism** — the exemption is by path, and nothing re-proves the content. Strictly this is
*documented* behaviour ("confined to generated output"), so it is a design gap rather than a bug;
but no document in the PR says the two sentences that matter: that `CURRENT` says nothing about the
artwork, and that a damaged asset is invisible to the read path.

**Fix** — now it is not invisible (the tested premise covers a committed-but-broken asset because the
regenerated output no longer matches it), `AGENTS.md` step 7 and `CONTRIBUTING.md` say which command
certifies what, and `docs/PRESENTATION.md` documents `generated_output_agreement`.

---

## F-4 · LOW · a no-op merge closes the duty

**Reproduced** — scenario 29: an empty two-parent commit (`git commit-tree` with two parents) on
main → reconciler exit **0**; 29b confirms it is named once a record exists.

**Mechanism** — `reviewed_ancestry()` returns "covered" when the introduced set is empty, so any
`--no-ff` merge of an already-contained branch is cleared by having happened. `AGENTS.md` step 2
says the opposite in as many words: *"a no-op merge is still a merge, and it is recorded as reviewed
in step 6 rather than skipped."* So the documentation is the spec and the code disagreed with it.

**Fix** — an empty introduced set now means "outstanding, with the reason stated in the report
line", so a human records it in step 6 (s41).

---

## F-5 · LOW · flags accepted and ignored

**Reproduced** — scenario 30: `--record --boundary 000… --inspect 000…` without `--bootstrap`
exits **0** and prints `nothing to record`, having read neither flag; `--boundary`/`--inspect` are
only consulted by `bootstrap()`. Found while fixing it: `--note` without `--record` is also
silently dropped (not in the audit's list).

**Why it matters more than it looks.** An ignored flag is worse than a refused one: the operator
believes they set a boundary, and the tool's answer looks like a result.

**Fix** — both are exit 2 with the reason (s43). `--target` still accepts what it always accepted
(see F-6); nothing else changed.

---

## F-6 · REPORT CORRECTION (mine, and the author's) · `--target` does accept a commit

`--target <short sha>` and `--target HEAD` both exit **0** (scenario 36) — the earlier statement
that the flag "cannot point at a commit" is false. `resolve_target()` honours any explicitly named
rev and only refuses to fall back to a *local* branch when no remote ref resolves. The distinction
matters for §06: it is why `make dashboard-reconcile` can be run against a PR ref, and why "the
receipt must live on the target" is a statement about the default, not a restriction.

---

## F-7 · MEDIUM · a statistic the report prints was structurally always zero

`recorded_in_receipt` and `merges_cleared_by_reviewed_content` are filled from the two
classifications that *clear* a commit, but `assess()` tested `requires_inspection` first, so both
lists could only ever be empty and every commit that was legitimately cleared by a receipt landed in
`exempt_generated_only`. The report line

```
commits since then   N on the first-parent line (0 needing inspection, 0 already named in the receipt, …)
```

was a tautology: "0 already named in the receipt" could never be anything else, on any repository,
for the whole life of the branch. `test_s36` asserts the merge half, and reaches that assertion only
when git agrees to merge an already-contained branch — which it declines, so the test skips and the
dead code stayed quiet (`Ran 46 tests … OK (skipped=1)` on `5bc5d5d`; the module's only `skipTest`
is line 772, inside s36).

**Reproduced** — s44 on the pre-fix tree: `AssertionError: '<merge sha>' not found in []`.
**Fix** — classification order corrected (name-in-receipt → ancestry-clear → generated-output-exempt
→ outstanding) and s44 reaches the state through the flow a pull request takes, where git cannot
decline and no skip is available.

---

## What held up under attack

Recorded because a review that only lists defects misprices the work: **every** forgery variant of
the receipt I tried is refused — truncated, malformed, wrong schema, future date, foreign
repository id, boundary not an ancestor, inspected SHA that is not a commit, pre-approved merge SHA
(20a–20h, all correct); an unlanded SHA is refused (21); a feature branch cannot be made to look
current by extending the local receipt (24/24b); a stale receipt still names the merge (19); a dirty
tree blocks the write (18); a shallow clone is an honest exit 2, not an empty range (23); a
foreign-receipt NOTE is printed and the SHAs are still evaluated (31); a missing `git` is exit 2
with no traceback (32); octopus and reverse-oriented merges list the unreviewed commits (34, 35);
and a second merge after a legitimate record is still named (33).

## Which findings became new tests, and where they were verified to fail first

| scenario | finding | on `5bc5d5d` | on the fix branch |
|---|---|---|---|
| `test_s39` | F-3, F-1 | fail: `0 != 1` "a hand-written figure was reported as CURRENT" | pass |
| `test_s40` | F-2 | fail: `0 != 2` "`--record` accepted a stale figure" | pass |
| `test_s41` | F-4 | fail: `0 != 1` "a no-op merge was waved through" | pass |
| `test_s42` | F-1 | fail: `0 != 1` "the naming forgery" | pass |
| `test_s43` | F-5 | fail: `0 != 2` "`--boundary` without `--bootstrap` was ignored" | pass |
| `test_s44` | F-7 | fail: `'<sha>' not found in []` | pass |

All six on one command, both trees: `python3 -m unittest tests.test_dashboard_reconcile` →
`FAILED (failures=7)` against `5bc5d5d`'s tool with the new tests, `Ran 52 … OK (skipped=1)` with
the fix (the 7th failure is the structural test that pinned `report()`'s old verdict expression,
rewritten to require the union). Full-suite results for the fix branch are in §07.

---

## What the fix does *not* close, stated rather than implied

- **A hand-written receipt whose figures do match.** The tool cannot see an inspection that did not
  happen; it can only refuse to call a range `CURRENT` while the claim's premise is false. Half of
  F-1 is governance — `AGENTS.md` says the receipt is auditable rather than trusted, and a
  reviewer with the receipt's `inspected_commits` and `git log` can falsify it in one command.
- **Which tree the gates were run against.** `verify()` runs in the checkout, so the re-tested
  premise is "this checkout's generated output matches this checkout's data". The report line says
  "in this checkout" and `docs/PRESENTATION.md` says to run the duty where `HEAD` is the target,
  because the alternative — checking the target out — is a mutating action in a repository the
  operator may be working in, and this tool's rule is that it does not touch the working tree.
- **`make check` as a recorded gate.** There are exactly two recorded verifications
  (`presentation.py check`, `docs_check.py`). `AGENTS.md` step 4 requires `make check` to pass, but
  a receipt does not attest to it, and this audit left that asymmetry alone rather than inventing a
  third gate: a full-suite run is 100+ seconds and re-runnable from the receipt's `reconciled_at`
  SHA by anyone, which is a defensible split. It is recorded here so nobody reads
  `verification: exit 0` as "the test suite passed".

---

## F-8 · LOW · `--json` printed prose when the answer was a refusal

**Reproduced** — the fresh clone of #41's head used for §07:

```
$ python3 tools/dashboard_reconcile.py --repo /tmp/audit41/verify1 --json
OUTSTANDING — docs/evidence/dashboard-reconciliation.json does not exist on 52f682c8…  (exit 1)
$ … | python3 -c "import json,sys; json.load(sys.stdin)"
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

**Mechanism** — `main()` handles `Outstanding`/`ReconcileError` outside `report()`, so the two paths
that a scripted check hits *most often* (nothing recorded yet; the question cannot be answered)
produce human text where the machine interface promised an object. Not a correctness bug — the exit
code was right — but in a repository with no CI, `--json` is how a future check would be written, and
a parse error reads as "the tool is broken" rather than "a reconciliation is due".

**Fix** — both handlers emit a JSON object in `--json` mode (`{"status":"outstanding","reason":…,
"next":"make post-merge"}` on stdout, `{"status":"error",…}` on stderr alongside the existing text),
text mode byte-unchanged. `test_s45` fails on `5bc5d5d` (`not parseable (Expecting value: line 1
column 1 (char 0))`) and passes on the fix.

Found by *running the tool on a fresh clone*, not by reading the diff — which is the argument for the
fresh-clone step in §07 existing at all.
