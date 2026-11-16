# Research log

Dated entries, newest first. Facts are marked as reported or verified.

## 2026-11-15 — Re-audit, partition discovery, and the genuine-boot assessment

**Repository (verified here).** 90 tracked files; kernel+boot 5,986 lines, tests
4,157, host tools 3,061, docs 4,616. Gate results at this commit: `make verify`
53/53, `make check` 135 tests, `make unit` 99 checks, `make boot-test` 53/53,
`make stubtest` 42/42, `make roadmap` 14 milestones. The full inventory, with
the seven categories (verified / under-tested / experimental / simulated /
planned / hardware-blocked / infeasible), is §7 of
`docs/architecture/REPOSITORY_AUDIT_2026-11-15.md`.

**Partition discovery (implemented and verified here).** MBR and GPT tables are
now parsed read-only in the kernel, with header and entry CRC-32 checks, refusal
of overlapping or out-of-range partitions, and a damaged table reported as
`status=crc`. Verified on an emulated IDE disk whose bytes the test builds
(`tests/disk_images.py`) and in 99 host unit checks. It is read-only: no
filesystem, no installer.

**Genuine PS5 boot (assessment, not attempted).** The dependency chain is
recorded in `docs/playstation/BOOT_FEASIBILITY.md`: container understanding is
research; obtaining authorised inner components, a security-processor trust
anchor, console platform initialisation, GPU/audio/storage contracts and system
services are blocked. Three routes could change that — Sony authorisation, a
full-SoC emulator including the security processor, or reconstruction from
leaked material — and the third is excluded by project policy. No public
evidence of the first two exists. This is a finding, not a schedule slip.

**Harness quirk (verified here).** v86's default boot order produced no serial
output when `hda` carried a valid boot signature (a GPT protective MBR is one).
The test runner now sets the order it was verified with (0x213); four cases —
no disk, zero-filled disk, valid GPT, CRC-damaged GPT — all reach the CD. This
is a property of the test harness, not of PC5.

**Hygiene finding (verified here).** A 19 MB third-party source archive was
committed at the repository root and was never built by any target. It has since
been removed, with every reference to it, by an owner decision (ADR-025); a guard
test keeps it and its LFS pointer out of the tree.

## 2026-11-12 — Project start

**Environment verification (verified here).**
Debian 12 x86-64, gcc 12.2.0, binutils 2.40 (emulations `elf_x86_64`,
`elf_i386`, `i386pep`), node 22.22, python 3.11. Absent: `xorriso`,
`genisoimage`, `mtools`, `mkfs.vfat`, `nasm`, `cmake`, `ninja`, `qemu`,
`/dev/kvm`, 32-bit libc headers. Network: `github.com`, `codeload.github.com`,
`api.github.com`, npm and PyPI reachable; `deb.debian.org`, `cdn.kernel.org`,
`archive.ubuntu.com`, `raw.githubusercontent.com` and GitHub release-asset hosts
are not.

Consequences: ISO writer, bootloader and kernel must be written in-tree; boot
testing must use an emulator from npm (`v86-system`); UEFI cannot be executed.

**Third-party PS5 static relinker (reported by its own sources; metrics verified
here).** A static relinker, GPL-2.0-only, "nothing is emulated" — it converts PS5
ELF executables to native host programs and supplies independent PRX library
implementations. Measured in the archive supplied at project start: 478,791 C/C++
lines, 411 PRX libraries, 5,167 NID export declarations, 1,686 files with bare
`return 0;` stubs, 857 TODO/UNIMPLEMENTED markers. It could not be built here (no
CMake, submodules absent). The archive has since been removed (ADR-025) and its
upstream URL is deliberately not recorded: PC5 does not present itself as derived
from it.

**PS5 hardware (public documentation).** Custom AMD Zen 2, 8 cores/16 threads up
to 3.5 GHz; custom RDNA 2 GPU, 36 CUs, up to 2.23 GHz (~10.28 TFLOPS); 16 GB
GDDR6 at 448 GB/s; custom 825 GB NVMe SSD with ~5.5 GB/s raw throughput;
Wi-Fi 6, Bluetooth 5.1, Gigabit Ethernet; Tempest 3D audio.
Sources: <https://www.psdevwiki.com/ps5/>, Sony's published specifications,
secondary summaries.

**PS5 package format (reported).** Finalised images use the magic `\x7fFIH`
(little endian) with the PFS image at 0x10000; metadata container magic
`\x7fCNT` (big endian, shared with PS4); PFS is AES-XTS over 0x1000-byte
sectors with a Merkle tree for integrity.
Sources: <https://github.com/SvenGDK/LibProsperoPKG/blob/main/docs/ps5-pkg-format.md>,
<https://www.psdevwiki.com/ps5/Package_Files>.

**PS5 firmware update container (reported).** Sony documents the USB layout
`PS5/UPDATE/PS5UPDATE.PUP` and the ~1.1 GB update / ~1.2 GB+ reinstall sizes.
Community analysis reports an outer container beginning with `SLB2`, containing
`PS5UPDATE1.PUP` / `PS5UPDATE2.PUP`, with a plaintext header and signed or
encrypted entries whose layout is version specific; one thread explicitly notes
that a modified PUP must still pass authentication.
Sources: <https://www.playstation.com/en-us/support/hardware/ps5/system-software/>,
<https://gbatemp.net/threads/built-a-pc-based-pup-decrypt-tool-one-key-away-from-decrypting-any-firmware-forever-need-someone-on-13-60-to-run-an-extraction-payload.685380/>,
<https://github.com/ZigZagBoi1/PS5-14.10-Research/issues/73>.

**Conclusion recorded.** No public, verified path exists from generic x86
hardware to genuine PS5 system-software execution; the console security
processor roots that trust. PC5 therefore separates a firmware path (BLOCKED,
documented) from an execution path for PS5 binaries (researchable, legitimate
inputs) and never reports one as the other.

## Open questions

1. Exact `SLB2` entry-table layout per firmware version (needs a supplied image;
   currently unverified).
2. Which PS5 system services are reachable from user space without console
   hardware (unknown; no authorised interface documented publicly).
3. Whether the relinking approach scales to retail titles: depends on PRX
   coverage and GPU translation, both unmeasured by PC5.
4. DualSense input report layout for adaptive triggers and haptics beyond the
   documented HID descriptors (HID reports are public; effect semantics need
   hardware testing).
