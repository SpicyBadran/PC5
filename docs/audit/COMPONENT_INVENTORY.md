# Component inventory — what each part of this repository actually is

Measured at baseline `9b20228` (see [`../TRANSFORMATION_BASELINE.md`](../TRANSFORMATION_BASELINE.md));
line counts are `git cat-file` sums over `git ls-tree -r` on `main`. Status words are the
project's: *complete* (evidence at the level its milestone demands), *partial*, *stub*,
*experimental*, *historical*, *source only*. **Nothing here is credited by its filename.**

| Component | Purpose — what it really does | Implementation | Status | Dependencies | Evidence | Reuse for the Windows product | Migration action |
|---|---|---|---|---|---|---|---|
| `boot/` (13 files, 4,267 lines) | boot sector, stage 2, ESP/hybrid paths: gets a machine from firmware to a kernel | x86 assembly and C, linked by the Makefile | complete for its own gate | `kernel/` | `make unit`; the OVMF job in run `46102447128` | **research / reference** — a `PC5.exe` process cannot replace host firmware; its contract is a model of what a guest must be shown | keep in place; cite it in guest-boot research |
| `kernel/` (25 files, 6,942 lines) | 32-bit PC5 kernel: ACPI, PCI, ATA, ISO9660, FAT32, console, UI | freestanding C with its own linker script | partial (`M2a`, `M2b` in progress) | none beyond freestanding C | `make unit` → 165 checks; `docs/TEST_RESULTS.md` | **adaptable** — table-parsing and device enumeration are PC5's closest "what a guest sees" reference | keep; lift findings into the CPU/memory requirement documents |
| `kernel64/` (25 files, 5,107 lines) | 64-bit long mode: APIC, IDT, paging, TSS, MADT, timers | freestanding C and `.S` | partial | `boot/` | `make unit`, `tests/test_kernel64.py`, the 64-bit OVMF gate | **adaptable** — interrupt, exception and paging behaviour feeds any virtual-CPU design | keep; pair with `PS5_CPU_REQUIREMENTS` work |
| `psm/` (15 files, 3,541 lines) | PC5's package-manifest format and registry | C plus Python tools | complete for its conformance suite | none | `make psm`, `tests/test_psm_*` | **directly reusable** — package identity and verification is the shape of the firmware-metadata path | keep; `Pc5.Core.Firmware` mirrors its "identity recorded, never asserted" rule |
| `tools/mkiso.py`, `tools/verify_iso.py`, `tools/buildmeta.py` | build an image, then verify it with a *separate* parser so a bug cannot certify itself | Python | complete | none | ADR-001; the `build` job | **directly reusable pattern** — writer plus independent reader, which is what `app_evidence.py` copies | keep |
| `tools/completion.py`, `tools/roadmap.py`, `tools/presentation.py`, `tools/docs_check.py` | the progress model, the milestone graph, the generated prose, the documentation gate | Python | complete | `docs/architecture/roadmap.json` | `make completion-check`, `make roadmap`, `make docs-check` | **directly reusable** — the transformation's dimensions plug into these rather than standing beside them | extend to four dimensions (ADR-029) |
| `tools/progress_record.py` | files a verified Actions run as a committed evidence record, refusing a bot author | Python | complete and **operationally proven** | GitHub API | run `46104362009` → commit `9b20228` | **directly reusable** — `tools/app_evidence.py` is its sibling | keep the author-refusal guard intact |
| `tools/app_evidence.py` *(new)* | derives the app dimension's record from a TRX; refuses `passed` over zero executed tests | Python | partial — works, not yet wired to a committed record | none | `tests/test_app_evidence.py` (8 tests, run here) | new | add the commit path with its own refusal rules |
| `app/src/PC5.Core` *(new)* | lifecycle state machine, settings store, file log, capability model, execution contracts, firmware-integrity probe, service registry, build ceiling | C#, `net8.0`, zero package references | **source only** — no compiler has seen it | .NET 8 SDK | `tests/test_pc5_app_structure.py` here; `app/tests/PC5.Core.Tests` awaits a runner | new | build and test on CI, then pin the SDK and Windows App SDK versions from the run that succeeded |
| `app/src/PC5.App` *(new)* | the WinUI 3 window, the Windows-only host probes, the composition root | C#, `net8.0-windows10.0.19041.0`, Windows App SDK, MSIX | **source only** | `PC5.Core` | `.github/workflows/windows-app.yml`, first run pending | new | Windows runner validation, then the physical procedure in `docs/engineering/WINDOWS_VALIDATION.md` |
| `tests/` (64 files, 34,557 lines) | host-side unit and conformance tests, QEMU/OVMF harnesses, stub firmware, machine models | Python and C | complete for what they cover | `unicorn` for the machine tests (debt D-8) | 1382 host tests OK at `a79df48` | **reusable as a pattern** — behaviour tests, negative controls, refusal tests | keep; add the app's validation layers beside it, never inside it |
| `evidence/` + `docs/evidence/` (181 files, 85,121 lines) | the record: run logs, verification results, hardware records | JSON and text | historical and live | — | `docs/evidence/ci/46102447128.json`, self-filed | **retain as the record** | never rewritten; the app dimension appends its own subdirectory |
| `docs/` (141 files, 29,302 lines) | the specification corpus: architecture, contracts, feasibility, gates, research | Markdown | mixed | the generators above | `make docs-check` | retain | add the transformation documents beside them; correct, never delete |
| `brand/` (12 files), `docs/assets/` (7), the SMIL dashboard | PC5's own identity and its generated presentation | SVG, SMIL | complete | `tools/presentation.py` | `make brand-check`, `make presentation-check` | retain — PC5's identity, deliberately not PlayStation's | keep; the README redesign reuses the generators |

## What this inventory refuses to say

It does not call the PC5 kernel a PlayStation kernel. It does not treat `PS5UPDATE.PUP` as
firmware — it is a pointer with no object behind it
([`../research/PS5_FIRMWARE_AND_PUP.md`](../research/PS5_FIRMWARE_AND_PUP.md)). It does not
describe an OVMF boot as hardware validation. And it grades nothing in `app/` above *source
exists* until a Windows run says otherwise.

## Documents this one does not duplicate

The migration record is [`../architecture/MIGRATION_PLAN.md`](../architecture/MIGRATION_PLAN.md);
the earlier repository audit is
[`../architecture/REPOSITORY_AUDIT_2026-11-15.md`](../architecture/REPOSITORY_AUDIT_2026-11-15.md);
the subsystem contracts are
[`../architecture/PS5_PLATFORM_CONTRACT.md`](../architecture/PS5_PLATFORM_CONTRACT.md); the risk
register is [`../architecture/RISK_REGISTER.md`](../architecture/RISK_REGISTER.md); the frozen
state summary is [`../TRANSFORMATION_BASELINE.md`](../TRANSFORMATION_BASELINE.md). This table is
the component view those documents do not carry: one row per component, with a reusability
verdict and a migration action.
