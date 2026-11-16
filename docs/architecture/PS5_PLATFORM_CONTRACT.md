# PS5 platform contract — what PC5 has to present to the genuine system software

| | |
|---|---|
| Status | **specification skeleton.** Every row is either PUBLIC, INFERRED or UNKNOWN, and UNKNOWN means "not guessed". |
| Milestone | `M10` (this document), consumed by `M11`–`M16` |
| Decisions | ADR-019 (destination), ADR-022 (protected input) |
| Related | [`../PS5_SYSTEM_SOFTWARE_TARGET.md`](../PS5_SYSTEM_SOFTWARE_TARGET.md), [`PC5_ARCHITECTURE_SPEC_v2.md`](PC5_ARCHITECTURE_SPEC_v2.md) §6 |

PC5's platform compatibility layer (PSCL) is a **model of a PlayStation 5** that
the genuine system software can execute on. A model is only as good as its
contract, so this document writes the contract first and implements it second.

## 1. Source discipline

| Marker | Means | May be implemented? |
|---|---|---|
| `PUBLIC` | documented by Sony, AMD or independent measurement that is reproducible | yes, against a conformance test |
| `INFERRED` | follows from a public fact plus a stated assumption | yes, but the assumption is written down and the test asserts it |
| `UNKNOWN` | nothing public supports a value or a behaviour | **no** — the model must report it as unsupported, never invent it |
| `RESTRICTED` | reaching it would require circumvention | **no**, ever (ADR-007, ADR-022) |

Rule: an `UNKNOWN` row is implemented as an explicit *unimplemented device
response*, so the guest fails visibly and the failure is recorded. Silent
guessing is the one thing this model may not do.

## 2. The contracts

Each row names what PC5 must present, what is known, what is not, and the
test-double the model exposes so that everything above it can be tested before
the real thing exists.

### CPU and privileged execution (`M11`)

| Item | Value / behaviour | Source | Notes |
|---|---|---|---|
| ISA family | x86-64, AMD Zen 2 core | PUBLIC | 8 cores / 16 threads with SMT in the console |
| clock | variable, up to 3.5 GHz, shared power budget with the GPU | PUBLIC | the model must not assume a fixed clock |
| privilege model | ring 0 kernel, ring 3 user, hypervisor present in the console | INFERRED | a nested hypervisor on PC hardware is the open question for `M11` |
| CPUID surface | partly console-specific | **UNKNOWN** | the model exposes a documented subset and records what it does not expose |
| virtualisation | AMD SVM on Zen 2 | PUBLIC (AMD) | whether the guest uses it is **UNKNOWN** |

Test double: a CPU contract object exposing feature bits, MSR behaviour and
exception semantics, with a conformance suite that a PC5-authored payload runs
against.

### Memory (`M12`)

| Item | Value / behaviour | Source | Notes |
|---|---|---|---|
| topology | 16 GB GDDR6 shared between CPU and GPU | PUBLIC | unlike a PC: one pool, both engines |
| bandwidth | 448 GB/s | PUBLIC | measured on PC hardware for the mapped case, never assumed |
| GPU-visible mappings | direct, coherent | INFERRED | the model must expose a mapping primitive, not a copy |
| protection domains | **UNKNOWN** | — | modelled only as far as the contract supports |
| allocation policy | **UNKNOWN** | — | must not be invented from PC conventions |

### Interrupts, timers, service devices (`M16`)

| Item | Value / behaviour | Source | Notes |
|---|---|---|---|
| interrupt controller | **UNKNOWN** (console-specific) | — | the model presents a documented event-queue abstraction instead of pretending to be the hardware |
| timers | high-resolution counter, RTC | INFERRED | contract shape fixed, values measured |
| IPC | mailbox-style queues between guest components | **UNKNOWN** | modelled as an opaque transport with a conformance test |

### Storage (`M13`)

| Item | Value / behaviour | Source | Notes |
|---|---|---|---|
| device class | NVMe-class, console-specific controller | PUBLIC | 825 GB in the launch console |
| raw throughput | 5.5 GB/s | PUBLIC | PC NVMe measured per device, reported per row |
| decompression block | dedicated hardware in the I/O path | PUBLIC (announced) | contract **UNKNOWN** |
| queue semantics | NVMe-like submission/completion | INFERRED | the model implements the documented subset and refuses the rest |
| storage layout the guest expects | **UNKNOWN** | — | prepared by `M21`, not guessed |

### Graphics (`M14`)

| Item | Value / behaviour | Source | Notes |
|---|---|---|---|
| GPU | custom RDNA 2, 36 CUs, up to 2.23 GHz | PUBLIC | clock is variable |
| command submission | ring buffer with fences | INFERRED | shape fixed, encoding **UNKNOWN** |
| shader translation | required for a PC GPU | INFERRED | specified in `M14`, not attempted yet |
| GPU firmware interface | **RESTRICTED / UNKNOWN** | — | not modelled; no bypass |

### Audio and USB (`M15`)

| Item | Value / behaviour | Source | Notes |
|---|---|---|---|
| audio engine | "Tempest" 3D audio | PUBLIC | DSP contract **UNKNOWN** |
| audio output shape | PCM streams with latency classes | INFERRED | measurable on the PC side |
| USB | xHCI, HID, DualSense | PUBLIC | the PC side is ordinary hardware |

### Security state (`M17`)

| Item | Value / behaviour | Source | Notes |
|---|---|---|---|
| boot-chain authentication | hardware-anchored in console silicon and fuses | PUBLIC (console architecture, general) | `RESTRICTED` for PC5 — analysis only |
| secure storage / key slots | **UNKNOWN** | — | not modelled |
| attestation | **UNKNOWN** | — | not modelled |

The security-state device is deliberately the least complete contract in this
document. It is complete enough to state what PC5 will not do, which is the
honest state of the art.

## 3. Conformance: what "the model works" means

Every contract above has a test double and every test double has a suite. A device
model is `complete` only when:

1. its conformance suite passes on **both** hosts (PC5 kernel, optional Linux
   tier), through the same interface (`PC5-PSM`);
2. the suite fails when the model is deliberately broken (mutation runs, as the
   ISO and boot gates already do);
3. every `UNKNOWN` row produces an explicit "unsupported" response that the
   suite asserts — a model that silently returns zeros for an unknown device is
   a model that will lie to the guest.

## 4. What this document is not

It is not evidence that the genuine system software runs, boots, loads or has ever
been read by this project. It is the contract PC5 writes *before* it can be, and
it is versioned so that the day a real observation contradicts a row, the row
changes and the change is visible in git.
