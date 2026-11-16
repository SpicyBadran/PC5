"""The registry behind ``docs/architecture/PS5_PLATFORM_CONTRACT.md``.

Two lists live here and nowhere else:

* :data:`SOURCES` — documents PC5 is willing to cite, with how far each was
  verified in this environment.
* :data:`CLAIMS` — statements about the console, each with its classification,
  support, residual uncertainty, the decision that depends on it, and the
  conformance case that exercises PC5's side.

:data:`DECISIONS` records the other half of the contract: things that are not
claims about the console at all but PC5's own interface choices. Keeping them
apart is the point — a PC5 design decision must never be cited as evidence about
Sony's hardware, and a console fact must never be invented to justify a design.

Verification status of the sources, stated plainly because it bounds every claim
below: four documents were verified from this environment (two of them fetched and
read here, one located with publisher, revision and URL confirmed, one fetched
with its revision table read). Nothing in this registry rests on a search-result
snippet, and no URL is included that was not opened.
"""

from __future__ import annotations

from typing import Tuple

from .contract import Claim, Contract, Source

SOURCES: Tuple[Source, ...] = (
    Source(
        id="S-SONY-2020",
        title="Unveiling New Details of PlayStation 5: Hardware Technical Specs [UPDATED]",
        publisher="Sony Interactive Entertainment (PlayStation.Blog)",
        date="2020-03-18 (updated 2020-03-20)",
        url="https://blog.playstation.com/2020/03/18/unveiling-new-details-of-playstation-5-hardware-technical-specs/",
        verification="verified",
        location="the 'official list of PS5 specs' table at the end of the post: CPU, GPU, "
                 "System Memory, SSD, Video Out and Audio rows",
        notes="First-party. Gives headline numbers only: no register layout, no firmware "
              "interface, no allocation policy, no CPUID leaves."),
    Source(
        id="S-AMD-APM-V1",
        title="AMD64 Architecture Programmer's Manual Volume 1: Application Programming "
              "(publication 24592, revision 3.25)",
        publisher="Advanced Micro Devices, Inc.",
        date="May 2026",
        url="https://docs.amd.com/v/u/en-US/24592_3.25_APM_Vol1_PUB",
        verification="located",
        location="publication metadata confirmed on AMD's documentation portal; the body of "
                 "the PDF was not read in this environment",
        notes="Establishes the architecture PC5 presents. Volume 2 (System Programming, "
              "publication 24593) is the primary source for privilege levels, MSRs and "
              "exception delivery; its current revision could not be confirmed here, so "
              "claims resting on it are INFERRED, not PUBLIC."),
    Source(
        id="S-UEFI",
        title="UEFI Specifications (UEFI Specification 2.11, released December 2024; "
              "ACPI Specification 6.6, released May 2025)",
        publisher="Unified Extensible Firmware Interface Forum",
        date="2024-12 (UEFI 2.11); 2025-05 (ACPI 6.6)",
        url="https://uefi.org/specifications",
        verification="verified",
        location="'Latest Versions of the UEFI Specifications' section of the index page",
        notes="Covers the host firmware interface PC5 boots on; says nothing about the "
              "console."),
    Source(
        id="S-NVME",
        title="NVM Express Base Specification, Revision 2.4 (ratified 2026-07-31)",
        publisher="NVM Express, Inc.",
        date="2026-08-04 (index page)",
        url="https://nvmexpress.org/specifications/",
        verification="verified",
        location="the specification index table: 'NVM Express Base Specification', "
                 "Revision 2.4, with its download link",
        notes="Defines submission/completion queue semantics, flush and status reporting. "
              "The console's controller is not claimed to be a standard NVMe device."),
)

CLAIMS: Tuple[Claim, ...] = (
    # ------------------------------------------------------------------ CPU
    Claim(
        id="C-CPU-01", subsystem="cpu", classification="PUBLIC",
        statement="The CPU is an x86-64 AMD Ryzen \"Zen 2\" with 8 cores / 16 threads at a "
                  "variable frequency of up to 3.5 GHz",
        sources=("S-SONY-2020",), establishes="direct",
        value="x86-64 Zen 2, 8 cores / 16 threads, variable up to 3.5 GHz",
        confidence="high: first-party specification table",
        uncertainty="the actual frequency at any moment, and the boost policy, are not public",
        decision="the model presents this ISA and core count; it never presents a fixed clock",
        conformance="PSM-CPU capabilities"),
    Claim(
        id="C-CPU-02", subsystem="cpu", classification="PUBLIC",
        statement="The CPU frequency is variable rather than fixed",
        sources=("S-SONY-2020",), establishes="direct",
        value="variable frequency, up to 3.5 GHz",
        confidence="high: first-party specification table",
        uncertainty="what determines the current frequency (load, power, thermal) is not public",
        decision="nothing in the model may depend on a cycle budget derived from a fixed clock",
        conformance="PSM-CPU capabilities"),
    Claim(
        id="C-CPU-03", subsystem="cpu", classification="INFERRED",
        statement="The CPU implements the AMD64 architectural exception model (vectors 0-31 "
                  "with architectural error-code behaviour)",
        sources=("S-AMD-APM-V1",),
        establishes="assumes the console's Zen 2 core is architecturally conformant, with no "
                    "console-specific deviation in exception delivery",
        confidence="medium: architecture-level, not console-specific",
        uncertainty="whether the console redirects or filters any vector before the system "
                    "software sees it is not public",
        decision="the model delivers architectural vectors and reports an error-code flag per "
                  "vector; it does not invent console-specific vectors",
        conformance="PSM-CPU exception"),
    Claim(
        id="C-CPU-04", subsystem="cpu", classification="UNKNOWN",
        statement="The console-specific CPUID leaf space beyond the documented leaves",
        confidence="n/a",
        uncertainty="nothing public documents the console's leaf space, its vendor string or "
                    "the feature bits it advertises",
        decision="the model exposes a PC5-chosen documented subset and returns UNSUPPORTED for "
                  "every other leaf",
        conformance="PSM-CPU cpuid (UNSUPPORTED path)"),
    Claim(
        id="C-CPU-05", subsystem="cpu", classification="UNKNOWN",
        statement="The console's own MSR usage and any security-relevant MSR values",
        confidence="n/a",
        uncertainty="no public source describes the console's MSR programming or the values it "
                    "relies on",
        decision="architectural MSRs are exposed with PC5 semantics; security-state MSRs are "
                  "DENIED and never zero (ADR-007)",
        conformance="PSM-CPU msr_read (DENIED and UNSUPPORTED paths)"),
    Claim(
        id="C-CPU-06", subsystem="cpu", classification="INFERRED",
        statement="Hardware-assisted virtualisation (AMD SVM) is available to the platform",
        sources=("S-AMD-APM-V1", "S-SONY-2020"),
        establishes="assumes the console's Zen 2 cores expose SVM as the architecture "
                    "specifies and that nothing in the console disables it",
        confidence="low-medium: the ISA is public, the console's configuration is not",
        uncertainty="whether the system software uses SVM, and whether nested virtualisation is "
                    "available beneath it, are not public",
        decision="the model reports SVM as presented with nested=false; it does not claim the "
                  "guest uses it",
        conformance="PSM-CPU virtualisation"),
    Claim(
        id="C-CPU-07", subsystem="cpu", classification="UNKNOWN",
        statement="Whether the console's system software enables or expects hardware "
                  "virtualisation",
        confidence="n/a",
        uncertainty="no public source describes the guest's use of SVM",
        decision="the execution model (M11) must be able to run with or without it and record "
                  "which was used",
        conformance="PSM-CPU execute_payload (UNSUPPORTED until M11)"),

    # --------------------------------------------------------------- memory
    Claim(
        id="C-MEM-01", subsystem="memory", classification="PUBLIC",
        statement="System memory is 16 GB of GDDR6 with 448 GB/s bandwidth",
        sources=("S-SONY-2020",), establishes="direct",
        value="GDDR6, 16 GB, 448 GB/s",
        confidence="high: first-party specification table",
        uncertainty="the split of usable memory and what the system software reserves are not "
                    "public",
        decision="the model presents one 16 GiB pool and 448 GB/s of nominal bandwidth, and "
                  "never claims measured throughput",
        conformance="PSM-MEM topology"),
    Claim(
        id="C-MEM-02", subsystem="memory", classification="INFERRED",
        statement="The memory is one pool both the CPU and the GPU address directly",
        sources=("S-SONY-2020",),
        establishes="assumes 'System Memory' in the specification table means a single shared "
                    "pool rather than separate CPU and GPU memory, as the term and the "
                    "bandwidth figure imply",
        confidence="medium-high: implied by the specification, not stated as an architecture",
        uncertainty="the address ranges each engine may access, and any carve-outs, are not "
                    "public",
        decision="the model allocates from one address space and shares by mapping, never by "
                  "copying",
        conformance="PSM-MEM allocate / share"),
    Claim(
        id="C-MEM-03", subsystem="memory", classification="INFERRED",
        statement="GPU-visible memory is presented to the guest as a mapping of shared memory",
        sources=("S-SONY-2020",),
        establishes="assumes the unified pool is mapped rather than copied; a copy would make "
                    "the published bandwidth figure meaningless for GPU-visible data",
        confidence="medium",
        uncertainty="the mapping granularity and the guest-visible mapping interface are not "
                    "public",
        decision="the model exposes a mapping primitive and a translate operation; it never "
                  "silently copies",
        conformance="PSM-MEM translate"),
    Claim(
        id="C-MEM-04", subsystem="memory", classification="UNKNOWN",
        statement="The allocation policy the console's system software uses",
        confidence="n/a",
        uncertainty="no public source describes allocation granularity, fragmentation policy or "
                    "what the system software reserves for itself",
        decision="the model implements a first-fit pool with explicit exhaustion; it does not "
                  "present itself as the console's allocator",
        conformance="PSM-MEM allocate (EXHAUSTED path)"),
    Claim(
        id="C-MEM-05", subsystem="memory", classification="UNKNOWN",
        statement="Protection domains and isolation between guest components",
        confidence="n/a",
        uncertainty="nothing public describes the console's protection model or its "
                    "capabilities",
        decision="protection domains are an UNSUPPORTED operation: the model reports the gap "
                  "rather than inventing a policy",
        conformance="PSM-MEM protection_domain (UNSUPPORTED)"),
    Claim(
        id="C-MEM-06", subsystem="memory", classification="UNKNOWN",
        statement="Cache coherence and flush semantics for GPU-visible memory",
        confidence="n/a",
        uncertainty="no public source states the coherence model or the operations the system "
                    "software must perform",
        decision="the model is coherent by construction and records that this is a PC5 "
                  "assumption, not a modelled console behaviour",
        conformance="PSM-MEM share"),

    # ----------------------------------------------------------- interrupts
    Claim(
        id="C-IRQ-01", subsystem="interrupts", classification="UNKNOWN",
        statement="The console's interrupt controller and its programming interface",
        confidence="n/a",
        uncertainty="no public source describes the controller, its register layout or its "
                    "delivery model",
        decision="the model presents a PC5 event-queue abstraction and states that it is not "
                  "the console's controller",
        conformance="PSM-IRQ controller_info"),
    Claim(
        id="C-IRQ-02", subsystem="interrupts", classification="UNKNOWN",
        statement="Whether, and how, the console delivers interrupts to the system software",
        confidence="n/a",
        uncertainty="nothing public describes the guest-visible interrupt path",
        decision="vector claim/raise/take is a PC5 abstraction with explicit BAD_STATE and "
                  "NOT_READY outcomes; it is not presented as console behaviour",
        conformance="PSM-IRQ claim / raise / take"),
    Claim(
        id="C-TMR-01", subsystem="interrupts", classification="UNKNOWN",
        statement="The console's timer and clock interfaces",
        confidence="n/a",
        uncertainty="no public source describes the timer width, resolution or the clock the "
                    "system software reads",
        decision="the model exposes a monotonic nanosecond counter on a virtual clock and names "
                  "its source in every response",
        conformance="PSM-TMR now / schedule"),
    Claim(
        id="C-TMR-02", subsystem="interrupts", classification="UNKNOWN",
        statement="Real-time clock behaviour on the console",
        confidence="n/a",
        uncertainty="no public source describes how the system software reads wall-clock time",
        decision="rtc_read names its source so a platform-configured value is never mistaken "
                  "for a firmware RTC",
        conformance="PSM-TMR rtc_read"),
    Claim(
        id="C-IPC-01", subsystem="interrupts", classification="UNKNOWN",
        statement="The console's inter-process message transport",
        confidence="n/a",
        uncertainty="no public source describes the IPC mechanism, its queue depths or its "
                    "failure semantics",
        decision="the model provides depth-bounded named queues with OVERFLOW and TIMEOUT as "
                  "explicit outcomes; it is not presented as the console's IPC",
        conformance="PSM-IPC send / recv"),

    # -------------------------------------------------------------- storage
    Claim(
        id="C-STO-01", subsystem="storage", classification="PUBLIC",
        statement="The console has an 825 GB SSD with 5.5 GB/s raw read bandwidth",
        sources=("S-SONY-2020",), establishes="direct",
        value="825 GB, 5.5 GB/s raw read",
        confidence="high: first-party specification table",
        uncertainty="the usable capacity, the controller's command set and the on-disk layout "
                    "are not public",
        decision="the model presents an NVMe-class device of configurable capacity and reports "
                  "measured PC throughput per device, never the console's figure",
        conformance="PSM-STOR present"),
    Claim(
        id="C-STO-02", subsystem="storage", classification="INFERRED",
        statement="The storage device uses submission/completion queues with tags",
        sources=("S-NVME", "S-SONY-2020"),
        establishes="assumes the console's NVMe-class device follows the standard queue model; "
                    "the specification table's 'SSD' term plus published read bandwidth is all "
                    "the console-side support there is",
        confidence="medium",
        uncertainty="whether the controller is strictly NVMe, and what it adds, is not public",
        decision="the model implements tag-based submission and completion with explicit "
                  "queue-depth back-pressure",
        conformance="PSM-STOR submit / complete"),
    Claim(
        id="C-STO-03", subsystem="storage", classification="INFERRED",
        statement="Writes become durable only after an explicit flush",
        sources=("S-NVME",),
        establishes="assumes standard NVMe semantics apply to the modelled device",
        confidence="medium-high for a standard device; unknown for the console's controller",
        uncertainty="the console's own flush and persistence guarantees are not public",
        decision="flush reports what it made durable, and the model never reports durability it "
                  "did not provide",
        conformance="PSM-STOR flush"),
    Claim(
        id="C-STO-04", subsystem="storage", classification="INFERRED",
        statement="The I/O path includes a dedicated decompression stage",
        sources=("S-SONY-2020",),
        establishes="assumes the 'integrated custom I/O system' described in the post performs "
                    "the decompression the post attributes to it",
        confidence="medium: the post names a custom I/O system, not its programming interface",
        uncertainty="the decompression hardware's interface, formats and ownership rules are "
                    "not public",
        decision="the model records the stage's existence but implements none of it",
        conformance="not modelled — M13 owns the decompression stage; PSM-STOR models "
                    "queues and durability and refuses only the stage's interface"),
    Claim(
        id="C-STO-05", subsystem="storage", classification="UNKNOWN",
        statement="The on-disk layout the console's system software expects",
        confidence="n/a",
        uncertainty="nothing public describes the layout, and PC5 will not infer it from a "
                    "device it cannot open",
        decision="guest_layout is UNSUPPORTED and is recorded as M21 work",
        conformance="PSM-STOR guest_layout (UNSUPPORTED)"),
    Claim(
        id="C-STO-06", subsystem="storage", classification="UNKNOWN",
        statement="The programming interface of the console's decompression hardware",
        confidence="n/a",
        uncertainty="no public document describes the decompression block's registers, "
                    "queues, formats or completion semantics; the 2020 post names the "
                    "stage and stops there",
        decision="decompress is UNSUPPORTED and is deferred to M13 with the queue model",
        conformance="PSM-STOR decompress (UNSUPPORTED)"),

    # ------------------------------------------------------------- graphics
    Claim(
        id="C-GFX-01", subsystem="graphics", classification="PUBLIC",
        statement="The GPU is an AMD Radeon RDNA 2-based engine with ray tracing acceleration "
                  "at a variable frequency of up to 2.23 GHz (10.3 TFLOPS)",
        sources=("S-SONY-2020",), establishes="direct",
        value="RDNA 2-based, ray tracing, variable up to 2.23 GHz, 10.3 TFLOPS",
        confidence="high: first-party specification table",
        uncertainty="the shader ISA the system software submits, and the command encoding, are "
                    "not public",
        decision="the model reports the published engine description and models context, fence "
                  "and present shape only",
        conformance="PSM-GFX info"),
    Claim(
        id="C-GFX-02", subsystem="graphics", classification="INFERRED",
        statement="The GPU has 36 compute units",
        sources=("S-SONY-2020",),
        establishes="derived from Sony's own figures: 10.3 TFLOPS / (2.23 GHz x 128 FP32 "
                    "operations per CU per cycle) is approximately 36, assuming RDNA 2 issues "
                    "128 FP32 operations per CU per cycle and every CU is available to software",
        confidence="medium: arithmetic on a first-party number, not a published CU count",
        uncertainty="the published TFLOPS is rounded, and the console may reserve CUs; the "
                    "exact count is not in the specification table",
        decision="the model does not present a CU count to the guest; M14 must derive it from "
                  "measurement, not from this arithmetic",
        conformance="PSM-GFX info (reports the derivation, not a count)"),
    Claim(
        id="C-GFX-03", subsystem="graphics", classification="UNKNOWN",
        statement="The GPU command encoding the system software submits",
        confidence="n/a",
        uncertainty="no public source describes the command stream, its packets or its "
                    "synchronisation primitives",
        decision="submit is UNSUPPORTED: the model will not accept a command buffer it does not "
                  "understand",
        conformance="PSM-GFX submit (UNSUPPORTED)"),
    Claim(
        id="C-GFX-04", subsystem="graphics", classification="RESTRICTED",
        statement="The GPU's firmware interface and its authentication",
        confidence="n/a",
        uncertainty="not public, and reaching it would require circumvention, which PC5 does "
                    "not perform (ADR-007)",
        decision="gpu_firmware is UNSUPPORTED and is never implemented; the row exists so the "
                  "refusal is explicit",
        conformance="PSM-GFX gpu_firmware (UNSUPPORTED)"),

    # ---------------------------------------------------------------- audio
    Claim(
        id="C-AUD-01", subsystem="audio", classification="PUBLIC",
        statement="The console provides \"Tempest\" 3D AudioTech",
        sources=("S-SONY-2020",), establishes="direct",
        value="Tempest 3D AudioTech",
        confidence="high: first-party specification table",
        uncertainty="the engine's programming interface, its formats and its timing guarantees "
                    "are not public",
        decision="the model negotiates PCM streams and refuses DSP operations",
        conformance="PSM-AUD open_stream"),
    Claim(
        id="C-AUD-02", subsystem="audio", classification="INFERRED",
        statement="Audio reaches the platform as bounded PCM buffers with a latency class",
        sources=("S-SONY-2020",),
        establishes="assumes a 3D audio engine consumes PCM frames from a buffer rather than "
                    "exposing an unbounded stream, which is how every documented audio path "
                    "works; the console-side detail is not public",
        confidence="low-medium: a modelling assumption, not a console fact",
        uncertainty="buffer depth, latency classes and underrun behaviour on the console are "
                    "not public",
        decision="buffers are sized from a PC5-chosen latency class in milliseconds and "
                  "reported in every response",
        conformance="PSM-AUD open_stream / write"),
    Claim(
        id="C-AUD-03", subsystem="audio", classification="UNKNOWN",
        statement="The console's audio DSP programming interface",
        confidence="n/a",
        uncertainty="no public source describes the DSP, its programs or their upload format",
        decision="dsp_load is UNSUPPORTED",
        conformance="PSM-AUD dsp_load (UNSUPPORTED)"),

    # ------------------------------------------------------------------ USB
    Claim(
        id="C-USB-01", subsystem="usb", classification="UNKNOWN",
        statement="The console's USB topology and its controller interface",
        confidence="n/a",
        uncertainty="no public primary source in this registry documents the console's USB "
                    "controller, addresses or transfer scheduling",
        decision="the model presents standard xHCI/HID semantics for PC hardware and says so; "
                  "it does not present itself as the console's controller",
        conformance="PSM-USB enumerate"),
    Claim(
        id="C-USB-02", subsystem="usb", classification="UNKNOWN",
        statement="How the console's system software binds controller input to a session",
        confidence="n/a",
        uncertainty="nothing public describes the binding, the report rate or the "
                    "disconnect semantics the system software expects",
        decision="the model validates HID report length, bounds the input queue and reports "
                  "removal as BAD_STATE rather than dropping input",
        conformance="PSM-USB hid_report / interrupt_in"),
    Claim(
        id="C-USB-03", subsystem="usb", classification="UNKNOWN",
        statement="Controller features beyond standard HID reports",
        confidence="n/a",
        uncertainty="nothing public describes the console's extended controller protocol, "
                    "and the 2020 post says nothing about input devices at all",
        decision="dualsense_features is UNSUPPORTED",
        conformance="PSM-USB dualsense_features (UNSUPPORTED)"),

    # ------------------------------------------------------------- services
    Claim(
        id="C-SVC-01", subsystem="services", classification="UNKNOWN",
        statement="The console's service layer and the interface it exposes to the system "
                  "software",
        confidence="n/a",
        uncertainty="no public source describes the service model, its names or its ABI",
        decision="the model exposes a PC5 service registry with explicit NO_DEVICE lookups; it "
                  "does not invent console service names",
        conformance="PSM-SVC service_lookup"),
    Claim(
        id="C-SVC-02", subsystem="services", classification="UNKNOWN",
        statement="The console's logging and diagnostic interfaces",
        confidence="n/a",
        uncertainty="nothing public describes them",
        decision="the model provides a bounded structured log whose overflow is reported, never "
                  "silently dropped",
        conformance="PSM-SVC log_write (OVERFLOW path)"),

    # ------------------------------------------------------------- security
    Claim(
        id="C-SEC-01", subsystem="security", classification="RESTRICTED",
        statement="The console's boot-chain authentication and its root of trust",
        confidence="n/a",
        uncertainty="the mechanism is not public, and reaching it would require circumvention",
        decision="M17 documents the boundary and PC5 performs no reference to it: no key "
                  "handling, no signature bypass, no attestation spoofing (ADR-007, ADR-022)",
        conformance="not modelled — no PSM interface exists for security state"),
    Claim(
        id="C-SEC-02", subsystem="security", classification="UNKNOWN",
        statement="What the system software does with authenticated state once running",
        confidence="n/a",
        uncertainty="no public source describes it",
        decision="PC5 states the unknown and does not build a substitute for it",
        conformance="not modelled — recorded so the gap is visible"),
)


#: PC5's own interface decisions: not claims about the console, and never cited as
#: if they were. Each names the claims it must remain consistent with.
DECISIONS: Tuple[dict, ...] = (
    {"id": "D-PSM-01", "interface": "PSM-CPU", "subsystem": "cpu",
     "decision": "The CPU double exposes only the documented leaves and refuses the rest.",
     "rationale": "A guest asking an uncovered question must get UNSUPPORTED, so a missing "
                  "model is visible instead of plausible.",
     "claims": ("C-CPU-04",)},
    {"id": "D-PSM-02", "interface": "PSM-CPU", "subsystem": "cpu",
     "decision": "Security-state MSRs return DENIED, architectural ones return PC5 values.",
     "rationale": "DENIED and UNSUPPORTED mean different things to a guest and must not be "
                  "conflated (ADR-007).",
     "claims": ("C-CPU-05",)},
    {"id": "D-PSM-03", "interface": "PSM-MEM", "subsystem": "memory",
     "decision": "One pool, first-fit allocation, explicit EXHAUSTED, mapping not copying.",
     "rationale": "Matches the one public fact (one pool) and refuses to invent the "
                  "allocation policy, which is UNKNOWN.",
     "claims": ("C-MEM-01", "C-MEM-02", "C-MEM-04")},
    {"id": "D-PSM-04", "interface": "PSM-MEM", "subsystem": "memory",
     "decision": "Protection domains are UNSUPPORTED.",
     "rationale": "The contract does not know them; a fabricated policy would be worse than "
                  "an explicit gap.",
     "claims": ("C-MEM-05",)},
    {"id": "D-PSM-05", "interface": "PSM-IRQ", "subsystem": "interrupts",
     "decision": "A PC5 event-queue abstraction with explicit BAD_STATE and NOT_READY.",
     "rationale": "The console's controller is UNKNOWN; the abstraction is named as PC5's.",
     "claims": ("C-IRQ-01", "C-IRQ-02")},
    {"id": "D-PSM-06", "interface": "PSM-TMR", "subsystem": "interrupts",
     "decision": "A virtual nanosecond clock; every response names its source.",
     "rationale": "Makes timeouts exact and replay deterministic, and never lets a synthetic "
                  "clock look like firmware.",
     "claims": ("C-TMR-01", "C-TMR-02")},
    {"id": "D-PSM-07", "interface": "PSM-IPC", "subsystem": "interrupts",
     "decision": "Depth-bounded named queues; OVERFLOW and TIMEOUT are explicit statuses.",
     "rationale": "A dropped message that the guest cannot observe is a silent protocol "
                  "failure.",
     "claims": ("C-IPC-01",)},
    {"id": "D-PSM-08", "interface": "PSM-STOR", "subsystem": "storage",
     "decision": "Tag-based submission with per-tag ownership and queue-depth back-pressure.",
     "rationale": "Standard NVMe semantics are public and testable; the console's extras are "
                  "not modelled.",
     "claims": ("C-STO-02", "C-STO-03")},
    {"id": "D-PSM-09", "interface": "PSM-GFX", "subsystem": "graphics",
     "decision": "Command submission and GPU firmware are UNSUPPORTED; contexts, fences and "
                 "PC5-owned surface presentation are modelled.",
     "rationale": "Accepting a command buffer PC5 cannot decode would be a fabricated success.",
     "claims": ("C-GFX-03", "C-GFX-04")},
    {"id": "D-PSM-10", "interface": "PSM-AUD", "subsystem": "audio",
     "decision": "PCM streams with PC5-chosen latency classes; DSP upload is UNSUPPORTED.",
     "rationale": "The DSP interface is UNKNOWN; the buffer contract is the part PC5 can own.",
     "claims": ("C-AUD-02", "C-AUD-03")},
    {"id": "D-PSM-11", "interface": "PSM-USB", "subsystem": "usb",
     "decision": "Standard xHCI/HID semantics with report-length validation and bounded "
                 "queues; extended controller features are UNSUPPORTED.",
     "rationale": "The PC side is ordinary hardware; the console's extensions are not public.",
     "claims": ("C-USB-01", "C-USB-02", "C-USB-03")},
    {"id": "D-PSM-12", "interface": "PSM-SVC", "subsystem": "services",
     "decision": "A PC5 service registry and a bounded log; unknown lookups are NO_DEVICE and "
                 "log overflow is OVERFLOW.",
     "rationale": "Console service names are UNKNOWN and must not be invented.",
     "claims": ("C-SVC-01", "C-SVC-02")},
    {"id": "D-PSM-13", "interface": "PSM-CPU", "subsystem": "cpu",
     "decision": "reset returns the double to power-on state and increments a reset epoch.",
     "rationale": "A deterministic replay needs a defined origin; the epoch makes a stale "
                  "handle visibly stale instead of silently valid.",
     "claims": ()},
    {"id": "D-PSM-14", "interface": "PSM-MEM", "subsystem": "memory",
     "decision": "stats reports only live handles, and a refused allocation changes nothing.",
     "rationale": "Exhaustion that leaks accounting would let a guest starve itself while the "
                  "numbers say otherwise.",
     "claims": ()},
)

CONTRACT = Contract(sources=SOURCES, claims=CLAIMS, revision="r1 (2026-11-16)")
