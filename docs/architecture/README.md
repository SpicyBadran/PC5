# PC5 architecture

**PC5 — An independent operating-system project for PC hardware, built toward genuine Sony PlayStation 5 system software.** The
destination is to run the genuine Sony PS5 system software on compatible PC
hardware; every workstream below serves that, and the graph is checked by `make
roadmap` and `make check`.

This directory was rewritten on 2026-11-15 by the M0 architecture realignment.
The previous version froze a two-track model in which the destination was an
optional side project; that model is withdrawn (ADR-019, ADR-021) and its
document is kept unedited as history.

Read in this order:

| # | Document | What it answers |
|---|---|---|
| 1 | [`ARCHITECTURE_DISCREPANCY_REPORT.md`](ARCHITECTURE_DISCREPANCY_REPORT.md) | What the repository said, built and promised before this change, measured — including what the PS5 package in the tree actually is |
| 2 | [`PC5_ARCHITECTURE_SPEC_v2.md`](PC5_ARCHITECTURE_SPEC_v2.md) | **The authority.** Product identity, invariants, workstreams, success levels, the PS5 platform compatibility layer, interfaces, governance |
| 3 | [`PS5_PLATFORM_CONTRACT.md`](PS5_PLATFORM_CONTRACT.md) | The device contracts the model must present, with every unknown marked UNKNOWN |
| 4 | [`PC5_MILESTONE_GRAPH.md`](PC5_MILESTONE_GRAPH.md) | The milestone graph, its rules and the destination path (generated block) |
| 5 | [`REPOSITORY_AUDIT_2026-11-15.md`](REPOSITORY_AUDIT_2026-11-15.md) | The earlier audit: what existed on 2026-11-15 before the realignment, measured |
| 6 | [`LINUX_VS_FREESTANDING.md`](LINUX_VS_FREESTANDING.md) | The substrate comparison — read it with ADR-020, which decides the *role* Linux has |
| 7 | [`HARDWARE_STRATEGY.md`](HARDWARE_STRATEGY.md) | Reference machines, qualification matrix, test protocol, evidence format |
| 8 | [`MIGRATION_PLAN.md`](MIGRATION_PLAN.md) | How the tree moves to the target structure without breaking what works |
| 9 | [`RISK_REGISTER.md`](RISK_REGISTER.md) | Blockers and unproven assumptions, each with a way to settle it |
| 10 | [`FIRST_THREE_TASKS.md`](FIRST_THREE_TASKS.md) | The next three implementation tasks with exact acceptance criteria |

Superseded, kept as history: [`PC5_ARCHITECTURE_SPEC_v1.md`](PC5_ARCHITECTURE_SPEC_v1.md)
(two tracks, Linux as the product foundation) and `M0_M8_DEPENDENCY_GRAPH.md`
(deleted; its generated content lives in the milestone graph, its track gate is
withdrawn).

Supporting artefacts:

* [`roadmap.json`](roadmap.json) — the milestone graph as data: product identity,
  destination, success levels, workstreams and 35 milestones.
* `tools/roadmap.py` — validates it, renders the graph into document 4, and
  fails if: the destination is missing or unreachable, a required milestone is
  gone, a workstream has no milestone, an experimental milestone gates a release,
  or a complete milestone has no evidence.
* `tests/test_architecture.py` — the graph rules, each proved on a synthetic
  graph and on the shipped one, inside `make check`.
* `tests/test_product_identity.py` — the identity rules: the destination is
  level 5 and not complete, the substrate is not the product, forbidden claims
  are confined to historical documents, and the PS5 package is a pointer whose
  payload is absent.
* `docs/TECHNICAL_DECISIONS.md` — ADR-001 … ADR-022. The realignment issued
  ADR-019 (one platform), ADR-020 (Linux role), ADR-021 (release isolation) and
  ADR-022 (the PS5 package as a protected input), and superseded ADR-009 and
  ADR-010.

## Status, stated plainly

The architecture is **accepted**; almost none of it is **built**. What exists
today is a reproducible bootable diagnostic environment — booted by genuine OVMF
firmware and by SeaBIOS in an emulator — with its own storage stack, not an
operating system and not a PlayStation.

The destination workstream (`WS10`) has produced one document and one provenance
record. The genuine Sony system software has **not** been read, loaded, decrypted
or executed by this project, and five of its milestones are `blocked` on
preconditions that are not under the project's control.

The fastest way to check any claim in these documents is:

```sh
make && make verify && make check && make boot-test
make roadmap                  # graph rules
make stubtest                 # UEFI loader against stub firmware
make ovmf-test                # real firmware (0 verified, 1 failed, 3 NOT TESTED)
```

## Maintenance rules

1. Change the milestone graph by editing `roadmap.json`, then
   `python3 tools/roadmap.py sync`; commit both files. `make check` fails on a
   stale generated block or a broken rule.
2. A milestone may only become `complete` with evidence paths that exist.
3. A claim about the product must match `product.identity` and
   `product.destination`; the forbidden-claims list is data, not prose, and
   historical documents are exempt only because they are listed as such.
4. If a measurement contradicts a document, the document changes that session.
5. Decisions that are expensive to reverse get an ADR with alternatives, a
   reversal condition and — for this project — an explicit statement of what the
   decision does **not** claim.
