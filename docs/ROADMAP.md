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

* `make iso` → `build/pc5.iso` (1,601,536 bytes, reproducible; the image carries a
  1.44 MB UEFI ESP, which is why it grew from the 96,256-byte BIOS-only baseline
  recorded in `docs/TEST_RESULTS.md` §1). Since M2 the ESP also carries
  `KERNEL.BIN`, because firmware cannot read ISO9660 (§16, ADR-016).
* `make verify` → 75/75 structural checks (M1h added the system-area MBR).
* `make check` → 299 host tests (25 tools + 41 UEFI + 10 release + 32
  architecture + 14 storage + 13 partitions + 20 FAT32 read + 38 FAT32 write + 50
  FAT32 expand + 37 hybrid MBR + 19 OVMF-harness tests).
* `make boot-test` → 119/119 checks on the v86/SeaBIOS emulated PC, including the
  kernel mounting its own volume through its ATAPI/ISO9660 driver, the ATA write
  test (M1d), partition discovery (M1e), FAT32 read/create/replace/mkdir (M1f,
  M1g, M1i), the hybrid-MBR boot with the CD detached (M1h) and a second boot that
  reads back what the first one wrote.

## M1c — UEFI boot path · **DONE — BOOTED BY REAL OVMF FIRMWARE**

* `boot/uefi/`: a freestanding PE32+ EFI application (`\EFI\BOOT\BOOTX64.EFI`)
  built by binutils `i386pep`, filling the identical `struct pc5_boot_info`
  (offset-pinned against `kernel/include/pc5.h`), reading the GOP framebuffer and
  the kernel through `EFI_SIMPLE_FILE_SYSTEM`, and entering the kernel in 32-bit
  protected mode.
* `tools/mkefiimg.py` (FAT12/16 ESP), `tools/eficheck.py` (PE32+ validator) and
  `tools/verify_iso.py` (walks the ESP out of the finished ISO) were added with
  it; the ESP now also carries `KERNEL.BIN`, because firmware has no ISO9660
  driver and an El Torito UEFI boot can only read the ESP (ADR-016).
* **Booted by genuine firmware:** `make ovmf-test` boots `build/pc5.iso` under
  TianoCore EDK II OVMF (QEMU 11.0.2, q35, TCG) with a fresh writable VARS copy,
  then again with the variable store the first boot wrote. Both runs show the
  firmware boot manager starting the El Torito UEFI entry, the PC5 loader
  identifying `EDK II`, 40 memory-map descriptors handed over,
  `ExitBootServices` accepted on the first attempt, the kernel entering at
  `0x10000` with protocol 1 and `PC5-BOOT-OK` on the serial log. The harness
  prints `PC5-OVMF-BOOT-OK` itself, after all 40 checks pass, and one path
  (disk-style UEFI boot) is reported NOT APPLICABLE rather than passed.
* **Six broken machines** are attributed honestly: a missing `BOOTX64.EFI`, a
  corrupted PE32+ header and a truncated ISO are refused by firmware (it drops to
  its own shell or boots nothing); a missing kernel and a bad `PC5K` signature are
  refused by the loader with `E2` and `E4`; a one-second deadline is reported as a
  harness timeout and never as a pass.
* **Real firmware found six genuine defects** the stub could not: the kernel was
  unreachable on the El Torito path, the size of a file was learned with a seek
  OVMF answers literally, `ExitBootServices` was refused because the map key had
  been invalidated by console output, the console protocol was called after it
  had been closed, the serial log doubled every character, and the kernel's
  display evidence line had a format/argument mismatch. All six are fixed, each
  with a regression test (`tests/uefi_stubtest.py` 48 checks,
  `tests/boot_test.py` 119 checks); details in `docs/TEST_RESULTS.md` §16.4.
* **Still not done:** physical hardware (one UEFI machine, Secure Boot off), the
  disk-style UEFI path (this ISO has no ESP partition), and an ACPI RSDP handover
  — the kernel reports `acpi=absent` on this path, honestly. Secure Boot was
  never enabled, and only EDK II firmware was exercised.

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
  reader. As of M1f the module had no write path at all; M1g adds exactly one
  (create a new root file) behind an injected sector writer.
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

## M1g — Safe FAT32 write · **COMPLETE (create only; emulated disk + host driver)**

* `fat32_create_file()` creates one new 8.3 file in the root directory of an
  explicitly mounted volume. Nothing else writes: no overwrite, no delete, no
  resize, no rename, no subdirectory write, no directory expansion.
* Allocation scans the FAT for free entries and holds the whole chain in memory
  before touching the disk, so a full volume is refused with **no writes at
  all**. Out-of-range, bad and reserved entries are never allocated.
* Every FAT copy is written and then re-read entry by entry; a divergent mirror
  is reported (`corrupt`), never assumed. FSInfo is validated (three signatures,
  counters inside the volume) and only rewritten when trustworthy; its free count
  is recomputed by scanning the FAT instead of being believed.
* Write order is content → FAT copies → FSInfo → **flush** → directory entry →
  **flush** → read-back. A directory entry therefore never names data that was
  not written first. The worst outcome of an interruption is an allocated but
  unreferenced chain (a leak) — never a file that reads back as rubbish.
* `ata_flush()` issues FLUSH CACHE (0xE7) on an explicit device and fails on ERR
  or timeout; ATAPI is refused. A missing flush callback is reported
  (`flush=0`), not treated as durability.
* An independent FAT32 implementation reads the resulting image: it extracts the
  new file, compares SHA-256 with the source payload, walks the chain, checks
  both FAT copies, the FSInfo counters and every pre-existing file. Changed
  sectors are confined to the data, the FATs, the FSInfo blocks and the root
  directory.
* Fault injection (write failure at each stage, failure between FAT copies,
  corrupted data, failed flush) is classified by that independent reader:
  originals always intact, and the resulting state is reported as intact,
  leaked-chain or inconsistent mirror.
* Cost, said plainly: allocation walks the FAT entry by entry, so creating a
  file on a nearly full volume of *N* clusters costs O(*N*) sector reads; the
  free-space count itself is one read per 128 clusters.
* Not claimed: crash consistency against real power loss, or any physical disk.
  See `docs/KNOWN_LIMITATIONS.md` §5 and `docs/TEST_RESULTS.md` §13.

## M1h — Hybrid MBR · **COMPLETE (emulated disk; no physical boot)**

* The ISO9660 **system area** — the 32 KiB ECMA-119 reserves and the filesystem
  never reads — now carries a hard-disk MBR in its first sector: `55 AA`, a fixed
  disk signature, and **one** partition entry with the boot flag set, type
  `0x17`, start LBA 0 and length equal to the whole image in 512-byte sectors.
  The table is computed from the layout that was just built, never copied from a
  template, and the CHS fields are derived from the same LBAs.
* `boot/mbr.S` is exactly 440 bytes (the builder owns the rest of the sector). It
  copies a relay to `0000:0500` out of the loader's way, reads the boot image
  with INT 13h extended read into `0000:7C00`, retries three times with a drive
  reset, prints one line and halts on failure, and hands off with `DL` preserved.
  It loads the **same** 4096-byte stage-1 loader that El Torito names; the LBA
  and length are patched in from the El Torito entry, so the two paths cannot
  disagree.
* Nothing else moved: the El Torito entries, every file extent (the kernel still
  starts at LBA 747), the loader, the FAT ESP and the kernel are what they were,
  and the image is still 778 blocks / 1,593,344 bytes — the MBR lives in space
  the filesystem already reserved.
* The loader needed no change — `read_pvd` already probes 2048- then 512-byte
  units and `load_kernel` already converts LBAs — so the hard-disk path reports
  `disk unit bytes 0200` where the CD path reports `0800`, and both reach the
  same kernel build.
* Evidence: the ISO booted in the emulator with **no CD attached** and the image
  attached as a disk (`boot_order=2`), reaching `PC5-BOOT-OK`; the same image
  with sector 0 blanked produced zero output, so the result is the MBR and not a
  firmware fallback.
* `tools/verify_iso.py` verifies sector 0 the way firmware reads it and rejects a
  table that disagrees with the image; `tests/test_hybrid.py` corrupts each field
  and requires the rejection.
* Not claimed: a boot on physical hardware. SeaBIOS accepting the medium says
  nothing about a particular machine's USB-HDD mode. See
  `docs/KNOWN_LIMITATIONS.md` item 2 and ADR-014.

## M1i — FAT32 write expansion: overwrite, directories, paths · **COMPLETE (emulated disk + host driver)**

* **Overwrite.** `fat32_overwrite_file()` replaces a regular file's content in
  place: same name, same attributes, same directory slot, new first cluster and
  size. The replacement is built in unreferenced clusters, linked in every FAT
  copy and re-read, flushed, and only then does **one** 32-byte directory-entry
  write switch the name over. The replaced chain is freed afterwards, cluster by
  cluster, re-verified and re-counted. A shorter, longer, same-length, empty and
  fragmented replacement are all covered.
* **Directories.** `fat32_create_dir()` writes a zeroed cluster with `.` and `..`
  (parent, or cluster 0 when the parent is the root), links and verifies its
  chain, then writes the parent entry and reads the whole thing back through the
  path. A parent with no free slot grows by one cluster — new cluster zeroed and
  terminated *before* the old tail links to it — instead of refusing.
* **Paths.** Every entry point takes `COMPONENT { "/" COMPONENT }`: at most 8
  components, 63 characters, each an 8.3 name matched case-insensitively. Missing
  parents, a file used as a directory, trailing or doubled separators, `.`/`..`
  and over-long paths are refused with a distinct status and never truncated.
* **Integrity before speed.** Both versions must fit on the volume at once, or the
  overwrite is refused with zero writes. The root's own cluster, bad and reserved
  clusters are never allocated; a divergent mirror, an invalid chain or a
  nonsensical entry is refused as `corrupt` before anything destructive happens.
  The growth cluster is reserved in the *same* allocation as the content, because
  a second scan of the FAT would hand out the same still-unlinked cluster.
* **Failure model, measured.** Every write of every operation was failed in turn:
  before the switch the old content is intact, after it the new content is
  complete, and the only possible damage is a leaked chain, which the report
  counts (`freed`/`old_clusters`) and the independent reader confirms. No
  unrelated file was damaged, no cluster was ever claimed twice, and the
  accounting `freed + leaked + freed-in-one-copy-only == old chain` closed on all
  26 failures of the overwrite sweep. **Not claimed: power-loss atomicity.**
* **Console.** `put PATH text` (create, refuses existing), `write PATH text`
  (replace), `mkdir PATH`, `ls [PATH]`, `read PATH`. Nothing on the boot path
  calls any of them.
* **Evidence.** 50 new host tests (`tests/test_fat32_expand.py`), +29 boot checks
  including a second boot of the image the first boot wrote, 18 milestones in
  sync, two identical pinned builds. See §15 of `docs/TEST_RESULTS.md` and
  ADR-015.
* Not claimed: any physical disk, delete/rename/truncate, long file names, or
  crash consistency. See `docs/KNOWN_LIMITATIONS.md` item 5.

## M2 — Physical boot and safe installation · **IN PROGRESS**

* Physical boot: **not attempted** (no hardware access in this environment).
  Instructions and the troubleshooting matrix exist; the evidence does not.
* Installer: **not started**. Block write (M1d), partition discovery (M1e),
  read-only FAT32 (M1f), FAT32 create (M1g), the hybrid medium (M1h) and
  overwrite/directories/paths (M1i) exist. A writable directory tree is now
  available; the remaining blockers are a bootloader installer, delete/rename for
  a real update path, and AHCI/NVMe so the installer can target the machines it
  claims.
* USB writing: implemented and safe (`pc5ctl usb`), and since M1h the image is
  hybrid — it boots through El Torito *and* through its MBR when firmware
  presents the stick as a hard disk. The emulated proof is §14 of
  `docs/TEST_RESULTS.md`; the physical stick is still M2's business.

## M3 — Hardware and core services · **PARTIAL**

| Service | State |
|---|---|
| boot/service management | minimal (no service model; single address space) |
| hardware detection | implemented (CPUID, E820, PCI, ACPI, SMBIOS) |
| storage management | reads ISO9660; read/write 512-byte sectors on an explicit ATA disk; MBR/GPT discovery; FAT32 read and create (emulated) |
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
4. ~~**isohybrid MBR** — so a stick boots on firmware that insists on HDD mode.~~
   Delivered as M1h (ADR-014).
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
