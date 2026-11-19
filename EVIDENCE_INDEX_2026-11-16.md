# Evidence index — PR #26 / PR #27 stack (2026-11-16)

**Author:** the PC5 implementer. **Nothing in this index is independent review.** It exists so
that a genuinely independent reviewer can find every artifact, know what each one is worth, and
see which revision it describes, without having to trust a summary.

Committed in this follow-up at the revision that contains it. **Tested revision** for the
follow-up work: the commit that adds this file; the parent it applies to is
`0a7eaa07222df5b6182050933c650e8b74efde94`. Every SHA below was verified with
`git ls-remote` and `gh pr view` before being written here.

---

## 1. Documents in this repository

| # | Document | Purpose | Tested SHA | Evidence type | Historical? |
|---|---|---|---|---|---|
| 1 | `AUDIT_PR26_PR27_2026-11-16.md` | The pre-merge audit. Original verdict REQUEST CHANGES / two critical findings (D1, D2). §11 records remediation; §12 is a dated forward pointer to the L1 fix. | `d0be617` (audited), §11–§12 reference `ed9c6a3`/`ec16c65`/`141f91f` | **Author-reported**, re-derived from source | **Yes** — describes a defect state that no longer exists. Deliberately preserved. |
| 2 | `REVIEW_PACKAGE_PR26_PR27_2026-11-16.md` | Reading guide for a human reviewer: what changed, what to look at, what not to sign off. | `141f91f`, with later sections on `0a7eaa0` | **Implementer-assembled** | Partly |
| 3 | `REMEDIATION_REPORT_F1_F5_2026-11-16.md` | What F1–F5 were, how each was closed, the regression table. | `0a7eaa0` | **Implementer-reported**, each result re-derived | Yes — predates the F2 integrity rewrite |
| 4 | `AUDIT_PR26_PR27_INDEPENDENT_2026-11-16.md` | Post-remediation audit of the stack. **Filename retains "INDEPENDENT"; the claim in its title has been withdrawn** — see the note at its head. | `170e611`, `141f91f` | **Author-reproduced** | Yes |
| 5 | `ADVERSARIAL_REVIEW_PR26_PR27_2026-11-16.md` | Adversarial pass that produced findings F1–F5 (including two errors in the author's own earlier work). Title corrected from "Independent". | `141f91f` | **Author-reproduced** | Yes |
| 6 | `FINAL_REVIEW_PR26_PR27_2026-11-16.md` | The review that produced verdict `DO NOT APPROVE — TECHNICAL FINDINGS REMAIN` and drove this follow-up. Self-discloses non-independence at its head. | `0a7eaa0` | **Author-reproduced** | Yes — its findings are what this branch addresses |
| 7 | `LANDING_STRATEGY_D_READINESS_2026-11-16.md` | Landing readiness: Strategy D ordering, blockers, repository settings. | `170e611` | **Implementer-assessed** | Yes |
| 8 | `EVIDENCE_INDEX_2026-11-16.md` | This file. | the follow-up commit | Implementer-assembled | No |
| 9 | `FOLLOWUP_EVIDENCE_F1_F5_2026-11-16.md` | The follow-up: F2 coverage redesign, F1 doc corrections, F4/F5 delivery, cold-start investigation, **and (§9) the shared-scratch-path races**. | follow-up commits; §9 describes `59d83cf` and its successor | **Implementer-reported**, reproduced on demand | No |

**Every one of documents 1–7 was written by the same author as the code it describes.**
A reviewer should read them as a map of what to check, not as a verdict. The repository has
**no CI, no branch protection and no third-party verification**; there are **0 reviews and
0 comments** on both PRs.

## 2. What each evidence type means here

| Label | Meaning |
|---|---|
| **Independently reproduced** | Reproduced from source by someone who did not write it. **No artifact in this repository qualifies.** |
| **Author-reproduced** | Re-run from source by the author in a fresh worktree, with commands and output recorded. Rules out careless or false claims; is not a second opinion. |
| **Implementer-reported** | The author states a result. Where the follow-up re-derived it, the derivation is shown. |
| **Untested** | Not executed in this environment. Listed as a blocker, never as a pass. |
| **Blocked** | Cannot be executed here at all; see §5. |

## 3. Reproduction commands

Each is exact and was run. Worktrees are detached at the named SHA so a reviewer can repeat
them without disturbing their own checkout.

```sh
git fetch origin
git worktree add --detach /tmp/rev 0a7eaa07222df5b6182050933c650e8b74efde94

# Full suite (the numbers quoted in the docs)
cd /tmp/rev && make -s check

# D1: the 4 GiB identity map, walked from PML4 rather than trusted via pc5_pt_lookup
python3 -m unittest tests.test_pagetables64.IdentityHierarchyTests -v

# L1 + F2: the coverage guards and the eight adversarial controls
python3 -m unittest tests.test_pagetables64.ShortSpanCoverageIntegrityTests \
                   tests.test_pagetables64.AdversarialCoverageTests -v

# F1: the stub answers a status instead of raising, and the loader takes its own path
python3 -m unittest tests.test_boot64_stub.PoolExhaustionTests -v

# F5: the headroom figures, from the allocation trace
python3 - <<'PY'
import sys; sys.path.insert(0,'tests'); sys.path.insert(0,'.')
import uefi_stub_firmware as fw, boot64_stub
from test_boot64_stub import artifacts
image, kernel = artifacts()
m = boot64_stub.StubFirmware64(image=image,
    esp_volume={"\\EFI\\BOOT\\BOOTX64.EFI": image, "\\KERNEL64.BIN": kernel},
    kernel_bytes=kernel)
m.run()
pool = fw.POOL_END - fw.POOL_BASE
used = m.pool_next - fw.POOL_BASE
pages = sum(r[1] for r in m.allocations)
print("pool", pool, "loader pages", pages, "loader bytes", pages*4096)
print("total used", used, "loader-only", round(pool/(pages*4096),1),
      "combined", round(pool/used,1))
PY

# Release isolation: pins BOTH inputs; ISO alone is not evidence
E=1700000000; B=141f91f
cd /tmp/rev && rm -rf build && make -s all SOURCE_DATE_EPOCH=$E BUILD_ID=$B \
  && sha256sum build/pc5.iso build/kernel.bin build/ldr.bin 2>/dev/null
```

## 4. Numbers a reviewer should check rather than accept

| Claim | Value | How it was obtained |
|---|---|---|
| Stub pool capacity | 16,777,216 B = 16 MiB = 4096 pages | `POOL_END - POOL_BASE` = `0x03000000 - 0x02000000` |
| Loader demand | 27 pages / 110,592 B over 5 requests | `sum(pages)` over `machine.allocations`, read after a real `run()` |
| Harness scaffolding | 57,344 B (14 pages) | total consumed minus loader requests |
| Total consumed | 167,936 B = 41.00 pages = 1.00 % of pool | `pool_next - POOL_BASE` at completion |
| Loader-only headroom | **151.7×** | 4096 / 27 = 151.70 |
| Combined headroom | **99.9×** | 16,777,216 / 167,936 = 99.9 |

The superseded figure "~35 pages (≈470×)" is both mismeasured and arithmetically impossible:
470 × 35 = 16,450 pages exceeds the pool's entire 4,096-page capacity. It appeared only in
documents that were never committed and is quoted in the correction text solely so the error
stays identifiable.

## 5. Gates that did not run

| Gate | Status | Why |
|---|---|---|
| `make -s boot-test` | **NOT RUN** | `node_modules` is absent; the v86 assets are not installed. Exit 77 / skip. |
| `make -s ovmf-test` | **NOT PASSED** | No OVMF firmware and no QEMU in this environment. 22 required matrix rows, 0 executed. |
| Physical hardware | **UNTESTED** | No machine. |
| Genuine Sony firmware / PS5 system software | **UNTESTED** | Never attempted. No claim is made anywhere in this repository that it has been. |
| GitHub Actions / CI | **DOES NOT EXIST** | `.github/workflows/` is absent; `actions/workflows` returns `total_count: 0`. |
| Branch protection | **ABSENT** | `protected: false` on `main` and both PR branches. |

## 5a. Test scratch isolation (added later the same day)

Three paths that should have been private were shared. All three are fixed:

| Shared path | Was | Now |
|---|---|---|
| `build/test/m1i/{in,out}.img`, `lba.txt` | fixed directory and names | per-process root, per-case directory (`tests/scratch.py`) |
| `$(BUILD)/kernel-unit` | one fixed name | `mktemp` + `trap` in the `unit` target |
| `build/manifest-{test,cli}.json` | fixed names in the checkout | per-case directory |

The isolation model is documented in `tests/scratch.py`: the compiled driver is
per **process** (it may be executed concurrently but never rebuilt while in use,
which is what `ETXTBSY` was about), and mutable run files are per **case**. A
failing case keeps its directory as the diagnostic artifact; a passing run cleans
up; `PC5_KEEP_TEST_SCRATCH=1` keeps everything.

**Correction (see report §10):** as first landed, the second half of that
sentence was false. The keep/remove decision read `_outcome.errors` and
`_outcome.failures`, which do not exist on `unittest.case._Outcome`, so the
`AttributeError` branch made every case "failed" and every directory was kept.
A fully passing 88-test run left 82 case directories and 84 class trees behind;
only `atexit` removed them, at process exit. `outcome.success` is not the fix
either - `doCleanups` re-enters `testPartExecutor`, which resets it to `True`
before every cleanup, so it reads `True` even after a failure.
`scratch.case_failed()` now scans `result.failures` / `.errors` /
`.unexpectedSuccesses` (unwrapping `_SubTest.test_case`) and attributes failures
per case, and the process-wide `note_failure()` flag is gone. **Further
correction (report §11):** the `.unexpectedSuccesses` half of that scan is inert
at cleanup time - `TestCase.run` calls `doCleanups()` before it records the
outcome - so an unexpected success deleted its own directory. It is now
reconstructed from the expected-failure marker, and four real-lifecycle tests
bind it in both directions (retain an unexpected success, remove a met
expectation). `test_scratch_isolation` is now **28** tests; the two failures the
old implementation produces are listed in §11.3.

**Correction after independent review (report §12-§13):** the percent-decoding
added to the link tooling let `%00` reach `Path.resolve()`, which raises
`ValueError: embedded null byte` - so a malformed link crashed the checker
instead of being reported. Fixed in `tools/docs_check.py` and
`tools/presentation.py` only (the third site uses `os.path.exists`, which returns
False); four regression tests, three of which fail against the previous code.
Separately, §13 records the documented LIFO constraint on `register_cleanup`:
evidence is retained for failures of the test body and of earlier cleanups, not
for an exception raised after the scratch cleanup has run. Measured after the
fix, with the same passing 88 tests: **0** case directories, **0** class trees,
**0** files. With one failure injected into a 17-case class, exactly that case's
directory survives (3 files).

`tests/test_scratch_isolation.py` (now **23** tests) binds this, including a
control that reproduces the old scheme in a throwaway tree and **requires it to
collide**, plus its inverse, which **requires the isolated scheme not to**. Both
were verified to fail in the wrong configuration, so the control is not vacuous.
The earlier 14 included two cleanup tests that faked `_outcome` with an object
exposing `errors`/`failures` - a double that never existed in the real runner,
so they passed while the code was broken. Those are replaced by
`CleanupLifecycleTest` (11 tests) driving real `TestCases` through a real
`TextTestRunner`. Against the old `scratch.py` as a control, 7 of the 11 fail.

Measured: two concurrent `make -s check` runs → 727 OK / 727 OK, 0 failures
(previously 40+ failures); four concurrent instances of the affected module →
50 OK ×4. **This is not a general proof of concurrency safety** — see
`FOLLOWUP_EVIDENCE_F1_F5_2026-11-16.md` §9.5. After the cleanup fix the full
suite is 736 tests (727 + 9 net new); a single `unicorn`-enabled run and a
concurrent pair of the two FAT32 modules (88 tests each, 0 scratch roots left
after either exits) both pass.

## 6. Known defects and limitations carried forward

1. **~~Test harness is not concurrency-safe.~~ Fixed later the same day.** The fixed working
   directory and the two other shared paths are described in §5a. The defect was real and was
   reproduced (19–48 failures with a single competing process); it is no longer reproducible
   after the fix. **The original single-process failure remains unexplained and is not claimed
   to have been caused by this defect** — see `FOLLOWUP_EVIDENCE_F1_F5_2026-11-16.md` §9.6.
2. **The suite has never run in CI.** Every green result in every document was produced on the
   author's machine.
3. **The 64-bit path is not wired into the shipping artifact.** `boot/uefi64/` and `kernel64/`
   compile and are tested at three levels, but the ISO is byte-identical to `main`'s.
4. **`OVMF`/QEMU/hardware are untested**, so the loader's behaviour against real firmware is
   inferred from a stub, not observed.
