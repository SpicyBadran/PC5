# Technical decisions

Each record states the problem, the alternatives that were considered, the
evidence, the choice, and what would reverse it.

## ADR-001 — Write the ISO9660/El Torito writer in-tree

* **Problem**: the deliverable must be a reproducible `build/pc5.iso`.
* **Alternatives**: `xorriso`, `genisoimage`, `mkisofs` (all absent and not
  installable here); downloading a prebuilt boot toolchain (release assets are
  unreachable from this environment).
* **Evidence**: `which xorriso genisoimage mkisofs` → not found; GitHub release
  downloads resolve to a host that is blocked, while `codeload` (source
  archives) works.
* **Decision**: implement ISO9660 + El Torito in `tools/mkiso.py`, and verify it
  with a *separate* parser (`tools/verify_iso.py`) so a bug cannot validate
  itself.
* **Reversal**: if a build host has `xorriso`, the writer can be swapped for it
  behind the same `make iso` target; the boot descriptor patching would move to
  a post-processing step.

## ADR-002 — BIOS path first, UEFI second

* **Problem**: modern machines boot UEFI; the reference laptop can do both.
* **Alternatives**: UEFI-first (PE32+ application + FAT ESP inside the ISO) or
  BIOS-first (El Torito no-emulation image).
* **Evidence**: the only executable test vehicle available is v86 with SeaBIOS,
  which implements BIOS/El Torito and *not* UEFI. Writing a UEFI application
  here would produce code that could not be executed once.
* **Decision**: build and prove the BIOS path now (it is fully tested), keep
  `tools/mkiso.py` capable of emitting a second El Torito entry for the UEFI
  platform (already implemented in the writer), and treat the UEFI loader as
  the next milestone with an explicit test requirement.
* **Consequence**: a UEFI-only machine cannot boot PC5 yet. This is stated in
  `docs/KNOWN_LIMITATIONS.md` and on the project README.

## ADR-003 — A dedicated 32-bit kernel instead of a Linux distribution

* **Problem**: the boot environment needs a console, hardware inventory and a
  diagnostics shell.
* **Alternatives**: (a) boot a Linux kernel + initramfs, (b) boot a
  microkernel/POSIX layer, (c) write a small freestanding kernel.
* **Evidence**: no Linux kernel source or binary was obtainable in this
  environment (no apt, no kernel.org, no release assets); no 32-bit libc headers
  either. A freestanding kernel needs only `gcc` and `binutils`.
* **Decision**: (c) for the bring-up environment. It is not a substitute for the
  final runtime: a Linux foundation remains the plan for the *compatibility*
  stage because it brings drivers (NVMe, USB, Wi-Fi, GPU) that cannot reasonably
  be rewritten. The bring-up kernel and the future Linux runtime share the boot
  contract and the chain reporting.
* **Reversal**: as soon as a build host can install `linux-image-*` and
  `debootstrap`, PC5 gains a Linux runtime path; the loader already reads
  multi-megabyte images (limit 572 KiB today, raised by switching the loader to
  unreal-mode high loads).

## ADR-004 — Separate the firmware-boot path from the execution path

* **Problem**: there are two very different ways to run PS5 software: booting
  Sony's system software, or executing PS5 binaries natively after relinking
  (the AnyPS5 approach).
* **Alternatives**: pretend they are the same problem; pick one and ignore the
  other.
* **Evidence**: AnyPS5's own architecture document shows it is a static
  relinker that never runs firmware; public research shows PS5 system software
  is signed and bound to the SoC. No public path exists from generic x86
  hardware to genuine PS5 system-software execution.
* **Decision**: model both explicitly. The compatibility chain (13 stages) is
  the firmware path; `docs/GAMING_COMPATIBILITY.md` requires PS5 titles to be
  executed through a documented mechanism and tested, never inferred from a
  library listing. Neither path may be reported as the other.
* **Consequence**: the project can make real progress on the execution path
  (relinking, PRX shims) without ever claiming the firmware stage.

## ADR-005 — Report the chain from measurements, not from a status file

* **Problem**: it is easy to write "PS5 compatible" in a README and impossible
  to disprove from the outside.
* **Decision**: the chain is computed at boot from the machine in front of you
  (`chain_evaluate()`), each stage carries an evidence string that names the
  measurement, and the UI refuses to colour a stage green without it. The
  serial log prints the same lines (`PC5-LOG chain_blocked_at=…`) so a log file
  is enough to audit a boot.
* **Test**: `tests/kernel_unit.c` asserts that the firmware-execution stages are
  never `OK` even when a firmware *manifest* is present.

## ADR-006 — Emulated boot tests are necessary but not sufficient

* **Problem**: no physical hardware is attached to this build environment.
* **Decision**: v86 boot tests prove "the ISO boots on an x86 PC with SeaBIOS".
  They do not prove driver behaviour on real chipsets, real USB controllers,
  real UEFI firmware or real timing. `docs/TESTING_STRATEGY.md` therefore keeps
  sixteen separate evidence levels and `docs/TEST_RESULTS.md` records which
  ones are currently proven.

## ADR-007 — No Sony firmware, keys or firmware-derived binaries in the repository

* **Problem**: firmware is a central research input, but redistribution is not
  permitted and the repository is public.
* **Decision**: the repository carries a *workflow* (`tools/firmware.py`) and a
  manifest, never the image. `.gitignore` excludes `firmware/`. The kernel only
  ever receives a short text summary of the analysis.
* **Test**: `tests/test_tools.py` checks the manifest round-trip and that only a
  summary string is emitted into C.

## ADR-008 — Keep the boot image under 600 KiB (load window)

* **Problem**: a real-mode loader can only read into conventional memory
  without unreal-mode tricks.
* **Decision**: kernel + assets must stay under `0x8F000 - 0x10000` (572 KiB);
  the Makefile fails the build if it grows past that, and the loader fails with
  a diagnostic code if the image would cross the limit. Measured 2026-11-15 after the
  storage-write work: 41,724 bytes (BUILD_ID=m1d) — well inside the window.
* **Reversal**: implement unreal-mode high loading (already scoped in
  `docs/ROADMAP.md`) when a Linux payload needs it.

## ADR-009 — Linux is the PC5 Native foundation; the freestanding kernel becomes recovery

* **Problem**: PC5 Native must drive mainstream AMD, Intel and NVIDIA hardware.
  The existing 32-bit freestanding kernel has no NVMe, AHCI, xHCI, audio, network
  or GPU driver, no user mode and no scheduler. Writing those is years of work
  that Linux already did, with more hardware coverage than this project will ever
  test.
* **Alternatives**: (a) extend the freestanding kernel into the whole operating
  system; (b) ship Linux only and retire the freestanding kernel; (c) keep both —
  Linux as the system, the freestanding kernel as the recovery and diagnostic
  environment; (d) a microkernel or BSD base.
* **Evidence**: weighted comparison in `docs/architecture/LINUX_VS_FREESTANDING.md`
  (hybrid 435/500, Linux-only 395, microkernel 285, freestanding-only 245). The
  weights are the argument: driver coverage 25, recovery 15, maintainability 15.
  Measured environment facts: Linux source is reachable (`codeload` HTTP 200 for
  `torvalds/linux` v6.12) but `flex`, `bison`, `bc`, libssl/zlib headers and any
  QEMU/KVM vehicle are absent, and `apt` is unreachable.
* **Decision**: (c). Linux provides hardware and system services; PC5 owns the
  boot experience, system image, sessions, shell and update model; the
  freestanding kernel is retained as the recovery entry, bootable from the same
  medium through the same `PC5-BOOT` v1 contract. This **extends** ADR-003 (which
  chose the freestanding kernel under environment constraints) rather than
  reversing it: ADR-003's exit condition is now the plan.
* **Consequence**: no Linux-based capability may be described as built until a
  kernel boots on a reference machine and produces `PC5-LOG` lines. The
  freestanding kernel must keep passing `make boot-test` in every release —
  invariant I4 — because a recovery environment built from the thing it recovers
  is not a recovery environment.
* **Reversal**: if Linux cannot be brought up reproducibly on the reference
  hardware, fall back to a freestanding-first plan with a narrower qualified
  device set (recorded in the risk register as A1/B4).

## ADR-010 — Dual-track governance with a machine-enforced Track A/Track B gate

* **Problem**: PC5 has two objectives with very different feasibility: an
  installable PlayStation-like operating system (achievable), and genuine PS5
  system software on PC hardware (no public path exists). Human optimism will
  eventually let the second consume the first unless the boundary is structural.
* **Alternatives**: one backlog with priorities; two repositories; two build
  trees; one repository with a dependency graph and a build/test boundary.
* **Evidence**: every previous overclaim in this project traced to prose, not
  code — nine documentation statements contradicted measurements on 2026-11-15
  (`docs/architecture/REPOSITORY_AUDIT_2026-11-15.md` §6). Prose rules drift;
  tested rules do not. A separate repository would make the boundary invisible
  and duplicate shared platform work.
* **Decision**: one repository, two tracks, and the separation expressed as data:
  `docs/architecture/roadmap.json` carries every milestone's track and
  dependencies, and `tools/roadmap.py` **fails** if a Track A milestone depends,
  directly or transitively, on a Track B milestone. `tests/test_architecture.py`
  runs that check in `make check`, including negative fixtures that prove the
  gate can fail. Additionally, a Track A build must succeed with the Track B
  subtree removed once that subtree exists.
* **Consequence**: Track B may consume Track A services, and may motivate future
  Track A features — but those features are proposed as Track A milestones, never
  smuggled in as Track B exceptions. The gate is about the release path, not
  about cooperation.
* **Reversal**: if the tracks genuinely merge (a single, proven path to PS5
  execution on PC), the gate is removed by an ADR that says so explicitly and
  deletes the corresponding tests. It is not disabled quietly.

## ADR-011 — Hardware support is a qualification matrix, not a claim

* **Problem**: the objective is "all mainstream PC hardware", which is not a
  testable statement and cannot be verified by anyone. Firmware revisions,
  device revisions and hybrid-graphics routing make model-level claims false for
  some owners of the very machine that was tested.
* **Alternatives**: maintain a brand-level compatibility list ("AMD supported");
  publish a "works on most PCs" statement; test and record per machine and per
  device class.
* **Evidence**: the project cannot test what it does not own; the environment has
  no hardware and no KVM at all. Historical examples in `docs/HARDWARE_COMPATIBILITY.md`
  already anticipated this (Optimus, GOP-only firmware, USB-as-HDD), all of which
  make brand-level claims unsafe.
* **Decision**: three reference configurations (AMD+Radeon P0, Intel iGPU P0,
  NVIDIA P1) plus an interim owner machine; every result is a committed record
  under `docs/evidence/<milestone>/<machine-id>/` with the build hash and a
  measured value. Levels L1–L3 are defined by pass conditions a log can prove;
  L4–L7 remain unreachable Track B levels. The allowed user-facing phrases are
  enumerated: qualified, boots, known issue, not supported.
* **Consequence**: release notes can only describe a machine that has a record
  from that release build (M8 acceptance). A device class that needs
  non-redistributable firmware is documented per device, never promised as a
  class.
* **Reversal**: not expected. If a truly device-independent certification
  mechanism appears (for example, an automated hardware lab), the matrix becomes
  its output rather than its replacement.

## ADR-012 — Architecture claims are machine-checked artefacts

* **Problem**: the architecture makes promises that documentation cannot keep:
  the track gate, milestone completeness, evidence that exists, links that
  resolve. The first audit found nine statements that had silently become false.
* **Alternatives**: trust reviewers; a checklist in a pull-request template; put
  the claims in data and check them in the test suite.
* **Evidence**: the drift in the audit was not carelessness — it was the normal
  consequence of a document describing a moving system. Everything in this
  repository that has stayed true (`_Static_assert` boot offsets, the independent
  ISO verifier, the stub-firmware tests) stayed true because a machine checked
  it.
* **Decision**: milestone states, dependencies, deliverables, acceptance criteria
  and evidence paths live in `docs/architecture/roadmap.json`. `tools/roadmap.py`
  validates the graph, enforces the track gate and renders the graph into
  `M0_M8_DEPENDENCY_GRAPH.md` between markers; `make check` fails on a stale
  block, a missing evidence path or a broken relative link in these documents.
  `make roadmap` exists so an operator can run the check alone.
* **Consequence**: changing a milestone requires editing data and syncing the
  document, which is slightly more work than editing prose and materially harder
  to get wrong. Documents that remain prose are still subject to the audit
  procedure: a measurement that contradicts a document corrects the document in
  the same session (invariant I5).
* **Reversal**: if the graph outgrows JSON (for example, per-task tracking), move
  to a richer format but keep the validator, the generated document and the
  failing tests. The check is the decision; the format is not.

## ADR-013 — Two transfer granularities: 512-byte disk sectors and 2048-byte optical blocks

* **Problem**: the storage task calls for a 512-byte logical-block write
  interface, while `ata_read_blocks()` has always read 2048-byte blocks because
  that is ISO9660's logical block size and ATAPI `READ(10)` returns 2048 bytes
  per block. Reinterpreting one as the other silently is how a driver writes a
  disk at the wrong offset.
* **Alternatives**: (a) change the ISO9660 path to 512-byte units and rewrite
  every caller; (b) keep only the 2048-byte interface and express disk writes in
  four-sector units; (c) keep both and make each name state its unit.
* **Evidence**: ATA `IDENTIFY DEVICE` reports capacity in 512-byte sectors
  (words 60–61); ATA `READ/WRITE SECTORS` transfers 512 bytes per sector; ATAPI
  packet devices use 2048-byte logical blocks and are configured for them in
  `ata_init()`; ECMA-119 (ISO9660) defines a 2048-byte logical block. The two
  numbers describe different hardware, not different conventions that could be
  unified without lying to one of them. The existing ISO path passes 25 boot
  checks and must not be disturbed by a units rewrite.
* **Decision**: (c). `ata_read_sectors()` and `ata_write_sectors()` take an
  explicit device index, count 512-byte sectors and are the only write path.
  `ata_read_blocks()` keeps 2048-byte blocks, operates on the *selected* boot
  device and is the only interface ISO9660 uses. No function name hides its
  unit; no write call exists without a device index.
* **Consequence**: a future filesystem, partition parser or installer uses the
  sector interface and can be validated on the host (`kernel/block.c` has no
  port I/O); optical reads stay untouched. AHCI/NVMe backends implement the
  same sector contract and nothing above them changes.
* **Reversal**: if a filesystem ever needs 4096-byte units, it composes them
  from sectors at the filesystem layer. The ATA contract itself is fixed by the
  hardware and will not change.

## ADR-014 — One boot image, two firmware paths: the hybrid MBR covers the whole image

* **Problem**: El Torito boots a CD/DVD and any medium the firmware presents as
  an ATAPI device, and nothing else. Firmware that treats a USB stick as a hard
  disk reads sector 0, looks for `55 AA` and jumps to `0000:7C00`; with the
  system area zero-filled it finds nothing and the stick is unbootable. The
  support case this creates ("the ISO works in a VM, the stick does nothing") is
  exactly the kind nobody can debug remotely. Two constraints bound the fix: the
  El Torito entry, the ISO9660 layout and the 4096-byte stage-1 loader must not
  change, and the builds must stay byte-identical.
* **Alternatives**: (a) ship a second, separate "USB image" — a second artifact,
  a second checksum, and two things that can diverge; (b) write a real
  filesystem and a real partition table (a full isohybrid with a FAT partition
  at a fixed offset, as `isohybrid`/`xorriso` do) — that duplicates the ESP,
  breaks the 1,593,344-byte layout and buys nothing, since the loader reads the
  ISO9660 volume, not a FAT filesystem; (c) cover the whole image with a single
  partition entry and let the stage-1 loader boot exactly as it does from CD.
* **Evidence**: ISO9660 (ECMA-119 §6.2.2) defines the first 32 KiB — 16 logical
  blocks — as *system area*: bytes the filesystem does not interpret and the
  producer may use. The first 512 bytes of that area are a hard-disk MBR as far
  as a BIOS is concerned, and no ISO9660 reader ever looks at them. The loader
  already probes the transfer unit (`read_pvd` tries 2048-byte units at LBA 16
  then 512-byte units at LBA 64) and already converts LBAs by `<<2` when the
  medium turns out to be a 512-byte-unit disk, so it needs no change at all. The
  image is 778 ISO blocks = 3,112 512-byte sectors, well inside the 32-bit LBA
  field, and 4-sector aligned. Measured after the change: the same image booted
  disk-only in the emulator reached `PC5-BOOT-OK` with `disk unit bytes 0200`
  (512-byte units) while the CD path reported `0800`, and zeroing sector 0 of
  that same image produced no output at all.
* **Decision**: (c), with the table derived from the layout, never from a
  template. `boot/mbr.S` is the first 440 bytes: it copies a relay to `0000:0500`
  (out of the way of the loader it is about to load), reads the boot image with
  INT 13h extended read into `0000:7C00`, and jumps there with `DL` preserved —
  the same hand-off El Torito performs, at the same address. `tools/mkiso.py`
  owns bytes 440..511: a fixed disk signature (`0x50433530`), one entry with the
  boot flag set, type `0x17`, start LBA 0 and length = the whole image in
  512-byte sectors, four CHS fields computed from the real geometry, and `55 AA`.
  It also patches the boot-image LBA and length into the bootstrap from the El
  Torito values, so the two paths cannot disagree. Type `0x17` is the
  "hidden/automount-suppressed" value the syslinux isohybrid uses: the entry is a
  BIOS compatibility shim, not a claim that the image contains a FAT or ext
  filesystem, and an entry that makes Windows or macOS offer to format the stick
  would be worse than no entry at all.
* **Consequence**: one artifact boots both ways; `tools/verify_iso.py` now reads
  sector 0 the way firmware does and rejects a table that disagrees with the
  image (wrong type or flag, short or over-long extent, stale CHS, a second or
  overlapping entry, patch words that no longer name the El Torito image);
  `tests/test_hybrid.py` corrupts each of those fields and requires the
  verifier to say so; the boot test boots the ISO with *no CD attached* and
  fails if it does not reach the kernel. The kernel's own partition scanner will
  report the medium as an MBR disk with one type-`0x17` partition when it is
  attached as an ATA disk, which is the truth about the image. Physical USB
  firmware behaviour is still not tested — see `docs/KNOWN_LIMITATIONS.md`.
* **Reversal**: if a real FAT partition on the stick ever becomes necessary (for
  example to carry files on a Windows-visible volume), the single covering entry
  is replaced by two explicit extents and the builder's `build_hybrid_mbr()` is
  the one place that changes. The bootstrap does not care where the boot image
  lives, because the LBA is patched in.
