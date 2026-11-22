# §5 audit of #42's tests, and of this review's own harnesses

## What #42 adds, and whether it can fail

`tests/test_dashboard_reconcile.py`: 46 test methods at #41, 53 at #42 — exactly seven added, numbered
`s39`–`s45`, and the module's `sNN` numbering is contiguous (1…45 in method names, no duplicates other
than the deliberate `s06`/`s06b` pair). Nothing was removed and no existing scenario's assertions were
deleted; assertions in the file go from 219 to 267.

Discrimination, measured rather than argued — the same test file run against three different tools:

| tool under test | result |
|---|---|
| #41 `5bc5d5d` + #42's tests | `FAILED (failures=8, skipped=1)` — all seven new scenarios **plus** `test_the_three_exit_codes_are_the_documented_ones`, which greps `report()`'s source and whose needle #42 moved |
| #42 `7095814` + #42's tests | `Ran 53 … OK (skipped=1)` |
| #42 `7095814` + this branch's tests | `FAILED (failures=4)` — exactly `s46`–`s49`, the four scenarios written for the four gaps found here |
| #41 `5bc5d5d` + this branch's tests | `FAILED (failures=13)` |

So each new scenario fails on the code it was written against and passes on the code that fixes it —
they are not fixture-by-construction, and neither are the four on this branch.

## The skip that hides an unreachable expectation (R-3)

`Ran 53 tests … OK (skipped=1)` in the module, and `Ran 1044 … OK (skipped=110)` for the whole suite at
#42's head. The one skip inside the reconciler module is `test_s36_a_merge_of_reviewed_content_clears_the_moment_it_lands`:

```python
merge = fx.git("merge", "--no-ff", "pr/like-40", "-m", "Merge pull request #40",
               check=False) or fx.head()
if len(fx.git("rev-list", "--parents", "-n1", "HEAD").split()) < 3:
    self.skipTest("git would not merge a branch already contained in main")
```

The fixture commits `work` to `main` first and only then branches `pr/like-40` at it, so the branch is
already contained, `git merge` answers "Already up to date.", and the skip fires — in *this*
environment, every run. The two assertions below it (`outstanding == []`, and `merge in
merges_cleared_by_reviewed_content`) are therefore never executed, while the scenario's name and
#42's own write-up both cite s36 as the evidence for F-7.

Reaching those assertions with `commit-tree` does not make them pass: for that topology #42's rule
(the one `s41` states) is *outstanding until recorded*, and the fixture also leaves its
`main moves on` commit unrecorded. s36 in #42 is a renamed pre-fix expectation that survives only
because its own fixture cannot produce its premise. This is precisely how the misfiled statistic in
F-7 stayed invisible before #42, and s44's docstring even describes the mechanism while citing a
different scenario as the skip-free replacement.

Coverage is not left broken by this: the *property* is genuinely covered by `s41` (no-op merge
mechanism) and `s44` (the PR-shaped flow, record on the branch then merge — no skip available), and
both run. What is wrong is the count: 45 numbered scenarios are advertised, 44 execute.

Fix on `fix/reconcile-premise-and-flags`: s36 builds its merge with `commit-tree` so it always runs,
and asserts what #42 actually promises for that shape — refusal, no `merges_cleared_by_reviewed_content`
credit, the merge named in the report, and the repair — after which the module is `Ran 57 tests … OK`
with **no** skip and the suite drops from `skipped=110` to `skipped=109`.

## Other properties of the suite worth recording

* **Real repositories, real commands.** Every scenario builds `tempfile.mkdtemp()` with a bare
  `origin`, copies the *real* reconciler into `tools/`, and runs it as a subprocess, asserting the
  exit code. Only `tools/presentation.py` and `tools/docs_check.py` are two-line stand-ins, and they
  are the point: they turn "the gates were run" into a falsifiable gate. `tests/test_progress_matrix.py`
  and `tests/test_presentation.py` use the real generator on the real tree.
* **Setup/teardown cannot hide a mutation.** The harness's `fixture()` does not `git clean`, and the
  assertions that matter (`fx.git("status", "--porcelain")`) run on the fixture's tree. This review
  additionally recorded `tree_mutated`/`receipt_changed` per probe at the CLI level, which is where a
  swallowed write would show.
* **Skip discipline elsewhere is sound.** `tests/test_progress_matrix.py:241` skips only when the
  platform-contract registry is not importable; the other 108 suite skips are the hardware/boot
  suites (no `/dev/sdX`, no OVMF, no PS5), not silent test absences. None of them touches the
  reconciler's behaviour.
* **Source-grep tests are the brittle kind and are worth knowing about**: `TheToolItselfIsSoundTest`
  asserts on the tool's *text* (`assertIn("return 1 if verdict else 0", body)`, "no `except Exception`",
  "`try_git` defined exactly once"). It is the reason `test_the_three_exit_codes_are_the_documented_ones`
  fails against #41 — and it means "the three exit codes are the documented ones" is partly enforced by
  a grep rather than by running the command. The CLI probes in `raw/` are what actually establish it.
* **Assertions #42 added but disabled** (R-4): `self.assert_current(fx.read()) if False else None` in
  `s39`. Deleted on this branch; an assertion that cannot run should not be in a diff.

## This review's own harnesses, audited the same way

`raw/lib.py` + `raw/probe.py`/`probe2.py`/`probe3.py` were written across five iterations, and four of
the failures they produced were *harness* bugs, not tool behaviour. Each is recorded so nobody re-runs
a broken claim:

1. a scenario that expects a clear answer must **push** the commit it names — `bootstrap`/`record`
   read `origin/main`, so an unpushed HEAD yields a wrong verdict (silently, until the fixture was
   fixed to assert);
2. `Repo.commit()` already stages, so a follow-up bare `git commit` aborted with "nothing to commit"
   (two builders died of this);
3. `tempfile.mkdtemp(prefix=…)` rejects `/` and spaces, so scenario names are sanitised;
4. "self-naming SHA" as originally written is arithmetically impossible, so F-1 was rebuilt as
   *work commit → receipt commit naming it*, and F-1b exists precisely because the achievable version
   of the attack is a different one;
5. deleting a tracked verifier while other files are dirty gives a *dirty-source* refusal, not the
   refusal under test — which is why `s46`/`r2.py` build a path-only (exempt) range first.
6. one batch-C "silently ignored" detector produced two false positives (`--record --note`,
   `--record --dry-run`), because it looked for the literal flag text; both rows were adjudicated by
   hand against the recorded output and are marked `honoured` in the matrix.

Gaps I could not close in this harness: no GitHub-side `origin` (a real fork/push was not available
and is not claimed), no network fetch of PR refs inside fixtures, and no Windows/macOS git to check
`--no-ff` behaviour differences. Anything depending on those is `not verified`.
