# Prepare the PR body for the USB descriptor remediation

Drafted `2026-11-21T19:32:12Z`, revised 2026-11-21T19:51:01Z after the F-4…F-10 disposition pass —
see `FINDINGS-CLOSURE-20261121.md` and `FINDINGS-DISPOSITION-20261121.md` for the
measured figures this text quotes. The text below the rule is the body submitted to the
pull request opened from `fix/usb-descriptor-bound-remediation`; the PR number, its URL,
the head SHA it carries and the identity of the diff GitHub serves for it are recorded in
`logs/30-pr-opened.txt`, added by the follow-up commit on this branch (a body cannot
contain the number of the PR it creates).

Authorisation for commit/push/draft-PR and for a conditional merge is the session
directive of 2026-11-21; no approval is claimed here, and the merge conditions are
restated at the end of this body.

---

## USB: bind the target decision to a descriptor, not to a pathname

**Base: `56c58521580e88c4339ceb6eb40ce724a2bb533b` (`main` tip).** Chosen by
measurement, not convenience: `git merge-base --is-ancestor` shows `main` is an
ancestor of the session checkpoint `ae9f3e25f66ac3d31b2b7b64dccef7695fef1d73`, which is
5 commits ahead and 0 behind, and all five are M2a kernel work (xAPIC bring-up, IDT
exception capture, PIT-calibrated TSC, MADT CPU enumeration, a roadmap note). Basing on
the checkpoint would put unreviewed kernel changes into a host-tool PR. The patch
applies with no fuzz at both bases and produces byte-identical files at both, so the
choice is diff hygiene, not applicability.

**Scope discipline.** PR #31 (head `ac17c5d3afa6fa3ca779c012c6aef36b64751b55`, base
`46a9fda1`) and PR #34 (head `547ebd5c829e2221f82f66d1967d90a773a0dea4`) are untouched:
no comment, no update, no merge, no approval implied by this change. No deployment is
claimed or implied — see the last section.

### What changed, and why

`python3 tools/pc5ctl.py usb --iso build/pc5.iso --device /dev/sdX [--yes]` writes the
boot image to a block device from the host. Three defects in that path were reported,
reproduced on the shipped source, and are corrected here:

1. **The safety decision followed a pathname.** The root-storage checks resolved the
   *name* the operator typed, so the object validated and the object written could be
   different devices.
2. **The child inherited nothing.** `dd` was launched with the pathname as `of=`, so
   even a correct parent-side check said nothing about what the child would open.
3. **Unknown topology was treated as empty.** A `/sys` relationship that could not be
   read yielded no protected devices, i.e. an unreadable directory was a licence to
   erase.

The correction is contained in `tools/pc5ctl.py`: `fd_writable`, `fd_block_identity`,
`fd_child_target_usable` and `_BoundTopology`, plus the rewritten `cmd_usb`. Once the
target is open, the *file description* is the subject of every subsequent decision: its
`fstat` yields `major:minor`, the whole topology comparison runs against that identity,
`_BoundTopology.devnum_for_path` ignores the pathname it is handed so a substitution
cannot re-enter, the identity is re-read immediately before the child is launched, and
`dd` receives the same descriptor via `pass_fds` and is addressed as
`/proc/self/fd/N`. The name is consulted only to refuse.

Fail-closed on every path: open failure, unwritable descriptor, unreadable `/proc`,
`fstat` error, changed identity, unknown or partial topology, unlaunchable child — each
prints `nothing was written` and exits non-zero, with no fallback to the pathname,
because falling back is how a validated descriptor turns back into an unvalidated name.
`--yes` never overrides a refusal.

### Security boundary, stated plainly

* Whole disk carrying `/`, the partition that holds `/`, **sibling partitions** on that
  disk, and the **dm/md/LVM closure** of the root (slaves and holders, transitively) are
  refused by device number, so a member disk of the same RAID or the physical volume
  under a logical volume cannot be selected as "unrelated".
* `/dev/root` and similar aliases are resolved through the descriptor, not the name.
* **What no userspace code can do:** make the topology atomic. A device-mapper reload,
  an md add/remove, a partition-table rewrite, a `pivot_root`, or a replug that reuses a
  `major:minor` can change what the same identity *means* after the last check. The
  decision is recomputed immediately before launching the child, which shortens that
  window to microseconds without closing it. Do not write media from a system whose own
  storage could be rearranged underneath you.
* Residual: when `/proc/self/mountinfo` is unavailable, the root side still falls back to
  resolving `/proc/mounts` source **names** (pre-existing, unchanged by this PR).
* Residual: an exception raised by a *post-decision* topology read surfaces as a
  traceback rather than a clean refusal; no write occurs on that path either way.

### Tests, and what they actually cover

| Evidence | Result |
|---|---|
| `python3 -m unittest discover -s tests -p 'test_pc5ctl_usb*.py'` | `Ran 95 … OK` — 56 safety, 27 descriptor-identity, 8 CLI-boundary, 4 unreadable-topology end-to-end |
| `evidence/usb-descriptor-remediation-v1/mutation_harness.py` | `RESULT: PASS: baseline green and every mutation detected by its intended assertion` — 9/9 |
| prior pass's suite (recovered from `recovery/pc5-identity-boundary-20261121-v5.tar.gz`) against this production | `Ran 87 … OK` — independently authored tests, one implementation |
| prior 8-mutant harness against that 87-test inventory | `RESULT: PASS`, all 8 detected |
| prior 8-mutant harness against *this* 91-test inventory | `RESULT: PROBLEM: [m1, m3, m5, m6, m8]` — every mutant still produces 1–6 assertion failures; the harness's *named* expectations are stale against the renamed tests, reported rather than quietly re-labelled |
| `make check` in a clean clone of `main`, this patch applied, nothing installed, nothing fetched | exit 0, `Ran 925 … OK (skipped=109)` |
| same clone after the documented venv + `make citation-refs` | exit 0, `Ran 925 … OK (skipped=2)` |

**Synthetic by design.** The tests use temporary regular files and a synthetic
`/sys/dev/block` tree; `mknod` is not permitted in the environment and no device node is
created or written. They prove the guard's decisions and call sequence are what the code
claims — including a real `/bin/dd` writing through a real inherited descriptor into a
temp file. They are not evidence about hardware.

### Reproducibility work in this PR (finding F-1)

`make check` had an undocumented dependency; now: `requirements-harness.txt` pins
`unicorn==2.1.4`; the two emulator-backed suites skip with a stated reason instead of
erroring, with the suite total identical either way; `make citation-refs` +
`tools/citation_refs.sh` (new, 34th target) provisions `refs/pull/*/head` for
`citations-check`; and `tools/commit_citations.py` distinguishes "citation is dead" from
"this clone cannot tell" (truncated history, or PR refs never fetched) without converting
either into a pass. The harness fix was found by running in a clean clone: four tests
failed there because `tests/test_presentation.py`'s synthetic trees did not know about a
newly documented root-level file — hence the one-line fixture additions here.

### Review findings and their resolution

The disposition of every remaining finding, with the command that reproduces it and
whether it blocks merge, is in `evidence/usb-descriptor-remediation-v2/FINDINGS-DISPOSITION-20261121.md`:
F-4 (loader portability), F-5 (stale intended-detector names, replaced by a measured
harness), F-6 (an end-to-end test that dies if the unreadable-relationship answer is
weakened — `tests/test_pc5ctl_usb_topology_unknown.py`, `Ran 4 … FAILED (failures=2)` under
`m7`), F-7 (docstring says the alias check is advisory) and F-10 (manifest scope) are closed;
F-8 and F-9 stay open with mechanisms and candidate fixes named, and neither leaves a
production path unprotected.

An independent adversarial review (`evidence/usb-descriptor-independent-review-v1/`)
returned `CONDITIONAL` with findings F-1…F-10. F-1, F-2 and F-3 are addressed by this PR;
F-4…F-10 remain open and are listed with their statuses in
`evidence/usb-descriptor-remediation-v2/FINDINGS-CLOSURE-20261121.md`. No boundary
property was found to be unsafe; the findings were about reproducibility, provenance and
test hygiene.

The review's own record is corrected in the open, because two of its claims do not
survive measurement:

* "A clean clone needs `refs/pull/*/head` for `make check`" — **false**: a full clone of
  `main` resolves 258/258 citations with three refs and no PR refs. The real condition
  is a *truncated* history (`--depth 1`, partial clone, CI `fetch-depth: 1`), which is
  now detected and named by the tool.
* `logs/make-check-base-clone-run1.txt` in the review package claims exit 0 over a body
  copied from a run that failed; the clean run is `logs/make-check-base-clone.txt`. Both
  files are left as they are, with `logs/make-check-base-clone-run1.txt.CORRIGENDUM.txt`
  beside them.

### Evidence provenance (findings F-2, F-3)

`evidence/usb-descriptor-remediation-v1/` is delivered **unmodified**, including the
`HASHES.txt` whose self-record is the subject of F-2. The corrections are additive, in
`evidence/usb-descriptor-remediation-v2/`:

* `CORRECTED-MANIFEST.txt` — 50 records, zero self-records, scope stated, the v1
  manifest's true identity recorded (`30,878 B` / `37cf9de4482319e9…` against the stale
  `30,519 B` / `17b2f47b…` it claims for itself), drift reported rather than repaired.
* `TIMESTAMP-RECONCILIATION.md` — separates archive creation, manifest generation and
  verification: the `17:16:48Z` in `HASHES.txt:24-25` is a pre-archive `date -u` reading
  mislabelled as a build time (the manifest inside the tar declares `17:17:26Z`, and the
  tar's member entry for it is stamped `17:17`, so the tar is at or after that); no
  timestamp was invented or backdated; filesystem mtimes in this environment are
  sandbox-normalised and cannot support any ordering claim; and all three manifests set
  `archive` to the *manifest's* filename instead of the `.tar.gz`, disclosed as found.

### README and docs

`README.md` gains the section *Writing the image to a USB device*: what the tool is for,
the protection rules (root disk, partition, sibling partitions, dm/md closure),
descriptor-bound identity and child inheritance, fail-closed behaviour, the userspace
limits that remain, which automated tests exist and what they cover, and that physical
device validation has **not** happened. `docs/INSTALLATION.md` §2/§8 and
`docs/BUILDING.md` (targets, the pinned module, `citation-refs`) are updated to agree
with the code. `make docs-check` → `DOCS OK: 92 Markdown files, 34 make targets`;
`make presentation-check` → `PRESENTATION OK`.

### One document deliberately not carried

The implementation's running record for this pass,
`docs/engineering/USB-DESCRIPTOR-REMEDIATION-20261121.md` (§1–§9, 21,959 B,
`1e890ef1e7b2f62b…`), is **not** in this diff. Its §8 links
`evidence/continuation-descriptor-v1/PR31-D1-V2-RECOVERY-AND-REVIEW-20261121.md`, a
PR31-scoped review record; landing it here would either drag that file's contents into
this PR or leave `make docs-check` failing on a dead link. Since `docs_check.py`
validates every Markdown link in the repository, the choice was made to carry only the
design document (`docs/engineering/USB-IDENTITY-BOUNDARY-DESIGN-v1.md`, which the
README links and which has no outbound links) and to leave the dated record in the
workspace and the continuity archives, where its links resolve. A reviewer who wants it
in-tree should say so; the honest way is to land the PR31 record first, or to convert
that one reference into a plain-text path mention, and neither is a change to make
silently.

### What is not claimed, and what blocks deployment

No real disk, USB stick, firmware or OVMF boot was used, here or in the prior passes. No
physical-device validation exists. Emulation, synthetic sysfs fixtures and 91 green
tests are evidence about the guard's logic, not about hardware safety, and nothing in
this PR should be read as approving media writes on a real machine.

**USB deployment remains BLOCKED**, pending (a) physical-device validation on real media
and real firmware, and (b) explicit authorisation to commit, push and merge. Reviewing
and approving this PR would be neither.

## Diff identity

This branch carries no committed copy of its own diff: a patch file inside the commit it
describes would have to contain itself, which is the defect finding F-2 was about. The
authoritative delta is `git diff 56c58521580e88c4339ceb6eb40ce724a2bb533b..<head>` and the
identical bytes GitHub serves as `gh pr diff <n> > USB-PR-<n>.patch`; that file's size and
sha256, and its comparison against the pre-review `USB-REMEDIATION-v2.patch`
(100,588 B, `e1dae862d0d01af8…`, which stays in this directory as the artifact the review
was performed on), are in `logs/30-pr-opened.txt`. The three files this branch adds to the
reviewed content are `tools/pc5ctl.py`'s nine-line docstring (proved docstring-only in
`logs/28-pc5ctl-delta-is-docstring-only.txt`), `tests/test_pc5ctl_usb_cli_boundaries.py`'s
loader bootstrap, and this closure package (30 logs + 2 harnesses + 3 measurement
records).

*Recorded 2026-11-21T19:51:01Z; no device, no firmware, no VM was touched.*
