# The release ESP now ships the artifact the 64-bit gate executed

Date: 2026-11-22. Branch: `arena/ac05db7e-pc5`, base `e53b4c31`
(`main` after PR #45). Every number below was produced in this repository at that
commit, on Debian 12, with Unicorn 2.1.4 in `/tmp/pc5-venv`.

## The defect this closes

`make stubtest64` executes `build/uefi64/BOOTX64.EFI` and `build/kernel64/KERNEL64.BIN`
and passes 41/41 — both fed to it as loose files (`--image
   …/BOOTX64.EFI --kernel …/KERNEL64.BIN`), so the stub never sees the ESP at all. `make iso` embedded a *different* file — `build/efi.img`, the 32-bit
bring-up loader — and `make verify` confirmed only that the ESP had a directory called
`EFI/BOOT` containing something called `BOOTX64.EFI`. Nothing connected the tested
bytes to the shipped bytes, so "the 64-bit path is gated" and "the release does not
contain it" were both true at the same time, and no check could tell them apart.

## What changed

1. `PC5_UEFI ?= 32` selects the ESP the release embeds. Mode 64 uses
   `$(BUILD)/efi64-release.img` (`EFI/BOOT/BOOTX64.EFI`, `KERNEL64.BIN`, `README.TXT`)
   and adds `--file KERNEL64.BIN=…` to the ISO9660 root. `BUILD` became `?=` so a test
   can build into a scratch directory.
2. `make iso64` = `make PC5_UEFI=64 iso verify`, one command for a release engineer.
3. `tools/verify_iso.py` gained `--expect-esp-loader PATH` and
   `--expect-esp-file NAME=PATH`: every ESP root entry is digested through its FAT
   cluster chain and compared with the file it is supposed to be. `make verify` passes
   both, so the shipped image is checked against the gated artifacts.
4. `docs/BUILDING.md`, `docs/ARCHITECTURE.md` §7/§7.7, `docs/TESTING_STRATEGY.md` and
   `docs/KNOWN_LIMITATIONS.md` 22a were corrected to describe packaging as what it is.

## Measurements

Both modes built from a clean tree (`git archive`-style copy, `make clean` between
runs, `SOURCE_DATE_EPOCH=1700000000` exported):

| build | sha256 of `build/pc5.iso` |
|---|---|
| `make clean iso`, first | `05abdfdf2983454295d4b80c05b2ad0607d1c24d20f2fca4f66e0959c9fe4aad` |
| `make clean iso`, second | same — byte-identical |
| `make clean PC5_UEFI=64 iso`, first | `5d7d688925a099ed00e3e8017400802d4e7c3530d9856ccc468a3932b6e81e23` |
| `make clean PC5_UEFI=64 iso`, second | same — byte-identical |

So the reproducibility recipe in `docs/BUILDING.md` holds for both paths, and the two
paths differ from each other — which is what makes a digest a discriminator rather
than a formality. Reproduced in the suite by
`tests/test_release_esp.py::ReleaseReproducibility`, which includes the control that
unsetting `BUILD_DATE`/`BUILD_ID`/`SOURCE_DATE_EPOCH` moves the digest.

ESP content, in-image against built artifact, mode 64:

```
[PASS] ESP loader is byte-identical to BOOTX64.EFI  (in-iso 10787 B 8c26df9c27b9  built 10787 B 8c26df9c27b9)
[PASS] ESP KERNEL64.BIN is byte-identical to KERNEL64.BIN  (in-iso f97a22730bdd  built f97a22730bdd)
```

Corruption sampling — one byte (`XOR 0xFF`) flipped at a fixed offset inside the ESP
region of a *finished* image, carved at `uefi_image_lba * 2048`, then verified:

| flip lands at | plain `verify` | with `--expect-esp-*` |
|---|---|---|
| FAT boot sector | exit 0 | exit 0 |
| FAT area | exit 0 | exit 0 |
| offset 0x9000 (file data extent) | exit 0 | **exit 1** |
| offset 0x20000 (file data extent) | exit 0 | **exit 1** |
| last sector of the ESP | exit 0 | exit 0 |

Read the left column as the reason for the change, not as a scare: without a content
pin, damage to a shipped file is invisible to the structural checks, which re-derive
the FAT chain from the image itself. With `--info` (which `make verify` always passes)
all five flips do fail — on `image SHA-256 matches build info`, a single unnamed
whole-image digest. That is a provenance check, so it cannot distinguish "someone
edited the release image" from "the release image is not what its own build recorded",
and it is green by construction when the edit happens upstream of the record. Measured
directly: flip one byte of `build/efi64-release.img`, `make PC5_UEFI=64 iso`, `make
PC5_UEFI=64 verify` —

```
[PASS] image SHA-256 matches build info  (916044b7216fffee)
[FAIL] ESP KERNEL64.BIN is byte-identical to KERNEL64.BIN  (in-iso 7dcb0722df6f  built f97a22730bdd)
```

which is the whole argument for the pins, and it is now a test
(`ReleaseEspContents.test_a_tampered_esp_rebuilt_into_the_release_is_still_caught`)
rather than a note. Read the right column as their limit: the pins compare the files
they name and nothing else, so FAT metadata and padding stay outside `make verify`.
`tests/boot_test.py` does compare the ESP's `KERNEL.BIN` against the ISO9660 copy, and
parsing the ESP the way firmware does is `make ovmf-test`'s job, which this machine
cannot run. One methodological warning, since it nearly produced the opposite
conclusion: getting the carve offset wrong — `×512`, which is El Torito's unit for
`virtual_sectors`, not the volume's 2048-byte block size — made an earlier draft of
these notes "discover" that *all* ESP damage went undetected, including damage the
pins do catch. The unit is now a named constant in the test.

Parse-time validation, verbatim:

```
$ make PC5_UEFI=128 -n iso
Makefile:309: *** PC5_UEFI must be 32 or 64, got '128'.  Stop.
```

`tests/test_release_esp.py`: 14 tests, OK, ~50 s, each building both UEFI modes
into a scratch `BUILD` directory (`BUILD ?= build` in the Makefile exists for exactly
this reason). Coverage: each mode's ESP contents; the ESP region inside the ISO
digesting to the ESP image on disk; a swapped-expectation control using two files that
both exist, so it proves content comparison and not existence; the cross-mode digest
difference; the rebuilt-corrupted-ESP control; `make iso64` end to end, including that
its recursive `$(MAKE)` forwards `BUILD=`; and the other mode's kernel reported absent
rather than skipped.

## What this does not establish

* **No firmware has booted either image.** `make ovmf-test` exits 3 with NOT TESTED in
  this environment: no `qemu-system-x86_64`, no OVMF volume, `apt` unusable, and no
  static QEMU/OVMF obtainable from PyPI or npm. The route stays closed by measurement,
  not by effort — see `docs/evidence/2026-11-22-emulator-capabilities/` for what the
  local emulator cannot stand in for, including that `ltr` never populates TR, so
  `tr=not-loaded` remains the honest boot line.
* A passing `make iso64` is not "the 64-bit release works"; it is "the 64-bit release is
  the artifact that was gated". The exception-dispatch criteria (real `#DF`/`#GP`
  delivery on the IST, `tr=loaded`) still belong to OVMF or hardware.
* **CI activation was not attempted a fourth time.** Three pushes touching
  `.github/workflows/` were rejected by the App's missing `workflows` permission, quoted
  verbatim in `ci/README.md`; `make iso64` is therefore a local and pre-merge command,
  and `ci/build.yml` remains dormant by location only.
