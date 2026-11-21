# PC5 — Independent adversarial review: USB descriptor-bound remediation

Reviewer: Arena agent, review pass (no implementation role in this pass)
Date: 2026-11-21, review window 18:04:52Z – 18:40Z (UTC)
Object under review: `evidence/usb-descriptor-remediation-v1/REMEDIATION.patch`
Verdict: **CONDITIONAL — SPECIFIC FINDINGS REMAIN** (no safety vulnerability found in the patch;
the evidence package and two reproducibility claims need correction)

---

## 0. What was reviewed, measured first

| Item | Measured value |
|---|---|
| Workspace HEAD / branch | `56c58521580e88c4339ceb6eb40ce724a2bb533b` / `arena/c8a76776-pc5` |
| Commits beyond base | 0 (no upstream configured for the branch) |
| Tracked modifications | exactly `tools/pc5ctl.py`, `tests/test_pc5ctl_usb_safety.py` |
| Untracked entries (`status --porcelain -uall`) | 215, all pre-existing to this review |
| Patch bytes / sha256 | **55,331 B / `19793a838870913b3afa59e84994bdf1032ad5248d09b0a5192eac6c8a499dac`** |
| Patch shape | 4 files, 9 hunks; sidecar `REMEDIATION.patch.sha256` agrees |
| Superseded revision | 55,289 B `65944ecd44922d67b7fa16d34c9716a2724a16e6e77a4d719d3655be0471e9ea` (recoverable from `recovery/…-v2.tar.gz`, re-measured there) |

mtimes in this sandbox are normalised (the patch, `HASHES.txt`, the archives and an untouched
doc all read `1795284290`), so **no ordering claim in this review rests on an mtime**; ordering
comes from log bodies and manifest fields.

## 1. Independent environment (requirement 2) — PASS

`git clone --no-hardlinks /home/user/pc5`, `git checkout 56c5852…`, clean status (0 entries).

* `git apply --check --verbose` → exit 0; `git apply` → exit 0; **no fuzz, no offset**.
* Resulting tracked diff is byte-for-byte the implementation's: `tests/test_pc5ctl_usb_safety.py | 33 ±`,
  `tools/pc5ctl.py | 190 ±`, `2 files changed, 196 insertions(+), 27 deletions(-)`.
* After applying, `status` shows exactly 2 `M` + the 2 new test files `??`, and all four files are
  sha256-identical to the delivered workspace copies (`2b221bd0…`, `11879172…`, `1a0955e1…`, `faf65e73…`).
* **No untracked prerequisite**: the new tests import stdlib + `pc5ctl` + the sibling test module only;
  nothing reads `evidence/` or `docs/`. Nothing unrelated changes.
* Same test on the *pushed* branch tip `ae9f3e25f66ac3d31b2b7b64dccef7695fef1d73`: the two files the patch
  modifies are **unchanged between base and tip** (`tools/pc5ctl.py` blob `c1684f8a…`,
  `tests/test_pc5ctl_usb_safety.py` blob `7a40a215…` at both), the patch applies with exit 0, the
  resulting `pc5ctl.py` is the same `2b221bd0…`, and the 91-test suite passes there too.

## 2. The eight boundary properties (requirement 3) — all hold, by construction

Traced in the patched source (`tools/pc5ctl.py`), not from comments:

1. **Descriptor opened before the decision** — `os.open` at line 792; the authoritative decision is at
   line 841, i.e. after. The earlier `check_target_safe(device)` at line 770 can only *refuse earlier*;
   it is never the authorising answer (probed: `test_verdict_is_invariant_under_the_name_when_the_fd_is_dangerous`).
2. **Identity from `fstat` on the held descriptor** — `fd_block_identity` (676–689) is `os.fstat` +
   `S_ISBLK` + `(major, minor)`, and is the only source of `opened_as`.
3. **Topology closure evaluated against that identity** — `_BoundTopology` (713–737): `devnum_for_path`
   (729–730) returns `self._devnum` and **ignores its `path` argument**; `__getattr__` delegates only
   root-side readers. `check_target_safe` (569–625) makes exactly one target-side call — line 574 —
   so the substitution is total, not partial. Instrumented: `_BoundTopology` performs no filesystem
   lookup for the target (`test_the_bound_view_answers_for_every_path_it_is_handed`).
4. **No pathname/symlink/device-node substitution can authorise a different object** — the only
   post-open pathname use, `named_as` (826–833), is compared and can only refuse; nothing consumes its
   value as consent. Probes: `test_symlink_swap_between_the_two_lookups_is_caught`,
   `test_reverse_swap_still_refuses…`, `test_name_vanishing_after_open…`,
   `test_name_flipping_back_before_the_final_validation_cannot_authorise_the_fd` (this one is the
   decisive ordering test — see §4).
5. **The child gets that exact fd and writes through it** — `writing = f"/proc/self/fd/{target_fd}"`,
   `subprocess.call(argv, close_fds=True, pass_fds=(target_fd,))` (856–860). `argv` never contains
   `of=/dev/...` (asserted in `test_safe_fd_and_safe_name_writes_through_the_descriptor`,
   `test_no_pathname_write` helpers).
6. **No failure path falls back to pathname authorisation** — open failure (793–796), fd < 3
   (797–803), non-writable (805–808), unidentifiable (809–813), procfs unusable (814–818), identity
   mismatch (826–833), bound refusal (841–844), drift (845–849), launch `OSError` (861–865): every one
   prints a reason plus `nothing was written` and returns. Probes cover each.
7. **Re-read/validation failures refuse** — final `fd_block_identity(target_fd) != opened_as` (845);
   drift between the decision and the launch is caught (`test_fstat_drift_between_validation_and_launch_is_caught`).
8. **fd cleanup on every exit path** — single `finally: os.close(target_fd)` (866–870) wrapping the
   whole post-open block; the fd<3 refusal deliberately precedes it so a std stream is never closed
   (probed with `os.close` instrumentation on 4 exit paths + a broken-`close` variant).

## 3. Challenge suite written independently (requirement 4) — `reviewer-probes.py`

50 cases, author's harness **not** reused: only two seams (`os.open`, `os.fstat`) plus the same
`subprocess.call`/`shutil.which`/`Topology` seams production needs, a `SyntheticSysfs` shaped like
`/sys/dev/block` (production's `SYS_BLOCK`, line 133), and the target object being an ordinary
temporary file. Scenario coverage for the fifteen required challenges: path replacement before open
(`test_safe_fd_with_dangerous_name…`), after open (`test_name_vanishing…`), symlink swap between
checks (`test_symlink_swap_between_the_two_lookups_is_caught`, reverse), root-device descriptor with
the name pointing elsewhere (`test_dangerous_fd_with_benign_name_is_refused`,
`test_name_flipping_back…`), partition/sibling of the root disk
(`test_each_dangerous_identity_is_refused_by_name`, `test_unsafe_identities…`), dm/md holders and
partial enumeration (`test_unreadable_sysfs_entries_in_the_fixture_refuse`,
`test_partial_enumeration_is_refused_not_silently_shortened`, `test_unknown_relationships_refuse`),
invalid/closed/wrong/non-writable fds (`TestUnusableDescriptorRefuses`), `fstat` failure and bad
`st_mode`/`st_rdev` (`test_fstat_failure_refuses`, `test_non_block_st_mode_on_the_descriptor_refuses`),
major:minor mismatch (`test_dangerous_fd_with_matching_name…`), launch failure and inheritance failure
(`test_launch_failure_refuses`, `test_dd_missing_refuses`), `/proc/self/fd/N` with the **real** child
(`TestRealChildSemantics`, four cases against `/bin/dd` writing only to temporary regular files),
refusal paths proving the destructive child is never invoked (`assert_never_launched` asserts
zero launches **and** zero bytes at the object), and a valid-write control
(`test_safe_fd_and_safe_name_writes_through_the_descriptor`).

The specific hazard I hunted — **a helper that silently re-resolves the pathname** — does not exist in
the post-open path. `fd_child_target_usable` touches `/proc/self/fd/N` only; `Topology`'s target-side
reader is the one call the bound class overrides. It does exist on the **root** side (§4 finding F-8).

Discrimination (a green suite is not proof): the same 50 cases run against the **unmodified** base
`tools/pc5ctl.py` → `FAILED (failures=12, errors=16)`; 28 of 50 discriminate. The 16 errors are
`AttributeError` on helpers that do not exist at base (expected); the 12 failures are behavioural,
including the decisive one:

> with the descriptor holding a root-disk partition and the *name* going benign → agreeing → benign,
> the base **launches** `dd` (`AssertionError: Lists differ: [['dd', 'if=…', 'of=/proc/self/fd/28', …]] != []`)
> while the patched build refuses with `refusing to overwrite …: /dev/pc5-review-synthetic is a
> partition of the disk carrying '/'`. Log: `logs/probes-{patched,pristine}.txt`.

## 4. Test and gate reproduction (requirement 5)

| Claim | Reproduced in the independent clone |
|---|---|
| 91-test USB suite green | **`Ran 91 tests … OK`** (56 safety + 27 identity + 8 cli) |
| 91 tests fail on pristine base | **`Ran 91 … FAILED (failures=13, errors=1)`** — case-for-case the implementation's list (test_01–04, 09, 12, 14, 16, 16b, 17, 18, 19, 25 fail; test_13 errors) |
| 9/9 mutation harness | **`RESULT: PASS`, exit 0**, baseline `Ran 91 OK`; per-mutant detail in §5 |
| prior 87-test suite | **`Ran 87 … OK`** with the prior (v5-archive) test files + the current patched production |
| prior 8-mutant harness | **`RESULT: PASS`, exit 0** on the same assembly; log body is byte-identical to `PRIOR-MUTATIONS-vs-CURRENT-implementation.log` after stripping the repository line and timings |
| `make check` exit 0 | **reproduced, with two prerequisites the record does not state** — see F-1 |

`make check` in the clean clone at base+patch, after `pip install --user 'unicorn==2.1.4'` and
`git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'`:
**exit 0**, `Ran 924 tests … OK (skipped=2)`, `PASSED: 165 checks, 0 failures`,
`PSM conformance: 261/261`, `DOCS OK: 57 Markdown files, 33 make targets`,
`CITATIONS OK: 258 commit reference(s) resolve across 11 documents`, `PRESENTATION OK`, `BRAND OK`.
The only skip reasons are artifact-gated: `build/efi.img is not built`, `build/pc5.iso not built`.
On the remote tip + patch: **exit 0**, `Ran 989 tests … OK (skipped=2)`, same gate lines.

## 5. Mutation sensitivity, per mutant (not just "caught")

m1→`test_09` (1 of 2 intended); m2→`test_01`,`test_03`,`test_16`,`test_25` (+`test_16b`);
m3→`test_01`,`test_16` (2 of 3; `test_16b` silent; +`test_15`,`test_25`);
m4→`test_14`; m5→`test_05`,`test_06` (+`test_10`,`test_6`);
m6→`test_22`,`test_device_disappearing_during_enumeration_is_unknown` (2 of 3);
m7→**only** `test_unreadable_slaves_is_unknown_not_empty` (1 of 3); m8→`test_17`,`test_02`; m9→`test_23`.

Every mutant is caught by at least one intended `AssertionError`, and `ERROR` never counts as detection.
Four listed "intended" tests are insensitive (F-5), and m7's single detector is unit-level (F-6): under
m7 the CLI-level tests still refuse because `partitions_of` independently probes `slaves/partition` and
returns `UNKNOWN`. I verified that mechanism by applying m7 by hand and running the classes directly —
the redundancy is real, but it is the kind that disappears if `partitions_of` is ever "simplified".

## 6. Evidence package audit (requirement 6)

`logs/evidence-audit.txt` (script: `audit_evidence.py`).

* `HASHES.txt`: 267 lines, **217** well-formed `<path> <bytes> <sha256>` records →
  **216 verified, 0 missing, 1 size-drift, 1 hash-drift** (the same line, the file's own record).
  No file under `evidence/usb-descriptor-remediation-v1/` lacks a record.
* `REMEDIATION.patch`: size, sha256, sidecar, `--check`, application, and byte equality — all confirm.
* v3 continuity archive: `927,493 B`, sha256 `ea6329e4e4b8287317d126f59d62f82f3aaa7cd6ca9abd4b73c662fbe700eb41`,
  sidecar agrees; manifest `member_count: 193` and 193 member entries; the tar holds **194 regular files**
  = the 193 listed **+ the manifest itself**; every one of the 193 member hashes and sizes verified
  against the extracted bytes (0 absent, 0 drift); the archived patch is the same `19793a83…` / 55,331 B
  and, applied to a fresh clone of the base, produces **`Ran 91 tests … OK`** — the round trip closes.
* Three archived copies differ from the live workspace copies (`HASHES.txt`, `MAKE-CHECK.log`,
  `REMOTE-CONTINUITY.md`): disclosed by `HASHES.txt` line 25 as the archive predating the final gate,
  and measured as such — the archived `MAKE-CHECK.log` is a *different, also-green* run
  (`Ran 924 … OK` at 76.930 s vs 75.881 s), which is a strength, not a concealment.
* Prose claims checked for overreach: `PROJECT_STATE.md`, `EVIDENCE_INDEX.md`, `HASHES.txt` and
  `REMOTE-CONTINUITY.md` all say nothing was committed, pushed, merged or run on hardware — verified
  true by `git ls-remote`, `gh api` and 0 commits beyond base.

## 7. Findings

| # | Sev | Class | Where | Finding |
|---|---|---|---|---|
| F-1 | **medium** | documentation-provenance | `evidence/usb-descriptor-remediation-v1/MAKE-CHECK.log`, `HASHES.txt:5-8`, `docs/engineering/USB-DESCRIPTOR-REMEDIATION-20261121.md:70` | The final gate is recorded as `exit 0 / DOCS OK: 87 / CITATIONS OK: 258` with no environment prerequisites stated. Reproduced only after (a) `pip install unicorn==2.1.4` — without it the same command yields `FAILED (errors=103)`, all attributable to the missing module; and (b) fetching `refs/pull/*/head`, because `citations-check` resolves historical SHAs that exist only in those merge refs. Without them the gate dies in a **fresh clone of this workspace**: `258 dead commit reference(s) across 11 documents`, `make: *** [Makefile:320: citations-check] Error 1`, exit 2 (`logs/make-check-without-pr-refs-NEGATIVE-CONTROL.txt`). Expected: a recorded gate that a reviewer can reproduce from the delivered artifacts, or the two prerequisites written into the log header. |
| F-2 | low | documentation-provenance | `HASHES.txt:33` and `:27` | The file records **itself** as 30,519 B / `17b2f47b…` while measuring 30,878 B / `37cf9de4…`, three lines after asserting "it cannot hash itself". A self-record is stale by construction. Expected: no self-record, or the prose corrected. |
| F-3 | low | documentation-provenance | `HASHES.txt:25` vs `recovery/…-v3-manifest.json` `generated` | `HASHES.txt` says the archive was built at `17:16:48Z`; the manifest inside it says `2026-11-21T17:17:26Z`. One number is a transcription; the recorded sizes/hashes all check out, the timestamps do not. Expected: measured values only. |
| F-4 | low | test-quality / portability | `tests/test_pc5ctl_usb_cli_boundaries.py:1-20` | Its sibling import (`import test_pc5ctl_usb_descriptor_identity …`) needs `tests/` on `sys.path`, so `python3 -m unittest tests.test_pc5ctl_usb_cli_boundaries` → `ModuleNotFoundError`; it passes under the repo's loader (`discover -s tests`) and with `PYTHONPATH=tests`. Expected: an import that works under both loaders. |
| F-5 | low | test-quality | `evidence/…/mutation_harness.py` EXPECTED lists | Four intended-detector names are insensitive (m1→`test_11_topology_change…`, m3→`test_16b…`, m6→`test_partial_list_never_permits_a_write`, m7→`test_20`/`test_21`). Detection is unaffected (the harness requires ≥1 intended `AssertionError`), but a reader of the mapping would over-attribute sensitivity. Expected: list only tests that actually discriminate. |
| F-6 | low-medium | test-quality | `tests/test_pc5ctl_usb_safety.py:596-616` vs `…:581-595` | For "unreadable relationship ⇒ empty set", the **only** failing test under m7 is the unit-level `assertNotEqual(topo.slaves(dm0), [])`; every CLI-level refusal in that fixture survives m7 because of a second, unrelated guard (`partitions_of` probes `slaves/partition`). Expected: at least one end-to-end case whose *only* protection is the `_relations` UNKNOWN path. |
| F-7 | informational | safety (measured, not exploitable) | `tools/pc5ctl.py:692-710` | `fd_child_target_usable` checks existence and the ` (deleted)` suffix but never checks that the alias's referent is the object it opened. I probed it (a spoofed `readlink` pointing at a decoy): the write still goes to the held descriptor, so this cannot redirect a write — the check is advisory. Expected: stated as such in the comment, which currently implies verification of "the referent". |
| F-8 | low | safety, pre-existing, out of the patch's stated scope | `tools/pc5ctl.py:393-409` (`root_devnum` fallback), `:574` | The **root** side of the decision is devnum-based only when `/proc/self/mountinfo` is readable; the fallback resolves the `/proc/mounts` *source name* through `devnum_for_path`. Probed: with mountinfo absent and the mount source retargeted, the protected set follows the name (`test_a_retargeted_root_node_would_move_the_boundary` passes, i.e. the residual is real), though an unresolvable source fails closed (`test_unresolved_root_refuses_all_targets`). The design doc lists topology-volatility residuals but does not name this path-based root lookup. Expected: named residual, or prefer `st_dev`-independent sources only. |
| F-9 | informational | operational | `tools/pc5ctl.py:574-625` reached from `:770`/`:841` | An exception raised out of a `Topology` method escapes `cmd_usb` as a traceback rather than the `nothing was written` refusal (measured with injected `EACCES` on `root_closure`/`parent_of`/`partition_of`/`_relations`). It fails closed — nothing is launched, nothing is written, and the exit status is still non-zero — so this is a reporting gap only; the real readers (`_read_text`, `_probe`, `listdir`) all swallow `OSError`, so it is not reachable through file permissions. |
| F-10 | informational | documentation | `recovery/…-v3-manifest.json` `note` | "An archive cannot contain … this manifest" — it does contain the manifest (194th file), which is why its own sha256 is unattested inside it. Harmless, but the wording should match the measurement. |

## 8. The identity question, answered unequivocally

**Yes.** Within the documented limits of userspace topology observation, the identity that authorises
a write necessarily refers to the same open object that receives it. `check_target_safe` asks exactly one
question about the target — `topology.devnum_for_path(device)` at `tools/pc5ctl.py:574` — and the object
passed to it during the authoritative pass is `_BoundTopology`, whose `devnum_for_path`
(`:729-730`) ignores its argument and returns the `(major, minor)` that `fstat` reported for the
descriptor being held. The child is then handed that same file description (`pass_fds=(target_fd,)`,
`:860`) and writes to `/proc/self/fd/<fd>` (`:856`), which the kernel resolves to the description, not to
a name. Unlinking, re-pointing or replacing the pathname cannot make the guard consent to writing
something it did not examine: it can only refuse (`:826-833`). I verified this three ways — source trace,
a 50-case probe suite that fails 28 of those cases against the base, and hand-applied mutants.

What the identity does **not** pin, and no userspace code can: what that `major:minor` *means* a moment
later. Device-mapper/md table reloads, partition-table rewrites, a replug that reuses a device number,
or a root remount can change the topology after the last check. The patch shrinks that window (the
decision is recomputed at `:841` and the identity re-read at `:845`) without closing it, and the design
document says so. F-8 adds one residual of the same class on the *root* side.

## 8b. What this review wrote

Nothing was deleted, regenerated or silently replaced. 18 new files under
`evidence/usb-descriptor-independent-review-v1/` (this report, `STATE.txt`, `COMMIT-HANDOFF.md`,
`HASHES.txt`, `reviewer-probes.py`, `audit_evidence.py`, `logs/`), plus one append-only edit: a
"Review addendum" section (184 → 226 lines) in `docs/engineering/USB-DESCRIPTOR-REMEDIATION-20261121.md`,
whose §7 claimed the work was "unreviewed by anyone else". `make docs-check` (exit 0,
`DOCS OK: 89 Markdown files, 33 make targets`) and `tests.test_architecture` (`Ran 53 … OK`) both run
clean afterwards, and the delivered `evidence/usb-descriptor-remediation-v1/` files still re-hash to
their own recorded values (`STATE.txt`, spot-check list) — except `HASHES.txt`, whose stale self-record
is finding F-2 and was that way before this review began.

## 9. Commit-readiness, GitHub presence, device validation

* **GitHub presence:** `refs/heads/main` = `56c5852…` (identical to local HEAD); remote
  `refs/heads/arena/c8a76776-pc5` = `ae9f3e25f66ac3d31b2b7b64dccef7695fef1d73`, which
  `gh api compare` reports as **5 ahead, 0 behind** the local checkout. So the remediation is **not on
  GitHub** — no commit, no push, nothing merged, as the records claim. Because those 5 commits touch
  neither file the patch modifies, the patch is equally valid at the tip (§1).
* **Commit-ready?** The *code* is: minimal, boundary-correct under attack, mutation-tested, and green
  on the tracked 924/989-test gates and on `make check` given F-1's two prerequisites. What is not ready
  is the *package*: F-1 through F-3 should be corrected (or explicitly accepted) in the record, and an
  authorized committer must decide whether the commit lands on `56c5852` (the verified base) or on
  `ae9f3e25` (the pushed tip) — a commit made on the current working state cannot be pushed to the
  branch without reconciling those 5 commits first.
* **Physical-device validation: none.** Everything in this review ran against synthetic sysfs fixtures,
  ordinary temporary regular files and `/bin/dd` pointed at a temporary file. No block device was
  created, opened or written; no `mknod`; no OVMF, firmware or hardware run. Nothing here constitutes
  USB or firmware acceptance evidence, and PR31 (`open`, `ac17c5d3`, base `46a9fda1…`) and PR34
  (`open`, `547ebd5c…`) are unchanged and are not approved by this result.
* **Reviewer actions on the repository:** 0 commits, 0 pushes, 0 PR comments/updates, no merges,
  no rebase; no existing evidence file was modified, deleted or regenerated (this review's own
  artifacts are new files under `evidence/usb-descriptor-independent-review-v1/`). Two read-only
  local-only side effects, disclosed: `git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'`
  and `git fetch origin ae9f3e25…` (objects and remote-tracking refs in `.git`, needed for F-1's
  diagnosis), plus `pip install --user unicorn==2.1.4` in the sandbox.

## 10. What would move this to PASS

1. Amend `MAKE-CHECK.log`/`HASHES.txt`/the design doc with F-1's two prerequisites and the clone-vs-workspace
   `DOCS OK` count (57 tracked-only vs 87 with the untracked working documents), or record the gate as
   "workspace-only" — this is the only finding that materially misleads a reviewer.
2. Drop or correct the self-record (F-2) and the archive timestamp (F-3); prefer re-measured values.
3. F-4's import fix, F-5's mapping trim, and one end-to-end test that dies if the `_relations`
   UNKNOWN path is weakened (F-6). F-7/F-9/F-10 are wording-level. F-8 needs a sentence in the design
   document's residual list, since it is not fixable by descriptor binding.

Then this is ready for an authorized commit to `arena/c8a76776-pc5` — and USB deployment stays blocked
independently of that commit until physical-device validation exists.
