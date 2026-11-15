# Test results

Recorded on 2026-11-13 (sections 1–6 and 7.7: milestone A, base `719432c`;
section 7: milestone B2, branch `arena/2f423f3b-pc5`, base `main` `4a7e2a7`;
section 6 activation attempts: milestone B3, bases `main` `237725d` and
`a266936`). Earlier revisions of this file dated the B3 work 2026-11-12; the
merges they describe are timestamped 2026-11-13, which is the date used now.
Build under test: PC5 `0.2.0`. Sections 1–6 describe the BIOS-only medium as of
`5ab7c48`; section 7 covers the UEFI boot path added on top of it and is the only
section that mentions it.
Host: Debian 12 x86-64, gcc 12.2.0, binutils 2.40, make 4.3, python 3.11.2,
node 22.22.3, v86 (npm `v86-system` 0.5.0) for the emulated boot test.

Every number below was produced by the command quoted next to it, in this
session, on this source tree.

Sections 9–14 are later milestones (M1d, M1e, M1f, M1g, M1h). Each one starts
with its own build inputs, sizes and hashes; the artifact numbers in §1 describe
base `5ab7c48` and are kept as history, not as the current image. The current
image is the one in §14.

## 1. Artifact

```
$ export BUILD_ID=$(git rev-parse HEAD)
$ export SOURCE_DATE_EPOCH=$(git log -1 --format=%ct HEAD)
$ make clean && make iso
pc5: loader image 4096 bytes
pc5: kernel image 36508 bytes (limit 585728)
pc5: wrote build/pc5.iso (96256 bytes, 47 sectors)
pc5: sha256 0644cf66faf77cba1034123ac22a6451de340c1ba2bffc0b484bcc6a05e7837f
```

| Property | Value |
|---|---|
| path | `build/pc5.iso` |
| size | 96,256 bytes (47 × 2048) |
| SHA-256 | `0644cf66faf77cba1034123ac22a6451de340c1ba2bffc0b484bcc6a05e7837f` |
| inputs | `BUILD_ID=5ab7c48…`, `SOURCE_DATE_EPOCH=1794561088` (commit time) |
| loader image | 4,096 bytes (8 El Torito virtual sectors) |
| kernel image | 36,508 bytes, `PC5K` signature, loaded at `0x10000` |
| volume id | `PC5` |
| side files | `build/pc5.iso.sha256`, `build/build-info.json`, `build/verify-report.json` |

The digest embeds the commit id and the build timestamp, so a rebuild from a
different commit is a different — equally valid — image. Pin both inputs
(`docs/BUILDING.md`) to reproduce this exact file.

## 2. Structural verification — `make verify`

```
$ make verify
VALID: build/pc5.iso  sha256=0644cf66faf77cba1034123ac22a6451de340c1ba2bffc0b484bcc6a05e7837f
```

32 checks, 0 failures, written to `build/verify-report.json` as well. Selected
lines:

```
[PASS] image is a whole number of 2048-byte blocks  (96256 bytes)
[PASS] volume descriptor signature at LBA 16  (CD001)
[PASS] logical block size is 2048  (2048)
[PASS] volume size matches the file  (47 blocks)
[PASS] El Torito system identifier  (EL TORITO SPECIFICATION)
[PASS] catalog validation checksum sums to zero  (0x0)
[PASS] default entry is bootable  (0x88)
[PASS] default entry is no-emulation  (0)
[PASS] boot image load segment is 0x7C0  (0x7c0)
[PASS] boot image advertises at least one sector  (8)
[PASS] boot descriptor CRC32 is valid
[PASS] descriptor kernel size matches the directory record  (36508 vs 36508)
[PASS] image SHA-256 matches build info
```

## 3. Unit tests — `make check`

```
$ make unit
PASSED: 35 checks, 0 failures

$ python3 -m unittest discover -s tests -p 'test_*.py'
Ran 32 tests in 1.721s
OK
```

The host suite grew from 13 to 32 tests in this milestone:

| Class | Tests | What it covers |
|---|---|---|
| `MkIsoTests` | 10 | extent layout, volume descriptors, catalog checksum, descriptor patching, `SOURCE_DATE_EPOCH` determinism |
| `FirmwareToolTests` | 4 | container identification, honest "unidentified", manifest → C |
| `KernelUnitTests` | 1 | builds and runs the kernel unit binary |
| `BuiltImageTests` | 1 | the real `build/pc5.iso` verifies and matches its record |
| `BuildMetaTests` | 3 | provenance written, hash re-checked, refusal on mismatch or failing report |
| `WorkflowTests` | 7 | actions pinned to SHAs, no `pull_request_target`, one write job, tag-only publishing, delegated publish script, no secrets, reproducibility gate |
| `ReleaseNotesTests` | 2 | release notes state measured numbers, never invent one |
| `PublishScriptTests` | 4 | publish script against a stub `gh`: three assets, idempotent re-run, verdict gate, checksum gate |
| `CiLayoutTests` | 2 | the pipeline file exists and the activation note explains where it lives |

Bugs these tests caught during development:

| Bug | Caught by | Fix |
|---|---|---|
| ATAPI IDENTIFY result discarded because a packet device answers with ERR set | serial log showed `storage drives=0` although the CD was present | wait for BSY only; treat ERR + signature as "ATAPI present" |
| ATA data register accessed as two byte reads/writes | PVD read back as garbage (`storage_mounted=no error=-2`) | added `inw`/`outw`; the data port is 16-bit |
| Loader advanced the sector address by bytes instead of disk units | first kernel image over 32 KiB hung the boot | `read_lba` advances in disk units |
| ISO volume/directory timestamps came from the wall clock | two `SOURCE_DATE_EPOCH` builds had different digests | stamps come from `SOURCE_DATE_EPOCH`; test added |
| Workflow test asserted on a literal that also appeared in a comment | test failed on the workflow itself | assert on parsed permission lines |

## 4. Emulated boot — `make boot-test`

```
$ make boot-test
PASSED: boot evidence written to build/test/boot-evidence.json
```

25 checks, 0 failures (list in `build/test/boot-evidence.json`), covering the
loader markers, the protected-mode handoff, `PC5-BOOT-OK`, the honest chain
report, the kernel's own ATAPI/ISO9660 read-back, and the six rendered pages
dumped from the console shadow buffer.

Serial excerpt (SeaBIOS, 128 MiB, emulated CD-ROM):

```
PC5 loader 0.2.0
pc5: framebuffer 1600x0900
pc5: medium is ISO9660, disk unit bytes 0800
pc5: kernel image verified
pc5: medium volume PC5
pc5: entering 32-bit kernel

PC5 KERNEL 0.2.0 (build …)
PC5-LOG storage drives=1 selected=0 atapi=1 model=v86 ATAPI CD-ROM
PC5-LOG storage_mounted volume=PC5 blocks=47 file=KERNEL.BIN bytes=36508 signature=ok
PC5-LOG chain_blocked_at=5 stage=PS5 firmware image supplied
PC5-LOG genuine_ps5_execution=no
PC5-BOOT-OK
```

## 5. Reproducibility

```
$ export BUILD_ID=$(git rev-parse HEAD) SOURCE_DATE_EPOCH=$(git log -1 --format=%ct HEAD)
$ make clean && make iso && sha256sum build/pc5.iso
0644cf66faf77cba1034123ac22a6451de340c1ba2bffc0b484bcc6a05e7837f  build/pc5.iso
$ make clean && make iso && sha256sum build/pc5.iso
0644cf66faf77cba1034123ac22a6451de340c1ba2bffc0b484bcc6a05e7837f  build/pc5.iso
```

Identical. The CI pipeline repeats this check and fails the job if the two
digests differ.

## 6. Release pipeline

| Level | State |
|---|---|
| Workflow YAML parses and every `run:` step passes `bash -n` | VERIFIED locally (13 steps, 2 embedded python blocks compile) |
| Pipeline steps executed locally in order | VERIFIED — two identical images, all suites green, assets staged |
| Publish script logic (checksum gate, verdict gate, create-then-update, `--clobber`, asset assertion) | VERIFIED against a stub `gh` (`tests/test_release.py`) |
| Workflow executed by GitHub Actions | **NOT VERIFIED** — the file cannot be pushed: the GitHub App token lacks the *workflows* permission |
| Release created by CI, three assets uploaded | **NOT VERIFIED** |
| Assets downloaded and checksum verified | **NOT VERIFIED** |

The activation push has now been attempted twice, on 2026-11-13, and rejected
both times with the same cause.

Against `main` `237725d`:

```
 ! [remote rejected] arena/0e2b454e-pc5 -> arena/0e2b454e-pc5 (refusing to allow a GitHub App to create or update workflow `.github/workflows/build.yml` without `workflows` permission)
```

Against `main` `a266936` (retry 2, after the permission was re-tested and found
unchanged; one decisive push, a pure rename with 0 insertions and 0 deletions):

```
 ! [remote rejected] arena/79f9165d-pc5 -> arena/79f9165d-pc5 (refusing to allow a GitHub App to create or update workflow `.github/workflows/build.yml` without `workflows` permission)
error: failed to push some refs to 'https://github.com/SpicyBadran/pc5.git'
```

Neither attempt reached the remote; the local commits were reverted and no
`.github/` directory exists on any remote branch. `gh api
repos/SpicyBadran/pc5/actions/workflows` reports `total_count: 0`, `gh run list`
is empty, and the `pc5-0.1.0` release carries **zero assets** — so no workflow
has ever been registered, no run has ever executed, and no release asset has
ever been published by CI.

The token is a GitHub App installation token (no OAuth scopes;
`X-Accepted-Github-Permissions: actions=read`). It holds `admin`/`push`, but the
installation lacks **Workflows: Read and write**; `/installation/repositories`
and `/actions/permissions` both return `403 Resource not accessible by
integration`.

**Nothing in this section may be upgraded until a real run exists.** See
`ci/README.md` for the owner action that unblocks it.

### 6.1 Workflow rehearsal, 2026-11-13 — a defect the pipeline would have hit

The workflow has never run on GitHub, so its steps were rehearsed locally in the
order the `build` job runs them, from a clean clone of `b97e302`. One step was
broken:

```
$ python3 -m pip install --disable-pip-version-check "unicorn==2.1.4"
error: externally-managed-environment
× This environment is externally managed
rc=1
```

The runner image marks its Python as externally managed (PEP 668), and the step
runs under `set -eu`, so this aborted the **whole build job** before a single
test ran — `make stubtest` was never reached. The workflow also uses no
`setup-python` action, so it inherits exactly that interpreter.

Fixed by installing into a virtual environment and putting it first on `PATH`,
which is what makes the Makefile's hard-coded `python3` resolve to it:

```
$ python3 -m venv .venv
$ .venv/bin/pip install --disable-pip-version-check "unicorn==2.1.4"
$ export PATH="$PWD/.venv/bin:$PATH"
$ python3 -c "import unicorn; print('unicorn', unicorn.__version__)"
unicorn 2.1.4
$ make stubtest
PASSED: 42/42 checks against the stub firmware
```

`.venv/` is now in `.gitignore`, next to the existing `node_modules/` entry, so
the rehearsal does not leave untracked files. The rest of the job was rehearsed
unchanged and behaves correctly: ISO build, `make verify` **VALID**,
`make unit` **35/0**, host suite **OK**, `make boot-test` **25/25**, two clean
builds byte-identical (`97c56ec5…016775`), and `release-assets` producing a
`build-info.json` whose verdict is `PASS` with artifact and record hashes equal.

This is a **local rehearsal, not a CI run**. It does not change the verdicts in
the table above: the workflow has still never been registered or executed by
GitHub, and no release asset exists.

Stage assets locally with:

```
$ make release-assets TAG=v0.2.0
pc5: release assets staged in build/release
0644cf66…  pc5.iso   pc5.iso.sha256   build-info.json
```

`build-info.json` then carries the `build` object (schema
`pc5.build-metadata/1`): version `0.2.0`, tag, commit, build id,
`SOURCE_DATE_EPOCH`, toolchain, the artifact hash re-computed from the file, and
each test result with the SHA-256 of the log it came from.

## 7. UEFI boot path — IMPLEMENTED — EXECUTED AGAINST STUB FIRMWARE (not OVMF)

Recorded on branch `arena/2f423f3b-pc5` (base `main` `4a7e2a7`). The label
changed in this milestone for one reason: the loader's own machine code now runs
to completion. It still has **not** run under real UEFI firmware, and nothing
below should be read as saying it has.

### 7.1 What was missing, and what closed it

Milestone A (previous section) proved the medium is structurally correct and
left the code "built but never run". No OVMF image, no QEMU, no UEFI-capable
VM, no KVM and no package manager exist in this environment, so firmware could
not be obtained. Floating-point-free workaround, used honestly:

`tests/uefi_stub_firmware.py` executes the real `build/uefi/BOOTX64.EFI` on the
Unicorn CPU emulator (2.1.4) with a firmware **model** around it:

* the PE32+ image is loaded the way firmware loads it — sections at their
  virtual addresses, every base relocation applied — at `0x400000`, deliberately
  **not** its `0x10000000` preferred base, so a missing relocation is fatal;
* Simple Text Output, Graphics Output, Boot Services, Loaded Image, Device Path
  and Simple File System are served at the byte offsets the specification
  assigns (`handle_protocol` 152, `locate_handle` 176, `get_memory_map` 56,
  `exit_boot_services` 232, …), each reached through a real x86-64 `ret` stub
  that the harness services from the **ms_abi** registers;
* the file system is two volumes: the FAT ESP parsed out of `build/efi.img`, and
  a second volume carrying `KERNEL.BIN`. That is the shape of a real El Torito
  UEFI boot, and it is why the loader must search for the volume instead of
  assuming its own — the search is exercised, not bypassed;
* execution starts at the image's own `_start` and follows the handover stub to
  the far jump into the kernel.

`tests/uefi_stubtest.py` asserts 42 properties of that run, and takes two
negative runs: a kernel with a corrupted signature must stop with `E4`, and a
machine with no kernel on any volume must stop with `E2`.

### 7.2 Recorded output

```
$ python3 -m venv /tmp/ucv && /tmp/ucv/bin/pip install "unicorn==2.1.4"
$ make stubtest
[PASS] the image is loaded away from its preferred base  (entry 0x401000)
[PASS] base relocations were applied  (93 fixups)
[PASS] the loader's own entry stub ran
[PASS] the loader printed its banner
[PASS] the loader reached its own trace output
[PASS] the loader searched every file-system volume
[PASS] it took the image device from LoadedImage, not a guess
[PASS] it found the volume holding KERNEL.BIN
[PASS] it read the kernel and checked the signature
[PASS] the framebuffer was discovered through GOP
[PASS] the memory map was read
[PASS] the loader left boot services
[PASS] boot info magic is 'PC5B'  (0x42354350)
[PASS] boot info protocol version is 1  (1)
[PASS] boot drive is the documented no-BIOS marker  (0xffffffff)
[PASS] E820 array address is the BIOS-loader address  (0x2000)
[PASS] E820 array has entries  (7)
[PASS] VBE mode info address is the BIOS-loader address  (0x2800)
[PASS] framebuffer is advertised
[PASS] no VGA font is claimed  (0x0)
[PASS] font bytes per char is zero when there is no font
[PASS] pvd_ok is honest (not reachable through the file protocol)
[PASS] kernel size matches the file on the medium  (36636 vs 36636)
[PASS] kernel LBA is not invented
[PASS] volume id is not invented
[PASS] iso sector count is not invented
[PASS] loader version string is recorded  (uefi-0.2.0)
[PASS] flags admit the UEFI origin and the unreported fields  (0x7)
[PASS] VBE block describes the real GOP geometry  (1024x768)
[PASS] VBE pitch matches the GOP scan line  (4096)
[PASS] VBE block is 32 bpp direct colour
[PASS] VBE block points at the framebuffer  (0x4000000)
[PASS] VBE character cell is 8x16 (what the kernel console assumes)
[PASS] conventional memory is reported as usable  ([{'base': 0, 'length': 651264, 'type': 1, 'acpi': 0}])
[PASS] loader data is NOT reported as usable  (0x20098b0)
[PASS] the handover targeted the kernel entry  ({'target': '0x10000', 'ebx': '0x4000', 'esp': '0x9f000'})
[PASS] the handover passed the boot-info address in EBX  ({'target': '0x10000', 'ebx': '0x4000', 'esp': '0x9f000'})
[PASS] the kernel image was copied to the handover address  (eb055043354b01fa)
[PASS] the handover GDT is the flat 3-entry table  ({'base': '0x402310', 'limit': 23})
[PASS] no harness-level problems were reported
[PASS] a kernel without the PC5K signature is refused
[PASS] no kernel on any volume stops the boot with E2

PASSED: 42/42 checks against the stub firmware
```

`make stubtest` exits 2 with an explanation when `unicorn` is not installed, so
a missing dependency reads as *cannot run* rather than as a failed or passing
boot. The machine-readable form of the same run is
`build/stubtest-report.json`.

### 7.3 The serial trace the loader itself produced

Captured from port `0x3F8`, i.e. written by the loader's own `outb`, not by the
harness:

```
PC5 UEFI loader uefi-0.2.0
PC5-UEFI: firmware_started
PC5-UEFI: firmware=PC5 stub firmware
PC5-UEFI: firmware_revision=131072
PC5-UEFI: display_width=1024
PC5-UEFI: display_height=768
PC5-UEFI: display_pitch=4096
PC5-UEFI: display_pixel_format=1
PC5-UEFI: image_device=media
PC5-UEFI: filesystem_volumes=2
PC5-UEFI: kernel_volume_opened
PC5-UEFI: medium_readme_unreadable
PC5-UEFI: kernel_bytes_read=36636
PC5-UEFI: kernel_protocol_version=1
PC5-UEFI: memory_map_entries=7
PC5-UEFI: memory_conventional_mib=2
PC5-UEFI: boot_info_ready
PC5-UEFI: kernel_loaded_at=0x00010000
PC5-UEFI: boot_services_exited
```

The `boot_info` block the kernel receives, as read back out of memory at
`0x4000` after the handover (`build/stubtest-report.json` → `data.boot_info`):

| Field | Value | Checked against |
|---|---|---|
| `magic`, `version` | `0x42354350`, `1` | `kernel/include/pc5.h` |
| `boot_drive` | `0xFFFFFFFF` | the documented no-BIOS marker |
| `e820_addr`, `e820_count` | `0x2000`, `7` | the BIOS loader's addresses |
| `vbe_mode_addr`, `vbe_ok` | `0x2800`, `1` | GOP mode reported by the stub |
| `font_addr`, `font_bytes_per_char` | `0`, `0` | no VGA font is claimed |
| `kernel_bytes` | `36636` | the size of `build/kernel.bin` on the medium |
| `kernel_lba`, `volume_id`, `iso_sectors`, `pvd_ok` | `0`, empty, `0`, `0` | not invented: the file protocol does not expose them |
| `flags` | `0x7` | bit 0 = UEFI origin, bits 1–2 = the two unreported fields |

The handover itself: target `0x10000` (decoded from the `EA imm32 imm16`
instruction, not assumed), `EBX = 0x4000`, the flat 3-entry GDT with limit 23
found by decoding the loader's own `lgdt`, and the kernel image copied to
`0x10000` with its `PC5K` header intact.

### 7.4 Two real loader bugs this found

Both were invisible to the structural tests and to review, and both would have
broken a real boot:

| Bug | Symptom in the harness | Fix |
|---|---|---|
| `find_kernel_volume()` tested the status of the *last probe*, so a volume without `\KERNEL.BIN` ended the search even when a later volume had it | a healthy two-volume machine stopped with `E2` | the loop is driven by the chosen root (`for (…; i < count && !chosen; i++)`), each volume is closed before moving on, and the chosen one is reopened for the caller |
| the kernel signature was compared at offset 0 | every real kernel was rejected with `E4` | `PC5_KERNEL_SIG_OFFSET = 2` in `kernel/include/pc5.h`, used by the loader; the file starts with the kernel's own short jump, then `PC5K`, then the protocol version |

Both are pinned by `LoaderRegressionTests` in `tests/test_uefi.py`, which fail
against the previous revision of `uefi_main.c` and pass now.

### 7.5 Reproducibility and the rest of the suite

```
$ export BUILD_ID=4a7e2a71d216d288830bdaa6cb8ebf7c9761eb2f
$ export SOURCE_DATE_EPOCH=1794562897
$ make clean && make iso
pc5: wrote build/pc5.iso (1570816 bytes, 767 sectors)
pc5: sha256 e00a2a0220a7553b8990bfd23f6b7b3c1a78b025931ad3e47713d962d1b329ae

$ sha256sum build/uefi/BOOTX64.EFI build/efi.img build/pc5.iso
c9b864a008bef1be4e65e55785697d5a19383488d732c39e90f36aa7e96783b6  build/uefi/BOOTX64.EFI
4c9c9f37bdace05d410f70c71f1189f013d751a6b9d30cccb4369e4f35356a39  build/efi.img
e00a2a0220a7553b8990bfd23f6b7b3c1a78b025931ad3e47713d962d1b329ae  build/pc5.iso
```

Run twice from `make clean` with those inputs: all three digests identical.

| Suite | Result |
|---|---|
| `make stubtest` (stub firmware, this milestone) | **42/42**, two negative runs included |
| `make verify` (structural ISO, unchanged) | **VALID**, sha256 `e00a2a02…b329ae` |
| `make unit` (kernel logic) | **35 checks, 0 failures** |
| host suite `python3 -m unittest discover -s tests` | **73 tests, OK** (was 66) |
| `make boot-test` (BIOS path, v86/SeaBIOS) | **25/25** |
| `tools/eficheck.py --require-relocs` | 22/22 |

### 7.6 What this does and does not establish

Established: the loader's own code executes from `_start` through the handover,
takes its protocols from the specification's slots, applies its relocations,
finds the kernel volume by searching rather than assuming, reads the kernel,
converts the memory map, fills the boot contract, leaves boot services and
performs the far jump into the kernel — and refuses a corrupt kernel or a
missing one with the documented codes.

Not established: that the image boots under real firmware. The stub has no
ACPI, no PCI, no interrupts, no timer and no NVRAM, does not enforce Secure
Boot, does not perform a real `ExitBootServices` memory-map transition, and
**emulates `lgdt`, the segment loads and the `lretq` itself** because the CPU
emulator does not implement them (the descriptor contents are checked, but the
CPU does not perform the switch). The kernel is stopped at its entry point: the
rest of it wants devices the harness does not provide. This is evidence about
the *loader*, not a UEFI compatibility result.

Closing the gap needs one recorded boot under OVMF or on hardware:

```
qemu-system-x86_64 -m 512 -serial stdio \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=copy-of-OVMF_VARS.fd \
  -cdrom build/pc5.iso -boot d
```

Until that exists, the label is IMPLEMENTED — EXECUTED AGAINST STUB FIRMWARE
(not OVMF), and the BIOS path remains the only verified boot path.

### 7.7 History — milestone A: the loader is built and structurally verified

Recorded on branch `arena/42c51afe-pc5` (base `main` `719432c`). Everything below
was produced by the command quoted next to it on that tree; the digests differ
now because the loader changed. This section is kept as the record of what that
milestone established — a medium that is structurally correct.

```
$ export BUILD_ID=719432cea7d1ebe7c748efefcac1cdfa6962439e
$ export SOURCE_DATE_EPOCH=1794561355
$ make clean && make iso
pc5: kernel image 36636 bytes (limit 585728)
22/22 checks passed                       <- tools/eficheck.py on BOOTX64.EFI
pc5: wrote build/efi.img (FAT12, 1474560 bytes, 22/2847 clusters used)
pc5: wrote build/pc5.iso (1570816 bytes, 767 sectors)
pc5: sha256 b66296c99abf30470a4fc2eb39777daeac174c2931e486e82aa8631965c10143

$ sha256sum build/uefi/BOOTX64.EFI build/efi.img build/pc5.iso
d941abdbb475e008c7706a6bb3c056985685ad00700a01d43054bf61e43e6b1c  build/uefi/BOOTX64.EFI
01e3004b1a5317e93a54d256da21a0dea9d52f63002ef289f20377b40734aaf5  build/efi.img
b66296c99abf30470a4fc2eb39777daeac174c2931e486e82aa8631965c10143  build/pc5.iso
```

Both builds were repeated from `make clean` with those inputs and all three
digests were identical, so the UEFI path is as reproducible as the BIOS path.
The build id is compiled into the kernel, so a build at the tip of this branch
has a different (equally valid) ISO digest; rebuilt with
`BUILD_ID=0cd396b…` and the same `SOURCE_DATE_EPOCH` it is
`b562caf35ba2da97cd529c967be46d3a85b6542d7f6721760332f732f9bbc275`, with the
same `efi.img` and `BOOTX64.EFI` digests above.

| Artifact | Size | Checks |
|---|---|---|
| `build/uefi/BOOTX64.EFI` | 9,771 bytes | 22/22 (`tools/eficheck.py --require-relocs --max-bytes 196608`) |
| `build/efi.img` | 1,474,560 bytes (FAT12, 2,880 sectors) | read back with `tools/mkefiimg.py --list` |
| `build/pc5.iso` | 1,570,816 bytes (767 sectors) | 53/53 (`make verify`) |

The ISO grew from 96,256 to 1,570,816 bytes because it now carries the 1.44 MB
ESP. Selected verifier lines that only exist because of this milestone:

```
[PASS] catalog has an initial entry
[PASS] initial entry is the 80x86 BIOS default  (0x0)
[PASS] the last section header is 0x91 (no further sections)  (145)
[PASS] no duplicate platform sections  ({0: 1, 239: 1})
[PASS] UEFI section entry is bootable  (0x88)
[PASS] UEFI section entry is no-emulation  (0)
[PASS] UEFI entry loads at segment 0  (0x0)
[PASS] UEFI image lies inside the ISO  (LBA 26 +2880 sectors)
[PASS] UEFI image has a valid FAT boot sector
[PASS] UEFI image is FAT12 or FAT16 (El Torito bootable form)  (2847 clusters -> FAT12)
[PASS] ESP root directory contains EFI/
[PASS] ESP contains EFI/BOOT
[PASS] ESP contains EFI/BOOT/BOOTX64.EFI
[PASS] BOOTX64.EFI content is readable through the FAT chain  (9771 bytes)
[PASS] BOOTX64.EFI machine is x86-64  (0x8664)
[PASS] BOOTX64.EFI is PE32+  (0x20b)
[PASS] BOOTX64.EFI subsystem is EFI application  (10)
```

Host suite: `python3 -m unittest discover -s tests -p 'test_*.py'` → **Ran 66
tests, OK** (was 32). The new `tests/test_uefi.py` holds the 34 new tests:
`FatImageTests` (12), `EfiCheckTests` (10), `UefiIsoTests` (7) and
`UefiSourceContractTests` (5). Kernel unit tests are unchanged at 35 checks, and
`make boot-test` still passes **25/25** on the BIOS path with the UEFI-capable
ISO, which is the regression test that matters here.

Bug this milestone started with, caught by the emulator rather than by review:

| Bug | Caught by | Fix |
|---|---|---|
| A 0x90 section header written in front of the BIOS default entry | `make boot-test` produced a 0-byte serial log: SeaBIOS reads catalog offset 32 as an entry, saw 0x90, and refused the medium | default entry back at +32, UEFI section header at +64, entry at +96; the verifier and tests now pin that layout |

## 8. What these results do and do not establish

Established: the image is a valid, reproducible ISO9660/El Torito medium; it
boots on an emulated PC; the kernel runs, inventories the machine, mounts its own
medium through its own ATAPI + ISO9660 code and reads a file back; six pages
render; the serial console works; provenance is generated from measurements and
refuses to describe a failing build as passing.

Not established: physical hardware boot, any driver on real silicon, UEFI
booting under real firmware (the loader now executes against a stub — §7 — but
has never run under OVMF or on hardware),
controller/network/audio support, genuine PS5 firmware execution, PS5 game
execution, a CI run, or a CI-published release. `docs/TESTING_STRATEGY.md`
defines the levels; `docs/PS5_COMPATIBILITY_STATUS.md` records what the machine
itself reports.

## 9. M0 architecture change — re-verification, 2026-11-15

The M0 change added the architecture documents, four ADRs, the milestone graph
and its validator, and corrected nine documented contradictions. It touched
documentation, one Makefile target and the test suite; it did not touch the
loader, the kernel or the ISO writer. This section records that the artefact and
every suite still behave, with the build inputs stated so the digest can be
reproduced.

```
$ make clean
$ make BUILD_ID=m0arch SOURCE_DATE_EPOCH=1763164800 iso
pc5: loader image 4096 bytes
pc5: kernel image 36500 bytes (limit 585728)
pc5: wrote build/pc5.iso (1570816 bytes, 767 sectors)
pc5: sha256 a665e27b5ef70ff2964e5103a02c09045c29306cd9273ade56602e3257e3e837
```

| Property | Value |
|---|---|
| path | `build/pc5.iso` |
| size | 1,570,816 bytes (767 × 2048) |
| SHA-256 | `a665e27b5ef70ff2964e5103a02c09045c29306cd9273ade56602e3257e3e837` |
| inputs | `BUILD_ID=m0arch`, `SOURCE_DATE_EPOCH=1763164800`, version 0.2.0 |
| loader image | 4,096 bytes |
| kernel image | 36,500 bytes (the default build at this commit is 36,508; the string length of the injected build id changes it by a few bytes) |
| UEFI application | 10,450 bytes |

| Suite | Result |
|---|---|
| `make verify` | **53/53** checks |
| two clean builds, same inputs | byte-identical ISO (`cmp` clean) |
| `make check` | **107** tests, OK — 24 tools + 41 UEFI + 10 release + 32 architecture |
| `make unit` | **35** kernel assertions, 0 failures |
| `make boot-test` | **25/25** checks on the v86/SeaBIOS emulated PC |
| `tests/uefi_stubtest.py` | **42/42** checks against the stub firmware |
| `make roadmap` | 12 milestones, tracks A/B separated, generated document in sync |

Two findings from this run are worth recording rather than hiding:

1. **A pre-existing flaky test was found.** `test_uefi_layout_is_deterministic`
   built the ESP twice without pinning `SOURCE_DATE_EPOCH`; FAT directory
   timestamps have two-second resolution, so a suite that straddled a tick
   produced two different hashes from identical content. The test now pins the
   epoch, which is what it was always trying to assert. This is a test defect,
   not a writer defect: the writer honours `SOURCE_DATE_EPOCH` correctly, which
   §5 already proved.
2. **The test count moved from 75 to 107** because the architecture tests were
   added, and the graph validator found nothing to fix in the shipped graph on
   its first run. A rising count is not progress by itself; the documentation
   contradictions corrected in the same change (see
   `docs/architecture/REPOSITORY_AUDIT_2026-11-15.md` §6) are the reason to
   believe the new tests are load-bearing.

Established by this section: the artefact and all five suites behave exactly as
before the architecture change, and the milestone graph validates.

Not established: anything new about hardware, firmware, drivers or PS5
compatibility. The M0 change is paper and checks, and paper has never booted a
machine.

## 10. M1d — ATA storage write path, 2026-11-15

The first storage write PC5 has ever performed. It is an emulated IDE disk, it
is opt-in, and it is verified from both ends: the kernel verifies what it reads
back, and the host verifies the bytes in the emulator's disk image.

Build inputs are stated so the digest is reproducible. Recording this evidence
found a build-system bug: `SOURCE_DATE_EPOCH` on the `make` command line was
silently ignored (make does not export command-line variables to the shell), so
the banner and `BUILDINFO.TXT` carried the wall clock. The Makefile now reads
`$(SOURCE_DATE_EPOCH)`, `tests/test_tools.py` asserts that, and two clean builds
with the same inputs are byte-identical.

```
$ make clean
$ make BUILD_ID=m1d SOURCE_DATE_EPOCH=1763164800 iso
pc5: loader image 4096 bytes
pc5: kernel image 41724 bytes (limit 585728)
pc5: wrote build/pc5.iso (1576960 bytes, 770 sectors)
pc5: sha256 22121ad90c1a745c09f590596672f91df0027c11e12c39a0664aecd36bc5b1b0
```

| Property | Value |
|---|---|
| image size | 1,576,960 bytes (770 × 2048) |
| image SHA-256 | `22121ad90c1a745c09f590596672f91df0027c11e12c39a0664aecd36bc5b1b0` |
| kernel image | 41,724 bytes (M0 build: 36,508; the write path, SHA-256 and self-test add ~5 KB) |
| UEFI application | 10,450 bytes (unchanged) |
| inputs | `BUILD_ID=m1d`, `SOURCE_DATE_EPOCH=1763164800`, version 0.2.0 |

| Suite | Result |
|---|---|
| `make verify` | **53/53** checks |
| two clean builds, same inputs | byte-identical ISO (`cmp` clean) |
| `make check` | **122** tests, OK — 25 tools + 41 UEFI + 10 release + 32 architecture + 14 storage |
| `make unit` | **61** checks, 0 failures (was 35: +14 block rules, +12 SHA-256) |
| `make boot-test` | **45/45** checks (was 25; +20 storage write, guard, and image checks) |
| `tests/uefi_stubtest.py` | **42/42** checks against the stub firmware |
| `make roadmap` | 13 milestones, tracks A/B separated, generated document in sync |

### 10.1 The test disk

| Property | Value |
|---|---|
| device | v86 IDE `hda`, primary disk; the CD-ROM stays the boot device and is `selected` |
| image | `build/test/wtest-hda.img`, 8 MiB, zero-filled by the boot test |
| attachment | `--hda-buffer` loads the file into an in-memory `ArrayBuffer`; `--hda-out` dumps it after the run |
| reported geometry | `v86 ATA HD sectors=16384` (8 MiB / 512) |
| scratch window | LBA 2048, 8 sectors, 4,096 bytes (1 MiB into the disk) |
| host disk safety | the harness only ever attaches that file; the input path is never written back, and no path under `/dev/` is referenced by any test |

### 10.2 What the kernel did

Captured from `build/test/serial-ui.log` (COM1 mirror of the boot log and the
serial console):

```
PC5-LOG storage drives=2 selected=1 atapi=1 model=v86 ATAPI CD-ROM
PC5-LOG storage_write_device dev=0 model=v86 ATA HD sectors=16384 writable=1
PC5-LOG storage_write_window lba=2048 sectors=8 bytes=4096
PC5-LOG storage_write_guard name=zero_count status=count ok=1
PC5-LOG storage_write_guard name=out_of_range_lba status=range ok=1
PC5-LOG storage_write_guard name=past_end status=range ok=1
PC5-LOG storage_write_guard name=lba_overflow status=range ok=1
PC5-LOG storage_write_guard name=invalid_device status=no-device ok=1
PC5-LOG storage_write_guard name=negative_device status=no-device ok=1
PC5-LOG storage_write_guard name=null_buffer status=alignment ok=1
PC5-LOG storage_write_guard name=scratch_window status=ok ok=1
PC5-LOG storage_write_guard name=atapi_write status=read-only ok=1
PC5-LOG storage_write_cdrom dev=1 selected=1 status=read-only refused=1
PC5-LOG storage_write_faults injected=0 reason=no-fault-injection
PC5-LOG storage_write=ok dev=0 lba=2048 sectors=8 bytes=4096 verify=ok sha256=80fdc12208ec4af1410125cbe146549182100a6b1e0cf67e382aa9aefdbafa3d
```

Reading it: the CD-ROM at index 1 is still the selected boot device (`atapi=1`)
and ISO9660 mounts from it; the write went to index 0, a different device,
chosen explicitly. The ATAPI write was refused with `read-only` — not by
falling back to the boot device, but because the request validator refuses an
ATAPI write before it looks at anything else (ADR-013 / `kernel/block.c`).

### 10.3 What the host verified

`make boot-test` reads `build/test/wtest-hda-after.img` — the emulator's 8 MiB
disk after the run — and checks:

| Check | Result |
|---|---|
| scratch bytes equal the expected 4 KiB pattern | pass |
| host SHA-256 of those bytes | `80fdc12208ec4af1410125cbe146549182100a6b1e0cf67e382aa9aefdbafa3d` |
| kernel SHA-256 of the read-back | identical to the host value |
| every byte outside the scratch window | still zero — nothing else was written |
| the input image file | unchanged (the runner writes only `--hda-out`) |
| image size | still 8,388,608 bytes |

The pattern is deterministic and public: `pattern[i] = (i * 7 + 0x5A) & 0xFF`.
It is generated identically in `kernel/storage_test.c` and
`tests/boot_test.py`, and its digest is also asserted against the kernel's
SHA-256 implementation in `tests/kernel_unit.c`.

### 10.4 Interface decision (ADR-013)

Two units, never conflated:

* `ata_read_blocks()` — 2048-byte optical/logical blocks on the **selected**
  boot device. Unchanged; ISO9660 has used it since M1a and still passes.
* `ata_read_sectors()` / `ata_write_sectors()` — 512-byte disk sectors on an
  **explicitly indexed** device, bounded LBA28 chunks, cache flush after write.

There is no write function without a device index, so no caller can reach the
boot medium implicitly. `kernel/block.c` holds every rule that can be decided
without port I/O, which is why the refusal table above is unit-tested as well as
observed.

### 10.4a A run that failed and did not reproduce

One `make boot-test` invocation, issued in a sequence immediately after the
host suite, recorded every check as failed, which is the signature of a run that
produced no serial output at all. It did not reproduce in five subsequent runs,
including one under the identical sequence of commands and load. Nothing in the
harness or the kernel was changed to make it pass, and no skip was converted into
a pass: the recorded result above is from a run in which all 45 checks executed.

### 10.5 What this does not establish

* **No physical disk has been written.** The only write target so far is v86's
  in-memory IDE disk.
* **No partition table, filesystem, installer or update path.** The write
  primitives exist; everything above them does not.
* **No fault injection.** The timeout and device-error branches are exercised by
  unit-testing the request rules, not by making a real device fail; the log says
  `injected=0` rather than implying otherwise.
* **No AHCI, NVMe, USB storage, DMA or interrupts.** Legacy PIO only, as before.
* **No claim that a writable disk changes the compatibility chain.** Stage 4
  stays PARTIAL: there is still no game installation.

## 11. M1e — MBR and GPT partition discovery, 2026-11-15

Read-only partition discovery, verified twice over: the kernel parses tables
whose exact bytes the test wrote, and the boot test asserts what the kernel
reported. Nothing is mounted, written or formatted.

Build inputs are pinned so the digest is reproducible:

```
$ make clean
$ make BUILD_ID=m1e SOURCE_DATE_EPOCH=1763164800
pc5: loader image 4096 bytes
pc5: kernel image 45660 bytes (limit 585728)
pc5: wrote build/pc5.iso (1581056 bytes, 772 sectors)
pc5: sha256 69e2cb1cda6d1fc9d065b25351ed117edfaeb94e41696d477c181379692af54e
```

Two clean builds with those inputs are byte-identical (`cmp` clean).

| Suite | Result |
|---|---|
| `make verify` | **53/53** |
| `make check` | **135** tests, OK (25 tools + 41 UEFI + 10 release + 32 architecture + 14 storage + 13 partitions) |
| `make unit` | **99** checks, 0 failures (was 61: +38 partition parser checks) |
| `make boot-test` | **53/53** (was 45: +8 partition checks) |
| `tests/uefi_stubtest.py` | **42/42** |
| `make roadmap` | 14 milestones, tracks A/B separated, in sync |

### 11.1 Fixtures the test builds

`tests/disk_images.py` writes the disk bytes itself, so the expected answer is
known exactly rather than inferred:

| Fixture | Contents |
|---|---|
| `part-hda-gpt.img` | 8 MiB (16,384 sectors); protective MBR, primary header at LBA 1, 128-entry array at LBA 2, backup array at LBA 16,351, backup header at LBA 16,383; partitions `PC5 ROOT` (LBA 2048, 2048 sectors) and `PC5 DATA` (LBA 4096, 2048 sectors) |
| `part-hda-gpt-corrupt.img` | the same image with one byte of the primary entry array flipped, so the header CRC still matches and the entry CRC does not |
| `wtest-hda.img` (unchanged) | zero-filled 8 MiB disk — the negative case: no table at all |

### 11.2 What the kernel reported

Valid GPT (`build/test/serial-part.log`):

```
PC5-LOG partitions device=0 scheme=gpt count=2 status=ok capacity=16384
PC5-LOG partition index=1 scheme=gpt type=0x00 guid=af3dc60f838472478e793d69d8477de4 first=2048 sectors=2048 name="PC5 ROOT"
PC5-LOG partition index=2 scheme=gpt type=0x00 guid=af3dc60f838472478e793d69d8477de4 first=4096 sectors=2048 name="PC5 DATA"
```

Damaged entry array (`build/test/serial-part-corrupt.log`):

```
PC5-LOG partitions device=0 scheme=gpt count=0 status=crc capacity=16384
```

The run continued to `PC5-BOOT-OK`; a damaged table is reported, not acted on.

Blank disk, via the serial `part` command (`build/test/serial-ui.log`):

```
part device=0 scheme=none status=signature count=0 capacity=16384
```

### 11.3 Unit-level coverage

`tests/kernel_unit.c` exercises the parsers with no device involved:

* CRC-32 check vector (`123456789` → `0xCBF43926`),
* MBR: missing `55 AA`, two primaries, overlap, past-end, extended container
  labelled `extended (not walked)`, protective MBR recognised as GPT,
* GPT: valid table with names, header CRC mismatch, entry CRC mismatch,
  partition past the device, overlapping entries, empty-but-valid table,
* GUID hex formatting and the status/scheme name tables.

### 11.4 What this does not establish

* **No filesystem is read or mounted.** A valid table says nothing about what is
  inside a partition.
* **Extended/logical MBR partitions are not walked** — they are labelled, not
  guessed.
* **No writes, no formatting, no installer.** The module contains no write call;
  `tests/test_partitions.py` asserts that mechanically.
* **No physical disk has been read for a partition table**, and no AHCI/NVMe
  device exists to read one from.
* **A harness quirk, fixed and recorded:** v86's default boot order booted
  nothing when `hda` carried a valid boot signature, which is exactly what a GPT
  protective MBR is. The runner now sets the boot order it was verified with, and
  the four cases (no disk, zero disk, valid GPT, damaged GPT) all reach the CD.
  This is a test-harness property, not a PC5 claim.

## 12. M1f — read-only FAT32, 2026-11-15

FAT32 mount, root enumeration, 8.3 lookup and file read, verified by three
independent computations of the same SHA-256 and by an unchanged disk image.

Build inputs are pinned so the digest is reproducible:

```
$ make clean
$ make BUILD_ID=m1f SOURCE_DATE_EPOCH=1763164800
pc5: kernel image 51828 bytes (limit 585728)
pc5: wrote build/pc5.iso (1587200 bytes, 775 sectors)
pc5: sha256 c3f265853fb72a636965ac29eec1efcec9bf37ed52ec1bf8ebf84555d8770798
```

Two clean builds with those inputs are byte-identical (`cmp` clean).

| Suite | Result |
|---|---|
| `make verify` | **53/53** |
| `make check` | **155** tests, OK (25 tools + 41 UEFI + 10 release + 32 architecture + 14 storage + 13 partitions + 20 FAT32) |
| `make unit` | **160** checks, 0 failures (was 99: +61 FAT32 parser checks) |
| `make boot-test` | **68/68** (was 53: +15 FAT32 checks) |
| `tests/uefi_stubtest.py` | **42/42** |
| `make roadmap` | 15 milestones, tracks A/B separated, in sync |

### 12.1 The fixture

`tests/fat32_images.py` builds a complete disk, so every byte the kernel parses
is written by the test:

| Property | Value |
|---|---|
| image | 35,143,680 bytes (2048 + 66,592 sectors) |
| partition | MBR type 0x0C at LBA 2048, 66,592 sectors |
| geometry | 512-byte sectors, 1 sector per cluster, 32 reserved, 2 FATs × 513 sectors |
| clusters | 65,534 — a spec-conforming FAT32 volume, not a small imitation |
| root | cluster 2, one sector, short names only |
| entries | label `PC5FS`, 6 files, 1 subdirectory, 1 deleted entry, 1 LFN record |

Files and their independent digests (all produced by the fixture generator):

| File | Size | SHA-256 |
|---|---|---|
| PC5TEST.TXT | 3,000 | `6c0d207f52de077d02b5aa25b77c50151351d6064f90d4fbdc54fab804c8cef8` |
| SMALL.BIN | 100 | `c87efd8ee1c6706fea28a51e83eb504b3c950ffba15a853d33b7dbf2a0e54432` |
| EXACT.BIN | 512 | `755929613d018a9cb308fd873b7b5b53078961f9be477d3650fa0710b350ad80` |
| EMPTY.TXT | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| MULTI.BIN | 5,000 | `56d00af17691ed4e2148e6c57cfd8907163048682773ad515e423129298935e3` |
| FRAG.BIN | 2,000 | `c551359b308fc808bbca9884fca00dd6c9a82cdd05d9e594431c0e4326ff9e81` |

FRAG.BIN's chain is deliberately out of order (four non-consecutive clusters),
MULTI.BIN spans ten clusters, EXACT.BIN is exactly one cluster and SMALL.BIN ends
part-way through one.

### 12.2 Three independent digests

| Source | How it computes | Result |
|---|---|---|
| fixture generator | the bytes the files were built from | reference |
| `tests/fat32_images.py: read_file()` | a second FAT32 implementation in Python | equal for all six files |
| `kernel/fat32.c` via `tests/fat32_check.c` | the kernel module compiled as a host program | equal for all six files |
| `kernel/fat32.c` inside the emulated PC | read through the real ATA path | equal (below) |

The kernel's own output in the emulator (`build/test/serial-fat32.log`):

```
PC5-LOG fat32 device=0 partition=1 status=ok label="PC5FS" fs=FAT32 clusters=65534 spc=1 base=2048
fat32 entry name=PC5TEST.TXT size=3000 attr=0x20
...
fat32 list count=7 status=ok
fat32 read name=PC5TEST.TXT size=3000 status=ok sha256=6c0d207f52de077d02b5aa25b77c50151351d6064f90d4fbdc54fab804c8cef8
fat32 read name=MULTI.BIN size=5000 status=ok sha256=56d00af17691ed4e2148e6c57cfd8907163048682773ad515e423129298935e3
fat32 read name=FRAG.BIN size=2000 status=ok sha256=c551359b308fc808bbca9884fca00dd6c9a82cdd05d9e594431c0e4326ff9e81
fat32 read name=SUBDIR size=0 status=directory sha256=
fat32 read name=NOPE.TXT size=0 status=no-file sha256=
```

`fat32 list count=7` is the volume label, the deleted entry and the long-name
record being ignored: seven real entries (six files plus one directory).

### 12.3 Refusals

Host-level checks (in `make check`, against the same 35 MB image and against
synthetic volumes in `make unit`):

| Case | Status |
|---|---|
| missing `55 AA` | `signature` |
| 1,024-byte sectors | `bpb` |
| sectors/cluster not a power of two | `bpb` |
| zero reserved sectors, five FATs, bad media byte | `bpb` |
| FSInfo or backup boot sector outside the reserved area | `bpb` |
| FAT version 1.0 | `not-fat32` |
| FAT16 root-entry count or 16-bit FAT size | `not-fat32` |
| volume larger than its partition, root cluster outside the volume | `geometry` |
| FAT too small to describe the clusters it claims | `geometry` |
| free / bad / reserved / out-of-range cluster in a chain | `free-cluster`, `bad-cluster`, `reserved-cluster`, `cluster` |
| cyclic chain (Floyd detection) | `loop` |
| chain shorter than the declared file size | `chain` |
| directory entry with a size but no cluster | `cluster` |
| file larger than the caller's buffer | `space` |
| unreadable sector | `io` |
| long/invalid 8.3 name | `name` |

A volume whose BPB declares FAT16 is refused in the emulator as
`PC5-LOG fat32 status=not-fat32` and the boot continues to `PC5-BOOT-OK`
(`build/test/serial-fat32-bad.log`).

### 12.4 Write protection

* `kernel/fat32.c` contains no write call, no `outb`, no `ata_write*`, no
  `ata_writable_device`; `tests/test_fat32.py` asserts that mechanically.
* The emulator's disk image is dumped after the run and compared byte-for-byte
  with the input: identical, and the input file itself is unchanged.
* The host driver re-reads the image file after reading every file and compares
  its SHA-256 with the pre-run value: identical.

### 12.5 What this does not establish

* **No filesystem writing.** No file is created, deleted, resized or renamed.
* **No long file names.** LFN records are skipped by design; a lookup by long
  name returns `no-file`, not a guess.
* **No subdirectory traversal.** Directories are listed and reading one returns
  `directory`.
* **Only 512-byte sectors**, only mirrored or explicitly selected FATs.
* **No physical hardware.** The disk is v86's in-memory IDE device.
* **No package or game parsing.** A readable file is not an installable title.

## 13. M1g — safe FAT32 write, 2026-11-15

One write operation exists: create a new 8.3 file in the root directory of an
explicitly mounted FAT32 volume. It is proved the way M1f was proved — by an
implementation that shares no code with the kernel — plus a fault injector that
forces the operation to fail at each stage and then asks the independent reader
what state the volume is in.

Build inputs are pinned, and two clean builds are byte-identical:

```
$ make clean
$ make BUILD_ID=m1g SOURCE_DATE_EPOCH=1763164800
pc5: kernel image 57692 bytes (limit 585728)
pc5: wrote build/pc5.iso (1593344 bytes, 778 sectors)

$ sha256sum build/kernel.bin build/pc5.iso
d4344906d8db1e1fdfe429e8245f8c50da75dbd9bb78132e3e54534eb326e965  build/kernel.bin
12f4f4e96fe437c025a4a3132a25e29c57e069edf77db4762afd4e3104798b02  build/pc5.iso
```

| Suite | Result |
|---|---|
| `make verify` | **53/53** |
| `make unit` | **160** checks, 0 failures |
| `make check` | **192** tests, OK (25 tools + 41 UEFI + 10 release + 32 architecture + 14 storage + 13 partitions + 20 FAT32 read + 37 FAT32 write) |
| `make boot-test` | **80/80** (was 68: +12 write checks) |
| `tests/uefi_stubtest.py` | **42/42** |
| `make roadmap` | 16 milestones, in sync |

### 13.1 What the write path does

`fat32_create_file(io, vol, name, data, size, &report)`:

1. refuses an invalid name, an oversize file, a volume it did not mount, or a
   sector source without a write callback (`read-only`);
2. scans the root directory for the name (`exists`) and for the first free slot
   (`dir-full` if there is none — as M1g behaved; since M1i the directory is
   grown by one cluster instead, §15.3) — a deleted slot is reused, unless it sits
   directly behind a long-name record, whose orphaned fragments would otherwise
   attach to the new name;
3. counts free clusters by reading the FAT (`full` if fewer than needed) and
   chooses the whole chain in memory before writing anything;
4. writes content → FAT copies → FSInfo → flush → directory entry → flush →
   read-back, reporting the stage it reached on failure.

The kernel's own read-back is the last step, and `put` prints
`verified=`, `mirrors=`, `flush=` and the stage so no partial operation can be
mistaken for success.

### 13.2 The created file, confirmed three ways

Console session in the emulator (`build/test/serial-fat32-write.log`):

```
fat32 create name=NEW.TXT size=9 status=ok stage=8 clusters=1 free=65509 mirrors=1 flush=1 verified=1 writes=6
fat32 read name=NEW.TXT size=9 status=ok sha256=18efaedb92997ed4c9c2b7f13c5d14d1398f4cbde3e166302af07c6908bc7288
fat32 list count=8 status=ok
fat32 create name=NEW.TXT size=5 status=exists stage=1 clusters=0 ... writes=0
fat32 create name=PC5TEST.TXT size=1 status=exists stage=1 ... writes=0
fat32 create name=BAD*.TXT size=1 status=name stage=0 ... writes=0
```

| Source | Result |
|---|---|
| payload the test asked for | `hello-m1g`, 9 bytes |
| SHA-256 the kernel computed in the emulator | `18efaedb…c7288` |
| SHA-256 of the payload on the host | `18efaedb…c7288` |
| bytes the independent reader extracts from the dumped disk | `hello-m1g` |

The dumped disk was then inspected structurally:

| Check | Result |
|---|---|
| every pre-existing file reads back byte for byte | yes (six files) |
| FAT copy 1 == FAT copy 2 (raw bytes) | yes |
| orphaned clusters (allocated, unreferenced) | none |
| changed sectors | 6: `2049` (FSInfo), `2055` (FSInfo backup), `2080`, `2593` (the two FATs), `3106` (root directory), `3130` (the new data cluster) |
| input image modified by the host | no |

### 13.3 Creation matrix (host driver + independent reader)

Every row starts from a fresh image and is verified with `read_file()`,
`cluster_chain()`, `fat_copies()`, `fsinfo_fields()`, `orphan_clusters()` and
`changed_sectors()`:

| Case | Result |
|---|---|
| 9-byte text file | 1 cluster, chain `[26]`, digest matches |
| zero-length file | 0 clusters, first cluster 0, entry size 0 |
| 100 bytes (sub-cluster) | one cluster, file size stays 100 |
| 512 bytes (exact cluster) | one cluster, no extra cluster allocated |
| 3,000 bytes (6 clusters) | contiguous chain, every FAT link checked |
| 2,000 bytes on a volume with holes | chain `[26, 28, 30, 32]` — fragmented, content correct |
| volume with exactly 2 free clusters | succeeds, volume ends 0 free |
| volume with 1 free cluster, 2 needed | `full`, image byte-identical |
| volume with no free clusters | `full`, zero writes |
| existing name (`PC5TEST.TXT`, `pc5test.txt`) | `exists`, zero writes |
| full root directory (16/16 slots live) | `dir-full`, zero writes — **M1g's behaviour; M1i grows it instead (§15.3)** |
| invalid names (`BAD*NAME.TXT`, `TOOLONGNAME.TXT`, `A.BBBB`, `name?.txt`, empty) | `name`, zero writes |
| read-only source (no write callback) | `read-only`, image byte-identical |
| deleted slot available | reused (`dir_offset=32`), the old name stays invisible |
| deleted slot behind a long-name record | skipped; entry goes to the end marker instead |
| FAT falsely marks the root directory's cluster free | cluster 2 is never allocated; every name survives |
| 65 KiB+ request beyond `FAT32_MAX_CHAIN` | `too-big`, zero writes |
| two identical runs | byte-identical images (fixed timestamps, deterministic order) |

FSInfo behaviour is tested separately: the fixture *lies* (it claims 65,504 free
clusters when 65,510 are free) and the write ignores that claim, recomputes from
the FAT and writes 65,509 after creating one cluster — to both the FSInfo block
and its backup. A block whose counters are impossible (`0xFFFFFFFF`) or whose
signature is broken is reported `fsinfo_valid=0`, is **not** rewritten, and the
create still succeeds.

### 13.4 Fault injection

`tests/fat32_write_check.c` fails or corrupts the N-th sector write, or the
flush, and the Python verifier classifies the resulting image. 700-byte file =
2 data clusters; the successful run issues 9 writes in this order: data(0,1),
FAT copies(2,3,4,5), FSInfo(6), FSInfo backup(7), directory entry(8).

| Injection | Status | Stage reached | Writes applied | File exists | Orphans | Mirrors equal | Originals intact |
|---|---|---|---|---|---|---|---|
| none | ok | 8 (done) | 9 | yes | none | yes | yes |
| `--fail-write 0` (first data write) | io | 3 (data) | 0 | no | none | yes | yes |
| `--fail-write 2` (first FAT copy) | io | 4 (FAT) | 2 | no | none | yes | yes |
| `--fail-write 3` (between FAT copies) | io | 4 (FAT) | 3 | no | `[26]` | **no** | yes |
| `--fail-write 6` (FSInfo) | io | 5 (FSInfo) | 6 | no | `[26, 27]` | yes | yes |
| `--fail-write 8` (directory entry) | io | 7 (directory) | 8 | no | `[26, 27]` | yes | yes |
| `--corrupt-write 0` (media corrupts the data) | **verify** | 7 (directory) | 9 | yes, wrong bytes | none | yes | yes |
| `--fail-flush` | **flush** | 6 (flush) | 8 | no | `[26, 27]` | yes | yes |
| `--no-flush` (source cannot flush) | ok, `flush=0` | 8 (done) | 9 | yes | none | yes | yes |

What this establishes, stated without rounding up:

* **No injection ever damaged a pre-existing file.** Every original file was
  byte-identical in every run.
* **The two worst outcomes are a leaked chain and a divergent mirror**, and both
  are reported rather than hidden. A leaked chain costs space and nothing else;
  a divergent mirror is exactly what the per-copy re-read exists to catch
  (`status=corrupt` if the kernel survives to that check).
* **A corrupted write is caught**, because the read-back goes through the sector
  source: the file exists with wrong bytes but the operation returns `verify`,
  never `ok`.
* **A failed flush is an error**, even though the directory entry was already
  written; the caller is told the durability of that entry is unknown.
* **Not established:** behaviour against real power loss (no cache-flush ordering
  beyond ATA FLUSH CACHE), torn writes inside a sector, or repair of a volume
  that is already inconsistent. There is no journal and no fsck.

### 13.5 What this does not establish

* No overwrite, delete, resize, rename, subdirectory write or directory
  expansion — each is refused, not approximated.
* No long file names, no 4K sectors, no multiple concurrent writers.
* No physical disk was written; every disk is v86's in-memory IDE device, filled
  from a generated image and dumped afterwards.
* No installer, no bootloader installation, and nothing about PS5 game or
  firmware compatibility follows from a writable FAT32 file.

## 14. M1h — hybrid MBR, 2026-11-15

The image now boots two ways from one file: El Torito (CD/DVD, USB presented as
CD-ROM) and a hard-disk MBR in the ISO9660 system area (USB presented as HDD).
This section records what was measured, with the commands, and — separately —
what was not.

Build inputs are pinned, and two clean builds are byte-identical:

```
$ make clean && make BUILD_ID=m1h SOURCE_DATE_EPOCH=1763164800
pc5: MBR bootstrap 440 bytes
pc5: loader image 4096 bytes
pc5: kernel image 57692 bytes (limit 585728)
pc5: wrote build/pc5.iso (1593344 bytes, 778 sectors)
pc5: sha256 1ff03e3fd1eafd8906f7c1b785006e0d0812715249fb561456966f880e1b3ce7

$ sha256sum build/loader.bin build/mbr.bin build/kernel.bin build/efi.img build/pc5.iso
f121b34a7ebc0e7dd6855e429496b7f3bbb636ac4249fad62542b8c6503da1bb  build/loader.bin   (4096 bytes)
d07ec2ba3aeb06b200ed8e918d9a14dfa18b730477f03cbd632dec06ab19d107  build/mbr.bin      (440 bytes)
cd00af6bff84979ca300074c157045788b75df0e21046bed39ec6d4d69029816  build/kernel.bin   (57692 bytes)
568c8d2966c67ad2b125b20eb15b714007c230fb67658b12301d8272dd52c8fc  build/efi.img      (1474560 bytes)
1ff03e3fd1eafd8906f7c1b785006e0d0812715249fb561456966f880e1b3ce7  build/pc5.iso      (1593344 bytes)
```

| Property | M1g (`12f4f4e9…`) | M1h (`1ff03e3f…`) |
|---|---|---|
| image bytes / ISO blocks | 1,593,344 / 778 | 1,593,344 / 778 (unchanged) |
| kernel image | 57,692 B, `d4344906…` | 57,692 B, `cd00af6b…` |
| stage-1 loader | 4,096 B | 4,096 B (identical source, identical size) |
| El Torito entries | BIOS `0x00` + UEFI `0xEF` | unchanged |
| kernel LBA / ESP | 747 / FAT12 1,474,560 B | 747 / unchanged |
| system area LBA 0 | all zero | **hybrid MBR** |

The kernel changed hash only because `PC5_BUILD_ID` is now `m1h` (the build id is
compiled into the version string); no kernel source file was touched, and both
builds are 57,692 bytes. The whole difference between the two images is the first
512 bytes.

| Suite | Result |
|---|---|
| `make verify` | **75/75** (was 53: +18 MBR checks, +4 build-info cross-checks) |
| `make unit` | **160** checks, 0 failures |
| `make check` | **229** tests, OK (25 tools + 41 UEFI + 10 release + 32 architecture + 14 storage + 13 partitions + 20 FAT32 read + 37 FAT32 write + **37 hybrid MBR**) |
| `make boot-test` | **87/87** (was 80: +7 MBR checks, one of them a negative control) |
| `tests/uefi_stubtest.py` | **42/42** |
| `make roadmap` | 18 milestones, in sync |

### 14.1 The layout, before and after

```
LBA 0   system area, sector 0 .. 0x1FE  MBR bootstrap (440 B), signature,
        reserved, partition table, 55AA         <- added by M1h
        LBA 0, bytes 512..32767: zero          <- unchanged
LBA 16  primary volume descriptor              unchanged
LBA 17  boot record volume descriptor          unchanged (catalog LBA at byte 71)
LBA 18  volume descriptor set terminator       unchanged
LBA 19  path table L (2 blocks)                unchanged
LBA 21  path table M (2 blocks)                unchanged
LBA 23  El Torito boot catalog                 unchanged
LBA 24  BIOS boot image, 4096 B (2 blocks)     unchanged (loader, descriptor at 0xE00)
LBA 26  UEFI image, 1,474,560 B (720 blocks)   unchanged (FAT12 ESP + BOOTX64.EFI)
LBA 746 root directory                         unchanged
LBA 747 KERNEL.BIN (57,692 B)
LBA 776 README.TXT, BUILDINFO.TXT
total   778 blocks = 3,112 x 512-byte sectors
```

### 14.2 The MBR, byte for byte

`dd if=build/pc5.iso bs=1 skip=440 count=72 | od -Ax -tx1z`:

```
000000 30 35 43 50 00 00 80 00 01 00 17 31 19 00 00 00  >05CP.......1....<
000010 00 00 28 0c 00 00 00 00 00 00 00 00 00 00 00 00  >..(.............<
000040 00 00 00 00 00 00 55 aa                          >......U.<
```

| Field | Offset | Value | Meaning |
|---|---|---|---|
| disk signature | 440 | `0x50433530` (`05CP`) | fixed constant, so builds stay reproducible |
| reserved | 444 | `00 00` | must be zero |
| boot indicator | 446 | `0x80` | bootable |
| CHS start | 447 | `00 01 00` | head 0, sector 1, cylinder 0 = LBA 0 |
| partition type | 450 | `0x17` | hidden; never auto-mounted (ADR-014) |
| CHS end | 451 | `31 19 00` | head 49, sector 25, cylinder 0 = LBA 3111 |
| start LBA | 454 | `0x00000000` | 0 |
| sector count | 458 | `0x00000C28` | 3,112 = every 512-byte sector of the file |
| entries 2–4 | 462–509 | all zero | no further partitions |
| signature | 510 | `55 AA` | |

Decoded by a throwaway script that shares no code with the repository
(`int.from_bytes` on the raw dump, `(c*255 + h)*63 + s - 1` for CHS):

```
image bytes 1593344 = 512-sector count 3112 exact: True
55AA: 55aa sig32: 0x50433530
flag 0x80 type 0x17 start 0 count 3112 end inclusive 3111 fits: True
chs start -> 0  chs end -> 3111
entries 2-4 all zero: True
patch lba*512 = 49152 (LBA 96 = ISO block 24)  patch sectors: 8
bytes at patch LBA: fa31c0   (= the stage-1 loader's first instructions)
```

The two patch words the bootstrap reads at `0x7DB0`/`0x7DB4` (file offsets
`0x1B0`/`0x1B4`) hold the El Torito boot image's LBA in 512-byte units (96) and
its length in 512-byte sectors (8 = the 4096-byte loader), so the MBR and El
Torito entries cannot disagree: the builder writes both from one computation and
`make verify` re-checks the equality.

### 14.3 El Torito compatibility

Nothing in the catalog moved. `make verify` passes the same 53 checks it did in
M1g plus the MBR ones, including the catalog checksum, the initial entry at
offset 32 (`0x88`, no-emulation, segment `0x7C0`, 8 virtual sectors), the UEFI
section header `0x91`, the ESP's FAT/PE32+ structure and the boot descriptor's
CRC32 (`stored dc97e002 computed dc97e002`). Files: `KERNEL.BIN`, `README.TXT`,
`BUILDINFO.TXT` all verify by SHA-256 against `build-info.json`.

### 14.4 Boot evidence, including a control

`tests/v86_runner.mjs` can detach the CD (`--no-cdrom`); the new boot-test section
therefore proves the MBR on its own rather than proving that the firmware found
some other way to boot:

```
$ node tests/v86_runner.mjs --cdrom build/pc5.iso --no-cdrom \
      --hda-buffer build/test/hybrid-hda.img --boot-order 2 --seconds 14
pc5: medium is ISO9660, disk unit bytes 0200        <- 512-byte units
PC5 loader 0.2.0
pc5: kernel image verified
PC5 KERNEL 0.2.0 (build m1h, ...)
PC5-BOOT-OK
```

| Check | Result |
|---|---|
| MBR path produced a serial log | PASS (2,628 bytes) |
| the MBR bootstrap handed control to the stage-1 loader | PASS |
| the loader read the volume through the 512-byte-unit probe | PASS (`disk unit bytes 0200`) |
| the loader verified the kernel image on the MBR path | PASS |
| the kernel started from the MBR path | PASS (`PC5-BOOT-OK`) |
| CD and MBR boots reach the same kernel build | PASS (`version=0.2.0 build=m1h`) |
| **control**: the same disk with sector 0 blanked does not boot | PASS (0 bytes of output) |

The CD path is unchanged: `disk unit bytes 0800`, `storage_mounted volume=PC5
blocks=778 file=KERNEL.BIN bytes=57692 signature=ok`, `PC5-BOOT-OK`. The only
differences between the two logs are the ones the medium implies (unit size, and
that the disk path has two drives and no ATAPI volume to mount).

Appendix: the first version of the control kept the CD attached, and it booted —
the firmware simply fell back to El Torito after the disk failed. That is correct
firmware behaviour and a wrong test, which is why the control now detaches the
CD before it claims anything.

### 14.5 What can now be rejected

`tests/test_hybrid.py` (37 tests) does not trust the generator: it rebuilds the
expected table from the image's own geometry, assembles `boot/mbr.S` with the
same `as`/`ld` invocation the Makefile uses and inspects the bytes, and then
corrupts the image one field at a time, requiring `tools/verify_iso.py` to fail
with the *named* check each time:

| Corruption | Rejected by |
|---|---|
| `55 AA` zeroed | `hybrid MBR signature 55AA at offset 510` |
| whole system area zeroed | `hybrid MBR signature 55AA at offset 510` |
| 440 code bytes zeroed | `hybrid MBR carries a bootstrap` |
| disk signature zeroed | `hybrid MBR disk signature is set` |
| reserved bytes non-zero | `hybrid MBR reserved bytes are zero` |
| boot indicator cleared | `hybrid MBR boot indicator is 0x80` |
| type changed to `0x83` | `hybrid MBR partition type is 0x17` |
| start LBA moved to 1 | `hybrid MBR partition covers every sector of the image` |
| length = whole + 1 | `hybrid MBR partition does not run past the end of the image` |
| length = whole − 1 | `hybrid MBR partition ends on the last image sector` |
| stale CHS start | `hybrid MBR CHS start address is consistent with its LBA` |
| a second entry | `hybrid MBR declares exactly one partition` |
| a second, overlapping entry | `hybrid MBR declares exactly one partition` |
| patch LBA pointing elsewhere | `hybrid MBR names the El Torito boot image LBA` |
| patch length shorter | `hybrid MBR names the El Torito boot image length` |
| patch LBA past the partition | `hybrid MBR boot image lies inside the partition` |
| image truncated by one sector | invalid (volume size vs file size, and the table) |

Generator-side refusals are tested too: a 439- or 441-byte bootstrap, a build with
no bootstrap at all, an empty partition, and CHS saturation above 1023 cylinders
(`0xFE 0xFF 0xFF`, never a wrapped CHS value).

The bootstrap's own contract is asserted on the assembled bytes: exactly 440
bytes, an INT 13h extended read, a far jump to `0000:7C00`, both patch words zero
in the source, the two absolute reads of `0x7DB0`/`0x7DB4`, the relay copy to
`0000:0500` with a length that keeps it inside one sector, and no `55 AA` anywhere
in the source (the builder owns the signature).

### 14.6 What this does not establish

* **No physical boot.** The medium has never been on real hardware. SeaBIOS
  accepting it in an emulator is evidence about the image, not about a
  particular machine's USB-HDD mode, its USB enumeration or its BIOS quirks.
* **No independent partition tool.** `fdisk`, `sfdisk`, `parted`, `file` and
  `gdisk` are **not installed** in this environment (checked with `command -v`),
  so the table was not cross-checked by util-linux/parted. Verification here is
  the repository's own byte-level verifier, a throwaway decoder that shares no
  code with it, and SeaBIOS — which parsed the MBR and booted from it.
* **No UEFI firmware.** The ESP is unchanged and the stub test still passes, but
  no OVMF or hardware UEFI run happened (M1c stays blocked).
* **No kernel change and no new storage feature.** The kernel still reads ISO9660
  and FAT32 and writes one FAT32 create; nothing about partitions, AHCI/NVMe,
  overwrite/delete/rename or directory creation changed.
* **One partition type.** Only type `0x17` was produced and tested. A BIOS that
  insists on `0x0C`/`0x83`/`0x07` before it will boot a USB disk is not covered.
* **INT 13h extensions are required.** The bootstrap uses the extended read
  (`AH=0x42`, disk address packet). A pre-1994 BIOS without extensions cannot use
  the MBR path; it can still boot the same medium through El Torito. The LBA is a
  full 64-bit field in the packet but only the low 32 bits are exercised (the
  image is 3,112 sectors).
* **The bootstrap trusts the read.** It checks the carry flag and retries three
  times with a drive reset, but it does not verify the boot image itself — the
  stage-1 loader verifies the kernel (`pc5: kernel image verified`), and a wrong
  loader fails visibly on the serial console rather than silently.

## 15. M1i — FAT32 write expansion: overwrite, directories, paths, 2026-11-15

M1g created a file. M1i makes the filesystem usable for the installer that M2
needs: an existing file can be **replaced in place**, a **directory** can be
created and used immediately, and every entry point takes a **path** instead of a
bare root name. The priority order the milestone was held to is data integrity →
correctness → fault tolerance → coverage → performance; this section records what
that produced and, in §15.9, what it does not.

Build inputs are pinned, and two clean builds are byte-identical:

```
$ make clean && make BUILD_ID=m1i SOURCE_DATE_EPOCH=1763164800
pc5: MBR bootstrap 440 bytes
pc5: loader image 4096 bytes
pc5: kernel image 66828 bytes (limit 585728)
pc5: wrote build/pc5.iso (1601536 bytes, 782 sectors)
pc5: sha256 5252e9b23427d7def196312352385da02b6d4b351854651e79da59ace5fc7ce2

$ sha256sum build/loader.bin build/mbr.bin build/kernel.bin build/efi.img build/pc5.iso
f121b34a7ebc0e7dd6855e429496b7f3bbb636ac4249fad62542b8c6503da1bb  build/loader.bin   (4096 bytes)
d07ec2ba3aeb06b200ed8e918d9a14dfa18b730477f03cbd632dec06ab19d107  build/mbr.bin      (440 bytes)
10bc3b7bac23676d80be8f8b8f48bc802fa97b9754578904622a766b97e44ea7  build/kernel.bin   (66828 bytes)
568c8d2966c67ad2b125b20eb15b714007c230fb67658b12301d8272dd52c8fc  build/efi.img      (1474560 bytes)
5252e9b23427d7def196312352385da02b6d4b351854651e79da59ace5fc7ce2  build/pc5.iso      (1601536 bytes)
```

| Artifact | M1h (`1ff03e3f…`) | M1i (`5252e9b2…`) | Why it changed |
|---|---|---|---|
| `pc5.iso` | 1,593,344 B / 778 blocks | 1,601,536 B / 782 blocks | the kernel grew by 9,136 B, so the kernel extent and everything after it moved |
| `kernel.bin` | 57,692 B `cd00af6b…` | 66,828 B `10bc3b7b…` | new code (overwrite, mkdir, paths) **and** `PC5_BUILD_ID` is now `m1i` |
| `loader.bin` | 4,096 B `f121b34a…` | 4,096 B `f121b34a…` | untouched source |
| `mbr.bin` | 440 B `d07ec2ba…` | 440 B `d07ec2ba…` | untouched source |
| `efi.img` | 1,474,560 B `568c8d29…` | 1,474,560 B `568c8d29…` | untouched source |

The two builds were made from a clean tree with the same `BUILD_ID`,
`SOURCE_DATE_EPOCH`, toolchain and inputs, and every one of the five artefacts
hashed identically in both. The kernel hash differs from M1h for exactly two
reasons, both named above; the loader, MBR and ESP did not change **at all**, so
their hashes and sizes are the M1h values. The ISO's size change is arithmetic:
+9,136 bytes of kernel = +2 ISO blocks (the kernel extent is block-aligned), and
the hybrid MBR's partition length, CHS end and patch words are recomputed from
the new layout, which is why sector 0 differs while `mbr.bin` does not.

| Suite | Result | Was |
|---|---|---|
| `make verify` | **75/75** | 75 (no layout change beyond the kernel extent) |
| `make unit` | **160** checks, 0 failures | 160 |
| `make check` | **280** tests, OK (25 tools + 41 UEFI + 10 release + 32 architecture + 14 storage + 13 partitions + 20 FAT32 read + 38 FAT32 write + **50 FAT32 expand** + 37 hybrid MBR) | 229 |
| `make boot-test` | **116/116** checks | 87 (+29 M1i) |
| `tests/uefi_stubtest.py` | **42/42** | 42 |
| `make roadmap` | **18** milestones, in sync | 17 (+M1i) |

### 15.1 What the three operations do, precisely

| | create (`put`) | overwrite (`write`) | mkdir (`mkdir`) |
|---|---|---|---|
| same directory slot | new slot | **yes**, the existing one | new slot |
| new clusters before the name exists | yes | yes (the replacement) | yes (the directory) |
| old data freed | — | after the switch, flush and re-read | — |
| needs space for both versions | no | **yes** | no |
| sector writes, base fixture | 6 | 26 (1,500 B over a 6-cluster chain) | 7 (12 when the parent is full) |
| refusal statuses exercised | exists, full, name, too-big, read-only, io, verify, flush | no-file, directory, read-only, corrupt, full, too-big, name, io, verify, flush | exists, no-file, not-dir, name, full, read-only, io |

Paths are `COMPONENT { "/" COMPONENT }`, at most 8 components and 63 characters,
each component an 8.3 short name matched case-insensitively and stored upper
case. Anything else — a leading, trailing or doubled separator, `.` or `..`, an
empty component, a name longer than 8.3, more than 8 levels, a 70-character
path — is refused with `name`, never truncated and never guessed. A missing
parent is `no-file`, a file used as a directory is `not-dir`.

### 15.2 Overwrite: the ordering, and what it costs

The replacement chain is allocated in clusters the FAT says are free, filled, and
linked in **every** FAT copy, and the copies are then re-read entry by entry.
Only after that, and after a flush, is the single 32-byte directory entry
rewritten — same name, same attributes, same slot, new first cluster and size.
Then a flush, then the old chain is freed cluster by cluster, then the freed
entries are re-verified in every copy, then FSInfo is corrected, then the file is
read back **through its own name** and compared byte for byte.

Measured on the fixture (65,534 clusters, `pc5: free=65510`):

| Operation | clusters | old chain | freed | free after | writes | read-back |
|---|---|---|---|---|---|---|
| `PC5TEST.TXT` 3,000 → 1,500 B | 3 | 6 | 6 | 65,513 | 26 | ok |
| `PC5TEST.TXT` 3,000 → 0 B | 0 | 6 | 6 | 65,516 | 17 | ok |
| `EMPTY.TXT` 0 → 700 B | 2 | 0 | 0 | 65,508 | 9 | ok |
| `FRAG.BIN` 2,000 → 8 B (fragmented chain 21,23,22,24) | 1 | 4 | 4 | 65,513 | 16 | ok |

The cost is stated rather than hidden: an overwrite needs free space for **both**
versions at once, so a volume with room for one chain but not two is refused with
`full` and **zero writes**. That is the price of never having a name point at
unwritten data, and it is the reason a nearly-full volume cannot be compacted by
overwriting in place.

Two refusals exist only for damaged metadata: a non-empty file whose first
cluster is 0, and a zero-length file that owns a chain, are both `corrupt` and
untouched.

### 15.3 Directories

A new directory costs one zeroed cluster carrying `.` (itself) and `..` (the
parent, or cluster 0 when the parent is the root — the convention Windows, Linux
and this reader all expect), its chain written to every FAT copy and verified,
then the parent's entry. `fat32_list_dir()` must then return zero entries, and
the read-back re-checks `.`, `..`, the parent's entry and the size before the
operation reports `verified=1`. It is readable the instant the call returns: the
boot test creates a file inside a directory created seconds earlier.

A parent with no free slot **grows by one cluster** instead of refusing. The
growth is ordered so an interruption can only leave an allocated, unreferenced
cluster: the new cluster is zeroed and given its end-of-chain marker *before* the
old last cluster links to it, and no existing entry is rewritten. `grew=1` is
reported only after the link is verified in every copy.

The growth cluster is reserved by the **same** allocation as the content, not by
a second scan of the FAT. Two scans would each see the other's cluster as free
until it is linked — a double allocation waiting for a full volume. That bug was
found on the way (the FAT still shows reserved-but-unlinked clusters as free), and
`test_a_directory_with_no_free_slot_grows_when_a_file_is_created_in_it` is what
holds the fix in place.

### 15.4 Integrity invariants, checked after every successful operation

* the root directory's own cluster is never handed out as data, and no chain
  contains a free, bad, reserved or out-of-range entry;
* no cluster belongs to two objects (`chains()` from `tests/fat32_images.py`
  intersected across every object, root and subdirectories included);
* every pre-existing file, in the root and in subdirectories, still reads back
  byte for byte through an implementation that shares no code with the kernel;
* both FAT copies are identical, and FSInfo's free count equals the number of
  free entries the FAT actually holds;
* every written sector lies inside the partition, and the changed-sector set is
  confined to the data, the FATs, the FSInfo blocks and the directory clusters;
* the volume is left with no allocated-and-unreferenced cluster.

### 15.5 Fault injection: every write of every operation, failed in turn

The host driver (`--fail-write N`) fails the N-th write call and dumps the image;
the independent reader then classifies the result. "The switch" is read from the
successful run — the write to the sector holding the new directory entry — not
guessed from a count. A failure **on** the switch counts as "before": that write
did not happen, so nothing was named.

Overwrite of `PC5TEST.TXT` (3,000 → 1,500 B): 26 writes, switch at index 11.

| Failures | Target after the failure | Leaked clusters | freed | mirrors |
|---|---|---|---|---|
| 0–3 (data) | old content, byte for byte | 0–1 | 0 | 1 (chain not yet touched) |
| 4–8 (FAT copies of the new chain) | old content | 1–3 | 0 | 0 |
| 9–11 (FSInfo, the switch itself) | old content | 3 | 0 | 1 |
| 12–23 (freeing the replaced chain) | **new content, complete** | 6→0 | 0→5 | 0 |
| 24–25 (counters after the free) | new content | 0 | 6 | 1 |

Every one of the 26 failures returned an error status (`io`), never `ok`. In all
26 runs: no unrelated file was damaged, no cluster belonged to two chains, no
invalid FAT entry was written, and the accounting closed exactly —
`freed + leaked + clusters freed in one copy only == 6`, the size of the chain
being reclaimed. `mirrors` was `1` only when the copies were verified equal after
the operation and `0` whenever the free was interrupted, including the case where
a copy write failed *after* the other copy had already been updated (a real
divergence, reported as one).

Directory create (`NEWDIR`): 7 writes, switch at index 6.

| Failures | Directory named? | Leaked | mirrors |
|---|---|---|---|
| 0–1 (zeroing the cluster, writing `.`/`..`) | no | 0 | 0 |
| 2–3 (its FAT entry) | no | 1 | 0–1 |
| 4–5 (FSInfo) | no | 1 | 1 |
| 6 (the parent entry — the switch) | **no** | 1 | 1 |

A mkdir that fails anywhere leaves either nothing or one allocated cluster that
is a well-formed empty directory nothing points at; there is no state in which a
name refers to a half-initialised directory. When the *growth* of the parent
fails, the parent's chain is byte-for-byte unchanged, the directory is not named,
and exactly one cluster (the initialised directory) is leaked — asserted,
including that the leaked cluster still carries a valid `.` and `..`.

Corruption of the stored payload is caught by the read-back (`verify`, never
`ok`); a failing flush is `flush` with `flush=-1`; a failed FAT mirror is
reported rather than hidden; a source with no write callback is `read-only` and
writes nothing.

### 15.6 Through the real shell, and across a reboot

`kernel/ui.c` gained the verbs M1i needs, and `put` kept its promise:

| Command | Behaviour |
|---|---|
| `put PATH text` | create; an existing name is refused (`exists`) and nothing is written |
| `write PATH text` | replace the content of an existing file, keeping its name and slot |
| `mkdir PATH` | create one directory in an existing parent |
| `ls [PATH]` | list the root, or a subdirectory |
| `read PATH` | read through the path (was: a bare root name) |

`make boot-test` drives all of them on a disposable copy of the fixture and then
boots that dumped image **a second time** and reads the results back:

```
fat32 mkdir path=NEWDIR status=ok stage=8 clusters=1 first=28 grew=0 free=65507 mirrors=1 flush=1 verified=1 writes=7
fat32 list path=NEWDIR count=0 status=ok
fat32 create name=NEWDIR/A.TXT size=9 status=ok stage=8 clusters=1 free=65506 mirrors=1 flush=1 verified=1 writes=6
fat32 list path=NEWDIR count=1 status=ok
fat32 overwrite path=PC5TEST.TXT size=15 status=ok stage=8 clusters=1 first=30 old_first=3 old_clusters=6 freed=6 free=65511 mirrors=1 flush=1 verified=1 writes=20
fat32 overwrite path=NOPE.TXT size=1 status=no-file ... writes=0
fat32 overwrite path=SUBDIR size=1 status=directory ... writes=0
fat32 create name=PC5TEST.TXT size=1 status=exists ... writes=0
fat32 mkdir path=SUBDIR status=exists ... writes=0
fat32 mkdir path=MISSING/X status=no-file ... writes=0
fat32 mkdir path=BAD*NAME status=name ... writes=0
fat32 read name=SUBDIR/INNER.TXT size=700 status=ok sha256=d2e919d6…b2e331
```

Second boot, same disk image, fresh kernel:

```
PC5-LOG fat32 device=0 partition=1 status=ok label="PC5FS" fs=FAT32 clusters=65534 spc=1 base=2048
fat32 read name=PC5TEST.TXT size=15 status=ok sha256=b13f0d4d…5bf4f8
fat32 read name=NEWDIR/A.TXT size=9 status=ok sha256=496b7ee6…3f83b209
fat32 list path=NEWDIR count=1 status=ok
fat32 read name=SUBDIR/INNER.TXT size=700 status=ok sha256=d2e919d6…b2e331
```

The two SHA-256 values are what the independent reader computed for the bytes the
first boot wrote, so "readable after a reboot" is a measurement, not an
assumption. Every other file on the volume still read back identically, both FAT
copies matched, no cluster was double-allocated or orphaned, and FSInfo agreed
with the FAT. 29 new boot checks, 116/116 total.

### 15.7 What the test suite refuses to do

No test was deleted or weakened to reach these numbers. The one M1g test that M1i
made obsolete — "a full root directory is refused" — was replaced by two that
assert the new, stronger behaviour: the directory *grows by exactly one cluster
and keeps every entry*, and a directory that cannot grow is still refused with no
writes. Its assertion count went up, not down.

The new suite (50 tests) states its invariants once, in `classify()`, and every
fault-injection test consumes them: the "unrelated files" oracle is derived from
the image *before* the operation, by the independent reader, so it cannot drift
when a fixture changes, and the target file is compared separately as the subject
of the operation.

Three harness-safety guards carry over to the new entry points: nothing on the
boot path may call a write, the only callers of the write entry points are
console commands, and a mount without a write callback refuses every write.

### 15.8 Cost, said plainly

* Free space is counted by scanning the FAT: one sector read per 128 clusters,
  and an allocation scan that is O(clusters) in the worst case. A 65,534-cluster
  fixture costs ~512 sector reads before the first write.
* An overwrite reads and writes 12 sectors of FAT to free a 6-cluster chain,
  because every copy is written and then re-read. Correctness over speed, in the
  order the milestone ranks them.
* A directory grows by one cluster at a time; a directory that needs a second
  cluster of entries pays for a second growth.
* The kernel has no clock: entry timestamps are the fixed constant M1g chose
  (2026-11-15, 12:00), so two identical operations produce identical images.

### 15.9 What this does not establish

* **No power-loss atomicity, and none claimed.** A failing `write()` call is not a
  power cut: the driver returns an error before the sector reaches the medium, so
  the tests prove the *ordering* the code promises, not what a real disk does with
  a write that was issued and never completed. There is no journal, no repair pass
  and no second copy of anything. The strongest safe statement is the one in
  ADR-015: an interruption leaves the old content intact or the new content
  complete, plus at worst a leaked chain that the report counts.
* **No physical disk.** Everything here ran through an in-memory sector array and
  an emulated ATA disk. A real drive's cache, write-back and reordering are
  outside this evidence.
* **No delete, rename, truncate-in-place or attribute change.** The operations are
  exactly create, replace and mkdir.
* **No long file names.** They are skipped by design in the read path and never
  produced by the write path; accepting them would mean a second name format and
  checksum scheme.
* **One cluster per growth.** A directory needing many new clusters is grown one
  at a time, and a volume with one free cluster cannot grow a full parent.
* **Efficiency is not a goal here.** The allocation scan and the mirror re-reads
  are O(clusters)-ish deliberately; M2 and later can revisit them once AHCI/NVMe
  make the I/O cheaper.
