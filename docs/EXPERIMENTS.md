# Experiment registry

Every experiment PC5 runs or plans, with the question it answers, what would settle it, and
what actually happened. **Failed and blocked experiments stay in this file**: they are the
cheapest evidence a project can have, and re-running an experiment that already failed without
investigating it is the expensive alternative. Statuses:
`planned · ready · running · passed · failed · blocked · inconclusive · rejected`.

| ID | Question | Method / prerequisites | Expected | Actual | Status | Evidence | Next |
|---|---|---|---|---|---|---|---|
| **EXP-01** | Can the app tree's boundaries and claims be checked where no .NET SDK exists? | `python3 -m unittest tests.test_pc5_app_structure` (20 tests: core TFM purity, no Windows API in core, PC5.exe naming, brace balance, claim ban, CI permissions, roadmap cross-reference) | the gate passes and fails loudly on a violation | `Ran 20 tests … OK` after the two real defects it found (a claim-string false positive and a cited-but-missing validation document); before those, 4 failures | **passed** | this branch, 2026-11-28 | keep in `make check`'s suite |
| **EXP-02** | Can the app dimension's evidence be derived without a human asserting it? | `tools/app_evidence.py` over synthetic TRX files; 8 tests covering green, failing, absent, unparseable, all-skipped, caller-forced, claim-ban, read-only echo | `not_run`/`unknown` statuses when the file says so; refusal of `passed` with zero tests | `Ran 8 tests … OK`, including the refusal (`exit 1`, "zero executed tests") | **passed** | `tests/test_app_evidence.py` | wire the committed-record path in the follow-up that also fixes B-4 |
| **EXP-A** | What can `PC5.exe` honestly report about a Windows 11 host? | `RuntimeHostProbe` + `WindowsHostProbe` (WHP DLL loadability, registry-reported adapter), report file under `%LOCALAPPDATA%\PC5\reports` | a report where some rows are `unknown` | **not run** — needs a Windows host | ready (code), blocked (execution) | `docs/engineering/WINDOWS_VALIDATION.md` §5 | first `windows app` run |
| **EXP-B** | Does the lifecycle survive a real backend: init → start → pause → resume → stop, with cancellation and injected failures? | `Pc5TestBackend` + `TestBackendLifecycleTests` (10 tests, incl. "an injected failure never becomes a clean stop", "one pause handle owns a resume") | the state machine refuses every shortcut | **not run here** (no SDK); the *refusal* half is separately pinned by the legacy-suite test for the transition table | blocked (environment) | `app/tests/PC5.Core.Tests/` | CI, then label it E1+ |
| **EXP-C / VBP-1** | Can a PC5-created partition run a vCPU through Windows Hypervisor Platform on a CI runner? | WHP API from `PC5.App/Platform`; needs `HypervisorPlatform` feature enabled (administrator) + VT-x/AMD-V with SLAT | a logged exit-reason sequence for a PC5-authored real-mode workload | **not attempted**: the sandbox has no Windows and no hypervisor; `Enable-WindowsOptionalFeature` is out of scope for PC5 | blocked (environment + privilege) | `docs/research/VIRTUALIZATION_BACKENDS.md` §4b | decide the runner; if none, run it on the owner's machine |
| **EXP-D** | How does a guest surface reach the window? | framebuffer → swapchain/composition path, generic test image first | a presented surface, labelled "display-pipeline experiment" | not started | planned | — | after EXP-A |
| **EXP-E** | How does host input become guest input without pretending a DualSense exists? | RawInput/XInput capture → guest-visible device model | key/mouse/pad events in a log | not started | planned | — | after a guest exists |
| **EXP-F** | Does the firmware object the repository declares actually exist anywhere reachable? | `git cat-file -t 42ea0e5a…`; `.gitattributes`/`check-attr`; `ci/README.md`'s recorded `smudge filter lfs failed`, exit 128; `git lfs` unavailable in the sandbox | a definite answer with commands | **definite answer: no.** The pointer is real, the object is absent, and the absence has broken CI | **passed** (as a negative result) | `docs/research/PS5_FIRMWARE_AND_PUP.md` §1 | the owner's §5 procedure, if a package is lawfully held |
| **EXP-G** | Can `Pc5.Core.Firmware` distinguish the repository's pointer from a package? | compile + run `FirmwareArtifactTests` against the real pointer text | `Status.PointerOnly`, declared size 1,423,697,408, `ReadBytes` = the pointer's own length | **not run** — needs the SDK; the same assertion runs today in Python form via the repository's own tooling | blocked (environment) | `app/tests/PC5.Core.Tests/FirmwareArtifactTests.cs` | CI |
| **EXP-H** | Does the licence gate catch a wrong licence statement in prose? | the D-4 experiment: a README footer contradicting `LICENSING.md` on `main` | it should have caught it | **it did not** — `tools/licensing.py` reads only `LICENSING.md`/`LICENSE`; the wrong sentence reached `main` and was found by hand | **failed** (by design: kept as the reason for B-5) | [`TRANSFORMATION_BASELINE.md`](TRANSFORMATION_BASELINE.md) D-4 | extend the gate with third-party-notice exemptions |

## Rules this register enforces

1. An experiment earns a status only from its **actual result**, and `blocked` names the
   prerequisite that is missing — never a guessed outcome, never a substitute result.
2. An experiment that cannot run in this environment is recorded as *ready* or *blocked*, with
   the machine it does need. `docs/engineering/WINDOWS_VALIDATION.md` is that record for
   EXP-A…EXP-G.
3. `passed` on an infrastructure experiment proves E1/E2 territory only. The genuine-execution
   ladder (`E3`–`E6`) is not reachable from any row above, and no row here claims otherwise.
