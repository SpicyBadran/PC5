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
that a FAT16 BPB is refused, and that the module contains no write path.

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
tracks and roles, no cycles, acceptance criteria on every milestone, evidence
paths that exist, and — the architectural gate — no Track A milestone depending
directly or transitively on a Track B milestone. `tests/test_architecture.py`
runs the same validation inside `make check`, including negative fixtures that
prove the gate can fail, and checks that the generated graph in
`M0_M8_DEPENDENCY_GRAPH.md` matches the data and that the architecture
documents' relative links resolve.

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
* all six interface pages by dumping the console's shadow character grid over
  the serial console (`page N` + `screen`), so the assertions cover the
  *rendered* interface rather than a separate reporting path.

Evidence is written to `build/test/boot-evidence.json`, `serial.log`,
`serial-ui.log` and `screens.txt`.

### 2.5 What the emulator cannot prove

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
