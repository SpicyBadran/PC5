# `app/` — the PC5 Windows 11 application

This is the source tree for **`PC5.exe`**, the Windows host application PC5 is building
toward the objective of running the genuine Sony PlayStation 5 operating system.

```
app/
├── PC5.sln                      the solution: two projects and their tests
├── Directory.Build.props        shared language/analysis rules for everything under app/
├── global.json                  the .NET SDK floor this tree is written against
├── src/PC5.Core/                net8.0, dependency-free: lifecycle, settings, logging,
│                                diagnostics, execution contracts, firmware integrity reads
├── src/PC5.App/                 WinUI 3 / Windows App SDK, x64, MSIX. The window, and the
│                                Windows-only host probes. No lifecycle rule lives here.
└── tests/PC5.Core.Tests/        behaviour tests for the core, runnable on any .NET 8 host
```

## What is real here

| Claim | Status |
|---|---|
| Source exists for a core library, a Windows application and tests | yes, in this directory |
| The core has no Windows reference | enforced by `tests/test_pc5_app_structure.py` |
| The application builds and runs on Windows 11 | **not yet validated.** No `dotnet` exists in the environment this was written in; CI's `windows app` workflow is the first place it will be |
| Anything PS5 executes | **no.** The only backend in the tree is `Pc5TestBackend`, named "PC5 test backend (not PS5)" in its own `DisplayName` |

The build, test and packaging commands, and the physical-Windows procedure that proves them,
live in [`../docs/engineering/WINDOWS_VALIDATION.md`](../docs/engineering/WINDOWS_VALIDATION.md).
The evidence ladder this tree is graded against (`E0`…`E6`) is
[`../docs/research/VIRTUALIZATION_BACKENDS.md`](../docs/research/VIRTUALIZATION_BACKENDS.md) and the
milestone graph; `app/` sits at **E0** by design until a Windows job has run.

## Rules for anyone adding to this tree

1. `PC5.Core` stays `net8.0` with **zero** package references, so its tests run anywhere.
2. A new execution backend declares its own `MaximumEvidenceLevel` before it can be selected.
3. No string in this directory may say PS5 software ran, boots, or is supported.
4. The window renders what the core computed; it computes nothing itself.
