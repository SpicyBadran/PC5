# Gates A–E: what each one opens, and what it does not

A gate in this repository is a **dependency**, not an authorisation. Passing one
makes the next piece of work *possible*; it never widens the project's scope, and
it never turns one evidence level into another. Each gate lists what must already
exist, what evidence counts as passing it, and what it explicitly does not
authorise.

This document exists because "we have a 64-bit kernel now" and "we can run PS5
software" are not the same sentence, and a project that confuses them will end up
claiming both.

## Gate A — physical boot evidence

| | |
|---|---|
| Opens | the only route to evidence levels 5b and 9: a real machine running PC5's own image |
| Depends on | a built ISO (`make iso`, `make verify`), a target machine on the qualification list, `make hw-record` |
| Acceptance criteria | a record under `docs/hardware/` naming machine model, firmware version and mode (UEFI or CSM), the image SHA-256, the serial output, and whether the desktop appeared; `make hw-record` exits 0 on it |
| Does **not** authorise | claiming hardware support in general, claiming PS5 hardware behaviour, or skipping OVMF/emulator evidence for the same commit |
| Current status | **NOT TESTED.** No record has been filed; the template exists and says so. |

## Gate B — lawful investigation of a Sony package

| | |
|---|---|
| Opens | reading metadata, and only metadata, of a package the owner supplies |
| Depends on | an owner-supplied copy with documented provenance, recorded hashes, read-only handling, and written confirmation of the allowed scope |
| Acceptance criteria | provenance and hashes recorded; every step reproducible without any key, signature, authentication or encryption bypass; the package is never committed, redistributed or modified |
| Does **not** authorise | signature bypass, decryption, key extraction, secure-boot changes, or any claim that the package has been analysed when only its pointer/metadata was read |
| Current status | **BLOCKED.** The repository holds a 135-byte Git LFS pointer. A read-only batch-API query on 2026-11-16 answered HTTP 200 with a download action for the declared object id and size, so the object exists in storage and is readable without credentials; this says nothing about the package's contents, type or version, and no payload was fetched. |

## Gate C — 64-bit kernel foundation

| | |
|---|---|
| Opens | M2a work: APIC, timers, SMP, and per-machine CPU records, on a 64-bit base instead of the 32-bit compatibility path |
| Depends on | Gate A for firmware/hardware evidence; the stub-firmware and unit evidence delivered by `make kernel64`, `make uefi64`, `make stubtest64`, `make check` |
| Acceptance criteria | long mode entered without a mode switch; PC5-owned page tables installed and observed by the kernel; `ExitBootServices` with a live map key; boot protocol versioned and validated; the success marker printed only after all checks pass; the release artifact still 32-bit |
| Does **not** authorise | marking `M2a` complete (APIC/timers/SMP/SVM rows are still open), claiming process protection, claiming a hypervisor, or making the 64-bit path a dependency of the release image |
| Current status | **IMPLEMENTED and UNIT/STUB-FIRMWARE TESTED; OVMF and hardware NOT TESTED.** `M2a` remains `not_started`; the work is recorded as foundation `F64`. |

## Gate D — SVM feasibility

| | |
|---|---|
| Opens | experiments that ask whether *this* machine can host guests at all |
| Depends on | Gate C (a kernel that can program the CPU), a machine with CPUID SVM advertised, firmware settings that leave SVM enabled, and a permission decision that is written down before the experiment |
| Acceptance criteria | the six SVM evidence levels recorded separately per machine: advertised (CPUID), firmware-enabled, permitted, PC5-initialised, guest executed, VM-exit tested |
| Does **not** authorise | describing CPUID support as hypervisor functionality, claiming PS5 compatibility from virtualisation, or bypassing any Sony protection |
| Current status | **LEVEL 1 ONLY, in emulation.** The feature report exists; no SVM initialisation code has been written. |

## Gate E — hardware support

| | |
|---|---|
| Opens | moving device classes from "detected" to "supported" in the qualification list |
| Depends on | Gate A (so support is measured on hardware), Gate C (a 64-bit kernel to drive it) and, for storage work, Gate B if any Sony-produced medium is involved |
| Acceptance criteria | per device class: a driver that performs an operation, a measured result on a named machine, and a qualification-list row with the evidence linked; failures stay recorded |
| Does **not** authorise | universal-PC claims, speculative PS5 hardware claims, or replacing measured rows with the word "supported" |
| Current status | **NOT STARTED.** `M2` and `M2b` are `not_started`; the qualification list has no measured rows. |

## How the gates interact

```
Gate A (physical boot)  ──┬──► Gate C (64-bit foundation) ──► Gate D (SVM feasibility)
Gate B (lawful package) ──┘                │
                                           └──► Gate E (hardware support)
```

An arrow means "is a prerequisite of", not "is implied by". Gate C delivered its
stub-firmware and unit evidence without Gate A; that is why it is recorded as a
foundation with open criteria rather than as a passed gate.
