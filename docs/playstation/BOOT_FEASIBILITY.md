# Genuine PS5 boot feasibility

**Status: assessment, not a plan of record.** Nothing in this document claims
that genuine PS5 system software can run on a PC today. It exists so the project
can say precisely what is missing, which parts are engineering, and which part
is not available to us at all.

Scope: running Sony's PS5 system software — its kernel, services and the genuine
home interface — on hardware that is not a PlayStation 5.

Related: `docs/FIRMWARE_ANALYSIS.md` (what the package is),
`docs/PS5_COMPATIBILITY_ARCHITECTURE.md` (the two execution paths),
`docs/playstation/RESEARCH_LOG.md` (dated findings).

---

## 1. The dependency chain, in boot order

Each step can only be assessed once the one above it exists. This is why a
"boot the PS5 firmware" milestone has never been scheduled: the chain has no
first public link.

| # | Requirement | Why it is required | Status |
|---|---|---|---|
| 1 | The system software's outer container understood | Sony ships it as an update package (`SLB2`), not an image | RESEARCH — tooling exists, no image analysed |
| 2 | A verified, authorised way to obtain the inner components | the package is signed; components are encrypted per version | BLOCKED — no authorised means, and PC5 will not circumvent |
| 3 | A trust anchor equivalent to the console's security processor | the first code the SoC runs is authenticated by hardware and fuses | BLOCKED — Sony-specific silicon |
| 4 | Platform initialisation matching the console's expectations | boot strap, memory map, device tree of a custom SoC | BLOCKED — undocumented |
| 5 | CPU compatibility | Zen 2 core, but the kernel is built for the console's platform | PARTIAL — same ISA family, different platform contract |
| 6 | GPU and display | RDNA 2 with console-specific firmware and command paths | BLOCKED — no public driver contract |
| 7 | Storage | custom NVMe controller with its own decompression path | BLOCKED |
| 8 | Audio | Tempest DSP | BLOCKED |
| 9 | USB/controller and wireless | console-specific controllers | BLOCKED |
| 10 | System services and the shell | depend on 3–9 and on authorised services | BLOCKED |
| 11 | Game execution | depends on 10 for the native path | NOT REACHED |

## 2. Classifying every dependency

The project's rule is that each dependency is placed in exactly one class, with
evidence. "Compatible" is not a class you may enter by hoping.

| Class | Meaning | Items |
|---|---|---|
| **1. Already compatible with standard PC hardware** | runs on a PC as-is | the CPU instruction set family (x86-64), standard PCI/ACPI/SMBIOS discovery, standard AHCI/NVMe *hardware* (but not the console's storage contract) |
| **2. Potentially adaptable** | someone could write an adapter with authorised inputs | container parsing, structural metadata, filesystem drivers for documented formats |
| **3. Dependent on Sony-specific hardware** | no PC equivalent exists | security processor and fuses, Tempest audio DSP, custom SSD decompression path, console GPU firmware interface |
| **4. Dependent on undocumented interfaces** | behaviour is not public | the console boot strap, service IPC and shell contracts, controller pairing |
| **5. Restricted by technical or legal limits** | we must not proceed | decrypting protected components, bypassing authentication, redistributing firmware |
| **6. Currently impossible to verify** | no hardware, no authorised image, no emulator | any claim of "the shell runs" |

Class 1 is real but small, and it is *not* the part that is failing. The failure
is concentrated in classes 3 and 5.

## 3. What would have to be true

For genuine system software to run, at least one of these must happen:

**A. Sony authorises it.** A licensed PC platform or a published
virtualisation contract. Nothing like this exists publicly. No amount of
engineering substitutes for it.

**B. A complete console emulator exists**, including the security processor,
so that the signed boot chain is emulated rather than bypassed. This is a
very large project, publicly unattempted in full, and it would still require
decrypted inputs unless the emulator reproduces the security processor's
behaviour — which brings its own legal constraints.

**C. The system software is rebuilt from decompiled or leaked sources.** This is
what PC5 will not do: no Sony binaries, keys or derived code are distributed,
and no circumvention is implemented.

If none of A–C holds, genuine PS5 system software cannot be booted by this
project, and the honest deliverable is the analysis and tooling plus a clear
statement — which is what this repository contains.

## 4. Falsifiers

The assessment above is wrong if any of these is demonstrated:

1. an authorised, public path to obtain the inner system components,
2. a working full-SoC emulator that boots Sony-signed software on commodity
   hardware (with published evidence),
3. a Sony-licensed scheme to run PS5 system software on PC.

Any of these would be recorded in `docs/playstation/RESEARCH_LOG.md` with a
source, and this document would be revised. None has been observed.

## 5. Why the interface cannot be separated from the platform

A recurring hope is that "the PS5 interface" could run on its own. Publicly
documented behaviour argues against it: the shell is a user-space consumer of
system services, which consume kernel drivers, which assume console devices and
authorisation state. Even a partial port would need classes 3 and 4 to be solved
first. PC5 therefore builds its **own** interface (`kernel/ui.c`) and labels it
as PC5 — never as PlayStation software. The distinction is asserted by
`tests/test_architecture.py` and by the compatibility chain at runtime.

## 6. What PC5 does instead

The platform workstream (`WS10`) and the rest of the platform, in one product:

1. The **platform model** is built where it can be: contract, CPU and memory
   model, storage, graphics, audio, USB and service devices (`M10`–`M16`), each
   with a conformance suite and PC5-authored test payloads. `M10` acceptance is
   met and the milestone is still `in_progress`: the evidence-level rule keeps a
   level-4 milestone from being called complete on level 1–2 evidence.
2. The **security boundary** analysis (`M17`) is published whatever it finds;
   the load, boot, runtime and installation milestones (`M18`–`M21`) are
   `blocked` on it and on a lawful input, and say so in the graph.
3. Everything the destination needs and does not depend on Sony bytes — boot,
   diagnostics, storage, partitions, filesystem, installer, drivers, services —
   proceeds on its own evidence.
4. Every claim is attached to a level in `docs/PS5_COMPATIBILITY_STATUS.md`, and
   "genuine PS5 software is running" stays `NO` until a log proves otherwise.

## 7. What this means for scheduling

The genuine-execution milestone (`M19`) is the **end of the destination path** —
the longest dependency chain the graph has that ends there — and it is `blocked`,
not scheduled.
That is a statement about the blocker, not a decision to leave the destination
optional: the work that is *not* blocked (`M10`–`M16`) is on that path and is
where the next engineering effort goes. The previous revision of this section
drew the opposite conclusion, and ADR-019 withdrew it.
