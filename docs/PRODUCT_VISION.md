# Product vision — what PC5 is, permanently

| | |
|---|---|
| Authority | the owner's product statement, recorded 2026-11-28. No agent may redefine it; only the owner can |
| Baseline | [`TRANSFORMATION_BASELINE.md`](TRANSFORMATION_BASELINE.md) at `9b20228` |
| Decisions | [`architecture/ARCHITECTURE_DECISION_RECORD.md`](architecture/ARCHITECTURE_DECISION_RECORD.md) ADR-028…ADR-036 |

## 1. The product

**PC5 is a Windows 11 application named `PC5.exe` whose objective is to host and execute the
genuine, original Sony PlayStation 5 operating system inside its window.**

The intended experience, in the owner's sequence: the user opens `PC5.exe`; PC5 initialises
its host environment and the required execution or virtualisation architecture; PC5 loads
legitimately available original PS5 system-software components through a technically valid
path; the real guest boots; the genuine guest environment is presented inside the
application; PC5 manages lifecycle, graphics, input, storage, audio, devices, diagnostics and
shutdown.

Host, application, engine, guest — four different things, and PC5 keeps the words separate:
Windows 11 is the host; `PC5.exe` is the application; PC5's execution engine is the guest
environment; the PlayStation 5 operating system is the guest.

## 2. What PC5 is not

A launcher. A theme. A dashboard that looks like a console. A screenshot collection. A mock
operating system. A generic virtual machine presented as a PlayStation. A visual recreation
relabelled as execution. Infrastructure progress may be real progress on the way to the
objective and is **never** reported as the objective: the report in
[`PROGRESS.md`](PROGRESS.md) is split into dimensions for exactly this reason.

## 3. The three categories, which are not interchangeable

| Category | Meaning | Where PC5 stands today |
|---|---|---|
| **1 — genuine execution** | Sony's own system software running | not achieved; blocked on a lawful input and on hardware-anchored security (see [`architecture/EXECUTION_FEASIBILITY.md`](architecture/EXECUTION_FEASIBILITY.md)) |
| **2 — independent implementation** | PC5's own code reproducing observable behaviour, contracts and formats | the boot/image corpus, the PSM registry, the evidence pipeline, `app/` |
| **3 — hybrid** | PC5's code driving, wrapping or attaching to genuine execution | not started; the interface `IExecutionEngine` exists so it can be attempted honestly |

A deliverable carries one category. Mixing them is the substitution this project forbids, so
the category is data in the roadmap, not prose (ADR-031).

## 4. Boundaries that do not move

* No proprietary Sony firmware, keys or derived binaries in the repository, ever.
* No bypassing signatures, encryption, secure boot, authentication or access controls.
* No fabricated results: no invented test outcome, no "works on Windows" without a Windows
  record, no percentage that a generator did not compute.
* No deletion of existing engineering because direction changed. History is preserved; the
  legacy kernel is never described as the Sony kernel — it is PC5's own.
* Lawfulness is not an obstacle to be routed around. When a requirement is unreachable, PC5
  names it precisely and keeps building what does not depend on it.

## 5. The evidence ladder the product is graded on

`E0` research · `E1` the application builds · `E2` a generic guest boots · `E3` a PS5-related
component is identified and validated · `E4` authorised PS5 system-software components execute ·
`E5` the genuine PS5 OS boots · `E6` the genuine guest operates inside `PC5.exe` with devices
and services validated. A generic VM is never E5; a themed screen is never E5; a mock boot
animation is never E5.

The same ladder is in code — [`../app/src/PC5.Core/Execution/Contracts.cs`](../app/src/PC5.Core/Execution/Contracts.cs)
declares `EvidenceLevel`, and `ExecutionStatus.AllowableClaim()` returns the sentence a level
may print, so the ceiling is not only a policy document but the string a UI is capable of
showing.

## 6. Where the work goes next

`M22`–`M29` in [`architecture/roadmap.json`](architecture/roadmap.json) — entered on this
branch, under the graph's own evidence rules (`complete` needs acceptance *and* an existing
evidence path), numbered after the existing `M0`–`M21` rather than renaming them, with
`WS14` as their workstream and ADR-036 as the mapping. Declaring them moved the published
figure from 26.7 % to 23.4 %: new scope costs progress, it does not manufacture it.
tracked per dimension in [`PROGRESS.md`](PROGRESS.md), and reported by separate CI workflows:
[`../.github/workflows/windows-app.yml`](../.github/workflows/windows-app.yml) for the app
dimension, `build.yml` for the legacy image, and `progress.yml` for the evidence loop that
writes the findings back into the README.
