# PS5 compatibility status

**PC5 — An independent engineering project that builds PC-side boot, hardware and compatibility infrastructure toward running genuine Sony PlayStation 5 system software on compatible PC hardware.** This page is
the per-level account of the destination workstream. Every `NO` below is a
measurement, not a mood.

Build under test: PC5 `0.2.0` at `main` (`8aab6d3`) plus the unmerged PR #26
branch; recorded gates are the ones in `docs/evidence/validation/`. `M2` is
**not** complete — it is `not_started` in `roadmap.json`, and this page does
not claim otherwise.
Architecture authority: [`architecture/PC5_ARCHITECTURE_SPEC_v2.md`](architecture/PC5_ARCHITECTURE_SPEC_v2.md).
Evidence: [`TEST_RESULTS.md`](TEST_RESULTS.md) §16 and §17.

---

## 1. The six success levels

| Level | Statement | State | Evidence |
|---|---|---|---|
| 1 | PC5 boots on qualified PC hardware | **NO for physical hardware** — emulated only (SeaBIOS + real OVMF) | `TEST_RESULTS.md` §16 |
| 2 | PC5 controls the required hardware through its own architecture | **PARTIAL** — firmware tables, PCI/ACPI, ATA PIO, PS/2, FAT32; no APIC, NVMe, USB | `make boot-test` 119/119 (emulated) |
| 3 | Core platform services work | **NO** | `M3`, `M2c`, `M2d`, `M7` not started |
| 4 | PS5 execution and system-software interfaces exist and are experimentally validated | **PARTIAL** — the contract, its evidence registry and ten PC5-authored interfaces exist and pass 261 conformance checks; no console-side behaviour is modelled and no Sony software has run | `PS5_PLATFORM_CONTRACT.md`, `psm/`, `M10` |
| 5 | Genuine system-software compatibility | **NO — BLOCKED** | `M17`–`M21` in `roadmap.json` |
| 6 | Platform experience | **NO** | `M4`, `M5`, `M20` |

## 2. The destination workstream, milestone by milestone

| Milestone | State | What it would take |
|---|---|---|
| `M9` baseline and provenance | **in progress** | the package is a 135-byte LFS pointer; the 1.33 GiB payload is not in any checkout and was never analysed here |
| `M10` platform contract | **in progress — acceptance met** | 38 claims classified PUBLIC/INFERRED/UNKNOWN/RESTRICTED over four sources, ten interfaces with refusal semantics, 261 conformance checks and 16 caught mutations. Held from `complete` by the evidence-level rule (level 4, evidence at levels 1–2). It establishes nothing about the console's behaviour; that is `M11`–`M16` |
| `M11` CPU and privileged execution model | not started | virtual-platform core plus conformance payload |
| `M12` memory architecture model | not started | unified address space, GPU-visible mappings, protection domains |
| `M13` storage and I/O model | not started | queue/DMA/completion semantics; host a filesystem the guest mounts |
| `M14` graphics device model | not started | command submission, fences, read-back through the PC5 graphics stack |
| `M15` audio and USB device model | not started | measured audio latency; controller report path |
| `M16` system-services device model | not started | timers, event queues, IPC mailboxes, RTC |
| `M17` security and authenticity boundary | **blocked** | analysis only: the trust anchor is Sony silicon and fuses |
| `M18` genuine system-software load | **blocked** | needs `M17` and a lawful input |
| `M19` genuine system software boots — **the destination** | **blocked** | needs `M18`, plus `M14`–`M16` |
| `M20` genuine runtime and platform experience | **blocked** | needs `M19` |
| `M21` genuine installation via PC5 | **blocked** | needs `M17`, `M18` |

`blocked` in this table means exactly one thing: a precondition exists that the
project cannot satisfy by writing code — and the graph refuses to let it be
mistaken for progress.

## 3. The thirteen chain stages, as the boot environment reports them

The runtime chain in `kernel/chain.c` is unchanged and still honest. Captured
from the emulated boot:

```
 >  1. PC5 boot environment executing          OK
    2. Host hardware inventoried               OK
    3. Controller / input path                 PARTIAL
    4. Storage stack for game installation     PARTIAL
    5. PS5 firmware image supplied             BLOCKED
    6. Firmware format analysed                BLOCKED
    7. Firmware loading pathway implemented    BLOCKED
    8. Firmware initialisation attempted       NOT-STARTED
    9. Firmware executing                      NOT-STARTED
   10. PS5 system services operating           NOT-STARTED
   11. Genuine PS5 home screen                 NOT-STARTED
   12. PS5 game installation                   NOT-STARTED
   13. PS5 game execution                      NOT-STARTED
```

Stages 5–13 are the destination; stage 5 is `BLOCKED` because the payload is not
in the tree and `M17` does not have an answer. Nothing above stage 4 has ever
been attempted, so nothing above stage 4 is claimed.

## 4. What is genuinely working on the PC5 side

| Capability | Status | Evidence |
|---|---|---|
| Bootable ISO (BIOS El Torito + UEFI section + hybrid MBR) | COMPLETE AND VERIFIED | `make verify` VALID, `TEST_RESULTS.md` |
| UEFI boot under genuine OVMF firmware | COMPLETE AND VERIFIED (emulator, prior machine) | `make ovmf-test` 40 pass / 1 N/A, §16 |
| UEFI boot chain executed against stub firmware, including the memory-map and handoff checks | COMPLETE AND VERIFIED (stub) | `make stubtest` 58/58, §18 |
| Loader (A20, E820, VBE, font, ISO probe, kernel load, PM handoff) | COMPLETE AND VERIFIED (emulated) | serial log |
| Framebuffer console, text fallback, read-back self-test | COMPLETE AND VERIFIED (emulated) | diagnostics page |
| CPUID / E820 / PCI / ACPI / SMBIOS inventory | IMPLEMENTED — TESTING REQUIRED | hardware page; no physical run |
| PS/2 keyboard, serial console (19 dispatchable commands) | COMPLETE AND VERIFIED (emulated) / IMPLEMENTED | boot test drives it |
| ATAPI PIO + ISO9660 in the kernel | COMPLETE AND VERIFIED (emulated) | `storage_mounted volume=PC5` |
| MBR + GPT discovery (read-only) | COMPLETE AND VERIFIED (emulated disk + unit tests) | `partitions … status=ok` / `status=crc` |
| FAT32 read, create, overwrite, mkdir, 8.3 paths | COMPLETE AND VERIFIED (emulated disk + host driver) | 50 expand tests, second-boot read-back |
| ATA PIO write with verified read-back | COMPLETE AND VERIFIED (emulated IDE disk) | `storage_write=ok … verify=ok` |
| Reproducible build | COMPLETE AND VERIFIED | two identical builds, pinned `BUILD_ID` |
| Firmware analysis tool (identification only, no decryption) | COMPLETE AND VERIFIED on synthetic containers; ran on the pointer | `tools/firmware.py` |
| APIC / SMP / long mode, NVMe, USB, audio, network | **NOT STARTED** | `M2a`, `M2b`, `M2c`, `M2d` |
| Installer, update slots, recovery entry | **NOT STARTED** | `M8` |
| PS5 platform model | **NOT STARTED** — contract and test doubles only (`M10` acceptance met) | `M11`–`M16` |
| Genuine PS5 system software | **NOT EXECUTED, BLOCKED** | `M17`–`M21` |

## 5. Honest summary

PC5 today is a reproducible, bootable PC environment with real storage and
filesystem work, a verified UEFI path under real firmware, and an honest model of
how far it has to go. It is **not** a PlayStation 5: no Sony code runs, no PS5
game runs, no PS5 system service exists, and the destination workstream has
produced one contract document and one provenance record.

The difference between this page and its predecessor is not optimism: it is that
the destination is now a milestone on the critical path (with a named blocker)
instead of a side track that the build system was configured to keep out.
