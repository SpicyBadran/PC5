# Execution feasibility — can genuine PlayStation software run here, and what do we build meanwhile?

| | |
|---|---|
| Question | the owner's product statement makes *genuine PlayStation software execution wherever technically and legally possible* a first-class goal. Is it possible, on which route, and what blocks it — as facts, not as posture in either direction? |
| Extends | the two-path model of [`../PS5_COMPATIBILITY_ARCHITECTURE.md`](../PS5_COMPATIBILITY_ARCHITECTURE.md) and the dependency classes of [`../playstation/BOOT_FEASIBILITY.md`](../playstation/BOOT_FEASIBILITY.md). **It does not replace either** |
| Depends on | [`../research/PS5_FIRMWARE_AND_PUP.md`](../research/PS5_FIRMWARE_AND_PUP.md) — the input is a pointer, not a package |
| Decided by | [`ARCHITECTURE_DECISION_RECORD.md`](ARCHITECTURE_DECISION_RECORD.md) ADR-030 (protected input) and ADR-034 (the feasibility verdict below) |
| Measured on | 2026-11-28, baseline `9b20228` |

## 0. Vocabulary borrowed, not invented

The repository already grades a claim, and this document uses those grades so no sentence
here can inflate one:

> **detected** → read from tables or CPUID · **implemented** → code exists and a test in this
> repository exercises it · **verified** → the result is recorded in `docs/TEST_RESULTS.md` ·
> **executed** → the actual code of that system ran on real or emulated hardware ·
> **playable** → sustained gameplay measured and recorded per title.

A route is *feasible* here only if it can reach **executed** with an input PC5 may lawfully
hold. Reaching *detected* is not feasibility, and a mock-up is not execution.

---

## 1. Path A — boot the genuine PS5 system software (firmware path, stages 1–4)

**Route.** Take a real package, load it, satisfy the boot environment, execute it on PC-side
hardware. This is `M18`→`M19`→`M20` in the graph, all `blocked`.

**What supports it.**
The instruction set is not the problem: the PS5's CPU is an AMD Zen 2 x86-64 part and the OS
is a proprietary FreeBSD derivative ("Prospero"), reported as a FreeBSD 11/12-lineage kernel
([apexgear](https://apexgear.blog/what-os-does-ps5-use), tier D; the FreeBSD lineage is the
long-standing community position and matches the PS4 "Orbis" precedent). Class 1 of
`BOOT_FEASIBILITY.md` already lists "the CPU instruction set family (x86-64), standard
PCI/ACPI/SMBIOS discovery, standard AHCI/NVMe *hardware*" as compatible-as-is.

**What blocks it — and it is not one thing.**

| # | Blocker | Evidence | Class |
|---|---|---|---|
| A1 | **No input.** The only package PC5 references is a dangling LFS pointer | [`../research/PS5_FIRMWARE_AND_PUP.md`](../research/PS5_FIRMWARE_AND_PUP.md) §1; `ci/README.md` records the clone-time failure | 6 |
| A2 | **The inner components are encrypted per-binary**, with keys bound to on-die secure storage, so a package alone does not yield runnable code | tier-C format reports (SELF `ET_SCE_EXEC` type `0xFE00`, per-segment AES, "keys … stored in Aeolia's secure storage on the hardware") | 3, 5 |
| A3 | **The boot chain is anchored in hardware PC5 cannot present**: security processor and fuses, console GPU firmware interface, custom SSD decompression path, Tempest audio DSP | `BOOT_FEASIBILITY.md` §2 classes 3–4; the console "is cryptographically locked" to its own authenticated firmware (tier D, consistent with A2) | 3 |
| A4 | **The shell is not separable from the platform**: it is a user-space consumer of system services that consume kernel drivers that assume console devices and authorisation state | `BOOT_FEASIBILITY.md` §5 | 3, 4 |
| A5 | **Authentication is outside our control**: a modified package must pass checks PC5 neither holds nor may bypass | `PS5_SYSTEM_SOFTWARE_TARGET.md`; Issue #73's explicit warning | 5 |

**Verdict.** *Feasible as research, not as a deliverable.* No public evidence shows PS5
system software booting outside a console, and the blockers are hardware-anchored
(A2, A3) plus legal (A5), not engineering gaps that effort alone closes. Treating A1 as the
only gap would be the exact optimism the directive forbids. This is the same conclusion
`BOOT_FEASIBILITY.md` reached ("assessment, not a plan of record"; `M19` is *the end of the
destination path*, blocked, not scheduled) — with the 2026 external field data now added
below, which had not been gathered when that page was written.

## 2. Path B — run genuine PS5 *binaries* on a PC host (execution path, stages 5–7)

**Route.** No console OS at all: take the game executable, satisfy its system-library and
GPU contract natively — relinking and shim translation rather than full-machine emulation.

**What supports it, as of late 2026 — the field moved, and the numbers matter.**

| Observation | Source | Reading |
|---|---|---|
| RPCS3's lead developer, 8 July 2026: *"A PS5 emulator does not yet exist!"* | [VideoCardz](https://videocardz.com/newz/ps5-emulation-race-begins-before-gta-6-launch-some-2d-games-already-run-on-pc) | the most credible voice in PS3 emulation declining to call the current state an emulator |
| SharpEmu shipped a first numbered public build (`v0.0.5-nexus-release.2`, 3 Oct 2026); its headline result is a Steam Deck demo at **~0.6 FPS** that stalls before normal gameplay; 60.3 MB Windows build | [tech-insider](https://tech-insider.org/shadps4-vs-sharpemu-ps4-ps5-emulator-2026/) (tier D, consistent with VideoCardz) | ~60 MB of code is a fraction of a PS5 system stack; performance is not a hardware-tuning gap |
| Community compatibility tracker: **46 of 9,011** catalogued PS5 titles with any test report, **3** rated playable — all simple titles | [teckhustlers](https://teckhustlers.com/is-there-a-ps5-emulator-for-pc/) summarising the tracker (tier D) | the honest scale of the field, one generation behind PS4 emulation |
| RPCSX can boot PS5 **VSH** (the console's system UI) and safe mode, with reported sound and pad input, on Linux or Windows via WSL; game launch still in development | [VideoCardz](https://videocardz.com/newz/ps5-emulation-race-begins-before-gta-6-launch-some-2d-games-already-run-on-pc) | **the strongest single datum for Path A**: a partial shell boot has been demonstrated by third parties, so A4 is a matter of depth, not impossibility |
| A third-party PS5 *relinker* converts commercial PS5 executables into native Windows/Linux binaries — no CPU emulation, SPIR-V shader translation, GPL v2; its mid-October 2026 release claims 100 % *GPU shader-instruction* coverage and **1 verified playable game** (`Dreaming Sarah`), with reports of `Hollow Knight: Silksong` at ~60 FPS | VideoCardz, Tom's Hardware, KitGuru and NotebookCheck coverage as recounted on shattered.io (tier C/D; the project's own name is its URL slug and is deliberately not written here — `tests/test_project_independence.py` forbids that string anywhere in the tracked tree) | the *architecture* PC5's execution path describes is viable enough that other groups ship it; the *result* is one indie title |
| Kyty's PS5 work: last official release Aug 2022; documentation confirms simple PS4 games and PS5 **homebrew**, with audio/networking absent | [VideoCardz](https://videocardz.com/newz/ps5-emulation-race-begins-before-gta-6-launch-some-2d-games-already-run-on-pc) | homebrew-only is the honest label for that codebase's state |

**What blocks it.**

| # | Blocker | Note |
|---|---|---|
| B1 | Retail executables are SELF-packaged and segment-encrypted (A2) | without lawful keys, PC5 cannot *obtain* the code it would relink. This is the blocker, not the relinking |
| B2 | The GPU contract: RDNA2/LibAGC command streams (PM4 Type-3 buffers) must be translated to D3D12/Vulkan, and shader *translation* ≠ shader *coverage* | the field's own wording: "100% GPU shader-instruction coverage (**not** full GPU support)" |
| B3 | The system-library surface is large and undocumented (Prx/PRX modules, `sys_regmgr_call`, `sys_mmap_dmem`, `sys_equeue_create` and friends appear in third-party syscall tables) | every HLE shim is an engineering claim about behaviour PC5 has not measured |
| B4 | Audio, networking, pad features lag in every project (Kyty explicit) | the "3 of 9,011" outcome is a *stack* problem, not a CPU problem |
| B5 | Legal: any tool that *decrypts* protected content engages DMCA §1201 regardless of how clean the rest is (see §4) | this is why PC5 implements intake/verification, never decryption |

**Verdict.** *Partially feasible and demonstrably hard.* Relinking native x86-64 PS5
executables is proven possible in principle by third parties; PC5 cannot reach it for
retail titles because of B1, and the supporting stack (B2–B4) is years, not weeks. PC5
therefore keeps stages 5–7 at **RESEARCH** in the compatibility architecture, builds the
intake and *verification* path that needs no proprietary bytes, and refuses to present a
shader-coverage percentage as a playable claim.

## 3. Path C — the Windows 11 application that delivers the system environment now

**Route.** PC5's own product: a Windows 11 application implementing the observable PS5
system environment — the shell and its navigation, the update/installer semantics, storage
and device presentation, controller model, diagnostics — over PC5's own service layer, with
genuine execution attached **only** where a lawful path exists (Path A/B research, or an
attached genuine console the owner operates).

**What supports it.** Everything needed is either PC5's own code or public Windows API: the
legacy corpus is category-2 engineering that already builds and tests (1382 tests at
baseline); no blocker in A or B applies, because C makes no claim to run Sony bytes.

**What blocks it.** Only environment: this sandbox has no `dotnet`, `msbuild`, `csc`,
`clang`, `cmake` or `cargo` (`command -v` → `ABSENT` for each) and no Windows kernel, so the
app is compiled and run by CI and by the owner on Windows 11 — ADR-033's rule. See
§5 of [`../TRANSFORMATION_BASELINE.md`](../TRANSFORMATION_BASELINE.md).

**Verdict.** *Feasible, immediately, and it is the spine of the transformation.* Category 2
by definition, category 3 where it attaches to genuine execution.

### How C relates to the existing two paths — the mapping, so nothing forks

| `PS5_COMPATIBILITY_ARCHITECTURE.md` | This document | Transformation handling |
|---|---|---|
| firmware path stages 1–4 | **Path A** | stays `blocked` with its blockers named; research continues on the input question |
| execution path stages 5–7 | **Path B** | stays `RESEARCH`; intake + verification only |
| *(not represented — the model predates the Windows product)* | **Path C** | new milestones `M0`–`M7` in the graph, four separate CI dimensions (ADR-028, ADR-029) |

The rule "neither path is ever used as evidence for the other" is **kept and extended**:
C's app dimension, A/B's research dimension, the legacy image dimension and release
readiness are four independent columns (§ ADR-029).

## 4. The legal line, stated as the field actually draws it

| Precedent / event | What it establishes | Effect on PC5 |
|---|---|---|
| *Sony Computer Entertainment, Inc. v. Connectix Corp.*, 203 F.3d 596 (9th Cir. 2000) | intermediate copying of a console BIOS to *reverse-engineer* unprotected elements was fair use; the emulator was "a legitimate competitor" | independent reimplementation — Path C, and Path B's shims — has a basis |
| *Sega v. Accolade* (9th Cir. 1992), *Sony v. Bleem* (9th Cir. 2000) | reverse engineering for compatibility is protected in the same circuit | supports analysis-in-documentation, which is what ADR-025 mandates (cite, never copy) |
| *Nintendo of America v. Tropic Haze* (Yuzu), D.R.I. 1:24-cv-00082 — $2.4M, permanent injunction, March 2024 | the holding rests on **decryption with unauthorised keys** and a tool "primarily designed to circumvent", i.e. 17 U.S.C. §1201 — **not** on emulation as such; a settlement, so not precedent | PC5 ships **no** keys, **no** decryption, **no** instruction for either; the refusal in ADR-022 is the risk control, not a formality |
| DMCA wave against 13 Switch-emulator projects, 13 Feb 2026 (incl. Eden, Suyu, Sudachi); 311 Suyu-related repos removed in one August sweep; suyu archived 13 Sep 2026 | distribution infrastructure is fragile even where the code is lawful; enforcement targets keys/circumvention | PC5's release engineering must never carry firmware, and documentation must not read as a circumvention guide |

Practical consequence, in one sentence: **the safe and honest position is the same one —
build PC5's own environment, verify but never decrypt, and let the graph carry the
blocked-ness of the rest.**

## 5. What would reverse each verdict

Recorded so that a future change is a *decision*, not a drift.

| Verdict | Reversed by |
|---|---|
| A: blocked on input + hardware | a lawfully held package (research §5 procedure) **and** a documented, lawful trust path that does not require extracting an on-die key. The first alone changes only A1 → and would let `M18` move to `in_progress` |
| A: shell not separable | a public, reproducible demonstration that the shell runs with a PC5-presented device set (third-party VSH boots get close; PC5 has not reproduced it, so it stays a falsifier) |
| B: unobtainable retail code | a title whose licence permits redistribution for research, or homebrew/self-authored PS5 binaries. Path B work then becomes testable end-to-end without A2 |
| B: GPU/audio immaturity | measured per-title *executed* logs from PC5's own harness — not coverage percentages |
| C | nothing reverses C. It is the route that is always open, which is why it is the spine |

## 6. Standing conclusions (the four sentences, no more)

1. **Genuine PS5 system software on PC hardware is not achievable by PC5 today**, for
   input, hardware and legal reasons (A1–A5), and the graph says so with `blocked`.
2. **Genuine PS5 binary execution is partially feasible and immature across the whole
   field** — 3 titles playable out of 9,011 catalogued by community counting, and the
   best-funded approaches report one verified indie game; PC5 keeps it at research.
3. **The blockers are not "we lack effort"**: they are a missing lawful input and
   hardware-bound key material. Saying otherwise would be the drift the directive bans.
4. **Path C is where PC5 becomes real software**: a Windows 11 application, its own
   services, its own evidence, its own CI dimension, with genuine execution attached the day
   a lawful path exists.

## 7. Sources

Format/legal background as tiered in [`../research/PS5_FIRMWARE_AND_PUP.md`](../research/PS5_FIRMWARE_AND_PUP.md) §4; field-status pages cited inline above
(VideoCardz tier C; the aggregator articles tier D and used only where consistent with C).
Court records: *Connectix* 203 F.3d 596; *Bleem* 214 F.3d 1022; *Tropic Haze* 1:24-cv-00082 —
as summarised in [aliteq's case review](https://aliteq.com/is-game-emulation-legal) and
[Argo Law's analysis](https://argolawyer.com/the-possibly-illegal-world-of-video-game-emulators-understanding-yuzu/);
the February 2026 DMCA wave and its effects per
[Boiling Steam](https://boilingsteam.com/did-nintendo-kill-switch-emulation-the-state-of-yuzu-ryujinx-and-their-forks-in-2026/).
None of these is a legal opinion; PC5 is not counsel and does not pretend otherwise.
