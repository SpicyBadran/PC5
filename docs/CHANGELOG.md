# Changelog

What PC5 has actually done, newest first. Entries record delivered changes with their commit
or run; they do not record intentions. The generated progress figures live in the README and
`docs/ABOUT.md`, not here.

## Unreleased — on `arena/5e36f38b-pc5` (PR #55)

Carried by `c433362` (the application and its dimension), `6a83af9` (derived figures),
`b207163` (root-level debts), `2ec836b` (governing pages) and `74fa139` (this file's
neighbours). Every item below is a delivered change on that commit; nothing here is a plan.

### Added
- `docs/SECURITY.md` §5b — the Windows application's host surface, stated as written rather
  than observed: settings file, log file, the single read-only registry key, `WinHvPlatform.dll`
  loadability, packaging, and the two rules that bind anything added under `app/`.
- `CONTRIBUTING.md` — "Working on the Windows application (`app/`)": the four commands, the
  core/shell boundary as a rule, the backend ceiling declaration, and the sentence that
  nothing under `app/` may be called working on Windows from a Linux build.
- `docs/TRANSFORMATION_BASELINE.md` §12 — a dated follow-up on the debt register, appended
  rather than edited into §5, because a baseline that rewrites itself stops being a baseline.
- `docs/TRANSFORMATION_BASELINE.md` (`2e5d39f`) — the frozen measurement of `main` at
  `9b20228`: inventory, what works with its evidence, roadmap states, and debt `D-1`…`D-10`.
- `docs/research/PS5_FIRMWARE_AND_PUP.md` (`2e5d39f`) — the firmware input measured as a
  135-byte Git LFS pointer with no object behind it, plus the public format reports with
  reliability tiers and the owner's verification procedure.
- `docs/architecture/EXECUTION_FEASIBILITY.md` (`2e5d39f`) — path verdicts for genuine
  execution, with the 2026 field evidence and the legal line.
- `docs/architecture/ARCHITECTURE_DECISION_RECORD.md` (`2e5d39f`) — ADR-028…ADR-035,
  continuing `docs/TECHNICAL_DECISIONS.md`.
- `app/` (31 files) — the Windows application tree: `PC5.Core` (lifecycle state machine,
  settings store, file log, capability model, execution contracts with the E0–E6 ladder,
  firmware-integrity probe, service registry, build-info ceiling), `PC5.App` (WinUI 3 shell
  whose window holds no policy, Windows-only host probe, composition root), and
  `PC5.Core.Tests` (behaviour tests). Source exists; it has **not** been compiled, because this
  environment has no .NET SDK — see the `Windows application` row of `docs/PROGRESS.md`.
- `.github/workflows/windows-app.yml` — the `app` evidence dimension: portable core build +
  tests on Linux, Windows build + MSIX packaging + hashes on `windows-latest`, artifacts
  uploaded, limitations printed. It cannot write to the repository (`contents: read`).
- `tools/app_evidence.py` + `tests/test_app_evidence.py` — derives the app dimension's
  evidence record from a TRX file; refuses `passed` over zero executed tests; cannot be
  talked into a guest-execution claim. 8 tests pass on this host.
- `tests/test_pc5_app_structure.py` — the boundary, naming and claim gates for `app/` that run
  without a .NET toolchain (20 tests).
- `docs/engineering/WINDOWS_VALIDATION.md`, `docs/research/VIRTUALIZATION_BACKENDS.md`,
  `docs/audit/COMPONENT_INVENTORY.md`, `docs/PRODUCT_VISION.md`, `docs/PROGRESS.md`,
  `docs/EXPERIMENTS.md`, `docs/AGENT_HANDOFF.md`, `app/README.md`, `AGENTS.md` — governance,
  the owner-executable Windows procedure, the component inventory with a reusability verdict
  per component, and the M2 backend comparison.
- `docs/architecture/roadmap.json` — workstream `WS14` (the Windows host application), the
  role `application-engineer`, and milestones **`M22`–`M29`** carrying the owner's directive
  `M0`–`M7` (ADR-036). Published scope moved **down** from 26.7 % to 23.4 % because eight
  milestones of undeclared-until-now work entered the denominator.
- `tools/completion.py` — `app_summary()`: the README's new **Windows application** row, read
  from `docs/evidence/app/*.json` and printed as "no record filed" while none exists, so the
  application dimension is visible as an absence instead of being inferred from the legacy row.
- `tools/completion.py` — `tag_facts()` and `publication_facts()`: release state is now
  *derived* from the filed CI record and the tag store, with `unknown` when neither speaks
  (closes debt D-6).

### Changed
- `tools/docs_check.py` — the licence gate now reads a licence stated **in words**
  ("GNU General Public License version 2 only"), not only an SPDX identifier, across
  `README.md`, `docs/ABOUT.md`, `CONTRIBUTING.md`, `docs/BRAND.md`, `AGENTS.md` and
  `docs/SECURITY.md`; quotations and code spans stay exempt so a corrected mistake can still
  be written down (closes the gate half of debt D-4, with three new tests in
  `tests/test_docs_check.py`).
- `tools/ci_report.py` — a probe that fails for a missing dependency is reported as
  `not available (ModuleNotFoundError: …); the machine-model tests import it; pip install
  -r requirements-harness.txt`, never as a pasted traceback (closes debt D-8: two
  `test_ci_report` tests used to fail on any host without `unicorn`).
- `.gitattributes` — the inherited Unity ruleset (`## Unity ##`, `merge=unityyamlmerge` on
  `.mat`/`.unity`/`.asset`/`.meta`) is replaced by rules for the tree PC5 has (`.cs`, `.xaml`,
  `.csproj`, `.props`, `.sln`, `.trx`), keeping the file's line count so that no citation of a
  line number in it can drift. The `*.pup` / `PS5UPDATE.PUP` LFS routing is untouched (closes
  debt D-2).
- `README.md`, `docs/ABOUT.md`, `docs/assets/*.svg`, `docs/ROADMAP.md`,
  `docs/architecture/PC5_MILESTONE_GRAPH.md` — regenerated by their own tools
  (`make presentation-check` and `make roadmap` refuse a hand edit).

- `tools/ci_annotations.py` + `tests/test_ci_annotations.py` (16 tests) — quotes a build or
  test log's compiler errors as GitHub workflow annotations, so a hosted failure is legible to
  a reviewer who cannot reach the runner's log body. It always exits 0: a reporter must never
  become the verdict it is reporting.

- `tests/test_pc5_app_structure.py` — the tree-walking gates now skip generated output
  (`bin/`, `obj/`, `TestResults/`, `build/`, `.vs/`) through one `walk()` helper, and `.gitignore`
  declares those paths for `app/`. The first hosted run of the structure gates failed on
  `app/src/PC5.Core/obj/Debug/net8.0/AssemblyInfo.cs` — a file no human wrote, carrying no
  `namespace`, judged by a rule about human-written source. Proven by planting that exact path
  and watching the 24 gates pass.
- `.github/workflows/windows-app.yml` — `dotnet test` ran with `--no-build` on a test project
  that nothing had built (only `PC5.Core` was restored and built), so the *unit test* step of
  the first legible run died on the harness rather than on an assertion. Both jobs now restore
  and build the test project's needs (`--no-restore`, no `--no-build`). The invented packaging
  switches `-p:PublishProfile=Default` and `-p:RuntimeIdentifierOverride=win-x64` are gone: they
  were plausible guesses that no run had ever verified, and the Windows job now publishes the
  application folder with a documented `-p:RuntimeIdentifier=win-x64 --self-contained false`
  and states that **no signed `.msix` is produced here** — that is `M29`, and it needs a signing
  identity that deliberately does not live in this repository.

### Fixed
- `.github/workflows/windows-app.yml` — its first hosted run (`46109038081`) failed twice over:
  the *File the evidence record* step piped into `tee build/app-evidence.json` in a directory
  only the (skipped) test step created, and two multi-line `run:` blocks passed a folded newline
  to the shell, which read `--logger …` as its own command. Both jobs now create their output
  directories first, every command is a `|` block with real continuations, and the structural
  gates run with `if: always()` so a compile failure no longer hides them. **The build itself
  still fails** — that is `app/`'s own state, not the workflow's, and it is now annotated.
- `README.md` (`2e5d39f`) — the licence footer said "GNU General Public License version 2
  only" while `LICENSING.md` grants `GPL-3.0-only` over a verbatim GPLv3 `LICENSE`.

## `main` — 2026-11-28

- **The CI evidence loop became self-acting.** Run `46104362009` (`workflow_run`) completed
  all 14 steps and wrote commit `9b20228` — authored and committed by `github-actions[bot]` —
  filing `docs/evidence/ci/46102447128.json` and updating the Hosted CI rows in `README.md`
  and `docs/ABOUT.md` from run `46102447128` (1382 host tests OK, 13 skipped). The bot's push
  triggered no further build: the loop terminates. (PRs #51–#54, merged as `c7234f7`,
  `c9226a7`, `70803cb`, `a79df48`.)

## `main` — 2026-11-26

- Licence model: `LICENSE` (verbatim GPL-3.0 text), `LICENSING.md` (the grant),
  `tools/licensing.py`, ADR-027, `tests/test_licensing.py` (PR #50 chain).
- Progress dashboard: animated completion figure and generated About page, with the gates that
  keep both true (PR #51).

## Earlier

- The boot/image corpus, the PSM registry, the ISO identity chain, the OVMF boot gate and the
  release-attempt history are recorded in `docs/` and `docs/evidence/`; this file starts where
  the transformation starts, rather than restating 442 files.
