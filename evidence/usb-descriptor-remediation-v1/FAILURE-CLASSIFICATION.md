# Failure classification — individual, with controlled reproductions

Two inventories are classified here. Nothing is dismissed collectively: every item has a named
cause and, where the cause is not in the product, a reproduction that flips the result.

## A. The new tests against the unmodified shipped source — 13 failures + 1 error

`USB-SUITE-pristine.log` (and independently reproduced in a real clone of
`56c58521580e88c4339ceb6eb40ce724a2bb533b`, `BASELINE-REAL-CLONE.log`, same numbers: the export
method is not doing any work here). These are the intended teeth: the same 91 test files, production
source swapped from "shipped" to "remediated", 13F+1E → `OK`.

Every item below is one of the 14, with the *specific* shipped behaviour that makes it fail — not
"a test failed, therefore the fix is good". Causes are grouped by defect: **1** = the child never
receives the descriptor; **2** = the decision is not bound to the descriptor; **guards** = the
interrogations that a descriptor-bound write requires and the shipped code does not perform.

| Test | Cause | Shipped behaviour that makes it fail |
|---|---|---|
| `test_25_real_dd_writes_through_the_inherited_descriptor` | 1 | the real `/bin/dd` child cannot open `/proc/self/fd/N`, so the image never arrives (exit 1, zero bytes) |
| `test_01_valid_descriptor_and_matching_identity_writes_through_the_fd` | 1 | the launch carries neither `pass_fds` nor the explicit `close_fds=True` |
| `test_03_missing_descriptor_inheritance_is_explicit` | 1 | `pass_fds` is absent from the call entirely |
| `test_16_child_receives_exactly_the_validated_descriptor` | 1 | no inheritance set exists to name the validated descriptor |
| `test_16b_inherited_descriptor_number_is_the_closed_one` | 1 | same, asserted from the other side: the inherited number must be the open one |
| `ERROR: test_13_child_startup_failure_never_falls_back_to_the_pathname` | 1 | a launch `OSError` escapes `cmd_usb` as a traceback: the shipped code has no handler, so the "refusal" is a crash rather than a contract |
| `test_09_path_replaced_between_the_identity_check_and_final_validation` | **2** | **the write is launched**: the final decision re-resolves the name (stick) while the descriptor holds a partition of the root disk — the fail-open |
| `test_14_final_validation_failure_immediately_before_writing` | 2 | the descriptor is not re-read after the decision, so an object that changed underneath is written |
| `test_02_descriptor_closed_before_the_child_launches` | guards | nothing interrogates the fd before use, so a dead descriptor still reaches the launch |
| `test_04_invalid_descriptor_number_is_refused_without_closing_a_standard_stream` | guards | no `fd < 3` guard: a std stream would be handed to `pass_fds` (which rejects it) or closed by the refusal path |
| `test_17_descriptor_not_open_for_write_is_refused` | guards | the writability of the open file description is never asked |
| `test_18_missing_procfs_entry_cannot_fall_back_to_the_pathname` | guards | procfs usability is unchecked; the child is launched regardless |
| `test_19_unlinked_referent_is_refused` | guards | an unlinked referent (`(deleted)`) is not detected |
| `test_12_failed_descriptor_read` | 2 (semantics) | `fstat` failure is collapsed into "is no longer a block device" instead of an identity refusal: right direction, wrong statement — the operator is told something about the device that was never established |

**What passes on both builds, stated so it cannot be mistaken for evidence.** The other 13
identity/CLI cases (`test_05`, `test_06`, `test_07`, `test_08`, `test_10`, `test_11`, `test_15`,
`test_20`–`test_23`, and all 8 CLI-boundary cases) and all 56 retained safety cases pass against
the shipped source too. `test_08` and `test_10` — the other two substitution orderings — are caught
shipped only by its incidental name-versus-descriptor comparison, which is why the mutation harness
includes `m5` (drop that comparison) and why they fail there. They are retained as guarantees that
no ordering can authorise a dangerous held object, not as proof that the shipped build was unsafe.

## B. The repository suite in differently prepared environments

| # | Preparation | Result | Individual cause and reproduction |
|---|---|---|---|
| 1 | `git archive HEAD \| tar -x`, tests run in the export | `Ran 889 … FAILED (failures=4, errors=6)` | Invalid method, not a state of the repository: `git archive` honours `export-ignore` (files the tests need are absent) and a tar export has no `.git`, so `test_commit_citations` legitimately refuses ("not a Git repository, so no commit reference can be resolved"). Reproduced by method alone: the same tree as a clone is green. |
| 2 | real clone rooted at `/tmp/tmp.*/clone`, `TMPDIR` = the clone | `Ran 889 … FAILED (failures=2)` | (a) `test_scratch_isolation.test_the_root_is_outside_the_repository` — my placement put the *repository root* inside a temp dir; the guard is asserting exactly this. (b) `test_commit_citations.test_a_non_repository_exits_unchecked` — `TMPDIR` was *inside* the clone, so the "non-repository" fixture had a `.git` above it and the guard correctly found a repository. Both flip to pass with the clone outside `/tmp` and `TMPDIR=/tmp` (row 4). Same two traps recorded in a previous session; re-derived, not inherited. |
| 3 | real clone at `/home/user/baseline-clone`, `TMPDIR=/home/user/baseline-tmp` (which I never created) | `Ran 889 … FAILED (failures=1)` | `test_release.PublishScriptTests.test_publishes_three_assets_and_is_idempotent`: `mktemp: failed to create file via template '/home/user/baseline-tmp/tmp.XXXXXXXXXX': No such file or directory` — the publish script could not make a temp dir because my environment named a non-existent one. Harness bug, reproduced by the error text. |
| 4 | real clone at `/home/user/baseline-clone`, `TMPDIR=/tmp` | `Ran 889 … OK (skipped=2)`, exit 0 | **The correctly prepared tracked baseline.** |
| 5 | this working tree, `make check` | `Ran 924 … OK (skipped=2)`, `DOCS OK: 87 Markdown files` (73 in the earlier run of this row, before the recovered records landed in `evidence/`), `CITATIONS OK: 258 commit references across 11 documents`, kernel unit `PASSED: 165 checks, 0 failures`, exit 0 | 924 = 889 tracked + 27 identity + 8 CLI-boundary, exactly; no test removed, no count conflated. |

## C. What is *not* in either list

No failure was attributed to "the sandbox" without a reproduction, and no passing result is
claimed to be a hardware result:

* the 2 skips are the pre-existing conditional skips in the tracked suite (unchanged by this work);
* `unicorn==2.1.4` was installed into the sandbox user site because the emulator-backed tests import
  it; without it the suite reports 103 `ModuleNotFoundError` errors, which is a missing documented
  prerequisite and not a product state (recorded so nobody re-runs the suite to "discover" it);
* `which` is present in this image, so the `setUpClass` failures seen in an earlier environment did
  not recur; had they, they would have been listed here individually;
* permission-denied fixtures (`chmod 000`) depend on the suite **not** running as root: the retained
  safety file contains its own self-check for that (`test_unreadable_fixture_actually_denies_reads`),
  which passed, so the unreadable-sysfs refusals in this run are real `EACCES` semantics rather than
  a privilege artifact.
