# AGENTS.md — what an agent working on PC5 owes this repository

This is the authoritative instruction file for AI agents and automation working in
`SpicyBadran/PC5`. It is deliberately small and deliberately binding: it states the duty
that no other document carries, and it points at the rules that other documents already
own instead of restating them.

**Scope.** Everything here applies to every agent, in every session, on every branch,
including agents that were not asked to read this file. If you are an agent and you have
opened this repository, these rules are already in force.

**One file, one policy.** `CONTRIBUTING.md` owns the evidence and documentation rules
(`Which document is authoritative for what`, the non-negotiable list, the gate loop).
This file owns the *post-merge* duty below and does not restate those rules; the two
must not drift. `docs/PRESENTATION.md` documents how the figures are generated,
`docs/TESTING_STRATEGY.md` how evidence levels are defined, and
`docs/architecture/roadmap.json` is the only source of milestone state. Do not create
another instruction file (no `CLAUDE.md`, no `.cursorrules`, no second policy document):
a competing file is a bug, because two authorities is how a rule stops being one.

---

## 1. The README progress dashboard is a deliverable, and a merge is its trigger

**after every successful merge into the primary development branch (`main`), the agent
must reconcile the progress dashboard — without waiting to be asked.** The user must
never have to say "update the README", "refresh progress", or anything like it. A
dashboard that lags the branch it describes is a false statement in the project's front
window, which is why this is a duty and not a courtesy.

The trigger is a *successful merge*. It is not a pushed branch, not an opened pull
request, not `mergeable=CLEAN`, and not **passing tests**.

The reconciliation, in order, each time:

1. **Detect the merge and name it by its full SHA.**
   `git fetch origin main && git rev-parse origin/main` — never trust a remembered SHA,
   a local `main`, or a pull-request head. Then
   `make dashboard-reconcile` (`tools/dashboard_reconcile.py`), which prints every
   first-parent merge that is newer than the commit at which
   `docs/assets/pc5-progress-matrix.svg` and the generated README blocks were last
   written, and exits 1 while any of them is unprocessed.
2. **Read what actually landed.** `git show --stat <merge>`, `git diff
   <previous-watermark>..HEAD -- docs/architecture/roadmap.json psm/ docs/evidence/
   docs/ PS5UPDATE.PUP`. Determine which subsystems, milestones, requirements, tests,
   evidence records and documentation the merge touched. Judge impact from the diff, not
   from the merge's title.
3. **Recalculate every affected figure from the authoritative sources** —
   `docs/architecture/roadmap.json` for milestone state, level and deliverables; the
   platform contract's generated tables for modelled operations; `psm/registry.py` for
   claim classifications; `docs/evidence/validation/*.json` for gate results;
   `docs/evidence/*.json` for physical records; the tracked `PS5UPDATE.PUP` for the
   package. Do not retype a number anywhere: `tools/presentation.py` derives each one,
   and the same function feeds the SVG and the README text fallback.
4. **Regenerate the visualization and the README blocks:** `make presentation`. Then
   prove it: `make presentation-check` and `make docs-check` must both exit 0, and
   `make check` must pass. A stale figure is a defect, not a formatting nuisance.
5. **Update the generator or the data if the merge changed what the dashboard can
   honestly show.** A new evidence class, a new gate or a new milestone level is a
   presentation-layer change: extend `tools/presentation.py` and its test in the same
   commit rather than hand-writing a row.
6. **Verify synchronisation:** the committed figure, the README blocks, `roadmap.json`
   and the validation records must all agree at the commit you are about to write. The
   four checks are `make presentation-check`, `make docs-check`,
   `python3 -m unittest tests.test_progress_matrix tests.test_presentation` and
   `make dashboard-reconcile` (which must then exit 0).
7. **Commit the reconciliation** on the established documentation workflow: a focused
   commit that states the merge SHA it reconciles, then pushing the branch and opening a
   pull request for a human to merge — exactly like every other change here.
   Never commit a dashboard update straight to `main`, and never edit the generated
   regions of `README.md` by hand.
8. **Report it:** the merged commit's full SHA, the dashboard-update commit SHA, the
   files changed, every figure that moved and the figures that did not, the commands run
   with their real exit codes, and what remains unverified.
9. **If nothing moved, say so in the report.** "No displayed metric changed, because the
   merge touched `boot/` only" is a result; silence is indistinguishable from a skip.

### Catching up

If **multiple merges** landed between two sessions, reconcile each of them against the
current repository state in one pass, and list every merge SHA you processed. If a
**another person or agent** merged the pull request, the duty is unchanged: the trigger
is the merge, not who performed it. If this agent itself performs an explicitly
authorized merge, the reconciliation is part of that same workflow, in the same turn.

### When the impact cannot be established

Preserve the existing figures, and report the dashboard update as **outstanding** with
the reason — never quietly skip it, never estimate a number to fill a cell. Typical
cases: the merge commit is unreachable from the fetched refs, the evidence it cites is
not in the tree, or a gate it relied on has no committed record. In each of those the
honest figure is the one already on the page, annotated in the report as unverified
against the new state. A missing metric is labelled unknown; it is never invented.

## 2. Hard rails while doing it

- **never merge** anything in order to trigger, test or exercise this workflow. Merging
  is a maintainer decision, and a dashboard update is never a reason to land code.
- **never inflate** progress. Do not change a milestone state, an acceptance criterion,
  an evidence class or a validation record to make the dashboard look better; blocked
  milestones stay visibly **blocked**, and a green cell must stay backed by a file.
- Do not weaken, rewrite or re-scope acceptance criteria while touching the presentation
  layer; the dashboard describes the plan, it does not negotiate with it.
- Do not force-push, do not rewrite published history, do not push to `main`.
- Read `git ls-remote` and `gh pr view` rather than trusting a local clone's refs, and
  do not treat `mergeable=CLEAN`, a passing `make check`, or an absence of reported
  checks as CI, approval or review.
- Do not touch an unrelated branch, PR or workspace while reconciling; the dashboard
  update is its own narrowly scoped change.

## 3. Commands

```sh
make presentation          # write: the seven SVGs and the six generated README blocks
make presentation-check    # read: are they current?  run by `make check`
make docs-check            # read: links, anchors, documented commands and make targets
make dashboard-reconcile   # read: which merges the dashboard has not caught up with
make post-merge            # write + read: presentation, then the three checks above
python3 tools/dashboard_reconcile.py --json
```

`make presentation` is the only target in this list that writes, and it writes only the
files the presentation layer owns: `docs/assets/*.svg` and the marker-bounded blocks in
`README.md`. `make check` never runs it, so an ordinary test run cannot rewrite tracked
documentation.
