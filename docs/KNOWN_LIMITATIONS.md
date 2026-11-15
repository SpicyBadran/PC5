# Known limitations

Everything below is true of the current build. None of it is a placeholder for
work that has secretly been done.

## Boot

1. **The UEFI boot path has been executed, but not under real firmware.**
   `boot/uefi/` builds a PE32+ `\EFI\BOOT\BOOTX64.EFI` application, the ISO
   carries the El Torito UEFI section that points at it, and the loader's own
   machine code now runs end to end against `tests/uefi_stub_firmware.py` — a
   stub that serves the UEFI protocols at their specified slots on top of the
   Unicorn CPU emulator (`make stubtest`, 42 checks). That harness has no ACPI,
   PCI, interrupt or timer model, and it emulates `lgdt`, the segment loads and
   the far return itself, so a passing run proves the loader's logic and the
   handover contract — *not* UEFI compatibility. No OVMF image and no
   UEFI-capable virtual machine were obtainable here, so treat a UEFI machine as
   unsupported until a recorded boot exists (§7 of `docs/TEST_RESULTS.md`). On the UEFI path the loader also cannot report the
   ISO volume id, the kernel LBA or the ISO9660 descriptor (the file protocol
   does not expose them), and it hands over no VGA font, so the kernel's
   framebuffer console falls back to text mode.
2. **USB-HDD boot is implemented, not proven on hardware.** Since M1h the image
   carries a hybrid MBR (type `0x17`, covering the whole image) and the stage-1
   loader loads the same boot image through INT 13h, verified in the emulator
   with the CD detached. It has never been booted from a physical USB stick: the
   emulated firmware (SeaBIOS) is not evidence about a particular machine's
   firmware, about USB mass-storage enumeration, or about a BIOS that insists on
   a different partition type. The MBR bootstrap reads the boot image with INT 13h
   **extended read** (`AH=0x42`), so a BIOS from before extensions existed cannot
   use that path at all (El Torito still works on such a machine). Treat "boots
   from USB on machine X" as untested until it has been recorded per §7 of
   `docs/TEST_RESULTS.md`.
3. **No Secure Boot support.** No signed shim, no enrolled keys.
4. **No installer.** PC5 runs from the medium; nothing is written to disk.
   Partitioning, data-loss confirmation and installed-system boot do not exist.

## Environment

5. **Storage is legacy-only, the filesystem is a small writable subset, and
   there is no installer.** The kernel drives ATA PIO and ATAPI on the two legacy
   channels. It reads ISO9660 from the boot medium, reads and writes 512-byte
   sectors on an explicitly indexed ATA disk, reads MBR and GPT partition tables,
   and mounts a FAT32 partition to list, read, create, replace and create
   directories in. Since M1i the write path covers:

   * `put PATH text` — create a new 8.3 file, allocating clusters, updating every
     FAT copy, updating FSInfo, flushing, then reading the file back;
   * `write PATH text` — replace an existing file's content in place, built in
     new clusters and switched with one directory-entry write, the old chain freed
     afterwards;
   * `mkdir PATH` — create one directory with valid `.` and `..`, growing a full
     parent by one cluster.

   Paths are 8.3 components separated by `/`, at most 8 components and 63
   characters, matched case-insensitively. There is **no** delete, rename,
   truncate-in-place, attribute change, move, long file name, link, journal or
   repair, no installer and no bootloader installation. Only 512-byte sectors are
   supported. Writes never happen on an ordinary boot: the only write paths are
   the explicit `wtest` console command (a reserved 8-sector scratch window) and
   the explicit `put`, `write` and `mkdir` commands. All of this has been verified
   only through an in-memory sector array and the emulated test disk — **no real
   disk has ever been written or read.**

   Two consequences are worth stating because they are properties, not bugs:
   an overwrite needs free space for **both** versions at once (a volume with room
   for one chain but not two is refused with no writes), and a nearly full volume
   costs O(clusters) sector reads to scan. Nothing here is crash-consistent
   against a real power cut: the write ordering guarantees that an interruption
   leaves either the old content intact or the new content complete, plus at worst
   a leaked chain that the operation reports — **not** that the volume is always
   mountable without a filesystem check. See ADR-015 and §15.9 of
   `docs/TEST_RESULTS.md`.
6. **No USB stack.** No USB host controller driver exists, therefore no
   DualSense, no USB storage, no USB Ethernet.
7. **No network stack.** PCI network adapters are enumerated, but no driver,
   DHCP or DNS exists.
8. **No audio.** The audio class is counted in the inventory; there is no
   driver.
9. **Input is PS/2 keyboard only.** USB HID and Bluetooth are not implemented;
   controller support is chain stage 3 and is reported as PARTIAL for exactly
   this reason.
10. **No real-time clock and no interrupt timers.** Timing is PIT-calibrated
    TSC deltas; the clock shown is uptime, not wall time.
11. **No power-off.** ACPI PM1 control is not driven. The Power page says so
    instead of showing a button that does nothing.
12. **No ACPI AML interpreter.** Only table signatures are read.
13. **Framebuffer console only at 16/24/32 bpp VBE modes ≤ 1600×1200.** If
    firmware offers no such mode, the environment falls back to 80×25 VGA text.
14. **32-bit kernel, no paging.** Physical memory above 4 GiB is reported but
    not used; long mode is not entered.

## PS5 compatibility

15. **Genuine PS5 system software does not run and no path to run it is
    implemented.** Stages 5–13 of the compatibility chain are BLOCKED or
    NOT-STARTED by measurement.
16. **No PS5 package parsing.** The `FIH`/`CNT` structures are identified by
    signature in the host tool only; nothing in the kernel understands them.
17. **No PS5 executable execution.** No SELF/ELF loader, no PRX shim, no
    graphics translation for titles.
18. **No PSN or any Sony service access**, authorised or otherwise.
19. **No game compatibility claims.** `docs/PS5_GAME_TEST_RESULTS.md` is empty
    because no title has been executed.

## Engineering process

20. **Physical hardware is untested.** Nothing in this repository has run on the
    reference Dell Inspiron 15 7577; the sandbox has no hardware access and no
    KVM. Emulated boot is the highest evidence level reached.
21. **No UEFI boot has run under real firmware.** The loader's own machine code
    has been executed against the stub in `tests/uefi_stub_firmware.py` (42
    checks), and the PE32+ image, FAT ESP and ISO layout are checked structurally
    (`tools/eficheck.py`, `tools/mkefiimg.py`, `tools/verify_iso.py`; 41 UEFI host
    tests). What has never happened is an OVMF or hardware boot; §1 above states
    the same thing in more detail.
