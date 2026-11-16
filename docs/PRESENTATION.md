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
  no text smaller than 11 px, and every figure has `<title>` and `<desc>` for screen
  readers plus descriptive `alt` text in the README.

## 3. The four figures

| Figure | What it shows | Where the facts come from |
|---|---|---|
| `docs/assets/pc5-banner.svg` | wordmark, roadmap version, workstream count, completed-milestone count, destination state, and the "no Sony software" line | `roadmap.json` `version`, `workstreams`, milestone states, `M19` state |
| `docs/assets/pc5-architecture.svg` | eight layers from PC hardware to genuine system software, each badged with the state of the milestones it maps to | `roadmap.json` states per layer; firmware forced to **NOT TESTED** because `M1h` completing says nothing about a real machine's firmware |
| `docs/assets/pc5-progress.svg` | one bar per workstream split by state, blocked counts, and the longest dependency chain to `M19` | `roadmap.json`; the chain is computed by the same longest-path rule `tools/roadmap.py path` uses |
| `docs/assets/pc5-boot-workflow.svg` | six steps from building the image to Sony software executing, each labelled verified, not tested, testing required or blocked | the gates in `Makefile` plus the state of `M1` and `M19` |

The README's status table is generated into the block between
`<!-- pc5:status:begin -->` and `<!-- pc5:status:end -->`. Milestone rows take their
label from milestone state (`complete` → VERIFIED, `in_progress` → IN PROGRESS,
`not_started` → NOT STARTED, `blocked` → BLOCKED); `M1` renders as **NOT TESTED**
until a physical record exists under `docs/evidence/`. Gate rows declare their label
but keep it only while the document they name still contains the evidence token, so a
reworded test record cannot leave a VERIFIED chip behind.

## 4. Generation and checking

```sh
make presentation            # rewrite the four SVGs and the README status block
python3 tools/presentation.py check
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
iteration, integer geometry. The same `roadmap.json` produces byte-identical figures on
any machine, so a diff means the data changed — which is the point.

## 5. What this presentation does not do

* It shows no screenshot of a boot, because no boot on physical hardware has happened
  and an illustration of one would be a fabricated result.
* It publishes no compatibility percentage: the only counting it does is milestones by
  state, and it says so under the figure.
* It does not claim continuous integration. The pipeline definition exists; the
  workflow is not active.
* It does not make the destination look closer than it is: `M19` is drawn blocked, in
  red, in three of the four figures.
