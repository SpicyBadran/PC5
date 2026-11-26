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
  (the static-relinking approach).
* **Alternatives**: pretend they are the same problem; pick one and ignore the
  other.
* **Evidence**: the public static-relinking prior art is a relinker that never
  runs firmware; public research shows PS5 system software is signed and bound to
  the SoC. No public path exists from generic x86
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

**Status: superseded by ADR-020 (2026-11-15).** The engineering reasoning below
(drivers, economics, the build-environment blocker) is still correct and is
carried forward; what is withdrawn is the claim that Linux is *the product*.
Linux is now an optional hardware-enablement substrate and PC5's own platform
remains the product surface.

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

**Status: superseded by ADR-019 and ADR-021 (2026-11-15).** The *safety* concern
was real and is preserved as the release-isolation invariant; the *gate* was
wrong, because it made the mission structurally unable to be a dependency of the
product. History is kept below because the reasoning is worth reading.

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

## ADR-015 — Overwrite builds a second copy and switches one entry; it is not a transaction

* **Problem**: the installer M2 needs cannot be built on a filesystem that can
  only create files. It has to replace a file whose name is already taken (a
  bootloader configuration, a kernel image, a manifest) and it has to create
  directories. Replacing data in place is the most dangerous operation a
  filesystem performs: if the entry is updated before the new bytes are on the
  medium, the file reads back as rubbish; if the old chain is freed before the
  new one is safe, a crash loses both versions; if anything is half-done, the
  volume itself can become unreadable. Three constraints bound the design: the
  kernel is freestanding with a fixed stack and no heap, there is no journal and
  there will not be one in this milestone, and the only durable primitive
  available is "write a 512-byte sector, then issue FLUSH CACHE".
* **Alternatives**: (a) overwrite the existing clusters in place — cheapest in
  space, but every interruption lands *inside* the live file, so the result is a
  file that reads back as a mixture of both versions, and a longer replacement
  cannot fit at all; (b) allocate the new chain, free the old one first, then
  write the new data and update the entry — a failure in between leaves a name
  pointing at freed clusters, which a later allocation can hand to another file
  (silent cross-linked corruption); (c) build the new chain in unreferenced
  clusters, switch the entry with one sector write, and only then free the old
  chain — costs transient space for both versions and can leak; (d) do nothing
  and require a full filesystem check after every failure — there is no fsck in
  this tree, and telling an operator to run a repair tool that does not exist is
  not a design.
* **Evidence**: the failure sweep in §15.5 of `docs/TEST_RESULTS.md` fails each of
  the 26 writes of an overwrite in turn and classifies the resulting image with
  an independent FAT32 reader. In every run the target was either the old bytes
  (failures 0–11, i.e. up to and including the switch) or the complete new bytes
  (failures 12–25), no unrelated file changed, no cluster was ever claimed by two
  chains, and the cluster accounting closed exactly as
  `freed + leaked + freed-in-one-copy-only = 6`. The same sweep on mkdir leaves
  either nothing or one well-formed empty directory nothing points at.
* **Decision**: (c). The order is: plan and validate the entire allocation in
  memory (free count from the FAT, never from FSInfo; the old chain walked and
  refused if it is not coherent) → write the replacement into clusters nothing
  references → link it in **every** FAT copy and re-read every copy → recompute
  FSInfo (over-reporting usage is the safe direction) → flush → **one** 32-byte
  directory-entry write, which is the only instant at which the operation becomes
  visible → flush → read the file back through its own name → free the replaced
  chain, re-verifying every copy → correct FSInfo → flush. A volume that cannot
  hold both chains at once is refused with `full` **before anything is written**:
  space is what the property costs, and refusing is the honest answer. Directory
  creation uses the same discipline with `.`/`..` as the payload, and a full
  parent is grown by reserving the growth cluster in the *same* allocation as the
  content — a second scan of the FAT would see the reserved-but-unlinked clusters
  as free and hand one out twice.
* **Consequence**: the failure model is now stated, testable and tested, and it is
  deliberately narrower than "atomic". Before the switch the volume holds the old
  file plus a leaked chain; after it, the new file plus (until freed) the old
  chain. There is no state in which a name refers to unwritten data, no state in
  which two objects share a cluster, and no false "ok": every failure path returns
  a status and a stage, and the report never claims a verification it did not
  perform (`mirrors` is 1 only after the copies were re-read, `verified` only
  after the read-back). What is **not** claimed is anything about a power cut: a
  failing `write()` in the host driver returns before the sector reaches the
  medium, so the tests prove ordering, not durability. A real power loss between
  the switch and the free leaves a leaked chain that needs a filesystem check.
  Delete, rename and truncate-in-place are still absent — each needs its own
  ordering argument, and M1i did not need them.
* **Reversal**: if a journal ever lands, the switch and the free become one logged
  transaction and the transient-space requirement disappears; the code already
  isolates the three moments (link, switch, reclaim) as separate stages, which is
  the structure a log would replay.

## ADR-016 — The ESP carries a copy of the kernel, because firmware cannot read ISO9660

* **Problem**: the UEFI path was built on an assumption that only a stub could
  hold: that `\KERNEL.BIN` could be opened on the volume firmware boots from.
  Real OVMF disproved it on the first run: `EFI_SIMPLE_FILE_SYSTEM_PROTOCOL` only
  ever exposed the RAM-disk copy of the ESP that firmware extracts from the El
  Torito catalog, and EDK II ships **no ISO9660 driver at all**, so the ISO9660
  root the BIOS loader reads is invisible on the UEFI path. The loader stopped
  with `E2`.
* **Options**: (a) write an ISO9660 reader into the loader over
  `EFI_BLOCK_IO_PROTOCOL`; (b) make the ESP carry the kernel; (c) build a second
  medium layout for UEFI.
* **Decision**: (b). `tools/mkefiimg.py` is invoked with `KERNEL.BIN` and
  `README.TXT` as well as `BOOTX64.EFI`, and the loader tries the volume it was
  *booted from* first (`kernel_volume=image`), falling back to scanning every
  file system (`kernel_volume=scan`) for machines that keep the kernel elsewhere.
  The ISO9660 root keeps its copy for the BIOS path, unchanged.
* **Why not (a)**: it is a filesystem driver on the boot path, written twice (the
  kernel has its own), inside a loader whose job is to be small enough to audit.
  It also buys nothing: the medium must carry the file either way.
* **Cost**: the kernel exists twice on the medium (≈65 KiB), and the two copies
  must not diverge. `tests/boot_test.py` reads both back with independent readers
  (the FAT12 walker and the ISO9660 directory-record parser) and fails if they
  differ, and `tests/uefi_stubtest.py` covers the image-volume and scan paths
  separately.
* **Consequence for users**: an ISO built by this repository boots under UEFI
  from the CD-ROM without a second partition. It still has no ESP *partition*, so
  it cannot be booted as a disk by UEFI; that is reported as NOT APPLICABLE, not
  as a pass.

## ADR-017 — File size comes from `EFI_FILE_INFO`; `ExitBootServices` is retried with a fresh map key

* **Problem**: two loader behaviours were correct against the stub and wrong
  against firmware.
  1. The loader learned a file's size by `SetPosition` past EOF and reading the
     position back. OVMF *accepts* that position and reports it back verbatim, so
     a 65 KiB kernel was read as a 4 MiB file and the read failed (`E3`,
     `read_size=0x400000 read_got=0x10534`).
  2. The loader read the memory map once, then printed to the console (which
     allocates in firmware), then called `ExitBootServices` with the now-stale
     key. OVMF answered `EFI_INVALID_PARAMETER` (`E13`).
* **Decision**:
  * Size is taken from `EFI_FILE_INFO` via `GetInfo`, the only authoritative
    source; the read loops until the reported size has arrived; the seek trick is
    kept **only** as a fallback for firmware without `GetInfo`.
  * `GetMemoryMap` and `ExitBootServices` sit in one retry loop. The map is
    re-read (with slack for descriptor growth, walked with the reported
    descriptor stride), the E820 array is rebuilt from the same copy, and
    `ExitBootServices` is retried up to eight times while firmware answers
    `EFI_INVALID_PARAMETER`. Nothing that can allocate — console output included
    — runs between the keyed `GetMemoryMap` and the call, and `console_closed` is
    set the moment it succeeds so no protocol is called afterwards.
* **Test coverage**: the stub firmware can now *be* OVMF in both respects
  (`clamp_seek_past_end=False`, `race_map_key=True`) and asserts that the loader
  boots anyway, that the retry count is exactly one, and that the console is
  never used after `ExitBootServices`.
* **Not claimed**: a memory map that keeps moving for more than eight attempts is
  reported as `E13`, not retried forever. A firmware that implements neither
  `GetInfo` nor the seek convention cannot be served, and that case ends at `E3`.

## ADR-018 — Real firmware is an external gate with three outcomes, never two

* **Problem**: the OVMF integration test cannot run on every machine — most
  build environments have no OVMF package and no emulator. A gate that silently
  degrades to "skipped" is the exact failure mode this project keeps hitting:
  green everywhere, nothing actually verified.
* **Decision**: `make ovmf-test` is its own gate, deliberately outside
  `make test` and `make verify`, and `tests/ovmf_boot.py` returns **three**
  outcomes: `0` verified, `1` verified failure, `3` NOT TESTED (no emulator, no
  firmware, or a file that is not a firmware volume). NOT TESTED is loud, lists
  what is missing, and is never reported as a pass. Inside a run, every check is
  PASS, FAIL or **NOT APPLICABLE**; a path this ISO does not offer is printed as
  N/A, counted separately, and never folded into the passes.
* **Firmware is external and stays external**: the harness discovers
  `PC5_OVMF_CODE` / `PC5_OVMF_VARS` / `PC5_QEMU` or the distribution paths, copies
  the VARS image per run, records the firmware SHA-256 and QEMU version in
  `build/ovmf-report.json`, and refuses a "firmware" that is not a firmware
  volume (a `_FVH` signature at offset 0x28), is implausibly sized, or contains
  the PC5 loader banner. No firmware is committed, embedded in the ISO or linked
  into the kernel, and a test asserts that.
* **Credit where it is due**: the harness prints `PC5-OVMF-BOOT-OK` from its own
  process after the checks pass. The guest never prints that marker, so a boot
  that merely *looks* right cannot fake success.

## ADR-019 — One PC5 platform: genuine PS5 system software is the destination, everything else serves it

*Naming note: the directive that ordered this decision proposed "ADR-016".
ADR-016, ADR-017 and ADR-018 were already issued for the M2 real-firmware work,
so this decision is ADR-019. Nothing about the decision depends on its number.*

### Context

PC5 exists to make compatible PC hardware operate as a PlayStation 5, with the
genuine Sony system software as the execution target. Until this decision the
repository was organised in two tracks (ADR-010): "PC5 Native" (a
PlayStation-inspired PC operating system with Linux as its foundation) and
"Prospero" (PS5 compatibility and research), with a machine-enforced rule that
the second may never gate the first.

That structure answered a real engineering question — *how do we keep an
experimental, legally constrained workstream from making a bootable product
unbootable?* — with an answer that also answered a question nobody asked: *how do
we keep the mission from being the product?* The roadmap made it explicit: the
rendered critical path (`M0 → … → M8`) contained no PS5 milestone, and
`tools/roadmap.py` **failed the build** if one appeared as a dependency.

### Original architectural problem

Three requirements were conflated:

1. **Product safety.** An experimental component must not be able to break the
   platform that boots.
2. **Legal safety.** PC5 must never require key material, decryption or
   circumvention to be useful or to be built (ADR-007).
3. **Mission ordering.** Which work gets done first.

Treating (1) and (2) as *architectural separation of objectives* produced (3) by
accident: the destination became optional.

### Actual product mission (restated, and now normative)

> **PC5 — An independent engineering project that builds PC-side boot, hardware and compatibility infrastructure toward running genuine Sony PlayStation 5 system software on compatible PC hardware.**
> PC5 is an engineering platform whose destination is to run the genuine Sony
> PlayStation 5 system software on compatible PC hardware, and to deliver the
> authentic PS5 platform and gaming experience there. PC5 is not a Linux
> distribution with a PlayStation theme, not an imitation of the PS5 interface,
> and not a project in which PS5 compatibility is optional.

The mission statement is data in `docs/architecture/roadmap.json`
(`product.identity`, `product.destination`) so that it is machine-checked rather
than retyped in each document.

### Existing architectural assumptions that were examined

| Assumption | Held? | Consequence |
|---|---|---|
| "A PlayStation-like PC operating system is the achievable product" | **no** | it is a different product; the achievable part is the *substrate*, not the destination |
| "PS5 compatibility must not gate the platform" | **no** | it must gate the destination, or the destination is a hobby |
| "Experimental code must not break boot" | **yes** | kept, as release isolation (ADR-021) |
| "Linux is the foundation" | **partly** | kept as hardware enablement, rejected as product identity (ADR-020) |
| "The freestanding kernel is the recovery environment" | **yes** | kept, and promoted: it is also the reference substrate for the platform model |
| "No Sony keys, firmware or derived binaries in the tree" | **yes** | kept and extended to the PUP (ADR-022) |

### Alternatives evaluated

**A. Keep two tracks, add a "shared vision" paragraph.** Rejected: nothing
changes mechanically, the gate still forbids the dependency, and the next
contributor reads the graph, not the paragraph.

**B. One track, PS5 work first, nothing else until it is unblocked.** Rejected:
the blocking condition (the authenticity boundary, §"Technical risks") is not
under the project's control, so this stops all engineering progress and produces
no evidence.

**C. One product, many workstreams, one destination, with a release boundary.**
**Selected.** Every workstream is required to state which part of the destination
it serves; the destination milestone must exist, must be reachable in the
dependency graph, and may never be marked complete without execution evidence.
Experimental and legally constrained components are isolated at *build and
runtime* boundaries instead of being isolated as *objectives*.

### Linux foundation analysis (evidence-based)

What Linux would provide: drivers for NVMe/AHCI, xHCI, audio codecs, Ethernet,
Wi-Fi, three GPU vendors; a scheduler, memory manager, VFS and network stack;
KVM for hardware-assisted virtualisation; VFIO for device passthrough. That is
years of work this project will not repeat.

What Linux cannot provide, and what decides the question:

1. **It cannot execute PS5 system software.** Sony's kernel and services are
   built against console devices and a console trust anchor. A Linux host can
   only *host a model of that platform*; the PS5 software would still have to run
   as the guest of that model. So Linux's role can only ever be substrate.
2. **It would become the visible product by default.** If PC5 ships a Linux
   userspace as the thing the user sees, the product is a Linux distribution, and
   the destination quietly becomes "a nice front-end".
3. **Its enablement does not touch the critical path.** The blocking research
   (the authenticity boundary, §"Technical risks") is about the console's
   security processor and fuses, not about how many Wi-Fi chipsets work.

**Conclusion**: Linux is a *substrate of convenience* for bring-up and for the
virtual-platform workstream, on hardware PC5's own kernel cannot drive yet. It is
not the product, it is not required to build or demonstrate the product, and it
is removable: the freestanding PC5 kernel remains the reference environment and
is the substrate the platform model is specified against. Recorded as ADR-020
with the exit condition.

### Independent kernel analysis

Cost: every device class becomes PC5 work — NVMe, xHCI, audio, network, GPU. On
mainstream hardware that is the largest single risk in the project and it does
not advance the destination, because the destination needs a *model* of PS5
hardware, not broad PC support.

Benefit: complete control of the platform contract, no foreign scheduler between
PC5 and the guest, deterministic behaviour, and a small auditable surface. The
existing kernel already proves the hardest part of the boot path (real OVMF
firmware, ELF-free PE32+ loader, its own storage stack).

**Conclusion**: the independent kernel stays, grows along the workstreams the
destination needs (interrupts, timers, memory protection, PCIe, NVMe, VMX/SVM),
and is not replaced by Linux. It is used for bring-up on qualified hardware even
where Linux would be more convenient, because the platform model must eventually
run on it.

### Hybrid architecture analysis

Selected shape: PC5 kernel is the platform; Linux is an *optional host tier*
("hardware enablement tier") that may run the same virtual-platform component
when PC5's own drivers are not yet sufficient. The boundary is a single
interface: the virtual-platform component is a library plus a device-model set
that can be hosted by either environment. Risk: two hosts to keep working. It is
accepted because the alternative (Linux-only) makes the model unportable, and the
other alternative (PC5-only) blocks bring-up on hardware today.

### Genuine PS5 compatibility implications

Running the genuine system software requires all of:

1. a lawful, authenticated input (the update/reinstallation package),
2. a platform contract close enough that Sony's kernel does not fault on
   unimplemented devices,
3. a device model presenting CPU features, memory map, interrupt controller,
   storage, GPU command processor, audio, USB and **security state**,
4. a trust story for the boot chain that does not involve circumvention.

Items 1–4 are workstreams in the roadmap. Item 4 is the one that may not be
solvable by engineering (see "Technical risks"). The architecture therefore
separates *what PC5 builds* (1–3, plus the analysis of 4) from *what must be true
externally* (a lawful trust path). It does not assume a breakthrough, and it does
not disguise the assumption as a plan.

### Selected architecture

* **One product, one destination.** `product.destination` in `roadmap.json`,
  level 6 of the success hierarchy, a milestone that must exist in the graph.
* **Twelve engineering workstreams plus governance and experience** (WS0–WS13),
  every milestone tagged with its workstream, its kind
  (`infrastructure` / `native-platform` / `ps5-compatibility` / `research` /
  `hardware-validation` / `product-experience`) and the mission link it advances.
* **A success hierarchy of six levels** with measurable acceptance criteria
  (`PC5_ARCHITECTURE_SPEC_v2.md` §5), from "boots on qualified PC hardware" to
  "genuine PS5 system software running on qualified PC hardware".
* **Release isolation instead of a track gate** (ADR-021): a component that is
  experimental, legally constrained, or depends on unverified inputs may not be
  *required* by the base platform's build or boot; it may be a dependency of the
  destination.
* **The destination is on the critical path.** The rendered graph's destination
  path runs through the PS5 platform workstream, and the tool reports it as the
  destination path, not as an optional branch.

### Reasons for the decision

* It removes the mechanism that made the mission optional while keeping the
  safety property that mechanism was introduced for.
* It makes the destination a data structure that tests can defend, so a future
  refactor cannot quietly delete it.
* It preserves every line of working code and every piece of evidence; no
  milestone that is complete today changes state.
* It makes the Linux question explicit and bounded instead of implicit and
  unbounded.

### Technical risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | The authenticity boundary cannot be crossed lawfully by anyone outside Sony | **existential for level 5** | stated as an external precondition; PC5 builds everything up to it; falsifiers recorded |
| R2 | The platform model needs undocumented devices | high | model only what public documentation and observables support; never guess silently; each device model has a conformance test |
| R3 | Emulating console hardware is mistaken for running console software | high | vocabulary rules stay enforced; level 5 requires execution evidence, not a model |
| R4 | Two hosts (PC5 kernel, Linux tier) diverge | medium | one interface, one conformance suite, both hosts run it |
| R5 | Performance of a modelled platform is unusable for games | high | measured per device model; passthrough path (VFIO/IOPM) evaluated before claiming game compatibility |
| R6 | Legal exposure from distributing anything derived from Sony code | **critical** | ADR-007 and ADR-022: no keys, no decryption, no redistribution; analyses are hash-only |

### Rejected alternatives

* Two tracks with a shared-vision paragraph (no mechanical change).
* PS5-work-first with everything else frozen (stops all progress on an external
  precondition).
* Linux-as-product with PS5 as a feature (the destination quietly changes).
* Declaring genuine execution "out of scope" (dishonest: it is the mission *and*
  the thing that may not be achievable).

### Reversal conditions

This decision is wrong if any of these is demonstrated:

1. A lawful, public path to executing genuine PS5 system software on non-console
   hardware exists and requires a *different* platform contract than the model
   described here — the platform model is then re-specified.
2. PC5's own kernel cannot reach the required device coverage on qualified
   hardware within the project's means, **and** a Linux-hosted model is shown to
   satisfy the same destination — the kernel tier is then demoted to reference
   implementation only.
3. The release-isolation rule proves unable to protect boot stability — a
   stricter build boundary is introduced, not a return to two objectives.
4. The authenticity boundary is proven uncrossable for everyone (not just for
   PC5) — level 5 is then recorded as unreachable and the product is redefined
   *explicitly and publicly*, never silently.

### Evidence and unresolved questions

Evidence for this decision: the audit in
`ARCHITECTURE_DISCREPANCY_REPORT.md` (measured), the gates listed in
`docs/TEST_RESULTS.md` §17, and the machine checks in
`tests/test_architecture.py` and `tests/test_product_identity.py`.

Unresolved, and stated as such:

* the exact platform contract of the PS5 SoC (undocumented),
* whether a modelled security state can ever satisfy Sony's boot chain lawfully,
* the performance ceiling of a modelled GPU for real titles,
* whether the PUP in the repository is the full reinstallation package (payload
  not fetched, `NOT VERIFIED`).

**This decision does not claim that genuine PS5 system software has been
executed. It claims that the repository is now organised around doing so, and
that every claim about how far it has got is machine-checked.**

## ADR-020 — Linux is a hardware-enablement substrate, not the PC5 product

* **Context**: ADR-009 made Linux the foundation of "PC5 Native". ADR-019
  restates the mission and makes the substrate question explicit.
* **Decision**: Linux may be used as an **optional host tier** that runs
  PC5-authored components (drivers bring-up, the virtual-platform device models,
  VFIO passthrough experiments) on hardware the PC5 kernel cannot yet drive. It
  is **not** the product, not the thing the user sees, and not required to build,
  boot or demonstrate PC5.
* **What Linux provides, precisely**: device drivers for classes PC5 has not
  written; a scheduler; memory management; KVM/SVM hosting; VFIO passthrough;
  established filesystems. That is engineering economy, and it is the reason the
  option is kept.
* **What Linux never provides**: execution of Sony's system software. A Linux
  host can only host a *model* of the PS5 platform; the PS5 software runs as that
  model's guest, or it does not run.
* **Boundary**: one interface — the virtual-platform component may be hosted by
  PC5's kernel or by the Linux tier; both hosts run the same conformance suite.
  No PC5 component may require Linux headers, binaries or a distribution.
* **Exit condition**: the Linux tier is removed when the PC5 kernel's device
  coverage and the model's host requirements are met on the qualified hardware
  list. The roadmap records this as a milestone (`M8`, release quality) rather
  than as an aspiration.
* **What must not be claimed in the meantime**: that PC5 *is* a Linux
  distribution; that Linux improves PS5 compatibility (it does not, in itself);
  that shipping a Linux desktop is a step toward the destination (it is a step
  toward a different product).
* **Enforced by**: `docs/architecture/roadmap.json` `product.foundation`
  (role `hardware-enablement`, ADR reference), and
  `tests/test_product_identity.py`, which fails if a document declares Linux the
  product's foundation or destination.

## ADR-021 — Release isolation replaces the two-track gate

* **Problem**: ADR-010's gate existed to stop an experimental component from
  breaking the bootable platform. It achieved that by forbidding dependencies in
  one direction, which also forbade the mission from being a dependency of the
  product.
* **Decision**: the safety property is re-expressed as *release isolation* and
  enforced structurally instead of by freezing objectives:

  1. The **base platform** (`make iso`, `make verify`, `make boot-test`,
     `make stubtest`, `make ovmf-test`, `make check`) must build and boot with
     every experimental and legally constrained component absent from the build.
  2. Such components are marked `experimental: true` in `roadmap.json`; the
     validator forbids them from being a dependency of any
     `kind: infrastructure` or `kind: native-platform` milestone that is marked
     `release_gating: true`.
  3. The destination may depend on anything, including blocked research. Nothing
     may depend on the destination to *boot*.
* **Consequences**: PS5 work can be a real dependency of the product (the point
  of ADR-019) while a broken experiment still cannot stop the machine booting.
  The claim "a PC5 release builds with `playstation/` deleted" is kept and
  generalised to "with every `experimental` milestone's code deleted".
* **Test**: `tests/test_architecture.py` builds a synthetic graph where an
  experimental milestone gates a release-gating one and requires the validator to
  reject it, and checks the shipped graph satisfies the rule.

## ADR-022 — The PS5 system-software package is a protected input, never a payload in the tree

* **Context**: the directive places the original Sony package (`PS5UPDATE.PUP`,
  declared as the fresh-installation/reinstallation package) at the centre of the
  ultimate objective. The file at the repository root is a Git LFS pointer; the
  1.33 GiB payload is in LFS storage and is not part of any checkout here.
* **Decision**:
  1. The payload is **never** committed as bytes, never extracted into the tree,
     never redistributed, and never modified. Its identity is recorded by hash
     and size only.
  2. PC5 implements no decryption, no key handling, no signature bypass and no
     authentication circumvention, for this package or any other Sony artifact
     (ADR-007 unchanged and extended).
  3. The package is an **input** to the platform model, not a disk image: it is a
     console updater's payload, and whether it contains everything needed for a
     first boot is `NOT VERIFIED`.
  4. Any analysis that is performed records *hashes and structure*, never content
     that could be redistributed, and runs in a workspace outside the repository.
  5. The roadmap's first PS5 milestone (`M9`) exists to prove these rules with a
     recorded provenance file rather than to promise progress: pointer hash,
     declared object id and size, lawful-acquisition statement, and an explicit
     "payload not present" status.
* **Why a decision record**: because the next contributor will otherwise do the
  obvious thing — `git lfs pull`, unpack it, and start writing a parser. That
  path ends in a repository that cannot be published and a project that has
  traded its legal position for a file.
* **Evidence**: `ARCHITECTURE_DISCREPANCY_REPORT.md` §1.1 (measured pointer,
  measured analyser output, LFS metadata), `tests/test_product_identity.py`
  (the pointer's oid/size are asserted and the payload's absence is asserted),
  `docs/PS5_SYSTEM_SOFTWARE_TARGET.md` (what is known and what is not).

---

## ADR-023 — The loader validates what firmware hands it, and firmware is an external dependency of the gate

* **Context**: the UEFI loader copied the kernel to a fixed address, trusted the
  memory map it was given and reported a handover it had never verified. All
  three are invisible when the machine is well behaved, which the emulator is;
  the first reserved low page on a real board turns each into a crash a long way
  from its cause. At the same time, the only way to execute real firmware is to
  depend on a package the repository does not contain, which repeatedly produced
  the failure mode this project exists to avoid: a gate that silently degrades
  into "skipped" and is read as green.
* **Decision**:
  1. **Validate, then cross.** Before `ExitBootServices`, the loader checks that
     its whole handover window `[0x2000, 0x9F000)` — E820 buffer, VBE block, PVD
     buffer, boot info, kernel, stack — is covered by conventional memory in the
     map it is about to hand over (`E15`); the kernel copy is read back before
     the jump (`E17`); descriptors are merged rather than silently truncated and
     an over-large map is `E16`.
  2. **The map key is a loop, not a formality.** `GetMemoryMap` and
     `ExitBootServices` stay one retry loop: nothing that can allocate runs
     between the keyed query and the call, `EFI_INVALID_PARAMETER` means "read
     it again", and exhausting the retries is a failure with a code (`E13`), not
     a jump.
  3. **Firmware is external and versioned, and the gate says when it is absent.**
     No firmware is committed, embedded or downloaded into the tree;
     `tools/ovmf_setup.sh` provisions it from the distribution, the report
     records its paths and SHA-256, and a missing dependency is exit code 3
     (NOT TESTED) — which fails CI rather than passing quietly.
  4. **Secure Boot is judged by the refusal.** PC5 signs nothing, so an image
     that boots with a policy enabled is evidence the policy was not in force,
     not evidence of compatibility.
  5. **A row in the required matrix is never inherited.** `--matrix-out` marks a
     row `verified` only when an executed run produced it, `external` when its
     evidence is a named different gate, `not-tested` otherwise.
* **Alternatives considered**: allocating the kernel with `AllocateAddress` and
  letting firmware choose the address (rejected: the boot contract fixes
  `0x10000`, and moving it is a protocol bump); keeping the silent truncation at
  `E820_MAX` (rejected: an under-reported map is worse than a stopped boot);
  vendoring an OVMF blob so the gate always runs (rejected: unversioned
  proprietary-adjacent binary in a repository whose whole legal position is that
  it ships no firmware).
* **Consequences**: the loader now fails early and loudly where it used to
  succeed by luck, which is the point; the OVMF gate has a provisioning step
  someone has to run; and a machine without OVMF reports NOT TESTED rather than
  a green tick.
* **Evidence**: `docs/TEST_RESULTS.md` §18 (58/58 stub checks, 119/119 BIOS
  checks, 165 unit checks, OVMF not tested there), `boot/uefi/uefi_main.c`
  (`E15`–`E17`, watchdog, retry loop), `tests/uefi_stubtest.py`,
  `tests/ovmf_boot.py` (`--secure-boot`, `--matrix-out`),
  `tools/ovmf_setup.sh`.

---

## ADR-024 — The platform contract is a registry, and an UNKNOWN operation is a refusal

* **Context**: `M10` produced a contract document written by hand, one table per
  subsystem, with a source marker in each row. Three failure modes were already
  visible in it. A sentence could be softened without anyone noticing, because
  nothing compared the document to a source of truth. A row could keep a claim that
  a source did not support, because "PUBLIC" was a word someone typed. And the
  interfaces the subsystem sections described existed only as prose, so no test could
  prove that an `UNKNOWN` row was actually implemented as a refusal rather than as a
  plausible zero.
* **Decision**:
  1. **Evidence lives in a registry, not in prose.** `psm/registry.py` holds every
     source and every claim as data: classification, what the source establishes,
     confidence, remaining uncertainty, the engineering decision that depends on it,
     and the conformance case that exercises PC5's side. `Contract.problems()`
     enforces the rules and `tests/test_psm_registry.py` runs them.
  2. **The document's tables are generated.** Each sits between
     `<!-- psm:begin … -->` markers; `python3 -m psm.render --write` regenerates them
     and the document test fails byte-wise when a block and its renderer differ.
     Prose stays hand-written — that is where judgement lives — but no number, claim
     or interface surface is typed twice.
  3. **`UNKNOWN` is a refusal with no handler.** An operation the contract marks
     unsupported is recorded as unsupported in its operation table and has *no*
     implementation method at all; dispatch returns `UNSUPPORTED` before a handler
     could invent a value. A construction-time check fails if a *supported* operation
     is documented without a handler, so the two directions are both enforced.
  4. **The linkage is audited.** `psm/audit.py` fails when an operation cites a claim
     that does not exist, cites a claim from another subsystem, cites only supportable
     claims while claiming to be unsupported, or implements something a `RESTRICTED`
     claim says must never be built. It also enforces the eight-group mapping over ten
     interfaces, so a missing group cannot hide behind a split.
  5. **The suite must be able to fail.** `tests/test_psm_mutations.py` replaces one
     double at a time with a deliberately broken variant — overlapping allocations,
     double release, a fabricated success for an `UNKNOWN` operation, a timeout that
     returns a message, a log that drops records — and requires the conformance suite
     to notice. A surviving mutation fails the build.
  6. **A green suite claims nothing about Sony.** The suite proves PC5's doubles match
     PC5's contract. It does not prove that the genuine system software accepts the
     interface, and no document may present it as doing so.
* **Alternatives considered**: keeping the hand-written tables and reviewing them
  (rejected: review is exactly what the repository already does, and the three failure
  modes survived it); generating the entire document including prose (rejected: the
  judgement — what to say about an unknown, which decision depends on it — is the
  valuable part and does not belong in a template); implementing `UNKNOWN` operations
  as handlers that raise (rejected: a handler is a place where someone later returns a
  zero, and the absence of one is checkable).
* **Consequences**: the contract cannot drift from the registry without failing
  `make check`; adding a claim now requires naming a source, an uncertainty, a
  decision and a conformance case, which is slower and is the point; and `M10`'s
  acceptance is documentary by construction, asserted against tests rather than
  against optimism — while its roadmap state stays `in_progress`, because a level-4
  milestone may not be `complete` on level 1–2 evidence.
* **Evidence**: `docs/architecture/PS5_PLATFORM_CONTRACT.md` (revision r1, 38 claims,
  10 interfaces, 261 conformance checks), `psm/` (registry, contract rules, audit,
  render, ten doubles), `tests/test_psm_registry.py`,
  `tests/test_psm_conformance.py`, `tests/test_psm_mutations.py`,
  `tests/test_psm_document.py`.

---

## ADR-025 — PC5 vendors no third-party source archive and borrows no project identity

* **Context**: the repository began as a single upload: a 19.7 MB third-party
  source archive of a PS5 static relinker, committed at the root and tracked as a
  Git LFS pointer. Around it grew a vendor note explaining why it was studied
  rather than vendored, a fetch script that could re-download it, and roughly a
  dozen documents that referenced the project by name — including the licence
  paragraph, the README, the architecture specs, the audit records and the
  research log. None of that was ever a dependency: no build target read the
  archive, and PC5's destination (genuine PS5 system software on PC hardware) has
  nothing to do with relinking game binaries. But a tree that names another
  project a dozen times reads, to anyone arriving later, as a derivative of it.
  PC5 is not a fork, not a companion and not an affiliate of any other project.
* **Decision**:
  1. **The archive is removed.** The ZIP, its LFS pointer, the vendor note and the
     fetch script are deleted from the tree. The payload was never fetched here
     and is not reproduced in any form — not as an archive, not extracted, not
     renamed, not as a pointer.
  2. **No references remain.** The project's name is not used in PC5's README,
     documentation, architecture, roadmap, specifications, comments, commit
     messages or release notes, and no new reference may be added. Where the
     audit record needs to say what arrived with the repository, it says "a
     third-party source archive" and keeps the measurements, which are PC5's own
     work. Nothing about the removal is concealed: the audit documents state that
     the archive existed, that it was assessed, and that it is gone.
  3. **No third-party archive is vendored, ever.** PC5 tracks no source archive of
     another project and fetches none at build time. Prior art is cited in prose,
     with its own licence, and is never copied into the tree.
  4. **A guard test enforces it.** `tests/test_project_independence.py` walks every
     tracked file and fails the build if the removed project's name appears in a
     path or a text file, if any `.zip` is tracked at the repository root, if the
     removed archive's LFS object id reappears, or if `vendor/` comes back. The
     forbidden token is assembled from parts inside the test so that the guard
     does not itself reintroduce the string it forbids.
  5. **History is not rewritten.** The commits that added and updated the archive
     stay in the repository. Deleting history would hide how the tree reached its
     present state, which is the opposite of what this project does elsewhere; the
     requirement was to remove the file from the current tree and stop it
     returning, and both are satisfied without a force-push.
* **Alternatives considered**: keeping the archive as "provenance" (rejected: it
  is another project's source, not PC5's evidence, and it was 99.9% of the
  repository by size); keeping the vendor note and the fetch script as
  documentation of prior art (rejected: both are build-time paths to another
  project's tree, and both name it); rewriting history to erase the archive from
  the commits (rejected: unauthorised, and it would conceal rather than clean);
  leaving the references and only deleting the file (rejected: the documents, not
  the file, are what made PC5 look derived).
* **Consequences**: the tree is smaller, self-describing and free of another
  project's name; the audit documents now describe the archive instead of naming
  it, so a reader cannot identify the upstream project from PC5 alone — which is
  the point, and is stated in those documents rather than hidden; and any future
  attempt to re-add the archive fails `make check` instead of landing quietly.
* **Evidence**: `tests/test_project_independence.py` (five path/content guards
  plus three identity assertions), `git ls-files` showing no archived ZIP and no
  `vendor/`, `docs/PROJECT_AUDIT.md` §3 (the assessment, kept, renamed),
  `docs/architecture/REPOSITORY_AUDIT_2026-11-15.md` §8.1 (repository weight, now
  resolved), and `docs/TEST_RESULTS.md` §20 for the executed gates.

---

## ADR-026 — Firmware identity is the implementation token; the distribution is recorded, never asserted

* **Context**: the OVMF gate's row *"the loader names the firmware it is running on"* was a
  substring test for `firmware=EDK II`. It had held for every boot this repository has ever
  captured — every committed genuine boot that carries the marker prints `EDK II` (16 of the
  17 serial logs under `docs/evidence/`, 22 occurrences) — and it
  failed on the hosted runner, where the same firmware reports
  `Ubuntu distribution of EDK II`. The reason is that the string is a build-time PCD
  (`PcdFirmwareVendor`, whose upstream value in `MdeModulePkg.dec` is exactly `L"EDK II"`) and
  distributors override it to name themselves. The same predicate was also loose where it
  should have been strict: an unbounded substring match accepts `EDK II-beta` and
  `EDK II (whatever follows)`. One row was therefore simultaneously asserting a
  distributor's choice of build flag and forgiving arbitrary trailing text — the two
  properties a gate on identity may not have at the same time.
* **Decision**:
  1. **The gate asserts the implementation, exactly.** `classify_firmware_identity` accepts
     the literal `EDK II`, or `<clause> of EDK II` anchored at both ends. Nothing else is read
     into the string, and the case-sensitive form is kept because the firmware's own bytes are
     the evidence.
  2. **The distribution is recorded, never asserted.** The clause before ` of EDK II` is
     captured verbatim into `loader_firmware_distribution` and into
     `report.data[<stage>]["firmware_identity"]`, and no row, stage or matrix requirement may
     key on it — `tests/test_ovmf_harness.py` asserts the stage table stays free of it. A
     distributor name is not evidence of a distributor: anyone building EDK II can write any
     name into that PCD, which is also why no document may call this row a provenance check.
  3. **The raw string survives verbatim, including on a refusal.** `loader_firmware_said`
     keeps what the loader printed. Classification is recorded *beside* it, never instead of
     it, so an unexpected identity can be audited after the fact rather than only the verdict.
  4. **`UNKNOWN` is a refusal with a name.** `TianoCore`, `EFI Development Kit II` and `OVMF`
     are refused, each with its raw string quoted, because they are plausible spellings this
     project has no captured boot for. Adding one is a decision taken against a log, in the
     ADR-024 sense; it is not a repair to be inferred from the fact that the words look right.
  5. **A self-issued label cannot certify a boot.** Any vendor string containing `PC5` is
     refused whatever its shape, because `tests/uefi_stub_firmware.py` sets that same
     system-table field to `PC5 stub firmware` deliberately. The row's power is stub-versus-
     genuine firmware, and the rule is by shape so it cannot be recovered by renaming a stub
     into the accepted grammar.
  6. **Three absences stay three.** No line at all (the 32-bit loader prints none when the
     field is NULL or empty), `absent` (NULL pointer) and `empty` (zero-length string) are
     refused under distinct reasons; and a string of exactly the profile's own print limit
     (31 for `utf16_to_ascii` into a 32-byte buffer, 32 for `serial_write_ascii16`) is refused
     as *ambiguous*, not as a mismatch — the loader's buffer would be the cause, not the
     firmware's identity.
* **Alternatives considered**: accept any string containing `EDK II` (rejected — it is the
  defect being repaired, and it leaves the false accept in place); an allowlist of recognised
  distributors, refusing anything else (rejected — it asserts exactly the provenance claim the
  string cannot support, and would turn red on the next distribution upgrade for a reason that
  is not a defect); making the loader print a canonical identity, or trim the field itself
  (rejected — the loader's job is to echo `SystemTable->FirmwareVendor`, and normalising it in
  the product would leave the gate measuring our own formatting; ADR-023 keeps firmware an
  external dependency); narrowing the row to "a `firmware=` line was printed" (rejected — that
  is a weaker claim than the one under test, dressed as a simpler one).
* **Consequences**: the hosted runner's OVMF now passes the row on its own evidence; five
  shapes that previously passed now refuse (§41's replay), which is the tightening this record
  exists to make visible rather than to slip through; the report gained fields, so a verdict can be audited
  from the artifact; and an unfamiliar firmware arrives as a named `UNKNOWN` refusal carrying
  its own raw string — evidence for a decision, instead of a mystery in an annotation.
* **Evidence**: `tests/ovmf_boot.py` (`EDK2`, `EDK2_DISTRIBUTION_RE`, `SELF_ISSUED_MARK`,
  `ABSENT_FORMS`, `PRINT_LIMIT`, `classify_firmware_identity`, `identity_record`,
  `FIRMWARE_LINE_IA32`/`FIRMWARE_LINE_X64`), `tests/test_ovmf_harness.py`
  (`FirmwareIdentityContractTests`: every row on both profiles, the self-issued refusal, the
  three absences, truncation-versus-mismatch, and the 64-bit `revision=` boundary),
  `boot/uefi/README.md` (the marker chain), `docs/TEST_RESULTS.md` §40–§41 (the hosted
  annotation that started this), and edk2's `MdeModulePkg.dec` for the default PCD value.

## ADR-027 — PC5's licence is GPL-3.0-only, and the grant is stated outside the licence body

* **Context**: the tree carried two incompatible answers to one question. `main` received a
  `LICENSE` that is the verbatim GNU General Public License version 3 text — 674 lines, 35,149
  bytes, no sentence anywhere in it granting anything to PC5 — added at `9e3fda61e420` with the
  message "Add LICENSE file". The working branch held a fifteen-line file instead: a GPL
  preamble, a pointer to the FSF text, and a grant of the *second* version. Four surfaces then
  disagreed by construction — the repository's licence file, GitHub's badge (which is *detected*
  by scanning the opening of the text, so it read the 674-line file and reported version 3), the
  README summary table, and `tests/test_project_independence.py`, which asserted a sentence from
  the branch's file and therefore failed on any merge that took `main`'s. Nothing caught the
  divergence while it formed, because the documentation check derived the identifier from
  whichever file it was handed and a verbatim copy of somebody else's text parses as a licence
  document while stating no decision.
* **Decision**:
  1. **The licence is `GPL-3.0-only`.** The project owner chose it; the choice is recorded by
     this ADR, and no tool here infers, widens or narrows it. The *only* half is not a stylistic
     preference: an "any later version" clause is a promise about terms nobody has reviewed, so
     widening the grant has to be an act in `LICENSING.md`, not a default in a parser.
  2. **`LICENSE` is the verbatim official text, unchanged.** It is the standard document, byte for
     byte — no spliced paragraph, no local amendment, no project-specific sentence. A licence the
     distribution cannot reproduce from the FSF's own text is not the GPL, and the file is the
     thing a recipient hashes.
  3. **The grant lives in `LICENSING.md`.** A project's decision is stated in the project's own
     person, in a file that is not the quoted document, so the two can be validated against each
     other. `tools/licensing.py::read` is the one reader: it derives the identifier from the
     notice's grant sentence, verifies the official body against the SHA-256 it pins, and refuses
     to derive anything from the boilerplate's *How to Apply* instructions.
  4. **A refusal is a value, not an exception.** A file that states no grant, a grant with no
     version or no *only* clause, a notice contradicting its own asserted identifier, and a
     notice contradicting the official text each return their own sentence. `make docs-check`
     fails the build once, against the file at fault; the About page prints the refusal instead of
     inventing a number, because a page that cannot be rendered cannot say anything.
  5. **No copyright line is invented.** No holder, no year range, and no per-file
     `SPDX-License-Identifier` on a file whose author this project cannot confirm. A header
     comment is a copyright statement, and asserting one over somebody else's work is a factual
     error dressed as hygiene.
* **Alternatives considered**: keep the branch's short file and let it win the merge (rejected —
  a pointer to a licence, rather than the licence, is what a recipient of a distributed image has
  to be handed); paste the grant sentence into `LICENSE` so one file answers everything (rejected
  — it edits the official text, which breaks the one property that makes the file reusable as
  evidence and makes `License: GPL-3.0` detection and hash comparison unreliable); adopt
  `GPL-3.0-or-later` because a badge said version 3 (rejected — that is a different grant, chosen
  by nobody); dual-license the kernel against the Linux kernel's own terms (rejected — PC5 ships
  no Linux code, so the reasoning in
  [`docs/architecture/LINUX_VS_FREESTANDING.md`](architecture/LINUX_VS_FREESTANDING.md) applies to
  a licence PC5 does not need); print the identifier in prose in addition to the derived row
  (rejected — a second copy of a one-source fact is the copy that goes stale, and it is what made
  the discrepancy survivable in the first place).
* **Consequences**: the README row, `docs/ABOUT.md` §1 and §6, `docs/PRESENTATION.md`'s contract
  row, the risk register and the freestanding comparison all become derived or checked against one
  reading, so the discrepancy cannot silently return; `make docs-check` fails on an edited
  `LICENSE` even when the notice is perfect, which is the new behaviour and the reason this record
  exists; the About row must be regenerated whenever either file changes (`make presentation`, then
  `make presentation-check`), and a merge that takes a different `LICENSE` therefore has to
  regenerate the page rather than resolve text by hand; third-party licences stay out of scope —
  the vendoring ban of ADR-025 is what keeps the GPL's reach bounded, and `pupil.png`'s provenance
  stays UNVERIFIED rather than being swept into "GPL-3.0-only" by a blanket statement.
* **Evidence**: `LICENSING.md`, `LICENSE` (whose sha256 `python3 tools/licensing.py` prints),
  `tools/licensing.py`, `tools/docs_check.py::licence_grant`, `tools/presentation.py::licence_row`,
  `tests/test_licensing.py` (the eight refusals, including the two contradictions and the
  modified-text case, which is built by editing a copy of the real file), and
  `tests/test_project_independence.py`.
