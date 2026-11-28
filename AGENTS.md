# PC5 — agent instructions

> PC5 is an engineering project whose primary objective is to run the ORIGINAL Sony
> PlayStation 5 operating system inside a Windows 11 application named PC5.exe. The project
> must not be redefined as a simulator, visual clone, theme, launcher, dashboard or
> independent replacement operating system.

**Read this before touching anything.** Then read
[`docs/TRANSFORMATION_BASELINE.md`](docs/TRANSFORMATION_BASELINE.md) (the measured state),
[`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md) (the permanent definition),
[`docs/PROGRESS.md`](docs/PROGRESS.md) (what is true right now, per dimension), and
[`docs/architecture/ARCHITECTURE_DECISION_RECORD.md`](docs/architecture/ARCHITECTURE_DECISION_RECORD.md)
(the decisions you may not quietly reverse). Conversation history is not a source of truth;
this repository is.

## 1. Product identity

The objective is *genuine original PS5 system software executing inside `PC5.exe`*. Host
(Windows 11), application (`PC5.exe`), execution engine (PC5's guest environment) and guest
(Sony's system software) are four different things, and prose, code and progress figures keep
them separate. A beautiful interface, a working window, a green test suite, a generic VM or a
convincing demo is *supporting work*, never the objective reported as achieved.

Prohibited substitutions, without exception: PS5-style dashboards, themes, launchers,
graphical mockups, a "mock operating system", a generic virtual machine presented as a
PlayStation, or an independent OS described as Sony's. Where PC5 implements observable
behaviour itself, it says so and labels the category (§3).

## 2. Technical honesty

1. **Evidence over intent.** A claim needs a record: a run id, a log, a test name, a commit.
   `docs/PROGRESS.md` links each claimed capability to its evidence; if you cannot link it,
   you have a *task*, not a capability.
2. **The evidence ladder** (`EvidenceLevel` in `app/src/PC5.Core/Execution/Contracts.cs`):
   `E0` research · `E1` application builds · `E2` generic guest boots · `E3` PS5 component
   identified · `E4` authorised components execute · `E5` genuine PS5 OS boots · `E6`
   integrated guest. A generic guest is **never** E5. A screenshot is never any level.
3. **Unknown is a value, not a failure.** `null`/`unknown` must never be rendered as
   `passed`, `supported`, or `false`. Do not convert a skipped test into a pass, or a
   `not_run` into a `failed`, or a failed API call into a negative finding.
4. **Absence is stated.** The repository's `PS5UPDATE.PUP` is a 135-byte Git LFS pointer with
   no object behind it (`docs/research/PS5_FIRMWARE_AND_PUP.md`). Never write, imply, or
   schedule around the assumption that firmware is in hand.
5. **Limitations of the environment are reported as facts**: the development sandbox has no
   Windows, no hypervisor, and no .NET SDK, so nothing may be claimed about a Windows run
   until the `windows app` workflow or the owner's machine produces one.

## 3. Three categories, one label per deliverable

*Category 1* genuine PlayStation software execution · *Category 2* PC5's own independent
implementation of observable behaviour · *Category 3* hybrid. Every milestone, subsystem and
user-visible claim carries exactly one. Mixing them is the substitution this project forbids;
`docs/research/PS5_FIRMWARE_AND_PUP.md` and
`docs/architecture/EXECUTION_FEASIBILITY.md` are the reference for where the boundary sits
today. Do not silently redefine genuine execution as visual recreation, and do not declare
genuine execution impossible without research.

## 4. Legal and licensing boundaries

* No proprietary Sony firmware, keys, system assets or derived binaries — committed,
  released, embedded, or distributed. `.gitignore` keeps `firmware/` out.
* No bypassing signatures, encryption, secure boot, hardware fuses, authentication or access
  controls; no obtaining or using unauthorised keys; no instructions for either.
* Analysis is limited to what a lawful owner-supplied file permits: hashes, container
  identification, structural metadata. See ADR-007, ADR-022, ADR-026, ADR-030.
* PC5's licence is **GPL-3.0-only**, granted in `LICENSING.md` over the verbatim `LICENSE`;
  no vendored third-party source and no borrowed project identity (ADR-025, ADR-027). Any new
  component needs a documented licensing decision, and no per-file `SPDX` header may be added
  without verifying the holder. If the licence statement in a document contradicts
  `LICENSING.md`, fix the document, not the grant.
* A technical obstacle never authorises a legal or security violation. Record the obstacle.

## 5. Architecture governance

* `docs/architecture/roadmap.json` is the **only** milestone graph; `tools/roadmap.py` owns its
  validity and `tools/completion.py` owns the arithmetic. Never hand-edit a figure the
  generator produces, and never create a parallel roadmap or progress document.
* Decisions live in `docs/TECHNICAL_DECISIONS.md` (ADR-001…027) and
  `docs/architecture/ARCHITECTURE_DECISION_RECORD.md` (ADR-028+, the transformation). One
  decision per ADR, with *Problem / Alternatives / Evidence / Decision / Reversal*. Changing an
  architecture choice means a new or amended ADR, never a silent edit or a code-first drift.
* Do not create duplicate implementations of an existing subsystem; one contract, one owner
  (`ServiceRegistry` refuses a duplicate registration — the code enforces what the policy says).
* `PC5.Core` stays `net8.0` with zero package references and no Windows API; the Windows
  surface lives in `PC5.App/Platform`. The window holds no policy. A backend must declare its
  own `MaximumEvidenceLevel` before it can be selected.

## 6. Coding standards

Small, named, testable units; errors carry a category and a next step; cancellation honoured;
every lifecycle transition has a reason; no empty stubs presented as features; no speculative
abstraction without a consumer or an experiment; dependencies pinned from a run that actually
resolved them, never guessed; no formatting-only churn mixed into a behavioural change.

## 7. Testing and validation

Validation layers, never conflated: **unit** (settings, state machine, capability models,
firmware metadata) → **integration** (backend, guest memory, device registration, lifecycle) →
**Windows application** (build, launch, packaging, shutdown) → **guest execution experiments**
→ **genuine PS5 OS validation**. Tests assert behaviour, not existence: a test that only
instantiates a class is not evidence.

A gate that cannot run here must still be honest about itself: `tests/test_pc5_app_structure.py`
checks boundaries and claims *without pretending to compile C#*, and the compile/test claim
belongs to the `windows app` workflow.

## 8. Documentation requirements

Every significant change updates: the affected document, `docs/CHANGELOG.md` (delivered items
only), and `docs/PROGRESS.md` if a dimension moved. New documents must not duplicate existing
authority — extend it, or record in an ADR why a new file is needed. Cited paths must resolve;
`make docs-check`, `citations-check`, `presentation-check`, `completion-check`, `roadmap` and
`brand-check` are the mechanical version of that rule, and they run in CI.

Registry policy: the feature registry **is** `roadmap.json` plus `docs/audit/COMPONENT_INVENTORY.md`;
the blocker register **is** `docs/PROGRESS.md` §4; the experiment registry is
`docs/EXPERIMENTS.md`. Do not start a second copy of any of them.

## 9. Branches, commits, pull requests

* Work on a dedicated branch; never rewrite `main` history; never force-push over owner work;
  never delete a branch or artifact to tidy up.
* Commits are coherent, reviewable units, one concern each, with an accurate body: what
  changed, how it was verified, and what was not verified.
* Open focused PRs. Do not merge a PR — **the owner authorises every merge, explicitly, by
  number**. Do not merge unrelated PRs, and do not close other contributors' branches to
  simplify the list.
* Never change a workflow's `permissions:` or timeouts to make a red step green, and never
  weaken a test to pass a gate. Report the failure with its per-step CI facts.
* Preserve uncommitted work that is not yours. `git status` before you start; `.arena/` is
  agent-local and must never be committed.

## 10. Autonomous continuity

Work continuously: verify → design → implement → test → document → capture evidence → commit →
push → check CI → hand off. Ask the owner only for what needs their authority (merges,
releases, tags, destructive operations, licence changes, firmware acquisition) or a
decision you cannot make from evidence.

**Before ending a session**: update [`docs/AGENT_HANDOFF.md`](docs/AGENT_HANDOFF.md) with the
exact branch and SHA, what is done, what is running, what is blocked, the test status with
counts, and the next three actions with their commands. Push work; nothing durable may exist
only in the sandbox. If an experiment fails, keep it — `docs/EXPERIMENTS.md` records failures,
and never re-run the same failed experiment without investigating it.

## 11. Verification commands

```
make check                    # the whole legacy suite and the doc gates
make unit psm docs-check citations-check presentation-check brand-check completion-check roadmap
python3 -m unittest tests.test_pc5_app_structure tests.test_app_evidence   # the app gates here
gh run list --branch "$(git rev-parse --abbrev-ref HEAD)"                  # CI, from GitHub
```

`app/` itself needs a .NET 8 SDK, which this sandbox does not have: see
`docs/engineering/WINDOWS_VALIDATION.md` for what a real run must show.
