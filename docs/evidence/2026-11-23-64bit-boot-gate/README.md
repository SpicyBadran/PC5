# The 64-bit release ISO, booted by a gate that knows its vocabulary

**Date:** 2026-11-23 · **Commit built and booted:** `d014a5b` (clean tree, so the image's
own `build:` label is `d014a5b` and not `d014a5b-dirty`) · **Result:** both modes green —
`make PC5_UEFI=64 ovmf-test` **exit 0** (78 checks PASS, 2 NOT TESTED, matrix 43 rows =
37 verified + 5 external + 1 not-tested) and `make ovmf-test` **exit 0** (63 PASS, 1 NOT
TESTED, matrix 35 rows = 30 verified + 5 external).

This is the follow-up to
[`2026-11-23-64bit-genuine-ovmf-boot`](../2026-11-23-64bit-genuine-ovmf-boot/README.md),
which proved the 64-bit path boots under TianoCore with a hand-written QEMU command line.
That proof was real and unenforced: `tests/ovmf_boot.py` graded every run with the 32-bit
marker set, so the same image run through the gate could only "pass" by *not* finding
`PC5-BOOT-OK` — the failure condition, reported as success. Here the gate itself is the
artifact, and it is what found the three defects below.

## Provenance note, added on re-landing this branch

The files below are the bytes a real run produced, and `verification.sh` re-derives every
claim in this page from them (it passes: `OK (43 checks)`). One thing they cannot do in
this repository is point at a commit that still exists: they were captured in a workspace
that was re-provisioned afterwards, which kept its files and lost its objects, so
`d014a5b` named in the build record of the recorded artifacts is not reachable from this
history. Treat the digests as identifying *the artifacts on file here* — which they do,
byte for byte — and read the corresponding run at the branch's current HEAD as the
authoritative one, in the section below this note.

## What was run

Nothing was substituted, stubbed, or skipped: the release ISO produced by `make iso` (via
`tools/mkiso.py --uefi-image build/efi64-release.img`), attached to QEMU's q35 machine as
an El Torito medium, started by the firmware's own boot manager, cold and warm, plus every
negative control (`--skip-negative` was *not* passed, so 8 mutants were built and booted).

| | 64-bit leg | 32-bit leg |
| --- | --- | --- |
| build | `SOURCE_DATE_EPOCH=1700000000 make PC5_UEFI=64 iso verify` → `VALID`, `MANIFEST VALID`, exit 0 | same without `PC5_UEFI=64`, exit 0 |
| gate | `make PC5_UEFI=64 ovmf-test` → **exit 0** | `make ovmf-test` → **exit 0** |
| ISO | `build/pc5.iso` 1806336 B `sha256=1bae49a3284e92a82669…` | `build/pc5.iso` 1601536 B `sha256=ed98325d72fdb867d9e8…` |
| release ESP in the ISO | `build/efi64-release.img` 1474560 B, byte-identical (`release_esp_identical: true`) | `build/efi.img`, byte-identical |
| boot report's `uefi-path` | 64, read from the medium, not from a flag | 32 |
| cold / warm stop markers | `evidence` / `evidence` | `evidence` / `evidence` |
| release metadata | `make PC5_UEFI=64 metadata` → `checks=2 verdict=PASS`, exit 0 | `make metadata` → `checks=2 verdict=PASS`, exit 0 |

`checks=2` is the point of the last row: `tools/buildmeta.py` embeds `iso_structure` *and*
`firmware_boot` — the firmware verdict is now part of the release record, and it is
refused (exit 3, both digests quoted) if the report on disk describes a different image
than the one being described. That was observed for real while writing this: after a
rebuild, `make metadata` printed

```
buildmeta: build/ovmf-report.json is a boot record for iso sha256 8769262d…, but
build/pc5.iso is bf13dac2…: the report describes a different artifact.
```

— which is the stale-evidence case the milestone asks to be detected, caught by the tool
rather than noticed by a reader.

QEMU and firmware are the same packages as the earlier boot evidence
([`x64-env.txt`](x64-env.txt), [`ia32-env.txt`](ia32-env.txt)): QEMU 11.0.2 (musl build
from npm `qemu-portable-linux-x86_64-musl@0.2.1`, `qemu-system-x86_64` sha256
`0539587908d9a9b83ccdce7a48d968b50f827476b9852814e07445e9402df562`), with the EDK II
OVMF shipped inside that package — CODE `edk2-x86_64-code.fd` sha256
`33090cc07675baa5190d9f1e84bf5176b33bcbfa9bacac522961150cdb6dbb2a`, VARS template
`edk2-i386-vars.fd` sha256 `5d2ac383371b408398accee7ec27c8c09ea5b74a0de0ceea6513388b15be5d1e`,
a fresh copy of the variable store per boot, TCG-only, no network, `-no-reboot`.
**The same CODE image is used for both modes**: `edk2-i386-code.fd` on this build starves
the boot before our application starts, and a gate cannot grade a boot it never reached.
Nothing was downloaded for this run; the provisioning recipe is in
[`docs/BUILDING.md`](../../BUILDING.md) and was re-verified here by `sha256sum`.

## The evidence files, and what each one is for

[`summary.json`](summary.json) is the machine-readable record: exit codes, check and
matrix counts, both `not_tested_names`, the ISO and ESP digests, the four serial-log
digests, the QEMU version, and **two copies of the image's build record** —
`iso_buildinfo_quoted_by_report` (what the boot report says the medium carries) and
`iso_buildinfo_measured_from_file` (the same fields read off `build/iso/BUILDINFO.TXT`
afterwards). They agree, and both name `commit=d014a5b8…`. That agreement is the whole
attribution chain in one line: the boot verdict, the ISO, and the commit all name each
other, and no step trusts a file name to say which artifact it is.

The four logs are the guests' own console output, unmodified except for stripping carriage
returns and ANSI escapes: [`ovmf64-serial-cold.log`](ovmf64-serial-cold.log) (70 lines),
[`ovmf64-serial-warm.log`](ovmf64-serial-warm.log), [`ovmf32-serial-cold.log`](ovmf32-serial-cold.log)
(78 lines), [`ovmf32-serial-warm.log`](ovmf32-serial-warm.log). Cold and warm differ only in
the lines that measure time (`spins=`), which is what a warm boot is *supposed* to show: the
second run reused the variable store the first one wrote, asserted by its own row.

Read the 64-bit log in order and you have the stage list the gate asserts on:

| Serial line | What it establishes |
| --- | --- |
| `BdsDxe: loading Boot0001 "UEFI QEMU DVD-ROM …"` / `starting Boot0001 …` | firmware found the El Torito entry on our medium and chose it |
| `PC5-64-LOADER firmware=EDK II revision=0x2 table-rev=0x10000` | the loader identified the firmware that started it — this line is *why* "firmware initialised" and "PC5 ran" are distinguishable, and it was added for this gate |
| `PC5-64-LOADER-BANNER 64-bit UEFI path (experimental)` | our `BOOTX64.EFI` was entered, in long mode |
| `PC5-64-LOADER kernel-volume=image` | the kernel was opened on the loaded image's own device handle |
| `PC5-64-LOADER kernel at=0x100000 bytes=204800` | the release `KERNEL64.BIN`, read off the real medium, at the size the release recorded |
| `PC5-64-LOADER watchdog=disabled` | firmware was asked to stop its watchdog and said yes (`disable-refused=<hex>` is the other branch, also accepted, with the status quoted) |
| `PC5-64-LOADER exit-boot-services=ok attempts=1 descriptors=98` | `ExitBootServices` accepted first try, with a 98-descriptor map |
| `PC5-64-MAP entries=98 …`, `PC5-64-MEM status=ok …`, `PC5-64-MEM-SELFTEST … result=ok` | the kernel mapped the same map, sized the frame allocator from it, and passed 17 allocator behaviours |
| `PC5-64-TSS status=ok … tr=not-loaded reason=ltr-unverifiable-here` | the GDT and TSS were built and validated; TR is reported honestly, see below |
| `PC5-64BIT-LONG-MODE-OK`, `PC5-64-DONE` | the mode contract, and the kernel reaching its own end |

## What the gate had to learn first (three defects, all found by running it)

1. **A stale pairing, reported as four unrelated failures.** The first 64-bit gate run
   booted `build/pc5.iso` while handed `build/efi64-release.img`, and the ISO still
   carried the 32-bit ESP. Every stage then failed on kernel sizes. The gate now compares
   the El Torito boot image against the file it was pointed at, byte for byte
   (`the ISO embeds the release ESP the build produced`), so the *pairing* is the failure
   that gets reported, with both digests.
2. **An in-ISO label that could drift from the image.** `BUILDINFO.TXT` inside
   `build/pc5.iso` said `uefi-path: 64` while the ESP held `KERNEL.BIN`, and its
   `esp-loader:` quoted the other mode's loader — caught by the new
   `the ISO's build record names the medium it describes` row, on a build every other
   check passed. The build side was fixed alongside it: `$(BUILD)/pc5.iso.stamp` holds
   `mode=`, `commit=` and `loader=` and is what `BUILDINFO.TXT` depends on, because a
   timestamp-only dependency cannot notice a mode switch in a directory that already holds
   both loaders.
3. **Controls graded with the wrong vocabulary.** `loader reports the missing KERNEL.BIN
   as E2` is a 32-bit expectation; the 64-bit boot had reported the same fault as
   `kernel-not-found` and the gate read it as a pass-requiring-absence. Each control's
   expected code now comes from the profile, and a small-memory run is stopped on the
   profile's own markers.

## Two NOT TESTED rows, and why they are not failures

* `disk-style UEFI boot (ESP attached as a block device)` — the release medium carries its
  ESP as an El Torito image inside ISO9660, so there is no ESP *partition* for firmware to
  mount. Structural, not a gap in this run.
* `loader refuses a kernel with a corrupted header (bootloader fault): KERNEL64.BIN has no
  header field to corrupt yet` — the honest shape of the missing-image-header control.
  `KERNEL64.BIN` starts `fa 48 89 fb` (`cli; mov rbx, rdi`), so there is no field to
  corrupt and a mutant that cannot be built must not read as a refusal that worked. The row
  becomes enforced the moment the format carries a header, with no flag to remember,
  because `the gate's kernel-header claims match the kernel image on the medium` fails if
  the gate's claim and the medium disagree. 32-bit keeps the enforced version of the same
  control (`… as E4`, PASS).

## Not claimed here

`tr=loaded` (TR is still not loaded — see `docs/KNOWN_LIMITATIONS.md` and the TSS/IST
evidence; IST1/IST2 are allocated and validated, and no exception has yet been *shown* to
run on them), an SMP or APIC timer claim, anything about physical hardware, anything about
a PS5, and any CI statement: **no GitHub Actions run backs this page**. Local green is not
Actions green, and this milestone was not granted Actions activation.

## Reproducing it

`/tmp` is wiped between Arena workspaces, so the emulator and firmware need the recipe in
`docs/BUILDING.md` first. Then:

```sh
SOURCE_DATE_EPOCH=1700000000 make PC5_UEFI=64 iso verify   # expect VALID, MANIFEST VALID
make PC5_UEFI=64 ovmf-test                                 # expect PC5-OVMF-BOOT-OK, exit 0
make iso verify && make ovmf-test                          # the 32-bit release, same shape
bash docs/evidence/2026-11-23-64bit-boot-gate/verification.sh  # expect: OK (43 checks)
```

Alongside them: the four captured run logs (`x64-gate.log`, `x64-build.log`,
`ia32-gate.log`, `ia32-build.log`, plus the two `metadata.log`) — the check lists the gate
printed and the digests the build measured, so the exit codes in the table above are read
from the terminal output that produced them rather than transcribed by hand.

[`verification.sh`](verification.sh) re-derives 43 assertions from the files in this
directory alone — no network, no build, no QEMU — including that `summary.json`'s two
copies of the build record are the same record and both name a full 40-character commit
with no `-dirty` suffix, which is the part that makes this page's numbers about *this*
tree. [`summarize.py`](summarize.py) is the generator that produced it from `build.log`,
`gate.log`, `metadata.log` and the gate's own report, so the record is not a hand-typed
transcription of a terminal. Like its sibling document, it can quote the recipe and the
digest of the commit the image was built from, but never the digest of a build that
includes this file: adding text here changes the commit, which changes `BUILDINFO.TXT`,
which changes the image.
