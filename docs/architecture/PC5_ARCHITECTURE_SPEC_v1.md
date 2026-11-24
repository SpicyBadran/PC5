# PC5 Architecture Specification v1

| | |
|---|---|
| Status | **SUPERSEDED on 2026-11-15 by [`PC5_ARCHITECTURE_SPEC_v2.md`](PC5_ARCHITECTURE_SPEC_v2.md).** Kept unedited as history: it is the document that made the destination optional, and `ARCHITECTURE_DISCREPANCY_REPORT.md` cites it as such. |
| Was | accepted as the M0 architecture freeze for the PC5 0.3 development line |
| Date | 2026-11-15 |
| Branch / base | `arena/77118274-pc5`, base `f5e8c78` |
| Supersedes | the implicit single-track architecture in `docs/ARCHITECTURE.md` (the boot-chain description there remains accurate) |
| Decision records | ADR-001 … ADR-013 in `docs/TECHNICAL_DECISIONS.md` |
| Machine-checked parts | `docs/architecture/roadmap.json`, `tools/roadmap.py`, `tests/test_architecture.py` |

This document freezes the architecture before implementation continues. It does
not claim any capability: the measured state of the repository is in
`REPOSITORY_AUDIT_2026-11-15.md`, and every claim below is either implemented
today or explicitly marked as planned.

---

## 1. Objectives

Permanent, from the owner's directive:

1. **PC5 Native** — a complete, installable operating system for mainstream PC
   hardware that delivers a PlayStation-like experience.
2. **Prospero** — a separate research and compatibility effort aimed at genuine
   PS5 system software on PC hardware.
3. **PS5 application compatibility** — a runtime that executes PS5 applications
   through independent implementations, not through Sony code.

### Non-goals

* Universal hardware support. PC5 publishes a **qualification matrix**, not a
  compatibility promise (`HARDWARE_STRATEGY.md`).
* Redistributing, decrypting or keying Sony firmware. PC5 has no such code and
  will not acquire any (`docs/SECURITY.md`).
* Presenting PS5 binaries, PC games under Proton, or streamed video as "PS5
  system software". The vocabulary is fixed in §9.
* Replacing Linux for its own sake. The freestanding kernel exists because it is
  the recovery and diagnostic environment, not because it is a religion.

---

## 2. Invariants

These are the rules every future change is measured against. Each has a way to
fail loudly.

| # | Invariant | Enforced by |
|---|---|---|
| I1 | **Track B never gates Track A.** No Track A milestone may depend, directly or transitively, on Track B work. | `tools/roadmap.py` track gate, run by `make check` |
| I2 | **No unmeasured claim.** A capability is reported at the level its evidence proves. | compatibility chain (`kernel/chain.c`), test evidence levels, per-milestone evidence in `roadmap.json` |
| I3 | **The boot contract is frozen while it has users.** `struct pc5_boot_info` v1 changes only with a protocol version bump and a loader/kernel change in the same commit. | `_Static_assert` offsets in `kernel/include/pc5.h`, stub-firmware tests |
| I4 | **The legacy kernel survives.** The freestanding PC5 kernel stays buildable and bootable as long as it is the only environment that can diagnose a machine with no working Linux. | `make iso`, `make boot-test` remain release-gating |
| I5 | **One status source.** Where a chain stage and a document disagree, the chain wins and the document is a bug. | audit procedure (`REPOSITORY_AUDIT_2026-11-15.md` §6) |
| I6 | **No proprietary material in the tree.** Firmware, keys and derived binaries never enter git or a release. | `.gitignore`, `docs/SECURITY.md`, tool design (`tools/firmware.py` analyses, never parses encrypted payloads) |
| I7 | **Reproducible artefacts.** Same commit, same `SOURCE_DATE_EPOCH` ⇒ identical bytes. | `make`, `.github/workflows/build.yml`, `docs/TEST_RESULTS.md` §5 |
| I8 | **Emulation is not hardware.** An emulated or stub-firmware run is labelled as such in every artefact it produces. | boot/stub test wording, `docs/TESTING_STRATEGY.md` |
| I9 | **Interfaces before implementations.** A new subsystem lands behind a named interface with a test, or it does not land. | §6 of this document |
| I10 | **Reversible decisions are cheap; irreversible ones are reviewed.** Boot chain, partitioning, update signing and anything touching user data need independent review. | governance §10 |

---

## 3. The dual-track model

```
                         PC5
        PlayStation-compatible PC platform
        ┌───────────────────────────────────────────────┐
        │  TRACK A · PC5 NATIVE                         │
        │  shell · launcher · settings · library        │  A4/A5
        │  console session  ·  desktop session          │
        │  PC5 platform services                        │  A3
        │  PC5 system image + update slots              │  A2
        │  Linux kernel + firmware  (hardware support)  │  A1
        │  boot: UEFI loader · legacy loader · recovery │  A0
        └───────────────▲───────────────────────────────┘
                        │  consumes only the public PC5
                        │  platform interfaces (§6)
        ┌───────────────┴───────────────────────────────┐
        │  TRACK B · PROSPERO                           │
        │  B1 executable compatibility                  │
        │     intake → relocation → NID/PRX shims →     │
        │     syscall surface → graphics/audio          │
        │  B2 genuine system-software research          │
        │     firmware · boot chain · SoC deps ·        │
        │     emulation feasibility · legal             │
        └───────────────────────────────────────────────┘
                        PC5 hardware abstraction
        CPU · memory · GPU · storage · USB/DualSense · audio · network
```

The arrow points one way **by design**. Track B consumes Track A; it never sits
inside Track A's build, boot or release path. Concretely:

* a Track A release must build, boot and pass its tests with the entire
  `playstation/` subtree deleted;
* Track B work lands behind a runtime or build-time boundary (a separate
  component, not a patch to the kernel) so a broken experiment cannot make the
  operating system unbootable;
* no Track B state appears in Track A's release notes as a capability, only as
  research status.

This is also why the compatibility chain in `kernel/chain.c` exists: it reports
the firmware path separately from everything Track A does, and it hard-blocks
the stages that would otherwise tempt a project to overclaim.

---

## 4. Track A — PC5 Native

### A0 · Boot, installation and recovery

| Element | Today | Target |
|---|---|---|
| Legacy BIOS loader | implemented, emulated-boot verified | retained for CSM machines and recovery |
| UEFI loader | implemented; executed against stub firmware, not OVMF | boots under OVMF and on hardware (M1c) |
| Installer | none | explicit-target installer with verification and abort (M1) |
| Recovery | serial console inside the diagnostic kernel | separate recovery entry with disk tools and rollback (M1/M3) |

The loader chain's job is narrow: get a verified payload and a boot contract into
memory, then get out of the way. It is also the last line of defence when
everything above it fails, so it must never depend on the system it boots.

### A1 · Hardware foundation

**Linux kernel plus firmware is the planned foundation for PC5 Native**
(ADR-009). The reasoning is engineering economics, not preference: drivers for
NVMe, xHCI, audio codecs, Ethernet, Wi-Fi chipsets and three GPU vendors already
exist, are maintained, and are tested on far more hardware than this project will
ever own. Re-implementing them would consume the project's entire budget before
the first game launches.

The existing freestanding kernel is **not replaced**; it becomes the recovery and
diagnostic environment (A0), which is exactly what it is good at: no
dependencies, 36 KB, boots from firmware, and can report on a machine whose
graphics stack is broken.

Execution requirement: Linux cannot be built or tested in the current build
environment (no `flex`, `bison`, `bc`, no libssl headers, no install path, no
virtual machine with KVM). The decision is therefore **accepted in principle and
blocked in execution**; ADR-009 records the exit condition and what must not be
claimed in the meantime.

### A2 · PC5 system image

* Immutable base image plus an update slot (A/B), both verifiable by hash.
* Package/application database owned by PC5, not by the host distribution.
* Release identity: version, build id, build date, channel, image hash — the
  seed already exists in `build/build-info.json`.
* Updates verified against a signed manifest before applying, with rollback on
  failed boot (`docs/UPDATE_SYSTEM.md` is the design input).

### A3 · PC5 platform services

Stable interfaces over hardware and session state. These are the services both
tracks consume:

| Service | Responsibility | Today |
|---|---|---|
| display / session | mode setting, compositing, session switch, HDR capability reporting | framebuffer console only |
| input | keyboard, mouse, DualSense, hot-plug, mapping, rumble/trigger output | PS/2 keyboard only |
| storage | block devices, partitions, filesystems, install targets, health | ISO9660 read from the boot medium; 512-byte sector read/write on an explicitly indexed ATA disk (verified on an emulated disk only); no partitions, filesystem or installer |
| audio | routing, volume, per-application streams, latency class | none |
| network | link, DHCP, DNS, connectivity state, update transport | PCI enumeration only |
| power | reset, power-off, suspend/resume, thermal state | reset and halt only |
| diagnostics | boot log, self-tests, serial console, `PC5-LOG` lines | implemented |
| telemetry | local crash and performance records, opt-in export | none |
| update / recovery | manifest verification, slots, rollback, recovery entry | design only |

### A4 · Sessions

Two sessions, one image: a **console session** (controller-first, full-screen,
the PlayStation experience) and a **desktop session** (keyboard and mouse,
windows, settings, developer tools). The session manager owns switching, resource
policy and crash containment. Neither session may be required for the other to
boot.

### A5 · Shell, launcher, settings

Controller-first home screen, game library, launch manager, system settings,
profile handling, and the compatibility/status surfaces that keep the project
honest. Achievements and social features are **PC5-local only**; nothing here
implies PlayStation Network access.

### A6 · Installation, update and recovery flow

Install → verify → boot slot A → update to slot B → verify boot twice → commit,
else roll back. Recovery is reachable from the boot menu on every installed
system. This flow is designed now and implemented after M1, because a recovery
path that cannot read a disk is theatre.

---

## 5. Track B — Prospero

### B1 · PS5 executable compatibility

The only Track B work that can produce a runnable result with lawful inputs.
Pipeline, with the state of each stage in this repository:

| Stage | Ownership | Today |
|---|---|---|
| intake: identify containers (`SLB2`, `FIH`, `CNT`, SELF, PKG, ELF) | host tool | identification implemented without decryption |
| executable loading and relocation | runtime | not implemented |
| import resolution by NID + independently implemented PRX shims | runtime | public prior art only; not built, vendored or depended on here |
| syscall / system-service surface | runtime | not implemented |
| graphics translation (shader and command level) | runtime + platform GPU service | not implemented |
| audio, input, storage, timing | platform services | not implemented |
| per-title evidence | test data | `docs/PS5_GAME_TEST_RESULTS.md`, empty |

Rules: a title is recorded per attempt; "reaches gameplay" requires a trace; a
readable package is never evidence of executability; nothing here is called
firmware execution.

### B2 · Genuine PS5 system software

Research only: firmware structure on lawfully obtained images (hashed, never
redistributed), public boot-chain documentation, hardware dependency mapping, SoC
security architecture, full-system emulation feasibility, and a legal
interoperability assessment **before** any experiment.

The architectural position is unchanged from ADR-004 and is now sharper: PS5
system software is bound to a security processor and console fuses; no public
path exists from generic x86 hardware to executing it. PC5 does not plan around
a breakthrough, does not simulate one, and will not describe emulation of an
individual binary as execution of the system software.

### B3 · Research infrastructure

Reproducible analysis scripts, hash-only records, version tracking
(`docs/playstation/`), and a rule that every claim cites either a measurement or
a public source. This is deliberately the least glamorous part of Track B and
the one that makes the rest publishable.

---

## 6. Interface contracts

An interface is where a subsystem can be replaced without rewriting its
neighbours. Each has an owner, a stability class and a test.

| Interface | Producer → consumer | Stability | Today | Test |
|---|---|---|---|---|
| `PC5-BOOT` v1 (`struct pc5_boot_info`) | loader → kernel / recovery | **frozen** while users exist; version bump to change | implemented for BIOS and UEFI paths | offset `_Static_assert`s; stub-firmware run; boot test |
| `PC5-LOG` (line protocol, `PC5-LOG key=value`) | every component → operators and tests | additive only | implemented | `tests/boot_test.py` assertions |
| `PC5-CHAIN` (13-stage status with evidence strings) | diagnostics → UI, serial, release notes | additive stages only | implemented | kernel unit tests + boot test |
| `PC5-REC` (recovery image contract) | recovery environment → installed system | planned, v0 | does not exist | — |
| `PC5-PLAT` (platform service ABI: display, input, storage, audio, network, power) | system services → sessions and Track B | planned, v0 | does not exist | — |
| `PC5-PKG` (release/package manifest) | build → updater, launcher, compatibility DB | planned, v0; seed is `build/build-info.json` | partial | `tools/verify_iso.py`, `tools/buildmeta.py` |
| `PC5-COMPAT` (runtime host boundary: memory, threads, sync, GPU submit, audio out) | Track B runtime → platform services | planned, v0 | does not exist | — |

`PC5-BOOT` freezes the loader contract; ADR-013 freezes the units on the other
side of the kernel — 512-byte disk sectors for storage, 2048-byte blocks for
optical media — because a driver that confuses them writes at the wrong offset.

Two rules follow from I9:

1. A stage may not merge code that talks to a subsystem except through that
   subsystem's interface.
2. An interface change requires the consumer's test to be updated in the same
   commit. `PC5-BOOT` is the worked example: the offsets are pinned in the
   header, checked by every loader, and executed by the stub harness.

---

## 7. Hardware model

Full strategy: `HARDWARE_STRATEGY.md`. The architectural commitments are:

* **Three reference configurations** — AMD CPU + Radeon (P0), Intel CPU +
  integrated graphics (P0), NVIDIA discrete GPU (P1). The AMD machine is the
  closest architectural analogue to the PS5 and is the primary bring-up target.
* **Qualification rows, not claims.** Every device class has a per-machine row
  with the measurement that produced it. "Works" without a row does not exist.
* **Vendor support is asymmetric and admitted**: Mesa/RADV and ANV are P0
  because they are open and testable here; the NVIDIA proprietary driver is P1
  because integration and reproducibility constraints are different.
* **Device enablement is a platform decision**: a device class whose Linux
  support depends on non-redistributable firmware (common for Wi-Fi) is
  qualified per device and documented as such, never promised generally.

---

## 8. Security and legal architecture

| Boundary | Rule |
|---|---|
| Boot | hash/signature checks at load; Secure Boot via a signed shim when one exists; no bypass, ever |
| Update | detached Ed25519 signature over a manifest, verified before apply, A/B rollback |
| Runtime | user mode isolation once Linux is the foundation; the freestanding kernel stays ring-0-by-design and is documented as such |
| Research | firmware hashed and analysed locally, never committed, never in logs as bytes |
| Services | no PSN, no console identity spoofing, no DRM circumvention |
| Distribution | public releases contain only project code and lawfully redistributable assets |

The refusal list is part of the architecture, not a disclaimer: it is why Track B
can proceed in public at all.

---

## 9. Evidence model

The repository already defines sixteen evidence levels
(`docs/TESTING_STRATEGY.md`) and a 13-stage compatibility chain. This
specification adds the milestone-level rule:

* A milestone becomes `complete` only when its acceptance criteria are met **and**
  its evidence paths exist in the tree.
* Emulated, stub-firmware, and physical results are never interchangeable; each
  record names its level.
* Where a document and a measurement disagree, the measurement wins and the
  document is corrected in the same session (I5). The first audit did this to
  nine statements.

Vocabulary, unchanged and enforced in the release notes:

| Word | Requires |
|---|---|
| detected | a value read from firmware tables, CPUID or the bus |
| implemented | code in the tree, exercised by a test |
| verified | a recorded result in `docs/TEST_RESULTS.md` or `docs/evidence/` |
| executed | the system's own code ran, on real or explicitly-labelled emulated hardware |
| playable | sustained gameplay with recorded measurements, per title |

---

## 10. Governance

| Role | Owns | Review duty |
|---|---|---|
| lead architect | this spec, ADRs, milestone graph, integration | approves any change to §2, §6 or the graph's track assignment |
| kernel engineer | A0, A1, drivers | independent review for boot and kernel changes |
| platform engineer | A2, A3, A4, A5 | session and update review |
| graphics engineer | GPU stack, display, translation | cross-track review of B1 graphics |
| compatibility engineer | B1, B2, research records | legal review trigger for any firmware experiment |
| QA engineer | evidence levels, matrices, benchmarks | blocks a release without evidence |
| release engineer | reproducible builds, CI, signing, recovery media | owns the qualification list |

Process:

1. **ADR first** for irreversible decisions (boot chain, partitioning, signing,
   update flow, hardware foundation). Records live in
   `docs/TECHNICAL_DECISIONS.md` and must name alternatives and a reversal
   condition.
2. **Branch-per-concern**, with Track B work isolated so a Track A build never
   depends on it (currently expressed by the track gate; enforced in the build
   when B1 produces code).
3. **Definition of done**: acceptance criteria met, evidence committed, tests
   green (`make check`, `make verify`, `make boot-test`, `make stubtest` where
   applicable), and no document left contradicting the result.
4. **Weekly review** reads four inputs: milestone states in `roadmap.json`,
   new `docs/evidence/` records, the risk register deltas, and the compatibility
   chain output from the newest physical boot. Anything claimed without an input
   is struck from the review.

---

## 11. Migration posture

The repository is **not** reorganised in this change. `MIGRATION_PLAN.md` records
the target tree, the staged moves, the build/test gates per move and the
rollback. Until a phase is executed, the current tree is the tree of record and
all documents point at real paths.

---

## 12. Decisions

| ADR | Decision | Status |
|---|---|---|
| ADR-001 | ISO writer in-tree | accepted, in force |
| ADR-002 | BIOS path first, UEFI second | accepted; UEFI loader now implemented, real-firmware run outstanding |
| ADR-003 | Dedicated 32-bit kernel instead of a Linux distribution (environment constraint) | accepted; **extended** by ADR-009 |
| ADR-004 | Firmware-boot path separate from execution path | accepted, in force |
| ADR-005 | Report the chain from measurements | accepted, in force |
| ADR-006 | Emulated boot tests necessary but not sufficient | accepted, in force |
| ADR-007 | No Sony firmware, keys or derived binaries | accepted, in force |
| ADR-008 | Boot image under the loader window | accepted; limit is 572 KiB, measured 36 KB kernel |
| ADR-009 | Linux as the PC5 Native foundation; freestanding kernel as recovery | accepted in principle, execution blocked by build environment |
| ADR-010 | Dual-track governance with a machine-enforced Track A/B gate | accepted, in force |
| ADR-011 | Hardware qualification matrix instead of universal support claims | accepted, in force |
| ADR-012 | Architecture claims are machine-checked artefacts | accepted, in force |
| ADR-013 | 512-byte disk sectors vs 2048-byte optical blocks | accepted, in force |

---

## 13. What would falsify this architecture

An architecture that cannot be wrong cannot be trusted. These observations would
force a revision:

| Observation | Would falsify | Consequence |
|---|---|---|
| Linux cannot be made to boot reproducibly on the reference machines even with full tooling | A1 / ADR-009 | fall back to a freestanding-first plan and re-scope M1–M4 |
| The UEFI loader cannot be made reliable under real firmware | A0 / M1c | keep BIOS/CSM as the supported path and state the limitation |
| A track B result turns out to require a Track A kernel change | I1 / ADR-010 | the change is proposed as a Track A feature with its own milestone, never as a Track B exception |
| PS5 executables cannot be obtained lawfully by their owners for testing | B1 acceptance | M5 is recorded as not reachable; Track B reduces to B2 research |
| Hardware qualification shows a P0 device class is not supportable | §7 | reclassify the device and the machine, not the claim |
| Any measured result contradicts this document | I5 | the document changes that day |

---

## 14. First-assignment deliverables

The directive asked for eight artefacts before implementation. Their status:

| # | Requested | Delivered as |
|---|---|---|
| 1 | Complete repository audit | `REPOSITORY_AUDIT_2026-11-15.md` |
| 2 | Proposed dual-track architecture | `PC5_ARCHITECTURE_SPEC_v1.md` — this document (§3–§6) |
| 3 | Linux-based vs freestanding-kernel comparison | `LINUX_VS_FREESTANDING.md` |
| 4 | Hardware compatibility strategy (AMD/Intel/NVIDIA) | `HARDWARE_STRATEGY.md` |
| 5 | Migration plan preserving loader and diagnostic kernel | `MIGRATION_PLAN.md` |
| 6 | Dependency graph for M0–M8 | `M0_M8_DEPENDENCY_GRAPH.md` + `roadmap.json` + `tools/roadmap.py` |
| 7 | Technical blockers and unproven assumptions | `RISK_REGISTER.md` |
| 8 | First three implementation tasks with acceptance criteria | `FIRST_THREE_TASKS.md` |

Implementation does not begin from this document alone: it begins from
`FIRST_THREE_TASKS.md`, and the first two of those tasks are executable in this
build environment today.
