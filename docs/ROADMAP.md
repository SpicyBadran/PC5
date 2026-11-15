# Roadmap

Milestones from the project directive, with what is actually true today. A
milestone is not "done" because its documentation exists.

The 0.3 development line renumbers these milestones into a two-track graph. The
canonical, machine-checked states now live in
[`docs/architecture/roadmap.json`](architecture/roadmap.json) and are rendered in
[`docs/architecture/M0_M8_DEPENDENCY_GRAPH.md`](architecture/M0_M8_DEPENDENCY_GRAPH.md);
this file keeps the historical M0–M9 record and the evidence behind it.

## M0 — Repository and technical audit · **COMPLETE**

Repository inspected (one file: the AnyPS5 archive), environment characterised,
AnyPS5 audited with measured metrics, firmware availability established (none
supplied), licences checked. Evidence: `docs/PROJECT_AUDIT.md`. Extended on
2026-11-15 by `docs/architecture/REPOSITORY_AUDIT_2026-11-15.md`, which measures
the current tree and corrects nine documented contradictions.

## M1 — Working bootable PC5 ISO · **COMPLETE (emulated)**

* `make iso` → `build/pc5.iso` (1,570,816 bytes, reproducible; the image carries a 1.44 MB UEFI ESP, which is why it grew from the 96,256-byte BIOS-only baseline recorded in `docs/TEST_RESULTS.md` §1).
* `make verify` → 53/53 structural checks.
* `make boot-test` → 53/53 checks on the v86/SeaBIOS emulated PC, including the
  kernel mounting its own volume through its ATAPI/ISO9660 driver, the ATA write
  test (M1d) and partition discovery (M1e).

## M1c — UEFI boot path · **IMPLEMENTED — EXECUTED AGAINST STUB FIRMWARE (not OVMF)**

* `boot/uefi/`: a freestanding PE32+ EFI application (`\EFI\BOOT\BOOTX64.EFI`)
  built by binutils `i386pep`, filling the identical `struct pc5_boot_info`
  (offset-pinned against `kernel/include/pc5.h`), reading the GOP framebuffer and
  the kernel through `EFI_SIMPLE_FILE_SYSTEM`, and entering the kernel in 32-bit
  protected mode.
* `tools/mkefiimg.py` (FAT12/16 ESP) and `tools/eficheck.py` (PE32+ validator)
  are new; `tools/mkiso.py` now emits the BIOS default entry plus a UEFI section
  and `tools/verify_iso.py` walks the ESP out of the finished ISO.
* The BIOS path still boots and passes its checks in the emulator (25 at the
  time; 45 today with the write test added in M1d), which is what caught the
  catalog-layout bug this milestone started with.
* **Executed, but not under real firmware:** no OVMF/UEFI firmware and no
  UEFI-capable VM exist in the build environment. `tests/uefi_stub_firmware.py`
  therefore runs the real `BOOTX64.EFI` machine code on the Unicorn CPU emulator
  with the UEFI protocols served by a stub at their specified slots, and
  `tests/uefi_stubtest.py` (`make stubtest`, 42 checks, including two negative
  runs) asserts the whole path from `_start` to the far jump into the kernel.
  That harness found and fixed two real loader defects (`find_kernel_volume`
  reading a stale status, and the `PC5K` signature read at the wrong offset).
  It is not a compatibility result: nothing about the UEFI path may be called
  verified until a recorded OVMF or hardware boot exists.

Not done here: isohybrid USB, physical hardware (M2).

## M1b — Automated, reproducible releases · **IMPLEMENTED, ACTIVATION BLOCKED**

* `make metadata` + `make release-assets` produce `pc5.iso`, `pc5.iso.sha256`
  and `build-info.json` with full provenance (`tools/buildmeta.py`).
* `ci/build.yml` builds twice (identical hashes required), verifies, runs all
  suites, stages the three assets, and — on a tag — publishes them through
  `tools/publish_release.sh`. The publish script is tested against a stub `gh`.
* Reproducibility is proven locally: two builds at the same commit and
  `SOURCE_DATE_EPOCH` produce identical images.
* **Blocked:** the file cannot be pushed to `.github/workflows/` because the
  GitHub App token used in this session has no *workflows* permission; see
  `ci/README.md`. Therefore no CI run and no published assets yet.

## M1d — ATA storage write path · **COMPLETE (emulated disk only)**

* A device-indexed 512-byte sector API (`ata_read_sectors` / `ata_write_sectors`)
  alongside the untouched 2048-byte ISO9660 path, with the unit distinction
  recorded as ADR-013.
* Writes are refused for ATAPI devices, unknown capacity, out-of-range or
  overflowing requests, invalid indices and NULL buffers — each with its own
  status, checked at runtime and in `tests/kernel_unit.c`.
* The explicit `wtest` console command writes 4 KiB of a deterministic pattern
  to a reserved 8-sector window, flushes the device cache, reads it back and
  compares it. `make boot-test` then verifies the same bytes in the emulator's
  disk image and matches the kernel's SHA-256 against the host's.
* No ordinary boot path calls the test, and nothing outside the scratch window
  is written. **No physical disk has been written.**

## M1e — MBR and GPT partition discovery · **COMPLETE (read-only, emulated + unit tests)**

* `kernel/part.c` parses the four MBR primary slots and a GPT entry array,
  verifies the header and entry CRC-32, and refuses a table that overlaps or
  extends past the device.
* The device layer takes an explicit disk index, refuses ATAPI before reading a
  sector, and reads only 512-byte sectors — it cannot write, format or mount.
* A protective MBR is treated as "the table is GPT"; a damaged GPT is reported
  as `status=crc` and is never offered as a target.
* Fixtures are generated deterministically in `tests/disk_images.py`, so the
  boot test knows the exact bytes it is asking the kernel to parse.
* Boot evidence: `partitions device=0 scheme=gpt count=2 status=ok` with both
  partitions and their LBAs; a CRC-damaged table yields `count=0 status=crc` and
  the boot continues.

## M1f — Read-only FAT32 · **COMPLETE (read-only, emulated disk + host driver)**

* `kernel/fat32.c` validates the BPB and geometry structurally (root entries and
  the 16-bit FAT size must be zero, version 0.0, FAT large enough to describe
  every cluster), then walks the FAT, enumerates the root directory, matches
  8.3 names case-insensitively and reads the file through an injected sector
  reader. There is no write path in the module at all.
* Corruption is refused, not guessed: free, bad, reserved, out-of-range and
  cyclic chains each have their own status; a FAT16 BPB is `not-fat32`; a file
  larger than the caller's buffer is `space`, never a silent truncation.
* Three independent views must agree for every fixture file: the generated
  bytes, an independent Python FAT32 reader, and the kernel module compiled as a
  host program. In the emulator the kernel's SHA-256 is compared against both.
* The disk image is byte-identical before and after a mount/list/read run.
* Deliberate limits: long file names are skipped (not parsed on a guess), sector
  size is 512 bytes only, subdirectories are listed but not descended, and
  nothing is ever written.

## M2 — Physical boot and safe installation · **IN PROGRESS**

* Physical boot: **not attempted** (no hardware access in this environment).
  Instructions and the troubleshooting matrix exist; the evidence does not.
* Installer: **not started**. Block write (M1d), partition discovery (M1e) and
  read-only FAT32 (M1f) exist; the remaining blocker is FAT32 *writing*, then a
  bootloader installer and an isohybrid medium.
* USB writing: implemented and safe (`pc5ctl usb`), but the image is not yet
  isohybrid.

## M3 — Hardware and core services · **PARTIAL**

| Service | State |
|---|---|
| boot/service management | minimal (no service model; single address space) |
| hardware detection | implemented (CPUID, E820, PCI, ACPI, SMBIOS) |
| storage management | reads ISO9660; read/write 512-byte sectors on an explicit ATA disk (emulated); no partitions or filesystem |
| networking | not started |
| audio | not started |
| graphics | framebuffer console only |
| controller input | PS/2 keyboard only |
| logging | boot log + serial mirror |
| notifications | n/a (no services) |
| updates / recovery | serial recovery console only |

## M4 — PS5 firmware execution research and prototype · **BLOCKED**

No firmware image supplied; no verified pathway exists to load Sony-signed
system software on generic x86 hardware. Work that *can* proceed and has:
container identification (`tools/firmware.py`), the manifest workflow, and the
chain reporting that will name the blocker on screen.

## M5 — Genuine PS5 system-software compatibility · **NOT STARTED**

Depends on M4.

## M6 — Controller and network operation · **NOT STARTED**

Prerequisites: xHCI/USB stack, HID parser, DualSense report parsing, network
driver + IP stack. None exist. Chain stage 3 is PARTIAL for exactly this reason.

## M7 — Actual PS5 game compatibility · **NOT STARTED**

Two prerequisites, both absent: package/executable intake (storage driver) and
an execution layer (relinking + PRX shims, per AnyPS5). Per-title testing
methodology is written; no title has been attempted.

## M8 — Performance, reliability and expanded hardware · **NOT STARTED**

No driver exists to measure. There is no performance claim in this repository.

## M9 — Release-quality PC5 · **NOT STARTED**

A release process exists in outline (`docs/UPDATE_SYSTEM.md`) and the artifact
is reproducible; that is not a release-quality product.

---

## The next twelve engineering tasks, in dependency order

1. **FAT32 writing** — reading a filesystem, creating a file, flushing and
   independently reading it back is the next step (M1g); AHCI/NVMe follow behind
   the same sector interface.
2. **Package intake from the medium** — the kernel can already read a file from
   its own volume; the missing piece is a container parser behind that call.
3. **UEFI loader** — PE32+ application filling the same `struct pc5_boot_info`,
   plus the FAT ESP the ISO writer already supports.
4. **isohybrid MBR** — so a stick boots on firmware that insists on HDD mode.
5. **GOP graphics path** — framebuffer from `EFI_GRAPHICS_OUTPUT_PROTOCOL` for
   machines with no VBE.
6. **USB stack (xHCI + HID)** — then a DualSense driver behind
   `controller_read()`; chain stage 3 becomes measurable.
7. **Network driver + IPv4/DHCP/DNS** — start with the emulator's RTL8139, then
   real chipsets.
8. **ACPI PM1 driver** — real power-off and reset.
9. **Installer** — drive identification, explicit confirmation, verification.
10. **Signed update manifest** — Ed25519, channels, rollback.
11. **Linux runtime decision point** — if a build host can install a kernel and
    `debootstrap`, add the compatibility runtime and keep the bring-up kernel as
    the recovery environment.
12. **Execution-layer prototype** — build AnyPS5 on a capable host, run one
    legally distributable PS5 ELF, record the first game-test row.
