# The 64-bit release ISO, booted under genuine firmware

**Date:** 2026-11-23 · **Commit this image was built from:** `e784f66` (main `964b61e741820bb03d26a462f1ade8866ec54cea`
after this PR merged; the tree is identical, and the ISO above was built from a fresh clone of it)
· **Result:** `PC5-64BIT-LONG-MODE-OK` and `PC5-64-DONE`, no `PC5-64-LOADER-FAIL`
line, `tr=not-loaded` reported honestly.

This is the first time PC5's **64-bit** boot path has been executed by firmware that is not
PC5's own stub. It matters because `make stubtest64` had been green — 41/41, then 48/48 — on
exactly the image that could not boot at all, and the reason it could not boot was invisible
to every check the project had.

## What was run

Not a PE inspection, not an emulation of the loader's C code: QEMU's q35 machine with the
EDK II OVMF firmware volume, the release ISO attached as the El Torito medium, and the
firmware's own boot manager choosing `EFI/BOOT/BOOTX64.EFI` from the ESP inside that ISO.

```
$qemu-system-x86_64 --version | head -1
QEMU emulator version 11.0.2
```

| Component | SHA-256 | Size |
| --- | --- | --- |
| `qemu-system-x86_64` (musl build, npm `qemu-portable-linux-x86_64-musl@0.2.1`) | `0539587908d9a9b83ccdce7a48d968b50f827476b9852814e07445e9402df562` | — |
| `edk2-x86_64-code.fd` (OVMF CODE) | `33090cc07675baa5190d9f1e84bf5176b33bcbfa9bacac522961150cdb6dbb2a` | 3653632 |
| `edk2-i386-vars.fd` (OVMF VARS template, fresh copy per run) | `5d2ac383371b408398accee7ec27c8c09ea5b74a0de0ceea6513388b15be5d1e` | 540672 |

QEMU 11.0.2, packaged from upstream `v11.0.2` (`sourceArchiveSha256`
`3745f6ea88e2e87fe0dc838b2b1d4e0a770bf48e01a1d5a186842a1fff76ccf5`, GPL-2.0-only); the
firmware is the OVMF shipped **inside that same package**, not downloaded separately. The
guest is TCG-only (`-machine q35`, no `accel=` needed), no network, `-no-reboot`, and a
separate disposable copy of the variable store per boot.

```
timeout 260 "$QEMU" -machine q35 -m 512 \
  -drive if=pflash,format=raw,unit=0,readonly=on,file="$OVMF_CODE" \
  -drive if=pflash,format=raw,unit=1,file="$VARS_COPY" \
  -cdrom build/pc5.iso -serial file:ovmf64-serial.log -display none -net none -no-reboot
```

`build/pc5.iso` here is the **release artifact**, produced by `make PC5_UEFI=64 iso` with
`BOOTX64.EFI` = `build/uefi64/bootx64.efi` and `KERNEL64.BIN` = `build/kernel64.bin`:

The ISO whose boot is on file in this directory, and the three digests measured while getting this
right:

| Built from | `pc5.iso` SHA-256 (`SOURCE_DATE_EPOCH=1700000000`) | Booted here? |
| --- | --- | --- |
| `4a3e69c` | `d8e4e2a70f195abca8c33a0f6f8ffee8f4d403630cccef9690e600ea20248caf` | yes — first genuine 64-bit boot; log differed only in `spins=5377` |
| `e784f66` (tree = `main` `964b61e`) | `d6778d0efa4ff6e450c9272a423ce33618d84d5f081cb41f8034dfe0dc060396` | yes — 68 lines, `spins=9583` |
| `main` `2a5b206` | `370a85f9be90bdea3207b1f81d25f43a0165fcf236813d75749962a046c6c94f` | **yes, and [`ovmf64-serial.log`](ovmf64-serial.log) is this boot** |

All three are 1806336 bytes, all three reached `PC5-64BIT-LONG-MODE-OK` / `PC5-64-DONE` with no
`PC5-64-LOADER-FAIL` line, and any two differ in exactly one line of 68: `PC5-64-TIMER spins=`
(5377 / 9583 / 9174), which is TCG scheduling and not a property of the image.

```
$ SOURCE_DATE_EPOCH=1700000000 make BUILD=/tmp/m2 PC5_UEFI=64 iso      # fresh clone of 2a5b206
$ sha256sum /tmp/m2/pc5.iso
370a85f9be90bdea3207b1f81d25f43a0165fcf236813d75749962a046c6c94f  /tmp/m2/pc5.iso
```

**Why the digests differ while nothing relevant changed, and the limit that follows from it.**
`BUILDINFO.TXT` carries the source commit, and `docs/BUILDING.md`'s reproducibility claim is
therefore "this commit reproduces byte for byte", never "this hash is PC5's release hash". Two
builds of one commit agree across separate `BUILD=` directories; builds of different commits
cannot, even when only tests and prose differ. One consequence is worth stating instead of
discovering: **this file can never quote the digest of the commit it is committed in** — editing
it changes the commit, which changes the ISO. What is reproducible is the recipe: check out the
commit you care about, run the `make` line above, boot it with the QEMU command above, then run

```
$ bash docs/evidence/2026-11-23-64bit-genuine-ovmf-boot/verification.sh   # expect: OK
```

which re-derives 11 assertions from the log in this directory — including that the frame
allocator's counts follow from the memory map, and that `tr=loaded` is *absent*. A second caveat
sits alongside, and it is the one that made an earlier draft of this file quote a number nobody
could reproduce: with no `SOURCE_DATE_EPOCH`, a build of this same loader plus `bootx64.S`
working tree produced `ff1c2baab92f7a4959346d22112c8a4b10f13644701e3d5e988be0c8701dbec1` —
measured on 2026-11-23 while the evidence prose was still uncommitted, so it is named here as an
example of the mechanism (FAT timestamp and serial number come from the clock) and not as a
reproducible artifact of any commit. Quote a digest with the epoch and the commit, or quote
neither.

## The log, and what each line proves

[`ovmf64-serial.log`](ovmf64-serial.log) is the complete guest output, unmodified except that
carriage returns and ANSI escapes were stripped. 68 lines. Read in order:

| Serial line | Stage it proves |
| --- | --- |
| `BdsDxe: loading Boot0001 "UEFI QEMU DVD-ROM QM00005 " …` / `starting Boot0001 …` | firmware started, discovered the El Torito boot entry on our medium, and chose it |
| `PC5-64-LOADER-BANNER 64-bit UEFI path (experimental)` | our `BOOTX64.EFI` was entered in long mode |
| `PC5-64-LOADER kernel-volume=image` | `HandleProtocol(ImageHandle, LOADED_IMAGE)` gave the device handle, and the filesystem was opened on that handle |
| `PC5-64-LOADER kernel at=0x100000 bytes=204800` | the real `KERNEL64.BIN`, read off the real medium, at the protocol address |
| `PC5-64-LOADER pagetables=0x1e1f4000 pages=6 mapped_2mib=2048 span=0x100000000` | PC5's own page tables, built and used |
| `PC5-64-LOADER image=0x10000000-0x10004000 below-span=1` | the loader's own placement was checked, not assumed |
| `PC5-64-LOADER exit-boot-services=ok attempts=1 descriptors=98` | `ExitBootServices` accepted on the first try, with **98** descriptors from the memory map |
| `PC5-64BIT-KERNEL-ENTRY`, `PC5-64-PAGING enabled=1 root=0x1e1f4000` | the kernel was entered, paging on, root = the page table the loader just reported |
| `PC5-64-MAP entries=98 conventional_pages=119288` | the kernel mapped the same 98-descriptor map the loader saw |
| `PC5-64-MEM status=ok … frames=130630 conventional=119288 free=119287 largest=59520` | **frame allocator initialised from the real map**: conventional total, then the usable pool below the 4 GiB span |
| `PC5-64-MEM-SELFTEST … result=ok` | 17 allocator behaviours verified after that initialisation |
| `PC5-64-CPU vendor=AuthenticAMD family=15 model=107 … long_mode=1 nx=1 phys_bits=40` | real CPU state, read by the kernel itself |
| `PC5-64-TSS status=ok arm-rules=1 … tr=not-loaded reason=ltr-unverifiable-here` | the TSS was built; TR is reported as not loaded, because it was not verified on this boot |
| `PC5-64BIT-LONG-MODE-OK` · `PC5-64-DONE` | the mode contract held end to end |

## Two bugs only firmware could find

Both were in code that `make stubtest64` executed and passed.

**1. `boot/uefi64/bootx64.S` destroyed the image handle.** The entry stub cleared `.bss` with
`rep stosq`, whose repeat count lives in `RCX` — and under the Microsoft x64 ABI `RCX` is
where firmware passes `ImageHandle`. So the C loader always started with `image = 0`:
`HandleProtocol(0, LOADED_IMAGE)` is `EFI_INVALID_PARAMETER`, the "which volume was I loaded
from" shortcut was gone, `ExitBootServices(0, key)` would have been refused, and the handle
scan that was meant to recover could not recover it either. `serial_write("PC5-64-LOADER
loaded-image=")` on the error path is what pinned it: `0x8000000000000002` with no other
explanation for a handle that was plainly non-NULL.

**2. `boot/uefi64/uefi64_main.c` trusted firmware's pages.** `boot_info` came from
`AllocatePages`, which returns memory holding whatever the previous owner left. The stub's
allocator hands back zeroed pages, so every field PC5 does not set read as absent — on OVMF
the residue was `flags=PC5_BOOT64_F_FRAMEBUFFER` with `framebuffer_addr=0xafafafafafafafaf`,
a 5.8 exabyte "framebuffer", and the boot died at `framebuffer-outside-span`. The address
was caught only because the span check happened to reject it. `boot/uefi/efi.h` also models
`LocateHandle` with the wrong argument order (`BufferSize` first, four arguments, per its own
comment) so the sizing call returns `EFI_INVALID_PARAMETER` with `need=0` — which is why the
fallback scan reported zero volumes even after the handle was fixed.

**The rule this earns:** a stub-green 64-bit claim is not a boot claim. Both bugs are the same
class — firmware behaviour the stub approximates instead of implementing — and the only reason
either surfaced at all was that the failing path was made to print the number it was refusing
(`loaded-image=`, `fb=… bytes=… span=…`).

## A control that was tried, and what it actually found

The obvious negative control for a boot path is: corrupt the kernel inside the release ISO and
check that the boot refuses. Run against `boot/uefi64/bootx64.S`+loader, that control **found a
gap rather than closing one.**

The first attempt rewrote `PC5K` to `XXXX` at ISO offset 1529858, and the 64-bit ISO booted to
`PC5-64BIT-LONG-MODE-OK` anyway. Adding the signature check the 32-bit loader has, and printing
the bytes actually read, showed why: the file `KERNEL64.BIN` starts with

```
PC5-64-LOADER kernel-header=0xfa0x480x890xfb0x480x8d0x250xa5
```

`fa 48 89 fb` = `cli; mov rbx, rdi` — the first instructions of `kernel64/start.S`. **The 64-bit
kernel image carries no image header at all**; the `PC5K` bytes I had been corrupting belong to
the *32-bit BIOS* `KERNEL.BIN`, which the same ISO also contains for its legacy catalog entry and
which the 64-bit path never opens. The check was therefore reverted: keeping it would have made
every 64-bit boot fail while "proving" a property the file format does not have.

That leaves a real, named gap, and it is not this boot's to fix:

* `tools/verify_iso.py` asserts "kernel image carries the PC5K signature" against the kernel
  pointed at by the boot descriptor. On the 64-bit ISO that check passes — by reading the BIOS
  kernel. The payload the 64-bit firmware path actually loads is signature-checked by nobody:
  not the host verifier, not the loader.
* Fixing it means adding a header to `kernel64`'s image format (start.S, `kernel64.ld`,
  `tools/build_release.py`, `tests/test_release_esp.py`'s digests) and then teaching the loader to
  reject a mismatch — a format decision for a dedicated slice, recorded in
  `docs/KNOWN_LIMITATIONS.md`, not a late edit to a boot PR.

## What this does *not* cover

`make ovmf-test` still asserts the **32-bit** path's markers: `tests/ovmf_boot.py`'s
`classify()` knows `PC5-UEFI:` and `PC5-BOOT-OK`, not `PC5-64-*`. So this boot is proven by
the command above and this log, and by the automated gate for the 32-bit ISO — teaching the
gate the 64-bit marker set is the open piece, recorded in `docs/KNOWN_LIMITATIONS.md`.
`tr=loaded` is **not** claimed here. The 64-bit TSS/IST work is exercised by `make stubtest64`
and the unit tests, `make emulator-probe` records what the emulator can and cannot prove about
exception dispatch, and this boot simply prints the honest `tr=not-loaded reason=...`.
