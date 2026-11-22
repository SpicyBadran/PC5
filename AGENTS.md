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
   a local `main`, or a pull-request head. Then `make dashboard-reconcile`
   (`tools/dashboard_reconcile.py --target origin/main`), which reads the reconciliation
   receipt at `docs/evidence/dashboard-reconciliation.json` **from `origin/main`**, walks
   every first-parent commit since the boundary that receipt records, and exits 1 while
   any of them needs inspecting. Nothing here is inferred from which files a commit
   touched: the previous watermark model could not clear its own delivery, and merge
   strategies that add no commit are invisible to `git log --merges`.
2. **Read what actually landed.** `python3 tools/dashboard_reconcile.py --json` lists the
   commits in the range with the paths each one brought and which dashboard drivers
   (`roadmap.json`, the platform contract, `psm/`, `docs/evidence/`, `PS5UPDATE.PUP`,
   `tools/presentation.py`) it moved. For a merge, `git diff <merge>^1 <merge>` is the
   diff that matters — what the branch added to `main` — and `git show --stat <merge>`
   shows it. Judge impact from the diff, not from the merge's title, and never conclude
   from an empty diff that there was nothing to look at: a no-op merge is still a merge,
   and it is recorded as reviewed in step 6 rather than skipped.
3. **Recalculate every affected figure from the authoritative sources** —
   `docs/architecture/roadmap.json` for milestone state, level and deliverables; the
   platform contract's generated tables for modelled operations; `psm/registry.py` for
   claim classifications; `docs/evidence/validation/*.json` for gate results;
   `docs/evidence/*.json` for physical records; the tracked `PS5UPDATE.PUP` for the
   package. Do not retype a number anywhere: `tools/presentation.py` derives each one,
   and the same function feeds the SVG and the README text fallback.
4. **Regenerate the visualization and the README blocks:** `make presentation`. Then
   prove it: `make presentation-check` and `make docs-check` must both exit 0, and
   `make check` must pass. A stale figure is a defect, not a formatting nuisance. The
   order is not optional: the reconciler verifies the working tree, so regenerate
   **before** you record, or the receipt is refused for output you were about to fix.
5. **Update the generator or the data if the merge changed what the dashboard can
   honestly show.** A new evidence class, a new gate or a new milestone level is a
   presentation-layer change: extend `tools/presentation.py` and its test in the same
   commit rather than hand-writing a row.
6. **Record the receipt.** `make post-merge` runs the generator, both read-only checks and
   `python3 tools/dashboard_reconcile.py --here --record`, which writes
   `docs/evidence/dashboard-reconciliation.json` naming every commit it inspected, the
   paths they changed, whether the figures or README moved, and the verification commands
   with their real exit codes. It runs those gates even when it decides it has nothing to
   write, so a stale asset cannot be waved through on the technicality that no new receipt was
   due. It refuses to write when a gate fails or when a non-
   generated file is uncommitted, so a receipt can only ever describe work that has
   landed. The first receipt on a line of history has to be seeded explicitly —
   `--record --bootstrap --boundary <full SHA> --inspect <full SHA>` — because there is
   no earlier record saying what was already read.
7. **Commit the reconciliation** on the established documentation workflow: one focused
   commit holding the regenerated figures, the README blocks and the receipt, with a
   message stating the merge SHA it reconciles; then pushing the branch and opening a pull
   request for a human to merge — exactly like every other change here. Never commit a
   dashboard update straight to `main`, and never edit the generated regions of `README.md`
   by hand. That follow-up commit is *exempt* from inspection because of what it is: it
   changes only generated output and the receipt. The exemption is a premise — that the
   output on disk is what the generator produces from that data — and the reconciler now
   *tests* the premise with the same gates before it says `CURRENT`. So the loop still
   closes in one step, and it closes only when the figures really do match. A merge that
   also changes a driver is not exempt and cannot be cleared by anything except being named
   in a receipt; neither can a commit that writes a figure by hand, or damages or deletes
   one. `CURRENT` means *nothing is owed to this range and the gates passed*; it has never
   meant "the artwork is intact", and that is `make presentation-check`'s job.
8. **Report it:** the merged commit's full SHA, the dashboard-update commit SHA, the
   files changed, every figure that moved and the figures that did not, the commands run
   with their real exit codes, and what remains unverified.
9. **If nothing moved, say so in the report.** "No displayed metric changed, because the
   merge touched `boot/` only" is a result; silence is indistinguishable from a skip.

### Catching up

If **multiple merges** landed between two sessions, they are all in the range: one
reconciliation pass inspects each of them against the current repository state and the
receipt names every SHA it read. Do not let a later commit clear an earlier one —
`--record` lists them individually, and a regenerated figure does not absorb the merge
that made it stale.

If **another person or agent** merged the pull request, the duty is unchanged: the
trigger is the merge, not who performed it. If this agent itself performs an explicitly
authorized merge, the reconciliation is part of that same workflow, in the same turn.

**This is a standing responsibility of whoever is working in the repository, not a
monitor.** Nothing here runs by itself: there is no CI workflow configured in this
repository, no Git hook is installed by `make`, and no scheduled job checks `main`. A
merge that nobody followed with `make post-merge` leaves the dashboard stale and
`make dashboard-reconcile` exiting 1 — which is the point of making the state
checkable rather than pretending it is enforced.

### When the impact cannot be established

Preserve the existing figures, and report the dashboard update as **outstanding** with
the reason — never quietly skip it, never estimate a number to fill a cell. The tool
distinguishes the two ways this can fail, and so should the report: *outstanding* (exit
1) means the change is readable but unreconciled, and *error* (exit 2) means it cannot be
established at all — the merge commit is unreachable from the fetched refs, `origin/main`
cannot be resolved, the receipt is malformed or names a commit that has not landed, or
the clone is shallow so the range cannot be enumerated. Exit 2 never means clean. In each
of those the honest figure is the one already on the page, annotated in the report as
unverified against the new state. A missing metric is labelled unknown; it is never
invented.

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
make dashboard-reconcile   # read: which changes on origin/main the receipt does not cover
make post-merge            # write + read: presentation, the checks, then --record --here
python3 tools/dashboard_reconcile.py --json          # the same verdict, machine-readable
python3 tools/dashboard_reconcile.py --here          # audit the checked-out branch instead
```

`make presentation` is the only target here that writes figures, and it writes only the
files the presentation layer owns: `docs/assets/*.svg` and the marker-bounded blocks in
`README.md`. `make check` never runs it, so an ordinary test run cannot rewrite tracked
documentation. `make post-merge` writes one further file, the receipt at
`docs/evidence/dashboard-reconciliation.json`, and it writes that receipt only after the
checks above have exited 0 — which is what makes the receipt evidence rather than a
claim. See [`docs/PRESENTATION.md`](docs/PRESENTATION.md) §6 for the receipt's fields.
