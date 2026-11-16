# Repository audit — 2026-11-15

| | |
|---|---|
| Status | **Historical record.** Written before ADR-019. |
| Measures | the tree at `9912c78` and the M1e change under review |
| Superseded by | `ARCHITECTURE_DISCREPANCY_REPORT.md` (findings) and `PC5_ARCHITECTURE_SPEC_v2.md` (architecture authority) |

The two-track vocabulary and the conclusions in §9 below are what this audit
found and concluded at the time; they describe the architecture that ADR-019
replaced. They are preserved because an audit that quietly rewrites itself is
worth nothing. Where a statement here conflicts with `roadmap.json` or the v2
spec, those two win.

Scope: **every tracked file** in `SpicyBadran/pc5`, the build and test evidence
that can be reproduced in this environment today, and every factual claim in the
documentation that could be checked against it.

This is the second audit. The first (`docs/PROJECT_AUDIT.md`, 2026-11-12) recorded
the repository as received — one commit, one 19 MB zip, no source tree. It stays
as the historical record; this one audits the tree that exists now and corrects
what the first audit's successors got wrong.

---

## 1. Method

| Step | Command | Purpose |
|---|---|---|
| classify | `git ls-files` | every tracked path accounted for below |
| measure | `wc -l`, `git ls-files -z \| xargs -0 du -h` | size, not estimates |
| rebuild | `make clean && make` | artefact sizes and hashes from this tree |
| verify | `make verify` | structural checks by the independent parser |
| test | `make check`, `make unit` | host and kernel unit tests |
| boot | `make boot-test` | emulated boot evidence |
| firmware path | `.venv/bin/python tests/uefi_stubtest.py` | stub-firmware execution |
| cross-check | `grep` over `docs/`, `README.md` for each numeric claim | drift between documents and measurements |

No test was run against physical hardware; none is attached to this environment.

---

## 2. Inventory — all 74 tracked files

Counts at the start of the audit: 74 tracked files; 11,269 lines of source and
test code; 2,326 lines of documentation across 22 tracked documentation files;
one 19,749,977-byte archive. This change then added 12 files — nine documents in
this directory, `roadmap.json`, `tools/roadmap.py` and
`tests/test_architecture.py` — for 86 tracked files after the change. The
inventory below classifies the tree that was audited; the additions are
classified in §2.6.

### 2.1 Root

| File | Size | Classification |
|---|---|---|
| `README.md` | 7,034 B | project entry point; capability table is evidence-linked |
| `Makefile` | 8,345 B | build; targets `iso`, `uefi`, `check`, `unit`, `verify`, `boot-test`, `stubtest`, `metadata`, `release-assets`, `test` |
| `LICENSE` | GPL-2.0-only | PC5's own licence; no third-party source is vendored |
| `.gitignore` | 472 B | excludes `build/`, `node_modules/`, `.venv/`, `firmware/`, bytecode |

A 19.7 MB third-party source archive was also tracked at this date. It was an
audit input, never a PC5 component, and has since been removed from the tree
(ADR-025, §8.1).

### 2.2 `boot/` — 6 files, 2,065 lines

| File | Lines | Classification |
|---|---|---|
| `boot/loader.S` | 855 | real-mode BIOS loader: A20, E820, VBE, firmware font, ISO probe, kernel load, protected-mode handoff. Verified in emulation. |
| `boot/uefi/bootx64.S` | 158 | ms_abi entry stub, `.bss` clear, handover trampoline out of long mode. Executed against stub firmware. |
| `boot/uefi/uefi_main.c` | 669 | PE32+ EFI application: GOP, Simple File System, memory map conversion, boot-info fill, `ExitBootServices`. Executed against stub firmware; never under OVMF. |
| `boot/uefi/efi.h` | 318 | UEFI protocol structures used by the loader. |
| `boot/uefi/bootx64.ld` | 65 | PE32+ link layout. |
| `boot/uefi/README.md` | 154 | UEFI path documentation, including what the stub harness is not. |

### 2.3 `kernel/` — 20 files, 3,294 lines

| File | Lines | Classification |
|---|---|---|
| `kernel/start.S` | 218 | 32-bit entry, `.bss` clear, GDT/IDT with 32 exception gates, `PC5K` header. |
| `kernel/main.c` | 174 | boot order, boot log, chain evaluation, UI handoff. |
| `kernel/console.c` | 389 | framebuffer console, VGA text fallback, shadow grid, read-back self-test, panic. |
| `kernel/ui.c` | 627 | six pages, keyboard navigation, serial recovery console (11 commands), audit log. |
| `kernel/lib.c` | 288 | freestanding string/memory/formatter. |
| `kernel/ata.c` | 261 | legacy ATA/ATAPI PIO block driver. |
| `kernel/iso9660.c` | 108 | ECMA-119 mount and file read over `ata.c`. |
| `kernel/chain.c` | 162 | 13-stage compatibility chain with evidence strings. |
| `kernel/cpu.c` | 98 | CPUID vendor/brand/features/long-mode. |
| `kernel/mem.c` | 29 | E820 accounting. |
| `kernel/pci.c` | 131 | PCI configuration mechanism #1 enumeration. |
| `kernel/acpi.c` | 75 | RSDP discovery and RSDT signature walk. |
| `kernel/smbios.c` | 125 | `_SM_`/`_SM3_` structure walk. |
| `kernel/kbd.c` | 101 | 8042 PS/2 keyboard, polled. |
| `kernel/serial.c` | 40 | 16550 COM1. |
| `kernel/timer.c` | 57 | PIT calibration against TSC. |
| `kernel/fw.c` | 35 | firmware-manifest probe; cannot claim execution. |
| `kernel/manifest_default.c` | 4 | default (no firmware supplied) manifest. |
| `kernel/include/pc5.h` | 372 | boot contract with `_Static_assert` offsets, shared types, module interfaces. |
| `kernel/kernel.ld` | 23 | kernel link layout. |

### 2.4 `tools/` — 10 files, 2,810 lines

| File | Lines | Classification |
|---|---|---|
| `tools/mkiso.py` | 455 | ISO9660 + El Torito writer, deterministic layout, BIOS and UEFI entries. |
| `tools/verify_iso.py` | 455 | independent verifier (does not share code with the writer). |
| `tools/mkefiimg.py` | 598 | FAT12/16 ESP writer with read-back. |
| `tools/eficheck.py` | 302 | PE32+ validator (machine, subsystem, relocations, no imports). |
| `tools/roadmap.py` | 414 | **new in this audit's change**: validates and renders the milestone graph. |
| `tools/firmware.py` | 279 | container identification, entropy profile, manifest; no decryption. |
| `tools/pc5ctl.py` | 191 | operator CLI (`info`, `verify`, `boot`, `chain`, `fw`, `usb`). |
| `tools/buildmeta.py` | 253 | build provenance and release metadata. |
| `tools/release_notes.py` | 114 | release notes from recorded evidence. |
| `tools/publish_release.sh` | 118 | release publishing gate (the third-party fetch script listed at this date has since been removed, ADR-025) |

### 2.5 `tests/` — 8 files, 2,826 lines

| File | Lines | Classification |
|---|---|---|
| `tests/kernel_unit.c` | 181 | 35 host-side assertions over kernel pure logic. |
| `tests/test_tools.py` | 338 | 24 tests: ISO writer, verifier, firmware tool. |
| `tests/test_uefi.py` | 529 | 41 tests: PE32+, FAT, UEFI source contract, ISO UEFI section. |
| `tests/test_release.py` | 254 | 10 tests: metadata, publish script against a stub `gh`. |
| `tests/test_architecture.py` | 366 | **new**: 32 tests for the roadmap graph, the Track A/B gate, evidence paths and document links. |
| `tests/boot_test.py` | 213 | 25 assertions on a real emulated boot. |
| `tests/v86_runner.mjs` | 157 | v86 harness: serial capture, screen dump, input injection. |
| `tests/uefi_stub_firmware.py` | 810 | Unicorn-based stub firmware for the real `BOOTX64.EFI`. |
| `tests/uefi_stubtest.py` | 344 | 42 assertions over that run, including two negative cases. |

### 2.6 `docs/` — 19 files here plus 3 under `docs/playstation/` (2,326 lines)

`ARCHITECTURE.md`, `BUILDING.md`, `FIRMWARE_ANALYSIS.md`,
`GAMING_COMPATIBILITY.md`, `HARDWARE_COMPATIBILITY.md`, `INSTALLATION.md`,
`ISO_README.txt`, `KNOWN_LIMITATIONS.md`, `PROJECT_AUDIT.md`,
`PS5_COMPATIBILITY_ARCHITECTURE.md`, `PS5_COMPATIBILITY_STATUS.md`,
`PS5_GAME_TEST_RESULTS.md`, `RECOVERY.md`, `ROADMAP.md`, `SECURITY.md`,
`TECHNICAL_DECISIONS.md`, `TESTING_STRATEGY.md`, `TEST_RESULTS.md`,
`UPDATE_SYSTEM.md`, `playstation/FIRMWARE_VERSIONS.md`,
`playstation/RESEARCH_LOG.md`, `playstation/SYSTEM_FEATURE_PARITY.md`.

Classification: all are claim-bearing. Nine statements were contradicted by the
measurements in §3 and are corrected in §6. Nine new documents in this directory
(plus `roadmap.json`) complete the M0 deliverable set and are indexed in
[`README.md`](README.md).

### 2.7 `ci/` and `vendor/` — 3 files

| File | Classification |
|---|---|
| `ci/build.yml` | complete build-and-release pipeline, **stored outside `.github/workflows/`** because the GitHub App token has no Workflows permission |
| `ci/README.md` | why, with two recorded activation rejections |
| `vendor/` (removed) | held a short provenance note about that archive; removed with it (ADR-025) |

---

## 3. Reproduced evidence, 2026-11-15

Built and run on this tree in this session. Host: Debian 12 x86-64, gcc 12.2.0,
binutils 2.40, python 3.11.2, node 22.22.3.

| Artefact / suite | Result |
|---|---|
| `build/loader.bin` | 4,096 bytes (exactly one El Torito virtual sector set) |
| `build/kernel.bin` | 36,508 bytes (limit 585,728) |
| `build/uefi/BOOTX64.EFI` | 10,450 bytes, PE32+ validated |
| `build/efi.img` | 1,474,560 bytes, FAT12, 23/2,847 clusters used |
| `build/pc5.iso` | **1,570,816 bytes**, SHA-256 `a3a13dba4fc5c981…` |
| `make verify` | **53/53** structural checks |
| `make check` | **107** host tests, OK (24 tools + 41 UEFI + 10 release + 32 architecture) |
| `make unit` | **35** kernel assertions, 0 failures |
| `make boot-test` | **25/25** checks on the v86/SeaBIOS emulated PC |
| stub firmware run | **42/42** checks executing the real `BOOTX64.EFI` |

The UEFI stub run and the emulated boot were both reproduced here, which matters
because the previous record was produced in a different session. Neither is a
hardware result.

These are the M0 numbers, kept as the record of what was measured then. The
same day's later work raised them further; the current figures are in §7.4 below
and in `docs/TEST_RESULTS.md` §10 (M1d) and §11 (M1e).

---

## 4. Environment constraints (measured, not assumed)

| Resource | State | Consequence |
|---|---|---|
| `gcc`, `binutils`, `make`, `python3`, `node` | present | the whole tree builds |
| `qemu-system-x86_64`, OVMF, `/dev/kvm` | **absent** | no UEFI firmware boot, no hardware-like VM |
| `xorriso`, `genisoimage`, `mtools`, `mkfs.vfat` | **absent** | ISO and FAT writers are in-tree |
| `flex`, `bison`, `bc`, libssl/zlib headers | **absent** | **Linux cannot be built here** |
| `apt` | unreachable | those prerequisites cannot be installed |
| `codeload.github.com` | **reachable** (HTTP 200 for `torvalds/linux` v6.12) | Linux *source* is obtainable; its toolchain and a test vehicle are not |
| npm registry, PyPI | reachable | `v86-system` for the boot test, `unicorn` in `.venv` for the stub test |

This corrects a claim in the previous documents: Linux source is no longer
"unobtainable". What is missing is the **build prerequisite set and any way to
boot and test the result**. That distinction is what ADR-009 records, and it is
why the Linux foundation is accepted in principle but not claimed as built.

---

## 5. What actually exists, by maturity

| Subsystem | Exists | Proven at the highest level | Missing |
|---|---|---|---|
| BIOS boot chain | yes | emulated boot (25/25) | physical run |
| UEFI boot chain | yes | stub-firmware execution (42/42) | OVMF run, physical run, GOP-only machine |
| Boot contract | yes | compile-time offsets + executed handover | version 2 if payloads grow |
| Diagnostics / inventory | yes | emulated boot; self-tests | physical runs, more than signatures from ACPI |
| Storage (read) | yes | kernel ATAPI + ISO9660 read-back | AHCI, NVMe, USB, partitions, write |
| Storage (write) | **no** | — | block write, filesystem, installer |
| Display | framebuffer text console | emulated self-test | GPU acceleration, compositor, modes beyond VBE limits |
| Input | PS/2 keyboard | emulated input injection | xHCI, HID, DualSense, Bluetooth |
| Audio / network / power management | **no** | enumeration only | drivers, stacks, ACPI PM1 |
| Operating-system services | **no** | — | sessions, package management, updates |
| PlayStation shell | **no** | — | everything |
| PS5 executable compatibility | **no** | — | loader, relocations, PRX shims, translation |
| Genuine Prospero execution | **no** | — | a path that does not exist publicly |

Nothing in this table is "partially working" by accident; each row is either a
measured artefact or an absence.

---

## 6. Documentation drift found and corrected

Nine statements in the tree contradicted the measurements in §3. Where a
document and a measurement disagree, the measurement wins (invariant I5).
Corrections were made in this change; nothing was deleted to make a claim
disappear.

| # | Document | Claim | Reality | Action |
|---|---|---|---|---|
| 1 | `PS5_COMPATIBILITY_STATUS.md` | `make verify` **32/32** | 53/53 | corrected |
| 2 | `PS5_COMPATIBILITY_STATUS.md` | UEFI boot path **NOT STARTED** | implemented and executed against stub firmware (README, ROADMAP, TEST_RESULTS §7) | corrected with the exact evidence level |
| 3 | `TESTING_STRATEGY.md` | level 4 evidence = 32 checks; kernel unit = 32 assertions | 53 checks; 35 assertions | corrected |
| 4 | `ROADMAP.md` M1 | ISO is **96,256 bytes** | 1,570,816 bytes because the ISO carries a 1.44 MB ESP; TEST_RESULTS §7.5 explains the growth | corrected to the measured figure with the reason |
| 5 | `KNOWN_LIMITATIONS.md` §21 | "no boot of the UEFI path has been executed or recorded", "34 host tests" | contradicts §1 of the same file; 41 UEFI tests exist | rewritten to the accurate limitation (no OVMF, no hardware) |
| 6 | `ARCHITECTURE.md`, `TECHNICAL_DECISIONS.md` (ADR-008) | kernel ≈ 31 KB | 36,508 bytes | corrected to the measured figure |
| 7 | `PS5_COMPATIBILITY_ARCHITECTURE.md` row 10 | "loader reads the medium via INT 13h; no kernel driver" | kernel ATAPI + ISO9660 mount and read-back is verified in the boot test | corrected; PARTIAL now means *no write path* |
| 8 | `PS5_COMPATIBILITY_ARCHITECTURE.md` §5 | next three implementations: storage, UEFI loader, USB HID | storage read and the UEFI loader are done | reordered to the real next three |
| 9 | `GAMING_COMPATIBILITY.md` §5 | "Storage driver + ISO9660 reader in the kernel — NOT STARTED" | read path complete and verified | corrected; writable target still missing |

Two further items are **not** drift and were left alone:

* `docs/TEST_RESULTS.md` §1 quotes a 96,256-byte image. It is explicitly scoped
  to commit `5ab7c48`, and §7.5 records the later growth. Rewriting a historical
  measurement would be falsification.
* `docs/PROJECT_AUDIT.md` describes a repository with one zip and no source. That
  is what was received; it remains true of that date.

---

## 7. What must not break

The directive requires that currently working features are preserved. This is
the list, with the test that protects each:

| Capability | Protected by |
|---|---|
| `make` produces a reproducible ISO | `make verify`, reproducibility test in `tests/test_tools.py` |
| BIOS boot reaches the kernel and the interface | `make boot-test` (25 checks) |
| kernel reads its own medium (ATAPI + ISO9660) | boot-test assertions on `storage_mounted` and signature read-back |
| serial recovery console and six UI pages | boot-test screen dumps |
| boot contract offsets | `_Static_assert`s; stub-firmware tests |
| UEFI loader logic | `make stubtest` (42 checks) |
| honest chain reporting | `tests/kernel_unit.c` asserts firmware stages never turn OK |
| release artefacts and provenance | `tools/buildmeta.py`, `tests/test_release.py` |
| build under the 572 KiB loader window | Makefile size gate |

Every subsequent migration phase in `MIGRATION_PLAN.md` runs these suites before
it is accepted.

---

## 8. Gaps between this tree and the directive's target structure

### 8.1 Repository weight

A 19,749,977-byte third-party source archive was 99.9% of the repository by size
at this date. It was an audit input, not a component, and it was kept then
because removing it without the owner's decision would have destroyed the
provenance of the assessment. The owner has since decided: the archive, the
vendor note and the fetch script are gone (ADR-025), and
`tests/test_project_independence.py` fails the build if a third-party archive or a
reference to another project's identity returns.

### 8.2 Directories that do not exist yet

`system/`, `drivers/`, `desktop/`, `playstation/`, `releases/`, `tests/{unit,
integration,emulator,hardware}`. The directive is explicit that the target
structure is a destination, not a description. No empty directory is created
here: an empty `desktop/` would imply work that does not exist.

### 8.3 Capability gaps, in dependency order

1. Storage **write** path (block write, partitions, filesystem) — blocks the
   installer, updates, recovery and persistent logs.
2. UEFI under real firmware — blocks physical bring-up on modern machines.
3. USB stack (xHCI + HID) — blocks DualSense, USB storage, USB Ethernet.
4. Linux foundation — blocks GPU acceleration, audio, networking and the
   operating-system work that depends on them.
5. Session/shell layer — depends on 1–4.

---

## 9. Conclusions

1. **The project's honest core is sound.** The boot medium, the boot contract,
   the chain reporting and the test harnesses are real and reproduce here. The
   documentation is unusually careful, but it had drifted in nine places, which
   is exactly what a periodic audit is for.
2. **Nothing in the tree is an operating system yet.** It is a bootable
   diagnostic environment with a verified boot chain and a good reporting model.
   The dual-track architecture does not change that; it organises the work that
   changes it.
3. **The binding constraint is the environment, not the design.** Storage write
   support, a Linux build and physical hardware are all absent here. Plans that
   require them must state their evidence requirement instead of simulating a
   result.
4. **Track B has no executable path today** and the architecture must be able to
   succeed without one. The dependency graph enforces that.
5. **The next move is small and physical**: close M1c (OVMF), then build the
   storage write path that makes installation measurable.

## 7. Re-audit at `9912c78`, after M1d and M1e

Scope: the tree as merged at `9912c78` plus the M1e change under review. Method:
read every kernel, boot, tool and test file; run every gate; count what is
there. Numbers below are from this working tree on 2026-11-15 and are dated for
that reason.

### 7.1 Size and shape

| Item | Measured |
|---|---|
| tracked files | 90 |
| kernel + boot sources (`kernel/**`, `boot/**`) | 5,986 lines |
| tests (`tests/**`) | 4,157 lines |
| host tools (`tools/**`) | 3,061 lines |
| documentation (`docs/**`) | 4,616 lines |
| dependency footprint | node (v86 for tests); python3 stdlib; gcc/binutils; unicorn in a venv for the UEFI stub |

### 7.2 The seven categories, with evidence

**1. Implemented and verified** (a test in this repository records the result)

| Capability | Evidence |
|---|---|
| ISO9660 image writer, verifier, reproducible under `SOURCE_DATE_EPOCH` | `make verify` 53/53; `tests/test_tools.py`, `tests/test_release.py` |
| BIOS boot: loader, E820, VBE, kernel load, protected-mode handoff | `make boot-test` 53/53 |
| Freestanding 32-bit kernel: console, serial, PIT, CPUID, E820, PCI, ACPI, SMBIOS, PS/2 keyboard | boot log lines in `build/test/serial.log` |
| ATAPI PIO read + ISO9660 mount + file read-back | `storage_mounted volume=PC5 … signature=ok` |
| ATA PIO 512-byte sector write, flush, read-back, digest | `storage_write=ok … sha256=80fdc122…`; host-side image comparison |
| MBR and GPT partition discovery (read-only) | `partitions device=0 scheme=gpt count=2 status=ok`; `make unit` 99 checks |
| Compatibility chain reporting, 13 stages, evidence strings | `tests/kernel_unit.c`; Status page assertions |
| Serial console with 12 commands | driven by the boot test |
| Firmware container identification (no decryption) | `tests/test_tools.py` synthetic containers |
| Roadmap graph and Track A/B isolation gate | `make roadmap`; `tests/test_architecture.py` |
| Release packaging + provenance | `tests/test_release.py` |

**2. Implemented but insufficiently tested**

| Capability | What is missing |
|---|---|
| UEFI loader (`boot/uefi/`) | executed against a Unicorn stub firmware (42/42), never under OVMF or real UEFI firmware |
| CPUID / E820 / PCI / ACPI / SMBIOS inventory | emulated v86 only; no physical machine has run it |
| PS/2 keyboard and the six-page UI | emulated input only |
| ATA write path | verified on v86's IDE disk; no physical disk, no fault injection |
| Partition discovery | GPT validated in emulation and in unit tests; MBR parsed only in unit tests; extended/logical partitions not walked |
| `SOURCE_DATE_EPOCH`-pinned builds | verified by two clean builds; the CI job that would enforce it cannot run (see 7.5) |

**3. Experimental / research**

| Item | State |
|---|---|
| Static-relinking route (third-party prior art, never vendored) | source snapshot only at audit date; never built here; no PS5 binary has been relinked or executed |
| Firmware container research | third-party reports only; no image analysed |
| Genuine-PS5 boot and game execution | no path exists; `docs/playstation/BOOT_FEASIBILITY.md` records why |

**4. Stubbed or simulated**

| Item | Honest description |
|---|---|
| UEFI firmware (`tests/uefi_stub_firmware.py`) | a Unicorn harness that supplies the EFI table the loader calls; it is not firmware |
| Emulator GPU/VGA | v86's VGA and Bochs VBE, not real GPU initialisation |
| Physical hardware qualification | no machine has been tested; the matrix marks everything UNTESTED |
| Controller support | chain stage 3 is PARTIAL by construction; no USB stack |

**5. Planned but not implemented**

Writable filesystem, installer, bootloader installation, isohybrid MBR, AHCI,
NVMe, USB/xHCI, DualSense, networking, audio, power-off, paging/long mode,
Secure Boot, shell, PS5 package parser, PS5 executable loader, PS5 services.

**6. Blocked on unavailable hardware or access**

| Blocker | Consequence |
|---|---|
| no UEFI firmware (no OVMF) and no VM with UEFI | M1c cannot progress past the stub |
| no physical reference machine in this environment | every "physical" column stays UNTESTED |
| GitHub token lacks the workflows permission | CI cannot be activated |
| no PS5 console and no authorised firmware image | Track B intake remains unexecuted (the tooling exists) |

**7. Potentially infeasible under current constraints**

Running genuine PS5 system software on a PC requires the console's
security-processor trust root or full SoC emulation including it. No public
solution exists, Sony has not authorised it, and PC5 will not circumvent it.
The project therefore keeps Track B honest: research, tooling and evidence,
never a claimed boot. This is a constraint, not a scheduling problem — see
`docs/playstation/BOOT_FEASIBILITY.md`.

### 7.3 Findings that are not capabilities

1. **A 19 MB third-party archive was committed at the repository root.** It was
   third-party source, not PC5 code, never built by any target, and it inflated
   every clone. It has since been removed, together with the vendor note and the
   fetch script that referenced it (ADR-025), and a guard test now keeps it and
   its LFS pointer out of the tree.
2. **`.venv` is not reproducible in a fresh sandbox.** `make stubtest` needs
   unicorn; the check prints the install command and exits 2 rather than
   skipping silently. Documented in `docs/BUILDING.md`.
3. **The compatibility chain stage 4 stays PARTIAL** even with partition
   discovery: a readable table is not an installer and not an installed game.

### 7.4 Gate results at this commit

| Gate | Result |
|---|---|
| `make verify` | 53/53 |
| `make check` | 135 tests, OK |
| `make unit` | 99 checks, 0 failures |
| `make boot-test` | 53/53 |
| `make stubtest` | 42/42 |
| `make roadmap` | 14 milestones, tracks separated, in sync |
