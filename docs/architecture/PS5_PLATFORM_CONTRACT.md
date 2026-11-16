# PS5 platform contract — what PC5 has to present to the genuine system software

| | |
|---|---|
| Status | **Experimental specification, revision r1 (2026-11-16).** Every `M10` acceptance item is met and evidenced; the milestone stays `in_progress` because a level-4 milestone may not be `complete` while only levels 1–2 have evidence. This document is not evidence that any Sony software has ever run on PC5. |
| Milestone | `M10` (this document); `M11` and `M17` depend on it directly, and `M12`–`M16` and `M18`–`M21` inherit it transitively |
| Implementation | `psm/` — the registry, the rule engine and the ten PC5-authored doubles |
| Tests | `tests/test_psm_registry.py`, `tests/test_psm_conformance.py`, `tests/test_psm_mutations.py`, `tests/test_psm_document.py` |
| Decisions | ADR-019 (destination), ADR-022 (protected input), ADR-024 (this contract is a registry, and UNKNOWN is a refusal) |
| Related | [`../PS5_SYSTEM_SOFTWARE_TARGET.md`](../PS5_SYSTEM_SOFTWARE_TARGET.md), [`PC5_ARCHITECTURE_SPEC_v2.md`](PC5_ARCHITECTURE_SPEC_v2.md) §6, [`../../roadmap.json`](roadmap.json) `M10` |

## 1. What this document is, and what it is not

PC5's destination is unchanged and is not re-interpreted here: run **Sony's genuine,
original PS5 system software** on compatible PC hardware, through PC5's own platform.
It is not a Linux distribution with a PS5 theme, not a recreation of the PS5 user
interface, not a replacement operating system that resembles one, and not a generic
emulator that quietly substitutes its own software for Sony's.

Before a device model can be built, something has to say what the model must present
and how anyone would know whether it does. That is this document. It is a
*contract*: for each subsystem, what is publicly established about the console, what
PC5 assumes on top of that, what nobody knows, what PC5 will therefore implement, and
what a PC5-authored test double must do so that the layer above can be built and
tested before the real thing exists.

Three limits are stated here rather than discovered later:

1. **Nothing in this repository has ever executed Sony's system software.** No claim
   of compatibility, loading, inspection or partial boot of genuine PS5 software is
   made anywhere in this document, the registry or the tests. See §7.
2. **A published console specification is not a register map.** The 2020 specification
   table establishes a CPU family, a GPU family, a memory capacity and a storage
   bandwidth. It establishes no register layout, no firmware interface, no command
   encoding, no allocation policy, no boot protocol and no security behaviour. Rows
   that would need those are `UNKNOWN`, and `UNKNOWN` is implemented as a refusal.
3. **The doubles are PC5's, not Sony's.** `PSM-CPU` is a PC5-authored Python object.
   It is not a PS5 CPU, it does not execute guest code, and a suite passing against it
   says nothing about whether Sony's software would accept the interface.

The document is generated in part. Every table below sits between
`<!-- psm:begin … -->` markers and is rendered from `psm/registry.py` and `psm/*.py`
by `python3 -m psm.render --write`. `tests/test_psm_document.py` fails the build when
a block and its renderer disagree, so the prose cannot drift from the data. Prose is
hand-written; data is not.

## 2. Source discipline

Four classifications, and only four. The registry refuses any other value.

| Marker | Means | May be implemented? |
|---|---|---|
| `PUBLIC` | a public primary source states it, cited with publisher, title, date, URL and location | yes, and a conformance case asserts the PC5 side |
| `INFERRED` | it follows from public facts plus an assumption written into the claim | yes, and the assumption is named in the claim |
| `UNKNOWN` | nothing public supports a value or a behaviour | **no** — the double returns `UNSUPPORTED`, never a plausible value |
| `RESTRICTED` | reaching it would require circumvention | **no, ever** (ADR-007, ADR-022) |

Two further fields keep this honest rather than decorative. Every claim records
`establishes` — whether the source states the thing directly or merely supports an
inference — and `uncertainty`, which must be non-empty and, for `UNKNOWN`, must say
*why* nothing public supports it. A claim also records the engineering decision that
depends on it and the conformance case that exercises PC5's side; a claim with no
decision does not belong in a contract and the rule engine rejects it.

Sources are marked `verified` (fetched and read in the environment that wrote this),
`located` (publisher, title, revision and URL confirmed; body not read here) or
`not-verified`. `PUBLIC` requires a `verified` or `located` source. The retrieval
date for every row is 2026-11-16.

<!-- psm:begin sources -->
| ID | Title | Publisher | Date | URL | Verification |
|---|---|---|---|---|---|
| `S-SONY-2020` | Unveiling New Details of PlayStation 5: Hardware Technical Specs [UPDATED] | Sony Interactive Entertainment (PlayStation.Blog) | 2020-03-18 (updated 2020-03-20) | [blog.playstation.com/2020/03/18/unveiling-new-details...](https://blog.playstation.com/2020/03/18/unveiling-new-details-of-playstation-5-hardware-technical-specs/) | verified |
| `S-AMD-APM-V1` | AMD64 Architecture Programmer's Manual Volume 1: Application Programming (publication 24592, revision 3.25) | Advanced Micro Devices, Inc. | May 2026 | [docs.amd.com/v/u/en-US/24592_3.25_APM_Vol1_PUB](https://docs.amd.com/v/u/en-US/24592_3.25_APM_Vol1_PUB) | located |
| `S-UEFI` | UEFI Specifications (UEFI Specification 2.11, released December 2024; ACPI Specification 6.6, released May 2025) | Unified Extensible Firmware Interface Forum | 2024-12 (UEFI 2.11); 2025-05 (ACPI 6.6) | [uefi.org/specifications](https://uefi.org/specifications) | verified |
| `S-NVME` | NVM Express Base Specification, Revision 2.4 (ratified 2026-07-31) | NVM Express, Inc. | 2026-08-04 (index page) | [nvmexpress.org/specifications/](https://nvmexpress.org/specifications/) | verified |
<!-- psm:end sources -->

What the four sources establish, and what they do not:

* **`S-SONY-2020`** is the only primary statement of console capabilities used here.
  It gives the CPU family, core/thread count, variable clock, GPU family with ray
  tracing and variable clock, 16 GB GDDR6 with 448 GB/s, an 825 GB SSD at 5.5 GB/s raw
  read, UHD Blu-ray, HDMI 2.1 output modes and "Tempest" 3D audio. It does **not**
  mention input devices, the interrupt controller, timers, the service layer, the
  message transport, the file-system layout, GPU command encoding or anything about
  authentication. Claims about those are `UNKNOWN`, and the corresponding operations
  are refusals.
* **`S-AMD-APM-V1`** is the authority for the AMD64 architectural model — exception
  vectors, MSR numbers, CPUID leaves, SVM. It is `located`, not `verified`: the
  document was identified with publisher, revision and URL, but its body was not read
  in this environment. Consequence: claims that rest on it are `INFERRED`, not
  `PUBLIC`, and they say so. Reading the volume is the first task of `M11`.
* **`S-UEFI`** and **`S-NVME`** establish the standards PC5's firmware and storage
  layers already implement elsewhere in this repository. They are cited for the
  *standard* semantics only. A standards-conformant NVMe queue is not evidence about
  the console's storage path, and the registry keeps those two apart.
* The previous revision of this document cited a Sony "tech specs" page. That page
  now returns 404 and is not cited anywhere; the PlayStation.Blog post of 2020-03-18
  (updated 2020-03-20) is used instead. Secondary reporting that repeats Sony's table
  is not cited as a source at all.

Registry totals, which the document cannot override:

<!-- psm:begin summary -->
| Classification | Claims |
|---|---|
| PUBLIC | 6 |
| INFERRED | 9 |
| UNKNOWN | 21 |
| RESTRICTED | 2 |
| **total** | **38** |
<!-- psm:end summary -->

## 3. Subsystem contracts

Seven required subsystems, one section each. Each section states what PC5 must
present, what is publicly established, what PC5 assumes, what is unknown, and the
interface and invariants of the corresponding double.

### 3.1 CPU and privileged execution (`M11`)

What PC5 must present: an x86-64 execution environment whose privilege model,
exception delivery, CPUID surface and MSR visibility are defined, so that system
software which inspects the machine gets answers that are either true to the contract
or explicitly refused.

What is public: the CPU is an x86-64 AMD Ryzen "Zen 2" part with 8 cores and 16
threads at a variable frequency up to 3.5 GHz. The frequency is *variable* and shared
with the GPU's power budget: the model must never assume a fixed clock, and a guest
that measures frequency must be able to see it change.

What PC5 assumes (`INFERRED`): that the console implements the AMD64 architectural
exception model, i.e. vectors 0–31 with the error-code behaviour AMD64 defines, and
that AMD SVM exists on the silicon. Both are assumptions about a *family*, recorded
with the assumption attached, not observations of a console.

What is unknown: the console-specific CPUID leaf space beyond the documented leaves,
the console's own MSR usage and any security-relevant MSR values, and whether the
console's system software enables or expects hardware virtualisation. The previous
revision of this document listed "hypervisor present" as an `INFERRED` row. It is
not supported: whether the guest runs under a hypervisor, and what it does with SVM,
is unknown, and the row is now `UNKNOWN`. A guest asking about it gets
`guest_usage == "unknown"`.

The registry:

<!-- psm:begin claims-cpu -->
| ID | Claim | Class | Source | Confidence | Remaining uncertainty | Conformance |
|---|---|---|---|---|---|---|
| `C-CPU-01` | The CPU is an x86-64 AMD Ryzen "Zen 2" with 8 cores / 16 threads at a variable frequency of up to 3.5 GHz | **PUBLIC** | `S-SONY-2020` | high: first-party specification table | the actual frequency at any moment, and the boost policy, are not public | PSM-CPU capabilities |
| `C-CPU-02` | The CPU frequency is variable rather than fixed | **PUBLIC** | `S-SONY-2020` | high: first-party specification table | what determines the current frequency (load, power, thermal) is not public | PSM-CPU capabilities |
| `C-CPU-03` | The CPU implements the AMD64 architectural exception model (vectors 0-31 with architectural error-code behaviour) | **INFERRED** | `S-AMD-APM-V1` | medium: architecture-level, not console-specific | whether the console redirects or filters any vector before the system software sees it is not public | PSM-CPU exception |
| `C-CPU-04` | The console-specific CPUID leaf space beyond the documented leaves | **UNKNOWN** | — (no public source) | n/a | nothing public documents the console's leaf space, its vendor string or the feature bits it advertises | PSM-CPU cpuid (UNSUPPORTED path) |
| `C-CPU-05` | The console's own MSR usage and any security-relevant MSR values | **UNKNOWN** | — (no public source) | n/a | no public source describes the console's MSR programming or the values it relies on | PSM-CPU msr_read (DENIED and UNSUPPORTED paths) |
| `C-CPU-06` | Hardware-assisted virtualisation (AMD SVM) is available to the platform | **INFERRED** | `S-AMD-APM-V1`, `S-SONY-2020` | low-medium: the ISA is public, the console's configuration is not | whether the system software uses SVM, and whether nested virtualisation is available beneath it, are not public | PSM-CPU virtualisation |
| `C-CPU-07` | Whether the console's system software enables or expects hardware virtualisation | **UNKNOWN** | — (no public source) | n/a | no public source describes the guest's use of SVM | PSM-CPU execute_payload (UNSUPPORTED until M11) |
<!-- psm:end claims-cpu -->

The interface:

<!-- psm:begin interfaces-PSM-CPU -->
| Operation | Behaviour | Inputs | Outputs | Documented failures | Status | Claim |
|---|---|---|---|---|---|---|
| `capabilities` | Report the CPU surface PC5 presents to a guest | — | `isa, cores, threads, modes, privilege_levels, clock_policy, documented_leaves, unknowns` | — | implemented | `C-CPU-01`, `C-CPU-02` |
| `cpuid` | Return the four architectural registers for a CPUID leaf | `leaf`, `subleaf` | `eax, ebx, ecx, edx` | `invalid_argument`, `unsupported` | implemented | `C-CPU-03` |
| `msr_read` | Read one MSR from the exposed set | `address` | `value` | `invalid_argument`, `unsupported`, `denied` | implemented | `C-CPU-04` |
| `exception` | Describe how one architectural exception is delivered | `vector` | `name, error_code, pc5_action` | `invalid_argument`, `unsupported` | implemented | `C-CPU-05` |
| `virtualisation` | Report whether AMD SVM is presented, and to whom | — | `svm_presented, nested, guest_usage` | — | implemented | `C-CPU-06` |
| `reset` | Return the double to its power-on state | — | `reset_epoch` | — | implemented | — |
| `execute_payload` | Run a PC5-authored conformance payload (M11 deliverable) | `payload` | `none` | `unsupported` | **UNSUPPORTED by design** | `C-CPU-07` |
<!-- psm:end interfaces-PSM-CPU -->

<!-- psm:begin invariants-PSM-CPU -->
| Operation | Invariant the conformance suite checks |
|---|---|
| `capabilities` | cores and threads are positive<br>every entry in unknowns is also UNKNOWN in the contract |
| `cpuid` | a returned leaf carries all four registers<br>an uncovered leaf returns UNSUPPORTED and no values |
| `msr_read` | security-state MSRs are DENIED, never zero<br>unknown MSRs are UNSUPPORTED |
| `exception` | vectors outside 0..31 are INVALID_ARGUMENT<br>vectors with no public description are UNSUPPORTED |
| `virtualisation` | guest_usage is 'unknown' - the contract does not know it<br>svm_presented is a PC5 choice, not a console fact |
| `reset` | mutable state is cleared<br>the epoch increments |
| `execute_payload` | returns UNSUPPORTED until M11 builds the execution model |
<!-- psm:end invariants-PSM-CPU -->

Two refusals are deliberately different. `msr_read` returns `DENIED` for the
security-state MSRs (`0xC0010015`, `0xC0010114`) and `UNSUPPORTED` for an address
outside the exposed set. Collapsing them would tell a guest that a register it may not
read does not exist, which is a different — and false — statement. This distinction
is ADR-007's and is asserted by the suite.

`execute_payload` is the honest hole in the double: `M11` is where a PC5-authored
conformance payload executes to a recorded completion. Until then the operation is
documented and returns `UNSUPPORTED`.

### 3.2 Memory (`M12`)

What PC5 must present: one address space in which the CPU and the GPU see the same
bytes, with defined ownership, alignment, sharing, translation and failure behaviour.

What is public: 16 GB of GDDR6 at 448 GB/s.

What PC5 assumes (`INFERRED`): that this is a single unified pool the CPU and GPU
address directly — the natural reading of GDDR6 as system memory in the 2020 post —
and that GPU-visible memory is presented to the guest as a mapping of memory it
already owns rather than as a copy.

What is unknown: the console's allocation policy, protection domains and isolation
between guest components, and cache coherence and flush semantics for GPU-visible
memory. Capacity and bandwidth are *not* turned into allocation rules here: "16 GB at
448 GB/s" says nothing about block sizes, granularity, NUMA-like behaviour or what
happens under pressure. A previous revision implied a measured 448 GB/s on PC
hardware; nothing was measured, and no such statement survives.

The registry:

<!-- psm:begin claims-memory -->
| ID | Claim | Class | Source | Confidence | Remaining uncertainty | Conformance |
|---|---|---|---|---|---|---|
| `C-MEM-01` | System memory is 16 GB of GDDR6 with 448 GB/s bandwidth | **PUBLIC** | `S-SONY-2020` | high: first-party specification table | the split of usable memory and what the system software reserves are not public | PSM-MEM topology |
| `C-MEM-02` | The memory is one pool both the CPU and the GPU address directly | **INFERRED** | `S-SONY-2020` | medium-high: implied by the specification, not stated as an architecture | the address ranges each engine may access, and any carve-outs, are not public | PSM-MEM allocate / share |
| `C-MEM-03` | GPU-visible memory is presented to the guest as a mapping of shared memory | **INFERRED** | `S-SONY-2020` | medium | the mapping granularity and the guest-visible mapping interface are not public | PSM-MEM translate |
| `C-MEM-04` | The allocation policy the console's system software uses | **UNKNOWN** | — (no public source) | n/a | no public source describes allocation granularity, fragmentation policy or what the system software reserves for itself | PSM-MEM allocate (EXHAUSTED path) |
| `C-MEM-05` | Protection domains and isolation between guest components | **UNKNOWN** | — (no public source) | n/a | nothing public describes the console's protection model or its capabilities | PSM-MEM protection_domain (UNSUPPORTED) |
| `C-MEM-06` | Cache coherence and flush semantics for GPU-visible memory | **UNKNOWN** | — (no public source) | n/a | no public source states the coherence model or the operations the system software must perform | PSM-MEM share |
<!-- psm:end claims-memory -->

The interface:

<!-- psm:begin interfaces-PSM-MEM -->
| Operation | Behaviour | Inputs | Outputs | Documented failures | Status | Claim |
|---|---|---|---|---|---|---|
| `topology` | Report the memory pool PC5 presents | — | `pool_bytes, engines, coherent, unknowns` | — | implemented | `C-MEM-01`, `C-MEM-02` |
| `allocate` | Own a range of the pool and return a handle | `size`, `alignment`, `purpose`, `engine` | `handle, device_address, size` | `invalid_argument`, `exhausted`, `denied` | implemented | `C-MEM-03`, `C-MEM-04` |
| `release` | Give an allocation back to the pool | `handle` | `freed_bytes` | `ownership` | implemented | `C-MEM-05` |
| `translate` | Turn an offset inside an allocation into a device address | `handle`, `offset` | `device_address` | `ownership`, `invalid_argument` | implemented | `C-MEM-05` |
| `share` | Make an allocation visible to another engine | `handle`, `engine` | `engines` | `ownership`, `invalid_argument` | implemented | `C-MEM-06` |
| `stats` | Pool accounting as the guest would see it | — | `pool_bytes, allocated_bytes, live_handles, largest_free_bytes` | — | implemented | — |
| `protection_domain` | Create a guest protection domain | `name` | `none` | `unsupported` | **UNSUPPORTED by design** | `C-MEM-05` |
<!-- psm:end interfaces-PSM-MEM -->

<!-- psm:begin invariants-PSM-MEM -->
| Operation | Invariant the conformance suite checks |
|---|---|
| `topology` | the pool is one address space both engines see |
| `allocate` | the device address is aligned to the requested alignment<br>two live handles never overlap<br>EXHAUSTED leaves no allocation behind |
| `release` | a released handle is never released again<br>an unknown handle is OWNERSHIP, not a silent success |
| `translate` | the result stays inside the allocation |
| `share` | sharing is idempotent<br>sharing never copies: the handle's address does not change |
| `stats` | allocated_bytes never exceeds pool_bytes |
| `protection_domain` | returns UNSUPPORTED: the console's protection model is UNKNOWN |
<!-- psm:end invariants-PSM-MEM -->

Allocation is first-fit with an explicit `EXHAUSTED`, and sharing is idempotent and
does not move the memory: a device address obtained once stays valid while the handle
lives. `protection_domain` is `UNSUPPORTED` because the console's isolation model is
`UNKNOWN`; a fabricated domain handle would be worse than a refusal, since isolation
is exactly the property a security argument would rest on.

### 3.3 Interrupts, timers and system services (`M16`)

What PC5 must present: an event-delivery mechanism, a clock and timer facility with a
real-time-clock path, and a message transport — each with defined ordering, ownership
and timeout behaviour.

What is public: nothing about the console's interrupt controller, its timer
interfaces, its RTC behaviour, its message transport or its service layer. All of it
is `UNKNOWN`. The 2020 post does not mention any of these.

What PC5 does instead of guessing: it defines **three separate doubles** — `PSM-IRQ`,
`PSM-TMR`, `PSM-IPC` — rather than one merged "system interface". Merging them would
manufacture precisely the fictional hardware interface this contract forbids, and
would hide the fact that the boundary between them on the console is unknown. `M16`
owns all three as one workstream; the interface count is ten, the required group count
is eight, and §5 states the mapping explicitly.

The registry:

<!-- psm:begin claims-interrupts -->
| ID | Claim | Class | Source | Confidence | Remaining uncertainty | Conformance |
|---|---|---|---|---|---|---|
| `C-IRQ-01` | The console's interrupt controller and its programming interface | **UNKNOWN** | — (no public source) | n/a | no public source describes the controller, its register layout or its delivery model | PSM-IRQ controller_info |
| `C-IRQ-02` | Whether, and how, the console delivers interrupts to the system software | **UNKNOWN** | — (no public source) | n/a | nothing public describes the guest-visible interrupt path | PSM-IRQ claim / raise / take |
| `C-TMR-01` | The console's timer and clock interfaces | **UNKNOWN** | — (no public source) | n/a | no public source describes the timer width, resolution or the clock the system software reads | PSM-TMR now / schedule |
| `C-TMR-02` | Real-time clock behaviour on the console | **UNKNOWN** | — (no public source) | n/a | no public source describes how the system software reads wall-clock time | PSM-TMR rtc_read |
| `C-IPC-01` | The console's inter-process message transport | **UNKNOWN** | — (no public source) | n/a | no public source describes the IPC mechanism, its queue depths or its failure semantics | PSM-IPC send / recv |
<!-- psm:end claims-interrupts -->

<!-- psm:begin claims-services -->
| ID | Claim | Class | Source | Confidence | Remaining uncertainty | Conformance |
|---|---|---|---|---|---|---|
| `C-SVC-01` | The console's service layer and the interface it exposes to the system software | **UNKNOWN** | — (no public source) | n/a | no public source describes the service model, its names or its ABI | PSM-SVC service_lookup |
| `C-SVC-02` | The console's logging and diagnostic interfaces | **UNKNOWN** | — (no public source) | n/a | nothing public describes them | PSM-SVC log_write (OVERFLOW path) |
<!-- psm:end claims-services -->

The interfaces:

<!-- psm:begin interfaces-PSM-IRQ -->
| Operation | Behaviour | Inputs | Outputs | Documented failures | Status | Claim |
|---|---|---|---|---|---|---|
| `claim` | Bind a vector to a PC5-owned handler slot | `vector` | `vector, slot` | `invalid_argument`, `exhausted`, `bad_state` | implemented | `C-IRQ-01` |
| `raise` | Deliver an interrupt on a claimed vector | `vector` | `pending` | `invalid_argument`, `bad_state` | implemented | `C-IRQ-02` |
| `take` | Take the next pending delivery | — | `vector, sequence` | `not_ready` | implemented | `C-IRQ-02` |
| `release` | Unbind a vector | `vector` | `dropped` | `bad_state` | implemented | `C-IRQ-02` |
| `controller_info` | Report what PC5 presents instead of the console's controller | — | `model, vectors, unknowns` | — | implemented | `C-IRQ-01` |
<!-- psm:end interfaces-PSM-IRQ -->

<!-- psm:begin invariants-PSM-IRQ -->
| Operation | Invariant the conformance suite checks |
|---|---|
| `claim` | a vector is claimed once<br>re-claiming the same vector is BAD_STATE |
| `raise` | raising an unclaimed vector is BAD_STATE, never a silent drop<br>deliveries are counted, not coalesced silently |
| `take` | nothing pending is NOT_READY, not vector 0<br>delivery order is first-raised-first-taken |
| `release` | releasing an unclaimed vector is BAD_STATE<br>pending deliveries for a released vector are counted and dropped |
| `controller_info` | the returned model describes PC5's own abstraction, not console hardware |
<!-- psm:end invariants-PSM-IRQ -->

<!-- psm:begin interfaces-PSM-TMR -->
| Operation | Behaviour | Inputs | Outputs | Documented failures | Status | Claim |
|---|---|---|---|---|---|---|
| `now` | Monotonic nanoseconds since platform start | — | `nanoseconds` | — | implemented | `C-TMR-01` |
| `schedule` | Arm a deadline that raises a claimed vector | `deadline`, `vector` | `deadline, vector` | `invalid_argument`, `bad_state` | implemented | `C-TMR-02` |
| `advance` | Advance the virtual clock and fire due deadlines | `nanoseconds` | `fired, now` | `invalid_argument` | implemented | `C-TMR-02` |
| `rtc_read` | Read the real-time clock as configured by the platform | — | `seconds, source` | — | implemented | `C-TMR-02` |
<!-- psm:end interfaces-PSM-TMR -->

<!-- psm:begin invariants-PSM-TMR -->
| Operation | Invariant the conformance suite checks |
|---|---|
| `now` | the value never decreases |
| `schedule` | a deadline in the past is INVALID_ARGUMENT<br>a vector that is not claimed is BAD_STATE<br>deadlines fire in order |
| `advance` | fired deadlines are removed<br>the clock never runs backwards |
| `rtc_read` | the source is named, so a synthetic clock is never mistaken for a firmware RTC |
<!-- psm:end invariants-PSM-TMR -->

<!-- psm:begin interfaces-PSM-IPC -->
| Operation | Behaviour | Inputs | Outputs | Documented failures | Status | Claim |
|---|---|---|---|---|---|---|
| `create` | Create a named queue of a given depth | `name`, `depth` | `name, depth` | `invalid_argument`, `bad_state` | implemented | `C-IPC-01` |
| `send` | Enqueue one message | `name`, `message` | `depth_used` | `no_device`, `overflow`, `invalid_argument` | implemented | `C-IPC-01` |
| `recv` | Dequeue one message, waiting on the virtual clock | `name`, `timeout` | `message, waited` | `no_device`, `timeout`, `invalid_argument` | implemented | `C-IPC-01` |
| `destroy` | Remove a queue and count what it still held | `name` | `discarded` | `no_device` | implemented | `C-IPC-01` |
<!-- psm:end interfaces-PSM-IPC -->

<!-- psm:begin invariants-PSM-IPC -->
| Operation | Invariant the conformance suite checks |
|---|---|
| `create` | a duplicate name is BAD_STATE<br>depth is 1..1024 |
| `send` | a full queue is OVERFLOW, never a silent drop<br>an unknown queue is NO_DEVICE |
| `recv` | an empty queue after the timeout is TIMEOUT |
| `destroy` | messages still queued are counted, not hidden |
<!-- psm:end invariants-PSM-IPC -->

And the service layer itself, which is a fourth interface in the same `M16` group:

<!-- psm:begin interfaces-PSM-SVC -->
| Operation | Behaviour | Inputs | Outputs | Documented failures | Status | Claim |
|---|---|---|---|---|---|---|
| `clock_get` | Read the platform service clock | — | `nanoseconds, source` | — | implemented | `C-SVC-01` |
| `sleep` | Advance the virtual clock by a duration | `nanoseconds` | `now` | `invalid_argument` | implemented | `C-SVC-01` |
| `log_write` | Append one structured log record | `level`, `message` | `records, dropped` | `invalid_argument`, `overflow` | implemented | `C-SVC-02` |
| `log_read` | Read up to N records, oldest first | `count` | `records` | `invalid_argument` | implemented | `C-SVC-02` |
| `service_register` | Register a PC5 service name and ABI version | `name`, `version` | `name, version` | `invalid_argument`, `bad_state` | implemented | `C-SVC-01` |
| `service_lookup` | Find a registered service | `name` | `name, version` | `no_device` | implemented | `C-SVC-01` |
| `panic` | Stop the platform with a reason | `reason` | `stopped` | `invalid_argument` | implemented | `C-SVC-01` |
<!-- psm:end interfaces-PSM-SVC -->

<!-- psm:begin invariants-PSM-SVC -->
| Operation | Invariant the conformance suite checks |
|---|---|
| `clock_get` | the clock never runs backwards |
| `sleep` | a negative duration is INVALID_ARGUMENT |
| `log_write` | an unknown level is INVALID_ARGUMENT<br>a full ring is OVERFLOW and is counted, never silently dropped |
| `log_read` | reading does not reorder or invent records |
| `service_register` | re-registering a name is BAD_STATE |
| `service_lookup` | an unknown service is NO_DEVICE, not an empty record |
| `panic` | after a panic every other call is BAD_STATE |
<!-- psm:end invariants-PSM-SVC -->

The clock is virtual and monotonic: nothing in the suite sleeps, and a timeout is a
documented advance of the virtual clock followed by `TIMEOUT`. That is what makes
deterministic replay possible (§9) and what keeps the suite runnable in CI. Every
clock answer names its source, so a caller can tell PC5's virtual time from a machine
reading — and the RTC op says, in its returned value, that it is not a read of the
machine.

### 3.4 Storage and I/O (`M13`)

What PC5 must present: a device that accepts submissions, completes them with tags,
reports durability only after a flush, and refuses what it cannot honour.

What is public: an 825 GB SSD with 5.5 GB/s raw read bandwidth.

What PC5 assumes (`INFERRED`): that the storage path is queue-based with tagged
submission and completion, that a write is durable only after an explicit flush, and
that the path includes a dedicated decompression stage. Each is an assumption about
how a modern NVMe-class device with a custom I/O complex behaves, recorded with the
assumption attached.

What is unknown: the decompression hardware's programming interface, and the on-disk
layout the console's software expects. The registry keeps "the stage exists"
(`INFERRED`) apart from "here is how to program it" (`UNKNOWN`, and `decompress`
returns `UNSUPPORTED`). No file-system layout and no installation format is invented;
`guest_layout` is a refusal, and `M21` owns the day it stops being one.

The registry:

<!-- psm:begin claims-storage -->
| ID | Claim | Class | Source | Confidence | Remaining uncertainty | Conformance |
|---|---|---|---|---|---|---|
| `C-STO-01` | The console has an 825 GB SSD with 5.5 GB/s raw read bandwidth | **PUBLIC** | `S-SONY-2020` | high: first-party specification table | the usable capacity, the controller's command set and the on-disk layout are not public | PSM-STOR present |
| `C-STO-02` | The storage device uses submission/completion queues with tags | **INFERRED** | `S-NVME`, `S-SONY-2020` | medium | whether the controller is strictly NVMe, and what it adds, is not public | PSM-STOR submit / complete |
| `C-STO-03` | Writes become durable only after an explicit flush | **INFERRED** | `S-NVME` | medium-high for a standard device; unknown for the console's controller | the console's own flush and persistence guarantees are not public | PSM-STOR flush |
| `C-STO-04` | The I/O path includes a dedicated decompression stage | **INFERRED** | `S-SONY-2020` | medium: the post names a custom I/O system, not its programming interface | the decompression hardware's interface, formats and ownership rules are not public | not modelled — M13 owns the decompression stage; PSM-STOR models queues and durability and refuses only the stage's interface |
| `C-STO-05` | The on-disk layout the console's system software expects | **UNKNOWN** | — (no public source) | n/a | nothing public describes the layout, and PC5 will not infer it from a device it cannot open | PSM-STOR guest_layout (UNSUPPORTED) |
| `C-STO-06` | The programming interface of the console's decompression hardware | **UNKNOWN** | — (no public source) | n/a | no public document describes the decompression block's registers, queues, formats or completion semantics; the 2020 post names the stage and stops there | PSM-STOR decompress (UNSUPPORTED) |
<!-- psm:end claims-storage -->

The interface:

<!-- psm:begin interfaces-PSM-STOR -->
| Operation | Behaviour | Inputs | Outputs | Documented failures | Status | Claim |
|---|---|---|---|---|---|---|
| `present` | Present a block device with a capacity and queue depth | `device`, `device_class`, `capacity_bytes`, `queue_depth` | `device, queue_id, namespaces` | `invalid_argument`, `bad_state` | implemented | `C-STO-01` |
| `submit` | Submit one read or write and get a completion tag | `device`, `queue_id`, `op`, `lba`, `sectors`, `owner` | `tag, completion` | `no_device`, `invalid_argument`, `bad_state`, `ownership` | implemented | `C-STO-02`, `C-STO-03` |
| `complete` | Fetch and retire a completion by tag | `device`, `tag`, `owner` | `status, sectors, latency_ns` | `no_device`, `bad_state`, `ownership` | implemented | `C-STO-03` |
| `flush` | Flush writes acknowledged before this call | `device`, `queue_id` | `flushed_sectors` | `no_device`, `invalid_argument` | implemented | `C-STO-04` |
| `health` | Report device health as the guest would see it | `device` | `device, wear, errors, present` | `no_device` | implemented | `C-STO-05` |
| `decompress` | Use the console's I/O decompression path | `device`, `compressed`, `algorithm` | `none` | `unsupported` | **UNSUPPORTED by design** | `C-STO-06` |
| `guest_layout` | Present the on-disk layout the console's system software expects | — | `none` | `unsupported` | **UNSUPPORTED by design** | `C-STO-05` |
<!-- psm:end interfaces-PSM-STOR -->

<!-- psm:begin invariants-PSM-STOR -->
| Operation | Invariant the conformance suite checks |
|---|---|
| `present` | presenting the same device twice is BAD_STATE<br>capacity and depth are positive |
| `submit` | submit before present is NO_DEVICE<br>an LBA past the namespace is INVALID_ARGUMENT<br>a completed tag is not reused |
| `complete` | completing a tag twice is BAD_STATE<br>completing another owner's tag is OWNERSHIP |
| `flush` | flush reports what it made durable, not what it hopes |
| `health` | an absent device is NO_DEVICE |
| `decompress` | returns UNSUPPORTED: the block's programming interface is UNKNOWN |
| `guest_layout` | returns UNSUPPORTED: the layout is UNKNOWN and is M21 work |
<!-- psm:end invariants-PSM-STOR -->

The queue model is deliberately narrow: one namespace per device, a queue depth the
caller declares at presentation time, per-tag ownership, and queue-depth exhaustion as
`BAD_STATE` rather than a silent retry. `health` reports wear as `"unknown"`: SMART-
style figures are device-specific and inventing one would be a fabricated measurement.
A device that is not present is `NO_DEVICE`, not an empty success.

### 3.5 Graphics (`M14`)

What PC5 must present: a submission and presentation path with contexts, fences,
surface ownership and defined error behaviour — and a hard boundary where translation
of Sony's command stream would begin.

What is public: the GPU is an AMD Radeon RDNA 2-based engine with ray tracing
acceleration and a variable clock up to 2.23 GHz (10.3 TFLOPS). Output is HDMI 2.1
class. The 36-compute-unit figure used elsewhere in PC5 is arithmetic
(10.3 TFLOPS ÷ (2.23 GHz × 128 lanes) ≈ 36), not a published number: it is
`INFERRED`, it is reported by `info` as a derivation, and it is never presented to a
guest as a count it can rely on.

What is unknown: the GPU command encoding the system software submits, and the GPU
firmware interface together with its authentication. The second is `RESTRICTED`, not
merely unknown: obtaining it would require circumvention, so it is recorded so that
the refusal is explicit and testable, and no part of it is specified or implemented.

The registry:

<!-- psm:begin claims-graphics -->
| ID | Claim | Class | Source | Confidence | Remaining uncertainty | Conformance |
|---|---|---|---|---|---|---|
| `C-GFX-01` | The GPU is an AMD Radeon RDNA 2-based engine with ray tracing acceleration at a variable frequency of up to 2.23 GHz (10.3 TFLOPS) | **PUBLIC** | `S-SONY-2020` | high: first-party specification table | the shader ISA the system software submits, and the command encoding, are not public | PSM-GFX info |
| `C-GFX-02` | The GPU has 36 compute units | **INFERRED** | `S-SONY-2020` | medium: arithmetic on a first-party number, not a published CU count | the published TFLOPS is rounded, and the console may reserve CUs; the exact count is not in the specification table | PSM-GFX info (reports the derivation, not a count) |
| `C-GFX-03` | The GPU command encoding the system software submits | **UNKNOWN** | — (no public source) | n/a | no public source describes the command stream, its packets or its synchronisation primitives | PSM-GFX submit (UNSUPPORTED) |
| `C-GFX-04` | The GPU's firmware interface and its authentication | **RESTRICTED** | — (no public source) | n/a | not public, and reaching it would require circumvention, which PC5 does not perform (ADR-007) | PSM-GFX gpu_firmware (UNSUPPORTED) |
<!-- psm:end claims-graphics -->

The interface:

<!-- psm:begin interfaces-PSM-GFX -->
| Operation | Behaviour | Inputs | Outputs | Documented failures | Status | Claim |
|---|---|---|---|---|---|---|
| `info` | Report the graphics device as the contract describes it | — | `engine, ray_tracing, clock_policy, unknowns` | — | implemented | `C-GFX-01`, `C-GFX-02` |
| `create_context` | Create a PC5-owned graphics context | `owner` | `context, surface` | `invalid_argument`, `exhausted` | implemented | — |
| `destroy_context` | Destroy a context and release its fences and surface | `context`, `owner` | `fences_released, surface_released` | `ownership` | implemented | — |
| `create_fence` | Create a fence that a modelled submission can signal | `context`, `owner` | `fence` | `ownership`, `exhausted` | implemented | — |
| `signal_fence` | Signal a fence (modelled completion, not a GPU event) | `fence`, `owner` | `signalled_at` | `ownership`, `bad_state` | implemented | — |
| `wait_fence` | Wait for a fence on the virtual clock | `fence`, `timeout` | `waited` | `ownership`, `timeout`, `invalid_argument` | implemented | `C-GFX-03` |
| `present` | Present a PC5-owned surface to the display | `surface`, `owner` | `sequence` | `ownership`, `invalid_argument` | implemented | — |
| `submit` | Submit a guest command buffer to the modelled GPU | `context`, `commands`, `owner` | `none` | `unsupported` | **UNSUPPORTED by design** | `C-GFX-03` |
| `gpu_firmware` | Talk to the GPU firmware interface | `message` | `none` | `unsupported` | **UNSUPPORTED by design** | `C-GFX-04` |
<!-- psm:end interfaces-PSM-GFX -->

<!-- psm:begin invariants-PSM-GFX -->
| Operation | Invariant the conformance suite checks |
|---|---|
| `info` | the unknowns list names the encoding and firmware interfaces |
| `create_context` | contexts are owned by the caller that created them<br>the context limit is enforced with EXHAUSTED<br>a context owns exactly one scanout surface for its lifetime |
| `destroy_context` | another owner cannot destroy it |
| `create_fence` | a fence belongs to one context |
| `signal_fence` | signalling twice is BAD_STATE<br>the signal time comes from the virtual clock |
| `wait_fence` | an unsignalled fence after its timeout is TIMEOUT |
| `present` | presentation sequence numbers are monotonic<br>an unregistered surface is INVALID_ARGUMENT, never a blank frame<br>a destroyed context's surface can no longer be presented |
| `submit` | returns UNSUPPORTED: the command encoding is UNKNOWN |
| `gpu_firmware` | returns UNSUPPORTED: the interface is RESTRICTED/UNKNOWN |
<!-- psm:end invariants-PSM-GFX -->

That a context owns exactly one scanout surface, that fences are per-context, that
signalling twice is `BAD_STATE` and that presentation numbers increase are PC5's own
lifecycle rules (`D-PSM-09`), not console facts — which is why they are cited as
decisions and not as claims. `submit` and `gpu_firmware` are the two refusals that
bound `M14`'s real work.

### 3.6 Audio and USB (`M15`)

What PC5 must present: bounded PCM streams with a stated latency class, and a USB
path with discovery, HID reports, interrupt endpoints and hot-plug, for a controller
that the guest can use without PC5 pretending to know Sony's proprietary layer.

What is public: the console provides "Tempest" 3D AudioTech. That is a product name
with a described capability; it is not a DSP programming model, and the 2020 post
does not describe one. Nothing in the post covers USB topology, HID usage or
controllers at all.

What PC5 assumes (`INFERRED`): that audio reaches the platform as bounded PCM buffers
with some latency class, which is what any audio path must look like from the side
that consumes it.

What is unknown: the audio DSP's programming interface, the console's USB topology and
controller interface, how its software binds controller input to a session, and
controller features beyond standard HID reports. `dsp_load` and `dualsense_features`
are refusals for exactly those reasons.

The registry:

<!-- psm:begin claims-audio -->
| ID | Claim | Class | Source | Confidence | Remaining uncertainty | Conformance |
|---|---|---|---|---|---|---|
| `C-AUD-01` | The console provides "Tempest" 3D AudioTech | **PUBLIC** | `S-SONY-2020` | high: first-party specification table | the engine's programming interface, its formats and its timing guarantees are not public | PSM-AUD open_stream |
| `C-AUD-02` | Audio reaches the platform as bounded PCM buffers with a latency class | **INFERRED** | `S-SONY-2020` | low-medium: a modelling assumption, not a console fact | buffer depth, latency classes and underrun behaviour on the console are not public | PSM-AUD open_stream / write |
| `C-AUD-03` | The console's audio DSP programming interface | **UNKNOWN** | — (no public source) | n/a | no public source describes the DSP, its programs or their upload format | PSM-AUD dsp_load (UNSUPPORTED) |
<!-- psm:end claims-audio -->

<!-- psm:begin claims-usb -->
| ID | Claim | Class | Source | Confidence | Remaining uncertainty | Conformance |
|---|---|---|---|---|---|---|
| `C-USB-01` | The console's USB topology and its controller interface | **UNKNOWN** | — (no public source) | n/a | no public primary source in this registry documents the console's USB controller, addresses or transfer scheduling | PSM-USB enumerate |
| `C-USB-02` | How the console's system software binds controller input to a session | **UNKNOWN** | — (no public source) | n/a | nothing public describes the binding, the report rate or the disconnect semantics the system software expects | PSM-USB hid_report / interrupt_in |
| `C-USB-03` | Controller features beyond standard HID reports | **UNKNOWN** | — (no public source) | n/a | nothing public describes the console's extended controller protocol, and the 2020 post says nothing about input devices at all | PSM-USB dualsense_features (UNSUPPORTED) |
<!-- psm:end claims-usb -->

The interfaces:

<!-- psm:begin interfaces-PSM-AUD -->
| Operation | Behaviour | Inputs | Outputs | Documented failures | Status | Claim |
|---|---|---|---|---|---|---|
| `open_stream` | Negotiate a PCM output stream | `rate`, `channels`, `format`, `latency_class`, `owner` | `stream, buffer_frames, latency_ns` | `invalid_argument`, `exhausted` | implemented | `C-AUD-01`, `C-AUD-02` |
| `write` | Queue PCM frames for output | `stream`, `frames`, `owner` | `accepted, free_frames` | `ownership`, `bad_state`, `invalid_argument`, `overflow` | implemented | `C-AUD-03` |
| `drain` | Advance the virtual clock until the buffer empties | `stream` | `drained_frames, now` | `ownership` | implemented | `C-AUD-03` |
| `close` | Close a stream and report what was still queued | `stream`, `owner` | `discarded_frames` | `ownership` | implemented | `C-AUD-03` |
| `dsp_load` | Load a program onto the console's 3D audio DSP | `program` | `none` | `unsupported` | **UNSUPPORTED by design** | `C-AUD-03` |
<!-- psm:end interfaces-PSM-AUD -->

<!-- psm:begin invariants-PSM-AUD -->
| Operation | Invariant the conformance suite checks |
|---|---|
| `open_stream` | rate and channel counts are range-checked<br>the buffer size follows from the latency class, not from a guess<br>the stream limit is enforced with EXHAUSTED |
| `write` | writing more than the buffer holds is OVERFLOW, not a silent drop<br>a closed stream is BAD_STATE |
| `drain` | draining consumes exactly the queued frames |
| `close` | queued frames are counted, never silently lost |
| `dsp_load` | returns UNSUPPORTED: the DSP interface is UNKNOWN |
<!-- psm:end invariants-PSM-AUD -->

<!-- psm:begin interfaces-PSM-USB -->
| Operation | Behaviour | Inputs | Outputs | Documented failures | Status | Claim |
|---|---|---|---|---|---|---|
| `attach` | Attach a device at an address with a class and VID:PID | `address`, `device_class`, `vid_pid` | `address, device_class` | `invalid_argument`, `bad_state` | implemented | `C-USB-01` |
| `detach` | Remove a device (hot-plug removal) | `address` | `address` | `no_device` | implemented | `C-USB-02` |
| `enumerate` | List the attached devices as a guest would see them | — | `devices` | — | implemented | `C-USB-01` |
| `open` | Claim a device for an owner | `address`, `owner` | `handle` | `no_device`, `ownership` | implemented | `C-USB-03` |
| `close` | Release a claimed device | `handle`, `owner` | `address` | `ownership` | implemented | `C-USB-03` |
| `hid_report` | Send a validated HID output report | `handle`, `report` | `bytes` | `ownership`, `invalid_argument`, `bad_state` | implemented | `C-USB-02` |
| `interrupt_in` | Wait for an input report on the virtual clock | `handle`, `timeout` | `report, waited` | `ownership`, `timeout`, `invalid_argument` | implemented | `C-USB-02` |
| `inject_input` | Deliver an input report to a claimed device (test affordance) | `handle`, `report` | `queued` | `ownership`, `invalid_argument`, `overflow` | implemented | `C-USB-02` |
| `dualsense_features` | Use controller features beyond standard HID reports | `handle` | `none` | `unsupported` | **UNSUPPORTED by design** | `C-USB-03` |
<!-- psm:end interfaces-PSM-USB -->

<!-- psm:begin invariants-PSM-USB -->
| Operation | Invariant the conformance suite checks |
|---|---|
| `attach` | addresses are 1..127<br>an occupied address is BAD_STATE |
| `detach` | a detached address is free for reuse |
| `enumerate` | every attached device appears exactly once |
| `open` | a second owner cannot claim a claimed device |
| `close` | closing another owner's handle is OWNERSHIP |
| `hid_report` | an empty or oversized report is INVALID_ARGUMENT<br>a device removed while open is BAD_STATE |
| `interrupt_in` | no report within the timeout is TIMEOUT |
| `inject_input` | the queue is bounded and overflow is explicit |
| `dualsense_features` | returns UNSUPPORTED: the console-specific behaviour is UNKNOWN |
<!-- psm:end invariants-PSM-USB -->

Buffer sizing follows the declared latency class — the class is a PC5 invention
(`D-PSM-10`) and the returned `buffer_frames` and `latency_ns` are derived from it, so
a caller can never be surprised by a bound it did not ask for. On the USB side the
double models standard xHCI/HID semantics only: addresses are explicit, an occupied
address is `BAD_STATE`, reports are length-checked against the 64-byte interrupt
endpoint maximum, and detaching a device invalidates its handles rather than leaving
them dangling.

### 3.7 Security (`M17`)

This subsystem has a contract and **no interface**. That asymmetry is the point.

What may be said publicly: general hardware-root-of-trust concepts are standard
industry practice, and the console has an authenticated boot chain — the existence of
one is not controversial. What may **not** be said: how it works, what it measures,
what it accepts, how the GPU firmware is authenticated, or what the system software
does with authenticated state once running.

The registry:

<!-- psm:begin claims-security -->
| ID | Claim | Class | Source | Confidence | Remaining uncertainty | Conformance |
|---|---|---|---|---|---|---|
| `C-SEC-01` | The console's boot-chain authentication and its root of trust | **RESTRICTED** | — (no public source) | n/a | the mechanism is not public, and reaching it would require circumvention | not modelled — no PSM interface exists for security state |
| `C-SEC-02` | What the system software does with authenticated state once running | **UNKNOWN** | — (no public source) | n/a | no public source describes it | not modelled — recorded so the gap is visible |
<!-- psm:end claims-security -->

PC5's lawful boundary, stated as a refusal list rather than a policy aspiration. PC5
does not specify, implement, test or plan: signature bypass, key extraction,
authentication circumvention, secure-boot defeat, decryption of protected Sony
firmware, or exploitation of console security. There is no `PSM-SEC` interface
because there is no lawful operation to put in it. `tests/test_psm_registry.py`
asserts that no interface exists for the `security` subsystem, and the audit refuses
any operation that cites a `RESTRICTED` claim while being implemented.

The single largest consequence is recorded rather than hidden: PC5 cannot claim that
the genuine system software will accept its platform until the software itself says
so, and that statement cannot be purchased by writing more contract. `M17` is where
the boundary is examined; the answer may be that it is a blocker, and the roadmap
keeps `M17`–`M21` blocked accordingly.

## 4. Status vocabulary

Every operation returns one of these. The vocabulary is small on purpose: a guest
that can distinguish "you do not know" from "you will not" from "malformed" can adapt;
a guest handed a zero cannot.

<!-- psm:begin statuses -->
| Status | Means |
|---|---|
| `ok` | the operation did what its specification says |
| `invalid_argument` | the request was malformed; nothing changed |
| `unsupported` | the contract marks this behaviour UNKNOWN or the operation is not part of this interface |
| `not_ready` | the operation is valid but there is nothing to return yet |
| `bad_state` | the object exists but the operation is not legal in its state |
| `no_device` | no such device, queue, stream or service is present |
| `exhausted` | a documented limit is reached; the limit is unchanged |
| `timeout` | the virtual clock advanced and the event did not arrive |
| `ownership` | the caller does not own what it is trying to use |
| `overflow` | a bounded buffer or queue is full |
| `denied` | a security-state refusal: distinct from UNSUPPORTED (ADR-007) |
<!-- psm:end statuses -->

Two rules are absolute. A non-`OK` result carries **no value** — never a zero-filled
structure, never an empty list that a caller could mistake for an answer. And
`UNSUPPORTED` is decided by the contract, not by the implementation: an operation
marked unsupported returns `UNSUPPORTED` before its handler is reached, so it cannot
accidentally start working.

## 5. Interfaces, doubles and the eight-group mapping

The contract requires eight test-double groups. The implementation has ten classes,
because interrupts, timers and the message transport are one *group* but three
*doubles*. Merging them would create the fictional combined interface §3.3 forbids.
The mapping is asserted by `psm/audit.py`, which fails if a required group has no
interface or if an interface is neither required nor declared as an extra.

<!-- psm:begin groups -->
| Required group | Interface(s) |
|---|---|
| CPU | `PSM-CPU` |
| memory | `PSM-MEM` |
| interrupts and timers | `PSM-IRQ` + `PSM-TMR` |
| storage and I/O | `PSM-STOR` |
| graphics | `PSM-GFX` |
| audio | `PSM-AUD` |
| USB and controller | `PSM-USB` |
| system services | `PSM-SVC` |
| extra: PSM-IPC | PC5's own message transport, delivered under M16 as part of the interrupts/timers group; it is not claimed to be the console's transport |
<!-- psm:end groups -->

### 5.1 PC5's own decisions, kept apart from console claims

A design decision is not evidence. The register below holds the choices PC5 made
about its own interfaces — buffer sizing, ownership rules, refusals — and each one is
cited by the operations that implement it. Keeping them in their own register is what
stops a PC5 choice from being re-read later as a statement about the console.

<!-- psm:begin decisions -->
| ID | Interface | PC5 decision | Why |
|---|---|---|---|
| `D-PSM-01` | `PSM-CPU` | The CPU double exposes only the documented leaves and refuses the rest. | A guest asking an uncovered question must get UNSUPPORTED, so a missing model is visible instead of plausible. |
| `D-PSM-02` | `PSM-CPU` | Security-state MSRs return DENIED, architectural ones return PC5 values. | DENIED and UNSUPPORTED mean different things to a guest and must not be conflated (ADR-007). |
| `D-PSM-03` | `PSM-MEM` | One pool, first-fit allocation, explicit EXHAUSTED, mapping not copying. | Matches the one public fact (one pool) and refuses to invent the allocation policy, which is UNKNOWN. |
| `D-PSM-04` | `PSM-MEM` | Protection domains are UNSUPPORTED. | The contract does not know them; a fabricated policy would be worse than an explicit gap. |
| `D-PSM-05` | `PSM-IRQ` | A PC5 event-queue abstraction with explicit BAD_STATE and NOT_READY. | The console's controller is UNKNOWN; the abstraction is named as PC5's. |
| `D-PSM-06` | `PSM-TMR` | A virtual nanosecond clock; every response names its source. | Makes timeouts exact and replay deterministic, and never lets a synthetic clock look like firmware. |
| `D-PSM-07` | `PSM-IPC` | Depth-bounded named queues; OVERFLOW and TIMEOUT are explicit statuses. | A dropped message that the guest cannot observe is a silent protocol failure. |
| `D-PSM-08` | `PSM-STOR` | Tag-based submission with per-tag ownership and queue-depth back-pressure. | Standard NVMe semantics are public and testable; the console's extras are not modelled. |
| `D-PSM-09` | `PSM-GFX` | Command submission and GPU firmware are UNSUPPORTED; contexts, fences and PC5-owned surface presentation are modelled. | Accepting a command buffer PC5 cannot decode would be a fabricated success. |
| `D-PSM-10` | `PSM-AUD` | PCM streams with PC5-chosen latency classes; DSP upload is UNSUPPORTED. | The DSP interface is UNKNOWN; the buffer contract is the part PC5 can own. |
| `D-PSM-11` | `PSM-USB` | Standard xHCI/HID semantics with report-length validation and bounded queues; extended controller features are UNSUPPORTED. | The PC side is ordinary hardware; the console's extensions are not public. |
| `D-PSM-12` | `PSM-SVC` | A PC5 service registry and a bounded log; unknown lookups are NO_DEVICE and log overflow is OVERFLOW. | Console service names are UNKNOWN and must not be invented. |
| `D-PSM-13` | `PSM-CPU` | reset returns the double to power-on state and increments a reset epoch. | A deterministic replay needs a defined origin; the epoch makes a stale handle visibly stale instead of silently valid. |
| `D-PSM-14` | `PSM-MEM` | stats reports only live handles, and a refused allocation changes nothing. | Exhaustion that leaks accounting would let a guest starve itself while the numbers say otherwise. |
<!-- psm:end decisions -->

### 5.2 The interfaces

Every interface, with its identity as the doubles report it:

<!-- psm:begin interfaces -->
| Interface | Version | Subsystem | Milestone | Ops | Author | Note |
|---|---|---|---|---|---|---|
| `PSM-CPU` | 0.1.0 | cpu | M11 | 7 | PC5 | PC5-authored test double for the CPU contract; not a Sony interface. |
| `PSM-MEM` | 0.1.0 | memory | M12 | 7 | PC5 | PC5-authored test double for the memory contract; not a Sony interface. |
| `PSM-IRQ` | 0.1.0 | interrupts | M16 | 5 | PC5 | PC5-authored test double; the console's interrupt controller is UNKNOWN. |
| `PSM-TMR` | 0.1.0 | interrupts | M16 | 4 | PC5 | PC5-authored test double; timer and RTC values on the console are not public. |
| `PSM-IPC` | 0.1.0 | interrupts | M16 | 4 | PC5 | PC5-authored test double; the console's IPC mechanism is UNKNOWN. |
| `PSM-STOR` | 0.1.0 | storage | M13 | 7 | PC5 | PC5-authored test double for standard NVMe semantics; not a Sony device model. |
| `PSM-GFX` | 0.1.0 | graphics | M14 | 9 | PC5 | PC5-authored test double; the console's command encoding is UNKNOWN. |
| `PSM-AUD` | 0.1.0 | audio | M15 | 5 | PC5 | PC5-authored test double; the console's audio DSP contract is UNKNOWN. |
| `PSM-USB` | 0.1.0 | usb | M15 | 9 | PC5 | PC5-authored test double for standard xHCI/HID semantics; not a Sony device model. |
| `PSM-SVC` | 0.1.0 | services | M16 | 7 | PC5 | PC5-authored service layer; not the console's service interface. |
<!-- psm:end interfaces -->

Each double is a Python object with a fixed surface: a name and version, a documented
operation table, name-based dispatch, a call transcript, documented failures per
operation, invariants, and an entry point that runs its conformance cases. Two rules
are enforced structurally rather than by convention:

* the base class checks at construction time that every documented, supported
  operation has a handler — an operation documented and not implemented is an
  assertion failure, not a surprise at call time;
* an operation the contract marks unsupported has no handler at all. It cannot
  fabricate a value because there is nothing there to fabricate one.

## 6. Conformance plan

Ten categories, all of which the suite exercises for every interface where they apply:

1. **valid** — the documented happy path, including the shape of the returned value;
2. **invalid parameters** — negative leaves, misaligned addresses, zero sizes,
   oversized reports: refused with `INVALID_ARGUMENT`, state unchanged;
3. **boundaries** — the last legal value and the first illegal one (vector 31 vs 32,
   report length 64 vs 65, alignment powers of two, log capacity);
4. **missing devices** — operations against an absent device, queue, stream or
   service: `NO_DEVICE`, never an empty success;
5. **unsupported operations** — every `UNSUPPORTED` row, asserted to carry no value;
6. **resource exhaustion** — pool, queue depth, context limit, stream limit, fence
   table: `EXHAUSTED` and no accounting leak afterwards;
7. **invalid transitions** — double release, double signal, use after close, call
   after panic, presentation of a destroyed surface;
8. **ownership violations** — another owner's handle, fence or context: `OWNERSHIP`;
9. **timeouts and error propagation** — an advance of the virtual clock, `TIMEOUT`,
   and the clock's new value checked;
10. **deterministic replay** — two runs of the whole suite produce identical check
    results and identical call transcripts.

Case counts, generated from the suite itself so the document cannot claim more tests
than exist:

<!-- psm:begin cases -->
| Interface | Conformance checks |
|---|---|
| `PSM-CPU` | 30 |
| `PSM-MEM` | 32 |
| `PSM-STOR` | 33 |
| `PSM-GFX` | 29 |
| `PSM-AUD` | 26 |
| `PSM-USB` | 29 |
| `PSM-SVC` | 26 |
| `PSM-IRQ` | 21 |
| `PSM-TMR` | 13 |
| `PSM-IPC` | 22 |
| **total** | **261** |
<!-- psm:end cases -->

`tests/test_psm_mutations.py` answers the obvious objection — that a suite which only
ever sees working doubles proves nothing about its own sensitivity. It builds
deliberately broken doubles and requires the suite to catch each one: an allocation
that overlaps, a release that succeeds twice, a fabricated success for an `UNKNOWN`
operation, a timeout that returns a value, a handle accepted from a non-owner, a log
that silently drops records, a queue that reorders messages, an interrupt released
with deliveries still pending, a clock that runs backwards. Each mutation must produce
at least one failing check; a mutation that survives is a hole in the suite and fails
the test.

## 7. What a passing suite proves

It proves that PC5's own interfaces behave as PC5 specified them, that the registry's
claims are internally consistent and linked to the code, that the document's tables
match the data, and that the suite detects broken implementations.

It does not prove that Sony's system software accepts, uses or resembles this
interface. That requires genuine software executing against PC5's platform and
producing evidence — which is `M2`'s physical-validation path and `M11` onwards, and
which no amount of conformance testing substitutes for. The distinction matters
because "conformance" is exactly the word that gets stretched: here it means
*conformance of PC5's doubles to PC5's own contract*, and nothing more.

## 8. Ownership, lifetime and determinism

Ownership is explicit everywhere: a handle, fence, context, stream or queue belongs
to the owner string that created it, and every operation that takes a handle also
takes an owner and compares them. A mismatch is `OWNERSHIP`; the object is unchanged.
Lifetime ends exactly one way — an explicit release, close, destroy or detach — and
the double reports what it discarded, so a leak is visible in the transcript instead
of accumulating silently.

Determinism comes from three choices: no wall-clock time inside any double, a virtual
monotonic clock advanced only by explicit calls, and name-based dispatch recorded in a
transcript. Two fresh runs of the suite produce identical results and identical
transcripts; the test asserts it rather than assuming it.

## 9. Dependencies on `M11`–`M17`

| Milestone | What it consumes from this contract | What is still missing |
|---|---|---|
| `M11` CPU | `PSM-CPU`: CPUID subset, MSR policy, exception model, reset epoch | the execution model itself; `execute_payload` is `UNSUPPORTED` until then |
| `M12` memory | `PSM-MEM`: one pool, ownership, sharing, translation | protection domains; coherence and flush semantics |
| `M13` storage | `PSM-STOR`: queues, tags, durability, back-pressure | the decompression interface; the on-disk layout |
| `M14` graphics | `PSM-GFX`: contexts, fences, surfaces, presentation | command encoding; GPU firmware (`RESTRICTED`) |
| `M15` audio, USB | `PSM-AUD`, `PSM-USB`: streams, latency classes, HID, hot-plug | DSP interface; controller features beyond HID |
| `M16` services | `PSM-IRQ`, `PSM-TMR`, `PSM-IPC`, `PSM-SVC`: delivery, clock, RTC, transport, registry | every console-side behaviour: all `UNKNOWN` |
| `M17` security | the refusal boundary and `C-SEC-01`/`C-SEC-02` | everything; no interface exists by design |

`M18`–`M21` do not appear above because they consume this contract only through
the chain: `M18` depends on `M9`, `M13` and `M17`; `M19` on `M14`, `M15`, `M16`
and `M18`; `M20` on `M19`; `M21` on `M17` and `M18`. Nothing in `M18`–`M21` reads
a definition that is not already in the table.

`M10` blocks nothing it should not: what it delivers is documentary and structural
(contract written, unknowns marked, doubles present, suite green). It asserts nothing
about the console, it stays `experimental` so it cannot become a release dependency,
and its roadmap state stays `in_progress`: the repository's evidence-level rule
(tested in `tests/test_product_identity.py`) allows `complete` only for milestones
whose level has evidence, and only levels 1–2 do.

## 10. Unresolved questions

Recorded as questions, not as answers, because each one changes what `M11`–`M17` must
build:

1. Does the console's system software require a hypervisor-visible environment, and if
   so with which CPUID and MSR surface? (`UNKNOWN`; decides whether `M11` models SVM
   as absent or as present-and-hidden.)
2. Is GPU-visible memory coherent, or must the guest flush? (`UNKNOWN`; changes
   `PSM-MEM`'s sharing op from informational to load-bearing.)
3. How does the system software locate its first service? (`UNKNOWN`; decides whether
   `PSM-SVC`'s registry is discovery or only bookkeeping.)
4. Is the message transport shared memory, a mailbox register pair, or a queue pair?
   (`UNKNOWN`; `PSM-IPC` currently models a named, depth-bounded queue and says so.)
5. What does the storage path expect to find, and in what layout? (`UNKNOWN`; `M21`.)
6. Does anything in the boot path require an authenticated response that PC5 cannot
   lawfully produce? (Probably; this is `M17`'s central question and a candidate
   blocker for the destination, not a technical detail.)
7. Can the AMD APM volume be read in this environment so §3.1's `INFERRED` rows can
   become `PUBLIC`? (Blocked on retrieval; the first `M11` task.)

## 11. Corrections to the previous revision

Kept as a list because a contract that quietly edits itself is not auditable:

* "hypervisor present in the console" was `INFERRED`; it is `UNKNOWN`. Public
  specifications do not say whether the system software runs under a hypervisor.
* "448 GB/s measured on PC hardware" — nothing was measured. The row is now the
  published figure only, and no measurement claim exists.
* The 36-compute-unit figure was presented as a fact; it is arithmetic, now marked
  `INFERRED` and reported as a derivation.
* Rows for interrupts, timers, RTC, IPC, services, USB topology, controllers and
  security were either absent or unsupported; they are now explicit `UNKNOWN` claims
  with the refusal each one implies.
* The document cited a `playstation.com` tech-specs page that now returns 404. It is
  removed and replaced by the PlayStation.Blog post it duplicated.
* "Each contract names its source" was a prose promise; it is now a rule engine, a
  linkage audit and a document block check.
* The single line naming the doubles became ten documented interfaces with
  invariants, refusals and a conformance suite.

## 12. Change history

| Revision | Date | Change |
|---|---|---|
| r0 | 2026-11-15 | Skeleton: source markers, seven subsystem tables, conformance meaning. No registry, no interfaces, no tests. |
| r1 | 2026-11-16 | `M10`: evidence registry (4 sources, 38 claims, 14 PC5 decisions), rule engine, linkage audit, ten interfaces with 64 documented operations, conformance suite, mutation tests, generated tables, ADR-024. Unsupported statements corrected (§11). |

## 13. Running it

```sh
python3 -m psm.contract            # registry summary and rule violations
python3 -m psm.audit               # claim ↔ interface linkage
python3 -m psm.conformance         # the suite, standalone
python3 -m psm.render --check      # document blocks vs renderers
make check                         # all of the above, inside the test suite
```

None of these commands touches hardware, firmware, or the PS5 package. The protected
input stays a pointer: `PS5UPDATE.PUP` is never fetched, extracted, decrypted,
modified or redistributed (ADR-022), and nothing in `psm/` reads it.
