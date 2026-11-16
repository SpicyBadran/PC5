# PC5

**A native PlayStation 5 operating system for PC hardware.**

PC5 is an independent engineering project whose destination is to run the
**genuine Sony PlayStation 5 system software** on compatible PC hardware, and to
deliver the authentic PS5 platform and gaming experience there. It is not a Linux
distribution with a PlayStation theme, not an imitation of the PS5 interface, and
not a project in which PS5 compatibility is an optional side quest.

That destination is defined as data — `product.identity` and
`product.destination` in [`docs/architecture/roadmap.json`](docs/architecture/roadmap.json)
— and the tests fail if the repository drifts away from it.

## Read this first

**The genuine PS5 system software does not run on PC5 today.** It has never been
loaded, and it has not been read by this project either: the package in the
repository root is a Git LFS pointer, not the 1.33 GiB payload, and no analysis of
its bytes exists. What exists is the substrate the destination needs, plus an
honest, per-stage account of how far it has got.

Every milestone that would take the project closer to that destination while it is
still blocked is in the graph; five of them are marked **blocked** with the
blocker named, because that is what they are.

What *does* work today, verified in this repository:

| Capability | Status | Evidence |
|---|---|---|
| Reproducible ISO build (`build/pc5.iso`) | COMPLETE AND VERIFIED | `make iso`, [`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md) |
| ISO structure (ISO9660 + El Torito BIOS and UEFI sections + boot descriptor + hybrid MBR) | COMPLETE AND VERIFIED | `make verify` → VALID |
| Boots on an emulated x86 PC (SeaBIOS, CD-ROM and, with the CD detached, its hybrid MBR) | COMPLETE AND VERIFIED | `make boot-test` → 119/119 checks |
| UEFI boot path (PE32+ `\EFI\BOOT\BOOTX64.EFI`, GOP, FAT ESP) | BOOTED BY REAL OVMF FIRMWARE (prior run); chain hardened and executed against stub firmware, 58/58 | `make ovmf-test` → 40 checks, 1 NOT APPLICABLE, `PC5-OVMF-BOOT-OK` on the machine that had OVMF (§16); memory-map validation, retry policy, watchdog and read-back handoff executed under the stub firmware (§18). **Not re-run under OVMF here**; physical hardware, Secure Boot (unsupported — no signing key) and disk-style UEFI boot are NOT tested |
| ATA PIO write, cache flush and verified read-back (explicit test only) | COMPLETE AND VERIFIED (emulated IDE disk) | `wtest` over the serial console; `make boot-test` compares the image and SHA-256 |
| Kernel reads its own medium (ATAPI + ISO9660, no firmware calls) | COMPLETE AND VERIFIED (emulated) | `storage_mounted volume=PC5` in the boot log |
| MBR/GPT partition discovery, read-only | COMPLETE AND VERIFIED (emulated disk + unit tests) | `partitions device=0 scheme=gpt count=2 status=ok`; a damaged table reports `status=crc` |
| FAT32 read-only: mount, list, find, read, digest | COMPLETE AND VERIFIED (emulated disk + host driver) | kernel SHA-256 equals an independent reader's for every fixture file; image byte-identical after reading |
| FAT32 create: allocate, FAT copies, FSInfo, flush, read-back | COMPLETE AND VERIFIED (emulated disk + host driver) | `put NEW.TXT hello-m1g` → independent reader extracts `hello-m1g`; originals intact, mirrors equal, no orphans |
| FAT32 overwrite, directories and 8.3 paths | COMPLETE AND VERIFIED (emulated disk + host driver + a second boot) | `write PATH text` replaces in place; `mkdir NEWDIR` then `put NEWDIR/A.TXT` reads back after a reboot; 50 host tests |
| Hardware inventory (CPUID, E820, PCI, ACPI, SMBIOS) | IMPLEMENTED — TESTING REQUIRED | hardware page + serial log; no physical machine yet |
| Framebuffer console with VGA text fallback | COMPLETE AND VERIFIED (emulated) | boot log, framebuffer self-test |
| PS/2 keyboard navigation | IMPLEMENTED — TESTING REQUIRED | 8042 driver, emulator input |
| Serial recovery console | COMPLETE AND VERIFIED | `pc5>` commands |
| Hardware drivers for the qualified list (APIC, NVMe, USB, audio) | NOT STARTED | `M2`, `M2a`, `M2c`, `M2d` |
| Platform services (processes, protection, syscalls) | NOT STARTED | `M3` |
| PS5 platform model (the destination workstream) | ONE DOCUMENT AND ONE PROVENANCE RECORD | `M9`, `M10`; `M11`–`M21` not started or blocked |
| **Genuine PS5 system software executing** | **BLOCKED — no lawful path exists** | [`docs/PS5_SYSTEM_SOFTWARE_TARGET.md`](docs/PS5_SYSTEM_SOFTWARE_TARGET.md), `M17` |

## Architecture: one platform, thirteen workstreams

There is no second product and no side track. PC5 is one platform, cut into
fourteen workstreams (`WS0`–`WS13`), each stating which part of the destination it
serves. The destination workstream is **WS10 — PS5 platform compatibility**: a
model of the PS5 platform (CPU and privilege model, unified memory, storage,
graphics, audio, USB, service devices, security state) on top of which the genuine
system software can execute.

```
DESTINATION: genuine Sony PS5 system software on qualified PC hardware  (M19, level 5)
        ▲
    WS10 PS5 platform compatibility  ·  M9 M10 M11 M12 M13 M14 M15 M16 M17 M18 M19 M20 M21
        ▲ needs WS2 CPU · WS4 memory · WS5 storage · WS6 GPU · WS7 audio · WS8 USB · WS9 services
        ▲ on WS1 hardware · WS3 boot · WS12 install/recovery · WS13 experience (surface, not destination)
```

* **Linux** is an *optional hardware-enablement substrate*, never the product
  (ADR-020): it may host PC5's device models on hardware the PC5 kernel cannot
  drive yet, and the build must pass every gate without it.
* **The destination is on the critical path.** `python3 tools/roadmap.py path`
  prints the longest chain in the graph, and it ends at `M19`: the work that
  reaches genuine PS5 system software is the product's critical path.
* **Experiments cannot break releases.** Release isolation (ADR-021) replaces the
  old "the PS5 work may never gate the product" rule: an experimental milestone
  may not be a dependency of a release-gating one, and the validator enforces it.
* **Level 5 cannot be claimed by a lookalike.** `product.destination.must_not_be_satisfied_by`
  lists the things that do not count: a PC5 kernel booting, a model running PC5
  payloads, an imitation of the interface, an emulator menu PC5 drew.

Read [`docs/architecture/PC5_ARCHITECTURE_SPEC_v2.md`](docs/architecture/PC5_ARCHITECTURE_SPEC_v2.md)
for the architecture, [`docs/architecture/PC5_MILESTONE_GRAPH.md`](docs/architecture/PC5_MILESTONE_GRAPH.md)
for the graph, or [`docs/architecture/ARCHITECTURE_DISCREPANCY_REPORT.md`](docs/architecture/ARCHITECTURE_DISCREPANCY_REPORT.md)
for the measured audit that replaced the previous two-track model.

## The PS5 system software as an engineering input

The repository root contains `PS5UPDATE.PUP`. What that file *is* today:

| Property | Value |
|---|---|
| working-tree content | a Git LFS pointer, 135 bytes (`sha256 6e8f5a45…e73b42`) |
| declared object | `oid 42ea0e5a…b063fb`, size **1,423,697,408 B** |
| payload in this checkout | **no** — `tools/firmware.py` reports `size 135 bytes, kind unidentified` |
| analysed, decrypted or executed | **no**, in any sense |

The package is a **protected input** (ADR-022): PC5 records hashes and structure,
never redistributes or modifies it, and implements no decryption, key handling or
authentication circumvention. Whether it is the full reinstallation image, which
version it carries and what it contains are recorded as `NOT VERIFIED` in
[`docs/PS5_SYSTEM_SOFTWARE_TARGET.md`](docs/PS5_SYSTEM_SOFTWARE_TARGET.md).

## Quick start

Build it (needs `gcc`, `binutils`, `make`, `python3`; nothing else):

```sh
make            # build/pc5.iso + build/pc5.iso.sha256 + build/build-info.json
make verify     # independent structural verification of the image
make check      # host unit tests + kernel unit tests
make roadmap    # validate the milestone graph, release isolation and the destination path
make boot-test  # boots the ISO in the emulator and asserts the interface
make release-assets TAG=v0.2.0   # verify + metadata + the three release files
```

To execute the UEFI loader as well — it runs the real `BOOTX64.EFI` against a stub
firmware, not OVMF — install the one optional dependency:

```sh
pip install "unicorn==2.1.4"
make stubtest   # 58 checks; exits 2 and says so if unicorn is absent
make ovmf-test  # real OVMF boot; 0 = verified, 1 = failed, 3 = NOT TESTED
```

A release pipeline (`.github/workflows`-ready, stored in `ci/build.yml` while the
GitHub token lacks the workflows permission — see `ci/README.md`) builds, tests and
publishes `pc5.iso`, `pc5.iso.sha256` and `build-info.json` on every tag. Until it
is activated, build the image above; `docs/BUILDING.md` has both paths.

Write it to a USB stick and boot a real machine: [`docs/INSTALLATION.md`](docs/INSTALLATION.md).
No physical machine has done that yet — it is milestone `M1`, the project's most
important unblocked task.

## What the boot environment gives you

* a hardware inventory taken from firmware tables and CPUID, not from a database,
* a storage stack of its own: ATAPI PIO driver + ISO9660 reader, used at boot to
  mount the CD and read the kernel file back as a self-test,
* a 13-stage compatibility chain with the evidence behind every stage,
* self-tests for the graphics mode, font, serial port, PCI, ACPI, SMBIOS,
  ISO9660 descriptor and boot-descriptor integrity,
* an ATA write path that is never used automatically: `wtest` writes a 4 KiB
  pattern to a reserved window, flushes, reads it back and reports a SHA-256,
* a serial console on COM1 at 115200 8N1 (`status`, `chain`, `hw`, `fw`,
  `storage`, `part`, `ls`, `read NAME.EXT`, `put NAME.EXT text`, `wtest`, `diag`, `log`, `screen`, `page N`, `reboot`, `halt`).

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture/`](docs/architecture/README.md) | the architecture authority, the discrepancy report, the platform contract, the milestone graph, audit, hardware strategy, migration plan, risks, next tasks |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | the six success levels, the destination workstream, the destination path, next tasks |
| [`docs/PS5_SYSTEM_SOFTWARE_TARGET.md`](docs/PS5_SYSTEM_SOFTWARE_TARGET.md) | what the PS5 package is, what is verified, and what running it would require |
| [`docs/PS5_COMPATIBILITY_STATUS.md`](docs/PS5_COMPATIBILITY_STATUS.md) | per-level status of the PS5 workstream, with evidence |
| [`docs/PS5_COMPATIBILITY_ARCHITECTURE.md`](docs/PS5_COMPATIBILITY_ARCHITECTURE.md) | the subsystems the destination needs |
| [`docs/playstation/BOOT_FEASIBILITY.md`](docs/playstation/BOOT_FEASIBILITY.md) | what genuine PS5 boot requires, classified by blocker |
| [`docs/TECHNICAL_DECISIONS.md`](docs/TECHNICAL_DECISIONS.md) | ADR-001 … ADR-022, including ADR-019/020/021/022 from this realignment |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | boot chain, kernel modules, host tools as implemented |
| [`docs/BUILDING.md`](docs/BUILDING.md) | toolchain, reproducibility, targets |
| [`docs/INSTALLATION.md`](docs/INSTALLATION.md) | flashing, booting, troubleshooting |
| [`docs/TESTING_STRATEGY.md`](docs/TESTING_STRATEGY.md) | the evidence levels and how each is proven |
| [`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md) | results of the current build, with commands |
| [`docs/HARDWARE_COMPATIBILITY.md`](docs/HARDWARE_COMPATIBILITY.md) | reference machine, detection, matrix |
| [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) | everything that does not work yet |
| [`docs/SECURITY.md`](docs/SECURITY.md) | threat model, firmware handling, what PC5 refuses to do |
| [`docs/UPDATE_SYSTEM.md`](docs/UPDATE_SYSTEM.md) | PC5 update design vs Sony firmware updates |
| [`docs/RECOVERY.md`](docs/RECOVERY.md) | recovery console and re-flashing |
| [`docs/FIRMWARE_ANALYSIS.md`](docs/FIRMWARE_ANALYSIS.md) | what firmware research can and cannot do |
| [`docs/GAMING_COMPATIBILITY.md`](docs/GAMING_COMPATIBILITY.md) | PC games vs PS5 games, kept separate |
| [`docs/PS5_GAME_TEST_RESULTS.md`](docs/PS5_GAME_TEST_RESULTS.md) | per-title test records (empty so far, honestly) |
| [`docs/PROJECT_AUDIT.md`](docs/PROJECT_AUDIT.md) | what the repository contained, what was inspected, environment limits |

## Legal

PC5 is an independent interoperability and preservation project. It is not
affiliated with, endorsed by, or authorised by Sony Interactive Entertainment.
"PlayStation" and "PS5" are trademarks of Sony Interactive Entertainment Inc.

PC5 contains no Sony firmware, keys or proprietary libraries, implements no
decryption or authentication circumvention, and will not redistribute them. See
[`docs/SECURITY.md`](docs/SECURITY.md), [`docs/FIRMWARE_ANALYSIS.md`](docs/FIRMWARE_ANALYSIS.md)
and ADR-022.

PC5 is licensed under the GNU General Public License version 2 only, matching the
AnyPS5 sources it studies and intends to interoperate with.
