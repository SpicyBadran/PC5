# §6 the README against the reference image · §7 where the duty is discoverable

## §6 — what the reference image is, and what was actually compared

The reference the user supplied is a **style** reference from a different project (a Vulkan/RDNA2
driver coverage dashboard). It is not PC5's target content, and nothing here "corrects" PC5's README
toward it. It is also **not present as a file in this workspace** (`find` for `*.png|*.jpg|*.webp`
under `/home/user` returns nothing), so pixel comparison is `not verified`: what can be verified is
(a) which artefacts each PR touches, (b) whether the figures are self-consistent with their data, and
(c) whether the visual language already carries the load-bearing ideas the reference is liked for.

| check | result |
|---|---|
| artefacts touched by #42 | none: `tools/presentation.py`, `docs/assets/pc5-dashboard.svg`, `pc5-progress.svg`, `pc5-progress-matrix.svg`, `pc5-banner.svg`, `README.md` are byte-identical between #41's and #42's heads (`sha256` in `HASHES.txt`). #42 changes no visual artefact, so no cosmetic work is bundled into the correctness fix |
| truthfulness of the dashboard | `make -s presentation-check` exit 0 at #40, #41, #42 and this branch, on fresh clones: `7 figures and 6 blocks current, 13 status rows match roadmap.json, 11 validation record(s) read, links and alt text resolve` — the numbers are generated from `docs/architecture/roadmap.json`, and a hand-edit that overstates progress fails this gate (F-2 is precisely a hand-edit reaching the *receipt* path and being refused) |
| generator stability | `make -s presentation` on a fresh clone of each ref leaves `git status --porcelain` empty (exit 0) — nothing in the chain depends on a stale figure |
| style overlap | the existing `pc5-progress-matrix.svg` already uses the reference's load-bearing grammar: fixed-width monospace, one compact `viewBox` column layout, a small size ladder (10/11/13/20), a status palette in which **blocked is its own colour**, counted from the file: `#34d399` green ×65, `#fbbf24` amber ×24, `#f4b9bd` blocked-pink ×24, `#8b98a8` muted ×126 (and 103 foreground / 88 panel fills) |
| deliberate non-goals | the reference's binary green/grey treatment is **not** copied: it would collapse `blocked` (6) into `not_started` (18) and hide exactly the distinction PC5's milestones carry, and its "code exists ⇒ green" convention would overstate PC5's evidence state (35/35 metrics recorded, 9 of 35 complete, M17–M21 blocked). The compaction ideas are recorded in `review/pr41-independent-audit-2026-11-22/05-README-VISUAL-REVIEW.md` and were **not** implemented — layout changes do not belong on a correctness branch, and none of them was needed to prevent misleading output |

One documentation truthfulness defect *did* concern the README, and it is on the correctness side of
that line: the contract table attributes an exit-code pair to the `make` target, which cannot express
it (R-7, `FINDINGS.md`). Fixed on this branch by a note under the row; no metric, count or colour was
changed, and `presentation-check`/`docs-check` still pass with the paragraph in place (the table sits
outside every generated span — `make -s presentation` leaves it untouched).

## §7 — the post-merge duty: permanent, discoverable, and not a service

The duty ("after every successful merge into `main`, reconcile and update the README dashboard") is
standing, and this review treats it as such: it is *not* something to trigger by merging anything, and
it does not lapse because the next person forgets it. Where it is written, at `7095814`:

| place | present | what it says |
|---|---|---|
| `AGENTS.md` | yes | §1 owns it: *"If another person or agent merged the pull request, the duty is unchanged: the trigger is the merge, not who performed it"*, and **"This is a standing responsibility of whoever is working in the repository, not a monitor. Nothing here runs by itself: there is no CI workflow configured in this repository, no Git hook is installed by `make`, and no scheduled job checks `main`."** |
| `CONTRIBUTING.md` | yes | line 10 "binding post-merge duty"; line 121 "None of this is enforced by a machine: this repository has no CI workflow and installs no [hook]" |
| `README.md` | yes | "There is no CI watching this; after a merge into `main`, running it is the contributor's job", plus the contract table for both targets |
| `docs/PRESENTATION.md` | yes | both targets documented with their fields and exit codes; "What is *not* claimed. Nothing enforces this automatically: the pipeline definition [does not exist], no CI service runs these gates on a push, and no Git hook is installed by…" |
| the command | partly | the tool prints `run: make post-merge` in every refusal, but `--help` said nothing about the duty or its non-automatic nature, and the module docstring did not either |
| the Make targets | partly | the comment block explains what the two targets do and why `post-merge` uses `--here`, but does not say that nothing runs them |
| **in fact** | — | `actions/workflows` total_count 0, `actions/runs` total_count 0, `.github/workflows` absent on `main`/#40/#41/#42, and `ls .github` shows only issue/PR templates. There is **no** workflow, webhook, hook or monitor. Stated plainly rather than implied |

Both gaps are closed on `fix/reconcile-premise-and-flags` (R-6): the module docstring and `--help` now
say the command is never run for you and explain that `make` flattens the 1-vs-2 distinction, and the
Makefile comment says so above the targets themselves. Nothing was added to *enforce* it — a fabricated
CI configuration would be the misleading move, not the helpful one.
