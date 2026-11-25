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

Sections 9–16 are later milestones (M1d, M1e, M1f, M1g, M1h, M1i, and M2 in
§16 — the first section recorded against *real firmware* rather than an
emulator). §17 records the M0 architecture realignment, which changes documents,
the roadmap graph and the gate vocabulary rather than the boot path. §18 records
the UEFI boot-chain hardening and the four-tier result that follows from it:
the loader is executed under the stub firmware, the BIOS path is executed under
v86, and genuine OVMF was **not** available on the machine that ran these. Each
section starts with its own build inputs, sizes and hashes; the artifact numbers
in §1 describe base `5ab7c48` and are kept as history, not as the current image.
The current image is the one in §17.2 (and, for the firmware measurement, §16).

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

Nothing in the catalog moved. `make verify` passed the check set of that time —
53 checks, the same ones as M1g plus the MBR ones, and fewer than the 80 it runs
after the descriptor work in §21 — including the catalog checksum, the initial entry at
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

## 16. M2 — real UEFI firmware boot under OVMF, 2026-11-15

Section 7 said, in as many words, that the UEFI path was *implemented but never
executed against firmware*. This section removes that limitation: the real
`build/pc5.iso` was booted by genuine TianoCore OVMF under QEMU, and the serial
log shows real firmware handing control to the PC5 loader and the loader handing
control to the PC5 kernel.

Base: `main` `af726d2` (M1i merged). Branch `arena/473f5139-pc5`.

*Note, added 2026-11-16.* `af726d2` is a real commit — "Merge pull request #16 from
SpicyBadran/arena/3a8e35e9-pc5", 2026-11-15 — and it was `main` at the time this section
records. It is **deliberately not repointed**, and it does not resolve in a fresh clone:
`main` was later rewritten, so that merge is no longer an ancestor of it and a clone
fetches only the rewritten line. It is therefore an accurate historical base, not a broken
citation, and replacing it with a current SHA would fabricate a run that never happened
against that revision. Two consequences follow, both recorded rather than fixed:
`BUILD_ID=af726d2` in the commands below names that historical revision, and a reader
cannot re-run this section exactly against a fresh clone.

### 16.1 Environment (external dependency, not part of the repository)

| Item | Value |
|---|---|
| emulator | QEMU 11.0.2, `-machine q35`, TCG only (no `/dev/kvm` in this environment), 1 vCPU, 256 MiB |
| firmware CODE | TianoCore EDK II OVMF, `OVMF_CODE-pure-efi.fd`, 1,966,080 bytes, `_FVH` firmware volume at offset 0x28, build dated 2023-06-03 |
| firmware VARS | `OVMF_VARS-pure-efi.fd`, 131,072 bytes, copied per run (never written in place) |
| firmware source | external package/repository; **no firmware image is committed here, embedded in the ISO or linked into the kernel** |
| emulator build note | the QEMU used was a portable musl build run through a musl dynamic loader built from source in this environment; it is an external tool with no bearing on the ISO |

`tests/ovmf_boot.py` records the firmware SHA-256, the QEMU version and the ISO
SHA-256 in its JSON report (`build/ovmf-report.json`). The run recorded below
booted the image pinned in §16.2 - `10971e54b0519f08ff04029ce9caa7576a4cb1fd71
0fedde1f85f0e4191936d9` - so the verdict and the bytes it applies to can be
checked against each other.

### 16.2 Build inputs (pinned, two clean builds)

```
$ export SOURCE_DATE_EPOCH=1700000000
$ make clean && make iso BUILD_ID=af726d2 && make clean && make iso BUILD_ID=af726d2
# both builds identical
```

`BUILD_ID` is pinned because the build id is compiled into the kernel and printed
at boot, so a default build differs by that string alone - and it changes with
every commit, documentation commits included. A default `make iso` at the head of
this branch therefore produces an image that differs from the one below in the
embedded build id and nothing else. The numbers below are the pinned ones, and
they can be reproduced exactly from any later commit.

| Artifact | Bytes | SHA-256 |
|---|---|---|
| `build/pc5.iso` | 1,601,536 | `10971e54b0519f08ff04029ce9caa7576a4cb1fd710fedde1f85f0e4191936d9` |
| `build/kernel.bin` | 66,836 | `93800460d4ba52cc393a75088377098c38f3776590b502ec3833e1e7d4c10b09` |
| `build/loader.bin` | 4,096 | `f121b34a7ebc0e7dd6855e429496b7f3bbb636ac4249fad62542b8c6503da1bb` |
| `build/mbr.bin` | 440 | `d07ec2ba3aeb06b200ed8e918d9a14dfa18b730477f03cbd632dec06ab19d107` |
| `build/efi.img` (ESP) | 1,474,560 | `efc6c7580c147bf5cd7f0cc85b6bb3f3048907c49a1921ce19e6e932b1d8ae48` |
| `build/uefi/BOOTX64.EFI` | 15,309 | `b51ea5a17d442cfb1df634c588d8839f88cc0df90de47b0148e9e45a6caf07bb` |

`build/build-info.json` was byte-identical between the two builds. The MBR
layout is unchanged from M1h (440-byte code area, same partition entry), and the
BIOS loader is unchanged (its hash is the M1i value).

### 16.3 The boot that now works

`make ovmf-test` (cold boot, fresh VARS) — serial log, firmware console mirror
de-duplicated:

```
BdsDxe: loading Boot0001 "UEFI QEMU DVD-ROM QM00005 " from PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x2,0xFFFF,0x0)
BdsDxe: starting Boot0001 "UEFI QEMU DVD-ROM QM00005 " from PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x2,0xFFFF,0x0)
PC5 UEFI LOADER uefi-0.2.0
PC5 UEFI loader uefi-0.2.0
PC5-UEFI: firmware_started
PC5-UEFI: serial_probe=16550
PC5-UEFI: firmware=EDK II
PC5-UEFI: firmware_revision=65536
PC5-UEFI: display_width=1280
PC5-UEFI: display_height=800
PC5-UEFI: display_pitch=5120
PC5-UEFI: display_pixel_format=1
PC5-UEFI: image_device=acpi
PC5-UEFI: kernel_volume=image
PC5-UEFI: kernel_volume_opened
PC5-UEFI: medium_readme_bytes=1273
PC5-UEFI: kernel_bytes_read=66836
PC5-UEFI: kernel_protocol_version=1
PC5-UEFI: boot_services_exited
PC5-UEFI: memory_map_entries=40
PC5-UEFI: ExitBootServices_retries=0
PC5-UEFI: memory_conventional_mib=216
PC5-UEFI: kernel_loaded_at=0x00010000
PC5-UEFI: entering 32-bit kernel
PC5 KERNEL 0.2.0 (build af726d2, …)
PC5-LOG kernel_entry eip=0x10000
PC5-LOG protocol=1 magic=ok
PC5-LOG display=framebuffer 1280x800 pitch=5120 font=none
PC5-LOG memory_mib=234 entries=40 top_mib=236
…
PC5-BOOT-OK
```

* Firmware: `firmware=EDK II`, revision `0x00010000` (1.0).
* Display: GOP 1280×800, 32 bpp, pitch 5120, format 1 (BGR) — reported honestly,
  so the kernel swaps red and blue, as documented since M1c.
* Memory map: 40 descriptors converted to E820, 216 MiB conventional.
* `ExitBootServices`: succeeded on the first attempt with the map key it had just
  read (`ExitBootServices_retries=0`).
* Kernel: entry at `0x10000`, protocol 1, magic ok, `PC5-BOOT-OK`.
* The harness emits `PC5-OVMF-BOOT-OK` from its own process, and only after every
  check has passed — the guest never prints that marker.

**Warm boot**: same firmware, same ISO, the variable store the cold boot had just
written. Identical result, `ExitBootServices_retries=0`, `PC5-BOOT-OK`.

**Distinguishing UEFI from SeaBIOS**: the harness requires a `BdsDxe:` boot
manager line, requires the El Torito UEFI boot option or the loader banner, and
fails if the log contains `SeaBIOS`.

### 16.4 What real firmware broke, and what was fixed

Every fix below was found by running under OVMF, not by reading the code. Each
one has a regression test in `tests/uefi_stubtest.py` (48 checks, up from 42) or
in `tests/boot_test.py` (119 checks, up from 116).

| # | Symptom on real firmware | Cause | Fix |
|---|---|---|---|
| 1 | `PC5-UEFI FAILURE E2: no volume on this machine holds \KERNEL.BIN` | OVMF has **no ISO9660 driver**. On an El Torito UEFI boot the only file system the loader can see is the RAM-disk copy of the ESP; a kernel that lived only in the ISO9660 root was unreachable | the ESP now carries `KERNEL.BIN` (and `README.TXT`) next to `BOOTX64.EFI`; the loader tries the volume it was booted from first, then falls back to scanning every file system (ADR-016) |
| 2 | `E3: cannot read \KERNEL.BIN`, with `read_size=0x400000 read_got=0x10534` | the loader learned a file's size by seeking past EOF and reading the position back. OVMF *accepts* that seek and reports the requested position (4 MiB), so every file looked 4 MiB long | size now comes from `EFI_FILE_INFO` through `GetInfo`; the read loops until the reported size has arrived; the seek trick survives only as a fallback (ADR-017) |
| 3 | `E13: ExitBootServices was refused (status=0x8000000000000002)` | the memory-map key was read, then boot information was printed to the console — firmware allocations invalidate the key — and `ExitBootServices` was called once with the stale key | the map is re-read and `ExitBootServices` retried with a fresh key (up to 8 attempts), and nothing that can allocate runs between the keyed `GetMemoryMap` and the call |
| 4 | (latent) the console protocol was called after `ExitBootServices` | `uefi_puts` always wrote to `ConOut`, including the post-exit trace lines. That is firmware memory the loader no longer owns | `console_closed` is set the moment `ExitBootServices` returns; post-exit markers go to the 16550 only |
| 5 | `PPCC55--UUEEFFII::  ffiirrmmwwaarree__ssttaarrtteedd` | OVMF's ConSplitter mirrors the console to the same serial port the loader writes to, so a character-at-a-time echo put every character on the wire twice | each line is assembled in a buffer and written once per sink; the log now shows each line twice, in order, and the harness de-duplicates consecutive duplicates |
| 6 | `PC5-LOG display=framebuffer 1280x800x5120 pitch=122409 font=(null)` | the kernel's evidence line had one `%u` too many: the font name was read as the pitch and the font field read whatever followed | one conversion per argument in `kernel/main.c`, plus a `boot-test` assertion that the line carries one pitch and one recognised font name |

The stub firmware was taught the two OVMF behaviours that caused #2 and #3
(`clamp_seek_past_end=False` and `race_map_key=True`), so the same bugs cannot
come back unnoticed without firmware, and the harness was corrected where it had
modelled the x64 calling convention wrongly (arguments five and six are on the
stack, not in spare registers).

### 16.5 Negative tests — who fails, and how

Each machine below is a real ISO built by the repository's own tools and booted
under the same OVMF/QEMU configuration. Nothing calls a failing path directly.

| Machine | What was changed | Observed | Attributed to |
|---|---|---|---|
| `no-boot-app` | ESP without `\EFI\BOOT\BOOTX64.EFI` | firmware falls back to its built-in UEFI shell, prints its file-system map and a `startup.nsh` prompt; the PC5 loader never runs | firmware |
| `bad-pe` | `MZ` magic of `BOOTX64.EFI` corrupted | firmware refuses the image and drops to the same shell; no loader output | firmware |
| `truncated-iso` | ISO cut at 64 KiB (boot catalog present, boot image missing) | firmware finds the catalog, cannot read the image, never starts anything | firmware |
| `no-kernel` | kernel removed from both the ESP and the ISO9660 root | `PC5-UEFI FAILURE E2` + `PC5-UEFI-FAILURE E2`, no handover | bootloader |
| `bad-signature` | `PC5K` signature in `KERNEL.BIN` corrupted | `PC5-UEFI FAILURE E4`, no handover | bootloader |
| `deadline` | good ISO, one-second budget | harness reports a timeout; a timeout is never a pass | harness |

### 16.6 BIOS regression (unchanged behaviour)

| Gate | Result |
|---|---|
| `make verify` | `VALID: build/pc5.iso sha256=10971e54…36d9` (El Torito BIOS entry, UEFI section, catalog checksum, hybrid MBR, per-file SHA-256) |
| `make boot-test` | **119/119** checks: SeaBIOS El Torito boot, MBR-as-hard-disk boot, FAT32 read/write on a disposable disk, GPT parsing, UI pages, and the two new M2 checks (ESP and ISO9660 copies of `KERNEL.BIN` are byte-identical; the display evidence line is well formed) |

The MBR bytes are identical to M1h/M1i (`d07ec2ba…`); the hybrid-MBR path was
not touched.

### 16.7 Gate results, reported separately

| Gate | Command | Result |
|---|---|---|
| structural | `make verify` | VALID |
| host unit | `make unit` | 160 checks, 0 failures |
| host suite | `make check` | 299 tests, OK (280 before this milestone + 19 new tests of the OVMF harness itself: `tests/test_ovmf_harness.py`) |
| emulated BIOS boot | `make boot-test` | 119/119 |
| stub firmware (loader executed, not firmware) | `tests/uefi_stubtest.py` | 48/48 |
| roadmap graph | `make roadmap` | 18 milestones, document in sync |
| **real OVMF boot** | `make ovmf-test` | **40 PASS, 1 NOT APPLICABLE, 0 FAIL**, `PC5-OVMF-BOOT-OK` |

The harness that produces these numbers is itself tested, because a gate that
lies is worse than no gate: `tests/test_ovmf_harness.py` (19 tests, part of
`make check`) proves that a file without a `_FVH` volume header, a file that is
really the PC5 loader, an implausibly small file and the real `build/efi.img` are
all refused as firmware; that the firmware console mirror (the same line twice)
is collapsed while character doubling (`PPCC55…`) is *not* silently repaired;
that a SeaBIOS log is not mistaken for a UEFI one; and that with no firmware
installed the harness exits **3** (NOT TESTED) with a blocker listed and no
success marker in the report — the one outcome that must never look like a pass.

### 16.8 Not tested, and not claimed

* **Disk-style UEFI boot** (ESP attached as a block device) — reported as
  NOT APPLICABLE, not as a pass: this ISO carries its ESP as an El Torito image
  inside ISO9660, so there is no ESP partition to attach. Making that path real
  is a partition-table change and was not done (the MBR layout is unchanged).
* **Physical hardware.** No PS5, no PC, no UEFI board was booted. Everything in
  this section is QEMU + OVMF, an emulator and firmware, not a machine.
* **Secure Boot.** The firmware used is the "pure EFI" build without the
  Microsoft-signed shim chain; Secure Boot was neither enabled nor tested.
* **KVM.** This environment has no `/dev/kvm`; OVMF ran under TCG. Timing results
  from TCG mean nothing about hardware performance.
* **ACPI.** The UEFI handover still passes no RSDP, so the kernel reports
  `acpi=absent` on this path. That is honest reporting of a real gap, not a
  regression.
* **Other firmware.** Only EDK II/OVMF was exercised. AMI, Insyde, Phoenix and
  Apple firmware are untested; the loader's behaviour there is unknown.

---

## 17. M0 — architecture realignment, 2026-11-15

This milestone changes what the project *says it is*, the machine-checked graph
that says how it gets there, and the vocabulary the gates enforce. It does not
touch the boot path, the kernel or the loader: every artefact below is the same
code that §15 and §16 measured, rebuilt to confirm the documentation change
broke nothing.

### 17.1 Inputs

| | |
|---|---|
| Branch | `arena/5b7f7e7a-pc5`, based on `main` `95a7002` |
| Commit under test | `2a99c51` plus the M0 documentation and gate change |
| Host | Debian 12 x86-64, gcc 12.2.0, python 3.11.2 |
| Emulator | none available in this environment (see 17.4) |

### 17.2 Artifact

`make` → `build/pc5.iso`, 1,601,536 bytes (782 sectors of 2048),
sha256 `5cde77865a7b43e1115541ce4281a778c6d1f0c4f09f97d807456d10ac175f99`,
kernel `KERNEL.BIN;1` 66,836 bytes
sha256 `a1b492c96fe66690e9d54e7952ad5bcaf22e1d8922eefe93e42f4962854cd0cc`.

The size is identical to §16.2 because the ISO layout is unchanged; the hash is
not comparable to the pinned `BUILD_ID=af726d2` build in §16.2, which pins
`SOURCE_DATE_EPOCH` and the build identifier to reproduce a specific historical
image. A different `SOURCE_DATE_EPOCH` produces a different hash by design
(invariant I8 is about *same inputs, same bytes*, not about one frozen hash).

### 17.3 Gates

| Gate | Result | Detail |
|---|---|---|
| `make check` | **PASS** | 338 tests, 0 failures, 2 skipped (`unittest discover` over `tests/test_*.py`) |
| `make roadmap` | **PASS** | 35 milestones, 14 workstreams, destination `M19` reachable, generated document in sync |
| `make verify` | **VALID** | structural verification of the ISO; per-file hashes match `build-info.json` |
| `make unit` | **PASS** | kernel host-side assertions (the C unit binary; no new failures) |
| `tests/test_architecture.py` | **PASS** | 53 tests: graph shape, evidence paths, destination reachability, release isolation, document links |
| `tests/test_product_identity.py` | **PASS** | 18 tests: identity and destination are data, the six levels are fixed, forbidden claims are absent from current documents, the package pointer's oid/size are recorded and its payload's absence is asserted |
| `make boot-test` | **NOT TESTED** | node or the v86 assets are absent here; the harness exits 77 rather than pretending to pass. Last passing run: §15 (119/119) |
| `make stubtest` | **NOT TESTED** | the `unicorn` module is absent; the harness exits 2. Last passing run: §7 interface checks, 48/48 |
| `make ovmf-test` | **NOT TESTED** | no QEMU and no OVMF build in this environment; exit 3 (NOT TESTED). Last passing run: §16, 40 pass / 1 NOT APPLICABLE |

### 17.4 What this environment could not run

An honest record, because these are the gates that measure execution:

* no `node` / `v86-system`, so no emulated boot and no screen or input capture;
* no `unicorn`, so the real `BOOTX64.EFI` was not executed against the stub firmware;
* no `qemu-system-x86_64` and no OVMF firmware, so nothing ran under real firmware;
* no physical machine, so every hardware claim stays UNTESTED.

What *was* run is everything that does not need an emulator: the whole Python
test corpus (338 tests), the ISO writer and verifier, the roadmap validator, and
the kernel's host-side unit binary. The M0 change is documentation, graph data
and gate vocabulary, which is exactly the part those gates cover.

### 17.5 What M0 changed, and what it proves

| Change | Checked by |
|---|---|
| one destination, `M19` (level 5), on the critical path rather than a side branch | `tools/roadmap.py` reachability, `tests/test_architecture.py` |
| product identity, non-goals, foundation role and the six levels are data in `roadmap.json` | `tests/test_product_identity.py` |
| the two-track vocabulary and its gate are gone; a milestone that serves level 4–5 exists in WS10 | `tests/test_architecture.py`, forbidden-claim scan |
| release isolation, not track isolation, protects a release (ADR-021) | validator rule + synthetic-graph test |
| the protected PS5 package is recorded by hash and size, payload absent | `tests/test_product_identity.py` |

### 17.6 Not claimed

* Nothing here is evidence that PC5 is closer to running PS5 system software.
  M0 is a governance milestone; `M17`–`M21` remain blocked with named blockers.
* No gate in this section executed guest code. §16 remains the only section
  recorded under real firmware, and it measured the PC5 loader, not Sony code.

---

## 18. UEFI boot chain: validated handover, and what could not be executed

Requested as "M2: real UEFI firmware boot, OVMF validation", which in this
roadmap is `M1c` (UEFI boot path under real firmware, **complete**, `§16`) plus
the hardening that `M1c`'s acceptance list did not yet demand. `M2` proper —
hardware abstraction and drivers — is untouched by this section.

### 18.1 Inputs

| | |
|---|---|
| Branch | `arena/804a7d39-pc5`, based on `main` `bc8b41d` (PR #18 merged) |
| Commit under test | the four commits of this branch (loader, harness, tests, docs) |
| Host | Debian 12 x86-64, gcc 12.2.0, python 3.11.2, node 22.22.3 |
| Harnesses available | `unicorn` 2.1.4 (installed into a venv, `pip install unicorn`), `v86-system` 0.5.0 (`npm install v86-system`) |
| Harnesses **not** available | `qemu-system-x86_64` (not installed, no package access), TianoCore OVMF (not present) |

### 18.2 Artifact under test

`make iso` with `SOURCE_DATE_EPOCH=1763251200` → `build/pc5.iso`, 1,601,536
bytes, sha256 `29dcc23179b98821b64f61878c37287d1ec8cf142be97add1455657c6b8e70ea`;
`KERNEL.BIN;1` 67,028 bytes (the kernel gained the boot-contract predicate);
loader `uefi-0.2.0` with the checks listed in 18.3. A second clean build from the
same inputs produced the same sha256 — the number below is reproducible, not a
timestamp. (With no `SOURCE_DATE_EPOCH` the build stamps the wall clock, so two
builds minutes apart legitimately differ; that is invariant I8, not a
regression.)

### 18.3 What changed in the loader

| Change | Why it was wrong before | Failure code if it now triggers |
|---|---|---|
| the handover window `[0x2000, 0x9F000)` is checked against the firmware memory map before `ExitBootServices` | the kernel was copied to a fixed address with no check that the range was conventional memory; on a machine with reserved low memory the copy would land in firmware memory and the crash would appear inside the kernel | `E15` |
| the memory map is merged and an over-large map is a failure instead of a silent truncation | `build_e820` stopped at 40 entries, so a firmware that reports memory in many pieces lost entries — under-reporting usable memory and possibly hiding a reserved region | `E16` |
| the kernel copy is read back with `memcmp` before the jump | a short or misplaced copy produced a jump to bytes that were not the file | `E17` |
| `SetWatchdogTimer(0,…)` before `ExitBootServices` | firmware may arm a watchdog; a reset mid-handover looks like a hardware fault | trace line `watchdog_disabled` / `watchdog_disable_refused=` |
| boot-service pointer table gains `set_watchdog_timer` at its specified offset (+256), pinned by a `_Static_assert` | the slot existed only as `void *`; a wrong offset would corrupt the table | compile time |
| the kernel's boot-information validation is one predicate (`pc5_boot_info_problem`) used by `kmain` and by the host unit test | the check existed only inline in `kmain`, where nothing could test it | `pc5: FATAL: bad boot info magic` / `boot protocol version mismatch` |

The `ExitBootServices` sequence itself was already correct and is unchanged: a
size probe, a buffer sized with slack, a keyed `GetMemoryMap`, **no** firmware
call between the keyed query and `ExitBootServices`, `EFI_INVALID_PARAMETER`
treated as "the map moved" (re-read, up to 8 attempts), any other error fatal,
and after success no console protocol call at all (`console_closed`).

### 18.4 Executed: the loader under the stub firmware (`make stubtest`)

`tests/uefi_stubtest.py` executes the real `build/uefi/BOOTX64.EFI` (PE32+,
relocated, Unicorn x86-64) against a stub that implements EFI tables at the
specification's byte offsets.

**58/58 checks passed**, up from 48. The ten new checks:

| Check | Result |
|---|---|
| no boot service was called after `ExitBootServices` (every slot wrapped) | PASS — `[]` |
| the watchdog was disabled while boot services were still callable | PASS — `timeout=0`, before exit |
| a firmware that refuses to disable its watchdog still boots, and says so | PASS |
| a fragmented memory map is merged, not truncated | PASS — 6 entries both ways, same usable total |
| the handover window was checked against the map | PASS — `handover_window_checked=1` |
| the kernel copy was read back before the handover | PASS — `kernel_handoff_verified=1`, `handoff_bytes=67028` |
| a key that is always stale stops the boot with `E13`, never a false success | PASS — 8 attempts, no `boot_services_exited` line |
| two stale keys are retried twice, then the handover proceeds | PASS — `ExitBootServices_retries=2` |
| low memory that is not conventional stops the boot with `E15` | PASS |
| a memory map too large for the E820 buffer stops the boot with `E16` | PASS |

Serial excerpt from the clean run (as executed):

```
PC5-UEFI: watchdog_disabled
PC5-UEFI: boot_services_exited
PC5-UEFI: memory_map_entries=6
PC5-UEFI: ExitBootServices_retries=0
PC5-UEFI: handover_window_checked=1
PC5-UEFI: memory_conventional_mib=2
PC5-UEFI: kernel_handoff_verified=1
PC5-UEFI: handoff_bytes=67028
```

These are *loader-logic* results. The firmware is a stub: it models the
interfaces and the misbehaviours, not the machine.

### 18.5 Executed: the BIOS path (regression)

`make boot-test` (v86, SeaBIOS): **119/119 checks**, including the El Torito
boot, the MBR-as-hard-disk boot, FAT32 create/overwrite on a disposable disk,
GPT parsing and every UI page. The UEFI change did not regress the BIOS path,
and the two `KERNEL.BIN` copies in the image are still byte-identical.

`make verify`: **VALID**, per-file hashes match `build-info.json`.

`make unit`: **165 checks, 0 failures** — the five new ones are the boot-contract
predicate: good block accepted, bad magic rejected (1), version mismatch rejected
(2), empty block rejected, null pointer rejected.

### 18.6 NOT EXECUTED: genuine OVMF

The machine that produced this section has no QEMU and no OVMF, and no package
access to install them. `tests/ovmf_boot.py` therefore exits **3 (NOT TESTED)**
and prints the required matrix with no row executed:

```
[FAIL] no qemu-system-x86_64 (set PC5_QEMU)  (external dependency missing)
[FAIL] no OVMF CODE image found (set PC5_OVMF_CODE)  (external dependency missing)
NOT TESTED: genuine OVMF firmware and/or QEMU is not available on this machine.
Required matrix rows: 22, none executed here.
```

This is not a pass and is not counted as one. The last real-firmware run remains
§16 (40 checks, 1 NOT APPLICABLE, `PC5-OVMF-BOOT-OK`) on the machine that had
OVMF. What this branch adds for that machine:

* six more checks per boot: the handover window, the watchdog, the retry count,
  the read-back handoff, the on-medium kernel size, and the kernel's own memory
  report cross-checked against the loader's count (two independent witnesses to
  the same handover);
* a Secure Boot mode (`--secure-boot`, SMM + secure flash) that asserts the
  *refusal* an unsigned image must get, because PC5 has no key and no shim;
* a low-memory negative run (24 MiB) that must fail safely;
* a required matrix (`build/ovmf-matrix.json`, 22 rows) in which a row is
  `verified` only if an executed run produced it, `external` if its evidence is
  another gate (named), and `not-tested` otherwise;
* `make ovmf-setup`, which provisions QEMU and OVMF from the distribution's
  packages and prints the firmware paths and hashes the report must record.

### 18.7 The matrix, as it stands

| Requirement | Evidence | State |
|---|---|---|
| firmware recognises the medium; EFI entry found; loader executes | `§16` OVMF run (other machine) | OVMF-EXECUTED, not re-run here |
| required files located and loaded | `§18.4` stub (executed), `§16` | EXECUTED (stub) |
| memory map read, merged, window validated | `§18.4` | EXECUTED (stub) |
| `ExitBootServices` with a current key, retries, no false success | `§18.4` | EXECUTED (stub) |
| kernel handoff, entry point, boot info accepted | `§18.4` + `§18.5` unit | EXECUTED (stub) + BUILD-VERIFIED |
| kernel reaches `PC5-BOOT-OK` | `§16` (OVMF), `§18.5` (BIOS/v86) | OVMF-EXECUTED (prior), BIOS EXECUTED |
| missing kernel / invalid EFI image / truncated medium | `§16` negative runs (OVMF), `§18.4` E2/E4 (stub) | OVMF-EXECUTED (prior) |
| invalid boot information rejected | `§18.5` host unit test | BUILD-VERIFIED |
| insufficient memory fails safely | implemented in the harness | NOT TESTED |
| Secure Boot policy enforced on an unsigned image | implemented in the harness | NOT TESTED |
| repeatable reboot | `§16` cold + warm | OVMF-EXECUTED (prior) |
| BIOS regression | `§18.5` | EXECUTED |

### 18.8 What this section does not establish

* Nothing here proves the loader boots on real firmware *today*. The only
  real-firmware evidence in this repository is §16, from a machine with OVMF.
* No physical machine was booted, so `M1` (level 1) stays `not_started`.
* ACPI is still not handed over: the UEFI path passes no RSDP, and the kernel
  reports `acpi=absent` there. Fixing that changes `struct pc5_boot_info`, which
  is a protocol bump under invariant I4 and is not attempted here.
* Secure Boot is not supported and is not claimed: PC5 ships no signature.

---

## 19. Platform contract and conformance interfaces (`M10`)

Requested as "M10: PS5 platform contract, evidence registry, conformance
interfaces". This section records what was executed, on which machine, and what
the result does and does not establish. The headline is unchanged from §18:
**no Sony software has ever been executed here**, and nothing below is evidence
about it.

### 19.1 Inputs

| Item | Value |
|---|---|
| Base commit | `a0019657c33ad0cc3f6e4d972ba2d781bb371e97` (`main`, PR #19 merged) |
| Working tree | `arena/a6a41f42-pc5`, this branch's commits |
| New code | `psm/` — 15 modules: registry, rule engine, linkage audit, renderer, conformance runner, ten interfaces |
| Document | `docs/architecture/PS5_PLATFORM_CONTRACT.md` revision r1 (generated tables) |
| Environment | Linux x86-64, gcc/ld/objcopy, python3.11, node v22.22.3 + `v86-system`, `unicorn` 2.1.4 in a virtualenv |
| Not present | QEMU, OVMF, KVM, physical PS5 hardware, any Sony artifact other than the untouched LFS pointer |

### 19.2 The registry and its rules (BUILD-VERIFIED)

```
$ python3 -m psm.contract
PS5 platform contract registry, revision r1 (2026-11-16)
  sources: 4   claims: 38
  public     6
  inferred   9
  unknown   21
  restricted 2
registry: OK

$ python3 -m psm.audit
PSM audit: 38 claims, 64 operations, 10 interfaces
audit: OK
```

The rule engine rejects, and the tests prove it rejects, a `PUBLIC` claim without
a usable source, a `PUBLIC` claim without a value, an `INFERRED` claim that does
not state its assumption, an `UNKNOWN`/`RESTRICTED` claim carrying a value, an
`UNKNOWN` claim that does not say why nothing public supports it, a claim citing a
non-existent source, duplicate claim ids, a verified source with no location, and
a registry with no revision. The linkage audit rejects an operation citing a
missing claim, a foreign subsystem's claim, or a `RESTRICTED` claim while being
implemented, an interface that is neither required nor declared extra, and a
required group with no interface.

### 19.3 The conformance suite (`CONFORMANCE-TESTED`, against PC5's own doubles)

```
$ python3 -m psm.conformance
PSM conformance: 261/261 checks passed
```

| Interface | Checks |
|---|---|
| `PSM-CPU` | 30 |
| `PSM-MEM` | 32 |
| `PSM-STOR` | 33 |
| `PSM-GFX` | 29 |
| `PSM-AUD` | 26 |
| `PSM-USB` | 29 |
| `PSM-SVC` | 26 |
| `PSM-IRQ` | 21 |
| `PSM-TMR` | 13 |
| `PSM-IPC` | 22 |
| **total** | **261** |

The categories exercised are the ten the contract requires: valid paths, invalid
parameters, boundaries, missing devices, unsupported operations, resource
exhaustion, invalid transitions, ownership violations, timeout/error propagation
and deterministic replay. Every check is run twice with fresh doubles to assert
identical results and identical call transcripts.

**This label means:** PC5's doubles satisfy PC5's contract. It does not mean Sony
conforms to anything, and it is not `EMULATOR-EXECUTED`.

### 19.4 Mutation tests: the suite can fail

```
$ python3 -m unittest tests.test_psm_mutations -v
test_a_mutation_is_reported_with_its_check_name ... ok
test_every_mutation_is_caught ... ok
test_the_baseline_is_green_so_failures_mean_something ... ok
```

Sixteen deliberately broken doubles, one per row below, each required to make at
least one check fail:

| # | Mutation | Caught by |
|---|---|---|
| 1 | allocator hands out the same address twice | non-overlap check |
| 2 | release succeeds twice | double-release `OWNERSHIP` |
| 3 | a security-state MSR returns zero instead of `DENIED` | `DENIED` path |
| 4 | reset keeps its epoch and its state | epoch increment |
| 5 | a timed-out receive returns a fabricated message | timeout carries no value |
| 6 | queue delivers last-in-first-out | ordering |
| 7 | a foreign owner completes a storage tag | completion ownership |
| 8 | flush reports zero durable sectors | durability report |
| 9 | presentation sequence numbers freeze | monotonic presentation |
| 10 | an `UNKNOWN` submission reports success | `UNSUPPORTED` with no value |
| 11 | an oversized audio write is accepted | `OVERFLOW` |
| 12 | an oversized HID report is truncated | report length validation |
| 13 | the log silently drops records when full | log `OVERFLOW` |
| 14 | released vectors forget their deliveries | dropped-delivery count |
| 15 | the clock can run backwards | negative-advance refusal |
| 16 | panic reports success but leaves the interface running | call-after-panic `BAD_STATE` |

### 19.5 Document synchronisation (BUILD-VERIFIED)

```
$ python3 -m psm.render --check
document blocks: OK
```

36 generated blocks (sources, classification totals, nine claim tables, ten
interface tables, ten invariant tables, the decision register, the case counts and
the status vocabulary) are compared byte-for-byte with their renderers. A claim
edited in the registry without regenerating the document fails `make check`.

### 19.6 Whole-repository gates, actually executed

| Command | Result | Notes |
|---|---|---|
| `make check` | **408 tests, OK, 2 skipped** (`skipped=2` are the pre-existing environment-dependent ones) | includes the four new `test_psm_*` modules and the new `psm` target |
| `make psm` | contract `OK`, audit `OK`, conformance `261/261`, document `OK` | new target, also run as part of `make check` |
| `make roadmap` | `OK — 35 milestones in 14 workstreams, destination M19 reachable, document in sync` | `M10` `in_progress` with nine evidence paths; acceptance met, completion withheld by the evidence-level rule |
| `make verify` | `VALID: build/pc5.iso sha256=44f6ec7b…` | `SOURCE_DATE_EPOCH=1763251200` |
| `make boot-test` | **PASSED**, 119 checks, `build/test/boot-evidence.json` | v86 executed the BIOS image |
| `make stubtest` | **PASSED**, 58/58 checks | `unicorn` 2.1.4 in a virtualenv |
| `make ovmf-test` | **exit 3 — NOT TESTED** | no QEMU and no OVMF on this machine; 22 required matrix rows, none executed here. Not a pass and not recorded as one. |

### 19.7 What this section does not establish

* No genuine PS5 system software has been loaded, executed, inspected or made
  compatible. Nothing in `psm/` reads `PS5UPDATE.PUP`; the pointer is untouched.
* The doubles are PC5-authored Python objects. They are not Sony interfaces, and
  the conformance suite says nothing about whether Sony's software would accept
  them.
* No register layout, firmware interface, command encoding, allocation policy,
  file-system layout or authentication behaviour is specified for anything the
  sources do not cover; those rows are `UNKNOWN` and their operations refuse.
* `M1` (physical boot) remains `not_started`; no physical machine was touched.
* The OVMF gate was not executed, so `OVMF-EXECUTED` is not claimed for this
  section. The prior execution recorded in §16/§18 stands as it was.

---

## 20. Repository cleanup: the third-party archive, and project independence

Requested as a repository-cleanup requirement: the tree must contain no
third-party source archive and no references presenting PC5 as derived from,
affiliated with or dependent on another project. This section records what was
removed, what replaced it, and what the gates said afterwards.

### 20.1 What was in the tree

| Item | State at the start of this change |
|---|---|
| a third-party source archive at the repository root (a ZIP of a PS5 static relinker) | tracked as a **Git LFS pointer**, 133 bytes, oid `eb4fd557…4bb2`, declared size 19,749,977 B. The payload was never fetched into any checkout here. |
| `vendor/` | one file: a provenance note about that archive |
| `tools/fetch_*.sh` | a script that re-obtained it, extraction or clone |
| references | 14 tracked files named the upstream project, including `LICENSE`, `README.md`, the architecture specs, the migration plan, the research log and two dated audits |

No build target read the archive; no test referenced it; no release asset
contains it (`gh release view pc5-0.1.0 --json assets` → `[]`); there is no
`.gitmodules`, so it was never a submodule.

### 20.2 What was removed, and what replaced it

* the archive's tracked pointer, the `vendor/` note and the fetch script — all
  deleted from the tree, nothing substituted (no copy, no extract, no rename, no
  new pointer);
* every textual reference: replaced with a neutral description ("a third-party
  source archive"), keeping the audit's own measurements — C/C++ line count, PRX
  library count, NID declarations, stub counts — which are this project's work,
  not the upstream project's identity;
* the licence and README paragraphs that described the archive as a dependency:
  both now state that PC5 vendors no third-party source archive (ADR-025);
* `docs/PROJECT_AUDIT.md` §3 and `docs/architecture/REPOSITORY_AUDIT_2026-11-15.md`
  §8.1 keep the *finding* (what arrived, what it measured, why it cannot serve
  the destination) and record that the archive is now gone. They no longer name
  the project or link to it; both say so explicitly, so the omission is disclosed
  rather than concealed.

### 20.3 The guard, and what it actually checks

`tests/test_project_independence.py` — 9 tests, run by `make check`:

| Check | Fails when |
|---|---|
| tracked paths | any tracked path contains the removed project's name |
| tracked text | any tracked text file contains it (binary files are skipped by sniffing, their paths are still checked) |
| root archives | any `.zip` is tracked at the repository root |
| LFS recurrence | any tracked file references the removed archive's LFS object id |
| vendor directory | anything under `vendor/` is tracked again |
| PC5 identity | the licence, the README destination statement and ADR-025 are present |

The forbidden token is assembled from two literals inside the test, so the guard
does not itself reintroduce the string it forbids.

### 20.4 Gates, actually executed after the change

```
$ make check
Ran 417 tests ... OK (skipped=2)

$ make roadmap
roadmap: OK - 35 milestones in 14 workstreams, destination M19 reachable, document in sync

$ make psm
registry: OK   audit: OK   conformance 261/261   document blocks: OK

$ SOURCE_DATE_EPOCH=1763251200 make verify
VALID: build/pc5.iso  sha256=0c995fb6835b815f116e29d20eaff665fcbf5e1cf2d7b9cc5cc5dcfbf7f14c26

$ SOURCE_DATE_EPOCH=1763251200 make boot-test
PASSED: boot evidence written to build/test/boot-evidence.json   (119 checks)

$ PATH=/tmp/venv-unicorn/bin:$PATH make stubtest
PASSED: 58/58 checks against the stub firmware

$ make ovmf-test
exit 3 — NOT TESTED: no QEMU and no OVMF on this machine (22 required rows, none executed)
```

`make check` went from 408 to 417 tests: the nine new independence guards, with
no existing test weakened, skipped or removed. The ISO hash differs from §19.2
because `README.md` is embedded in the image and its licence paragraph changed.

### 20.5 What this section does not establish

* **History was not rewritten.** The commits that added and updated the archive
  remain; the requirement was to clean the current tree and stop the file
  returning, and a force-push would have concealed rather than cleaned. Anyone
  browsing the commit history of `main` can still see that the archive was once
  there, which is the honest state of affairs.
* **Git LFS storage is outside this repository.** Deleting the pointer removes the
  file from every checkout; it does not reach into the hosting service's object
  store. No claim is made that the 19.7 MB payload has been deleted there, and it
  was never fetched, extracted or opened here.
* **Nothing about Sony's software changed.** This section is repository hygiene:
  it is `BUILD-VERIFIED` at most, and it says nothing about the PS5, `M1`, or any
  execution path.

---

## 21. ISO build manifest, and a defect it found in the writer

Requested as the next engineering step toward a bootable PC5 ISO: make the release
evidence for *ISO build verified* real, and advance what can be advanced while
physical hardware is unavailable. Two things came out of it — a real writer defect,
fixed, and the second reader that found it.

### 21.1 The defect (found here, fixed here)

Writing the independent reader exposed a four-byte error in the primary volume
descriptor. `tools/mkiso.py` wrote the logical-block-size field (a four-byte 7.3.3
value at ECMA-119 §8.4.12) with an eight-byte slice, which shortened the descriptor
by four bytes, and wrote the creation/modification dates 15 bytes wide where 7.4.3
requires 17. Every field after the block-size field therefore sat at the wrong
offset:

| Field | ECMA-119 offset | Where it actually was | Effect |
|---|---|---|---|
| path table size | 132 | 136 | path tables unreachable by a conformant reader |
| type L / M path table | 140 / 148 | 144 / 152 | same |
| volume set identifier, publisher, preparer, application id | 190 / 318 / 446 / 574 | 194 / 322 / 450 / 578 | metadata misplaced |

Nothing PC5 ships reads those fields, so the image booted for two releases and
`make verify` passed. The defect is fixed at the source, and `tools/verify_iso.py`
now checks the path-table fields, their both-endian agreement, and that the
publisher and application identifiers are where the standard puts them — so the
same shift fails `make verify` instead of shipping.

### 21.2 The second reader

`tools/iso_manifest.py` parses the image from its bytes and **does not import the
writer**: primary volume descriptor, directory records, file extents, the El Torito
catalog with its section headers, and the build record inside the image. It writes
a sorted, wall-clock-free manifest and verifies an image against one.

```
$ make verify
VALID: build/pc5.iso  sha256=6cc848514ca1d6450be7b3ed79d48599d158b36ec1ae2617847b8b10d542dba6
manifest: build/pc5.iso.manifest.json (3 files, 4 boot entries, sha256 6cc84851…)
MANIFEST VALID: build/pc5.iso matches build/pc5.iso.manifest.json
```

What the manifest records, for this image:

| Item | Value |
|---|---|
| files | `KERNEL.BIN` 67,028 B · `README.TXT` 1,273 B · `BUILDINF.TXT` 96 B, each with SHA-256 |
| El Torito | validation entry (x86) · initial entry, no-emulation, load segment `0x7C0`, 8 virtual sectors at LBA 24 · UEFI section header · UEFI entry, 2,880 sectors at LBA 26 |
| volume | id `PC5`, publisher `PC5 Project`, application id `PC5` |
| build record | `version 0.2.0`, `build 11612ee`, `date 2025-11-16T00:00:00Z` |

`make release-assets` now ships the manifest next to the ISO, so a third party can
confirm the image's contents without trusting its writer.

### 21.3 What was added around it

* `tests/test_iso_manifest.py` — 17 tests: every file listed with size and hash, both
  boot platforms identified, section headers understood, a flipped payload byte
  caught, a changed catalog caught, a manifest for another image caught,
  determinism, refusal of a non-ISO, refusal of an image without a catalog, a
  regression test for the shifted path-table field, and an assertion that the
  reader does not import the writer.
* `docs/hardware/M1_PHYSICAL_BOOT_PROCEDURE.md` and
  `docs/evidence/M1_RECORD_TEMPLATE.json` — the procedure for the first physical
  boot, with the five evidence levels kept separate.
* `tools/hw_record.py` — the record validator. It rejects an unfilled field, a
  physical-boot claim without a serial log, a missing log file, a medium whose hash
  differs from the image's, an emulator or OVMF run filed as a PC boot, a
  "booted" record that also lists blockers, and the shipped template itself.
* `tests/test_hw_record.py` — 14 tests, including that the template fails validation
  (an unfilled record must never look like evidence) and that a genuinely failed
  attempt validates (only dishonesty is rejected).

**M1 remains `not_started`.** No physical machine was touched; the procedure and the
validator are what make the first attempt checkable, not a substitute for it.

### 21.4 Gates, actually executed

```
$ make check
Ran 434 tests ... OK (skipped=2)

$ make roadmap
roadmap: OK - 35 milestones in 14 workstreams, destination M19 reachable, document in sync

$ SOURCE_DATE_EPOCH=1763251200 make verify
VALID ... sha256=6cc84851… ; MANIFEST VALID

$ SOURCE_DATE_EPOCH=1763251200 make boot-test
PASSED: boot evidence written to build/test/boot-evidence.json   (119 checks)

$ make hw-record
template: OK (rejected as expected)

$ make ovmf-test
exit 3 — NOT TESTED: no QEMU and no OVMF on this machine
```

The ISO changed (the descriptor fix) and therefore its SHA-256 changed with it. The
image still boots: `make boot-test` passes 119 checks against the rebuilt image,
which is how the fix was shown not to disturb the boot path.

### 21.5 What this section does not establish

* No Sony software was loaded or executed, and none is present.
* No physical PC booted the image; level 4 and level 5 evidence do not exist.
* No real UEFI firmware ran here; the OVMF gate reports `NOT TESTED`.
* The manifest proves what is *inside* the image, not that the image is *good* on a
  machine — those are different claims and are recorded separately.

---

## 22. Repository presentation: a front page generated from the graph

Requested as a separate presentation change, kept out of the kernel and hardware work.
The README was accurate but was 186 lines of text and tables, with no images anywhere
in the tree, and a hand-maintained capability table that duplicated state already in
`docs/architecture/roadmap.json` and could drift from it silently.

### 22.1 What was built

| Asset | Purpose |
|---|---|
| `docs/assets/pc5-banner.svg` | wordmark, roadmap version, workstream count, completed-milestone count, destination state, and the "no Sony software" line |
| `docs/assets/pc5-architecture.svg` | eight layers from PC hardware to genuine system software, each badged from the milestones it maps to |
| `docs/assets/pc5-progress.svg` | one bar per workstream split by state, blocked counts, and the longest dependency chain to `M19` |
| `docs/assets/pc5-boot-workflow.svg` | six steps from building the image to Sony software executing, labelled verified / not tested / testing required / blocked |
| `tools/presentation.py` | renders the four figures from `roadmap.json`, writes the README status block, and checks all of it |
| `docs/PRESENTATION.md` | the audit that preceded the change, the design system, and what the presentation refuses to claim |
| `CONTRIBUTING.md` | the contributor entry point the new README links to |

Every figure is geometry and text in an SVG with no external reference, no embedded
font and no image: they render offline, on GitHub, and at any width. Generation is
deterministic — same `roadmap.json`, byte-identical output — so a diff in the figures
always means a change in the data.

### 22.2 Why the status table is generated

The capability table in the README now lives between `<!-- pc5:status:begin -->` and
`<!-- pc5:status:end -->` and is produced by `tools/presentation.py`. Milestone rows
take their label from milestone state; `M1` renders as **NOT TESTED** until a record is
filed under `docs/evidence/` and becomes **VERIFIED** when one is. Gate rows declare a
label but keep it only while the document they name still contains the evidence token,
so a reworded test record cannot leave a VERIFIED chip behind. The architecture figure
forces firmware to **NOT TESTED** rather than inheriting `M1h`'s completion: the hybrid
MBR being verified says nothing about a real machine's firmware.

### 22.3 The check, and what it refuses

`make presentation` runs `tools/presentation.py check`, which fails on a stale figure,
a hand-edited status block, a broken local link or image path, an image without alt
text, a figure without `<title>`/`<desc>`, a figure no longer embedded in the README, a
missing honesty token, or one of the banned claims: "runs PS5 games", "boots on any
PC", "Secure Boot supported", "CI passing", "PS5 firmware included", "production
ready". Each of those has a test in `tests/test_presentation.py` that expects the
failure, alongside the pass case.

### 22.4 What is deliberately absent

* **No screenshots.** No physical boot has happened; an illustration of one would be a
  fabricated result. `docs/assets` contains exactly the four generated figures, and a
  test asserts that.
* **No CI badge.** `ci/build.yml` is a pipeline definition stored in the tree and not
  activated; the README says so instead of showing a passing badge.
* **No compatibility percentage.** The only counting is milestones by state, and the
  README says under the figure what that counts.
* **No claim that the ISO boots Sony software.** The banner carries the opposite
  sentence.

### 22.5 Gates, actually executed on this change

```
$ make check
Ran 474 tests ... OK (skipped=2)      (includes 25 new presentation tests)

$ make presentation
PRESENTATION OK: 4 figures current, 13 status rows match roadmap.json, links and alt text resolve

$ SOURCE_DATE_EPOCH=1763251200 make verify
VALID: build/pc5.iso  sha256=846ef41ca154f12674ac3ded6f0098270d1b9d3ca71f40004a0c84652e60d11e
MANIFEST VALID: build/pc5.iso matches build/pc5.iso.manifest.json

$ SOURCE_DATE_EPOCH=1763251200 make boot-test
PASSED: 119 checks

$ make stubtest
PASSED: 58/58 checks against the stub firmware

$ make ovmf-test
exit 3 — NOT TESTED: no QEMU and no OVMF on this machine
```

The ISO itself is unchanged in content by this work: the same 782 sectors and the same
SHA-256 as the build recorded in §21, rebuilt from scratch to confirm it.

### 22.6 What this change does not establish

Nothing about hardware or Sony software. It changes how the repository presents what it
already knows, and every state it presents is one the repository can already prove.

---

## 23. Repository quality audit: the command contradiction, the identity line, and the gaps around them

A repository-wide audit rather than a feature. What follows is what was found, what was
changed, and the gates that were run afterwards. Everything below was found in the tree
at `7505a39`, not in a description of it.

### 23.1 Findings and fixes

| # | Finding | Evidence | Fix |
|---|---|---|---|
| 1 | **The headline command was inverted.** `README.md`, `CONTRIBUTING.md` and `docs/PRESENTATION.md` all said `make presentation` regenerates the figures; the target ran the *check*, and the write was in an undocumented `presentation-sync` | `Makefile` targets `presentation:` → `tools/presentation.py check` | `presentation` now writes, `presentation-check` reads, `make check` runs the read-only one. The alias is removed rather than kept |
| 2 | `presentation-sync` was not declared `.PHONY`, so a file of that name could shadow the target | `.PHONY:` line omitted it | every relevant target is declared; a test asserts it |
| 3 | **No documentation-wide link checker.** Only `README.md` links were tested; the other 41 Markdown files could rot unnoticed | `tests/test_architecture.py` checks `README.md` only | `tools/docs_check.py` checks every Markdown file: relative links, heading anchors (GitHub slug rule), and `make <target>` mentions. Wired as `make docs-check`, run by `make check` |
| 4 | **The identity line read as a claim.** "A native PlayStation 5 operating system for PC hardware." could be read as asserting PC5 *is* Sony's OS | `product.identity` in `roadmap.json`, repeated in eight documents | identity is now "An independent operating-system project for PC hardware, built toward genuine Sony PlayStation 5 system software.", with `product.identity_note` in the data and an explicit non-affiliation paragraph directly under it in the README. Rendered graph regenerated with `tools/roadmap.py sync` |
| 5 | **Stale gate count.** The evidence-level table claimed `make verify` runs "53 checks"; it runs 80 | `build/verify-report.json` reports 80 | the table no longer hard-codes a count; the historical section says 53 *at that time* and points at §21 |
| 6 | A figure label could clip its bar, and nothing checked text against the canvas | `pc5-progress.svg`, workstream names | labels clip at 34 characters; `tools/presentation.py` now estimates rendered text width and fails if any line leaves its figure. Two tests pin it |
| 7 | **No contributor templates**, so the first issue a stranger opened would arrive without machine, firmware, evidence level or log | `.github/` did not exist | issue forms for bugs and proposals and a pull-request template that makes the evidence level a required checkbox list, plus a security-model contact link |
| 8 | The gate inventory was implicit: which command proves what, and which ones may legitimately say `NOT TESTED` | `docs/TESTING_STRATEGY.md` | new §1b table: each command, what it proves, what it cannot prove, and its exit-code semantics |

Audited and found clean, for the record: no `TODO`/`FIXME` in `kernel/`, `boot/`, `tools/`
or `psm/`; no bare or blanket `except` in the Python tools; no committed secrets, keys or
tokens; no file over 200 KB outside `.git`; `make clean` removes only `build/`, and
`distclean` only the generated firmware manifest; every relative link and anchor in the
tree resolved before the change and still does.

### 23.2 The command contract, now tested

`tests/test_presentation.py` gained a class that reads the `Makefile` and fails if:
`presentation` stops writing, `presentation-check` starts writing, `check` depends on the
writing target, a command is not `.PHONY`, or the old alias returns. Alongside it:
generation repairs stale output, a clean generation writes nothing, `check` leaves every
tracked file byte-identical, and two renders of the same data are identical.

### 23.3 Gates, actually executed on the final commit

```
$ make check
DOCS OK: 42 Markdown files, 25 make targets, links, anchors and commands resolve
PRESENTATION OK: 4 figures current, 13 status rows match roadmap.json, links and alt text resolve
Ran 501 tests ... OK (skipped=2)
```

The emulator, stub-firmware and firmware gates are not part of `make check` and were run
separately, on the same commit:

```
$ SOURCE_DATE_EPOCH=1763251200 make && make verify
pc5: sha256 b1810431c15d04b98628aef678cacd15aca0e96d3c163e055a750d404c10fb7e
VALID: build/pc5.iso  sha256=b1810431…
MANIFEST VALID: build/pc5.iso matches build/pc5.iso.manifest.json   (80 verify checks)

$ make boot-test
PASSED: 119 checks against the emulator

$ make stubtest
PASSED: 58/58 checks against the stub firmware

$ make ovmf-test
NOT TESTED: genuine OVMF firmware and/or QEMU is not available on this machine.
required matrix rows: 22, none executed here   (tool exit 3; make exits 2)

$ make roadmap && make hw-record
roadmap: OK — 35 milestones in 14 workstreams, destination M19 reachable, document in sync
template: OK (rejected as expected)
```

The ISO's SHA-256 changed from `846ef41c…` because `build/build-info.json` records the
commit, and the commit changed. The image's structure and contents did not.

### 23.4 What this audit did not do

* No kernel, bootloader or driver rewrite: nothing was found that justified one in a
  focused quality pass, and inventing work there would be worse than recording the gap.
* No change to what PC5 can execute. The ISO still boots PC5's own loader and kernel; the
  destination `M19` is still blocked; no Sony software exists in the tree.
* No CI activation: the workflow definition still cannot be installed with the token in
  use, so no badge is shown and `make check` remains what CI would run, not proof that CI
  ran it.

---

## 24. The README became a dashboard: generated statistics, committed evidence records

The presentation layer from §22 could be polished but not trusted: it rendered milestone
states, and everything else on the page — check counts, ISO sizes, the PUP's byte counts,
"119 checks" in the quick start — was typed by hand. This section records what replaced
that, and what enforces it.

### 24.1 What was wrong with the old page

| Problem | Evidence |
|---|---|
| Statistics typed into prose | `README.md` carried `119 checks`, `58 checks`, `40 checks`, `135 bytes`, `1.42 GB` in five places; none was generated |
| No record of *when* a gate ran, or at which commit | nothing under `docs/evidence/` except an empty physical-boot template |
| One generated block | `README.md` had exactly one marker pair (`pc5:status`); figures were generated, numbers were not |
| A stale number was invisible | `make presentation-check` compared figures and that one block; a wrong count in a paragraph passed |

### 24.2 What now exists

| Piece | Purpose |
|---|---|
| `tools/validation.py` | runs a gate and writes `docs/evidence/validation/<gate>.json`: command, exit code, result, check counts, commit, date, dirty flag. A record can only exist because the command ran |
| `docs/evidence/validation/*.json` | the committed records the page quotes — `check`, `verify`, `roadmap`, `docs-check`, `boot-test`, `stubtest`, `ovmf-test` |
| five README blocks | `pc5:stats`, `pc5:status`, `pc5:evidence`, `pc5:blockers`, `pc5:pup` — each generated from exactly one source |
| `docs/assets/pc5-dashboard.svg` | six stat cards and a footer with the latest recorded verification, test run, OVMF result and physical-boot count |
| `docs/assets/pc5-evidence.svg` | the evidence ladder, one row per level, each with its own record |
| stray-statistic scan | `make presentation-check` rejects `N checks`, `N milestones`, `N workstreams`, `N-byte`, `N.N GB` anywhere outside a generated block |

### 24.3 Why the numbers cannot lie (much)

* `passed` is only ever written when the command exited 0, and `verify-records` fails a
  record whose verdict and exit code disagree.
* A missing dependency becomes `not-tested` or `skipped` — `make ovmf-test` on this
  machine produces exactly that, and the dashboard shows `not-tested`, not a pass.
* Records are snapshots: each row prints the commit it ran at, and the block says in
  words that an older run is not evidence for the current tree.
* Physical boot has no record here, so its row says `NOT TESTED` and the dashboard's card
  says `not tested` — the field cannot be filled by writing prose.
* No compatibility percentage is generated or displayed: the counts are graph nodes, and
  the text next to the progress figure says so.

### 24.4 Tests

`tests/test_presentation.py` grew from 37 to 59 tests. The new ones change the data and
assert the page changes (a milestone, a workstream, the destination state, a record), that
a missing record never renders as verified, that every generated number equals the data,
that a typed statistic is rejected, and that records with impossible exit-code/verdict
combinations are rejected. `tests/test_docs_check.py` continues to hold links and
commands.

### 24.5 What this does not establish

Nothing about hardware, firmware or Sony software. It establishes that the page's numbers
can be traced to a command that ran, at a named commit, on a named date — and that they
will change when the underlying data changes rather than when somebody remembers.

## 25. The 64-bit foundation: long mode, PC5-owned paging, and a marker that means what it says

Section 24 closed the dashboard. This section is the first work in the repository
that runs **64-bit kernel code** — and the first that has to be careful about what
"64-bit" is allowed to mean.

### 25.1 What was wrong before

Verified from the tree, not from a summary: `boot/uefi/bootx64.S` leaves long mode
with a far return into a 32-bit compatibility segment, clears `CR0.PG`, and enters
a flat 32-bit kernel with `EBX` pointing at `struct pc5_boot_info`. The existing
"64-bit" part of the boot path is firmware's, not PC5's: once the loader hands
over, nothing is in long mode. A `-m64` flag on that kernel would produce an image
that cannot run, which is why this phase added a path instead of flipping flags.

### 25.2 What was built

| Piece | File | Purpose |
|---|---|---|
| 64-bit EFI loader | `boot/uefi64/bootx64.S`, `boot/uefi64/uefi64_main.c` | stays in long mode, reads `KERNEL64.BIN` from the ESP, builds PC5 page tables, `ExitBootServices` with retry, jumps to the kernel |
| page tables | `boot/uefi64/pagetables.c/.h` | identity map below 4 GiB, 2 MiB pages, 6 pages total, uncached framebuffer marking, lookup helper *(this row described the intent; the map covered only the first 1 GiB until `ed9c6a3` — see §27)* |
| boot protocol | `kernel64/include/pc5_boot64.h` | `struct pc5_boot64`, version 1, 112 bytes, magic + size + flags, physical pointers below 4 GiB |
| 64-bit kernel | `kernel64/start.S`, `kernel64/main.c`, `serial64.c`, `cpu64.c`, `kernel64.ld` | proves its own execution environment, reports CPU features, prints the marker last |
| handover record | `boot/uefi64/handover64.h` | `PC5HND02`: protocol version, boot-info size, page-table pages, identity span |
| tests | `tests/test_kernel64.py`, `tests/test_boot64_stub.py`, `tests/test_roadmap_dependencies.py`, `tests/kernel64_machine.py`, `tests/boot64_stub.py`, `tests/boot64_stubtest.py` | build, ABI, page tables, kernel contract, stub-firmware run, roadmap order and version discipline |

The kernel prints `PC5-64BIT-LONG-MODE-OK` **only** after it has checked: a valid
boot-info structure, `CR0.PG`, `CR4.PAE`, a page-aligned non-zero `CR3`, 64-bit
operand size (a value that cannot survive 32-bit truncation), a 16-byte aligned
stack that came from the boot info, and a CPUID long-mode report. The loader never
prints the marker; the kernel never trusts the loader's word for any of it.

### 25.3 Measured results (2026-11-16, this branch)

| Gate | Command | Result | Level |
|---|---|---|---|
| 64-bit kernel build | `make kernel64` | **PASS** — `KERNEL64 OK: 40960 bytes at 0x100000`, ELF64 `ET_EXEC`, no unresolved symbols | BUILD VERIFIED |
| 64-bit loader build | `make uefi64` | **PASS** — `UEFI64 OK: 1474560 bytes sha256=9913b7ea…`; `tools/eficheck.py` 22/22 checks | BUILD VERIFIED |
| page tables | `make check` (`tests/test_kernel64.py::PageTableTests`) | **PASS** — 2048 × 2 MiB entries, present/writable/PS, uncached marking touches only the framebuffer page, bad arguments refused | UNIT TESTED |
| boot-info ABI | `make check` (`BootInfoAbiTests`) | **PASS** — host build of the same header: size 112, offsets 16/40/64/72/88/96/104 | UNIT TESTED |
| kernel contract | `make check` (`KernelContractTests`) | **PASS** — valid handover boots; 12 invalid handovers each print their reason and **no** marker | UNIT TESTED |
| stub firmware, end to end | `make stubtest64` | **PASS — 16/16 checks**; serial order: loader banner → kernel read → page tables → `ExitBootServices` → CR3 → jump → kernel checks → marker → `PC5-64-DONE` | STUB FIRMWARE TESTED |
| stale map key | `make check` (`test_a_stale_memory_map_key_is_retried_not_fatal`) | **PASS** — `stale-key retry=1` then `ok attempts=2` | STUB FIRMWARE TESTED |
| permanently stale key | `make check` (`test_a_permanently_stale_key_stops_the_boot_without_the_marker`) | **PASS** — eight retries, then `PC5-64-LOADER-FAIL=exit-boot-services`, no marker | STUB FIRMWARE TESTED |
| whole host suite | `make check` | **PASS — 571 tests, OK (2 skipped)**; 523 before this phase, 48 added | UNIT TESTED |
| real firmware | `make ovmf-test` | **NOT TESTED** — exit 3: no TianoCore OVMF and no `qemu-system-x86_64` on this machine | NOT TESTED |
| physical hardware | — | **NOT TESTED** — no machine booted this image | NOT TESTED |

The exact serial output of the stub run starts with the loader and ends with the
kernel's own verdict:

```
PC5-64-LOADER-BANNER 64-bit UEFI path (experimental)
PC5-64-LOADER kernel at=0x100000 bytes=40960
PC5-64-LOADER pagetables=0x2009000 pages=6 mapped_2mib=2048 span=0x100000000
PC5-64-LOADER exit-boot-services=ok attempts=1 descriptors=6
PC5-64-LOADER cr3=0x2009000 cr0=0x80000011 cr4=0x50220 long-mode=1
PC5-64-LOADER-JUMP entry=0x100000 bootinfo=0x2007000
PC5-64BIT-KERNEL-ENTRY
PC5-64-PAGING enabled=1 root=0x2009000 kernel=0x100000-0x10a000 loaded=0x100000
PC5-64-REGS wide-operands=ok arithmetic=ok
PC5-64-STACK rsp=0x2012f70 top=0x2013000 aligned=16
PC5-64-BOOTINFO magic=ok version=1 size=112 memdesc=32 entries=6 flags=0xf
PC5-64-CPU vendor=GenuineIntel
PC5-64BIT-LONG-MODE-OK
PC5-64-DONE
```

### 25.4 What this does not establish

- It is not an OVMF run and not a hardware run. The emulator's firmware is
  `tests/boot64_stub.py`, a Python model: it enforces `ExitBootServices` map keys
  and walks PC5's page tables, but it is not firmware.
- It is not process protection. There is one address space, no user mode, no
  guard pages and no NX use; `M2b`'s acceptance criteria are untouched.
- It is not a hypervisor. CPUID's SVM bit is reported as *advertised*, nothing more;
  the SVM ladder is at level 1 of 6 and `M2a` remains `not_started`.
- It is not a change to the release artifact. `make iso`, the release ESP, the
  32-bit kernel and the 32-bit UEFI loader are untouched, and
  `ReleaseIsolationTests` fails if `make -n all` starts to build 64-bit work.

### 25.5 Two defects the tests found while being written

1. **The kernel was being loaded at a firmware-chosen address.** A flat,
   non-relocatable image linked at `0x100000` must be loaded there; the first
   version let `AllocatePages` pick an address and the kernel faulted on its first
   string literal (`0x101010` was a link-time address, not a runtime one). The
   loader now requires exactly the link address, and both sides assert
   `PC5_KERNEL64_LINK_BASE`.
2. **The spare memory descriptor was reaching the kernel.** Firmware sizes the map
   with one extra zeroed descriptor; the loader copied it, and the kernel refused
   the handover exactly as designed. The loader drops zero-length descriptors now,
   and the kernel's strictness stays: a zero-page descriptor inside the declared
   map is still refused, as `bootinfo-memmap-descriptor` (§26 renames the reason to
   say what it checks).

## 26. The 64-bit handover audited: placement, bounds, and a contract that is checked

Recorded 2026-11-16. Start of this work: `7d5ef2e` (the head of PR #26). The gates
below were run by `make record-validation` in a clean worktree; each record in
`docs/evidence/validation/` names the commit it ran at, with `dirty: false` and
`code_revision` equal to that commit. The work was an audit of the 64-bit path, so
this section records what was *rejected* as well as what was confirmed: several of
the claims checked against the source did not survive contact with it.

### 26.1 Findings

| # | Claim | Verdict | What the source actually said |
|---|---|---|---|
| 1 | `pc5_pt_mark_uncached()` can write outside the six-page block | **confirmed** | `first`/`last` came from the raw address with no `last <= 2047` check, so a framebuffer at or above 4 GiB wrote page-directory entries into whatever followed the block |
| 2 | The boot-info contract claimed validation the kernel never did | **confirmed** | the v1 header documented that every pointer is validated; `kernel64/main.c` checked magic, version, size and stride, then dereferenced the memory map, the stack, the page tables, the framebuffer and the RSDP |
| 3 | The loader assumed firmware placed the application below 4 GiB | **confirmed** | nothing asked `EFI_LOADED_IMAGE_PROTOCOL`; the loader switched `CR3` and continued executing code it had not verified was mapped |
| 4 | A high framebuffer/RSDP address could be passed over as usable | **confirmed** | the address was copied into the boot info regardless of whether the map covered it |
| 5 | The stub harness contradicted itself about page-table walking | **confirmed** | the module docstring said the harness "does not walk the page tables"; the paging builder said Unicorn walks them whenever `CR0.PG` is set. Both could not be true, and the second is |
| 6 | Validation records named the wrong commit | **confirmed** | all 11 records said `commit=e69d2a6, dirty=true` while head was `7d5ef2e`: `HEAD` was recorded without checking the tree matched it |
| 7 | The kernel trusted `stack_top` before validating it | **confirmed while writing the tests** | `kernel64/start.S` loaded `RSP` from the unvalidated boot info, so a bad stack faulted on the next `push` and the check that would have reported it never ran |
| 8 | `make kernel64` from a clean tree was broken | **confirmed while running the gates** | the link rule passed `$^` to `ld`, which includes the linker script already named by `-T`: `ld: error: linker script file 'kernel64/kernel64.ld' appears multiple times` |
| 9 | `PC5_PT_PAGES` is 6, or the `total`/`pd_pages` bounds in `build_identity` are wrong | **disproven** — see §27 | 1 PML4 + 1 PDPT + 4 PD = 6 pages, 2048 × 2 MiB = 4 GiB, which is exactly representable in the walk |
| 10 | The 4 GiB span cannot be described by the map | **disproven — SUPERSEDED, see §27** | `(pages − 2) × 512 × 2 MiB` = 4 GiB exactly for 6 pages; the kernel now recomputes this and refuses a boot info whose `identity_span` disagrees |

### 26.2 What changed, and why

- **Page tables** (`boot/uefi64/pagetables.c`): bounded walk, `pc5_pt_range_supported()`
  for an overflow-free "can this be mapped at all", clipping with an explicit count
  of entries changed, `PC5_PT_MAX_INDEX`, no 1 GiB leaves in the lookup. A resource
  that cannot be mapped is now *represented* as unmapped rather than silently
  ignored.
- **Boot protocol 2** (160 bytes, `kernel64/include/pc5_boot64.h`): adds
  `stack_size`, `pagetable_addr`, `pagetable_pages`, `identity_span`,
  `loader_image_addr`, `loader_image_size`, plus `PC5_BOOT64_F_UNMAPPED_FRAMEBUFFER`
  and `..._RSDP`. Offsets are asserted in both translation units because
  `bootx64.S` loads `stack_top` from offset 88.
- **Loader** (`boot/uefi64/uefi64_main.c`, `bootx64.S`): placement validated from
  the loaded-image protocol before `CR3`; every allocation asserted inside the span;
  the switch itself moved into assembly that masks interrupts, verifies the write,
  restores firmware's root on failure and is the only code between the two worlds.
- **Kernel** (`kernel64/main.c`, `start.S`): staged validation (structure, header,
  memory map, mandatory ranges, environment, optional resources), shared arithmetic
  in `kernel64/include/pc5_map.h` so both sides apply one rule, and boot on the
  image's own stack until the loader's stack has been validated.
- **Harness** (`tests/uefi_stub_firmware.py`, `tests/boot64_stub.py`): honest
  loaded-image reporting, configurable placement, firmware page tables for whatever
  it placed, and a docstring that says what the emulation does and does not prove.
- **Evidence** (`tools/validation.py`): the dirty state is measured before each gate
  and recorded; a record that passed on a dirty tree is now a verification failure.

### 26.3 Commands and results

Every gate below was run by `make record-validation` in a clean worktree, and the
exit code and summary in each record is that run's output.
`ovmf-test` is **not tested**: TianoCore and `qemu-system-x86_64` are absent here,
so the harness exits 2 and the record says `not-tested` with that reason. No
hardware run, and no OVMF run, is claimed anywhere in this section.

| Gate | Command | Result | Exit | Evidence |
|---|---|---|---|---|
| unit + integration | `make check` | passed | 0 | 612/612 tests |
| artifact | `make verify` | passed | 0 | `MANIFEST VALID: build/pc5.iso matches …` |
| emulated BIOS boot | `make boot-test` | passed | 0 | boot evidence written |
| 32-bit stub firmware | `make stubtest` | passed | 0 | 58/58 checks |
| 64-bit kernel | `make kernel64` | passed | 0 | flat image, no unresolved symbols |
| 64-bit ESP | `make uefi64` | passed | 0 | PE32+ passes `tools/eficheck.py` |
| 64-bit stub firmware | `make stubtest64` | passed | 0 | 22/22 checks |
| documentation | `make docs-check` | passed | 0 | 44 Markdown files, links and anchors resolve |
| presentation | `make presentation-check` | passed | 0 | 6 figures, 5 blocks, 13 status rows |
| roadmap | `make roadmap` | passed | 0 | 35 milestones, document in sync |
| records | `make validation` | passed | 0 | `records: OK` |
| real firmware | `make ovmf-test` | **not tested** | 2 | TianoCore and `qemu-system-x86_64` not installed |

Test counts, exactly: 46 tests added and 5 removed, so the suite went from 571 to
612. The 46 are 19 in the new `tests/test_pagetables64.py`, 24 in
`tests/test_kernel64.py` (25 → 49: version-2 contract cases plus the
`PlacementPolicyTests` class) and 3 placement runs in `tests/test_boot64_stub.py`
(5 → 8). The 5 removed were the duplicated `PageTableTests` class in
`tests/test_kernel64.py`; the same properties are covered in the new file, which
adds canary pages either side of the page-table block. `tests/boot64_stubtest.py`
also grew from 16 to 22 checks without changing the test count.

### 26.4 What the placement runs actually show

The stub firmware now places the application itself, so these are executions, not
simulations of an intention:

```
image at the top of the map      PC5-64-LOADER image=0xfe00000-0xfe03000 below-span=1
                                 PC5-64-PAGING enabled=1 root=0x200e000 …
                                 PC5-64-LOADER image=0xfe00000 size=0x3000 range=ok
handoff regions at the top       PC5-64-LOADER pagetables=0xff01000 pages=6 …
                                 bootinfo=0xff00000 stack=0xff0b000
                                 PC5-64-STACK rsp=0xff0af20 … aligned=16 range=ok
```

Both runs finish with `PC5-64BIT-LONG-MODE-OK` and `PC5-64-DONE`, and Unicorn
translates every fetch after the switch through PC5's tables, so a region the map
missed would fault instead of passing.

The one case that cannot be executed faithfully is an application placed *above*
4 GiB: `BOOTX64.EFI` carries 88 32-bit base relocations, so above 4 GiB the
emulated code would no longer be the code under test. That side is covered by
evaluating the loader's own rule on the host (`PlacementPolicyTests`): a range
ending exactly at 4 GiB is accepted, one starting at or crossing it is refused, and
empty, wrapped and above-the-limit ranges are refused.

### 26.5 What this does not establish

- No OVMF, no hardware: the marker is evidence about PC5's code, not about
  firmware compliance, and not about the MMU's treatment of cache attributes, PAT,
  `CR0.WP`, TLB invalidation or memory types.
- The map still stops at 4 GiB. Resources above it are now *represented* as
  unusable rather than silently passed over; widening the map is future work.
- The 64-bit kernel is still minimal: no interrupts (it boots with them masked and
  installs no IDT), no APIC, no timer, no SMP, no allocator, no protection. `M2a`
  remains `not_started`.
- The 32-bit release path is unchanged: `git diff --stat 8aab6d3…196f504 -- boot/uefi
  boot/loader.asm kernel kernel/include` is empty, and `ReleaseIsolationTests`
  fails if `make -n all` starts to build any 64-bit work.

## 27. Correction to §26: the identity map did not cover 4 GiB

Recorded 2026-11-16. This section **supersedes rows 9 and 10 of §26.1** and is
appended rather than merged into it: §26 is a dated record of what that audit
concluded, and rewriting it would erase the evidence of how the wrong conclusion
was reached. Nothing above this line has been deleted or reworded apart from the
two row labels pointing here.

### 27.1 What §26 got wrong

Rows 9 and 10 asked the right question and answered it wrongly. Both computed the
map size from the **page-directory** entries:

> `(pages − 2) × 512 × 2 MiB` = 4 GiB exactly for 6 pages

That arithmetic is correct as far as it goes, and the conclusion drawn from it —
"the 4 GiB span cannot be described by the map: **disproven**" — does not follow.
It counts the capacity of the four PD pages while ignoring what makes those pages
*reachable*. A PDPT entry is consumed by a page-directory pointer, and one page
directory covers exactly 1 GiB when its leaves are 2 MiB pages. Four PD pages
therefore require **four** PDPT entries. The code installed one.

So the question §26 tested was "is there room for 2048 leaf entries?" (yes), when
the question that mattered was "does the installed hierarchy reach them?" (no).
`pc5_pt_build_identity()` wrote all 2048 leaves and returned 2048, and
`pc5_pt_lookup()` — the function that performs the real walk — would have shown the
gap immediately at any address ≥ 1 GiB. No test asked it to.

### 27.2 The defect, exactly

`boot/uefi64/pagetables.c` stored only `PDPT[0]`:

```c
store(pc5_pt_pdpt(pt), pc5_pt_pd_base(pt) | 1ull | 2ull);   /* entry 0 only */
```

**Impact.** Only `[0, 1 GiB)` translated. The outer three quarters of the "4 GiB
map" was unreachable while the function reported success and the loader's own
`mapped != PC5_PT_SPAN / PC5_PT_2MIB` check passed. Concretely:

- a framebuffer anywhere in `[1 GiB, 4 GiB)` passed `pc5_range_within()` against
  `identity_span`, passed `pc5_map_covers()` against the descriptor set, and was
  reported `usability=ok` with no mapping behind it;
- `pc5_pt_mark_uncached()` reported entries changed while writing PWT|PCD into PD
  pages that no PDPT entry referenced — the marking was applied to memory the MMU
  never consulted;
- `PDPT[1..3]` were never initialised, so stale firmware values survived inside an
  active paging structure.

This was a **false report**, not an immediate fault: `kernel64` prints framebuffer
usability but does not yet dereference `framebuffer_addr`. It becomes a hard fault
on the first write to an unmapped framebuffer.

### 27.3 The correction

Fixed in `ed9c6a3` on `arena/17f0c4f8-pc5`:

- one PDPT entry per gigabyte, each pointing at its own PD page, with the count
  derived from the span so a shorter map installs fewer entries and every
  installed entry still references a PD inside the six-page block;
- all six pages zeroed before use, including the whole PDPT, so no firmware value
  can survive as a phantom mapping;
- `PC5_PT_TABLE_FLAGS` (present|writable, no page-size bit) distinguishes a table
  pointer from a 2 MiB leaf, where the byte would be a reserved-bit violation;
- the span is validated before anything is written, so a refused call cannot leave
  a half-built hierarchy active.

A second defect in the same path, `ec16c65`: `alloc_below_4g()` passed `0` as the
*input* maximum address to `EFI_ALLOCATE_MAX_ADDRESS`. UEFI 2.9A §7.2.1 defines
that input as the highest address the returned range may occupy, so zero describes
a range that cannot exist. It was invisible because the stub firmware ignored the
allocation type entirely. The bound is now `PC5_IDENTITY_LIMIT - 1`, the result is
checked rather than trusted, and the stub implements all three `EFI_ALLOCATE_TYPE`
modes.

### 27.4 What the tests now prove, and at what level

`tests/test_pagetables64.py` (`IdentityHierarchyTests`) compiles the real
`pagetables.c` on the host with the loader's own flags and walks the installed
hierarchy:

- the returned count equals the number of 2 MiB pages the walk actually reaches —
  **2048 vs 512** against the previous implementation, which is the defect in one
  assertion;
- all four PDPT entries point at their own PD page, with the correct flags and no
  page-size bit;
- translation succeeds immediately below, at and above each 1 GiB boundary and at
  the last 2 MiB page of the span, and fails at the 4 GiB exclusive edge;
- removing one PDPT link by hand makes `pc5_pt_lookup()` stop translating even
  though the leaf is still populated — the exact false positive that was shipped;
- `pc5_pt_mark_uncached()` reaches a leaf in **every** 1 GiB region and does not
  leak to neighbours;
- `PML4[1..511]`, `PDPT[4..511]` and the unused PD entries are zero.

`tests/test_boot64_stub.py` (`AllocateMaxAddressTests`) drives the real
`BOOTX64.EFI` and `KERNEL64.BIN` under the stub and asserts that the loader sends a
satisfiable bound, that every allocation lands inside the limit, that firmware
which ignores the bound or returns a straddling range stops the boot, and that the
handover structures translate through the hierarchy that was installed.

**Evidence level.** This remains host-and-stub execution: the real production C,
the real loader PE32+ and the real kernel binary, under emulation. It is **not**
OVMF, not physical hardware, and not Sony execution. `make ovmf-test` still reports
NOT TESTED on this machine. The claim this section supports is that the page-table
hierarchy the loader installs covers the span it advertises and that the
allocations it requests are satisfiable under the UEFI contract — nothing about
real firmware.

## 28. The 64-bit boot became a gate, and the release record became attributable

**Run:** 2026-11-23, workspace at `d014a5b` (clean tree), QEMU 11.0.2 with the EDK II OVMF
shipped in the same package, TCG only. **`make check` → `suite and all gates passed`, 1075
tests OK (117 skipped).** The two firmware gates, run separately because they need an
emulator:

| Command | Result | Checks | Matrix rows |
| --- | --- | --- | --- |
| `make PC5_UEFI=64 ovmf-test` | **exit 0**, `PC5-OVMF-BOOT-OK` | 78 PASS, 0 FAIL, 2 NOT TESTED | 43 = 37 verified + 5 external + 1 not-tested |
| `make ovmf-test` (32-bit) | **exit 0**, `PC5-OVMF-BOOT-OK` | 63 PASS, 0 FAIL, 1 NOT TESTED | 35 = 30 verified + 5 external |
| `SOURCE_DATE_EPOCH=1700000000 make PC5_UEFI=64 iso verify` | `VALID`, `MANIFEST VALID`, exit 0 | ISO `1bae49a3284e92a8…`, 1806336 B | ESP `build/efi64-release.img` byte-identical to the copy inside the ISO |
| `SOURCE_DATE_EPOCH=1700000000 make iso verify` | `VALID`, `MANIFEST VALID`, exit 0 | ISO `ed98325d72fdb867…`, 1601536 B | ESP `build/efi.img` byte-identical |
| `make PC5_UEFI=64 metadata`, `make metadata` | exit 0, `checks=2 verdict=PASS` | `iso_structure` + `firmware_boot` | the firmware verdict is now part of the release record |

**What changed, and what it caught.** `tests/ovmf_boot.py` had been grading every run with
the 32-bit marker set; the 64-bit path speaks a different one (`PC5-64-LOADER-BANNER`,
`PC5-64BIT-LONG-MODE-OK`, `PC5-64-DONE`, `PC5-64-LOADER-FAIL=<name>`), and grading a 64-bit
log with 32-bit eyes means the *absence* of `PC5-BOOT-OK` — the failure condition — reads as
nothing went wrong. The harness now holds two `Profile`s, each owning its banner, success and
stop markers, failure pattern, boot-protocol version and log→stage mapping, and which one a
run is graded with is **read from the medium**: both flavors are written to
`build/pc5.iso`, both ESPs carry `EFI/BOOT/BOOTX64.EFI`, and the only medium-visible
difference is `KERNEL.BIN` against `KERNEL64.BIN` inside the El Torito ESP. Three defects
surfaced the moment the gate could see both paths, all in the artifacts rather than the logs:

- a run booted `build/pc5.iso` while pointed at `build/efi64-release.img`, and the ISO still
  held the 32-bit ESP — reported as four unrelated size failures. The gate now compares the
  El Torito boot image against the file it was handed, byte for byte, and the *pairing* is
  the failure that gets named;
- the record inside the image (`BUILDINFO.TXT`) had drifted from the image: `uefi-path: 64`
  over a 32-bit ESP, quoting the other mode's loader digest. `the ISO's build record names
  the medium it describes` now re-derives that from the volume, and the build writes
  `$(BUILD)/pc5.iso.stamp` (`mode=`, `commit=`, `loader=`) so a mode switch or a commit
  regenerates the record instead of leaving a stale one — a timestamp-only dependency cannot
  notice either, because in a shared `build/` directory no mtime the record depends on moves;
- the negative controls inherited the same blindness: `missing kernel → E2` is 32-bit
  vocabulary, so a 64-bit boot that *did* report `kernel-not-found` was graded as a
  control that failed to fire. Expectations are per-profile now, and the small-memory control
  stops on the profile's own markers.

**Two NOT TESTED rows on the 64-bit leg, both stated with a reason.** The disk-style ESP
partition path does not exist in this medium (structural). The corrupted-header control
cannot be built, because `KERNEL64.BIN` carries no header to corrupt — it begins
`cli; mov rbx, rdi`, the first instructions of `kernel64/start.S`. That row is reported
rather than passed, and `the gate's kernel-header claims match the kernel image on the
medium` fails the run if the claim and the medium ever disagree, so the control becomes
enforced the moment the image format gains a header, with no flag left to remember. The
32-bit leg keeps the enforced version (`… as E4`, PASS).

**Attribution, which is what makes the numbers above belong to a commit.** `BUILDINFO.TXT`
inside the ISO carries `build:` (with `-dirty` when tracked files differ from HEAD, so an
artifact no commit produced cannot be read as one), `commit:` (full object id),
`uefi-path:` and `esp-loader:`; `make metadata` embeds the firmware gate's verdict — profile,
cold/warm stop markers, matrix row counts, ISO and ESP digests, firmware and QEMU versions,
and the NOT TESTED row names — and refuses with exit 3, both digests quoted, when the report
on disk describes a different image than the one being described. That refusal was observed on
the real files while this was being written, not only in a test.

**Evidence level.** Genuine TianoCore OVMF under TCG, the release artifact itself, cold and
warm, with the negatives run against mutants built from it. Not physical hardware, not Sony
firmware, not a PS5, and **not CI**: no GitHub Actions run backs any of this, and this
milestone was not granted Actions activation. `tr=loaded` is not claimed — TR is still not
loaded, `PC5-64-TSS … tr=not-loaded reason=ltr-unverifiable-here` is what the boots print,
IST1/IST2 are allocated and validated but no exception has yet been shown to run on them, and
`make emulator-probe` remains the record of what the emulator can and cannot establish. Full
serial logs for all four boots, `summary.json`, and a `verification.sh` that re-derives 35
assertions from them: `docs/evidence/2026-11-23-64bit-boot-gate/`.

*Superseded on 2026-11-24, and left as written above:* §30 loaded TR, and §31 delivered a real
double fault onto IST1. `tr=not-loaded reason=ltr-unverifiable-here` was what the boots printed at
that commit; the paragraph describes the run it records, and the correction belongs to the later
sections rather than to this one. Note the one sentence that has not changed meaning: *no* claim
about an exception running on the IST stacks was made in §29 even though the fields were all
present, which is the standard §31 was written to meet.


## 29. The 64-bit kernel image became a format, and three gates enforce it

**Run:** 2026-11-24, workspace at `1fdb24e` on `arena/5e36f38b-pc5`, Unicorn 2.1.4 from
`requirements-harness.txt`, host GCC for the validator probe. §28 left one sentence open — the
64-bit image "has no header to corrupt yet" — and this is the work that closes it, from the
limitation entry `docs/KNOWN_LIMITATIONS.md` **UEFI boot path** and
[`docs/engineering/KERNEL-IMAGE64-HEADER-DESIGN-v1.md`](engineering/KERNEL-IMAGE64-HEADER-DESIGN-v1.md).

**What was built.** `KERNEL64.BIN` now begins with a 128-byte header (`PC5K64IM`, format
version 1) declaring the image's exact length, load address, entry offset, alignment
requirements, architecture in two encodings, `.bss` size, a payload checksum, a header CRC, and
zero-filled reserved fields. `kernel64/kimage64_head.c` puts it in a `.head` section that
`kernel64/kernel64.ld` places first and pads to a page, so no instruction address moved;
`tools/kimage64.py --stamp` fills in what only exists after the link, reading `__kernel_start`,
`_start64`, `__bss_start` and `__bss_end` out of the ELF rather than being told any of them;
`boot/uefi64/kimage64.c` validates all of it and returns a *named* refusal. The image grew
204800 → 208896 bytes (`0x33000`) and the entry point from `0x100000` to `0x101000` — the
header page, not a code change — so `0x32000` in §28's tables and in
`docs/evidence/2026-11-23-64bit-boot-gate/` describes the pre-header artifact.

| Command | Result |
| --- | --- |
| `python3 -m unittest tests.test_kimage64` | **OK, 21 tests** |
| `TMPDIR=/tmp PATH=/tmp/pc5-venv/bin:$PATH make stubtest64` | **`64-BIT STUB FIRMWARE OK: 45/45 checks passed`** (was 41) |
| `python3 -m unittest tests.test_boot64_stub.KernelImageHeaderTests` | **OK, 4 tests, 15.4 s** |
| `python3 -m unittest tests.test_ovmf_gate_profiles tests.test_ovmf_harness` | **OK, 87 tests** (2 skipped: no QEMU/OVMF in this unit run) |
| `python3 -m unittest tests.test_release_esp tests.test_tools tests.test_kimage64` | **OK, 41 tests** |
| `SOURCE_DATE_EPOCH=1700000000 make PC5_UEFI=64 iso verify` | `VALID` (ISO `ae38b319d293ad52…`), `MANIFEST VALID`, and the new `KERNEL64.BIN in the ISO is a valid PC5 64-bit kernel image` row reports `v1, 208896 bytes, entry 0x1000, .bss 172032 bytes, payload checksum verified` |
| `python3 tools/docs_check.py` | `DOCS OK: 80 Markdown files, 38 make targets` |

**The unit layer.** `tests/test_kimage64.py` cross-pins `tools/kimage64.py`'s field table and
the C struct against the header's `#define`s — names, offsets, widths and the total — compiles
the loader's *real* validator for the host with GCC and drives all fifteen named refusals
through it, requires the C verdict and the Python verdict to agree over a corpus of hand-built
images, and walks a truncation ladder: 0, 8, 64, 120 bytes are all `kimage64-truncated`, 128 —
a header that declares a 208896-byte image which is not there — is `kimage64-image-size`.

**The emulator layer.** `make stubtest64` gained four checks over the loader's own serial
lines, read as fields rather than substring-matched: `image` equals the image the stub was
given and the address range the loader reports is exactly `base + image_size`; `entry` equals
`JUMP entry - load base` (a header saying `0x1000` while the loader jumped at `+0` is the
failure this exists for, and the first build drew it); `header_crc` is recomputed here with an
independent FNV implementation; and `arch`/`machine` must agree with the host and with each
other. `tests/test_boot64_stub.py::KernelImageHeaderTests` then boots the **release**
`BOOTX64.EFI` — the same bytes, the same link, which is why `artifacts()` builds them rather
than reusing the objects other cases hand-compile — fourteen times over `tools/kimage64.py`'s
mutations, plus a truncation: each must abort with exactly the refusal that mutation earns,
and must never reach `PC5-64-LOADER-JUMP`. `mutate_bytes` self-checks the mutation→refusal
mapping, so a mutant that stays valid is a tool failure rather than a vacuous pass.

**The firmware layer.** `tests/ovmf_boot.py` reads the kernel header kind from the bytes of the
ESP it was handed, and its `HEADER_VOCABULARY_CHECK` now compares the *refusal vocabulary* the
gate claims against what the artifact is: `bios8` ⟺ the 32-bit `PC5K` check, `kimage64` ⟺ the
named `kimage64-*` set. Its x64 profile gained three good-path stages (header validated,
declared length == file == FAT record, and the jump target equal to `load_base + entry_offset`
as declared) and three mutant boots (`arch`, `image-size`, `checksum`) alongside the
`magic`/bad-signature row, each of which additionally fails if the log contains `JUMP` or
`BANNER`. `required_rows()` derives one control row per declared refusal, so adding a refusal
without a control breaks the matrix instead of going unmeasured.

**Two bugs in the tests, found by running them.** Both are the reason this section exists
alongside the code.

- `tests/test_ovmf_gate_profiles.py` grades the x64 stages against a **hand-written log
  fixture**, not the artifact. The moment the stages began reading `kernel-header …` out of the
  loader's serial line, three rows failed — `image_size` reported `None vs 208896` — because
  the fixture still showed the pre-header log, where the jump went to `0x100000` and no header
  line existed. The fixture is now a header-era log. The lesson is written into the file: a
  fixture that describes an older artifact makes a *correct* implementation look broken, and
  the only defence is that the same commit updates both.
- `tests/test_release_esp.py::ReleaseAttributionRebuild` builds in a `git clone` of HEAD and
  then copies **one** named file (`Makefile`) into it — so every case in that class graded the
  committed tree while reporting the working one, and an uncommitted build change was invisible
  to exactly the tests meant to catch it. With the header's new sources untracked, seven cases
  failed in the clone for reasons that had nothing to do with their assertions. `_scratch()` now
  installs everything `git status` reports, skips build output and the evidence directory, and
  commits it with `git add -A` (a fixed list plus `commit --all` cannot see a *new* file, and
  `git clone` hands out HEAD). Asserting "the Makefile arrived" on the copied list was itself
  briefly a false green: the porcelain line's two status columns mean `stdout.strip()` shifts
  the first path left by a character — `kefile` — so the parse is now explicit and the Makefile
  is verified by comparing bytes.

**Both firmware legs re-run at this HEAD, and both are green.**

| Command | Result | Checks | Matrix rows |
| --- | --- | --- | --- |
| `make PC5_UEFI=64 ovmf-test` | **exit 0**, `PC5-OVMF-BOOT-OK` | 88 PASS, 0 FAIL, 1 NOT TESTED (89) | **49** = 43 + 6 kernel-header rows |
| `make ovmf-test` (32-bit) | **exit 0**, `PC5-OVMF-BOOT-OK` | 63 PASS, 0 FAIL, 1 NOT TESTED (64) | 35 — byte-for-byte the previous run's counts |

The 64-bit cold and warm logs each carry
`PC5-64-LOADER kernel-header version=1 header=128 image=208896 entry=0x1000 bss=172032
arch=x86-64 machine=62 checksum=ok` followed by `LOADER-JUMP entry=0x101000`, and the report's
derived markers agree with the serial text in both boots (`kernel_header_image ==
kernel_bytes_read == handoff_bytes == 208896`, `kernel_jump_entry == kernel_load_base +
kernel_entry_declared`). Four mutant ISOs booted under the same firmware and were refused by
name — `kimage64-magic`, `kimage64-arch`, `kimage64-image-size`, `kimage64-checksum` — each with
`kernel_entry: false` in the report and no `JUMP`/`BANNER` in its log. The release ISO is
`ae38b319d293ad52…` (`make PC5_UEFI=64 iso verify`'s own line), recorded in the report next to
`commit: 1fdb24e519d59b49152011ecc52e5da4ad57dadd`; the 32-bit ISO is `c3714d7978010e39…`.
`docs/evidence/2026-11-24-kernel-image-header/` holds the six new logs, both reports, both
matrices and a `verification.sh` that re-derives all of it; `docs/evidence/2026-11-23-64bit-boot-gate/`
(`OK (43 checks)`) stays as the record of the run *before* the header existed, and its
`0x32000`/204800 numbers describe that artifact, not this one — which is why it is kept rather
than corrected.


## 30. TR is loaded: the selector was wrong for three milestones, and executing LTR found it

**Run:** 2026-11-24, workspace on `arena/5e36f38b-pc5` at `898f708` with this work uncommitted,
QEMU 11.0.2 + the EDK II OVMF from the same package, TCG only; Unicorn 2.1.4 for the stub.
**Milestone 8, PR A.** §29 left `tr=not-loaded reason=ltr-unverifiable-here` standing, and this is
the work that removes it — by loading the register, not by editing the marker.

**What was wrong.** `PC5_TSS64_SELECTOR` was `0x28`. The TSS descriptor is 16 bytes, written as
`g_gdt[4]` (low half) and `g_gdt[5]` (high half), and a GDT selector is the byte offset of the
descriptor divided by 8 — which is exactly why the kernel's own code segment at `g_gdt[1]` is
`0x08` and its data segment at `g_gdt[2]` is `0x10`. Slot 4 begins at byte 32, so the descriptor
is named by `0x20`; `0x28` names the *high half*, whose low bits are `base[63:32]` and which
therefore reads as a not-present, type-0 descriptor. The correct selector is `0x20`.

**Why that had never been seen.** Nothing executed `ltr`. `pc5_tss64_build()` encodes the pair and
decodes the same pair, so the builder agreed with itself; the IDT gates were deliberately *not*
armed, because arming them requires TR to name the TSS and nothing had loaded it; and the gate
asserted the unloaded TR that a never-attempted load necessarily produces. `docs/evidence/2026-11-22-tss-and-ist/ltr-measurement.md`
had recorded the fault under the stub, ruled out six hypotheses about the *emulator*, and named the
cause as undetermined — an erratum now follows that write-up, because the seventh hypothesis was in
this repository's own header.

**What was built.** `pc5_tss64_check_loaded()` reads the descriptor *through GDTR* (not out of the
builder's array), checks present/S/DPL/type/L/G, decodes base and limit against the granted frame
and `PC5_TSS64_SIZE - 1`, and then reads the TSS through that base: IST1, IST2, the I/O-map base,
and RSP0..2. `pc5_tss64_arm()` brackets the instruction with it — refusing a descriptor that is
already busy *before* the load (a real CPU raises #TS for that, and the kernel now says so instead
of faulting inside the setup of the thing meant to catch faults) — executes `ltr %ax` and `str %ax`,
requires STR's readback to equal the selector, and re-runs the checks expecting the busy transition
it performs in memory. `report_tss()` arms `#DF` → IST1 and `NMI` → IST2 **if and only if**
`tr_armed`, and the pairing rule that used to read "not loaded, so not armed" is now a two-way
equivalence in both the stub and the firmware gate.

| Command | Result |
| --- | --- |
| `make stubtest64` | **`49/49 checks passed`** (was 45; one check replaced by five) |
| `python3 -m unittest tests.test_tss64` | **OK, 14 tests** — 3 new: the activation battery, the selector/`nm`-free layout assertion, and the `tr_armed`-before-install rule |
| `tests/test_tss64.py::Tss64ActivationMutationTests` | 4 mutants of the new checks, each caught: GDTR bounds widened, `available` redefined to `busy`, the I/O-map read trusted, and the descriptor read from the builder's array instead of GDTR's |
| `python3 -m unittest tests.test_ovmf_gate_profiles tests.test_ovmf_harness tests.test_idt64 tests.test_kernel64 tests.test_tss64` | **OK, 187 tests** (67 skipped without QEMU) |
| `make PC5_UEFI=64 iso` + a hand-run cold boot under TianoCore | `PC5-64-TSS status=ok … ist-df=1 ist-nmi=2 tr=loaded str=0x20 desc=0x1000/103 busy=available-to-busy iomap=104`, no `PC5-64-EXCEPTION`, `PC5-64BIT-LONG-MODE-OK`, `PC5-64-DONE` |

**The distinction this run can make, and the one it cannot.** `busy=available-to-busy` is a write
into the GDT that only a CPU performing a load makes, so the four fields above together are evidence
about the machine rather than about the log — and they are read back through GDTR and through the
descriptor, so a kernel that printed the right words without loading anything fails at least three
of the five new gate rows (`tests/test_ovmf_gate_profiles.py::test_a_log_that_only_claims_tr_is_loaded_fails_the_row`
asserts exactly that by deleting the readbacks from a good log). What is **not** yet shown is the
consequence that matters most: that a `#DF` or an NMI is *delivered* on those stacks. The gates are
now armed and the TSS is live, which is the precondition; the delivery evidence is PR B and PR C.
`make check`, both firmware gates, and the Milestone 8 evidence directory are owed a run at the
commit that lands them, and none of this is claimed until they are green.


## 31. A double fault, taken on purpose, lands on the IST stack — and finds the second TSS bug

**Run:** 2026-11-24, workspace on `arena/5e36f38b-pc5`, QEMU 11.0.2 with the EDK II OVMF from the
same package, TCG only; Unicorn 2.1.4 for the stub.  **Milestone 8, PR B.**  §30 made the TSS live;
this is the measurement the TSS exists to make, and it did not go as designed.

**What was built.** `report_tss_ist_probe()` in `kernel64/main.c`: it checks the two preconditions
it can check (the #DF gate's IST field is 1; the IST1 the *loaded TSS* names, read back through
the descriptor, is the page the pool granted), stores a sentinel 256 bytes below that page top,
repoints the IDT's #DF gate at a returning handler in `kernel64/idt64.S`, records its own RSP and
resume address, executes `int $8`, and then reads back what the handler found.  `pc5_tss64_probe_verdict()` in `kernel64/tss64.c` judges the recorded numbers by seven named
refusals.  The handler records exactly one number — the RSP it was entered with — and the frame is
dumped afterwards from memory by the C side, because which quadword holds the saved RIP depends on
whether the CPU pushed an error code and on whether it saved SS and RSP.

**The first boot refused, and the refusal was the point.** `int $8` under TianoCore raised a
not-present, supervisor, write `#PF` at the address of the `int` itself.  The sentinel store to the
same page had just succeeded, so the *page* was fine and the *field* was not: the kernel wrote IST1
at offset 32 and IST2 at 40, while a 64-bit CPU reads IST1 at **36** (SDM vol. 3A figure 7-2 — the
reserved quadword at 28 is real, because the three RSP slots start at offset 4 under a four-byte
link field).  What the CPU had been handed was therefore the high half of IST1 welded to the low
half of IST2 — for those two frames, `0x0000400000000000`, an address no page table maps.  The
I/O-map base was a related half-error: written and read as a dword at 100 rather than the word at
102, which agreed with itself and disagreed with the architecture.

**Three files, one shared mistake.** The builder wrote at 32, `pc5_tss64_check_loaded` read back
through the same offset helper, and `tests/test_tss64.py` carried `IST1_OFFSET = 32` with the
comment "the AMD64 TSS: IST1 is the first field after RSP0..2 + RSV".  Every check agreed, because
every check asked the kernel what the kernel thought.  This is the same failure mode as §30's
selector, one layer down — and the reason the file now states the layout as a field table
transcribed from the SDM, with each field's width, instead of deriving it from the header.

**The second thing that was wrong is worse.** An earlier revision of the handler copied the frame's
RIP and RFLAGS out at fixed offsets.  On the soft-interrupt path the word at +16 is not the RIP and
the word at +24 is the *saved stack pointer*, so the probe installed `0xe1f9c80` as RFLAGS and
reported `frame-rip=0x38` — a code selector, in exactly the shape that would be believed.  The
machine survived because the bits happened to be ignorable.  The handler no longer reads the frame
at all; the flags are corrected in place (`pushfq; andq $~0x400, (%rsp); popfq`) so DF is cleared
without any claim about layout, and the frame is read back from memory afterwards, where a wrong
offset cannot corrupt anything.

| Command | Result |
| --- | --- |
| cold boot of `make PC5_UEFI=64 iso` under TianoCore | `PC5-64-TSS-DF status=ok reason=ok rsp=0x2fd8 rsp-before=0xe1f9c70 ist1=0x3000 resume=0x10245c gate-ist=1 df=0 frame=0x10245c,0x38,0x2,0xe1f9c70,0x30 stack-intact=1 gate-restored=1` |
| `make stubtest64` | **`50/50 checks passed`** (was 49; the new check is the emulator-limitation tripwire below) |
| `python3 -m unittest tests.test_tss64` (venv) | **OK, 20 tests** — +6: the probe verdict battery (7 refusals, each reached by damaging one field), the frame-scan test, the "verdict writes no state" test, the SDM field-table decode, and two mutants |
| `python3 -m unittest tests.test_unicorn_soft_int` (venv) | **OK, 3 tests** |
| `python3 -m unittest tests.test_ovmf_gate_profiles` (venv) | **OK, 51 tests** — +2: the five ways the probe line can be printed and still be wrong, and the decline-vs-refusal rule |
| `make PC5_UEFI=64 ovmf-test`, `make ovmf-test` | recorded in the Milestone 8 evidence directory at the commit that lands them |

**The two refusals worth reading closely.** `probe-stack-unchanged`: if the CPU ignores the gate's
IST field, the frame lands on the stack that just overflowed and *every other field on the line
still reads `ok`* — that row is the reason the other rows are allowed to mean anything.
`probe-frame-rip-missing`: a stack pointer is a number, but a frame containing the address the trap
was taken at is a thing a CPU did.  That check is what stops this being a log line about a
measurement instead of a measurement.

**`tests/test_unicorn_soft_int.py`, and why the emulator gets its own test.** The probe cannot run
in the stub: Unicorn does not deliver a guest software interrupt to a guest IDT at all, so the run
faults at the `int`.  That is a claim about the *tool*, and §30 is the record of what an untested
claim about a tool costs, so the file tests it: it boots the shipped image with the harness's stop
markers cleared, finds the single `cd 08` in the built file (the count is itself asserted), and
requires the emulator's fault to land on that instruction — so the refusal is demonstrably in the
interrupt path and not in the TSS, the GDT, or a selector.  When Unicorn learns this, that test
fails, the stub gains a real IST measurement, and the stub check that says "unreachable here, by
measurement" has to become an assertion on the probe's fields.

**Not claimed.** An NMI has been delivered on IST2: QMP's `inject-nmi` is PR C, and nothing here
shows anything about vector 2 except that its gate names slot 2 and the TSS field behind it reads
back correctly at the architectural offset.  A `#DF` arriving spontaneously because a real fault
could not be delivered is also not claimed — the probe invited the fault; a real double fault
pushes an error code first, which is exactly the layout difference this design refuses to depend
on.  And no claim here is backed by CI: none is granted, and none is asserted.


## 32. An NMI from outside the guest, delivered on the stack the TSS names for slot 2

**Run:** 2026-11-24, workspace on `arena/5e36f38b-pc5`, QEMU 11.0.2 with the EDK II OVMF from the
same package, TCG only.  **Milestone 8, PR C.**  §31 invited a double fault; the claim still open
was the one nobody can invite on purpose — that a vector arriving from *outside* also lands on its
interrupt stack.

**What was built.** `run_qemu(..., inject_nmi=True)` starts QEMU with `-qmp
unix:<serial>.qmp,server=on,wait=off`, and once the boot has settled it completes the QMP handshake
(`qmp_capabilities` — QMP refuses everything before it), sends `inject-nmi`, and waits for the
guest to answer.  Only the 64-bit profile asks: the 32-bit kernel has no TSS, and injecting into it
would just move a halt earlier.  `idt64_report` gained two fields, `rsp=` (the stack pointer the
handler woke up on, captured by the trampoline *before* it aligns its own stack) and `ist=` (the
IST field of the live gate that delivered it), and vector 2 gained its name.

| Cold boot, after `PC5-64-DONE` | |
| --- | --- |
| `PC5-64-EXCEPTION vector=2 name=non-maskable-interrupt error=none rip=… cs=0x8 rflags=0x10046 rsp=0x3fc8 ist=2` | the NMI the hypervisor raised |
| `PC5-64-TSS … ist2=0x4000 ist-df=1 ist-nmi=2 tr=loaded str=0x20 … iomap=104` | the TSS the same boot installed |
| `rsp=0x3fc8` inside `[0x3000, 0x4000)` | `ist=2` naming the slot that page belongs to |

**The row and its refusals.** `the NMI injected from outside arrived on the IST2 stack` (asked of
both boots, so two of the report's 103 per-boot check rows; one of the 40 per-stage matrix rows,
which the gate publishes for the cold boot only — §33).  It refuses, in this order: the harness never asked, QEMU
refused the command, the guest never reported vector 2, the report arrived *before* the boot marker,
`rsp` outside the IST2 page, and the gate's `ist` not equal to 2.  The ordering is deliberate — a
kernel must not be blamed for an event that was never raised, which is why `accepted` is read out of
QEMU's own reply and stored in the report (`report.data.cold.nmi`) rather than inferred from the
presence of a log line.  `tests/test_ovmf_gate_profiles.py` drives all six.

**Two things that were wrong in the harness, found by looking at its own output.** The first run
recorded `exception_lines: 0` in a report whose serial log contained two exception lines.  The count
had been taken over "the drain buffer from the index where the injection finished", and the guest
answered *during* the QMP exchange, before that index was captured — so the window was empty and the
run also waited out its full 20 s for lines it already had.  The field is now computed from the log
the run produced, and the wait is on the reporter's last word (`PC5-64-EXCEPTION halted`) rather
than on a first-match heuristic, so a truncated report cannot be mistaken for a delivered NMI.  The
second was that `name=` read `unnamed` for the one vector this gate injects: a name table built from
the SDM's *fault* list has no entry for an *interrupt*, and "the reporter does not know what it
caught" is what a reader would take from it.

| Command | Result |
| --- | --- |
| `make PC5_UEFI=64 ovmf-test` | **exit 0, 102 PASS / 0 FAIL / 1 NOT TESTED**, matrix **56 rows** (49 before §30; +5 TSS readback rows, the `#DF` delivery row and the NMI delivery row, all published under the cold boot) — both NMI rows pass, cold and warm, in the report's per-boot list |
| `make stubtest64` | **`50/50 checks passed`** — the probe and the NMI are not reachable in the stub, and both absences are checked statements with named causes (§31) |
| `python3 -m unittest tests.test_ovmf_gate_profiles tests.test_ovmf_harness tests.test_idt64 tests.test_tss64 tests.test_kernel64 tests.test_unicorn_soft_int` | **OK, 199 tests**, no skips with the harness venv on `PATH` |

**Not claimed.** That an NMI arriving *during* the boot, or on a machine with interrupts enabled, or
while a different stack is in use, behaves the same way: the injection lands after the boot has
settled, with IF clear and one CPU, which is the environment this kernel actually runs in — and the
`rsp`/`ist` agreement is about delivery, not about nesting or about the `IRETQ` that a returning NMI
handler would need (this one halts, deliberately).  No claim is made about hardware: QEMU's TCG is
not a CPU, and `in-window` agreement between an injected NMI and an invited `#DF` on two different
emulators of the same architecture (§31 in Unicorn is *not* reachable, and is measured as such) is
evidence about the ISA, not about a PS5.

## 33. The evidence page for §30–§32, and the three numbers on it that were wrong

**Run:** 2026-11-24, same tree as §32 (`50ebe7d`) plus documentation.  **Milestone 8, PR D.**  §30,
§31 and §32 each ended with a claim about a boot; none of them could be re-checked by a reader, which
is the job of `docs/evidence/2026-11-24-tss-delivery/`.  The citation had existed since §31 — in the
sentence in `kernel64/tss64.h` that tells a reader where the delivery was measured — pointing at a
directory that then held one script and no artifacts.

**What the page holds.** The two 64-bit serial logs, both reports, both matrices, the stub gate's
report, the Unicorn refusal capture and the command record; plus `verification.sh`, which
re-derives the page's own claims from those files:

```
$ bash docs/evidence/2026-11-24-tss-delivery/verification.sh
…
OK (114 checks re-derived from 16 files in this directory)
```

The script was then pointed at a scratch copy with one field of the serial logs edited — the NMI's
`rsp=0x3fc8` rewritten to `0x2fd8`, the `#DF`'s address, which is a number from the same log and so
the least suspicious value available.  It exited 1 with seven failures, six of them the claim itself
on both boots, and one of them the gate's parse disagreeing with the text it had just been edited in.
A page that could not fail would not have been worth writing.

It is not a summary of the logs.  It recomputes the windows — `0x2fd8 ∈ [0x2000, 0x3000)` for the
invited `#DF` and `0x3fc8 ∈ [0x3000, 0x4000)` for the injected NMI, from `ist1`/`ist2` in the TSS
line rather than from the gate's parse of them — asserts the two pages are *different*, re-derives
the descriptor limit and the GDT bound from `sgdt`'s readback, and compares the gate's parsed values
(`probe_rsp`, `ist2_top`, `nmi_rsp`, `nmi_ist`, `tr_selector`, `tr_busy`) against values computed
from the raw text, so a gate that parses its own log wrongly fails the page even when it reports
`PASS`.  The absences are checked the same way: `tr=loaded` present in the stub serial, the probe
verdict and every `PC5-64-EXCEPTION` absent from it, and the single `cd 08` in the shipped image
agreeing with the address Unicorn blamed.

**Three numbers I got wrong while writing it, and what each one means.**

| Wrong | How | The actual fact |
| --- | --- | --- |
| "the serial logs of the 64-bit boot", copied from `build/ovmf-serial/` | the copy was byte-identical to the 32-bit log; `grep` for `PC5-64-TSS status` returned nothing and the two files were the same length, which is what made it look plausible rather than alarming | the gate writes its serials to `$(BUILD)/ovmf-serial` for whichever profile it is running, so the 32-bit run that followed overwrote the 64-bit evidence.  The rerun went to `BUILD=/tmp/m8iso` and the page now files both profiles, with a check that the 32-bit log contains no `PC5-64-` marker |
| "14 of the 56 matrix rows are the delivery rows, seven per boot" | counted from the report's `checks` list and then written as if it described the matrix | the matrix is keyed by *requirement*: 40 cold rows, **one** warm row repeating them, ten negative runs, five rows owned by other gates.  Seven of the 40 are the delivery rows; the warm boot's seven live in `checks`.  §32's closing table said the same thing wrongly and is corrected above |
| "`report.data.cold.nmi.exception_lines` should read 2" | asserted from the fix, not from a run | §32's boot happened *before* the drain-window fix, so that run's report legitimately says `0`.  Only the rerun behind this page has the `2` — which is why `make PC5_UEFI=64 ovmf-test` was run again here rather than the earlier log being quoted |

**One hazard found while naming the `cs=` field.** The kernel runs with a `CS` its own GDT does not
describe until the first exception delivery replaces it: the frame the `#DF` probe examined saves
`cs=0x38` (the loader's table, 48 bytes now installed, no slot 7), while the NMI's frame — taken
*after* that `iretq` — saves `cs=0x8`.  Legal in 64-bit mode, where CS base and limit are ignored,
and not what an exception handler should be relied on to fix by accident.  Recorded in
`docs/KNOWN_LIMITATIONS.md` with both halves of the measurement; the fix is a far jump through the
new table at the end of `pc5_tss64_install()`, which is a change to the handover's control flow and
is deliberately not smuggled into the TSS work.

**Gates, on the tree this page describes.**

| Command | Result |
| --- | --- |
| `make check` | **`make check: suite and all gates passed`** — 1143 unit tests in 581 s, then `psm`, docs, citations, presentation, brand |
| `make citations-check` | **`CITATIONS OK: 263 commit reference(s) resolve across 11 documents`** — this passed only once the directory §31 cited actually existed with artifacts in it |
| `python3 tools/docs_check.py` | **DOCS OK: 82 Markdown files**, 38 make targets, links, anchors and commands resolve |
| `make PC5_UEFI=64 ovmf-test` (rerun, `BUILD=/tmp/m8iso`) | exit 0 · 102 PASS / 0 FAIL / 1 NOT TESTED · both NMI rows pass with `name=non-maskable-interrupt` and `exception_lines=2` |
| `make ovmf-test` (32-bit) | exit 0 · 63 PASS / 0 FAIL / 1 NOT TESTED, 35 matrix rows — the other path is unchanged, and filed as evidence of that rather than asserted |

**Not claimed.** The page is a re-derivation from artifacts, not an independent measurement: if the
logs themselves were fabricated, `verification.sh` would happily verify the fabrication — its
cross-checks are between the gate's parse and the kernel's text, which is two sources rather than
two *independent* tools.  Nothing about hardware, Secure Boot, NMI nesting, `IRETQ`, or CI is
claimed here or anywhere in §30–§33.

## 34. The kernel re-reads its own image header, and finds a harness that had been lying about its size

**Run:** 2026-11-24, workspace on `arena/5e36f38b-pc5`, QEMU 11.0.2 with the EDK II OVMF from the
same package (TCG), Unicorn 2.1.4 for the stub.  **Milestone 8, PR E.**  §25 gave the kernel image a
header and §29 made three gates enforce it — all three of them *before* the jump.  What nobody had
was the other half: the running kernel had no idea what its own image declared, and the size it was
handed in the boot info was compared with the memory map but never with the file it came from.

**What was built.** `boot/uefi64/kimage64.c`'s rules are now two entry points:
`pc5_kimage64_validate_header()` decides everything about the *description*, and
`pc5_kimage64_validate()` is that plus the payload sum, last, over a header that has already been
bounded.  The reason strings and their order are unchanged — a mutant that corrupts field X still
has to be refused *for X*, which is what the whole corpus in `tests/test_kimage64.py` asserts.  The
kernel links the same object the loader links (`$(BUILD)/kernel64/kimage64.o` now appears in the
`pc5-64.elf` link line as well as the `BOOTX64.EFI` one, and
`test_the_kernel_and_the_loader_link_the_same_validator_object` reads both lines out of the
Makefile), because a kernel carrying its own copy of the rules would make any agreement between
them meaningless.  Then `validate_image_self()` in `kernel64/main.c`:

```
PC5-64-IMAGE base=0x100000 size=0x34000 range=ok
PC5-64-KIMAGE status=ok reason=ok image=0x34000 handover=0x34000 bss=0x2a000 entry=0x1000 crc=ok
```

Two numbers on one line, from two places: `image=` is what the header in the kernel's own memory
says and `handover=` is what the loader said.  The firmware gate compares both with the length of
the file it built (`ctx["expected_bytes"]`, the artifact, not the log), which is why the row is not
the kernel grading its own homework.

**What it costs to re-verify a running kernel, and where that stops.** `checksum` covers the whole
image, `.bss` travels inside the image, and `_start64` clears `.bss` and takes its stack out of it
before `kmain` runs — so by the time any C executes, part of the checksummed range is in use as a
stack.  A runtime payload sum would measure the ordering of the boot code, not the integrity of the
load, and it would fail.  So `validate_header` is the honest half, and the boundary is *tested*
rather than explained: `tests/test_kimage64.py` feeds a mutated body to both entry points and
requires the full validator to refuse it and the header half to accept it;
`tests/test_kernel64.py` boots a kernel with a byte of `.head`'s padding edited in loaded memory and
requires the boot to **succeed**.  Closing the gap properly is a version-2 header with a second
digest over a defined prefix — a format change, not a bug fix, and it is recorded that way in
`docs/KNOWN_LIMITATIONS.md` and in the design document.

The same reasoning bounds the *stacks* the delivery lands on: each IST window is one 4 KiB page with
its top at the page boundary, the measured layout (`tss=0x1000 ist1=0x3000 ist2=0x4000`) puts the
TSS's page immediately below IST1's, and neither has a guard page or a canary.  The rows in this
section assert **which page** the CPU arrived on, which is the claim; they do not and cannot bound
**how deep a fault that page can absorb**, and that asymmetry is recorded in `KNOWN_LIMITATIONS.md`
rather than left for the next reader to discover.

**The finding this surfaced first was in the tests.** `tests/kernel64_machine.py` had been building
every synthetic handover with `kernel_size=0xA000` — against an image of 212,992 bytes — and the
kernel accepted it, because nothing compared the two.  The first build of the self-check turned up
**42 failing tests in `tests/test_kernel64.py`**, all of them asserting `PC5-64-DONE` on a boot
info that contradicted the bytes being booted.  That is the exact number of lies, not a fluke: the
default is now the length of the image actually loaded, and the tests that mean to understate it
say so explicitly (`test_a_handover_that_understates_the_image_is_refused_by_name`, refused by name
as `kimage64-image-size` — from the *kernel*, where before it was unreachable).

**A test name that promised more than the code delivered.** `test_kernel_size_that_does_not_match_the_image_is_refused`
fed in `4 << 30` and expected `bootinfo-kernel-size` — an implausibility check, not a match with
the image, which it had never performed.  It is now
`test_kernel_size_that_is_implausible_is_refused_by_the_range_check`, and the name it used to have is
what the new case does.  A test whose title describes a comparison that does not exist is worse than
a missing test, because it stops anyone writing the real one.

**The emulator half.** The stub gate gained two checks, taking it from 50 to 52, and one of them is
the cross-line comparison the whole change is about — the loader's `kernel at=…bytes=`, the loader's
`kernel-header image=`, and the kernel's `image=`/`handover=` all reduced to one integer set
({212992}), which is the same style of row §29 established for the loader's own four lines.

| Command | Result |
| --- | --- |
| `make PC5_UEFI=64 ovmf-test` | exit 0 · **104 PASS / 0 FAIL / 1 NOT TESTED**, matrix **57 rows** (was 56: `the kernel re-read its own image header after the jump`, cold and warm both passing) |
| `make stubtest64` | **`52/52 checks passed`** (was 50; the 32-bit stub level stayed at 22/22, and both legs ran in the same gate) |
| `python3 -m unittest tests.test_kernel64` | **OK, 75 tests** (71 before; the four new ones are the two-size report, the understated handover, the in-memory CRC corruption, and the boundary case) |
| `python3 -m unittest tests.test_kimage64` | **OK, 25 tests** — the header half driven over the same mutant corpus as the full validator |
| `python3 -m unittest tests.test_kernel64 tests.test_kimage64 tests.test_ovmf_gate_profiles tests.test_ovmf_harness tests.test_apic64_kernel` | **OK, 198 tests** — the refusal corpus for both halves of the validator, six new refusals on the gate's own row, and the APIC module brought back through the honest handover |
| `make check` | **`make check: suite and all gates passed`** — 1152 tests in 630 s, then `unit` (165 C checks), `psm`, docs, citations, presentation, brand |

Every row above was measured against `build/kernel64/KERNEL64.BIN` at
`sha256 e390f7e394098401…`, which is the digest the gate report records for the boot it booted
(`data.environment.kernel_file.sha256`) — the habit §33 established, because "the tests pass" and
"the tests pass against the artifact whose behaviour you are describing" are different claims.  One
gap in that habit showed up while writing this section: the 104/57 run first measured here came
from a kernel one edit older, and only the digest comparison in the report caught it.

| `make ovmf-test` (32-bit) | exit 0 · **63 PASS / 0 FAIL / 1 NOT TESTED**, matrix **35 rows** — byte-for-byte the same verdict as before this change, so nothing the other path claims moved |

**Not claimed.** This is integrity, not authenticity: a rewrite that re-stamps both digests is
still accepted, exactly as §29 said of the loader's half, and `docs/SECURITY.md` keeps the
cryptographic boundary open.  Nothing here verifies the bytes *between* the header and the end of the
image after the jump — see the boundary above, which is the honest shape of the claim.  No hardware,
no Secure Boot, no PS5, and no CI run: the numbers above came from boots in this workspace.
## 35. Adversarial review of PR #49: two mutants refused, one survived, and a .bss declaration nobody compares

The review of #49 asked for something narrower than another milestone: take each Critical/High
claim, and try to break it.  The method is the one this repository already uses against itself —
one surgical *leniency* edit into the shipped C, run through the batteries that are supposed to
notice, then reverted.  Every probe ran in a detached worktree at `/tmp/pc5-adv`; the reviewed tree
was not edited to make a measurement pass, and `git diff --stat` was clean when the probing ended.

| Probe | The edit | Verdict |
| --- | --- | --- |
| TSS descriptor limit | `limit != PC5_TSS64_SIZE - 1` relaxed to accept any limit of half the size or more | **refused** by the activation battery |
| I/O-map base field | `tss_word(...) != PC5_TSS64_SIZE` relaxed to `>= 100` | **refused** by the activation battery |
| Descriptor type after `ltr` | the tolerated-after-load type widened from `== PC5_DESC_TYPE_TSS_AVAIL` to `<= PC5_DESC_TYPE_TSS_AVAIL` | **survived every check in `tests/test_tss64.py`** until the control below was added |

The third row is the one worth the trip.  The checker tolerates `available` after the load for a
specific, defensible reason: TCG does not mark a TSS descriptor busy, so "the host did not set the
bit" and "the host refused to load the segment" are different facts and the kernel should not
conflate them.  Writing that tolerance as `type <= AVAIL` reads like a slip of one character and
empties the check: a descriptor whose type byte had become 3 — a system segment that is not a TSS
at all — then satisfies "the TSS is loaded", the kernel prints `PC5-64-TSS status=ok`, and the only
thing standing between that and a shipped milestone was a gate that reads the printed
`busy=available-to-busy` string, which needs QEMU or the stub to exist.  Nothing pinned the set.

Closing it changed no code, only tests: `tests/test_tss64.py` gains a control that writes type 3
into the descriptor after the hand-installed load and requires the refusal
`check-descriptor-type-after`, and `Tss64ActivationMutationTests` gains `after-load-type-widened`,
which compiles the widening itself and fails if the battery passes it.  The two together are what
make the row above read "refused" rather than "untested".  Because no kernel source moved,
`build/kernel64/KERNEL64.BIN` is still `e390f7e394098401…` and 212992 bytes, so §30–§34 and the
evidence page are unaffected by construction — re-running `verification.sh` here still returns
`OK (114 checks re-derived from 16 files in this directory)`, and `tests.test_tss64` is 22 tests
where it was 20 — 21 for the control and its mutant, 22 for the sweep's counterfactual.

**A `.bss` declaration that is bounded on one side only.** The same review asked whether the header
rules could be made to certify an impossible image.  One way to try is to fabricate a header whose
declared `.bss` *is* the file — set `bss_size` to `image_size`, then re-stamp `checksum` and
`header_crc` so the image is self-consistent (nothing here is a tamper claim; a rewrite that
re-stamps both digests is accepted by design, as §29 and §34 both say).  Measured at this commit:

- `tools/kimage64.py`'s own `verify()` returns **ok** for it, and the reject table has no row for
  it — `MUTATIONS["bss-size"]` tests only the overshoot (`len + 4096`), which is refused;
- the running kernel agrees: `PC5-64-KIMAGE status=ok reason=ok image=0x34000 handover=0x34000
  bss=0x34000 entry=0x1000 crc=ok`, and the boot reaches `PC5-64-DONE`;
- `grep bss tests/ovmf_boot.py tests/boot64_stubtest.py` returns **nothing**: both gates parse the
  line and neither compares the field, so `bss=` is recorded by the kernel and read by no judge.

So the rules say `.bss` may not be *larger* than the file and say nothing about it being *smaller*
than the space the file actually has: the declaration above puts the entry point at `0x1000` inside
a region the image calls zero-fill, and three consumers — tool, kernel, gates — all decline to
notice.  It is harmless *today* for a reason that is itself the finding: `_start64` zeroes the
linker's `.bss` and adopts its stack from the link layout, not from `bss_size`, so the declared
number is currently information, and bad information that nobody acts on.  It stops being harmless
at exactly the moment v2 makes the boot read the header — which is why this belongs in
`docs/engineering/KERNEL-IMAGE64-HEADER-DESIGN-v1.md` and not in a hot fix.  The tightening is one
comparison in the shared header validator (`entry_offset < image_size - bss_size`), one row in the
reject table, and the same rule reached by both halves of the validator; it changes the kernel
image, so it owes a re-measurement of both firmware gates, which this workspace can no longer
produce (below).  Recorded, not fixed.

### Did the new control earn its place, or is one value enough?

The review asked for exactly that check: a control that rejects the *one* value a mutant happens to
permit is a control against a byte pattern, not against a set.  So the counterfactual was run rather
than argued, in a detached worktree, asking the activation battery itself (`arm_battery(build())`)
so that a mutated source could not masquerade as a detection through a missing `mutate()` anchor:

| Run | Checker | Control | Battery |
| --- | --- | --- | --- |
| A1 | tolerates `available`, `busy` **and type 4** | the old single value (type 3) | `problems=0` — **survived** |
| A2 | same mutant | the 16-type sweep | `problems=1`: `system type 0x4 after the load is refused as check-descriptor-type-after: returned 1 with 'ok'` |
| A3 | unmutated | the 16-type sweep | `problems=0` — the sweep is not merely strict |
| C | unmutated | **control deleted** | the two mutant tests fail (`FAILED (failures=2)`) |

Type 4 rather than 3 for the mutant on purpose: 3 is the value the narrow control happened to test,
and a counterfactual built around the tested value proves nothing about the sweep.  A1 is the answer
to "would one value have done?" — no.  C is the answer to "is the control load-bearing?" — remove it
and the suite's own mutants go green.

### Re-producing the `.bss` measurement

The finding above was reached by this, and it is the procedure a reviewer should run rather than a
description of one.  It needs `unicorn` for the boot half (`python3 -m venv /tmp/pc5-venv &&
/tmp/pc5-venv/bin/pip install -r requirements-harness.txt`) and a built kernel
(`make PC5_UEFI=64 kernel`).  Copy-paste, from the repository root:

```python
import sys; sys.path.insert(0, ".")
import tools.kimage64 as K
from tests.kernel64_machine import run_kernel
raw = open("build/kernel64/KERNEL64.BIN", "rb").read()
bss = K.read_fields(raw)["image_size"]            # declare the whole file .bss
d = bytearray(raw)
K.set_field(d, "bss_size", bss)
K.set_field(d, "checksum", K.payload_checksum(bytes(d)))   # self-consistent payload sum
K.set_field(d, "header_crc", K.header_crc(bytes(d)))        # self-consistent header CRC
print("loader-side:", K.verify(bytes(d), K.LINK_BASE)[0] or "ACCEPTED")
r = run_kernel(bytes(d))
print([l for l in r["serial"].splitlines() if "KIMAGE" in l or "DONE" in l])
```

Expected, and expected *wrongly*: `loader-side: ACCEPTED`, then a `status=ok` line carrying
`bss=0x34000` and a `PC5-64-DONE`.  The two re-stamps are why this is not a security claim — it is
what a self-consistent rewrite looks like — and the reason the boot survives is that
`kernel64/start.S` clears `.bss` from `__bss_start`/`__bss_end`, the linker's symbols, never from
`bss_size`.  `grep bss tests/ovmf_boot.py tests/boot64_stubtest.py` returning nothing is the other
half of the finding: the declaration is printed by the kernel and the loader and read by neither
judge.

**What the review could not re-measure, and why that is a result too.** The sandbox's `/tmp` was
wiped between sessions, which took QEMU, the OVMF pair and the harness venv with it.  Two things
followed, both of them about the checkout rather than the source:

- without `unicorn`, `python3 -m unittest tests.test_kernel64` reports `OK` while **skipping 64 of
  its own cases** — the machine tests that carry the IST-delivery and header-refusal claims.
  Re-provisioning is `python3 -m venv /tmp/pc5-venv && /tmp/pc5-venv/bin/pip install -r
  requirements-harness.txt` (pinned `unicorn==2.1.4`), after which the same modules run 106 tests
  with no skips.  This is the standing rule made concrete: read the `Ran N tests` line, not the
  `OK`.
- the clone was **shallow** (`.git/shallow`, depth 1, no PR refs), which turned `make
  citations-check` into 263 failures naming commits that exist.  `git fetch --unshallow` plus
  `make citation-refs` (49 pull-request heads) makes the same 263 resolve.  A citations failure is
  a statement about the clone before it is a statement about the history.

**The skip count, taken seriously.** The first `make check` in this workspace read `Ran 1153 tests
... OK (skipped=9)`, and that `OK` was doing work nine one-line reasons should have done.  Listing
them is the difference between "9 tests did not run" and "these 9 did not run, for this"; every
reason named a missing artifact (`no build/pc5.iso; run make iso first`, `build/efi.img is not
built`, `need both build/pc5.iso and build/efi.img`, `no build/pc5.iso and
build/efi64-release.img to compare`) or the inverted OVMF ceiling, and one of the two is a skip that
*removes* itself when the firmware evidence exists.  Not one was a statement about the source:
`build/` had lost its artifacts to the same `/tmp` wipe that took QEMU.  Re-derive any of it with
`python3 -m unittest discover -s tests -p test_\*.py -v` piped through `grep skipped`.  After `make iso`, which also produces the 64-bit release ESP image the two
comparison cases want, the identical command reads
`Ran 1154 tests in 576.701s`, exit 0, `skipped=1`, and the one skip left is
`'OVMF ran on this machine; the ceiling no longer applies'` — a test that removes itself when the
firmware evidence exists.  So the total moved 1152 → 1153 → 1154 for exactly two reasons, both of
them tests this section added (the widened-type mutant, then the sweep's counterfactual), and the skip
count moved 9 → 1 because somebody built the artifacts, not because the suite changed.

The README's `1152/1152 @ 86b862f` row is deliberately left alone: that is what the recorded
validation run measured, and re-recording it needs the QEMU this workspace no longer has.  A count in
a document belongs to the revision it was measured at; the honest move when the two drift is to say
so, which is what this paragraph is.


## 36. Naming note: "Milestone 8" in §29–§35 is a delivery ordinal, not roadmap id `M8`

The live review of #49 caught a collision the documents had created between themselves.  The PR was
titled "Milestone 8", and §29–§35 label their packages "Milestone 8, PR A" through "PR E"; but in
`docs/architecture/roadmap.json`, `M8` is *Release-quality PC5: installer, updates, rollback,
Linux-tier exit*, whose state is `not_started`.  Both statements were true and neither was about the
other thing: the sections number the **delivery packages this project has shipped**, the roadmap
numbers **milestones by workstream**, and nothing in the tree said so.  A reader who took "Milestone
8" as a roadmap id would conclude this PR shipped an installer, which it does not.

The record is corrected where it is machine-readable rather than by rewriting prose.  §29–§35 keep
their labels and their numbers untouched — they are dated measurements, and "the eighth package" is
what they said at the time — and `roadmap.json` now names where the evidence for this package
actually lives: `M1c` (UEFI boot path under real firmware) gains
`docs/evidence/2026-11-23-64bit-boot-gate/README.md`,
`docs/evidence/2026-11-23-64bit-genuine-ovmf-boot/README.md`,
`docs/evidence/2026-11-24-kernel-image-header/README.md`, the header design document and
`tests/test_kimage64.py`; `M2a` (CPU execution environment) gains
`docs/evidence/2026-11-24-tss-delivery/README.md` and `tests/test_kernel64.py`.  The TSS/IST
allocation evidence stays under `M2b`, where the 2026-11-22 page already sat.

**No state moved, and that is the part with teeth.** `M2a` and `M2b` stay `in_progress` because their
acceptance lists still hold items this package does not provide: a second CPU brought up through the
same path, a per-process address space with a test that a write to a read-only page faults, and a
DMA-visible buffer round-tripped through a driver.  `M8` stays `not_started`: there is no installer
here, no update slot, no rollback.  The PR title was changed to carry both the package ordinal and
the roadmap ids it advances, so the merge-time record matches the roadmap; the rule going forward is
that a claim which could be read as a gating statement cites the **roadmap id**, and the delivery
ordinal is treated as a filing number.


## 37. CI activation was attempted, refused, and the pipeline was fixed before it was offered

The merge gate for #49 ended with the owner choosing CI activation *before* the merge.  That choice
was executed as far as this environment is able to take it, and the boundary is a hard one: the
GitHub App whose token signs these pushes is not granted the **Workflows** permission, so GitHub
refuses any commit that creates a file under `.github/workflows/`.  Two pushes of the pure rename
were refused, twenty seconds apart, both quoting the sentence `ci/README.md` already records from
three earlier attempts.  No workflow has ever been read by GitHub for this repository —
`actions/workflows` reports `total_count: 0` — and the setting itself is not readable with this
token (`actions/permissions` → `403 Resource not accessible by integration`), so the grant cannot be
observed here, only tested by pushing.  Nothing was attempted that would side-step that check.

**What landed instead is the part that was always in reach.**  Reading `ci/build.yml` against this
milestone's own findings turned up five defects, and all five were fixed in place — `8c14d39`,
`84e6325` — because a pipeline that cannot fail is worse than no pipeline, and the first run GitHub
ever produces should not be a run of a broken one:

| # | Defect | Effect if left |
| --- | --- | --- |
| 1 | The *unspecified* default shell on GitHub's Linux runners is `bash -e {0}` — no `pipefail` — while every gate is written `make <gate> \| tee log` | **every gate in the pipeline could fail and the job would report success** |
| 2 | The harness venv was provisioned *after* the host test suite ran | `unicorn` missing ⇒ `tests/test_kernel64.py` skips 64 of its own cases under an `OK` (§35 measured exactly this) |
| 3 | four of the six gates `make check` wraps around its suite (`psm`, `docs-check`, `citations-check`, `presentation-check`, `brand-check` — of which only `unit` had a step) had none | CI strictly weaker than the local gate: the platform contract, the brand assets, the documents and a dead citation could all drift with every job green |
| 4 | The `ovmf` job teed into `build/` before `build/` existed on a fresh checkout, and left checkout credentials behind | a confusing first-step failure, and a token in `.git/config` where the other two jobs keep none |
| 5 | The firmware job reported nothing about its own verdict | a reviewer could not tell a measured PASS from an absent one without opening the artifact |

Fix 1 is `defaults.run.shell: bash`, which brings `--noprofile --norc -eo pipefail`.  Fix 5 counts
the harness's own `[PASS]`/`[FAIL]` log markers rather than re-deriving a tally from the report JSON,
because `report["checks"]` (103 entries in the recorded run) and the requirement matrix (57 rows) are
different counts — conflating them is how 104 became 105 once already, in §33.  The deliberate
non-fix is the one that matters most: `tests/ovmf_boot.py` exiting 3 (NOT TESTED) still **fails** the
`ovmf` job, so a runner without firmware is a red build rather than a green one.

**What this section therefore does not claim.**  There is no CI run to report: no run URL, no
successful build, no "required checks green".  The evidence for #49 remains the locally measured one —
`Ran 1154 tests in 564.023s`, exit 0, `skipped=1`, at head `97b7066` — and `mergeable: CLEAN` on
GitHub continues to mean only *no conflict*, with `statusCheckRollup: []` and `reviews: 0`.  The
roadmap entry for M1b stays `blocked` with its blocker intact, and the living documents still point
at `ci/build.yml`, because moving them to `.github/workflows/build.yml` would describe a location the
repository does not have; that edit is a minute of work on the other side of the permission grant.


## 38. CI was activated, ran for real, and the first red it found was in the tests, not the image

§37 ended on a refusal.  It is superseded, and superseded *as recorded*: after the owner granted the
GitHub App *Workflows: Read and write*, the same operation that had been rejected five times was
accepted — `ci/build.yml` → `.github/workflows/build.yml`, a pure rename, `R100`, blob `9359c2bf`
unchanged byte-for-byte, commit `2c3cd5c`, pushed `0aaa881..2c3cd5c`.  GitHub read the workflow within
eight seconds: `actions/workflows` → `total_count: 1`, `id=428605735`, `state=active`.  The two probes
that could not see the grant from the inside (`actions/permissions` and `actions/permissions/workflow`,
both still `403 Resource not accessible by integration`, because those need `actions: read`, which was
not granted and was not needed) are worth keeping in the record: the only way to verify a *workflows*
grant in this environment is to attempt the push.

Then five runs, and the pipeline earned its keep by failing.

| Head | Outcome | What the platform said |
|---|---|---|
| `2c3cd5c` | `cancelled` ×2 | superseded by the next push, through `concurrency.cancel-in-progress` |
| `4d0c358` | `failure` (`build`, `ovmf`), `skipped` (`release`) | failed steps named: `Host tool tests`, `Boot it under OVMF…`; no reason retrievable |
| `0be97f9` | `failure` ×2 | first annotation from the reporter: `FAIL: test_moving_head_rebuilds_the_image_that_names_it`, one line, nothing under it |
| `169fe94` | `failure` ×2 | `exit 139` in `Host tool tests`; firmware `Traceback` + `make: *** [Makefile:637: ovmf-test] Error 1` |
| `cc12956` | `failure` (`build`), then `6293d65` in flight | full failure blocks, the per-file tally (`ran 47 of 47 files in 607s; 2 did not return 0`) and the reason |

**The reason is an LFS fetch, and it is not a product fault.**  `ReleaseAttributionRebuild._scratch`
clones the working tree to build in; `git clone` smudges LFS by default; `.gitattributes` routes
`PS5UPDATE.PUP` through LFS; and the LFS store has no object for that pointer — the `404` recorded in
`docs/PS5_SYSTEM_SOFTWARE_TARGET.md` and `docs/BUILDING.md`, three times, as *expected*.  So on a
runner with a cold cache the clone dies inside the filter (`remote missing object 42ea0e5…`,
`fatal: PS5UPDATE.PUP: smudge filter lfs failed`, exit 128) and both cases fail before building
anything, while a developer machine whose cache holds the object passes and learns nothing is wrong.
This is the §35 class again — a check whose verdict depends on which machine asked — with the polarity
reversed: there a missing dependency made tests *skip* green, here a missing object made them *fail*
red, and in both cases the environment, not the code, was the variable.

Fixed by `GIT_LFS_SKIP_SMUDGE=1` on that clone (the build reads no `.pup`; no assertion in the class
concerns the pointer's contents), plus a regression test written as a source assertion rather than a
behavioural one, because on this machine both variants pass — a `file://` clone inherits the source's
`objects/` path, so the failure is not reproducible here at all and only CI can grade it.  Recorded as
CI's measurement, not a local deduction.

**What the activation did *not* do.**  No gate was loosened along the way: `defaults.run.shell: bash`
still supplies `pipefail` to all 21 `| tee` steps, the harness venv is still provisioned before the
host suite, the six `make check` gates still have their own steps, `tests/ovmf_boot.py` exit 3
(NOT TESTED) still fails the `ovmf` job, `publish release assets` still reports `skipped` on every
non-tag ref, and no `secrets.*` reference exists anywhere in the file.  Three diagnostic steps were
*added*, all guarded by `if: failure()`, all exit-0, and `tests/test_ci_report.py` +
`tests/test_ci_triage.py` (27 tests between them) pin the part that is easy to get wrong: a reporter
that can fail becomes a second failure (it did, once, via a `grep` under `pipefail`), and a green
gate's `OK (skipped=3)` must never be quoted as an error.

**Locally, at the same heads:** `Ran 1172 tests in 773.884s` → `OK (skipped=3)`, exit 0, "suite and
all gates passed" at `cc12956`; `Ran 1154 in 763.261s` → `OK (skipped=10)` at `4d0c358`, the extra
skips all of the form `no build/pc5.iso; run \`make iso\` first`, i.e. tests deferred by build order,
not by missing capability.

**M1b stays `blocked`.**  A green run would not have closed it either: its acceptance is a tag that
publishes `pc5.iso`, `pc5.iso.sha256` and `build-info.json`, and a published hash equal to a local
rebuild at the same `SOURCE_DATE_EPOCH`.  No tag has been pushed, so the `release` job has never
executed, and `blocker` now reads as an owed pair of criteria rather than a permission.  Nothing in
this section is a claim that CI passed: as of this writing it has never been green, and the first
thing it ever caught was itself.

---

## 39. Two hosted failures, both real, neither in the image — and the instruments that hid them

§38 closed with the LFS fault fixed at the same head whose run had reported it.  This section records
what came back from that run, because the return was *not* the single remaining failure the previous
correction had implied: two jobs were still red, and the one line each of them could say turned out to
be an instrument defect rather than a verdict.

**Phase 1, evidence as it is reachable.**  Head `247c5a3`, run `45338046314` (`push`) and its twin
`45338053015` (`pull_request`), identical conclusions, so the readout reproduces across two executions
of the same SHA:

| Job | check-run | conclusion | failed step |
|---|---|---|---|
| `build and verify` | `138254819433` | `failure` | `Host tool tests` (step 16 of 21) |
| `UEFI boot under genuine OVMF` | `138254819799` | `failure` | `Boot it under OVMF…` (step 5 of 7) |
| `publish release assets` | `138264732741` | `skipped` | — (non-tag ref, as designed) |

The firmware job's steps 3 (`Provision`, `./tools/ovmf_setup.sh`) and 4 (`Build`, `make iso && make uefi`)
both returned `success`, so QEMU and the OVMF images were *present*: this is not a "the runner had no
firmware" story, and it is not the exit-3 `NOT TESTED` path, which the assertions still honour.  Raw logs
and artifacts remain unreachable from here (`gh api actions/jobs/<id>/logs` redirects to the results
host, which refuses the handshake), so everything above and below comes from
`check-runs/<job>/annotations`.  Nothing in this section quotes a log line that the API did not return.

**Two instrumentation defects had to be fixed before either cause could be named.**

1. `--only ovmf` did not exist.  `.github/workflows/build.yml` has passed it to `tools/ci_triage.py`
   since `6293d65`, but `parse()` accepted only `--root/--json-out/--python/--budget`, printed
   `ci_triage: ignoring unknown argument` to *stderr*, and exited 0.  The firmware job therefore walked
   all 47 modules without a virtualenv, and its annotation read "6 did not return 0" — six unicorn-import
   failures that a reader could only take as six defects.  That number is instrumentation noise and is
   retracted here; §38's claim that the flag had landed was checked against the tree and was wrong.
   `--only` is now implemented, with `files_found` beside `files_run` in the JSON and a note that says
   `--only 'ovmf' matched 2 of 47`, so an empty selection can never read as an all-clear.
2. The reporter could not carry a traceback body.  `extract()` filters on line *prefixes*, so the
   `Traceback (most recent call last):` header survived and every frame and the exception line beneath
   it were dropped — which is precisely why the hosted annotation said `Traceback … Error 1` and
   nothing else.  `tracebacks()` now reads the block, takes the last one, and quotes
   `exception | raised at <deepest project frame> | make status`.  It will not invent: a log truncated
   before its exception line is published as `(no exception line: log ends after the traceback)` rather
   than by promoting `make: … Error 1` into an exception, a `FAIL:` block that already carries its
   exception is not quoted twice, and a chained traceback reports the last link.  Every one of those is
   a test, and the step still cannot change a verdict: always exit 0, still `if: failure()`.

   A third claim was corrected rather than implemented: `ci_report.triage_text()` says the triage "runs
   immediately before this step precisely so its verdict can be quoted here", and the workflow's own
   comment said the same.  The triage step is the one *after* the reporter, so the glob could never find
   `triage.json`.  Reordering CI to match the prose would have meant rewriting four pinned wiring tests
   for no gain — the triage's `::notice` lines were already the annotations that carried it — so the
   prose now says which order is real and what would make the other path live.

**Cause one, the firmware job.**  With the exception line capturable, the crash reproduces locally on
demand by substituting firmware *discovery* only (`PC5_QEMU`, `PC5_OVMF_CODE`, `PC5_OVMF_VARS`,
`PC5_OVMF_SECBOOT_CODE`, `PC5_OVMF_SECBOOT_VARS` pointed at files shaped like the real thing — the
sandbox has no QEMU, so no boot is simulated and none is claimed):

```
TypeError: stat: path should be string, bytes, os.PathLike or integer, not Profile
  raised at tests/ovmf_boot.py, line 2146, in secure_boot_run:
            shutil.copyfile(vars_template, secure_vars)   [+3 frame(s) below]
make: *** [Makefile:637: ovmf-test] Error 1
```

`secure_boot_run(report, args, qemu, data_dir, work, iso_path, code_path, profile, vars_template)` was
called with its last two arguments the other way round, so the *vars template* slot held the boot
`Profile` and the first statement of the function died on a `shutil` type check — before QEMU was
launched, and so before the Secure Boot assertion anyone thought was being graded had ever run.  It is
CI-only because the branch requires *both* Secure Boot firmware files: `tools/ovmf_setup.sh` installs
the `ovmf` package, which ships `OVMF_CODE*.secboot.fd` and `OVMF_VARS*.ms.fd`, while a developer
machine typically has neither and takes the `applicable=False` path instead.  Nothing in Python can see
a swap between two same-shaped positional arguments; `pyflakes` cannot see it either.

The call site now names `code_path`, `profile` and `vars_template`.  Three tests cover it: the function
driven with the types its signature asks for (it fails on the old order because `profile.stop` does not
exist on a path), a static check that binds the call site's arguments against the definition, and a
sweep over every internal call in the harness that reports a role-shaped name bound to a parameter
sharing no role token.  The sweep finds exactly one offender in 162 calls — the one above — and was
run against the pre-fix file to confirm it names line 1970 and nothing else.  Both the behavioural and
the wiring test were then run against a copy of the tree with the bug re-introduced, and both fail
there; a guard that cannot fail is not a guard.

**Cause two, the build job.**  `test_the_dirty_label_and_the_loader_digest_stay_honest` failed with

```
AssertionError: False is not true : the label hides that no commit produced this image:
'247c5a375f6c9f36b30aa4e9d7f85eb1051ca3c5'
```

Forty characters.  `BUILD_ID` is built from `git rev-parse --short HEAD`, which prints seven, so the
value in the record did not come from the repository at all — it came from the workflow's own
`env: BUILD_ID: ${{ github.sha }}`, and `BUILD_ID ?=` *yields* to an environment variable.  The
`$(if $(PC5_DIRTY),-dirty)` marker lived inside that default, so on every runner the marker was
discarded and no local run could see it: export the variable here and the hosted failure reproduces
byte for byte, message and all.

The Makefile keeps the override (a release *should* stamp the sha it was handed, and
`docs/BUILDING.md` documents `make BUILD_ID=…`) and moves the marker outside it —
`override BUILD_ID := $(BUILD_ID)$(if $(PC5_DIRTY),-dirty)` — so the dirty state is appended to
whichever base won, from the environment or the command line.  A new case pins both halves of that:
a handed-in id passes through *unmarked* on a clean tree (which is what keeps a published
`build-info.json` byte-identical to a local rebuild) and gains `-dirty` the moment the tree moves under
it.  `tests/test_release_esp.py` was then run with `BUILD_ID` exported — the runner's shape, which the
suite had never been run in — and all 21 cases pass.

**Why no local run had ever seen cause one, even the one that passed.**  The only `make ovmf-test`
success on record is `docs/evidence/validation/ovmf-test.json` — `result: passed`, `exit_code: 0`,
`level: real firmware execution`, at `86b862f` — and its `command` field reads `make ovmf-test`, with
no `--secure-boot`.  That flag is what routes the harness into `secure_boot_run`, and only the workflow
passes it (`OVMF_ARGS="--secure-boot"`, step 5).  So the branch that died has been executed exactly
twice in this repository's history: on a runner that installs the Secure Boot firmware pair, and here,
with discovery substituted.  Both produce the same `TypeError`.  This is also the answer to "why is
there no failing test for it": there was no test that called `secure_boot_run` with a real-looking
argument list either — `SecureBootTests` graded `secure_boot_verdict`, a pure function two calls past
the crash, so the unit test and the integration gate were both true and neither implied the other.

**Locally, at this head:** `make check` → exit 0, `Ran 1207 tests in 526.855s`, `OK (skipped=3)`; the
suite grew by 35 cases over the `1172` §38 recorded at `247c5a3`.  The three skips, each an environment
statement and none of them promoted to a pass:

- `test_release_esp`: `no build/pc5.iso and build/efi64-release.img to compare` — deferred by build
  order, since the gate is `make iso` and the release ESP is built by another target;
- `test_release.CiLayoutTests.test_readme_explains_activation_when_not_installed`: `pipeline is active`
  — the README's dormancy note is only owed while the pipeline is dormant, which §36 ended;
- `test_roadmap_dependencies.FoundationTests.test_no_64bit_milestone_is_complete_while_ovmf_is_untested`:
  `OVMF ran on this machine; the ceiling no longer applies`, because the evidence record quoted above
  says `result: passed`.  It says so about a *local* machine and about a run without `--secure-boot`;
  it is not, and has never been claimed as, evidence that the hosted firmware job passes.

**What is *not* claimed.**  The hosted firmware log has still not been read: its exception line is
inferred from a locally reproduced crash at the same `Makefile:637`, in the same function, reached only
when the Secure Boot pair is installed — which the provisioning step does install, and which the
sandbox here does not have (no `qemu` binary, no `/usr/share/OVMF`), so discovery is what the local
reproduction substitutes and nothing about a boot is.  The next run's annotation is the confirmation — run 7, below,
carried no exception at all, because there was no longer a crash to report; the prediction is
recorded as tested, not as assumed.  A stub QEMU producing 45 failures is the
*correct* answer for a stub and is not a boot; the firmware gate passed nothing here and nothing in this
diff makes it able to.  `make ovmf-test` still exits 3 when firmware is absent and that still fails the
job.

**Run 7, and the prediction it settled.**  Both runs at `d5e4f01` finished, identically — the
`push` twin `45463229126` and the `pull_request` twin `45463245850`:

| Job | push run | PR run | What changed |
|---|---|---|---|
| `build and verify` | **success** | **success** | first green build job on this branch; the `BUILD_ID` fix is why |
| `UEFI boot under genuine OVMF` | `failure` | `failure` | no longer a crash: the step now returns a verdict |
| `publish release assets` | `skipped` | `skipped` | non-tag ref, as designed |

The prediction below was that the next annotation would carry the exception line.  It carried no
traceback at all, because there is no longer one to carry: `make ovmf-test` reached its
`[FAIL]`/`[PASS]` output and exited with a graded verdict at `Makefile:643` — the target sits six
lines lower than the `637` every earlier run quoted, because the fix added comment lines above it, and
the step's name is unchanged.  Both the `TypeError` and the `Error 1`-after-a-traceback shape are gone
from both runs, which is the confirmation this section asked for.  The `--only` fix reads back the
same way: `ran 2 of 2 files in 336s … --only 'ovmf' matched 2 of 47`, where run 6 said 47 of 47.

**What the firmware job reports now, and what is deliberately not touched.**  The gate's failure
annotations, identical across the twins (`300.169s`/`300.211s`, `34.332s`/`35.336s`):

    [FAIL] [cold] the loader identified EDK II as its firmware  (loader_firmware_edk2=False)
    [FAIL] [warm] the loader identified EDK II as its firmware  (loader_firmware_edk2=False)
    [FAIL] every required matrix row this run claims to verify is verified
           (matrix rows failed: the loader names the firmware it is running on)

One required row, in both the cold and the warm run.  Every other required row of the matrix is
reported as holding on the runner — including the ones no hosted run had ever reached: the firmware
recognising the boot medium, the loader executing, the kernel image read through the file protocol,
the handoff, the kernel reaching its initialisation point.  That is a statement about PC5's own UEFI
boot path on real EDK II firmware and nothing more: it is not evidence about Sony system software, and
no such claim is made here or anywhere else in this repository.

The failing row compares a *string the firmware chooses to say about itself*.  The 64-bit loader prints
`PC5-64-LOADER firmware=` followed by `g_st->firmware_vendor` verbatim (`boot/uefi64/uefi64_main.c`,
the `serial_write_ascii16` call in `efi_main64`), and the harness grades the run by searching the
serial text for the literal `PC5-64-LOADER firmware=EDK II` (`tests/ovmf_boot.py`, in the 64-bit
profile's marker table; the 32-bit profile looks for `PC5-UEFI: firmware=EDK II` in the same way).  So
the row asserts one particular vendor string from one particular build of the firmware, and the
runner's `ovmf` package evidently reports something else; the local `result: passed` record at
`86b862f` was produced by a different OVMF build, on a machine where the string happens to match.  What
the runner printed is in `build/ovmf-serial/` inside the uploaded artifact, and artifacts are
unreachable from here — so the observed value is not known, and this section does not guess it.

That is the point the brief said to stop at: a genuine firmware-path finding, reported rather than
patched.  No kernel or loader source was modified.  Two candidate next steps, each needing an owner's
decision because both change what the gate *means* rather than what it does:

1. make the evidence reachable — quote the observed `FirmwareVendor` bytes in that check's detail, so
   the annotation names what the runner's firmware actually said instead of `=False`; the assertion
   and its exit semantics stay exactly as they are; or
2. decide the row's contract — whether the row should require a specific vendor *string*, or require
   that the loader printed *the string it was given*.  Quietly narrowing it to the latter would be
   weakening a test, which is why it is not done in this commit.

The two host modules the triage names in that job (`Ran 43 … errors=1`, `Ran 53 … failures=1,
skipped=1`) are the same coupling one level down: those modules invoke the harness for real, so on a
machine that owns QEMU they spend 300s and 35s inside a boot, where here 96 of their tests finish in
0.336s with 6 skipped.  A test whose duration and outcome depend on whether the machine has firmware is
a test that should pin its own environment; that is named as the next item and not slipped into this
commit, because the failure it would remove is not the one this brief was about.

**M1b stays `blocked`.**  Both acceptance criteria are still unpaid — no tag has been pushed, so
`publish release assets` has still never executed.  What changed is that the pipeline can now describe
its own failures: after this, a red firmware job will say which exception it raised, and a red build job
will not be lying about the tree it came from.
## 40. The firmware gate grades instead of crashing, and the two places a cause could still hide

**Live state, re-read rather than copied.** §39 recorded a fix in flight, so the pipeline was
queried again at `e1aa9ec` instead of trusting the note: runs `45468263882` (push) and
`45468276720` (`pull_request`), both `completed/failure`. The *shape* of that failure is what
changed:

| job | conclusion | failed step |
| --- | --- | --- |
| `build and verify` | **success** | — |
| `UEFI boot under genuine OVMF` | failure | `Boot it under OVMF, including the Secure Boot policy check` |
| `publish release assets` | `skipped` | never executes off a tag |

The firmware job's annotations, which are the whole of what is reachable (`ci/README.md`,
"Reading a failed run"):

```
notice   ran 2 of 2 files in 336s; 2 did not return 0; interpreter python3; --only 'ovmf' matched 2 of 47
failure  exit 1 | ------ / Ran 43 tests in 300.136s / FAILED (errors=1)
failure  exit 1 | ------ / Ran 53 tests in 35.332s / FAILED (failures=1, skipped=1)
failure  [FAIL] [cold] the loader identified EDK II as its firmware  (loader_firmware_edk2=False)
         [FAIL] [warm] the loader identified EDK II as its firmware  (loader_firmware_edk2=False)
         [FAIL] every required matrix row this run claims to verify is verified  (matrix rows failed:
         the loader names the firmware it is running on)  make: *** [Makefile:643: ovmf-test] Error 1
```

Four things that were broken are now demonstrably not, each by the run rather than by the diff:
the scratch clone fetches no LFS object, so `smudge filter lfs failed` and exit `128` are gone;
`--only` narrows the walk (`matched 2 of 47`, where the same job once counted six broken files it
had no business examining); there is no traceback, because the gate reaches QEMU, boots, and
reports named assertions (`1e5e41d`); and `build and verify` is green for the first time in the
branch's history (`23dfa04`).

**A note on process, because it cost a turn.** The sandbox rolled back to the shallow baseline
`dbd7500` again — HEAD and index at the merge of #48, working tree at `247c5a3` plus new edits, so
`git status` reported all 27 branch commits as uncommitted changes and `citations-check` could not
resolve any commit reference. The recorded recovery (read the ref from the API,
`GIT_LFS_SKIP_SMUDGE=1 git fetch --unshallow --no-tags origin`, `git reset --mixed`) surfaced a
second fact: the ref had moved five commits past the recorded head, and the diagnosis being
re-derived in parallel was already committed — and committed better. A `tracebacks()` that quotes
the deepest *project* frame beat one that quoted the last indented line (which is CPython's `^^^^`
ruler); an `--only` taking a comma-separated list, reporting "`--only 'ovmf'` matched 2 of 47, so
nothing ran", and cross-checked against the flags the workflow passes beat a substring filter with
a warning; and the dirty marker belongs in the Makefile as `override BUILD_ID := $(BUILD_ID)$(if
$(PC5_DIRTY),-dirty)`, not in one test's environment, because the mislabelling was real for every
build the runner ever made. All three replacements were dropped rather than committed over the top
of them. What follows is only what the tree did not have.

**A cause could still hide in a failed mutant build.** `build_mutant_iso` ran the repository's own
ISO tool with `subprocess.run(command, check=True, capture_output=True)`. The child dies, writes
its complaint into `CalledProcessError.stdout`/`.stderr`, and nothing on the way out prints those:
the surviving message is `Command '[…]' returned non-zero exit status 1`, which names no cause —
the same hole `a6da2bd` widened from the reporter's end, one level down, where there was no
exception *line* worth carrying. It now writes what the child said and raises the same exception
via `check_returncode()`. Unlike the `secure_boot_run` transposition this reproduces on a machine
with no QEMU and no `/usr/share/OVMF`, because the child never reaches a boot:

```
mkiso.py failed (exit 1); its own output:
  File "…/tools/mkiso.py", line 559, in main
    with open(args.boot_image, "rb") as fh:
FileNotFoundError: [Errno 2] No such file or directory: '…/loader.bin'
```

Three cases in `tests/test_ovmf_harness.py` (`MutantBuildFailureTests`) hold it: the reason reaches
the log, the exception's type/status/command are unchanged, and the print is conditional on the
child having failed — six gate-profile rows call this function per run, and a green build must not
accompany six error lines.

**The remaining red quotes a verdict, not an observation.** `(loader_firmware_edk2=False)` is true
and useless: the row is a substring test for `firmware=EDK II`, and three different defects produce
that annotation — the firmware told the loader a different name, the loader printed the name with
different text around it, or the line never appeared. The serial log already distinguishes them, so
`classify` now also carries what it saw (`_said`, `loader_firmware_said`) and the row's proof quotes
it (`_firmware_named`) while its predicate stays the substring test:

| the loader printed | verdict | detail |
| --- | --- | --- |
| `firmware=EDK II` | pass | — |
| `firmware=EDK II (ACCID=0x6564)` | pass | — |

*The table above records what §40's commit did. ADR-026 (see §41) anchored the grammar
afterwards, so `EDK II (ACCID=0x6564)` is now refused as an ambiguous tail and the detail also
carries the classification.*
| `firmware=EFI Development Kit II` | fail | `loader_firmware_edk2=False; the loader printed firmware='EFI Development Kit II'` |
| no `firmware=` line | fail | `loader_firmware_edk2=False; the loader printed no \`firmware=\` line at all` |

`test_quoting_can_never_move_the_verdict` asserts the load-bearing half directly: for every shape and
on *both* profiles — `classify` asks the profile which words to look for, and a 64-bit line read as
IA32 proves nothing either way — the boolean equals `_seen`'s. What the runner's firmware actually
said is still not known; that is what the next run is for. §39's other option — whether the row
should require the literal `EDK II` at all — is a decision about what the gate *means*, and it stays
open, unweakened and unmade.

**Locally, at `e1aa9ec` plus this change.** `make check` with the harness venv on `PATH`, as CI
provisions it: `Ran 1215 tests in 514.048s` → `OK (skipped=10)`, exit 0, "make check: suite and all
gates passed". The ten skips are named, not counted as passes: four `no build/pc5.iso; run
\`make iso\` first`, and one each of `build/pc5.iso not built`, `no build/pc5.iso and
build/efi64-release.img to compare`, `need both build/pc5.iso and build/efi.img`,
`build/efi.img is not built` (all deferred by build order, not by capability), plus
`pipeline is active` and `OVMF ran on this machine; the ceiling no longer applies`, both of which
skip because the condition they guard against is absent here. `tests.test_ovmf_harness` run alone:
`Ran 51 tests in 0.349s` → `OK (skipped=1)` (`test_the_real_esp_is_not_firmware`,
`build/efi.img is not built`).

**M1b stays `blocked`.** `publish release assets` reported `skipped` on every run of this branch and
has still never executed, so neither acceptance criterion is met: no tag has published
`pc5.iso`, `pc5.iso.sha256` and `build-info.json`, and no published hash has been compared with a
local rebuild at the same `SOURCE_DATE_EPOCH`. And nothing here is a claim that CI is healthy: the
firmware job is red at the head of this section, for a reason the pipeline can now describe in words.
**The run after that commit answered the open question, in the annotation itself.** Run
`45528590239`/`45528583614` at `d86054a`: `build and verify` **success**, `UEFI boot under genuine
OVMF` failure at `Boot it under OVMF, including the Secure Boot policy check`,
`publish release assets` `skipped` — and the firmware row now reads

```
[FAIL] [cold] the loader identified EDK II as its firmware
       (loader_firmware_edk2=False; the loader printed firmware='Ubuntu distribution of EDK II')
```

So the remaining red is not a boot failure, not the loader, and not the firmware: the loader read
the vendor string correctly and echoed it, the runner's EDK II build reports
`Ubuntu distribution of EDK II`, and the row asks for the literal `firmware=EDK II`. It is §39's
option 2, stated as a fact rather than a guess — a contract question about what that row requires,
which belongs to the owner and is not answered by widening a substring test in a commit that wants
a green badge. What the two harness modules still report alongside it (`Ran 51 … errors=1`,
`Ran 53 … failures=1, skipped=1`) is unchanged in kind from the run before, and is named here as
the next item rather than folded into this one.

## 41. The firmware identity contract, ruled and implemented: implementation asserted, distribution recorded

§40 left the row quoting a verdict (`loader_firmware_edk2=False`) where it could have quoted an
observation, and named the two things it refused to guess about. Both are now settled — the
observation is recorded, and the contract that reads it is ADR-026.

**The ruling.** Accepted: **D1** the distribution is recorded, never gated; **D2** an
implausible-but-real-sounding identity (`TianoCore`, `EFI Development Kit II`, `OVMF`) is refused
as `UNKNOWN` until a captured boot supports it; **D3** the grammar is anchored at both ends, so an
ambiguous tail is refused; **D4** the three kinds of absence keep three distinct reasons.

**What the gate asserts now.** `classify_firmware_identity` in `tests/ovmf_boot.py` is a pure
function of the string the loader printed, and it is the only predicate behind
`loader_firmware_edk2` on both profiles. It accepts `EDK II` — edk2's own upstream
`PcdFirmwareVendor` value, and what all sixteen committed genuine serial logs contain — and
`<clause> of EDK II`, capturing the clause verbatim as `loader_firmware_distribution`. It refuses,
with the raw string quoted in the refusal: `PC5`-containing labels (the stub sets that field
itself, so a stub cannot certify a genuine boot, whatever it renames itself to), `absent`/
`empty`/no-line as three separate facts, a string at the profile's own print limit as *ambiguous*
rather than wrong, and everything else as `UNKNOWN`. `report.data[<stage>]["firmware_identity"]`
carries `raw`/`implementation`/`distribution`/`accepted`/`refusal`, so the artifact holds the
claim and its evidence even when the verdict is a refusal.

**The four strings that used to pass.** A repair that only widened the head would be a
relaxation, so the tail was closed too, and it is recorded here rather than left in the diff:

| firmware printed | before | now |
| --- | --- | --- |
| `EDK II (ACCID=0x6564)` | pass | refuse (D3: ambiguous tail) |
| `EDK IIx`, `EDK II-like` | pass | refuse (`UNKNOWN`) |
| `edk ii` | pass | refuse (case is part of the firmware's own bytes) |
| `EDK II revision=0x2 table-rev=0x10000` read as one identity | — | refuse; the 64-bit pattern stops at ` revision=` |

That last row is the trap in this change rather than a hypothesis: the 64-bit loader continues the
same line with ` revision=` and ` table-rev=`, so a pattern that captured to end-of-line would
read a perfectly good EDK II as an unknown string and turn **sixteen committed genuine boots** red
while the new acceptance cases still passed in isolation. It is measured, not assumed: the matrix
cases were replayed over `docs/evidence/*/ovmf*serial*.log` (16 genuine boots, 0 regressions), and
`test_the_64bit_marker_does_not_swallow_the_revision_fields` pins the boundary.

**The suite can fail, which is the part worth checking.** Four mutations of the new contract were
applied one at a time and each was caught, then reverted:

| mutation | result |
| --- | --- |
| restore the old `firmware=EDK II in text` predicate | `FAILED (failures=33)` |
| drop the ` revision=` lookahead | `FAILED (failures=15)` |
| remove the `PC5` self-issued refusal | `FAILED (failures=10)` |
| accept any tail (`EDK2_DISTRIBUTION_RE` neutered) | `FAILED (errors=22)` |
| all reverted | `OK` |

**Deliberately unchanged.** `boot/**` and `kernel64/**` are untouched: the loader prints
`SystemTable->FirmwareVendor` verbatim and does not normalise, pad, case-fold or truncate it for
the gate's convenience — a product that formatted its own evidence would leave the row measuring
ours. The Secure Boot rows, the refusal-based policy verdict (ADR-023 §4), the matrix's
no-inheritance rule, `make ovmf-test`'s exit codes (0 pass, 2 fail, 3 NOT TESTED failing the job)
and the substring semantics of every *other* marker are as they were. Nothing in this section
claims a host booted anything: the classifier has never been run against QEMU here, because this
sandbox has no `qemu-system-x86_64` and no `/usr/share/OVMF`.

**Locally.** `make check` with the harness venv on `PATH`, as CI provisions it: `Ran 1225
tests in 783.513s` → `OK (skipped=10)`, exit 0, `make check: suite and all gates passed`. Skips, named rather than
counted as passes: `OVMF ran on this machine; the ceiling no longer applies` (1); `build/efi.img is not built` (1); `build/pc5.iso not built` (1); `need both build/pc5.iso and build/efi.img` (1); `no build/pc5.iso and build/efi64-release.img to compare` (1); `no build/pc5.iso; run `make iso` first` (4); `pipeline is active` (1). Targeted, the modules that read these markers: `Ran 167 tests in 0.616s -> OK (skipped=7)`.

**Hosted, predicted before measured.** What follows was written from the local run alone, before
any hosted result existed for `330d471`, so that the run could be checked against a prediction
rather than admired afterwards; it is kept as written, and the measurement is the addendum below: the `[cold]`/`[warm]` identity rows pass on the runner's own
string, the `every required matrix row…verified` row clears with them, and `build and verify` stays
green — while `test_ovmf_harness`'s single error in that job, the gate-profile module's single
failure, and `publish release assets`'s `skipped` are untouched by this change and remain. M1b
stays `blocked` until a tag publishes the three artifacts and a published hash matches a local
rebuild at the same `SOURCE_DATE_EPOCH`; PR #49 stays open and unmerged.

**Hosted, measured.** Run `45543361657` (`pull_request`, `330d471`) → **`completed/success`**:

| job | conclusion | steps |
| --- | --- | --- |
| `build and verify` | **success** | 22 of 24 (the two `if: failure()` diagnostic steps are `skipped`, as designed) |
| `UEFI boot under genuine OVMF` | **success** | 8 of 10 (same two) |
| `publish release assets` | `skipped` | 0 of 0 — non-tag ref |

The firmware job's annotation list is now a single warning about Node 20 deprecation in
`actions/checkout`/`upload-artifact` — no `::error::`, because the step that writes them runs only
under `if: failure()`. **This is the first green `UEFI boot under genuine OVMF` in the branch's
history**, and it is green with `--secure-boot`, under the runner's own Ubuntu OVMF, on the strength
of a contract that still refuses four strings a substring test would have forgiven.

**What the push twin shows, and why it is not a verdict.** The matching push run `45543356695`
reports `completed/cancelled`: its `build and verify` died mid-step at
`Verify, test and stage the release assets`, because `concurrency.group: build-${{ github.ref }}`
with `cancel-in-progress: true` puts both runs of a single push in one group and the PR run —
created three seconds later on the same ref — superseded it. Its firmware job did complete
`success`, so both runs agree on the one question this section is about, but the green
`build and verify` rests on `45543361657` alone. **A cancelled job is not a passed job**, and
nothing here reads it as one. The grouping is a pre-existing property of the workflow, not a
regression from this change; narrowing it (`github.ref` plus `event`) is an owner decision about CI
behaviour and is deliberately not bundled into a firmware-contract commit.

**Prediction against outcome, kept because it is the honest part.** §41 predicted: identity rows
pass, the `every required matrix row…verified` row clears with them, `build and verify` stays
green, `publish release assets` stays `skipped`. All four held. It also predicted the two
host-module items would *remain* — that was wrong in a way worth writing down: those modules are
executed in that job only by the triage step, which did not run because nothing failed, so at
`330d471` their hosted status is **unmeasured**, not green. Their earlier hosted failures were this
same row asserted through the harness, and the row now passes on the runner's evidence; but the
next red in that job will name them again through triage, and until then this section claims no
pass for them. Same discipline for the artifact: `pc5-ovmf-<sha>` holds `ovmf-report.json` (which
would show `firmware_identity.distribution: Ubuntu distribution`), and uploaded artifacts live on
the blob host this sandbox cannot reach, so the report is *not* quoted as evidence here — the
job's own conclusion is.

**M1b stays `blocked`.** `publish release assets` has still never executed on this branch: no tag
has published `pc5.iso`, `pc5.iso.sha256` and `build-info.json`, and no published hash has been
compared with a local rebuild at the same `SOURCE_DATE_EPOCH`. A green firmware job is not a
released artifact. PR #49 remains open and unmerged, and this section is recorded before any
further authorization.
