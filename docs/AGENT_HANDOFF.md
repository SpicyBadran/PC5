# Agent handoff — 2026-11-28

For the next agent. Everything needed to resume is in the repository; this page says where.

## Objective, unchanged
Genuine original Sony PlayStation 5 operating system executing inside `PC5.exe` on Windows 11
([`PRODUCT_VISION.md`](PRODUCT_VISION.md), [`../AGENTS.md`](../AGENTS.md)). Do not substitute a
theme, launcher, mock OS, or a generic VM for that objective; do not claim progress on it
without evidence.

## Where the work is
| Item | Value |
|---|---|
| Verified `main` at hand-off | `9b20228` (`docs: refresh the progress dashboard at a79df48`, the first bot-authored commit — the evidence loop is proven) |
| Working branch | `arena/5e36f38b-pc5` |
| Commits on it | `2e5d39f` (baseline, PUP verification, path verdicts, ADR-028…035, the README licence fix) · `c433362` (`app/`, the `windows app` dimension, `WS14` + `M22`–`M29`, ADR-036/037) · `6a83af9` (derived release state, the README app row, the licence-prose gate, probe honesty) · `b207163` (debt D-2, D-5) · `248f27e` (progress register) and this page's commit |
| Open PR | **#55** → https://github.com/SpicyBadran/PC5/pull/55 — base `main` at `9b20228`; push this branch to it and re-read the runs per step, do not reuse an earlier verdict |
| Merge authority | **the owner's only.** Do not merge #55, or anything else, without an explicit instruction naming the number |
| Other open PRs | 9, on older foreign branches (#36–#39 clean against `main`; #31, #40–#43 conflict with the dashboard code #51–#54 replaced). Untouched, deliberately |
| Working tree | clean except untracked `.arena/` (agent-local — never `git add -A`, never commit it) |

## Verification state to re-read before doing anything
```
git fetch origin && git log --oneline -1 origin/main
gh pr view 55 --json state,mergeable,mergeStateStatus,headRefOid,changedFiles,additions,deletions
gh api repos/SpicyBadran/PC5/actions/runs/<id>/jobs --jq '.jobs[]|"\(.name): \(.status) \(.conclusion//"-") \([.steps[]?|select(.status=="completed")]|length)/\(.steps|length)"'
python3 -m unittest tests.test_pc5_app_structure tests.test_app_evidence   # 28 OK here
make docs-check citations-check presentation-check completion-check roadmap brand-check
```
Do **not** poll a ~26-minute hosted build inside one session; read its status once, at the
start of the next, and do not infer a hang from `updated_at`.

## Known environment limits (they shaped every status above)
No `dotnet`, `msbuild`, `csc`, `clang`, `cmake`, `cargo`; no Windows, no hypervisor, no GPU;
`git lfs` absent; `unicorn` absent (so 2 `test_ci_report` tests fail locally — D-8/B-7, not a
regression). `pyyaml` installs from pypi into a venv if you need to parse workflow YAML.

## Next three, in order
1. **Read #55's CI per step** and post the verdict; if the app job's structural gates fail on
   the real runner, fix what they caught, do not weaken them.
2. **Land the first Windows run of `app/`** (`windows app` workflow): confirm `dotnet restore`
   resolves `Microsoft.WindowsAppSDK`/`xunit` at the pinned versions, correct them from the
   API's own listing if they do not, **pin from the successful run**, and only then move `M23`
   from *source-only* toward E1. Never mark E1 from a Linux build of the core.
3. **Fix B-4 (release-state derivation) and B-5 (licence gate)** in isolated commits with tests —
   they are the two debt items where the repository currently states something false with
   confidence. Then `docs/TRANSFORMATION_BASELINE.md`'s D-5/D-6 lines can be marked closed by
   a dated follow-up note (do not rewrite the baseline).

## Recovery
`git push origin arena/5e36f38b-pc5` after every coherent unit; verify with
`git ls-remote origin refs/heads/arena/5e36f38b-pc5`. Nothing in this sprint exists only in the
sandbox. No history was rewritten, no tag moved, no branch deleted, nothing merged.
