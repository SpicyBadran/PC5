# Transformation baseline — the measured state PC5 branches from

| | |
|---|---|
| Baseline commit | `9b20228edb177823d8f928e1df99c5c9805dbd90` on `main` ("docs: refresh the progress dashboard at a79df48", authored and committed by `github-actions[bot]`, 2026-11-28T03:45:44Z) |
| Measured on | 2026-11-28, in the Linux sandbox this project is developed in (`Linux x86_64`, gcc 12.2.0, GNU Make 4.3, CPython 3.11.2, node v22.22.3) |
| Method | every figure below comes from a command printed next to it. Nothing here is carried over from a previous document, and nothing here is a claim about capability |
| Purpose | the fixed starting point of the transformation from "bootable PC5 image + evidence pipeline" to "Windows 11 application that delivers the PlayStation 5 system environment and pursues genuine execution where it lawfully can" |

This document is deliberately **not** a status report. A status report is regenerated
(`docs/ABOUT.md` and the README block are, by `tools/presentation.py` and
`tools/completion.py`); a baseline is a *frozen* measurement that later work is measured
against. It is therefore never edited after acceptance — corrections go into the next
document, with a date.

---

## 1. The three rules this baseline enforces

1. **Three categories, never blended.**
   *Category 1 — genuine PlayStation software execution*: Sony's code running, on any host.
   *Category 2 — independent PC5 implementation*: PC5's own code reproducing observable
   behaviour.
   *Category 3 — hybrid*: PC5's code driving, wrapping or attaching to genuine execution.
   Every deliverable in the transformation is assigned one category, and a document that
   mixes them is a defect. Today's corpus is classified in §7.
2. **A screen is not done until its interactions and state transitions work**, and a
   milestone is not done until its acceptance criteria have evidence at the level
   `docs/PS5_COMPATIBILITY_STATUS.md` requires. The existing graph already obeys a
   version of this rule (M10 is `in_progress` while its evidence sits at level 1–2), so
   the transformation inherits it rather than inventing it.
3. **Absence is stated, not smoothed.** Where the project cannot verify something — the
   firmware payload (§5, D-1), the licence footer (§6), a Windows build in this
   environment (§8) — the baseline records the absence. A future document that quietly
   drops it is the failure mode this page exists to prevent.

## 2. The inventory, as measured

`git ls-tree -r --name-only origin/main | wc -l` → **442 tracked files**.

| Path | Files | Lines | What it is |
|---|---|---|---|
| `docs/` | 141 | 29,302 | 25 top-level documents + `architecture/` (15), `evidence/` (87), `playstation/` (4), `engineering/` (2), `hardware/` (1), `assets/` (7) |
| `evidence/` | 94 | 55,819 | recorded run logs, verification records, hardware records |
| `tests/` | 64 | 34,557 | host-side unit/conformance tests, QEMU/OVMF harnesses, stub firmware |
| `tools/` | 26 | 11,072 | the Python toolchain: ISO writer and verifier, roadmap, completion model, presentation generator, licensing, CI evidence recorder |
| `kernel/` | 25 | 6,942 | the 32-bit boot kernel (ACPI, PCI, ATA, ISO9660, FAT32, console, UI) |
| `kernel64/` | 25 | 5,107 | the 64-bit kernel (APIC, IDT, paging, TSS, timers, MADT) |
| `psm/` | 15 | 3,541 | the package-manifest format and registry |
| `boot/` | 13 | 4,267 | boot sector, stage-2, ESP/hybrid paths |
| `brand/` | 12 | — | PC5's own identity assets |
| `.github/` | 6 | — | `build.yml`, `progress.yml`, issue/PR templates |
| root | — | — | `README.md`, `LICENSING.md`, `LICENSE`, `CONTRIBUTING.md`, `Makefile`, `PS5UPDATE.PUP`, `.gitattributes`, `requirements-harness.txt`, `ci/README.md`, and **10 date-stamped review documents** (§5, D-3) |

Language mix, by extension: 108 `.py`, 84 `.md`, 72 `.txt`, 40 `.log`, 39 `.c`, 28 `.json`,
18 `.svg`, 16 `.h`, 8 `.sh`, 7 `.S`, 5 `.yml`, 3 `.sha256`.

**There is no Windows application in this repository.** Measured, not inferred — on
`origin/main`, `git grep -il` returns **0 files** for each of `.xaml`, `WinUI`, `dotnet`,
`Win32`, `P/Invoke`, `cppwinrt`. The word "Windows" appears in 17 files, all of them prose.
The product the owner has specified therefore starts at zero lines of application code, and
the milestone graph (§4) does not contain a Windows milestone yet.

## 3. What demonstrably works, and the proof of each item

| Capability | Evidence | Read it as |
|---|---|---|
| Host test suite: 1382 tests, 13 skipped, all OK | `docs/evidence/ci/46102447128.json` — machine-filed record of Actions run `46102447128` at `a79df48`; job `build and verify` 24/24 steps (22 success, 2 skipped, 0 failed) | the Python toolchain and the kernel unit/machine tests pass **on Linux hosts**. It says nothing about Windows |
| A 64-bit boot kernel runs on real UEFI firmware | same record, job `UEFI boot under genuine OVMF`, 10/10 steps (8 success, 2 skipped) | OVMF is a *firmware emulator* — this is not hardware validation and is never described as such |
| A buildable, verifiable ISO whose identity is computed, not asserted | `tools/mkiso.py` + an independent parser `tools/verify_iso.py`, gated by `make check`; ADR-001 records why the writer is in-tree | reproducible in principle; §5 D-6 explains why "reproducible" is not yet claimed |
| Progress figures that cannot be hand-edited | `make completion-check`, `make roadmap`, `make presentation-check`; `tools/completion.py` derives the percentage from `docs/architecture/roadmap.json` | the 26.7% figure is arithmetic over a declared scope, not a judgement of quality |
| A self-recording CI evidence loop | run `46104362009` (`workflow_run`) → all 14 steps success → commit `9b20228` authored by `github-actions[bot]`, adding the record and rewriting the Hosted CI row in `README.md` and `docs/ABOUT.md` | **proven on 2026-11-28.** This is the first automated documentation commit in the repository's history; the loop that produced it is the pattern the transformation's new CI must copy |
| Documented licence position | `LICENSE` (verbatim GPL-3.0 text), `LICENSING.md` (the grant), `tools/licensing.py` + `tests/test_licensing.py` (the gate), ADR-027 | grant verified mechanically; one prose contradiction found and fixed in this baseline (§6) |

## 4. What is experimental, partial, or blocked

`docs/architecture/roadmap.json` (`schema pc5.roadmap/2`, `document_version 0.4.0-dev`,
`updated 2026-11-22`) — 35 milestones across 14 workstreams:

| State | Count | IDs |
|---|---|---|
| `complete` | 9 | `M0`, `M1a`, `M1c`–`M1i` |
| `in_progress` | 4 | `M2a`, `M2b`, `M9`, `M10` |
| `blocked` | 6 | `M1b`, `M17`, `M18`, `M19`, `M20`, `M21` |
| `not_started` | 16 | the remainder of the destination and support workstreams |

The published scope figure at the baseline is **26.7%** by acceptance criteria
(**25.7%** by milestone count). Both are recomputed on every run of `make roadmap`;
neither is edited by hand.

Two facts about the blocked set matter to the transformation:

* `M17`–`M21` are the **genuine-execution chain** (security boundary → load → boot →
  runtime → install). They are blocked on a lawful, authenticated input and on trust-path
  analysis, exactly as `docs/playstation/BOOT_FEASIBILITY.md` assesses. The transformation
  does not unblock them by renaming them; see `docs/architecture/EXECUTION_FEASIBILITY.md`.
* `M1b`'s own `blocker` field already says *"the pipeline is now active (installed as
  `.github/workflows/build.yml` on 2026-11-24 …); M1b still owes its two acceptance
  criteria"*. The state is honestly `blocked`, because the two criteria — a tag that
  publishes `pc5.iso`, `pc5.iso.sha256` and `build-info.json`, and a published hash equal
  to a local rebuild at the same `SOURCE_DATE_EPOCH` — have never been met.

## 5. Debt register

Numbered so later documents can close items by ID instead of restating them.

| ID | Finding | Where | Why it matters to the transformation |
|---|---|---|---|
| **D-1** | `PS5UPDATE.PUP` is a **135-byte Git LFS pointer** whose declared object (`sha256:42ea0e5a…`, 1,423,697,408 bytes) **does not exist in the repository's LFS store**. A clone with `git-lfs` enabled dies: `remote missing object 42ea0e5…`, `fatal: PS5UPDATE.PUP: smudge filter lfs failed`, exit 128 | `PS5UPDATE.PUP`; `.gitattributes:49,51`; `ci/README.md` §"The build job's failure was never about the image" | the mission's target input is an **assumption, not an object**. Proven in §1 of `docs/research/PS5_FIRMWARE_AND_PUP.md`; it is the single largest fact in this baseline |
| **D-2** | `.gitattributes` is a **leftover Unity project ruleset** (`## Unity ##`, `merge=unityyamlmerge` on `.mat/.unity/.prefab`). No Unity asset exists in the tree | `.gitattributes:1-14` | attributes that describe an absent engine mislead every future reader about what the project is; the LFS section it happens to carry is what makes D-1 semi-legible |
| **D-3** | **10 date-stamped review/audit documents sit at the repository root** (`ADVERSARIAL_REVIEW_PR26_PR27_2026-11-16.md`, `AUDIT_PR26_PR27*_…`, `EVIDENCE_INDEX_…`, `FINAL_REMEDIATION_REPORT_…`, `FINAL_REVIEW_PR26_PR27_…`, `FOLLOWUP_EVIDENCE_F1_F5_…`, `LANDING_STRATEGY_D_READINESS_…`, `REMEDIATION_REPORT_F1_F5_…`, `REVIEW_PACKAGE_PR26_PR27_…`) | repo root | a newcomer's first screen is PR paperwork. These are evidence, not front matter: they belong under `docs/evidence/`. They are **kept**, never deleted |
| **D-4** | The licence gate verifies `LICENSING.md` and `LICENSE` but **not the prose elsewhere**. `README.md:556` said "Licensed under the GNU General Public License version 2 only" — contradicting the GPL-3.0-only grant and the v3 text it links to. No test could catch it | `README.md:556`, `tools/licensing.py` | **the sentence was corrected by the commit that adds this file**; the *gate gap* remains open. A licence misstatement is the one class of documentation defect that becomes a legal question |
| **D-5** | `Makefile:680` cites **`tools/build_release.py`, which does not exist** | `Makefile:680` | a comment that names a missing script is how a release procedure silently becomes unrunnable. Either the script lands or the comment stops promising it |
| **D-6** | `tools/completion.py:355` returns `"published": False` **hardcoded**, with the note "no tag exists on this history and the release job has never executed". Both halves of that note are stale: tag `pc5-0.1.0` → commit `09b885a` exists, and release `pc5-0.1.0` (published 2026-11-12T11:39:33Z) exists with **0 assets** | `tools/completion.py:340-356`, `git ls-remote --tags`, `gh api repos/SpicyBadran/PC5/releases` | the release-readiness dimension is currently *asserted*, not derived — and its explanation is wrong in a way a reader could repeat. Fix: derive publication from the API and report the tag's relationship to `main` (measured: **`09b885a` is not an ancestor of `main`**, i.e. the only published release is anchored off the trunk) |
| **D-7** | **No Windows job exists in CI.** `.github/workflows/` contains exactly two workflows: `build.yml` (push to any branch, PR, `v*`/`pc5-*` tags, manual dispatch — Linux/gcc, QEMU/OVMF, tag-gated publish) and `progress.yml` (the evidence loop) | `.github/workflows/` | the transformed product would have **zero** automated evidence on day one. A green legacy ISO must never be read as "the app works" — hence ADR-029 |
| **D-8** | The machine-level tests need `unicorn==2.1.4` (pinned in `requirements-harness.txt`, installed into a venv by CI at `build.yml:111`). Without it, `tests/test_ci_report.py` fails for want of the module — reproduced at several commits | `requirements-harness.txt`, `.github/workflows/build.yml:106-111` | a green `make check` is environment-dependent. The transformation's new gates must state their dependency in the same breath as their command |
| **D-9** | The roadmap was last updated **2026-11-22**, five merges before the baseline | `docs/architecture/roadmap.json` `updated` | the graph predates the CI loop it now sits behind. Any milestone the transformation adds must be added *to the graph*, not described beside it |
| **D-10** | 9 open pull requests exist on **other, older branches** (#31, #36–#43). Four (#36–#39) merge cleanly against `main`; five conflict with the dashboard/presentation code that #51–#54 replaced | `gh pr list --state open`, `git merge-tree` | stale branches are how a project acquires claims it did not make. Each is either merged, superseded in writing, or closed by its owner — not left to rot in the PR list |

## 6. Licensing and provenance

* **Grant:** GPL-3.0-only, stated in `LICENSING.md` over the verbatim `LICENSE` text,
  per ADR-027. `tools/licensing.py` refuses a notice that does not state the grant, an
  edited `LICENSE`, or a version mismatch between the two.
* **No vendored third-party source**, no borrowed project identity (ADR-025), enforced by
  `tests/test_project_independence.py`. Prior art is cited in documentation, never copied
  into the tree.
* **No Sony firmware, keys, or derived binaries** are committed, released, or embedded
  (ADR-007, ADR-022); the PS5 package is a *protected external input* whose identity is
  recorded and never asserted (ADR-026).
* **The correction this baseline makes:** `README.md`'s closing sentence is changed from
  "version 2 only" to the wording that matches the grant and the licence text. One line,
  no reinterpretation, and the reason it could happen at all is recorded as D-4.
* **No new licence claims are introduced here.** No per-file `SPDX-License-Identifier`
  headers were added, because no tool has verified that every file's holder consents;
  no copyright year range was invented.

## 7. The existing corpus, classified

Nothing in this table is deleted. "Retire" below means *stop extending and mark
superseded*, which is a claim a document can support; deleting 19,857 lines of
engineering is not.

| Corpus | Category | Disposition |
|---|---|---|
| `kernel/`, `kernel64/`, `boot/` | **2** — independent implementation of boot/platform behaviour | **keep and reuse.** Genuine input-free code, tested at 1382-test scale, and the only part of PC5 that runs on hardware-adjacent firmware (OVMF) |
| `psm/`, `tools/mkiso.py`, `tools/verify_iso.py`, `tools/buildmeta.py` | **2** | **keep.** The image-identity machinery is the foundation the release-readiness dimension needs (D-5, D-6) |
| `tools/progress_record.py`, `progress.yml` | infrastructure | **keep as the pattern.** The new Windows CI reuses this loop: machine-filed record → generated prose → verify → refuse if the author is the bot |
| `tools/completion.py`, `roadmap.py`, `presentation.py`, docs gates | infrastructure | **keep, extend** to four dimensions (ADR-029) instead of one |
| `docs/FIRMWARE_ANALYSIS.md`, `docs/PS5_SYSTEM_SOFTWARE_TARGET.md`, `docs/playstation/*` | research (all three categories) | **keep as the research record**, extended by `docs/research/` |
| `docs/ROADMAP.md`, `docs/architecture/PC5_ARCHITECTURE_SPEC_v2.md` | mix of 2 and 3 | **keep**, superseded claims marked rather than rewritten; the transformation's milestones are *added* to the graph (D-9) |
| root review documents (D-3) | evidence | **relocate** under `docs/evidence/` in the same PR that fixes the links, never silently |
| a "PS5-like theme/mockup" of the console UI | — | **not in the tree, and not built as a substitute for execution.** Where the app renders a console-style surface it is labelled PC5's own implementation, category 2, per `docs/playstation/BOOT_FEASIBILITY.md` §5 |

## 8. What this development environment can and cannot prove

| Question | Answer here |
|---|---|
| Can the existing suite run? | **Yes** — CPython 3.11.2 tests and `make` targets run; `unicorn` must be installed for the machine tests (D-8) |
| Can a Windows application be compiled here? | **No.** `dotnet`, `msbuild`, `csc`, `clang`, `cmake`, `cargo` are all absent (`command -v` → `ABSENT` for each), and `uname` is `Linux … x86_64` |
| Can Windows-only behaviour (WinRT, Direct3D, the Windows App SDK, packaging, MSIX, sign-in, controller APIs) be executed here? | **No**, in any form — there is no Windows kernel, no WinRT, no GPU, and no code-signing toolchain |
| What therefore counts as evidence for the app? | Code + tests written to run on Windows 11, reviewed here, **executed and reported by CI or by the owner on a real Windows 11 machine**. A feature whose test has not run on Windows is *not* "working"; the exact validation procedure ships with it (ADR-033) |
| What will never happen | a fabricated "test passed on Windows", a screenshot presented as execution, or a pointer's declared size reported as firmware we hold |

## 9. Baseline decisions taken from this page

Recorded as decisions, not as intentions, in
[`architecture/ARCHITECTURE_DECISION_RECORD.md`](architecture/ARCHITECTURE_DECISION_RECORD.md):

* **ADR-028** — the product is a Windows 11 application; it lives in a new `app/` tree; the
  legacy boot/kernel corpus is preserved in place, not rewritten or deleted.
* **ADR-029** — CI reports four independent dimensions (app, legacy kernel/image, research,
  release readiness), each with its own evidence and its own colour; no dimension may
  certify another.
* **ADR-030** — firmware is a protected input, verified as an object before it is a design
  assumption; the pointer in the tree is recorded as absent payload (D-1).
* **ADR-031** — the three-category rule of §1 is enforced in the roadmap schema, the
  presentation layer, and the PR template.
* **ADR-032** — `M0`–`M7` milestones are added to `docs/architecture/roadmap.json` under the
  existing evidence-level rules, so the transformation inherits the gates instead of
  escaping them.
* **ADR-033** — every Windows-only capability ships with the exact Windows 11 validation
  procedure that proves it, and its status in the graph is `not_started`/`in_progress`
  until that procedure has been executed and recorded.

## 10. Reproducing this baseline

```
git fetch origin
git rev-parse origin/main                     # 9b20228… at the moment of measurement
git ls-tree -r --name-only origin/main | wc -l   # 442
git show origin/main:docs/architecture/roadmap.json | python3 -c \
    'import json,sys; d=json.load(sys.stdin); print(d["updated"], len(d["milestones"]))'
git check-attr -a -- PS5UPDATE.PUP            # filter/diff/merge = lfs
git cat-file -p "$(git rev-parse HEAD:PS5UPDATE.PUP)"   # the pointer: 135 bytes
cat docs/evidence/ci/46102447128.json         # the machine-filed run record
make docs-check citations-check presentation-check completion-check roadmap
```

The last line is the point: **the transformation begins inside a green gate set**, so any
red that follows is attributable to new work and not to the starting line.

---

## What this document does not claim

It does not claim that PC5 can run PlayStation 5 software. It does not claim that the
firmware payload exists anywhere reachable. It does not claim a reproducible build (D-5,
D-6), hardware compatibility (the OVMF result is a firmware-emulation result), or the
existence of any Windows code. It fixes no licence, adds no milestone, and deletes
nothing. It is a measurement, taken on 2026-11-28, of what there is to build on.

## 12. Follow-up on the debt register — 2026-11-28, same branch, not a rewrite

This page stays a measurement of `9b20228`, exactly as taken; the corrections below are
appended rather than edited into §5, because a baseline that rewrites itself stops being a
baseline. Measured on `arena/5e36f38b-pc5` after the work described in `docs/CHANGELOG.md`:

| Debt | State after this branch | What closed it |
|---|---|---|
| **D-2** | closed | `.gitattributes`'s Unity block replaced by rules for `.cs`/`.xaml`/`.csproj`/`.props`/`.sln`/`.trx`, same line count, `*.pup` routing untouched |
| **D-4** | closed in both halves | the sentence at `2e5d39f`; the *gate* in `tools/docs_check.py` (prose licence statements, six documents, quotation exemption, three tests) |
| **D-5** | closed | `Makefile:680` and `tests/test_ovmf_gate_profiles.py` name the real pins and cite this register |
| **D-6** | closed | `tools/completion.py` derives tag and publication facts; `unknown` is representable; `ReleaseDerivationTests` pins it |
| **D-7** | **partially closed** | `.github/workflows/windows-app.yml` exists, parses, and is read-only; it has **not run**, and its `app` dimension files no committed record yet — that is `M28`'s remaining acceptance |
| **D-8** | closed for the reporter | a missing-dependency probe no longer reads as a crash; the machine-model tests still import `unicorn` |
| **D-9** | closed | `document_version` 4, `updated 2026-11-28`, 43 milestones in 15 workstreams, `make roadmap` in sync |
| **D-3** | verified, unchanged | re-counted: exactly `10` root `*_2026-11-16.md` files (`ls *_2026-11-16.md \| wc -l`), as §5 recorded — an earlier census note claiming 11 was the error |
| **D-1**, **D-10** | open | the payload and the nine stale PRs are the owner's to decide on; nothing here changed either |

