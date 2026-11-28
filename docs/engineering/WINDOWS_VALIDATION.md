# Validating PC5 on Windows 11 — the procedure CI cannot replace

| | |
|---|---|
| Status | **not executed.** Not one command below has been run by the author of `app/`: the development environment is Linux with no .NET SDK (`command -v dotnet` → absent). This document exists so that the person with a Windows 11 machine can produce the evidence, and so nobody has to guess what "it works" was supposed to mean |
| Proves | evidence level **E1** (the application builds), **E1+** when the launch and diagnostics steps pass |
| Does not prove | that a guest operating system runs, and above all that PlayStation 5 system software runs. Nothing in this procedure can produce E5 or E6 |
| Related | [`../../app/README.md`](../../app/README.md), [`../research/VIRTUALIZATION_BACKENDS.md`](../research/VIRTUALIZATION_BACKENDS.md), [`../PROGRESS.md`](../PROGRESS.md) |

## 1. Prerequisites on the host

| Item | Requirement | How to check |
|---|---|---|
| Windows edition | Windows 11 x64 (the target host). Windows 10 21H2+ may build it; it is not the stated target | `winver` |
| .NET SDK | 8.0.100 or newer, `rollForward: latestMajor` per [`../../app/global.json`](../../app/global.json) | `dotnet --version` |
| Visual C++ runtime | needed by the Windows App SDK runtime at run time | the MSIX declares it; `winget install Microsoft.VCRedist.2015+.x64` if launching fails to find a runtime |
| Developer mode / signing | required to install a locally built (unsigned) MSIX | Settings → System → For developers |
| Virtualisation (only for later milestones) | not needed for E1 | `Get-ComputerInfo -Property HyperV*` in an elevated PowerShell |

Nothing here needs administrator rights for build and test. Installing an unsigned package
locally is the one step that changes system state, and `Add-AppxPackage -Register` (step 6)
avoids even that.

## 2. Build and test

```powershell
cd app
dotnet restore src/PC5.Core/PC5.Core.csproj
dotnet build   src/PC5.Core/PC5.Core.csproj -c Release
dotnet test    tests/PC5.Core.Tests/PC5.Core.Tests.csproj -c Release --no-build `
               --logger "trx;LogFileName=core.trx" --results-directory build/trx
dotnet build   src/PC5.App/PC5.App.csproj -c Release -p:Platform=x64
```

Expected: four exit codes of 0, and a TRX whose `Passed` count equals its test count.
Record the SDK version printed by `dotnet --version` — the version is pinned *from a
successful run*, not from assumption, so this line is where the pin comes from.

## 3. Package

```powershell
dotnet publish src/PC5.App/PC5.App.csproj -c Release -o build/publish
Get-ChildItem build/publish -Include *.exe,*.msix,*.appx,*.pri -Recurse |
    Get-FileHash -Algorithm SHA256 | Format-Table Hash, Path -AutoSize
```

Expected: `build/publish/PC5.exe` exists. Keep the hash table — it is the artifact record,
and the CI job writes the same list to `build/app-publish/artifact-hashes.txt` so the two can
be compared.

## 4. Launch, and what the window must show

```powershell
Add-AppxPackage -Register build/publish/AppxManifest.xml   # or double-click the .msix
```

The window must show, without you asking it for anything:

| Field | Required content |
|---|---|
| Title line | `PC5 <version> - evidence ceiling: ApplicationBuilds. genuine PlayStation 5 operating-system execution has not been demonstrated` |
| State | `NotInitialized` before you press anything |
| Firmware input | `Absent` (or `PointerOnly` if you point settings at the repository's pointer file) |
| Host capabilities | populated only after **Detect host capabilities** |

If the window opens and says anything stronger than that — any wording in which a PlayStation
is running, booted, or "supported" — that is a **defect to report**, not a success.

## 5. Diagnostics checks

1. **Detect host capabilities** → the pane fills with `name: yes|no|unknown`, each with an
   `evidence`, `meaning` and `does not` line. `unknown` lines **must** remain; a machine
   reporting no unknowns at all means a probe fabricated an answer.
2. A report file is written under `%LOCALAPPDATA%\PC5\reports\host-capabilities-*.txt`;
   the same text the pane shows.
3. `logs\pc5.log` under `%LOCALAPPDATA%\PC5\` contains `app.001`, `ui.001`, and one line per
   state transition with its reason.
4. **Start test backend** → state `Running`, and the claim line must read
   `a generic guest ran under test-backend; this is not PS5 execution`.
5. **Stop** → state `Stopped`. Closing the window afterwards must leave no `PC5.exe` process
   (`Get-Process PC5 -ErrorAction SilentlyContinue` prints nothing).
6. Point `FirmwarePath` at the repository's `PS5UPDATE.PUP` and restart: the window should say
   `PointerOnly`, i.e. PC5 correctly recognising a 135-byte LFS pointer as *not* a package.

## 6. Failure reporting

Attach, in this order: the log file, the report file, the TRX, the hash table, `dotnet
--version`, and the exact command that failed. Then, and only then, describe what you saw.

If the failure is "the app built and the capability pane says `yes` to something PC5 then
cannot use", that is the most important class of defect this project can receive: an
over-claim in a diagnostics surface. File it as such.

## 7. What CI does instead

The `windows app` workflow ([`../../.github/workflows/windows-app.yml`](../../.github/workflows/windows-app.yml))
runs steps 2–4 and uploads the artifacts; it files `app-windows-evidence.json` through
`tools/app_evidence.py`, which derives status from the TRX and refuses "passed" over zero
executed tests. Steps 4–6 need a desktop session and cannot be automated by that workflow,
which is why this page exists.
