# Roadmap

**PC5 — An independent engineering project that builds PC-side boot, hardware and compatibility infrastructure toward running genuine Sony PlayStation 5 system software on compatible PC hardware.**

The destination is to run the genuine Sony PS5 system software on compatible PC
hardware. This page is the human-readable view of the milestone graph; the data
lives in [`docs/architecture/roadmap.json`](architecture/roadmap.json) and the
generated view in
[`docs/architecture/PC5_MILESTONE_GRAPH.md`](architecture/PC5_MILESTONE_GRAPH.md).
`make roadmap` fails if they disagree.

A milestone is not done because its documentation exists: `complete` requires
acceptance criteria met **and** evidence committed, and the tool checks that the
evidence path exists.

---

## 1. The six levels, and where the project stands

| Level | Statement | State today |
|---|---|---|
| 1 | PC5 boots on qualified PC hardware | **emulated only** — SeaBIOS and real OVMF firmware; no physical machine yet (`M1`) |
| 2 | PC5 controls the required hardware through its own architecture | partial: PCI/ACPI inventory, ATA PIO, PS/2, FAT32; local APIC xAPIC bring-up only (no interrupt delivery, timer or SMP); no NVMe, USB (`M2`, `M2a`, `M2b`) |
| 3 | Core platform services work | not started (`M3`, `M2c`, `M2d`, `M7`) |
| 4 | PS5 execution and system-software interfaces exist and are experimentally validated | **partial** — the platform contract, evidence registry and ten PC5-authored interfaces exist and pass 261 conformance checks (`M10`, acceptance met); no console-side behaviour is modelled yet (`M11`–`M16`) |
| 5 | Genuine system-software compatibility | **blocked** — the authenticity boundary (`M17`) and a lawful input (`M18`) |
| 6 | Platform experience | not started (`M4`, `M5`); `M20` is blocked |

Nothing above level 1 is claimed, and level 1 itself is claimed only for
emulators. The evidence classes for each level are fixed in `roadmap.json`
(`product.levels`) so that "level 5" cannot be redefined later by enthusiasm.

## 2. What is actually complete

Nine milestones are complete, all of them infrastructure or storage work that
the destination needs:

| Milestone | What it delivered | Evidence |
|---|---|---|
| `M0` | one platform, one destination: this realignment, ADR-019…022 | `ARCHITECTURE_DISCREPANCY_REPORT.md`, `PC5_ARCHITECTURE_SPEC_v2.md` |
| `M1a` | reproducible BIOS boot medium | `make iso`, `make verify`, `TEST_RESULTS.md` |
| `M1c` | UEFI boot path, booted by genuine OVMF firmware; handover window validated against the firmware memory map, retry policy and read-back exercised against stub firmware | `make ovmf-test` (§16), `make stubtest` 58/58 (§18) |
| `M1h` | hybrid MBR: the same image boots as a hard disk | MRB-disk boot with the CD detached |
| `M1d` | ATA PIO write with verified read-back | `wtest`, boot-test image comparison |
| `M1e` | MBR/GPT partition discovery | `partitions … status=ok` / `status=crc` |
| `M1f` | read-only FAT32 | kernel SHA-256 equals an independent reader |
| `M1g` | FAT32 create | independent read-back, mirrors equal |
| `M1i` | FAT32 overwrite, directories, 8.3 paths | 50 host tests, second-boot read-back |

## 3. The destination workstream (`WS10`)

The thirteen PS5 milestones, in the order they become possible. Everything before
`M17` is buildable without Sony bytes, which is why it is scheduled first.

| Milestone | What | State |
|---|---|---|
| `M9` | system-software baseline and provenance record (the package is a protected input) | **in progress** — pointer recorded, payload absent |
| `M10` | PS5 platform contract, device by device, unknowns marked | **in progress** — acceptance met: 38 claims over 4 sources, 10 interfaces, 261 conformance checks, 16 caught mutations. Held from `complete` by the evidence-level rule (level 4 with level 1–2 evidence); no Sony software executed |
| `M11` | virtual platform core: CPU and privileged execution | not started |
| `M12` | memory architecture model: unified memory, GPU-visible mappings | not started |
| `M13` | storage and I/O device model | not started |
| `M14` | graphics device model | not started |
| `M15` | audio and USB device model | not started |
| `M16` | system-services device model: timers, queues, IPC, RTC | not started |
| `M17` | security and authenticity boundary | **blocked** — no lawful trust path known; analysis only |
| `M18` | genuine system-software load | **blocked** on `M17` and a lawful input |
| `M19` | genuine system software boots on PC5 — **the destination** | **blocked** |
| `M20` | genuine runtime: services, shell, platform experience | **blocked** |
| `M21` | genuine installation and reinstallation via PC5 | **blocked** |

`M6` ("genuine Prospero research") is not deleted: it is recorded as superseded
by `M9`–`M21`, and the validator refuses a graph in which those milestones are
missing.

## 4. The destination path

```
M0 -> M1d -> M1e -> M1f -> M1g -> M1i -> M1 -> M2a -> M2 -> M2b -> M3
   -> M11 -> M12 -> M13 -> M14 -> M19
```

This is the longest chain in the graph that ends at the destination, and
`python3 tools/roadmap.py path` prints it from the data. That is
the architectural statement of ADR-019: the work that reaches genuine PS5 system
software is the product's critical path, not a side branch. The previous roadmap
had no PS5 milestone on its critical path and a test that would have failed if
one appeared there.

## 5. Workstreams, and what each one serves

| WS | Workstream | Milestones | Serves |
|---|---|---|---|
| WS0 | Governance | `M0` | all levels |
| WS1 | Hardware discovery and compatibility | `M1`, `M2` | 1–2 |
| WS2 | CPU architecture and execution environment | `M2a` | 2, 4 |
| WS3 | Firmware and boot | `M1a`, `M1c`, `M1h` | 1 |
| WS4 | Memory management | `M2b` | 2, 4 |
| WS5 | Storage and filesystem | `M1d`–`M1i` | 2 |
| WS6 | GPU and graphics | `M7` | 3, 4 |
| WS7 | Audio | `M2c` | 3, 4 |
| WS8 | USB and controllers | `M2d` | 3 |
| WS9 | System services and OS interfaces | `M3` | 3 |
| WS10 | **PS5 platform compatibility** | `M6`, `M9`–`M20` | **4–5** |
| WS11 | Game execution and runtime | `M5` | 6 |
| WS12 | Installation, recovery, updates | `M1b`, `M8`, `M21` | 1, 3 |
| WS13 | Product experience | `M4` | 6 |

## 6. Next tasks, in dependency order

1. **`M1` — physical boot.** One machine from the qualification list, BIOS and
   UEFI, serial logs committed. Everything above level 1 is emulated until this
   exists.
2. **`M10` — platform contract.** Acceptance met: `docs/architecture/PS5_PLATFORM_CONTRACT.md`
   revision r1, backed by `psm/` (registry, rule engine, linkage audit, ten
   test doubles) and its conformance and mutation suites. It gates all device
   modelling and needed no Sony bytes. It is recorded as `in_progress` rather than
   `complete` because a level-4 milestone may not be complete while only levels 1–2
   have evidence; the work itself is documentary and says nothing about the
   console's behaviour, and every `UNKNOWN` row is a refusal.
3. **`M2a`/`M2b` — CPU and memory.** Long mode, APIC, SMP, paging, protection:
   the host side of the model, and level 2 evidence.
4. **`M11` — virtual platform core.** The first milestone that runs something
   *inside* the model (a PC5 conformance payload, not Sony software).
5. **`M17` — the security boundary analysis.** Publishable whatever the answer,
   and the answer decides whether level 5 is reachable at all.

## 7. What this roadmap refuses to do

* It does not put a look-alike interface on the critical path and call it
  progress: `M4` is level 6, owned by WS13, and explicitly not the destination.
* It does not schedule level 5 as if a prerequisite existed: `M17`–`M21` are
  `blocked` with named blockers, not `in_progress` with optimistic dates.
* It does not allow an experimental component to gate a release (ADR-021), and it
  does not allow the destination workstream to be deleted for tidiness.
* It does not count documentation as completion: `complete` requires evidence
  paths that exist and that a gate can re-run.

## 8. History

The M0–M9 record this file used to carry described the two-track period. It is
preserved in [`docs/TEST_RESULTS.md`](TEST_RESULTS.md) (sections 9–16),
[`docs/architecture/REPOSITORY_AUDIT_2026-11-15.md`](architecture/REPOSITORY_AUDIT_2026-11-15.md)
and, as the superseded architecture,
[`docs/architecture/PC5_ARCHITECTURE_SPEC_v1.md`](architecture/PC5_ARCHITECTURE_SPEC_v1.md).
The `legacy` field on each milestone maps the old names to the current ones.
