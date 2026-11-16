# Known limitations

Everything below is true of the current build. None of it is a placeholder for
work that has secretly been done.

## Boot

1. **The UEFI boot path is verified under OVMF firmware, not on hardware.**
   `boot/uefi/` builds a PE32+ `\EFI\BOOT\BOOTX64.EFI` application and the ISO
   carries the El Torito UEFI section that points at it. Three levels of
   evidence exist and are never conflated: the loader's machine code runs against
   a protocol stub (`make stubtest`, 58 checks); genuine TianoCore OVMF boots the
   ISO cold and warm and the kernel reaches `PC5-BOOT-OK` (`make ovmf-test`,
   40 checks, 1 NOT APPLICABLE, `docs/TEST_RESULTS.md` §16); and **no physical
   UEFI machine has done it** — `M1` is not started. On this path the loader also cannot report the
   ISO volume id, the kernel LBA or the ISO9660 descriptor (the file protocol
   does not expose them), and it hands over no VGA font, so the kernel's
   framebuffer console falls back to text mode.
2. **The UEFI gate has not been re-run under OVMF on every machine that tests
   this tree.** The environment that produced §18 has no QEMU and no OVMF and no
   way to install them, so `make ovmf-test` exits 3 (NOT TESTED) and says so;
   the executed evidence there is the stub firmware (58 checks), the BIOS path
   (119 checks) and the host unit tests (165 checks). `make ovmf-setup` and the
   `ovmf` CI job exist so the gate runs where OVMF is available, and exit code 3
   fails that job by design.
3. **Secure Boot is not supported and is never claimed.** PC5 ships no signing
   key and no shim, so firmware with a policy enabled refuses the image. The
   harness asserts that *refusal*; a boot under an armed policy would be
   evidence that the policy was not in force, not evidence of compatibility.
4. **The UEFI path hands over no ACPI RSDP**, so the kernel reports
   `acpi=absent` there. Passing one means adding a field to
   `struct pc5_boot_info`, a protocol bump under invariant I4, and is
   deliberately not smuggled into a hardening change.
5. **USB-HDD boot is implemented, not proven on hardware.** Since M1h the image
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
6. **No installer.** PC5 runs from the medium; nothing is written to disk.
   Partitioning, data-loss confirmation and installed-system boot do not exist.

## Environment

7. **Storage is legacy-only, the filesystem is a small writable subset, and
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
8. **No USB stack.** No USB host controller driver exists, therefore no
   DualSense, no USB storage, no USB Ethernet.
9. **No network stack.** PCI network adapters are enumerated, but no driver,
   DHCP or DNS exists.
10. **No audio.** The audio class is counted in the inventory; there is no
   driver.
11. **Input is PS/2 keyboard only.** USB HID and Bluetooth are not implemented;
   controller support is chain stage 3 and is reported as PARTIAL for exactly
   this reason.
12. **No real-time clock and no interrupt timers.** Timing is PIT-calibrated
    TSC deltas; the clock shown is uptime, not wall time.
13. **No power-off.** ACPI PM1 control is not driven. The Power page says so
    instead of showing a button that does nothing.
14. **No ACPI AML interpreter.** Only table signatures are read.
15. **Framebuffer console only at 16/24/32 bpp VBE modes ≤ 1600×1200.** If
    firmware offers no such mode, the environment falls back to 80×25 VGA text.
16. **32-bit kernel, no paging.** Physical memory above 4 GiB is reported but
    not used; long mode is not entered.

## UEFI boot path

Verified against genuine TianoCore OVMF on 2026-11-15 (`docs/TEST_RESULTS.md`
§16). What that does **not** cover:

* **Physical hardware.** No PC and no PS5 has booted this ISO through UEFI. OVMF
  is firmware, but it is firmware in an emulator; vendor firmware (AMI, Insyde,
  Phoenix, Apple) is untested and its quirks are unknown.
* **Secure Boot.** Never enabled, never tested. The loader is not signed and the
  firmware used here is the "pure EFI" OVMF build without a shim.
* **Disk-style UEFI boot.** The ISO carries its ESP as an El Torito image inside
  ISO9660, so there is no ESP *partition* for firmware to mount as a disk. That
  path is reported NOT APPLICABLE by the test harness, never passed. Adding it
  means a partition table the MBR does not have today.
* **ACPI.** The UEFI loader hands over no RSDP, so the kernel reports
  `acpi=absent` and finds no ACPI tables on this path. The BIOS path parses ACPI
  from the legacy areas; making the UEFI path do the same is future work.
* **No block I/O handover.** The file protocol hides the LBA of the file it read,
  so the kernel cannot mount its own medium on the UEFI path
  (`storage_mounted=no`, chain blocked at stage 4). This is reported, not hidden.
* **Framebuffer only via GOP.** A machine that provides no GOP gets the text
  fallback; that path is implemented and unit-tested but has not run under real
  firmware (OVMF always offers GOP).
* **Timing.** The OVMF runs used TCG (no KVM in the build environment). Nothing
  about boot time on real hardware may be inferred from them.

## PS5 compatibility

17. **Genuine PS5 system software does not run and no lawful path to run it is
    known.** Stages 5–13 of the compatibility chain are BLOCKED or NOT-STARTED by
    measurement, milestones `M17`–`M21` are `blocked` in the graph, and the trust
    analysis (`M17`) is the milestone that decides whether level 5 is reachable
    at all (ADR-019).
15a. **The PS5 package in the repository is a pointer, not the package.** The
    working-tree `PS5UPDATE.PUP` is a 135-byte Git LFS pointer; the 1.33 GiB
    payload is not in a checkout, was never fetched here and was never analysed.
    PC5 records its hash and size only (ADR-022,
    `docs/PS5_SYSTEM_SOFTWARE_TARGET.md`).
15b. **Nothing in this repository is a PS5 platform model.** `M10` is a contract
    document with UNKNOWN rows; `M11`–`M16` do not exist.
18. **No PS5 package parsing.** The `FIH`/`CNT` structures are identified by
    signature in the host tool only; nothing in the kernel understands them.
19. **No PS5 executable execution.** No SELF/ELF loader, no PRX shim, no
    graphics translation for titles.
20. **No PSN or any Sony service access**, authorised or otherwise.
21. **No game compatibility claims.** `docs/PS5_GAME_TEST_RESULTS.md` is empty
    because no title has been executed.

22a. **The 64-bit path has never run on firmware or hardware.** `boot/uefi64/`
    and `kernel64/` are exercised by unit tests, a kernel-contract harness and a
    long-mode stub firmware (`make kernel64`, `make uefi64`, `make stubtest64`).
    No OVMF run (no TianoCore image, no `qemu-system-x86_64` in this
    environment) and no physical machine has executed them. The success marker
    `PC5-64BIT-LONG-MODE-OK` is therefore evidence of the code path, not of
    firmware compliance.
22b. **The 64-bit kernel is minimal on purpose.** It has no interrupt handling,
    no APIC programming, no timer, no SMP bring-up, no scheduler, no syscalls and
    no process separation. Its identity map stops at 4 GiB, it does not use NX
    (CPUID reports it; EFER.NXE is not set), there are no guard pages, and there
    is no physical-memory allocator. Nothing in it is a protection mechanism yet.
22c. **The 64-bit path is not part of the release image.** It is built by its own
    targets and carried in a separate ESP image; `make iso` is unchanged and the
    release artifact is still 32-bit. This is deliberate isolation, not an
    oversight, and `tests/test_kernel64.py` fails if that changes by accident.
22d. **Stub firmware is emulation, and Unicorn is not a CPU.** The stub harness
    emits a memory map, answers the file protocol and enforces the
    `ExitBootServices` map key, but it is a model written in Python. Its paging
    model walks PC5's tables, which catches a wrong entry; it cannot prove cache
    attributes, memory types or real firmware behaviour.

## Engineering process

22. **Physical hardware is untested.** Nothing in this repository has run on the
    reference Dell Inspiron 15 7577; the sandbox has no hardware access and no
    KVM. Emulated boot is the highest evidence level reached.
23. **No UEFI boot has run under real firmware.** The loader's own machine code
    has been executed against the stub in `tests/uefi_stub_firmware.py` (42
    checks), and the PE32+ image, FAT ESP and ISO layout are checked structurally
    (`tools/eficheck.py`, `tools/mkefiimg.py`, `tools/verify_iso.py`; 41 UEFI host
    tests). What has never happened is an OVMF or hardware boot; §1 above states
    the same thing in more detail.
