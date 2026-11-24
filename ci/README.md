# CI pipeline (active)

`build.yml` is the PC5 build-and-release pipeline, installed at
`.github/workflows/build.yml` on 2026-11-24 in `2c3cd5c` — a pure rename of
`ci/build.yml` (`R100`, 0 insertions, 0 deletions, one commit, so the acceptance or
refusal belonged to that one act) — after the repository owner granted the GitHub App
*Workflows: Read and write*. GitHub then read the workflow as `active`
(`total_count: 1`, id 428605735) and ran it on the push that installed it.

Before that grant the file lived here, not because it was unfinished but because GitHub
rejected every push that created anything under `.github/workflows/`
(`refusing to allow a GitHub App to create or update workflow ... without workflows
permission`). Five of those refusals are quoted in *Attempts, dated* below; they stay
quoted, because the reason the pipeline slept for eleven days is part of what it means to
trust the pipeline now.

**Installation is not execution and execution is not the milestone.** What remains is
`M1b`'s own acceptance: publish the assets for a tag, and show the published hash equals
a local rebuild at the same `SOURCE_DATE_EPOCH`.

```sh
git tag -a v0.2.0 -m "PC5 0.2.0"
git push origin v0.2.0          # the `release` job is gated on refs/tags/, so this is the trigger
gh run list --workflow build --limit 5
gh release view v0.2.0 --json assets
```

## What the pipeline does

| Trigger | Job | Result |
|---|---|---|
| push to any branch, pull request, manual dispatch | `build` | ISO built twice (identical hashes required), verified, architecture/roadmap gate, unit/host/boot tests run, assets kept as a workflow artifact |
| push tag `v*` or `pc5-*` | `build`, then `release` | the same, then `pc5.iso`, `pc5.iso.sha256` and `build-info.json` published on the release |

The release job is the only one with `contents: write`, runs only for tags, only
after `build` succeeded, and delegates the publishing to
`tools/publish_release.sh` (checksum gate, verdict gate, create-or-update,
`--clobber`, final asset assertion). That script is unit-tested against a stub
`gh` in `tests/test_release.py`.

## Jobs

| Job | What it proves | Gate that can fail it |
|---|---|---|
| `build` | the image builds twice identically, verifies, passes unit/host/boot tests and the roadmap validator | any gate returning non-zero |
| `ovmf` | the same image boots under genuine TianoCore OVMF, cold and warm, with the Secure Boot policy and the negative matrix | `tests/ovmf_boot.py` exit 3 (NOT TESTED) **fails** the job, so a runner without firmware is a red build, not a green one |
| `release` | assets published for a tag, after `build` succeeded | `tools/publish_release.sh` verdict and checksum gates |

`ovmf` provisions its own firmware with `tools/ovmf_setup.sh` and uploads
`build/ovmf-report.json`, `build/ovmf-matrix.json` and every serial log, so a
reviewer can read what firmware said rather than what the harness concluded.

## Configuration

The pipeline asserts no capability level; it asserts that the repository is
self-consistent. `make roadmap` re-validates the milestone graph and compares it
with the generated `docs/architecture/PC5_MILESTONE_GRAPH.md`, and the host test
suite includes `tests/test_product_identity.py`, which fails if the product
identity, the destination or the forbidden-claim list drifts from the documents.

## Evidence that exists today, and what does not

* Proven locally: the exact workflow steps run on this machine and produce two
  byte-identical images; the publish script executes correctly against a stub.
* Proven on the platform since 2026-11-24: that GitHub Actions reads the workflow, starts it
  from a push, and that the actions it pins resolve on a hosted runner. The run record —
  job by job, with the artifacts it uploaded — is in the *First runs* section at the end.
* Still not proven, and therefore not claimed anywhere in this repository: that a tag
  published `pc5.iso`, `pc5.iso.sha256` and `build-info.json` whose hash equals a local
  rebuild. No tag has been pushed; the `release` job has never executed. That gap is
  `M1b`'s `blocker` field, not a footnote.

## Activation attempts (milestone B3)

### 2026-11-13, against `main` `237725d`

Performed the exact operation described above — `git mv ci/build.yml
.github/workflows/build.yml`, commit, `git push` — against `main` `237725d`.
The push was rejected and nothing reached the remote:

```
 ! [remote rejected] arena/0e2b454e-pc5 -> arena/0e2b454e-pc5 (refusing to allow a GitHub App to create or update workflow `.github/workflows/build.yml` without `workflows` permission)
error: failed to push some refs to 'https://github.com/SpicyBadran/pc5.git'
```

The token is a GitHub App installation token (it reports no OAuth scopes;
`X-Accepted-Github-Permissions: allows_permissionless_access=true`). It holds
`admin`/`push` on this repository, but the GitHub App installation is not
granted the **Workflows** permission, and GitHub refuses to let an App create or
modify anything under `.github/workflows/` without it. Reading the Actions API
confirms the consequence:

```
$ gh api repos/SpicyBadran/pc5/actions/workflows --jq '.total_count'
0
$ gh run list --limit 3
(no output — there are no runs)
```

Zero registered workflows and zero runs: the file has never been read by
GitHub, so no pipeline has ever executed and no release asset has ever been
published (the `pc5-0.1.0` release exists with an empty asset list).

### 2026-11-13, retry against `main` `a266936`

The permission was re-tested before attempting anything, and had not changed.
One decisive push was made — a pure rename (`0 insertions, 0 deletions`) — and
was rejected identically:

```
 ! [remote rejected] arena/79f9165d-pc5 -> arena/79f9165d-pc5 (refusing to allow a GitHub App to create or update workflow `.github/workflows/build.yml` without `workflows` permission)
error: failed to push some refs to 'https://github.com/SpicyBadran/pc5.git'
```

Nothing reached the remote; the local commit was reverted to `a266936` and
`.github/` removed. The permission probe results were unchanged:

| Probe | Result |
|---|---|
| `gh api .../actions/workflows` | `200`, `X-Accepted-Github-Permissions: actions=read` |
| `X-OAuth-Scopes` | empty — GitHub App installation token, not an OAuth token |
| `gh api repos/SpicyBadran/pc5` | `admin`/`push`/`maintain`/`triage`/`pull` all `true` |
| `gh api /installation/repositories` | `403 Resource not accessible by integration` |
| `gh api .../actions/permissions` | `403 Resource not accessible by integration` |
| registered workflows / runs | `0` / none |

The installation still lacks **Workflows: Read and write**. No bypass was
attempted: no Git Data API, no scratch repository, no alternate credentials.

### What the repository owner must do

Grant the Workflows permission to the GitHub App installation for this
repository, then re-run the activation:

1. GitHub → Settings → Applications → (the App that authored these branches) →
   **Configure** → Repository access → ensure `SpicyBadran/pc5` is selected →
   **Permissions → Repository permissions → Workflows: Read and write** → Save.
2. Next session, or by hand.  The move is one command; the repository then has to be told that it
   moved, which is a second commit, and both are listed here so the activation is a single
   continuous act rather than a half-done one:

   ```sh
   # a. the pure rename, so the acceptance or refusal is attributable to one commit
   mkdir -p .github/workflows          # `git mv` does not create the parent
   git mv ci/build.yml .github/workflows/build.yml
   git commit -m "ci: activate the build and release pipeline"
   git push

   # b. the references that say where the pipeline lives
   #    ci/README.md (its title and framing), docs/BUILDING.md, docs/TESTING_STRATEGY.md,
   #    docs/PRESENTATION.md, docs/RECOVERY.md, docs/architecture/PC5_ARCHITECTURE_SPEC_v1.md,
   #    docs/architecture/RISK_REGISTER.md (row B6)
   #    ... plus roadmap.json M1b: state blocked -> in_progress, deliverables/evidence retargeted,
   #    blocker removed, then `make presentation` to re-render the generated blocks and
   #    `python3 tools/roadmap.py sync` if the milestone graph drifts.
   make docs-check presentation-check roadmap citations-check unit
   python3 -m unittest tests.test_release          # pins the pipeline file's contents, both paths
   ```

   Step (b) was withheld until (a) was accepted, on the rule that a path in a document is a claim
   about the repository: writing `.github/workflows/build.yml` into eight living files while the push
   could still be refused would have made those documents false on purpose.  Both halves landed on
   2026-11-24, in that order, in two commits.  Dated records (the audit reports at the root,
   `docs/evidence/**`, the historical `TEST_RESULTS` sections) were *not* retargeted: they reported
   the location that was true when written, and a record rewritten to match a later state stops
   being evidence.

   Then read the first run before believing anything about it:

   ```sh
   gh run list --limit 3
   gh run watch <id> --exit-status && echo "green" || echo "red: diagnose, do not re-run blindly"
   ```

   A green first run says the pipeline executes; it does **not** close M1b.  The milestone's
   acceptance is a tag that publishes `pc5.iso`, `pc5.iso.sha256` and `build-info.json`, and a
   published hash equal to a local rebuild at the same `SOURCE_DATE_EPOCH` — which needs the
   `git tag` step below.

No shell command available in this environment can grant that permission; it is
an installation-level authorization held by the repository owner.

The fourth and fifth attempts (2026-11-24) re-confirmed that from three directions, which is the
only new information the attempts produced:

| Probe | Result |
|---|---|
| `git push` of a commit whose diff is a pure rename (`0 insertions, 0 deletions`) | `remote rejected`, twice, quoting the same sentence |
| `gh api repos/SpicyBadran/PC5/actions/workflows` | `total_count: 0` — GitHub has read no workflow in this repository, ever |
| `gh api repos/SpicyBadran/PC5/actions/permissions` | `403 Resource not accessible by integration` — the setting is not readable with this token, so the grant cannot even be *observed* from here, only tested by pushing |

Nothing was attempted that would side-step the check: no Git Data API, no contents API, no scratch
repository, no alternate credentials, no commit that hides the file under a different path.  The
local commit was reverted (`git reset --mixed HEAD~1`, `ci/build.yml` restored, `.github/workflows/`
removed) and the tree verified clean against `84e6325`.

What is different after these attempts is not the location of the pipeline — it is that the pipeline
is now *worth running*: the five corrections in the section below this one were made first, precisely
so that the first run GitHub produces is not a run in which every `make <gate> | tee` step could
report success after its gate failed.

## Reading a failed run

The raw Actions log and every uploaded artifact are served from hosts outside this
repository (`results-receiver.actions.githubusercontent.com`, then a blob store), and a
sandboxed environment may be able to reach none of them: `gh run view --log`, `--log-failed`
and `gh run download` all fail at the TLS layer, and `actions/runs/<id>/logs` only ever hands
back those same redirects.  What *is* reachable with a plain repository token is:

```sh
gh api "repos/SpicyBadran/PC5/actions/runs/<run>" --jq '.jobs'      # which step, by name
gh api "repos/SpicyBadran/PC5/check-runs/<job_id>/annotations" \
   --jq '.[] | "\(.annotation_level) \(.message)"'                  # and what it said
```

so each failing job ends with `python3 tools/ci_report.py`, a step guarded by `if:
failure()` that re-reads the gate logs and repeats their failure blocks as annotations and in
the job summary.  The script always exits 0 — its first version did not, because a `grep` that
matched nothing became a second red step while the real verdict stayed unread — and it quotes
a *failed* verdict only, never a green gate's `OK`, so the report cannot point a reviewer at
the wrong step.  `tests/test_ci_report.py` pins those properties, and one of them is a
one-line assertion: a log line that looks like `::error::` must not become a command.

A second case needed a second instrument.  When the interpreter *dies* — the run at
`169fe94` ended `Process completed with exit code 139` — there is no `FAIL:` block, no
traceback and no tally, because no test failed: `discover` never got to report anything, and
its half-written log ends mid-line.  A reporter that quotes logs cannot describe a run that
wrote none.  So each failing job also runs `tools/ci_triage.py`, which runs the same modules
`discover` runs, one interpreter per file, and names the first that does not return 0
(`SIGSEGV`, `SIGKILL (out of memory…)`, `exit N`), with `--only ovmf` in the firmware job so
it walks the suite that actually ran.  It is a diagnostic and never a gate: `if: failure()`,
read-only apart from `build/logs/triage.json`, and it swallows its own crashes — a helper
written to explain a failure became one once already, in bash, under `pipefail`, via a `grep`
that matched nothing.

### What the first four runs measured

| Run | Head | Result | What it says |
|---|---|---|---|
| push | `2c3cd5c` | `cancelled` | superseded by the next push, by `concurrency.cancel-in-progress` — the design working, not a fault |
| push + PR | `4d0c358` | `failure` ×2 | `Host tool tests` (build) and `Boot it under OVMF` (firmware) failed; the annotations could only say which step |
| push | `169fe94` | `failure` ×2 | build: `exit 139` in `Host tool tests`; firmware: `make: *** [Makefile:637: ovmf-test] Error 1` after a `Traceback` |
| push | `cc12956` | `failure` ×2 | the reporter quoted the *blocks* now: the build job's two failures are `git clone` dying in the LFS smudge filter, and the firmware job's is a `Traceback` inside `make ovmf-test` |
| push | `6293d65` | build job green-or-red below the fold | triage step ran; the readout is the point of that run, not its colour |

**The build job's failure was never about the image.**  `ReleaseAttributionRebuild._scratch` clones
the working tree to build in, `git clone` fetches and smudges LFS by default, `.gitattributes` routes
`PS5UPDATE.PUP` through LFS, and the LFS store holds no object for that pointer (`docs/BUILDING.md`
has recorded a `404` for it since the provenance work).  So on a runner with a cold cache the clone
dies — `remote missing object 42ea0e5…`, `fatal: PS5UPDATE.PUP: smudge filter lfs failed`, exit 128
— and both cases in that class fail before building anything, while a developer machine with the
payload cached passes and learns nothing is wrong.  `169fe94`'s `exit 139` is the same clone with its
output truncated by a signal (`tee`'s buffer lost); run 5, whose log survived, is the clean form of
the identical fault.  `169fe94` is the same fault with a truncated log — `exit 139`, `tee`'s buffer lost with the
   signal, nothing left for the reporter to quote; *why* that run died by signal rather than by
   `git`'s exit 128 is not recoverable from what survived, so it is recorded as the noisy form of
   this fault and not explained further.  The fix is `GIT_LFS_SKIP_SMUDGE=1` on that clone — the build reads no `.pup`,
and no assertion in the class is about the pointer's contents — pinned by a test written as text, so
it holds on machines where the fetch would succeed.

Neither variant is reproducible from this sandbox, and that is worth recording rather than papering
over: a `git clone file://…` inherits the source repository's `objects/` path, so git-lfs finds the
pointer's absence unremarkable and the smudge succeeds with or without the guard.  The failure is
CI's measurement, not a local deduction — which is the whole argument for having the pipeline.

Two properties of the pipeline are confirmed by these runs whatever the tests eventually do:
a failing gate fails the job (no `tee` hid it, no skip was counted as a pass), and
`publish release assets` reports `skipped` on every non-tag ref, which is its correct answer
rather than a hole.  A third is confirmed by the cancelled first run: `cancel-in-progress`
means a push while a run is in flight supersedes it, so a branch that commits often will
finish only its last run — read the newest, not the first.

## Attempts, dated

The instructions above were carried out and refused four times across five attempts, then
accepted on the fifth. Recording the refusals here, rather than only in a chat log, is what
keeps "the pipeline slept because of a permission, not because it was forgotten" checkable
later — and what makes the fifth row mean something: the same command, the same path, the
only difference being a permission the owner granted.

| When | Against | Command | Result |
|---|---|---|---|
| 2026-11-16 | `main` `237725d` | `git mv ci/build.yml .github/workflows/build.yml && git push` | `remote rejected … without workflows permission` |
| 2026-11-16 | `main` `a266936` | same | same |
| 2026-11-22 | `arena/b81a0441-pc5` off `aeca706e` | same, after `mkdir -p .github/workflows` (`git mv` does not create the parent) | `! [remote rejected] HEAD -> arena/b81a0441-pc5 (refusing to allow a GitHub App to create or update workflow `.github/workflows/build.yml` without workflows permission)` |
| 2026-11-24 | `arena/5e36f38b-pc5` off `84e6325`, PR #49's merge gate | same, after the five corrections above had landed so the first run would not be a run of a pipeline that cannot fail | refused, twice, 20 s apart, quoting the sentence verbatim (see the two 2026-11-13 blocks above for the same text) |
| 2026-11-24 | `arena/5e36f38b-pc5` off `0aaa881`, **after the owner granted *Workflows: Read and write*** | `mkdir -p .github/workflows && git mv ci/build.yml .github/workflows/build.yml && git commit && git push` | **accepted**: `0aaa881..2c3cd5c`, and GitHub read the workflow as `active` within eight seconds |

The third attempt was reverted (`git reset --mixed HEAD~1`, `ci/build.yml` restored, the
new directory removed) and the tree was verified clean against `aeca706e` afterwards; the
commit it created no longer exists locally, which is the point of quoting the rejection
verbatim above.

Meanwhile the pipeline file itself was freely editable — `ci/` is not a protected path, only `.github/workflows/` is — and it now runs the gates that were missing from it: `make stubtest64`, so the long-mode boot is executed, and
`make emulator-probe --json`, so the run carries its own record of what the emulator could
not prove.  `tests/test_release.py::CiLayoutTests` pins both, because a step that is
merely intended is a step that will be dropped.

### 2026-11-24, corrections made before the file moved (kept verbatim, including the date-stamped framing)

Five defects were found by reading the pipeline against what the repository has learned since it
was written.  All five are in the workflow now, committed to `ci/build.yml` at `8c14d39` and carried across by the rename, because a pipeline that
cannot fail is worse than no pipeline:

1. **`make <gate> | tee build/logs/<gate>.log` could not fail the job.**  GitHub's *unspecified*
   default shell on Linux runners is `bash -e {0}` — no `pipefail` — so the successful `tee` at the
   end of every pipe would have reported green after the gate failed.  The workflow now names
   `shell: bash`, which brings `--noprofile --norc -eo pipefail`.  This is the same failure mode
   §35 of `docs/TEST_RESULTS.md` measured in a test harness: a wrapper that certifies what it did
   not execute.
2. **The harness venv was installed after the host suite ran.**  A missing `unicorn` makes
   `tests/test_kernel64.py` *skip*, and a skip never fails a step — measured locally, the module
   printed `OK` after skipping 64 of its own cases.  Provisioning moved above the suite, the suite
   re-asserts the import, and every skip reason is written to the job summary: visible, never
   folded into a pass.
3. **Four of the six gates `make check` runs had no CI step at all.**  `make check` wraps the suite
   with `unit`, `psm`, `docs-check`, `citations-check`, `presentation-check` and `brand-check`; the
   pipeline ran `unit` and nothing else, so the platform contract (`psm/contract.py`, `psm/audit.py`,
   `psm/conformance.py`, `psm/render --check`), the brand assets, the documents and a dead commit
   citation could all drift while every CI job stayed green.  They run now — and `citation-refs` runs
   first, so a citation that cannot resolve in CI is reported as the tool reports it: first a
   statement about the clone, then about the history.
4. **The firmware job teed into `build/` before `build/` existed**, on a fresh checkout, and left
   checkout credentials behind where the other two jobs do not.
5. **The firmware job now states its own verdict in the run**, counting the harness's `[PASS]` and
   `[FAIL]` log markers and quoting its `NOT TESTED` line.  It counts the *log* rather than
   re-deriving a tally from the JSON, because `report["checks"]` (103 entries in the recorded run)
   and the requirement matrix (57 rows) are different counts, and conflating them is how 104 once
   became 105.

The exit-code contract is unchanged and deliberately *not* fixed: `tests/ovmf_boot.py` exiting 3
(NOT TESTED) still fails the `ovmf` job, so a runner without firmware is a red build, not a green
one.  Activation is still the owner-side grant below.
