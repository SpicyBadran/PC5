# USB descriptor remediation — package v2

This directory is the **closure package** for the three findings raised by the
independent review of `evidence/usb-descriptor-remediation-v1/`. It adds to that
package; it does not replace, edit, or invalidate it. Nothing under
`evidence/usb-descriptor-remediation-v1/` was modified to produce this directory —
its `REMEDIATION.patch`, `HASHES.txt`, logs and `pristine/` copies are the bytes the
implementation delivered, and they are re-verified here rather than rewritten.

| File | What it is |
|---|---|
| `USB-REMEDIATION-v2.patch` | The delta the independent review was performed on: 100,588 B, `e1dae862d0d01af8…`, 16 file diffs. Kept byte-identical so the reviewed artifact stays checkable; the branch's own delta is deliberately **not** committed here (a commit cannot contain its own diff — that is finding F-2) and is anchored in `logs/30-pr-opened.txt`. |
| `FINDINGS-CLOSURE-20261121.md` | F-1, F-2 and F-3: what each finding said, what was changed, how the correction was reproduced, and the status of each. Includes the corrections to the review's own claims, which are recorded rather than quietly dropped. |
| `TIMESTAMP-RECONCILIATION.md` | F-3: the three different times a continuity archive, its manifest and a verification run each record, and which of them can still be checked. |
| `CORRECTED-MANIFEST.txt` | F-2: a manifest that follows the rule `HASHES.txt` stated and broke. It excludes itself by design and names the identity of the manifest it corrects. |
| `PR-BODY.md` | The body submitted with the pull request opened from `fix/usb-descriptor-bound-remediation`, plus why this branch carries no committed copy of its own diff. The PR number and diff identity land in `logs/30-pr-opened.txt` after the push. |
| `FINDINGS-DISPOSITION-20261121.md` | F-4…F-10: one row per finding — class, the command that reproduces it, what it measured, what was done or why nothing was, and whether it blocks merge. F-6, F-8 and F-9 are recorded **open** with mechanisms and candidate fixes named. |
| `mutation_harness_v2.py` | The nine reviewed mutation texts, **imported** from `../usb-descriptor-remediation-v1/mutation_harness.py`, with an intention map that asserts each named test exists in the loader's 91-name inventory and fails under its mutant, prints unclaimed failures, and keeps v1's five non-discriminating entries in `NOT_PROTECTIVE` instead of deleting them. `RESULT: PASS` = baseline green, every mutant caught, every intended test present and discriminating. |
| `measure_mutation_mapping.py` | How that map was measured (the source of `logs/26-mutation-mapping-measured.json`); read-only against the repository, scratch copies only. |
| `HASHES-PR-TIP.txt` | Artifact identities as of the committed tip, generated after every file it records and excluding itself. `HASHES.txt` beside it is the 19:36Z capture and is **stale by construction** for the files this pass added — recorded in `CORRECTED-MANIFEST.txt`, not regenerated away. |
| `logs/` | Transcripts of every command in `FINDINGS-CLOSURE-20261121.md`, each prefixed with the command line that produced it and the UTC capture time. `logs/INDEX.txt` lists all 25 with the claim each one supports. |
| `HASHES.txt` | This directory's artifact identities, generated after every other file here existed, excluding itself. |

`logs/` numbering has gaps by intent, not by loss: `01` was a clone transcript that
was never needed, and `11`/`20` were renumbered once the prior-harness comparison needed
two inventories under distinguishable names. `INDEX.txt` is the authority on what exists.

## Reading order

`FINDINGS-DISPOSITION-20261121.md` for the seven remaining findings; then
`FINDINGS-CLOSURE-20261121.md`, which is the argument for the three that gated the merge. `logs/` is the evidence for
it, and `HASHES.txt` authenticates this directory's files. The review being answered
is `evidence/usb-descriptor-independent-review-v1/INDEPENDENT-REVIEW-20261121.md`.

## What this package does not claim

This package was produced while nothing was committed or pushed; the branch
`fix/usb-descriptor-bound-remediation` and its draft PR were created after the final
capture (2026-11-21T19:51:01Z) by the authorisation in the session directive, and every number quoted
above was measured before that commit — nothing here was re-authored afterwards. No
real block device, USB stick, firmware or machine was touched: every test here runs
against temporary regular files and a synthetic `/sys/dev/block` tree.
**Writing media with `pc5ctl usb` is not deployment-approved; it remains blocked
pending physical-device validation and explicit authorisation.**
