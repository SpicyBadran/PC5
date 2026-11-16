# PC5 architecture — as implemented

> **Scope.** This document describes the code that exists: the boot chain, the
> kernel and the host tools. It is not the product architecture — that is
> [`architecture/PC5_ARCHITECTURE_SPEC_v2.md`](architecture/PC5_ARCHITECTURE_SPEC_v2.md),
> where PC5 is one platform whose destination is genuine PS5 system software on
> PC hardware. Read this page as the map of the current implementation.

## 1. Boot chain as implemented today

```
 firmware (BIOS/SeaBIOS)
   │  two paths, one boot image (ADR-014):
   │    El Torito no-emulation entry written by tools/mkiso.py
   │      -> firmware copies the 4096-byte image to 07C0:0000 and jumps
   │    hybrid MBR in the ISO9660 system area (sector 0 of the image)
   │      -> boot/mbr.S (440 bytes) reads that same image with INT 13h
   │         extended read into 0000:7C00 and jumps there with DL intact
   ▼
 boot/loader.S         real mode, 4096 bytes, loaded at 07C0:0000
   ├─ COM1 serial console (115200 8N1) + failure codes
   ├─ INT 15h E820 memory map            → 0x2000
   ├─ INT 10h 1130h firmware 8x16 font   → 0x2900
   ├─ VBE 4F00/4F01/4F02 linear mode     → 0x2800  (text mode fallback)
   ├─ INT 13h read of ISO9660 sector 16  → 0x3A00  (proves the medium reads,
   │                                                and detects 2048 vs 512
   │                                                byte disk units)
   ├─ INT 13h read of the kernel image   → 0x10000 (signature "PC5K" checked)
   ├─ struct pc5_boot_info               → 0x4000
   └─ GDT + CR0.PE, 32-bit far jump      → 0x10000 with EBX = 0x4000
   ▼
 kernel/start.S        identity flat 32-bit entry, .bss cleared, IDT installed
   ▼
 kernel/main.c         console → timer → CPUID → E820 → PCI → SMBIOS → ACPI
                       → firmware probe → storage (ATAPI + ISO9660 self-test
                       → partition scan, read-only)
                       → keyboard → chain → interface
```

Fixed low-memory contract (both `boot/loader.S` and `kernel/include/pc5.h`):

| Address | Contents |
|---|---|
| `0x2000` | E820 entries (up to 40 × 24 bytes) |
| `0x2500` | VBE controller information |
| `0x2600` | copied VBE mode list |
| `0x2800` | VBE mode information block (framebuffer address/geometry) |
| `0x2900` | 256-glyph 8×16 font copied from the video BIOS |
| `0x3A00` | ISO9660 primary volume descriptor |
| `0x4000` | `struct pc5_boot_info` |
| `0x4400` | disk address packet |
| `0x10000` | kernel image (limit `0x8F000`) |
| `0x9F000` | stack top |

`_Static_assert`s in `kernel/include/pc5.h` pin the struct offsets so the
kernel and the loader cannot drift apart silently.

## 2. Kernel modules

| File | Responsibility |
|---|---|
| `kernel/start.S` | entry stub, `.bss` clear, protected-mode continuation, IDT with 32 real exception gates |
| `kernel/main.c` | boot order, evidence log, chain evaluation, hand-off to the UI |
| `kernel/console.c` | linear-framebuffer console (firmware font), VGA text fallback, shadow character grid, framebuffer read-back self-test, panic |
| `kernel/serial.c` | 16550 polling driver on COM1 |
| `kernel/lib.c` | freestanding string/memory/formatting (`pc5_snprintf` is the only formatter in the tree) |
| `kernel/cpu.c` | CPUID: vendor, brand, family/model, logical CPUs, feature bits, long-mode |
| `kernel/mem.c` | E820 accounting (usable, addressable, top of memory) |
| `kernel/pci.c` | configuration mechanism #1 enumeration and classification |
| `kernel/acpi.c` | RSDP discovery (EBDA + BIOS area), RSDT walk, table signatures |
| `kernel/smbios.c` | `_SM_`/`_SM3_` anchor, structure walk (BIOS, system, board, CPU, DIMMs) |
| `kernel/ata.c` | legacy ATA/ATAPI PIO driver: LBA28 512-byte sector read/write for disks, packet reads on 2048-byte blocks for optical media |
| `kernel/block.c` | block-request rules with no port I/O, so the host tests can prove them |
| `kernel/part.c` | MBR/GPT parsers (pure) and a read-only, explicit-device scan |
| `kernel/fat32.c` | FAT32: BPB validation, FAT chains, directory traversal, 8.3 lookup and case-insensitive path resolution, file read; three write operations (create, overwrite-in-place, mkdir) sharing one discipline — plan and validate the whole allocation in memory, build in unreferenced clusters, write every FAT copy and re-read it, flush, one directory-entry write, flush, read back through the name, then reclaim the replaced chain |
| `kernel/sha256.c` | SHA-256 for write verification (and, later, update manifests) |
| `kernel/storage_test.c` | explicit `wtest` write/read-back self-test on a reserved window |
| `kernel/iso9660.c` | ECMA-119 volume mount, root directory walk, file read on top of `ata.c` |
| `kernel/kbd.c` | 8042 PS/2 keyboard, polled, scan-code set 1 translation, E0 handling |
| `kernel/timer.c` | PIT calibration against the TSC; millisecond timeouts and uptime |
| `kernel/chain.c` | the 13-stage PS5 compatibility chain and its evidence strings |
| `kernel/fw.c` | firmware probe (manifest presence), never a claim of execution |
| `kernel/ui.c` | six pages, controller/keyboard navigation, serial recovery console, audit log |

The kernel is deliberately small (about 65 KB in the 0.3 line, of which the filesystem
is the largest single module) and freestanding: no libc, no
allocator, no paging, no interrupts beyond the IDT gates. Everything is
polled, which keeps the boot deterministic and makes failures reproducible.

## 3. Host tooling

| Tool | Purpose |
|---|---|
| `boot/mbr.S` | 440-byte hybrid MBR bootstrap: relay at 0000:0500, INT 13h extended read of the boot image, hand-off at 0000:7C00 |
| `tools/mkiso.py` | ISO9660 (ECMA-119) + El Torito writer plus the hybrid MBR in the system area, byte-exact control of the boot descriptor, deterministic layout |
| `tools/verify_iso.py` | independent verifier: system-area MBR (signature, one entry, type/flag/extent, CHS, patch words), volume descriptors, catalog checksum, boot entry, descriptor CRC, kernel signature, per-file SHA-256 |
| `tools/firmware.py` | firmware container identification, entropy profile, manifest generation — no decryption |
| `tools/pc5ctl.py` | operator CLI: `info`, `verify`, `boot`, `chain`, `fw`, `usb` |
| `tests/v86_runner.mjs` | boots an image in v86, captures serial and the VGA text screen, injects serial/PS2 input |
| `tests/boot_test.py` | asserts boot markers and the rendered interface, writes `build/test/boot-evidence.json` |
| `tests/kernel_unit.c` | host-side unit tests for the kernel's pure logic, including the block rules and SHA-256 vectors |
| `tests/test_storage.py` | contracts for the write test, its scratch window and harness safety |
| `tests/test_partitions.py` | fixtures and contracts for partition discovery |
| `tests/disk_images.py` | deterministic MBR/GPT disk fixtures used by the boot test |
| `tests/fat32_images.py` | deterministic FAT32 fixture, an independent FAT32 reader, and structural checks (FAT copies, chains, orphans, FSInfo, changed sectors) |
| `tests/fat32_check.c` | host driver that runs `kernel/fat32.c` against a real image |
| `tests/fat32_write_check.c` | host driver for the create, overwrite, mkdir and read paths, with write/flush/copy-targeted fault injection and an LBA log of every write |
| `tests/test_fat32.py` | three-way digest comparison and refusal contracts |
| `tests/test_fat32_write.py` | creation matrix, refusals, FSInfo and flush behaviour, fault-injection classification |
| `tests/test_fat32_expand.py` | overwrite, directory creation, path handling, integrity invariants and a per-write failure sweep that classifies what each failure left behind |
| `tests/test_hybrid.py` | the assembled MBR bootstrap byte by byte, the layout it produces, and one rejection test per way the table can lie about the image |
| `tests/mbr_fixture.py` | synthetic and really-assembled bootstrap fixtures shared by the unit tests |
| `tests/test_tools.py` | unit tests for the ISO writer, verifier and firmware tool |
| `tests/uefi_stub_firmware.py` | executes the real `BOOTX64.EFI` on the Unicorn CPU emulator against a stub firmware (protocols at their specified slots, two file-system volumes); emulates `lgdt`/segment loads/`lretq` |
| `tests/uefi_stubtest.py` | asserts 42 properties of that run, plus two negative runs; `make stubtest` |

## 4. Interfaces that the next stages plug into

* **Display**: `console_init()` accepts any boot info with a VBE mode block;
  a GOP-based mode can be supplied by a future UEFI loader without touching the
  console.
* **Input**: `kbd_read()` is the only input source in the UI; a USB HID or
  DualSense translator is a second source behind the same interface.
* **Storage**: two interfaces with different units, deliberately (ADR-013).
  `ata_read_blocks()` reads 2048-byte ISO9660 blocks from the selected boot
  device and is what `iso_read_file()` uses; `ata_read_sectors()` and
  `ata_write_sectors()` move 512-byte sectors on an explicitly indexed ATA disk
  and are what a filesystem, partition parser or installer will use. AHCI, NVMe
  and USB storage implement the same sector contract behind the same rules
  (`kernel/block.c` validates every request).
* **Filesystem**: `kernel/fat32.c` talks to a disk only through `struct
  fat32_io` — a `read` callback, an optional `write` callback and an optional
  `flush` callback, each `(ctx, lba, count, buf)`. The mount made during boot
  attaches a read callback and nothing else, so the boot path cannot write even
  by mistake; `mounted_write_io()` is the one place a write callback is attached,
  and only the explicit console commands reach it. Every write entry point
  (`fat32_create_file`, `fat32_overwrite_file`, `fat32_create_dir`) is a function
  of that interface alone, which is why the host driver can run the real module
  against an in-memory image with fault injection and no emulator.
* **Firmware**: `fw_probe()` consumes a manifest compiled into the kernel; the
  manifest is produced by `tools/firmware.py` from a user-supplied image.
* **Compatibility chain**: `chain_evaluate()` takes one argument per subsystem,
  so a new subsystem adds a stage without rewriting the reporting.

## 5. The UEFI boot path

The ISO carries two El Torito entries, so one medium boots on either kind of
firmware:

```
catalog +32   default entry  platform 0x00 (80x86)  -> boot/loader.S (BIOS)
catalog +64   section 0x91   platform 0xEF (UEFI)   -> build/efi.img
catalog +96   the UEFI entry                        -> \EFI\BOOT\BOOTX64.EFI
```

`boot/uefi/` is a freestanding PE32+ EFI application, cross-linked by binutils'
`i386pep` emulation with `--enable-reloc-section`. No EDK2, no mingw: the same
gcc and binutils the kernel uses. `bootx64.S` supplies the two things a C
compiler will not: the ms_abi entry frame (firmware calls `efi_main` with
ImageHandle in RCX and SystemTable in RDX, and PE images arrive with `.bss`
uninitialised) and the handover trampoline.

The handover order in that trampoline is the part that has to be exactly right:
in 64-bit mode CR0.PG cannot be cleared, so the CPU leaves long mode with a far
return into a 32-bit compatibility segment, and only then is paging switched
off before the flat 32-bit GDT is loaded and the kernel is entered at `0x10000`
with EBX = `0x4000`, which is what `boot/loader.S` does from real mode.

What the loader reports comes from firmware and is labelled as such:

| Field | UEFI source | Difference from the BIOS path |
|---|---|---|
| `e820_addr/count` | UEFI memory map, converted (conventional memory → type 1, everything else → type 2) | loader data is *not* reported as usable |
| `vbe_mode_addr`, `vbe_ok` | a synthetic VBE block describing the GOP framebuffer (32 bpp, direct colour) | GOP format 1 (BGR) is reported honestly; the kernel writes RGB order, so channels swap |
| `font_addr` | always 0 | firmware hands over no VGA font, so the kernel uses its text-mode fallback |
| `pvd_ok`, `volume_id`, `kernel_lba`, `iso_sectors` | 0 / empty / 0 / 0 | the file protocol does not expose the ISO9660 descriptor or a file's LBA; `flags` bits 1 and 2 say so, and the kernel reads the descriptor itself when it probes storage |
| `boot_drive` | `0xFFFFFFFF` | there is no BIOS drive on this path |

Everything about that handover that can be checked without firmware is checked:
`tools/eficheck.py` validates the PE32+ image (machine, subsystem, entry point,
base relocations, no imports), `tools/mkefiimg.py` writes and re-reads the FAT
ESP, and `tools/verify_iso.py` walks the ESP out of the finished ISO and
confirms a PE32+ EFI application sits at `\EFI\BOOT\BOOTX64.EFI`.

The handover is also *executed*: `tests/uefi_stub_firmware.py` runs the real
`BOOTX64.EFI` on the Unicorn CPU emulator, with firmware replaced by a stub that
answers Simple Text Output, GOP, Boot Services, Loaded Image, Device Path and a
two-volume Simple File System at the slots the specification assigns, and
`tests/uefi_stubtest.py` (48 checks, `make stubtest`) asserts the trace, the
boot-info block, the VBE block, the E820 conversion, `ExitBootServices`, the GDT
and the far jump. Because that stub is not firmware — no ACPI, no PCI, no
interrupts, and `lgdt`/segment loads/`lretq` emulated by the harness — a stub run
proves the loader's logic and the handover contract, not UEFI compatibility.

The firmware side is proved separately by `make ovmf-test`, which boots the ISO
under genuine TianoCore OVMF in QEMU, cold and warm, and prints
`PC5-OVMF-BOOT-OK` from the harness after 40 checks (1 NOT APPLICABLE). Physical
UEFI hardware is still untested; see `docs/TEST_RESULTS.md` §16.

## 6. Why a Linux foundation is not in this build

No Linux kernel source or binary could be *built or booted* here (apt is
unreachable, the Kconfig prerequisites and a QEMU/KVM vehicle are absent, and
kernel.org is blocked — though `codeload.github.com` can serve the source), so a
Linux-based PC5 runtime could not be built or tested. ADR-009 in
`docs/TECHNICAL_DECISIONS.md` records Linux as the accepted foundation for PC5
Native and the freestanding kernel as the recovery environment; the constraint is
execution, not direction. The bring-up kernel is therefore the whole
runtime, and it establishes the hardware-inventory and reporting layer that a
future Linux image would reuse. See `docs/KNOWN_LIMITATIONS.md` and
`docs/ROADMAP.md`.
