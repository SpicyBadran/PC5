# Project audit — what existed before any work started

| | |
|---|---|
| Status | **Historical record** — the state of the repository before any PC5 code existed. |
| Superseded for current state by | `architecture/REPOSITORY_AUDIT_2026-11-15.md` (tree audit), `architecture/ARCHITECTURE_DISCREPANCY_REPORT.md` (findings), `architecture/PC5_ARCHITECTURE_SPEC_v2.md` (architecture) |

Date of audit: 2026-11-12. Branch: `arena/2290edbd-pc5`, base commit `9576ad7`.
Nothing in this file describes the project as it is now; it describes the upload
it started from.

## 1. The repository as received

`git log --oneline` at the base commit contained exactly one commit
("Add files via upload") and one file: a 19,749,977-byte third-party source
archive (a ZIP of a PS5 static relinker). It has since been removed from the
tree (ADR-025).

There was no build system, no source tree, no documentation, no ISO, no tests,
no firmware image and no CI. Anything described elsewhere in this repository as
working today was built during this work; nothing was inherited except that
source archive, which was an *input* to the audit, not a PC5 component.

Checks performed:

| Check | Result |
|---|---|
| `git log` | 1 commit, no history to build on |
| tracked files | 1 (zip) |
| build system | none |
| tests | none |
| ISO/boot artifacts | none |
| Sony firmware in repository | **absent** (only a third-party source archive was supplied, since removed) |
| third-party binaries in repository | none |

Consequence: the firmware workstream that Part 3 of the project directive
describes cannot start from a supplied image. The firmware pathway is therefore
implemented as a *user-supplied* workflow (`tools/firmware.py`) and the boot
environment reports "no firmware image supplied" instead of pretending.
See `docs/FIRMWARE_ANALYSIS.md`.

## 2. Development environment actually available

The work was carried out on a sandboxed Debian 12 x86-64 host. What that meant
for the design:

| Resource | State | Effect on the project |
|---|---|---|
| `gcc` 12.2.0, `binutils` 2.40 (x86-64 + i386 + PE emulations), `make`, `python3` 3, `node` 22 | available | native toolchain for the kernel, loader and host tools |
| 32-bit libc headers | **absent** | kernel is fully freestanding; host unit tests build 64-bit with `PC5_HOST_TEST` |
| `xorriso`, `genisoimage`, `mtools`, `mkfs.vfat` | **absent** | `tools/mkiso.py` implements ISO9660 + El Torito directly |
| `qemu-system-x86_64`, OVMF, KVM | **absent** (`/dev/kvm` missing) | boot tests run on the v86 emulator instead; UEFI boot cannot be executed here |
| `apt` repositories | unreachable (only `github.com`, `codeload.github.com`, `api.github.com`, npm and PyPI are reachable) | no Linux kernel or distro packages could be installed |
| release-asset downloads from GitHub (`objects.githubusercontent.com`) | blocked | no prebuilt bootloader (limine/GRUB) could be fetched; hence the hand-written loader |
| Internet | partial | used only to study public documentation, cited in the research log |

Verified with:

```
$ which qemu-system-x86_64 xorriso mformat mkfs.vfat debootstrap
(none found)
$ ls /dev/kvm
ls: cannot access '/dev/kvm': No such file or directory
```

This is why the first bootable artifact is a BIOS-path image with a
hand-written loader and a hand-written ISO writer, rather than a bootable Linux
distribution. It is a constraint of the environment, not a design preference;
`docs/TECHNICAL_DECISIONS.md` records the decision and the exit condition.

## 3. Third-party static-relinking prior art (assessed, then removed)

At the audit date the repository contained a third-party source archive as its
only file: a static relinker for PS5 game binaries, GPL-2.0-only and not
affiliated with Sony. It was assessed, and it has since been removed from the
tree together with every reference to it (ADR-025). The findings that shaped PC5
are kept here; the project's name is deliberately not reproduced, because PC5 is
its own project and does not present itself as derived from another one.

### What that prior art implements

A **static relinker**, not an emulator and not a firmware loader: a PS5
executable is converted into a native host program and linked against
independently written implementations of the system libraries, which then runs as
an ordinary process. The pipeline is: read a PS5 ELF and its bundled
`sce_module/*` modules; resolve imports by NID (the numeric symbol id PlayStation
binaries use); lower AMD-only instructions for Intel hosts; build a SysV/PE
dynamic section and patch the image, renaming exports to their NIDs; link against
the PRX library reimplementations.

### Measured size and completeness (audit date)

| Metric | Value |
|---|---|
| C/C++ lines | 478,791 |
| PRX library projects | 411 |
| NID export declarations | 5,167 |
| files containing a bare `return 0;` stub | 1,686 |
| `TODO` / `UNIMPLEMENTED` / "not implemented" markers | 857 |
| build system | CMake ≥ 3.20 with a C++ toolchain; git submodules |
| buildable in this environment | **no** — no `cmake`, submodules not vendored |

That project documented silent stubs per library, "unknown signature" exports and
untested paths, which matches the stub counts above, and it published per-title
bring-up threads rather than a compatibility list.

### What it requires

* a *decrypted, unpacked* PS5 executable (containers whose segments look
  encrypted are refused),
* the title's own `sce_module/` modules,
* a host carrying the relinker's built PRX libraries,
* a game the implemented library set already covers.

### Why PC5 did not adopt it

It is the most credible *execution* route that exists for PS5 game binaries, and
it is still not PC5's route: it cannot boot PS5 system software and does not
attempt to, and running a relinked user-space program on a host OS is a different
product from the destination (ADR-004). PC5 therefore vendors nothing, fetches
nothing at build time and depends on nothing — no third-party source archive is
tracked, and `tests/test_project_independence.py` fails the build if one
reappears.

### Limitations that block the PC5 objective

1. No firmware execution path at all: PS5 system software never runs.
2. Requires decrypted binaries — the SYSCON/secure-loader chain is not touched.
3. GPU work is limited to shader recompilation; there is no full GPU command
   translation for PS5 titles.
4. Cannot be built in this environment, so it cannot be tested here either.

## 4. Other projects and sources consulted

* psdevwiki PS5 pages (package/file formats) — <https://www.psdevwiki.com/ps5/>
* LibProsperoPKG package-format notes — <https://github.com/SvenGDK/LibProsperoPKG>
* Sony's own system-software update instructions (PUP naming and USB layout) —
  <https://www.playstation.com/en-us/support/hardware/ps5/system-software/>
* v86 (x86 emulator used for boot tests) — <https://github.com/copy/v86>

## 5. Audit conclusions that shaped the build

1. Nothing existed; the first deliverable had to be a real bootable artifact.
2. No firmware was supplied, and none may be redistributed → the firmware
   workstream is a documented, user-driven analysis path, and the boot
   environment must state the truth on screen.
3. No virtual machine or UEFI firmware was available → build the bootloader and
   ISO writer in-tree; test on the only emulator that is reachable (v86).
4. The static-relinking prior art is an execution-layer approach for game
   binaries, not a boot solution → PC5 keeps a firmware/software-boot path and an
   execution-layer path separate, and reports them separately in the
   compatibility chain.

---

## Addendum, 2026-11-15

The counts and status in this document describe the repository as it was
received. Two later milestones changed them: M1d (verified sector write) and M1e
(read-only MBR/GPT discovery). The current, measured inventory is §7 of
`docs/architecture/REPOSITORY_AUDIT_2026-11-15.md`; the evidence is
`docs/TEST_RESULTS.md` §10 and §11.
