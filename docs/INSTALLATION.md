# Installing and booting PC5

## 1. Get the image

```sh
gh release download --repo SpicyBadran/pc5       # or build it: see docs/BUILDING.md
ls                     # pc5.iso  pc5.iso.sha256  build-info.json
sha256sum -c pc5.iso.sha256
```

Release assets are produced by the CI pipeline from the tagged commit, never
uploaded by hand. `build-info.json` records the layout, per-file hashes and the
build provenance (commit, toolchain, and the result of each test that ran);
`python3 tools/verify_iso.py pc5.iso --info build-info.json` re-checks the image
independently.

## 2. Write it to a USB stick

Safety rules enforced by `pc5ctl usb`:

* the checksum is re-verified before writing,
* the target must be a `/dev/` block device that exists,
* the device holding `/` is refused,
* nothing is written without `--yes`.

```sh
python3 tools/pc5ctl.py usb --iso build/pc5.iso --device /dev/sdX
# check the device path twice, then:
python3 tools/pc5ctl.py usb --iso build/pc5.iso --device /dev/sdX --yes
```

Or use a graphical imager (balenaEtcher, Rufus in DD mode, `dd` directly).

**What the image boots from, and what is proven:** one file boots three ways.
El Torito (a real CD/DVD, or a USB stick the firmware presents as a CD-ROM class
device, `disk unit bytes 0800`), the UEFI El Torito entry (a FAT ESP holding
`\EFI\BOOT\BOOTX64.EFI`), and — since M1h — the hybrid MBR in the ISO9660 system
area, which is what firmware that insists on a USB stick being a *hard disk*
reads (`disk unit bytes 0200`). All three reach the same stage-1 loader and the
same kernel; the emulated evidence is in §14 of `docs/TEST_RESULTS.md`.

What is *not* proven: physical hardware. The MBR path has been booted in an
emulator with the CD detached (and, as a control, does not boot at all when
sector 0 is blanked), never on a real USB stick on a real machine. Firmware with
quirks — a BIOS that ignores the partition table, one that demands a `0x0C`/`0x83`
type, or one whose USB-HDD mode mis-detects the geometry — may still refuse.
See `docs/KNOWN_LIMITATIONS.md` and `docs/HARDWARE_COMPATIBILITY.md`.

## 3. Boot the machine

1. Enter the firmware setup, enable legacy/CSM boot (needed for the current
   BIOS path), and disable Secure Boot (PC5 has no signed shim yet).
2. Select the USB device or CD/DVD drive as the boot device.
3. The PC5 boot environment starts on the Status page.

Expected boot log on COM1 (115200 8N1):

```
PC5 loader 0.2.0
pc5: framebuffer 1024x0768
pc5: medium is ISO9660, disk unit bytes 0800
pc5: kernel image verified
pc5: medium volume PC5
pc5: entering 32-bit kernel
PC5 KERNEL 0.2.0 …
PC5-LOG …
PC5-BOOT-OK
```

## 4. Using the environment

| Input | Action |
|---|---|
| ↑ / ↓ | move through the compatibility chain |
| 1 – 6 | switch page (Status, Hardware, Firmware, Diagnostics, Log, Power) |
| R / H | reboot / halt (Power page) |
| serial `help` | list console commands |

The serial console accepts: `status`, `chain`, `hw`, `fw`, `diag`, `log`,
`screen`, `page N`, `reboot`, `halt`.

## 5. Troubleshooting

| Symptom | Meaning | What to do |
|---|---|---|
| `Boot failed: Could not read from CDROM` | firmware rejected the El Torito catalog | verify the image with `make verify`; re-flash |
| `pc5: fatal: A20 gate did not open` | firmware/CPU issue | report the host; try another machine |
| `pc5: fatal: kernel load failed` followed by numbers | disk read error (`LBA sectors AH dest first-dword`) | the medium is unreadable at the kernel extent; re-flash |
| `pc5: VBE unavailable, using text mode` | no linear-framebuffer mode was offered | not fatal: the VGA text UI is used |
| Screen blank but serial log complete | framebuffer address wrong on this firmware | report with the `PC5-LOG display=` line |
| Nothing at all | machine offered no legacy/CSM boot path | the image has a UEFI boot path too (`M1c`, verified under OVMF only) — try a UEFI boot, but no physical UEFI machine has been tested |
| `PC5-OVMF-BOOT-OK` appears instead of `PC5-BOOT-OK` | the UEFI loader ran | expected on the UEFI path; see `docs/TEST_RESULTS.md` §16 |
| `PC5-UEFI FAILURE E15` | the loader's handover window is not conventional memory on this machine | not a bad medium: this firmware reserves low memory PC5 needs. Report the machine and its firmware version; the boot is refused rather than corrupting firmware memory |
| `PC5-UEFI FAILURE E16` | the firmware memory map has more distinct regions than the 40-entry E820 buffer | report the machine; the map is never truncated, because a truncated map under-reports memory |
| `PC5-UEFI FAILURE E17` | the kernel copy at the handover address did not read back as the file | memory fault or a loader bug; the handover is refused instead of jumping into wrong bytes |
| `PC5-UEFI FAILURE E13` | `ExitBootServices` was refused eight times with a fresh map key | firmware keeps invalidating the map; the boot stops instead of pretending boot services ended |

## 6. Image targets

PC5 intends four distinct images. Only the first three may ever be distributed;
the fourth exists in the plan so that the boundary is explicit.

| Target | Boots | Contains | Distributable |
|---|---|---|---|
| **PC5 Native installation ISO** | BIOS (El Torito) today; UEFI via ESP | kernel, loader, installer, docs | yes |
| **PC5 Native recovery ISO** | same as above | kernel, diagnostics, repair tools | yes |
| **PC5 diagnostic/research ISO** | same as above | kernel, inventory, serial console, analysis tools | yes |
| **Experimental Prospero compatibility image** | would depend on authorised material | never Sony firmware, keys or derived binaries | only if feasibility is demonstrated *and* distribution is authorised — today: does not exist |

The `.PUP` an owner may supply is an input to analysis, never to an image: it is
never committed, extracted into the tree, redistributed or modified (ADR-022).
See `docs/PS5_SYSTEM_SOFTWARE_TARGET.md` and `docs/FIRMWARE_ANALYSIS.md` §7.
Installing genuine PS5 system software is `M21` — blocked on `M17` and `M18`,
and nothing in this document should be read as a procedure that exists.

## 7. Installer architecture (not implemented)

The installer needs four primitives, and two of them now exist:

| Primitive | State |
|---|---|
| write 512-byte sectors on an explicit disk | done (M1d), emulated disk only |
| read MBR/GPT tables without modifying them | done (M1e), read-only |
| read a filesystem on a partition | done (M1f): FAT32 mount, list, find, read |
| create a file on a partition | done (M1g): create one 8.3 root file, allocate, flush, verify |
| overwrite an existing file, create directories, 8.3 paths | done (M1i), emulated disk + host tests |
| delete, resize, install a boot entry, drive a real disk | **missing** — `M3`, `M8` |

What the installer will *not* do: guess a target disk, format without an explicit
confirmation that names the device and its size, or report success without
re-reading what it wrote. Those rules are already enforced in the write path
(explicit device index, refuses ATAPI, refuses unknown capacity). An installation
is only "verified" when the installed system boots with the medium removed and
the serial log is committed — which requires physical hardware, so it stays
unproven here.

## 8. USB boot today

The image is hybrid: the same file boots through El Torito (CD/DVD, USB presented
as CD-ROM) and through the MBR in the ISO9660 system area (USB presented as hard
disk). The MBR bootstrap loads the very same 4096-byte stage-1 loader that El
Torito names, so both paths converge before the first PC5 instruction runs. On
the hard-disk path the loader sees 512-byte units and says so.

Writing to a stick is safe (`pc5ctl usb` verifies the checksum and refuses the
root device), and the medium no longer has to be a CD on a firmware level. What
remains unproven is the stick on real hardware: the emulator proves the MBR and
the boot path, not that a particular BIOS likes them. `docs/KNOWN_LIMITATIONS.md`
item 5 states the boundary, and `docs/HARDWARE_COMPATIBILITY.md` §6 lists the
firmware behaviours that could still refuse the medium.

## 9. Removing PC5

There is no installer yet: PC5 runs entirely from the medium and does not write
to the host. Remove the USB stick, or restore the firmware boot order. The
kernel's own write path (`M1d`, `M1g`, `M1i`) is driven by explicit commands and
is not exercised by a boot; it has only ever run against emulated disks.
