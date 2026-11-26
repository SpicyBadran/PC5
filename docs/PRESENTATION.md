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
| Was there CI to badge? | Not when this page was written: the historical path `ci/build.yml` was a pipeline definition stored in the tree because the token in use could not create workflows (`ci/README.md` kept the record). **No CI badge was shown and no claim of automated builds was made.** The workflow has since been installed as `.github/workflows/build.yml`; a badge is still not added here, because a badge is a promise about every future run and this page reports what was measured. |
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
  no text smaller than 11 px, and every figure has `<title>` and `<desc>` for screen
  readers plus descriptive `alt` text in the README.

## 3. The seven figures

| Figure | What it shows | Where the facts come from |
|---|---|---|
| `docs/assets/pc5-banner.svg` | wordmark, roadmap version, workstream count, completed-milestone count, destination state, and the "no Sony software" line | `roadmap.json` `version`, `workstreams`, milestone states, `M19` state |
| `docs/assets/pc5-architecture.svg` | eight layers from PC hardware to genuine system software, each badged with the state of the milestones it maps to | `roadmap.json` states per layer; firmware forced to **NOT TESTED** because `M1h` completing says nothing about a real machine's firmware |
| `docs/assets/pc5-progress.svg` | one bar per workstream split by state, blocked counts, and the longest dependency chain to `M19` | `roadmap.json`; the chain is computed by the same longest-path rule `tools/roadmap.py path` uses |
| `docs/assets/pc5-boot-workflow.svg` | six steps from building the image to Sony software executing, each labelled verified, not tested, testing required or blocked | the gates in `Makefile` plus the state of `M1` and `M19` |
| `docs/assets/pc5-dashboard.svg` | six stat cards (milestones, workstreams, in progress, blocked, not started, destination) and a footer with the latest recorded verification, test run, OVMF result and physical-boot count | `roadmap.json` plus `docs/evidence/validation/*.json` |
| `docs/assets/pc5-completion.svg` | the headline ring, the criteria-weighted versus count arithmetic, the four current milestones, the stacked release-gating bar, the level-by-level bars, and the destination / release / CI / verified chips | `tools/completion.py` over `roadmap.json`, `docs/evidence/validation/*.json` and `docs/evidence/ci/*.json`; the ring's `stroke-dasharray` *is* the computed fraction |
| `docs/assets/pc5-evidence.svg` | the evidence ladder: host tests, ISO verification, emulator, stub firmware, real OVMF, physical boot, Sony execution — each with its own record, never inferred from the rung below | validation records, `docs/evidence/*.json`, and `M19` |

### What is generated into the README

Six blocks, each between its own pair of markers, each from one source. The completion
block is first because it is the one the maintenance workflow exists to keep current
(`docs/ABOUT.md` carries three more, below):

| Block | Markers | Source | Shows |
|---|---|---|---|
| Completion | `<!-- pc5:completion:begin -->` | `tools/completion.py` over `roadmap.json` + `docs/evidence/validation/*.json` + `docs/evidence/ci/*.json` | the percentage and the count it agrees with, earned and total weight, milestone totals by state, the four current milestones, partial-credit status, the release-gating tally, the hosted CI conclusion, release readiness separately from publication, and the date of the newest verification record |
| Statistics | `<!-- pc5:stats:begin -->` | `roadmap.json` + validation records + `PS5UPDATE.PUP` pointer | roadmap version and date, milestone and workstream totals, the four state counts, destination and its state, last recorded ISO verification and test run, physical-boot evidence |
| Milestone status | `<!-- pc5:status:begin -->` | `roadmap.json` | thirteen capability rows with derived labels |
| Validation evidence | `<!-- pc5:evidence:begin -->` | `docs/evidence/validation/*.json` | gate, result, check count, the commit the run happened at, and its date; `no record` when there is none |
| Blockers | `<!-- pc5:blockers:begin -->` | `roadmap.json` | every blocked milestone with the blocker it records |
| The PUP | `<!-- pc5:pup:begin -->` | the tracked `PS5UPDATE.PUP` | pointer size and hash, declared object size, payload absent, package unverified |

### What is generated into `docs/ABOUT.md`

`docs/ABOUT.md` is the long-form orientation, and the generator owns three blocks in it
for the same reason it owns the six above: those are the parts of the page that a data
change must move by itself.

| Block | Markers | Source | Shows |
|---|---|---|---|
| Identity | `<!-- pc5:about-identity:begin -->` | `roadmap.json`'s `product` object, plus the licence pair — `LICENSING.md`'s grant and `LICENSE`'s text — through `tools/licensing.py` | the identity and destination sentences verbatim, the success hierarchy, non-goals, what the destination cannot be satisfied by, the licence id, and the authority document with its version |
| Capabilities | `<!-- pc5:about-capabilities:begin -->` | `roadmap.json` milestones and `foundations[]` | the five standings with their counts, every `complete` milestone with the evidence paths it cites, every `does_not_complete` line of each foundation, and the in-progress, planned and blocked sets |
| Progress | `<!-- pc5:about-progress:begin -->` | `tools/completion.py`, the same model the README's completion block uses | the criteria-weighted percentage, the state counts, the current milestones, the last verified record, the newest hosted run, and release status |

Both pages go through one `apply_block`, so one code path writes and checks either, and
each page carries its own label in a failure message. Two properties are worth stating
because they were the design decisions, not accidents:

* **A page the generator owns is never created by it.** `sync` skips an absent page and
  `check` fails it, so a deleted `docs/ABOUT.md` comes back through a deliberate commit
  rather than reappearing on somebody else's run.
* **Links inside a generated block are written for the page that receives them.** The
  About page lives in `docs/`, so a block rendered into it says `evidence/…` and
  `../tools/…` where the same text in the README says `docs/evidence/…` and `tools/…`.
  Without that, a correct block would be a broken link depending on which file it landed
  in — which is exactly how a generated page acquires dead links nobody typed.

**Statistics outside those blocks are rejected, on both pages.** `make
presentation-check` scans the prose for `N checks`, `N milestones`, `N workstreams`,
`N-byte`, `N.N GB` and any `N%`, because those are exactly the numbers that used to be
typed once and quietly rot. The scan removes the generated regions first, so a
generated percentage is never reported as typed; the ban applies to the prose, where the
staling happens.

The milestone status table is generated into the block between
`<!-- pc5:status:begin -->` and `<!-- pc5:status:end -->`. Milestone rows take their
label from milestone state (`complete` → VERIFIED, `in_progress` → IN PROGRESS,
`not_started` → NOT STARTED, `blocked` → BLOCKED); `M1` renders as **NOT TESTED**
until a physical record exists under `docs/evidence/`. Gate rows declare their label
but keep it only while the document they name still contains the evidence token, so a
reworded test record cannot leave a VERIFIED chip behind.

## 4. Generation and checking

```sh
make presentation           # write: seven SVGs and nine blocks, in README.md and docs/ABOUT.md
make presentation-check     # read: are they current?  also run by `make check`
make completion             # read: the completion model as one line
make completion-check       # read: is the completion data self-consistent?
make progress-check         # read: both of the above, the gate the workflow runs
make progress-record RUN=<id>   # write: file a hosted run as CI evidence
make progress-decide            # read: does this merge owe a dashboard update?
make progress-preview           # read: what would the maintenance commit write?
make progress-commit            # write: commit and push it, only if there is a diff
make docs-check             # read: links, anchors and documented commands in the tree, plus
                            #       the About contract (see below)
make validation             # read: the committed records, and whether they are coherent
make record-validation      # write: run the gates and replace the records (explicit only)
```

`make check` runs `presentation-check` and `docs-check`, never `presentation`, so a test
run cannot rewrite tracked documentation. Writing is always an explicit act.

`check` fails on: a stale figure (bytes differ from a fresh render), a hand-edited
status block, a broken local link or image path in `README.md`, `docs/ABOUT.md` or this
document, an image without alt text, a figure that lost its `<title>`/`<desc>`, a page
that stops embedding a figure it is supposed to show, a missing honesty token, or one of
the banned claims below.

The banned list exists because presentation is where a project is most tempted to
overstate itself: "runs PS5 games", "boots on any PC", "Secure Boot supported",
"CI passing", "PS5 firmware included", "production ready", "fully compatible".
Each is false here for a specific reason recorded in the tool, and the list is applied
to every page the generator writes, so an About page cannot say what the front page may
not.

The honesty tokens are per page, not global: the README must contain "NOT TESTED",
"BLOCKED", "Git LFS pointer" and the destination sentence, because it is the front
page; `docs/ABOUT.md` must contain those and "nothing published", because a long-form
orientation page that does not say what is missing has failed at its one job. A global
list would let either page satisfy the other's obligations by accident.

Determinism: no timestamps, no random ids, no locale-dependent formatting, sorted
iteration, integer geometry. The same `roadmap.json` produces byte-identical figures on
any machine, so a diff means the data changed — which is the point.

### The About contract in `make docs-check`

`tools/presentation.py` keeps the generated regions true. It cannot say anything about
the prose *around* them, and prose is where an About page drifts: a section deleted, a
licence named from memory, a milestone called "verified" because it sounds finished. So
`tools/docs_check.py` gained five rules for the pages that speak about the project, all
pure functions over text, each with a fixture test that must fail:

| Rule | What it holds to |
|---|---|
| Required sections | `docs/ABOUT.md` carries its eight numbered headings, in order; a dropped or reordered section fails |
| Generated regions present | the three `pc5:about-*` marker pairs exist, so the numbers on the page are still generated rather than quoted from a previous month |
| Licence identity | every GPL/MIT/Apache identifier in `README.md`, `docs/ABOUT.md`, `CONTRIBUTING.md` and `docs/BRAND.md` must equal the id derived from the project's own notice (a bare `GPL-2.0` family name is allowed, a different id is not). The derivation lives in `tools/licensing.py::read`, which the renderer and this gate share so that they cannot disagree about what the repository grants: it reads a **grant sentence naming this project** out of `LICENSING.md`, so a verbatim copy of the FSF text yields no id rather than the one in its boilerplate, and it then hashes `LICENSE` against the canonical `GPL-3.0-only` digest pinned in the same module, because an official body with a sentence spliced into it is no longer the official body. Five families of refusal — the notice missing or unreadable, a notice that grants nothing, a grant that is incomplete (no version, no *only* / *any later version* clause, or a family this tool cannot print), a notice that contradicts its own asserted identifier, and an official text that is not the adopted document — are returned as a sentence to print rather than raised, so an ambiguous licence fails one gate instead of disabling the other eight blocks; `docs_check` reports it once, against whichever file is at fault |
| Cited paths | a backticked repository path with a directory and a suffix, in the documents that describe the tree, has to exist — with two exclusions, recorded rather than hidden: paths under a generated or external prefix (`build/…`, an EDK2 package), and a citation that names itself historical on its own line, because "the pipeline used to live at `ci/build.yml`" is a true sentence about a file that is gone |
| Milestone standing | a clause that calls a milestone verified, experimental, blocked or planned is compared with that milestone's `state` in `roadmap.json`, clause by clause, so comparing two milestones in one sentence is allowed and calling open work "verified" is not |

Two limits are deliberate. The rules read the page's prose, never a generated block —
checking the generator's own output against the data it came from is `presentation.py`'s
job, and a second validator for one fact is how two of them end up disagreeing. And a
missing `docs/ABOUT.md` is reported by the link rule that pointed at it, not also by
this one: one fact, one failure.

## 4b. Where the numbers come from

A statistic is only worth printing if somebody can trace it. The chain is:

```
docs/architecture/roadmap.json            milestones, states, workstreams, version, date
        │                                   the only source of progress numbers
docs/evidence/validation/<gate>.json      one record per gate: command, exit code, result,
        │                                   check counts, commit, date, dirty flag
docs/evidence/<machine>.json              physical-boot records, validated by hw_record.py
PS5UPDATE.PUP                             the pointer itself: bytes, hash, declared size
        ▼
tools/presentation.py  →  docs/assets/*.svg  +  the five README blocks
```

`tools/validation.py` is what writes the records, and it can only write one by *running*
the gate: `run` captures the exit code, reads the test counts from the runner's own
output, stamps the commit and the date, and classifies the outcome as `passed`, `failed`,
`not-tested`, `skipped` or `error`. A missing dependency becomes `not-tested`; it cannot
become a pass. `verify-records` (and `make validation`) re-checks that no record claims
`passed` without a zero exit code, or `failed` with one.

Records are **snapshots**. Each row states the commit it ran at, and the evidence block
says so in words: a run at an earlier commit is not evidence for the current tree.

## 4a. The completion model

`tools/completion.py` is the single place the headline percentage is computed. The figure
in the README, the generated block, the banner chips and the maintenance workflow all read
it; none of them does arithmetic of their own.

**Weighting.** Each milestone's weight is the number of acceptance criteria the roadmap
declares for it (`len(milestone["acceptance"])`). There is deliberately no weight table: a
separate table is a second source of truth that can be renumbered without touching a
milestone, and it drifts. A milestone earns its full weight only when its state is
`complete`, which in this repository's data means its criteria are met *and* evidence is
filed. Percentages are rounded down to one decimal (`math.floor(p * 10) / 10`), because a
completion figure that rounds up is a small lie told with the best intentions.

**Partial credit** exists in the model but is switched off by the data: a milestone may
declare an integer `criteria_met`, and only that integer is credited. No milestone declares
one today, so the four `in_progress` milestones score zero, and the README says so in
words. The model refuses to infer credit from any other signal — not from a diff size, not
from a merged PR, not from a green build.

**Two formulas, printed together.** `percent` is criteria-weighted (31/116 → 26.7 %) and
`percent_by_count` is milestone-count (9/35 → 25.7 %). `check` reports the agreement between
them, and the figure prints both. If the headline ever diverges from the count view by a
lot, that is visible on the page rather than something a reader has to notice by accident.

**Validation is a refusal, not a warning.** `validate()` fails on a duplicated milestone
id, a state outside the four `tools/roadmap.py` allows, a milestone with no `acceptance`
(its weight would be undefined, so it cannot participate in a weighted mean), a
`criteria_met` outside `0..len(acceptance)`, and a `complete` milestone with no `evidence`.
Each of those is a way a generator could produce a confident, wrong number, so the
generator stops instead.

**One reader.** `presentation.load_records()` is a delegation to
`completion.load_records()`, not a second implementation of it. It used to be its own
loop over the same directory with the same schema filter and a different tie-break, which
is exactly how a figure and a stat card can end up describing two different verifications.
`test_the_figure_and_the_model_share_one_reader` pins it; if a future edit re-introduces a
second loader, the stamp test fails.

**Determinism.** The model reads files and nothing else: no clock, no network, no `git log
-1`. The verification stamp is the date recorded inside the newest
`docs/evidence/validation/*.json`, not the day the figure was rendered. That is what makes
`make progress` safe to run in CI: re-rendering a tree that has not gained evidence writes
the same bytes, so the workflow's `git diff --exit-code` says "nothing owed" and no commit
happens. A dashboard that changes every night with nothing new to report would be
decoration, and decoration in a README is how overstated progress gets published.

## 4b. Animation, and how it is kept honest

Every figure is a self-contained SVG with no external references of any kind: no `<style>`,
no stylesheet, no `url()`, no `xlink:href`, no `<image>`, no `https://`, no script, no
`@keyframes`. GitHub sanitises all of those out of a referenced SVG; the only motion that
survives is declarative SMIL, which runs inside the image's own security context.

Animation is confined to figures that express a quantity — the banner chips, the hero rule,
the completion ring and its bars, the workstream bars in the progress figure. The four
figures that carry a *state* rather than a quantity (architecture, boot workflow, evidence
ladder, status) are static on purpose: an animated state diagram invites a reader to
mistake motion for progress, which is precisely the failure this page exists to prevent.

The contract, enforced by `animation_problems()` in `tools/presentation.py` and therefore by
`presentation-check`, `make check`, `make progress-check` and the maintenance workflow:

| Rule | Why |
|---|---|
| every `<animate>` carries `fill="freeze"` | a figure that loops back to zero is a loading spinner, and a spinner implies something is coming |
| every animated element's static attribute already equals the animation's final value | a client with animation disabled, a print, a feed reader, or a screenshot must show the true numbers, never a mid-flight or zeroed state |
| one `<animate>` per element per attribute | two animations on the same attribute of the same element have undefined order in some engines, and `to` values that disagree would be undetectable |
| no external reference, no `<style>`, no script | the sanitizer would strip it and the figure would silently degrade in exactly the environment it is written for |
| figures stay under 32 KB each, 128 KB total | a README that takes seconds to paint gets skipped |

`arc()` and `grow_bar()` exist so those rules are the *default*: they write the final value
into the element and animate from the empty state, so a hand-rolled `<animate>` is the only
way to break them.

## 4c. Mobile rendering

The figures are `width="1200"` (1000 for the progress figure) with no `preserveAspectRatio`
overrides, so on a 390-point viewport they scale to roughly a third of their drawn size: a
14 px label renders near 4.5 px. Two consequences are treated as requirements.

1. A figure never carries the only copy of a number. `test_every_number_in_the_figure_is_in_the_text`
   extracts every numeric token rendered inside `pc5-completion.svg` and fails if any of them
   is missing from the generated README block, so the table below the figure is provably a
   superset of the picture. Anyone on a phone, with images blocked, in a screen reader, or
   in a feed gets the full account.
2. The figures still have to be legible as pictures, so type is never small inside one: the
   smallest `font-size` across all seven figures is 10 px (≈3.3 pt on a 390-point phone
   viewport), and the labels a reader is most likely to zoom for — the chips, the bar
   labels, the release-gating tally — are 11 px. The figure is 1200 units wide, so a
   maintainer checking a new label should assume it renders at about one third of that.

`alt` text stays a factual caption — what the figure shows and where its facts come from —
because that string is what a reader who cannot see the figure receives, and `check()`
rejects any `![...]` whose file is missing or whose alt text is empty.

## 4d. The maintenance workflow

`.github/workflows/progress.yml` reacts to `workflow_run` on `build` completing, plus
`workflow_dispatch` for a dry run. Decisions live in `tools/progress_record.py`
(`guard`, `record`, `decide`, `commit`) so they can be unit-tested and re-run locally; the
YAML only sequences them.

| Input | Outcome |
|---|---|
| `build` concluded `success`, the merge touched a significant path, regeneration differs | commit and push `docs: refresh the progress dashboard at <sha>` |
| `build` concluded `success`, nothing significant changed | no commit; the run reports `noop` |
| regeneration byte-identical | no commit; `git diff --exit-code` is the whole test |
| conclusion `failure`, `cancelled`, `skipped`, `action_required`, or still `in_progress` | **refuse**: the model keeps describing the last verified state, and the log says so |
| HEAD is itself a progress commit, or authored by `github-actions` | the guard skips; this is the second loop lock |
| `workflow_dispatch` | the same path with no CI record filed, for rehearsal |

**Significant paths** are `docs/architecture/roadmap.json` (the model),
`docs/evidence/validation/*` and `docs/evidence/ci/*` (the verification records),
`boot/`, `kernel/`, `kernel64/`, `psm/`, `user/`, `tools/`, `tests/`, `Makefile`,
`linker64.ld`, `Dockerfile` (the source), `.github/workflows/` (the pipeline), and
`tests/requirements*.txt`. A merge touching only prose is classified as nothing: the
dashboard does not move, and `docs/PRESENTATION.md`, the README, and the figures can be
edited freely without generating a refresh commit.

**Why `workflow_run`.** `push` fires before `build` has verified anything, so a dashboard
driven by `push` publishes unverified numbers — the exact failure mode the whole design
exists to prevent. `schedule:` is deliberately absent: a timer restamps the page with
nothing new to say and turns the "last verified" row into a lie about freshness. A
`push: paths:` trigger plus `[skip ci]` would work but couples the refresh to the merge
rather than to the verification, and a bot commit skipped by CI could then strand the
figures behind the data.

**Loop control, and where the real limits are.** A push made with the run's own
`GITHUB_TOKEN` does not start `push`-triggered workflows, so a dashboard commit does not
re-trigger `build.yml`, and therefore does not re-trigger this workflow either. The guard
step is the second lock because the first is GitHub's behaviour rather than this
repository's. Two consequences to know before changing anything here: `build.yml` triggers
on `push: branches: ["**"]` with no path filter, so *any* push to any branch runs the full
30-minute build, and the maintenance workflow's own push to `main` is exactly such a push
— the `[bot]`/`github-actions` author is what a reviewer should look for if that ever needs
auditing. And because a `GITHUB_TOKEN` push does not re-trigger `build.yml`, a dashboard
commit lands on `main` without its own hosted verification; it is safe for the reason above
(it was generated and checked inside the run that verified its parent commit), but the
branch-protection "required status check must pass on the latest commit" rule will refuse
it. Repositories that need both should have a human merge the refresh, or use a
repository-access token — and must then keep the guard, because a PAT push *does*
re-trigger workflows.

**Failure modes, and what the operator does.**

| Symptom | Cause | Action |
|---|---|---|
| run is red at `Recompute the completion model` | `roadmap.json` merged in a state the model refuses (duplicate id, missing `acceptance`, bad `criteria_met`) | fix the roadmap data; the workflow will not publish from it, by design |
| run is red at `Verify the generated state` | the generators disagree with the tree, or a test that pins the contract failed | run `make progress` and `make progress-check` locally and commit the difference |
| `nothing to do: the loop guard fired` | HEAD was a progress commit | expected; ignore |
| action `noop` on an ordinary merge | only prose changed | expected; ignore |
| action `refuse` | the triggering run did not conclude `success` | fix the build; the dashboard stays at the last verified state meanwhile |
| a push was rejected (non-fast-forward) | `main` moved while the run was working | re-run; the workflow never force-pushes, and never rewrites history |
| a stale percentage visible in the README after a merge | the refresh run has not finished, or was refused | check the Actions log; `make presentation-check` locally proves which side is stale |

## 5. What this presentation does not do

* It shows no screenshot of a boot, because no boot on physical hardware has happened
  and an illustration of one would be a fabricated result.
* It publishes no compatibility percentage: the only counting it does is milestones by
  state, and it says so under the figure.
* It does not claim continuous integration. The pipeline definition exists; the
  workflow is not active.
* It does not make the destination look closer than it is: `M19` is drawn blocked, in
  red, in three of the four figures.
