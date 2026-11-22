# Contributing to PC5

PC5 is one platform with one destination: genuine Sony PlayStation 5 system software
running on qualified PC hardware, through PC5's own boot path and platform model. Work
that serves that destination is welcome; work that redefines it is not.

## Before you start

Read [`AGENTS.md`](AGENTS.md) first if you are an agent or an automation: it carries the
binding post-merge duty (the README progress dashboard is reconciled after every
successful merge into `main`, without anybody asking) and it is the only instruction file
for agents. Then read
[`docs/architecture/PC5_ARCHITECTURE_SPEC_v2.md`](docs/architecture/PC5_ARCHITECTURE_SPEC_v2.md)
and [`docs/ROADMAP.md`](docs/ROADMAP.md). The milestone graph in
[`docs/architecture/roadmap.json`](docs/architecture/roadmap.json) is the authority on
what exists, what is next and what is blocked; `python3 tools/roadmap.py path` prints
the chain to the destination.

## Non-negotiable rules

1. **Do not weaken evidence.** No skipped tests, no relaxed gates, no placeholder
   driver that reports success without controlling the device. A new claim needs a
   test that would fail if the claim were false.
2. **Keep the levels apart.** An emulator pass is not a firmware boot, a firmware boot
   is not a physical boot, and a PC5 kernel boot is not PS5 system software executing.
3. **Never claim Sony software ran.** PC5 has not loaded the system software, and
   nothing in a contribution may imply it has. The PUP in the tree is a pointer; it is
   never extracted, decrypted, modified or redistributed (ADR-022).
4. **No circumvention.** No key extraction, signature bypass, secure-boot defeat,
   firmware decryption or authentication bypass — in code, tests or documentation. If a
   Sony requirement cannot lawfully be met, it is recorded as a blocker.
5. **Original work only.** No vendored archives, no borrowed project identity, no
   imported assets (ADR-025). `tests/test_project_independence.py` enforces it.
6. **Facts, not adjectives.** A published specification does not establish an internal
   register, a firmware interface or a boot process. Cite what you measured.

## Which document is authoritative for what

Several documents describe the project, and they are not equal. When they disagree, the
higher entry wins, and the lower one is a bug to fix:

| Question | Authority | Everything else |
|---|---|---|
| What exists, in what state, depending on what | `docs/architecture/roadmap.json` | `docs/ROADMAP.md` and `docs/architecture/PC5_MILESTONE_GRAPH.md` are rendered from it; README tables are generated from it |
| What the architecture is | `docs/architecture/PC5_ARCHITECTURE_SPEC_v2.md` | `docs/ARCHITECTURE.md` describes what is implemented today |
| Why a decision was made | `docs/TECHNICAL_DECISIONS.md` (ADR-001 … ADR-025) | summaries elsewhere cite the ADR, they do not restate it |
| What was measured, and when | `docs/TEST_RESULTS.md` (append-only, section per change) | README claims must be traceable to a section |
| What a boot attempt proved | the records under `docs/evidence/`, validated by `tools/hw_record.py` | `M1` stays `not_started` until one exists |
| What the destination is | `product.destination` in `roadmap.json` | every entry-point document must repeat it verbatim, and a test enforces that |
| What the public sees | `README.md` and `docs/assets/*.svg`, both generated/checked by `tools/presentation.py` | hand edits fail `make presentation-check` |

Nothing in that table is aspirational: each row is enforced by a test or a gate, which is
why the documents can be trusted to disagree loudly rather than quietly.

## The loop

```sh
make                    # build
make check              # host + kernel tests, contract, docs and generated-file guards
make verify             # structural verification of the image and its manifest
make roadmap            # the graph still validates
make docs-check         # links, heading anchors and documented commands
make presentation-check # figures and status block are current (make check runs it too)
```

If you touch the milestone graph, run `python3 tools/roadmap.py sync` so the rendered
document matches the data. If you touch the README or a figure, run
`make presentation` to regenerate the seven figures and six generated blocks from
`roadmap.json` and the committed records, then `make presentation-check` — the read-only check that `make check` also runs, and which
fails if a hand edit overstates progress. `make docs-check` does the same job for every
document in the tree: links, heading anchors and documented commands.

**If you run a gate, record it.** The dashboard quotes committed validation records, not
somebody's memory of a green run:

```sh
python3 tools/validation.py run check verify          # or: make record-validation
python3 tools/validation.py list                      # what is committed, and when it ran
```

A record carries the command, its exit code, the commit and the date, and `make
validation` fails if one is internally inconsistent. Never hand-write a record, and never
edit one to change a result: re-run the gate.

If you have physical hardware, the most valuable contribution is
[`docs/hardware/M1_PHYSICAL_BOOT_PROCEDURE.md`](docs/hardware/M1_PHYSICAL_BOOT_PROCEDURE.md):
a filled record validated by `tools/hw_record.py`, with the serial log committed
verbatim. A screenshot is not a substitute.

## After a merge

`AGENTS.md` requires the dashboard to be reconciled after every successful merge into
`main`. Two commands, answering two questions:

```sh
make presentation    # only if you are the one writing the reconciliation commit
make post-merge      # regenerate, check, and record `docs/evidence/dashboard-reconciliation.json`
make dashboard-reconcile   # read: does the committed receipt cover everything on origin/main?
```

`make post-merge` is what a *local* merge needs: it reconciles the branch you are standing
on (`--here`) and writes the receipt, which then travels with the commit. Once the change
is on `main`, `make dashboard-reconcile` reads that receipt **from `origin/main`** and
lists any first-parent commit since its boundary that no receipt has named — a merge
commit, a squash, a rebased pair of commits, a direct push, or a merge whose diff turned
out to be empty. It exits 1 while something is unreconciled and 2 when the question cannot
be answered at all; it never exits 0 because it could not read the history.

A reconciliation commit that carries nothing but regenerated figures and the receipt does
not itself require a further reconciliation — that exemption is what keeps the loop closed,
and it is a rule about file paths, not about who wrote the commit. See
[`docs/PRESENTATION.md`](docs/PRESENTATION.md) for the receipt's fields.

None of this is enforced by a machine: this repository has no CI workflow and installs no
Git hook. It is a documentation workflow and a human (or agent) duty, and it is not a
licence to merge anything — merging remains a maintainer decision.

## Commit and review

Focused commits with a message that says what changed and what it proves. Branches
never rewrite published history. A pull request states the evidence it adds, the gates
it ran and what it does not establish; a claim without the command that produced it is
reviewed as a claim, not as a result.
