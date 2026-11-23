# CI pipeline (not yet active)

`ci/build.yml` is the PC5 build-and-release pipeline. It is **stored here rather
than in `.github/workflows/` on purpose**: the GitHub App that authored this
branch has repository admin rights but not the *workflows* permission, so GitHub
rejects any push or API call that creates a file under `.github/workflows/`
(`refusing to allow a GitHub App to create or update workflow ... without
workflows permission`).

Nothing about the pipeline is missing — only the location. To activate it:

```sh
# 1. from a checkout of main with this file present
git mv ci/build.yml .github/workflows/build.yml
git commit -m "ci: activate the build and release pipeline"
git push

# 2. (or grant the GitHub App the "Workflows" permission and let an agent do it)
```

Then cut a release:

```sh
git tag -a v0.2.0 -m "PC5 0.2.0"
git push origin v0.2.0
gh run watch
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
* Not proven: that GitHub Actions runs it and accepts the upload — that needs
  the file in `.github/workflows/`, which needs the permission above.

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

   Step (b) is deliberately *not* applied in advance: `.github/workflows/build.yml` in a document is a
   claim about the repository, and until the push is accepted the claim would be false.  Milestone
   `M1b` stays `blocked`, and the living documents keep pointing at `ci/build.yml`, for the same
   reason.  Dated records (the audit reports at the root, `docs/evidence/**`, the historical
   `TEST_RESULTS` sections) are never retargeted: they reported the location that was true then.

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

## Attempts, dated

The instructions above have been carried out and refused three times. Recording the
refusals here, rather than only in a chat log, is what keeps "the pipeline is dormant
because of a permission, not because it was forgotten" checkable later.

| When | Against | Command | Result |
|---|---|---|---|
| 2026-11-16 | `main` `237725d` | `git mv ci/build.yml .github/workflows/build.yml && git push` | `remote rejected … without workflows permission` |
| 2026-11-16 | `main` `a266936` | same | same |
| 2026-11-22 | `arena/b81a0441-pc5` off `aeca706e` | same, after `mkdir -p .github/workflows` (`git mv` does not create the parent) | `! [remote rejected] HEAD -> arena/b81a0441-pc5 (refusing to allow a GitHub App to create or update workflow `.github/workflows/build.yml` without workflows permission)` |
| 2026-11-24 | `arena/5e36f38b-pc5` off `84e6325`, PR #49's merge gate | same, after the five corrections above had landed so the first run would not be a run of a pipeline that cannot fail | refused, twice, 20 s apart, quoting the sentence verbatim (see the two 2026-11-13 blocks above for the same text) |

The third attempt was reverted (`git reset --mixed HEAD~1`, `ci/build.yml` restored, the
new directory removed) and the tree was verified clean against `aeca706e` afterwards; the
commit it created no longer exists locally, which is the point of quoting the rejection
verbatim above.

Meanwhile `ci/build.yml` itself is freely editable, and it now runs the gates that were
missing from it: `make stubtest64`, so the long-mode boot is executed, and
`make emulator-probe --json`, so the run carries its own record of what the emulator could
not prove.  `tests/test_release.py::CiLayoutTests` pins both, because a step that is
merely intended is a step that will be dropped.

### 2026-11-24, corrections made while the file is still editable

Five defects were found by reading the pipeline against what the repository has learned since it
was written.  All five are in `ci/build.yml` now, before any activation, because a pipeline that
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
3. **The documents had no gate.**  `make docs-check`, `make presentation-check`,
   `make citation-refs` and `make citations-check` run now, so CI cannot be weaker than the local
   `make check`, and a dead citation is reported as what the tool already calls it — first a
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
