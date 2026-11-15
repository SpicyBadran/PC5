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
image has none (KNOWN_LIMITATIONS §2). This is a structural change with a
structural test, so it can be completed without hardware — and it directly
attacks blocker B3.

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
3. The BIOS El Torito path is unchanged: `make boot-test` still reports 25/25.
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
