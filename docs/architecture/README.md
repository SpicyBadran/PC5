# PC5 architecture

This directory is the **M0 architecture freeze** for the PC5 0.3 development
line. It was produced by auditing the repository before changing it, as the
directive requires, and it separates the project into two tracks that share
infrastructure without depending on each other.

Read in this order:

| # | Document | What it answers |
|---|---|---|
| 1 | [`REPOSITORY_AUDIT_2026-11-15.md`](REPOSITORY_AUDIT_2026-11-15.md) | What actually exists today, measured; what the documents got wrong; what must not break |
| 2 | [`PC5_ARCHITECTURE_SPEC_v1.md`](PC5_ARCHITECTURE_SPEC_v1.md) | The architecture: invariants, dual-track decomposition, interfaces, governance |
| 3 | [`LINUX_VS_FREESTANDING.md`](LINUX_VS_FREESTANDING.md) | Why Linux is the planned foundation and why the freestanding kernel is kept |
| 4 | [`HARDWARE_STRATEGY.md`](HARDWARE_STRATEGY.md) | Reference machines, qualification matrix, test protocol, evidence format |
| 5 | [`MIGRATION_PLAN.md`](MIGRATION_PLAN.md) | How the tree moves to the target structure without breaking what works |
| 6 | [`M0_M8_DEPENDENCY_GRAPH.md`](M0_M8_DEPENDENCY_GRAPH.md) | Milestones, gates, critical path, and the enforced Track A/B separation |
| 7 | [`RISK_REGISTER.md`](RISK_REGISTER.md) | Blockers and unproven assumptions, each with a way to settle it |
| 8 | [`FIRST_THREE_TASKS.md`](FIRST_THREE_TASKS.md) | The next three implementation tasks with exact acceptance criteria |

Supporting artefacts:

* [`roadmap.json`](roadmap.json) — the milestone graph as data.
* `tools/roadmap.py` — validates it, renders the graph into document 6, and
  **fails** if a Track A milestone depends on Track B.
* `tests/test_architecture.py` — runs that validation in `make check`, checks
  that every evidence path exists, that these documents' relative links resolve,
  and that this index and the directory agree.
* `docs/TECHNICAL_DECISIONS.md` — ADR-001 … ADR-013, including the four decisions
  made here (Linux foundation, dual-track gate, hardware qualification, and
  machine-checked architecture claims) and ADR-013 from the first executed task
  (512-byte disk sectors vs 2048-byte optical blocks).

## Status, stated plainly

The architecture above is **accepted**; almost none of it is **built**. What
exists today is a reproducible bootable diagnostic environment with a verified
boot chain, not an operating system. Track B has executed nothing, and the
repository contains no Sony firmware.

The fastest way to check any claim in these documents is:

```sh
make && make verify && make check && make boot-test
python3 tools/roadmap.py check
```

## Maintenance rules

1. Change the milestone graph by editing `roadmap.json`, then
   `python3 tools/roadmap.py sync`; commit both files. `make check` fails on a
   stale generated block.
2. A milestone may only become `complete` with evidence paths that exist.
3. If a measurement contradicts a document, the document changes that session.
   That rule is invariant I5 and it is why the audit lists nine corrections.
4. Decisions that are expensive to reverse get an ADR with alternatives and a
   reversal condition — not a paragraph in a design document.
5. No empty directories, no placeholder diagrams, no capability described as
   present before it is.
