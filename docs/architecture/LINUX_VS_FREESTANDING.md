# Linux-based vs freestanding-kernel foundation

Decision record: ADR-009. This document is the analysis behind it, including the
option that was rejected and the conditions under which the decision reverses.

## 1. The question, stated precisely

PC5 needs to boot mainstream PCs, drive their hardware, host a PlayStation-like
interface and run games. Which kernel provides the hardware foundation?

The question is **not** "which kernel is better". It is: *what is the cheapest
credible path to a machine that installs, boots, displays, stores data, accepts
controller input and plays a game — without giving up the diagnostics that make
failures debuggable?*

## 2. The four options

| | Option | Description |
|---|---|---|
| **A** | Freestanding-only | Extend the in-tree 32-bit kernel into the operating system: own drivers for NVMe, AHCI, xHCI, audio, Ethernet, Wi-Fi, and three GPU vendors; own scheduler, memory management and user mode. |
| **B** | Linux-only | Ship a Linux-based PC5 image and retire the freestanding kernel. Recovery is "boot the installer again". |
| **C** | **Hybrid (recommended)** | Linux is the hardware and system foundation for PC5 Native; the freestanding kernel is preserved as the recovery and diagnostic environment, bootable from the same medium. |
| **D** | Other microkernel / BSD | e.g. a seL4-class microkernel with a Linux driver host, or FreeBSD as the base. |

## 3. Criteria and weights

The weights are the argument; the scores are judgements. A reader who disagrees
with a weight can recompute the table rather than argue about a verdict.

| Criterion | Weight | Why that weight |
|---|---|---|
| Driver coverage for mainstream hardware | 25 | the directive's objective is "all mainstream PC hardware"; this is the dominant cost driver |
| Time to a first installable, usable system | 15 | the project must produce a real OS, not an experiment |
| Recovery and diagnosability | 15 | an OS that boots but cannot be diagnosed on a broken machine is a support dead end |
| Maintainability over years | 15 | drivers and security fixes arrive continuously; the project cannot outrun that alone |
| Control over the user experience | 10 | the PlayStation experience needs ownership of the shell, not of the GPU driver |
| Suitability for PS5 compatibility work | 10 | user-space runtime, GPU translation, storage and audio APIs |
| Legal and licensing clarity | 5 | GPL-2.0-only project distributing a kernel-based image |
| Feasibility in the current build environment | 5 | affects what can be proven now, not what is correct |

Scoring 1 (bad) to 5 (good), weighted score = Σ weight × score, maximum 500.

| Criterion (weight) | A freestanding | B Linux-only | **C hybrid** | D microkernel/BSD |
|---|---|---|---|---|
| Driver coverage (25) | 1 → 25 | 5 → 125 | **5 → 125** | 3 → 75 |
| Time to installable system (15) | 1 → 15 | 4 → 60 | **4 → 60** | 1 → 15 |
| Recovery / diagnosability (15) | 4 → 60 | 2 → 30 | **5 → 75** | 3 → 45 |
| Maintainability (15) | 1 → 15 | 5 → 75 | **4 → 60** | 3 → 45 |
| Experience control (10) | 5 → 50 | 3 → 30 | **4 → 40** | 4 → 40 |
| PS5 compatibility suitability (10) | 3 → 30 | 5 → 50 | **5 → 50** | 4 → 40 |
| Legal clarity (5) | 5 → 25 | 4 → 20 | **4 → 20** | 4 → 20 |
| Feasibility here (5) | 5 → 25 | 1 → 5 | **1 → 5** | 1 → 5 |
| **Total** | **245** | **395** | **435** | **285** |

C wins because it takes B's driver coverage and PS5-runtime suitability and adds
the one thing B gives up: a small, dependency-free environment that still boots
when the system does not. That is not sentimentality about the existing code; it
is the difference between "reinstall and hope" and "read the log and know".

## 4. Option A, honestly assessed

What the freestanding kernel already proves is real: a 4 KiB loader, a 36 KB
kernel, working ATAPI/ISO9660, firmware inventory, a serial console that survives
a broken display, and an honest compatibility chain. That is a strong diagnostic
environment and a weak operating system.

What it would need to become the operating system:

* NVMe, AHCI, xHCI, HDA/HDMI audio, Ethernet and several Wi-Fi families;
* a user/kernel boundary, virtual memory, processes, a scheduler, ELF loading;
* GPU drivers for AMD, Intel and NVIDIA — the single largest body of code in any
  operating system;
* a filesystem, a package system, an update mechanism, a compositor and a shell.

Even a generous estimate puts this beyond the project's capacity, and every one
of those drivers would be less tested than the Linux equivalent from the day it
lands. Option A is rejected on cost, not on principle, and the existing kernel is
retained because its strengths are exactly the ones the OS needs at its worst
moment.

## 5. Option B, honestly assessed

Linux-only is the fastest path to a working desktop and gets GPU, storage, USB,
audio and networking for free. What it loses:

* when the GPU stack fails on a specific laptop, the machine shows nothing — the
  current serial console and framebuffer fallback are exactly the tool that
  diagnoses such a failure;
* the project's honest reporting would have to be rebuilt inside a general
  distribution instead of remaining a small auditable kernel;
* an installer that cannot boot a diagnostic environment cannot verify the
  machine it is about to install onto.

B is rejected as a *complete* answer, and absorbed as the system half of C.

## 6. The recommended architecture (C)

```
┌──────────────────────────────────────────────────────────┐
│ PC5 media                                                │
│  ├─ system image: Linux kernel + firmware + PC5 services │  normal boot
│  └─ recovery image: freestanding PC5 kernel + tools      │  second entry
└──────────────────────────────────────────────────────────┘
          shared: PC5-BOOT contract · PC5-LOG format ·
                  chain reporting · release manifest
```

Rules that make the hybrid safe:

1. **Shared contract, independent binaries.** Both kernels accept the same
   `struct pc5_boot_info` v1 from the same loaders, so one medium boots either.
2. **The recovery environment is never built from the system it recovers.**
   Recovery stays freestanding and dependency-free.
3. **Linux is the system, not the interface.** PC5 owns the shell, sessions,
   platform services and update model; the distribution underneath is an
   implementation detail with a documented, reproducible composition.
4. **No driver is written twice.** Where Linux already supports a device, PC5
   does not duplicate it. The freestanding kernel grows only what recovery and
   diagnostics need (block read, filesystem read, input, display, log).
5. **The PS5 compatibility runtime is user space.** It consumes `PC5-PLAT`
   interfaces; it is never a kernel patch. This is what makes it possible to
   iterate on it without endangering boot.
6. **GPL compliance is explicit.** The project is GPL-2.0-only; kernel config,
   patches and the reproducible build recipe ship with any binary image.

## 7. What Linux does not solve

Stated here so no future reader mistakes the foundation for a result:

* **Genuine PS5 system software** — unchanged by the choice of kernel; the
  blocker is the console's security processor and boot chain, not the host OS.
* **PS5 executable compatibility** — relinking and PRX shims are user-space work;
  the kernel only has to provide sensible graphics, memory and audio.
* **GPU command translation for PS5 titles** — a runtime problem, not a driver
  problem; Mesa gives the host API, not PS5 compatibility.
* **DualSense feature depth** — the kernel enumerates the device; light bar,
  haptics and adaptive triggers need PC5 integration work.
* **Firmware blobs** — many Wi-Fi and some GPU features need non-redistributable
  firmware. PC5 will name the requirement per device rather than pretend the
  device class is universally supported.

## 8. Why this cannot be executed in this environment

Measured on 2026-11-15:

| Need | Available here |
|---|---|
| Linux source | **yes** — `codeload.github.com` serves `torvalds/linux` (verified HTTP 200) |
| Kconfig/build prerequisites (`flex`, `bison`, `bc`, libssl, zlib) | **no** — none installed, `apt` unreachable |
| Test vehicle (QEMU/KVM or hardware) | **no** — no `qemu-system-*`, no `/dev/kvm` |
| A way to boot a kernel other than the emulator used for the current image | **no** — v86 runs the current 32-bit image only |

So ADR-009 is accepted **in principle** and its implementation is **blocked**.
What the project must therefore not do: describe a Linux foundation as built,
measure "GPU support" from a PCI device id, or publish an image containing a
kernel nobody booted.

**Exit condition.** ADR-009 becomes executable when a build host can build a
Linux kernel (or accept a reproducibly-built one from a contributor) *and* boot
it, either in a VM or on the first reference machine. The first deliverable then
is not a desktop: it is `PC5-LOG` lines from that kernel on the AMD reference
machine, which is the same evidence standard every other milestone uses.

## 9. Reversal conditions

| Observation | Response |
|---|---|
| Linux cannot be made to boot reproducibly on the reference hardware | revert to a freestanding-first plan; M1–M4 re-scope around a narrower device set |
| A qualified device class needs invasive kernel patches that cannot be maintained | drop that device from the qualification matrix rather than fork the kernel silently |
| The freestanding kernel becomes the more capable environment (unlikely, but possible if Linux bring-up stalls) | re-evaluate the weights with real data; the decision is not a loyalty test |
| Legal review finds distribution constraints on required firmware | ship a documented install step for the user-supplied firmware, or de-qualify the device |

## 10. Summary

* Foundation: **Linux** — for drivers, security maintenance and the system
  services the project should not rewrite.
* Recovery and diagnostics: **the existing freestanding kernel** — because it is
  small, dependency-free and already proven to boot.
* Interface and experience: **PC5-owned** — sessions, shell, update model and the
  PlayStation experience.
* Compatibility runtime: **user space, Track B, isolated** — never a kernel patch.
* Today: nothing in this document has been built. The decision is recorded, the
  evidence requirement is defined, and the environment that blocks it is
  documented instead of worked around.
