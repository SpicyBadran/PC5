# PC5 presentation: the audit, the design system, and why it is generated

This document is the record for the README redesign: what the repository looked like,
what was decided, how the figures are produced, and what they deliberately do not say.
It is also the design system's home, so the next person changing a figure does not have
to reverse-engineer the palette from an SVG.

## 1. Audit of the repository as found

Inspected at `b1858ec` (the merge of PR #22), from the working tree rather than from a
previous report.

| Question | Finding |
|---|---|
| Was there a README? | Yes, 186 lines: accurate, dense, table-heavy, and entirely text. |
| Were there images? | None. No `docs/assets`, no PNG or SVG anywhere in the tree, no screenshots. |
| Did visual evidence exist to publish? | No screenshot of a boot exists in the repository, and there is no physical boot to photograph. The available real evidence is textual: test output, serial-log excerpts, verification reports. |
| Was there a status summary? | A large hand-maintained table in the README, duplicating states that also live in `roadmap.json`. It could drift silently. |
| Was there a graph to draw from? | Yes — `docs/architecture/roadmap.json`, 35 milestones in 14 workstreams, with states, dependencies, evidence lists and a named destination milestone. |
| Was there CI to badge? | No activated workflow. `ci/build.yml` is a pipeline definition stored in the tree because the token used here cannot create workflows; `ci/README.md` says so. **No CI badge is shown and no claim of automated builds is made.** |
| Were there contributor documents? | `LICENSE`, `docs/SECURITY.md`, `docs/BUILDING.md`, `docs/INSTALLATION.md`; no `CONTRIBUTING.md`. |
| What already tested the README? | `tests/test_architecture.py` (every markdown link resolves), `tests/test_product_identity.py` (the identity line, forbidden claims from data), `tests/test_project_independence.py` (no vendored project, no borrowed identity). |

What the redesign could therefore honestly show: generated diagrams of real
architecture, real milestone state, the real boot workflow with its unproven steps
marked, and a status table derived from the graph. What it could not show: screenshots,
benchmarks, compatibility percentages, or evidence that does not exist.

## 2. Design decisions

* **Dark technical palette, restrained accents.** Background `#0b0f14`, panels
  `#131a23`/`#0f151d`, hairlines `#243040`, text `#e6edf3`, muted `#8b98a8`.
  Accents carry meaning and nothing else: green `#34d399` complete, cyan `#38bdf8`
  in progress / identity, amber `#fbbf24` not tested, red `#f87171` blocked, violet
  `#a78bfa` structure. No decorative gradients, no stock imagery.
* **One typeface family, monospace.** `font-family` resolves through
  `JetBrains Mono` → `SFMono-Regular` → `Consolas` → `Liberation Mono` → `Menlo` →
  generic `monospace`. Nothing is downloaded; the figures render offline and on
  GitHub's SVG host.
* **Original mark.** Three stacked chevrons in cyan, violet and amber stand for the
  layers PC5 adds between firmware and the destination. It is drawn as three paths,
  not imported from anywhere.
* **Honesty in the artwork.** Every figure carries the state of the thing it draws, and
  the banner carries the sentence the project cannot stop repeating: the ISO boots
  PC5's own loader and kernel, no Sony software is present, physical boot not tested.
* **Mobile and GitHub first.** Widths are 1000–1200 px with generous internal margins,
  no text below the 10 px floor that `tests/test_presentation.py` enforces (the matrix
  needs it to fit a label inside a cell), and every figure has `<title>` and `<desc>` for
  screen readers plus descriptive `alt` text in the README.
* **Room is measured before it is spent.** A region heading and its right-aligned figure
  share one line, so `region_head` lays the figure out first, hands the remainder to the
  title, and shortens the title only if it still does not fit. Every other label goes
  through `fit`/`fit_lines`, which use the same `measure` the layout guard uses — an
  estimate that disagrees with the guard would either clip text that fits or let two
  labels print on top of each other. Both failures are tested: `overflow` asks whether
  anything left the canvas, `collisions` asks whether two labels on one baseline overlap,
  and `tests/test_progress_matrix.py` fails on any `…` in the figure at all, so clipping
  is a last resort rather than a way of hiding a wording problem.

## 3. The seven figures

| Figure | What it shows | Where the facts come from |
|---|---|---|
| `docs/assets/pc5-banner.svg` | wordmark, roadmap version, workstream count, completed-milestone count, destination state, and the "no Sony software" line | `roadmap.json` `version`, `workstreams`, milestone states, `M19` state |
| `docs/assets/pc5-architecture.svg` | eight layers from PC hardware to genuine system software, each badged with the state of the milestones it maps to | `roadmap.json` states per layer; firmware forced to **NOT TESTED** because `M1h` completing says nothing about a real machine's firmware |
| `docs/assets/pc5-progress.svg` | one bar per workstream split by state, blocked counts, and the longest dependency chain to `M19` | `roadmap.json`; the chain is computed by the same longest-path rule `tools/roadmap.py path` uses |
| `docs/assets/pc5-boot-workflow.svg` | six steps from building the image to Sony software executing, each labelled verified, not tested, testing required or blocked | the gates in `Makefile` plus the state of `M1` and `M19` |
| `docs/assets/pc5-dashboard.svg` | six stat cards (milestones, workstreams, in progress, blocked, not started, destination) and a footer with the latest recorded verification, test run, OVMF result and physical-boot count | `roadmap.json` plus `docs/evidence/validation/*.json` |
| `docs/assets/pc5-evidence.svg` | the evidence ladder: host tests, ISO verification, emulator, stub firmware, real OVMF, physical boot, Sony execution — each with its own record, never inferred from the rung below | validation records, `docs/evidence/*.json`, and `M19` |
| `docs/assets/pc5-progress-matrix.svg` | the dense board: milestone states, the six evidence levels with their own counts, acceptance and deliverable ratios, per-workstream cells, claim classifications, modelled operations with what each interface withholds, blocked milestones with the dependency that blocks each one, and one cell per committed validation record stamped with its commit | `roadmap.json`, the contract's generated tables, `psm/registry.py`, the tree for "does this deliverable exist", `docs/evidence/**` |

### What is generated into the README

Six blocks, each between its own pair of markers, each from one source:

| Block | Markers | Source | Shows |
|---|---|---|---|
| Statistics | `<!-- pc5:stats:begin -->` | `roadmap.json` + validation records + `PS5UPDATE.PUP` pointer | roadmap version and date, milestone and workstream totals, the four state counts, destination and its state, last recorded ISO verification and test run, physical-boot evidence |
| Milestone status | `<!-- pc5:status:begin -->` | `roadmap.json` | thirteen capability rows with derived labels |
| Validation evidence | `<!-- pc5:evidence:begin -->` | `docs/evidence/validation/*.json` | gate, result, check count, the commit the run happened at, and its date; `no record` when there is none |
| Blockers | `<!-- pc5:blockers:begin -->` | `roadmap.json` | every blocked milestone with the blocker it records |
| Progress matrix | `<!-- pc5:matrix:begin -->` | the same counts the matrix draws | the text fallback: every region's fraction and the file its denominator comes from, so the README still works with images switched off |
| The PUP | `<!-- pc5:pup:begin -->` | the tracked `PS5UPDATE.PUP` | pointer size and hash, declared object size, payload absent, package unverified |

**Statistics outside those blocks are rejected.** `make presentation-check` scans the
prose for `N checks`, `N milestones`, `N workstreams`, `N-byte` and `N.N GB` and fails,
because those are exactly the numbers that used to be typed once and quietly rot.

The milestone status table is generated into the block between
`<!-- pc5:status:begin -->` and `<!-- pc5:status:end -->`. Milestone rows take their
label from milestone state (`complete` → VERIFIED, `in_progress` → IN PROGRESS,
`not_started` → NOT STARTED, `blocked` → BLOCKED); `M1` renders as **NOT TESTED**
until a physical record exists under `docs/evidence/`. Gate rows declare their label
but keep it only while the document they name still contains the evidence token, so a
reworded test record cannot leave a VERIFIED chip behind.

## 4. Generation and checking

```sh
make presentation           # write: seven SVGs and six README blocks
make presentation-check     # read: are they current?  also run by `make check`
make docs-check             # read: every link, anchor and documented command in the tree
make validation             # read: the committed records, and whether they are coherent
make record-validation      # write: run the gates and replace the records (explicit only)
make dashboard-reconcile    # read: which merges into `main` the dashboard has not caught up with
make post-merge             # write then read: presentation, presentation-check, docs-check, reconcile
```

`make check` runs `presentation-check` and `docs-check`, never `presentation`, so a test
run cannot rewrite tracked documentation. Writing is always an explicit act.

```sh
make dashboard-reconcile    # read: does the receipt cover everything that has landed?
make post-merge             # write: presentation, the checks, then the receipt (--here)

`--json` stream rule, so a caller can rely on it: stdout carries the answer object when there is
one — the full state on a read, and `{"status": "written" | "nothing-to-record" | "dry-run",
"exit": …, "lines": […]}` on a recording run. When the question cannot be answered at all, the
error object goes to stderr and stdout stays empty, with exit 2. An empty stdout with exit 2 is
therefore "no answer", never "nothing outstanding".
```

`check` fails on: a stale figure (bytes differ from a fresh render), a hand-edited
status block, a broken local link or image path in `README.md` or this document, an
image without alt text, a figure that lost its `<title>`/`<desc>`, a README that stops
embedding one of the figures, a missing honesty token, or one of the banned claims
below.

The banned list exists because presentation is where a project is most tempted to
overstate itself: "runs PS5 games", "boots on any PC", "Secure Boot supported",
"CI passing", "PS5 firmware included", "production ready". Each is false here for a
specific reason recorded in the tool.

Determinism: no timestamps, no random ids, no locale-dependent formatting, sorted
iteration, integer geometry, and no reading of the current commit: a figure that stamped
`HEAD` into its own artwork would go stale on every commit and teach people to ignore the
gate (the commit stamp the matrix draws comes from the validation *record*, which is
immutable). The same `roadmap.json` produces byte-identical figures on any machine, so a
diff means the data changed — which is the point.

What the tests hold the *drawing* to, not only the data: `tests/test_progress_matrix.py`
asserts the byte equality, the 10 px floor, that nothing is drawn outside the canvas, that
the canvas has no dead band below the last region, that every percentage carries the
fraction it came from, and — because a figure that agrees with itself can still disagree
with the repository — that every green cell in the artwork names a milestone
`roadmap.json` calls `complete`, that the evidence ladder's per-level verified cells equal
the milestones complete at those levels (so levels 3–6 cannot show green while they hold
nothing), that the workstream rows and validation grid paint exactly as many verified cells
as the records support, and that no label is clipped or printed over another. Those
artwork checks read the committed SVG's `<rect>` and `<text>` elements rather than the
generator's own dictionaries, so changing a `fill=` at the drawing site fails a test
instead of silently producing a rosier figure.

## 4a. Four words that must not mean the same thing

Progress claims fail on words before they fail on arithmetic, so the generator keeps four
notions apart and prints them apart:

| Term | What it actually is | Where it is checked |
|---|---|---|
| a **cited path** | a file the roadmap names in a milestone's `evidence` array, which `presentation.py` confirms exists in this checkout | the matrix's "cited paths that resolve" row, `39/39` |
| a **filed record** | a JSON record under `docs/evidence/**`, written by a person or a gate that ran something | the matrix's "record filed under `docs/evidence/`" row — `0/35` today, and drawn as zero |
| a **validation record** | a gate result committed under `docs/evidence/validation/*.json`, stamped with the commit it ran at | `make validation`, and the matrix's bottom-right region |
| a **physical-hardware record** | a filled `docs/hardware/M1_PHYSICAL_BOOT_PROCEDURE.md` outcome validated by `tools/hw_record.py` | the matrix's "physical PC boot: none filed" row, and `M1`'s state |

A milestone can hold forty citations and no records; that is the present state of PC5, so
the figure says so in those words. `tests/test_progress_matrix.py` keeps the census
falsifiable: it recomputes the counts from `roadmap.json` against the tree, and it runs the
same code over a fixture roadmap containing a path that does not exist on disk, which must
lower the "resolve" count. A check that only ever sees paths that happen to exist cannot
tell verification from trust.

## 4b. Where the numbers come from

A statistic is only worth printing if somebody can trace it. The chain is:

```
docs/architecture/roadmap.json            milestones, states, workstreams, version, date
        │                                   the only source of progress numbers
docs/evidence/validation/<gate>.json      one record per gate: command, exit code, result,
        │                                   check counts, commit, date, dirty flag
docs/evidence/<machine>.json              physical-boot records, validated by hw_record.py
docs/architecture/PS5_PLATFORM_CONTRACT.md  the generated psm:begin/end tables: operations
        │                                   per interface, and which are UNSUPPORTED by design
psm/registry.py                           claim classifications, as psm/contract.py enforces them
PS5UPDATE.PUP                             the pointer itself: bytes, hash, declared size
the tracked tree                          "does this deliverable path exist" — a name in the
        ▼                                 graph is not a file, so it is checked, not assumed
tools/presentation.py  →  docs/assets/*.svg  +  the six README blocks
        │
tools/dashboard_reconcile.py   which merges the generated dashboard has not been reconciled with
```

`tools/validation.py` is what writes the records, and it can only write one by *running*
the gate: `run` captures the exit code, reads the test counts from the runner's own
output, stamps the commit and the date, and classifies the outcome as `passed`, `failed`,
`not-tested`, `skipped` or `error`. A missing dependency becomes `not-tested`; it cannot
become a pass. `verify-records` (and `make validation`) re-checks that no record claims
`passed` without a zero exit code, or `failed` with one.

Records are **snapshots**. Each row states the commit it ran at, and the evidence block
says so in words: a run at an earlier commit is not evidence for the current tree.

## 5. What this presentation does not do

* It shows no screenshot of a boot, because no boot on physical hardware has happened
  and an illustration of one would be a fabricated result.
* It publishes no compatibility percentage. Percentages appear only where a finite set in
  the repository can be counted — milestone states, modelled operations, committed
  validation records — and each one is printed next to its own `(n/d)` with the
  denominator named in §4b. Nothing is scored on a scale a file does not define.
* It does not claim continuous integration. The pipeline definition exists; the
  workflow is not active.
* It does not make the destination look closer than it is: `M19` is drawn blocked, in red,
  in every figure that can show it, and the matrix lists what it is blocked on beside it.
* It does not let the dense board imply a higher evidence level than a row's own: the
  matrix draws documentation progress, synthetic or mocked coverage, host and emulator
  validation, firmware-backed validation, physical-hardware validation and genuine
  target-system-software execution as separate regions, and the top two contain no green
  cell because the repository holds no evidence that could put one there. A cell is green
  only where a file records the thing as done.

## 6. The reconciliation receipt

Being current is two properties, and the first version of this machinery conflated them.
`presentation-check` proves the *content* matches the data. It says nothing about whether
every change that landed on `main` was *looked at* — a merge can land that the figures
describe correctly by accident, and a reconciliation that changes no visible output leaves
no trace for an inferred watermark to find. So the second property is recorded, not
inferred, in a tracked file: `docs/evidence/dashboard-reconciliation.json`, schema
`pc5.dashboard-reconciliation/1`, written only by `tools/dashboard_reconcile.py --record`.

| Field | Why it is in the receipt |
|---|---|
| `schema` | a reader must be able to tell which rules produced this record |
| `repository` | `owner/name` from `remote.origin.url`: the receipt vouches for one repository, and a mismatch is printed as a note rather than trusted |
| `target_ref`, `reconciled_at`, `previous_boundary` | what was reconciled and where the boundary moved from; `reconciled_at` is a full SHA, and it must be an ancestor of the target the receipt is read from |
| `boundary_date` | the only date in the file, and it comes from the commits, not from a clock — the receipt stays byte-reproducible |
| `inspected_commits` | every commit this line of history has processed; a SHA that is not an ancestor of the target is refused, which is what stops a receipt pre-approving a merge that has not happened |
| `commits_inspected_now` | per commit: subject, date, parent count, `kind` (`merge` or `single-parent`), the paths it brought, and which dashboard drivers it moved — the audit trail for "we looked" |
| `inspected_paths`, `paths_truncated` | the union of changed paths, capped, with the truncation stated instead of hidden |
| `figures_changed`, `readme_changed` | whether the reconciliation moved the artwork or only confirmed it; "nothing displayed changed" is a result, and it is recorded as one |
| `generated_output_pending_commit` | the figures and README blocks `--record` found uncommitted, which the follow-up commit is expected to carry |
| `verification` | each command, its argv and the exit code it really returned; a non-zero entry makes the receipt unusable, so a receipt cannot be written over a red gate |
| `generated_output_agreement` | read-side only, not a receipt field: when a range would be cleared by the generated-output exemption, the reconciler runs both gates against the checkout and records the result here. Non-empty means the output does not match its data, and the run is `OUTSTANDING` for that reason alone — the exemption is a premise, and it is tested rather than assumed. It is tested against **this checkout**, which is why the duty says to run it where `HEAD` is the target |
| `complete`, `incomplete_reason` | the explicit incomplete marker: an unfinished reconciliation reads as outstanding, never as clean |

**How a merge is detected.** `git rev-list --first-parent <boundary>..<target>` and, for
each commit, its diff against *its own first parent*. No `--merges` filter appears anywhere:
a `--no-ff` merge, a fast-forward, a squash, a rebase-merge and a commit pushed straight to
`main` are all just commits in the range, and a merge whose diff is empty is still a commit
that has to be named. `tests/test_dashboard_reconcile.py` runs each of those topologies in a
disposable repository and asserts the exit code.

**What is exempt, and why the loop stops.** A commit needs no inspection when everything it
changed is reconciliation output: a file under `docs/assets/`, this receipt, or `README.md`
lines that all fall inside a generated `<!-- pc5:*:begin -->` span. That is a rule about
*content*, so the follow-up commit which carries the regenerated figures and the receipt is
exempt, and the next read reports `CURRENT` instead of asking for another receipt. It is
also a rule that cannot be satisfied by a merge which changed `roadmap.json`, a gate, or
prose — which is the case the watermark got wrong.

**Exit codes.** `0` current, `1` outstanding, `2` error, and the difference between the
last two matters: `1` means the history is readable and something in it is unreconciled,
`2` means the question could not be answered (no `origin/main` to read, a shallow clone, a
malformed receipt, a receipt naming commits that have not landed, a tree with uncommitted
source changes). Missing assets and unreadable ranges never resolve to `CURRENT`, and the
default target is `origin/main` — the checked-out branch is consulted only when `--here`
says so out loud, and the receipt records that it was written that way.

**What is *not* claimed.** Nothing enforces this automatically: the pipeline definition
under `ci/` is not an active GitHub Actions workflow (`.github/workflows` does not exist in
this repository), no CI service runs these gates on a push, and no Git hook is installed by
the build. The receipt and
`make dashboard-reconcile` make the state *checkable* by anyone at any time; keeping it
current remains the standing duty written in [`AGENTS.md`](../AGENTS.md), carried by whoever
is working in the repository. A tool that can be ignored is not a monitor, and this document
does not pretend otherwise.
