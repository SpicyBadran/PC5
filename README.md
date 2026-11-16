# PC5

PC5 is an engineering project whose objective is to make compatible PC
hardware operate as a PlayStation 5: boot the machine, reach genuine PS5 system
software, and run real PS5 games.

This repository contains the parts of that objective that are actually built
and tested today: a bootable PC5 image, a 32-bit boot environment that
inventories the machine, and the compatibility-chain framework that reports —
honestly and per stage — how far the project has got.

## Read this first

**Genuine PS5 system software is not running on PC5 today.** If you boot the
image you will see the PC5 boot environment, which says so on screen. PC5 does
not ship Sony firmware, does not decrypt it, and does not claim compatibility
it has not measured. The current state of every stage is in
[`docs/PS5_COMPATIBILITY_STATUS.md`](docs/PS5_COMPATIBILITY_STATUS.md).

What *does* work today, verified in this repository:

| Capability | Status | Evidence |
|---|---|---|
| Reproducible ISO build (`build/pc5.iso`) | COMPLETE AND VERIFIED | `make iso`, [`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md) |
| ISO structure (ISO9660 + El Torito BIOS and UEFI sections + boot descriptor + hybrid MBR) | COMPLETE AND VERIFIED | `make verify` → 75/75 checks |
| Boots on an emulated x86 PC (SeaBIOS, CD-ROM and, with the CD detached, its hybrid MBR) | COMPLETE AND VERIFIED | `make boot-test` → 119/119 checks |
| ATA PIO write, cache flush and verified read-back (explicit test only) | COMPLETE AND VERIFIED (emulated IDE disk) | `wtest` over the serial console; `make boot-test` compares the image and SHA-256 |
| Kernel reads its own medium (ATAPI + ISO9660, no firmware calls) | COMPLETE AND VERIFIED (emulated) | `storage_mounted volume=PC5` in the boot log |
| MBR/GPT partition discovery, read-only | COMPLETE AND VERIFIED (emulated disk + unit tests) | `partitions device=0 scheme=gpt count=2 status=ok`; a damaged table reports `status=crc` |
| FAT32 read-only: mount, list, find, read, digest | COMPLETE AND VERIFIED (emulated disk + host driver) | kernel SHA-256 equals an independent reader's for every fixture file; image byte-identical after reading |
| FAT32 create: allocate, FAT copies, FSInfo, flush, read-back | COMPLETE AND VERIFIED (emulated disk + host driver) | `put NEW.TXT hello-m1g` → independent reader extracts `hello-m1g`; originals intact, mirrors equal, no orphans |
| FAT32 overwrite, directories and 8.3 paths | COMPLETE AND VERIFIED (emulated disk + host driver + a second boot) | `write PATH text` replaces in place and frees the old chain only after the switch; `mkdir NEWDIR` then `put NEWDIR/A.TXT` reads back after a reboot; 50 host tests and a per-write failure sweep |
| Hardware inventory (CPUID, E820, PCI, ACPI, SMBIOS) | IMPLEMENTED — TESTING REQUIRED | hardware page + serial log |
| Framebuffer console with VGA text fallback | COMPLETE AND VERIFIED (emulated) | boot log, framebuffer self-test |
| PS/2 keyboard navigation | IMPLEMENTED — TESTING REQUIRED | 8042 driver, emulator input |
| Serial recovery console | COMPLETE AND VERIFIED | `pc5>` commands |
| UEFI boot path (PE32+ `\EFI\BOOT\BOOTX64.EFI`, GOP, FAT ESP) | BOOTED BY REAL OVMF FIRMWARE | `make ovmf-test` → 40 checks, 1 NOT APPLICABLE, `PC5-OVMF-BOOT-OK`: TianoCore EDK II started the ISO, the loader exited boot services and the kernel reached `PC5-BOOT-OK`. Stub-firmware run: `make stubtest` → 48/48. Physical hardware, Secure Boot and disk-style UEFI boot are NOT tested — see [`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md) §16 |
| DualSense controller support | NOT STARTED (needs a USB stack) | chain stage 3 |
| Genuine PS5 firmware execution | BLOCKED — no verified path exists | [`docs/FIRMWARE_ANALYSIS.md`](docs/FIRMWARE_ANALYSIS.md) |
| Actual PS5 game execution | NOT STARTED | [`docs/PS5_GAME_TEST_RESULTS.md`](docs/PS5_GAME_TEST_RESULTS.md) |

## Architecture: two independent tracks

The architecture is frozen in [`docs/architecture/`](docs/architecture/README.md)
and splits the project into two tracks that share infrastructure without
depending on each other:

* **Track A — PC5 Native**: an installable, PlayStation-inspired PC operating
  system on qualified mainstream hardware. Linux is the planned hardware
  foundation; the existing freestanding kernel is kept as the recovery and
  diagnostic environment. **This is not built yet** — what exists is the boot
  medium described above.
* **Track B — Prospero**: PS5 executable compatibility and research into genuine
  PS5 system software. **Nothing on this track has executed**, and it must never
  gate Track A: `make check` fails if a Track A milestone depends on Track B.

Read [`docs/architecture/PC5_ARCHITECTURE_SPEC_v1.md`](docs/architecture/PC5_ARCHITECTURE_SPEC_v1.md)
for the invariants and interfaces, or the
[audit](docs/architecture/REPOSITORY_AUDIT_2026-11-15.md) for what is measurably
true today.

## Quick start

Build it (needs `gcc`, `binutils`, `make`, `python3`; nothing else):

```sh
make            # writes build/pc5.iso + build/pc5.iso.sha256 + build/build-info.json
make verify     # independent structural verification of the image
make check      # host unit tests + kernel unit tests
make roadmap    # validate the milestone graph and the dual-track gate
make boot-test  # boots the ISO in the emulator and asserts the interface
make release-assets TAG=v0.2.0   # verify + metadata + the three release files
```

To execute the UEFI loader as well — it runs the real `BOOTX64.EFI` against a
stub firmware, not OVMF — install the one optional dependency:

```sh
pip install "unicorn==2.1.4"
make stubtest   # 48 checks; exits 2 and says so if unicorn is absent
make ovmf-test  # real OVMF boot; 0 = verified, 1 = failed, 3 = NOT TESTED
```

A release pipeline (`.github/workflows`-ready, stored in `ci/build.yml` while the
GitHub token lacks the workflows permission — see `ci/README.md`) builds, tests
and publishes `pc5.iso`, `pc5.iso.sha256` and `build-info.json` on every tag.
Until it is activated, build the image above; `docs/BUILDING.md` has both paths.

Try the image without leaving the build host:

```sh
npm install v86-system          # emulator used by the boot test
make boot-test
```

Write it to a USB stick and boot a real machine: [`docs/INSTALLATION.md`](docs/INSTALLATION.md).

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
  `storage`, `part`, `ls`, `read NAME.EXT`, `put NAME.EXT text`, `wtest`, `diag`, `log`, `screen`, `page N`, `reboot`, `halt`) so a
  machine with no display can still be diagnosed.

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture/`](docs/architecture/README.md) | dual-track architecture, current audit, hardware strategy, migration plan, milestone graph, risks, next tasks |
| [`docs/PROJECT_AUDIT.md`](docs/PROJECT_AUDIT.md) | what the repository contained, what was inspected, environment limits |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | boot chain, kernel modules, host tools |
| [`docs/TECHNICAL_DECISIONS.md`](docs/TECHNICAL_DECISIONS.md) | decision records with rejected alternatives |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | milestones M0–M9 and their real status |
| [`docs/BUILDING.md`](docs/BUILDING.md) | toolchain, reproducibility, targets |
| [`docs/INSTALLATION.md`](docs/INSTALLATION.md) | flashing, booting, troubleshooting |
| [`docs/TESTING_STRATEGY.md`](docs/TESTING_STRATEGY.md) | the sixteen evidence levels and how each is proven |
| [`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md) | results of the current build, with commands |
| [`docs/HARDWARE_COMPATIBILITY.md`](docs/HARDWARE_COMPATIBILITY.md) | reference machine, detection, matrix |
| [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) | everything that does not work yet |
| [`docs/SECURITY.md`](docs/SECURITY.md) | threat model, firmware handling, what PC5 refuses to do |
| [`docs/UPDATE_SYSTEM.md`](docs/UPDATE_SYSTEM.md) | PC5 update design vs Sony firmware updates |
| [`docs/RECOVERY.md`](docs/RECOVERY.md) | recovery console and re-flashing |
| [`docs/FIRMWARE_ANALYSIS.md`](docs/FIRMWARE_ANALYSIS.md) | what firmware research can and cannot do |
| [`docs/PS5_COMPATIBILITY_ARCHITECTURE.md`](docs/PS5_COMPATIBILITY_ARCHITECTURE.md) | the 18 compatibility subsystems |
| [`docs/PS5_COMPATIBILITY_STATUS.md`](docs/PS5_COMPATIBILITY_STATUS.md) | per-level status with evidence |
| [`docs/GAMING_COMPATIBILITY.md`](docs/GAMING_COMPATIBILITY.md) | PC games vs PS5 games, kept separate |
| [`docs/PS5_GAME_TEST_RESULTS.md`](docs/PS5_GAME_TEST_RESULTS.md) | per-title test records (empty so far, honestly) |
| [`docs/playstation/`](docs/playstation/) | firmware versions, feature parity, research log |
| [`docs/playstation/BOOT_FEASIBILITY.md`](docs/playstation/BOOT_FEASIBILITY.md) | what genuine PS5 boot would require, classified by blocker |

## Legal

PC5 is an independent interoperability and preservation project. It is not
affiliated with, endorsed by, or authorised by Sony Interactive Entertainment.
"PlayStation" and "PS5" are trademarks of Sony Interactive Entertainment Inc.

PC5 contains no Sony firmware, keys or proprietary libraries, and will not
redistribute them. See [`docs/SECURITY.md`](docs/SECURITY.md) and
[`docs/FIRMWARE_ANALYSIS.md`](docs/FIRMWARE_ANALYSIS.md).

PC5 is licensed under the GNU General Public License version 2 only, matching
the AnyPS5 sources it studies and intends to interoperate with.
