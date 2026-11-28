# Architecture decision record — the transformation register

**What this file is.** [`../TECHNICAL_DECISIONS.md`](../TECHNICAL_DECISIONS.md) carries
ADR-001 … ADR-027: the decisions that produced the boot image, the ISO identity chain, the
evidence pipeline and the licence model. It stays exactly where it is, and stays the
authority for those decisions. **This file is its continuation** — the record of decisions
taken for the transformation into a Windows 11 application. Numbering does not restart, no
decision is restated in two places, and the format is the house format: problem,
alternatives, evidence, decision, reversal.

Every decision below was taken at baseline `9b20228` and cites either
[`../TRANSFORMATION_BASELINE.md`](../TRANSFORMATION_BASELINE.md) (debt IDs `D-1`…`D-10`) or
[`EXECUTION_FEASIBILITY.md`](EXECUTION_FEASIBILITY.md) (path verdicts).

---

## ADR-028 — The product surface is a Windows 11 application, added beside the legacy tree

* **Problem**: the project's destination was stated as a bootable image with PC5's own
  kernel. The owner's product statement is now explicit: *PC5 is a Windows application
  project dedicated to bringing the real PlayStation 5 system environment to Windows*. A
  bootable image cannot deliver that environment to a Windows user, and pretending the ISO
  is the app has already produced documents that read as though it were.
* **Alternatives**: (a) treat the transformation as a re-skin of the existing UI
  (`kernel/ui.c`) — rejected: it is the "visual imitation" reduction the directive forbids;
  (b) rewrite the legacy tree in place — rejected: the 19,857 lines of `kernel/`, `kernel64/`,
  `boot/` and `psm/` that are tested and cited by 141 documents would lose their evidence
  links, and `docs_check` would fail on every moved path; (c) a second repository —
  rejected: the legacy corpus is the *platform model* the app's service layer reuses, and
  two repositories would duplicate implementations.
* **Evidence**: `docs/` 141 files / 29,302 lines cross-reference the legacy paths;
  `tests/` (64 files, 34,557 lines) exercise them; the baseline confirms **0 files** mention
  `.xaml`, `WinUI`, `dotnet`, `Win32`, `P/Invoke` or `cppwinrt`, so there is nothing to
  supersede, only things to add beside.
* **Decision**: new top-level `app/` tree for the Windows application; `kernel/`,
  `kernel64/`, `boot/`, `psm/`, `tools/` and `tests/` remain in place and remain
  **maintained, not frozen** — they are the substrate the app's services talk to through an
  explicit boundary (ADR-029's *legacy* dimension), never by `#include`-style coupling.
  `README.md`'s first screen is rewritten to describe the application; the image becomes one
  capability of it. Root review documents move under `docs/evidence/` (D-3) in a dedicated
  commit whose only job is link repair.
* **Reversal**: if the app's boundary to the legacy substrate turns out to be a fork rather
  than a seam — i.e. the two evolve the same behaviour twice — then the legacy tree is
  carved into `legacy/` and the seam is re-drawn. That is a reorganisation, never a
  deletion, and it requires its own ADR.

## ADR-029 — Four CI dimensions, each with its own evidence and its own colour

* **Problem**: one green pipeline currently stands for everything. `build.yml` proves that
  the Linux host tests pass and that the image boots under OVMF; nothing in it says anything
  about a Windows application, and D-7 records that no Windows job exists at all. The owner's
  requirement is blunt: a green legacy ISO must never imply the app works.
* **Alternatives**: (a) add one `windows-latest` job to `build.yml` — rejected: the colour of
  the aggregate would still conflate "the ISO builds" with "the app builds", which is the
  defect; (b) separate repositories/pipelines — rejected by ADR-028(c); (c) four named
  dimensions, each independently recorded — chosen.
* **Evidence**: the repository already has the mechanism, proved on 2026-11-28: run
  `46104362009` (`workflow_run`) ran `progress.yml` end to end — 14/14 steps, both refusal
  guards green, `Commit and push the refresh` succeeding for the first time after #54 put a
  real commit identity on `main` (`9b20228`, authored `github-actions[bot]`) — filing
  `docs/evidence/ci/46102447128.json` and rewriting the Hosted CI rows in `README.md` and
  `docs/ABOUT.md` from the run's own API fields. That shape — *machine files a record →
  prose is generated → verify → refuse to write if the author is the bot* — is what each new
  dimension must copy. D-6 also warns what to avoid: `tools/completion.py` hardcodes
  `"published": False` with a note that is now factually stale, which is precisely an
  assertion where a derivation belongs.
* **Decision**: CI reports four dimensions, each a separate required check with a separate
  evidence file: **app** (Windows 11 build + unit/UI tests of `app/`), **legacy kernel and
  image** (today's `build.yml` content, unchanged), **research** (firmware/PUP verification
  records, feasibility documents, no capability claims), **release readiness** (derived from
  tags, releases and asset hashes — never hardcoded). A dimension is `unknown` when it has
  no run, and `unknown` renders as `unknown`, not as a inherited green. The
  `workflow_run` refresh refuses to push if it cannot identify the run it is reporting.
* **Reversal**: if four dimensions cannot be kept independently truthful (a generator that
  guesses instead of reading), the count drops to the number that can — the constraint is
  independence, not the number four.

## ADR-030 — The firmware input must be verified as an object before it is a design assumption

* **Problem**: the mission names the PS5 system software as its target input, and the tree
  contains a file named for it. D-1 measured what that file is: a 135-byte Git LFS pointer
  naming `sha256:42ea0e5a…` of 1,423,697,408 bytes, with **no object in the store** — enough
  to break a `git-lfs` clone (`smudge filter lfs failed`, exit 128, recorded in `ci/README.md`).
* **Alternatives**: (a) keep citing "the PUP" as if it were in hand — rejected: it has
  already misled scheduling language; (b) delete the pointer — rejected: it is the record of
  the declared input, and deleting evidence is not correcting it; (c) keep the pointer and
  state beside it, in the same breath, that the payload is absent — chosen.
* **Evidence**: [`../research/PS5_FIRMWARE_AND_PUP.md`](../research/PS5_FIRMWARE_AND_PUP.md)
  §1, with the measuring command next to each row; ADR-022 and ADR-026 already make the
  package a protected external input whose identity is recorded and never asserted.
* **Decision**: no design document, milestone or UI string may treat the package as readable.
  Where the app needs firmware-shaped behaviour it implements it (category 2) and says so.
  The research dimension files a verification record only when §5's procedure has actually
  been run; if the payload stays absent, that record's conclusion is "absent", which is a
  legitimate, publishable result and not a failure to hide.
* **Reversal**: a lawfully held package whose SHA-256 matches the declared `oid` lets ADR-030
  be amended with the report digest — and only then. A pointer's declared size is never a
  firmware size, and an unrelated firmware file is never a substitute.

## ADR-031 — Three categories are carried in the data, not in prose

* **Problem**: the failure mode of a project with this destination is quietly redefining
  genuine execution as visual recreation. A rule that lives only in prose cannot be checked,
  and this repository's culture is that unchecked rules rot (D-4 is exactly that story: a
  licence footer contradicted the grant and no gate could see it).
* **Alternatives**: (a) a paragraph in `AGENTS.md` — necessary, insufficient; (b) directory
  segregation by category — rejected: one feature legitimately spans categories (a shell
  that *attaches* to a genuine console is 2 + 3); (c) a per-deliverable field in the existing
  roadmap schema, verified by `make roadmap` — chosen.
* **Evidence**: `docs/architecture/roadmap.json` already carries `kind`, `level`,
  `acceptance[]` and `evidence[]` per milestone and is validated by `tools/roadmap.py`;
  `docs_check` proves documented commands exist. Extending a validator is cheaper than
  inventing a parallel register, and the vocabulary rule ("a parsed container is not an
  executed game") already lives in `../PS5_COMPATIBILITY_ARCHITECTURE.md` §4.
* **Decision**: every transformation milestone carries a **category** field — `genuine`,
  `independent`, `hybrid` — and the presentation layer groups by it, so a `genuine` claim is
  visibly orphaned if it has no research evidence behind it. `M18`–`M21` keep their `blocked`
  state in the same table as the app milestones, side by side, so the document cannot imply
  the destination advanced because the app did.
* **Reversal**: if categorisation becomes decoration (everything marked `hybrid`), the field
  is dropped and the distinction moves back to being enforced by the evidence level — but
  dropping it must itself be recorded, not just quietly done.

## ADR-032 — `M0`–`M7` go into the existing graph, under the existing evidence rules

* **Problem**: the directive defines milestones M0 (baseline) through M7 (release
  readiness). A parallel milestone list outside `roadmap.json` would produce two progress
  figures for one project — and D-9 records that the graph (`updated 2026-11-22`) already
  lags `main` by five merges.
* **Alternatives**: (a) `docs/ROADMAP.md` prose only — rejected: it is generated prose, and
  the generator would contradict it; (b) new `app/roadmap.json` — rejected: two graphs;
  (c) extend the single graph — chosen.
* **Evidence**: 35 milestones / 14 workstreams, states `complete` 9 · `in_progress` 4 ·
  `blocked` 6 · `not_started` 16, published as 26.7% by criteria and 25.7% by count;
  `tools/completion.py` computes both from the graph, and `make completion-check` refuses a
  README that disagrees. M10's case proves the rules bite: acceptance met, still
  `in_progress` because its evidence sits at level 1–2.
* **Decision**: `M0` baseline (this commit set) · `M1` first runnable Windows application —
  shell, navigation, service boundaries, persistent state, logging, real settings, tests ·
  `M2` PS5-environment subsystems with their own contracts · `M3` genuine-execution
  interface research (Path A/B inputs, verification only) · `M4` controller/input and media
  behaviour · `M5` update/installer semantics against PC5's own packages · `M6` four-dimension
  CI and the app evidence loop · `M7` Windows release readiness, including signing and
  packaging. Each enters `docs/architecture/roadmap.json` with `acceptance[]` and
  `evidence[]` written **before** it is claimed, and M1 is not complete until its interactions
  and state transitions work — a screen with no behaviour is `in_progress`, never `complete`.
* **Reversal**: if the transformation graph and the destination graph measure different
  things badly enough to confuse the published percentage, the percentage is split per
  dimension (ADR-029) rather than averaged. Averaging is not allowed.

## ADR-033 — A Windows-only capability ships with an executed validation record, or it is not validated

* **Problem**: this development environment cannot run Windows: no `dotnet`, `msbuild`,
  `csc`, `clang`, `cmake`, `cargo` (`command -v` → `ABSENT`), `uname` = `Linux … x86_64`. The
  temptation this creates — "looks correct, presumably passes" — is the one failure mode that
  would poison an evidence-based project.
* **Alternatives**: (a) implement without tests until the owner runs Windows — rejected:
  untested code is not a deliverable; (b) mock the WinRT surface in the sandbox — rejected for
  the *claim*, accepted for the *core*, because the boundary must be designed anyway;
  (c) split testable logic from platform surface and require a real run for the latter —
  chosen.
* **Evidence**: the app's logic that does not need Windows (state machines, settings
  persistence, service orchestration, log plumbing, package identity parsing) is testable in
  plain .NET on Linux; the WinUI/WinRT surface is not. `actions/runner-images` provides
  `windows-latest`, so the run can be automated rather than deferred to goodwill.
* **Decision**: every feature ships three artefacts — implementation, tests, and a
  **Windows 11 validation procedure** naming the exact commands (`dotnet build`, `dotnet
  test`, the MSIX install, the click path) and the expected observable output. CI executes it
  on a Windows job; if the owner executes it locally instead, the result is filed in the
  research/app dimension with date and host. Until a record exists, the graph's state is
  `in_progress` at best, and no document says "works on Windows".
* **Reversal**: none that removes the requirement. A claim may be promoted only by a record.

## ADR-034 — Path verdicts: A blocked on input+hardware, B research-grade, C the spine

* **Problem**: "genuine PlayStation software execution wherever technically and legally
  possible" must be turned into a schedulable position, and it must not be softened in
  either direction — no "not possible, so build a theme", no "possible, so plan the boot".
* **Alternatives**: (a) commit to genuine execution as the deliverable — rejected: blocked by
  A1–A5, and would make the project's honesty hostage to hardware-anchored key material;
  (b) commit to the app only and drop the research — rejected: it discards the mission and
  the 141-document research record; (c) the split verdict — chosen.
* **Evidence**: [`EXECUTION_FEASIBILITY.md`](EXECUTION_FEASIBILITY.md) §1–§4 and its §6
  standing conclusions; the 2026 field data there (RPCS3's "a PS5 emulator does not yet
  exist"; SharpEmu's 0.6 FPS demo; 46 reports and 3 playable titles out of 9,011; the third-party
  relinker's one verified game; RPCSX booting VSH) shows the ceiling for the whole field, and the legal
  row (§4: *Connectix* protects reverse engineering, *Tropic Haze* punished **decryption**)
  shows the boundary that does not move.
* **Decision**: Path A stays a research target with its blockers in the graph, named, and
  `M18`–`M21` remain `blocked` until A1 and A2 change. Path B keeps stages 5–7 at
  `RESEARCH`, implementing intake and verification only — never decryption. Path C is the
  product: a Windows 11 application that implements the environment, and gains genuine
  execution as a *feature* the day A or B earns it. The app never claims to be the console,
  and the console is never quietly replaced by a picture of it.
* **Reversal**: tabled in `EXECUTION_FEASIBILITY.md` §5 — chiefly a lawfully held, matched
  package, and/or a third-party reproduction showing the shell booting on a presented device
  set. Either promotes the corresponding milestone; neither promotes a screenshot.

## ADR-035 — Application stack: .NET core library + WinUI 3 shell, packaged, tested in two halves

* **Problem**: `M1` needs a named framework and build commands, and the choice determines
  what can be tested in this sandbox at all (ADR-033).
* **Alternatives**: (a) **C++/WinRT + WinUI 3** — closest to the metal and to the GPU path,
  but a slower loop for a shell whose first job is state and navigation; (b) **C#/.NET +
  WinUI 3 (Windows App SDK), MSIX-packaged** — chosen; (c) **Electron/Tauri or any web
  wrapper** — rejected: it cannot honestly be called a Windows 11 application, which is the
  product statement, and it would replace PS5 hardware abstraction with a browser's;
  (d) **UWP** — rejected: the platform Microsoft has moved off; (e) **Qt/Win32** —
  rejected: cross-platform looks would blur which OS the project targets.
* **Evidence**: the directive fixes Windows 11 as the surface and demands "shell, navigation,
  service boundaries, persistent state, logging, real settings, tests" — which is a
  XAML-apps-with-a-clean-core description; the sandbox constraint (no `dotnet` here) is what
  makes the two-halves split necessary rather than stylistic.
* **Decision**: `app/src/PC5.Core` — `net8.0` class library, no Windows-only reference,
  holding state machines, settings persistence, service interfaces, logging, package
  identity parsing; built and unit-tested with `dotnet test` on Linux runners.
  `app/src/PC5.App` — WinUI 3 on the Windows App SDK, x64, MSIX packaging, no business logic
  in code-behind, built and tested on `windows-latest`. **Pin toolchain versions in the
  project files and record them here once CI restores them** — this ADR deliberately does
  not assert a Windows App SDK version it cannot verify, and nothing claims a successful
  Windows build until the Windows job has run.
* **Reversal**: if WinUI 3's packaging or isolation model blocks a required capability
  (exclusive fullscreen/kiosk behaviour, direct GPU or device access), the shell moves to
  Win32 + Direct3D behind the same `PC5.Core` — the core's testability is the reason that
  swap is cheap, which is also the reason it is not done pre-emptively.

## ADR-036 — The transformation's milestones are numbered into the existing graph as `M22`–`M29`

* **Problem**: ADR-032 decided that the directive's `M0`–`M7` enter `roadmap.json` rather than
  a parallel file, but the graph already owns `M0`–`M21` (43 milestones now, 35 at the
  baseline), and `tools/roadmap.py` rejects a gap or a collision. The directive's labels also
  collide semantically: graph `M1` is *physical boot on qualified PC hardware*, not "the first
  runnable window", so reusing the names would make one id mean two things in one file.
* **Alternatives**: (a) renumber the whole graph — rejected: ids are cited in
  `docs/evidence/`, in `README.md`'s generated blocks, in commit messages and in merged PRs,
  and re-labelling history to fit a new plan is the reverse of an audit trail; (b) a second
  `app/roadmap.json` — rejected by ADR-032, and `tools/completion.py` would publish two
  percentages for one project; (c) continue the sequence — chosen.
* **Evidence**: `python3 tools/roadmap.py check` requires ids matching `^M[0-9]+[a-z]?$`, a
  workstream per milestone, no cycles, and `evidence[]` paths that exist on disk; `make
  completion-check` recomputes both published figures from the same file. Adding eight
  milestones with unmet acceptance moved the published number **down** (26.7 % → 23.4 % by
  criteria), which is exactly the property a scope ledger must have: declaring work you have
  not done costs you, it does not flatter you.
* **Decision**: `M22` baseline · `M23` first runnable `PC5.exe` (directive `M1`) · `M24`
  subsystems behind contracts (directive `M2`) · `M25` genuine-execution interface research
  (directive `M3`) · `M26` input and media (directive `M4`) · `M27` update/installer semantics
  against PC5's own packages (directive `M5`) · `M28` four CI dimensions and the app evidence
  loop (directive `M6`) · `M29` Windows release readiness (directive `M7`). A new workstream
  `WS14` owns the application, because `WS0` (governance) and `WS13` (PS5 product experience)
  would each mis-describe it. Every document that wants to say "M1" means the graph id and
  quotes `M23`; the directive's numbering survives only in this table.
* **Reversal**: none of the ids. If the mapping proves wrong, the *titles and dependencies* are
  amended in a new ADR; an id is never re-pointed at different work, because evidence already
  filed against it would silently change meaning.

## ADR-037 — Host execution backend: probe Windows Hypervisor Platform, vendor no emulator, write no hypervisor

* **Problem**: `M24`'s engine seam needs a first real backend, and the choice decides what PC5
  can even measure on a Windows host. It is also where a project under time pressure quietly
  takes a shortcut — bundling an emulator, or declaring "virtualisation works" because a
  documented API exists.
* **Alternatives**: (a) **vendor an upstream emulator** (QEMU-style) — rejected now: it is a
  licensing and identity decision under ADR-025, and the guest machine model would be
  somebody else's; (b) **Hyper-V through its management stack** — rejected: too little control
  over the device model PC5 has to own; (c) **PC5's own type-1 layer** — rejected as a product:
  it requires owning the host's boot, which is not a Windows application; (d) **WHP as the
  probe and lifecycle target, with nothing assumed about what it can do** — chosen.
* **Evidence**: [`../research/VIRTUALIZATION_BACKENDS.md`](../research/VIRTUALIZATION_BACKENDS.md)
  compares the candidates against what a PS5 guest would demand; Microsoft's own
  [`WHvAdviseGpaRange`](https://learn.microsoft.com/en-us/virtualization/api/hypervisor-platform/funcs/whvadvisegparange)
  requirement table fixes the API's floor (x64 and Arm64, Windows 10 20H2 / Windows 11 24H2 on
  Arm64); secondary reporting (tier D, used only for statements the primary page supports) says
  enabling `HypervisorPlatform` is an administrator operation needing VT-x/AMD-V with SLAT, and
  the sources **contradict each other on edition availability** — which PC5 records instead of
  resolving by preference. In-tree, `WindowsHostProbe` reports only that `WinHvPlatform.dll`
  loaded, with `NotProven` on the capability line, and `docs/TRANSFORMATION_BASELINE.md`
  records `virtualization-backend` as `unknown`.
* **Decision**: experiment `VBP-1` decides, not this document. `PC5.App/Platform` may probe and
  report; a backend that creates a partition must declare `MaximumEvidenceLevel = GenericGuest`
  like `Pc5TestBackend` does, so even a complete success is E2 and is printed as
  "not PS5 execution". No emulator is vendored without its own ADR; no custom hypervisor is
  written; PC5 does not enable Windows features for the user and does not treat "the DLL
  loaded" as "virtualisation is available".
* **Reversal**: VBP-1 producing a working partition with an E2 guest promotes the backend to
  the graph with its record; a documented API that turns out unusable on the target moves the
  candidate list, not the ceiling. Neither outcome touches the E5 claim, which no backend can
  produce on its own.

