# Testing strategy

## 1. Sixteen evidence levels

A level is only claimed when its own evidence exists. The table is the contract
used across this repository.

| # | Level | How it is proven | Current status |
|---|---|---|---|
| 1 | Code compiles | `make` | PROVEN |
| 2 | Build succeeds | `make iso` writes `build/pc5.iso` + `build-info.json` | PROVEN |
| 3 | ISO is generated | file exists with recorded SHA-256 | PROVEN |
| 4 | ISO integrity verified | `make verify` (independent parser, 53 checks) | PROVEN |
| 5 | ISO boots in a virtual machine | `make boot-test` (v86 + SeaBIOS) | PROVEN |
| 6 | Installer works | installer does not exist | NOT APPLICABLE YET |
| 7 | Installation succeeds | depends on 6 | NOT APPLICABLE YET |
| 8 | Installed system boots | depends on 6 | NOT APPLICABLE YET |
| 9 | Physical hardware boots | requires a physical machine | NOT PROVEN |
| 10 | Controller works | DualSense over USB/BT | NOT PROVEN (no USB stack) |
| 11 | Internet connectivity works | wired/Wi-Fi from the booted system | NOT PROVEN |
| 12 | Genuine PS5 firmware executes | instrumented boot of Sony system software | NOT PROVEN |
| 13 | Genuine PS5 home screen operates | observation on hardware | NOT PROVEN |
| 14 | Actual PS5 game installs | package installation from media | NOT PROVEN |
| 15 | Actual PS5 game launches | title execution trace | NOT PROVEN |
| 16 | Actual PS5 game playable | sustained gameplay measurements | NOT PROVEN |

Levels 1–5 are machine-checked in this repository. Levels 6–16 are not claimed
anywhere, in any document, on any screen.

## 2. Test layers

### 2.1 Host unit tests — `make check`

* `tests/test_tools.py`
  * ISO writer: determinism, volume descriptors, catalog checksum, virtual
    sector count, descriptor patching (LBA/size/CRC32).
  * Verifier: accepts a good image, rejects a corrupted catalog signature.
  * Firmware tool: SLB2 identification, FIH/PFS offset, honest "unidentified"
    result, manifest → C embedding.
  * Kernel unit binary: built and executed.
  * Built image: verifies the real `build/pc5.iso` when present.
* `tests/kernel_unit.c` — 160 assertions over the kernel's pure logic:
  formatter (`%s`, `%-4s`, `%08x`, `%llu`, `%%`), number conversion, E820
  accounting, chain state machine (including "a firmware manifest never turns
  execution into OK").

`tests/test_fat32.py` mounts a real FAT32 image with the kernel module compiled
as a host program and compares its SHA-256 for every fixture file against two
independent sources: the bytes the fixture was generated from and a second FAT32
reader written in Python. It also proves the image is unchanged after reading,
that a FAT16 BPB is refused, and that the filesystem code reaches the disk only
through its injected sector callbacks.

`tests/test_fat32_write.py` is the M1g layer, and it is written as an
adversarial test rather than a happy path. Each case builds a fresh image, runs
the kernel's create path against a copy, then hands the *resulting image* to the
independent Python reader and checks the file's bytes and SHA-256, its cluster
chain, every FAT copy, the FSInfo counters, every pre-existing file, and the
exact set of changed sectors. Refusals (existing name, full volume, full root
directory, invalid name, read-only source, oversize file) must leave the image
byte-identical. `tests/fat32_write_check.c` then forces write and flush failures
at each stage — first data write, between FAT copies, before the directory
entry, during the flush, and a corrupted data write — and the Python verifier
classifies the result as intact, leaked-chain, partial-file or inconsistent
mirror. No case is allowed to report a failed operation as a success.

`tests/test_fat32_expand.py` is the M1i layer and keeps that shape, extended to
the three operations that came after create. Overwrite, mkdir and the path
grammar are each driven through the real module and judged by the resulting
image: the file's bytes, its slot (unchanged for an overwrite), both FAT copies,
the chains of every object, `.` and `..`, FSInfo, the changed-sector set and every
other file in the volume. The refusals (missing file, directory, read-only entry,
volume without room for both versions, over-long name, nine components, missing
parent, file used as a directory) must leave the image byte-identical. The
failure half is a sweep rather than a handful of cases: every write of an
overwrite and of a mkdir is failed in turn, and an independent reader classifies
what each failure left behind — old state or new state, the exact leak, the exact
mirror divergence — with the accounting required to close. "Readable after a
reboot" is checked by booting the dumped image a second time, not by assuming it.

`tests/test_partitions.py` protects the partition work's seams: that the
fixture images really carry the CRCs and backup header the kernel checks, that
the module contains no write call, that ATAPI is refused before any read, and
that both build systems compile the parser.

`tests/test_storage.py` separately protects the write test's seams: that the
scratch window in the boot test matches `kernel/include/pc5.h`, that the host
pattern produces the kernel's expected digest, that no boot path starts a write
test, and that the harness only ever attaches a file it created.

These run in milliseconds and catch the class of bug that otherwise only shows
up on screen — the formatter bugs found this way are recorded in
`docs/TEST_RESULTS.md`.

### 2.2 Architecture and milestone graph — `make roadmap`

`tools/roadmap.py` validates `docs/architecture/roadmap.json`: unique ids, known
workstreams, kinds, levels and roles, no cycles, acceptance criteria on every
milestone, evidence paths that exist, and three architectural rules:

* the **destination** milestone exists, is level 5, is reachable from the roots,
  and is not `complete` — and every milestone the destination depends on
  (`product.required_milestones`) must exist, so it cannot be deleted quietly;
* **release isolation**: an `experimental` milestone may not appear in the
  dependency closure of a `release_gating` one (ADR-021). The former rule was the
  opposite — the PS5 work could not gate the product — which made the destination
  optional;
* every workstream owns at least one milestone, so a workstream cannot exist as a
  heading with no work behind it.

`tests/test_architecture.py` runs the same validation inside `make check` with
negative fixtures that prove each rule can fail, checks that the generated graph
in `PC5_MILESTONE_GRAPH.md` matches the data, and checks that the architecture
documents' relative links resolve. `tests/test_product_identity.py` checks the
identity itself: the destination is level 5 and not complete, the substrate is
not the product, and the forbidden claims stay in the historical documents.

### 2.3 Structural image test — `make verify`

A second, independent ISO9660/El Torito parser checks:
volume descriptor, block size, volume size vs file size, root directory walk,
boot record volume descriptor, catalog validation checksum, bootable
no-emulation entry, load segment `0x7C0`, virtual sector count in 512-byte
units, boot descriptor CRC32, descriptor↔directory agreement for the kernel
extent, `PC5K` kernel signature, and per-file SHA-256 against the build record.

### 2.4 Emulated boot test — `make boot-test`

`tests/v86_runner.mjs` boots the image in v86 (SeaBIOS, emulated CD-ROM,
El Torito no-emulation) and records serial output, the VGA text screen and
scripted input. It can also attach a writable disk from an in-memory copy of a
file (`--hda-buffer`), which is how the storage write test gets a disposable
8 MiB IDE disk; the image is dumped afterwards (`--hda-out`) and verified on the
host, and the input file is never written back to. `tests/boot_test.py` then asserts:

* loader banner, ISO9660 descriptor read, kernel verification, protected-mode
  handoff, kernel start, `PC5-BOOT-OK`;
* `genuine_ps5_execution=no` and a blocked chain stage with evidence in the log;
* the kernel mounted its own medium through its ATAPI/ISO9660 code and read a
  file back with a valid signature (`storage_mounted volume=PC5 … signature=ok`),
  and still does so with a writable disk attached;
* `wtest` wrote a 4 KiB pattern to the reserved window on that disk, read it
  back and verified it; every unsafe request (zero count, out-of-range,
  overflow, invalid index, NULL buffer, ATAPI write) was refused with
  `ok=1`, and the host's SHA-256 of the written sectors equals the kernel's;
* with a GPT disk attached, the kernel parsed the protective MBR, verified the
  header and entry CRC-32 and reported both partitions and their LBAs; with one
  entry byte flipped it reported `status=crc`, offered no target and still
  booted; with a zero-filled disk it reported `scheme=none`;
* with a FAT32 disk attached, the kernel mounted it, listed the root directory,
  read all six fixture files and matched their SHA-256 against both the fixture
  bytes and the independent reader, and the dumped image was byte-identical;
* a `put NEW.TXT hello-m1g` typed at the console created a real file on a
  *disposable* FAT32 disk; the dumped image was read back by the independent
  reader, which matched the payload byte for byte, found both FAT copies
  identical, no orphan clusters, every pre-existing file intact and the changed
  sectors confined to the data, FAT, FSInfo and root-directory regions; repeated
  and malformed names were refused with no writes;
* all six interface pages by dumping the console's shadow character grid over
  the serial console (`page N` + `screen`), so the assertions cover the
  *rendered* interface rather than a separate reporting path;
* the **hybrid MBR** path: the ISO is attached as a writable disk and booted with
  the CD *detached* (`--no-cdrom`, `boot_order=2`), and the run must show the
  loader banner, `disk unit bytes 0200`, kernel verification and `PC5-BOOT-OK`;
  a second run with sector 0 blanked must produce no output at all, so the
  evidence cannot be the firmware falling back to El Torito.

Evidence is written to `build/test/boot-evidence.json`, `serial.log`,
`serial-ui.log`, `serial-mbr.log`, `serial-mbr-blank.log` and `screens.txt`.

### 2.5 Real firmware boot — `make ovmf-test`

`tests/ovmf_boot.py` boots the finished ISO under genuine TianoCore OVMF in QEMU
and judges the serial log. It is a **separate gate**, never folded into
`make test` or `make verify`, because firmware is an external dependency: a
machine without OVMF must be able to say "not tested" out loud.

It does, in this order: copy the pristine VARS image (firmware writes it, so it
must never be the package's file), boot cold, boot again with the variable store
the first boot wrote, then boot five deliberately broken machines built with the
repository's own tools (no `BOOTX64.EFI`, corrupted PE32+ header, no kernel, bad
`PC5K` signature, truncated ISO), and finally a one-second deadline to prove a
hang is reported as a timeout. Each result is attributed: firmware refused to
start something, the loader stopped with its own code, or the harness ran out of
time.

Three outcomes, never two: `0` verified (and `PC5-OVMF-BOOT-OK` printed by the
harness, never by the guest), `1` verified failure, `3` **NOT TESTED** — no
emulator, no firmware, or a file that is not a firmware volume. Individual checks
can also be **NOT APPLICABLE**, which is printed, counted separately and never
counted as a pass: a boot path this medium does not offer (disk-style UEFI) is
not a boot path that works.

Firmware stays external: nothing is committed, nothing is embedded in the ISO,
nothing is linked into the kernel, and the harness refuses a "firmware" whose
bytes are the PC5 loader or that lacks a `_FVH` volume header.

### 2.6 What the emulators cannot prove

v86 is an x86 PC emulator, not a PC. It proves the boot chain and the interface
logic. It does **not** prove: real firmware behaviour (VBE vs GOP quirks), real
chipset/PCI topology, real disk controllers, USB, audio, network, timing or
thermal behaviour. No claim of physical compatibility is made from it.

## 3. Physical hardware plan (not yet executed)

For the reference Dell Inspiron 15 7577 and any other machine:

1. flash the ISO, boot with CSM enabled, capture the serial log on another host
   with a USB-serial adapter or via the same stick's log file if a storage
   driver exists (it does not yet — record the screen instead),
2. record `PC5-LOG` lines: display mode, CPUID, E820, PCI list, ACPI tables,
   SMBIOS strings,
3. compare against `docs/HARDWARE_COMPATIBILITY.md` and file the result,
4. repeat with a DualSense attached on the USB path once the USB stack exists.

## 4. Regression policy

* every bug fixed on screen becomes an assertion in `tests/boot_test.py` or
  `tests/kernel_unit.c`,
* `make check` must pass before a commit that touches `kernel/` or `tools/`,
* `make verify` must pass before any release.
