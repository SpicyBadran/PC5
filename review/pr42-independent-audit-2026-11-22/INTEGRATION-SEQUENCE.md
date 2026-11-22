# §8 the #40 → #41 → #42 → bootstrap sequence, evaluated and simulated

## Why the stack is shaped like this

`main` is an ancestor of #40's head (`merge-base(main, pr40) = 52f682c…`, `ahead = 1`), #40's head is
#41's merge-base (`ahead = 9`), and #41's head is #42's (`ahead = 6`). So the three are one linear
chain, each PR is *exactly* the commits after the previous head, and no PR contains a file that the
next one does not also touch. #42 changes only `tools/dashboard_reconcile.py`,
`tests/test_dashboard_reconcile.py`, `AGENTS.md`, `CONTRIBUTING.md`, `docs/PRESENTATION.md` and its own
`review/**` evidence — 48 files, +8780/−34, of which the code and tests are 2.

That has three consequences for integration, all of them verified below rather than assumed.

## Simulation, in a disposable clone with a real bare `origin`

Nothing was pushed to GitHub. Each method got a fresh clone, `main` forced to `52f682c…`, a local
bare origin so `origin/main` resolves exactly as it does on GitHub, and the three PR refs fetched
from the real remote. `raw/sim2.log` and `raw/sim3.log` are the unedited transcripts; every exit code
in them is the program's own (no pipes).

### `git merge --no-ff` — the method this stack is built for

| step | result |
|---|---|
| land #40 | merge commit `09096d5b17…`, tree `d041fd8367…` — **identical to #40's head tree** |
| land #41 | `b236b6d75e…`, tree `e9d2c7073b…` — identical to #41's |
| land #42 | `7e4f3897e2…`, tree `9e184d7714…` — identical to #42's; `git diff --stat` vs `7095814`: no diff |
| `make -s presentation-check` | exit 0 — `PRESENTATION OK: 7 figures and 6 blocks current, 13 status rows match roadmap.json, 11 validation record(s) read` |
| `make -s docs-check` | exit 0 — `DOCS OK: 76 Markdown files, 36 make targets` |
| `make -s citations-check` | exit 0 — `CITATIONS OK: 258 commit reference(s) resolve across 11 documents` |
| first read of the merged `main` | exit 1: `OUTSTANDING — docs/evidence/dashboard-reconciliation.json does not exist on <merged head> yet … Bootstrap it with --record --bootstrap --boundary <full SHA> --inspect <full SHA>` |
| `--record --bootstrap --boundary 52f682c… --inspect <the 3 merges>` | exit 0 — `bootstrapped … at boundary 52f682c84359 naming 3 commit(s)` |
| commit the receipt **alone**, push | `make -s dashboard-reconcile` → **exit 0**, `commits since then 4 on the first-parent line (0 needing inspection, 3 already named in the receipt, 1 generated-output only)` |

The receipt-only commit landing as CURRENT is the property the whole design turns on, and it holds on
the real merged history, not only in a fixture.

### `git rebase` + `--ff-only` (GitHub's "Rebase and merge")

Each branch replays cleanly onto the previous result — no conflicts, because the chain is already
linear — and `main`'s tree after each step is **identical to that PR's head tree** (same three tree
hashes as above). Gates green. The read says exit 1, and the bootstrap works, but it must name
**16** first-parent commits (`bootstrapped … naming 16 commit(s)`), after which the read is CURRENT
with `17 on the first-parent line (0 needing inspection, 16 already named in the receipt, 1
generated-output only)`. So the choice of method is free as to *content* and not free as to *labour*:
a rebase-merge multiplies what a human has to attest to, five-fold.

### `git merge --squash` — do not use it on this stack

Landing #40 by squash works (same tree). Squashing #41 onto that `main` then **fails**:
`Automatic merge failed` with **9 conflicted paths** — `AGENTS.md`, `CONTRIBUTING.md`, `Makefile`,
`README.md`, `docs/PRESENTATION.md`, `docs/assets/pc5-progress-matrix.svg` and three more — because
squash-landing #41 re-imports #40's diff *as well* (the merge base is `52f682c…`, not #40's head,
since the squashed commit shares no ancestry with #41's branch). A squash of a stacked PR therefore
does not "just" collapse the layer you wanted: it re-lands the layer below it and fights the copy.
#42's measurement (`s38` in the repo's own suite) already says a squash mints a new SHA and must be
re-recorded; this adds that on *this* stack it does not even apply cleanly.

### Two hazards specific to this stack

1. **#40's head ref must stay immutable while #41 is open.** Amending it in the simulation moved
   `merge-base(pr40, pr41)` from `8e3f0b6…` to `52f682c…`, and #41's diff against its own base grew
   from 13 files to 13 + #40's 9 (5 tool/test files among them): #41 would start showing #40's work
   as its own, and #42's base (`pr41`'s head) would no longer be a merge-base at all.
2. **`--record --bootstrap` can be re-run over a landed receipt, silently.** On the simulated merged
   `main`, a second bootstrap naming only *one* of the three merge commits exited 0 and overwrote the
   receipt. It is **not** a trust hole — after that receipt lands, the read answers exit 1 with three
   `OUTSTANDING` merge commits, each listing what it introduces (`it introduces 9 unreviewed
   commit(s): 5bc5d5d4353f, a81f71cbd15b, …`), and `--record` repairs it in one step. So a
   receipt *shrink* is loud. What is missing is a warning at write time that an existing receipt is
   being replaced, and a refusal to do it with a *narrower* set. Reported as an observation, not as a
   reason to hold #42: the failure direction is safe.

## The sequence this review recommends

`#40 → #41 → #42 → bootstrap receipt → main`, each `--no-ff`, and the bootstrap run once after #42
lands — with #43 (this branch) either folded in before that bootstrap or landed with it, so the
receipt is written by the tool that answers the four open findings correctly. Merge order is not
optional: #41 merged into a *feature* branch never reaches `main` without a retarget, and a
retarget of #41 onto `main` while #42 is open would leave #42's base dangling.

`docs/evidence/dashboard-reconciliation.json` does not exist on `main` today, so after #40 lands
alone, `make dashboard-reconcile` on a fresh clone answers **`CURRENT` at exit 0** — measured,
`raw/sim.log` §A: `HEAD 8e3f0b642a92 / dashboard written at 8e3f0b642a92 / CURRENT — every merge on
the first-parent line is reflected in the dashboard`. That is #40's watermark tool, and it is the
false green #41 and #42 exist to remove. **Merging #40 without #41 and #42 behind it ships a
reconciler that attests to a reconciliation nobody performed.** The exposure is one PR deep, but it
is exactly the class of error the stack is for, so the three should not be separated by more than a
bootstrap's worth of time.
