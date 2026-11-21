# Commit-ready handoff — USB descriptor-bound remediation (reviewed state)

Produced by the independent review pass. **No commit, push, merge or PR action was taken.**
This file exists so an authorized committer can act without re-deriving anything.

## The object to commit

| Field | Value |
|---|---|
| File | `evidence/usb-descriptor-remediation-v1/REMEDIATION.patch` |
| Bytes | 55,331 |
| SHA-256 | `19793a838870913b3afa59e84994bdf1032ad5248d09b0a5192eac6c8a499dac` |
| Sidecar | `REMEDIATION.patch.sha256` (agrees) |
| Contents | 4 files, 9 hunks: `tools/pc5ctl.py` (M), `tests/test_pc5ctl_usb_safety.py` (M), `tests/test_pc5ctl_usb_descriptor_identity.py` (new), `tests/test_pc5ctl_usb_cli_boundaries.py` (new) |
| Verified base | `56c58521580e88c4339ceb6eb40ce724a2bb533b` (`main` == local HEAD == branch base) |
| Also verified at | `ae9f3e25f66ac3d31b2b7b64dccef7695fef1d73` (pushed `arena/c8a76776-pc5` tip, 5 commits ahead): both patched files are unchanged between the two revisions, so the patch applies identically |
| Supersedes | 55,289 B `65944ecd44922d67b7fa16d34c9716a2724a16e6e77a4d719d3655be0471e9ea` (keep; recoverable from `recovery/pc5-usb-descriptor-remediation-20261121-v2.tar.gz`) |

## Resulting tree, by hash — measured, not transcribed

```
tools/pc5ctl.py                                     36,695 B  sha256 2b221bd01b6be8beaf05095e9107119496359eff77cb5bfda36336ed78cf1441
tests/test_pc5ctl_usb_safety.py                     49,004 B  sha256 11879172c53b192e198382d76841f51ea0694c1c2e1074d62759764b54ea3c80
tests/test_pc5ctl_usb_descriptor_identity.py        32,006 B  sha256 1a0955e17f6ef9977e511525206a00d9bb1c01d8621565e0b731baba505754d9
tests/test_pc5ctl_usb_cli_boundaries.py              6,338 B  sha256 faf65e73b2db477745fc54ab46711e41ca4286d5b766a059ae82f0d67929f0d1
```

Blob equality, checked both directions: the patch's `index` lines predict the delivered bytes, and the
base side equals `git rev-parse <rev>:<path>`.

| Path | patch pre-image | patch post-image | measured `git hash-object` of the workspace file | base blob at `56c5852` **and** at `ae9f3e25` |
|---|---|---|---|---|
| `tools/pc5ctl.py` | `c1684f8` | `f0176fe` | `f0176fe` ✓ | `c1684f8a3eb515c7271b092addb31f3410b56256` ✓ |
| `tests/test_pc5ctl_usb_safety.py` | `7a40a21` | `1715de9` | `1715de9` ✓ | `7a40a21547ff3cd636f7974f794f6e0609b5d11a` ✓ |
| `tests/test_pc5ctl_usb_descriptor_identity.py` | `0000000` | `3af8dad` | `3af8dad` ✓ | new file |
| `tests/test_pc5ctl_usb_cli_boundaries.py` | `0000000` | `2fd7a35` | `2fd7a35` ✓ | new file |

## ## Commands, verified to work from a clean clone

```sh
git clone --no-hardlinks <this-workspace-or-remote> pc5 && cd pc5
git checkout 56c58521580e88c4339ceb6eb40ce724a2bb533b
git apply --check /path/to/REMEDIATION.patch          # exit 0, no fuzz
git apply         /path/to/REMEDIATION.patch          # exit 0
# (the two new test files are created by `git apply`; nothing else has to be copied)
# 91-test boundary suite
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_pc5ctl_usb*.py'
#   -> Ran 91 tests ... OK
# full gate (needs the two prerequisites below)
pip install --user 'unicorn==2.1.4'
git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'
TMPDIR=/tmp make check
#   -> exit 0: Ran 924 tests ... OK (skipped=2); PASSED: 165 checks, 0 failures;
#      DOCS OK: 57 Markdown files, 33 make targets; CITATIONS OK: 258 across 11 documents;
#      PRESENTATION OK; BRAND OK; PSM conformance 261/261
```

Without `unicorn`: the same suite reports `FAILED (errors=103)`, all from the missing module.
Without the PR refs: `make` stops at `citations-check` with `258 dead commit reference(s) across 11
documents`, exit 2 — historical SHAs cited by pre-existing review documents resolve only through
`refs/pull/*/head`, which a plain clone does not fetch. Both facts are findings F-1 in
`INDEPENDENT-REVIEW-20261121.md`, not defects in the patch.

## Decisions the committer must make (reviewer cannot make them)

1. **Target revision.** Committing on the current working state (base `56c5852`) produces a branch head
   that is 5 behind the pushed `arena/c8a76776-pc5` tip; a push would be rejected non-fast-forward.
   Either integrate `ae9f3e25` first — safe: those commits touch no file this patch touches — or
   record explicitly that the branch is being rebased/merged by the committer's choice.
2. **Scope of the commit.** The patch is production + tests only. `evidence/` and `docs/engineering/`
   are untracked; if the gate documents are meant to be history, they need a separate decision
   (several of them cite commit SHAs that only exist via PR refs — F-1).
3. **Findings F-1…F-3** are documentation-provenance corrections to the *record*, and should be
   applied as an addendum rather than by rewriting the superseded logs (keep `65944ecd…`, keep the
   v1/v2 manifests with their known-wrong `generated` values, keep the archived `MAKE-CHECK.log`).

## Not established by this handoff

No physical-device, OVMF, firmware or USB acceptance evidence exists in this branch, and none of the
review's results substitute for it. USB deployment remains **BLOCKED**. PR31 and PR34 are separate
tracks and are not advanced by this review.
