# M1 — physical boot: the procedure, and the record it has to produce

**Status of M1: `not_started`.** No physical machine has booted PC5. This document
is the procedure and the evidence format, not evidence. Until a filled record and
a captured log exist in `docs/evidence/`, M1 stays `not_started` and level 1 of the
success hierarchy stays "emulated only".

Its purpose is narrow: make one physical boot reproducible by somebody else, and
make the result impossible to half-report. Every field below is checked by
`tools/hw_record.py`, so a record that omits a value, guesses one, or claims a
level without the log that proves it fails validation instead of being committed.

## 1. The five levels, which are never interchangeable

| Level | What it proves | Produced by | Where it is recorded today |
|---|---|---|---|
| 1. ISO structure verified | the image is a valid ISO9660/El Torito medium whose contents match a manifest | `make verify` | `docs/TEST_RESULTS.md` |
| 2. Emulated boot | PC5's own code ran under an emulator | `make boot-test` (v86) | `docs/TEST_RESULTS.md` §18 |
| 3. Real firmware boot | PC5's UEFI loader ran under genuine TianoCore OVMF | `make ovmf-test` | `docs/TEST_RESULTS.md` §16/§18 (machine-dependent; exit 3 = NOT TESTED) |
| 4. Physical PC, UEFI | the image boots on a named machine through its own UEFI firmware | this procedure | **nothing yet** |
| 5. Physical PC, BIOS | the same image boots through CSM/legacy BIOS, where the machine supports it | this procedure | **nothing yet** |

Level 2 is not level 4. Level 3 is not level 4. A UEFI stub is not firmware. A boot
on a virtual machine is not a boot on a PC somebody owns. Each level is recorded
separately, and a record names exactly one level per attempt.

## 2. Before touching the machine

1. Build the image from a clean tree at a named commit:
   ```sh
   SOURCE_DATE_EPOCH=1763251200 make
   make verify          # structure + manifest
   sha256sum build/pc5.iso
   ```
   Record the commit, the `SOURCE_DATE_EPOCH`, the ISO's SHA-256 and the manifest's
   file list. `make verify` now writes `build/pc5.iso.manifest.json`; a second
   reader reproduces it with:
   ```sh
   python3 tools/iso_manifest.py build/pc5.iso --verify build/pc5.iso.manifest.json
   ```
2. Write the image to the medium **without** adding a partition table or a
   filesystem of your own: `dd`-style byte-for-byte writing, or a tool that offers
   exactly that. The image is `isohybrid`-style already; a tool that "converts" it
   is a variable nobody needs.
3. Capture the medium's SHA-256 by reading it back, not by trusting the writer.
4. Have a serial capture method ready. PC5 writes its diagnostics to **COM1,
   115200 8N1**. On a machine without a serial port this is the step that fails
   first: a USB-to-serial adapter on the header, or a second machine reading the
   port, is required. A screenshot is not a serial log and does not satisfy the
   record.

## 3. Firmware settings to record (not to change blindly)

| Setting | Why it matters | Record the value |
|---|---|---|
| firmware vendor, version, date | a boot on one firmware version is not a boot on another | exact string as the setup screen shows it |
| boot mode | UEFI vs legacy/CSM changes which path of the image runs | `uefi` or `bios` |
| Secure Boot | PC5 ships no signature; a policy that is enforced blocks the image | `enabled` / `disabled`, and whether keys are in setup mode |
| USB boot | the medium must be offered as a boot target | the menu entry actually chosen |
| CSM | needed for level 5 only | `enabled` / `disabled` / `absent` |

Do not disable Secure Boot and then record the boot as if the policy had been
tested. If Secure Boot must be disabled for the attempt, the record says so, and
the attempt proves nothing about secure boot.

## 4. The record

Copy `docs/evidence/M1_RECORD_TEMPLATE.json` to
`docs/evidence/<date>-<vendor>-<model>.json`, fill every field, and validate it:

```sh
python3 tools/hw_record.py docs/evidence/2026-11-20-example-x570.json
```

The template's fields, and what each one is for:

| Field | Content | Why it is required |
|---|---|---|
| `date`, `operator` | when, and who can be asked about it | an unattributable record is not evidence |
| `level` | one of `structure`, `emulator`, `ovmf`, `physical-uefi`, `physical-bios` | the five levels are not interchangeable |
| `outcome` | `booted`, `partial`, `failed` | a failed attempt is a result and is recorded |
| `machine.vendor`, `machine.model` | the exact machine | "a PC" is not a qualification |
| `machine.cpu`, `machine.ram_gb` | model and capacity | the platform model has minimums |
| `machine.board`, `machine.chipset` | motherboard and chipset | drivers are per controller |
| `machine.gpu`, `machine.display` | GPU and how the display was attached | graphics bring-up is per output |
| `machine.storage` | controller and device, or "none present" | storage bring-up is per controller |
| `firmware.vendor`, `firmware.version`, `firmware.date` | firmware identity | see §3 |
| `firmware.secure_boot`, `firmware.boot_mode`, `firmware.csm` | configuration | see §3 |
| `medium.kind`, `medium.capacity_gb` | USB stick, SD, optical | the medium is a variable |
| `image.commit`, `image.source_date_epoch`, `image.sha256`, `image.manifest_sha256` | the exact image | ties the boot to a build |
| `image.medium_sha256` | read back from the medium | proves the medium holds the image |
| `serial.capture` (`none` / `file` / `piped`), `serial.log`, `serial.baud` | how the log was taken | a boot without a log is an anecdote |
| `serial.final_line` | the last line PC5 printed | the log's conclusion, quoted exactly |
| `observations` | what appeared on screen, in the reporter's words | the only place for a screen claim |
| `blockers` | what stopped a fuller result | the next task comes from here |

Rules the validator enforces:

* an empty or `TBD` value is rejected — an unfilled record is not a record;
* `sha256` fields must be 64 lowercase hex characters;
* `level: physical-uefi` or `physical-bios` with `outcome: booted` requires a serial
  log path that exists, because "it booted" is exactly the claim that needs the log;
* `level: emulator` and `level: ovmf` are rejected in a physical record: those are
  different gates and already have their own evidence;
* a record may not claim `booted` while also listing a blocker that contradicts it.

## 5. Committing the evidence

Commit the record, the serial log verbatim, and — separately — nothing else. Do not
commit screenshots of a PS5 interface: none exists, and an image of one would be a
claim PC5 cannot make. Do not edit a log to remove noise; mark the interesting
lines in the record's `observations` instead.

## 6. What a successful physical boot does and does not establish

It establishes level 4 (or 5): PC5's own boot path executed on physical hardware,
with the firmware and machine named and the log preserved.

It does not establish that PC5 drives the machine's devices natively (`M2`), that
the platform model satisfies the PS5 contract (`M11`–`M16`), or that any Sony
software runs (`M17`–`M19`). Those stay separate, and the honest state of each is in
`docs/PS5_COMPATIBILITY_STATUS.md`.
