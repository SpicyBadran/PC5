# Testing strategy

## 1. Sixteen evidence levels

A level is only claimed when its own evidence exists. The table is the contract
used across this repository.

| # | Level | How it is proven | Current status |
|---|---|---|---|
| 1 | Code compiles | `make` | PROVEN |
| 2 | Build succeeds | `make iso` writes `build/pc5.iso` + `build-info.json` | PROVEN |
| 3 | ISO is generated | file exists with recorded SHA-256 | PROVEN |
| 4 | ISO integrity verified | `make verify` — every check in `build/verify-report.json` passes, and the image's manifest is reproduced by a second reader | PROVEN |
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

## 1b. Gate inventory: what each command proves, and what it does not

Every gate below is a separate claim. A gate that cannot run says so and exits non-zero;
none of them is folded into a single "pass".

| Command | Proves | Cannot prove | Result semantics |
|---|---|---|---|
| `make` / `make iso` | the image builds reproducibly at this commit | that it is valid or bootable | exit 0, SHA-256 printed |
| `make verify` | ISO9660 + El Torito structure, ESP, hybrid MBR, payload hashes, and that `tools/iso_manifest.py` reproduces the image's manifest | that any firmware will boot it | `VALID` / `INVALID`, exit 1 on failure |
| `make check` | host and kernel unit tests, platform contract, documentation guards, generated files current | anything about hardware | exit 1 on any failure; `presentation-check` and `docs-check` never write |
| `make presentation-check` | the four figures and the README status block match `roadmap.json` | that the labels are flattering — they are derived, that is the point | exit 1 if stale or if a claim is banned |
| `make docs-check` | every Markdown link, heading anchor and documented `make` target resolves | that the prose is *true* | exit 1 on any unresolved reference |
| `make roadmap` | the graph is consistent, release isolation holds, the destination is reachable | that milestones are correct | exit 1 on any inconsistency |
| `make psm` | the platform contract's own rules, linkage audit and conformance suite | anything about Sony's implementation | exit 1 on any failure |
| `make hw-record` | every filed physical-boot record is complete; the template is not | that a boot happened | exit 1 on an incomplete record |
| `make boot-test` | PC5's own boot path runs under the v86 emulator and prints the expected interface | firmware, or hardware | exit 77 and a SKIP message without node/v86 |
| `make stubtest` | the real `BOOTX64.EFI` runs against a stub firmware | real UEFI firmware | exit 2 without unicorn, 0/1 otherwise |
| `make validation` | the committed records are coherent: schema, exit code against verdict, commit and date present | that any gate was run just now | exit 1 on an inconsistent record |
| `make record-validation` | *writes* one record per gate, by running it — never part of `make check` | anything it does not run | exit 1 only if a gate reports `failed`/`error` |
| `make kernel64` | the flat 64-bit kernel links at `0x100000` with no unresolved symbols | that it runs anywhere |
| `make uefi64` | the experimental 64-bit loader is a valid PE32+, and the ESP image that carries `KERNEL64.BIN` is built | that firmware boots it |
| `make stubtest64` | the 64-bit path runs end to end in long-mode emulation: page tables installed, `ExitBootServices` done, kernel validates the handover, marker printed last | real firmware, or that the MMU would accept the map on hardware | exit 3 without unicorn, 0/1 otherwise |
| `make ovmf-test` | a boot under genuine TianoCore OVMF | physical hardware, or Sony software | 0 verified, 1 failed, **NOT TESTED when the tool exits 3** (make then exits 2 and prints the reason) |

`make check` deliberately does not include the emulator, stub, OVMF or hardware gates:
they need dependencies an ordinary developer machine does not have. Each is invoked
explicitly and reports its own state.

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

### 2.1b Platform contract and conformance interfaces — `make psm`

`make psm` (also run by `make check`) gates the `M10` contract. It has four steps,
each of which fails the build on its own terms:

* `python3 -m psm.contract` — the evidence registry's rules: every claim names a
  source, a confidence, a remaining uncertainty, the engineering decision that
  depends on it and the conformance case that exercises PC5's side; a `PUBLIC`
  claim without a verified or located source is an error; an `UNKNOWN` or
  `RESTRICTED` claim carrying a value is an error.
* `python3 -m psm.audit` — the linkage between registry and code: every operation
  cites a claim from its own subsystem or a PC5 decision, an operation marked
  `UNSUPPORTED` must cite an `UNKNOWN`/`RESTRICTED` claim, an implemented
  operation may not cite a `RESTRICTED` one, and the eight required interface
  groups must all exist.
* `python3 -m psm.conformance` — 261 checks against the ten PC5-authored doubles:
  valid paths, invalid parameters, boundaries, missing devices, unsupported
  operations, exhaustion, invalid transitions, ownership, timeouts, determinism.
* `python3 -m psm.render --check` — the contract document's 36 generated tables
  compared byte-for-byte with their renderers.

`tests/test_psm_mutations.py` exists because a suite that has only seen working
code proves nothing about itself: it replaces one double at a time with a broken
variant (overlapping allocations, double release, a fabricated success for an
`UNKNOWN` operation, a timeout that returns a message, a log that drops records)
and requires a failing check for each. A surviving mutation fails the build.

None of this is evidence about Sony's system software. It is `CONFORMANCE-TESTED`
against PC5's own contract and nothing more; see
`docs/architecture/PS5_PLATFORM_CONTRACT.md` §7.

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

`make verify` runs two readers. `tools/verify_iso.py` checks the structure:
volume descriptors, El Torito catalog, the FAT ESP, the kernel payload and the
hybrid MBR, cross-checked against `build/build-info.json`. `tools/iso_manifest.py`
is the second reader — it does not import the writer — and it writes
`build/pc5.iso.manifest.json` (every file with its SHA-256, the boot entries and the
build record) and then verifies the image against it. A release ships both, so a
third party can confirm the contents without trusting the build.

The manifest reader found a real four-byte offset error in the primary volume
descriptor, recorded in `docs/TEST_RESULTS.md` §21.1: nothing PC5 ships read those
fields, and only an independent parser noticed.

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
the first boot wrote, then boot deliberately broken machines built with the
repository's own tools (no `BOOTX64.EFI`, corrupted PE32+ header, no kernel, bad
`PC5K` signature, truncated ISO, a 24 MiB machine that cannot hold the load
window), and finally a one-second deadline to prove a hang is reported as a
timeout. With `--secure-boot` it additionally boots with SMM and a secure flash
and asserts that the firmware **refuses** the unsigned image — a boot there would
mean the policy was not in force.

Each judged boot now also cross-checks the boot chain's own claims: the handover
window against the memory map (`E15`), the merged entry count against the
kernel's independent `memory_mib` report, the read-back kernel copy against the
file size on the medium, the watchdog call, and the `ExitBootServices` retry
count. `--matrix-out` writes the 22-row required matrix, in which a row is
`verified` only if an executed run produced it, `external` if its evidence is a
named different gate, and `not-tested` otherwise. Each result is attributed:
firmware refused to start something, the loader stopped with its own code, or the
harness ran out of time.

Three outcomes, never two: `0` verified (and `PC5-OVMF-BOOT-OK` printed by the
harness, never by the guest), `1` verified failure, `3` **NOT TESTED** — no
emulator, no firmware, or a file that is not a firmware volume. Individual checks
can also be **NOT APPLICABLE**, which is printed, counted separately and never
counted as a pass: a boot path this medium does not offer (disk-style UEFI) is
not a boot path that works.

Firmware stays external: nothing is committed, nothing is embedded in the ISO,
nothing is linked into the kernel, and the harness refuses a "firmware" whose
bytes are the PC5 loader or that lacks a `_FVH` volume header.

### 2.5b The 64-bit foundation — `make kernel64`, `make uefi64`, `make stubtest64`

The 64-bit path is tested at three levels, and none of them is called a firmware
test:

| Level | Command | What actually executes |
|---|---|---|
| unit | `make check` (`tests/test_kernel64.py`) | nothing: the ELF/PE images are inspected, `struct pc5_boot64` is compiled on the host and compared with the kernel's assertions, and `pagetables.c` is built as a host library so its entries can be read back |
| kernel contract | `make check` (`kernel64_machine.py`) | the kernel binary alone, at its link address, with deliberately broken boot-info structures: each failure must print its reason and must **not** print the marker |
| stub firmware | `make stubtest64` | the loader's PE image and the kernel's binary, under Unicorn in long mode with a modelled firmware: memory map, file protocol, `ExitBootServices` key checks, paging walked through PC5's own tables |

What is *not* proven at any of these levels: that real firmware accepts the
image, that a real MMU honours the map (Unicorn walks the tables, but it is not a
CPU), or that any physical machine reaches the marker. `make ovmf-test` remains
the only gate that can claim real firmware, and it needs TianoCore plus
`qemu-system-x86_64`; on this machine both are absent, so the honest result is
**NOT TESTED** and the reason is stated with the result.

### 2.6 Provisioning the firmware

`make ovmf-setup` (`tools/ovmf_setup.sh`) installs QEMU and OVMF from the
distribution's packages and prints the paths and SHA-256 of the exact CODE and
VARS images the run will use, which then belong in the report. `make ovmf-check`
does the discovery half only. The `ovmf` job in `ci/build.yml` runs the same
script on a hosted runner and treats exit code 3 from the harness as a failure,
so a pipeline that never executed firmware cannot pass quietly.

### 2.7 What the emulators cannot prove

v86 is an x86 PC emulator, not a PC. It proves the boot chain and the interface
logic. It does **not** prove: real firmware behaviour (VBE vs GOP quirks), real
chipset/PCI topology, real disk controllers, USB, audio, network, timing or
thermal behaviour. No claim of physical compatibility is made from it.

## 3. Physical hardware plan (not yet executed)

The gates that decide what physical evidence buys are scoped in
[`GATES.md`](GATES.md): Gate A opens hardware evidence, Gate C the 64-bit
foundation, Gate D SVM feasibility, Gate E device support. None of them is an
authorisation to claim more than its own acceptance criteria.

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
