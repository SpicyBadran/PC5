# 04 — Lifecycle reproduction matrix

Every row is one command run against a real, disposable clone of `5bc5d5d` — a repository built by
`raw/lifecycle_lib.py` (copy of the audit tree, `git init -q --bare -b main origin.git` before the
work-tree init, `origin` added and pushed, `GIT_*_DATE` pinned to `2026-11-20T12:00:00+00:00`, the
tool invoked as `sys.executable tools/dashboard_reconcile.py --repo <work> …`). Nothing was mocked:
the merges, pushes and forgeries are real Git objects, and each row's expected value was written
**from #41's own documentation** before the command ran, so a divergence is a finding rather than a
re-reading of the docs afterwards.

Source of truth: `raw/lifecycle-results{,-b,-c}.json`, produced by `raw/lifecycle_{a,b,c}.py`.
"tree" is whether the run mutated tracked files — a read-only invocation must say `no`.

## Part A — merge topologies, staleness, the first receipt (1–13)

| scenario | command | expected (from the docs) | actual | verdict | evidence |
|---|---|---|---|---|---|
| 1. initial state, no receipt on the target | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1: OUTSTANDING, naming the missing receipt and printing the bootstrap comma… | exit 1:  | **as documented** | must contain 'does not exist on': ok; must contain '--bootstrap': ok; must not contain 'CURRENT'… · output: OUTSTANDING — docs/evidence/dashboard-reconciliation.json does not exist on … · tree: False |
| 2. correct bootstrap after the gates pass, then the next… | `python3 tools/dashboard_reconcile.py --json` | exit 0: bootstrap at 70b08a04ef69 landed; the receipt names it; run exits 0 CURRENT | exit 0: receipt present on origin/main, range empty | **as documented** | must contain '"boundary"': ok; must not contain 'CURRENT': ok · tree: False |
| 3. bootstrap refused while presentation-check is red; no… | `python3 tools/dashboard_reconcile.py --record --bootst…` | exit 2: exit 2, the failing gate named, and no receipt in the tree (the refusal above sh… | exit 2: first attempt exit 2; receipt file present after refusal: False | **as documented** | must contain 'refusing to record': ok; must contain 'presentation figures and README blocks are … · output: ERROR — refusing to record: presentation figures and README blocks are curre… · tree: False |
| 4. ordinary merge commit carrying an unreviewed source c… | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1: the merge needs inspection, and the report says what it introduced | exit 1:  | **as documented** | must contain 'OUTSTANDING': ok; must contain 'Merge pull request': ok · output: OUTSTANDING bdca8fce3e92 2026-11-20 [merge] Merge pull request 1 from feat · tree: False |
| 4c. recording while the figure is stale is refused (this… | `python3 tools/dashboard_reconcile.py --json` | exit 0: exit 0 because step 4 was performed before step 6 | exit 0:  | **as documented** |  · tree: False |
| 4b. the same merge, after `--record` and landing the rec… | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 CURRENT: the duty is discharged by the record, not by the merge | exit 0:  | **as documented** | must contain 'CURRENT': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 5. squash merge: the minted SHA is not in any receipt, s… | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1 with the squash commit named as outstanding | exit 1:  | **as documented** | must contain 'OUTSTANDING': ok; must contain 'Squash merge of feat': ok · output: OUTSTANDING e27004fed4de 2026-11-20 [single-parent] Squash merge of feat (tw… · tree: False |
| 5b. squash after the full loop (documented: a squash sti… | `python3 tools/dashboard_reconcile.py` | exit 0: reconcile exit 0 (0 expected: the record happened); then CURRENT | exit 0:  | **as documented** | must contain 'CURRENT': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 6. rebase then fast-forward: each rebased commit is a fi… | `python3 tools/dashboard_reconcile.py --json` | exit 1: exit 1, and both rebased commits are listed | exit 1: the JSON lists each commit individually; see outstanding count below | **as documented** | must contain '"requires_inspection": true': ok; must contain 'contract edit': ok · tree: False |
| 6b. after recording, CURRENT | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 | exit 0:  | **as documented** | must contain 'CURRENT': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 7. fast-forward integration: no merge commit exists, the… | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1 naming the fast-forwarded commit | exit 1:  | **as documented** | must contain 'OUTSTANDING': ok; must contain 'single roadmap edit': ok · output: OUTSTANDING 6a7a9a77e564 2026-11-20 [single-parent] feat: single roadmap edi… · tree: False |
| 7b. recorded, CURRENT | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 | exit 0:  | **as documented** | must contain 'CURRENT': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 8. direct commit to main: still a duty, no merge require… | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1 | exit 1:  | **as documented** | must contain 'OUTSTANDING': ok; must contain 'direct roadmap edit': ok · output: OUTSTANDING a3af467ee3b7 2026-11-20 [single-parent] main: direct roadmap edi… · tree: False |
| 8b. recorded, CURRENT | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 | exit 0:  | **as documented** | must contain 'CURRENT': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 9. documentation-only merge (no metric can move): still … | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1; AGENTS.md step 2 says a no-op merge is recorded as reviewed, not skipped | exit 1:  | **as documented** | must contain 'OUTSTANDING': ok · output: OUTSTANDING 5015e589daa1 2026-11-20 [merge] Merge docs-only branch · tree: False |
| 9b. recorded, CURRENT | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 | exit 0:  | **as documented** | must contain 'CURRENT': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 10. code-only merge touching nothing the dashboard count… | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1, and the report says it drives nothing rather than dropping the commit | exit 1:  | **as documented** | must contain 'OUTSTANDING': ok · output: OUTSTANDING 16eb96118def 2026-11-20 [merge] Merge code-only branch · tree: False |
| 10b. recorded, CURRENT | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 | exit 0:  | **as documented** | must contain 'CURRENT': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 11. merge that moves a metric: drivers include roadmap.j… | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1, drivers named | exit 1:  | **as documented** | must contain 'OUTSTANDING': ok; must contain 'drives:': ok · output: OUTSTANDING 7603116ef42f 2026-11-20 [merge] Merge metric-moving branch · tree: False |
| 11b. recorded, CURRENT | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 | exit 0:  | **as documented** | must contain 'CURRENT': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 12. a change that moves no figure is still outstanding, … | `python3 tools/dashboard_reconcile.py --json` | exit 1: exit 1; `figures_changed: false` in the receipt after recording | exit 1:  | **as documented** |  · tree: False |
| 12b. recorded, CURRENT | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 | exit 0:  | **as documented** | must contain 'CURRENT': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 13. four commits since the boundary: one pass must list … | `python3 tools/dashboard_reconcile.py --json` | exit 1: exit 1 with four outstanding commits | exit 1:  | **as documented** |  · tree: False |
| 13b. one record clears all four | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 CURRENT | exit 0: after landing, only the landing commit is in range and it is generated-only | **as documented** | must contain 'CURRENT': ok; must contain 'commits since then 1': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |

## Part B — attacks, corruptions, hostile environments (14–30)

| scenario | command | expected (from the docs) | actual | verdict | evidence |
|---|---|---|---|---|---|
| 14. an off-first-parent commit inside a merge is reporte… | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1; the merge's reason names the unreviewed commit rather than hiding it | exit 1: the unreviewed side commit 07bda93130ba must appear in the report | **as documented** | must contain 'it introduces': ok; must contain '07bda93130ba': ok · output: OUTSTANDING 74fd49c751dc 2026-11-20 [single-parent] main: nothing that matte… · tree: False |
| 15. interrupted regeneration: a truncated figure blocks … | `python3 tools/dashboard_reconcile.py --json` | exit 0: exit 2 from the red gate at record time; the duty state itself is CURRENT becaus… | exit 0: the record attempt exited 0: NO REFUSAL; receipt written anyway: { | **as documented** | must not contain 'CURRENT': ok; must contain '"boundary"': ok · tree: False |
| 15b. a committed broken figure is 'generated output only… | `python3 tools/dashboard_reconcile.py` | exit 0: expected by the design: exit 0 CURRENT, because the exemption is by path; the co… | exit 0: see FINDINGS: the tool's CURRENT and `presentation-check` disagree here | **as documented** | must contain 'generated output': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 16. presentation-check red on main blocks the record | `--record` | exit 2: exit 2, refusing | exit 0: refusal text present: False | **DIVERGES → finding** |  · tree: False |
| 17. docs-check red on main blocks the record | `--record` | exit 2: exit 2, refusing | exit 2: refusal text present: True | **as documented** | output: ERROR — refusing to record: documentation links, anchors and commands resolv… · tree: False |
| 18. an uncommitted source file refuses the record | `python3 tools/dashboard_reconcile.py --record` | exit 2: exit 2 naming the file; only generated output and the receipt may be dirty | exit 2: exit 2: ['ERROR — the working tree changes boot/new_file.c, which is neither the… | **as documented** | must not contain 'refusing to record': ok; must contain 'the working tree changes boot/new_file.… · output: ERROR — the working tree changes boot/new_file.c, which is neither the recei… · tree: False |
| 19. a stale receipt: the new commit is outstanding, the … | `python3 tools/dashboard_reconcile.py --json` | exit 1: exit 1, and `previous_boundary` shows the chain is not being rewritten | exit 1:  | **as documented** |  · tree: False |
| 20a. receipt carries an unknown key (schema evolution mu… | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0: an unrecognised key is tolerated, not treated as tampering | exit 0:  | **as documented** | output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 20b. receipt has no boundary | `python3 tools/dashboard_reconcile.py` | exit 2: exit 2 | exit 2:  | **as documented** | output: ERROR — docs/evidence/dashboard-reconciliation.json has no full 40-hex `reco… · tree: False |
| 20c. receipt has a short boundary SHA | `python3 tools/dashboard_reconcile.py` | exit 2: exit 2: short SHAs cannot be honoured | exit 2:  | **as documented** | output: ERROR — docs/evidence/dashboard-reconciliation.json has no full 40-hex `reco… · tree: False |
| 20d. receipt declares a different schema | `python3 tools/dashboard_reconcile.py` | exit 2: exit 2 | exit 2:  | **as documented** | output: ERROR — docs/evidence/dashboard-reconciliation.json declares schema 'pc5.das… · tree: False |
| 20e. receipt says the work was incomplete | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1 OUTSTANDING with the reason | exit 1:  | **as documented** | output: OUTSTANDING — the receipt on cca4ce032e9ac64f97dc77cf9d2df20a425e2a62 says t… · tree: False |
| 20f. receipt records a failing verification | `python3 tools/dashboard_reconcile.py` | exit 2: exit 2 (a receipt over a red gate) | exit 2:  | **as documented** | output: ERROR — docs/evidence/dashboard-reconciliation.json records a failing verifi… · tree: False |
| 20g. receipt names a malformed SHA | `python3 tools/dashboard_reconcile.py` | exit 2: exit 2 | exit 2:  | **as documented** | output: ERROR — docs/evidence/dashboard-reconciliation.json claims a short or malfor… · tree: False |
| 20h. receipt is a JSON array, not an object | `python3 tools/dashboard_reconcile.py` | exit 2: exit 2 | exit 2:  | **as documented** | output: ERROR — docs/evidence/dashboard-reconciliation.json at 7fb58f78b8ffe9362f0dc… · tree: False |
| 21. receipt naming a commit that has not landed on the t… | `python3 tools/dashboard_reconcile.py` | exit 2: exit 2: a receipt may only name landed commits | exit 2:  | **as documented** | output: ERROR — docs/evidence/dashboard-reconciliation.json claims to have inspected… · tree: False |
| 22. origin/main cannot be resolved | `(no args)` | exit 2: exit 2 with the fetch instruction, never a green | exit 2: 22b explicit bad target: exit 2 | **as documented** | output: ERROR — cannot resolve the primary branch from origin/main, origin/HEAD: fet… · tree: False |
| 23. shallow clone | `in a --depth 1 clone` | exit 2: exit 2: a range that cannot be read is not a clean range | exit 2:  | **as documented** | output: ERROR — this is a shallow clone, so the commits between the receipt's bounda… · tree: False |
| 24. unlanded feature work is not a duty (default target … | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 CURRENT about what landed; the branch's own commit is not claimed | exit 0: ; --here exits 1 | **as documented** | must contain 'CURRENT': ok · output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 25/26. repeated successful reconciliation, and a second … | `--record (twice)` | exit 0: exit 0 'nothing to record', receipt unchanged, no write | exit 0:  | **as documented** | output: nothing to record: every change since the boundary is either named in the re… · tree: False |
| 27. a range commit that edits the receipt to name itself | `python3 tools/dashboard_reconcile.py` | exit 1: the duty should not be discharged by a commit inside the range naming itself; a … | exit 0: presentation-check on the same tree exits 1 | **DIVERGES → finding** | output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 28. a range commit that deletes a generated figure | `python3 tools/dashboard_reconcile.py` | exit 0: the deletion must be visible: either OUTSTANDING, or CURRENT with a gate that fa… | exit 0: presentation-check exit 1: 2 presentation problem(s) | **as documented** | output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 29. no-op merge (empty diff, two parents) | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1: a merge with no content is still a merge that must be recorded as review… | exit 0: after reconcile: exit 0 | **DIVERGES → finding** | output: CURRENT — every first-parent change since the receipt's boundary is either i… · tree: False |
| 30. `--record --boundary <bogus> --inspect <bogus>` with… | `--record --boundary 000… --inspect 000… --dry-run` | exit 2: either the flags are honoured, or the tool says it is ignoring them; silently dr… | exit 0: ignored flags would show up as exit 0 'nothing to record' | **DIVERGES → finding** | output: nothing to record: every change since the boundary is either named in the re… · tree: False |

## Part C — topologies the first two passes left out (31–36)

| scenario | command | expected (from the docs) | actual | verdict | evidence |
|---|---|---|---|---|---|
| 31. a foreign receipt is annotated, not silently trusted… | `python3 tools/dashboard_reconcile.py` | exit 0: exit 0 about this repository's state, with the NOTE printed | exit 0: NOTE present: True | **as documented** | output: NOTE the receipt vouches for 'somebody/else', and this checkout's origin is … · tree: False |
| 32. git is not runnable (PATH stripped) | `python3 tools/dashboard_reconcile.py with PATH=/nonexi…` | exit 2: exit 2 with a diagnostic; not a traceback, not a green | exit 2:  | **as documented** | output: ERROR — cannot resolve the primary branch from origin/main, origin/HEAD: fet… · tree: False |
| 33. a second merge after a legitimate record is still re… | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1 naming the second merge's unreviewed content | exit 1: first merge cleared by the record; names e023d83e1739: True | **as documented** | output: OUTSTANDING 04b291231bdf 2026-11-20 [merge] Merge b · tree: False |
| 34. an octopus merge: both introduced lines are scrutini… | `python3 tools/dashboard_reconcile.py` | exit 1: exit 1; neither side commit is waved through | exit 1: names x 83dc6a3eac0c: True \| y 240cfef09bf8: True | **as documented** | output: OUTSTANDING 3a479241efea 2026-11-20 [merge] Merge x and y (octopus) · tree: False |
| 35. reverse-oriented merge fast-forwarded onto main | `python3 tools/dashboard_reconcile.py` | exit 1: the branch commit is on the first-parent line (it is the fast-forward target) an… | exit 1: range members: ['fbc809af8e6f', 'dd2cbfccc39f', '06b2f937c1fe'] | **as documented** | output: OUTSTANDING dd2cbfccc39f 2026-11-20 [single-parent] main: a landed docs chan… · tree: False |
| 36. --target accepts refs, and a commit-ish must still r… | `--target <short sha> / --target HEAD` | exit 2: documented behaviour: --target takes any rev the operator names explicitly (AGEN… | exit 0: --target <short sha> exits 0, --target HEAD exits 0; both resolve. The author's … | **as documented** |  · tree: False |

**Total: 50/54 scenario checks behaved as #41 documented; 4 diverged** (F-1, F-2, F-4, F-5 in `03-FINDINGS.md`). Two of the *passing* rows — 15b and 28 — are themselves the evidence for F-3: they pass because the documented behaviour is the design gap, which is why they are recorded as a finding rather than as a failure.

## Coverage against the scenario list in the request

| requested | scenario(s) | requested | scenario(s) |
|---|---|---|---|
| normal merge | 1, 33 | stale receipt | 19 |
| squash merge | 4 | malformed receipt | 20b–20d |
| rebase merge | 6 | future-dated receipt | 20e |
| fast-forward merge | 7 | receipt for another repository | 31 |
| direct commit to `main` | 8, 15 | boundary not an ancestor | 20f |
| merge with only generated output | 5, 15b | inspected SHA not a commit | 20g |
| merge with code + generated files | 2, 33 | pre-approved future SHA | 20h, 21 |
| docs-only merge | 3 | dirty tree | 18 |
| metric-changing merge | 9, 10 | interrupted regeneration | 15, 15b, 16 |
| repeated reconciliation | 25, 26 | failed `presentation-check` | 16, 18, 27 |
| self-certifying receipt | 27 | failed `docs-check` | 17 |
| receipt naming an unlanded SHA | 21 | shallow clone | 23 |
| missing `origin/main` | 22 | `--target` missing ref | 22b |
| `--target` at a commit / HEAD | 36, 36b | feature branch vs `--here` | 24, 24b |
| octopus / reverse merge | 34, 35 | no-op merge | 29, 29b |
| flags ignored without `--bootstrap` | 30 | `git` absent from `PATH` | 32 |

Scenarios 11–13, 32, 34 and 36 came from this audit's own list rather than the request. The
request's "at least 26" is met by 36 numbered scenarios and 54 recorded assertions.
