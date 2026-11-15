# Repository audit — 2026-11-15

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
| `LICENSE` | GPL-2.0-only | matches the licence of the AnyPS5 sources the project studies |
| `.gitignore` | 472 B | excludes `build/`, `node_modules/`, `.venv/`, `firmware/`, bytecode |
| `AnyPS5-main (1).zip` | 19.7 MB | **audit input**, not a PC5 component (see §8.1) |

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
| `tools/publish_release.sh` | 118 / `tools/fetch_anyps5.sh` 45 | release publishing gate; reproducible AnyPS5 fetch. |

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
| `vendor/anyps5/README.md` | why AnyPS5 is studied rather than vendored (GPL-2.0-only, build prerequisites unavailable) |

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

`AnyPS5-main (1).zip` is 19,749,977 bytes — 99.9% of the repository by size — and
is an audit input, not a component. It is kept because removing it without the
owner's decision would destroy the provenance of the AnyPS5 assessment. The
migration plan proposes moving it to a release asset or documented external
source (§`MIGRATION_PLAN.md` phase 2).

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
