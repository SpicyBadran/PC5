# §4 lifecycle and attack matrix (beyond F-1…F-8)

Three harnesses, all running the public command against a disposable repository with a real
bare `origin`, real commits and real merges. Batch A = 24 falsification probes, batch B = 15
history/flag probes, batch C = the 27-combination flag matrix plus the read-path attribution
cases. Each ran against #41's head and #42's head; the tables below are generated from
`raw/probe*results-*.json`, so the exit codes, `tree_mutated` and `receipt_changed` columns are
the harness's own records, not prose. `expect` is what the head under test claims to do;
`ideal` is what it must do for the duty to be trustworthy.

| case | #41 exit | #42 exit | tree/receipt mutated by a read? | against ideal on #42 |
|---|---|---|---|---|
| F-1 — forged receipt in the same push, figure left stale: read | 0 | 1 | tree 0, receipt 0 | matches |
| F-1 — forged receipt in the same push, figure regenerated to mat | 0 | 0 | tree 0, receipt 0 | matches |
| F-1 — same forgery, JSON verdict and text agree | 0 | 1 | tree 0, receipt 0 | matches |
| F-2 — number typed into a generated README block, then --record | 0 | 2 | tree 0, receipt 0 | matches |
| F-3 — committed truncated figure, through the Make target | 0 | 2 | tree 0, receipt 0 | **diverges** |
| F-3 — committed deleted figure, read path | 0 | 1 | tree 0, receipt 0 | matches |
| F-3 — a stale asset must not be blamed on commits that are prope | 0 | 1 | tree 0, receipt 0 | matches |
| F-4 — no-op empty two-parent merge | 0 | 1 | tree 0, receipt 0 | matches |
| F-5 — --record --boundary --inspect without --bootstrap | 0 | 2 | tree 0, receipt 0 | matches |
| F-5 — --note without --record | 0 | 2 | tree 0, receipt 0 | matches |
| F-5 — --boundary/--inspect given to a read run (no --record at a | 0 | 2 | tree 0, receipt 0 | matches |
| F-5 — --bootstrap without --record | 0 | 0 | tree 0, receipt 0 | **diverges** |
| F-6 — --target accepts an explicitly named rev (short SHA) | 0 | 0 | tree 0, receipt 0 | matches |
| F-6 — --target HEAD, and a nonexistent ref | 0 | 0 | tree 0, receipt 0 | matches |
| F-7 — does the report ever say a commit was named in the receipt | 0 | 0 | tree 0, receipt 0 | matches |
| F-8 — --json on a repository with no receipt | 1 | 1 | tree 0, receipt 0 | matches |
| NEW-1 — a verifier cannot be executed, and the exemption would cle | 0 | 1 | tree 0, receipt 0 | **diverges** |
| NEW-2 — docs-check-only failure: is the diagnostic attributed to t | 0 | 1 | tree 0, receipt 0 | matches |
| NEW-3 — interrupted recording: the receipt directory is read-only | 2 | 2 | tree 0, receipt 0 | matches |
| NEW-4 — three merge→record→land cycles in a row | 0 | 0 | tree 0, receipt 0 | matches |
| NEW-5 — a merge whose side commits are not on the first-parent lin | 1 | 1 | tree 0, receipt 0 | matches |
| NEW-6 — regenerated-but-uncommitted figures do not block the recei | 0 | 0 | tree 1, receipt 0 | matches |
| NEW-7 — shallow clone cannot answer | 2 | 2 | tree 0, receipt 0 | matches |
| NEW-8 — receipt names a commit that has not landed | 2 | 2 | tree 0, receipt 0 | matches |

## Batch B — history, remote and flag shapes

| case | #41 exit | #42 exit | against ideal on #42 | note |
|---|---|---|---|---|
| G-1   --dry-run on a read run does nothing and says nothing | 1 | 1 | **diverges** | --dry-run is only meaningful with --record; silently accepted here, the F-5 class again |
| G-2   --record --dry-run prints the receipt and writes nothi | 0 | 0 | **diverges** | the preview must not touch the tree; git status is included in the needle check |
| G-3   --json --record: does the write path answer in JSON? | 2 | 2 | **diverges** | F-8's class: --json is honoured on the read path and ignored on the record path |
| G-4   --here and --target together: which one wins, and is i | 1 | 1 | **diverges** | contradictory selection flags; whatever wins must not win silently |
| G-5   --boundary and --inspect given a short SHA | 2 | 2 | matches | short SHAs must stay unacceptable, or a receipt could name an ambiguous commit |
| G-6   --target pointing at a tree object | 2 | 2 | matches | must not be read as a commit; a TypeError traceback would be a defect |
| G-7   truncated receipt on the target | 2 | 2 | matches | a receipt that cannot be parsed is cannot-answer, never an empty range |
| G-8   receipt from another repository | 1 | 1 | matches | the SHA is still honoured where it resolves; the mismatch is stated, not hidden |
| G-9   remote gone entirely | 2 | 2 | matches | no origin/main and no origin/HEAD: it must refuse, not fall back to a local branch |
| G-9b  same repository, but asked about the checked-out branc | 1 | 1 | matches | --here is the documented escape hatch and must still answer |
| G-10  four driver commits cleared by one record | 0 | 0 | **diverges** | one record must clear a run of commits, and the report must still *count* them |
| G-11  docs-only merge changes no metric and is still named | 1 | 1 | matches | a merge that moves no number is a successful change on main: reported, not hidden |
| G-12  repeated --record with nothing new is idempotent | 0 | 0 | matches | reading twice, or recording twice, must not drift |
| G-13  receipt marked incomplete | 1 | 1 | matches | an admitted-incomplete receipt must not read as a clean one |
| G-14  receipt verification claims a failing gate | 2 | 2 | matches | the receipt admits it was written over a red gate: that is an error, not a green |

## Batch C — every flag combination: honoured, refused, or silently ignored

A flag is *silently ignored* when the run answers as if it had not been typed and says nothing
about it. `make`/tool exit codes only; `refused` means the run said why.

| combination | #41 | #42 |
|---|---|---|
| `(read only)` | 0 honoured | 0 honoured |
| `--record` | 0 honoured | 0 honoured |
| `--bootstrap` | 0 silently ignored | 0 silently ignored |
| `--boundary` | 0 silently ignored | 2 refused |
| `--inspect` | 0 silently ignored | 2 refused |
| `--note` | 0 silently ignored | 2 refused |
| `--dry-run` | 0 silently ignored | 0 silently ignored |
| `--json` | 0 honoured | 0 honoured |
| `--here` | 0 honoured | 0 honoured |
| `--target` | 0 honoured | 0 honoured |
| `--bootstrap --boundary` | 0 silently ignored | 0 silently ignored |
| `--bootstrap --inspect` | 0 silently ignored | 0 silently ignored |
| `--bootstrap --boundary --inspect` | 0 silently ignored | 0 silently ignored |
| `--record --bootstrap` | 2 refused | 2 refused |
| `--record --boundary` | 0 silently ignored | 2 refused |
| `--record --inspect` | 0 silently ignored | 2 refused |
| `--record --note` | 0 silently ignored | 0 silently ignored |
| `--record --dry-run` | 0 silently ignored | 0 silently ignored |
| `--record --json` | 0 honoured | 0 honoured |
| `--record --bootstrap --note` | 2 refused | 2 refused |
| `--record --bootstrap --dry-run` | 2 refused | 2 refused |
| `--record --bootstrap --json` | 2 refused | 2 refused |
| `--here --target` | 0 honoured | 0 honoured |
| `--dry-run --here` | 0 honoured | 0 honoured |
| `--json --target` | 0 honoured | 0 honoured |
| `--bootstrap --here` | 0 honoured | 0 honoured |
| `--bootstrap --record --boundary --inspect --note --dry-run --json --here` | 0 honoured | 0 honoured |

Read together: #42 turns four silently-ignored combinations into refusals and leaves three.
The three left (`--bootstrap`, `--dry-run`, and `--here` with `--target`) are the same class of
defect as the four it fixed, which is why F-5 is recorded as *partially* closed.

## Attacks that behaved correctly on both trees (so are not findings)

* read-only `docs/evidence/` during `--record` → exit 2, no traceback, the tree is not mutated;
* three merge → record → land cycles in a row → exit 0 CURRENT each time, clean tree each time;
* a merge whose driver change exists only on a **side** commit → exit 1, reached through the
  merge's own diff (the case a watermark could not see);
* figures regenerated but not committed → exit 0, receipt written, the tool commits nothing
  (`tree_mutated: 1` is the figure, by design: AGENTS.md tells the operator to commit them);
* a receipt naming a commit that has **not** landed → exit 2 `not an ancestor`;
* a shallow clone, and a clone with no `origin` at all → exit 2 `cannot answer`, never an empty
  range; `--here` still answers in the same repository (exit 1, `OUTSTANDING`);
* a receipt copied from another repository → exit 1 with `NOTE the receipt vouches for …` — the
  SHAs are honoured where they resolve and the mismatch is stated rather than hidden.

### Notes on the two rows that do not read as "matches"

* *F-3 through the Make target* — expected 1, measured 2, on both trees' `make` path. `make` turns
  any non-zero recipe status into its own exit 2 (`Makefile:324`), and the tool was run through the
  target by design (the target is what AGENTS.md tells an operator to type). Invoking
  `python3 tools/dashboard_reconcile.py` in the same fixture gives 1 on #42 and 0 on #41
  (`raw/probe3-results-pr42.json`, case MV-3) — i.e. the tool is right and the wrapper flattens the
  distinction. That flattening is recorded as R-7, not as a tool defect.
* *batch B rows marked `DIVERGES`* — the four cases whose `ideal` the harness could not satisfy on
  either tree are the F-5 residue (`--bootstrap`, `--dry-run`, `--here`+`--target`) plus the
  self-consistent forgery F-1b. They are listed in `FINDINGS.md` with their classes.

Totals, from the JSON: batch A 24 probes (#41: 5 exit-0 answers where a refusal was due; #42: 1,
F-1b), batch B 15 probes (#41 5 divergences, #42 4), batch C 27 combinations × 2 trees plus 3
attribution cases. No case in any batch produced a traceback, a mutated tree from a read-only run,
or a receipt written by a refused run.
