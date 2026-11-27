# Risk register — blockers and unproven assumptions

Two lists, deliberately separate:

* **Blockers** — known reasons work cannot proceed, each with the condition that
  clears it.
* **Assumptions** — things the architecture believes without having proven, each
  with the test that would falsify it.

Scoring: impact (1 = annoyance, 2 = milestone slip, 3 = project-level setback) ×
likelihood (1 = low, 2 = medium, 3 = high). Anything at 6 or above needs a named
mitigation and a trigger for the weekly review.

---

## 1. Blockers

| ID | Blocker | Impact | Likelihood | Score | Clears when | Owner |
|---|---|---|---|---|---|---|
| B1 | **Storage write exists only in emulation.** ATA PIO 512-byte write/read-back works on the test disk (M1d), but no physical disk has been written, there is no partition-table parser or writable filesystem, and AHCI/NVMe are absent. | 3 | 2 | **6** | a physical write on REF-AMD, then GPT parsing + a writable filesystem behind the same sector interface, with a test that writes and re-reads a file | kernel-engineer |
| B2 | **No real UEFI firmware run.** The loader is executed only against `tests/uefi_stub_firmware.py`. | 3 | 3 | **9** | OVMF (or a UEFI-capable VM) is available and the recorded run is committed; a physical UEFI machine reaches the kernel | kernel-engineer |
| B3 | **No physical hardware access.** Nothing here has ever run on a real machine; no KVM either. | 3 | 3 | **9** | One reference machine produces a committed `docs/evidence/` record | qa-engineer |
| B4 | **Linux cannot be built or booted here.** `flex`, `bison`, `bc`, libssl/zlib headers absent; `apt` unreachable; no QEMU/KVM. Source is reachable via `codeload` but untestable. | 3 | 3 | **9** | A build host with the prerequisite set and a test vehicle exists; ADR-009 exit condition met | lead-architect |
| B5 | **No USB stack**, therefore no DualSense, USB storage or USB Ethernet. | 3 | 3 | **9** | xHCI enumeration + HID parser + DualSense report parsing, tested against a real controller | kernel-engineer |
| B6 | **CI could not be activated.** The GitHub App installation lacked the Workflows permission; five pushes were rejected and recorded. | 2 | 3 | **6** | **Mitigated 2026-11-24:** the owner granted *Workflows: Read and write*, and `ci/build.yml` moved to `.github/workflows/build.yml` in a pure-rename commit. The residual risk is the one this row never covered: a green run that certifies less than it appears to, which is why `ci/README.md` records the six pipeline corrections made *before* activation. | release-engineer |
| B7 | **No verified path to genuine PS5 system software.** The console's boot chain roots trust in a security processor and fuses; full-system emulation is not available. This is the blocker behind `M17`-`M21` and therefore behind the destination itself. | 3 | 3 | **9** | `M17`: publish the analysis and the falsifiers; if no lawful path exists for anyone, level 5 is recorded as unreachable by decision (ADR-019 reversal 4) | compatibility-engineer |
| B8 | **No lawfully obtained PS5 executable available to the project.** PC5 neither distributes dumps nor circumvents encryption; testing depends on an owner supplying their own. | 3 | 2 | **6** | The owner supplies a dump of their own disc for a legally clean title and it is analysed locally without redistribution | compatibility-engineer |
| B9 | **No firmware image supplied** and none may be supplied by the project. | 2 | 3 | **6** | The owner supplies their own image for local analysis; only hashes and structure are recorded | compatibility-engineer |
| B10 | **No compositor, session model or shell.** The interface is a text console in a ring-0 kernel. | 3 | 2 | **6** | M3 session/desktop work lands behind `PC5-PLAT` with a test | platform-engineer |

B7 is the honest headline: the project's most attention-grabbing objective has
the least available groundwork. The architecture is designed so that its failure
does not stop the operating system from shipping.

---

## 2. Unproven assumptions

Each row states what the project assumes, why it might be false, and the cheapest
test that settles it. An assumption with no test is a belief, and this project
does not put beliefs in its README.

| ID | Assumption | If false | Falsification test | Owner |
|---|---|---|---|---|
| A1 | Linux can be brought up reproducibly with PC5-owned branding and services on the reference machines. | ADR-009 reverses; M1–M4 re-scope around a narrower device set. | Boot a stock kernel on REF-AMD, capture `PC5-LOG`, then rebuild it with the PC5 config fragment and reproduce the hash. | lead-architect |
| A2 | The UEFI loader's logic is firmware-independent enough that a stub pass predicts a real pass. | The loader needs restructuring under real firmware; M1c slips. | Boot `BOOTX64.EFI` under OVMF and diff the loader's trace against the stub trace. | kernel-engineer |
| A3 | The boot contract v1 (`PC5-BOOT`) can carry a Linux payload without a version bump, or with a clean v2. | A new placeholder format is needed; loader and recovery change together. | Load a 6 MB payload with unreal-mode loading on hardware; confirm the loader refuses to overrun safely today. | kernel-engineer |
| A4 | A writable filesystem can be added behind the existing block interface without reworking the ATA driver. | Storage work grows into a controller rewrite; M1 slips. | Implement block write on ATA PIO against an emulated disk, then re-verify ISO reads. | kernel-engineer |
| A5 | AMD RADV/`amdgpu` is sufficient for the PC5 experience without vendor-specific work. | The console experience needs extensions Mesa does not expose; graphics engineer re-plans. | Render and measure on REF-AMD; enumerate the features the shell actually uses. | graphics-engineer |
| A6 | Intel integrated graphics on REF-INTEL can drive the console session at a usable frame rate. | REF-INTEL drops to a desktop-only role. | Measure the M4 shell workload on UHD 730. | graphics-engineer |
| A7 | The NVIDIA proprietary driver can be pinned tightly enough to keep performance claims reproducible. | NVIDIA becomes "boots, not qualified", or a separate reproducibility class. | Build twice with a pinned driver version and compare measured results, not just hashes. | qa-engineer |
| A8 | DualSense features (light bar, haptics, adaptive triggers) are reachable from user space via HID output reports. | Features move into a kernel driver or stay partial; M4 acceptance changes. | Dump a real controller's report descriptor and exercise each feature. | platform-engineer |
| A9 | Wi-Fi can be qualified per chipset without redistributing firmware. | Wi-Fi becomes "bring your own firmware" with a documented step, or P2. | Identify the chipset in each reference machine and record its firmware requirement. | kernel-engineer |
| A10 | PS5 executables can be relinked and run with independently written shims for at least one title. | M5 records "not reached"; WS11 reduces to research. | Build the upstream relinker on a capable host and attempt one lawful executable; record the outcome either way. | compatibility-engineer |
| A11 | Release isolation survives contact with real code: no experimental component is ever required to boot (ADR-021). | The charge is proposed as an explicit release-gating milestone, not smuggled in as an exception. | Remove every `experimental` milestone's code and run the full release suite (validator rule + synthetic-graph test). | lead-architect |
| A12 | The repository's evidence culture survives growth: new contributors report measurements, not impressions. | Documents drift again, as they did in nine places before this audit. | Every release review re-runs the §6 drift check; `tools/roadmap.py` and the link/evidence tests keep the machine-checkable parts honest. | qa-engineer |
| A13 | v86's SeaBIOS behaviour is representative enough for the BIOS boot path to be worth keeping. | Physical BIOS machines fail where the emulator passed; CSM support is documented as best-effort. | One physical CSM boot with the log committed. | qa-engineer |
| A14 | A console experience can be delivered without Sony trademarks, assets or services. | Product/legal re-scoping of the shell. | Legal review of the shell's icons, sounds and naming before M4 ships publicly. | lead-architect |

---

## 3. Legal and policy risks

| ID | Risk | Position |
|---|---|---|
| L1 | Firmware redistribution | Never. Images are analysed locally; the repository and releases contain none (ADR-007). |
| L2 | Circumvention of protection measures | Out of scope and refused. PC5 does not decrypt, patch signature checks or spoof console identity. |
| L3 | Interoperability legality of re-implementing system APIs | Research question for M6's legal assessment; precedent exists for independent re-implementation, but the assessment is a deliverable, not an assumption. |
| L4 | Trademark use in the project name and shell | "PC5" is the project's own name; PlayStation/PS5 marks are referenced descriptively. Shell assets must be original (A14). |
| L5 | GPL compliance for a distributed kernel-based image | Kernel config, patches and build recipe published with any binary image; the licence is GPL-3.0-only, granted in `LICENSING.md` over the verbatim `LICENSE` text (ADR-027). |
| L6 | Patent exposure in emulation/translation work | Not assessed; flag to counsel before any public B1 binary distribution. |

---

## 4. Product and process risks

| ID | Risk | Mitigation |
|---|---|---|
| P1 | Scope collapse: the project tries to build an OS, a compatibility layer and firmware research simultaneously and finishes none. | The graph orders the work and release isolation keeps experiments out of releases (ADR-021); the destination path is one chain, not three. |
| P2 | Overclaiming to attract interest, then being unable to defend it. | Evidence levels, per-machine records, and the drift audit that already corrected nine statements. |
| P3 | Hardware fragmentation consumes the schedule. | Three reference configurations, P0/P1/P2 classes, and a documented "not supported" list. |
| P4 | A single-agent session makes unsupervised changes at scale. | Every phase is one revertible commit; critical boot/kernel/compat changes require independent review; suites gate each phase. |
| P5 | Documentation rot returns after this audit. | Machine-checked artefacts: roadmap graph, evidence paths, documentation links, generated block sync. |
| P6 | Release pressure overrides qualification. | M8 acceptance requires release-build L3 evidence for every qualified machine. |

---

## 5. Top five for the next review

Ordered by what unblocks the most work per unit of effort:

1. **B2 — run the UEFI loader under OVMF.** No code change expected; it converts
   an "implemented and stub-executed" claim into a firmware result and de-risks
   the entire physical bring-up.
2. **B1 — physical storage write, then partitions and a filesystem.** The
   emulated write path (M1d) removed the API question; what remains is proving
   it on real iron and building installation on top of it.
3. **B3 — one physical boot.** Cheapest way to find out which of A2, A5, A13 are
   false.
4. **B4 — a Linux-capable build host.** Required before M2 can even be planned
   honestly.
5. **B6 — CI activation.** A one-line owner action that makes every future claim
   reproducible by a third party.

## 6. Review procedure

* The weekly review reads this register, the milestone graph, the newest
  `docs/evidence/` record and the newest physical boot log.
* A blocker is removed only by evidence; a mitigation is only accepted with the
  trigger that would invoke it.
* A new assumption enters the register when a design decision depends on it, not
  after it fails.
