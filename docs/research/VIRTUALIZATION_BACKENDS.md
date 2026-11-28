# Host execution backends — the M2 comparison, before any of them is chosen

| | |
|---|---|
| Question | what can `PC5.exe` usefully run a guest on, given the objective is the genuine PS5 operating system and not "a VM" |
| Method | each candidate is assessed against the requirements a PS5 guest would place on a host, not against developer convenience |
| Status | **research (E0).** No backend experiment has been executed in this repository; the environment has no Windows and no hypervisor access. No candidate is selected by this document |
| Related | [`../architecture/EXECUTION_FEASIBILITY.md`](../architecture/EXECUTION_FEASIBILITY.md) (path verdicts), [`../playstation/BOOT_FEASIBILITY.md`](../playstation/BOOT_FEASIBILITY.md) (dependency classes), [`../architecture/ARCHITECTURE_DECISION_RECORD.md`](../architecture/ARCHITECTURE_DECISION_RECORD.md) ADR-037 |

## 1. What the guest would demand, before what a host offers

From the feasibility analysis: the PS5 CPU is x86-64 (Zen 2), so *instruction* execution on a
PC is not the obstacle; the obstacles are (a) per-component code encryption with keys bound
to on-die storage, (b) a boot chain and authentication PC5 neither holds nor may bypass,
(c) guest-visible devices with no PC equivalent, and (d) a hypervisor layer the console's own
security model sits on. A backend that cannot present all four gets PC5 *to a running guest*,
which is E2, and E2 is not the objective.

## 2. Candidate comparison

| Criterion | Windows Hypervisor Platform (WHP) | Hyper-V / VHM native partition | QEMU/KVM-style emulator ported to Windows (accel: WHPX/HAXM) | Full software emulation (no hypervisor) | Direct exec / paravirtual "own hypervisor" |
|---|---|---|---|---|---|
| What it is | documented Win32 API (`WinHvPlatform.dll`) to create a partition, vCPUs and run them | the same hypervisor, through the VM management stack rather than the developer API | an existing machine model, using a Windows hypervisor accelerator for speed | interpreting the guest's instructions in software | PC5 writes its own ring -1 layer and runs the guest above it |
| Guest CPU fidelity | host CPU as-is, exposed with partition-level controls; no CPUID re-signing beyond what the API allows | same, with management-tool defaults | high: the emulator controls CPUID and feature bits, the accelerator only runs them | complete control, no speed | total control |
| Memory model | partition-configured GPA space; per-vp translate APIs available | same | emulated + host-mapped | fully modelled | PC5-defined |
| Interrupt/timer virtualisation | posted-interrupt and APIC-virtualisation behaviour is the hypervisor's, exposed through the API | platform-managed | emulator decides, hypervisor assists | emulator decides entirely | PC5 decides |
| Device model flexibility | the API stops at CPU/memory/exit handling; **every device is PC5's to write** | VSP/VSC stack, not a free-form device model | QEMU's model exists but is a large dependency to port and license-check | PC5 writes it, slowly, with full control | PC5 writes it |
| GPU path | none provided | none provided | none (guest driver would still need a real GPU interface) | none | none |
| Windows 11 support | user-mode API in `WinHvPlatform.dll`, header/lib from the Windows SDK, x64 and Arm64; the documented floor is Windows 10 1803 for the feature and 20H2 for the newer advisory calls. PC5's probe reports only that the DLL loads (`WindowsHostProbe`), not that the feature is enabled or usable for this purpose | present in every Windows 11 edition, restricted feature set outside Business SKUs | requires the WHPX accelerator or software TCG on Windows | any | requires the hypervisor to win ring -1 first, i.e. a host reboot into PC5's own loader — not a Windows application any more |
| Licensing cleanliness | Microsoft API, no code to carry | same | upstream is GPL v2 — a vendoring decision PC5 has not made and ADR-025 forbids by default | clean if written fresh | clean if written fresh |
| Debuggability | exit reasons and register state are queryable | richest tooling | best emulator debugging culture | total, and slow | only as good as PC5's own instrumentation |
| Distance to the objective | lets PC5 *run a guest*, not boot the console | same, with less control | fastest route to a rich machine model | the only model where PC5 can invent a device a console has | not available to an application |
| Verdict for M2 | the **probe target** for host capability and lifecycle plumbing | not chosen: too little control for a device model PC5 must own | **not adopted here**: adopting an upstream emulator is a licensing and identity decision (ADR-025), not a dependency bump | the long-term fallback for specific devices, per-subsystem | rejected as a *product*: it cannot be what a Windows 11 application does |

## 3. The honest limit of this table

Rows above say what an API is *documented* to allow, which is weaker than what PC5 has
measured. Until experiment `VBP-1` runs, no row may be quoted as "supported on Windows 11".
Nothing here was tested: this environment has no hypervisor, no Windows, and no .NET SDK.

## 4. Experiments, in the order that removes the most uncertainty

| ID | Question | Method | Outcome that would matter |
|---|---|---|---|
| **VBP-1** | Can a PC5-hosted partition be created and a vCPU run on a Windows 11 runner through WHP at all? | build `PC5.Host.Virtualization` over the WHP API; boot a **PC5-authored real-mode test workload** (an `E2` guest, explicitly not PS5); log exit reasons | a pass proves the host plumbing; a `not_run` on a CI image without the role proves the prerequisite list is real |
| VBP-2 | How far can guest-visible CPUID be shaped through the API? | read back the CPUID a guest sees for a requested topology | decides whether a guest's own CPU assertions could ever be met |
| VBP-3 | What does the guest see for memory below 1 MiB and for the E820-style map a BSD kernel probes? | map a known layout, read it from inside the guest | feeds `docs/research/PS5_MEMORY_REQUIREMENTS.md` (next) |
| VBP-4 | Is a PM4-style command stream to a host GPU translatable at all, or only interpretable? | offline: decode a captured *PC5-authored* test stream against D3D12/Vulkan | bounds the graphics claim before anyone writes a driver |
| VBP-5 | Which single PS5 boot requirement is the first to fail when a generic guest succeeds? | run VBP-1 against the dependency classes in `BOOT_FEASIBILITY.md` | this is the experiment that starts to close the E2→E5 gap, and it needs a lawful input first |

VBP-1 is the next engineering step after this document and is scoped as `M2` work:
`IExecutionEngine` already exists so that its result cannot leak into the UI as a claim — the
backend it exercises must declare `MaximumEvidenceLevel = GenericGuest`, exactly as
`Pc5TestBackend` is required to.

## 4b. Two reported requirements that a CI runner will not satisfy

* Enabling the feature is an **administrator** operation — `Enable-WindowsOptionalFeature
  -Online -FeatureName "HypervisorPlatform"` — which is outside what PC5 may ask of a user
  and outside what a GitHub-hosted runner is expected to do for us. VBP-1 must therefore
  *report the precondition*, not install it.
* The reported CPU prerequisites are a 64-bit CPU with virtualization enabled in firmware and
  SLAT (Intel VT-x/EPT or AMD-V/RVI). **Recorded contradiction:** one source states the
  feature is available on all desktop editions including Home, another on Pro/Enterprise/
  Education only. PC5 does not resolve vendor SKU policy; `PC5.exe` will report the observed
  state and treat the question as an environment fact, not a design assumption.

## 5. Sources

WHP statements come from Microsoft's own virtualization API documentation — the
[`WHvAdviseGpaRange`](https://learn.microsoft.com/en-us/virtualization/api/hypervisor-platform/funcs/whvadvisegparange)
requirement table (`WinHvPlatform.h` / `.lib` / `.dll`, x64 and Arm64, minimum
Windows 10 20H2 / Windows 11 24H2 on Arm64) — and are corroborated for the availability and
enablement details by secondary reporting: [edition and prerequisite comparison](https://techdows.com/whats-the-difference-between-hyper-v-virtual-machine-platform-windows-hypervisor-platform.html),
[the administrator-only enable step](https://www.elevenforum.com/t/enable-or-disable-windows-hypervisor-platform-in-windows-11.38917/),
and [the feature's general description](https://laptopjudge.com/what-is-the-windows-hypervisor-platform/).
The last three are tier D; they are used only for statements the primary page supports.
PS5-side claims are **not** taken from this table but from the sources tiered in
[`PS5_FIRMWARE_AND_PUP.md`](PS5_FIRMWARE_AND_PUP.md) §4 and the citations in
[`../architecture/EXECUTION_FEASIBILITY.md`](../architecture/EXECUTION_FEASIBILITY.md). This
document deliberately cites nothing it could not show a reader, and adds no vendor claim of
its own.
