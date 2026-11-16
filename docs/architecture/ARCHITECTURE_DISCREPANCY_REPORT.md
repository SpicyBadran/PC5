# Architecture discrepancy report — 2026-11-15

**Status: investigation record. No change was made before it was written.**
Base: `main` `95a7002` (after PR #17). Branch `arena/5b7f7e7a-pc5`.

This report answers one question: *what does this repository actually say, build
and promise — and where do those three disagree with the stated mission?*

The mission, restated by the owner in the directive that produced this report:

> PC5 is a native PlayStation 5 operating-system platform for PC hardware. Its
> destination is to run the **genuine Sony PS5 system software** on compatible PC
> hardware — not to build a Linux distribution with a PlayStation theme, and not
> to treat PS5 compatibility as an optional side project.

---

## 1. What the repository contains (measured)

| Item | Value | How it was measured |
|---|---|---|
| tracked files | 107 | `git ls-tree -r --name-only HEAD` |
| host test suite | 299 tests | `make check` |
| kernel unit checks | 160 | `make unit` |
| emulated BIOS boot checks | 119 | `make boot-test` |
| UEFI loader checks (stub firmware) | 48 | `tests/uefi_stubtest.py` |
| real-firmware boot checks | 40 pass, 1 N/A, 0 fail | `make ovmf-test` (`docs/TEST_RESULTS.md` §16) |
| milestones in the graph | 18 | `make roadmap` |
| `PS5UPDATE.PUP` in the working tree | **135 bytes** | `od -c`, `sha256sum` |

### 1.1 The PUP is a Git LFS pointer, not the package

The file at the repository root is a Git LFS pointer:

```
version https://git-lfs.github.com/spec/v1
oid sha256:42ea0e5aad1361e098837d40157863c4c519f0bbc66eb03c04dbaaf339b063fb
size 1423697408
```

* Working-tree SHA-256 of the pointer itself:
  `6e8f5a459551316397f1946cce4d540a56c0b790854c2c0d94c442ca86e73b42`.
* `python3 tools/firmware.py analyze --input PS5UPDATE.PUP` reports `size 135
  bytes`, `kind unidentified`, entropy 4.924 — the analyser is working correctly
  and says, correctly, that there is no container here.
* The LFS batch API for this repository answers with a `download` action for oid
  `42ea0e5a…` at `size 1423697408`, so **the payload exists in the repository's
  LFS storage** even though no clone without `git-lfs` contains it.
* The payload is **not** present in this working tree, was **not** fetched, and
  therefore **was not analysed**. No structural claim about it in this report or
  anywhere in the repository comes from inspecting its bytes.

Consequence: the PUP is currently a *declared* input, not a usable one. The
declared size (1,423,697,408 B ≈ 1.33 GiB) is consistent with a PS5
system-software update/reinstallation package, but **which** it is (full
reinstallation versus incremental update) is `NOT VERIFIED` and cannot be
checked without the bytes. Nothing in `boot/`, `kernel/`, `tools/`, `tests/` or
`ci/` reads this file.

---

## 2. Contradictions found

Each row is a statement found in the tree, the mission it contradicts, and
whether it was *documented* or *machine-enforced*. Machine-enforced contradictions
are the serious ones: a comment can be edited, a gate has to be dismantled.

### C1 — README declares two independent tracks and demotes the mission (MACHINE-ENFORCED)

`README.md` §"Architecture: two independent tracks":

> **Track A — PC5 Native**: an installable, PlayStation-inspired PC operating
> system on qualified mainstream hardware. Linux is the planned hardware
> foundation…
> **Track B — Prospero**: PS5 executable compatibility and research into genuine
> PS5 system software… and it must never gate Track A: `make check` fails if a
> Track A milestone depends on Track B.

The mission-critical workstream is placed in a track that is, by construction,
forbidden from being a dependency of the product. The README's own opening
sentence ("make compatible PC hardware operate as a PlayStation 5 … reach genuine
PS5 system software") contradicts its architecture section eleven paragraphs
later.

### C2 — The authority document encodes the wrong destination (MACHINE-ENFORCED)

`docs/architecture/PC5_ARCHITECTURE_SPEC_v1.md` is named as the authority by
`roadmap.json`. Its §1 objectives:

1. **PC5 Native** — "a complete, installable operating system for mainstream PC
   hardware that delivers a PlayStation-like experience";
2. **Prospero** — "a **separate** research and compatibility effort";
3. **PS5 application compatibility** — a runtime.

and invariant **I1**: "**Track B never gates Track A.** No Track A milestone may
depend, directly or transitively, on Track B work."

Delivering "a PlayStation-like experience" is a *different product* from running
the PlayStation 5 platform. The spec makes the second one structurally optional.

### C3 — The roadmap gate forbids the mission from being on the path (MACHINE-ENFORCED)

`tools/roadmap.py` fails the build if any Track A milestone depends on a Track B
milestone, and `tests/test_architecture.py` tests that rule from both directions.
The rendered critical path is:

```
M0 -> M1d -> M1e -> M1f -> M1g -> M1i -> M1 -> M2 -> M3 -> M4 -> M7 -> M8
```

`M5` (PS5 executable compatibility) and `M6` (genuine Prospero research) are
absent from it by design. The destination is off the critical path **because a
test says it must be**.

### C4 — Linux is declared the foundation of the product (ADR-009)

`docs/TECHNICAL_DECISIONS.md` ADR-009: "Linux as the PC5 Native foundation", with
an execution blocker (no `flex`, `bison`, `bc`, no libssl headers, no KVM in this
environment). The decision is reasonable *as hardware enablement*; it is wrong as
a product identity, and nothing in the tree distinguishes those two readings.

### C5 — Genuine PS5 work is described as permanently out of scope by scheduling

`docs/playstation/BOOT_FEASIBILITY.md` §7: "The genuine-boot objective is **not**
on the critical path, because no prerequisite for it can be obtained today." The
blocker analysis in that document is sound and is preserved; the *scheduling
conclusion* drawn from it is what the directive rejects.

### C6 — Status documents contradict the tree (STALE, not enforced)

`docs/PS5_COMPATIBILITY_STATUS.md` still reports `make check` 280, `make
boot-test` 116/116, `make stubtest` 42/42, "UEFI … not OVMF", "no partition table
support", "no UEFI run under real firmware". The tree has 299 / 119 / 48, a real
OVMF boot, MBR+GPT discovery and FAT32 write. Nine such statements, listed in §4.

### C7 — The PUP is referenced nowhere as an engineering input

The directive makes `PS5UPDATE.PUP` the central input of the ultimate objective.
In the tree it is: an LFS pointer, referenced by no document, consumed by no
tool, and not mentioned in `roadmap.json`. `docs/FIRMWARE_ANALYSIS.md` still
says "no image supplied".

### C8 — Terminology that pre-decides the destination

| Term | Where | Why it is wrong |
|---|---|---|
| "PlayStation-inspired PC operating system" | README, spec §1, roadmap track A objective | reduces the product to an imitation |
| "PlayStation-like experience" | spec §1, §4 A4 | same |
| "two independent tracks" / "dual-track" | README, spec §3, graph doc, ADR-010, migration plan | separates the mission from the product |
| "must never gate Track A" | README, spec I1, graph doc, `tools/roadmap.py` | forbids the mission from being a dependency |
| "Track B has executed nothing" | architecture index, README | true today, but framed as a permanent property of a side project |
| "not on the critical path" | `BOOT_FEASIBILITY.md` §7 | a scheduling decision dressed as a finding |

---

## 3. What the implementation actually is (inventory)

Nothing in the directive's list of existing progress was taken on trust; each row
was confirmed against the tree and a gate.

| Subsystem | Where | State | Evidence |
|---|---|---|---|
| BIOS boot (MBR → loader → kernel, El Torito) | `boot/mbr.S`, `boot/loader.S` | IMPLEMENTED, VERIFIED (emulated) | `make boot-test` 119/119 |
| Hybrid MBR / isohybrid | `tools/mkiso.py`, `boot/mbr.S` | IMPLEMENTED, VERIFIED | MBR-as-disk boot with the CD detached (M1h) |
| UEFI PE32+ loader | `boot/uefi/{uefi_main.c,bootx64.S,efi.h}` | IMPLEMENTED, VERIFIED on real OVMF | `make ovmf-test` 40 pass / 1 N/A (`TEST_RESULTS.md` §16) |
| Kernel environment (32-bit, own GDT/IDT, serial, UI) | `kernel/` | IMPLEMENTED, VERIFIED (emulated) | `make boot-test`, `make unit` 160 |
| Hardware inventory (CPUID, E820, PCI, ACPI, SMBIOS) | `kernel/hw*.c`, `kernel/acpi*.c` | IMPLEMENTED — **NOT TESTED on physical hardware** | hardware page, serial log |
| ATA PIO write + verified read-back | `kernel/ata.c` | IMPLEMENTED, VERIFIED (emulated IDE) | `wtest`, boot-test image comparison |
| MBR/GPT partition discovery (read-only) | `kernel/part.c` | IMPLEMENTED, VERIFIED | M1e evidence |
| FAT32 read / create / overwrite / mkdir / paths | `kernel/fat32*.c` | IMPLEMENTED, VERIFIED | M1f–M1i, 50 expand tests |
| ISO9660 + ATAPI (kernel reads its own medium) | `kernel/iso9660.c`, `kernel/atapi.c` | IMPLEMENTED, VERIFIED | `storage_mounted volume=PC5` |
| Reproducible build | `Makefile`, `tools/buildmeta.py` | VERIFIED | two identical builds, pinned `BUILD_ID` |
| Host tools (ISO writer, verifier, ESP builder, PE checker, firmware analyser) | `tools/` | IMPLEMENTED, VERIFIED | 25 tool tests |
| Firmware analysis (identification only, no decryption) | `tools/firmware.py` | IMPLEMENTED, VERIFIED on synthetic containers | unit tests; ran on the PUP pointer |
| USB, NVMe/AHCI, audio, network, GPU driver, installer, updater | — | **NOT IMPLEMENTED** | stated as such in `KNOWN_LIMITATIONS.md` |
| PS5 executable loading, NID/PRX shims, syscall surface | — | **NOT IMPLEMENTED** | no code exists |
| Genuine PS5 system software execution | — | **NOT IMPLEMENTED, BLOCKED** | no evidence anywhere |

The implementation is real and is worth keeping exactly as it is. What is wrong
is the *frame* around it.

---

## 4. Stale statements corrected by this change

| # | Document | Statement | Tree |
|---|---|---|---|
| 1 | `PS5_COMPATIBILITY_STATUS.md` | `make check` 280 | 299 |
| 2 | same | `make boot-test` 116/116 | 119/119 |
| 3 | same | `make stubtest` 42/42 | 48/48 |
| 4 | same | "UEFI … EXECUTED AGAINST STUB FIRMWARE (not OVMF)" | booted by real OVMF |
| 5 | same | "no partition table support" | MBR/GPT discovery since M1e |
| 6 | same | "the next real engineering step is the installer (overwrite/rename…)" | overwrite/mkdir/paths shipped (M1i) |
| 7 | same | "The UEFI run under real firmware stays blocked on OVMF" | unblocked (§16) |
| 8 | `README.md` | `make check` described as dual-track gate | gate replaced |
| 9 | `docs/FIRMWARE_ANALYSIS.md` | "no image supplied" | an LFS pointer is present; payload still absent |

---

## 5. What is frozen, what is only documented

| Decision | Status before this change | Status after |
|---|---|---|
| ISO writer in-tree (ADR-001) | frozen | unchanged |
| Boot contract `PC5-BOOT` v1 (ADR-008, I3) | frozen | unchanged |
| 512-byte sectors vs 2048-byte blocks (ADR-013) | frozen | unchanged |
| No Sony firmware/keys in the tree (ADR-007) | frozen | unchanged, **strengthened** for the PUP (ADR-022) |
| Linux as the product foundation (ADR-009) | "accepted in principle" | **superseded** — Linux becomes an optional hardware-enablement substrate (ADR-020) |
| Two tracks with a no-dependency gate (ADR-010) | machine-enforced | **superseded** — one product, workstreams, release isolation (ADR-019, ADR-021) |

---

## 6. What moves toward the mission, and what does not

| Milestone | Moves toward genuine PS5 system software? | Why |
|---|---|---|
| M0, M1a, M1c, M1h | yes, indirectly | firmware and boot paths are prerequisites of any PC5 platform |
| M1d, M1e, M1f, M1g, M1i | yes, indirectly | the storage stack any platform model needs |
| M1 (physical boot/install), M2, M3 | yes | a qualified, self-driving PC platform is the substrate |
| M4 (experience) | **no** | it is the user experience of PC5, not the platform contract; it must not be sold as the destination |
| M5 (PS5 executable compatibility) | yes, as an *intermediate* | proves PS5 user-mode contracts can be served on PC |
| M6 (genuine research) | **this is the destination** | was a side track; becomes the workstream that defines the product |
| M7, M8 | indirectly | quality of the substrate |

---

## 7. The question the repository could not answer

*Which single architecture makes genuine PS5 system software the thing everything
else serves?* — investigated in
[`PC5_ARCHITECTURE_SPEC_v2.md`](PC5_ARCHITECTURE_SPEC_v2.md) and recorded as
ADR-019 (unified platform), ADR-020 (Linux role), ADR-021 (release isolation),
ADR-022 (PS5 system software as execution target and the PUP as a protected
input).

**Nothing in this report claims that the PS5 system software has been executed,
analysed, decrypted or obtained beyond the LFS pointer recorded in §1.1.**
