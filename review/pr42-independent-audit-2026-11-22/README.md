# Independent adversarial review of PR #42, and integration verdicts for #40 / #41 / #42

Reviewed at head `709581450f24d61a23f4068c9b9df51e8729fc76` of `fix/reconcile-exemption-is-verified`
(base = `5bc5d5d4353f2e2c520224fd48711556b0054fc4`, the head of #41, whose base is #40's head
`8e3f0b642a92c2d2e27ba6d2a066803a70165697`; `main` = `52f682c8435956c8210b3189cad23ebea973d2de`).
Live GitHub state re-read at 05:55:10Z and again at 07:02:15Z on 2026-11-22, unchanged in between.

This is an audit, not an approval. Nothing was merged, approved, closed, retargeted or commented on;
no branch was renamed or rewritten; nothing was pushed to `main`; the working repositories under
`/home/user` were not modified. All measurement happened in a fresh clone
(`/tmp/audit42/repo`, `origin` renamed to `upstream`) with isolated worktrees, plus disposable
fixture repositories under `/tmp`.

| file | content |
|---|---|
| `PR42-INDEPENDENT-REVIEW.md` | live remote state, the full graph, and F-1…F-8 re-verified against both #41's and #42's exact heads through the public CLI |
| `FINDINGS.md` | the findings register: six classifications, evidence, and the fix applied on `fix/reconcile-premise-and-flags` |
| `LIFECYCLE-TEST-MATRIX.md` | 24 falsification probes + 15 history/flag probes + a 27-combination flag matrix, run against both trees |
| `TEST-COVERAGE-AUDIT.md` | audit of #42's own tests: skips, fixture-by-construction, discrimination, count consistency |
| `INTEGRATION-SEQUENCE.md` | #40 → #41 → #42 → bootstrap, simulated per merge method in a disposable clone with a real `origin` |
| `README-VISUAL-REVIEW.md` | §6 (README vs the reference image, truthfulness) and §7 (where the post-merge duty is discoverable) |
| `HASHES.txt` | every artefact hash, command, and exit code this review quotes |
| `raw/` | the three probe harnesses, their JSON, the gate logs, the simulation logs, the live API dumps |

Read `FINDINGS.md` first: it is the only file that separates what is closed from what is not.
Everything labelled `not verified` in these files was not established by anything I could run here
— chiefly GitHub branch protection (HTTP 403 to this token), physical hardware behaviour, and the
original reference screenshot, which is not present as a file in this workspace.
