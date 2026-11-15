# PS5 compatibility architecture

## 1. Two execution paths, one project

```
                     ┌─────────────────────────────────────────────┐
   firmware path     │ 1 firmware analysis/loading                 │  BLOCKED
   (genuine PS5      │ 2 boot-environment compatibility            │  BLOCKED
    system software) │ 3 SoC execution (CPU / memory / syscalls)   │  NOT STARTED
                     │ 4 graphics / shader / audio / IO emulation  │  NOT STARTED
                     └─────────────────────────────────────────────┘
                     ┌─────────────────────────────────────────────┐
   execution path    │ 5 package/executable intake (FIH/CNT, SELF) │  RESEARCH
   (PS5 binaries on  │ 6 static relinking + PRX shims (AnyPS5)     │  RESEARCH
    a PC host)       │ 7 graphics translation for relinked titles  │  RESEARCH
                     └─────────────────────────────────────────────┘
```

The firmware path and the execution path are reported separately everywhere.
The dependency chain behind the firmware path, and why it cannot be scheduled
today, is assessed in `docs/playstation/BOOT_FEASIBILITY.md`.
in the compatibility chain (stages 5–13 are the firmware path), in
`docs/GAMING_COMPATIBILITY.md`, and in the UI. Neither is ever used as evidence
for the other.

## 2. The eighteen subsystems

Required modules, their interface in this repository, and their real status.
"Interface" means the place a future implementation plugs in — it is not an
implementation.

| # | Subsystem | Interface today | Status |
|---|---|---|---|
| 1 | Firmware analysis and loading | `tools/firmware.py`, `kernel/fw.c`, manifest | ANALYSER COMPLETE, LOADER BLOCKED |
| 2 | Boot and initialisation compatibility | `boot/loader.S` → `struct pc5_boot_info` | IMPLEMENTED FOR THE PC5 ENVIRONMENT |
| 3 | CPU execution / instruction translation | none | NOT STARTED |
| 4 | Memory management | `kernel/mem.c` (E820 accounting); no paging, no address translation | PARTIAL (host side only) |
| 5 | Process / thread management | none (single-threaded ring 0) | NOT STARTED |
| 6 | Kernel / syscall compatibility | none | NOT STARTED |
| 7 | Graphics command translation | framebuffer console only | NOT STARTED |
| 8 | Shader processing and caching | none | NOT STARTED |
| 9 | GPU memory and synchronisation | none | NOT STARTED |
| 10 | Storage and filesystem | kernel ATAPI PIO + ISO9660 read; ATA PIO 512-byte read/write on an explicit device; MBR/GPT discovery; FAT32 read-only (mount/list/read) | PARTIAL — sectors, partition tables and a read-only filesystem exist (emulated only); no filesystem writing, so no install or package storage |
| 11 | Game package / executable handling | `FIH`/`CNT` signature identification only | RESEARCH |
| 12 | Audio | PCI audio class counted only | NOT STARTED |
| 13 | Controller / input translation | 8042 PS/2 keyboard driver behind `kbd_read()` | PARTIAL (keyboard only) |
| 14 | Network and device interfaces | PCI enumeration only | NOT STARTED |
| 15 | Platform services (PSN etc.) | none, and none may bypass authentication | BLOCKED |
| 16 | Runtime diagnostics | Diagnostics page, serial console, `PC5-LOG` | COMPLETE |
| 17 | Compatibility reporting | 13-stage chain in `kernel/chain.c` | COMPLETE |
| 18 | Crash handling and recovery | 32-gate IDT with exception reporting; `reboot`/`halt` | PARTIAL (no fault recovery, only reporting) |

## 3. Design rules that keep the modules replaceable

1. **One boot contract.** `struct pc5_boot_info` is the only interface between
   loader and kernel; its offsets are `_Static_assert`-pinned. A UEFI loader
   fills the same structure.
2. **No subsystem owns the screen.** The UI draws through `con_*`; a subsystem
   reports data, never pixels.
3. **Chain stages are functions of measurements.** `chain_evaluate()` takes
   plain data, so adding a subsystem means adding a stage and its evidence
   string, not editing the UI.
4. **The kernel stays freestanding until a Linux runtime exists.** No libc, no
   allocator; anything needing them belongs to the next-stage runtime.
5. **Reporting is a first-class subsystem.** If a stage is not measured, it says
   NOT-STARTED, and `chain_first_blocked()` names the blocker.

## 4. What "compatibility" means here

An emulated boot is not compatibility, an enumerated device is not a driver, and
a parsed container is not an executed game. The project's vocabulary:

| Word | Means |
|---|---|
| detected | read from firmware tables or CPUID |
| implemented | code exists and is exercised by a test in this repository |
| verified | the test result is recorded in `docs/TEST_RESULTS.md` |
| executed | the actual code of that system ran on real or emulated hardware |
| playable | sustained gameplay measured, recorded per title |

## 5. Next three implementations, in order

1. **FAT32 writing**: create a file, update the FAT and directory entries, flush,
   and prove it with an independent reader. Reading is complete (M1f); without
   writing there is no installer, no update and no package storage. See task B in
   `docs/architecture/FIRST_THREE_TASKS.md`.
2. **isohybrid medium**: the same ISO booting as a USB hard disk, which removes a
   distribution blocker without new kernel code (task C there).
3. **UEFI under real firmware**: the loader exists and is executed against stub
   firmware; it needs an OVMF or hardware run before UEFI machines can be called
   supported (M1c in `docs/architecture/roadmap.json`). This is an environment
   blocker, not an engineering one.
