# PC5 Architecture Specification v2 — one native PlayStation 5 platform

| | |
|---|---|
| Status | **Accepted. Replaces `PC5_ARCHITECTURE_SPEC_v1.md` as the architecture authority.** |
| Date | 2026-11-15 |
| Supersedes | `PC5_ARCHITECTURE_SPEC_v1.md` (two tracks, Linux-as-product); ADR-009, ADR-010 |
| Decision records | ADR-019 (this architecture), ADR-020 (Linux role), ADR-021 (release isolation), ADR-022 (PS5 package handling); ADR-001 … ADR-018 remain in force |
| Machine-checked parts | `docs/architecture/roadmap.json`, `tools/roadmap.py`, `tests/test_architecture.py`, `tests/test_product_identity.py` |
| Investigation this replaces | `ARCHITECTURE_DISCREPANCY_REPORT.md` |

This document states the destination, the architecture that serves it, and the
rules that stop the two from drifting apart again. It claims **no** capability:
what is measured is in `docs/TEST_RESULTS.md`; what is implemented is in
`REPOSITORY_AUDIT_2026-11-15.md` and `docs/PS5_COMPATIBILITY_STATUS.md`.

---

## 1. Product identity

> **PC5 — a native PlayStation 5 operating system for PC hardware.**
>
> PC5 is an independent engineering project whose destination is to run the
> genuine Sony PlayStation 5 system software on compatible PC hardware, and to
> deliver the authentic PS5 platform and gaming experience there.

Four things are kept distinct at all times, in every document:

| Layer | Meaning | Where it lives |
|---|---|---|
| **Destination** | genuine Sony PS5 system software executing on qualified PC hardware | `product.destination` in `roadmap.json`; level 6 |
| **Architecture** | how PC5 is built so that destination is reachable | this document |
| **Implementation** | what code exists today | `docs/ARCHITECTURE.md`, `docs/PS5_COMPATIBILITY_STATUS.md` |
| **Verified capability** | what a gate has measured | `docs/TEST_RESULTS.md` |

### Non-goals (unchanged in force, restated because they are part of the identity)

* Being a Linux distribution with a PlayStation theme. PC5's own platform is the
  product surface (ADR-020).
* Imitating the PS5 interface as an end in itself. The interface is PC5's; the
  *platform* is Sony's and is the target.
* Distributing, decrypting or keying Sony firmware, or circumventing
  authentication (ADR-007, ADR-022).
* Universal hardware support: PC5 publishes a qualification list, not a promise.
* Claiming any level below without its evidence, and especially claiming level 5
  on the strength of a model, an emulator or a lookalike UI.

---

## 2. Invariants

| # | Invariant | Enforced by |
|---|---|---|
| I1 | **The destination exists and is reachable.** The graph must contain a destination milestone, level 6, reachable from the roots, and it may never be `complete` without execution evidence. | `tools/roadmap.py validate()`; `tests/test_architecture.py` |
| I2 | **Release isolation.** No `experimental` milestone may be a dependency of a `release_gating` milestone of kind `infrastructure` or `native-platform`. A release must build, boot and pass every gate with all experimental code absent. | validator rule + synthetic-graph test (ADR-021) |
| I3 | **No unmeasured claim.** A capability is reported at the level its evidence proves; level 5 requires genuine execution evidence, not a model. | compatibility chain, vocabulary rules §8, per-milestone evidence |
| I4 | **The boot contract is frozen while it has users.** `struct pc5_boot_info` v1 changes only with a protocol bump and a loader/kernel change in the same commit. | `_Static_assert` offsets, stub-firmware run |
| I5 | **The legacy environment survives.** The freestanding kernel stays buildable and bootable; it is the reference substrate and the recovery environment. | `make iso`, `make boot-test` |
| I6 | **One status source.** Where a document and a measurement disagree, the measurement wins and the document is corrected the same day. | audit procedure |
| I7 | **No proprietary material in the tree.** Firmware, keys, extracted components and derived binaries never enter git or a release; the PS5 package is a hash-and-size record only. | `.gitignore`, ADR-022, `tests/test_product_identity.py` |
| I8 | **Reproducible artefacts.** Same commit, same `SOURCE_DATE_EPOCH` (and pinned `BUILD_ID` for pinned numbers) ⇒ identical bytes. | `make`, `docs/TEST_RESULTS.md` |
| I9 | **Emulation is not hardware; a model is not execution.** Every artefact names its evidence level. | gate wording, `docs/TESTING_STRATEGY.md` |
| I10 | **Product identity is data, not prose.** The identity, destination, foundation role and non-goals live in `roadmap.json` and are asserted against the documents. | `tests/test_product_identity.py` |

---

## 3. One platform, thirteen workstreams

There are no independent tracks. There is one product, cut into workstreams that
each state which part of the destination they serve. Workstream membership is a
property of a milestone, not a partition of the project.

```
                        DESTINATION · level 6
        genuine Sony PS5 system software executing on qualified PC hardware
                                     ▲
   ┌─────────────────────────────────┴──────────────────────────────────┐
   │  WS10  PS5 PLATFORM COMPATIBILITY   (the destination workstream)   │
   │  system-software baseline · platform contract · CPU/memory model   │
   │  storage model · graphics model · audio/USB model · security state │
   │  load · boot · runtime · services                                  │
   └───────▲──────────────▲──────────────▲──────────────▲───────────────┘
           │              │              │              │
   ┌───────┴──────┐ ┌─────┴─────┐ ┌──────┴──────┐ ┌─────┴──────┐
   │ WS11 GAME    │ │ WS9 SYSTEM│ │ WS6 GPU     │ │ WS5 STORAGE│
   │ EXECUTION    │ │ SERVICES  │ │ GRAPHICS    │ │ FILESYSTEM │
   └───────▲──────┘ └─────▲─────┘ └──────▲──────┘ └─────▲──────┘
           │              │              │              │
   ┌───────┴──────────────┴──────────────┴──────────────┴──────────────┐
   │  WS1 DISCOVERY · WS2 CPU/EXECUTION · WS3 FIRMWARE/BOOT · WS4 MEMORY│
   │  WS7 AUDIO · WS8 USB/CONTROLLER · WS12 INSTALL/RECOVERY/UPDATE    │
   └─────────────────────────────────▲─────────────────────────────────┘
                                     │
   ┌─────────────────────────────────┴─────────────────────────────────┐
   │  WS13 PRODUCT EXPERIENCE (shell, library, settings) — PC5's own   │
   │  WS0  GOVERNANCE (architecture, evidence, release discipline)     │
   └───────────────────────────────────────────────────────────────────┘
```

| WS | Workstream | Serves level | Initial foundation? |
|---|---|---|---|
| WS0 | Governance: architecture, evidence, releases | all | now |
| WS1 | Hardware discovery and compatibility | 1–2 | now |
| WS2 | CPU architecture and execution environment | 2, 4 | now |
| WS3 | Firmware and boot compatibility | 1 | now |
| WS4 | Memory management | 2, 4 | now |
| WS5 | Storage and filesystem | 2 | now |
| WS6 | GPU architecture and graphics | 3, 4 | later (after WS5) |
| WS7 | Audio architecture | 3, 4 | later |
| WS8 | USB, Bluetooth, controllers | 3 | later |
| WS9 | System services and operating-system interfaces | 3 | after WS2/WS4 |
| WS10 | PS5 platform compatibility (system software) | **4–5** | research now, build after WS2/WS4/WS9 |
| WS11 | Game execution and runtime compatibility | 6 | after WS9/WS10 |
| WS12 | Installation, recovery, system updates | 1, 3 | now (installer), later (PS5 install) |
| WS13 | Product experience (shell, library, settings) | 6 | last; never the destination |

Every milestone in `roadmap.json` names its workstream, its kind, the success
level it advances, and a one-line mission link (`advances`). The validator
rejects a milestone that is missing any of them.

### Why WS10 is not "another track"

WS10 is the only workstream whose milestones are on the destination path. The
other workstreams exist because WS10 needs them: a platform model needs an
execution environment (WS2), a memory manager (WS4), a storage model (WS5), and
a system-services surface (WS9). The roadmap's rendered **destination path**
shows this explicitly, and it is the longest chain in the graph — which is the
architectural statement that the destination, not a lookalike desktop, is the
critical path.

---

## 4. The two substrates, and which one is the product

| | PC5 kernel | Linux tier |
|---|---|---|
| What it is | the freestanding 32-bit PC5 environment in this repository, extended toward a 64-bit platform kernel | an optional host for PC5-authored components on hardware PC5 cannot yet drive |
| Role | **product substrate and reference implementation** | hardware-enablement substrate (ADR-020) |
| Provides | boot contract, diagnostics, storage stack, and the platform-model host | drivers for unported device classes, KVM/SVM hosting, VFIO passthrough |
| Required for the product? | **yes** | **no** — a release must build, boot and pass gates without it |
| Can execute Sony's software? | not by itself; it hosts the model | not by itself; it hosts the model |
| Exit condition | grows to cover the qualified hardware list | removed when it is no longer needed (M8 criterion) |

Both substrates host **one** component: the virtual-platform model (§6). The
model is a library plus a device-model set with a conformance suite; hosting it
twice is the price of not being locked into either.

---

## 5. Success hierarchy, with acceptance criteria

Long-term objectives. Current state is in `docs/PS5_COMPATIBILITY_STATUS.md`;
nothing below is claimed today.

| Level | Statement | Measurable acceptance criteria | Today |
|---|---|---|---|
| **1** | PC5 boots on qualified PC hardware | the ISO boots to the PC5 environment on ≥1 machine from the qualification list, with the serial log committed and the machine named; BIOS *and* UEFI configurations recorded | emulated only (SeaBIOS + real OVMF) |
| **2** | PC5 controls the required hardware through its own architecture | PC5's own drivers (not firmware, not a hosted OS) initialise: interrupts, timers, PCIe enumeration, at least one NVMe device, one input device, and report it in the boot log | partial: PCI/ACPI inventory, ATA PIO, PS/2, FAT32; no NVMe, no APIC, no USB |
| **3** | Core platform services work | process/thread management with memory protection and syscalls; display with mode setting and compositing; audio output; controller input; storage stack with a writable filesystem; each with a test that fails when the service is broken | none |
| **4** | PS5 execution and system-software interfaces exist and are experimentally validated | the platform model passes its conformance suite; a PS5 user-mode contract is served by PC5 (WS11) with a recorded trace; every modelled device has a documented contract and a test double | none |
| **5** | Genuine system-software compatibility | **genuine Sony PS5 system software executes on a PC-hosted PC5 platform model, with independently verifiable evidence**: a serial/console log from the software itself, a build/version identifier read from the software, and a third party able to reproduce it from the recorded procedure. A model booting PC5's own test payload does **not** count | blocked (authenticity boundary) |
| **6** | Platform experience | games execute and play at recorded frame rates on qualified hardware through the level-4/5 stack; the console experience (library, settings, accounts) runs on top of the platform, not instead of it | not started |

Rule: **a level is claimed only by its evidence**, and each level's evidence
class is fixed here so it cannot drift. Level 5 in particular can never be
satisfied by: a custom kernel booting, a simulated environment, an interface that
merely resembles the console's, a model running PC5-authored payloads, or an
emulator screenshot of a menu PC5 drew itself.

---

## 6. The PS5 platform compatibility layer (PSCL)

The destination workstream in engineering terms; the device contracts themselves
are written out in [`PS5_PLATFORM_CONTRACT.md`](PS5_PLATFORM_CONTRACT.md).
Nothing below exists yet.

```
genuine PS5 system software (guest)            ← level 5 input, protected (ADR-022)
────────────────────────────────────────────
PSCL  ·  platform contract + device models
  CPU/SVM · memory map · interrupts/timers · storage (NVMe-like, DMA)
  GPU command processor · audio · USB · security-state device
  firmware/boot-services contract · platform identity
────────────────────────────────────────────
host: PC5 kernel   |   Linux tier (optional)   ← one interface, one conformance suite
────────────────────────────────────────────
compatible PC hardware (qualified list)
```

Three rules:

1. **The guest is Sony's; the model is PC5's.** PC5 never substitutes its own
   kernel, shell or services for the guest's, and never describes a PC5 component
   as "the PS5 OS".
2. **Every device model has a written contract and a conformance test** before it
   has an implementation (I9). Undocumented behaviour is recorded as unknown, not
   guessed.
3. **The security-state device is specified but not faked.** PC5 may model what
   public documentation supports; it may not implement a bypass. If a lawful
   trust path cannot be established, level 5 is recorded as unreachable *for
   everyone* and the product is redefined explicitly (ADR-019 reversal condition
   4) — never quietly.

---

## 7. Interface contracts

Unchanged from v1 where it works; extended for the model.

| Interface | Producer → consumer | Stability | Today |
|---|---|---|---|
| `PC5-BOOT` v1 | loader → kernel / recovery | frozen while users exist | implemented (BIOS + UEFI, both verified) |
| `PC5-LOG` | every component → operators, tests | additive | implemented |
| `PC5-CHAIN` | diagnostics → UI, serial, release notes | additive stages | implemented |
| `PC5-REC` | recovery → installed system | planned | does not exist |
| `PC5-PLAT` | platform services → sessions, WS10/WS11 | planned, v0 | does not exist |
| `PC5-PKG` | build → updater, launcher, compatibility DB | planned (seed: `build-info.json`) | partial |
| `PC5-PSM` | PSCL device models → hosts (PC5 kernel, Linux tier) | planned, v0 | does not exist |
| `PC5-COMPAT` | PS5 runtime (WS11) → platform services | planned, v0 | does not exist |

`PC5-PSM` is the interface that makes the Linux tier optional: it is the only
thing either host provides, and both must satisfy its conformance suite.

---

## 8. Vocabulary and claim rules

| Word | Requires |
|---|---|
| detected | a value read from firmware tables, CPUID or the bus |
| implemented | code in the tree, exercised by a test |
| verified | a recorded result in `docs/TEST_RESULTS.md` or `docs/evidence/` |
| executed | the system's own code ran, on real or explicitly labelled emulated hardware |
| playable | sustained gameplay with recorded measurements, per title |

Two sentences are said plainly, in this document and in every report:
**nothing here is called firmware execution unless the software's own code ran**,
and **this architecture does not claim that genuine PS5 system software has been
executed** — no milestone below level 5 may use the words "executing" or
"running" about Sony's software.

Two sentences are forbidden in this repository and are checked as data
(`product.forbidden_claims` in `roadmap.json`):

* that PC5 is a PlayStation-*inspired* or PlayStation-*like* PC operating system
  (that is a different product),
* that Linux is the foundation or the destination of the product (ADR-020).

Historical documents (`PC5_ARCHITECTURE_SPEC_v1.md`, ADR-009, ADR-010, the
2026-11-15 audit, the migration plan) are exempt and labelled as superseded.
Being exempt is a property of a file listed in the roadmap data, not a habit.

---

## 9. Hardware model

Unchanged from v1 §7, with one addition: the qualification list is the input to
level 1 and to the platform model's hardware contract, because the model must be
able to present a *PS5-like* platform on *qualified PC hardware* — not on
anything.

* Three reference configurations: AMD CPU + Radeon (P0, closest analogue to the
  PS5 SoC), Intel CPU + integrated graphics (P0), NVIDIA discrete (P1).
* Qualification rows, not claims: every device class has a per-machine row with
  the measurement that produced it.
* Vendor asymmetry admitted: Mesa/RADV and ANV are P0; the NVIDIA proprietary
  driver is P1.

---

## 10. Security and legal architecture

| Boundary | Rule |
|---|---|
| Boot | hash/signature checks at load; Secure Boot via a signed shim when one exists; no bypass, ever |
| Update | detached signature over a manifest, verified before apply, A/B rollback |
| Runtime | user-mode isolation where it exists; the freestanding kernel is ring-0 by design and documented as such |
| PS5 package | an input, never a payload in the tree; hashes and structure only; no decryption, no keys, no circumvention (ADR-022) |
| Research | firmware hashed and analysed locally in a workspace outside the repository; never committed, never logged as bytes |
| Services | no PSN, no console identity spoofing, no DRM circumvention |
| Distribution | releases contain only project code and lawfully redistributable assets |

The refusal list is part of the architecture, not a disclaimer: it is why this
project can work in public at all.

---

## 11. Governance

Unchanged roles (lead architect, kernel, platform, graphics, compatibility, QA,
release). Two process changes:

1. **Mission check before merge.** Every milestone names `advances`; a change
   that cannot name which part of the destination it serves is either a
   WS13 experience item or it is out of scope.
2. **The destination is defended by tests.** Deleting a required milestone,
   demoting it to an optional track, or declaring the product to be a Linux
   distribution fails `make check` before it reaches a reviewer.

Definition of done is unchanged: acceptance criteria met, evidence committed,
`make check`, `make verify`, `make boot-test` green (plus `make stubtest` and
`make ovmf-test` where applicable), and no document left contradicting the
result.

---

## 12. What would falsify this architecture

| Observation | Would falsify | Consequence |
|---|---|---|
| A lawful PS5 execution path requires a different platform contract | §6 / ADR-019 | re-specify the model; keep the workstream |
| The authenticity boundary is proven uncrossable for everyone | level 5 | record level 5 as unreachable *explicitly and publicly*, redefine the product by decision, not by drift |
| PC5's kernel cannot reach the qualified hardware coverage within means | ADR-020 exit | demote the kernel to reference implementation; keep Linux optional, keep the model portable |
| Release isolation cannot protect boot stability | I2 / ADR-021 | tighten the build boundary; do not reintroduce separate objectives |
| A modelled platform cannot deliver playable performance | level 6 | record the ceiling; evaluate passthrough; do not claim playability |
| Any document contradicts a measurement | I6 | the document changes that day |

---

## 13. Deliverables of this realignment

| # | Requested | Delivered as |
|---|---|---|
| 1 | Repository-wide investigation before changes | `ARCHITECTURE_DISCREPANCY_REPORT.md` |
| 2 | Correct product identity | §1 here, `product` block in `roadmap.json`, `README.md` |
| 3 | Two-track decision | §3, ADR-019, ADR-021 |
| 4 | Linux decision with evidence | ADR-020, §4 |
| 5 | Technical feasibility and gap analysis | `PS5_SYSTEM_SOFTWARE_TARGET.md` §4, `PS5_PLATFORM_CONTRACT.md`, ADR-019 "Technical risks" |
| 6 | Reassessment of existing implementation | `ARCHITECTURE_DISCREPANCY_REPORT.md` §3 |
| 7 | Roadmap rebuilt around the mission | `roadmap.json`, `PC5_MILESTONE_GRAPH.md`, `docs/ROADMAP.md` |
| 8 | Objective success criteria | §5 (levels 1–6, measurable) |
| 9 | PS5 package baseline and rules | `PS5_SYSTEM_SOFTWARE_TARGET.md`, ADR-022, milestone `M9` |
| 10 | Automated architecture checks | `tools/roadmap.py`, `tests/test_architecture.py`, `tests/test_product_identity.py` |

Implementation still does not begin from this document alone: it begins from the
milestone graph, whose first ready item is the physical-boot and installer work
already in progress, and whose destination path is now visible in the same view.
