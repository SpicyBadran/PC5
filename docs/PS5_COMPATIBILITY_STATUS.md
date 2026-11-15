# PS5 compatibility status

Build: PC5 0.2.0. Every line below is what the boot environment prints on this
build; nothing is aspirational.

## 1. The twelve project levels

| Level | State | Evidence |
|---|---|---|
| Firmware analysed | **NO** | no image supplied; `tools/firmware.py` ready, nothing to run it on |
| Firmware loading pathway implemented | **NO** | `kernel/chain.c` stage 7 is BLOCKED by design |
| Firmware initialisation attempted | **NO** | stage 8 NOT-STARTED |
| Firmware executing | **NO** | stage 9 NOT-STARTED; why any PC route is blocked is recorded in [`playstation/BOOT_FEASIBILITY.md`](playstation/BOOT_FEASIBILITY.md) |
| PS5 system services operating | **NO** | stage 10 NOT-STARTED |
| Genuine PS5 home screen operating | **NO** | stage 11 NOT-STARTED |
| Controller navigation operating | **NO** (PC5 environment only) | PS/2 keyboard works in the PC5 UI; no controller driver |
| Network connectivity operating | **NO** | PCI enumeration only; no driver, no stack |
| Authorised PlayStation services operating | **NO** | nothing implemented, nothing attempted |
| Actual PS5 game installation operating | **NO** | no package parser in the kernel, no storage driver |
| Actual PS5 game execution operating | **NO** | no executable loader, no PRX shims |
| Actual PS5 gameplay verified | **NO** | no title has been run |

## 2. The thirteen-stage chain, as the machine reports it

Captured from the emulated boot (`build/test/screens.txt`):

```
 >  1. PC5 boot environment executing          OK
    2. Host hardware inventoried               OK
    3. Controller / input path                 PARTIAL
    4. Storage stack for game installation     PARTIAL
    5. PS5 firmware image supplied             BLOCKED
    6. Firmware format analysed                BLOCKED
    7. Firmware loading pathway implemented    BLOCKED
    8. Firmware initialisation attempted       NOT-STARTED
    9. Firmware executing                      NOT-STARTED
   10. PS5 system services operating           NOT-STARTED
   11. Genuine PS5 home screen                 NOT-STARTED
   12. PS5 game installation                   NOT-STARTED
   13. PS5 game execution                      NOT-STARTED
```

Why each non-OK stage has that state:

| Stage | Reason |
|---|---|
| 1 OK | the kernel is executing and says which CPU it is on |
| 2 OK | CPUID, E820 and PCI produced values on this machine |
| 3 PARTIAL | an 8042 keyboard driver exists; DualSense needs the USB stack that does not |
| 4 PARTIAL | the kernel mounts the ISO through its own ATAPI PIO + ISO9660 code and reads `KERNEL.BIN` back; there is still no writable target, no partition table support and no package installation |
| 5 BLOCKED | no firmware image in the build, and PC5 does not redistribute one |
| 6 BLOCKED | depends on 5; host-side analysis exists but has had nothing to analyse |
| 7 BLOCKED | no verified pathway exists to load Sony-signed system software on a PC |
| 8–13 | nothing has been attempted, so nothing can be claimed |

## 3. What is genuinely working on the PC5 side

| Feature | Status | Evidence |
|---|---|---|
| Bootable ISO (BIOS/El Torito + UEFI El Torito section + hybrid MBR) | COMPLETE AND VERIFIED (emulated) | `make verify` 75/75, `make boot-test` 87/87; MBR path booted with the CD detached, control with sector 0 blanked did not boot |
| Loader (A20, E820, VBE, font, ISO probe, kernel load, PM handoff) | COMPLETE AND VERIFIED (emulated) | serial log |
| Framebuffer console + text fallback + read-back self-test | COMPLETE AND VERIFIED (emulated) | Diagnostics page |
| CPUID / E820 / PCI / ACPI / SMBIOS inventory | IMPLEMENTED — TESTING REQUIRED | hardware page; needs physical runs |
| PS/2 keyboard navigation | IMPLEMENTED — TESTING REQUIRED | 8042 driver; emulator input only |
| Serial console with 11 commands | COMPLETE AND VERIFIED | boot test drives it |
| Compatibility chain + evidence reporting | COMPLETE AND VERIFIED | unit tests + boot test |
| ISO writer + independent verifier + hybrid MBR (reproducible under SOURCE_DATE_EPOCH) | COMPLETE AND VERIFIED | 62 host tests (25 tools + 37 hybrid MBR), `docs/TEST_RESULTS.md` §14 |
| Release packaging (`make release-assets`) + provenance in `build-info.json` | COMPLETE AND VERIFIED (local) | `docs/TEST_RESULTS.md` §6 |
| CI pipeline that publishes releases | WRITTEN, ACTIVATION BLOCKED (token lacks the workflows permission) | `ci/README.md` |
| Firmware analysis tool (no decryption) | COMPLETE AND VERIFIED | unit tests on synthetic containers |
| Kernel ATAPI PIO driver + ISO9660 reader | COMPLETE AND VERIFIED (emulated) | `storage_mounted volume=PC5 … signature=ok` |
| Kernel MBR + GPT partition discovery (read-only) | COMPLETE AND VERIFIED (emulated disk + unit tests) | `partitions device=0 scheme=gpt count=2 status=ok`, and `status=crc` for a damaged table |
| Kernel FAT32 read-only filesystem | COMPLETE AND VERIFIED (emulated disk + host driver) | `fat32 device=0 partition=1 status=ok label="PC5FS"`; file SHA-256 matches an independent reader |
| Kernel FAT32 create (one root file) | COMPLETE AND VERIFIED (emulated disk + host driver) | `put NEW.TXT hello-m1g` → independent reader matches, originals intact, both FAT copies equal |
| Kernel ATA PIO write path (explicit test only) | COMPLETE AND VERIFIED (emulated IDE disk) | `storage_write=ok … verify=ok sha256=…`; host-side image comparison in `make boot-test` |
| UEFI boot path | IMPLEMENTED — EXECUTED AGAINST STUB FIRMWARE (not OVMF) | `make stubtest` 42/42 running the real `BOOTX64.EFI`; no OVMF or hardware run yet |
| Writable filesystem, installer | PARTIAL (create only) | a new 8.3 root file can be created (M1g); no overwrite, delete, resize or install |
| AHCI / NVMe | NOT STARTED | legacy ATA channels only |
| USB stack / DualSense | NOT STARTED | — |
| Network stack | NOT STARTED | — |
| Audio | NOT STARTED | — |
| Installer / updater | NOT STARTED | — |

## 4. Honest summary

PC5 today is a real, reproducible, bootable PC environment with working
diagnostics, a verified raw-sector write path, read-only partition discovery and
a read-only FAT32 filesystem, and an honest compatibility model. It is **not** a PlayStation 5: no Sony code
runs, no PS5 game runs, and no stage of the genuine-PS5 chain has been reached.
The hybrid medium is done (M1h): the image carries an MBR in the ISO9660 system
area and boots as a hard disk in the emulator with the CD detached, so the next
real engineering step is the installer (overwrite/rename, directory creation, a
boot entry), followed by a recorded USB boot on physical hardware. The UEFI run
under real firmware stays blocked on OVMF or hardware.
