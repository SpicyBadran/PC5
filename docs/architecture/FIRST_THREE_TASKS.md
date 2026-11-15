# First three implementation tasks

The directive's first assignment ends with "the first three implementation tasks,
with exact acceptance criteria". These are those three. They are chosen so that
each one converts an *absence* into a *measurement*, and so that the first one
starts on the critical path.

## Why not the PS5 firmware loader

The most conspicuous objective is the one to start last. Four reasons:

1. **No pathway exists.** The PS5 boot chain roots trust in a security processor
   and console fuses. Nothing public changes that (B7 in the risk register).
2. **It cannot be tested.** No firmware image may be supplied by the project, no
   PS5 hardware is attached, and a "prototype loader" that never loads anything
   is a simulation with a progress bar.
3. **It teaches nothing reusable.** Every hour spent there is an hour not spent
   on storage, input, display and installation — the capabilities that make an
   operating system.
4. **It would violate the project's own rule.** Track B may not gate Track A
   (ADR-010). Starting with Track B would make the gate the first thing the
   project breaks.

Track B starts when it has lawful inputs and a test that can fail — M5/M6 in the
graph, after the legal assessment. Until then it is documentation and
identification, both of which already exist.

---

## Task 1 — Storage write path behind the block interface

**Milestone:** M1 (critical path). **Owner:** kernel-engineer. **Size:** large.
**Status: executed 2026-11-15 as milestone M1d** — all acceptance criteria below
are met on an emulated IDE disk, with the evidence in `docs/TEST_RESULTS.md`
§10. What remains open is explicitly *not* claimed: no physical disk has been
written, the timeout/IO error branches were not fault-injected, and partitions,
filesystems and the installer are still absent.

### Objective

Make the kernel able to write to a disk, so that an installer, a writable
filesystem, persistent logs and recovery stop being hypothetical. The read path
(ATAPI PIO + ISO9660) is proven; this task adds the other half behind the same
interface, without disturbing reads.

### Scope

* Sector write functions alongside `ata_read_blocks()` in `kernel/ata.c`.
  **As executed:** ADR-013 keeps the optical 2048-byte block interface intact
  and adds `ata_read_sectors()` / `ata_write_sectors()` for 512-byte disk
  sectors on an explicitly indexed device. No caller was reinterpreted.
* A writable target: the emulator's attached hard disk first; AHCI and NVMe
  register-level write paths only after that works.
* A kernel self-test that writes a known pattern to a reserved scratch area,
  reads it back, verifies it, and reports one `PC5-LOG` line.
* An explicit opt-in for any write: PC5 has never written to a disk, and a
  default-safe build must remain possible.

### Non-goals

Partition-table parsing, filesystem creation, the installer UI and boot-loader
installation. Those are the *next* tasks and depend on this one.

### Acceptance criteria

1. A write function exists with unit-testable logic and is exercised by
   `tests/kernel_unit.c` (bounds rejection, ATAPI refusal, overflow, alignment).
   **Met:** `ata_write_sectors()` + `kernel/block.c`.
2. In the emulated machine, the kernel writes a 4 KiB pattern to a scratch
   region, reads it back, and the boot log reports the digest. **Met** — the
   executed line is
   `PC5-LOG storage_write=ok dev=0 lba=2048 sectors=8 bytes=4096 verify=ok sha256=80fdc122…`,
   and the host checks the same digest against the emulator's disk image.
3. Negative tests exist: out-of-range LBA, past-end, LBA overflow, zero count,
   invalid and negative device index, NULL buffer, ATAPI write and scratch-window
   violation are each refused with a distinct status and logged, and the machine
   continues to boot. **Met** (nine guards, all `ok=1`). Timeout and device-error
   handling exists in the driver but was **not** fault-injected; the log says
   `storage_write_faults injected=0`.
4. Writing is impossible when the boot medium is the CD-ROM: the driver refuses
   and says why. The boot test asserts the refusal on the ISO path. **Met** —
   `storage_write_cdrom … status=read-only refused=1`.
5. The full suite passes: `make verify` (53), `make check` (121), `make
   boot-test` (45), `make stubtest` (42). **Met.**
6. `docs/KNOWN_LIMITATIONS.md` no longer says the storage stack is read-only
   without qualification — it says read-write on qualified devices, with the
   evidence linked. **Met**, and it now also states that only an emulated disk
   has been written.

### If it cannot be done here

The emulator must be shown to attach a writable disk before the kernel self-test
can exist. If v86 cannot, the task stops at step 1 with the AHCI/NVMe write path
and unit tests implemented, and the report says exactly that: "write path
implemented behind the interface, not executed on any device". A green
compile-time-only claim is not acceptance.

### Evidence to commit

`docs/TEST_RESULTS.md` section with the command, the log line, and the full suite
results; `build/test/boot-evidence.json` unchanged in format.

---

## Task 2 — UEFI boot under real firmware (close M1c)

**Milestone:** M1c → M1. **Owner:** kernel-engineer. **Size:** small, if the
environment cooperates.

### Objective

Replace "executed against stub firmware" with a firmware result. The loader code
is written; what is missing is an OVMF run (or a UEFI-capable VM), and this is
the cheapest evidence upgrade available to the project.

### Scope

* Obtain OVMF firmware (a VM image, a distribution package on a capable host, or
  a firmware blob supplied by the owner's machine).
* Boot `build/pc5.iso` under OVMF, capture the serial console and the loader's
  trace.
* Compare the loader's reported values with the stub run: GOP mode and pitch,
  memory-map conversion, `ExitBootServices` result, handover registers.

### Non-goals

Secure Boot, GOP-only display fixes, physical machines. Those follow once a
firmware run exists at all.

### Acceptance criteria

1. A committed transcript shows the ISO booting under OVMF: the El Torito UEFI
   entry loads `\EFI\BOOT\BOOTX64.EFI`, the loader finds the volume containing
   `KERNEL.BIN`, verifies `PC5K`, exits boot services and enters the kernel.
2. `PC5-BOOT-OK` appears in the kernel's serial log on the UEFI path.
3. The record names the firmware version and the emulator/VM (or machine), and is
   labelled `VERIFIED — OVMF` or `VERIFIED — HARDWARE`, never blurred with the
   stub result.
4. If the boot fails, the task still "completes": the failure is committed with
   the trace and `M1c` stays `in_progress`. A recorded failure is a result; a
   silent retry is not.
5. `docs/TEST_RESULTS.md`, `docs/KNOWN_LIMITATIONS.md`, the README table and the
   roadmap state for M1c are updated in the same change. Where the UEFI path now
   works, the README stops describing UEFI machines as unsupported.

### Evidence to commit

`docs/evidence/m1c/<firmware>/` containing `boot.log`, screenshots of the six
pages if a display exists, and `machine.json` with the firmware identity.

---

## Task 3 — Hybrid MBR so a USB stick boots as a hard disk

**Milestone:** M1. **Owner:** release-engineer. **Size:** medium, fully testable
here.

### Objective

A large fraction of real firmware treats a USB stick as a hard disk and ignores
the El Torito CD entry unless a valid MBR and partition table are present. The
image had none (KNOWN_LIMITATIONS §2); it has one since M1h. This is a structural
change with a structural test, so it can be completed without hardware — and it
directly attacks blocker B3.

### Scope

* `tools/mkiso.py` emits an optional hybrid MBR: one protective/active FAT
  partition entry whose extent covers the ISO, with the El Torito image still
  reachable for CD/CSM boots.
* `tools/verify_iso.py` validates the MBR signature, the partition entry, the
  CHS/LBA consistency and that the partition covers the image.
* `docs/INSTALLATION.md` documents both boot modes.

### Non-goals

Changing the El Torito layout, adding a UEFI fallback path, or claiming any
physical boot. The image remains the same operating system.

### Acceptance criteria

1. `make iso` produces an image whose sector 0 is a valid MBR (`0x55AA`), with
   exactly one active partition entry pointing at offset `0x800` and a length
   covering the ISO in 512-byte sectors.
2. An independent read of the MBR and partition table in `tests/test_tools.py`
   agrees with the writer's output — the verifier may not share the writer's
   arithmetic.
3. The BIOS El Torito path is unchanged: `make boot-test` still boots and passes
   every check (53 today, 25 when this task was written).
4. `make verify` gains at least three checks (MBR signature, partition extent,
   ISO and partition length agreement) and passes with the higher count; every
   document quoting the old count is updated in the same commit.
5. Byte-for-byte reproducibility under `SOURCE_DATE_EPOCH` still holds.
6. No document claims the image now boots on a specific machine.

### Evidence to commit

Updated `docs/TEST_RESULTS.md` with the new check count and the MBR layout, and
the new assertions in the host suite.

---

## Why these three and not others

| Alternative | Why it waits |
|---|---|
| Linux image | needs a build host the project does not have (B4); blocked, not deferred by choice |
| USB/xHCI stack | large, and useless before there is a writable disk to install onto |
| Installer UI | would be a menu of buttons until Task 1 lands |
| Shell / PlayStation experience | depends on sessions and GPU acceleration, both downstream of M2 |
| PS5 runtime | Track B; no lawful test input, and it must not gate any of the above |

The dependency they form is small and useful: Task 3 makes the medium reachable
by real firmware, Task 2 makes the UEFI path credible, Task 1 makes installation
possible. Together they satisfy M1's prerequisites, which is the gate in front of
everything else.

---

## Next three tasks, after M1d and M1e

The first three tasks (storage write, UEFI under real firmware, hybrid MBR) are
now: **task 1 executed** (M1d), **task 2 blocked on UEFI firmware**, **task 3
executed** (M1h). Partition discovery was pulled forward as M1e because it is the hard
prerequisite for both the installer and any filesystem work, and because it is
verifiable here without hardware. What follows is the next executable set, in
dependency order. Each is independently valuable and independently testable.

### Task A — FAT32 read: mount a real filesystem on a discovered partition

**Status: executed 2026-11-15 as milestone M1f.** Every acceptance criterion in
this task is met on an emulated disk, with three independent digest checks
(`docs/TEST_RESULTS.md` §12). Not claimed: writing, long file names, descending
into subdirectories, sector sizes other than 512 bytes, or any physical disk.

**Paths:** new `kernel/fat32.c`; `kernel/include/pc5.h`; `kernel/main.c` (mount
after `part_scan`); `kernel/ui.c` (`part` output, storage page); new
`tests/mkfat.py` fixtures; `tests/kernel_unit.c`; `tests/boot_test.py`.

**Why:** partition discovery stops at the table. An installer needs to read a
filesystem — to see whether a partition already holds an installation, and to
read the kernel it will copy. FAT32 is the only filesystem that is both simple
enough to implement honestly in this kernel and universally understood by
firmware, which matters for the UEFI ESP later.

**Implementation:** BPB validation (bytes/sector ∈ {512,1024,2048,4096},
sectors/cluster power of two, FAT count, reserved sectors, total sectors fitting
the partition), FAT chain walk with a loop/cycle bound, directory entry parsing
(8.3 and long names), read-only file read into a caller buffer. No allocator, no
write path, no cluster cache beyond one sector.

**Depends on:** M1d (sector reads), M1e (partition table).

**Output:** `PC5-LOG fat32 partition=1 status=ok label="PC5" files=…` and
`fat32_read` returning the bytes of a named file; a serial `ls <part>` command.

**Tests:** host unit tests against a fixture FAT32 image built by
`tests/mkfat.py` (valid, bad BPB, cyclic FAT chain, cluster past end); boot test
attaches a disk whose partition 1 contains a FAT32 volume with a known file and
asserts the kernel reads it back with the expected SHA-256.

**Acceptance:** the file's SHA-256 from the kernel equals the host's; a corrupt
BPB or cyclic chain is refused with a distinct status and the boot continues;
nothing writes to the volume (asserted by comparing the disk image before/after
and by the absence of any write call in the module).

**Risks:** FAT32 edge cases (LFN checksums, cluster-count boundary between FAT16
and FAT32); mitigated by refusing anything ambiguous and saying so. Time-boxed:
a read path that handles the fixture set, not every historical variant.

### Task B — FAT32 write: create a file, flush, and prove it from the host

**Status: create executed 2026-11-15 as milestone M1g; the rest is open.**
Done: allocation from the FAT, both FAT copies maintained and re-read, FSInfo
validated and recomputed, one root-directory entry written, two ATA flushes, and
a read-back through the sector source — with a fault injector for each stage and
an independent FAT32 reader confirming the dumped image
(`docs/TEST_RESULTS.md` §13). Not done: overwrite, delete, resize, rename,
subdirectory creation, directory expansion, and a boot-entry installer. The
original plan below is kept because the remaining work is still that plan; two
details changed in execution: the driver lives in `tests/fat32_write_check.c`
rather than `kernel/storage_test.c`, and the reader already existed in
`tests/fat32_images.py` (there is no `tests/mkfat.py`).

**Paths:** `kernel/fat32.c` (write path), `kernel/storage_test.c` (extend the
self-test to a filesystem round-trip), `tests/boot_test.py`, `tests/mkfat.py`.

**Why:** an installer that cannot create a file is not an installer. This is the
first operation that mutates a *filesystem* rather than raw sectors.

**Implementation:** allocate a cluster from the FAT, extend a directory entry
(or create one), write the data, update both FAT copies, flush the device, then
re-read through the read path and compare. Every metadata update is ordered so a
crash leaves a detectable inconsistency rather than a silent one.

**Depends on:** Task A.

**Output:** `PC5-LOG fat32_write=ok partition=1 bytes=… sha256=…` on an explicit
serial command, mirroring `wtest`; never automatic.

**Tests:** host unit tests for allocation and directory insertion; boot test with
an 8 MiB disk: create a file, flush, read it back in the kernel, dump the disk
image and verify it with an independent FAT32 reader written in the test
(`tests/mkfat.py` gains a parser) — two implementations must agree.

**Acceptance:** the host reader finds the file with the same bytes and the FAT
copies agree; a full disk fails with `no-space` rather than corrupting the
directory; the input image is only mutated when the explicit command runs.

**Risks:** write ordering and FAT-copy divergence; mitigated by refusing to
report success unless both FATs and the directory agree after a re-read.

### Task C — isohybrid medium: an ISO that also boots as a USB disk

**Status: executed 2026-11-15 as milestone M1h.** Done: a 440-byte MBR bootstrap
(`boot/mbr.S`) that loads the *same* El Torito boot image through INT 13h, a
partition table computed from the real layout, an `--no-cdrom` boot test with a
blank-sector-0 control, and verifier checks with a rejection test per corruption
(`docs/TEST_RESULTS.md` §14, ADR-014). Not done: a boot on physical hardware.
Four details differ from the plan below, and the plan is kept as history:
the entry is a single whole-image entry of type `0x17` (hidden), not a "FAT
partition entry" — the image contains no FAT filesystem outside the ESP, and
claiming otherwise would invite automounters to offer to format the stick; the
boot image's LBA comes from the **boot catalog** (LBA 24), not from the PVD; the
bootstrap is 440 bytes plus the table the builder owns, not a full 512-byte stub;
and the boot test detaches the CD rather than trusting `--hda` alongside
`--cdrom`, because firmware falls back to El Torito when the disk fails.

**Paths:** `tools/mkiso.py`, `tools/verify_iso.py`, `tests/test_tools.py`,
`tests/boot_test.py`, `docs/BUILDING.md`.

**Why:** the current image boots from CD/DVD and from a UEFI ESP, but a USB
stick written from it is not bootable by BIOS — the El Torito catalogue is the
only BIOS entry point. A hybrid MBR removes a real distribution blocker without
needing new kernel code.

**Implementation:** prepend/overlay a 512-byte MBR with one partition entry
covering the ISO, install a small boot stub that loads the El Torito boot image
by LBA (the 2048-byte sector count is already in the PVD), and set the ISO's
system area bytes so both interpretations stay valid. `verify_iso.py` learns to
check the MBR signature, the entry geometry and the stub's LBA arithmetic.

**Depends on:** nothing in the kernel; M1a's ISO writer.

**Output:** `build/pc5.iso` boots in v86 both as `--cdrom` and as `--hda`
(disk image), with identical serial evidence.

**Tests:** boot test runs the same image a second time as a hard disk; the ISO
verifier asserts the MBR fields; a negative test flips the signature and expects
refusal.

**Acceptance:** both boot paths produce `PC5-BOOT-OK`; the ISO still passes the
53 structural checks; the image remains byte-identical between two builds.

**Risks:** BIOSes differ in how they treat hybrid images; documented as
"verified in v86 only" until a physical USB test exists.

### Not in this set, and why

* **UEFI under OVMF** stays the top blocker, but it is an environment problem
  (no OVMF, no UEFI VM), not an engineering one. Until firmware exists, any
  "UEFI works" statement would be the stub run relabelled — which this project
  does not do.
* **Installer** depends on A, B and C; scheduling it now would mean building UI
  over three missing primitives.
* **AHCI/NVMe** widen the device set without unblocking anything above them;
  they follow the filesystem so the same filesystem code is tested on emulated
  AHCI rather than written blind.
