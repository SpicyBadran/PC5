# Frame allocator for the 64-bit kernel — evidence, 2026-11-22

What landed: `kernel64/mem64.c` + `kernel64/include/pc5_mem64.h` (a fail-closed physical
frame pool over the handover memory map), its report and self-test in the boot path
(`report_memory()` in `kernel64/main.c`), six new checks in the executed 64-bit gate
(`tests/boot64_stubtest.py`), and a host battery with pinned mutation controls
(`tests/test_mem64.py`). Three files new, four modified, +310/−5 at the commit that carries it.

This is **not** a memory manager for a running OS: nothing in the kernel allocates from the pool
yet, which is why the change is safe to land and why §"What this does not prove" is the most
important section here.

## Category discipline

| Category | Status for this change |
|---|---|
| host unit tests (`tests/test_mem64.py`, 7 tests, incl. 3 mutants) | executed, green |
| stub-firmware execution (`make stubtest64`, Unicorn long mode, the linked kernel + the real loader PE) | executed, 31/31 |
| OVMF / real firmware (`make ovmf-test`) | **NOT TESTED here** — no `qemu-system-x86_64`, no OVMF image, no `ovmf-check` pass available in this sandbox (exit 2, "no OVMF VARS image found") |
| physical PC | not attempted, no evidence |
| Sony PS5 | not attempted, no evidence, no claim |

A green stub-firmware gate is not a firmware boot. The allocator has run on emulated
long-mode execution of PC5's own objects and on the host through `ctypes`, and nowhere else.

## Commands and real exit codes

Run in a worktree of `ae9f3e25` (the session branch tip, which already carries the M2a
APIC/IDT/timer/MADT commits) with the slice applied, `unicorn 2.1.4` and node v22 on `PATH`, `TMPDIR=/tmp`.
`make -s clean` first, so every artifact below is from a from-scratch build of the exact committed source.

| command | exit | what it shows |
|---|---|---|
| `make kernel64` | 0 | `KERNEL64 OK: 200704 bytes at 0x100000` — `-Wall -Wextra -Werror`, freestanding |
| `make uefi64` | 0 | loader PE + ESP image build unchanged (`eficheck` still passes) |
| `make stubtest64` | 0 | `64-BIT STUB FIRMWARE OK: 31/31 checks passed` |
| `make stubtest` | 0 | 32-bit path unaffected |
| `make unit` | 0 | `PASSED: 165 checks, 0 failures` |
| `make docs-check` | 0 | `DOCS OK: 60 Markdown files, 33 make targets` (57 before this change, +3 from this directory) |
| `make citations-check` | **2** | pre-existing in this checkout — see "citation gate" below |
| `make presentation-check` | 0 | generated figures and README blocks current |
| `make check` | **2** | aborts at the same `citations-check` prerequisite, before the suite runs, so the suite was invoked directly |
| `python3 -m unittest discover -s tests -p 'test_*.py'` | 1 | `Ran 961 tests in 320.105s … FAILED (failures=5)` — all 5 in `test_commit_citations`, same root cause; the other 956 pass, including the 7 added here (`suite-summary.txt`) |
| `make iso` | 0 | `build/pc5.iso` 1601536 bytes, sha256 `2a6325812879c6afe499509bb6fa980be7d7981cbc57c88ffee5291713168f10` |
| `make verify` | 0 | `VALID` + `MANIFEST VALID` |
| `make boot-test` | 0 | the 32-bit release path still boots under v86 with the slice present |
| `make ovmf-check`, `make ovmf-test` | 2 | the firmware gate cannot run here at all |
| `make roadmap` | 0 | milestone graph and release isolation consistent |

**The citation gate failure is not caused by this change.** `make -s citations-check` was run in
a *pristine* `ae9f3e25` worktree with nothing applied: `exit 2`, 258 dead-citation lines
(see `citation-gate-comparison.md` in this directory, which carries the counts and three verbatim lines) — byte-identical in kind to the 258 lines with
the slice applied. The cause is the checkout: the documents cite objects that a default clone
does not fetch (PR heads live only under `refs/pull/N/head`). `git cat-file -t 141f91f0…`
fails locally while `gh api repos/SpicyBadran/PC5/commits/141f91f0…` returns the object, so the
citations are alive on GitHub and simply not provisioned in this sandbox. a `citation-refs` provisioning step (in the workspace's uncommitted changes, not in any branch) is the fix for that, and it is a
fetch-config problem, not a PC5 code problem. Because `make check` lists `citations-check` as a
prerequisite, the suite never starts under `make check` in this checkout — which is exactly the
"passing suite that silently skips the important test" pattern in reverse: a red gate hiding
the suite. The suite was therefore run directly, and the number reported as such.

## What the executed gate now requires

`PC5-64-MEM status=ok reason=ok frames=768 conventional=671 conventional_total=927 claimed=18
free=653 largest=512 bitmap=0x4000 span=0x100000000` and
`PC5-64-MEM-SELFTEST … result=ok`, both as *required markers*: a regression is a build failure,
not a note in a log. The two memory counts come from different code over the same descriptors —
`conventional_total` from the pool, `conventional_pages` from the map validator — and the gate
compares them, so a pool that loses or clips part of the map fails.

## Three defects the tests found, in the order they were found

1. **`0` was both a sentinel and a valid address.** The first API signalled failure by
   returning `0`, and frame 0 at physical `0x100000`… was reachable, so a caller could not tell
   "no memory" from "you got the first page". Found by the executed self-test on its first run.
   Fixed by reporting granted/refused separately, clearing the out-parameter on refusal, and
   reserving frame 0 by policy.
2. **A claim that the pool could not honour.** The self-test asserted "allocating every remaining
   page succeeds"; it does not, because a map with claims in the middle has no such contiguous
   run. The assertion was wrong, not the allocator; it is replaced by "a request one page longer
   than the largest run init measured must be refused" (`over-largest-refused=1`).
3. **A test that asserted a property of its fixture.** The self-test wrote a pattern into the
   page it had just been granted. That passes under the stub firmware, which maps the whole
   window, and faults under `tests/test_kernel64.py`'s synthetic machine:
   `UC_ERR_WRITE_UNMAPPED at 0x100805`, 41 contract tests failing on an unrelated line. The
   write is gone; the predicate it stood in for (the run is conventional memory inside the
   identity span) is asserted instead. A boot-time self-test must not dereference memory whose
   backing it cannot guarantee.

## A gap in the gate, found and closed

Deleting the allocation-time re-check in `pc5_mem64_alloc` (the clause that re-derives coverage
and claims for every grant) was caught by the host battery and **missed by the executed gate**:
`64-BIT STUB FIRMWARE OK: 31/31`. The guard was only tested somewhere else. The battery now
clears frame 0's bit behind the pool's back and requires the grant to be refused anyway
(`bitmap-lie-refused=1 refusal-counted=1`), which is a property a running kernel can observe.
With the same mutant applied, the executed gate now fails 29/31 with
`result=failed` (`negative-controls.md`).

## What this does not prove

- **No consumer.** `report_memory()` is the only caller. A kernel that allocates nothing cannot
  be brought down by a pool it never asks for; that is also why a failed self-test disarms the
  pool (`ready = 0`, every entry point refuses) instead of halting the boot.
- **Not verified on firmware.** No OVMF run, no physical PC, no PS5, and nothing here says
  otherwise. The pool's behaviour on a machine whose map has memory above 4 GiB, or a
  framebuffer inside the managed window, is exercised only by synthetic maps in the host battery.
- **4 GiB ceiling, and the 128 KiB that goes with it.** The pool manages only what the loader's
  identity map covers (`identity_span`, protocol limit 4 GiB). The bitmap has to describe that
  whole span or a real machine gets refused — so `PC5_MEM64_MAX_FRAMES` is *derived* from
  `PC5_IDENTITY_LIMIT` (1048576 frames → 128 KiB) rather than picked, and `.bss` travels with
  the image, which means the kernel-image claim covers the bitmap's own bytes. The measured
  cost is the linked image growing from 86016 to 200704 bytes, and `claimed` rising from 18 to
  50 frames because those 128 KiB are now inside the claim: `claimed + free == conventional`
  still holds (50 + 621 = 671), which is the invariant the gate checks. An earlier revision
  capped the pool at 512 MiB, which is smaller than any PC this kernel would ever boot on:
  the pool would have refused to manage a real machine's memory and printed
  `map-above-bitmap-cap`. That is now impossible by construction, and the refusal itself is
  still tested (host battery, 1-page bitmap) because a caller can pass a smaller buffer.
- **First-fit is a documented property, not a performance claim.** The scan is O(frames) per
  request; at 4096 frames measured here that is fine and is not benchmarked.
- `docs/ARCHITECTURE.md` §7.5 was edited so the tree still tells the truth: it said "Does not
  exist: a physical allocator". That is now false, so the page says what exists (a pool with no
  consumer) and what still does not (a *consumer* of it, a virtual-memory allocator, per-process
  address spaces, page-level protection, guard pages, NX use in EFER, anything above 4 GiB, DMA).

One note on the value of that gate: it failed once *because of this directory*. An earlier
revision of this file cited a target named `dashboard-reconcile`, and the check reported
`documented command ... has no target` — correctly, because that target exists only in the
unmerged reconciliation stack, not in this tree. Mentioning it in prose is what the sentence
above now does. A documentation gate that catches its own author's stale claim is worth more
than one that only checks links.

## Reproducing it

```sh
make -s clean
make kernel64            # must print KERNEL64 OK: 86016 bytes at 0x100000
make stubtest64          # 31/31; needs `pip install unicorn==2.1.4`, else it exits 3 (not-tested)
python3 -m unittest tests.test_mem64 -v     # host battery + the three mutation controls
```

The mutation controls in `tests/test_mem64.py` are pinned to source text: if one of the three
anchors moves, the test fails loudly with "the anchor moved; the mutant is stale" rather than
silently passing against a mutant that no longer exists.

## Tree state after the battery

`git status --porcelain` after all sixteen gates lists only this change — no generated file was
left behind. That is the property this repository's own gates exist to protect: a figure or a
block that a build silently rewrites is a claim that was never reviewed, which is why
`presentation-check` reads and refuses rather than regenerates.

```
 M Makefile
 M docs/ARCHITECTURE.md
 M kernel64/main.c
 M tests/boot64_stubtest.py
?? docs/evidence/2026-11-22-frame-allocator/
?? kernel64/include/pc5_mem64.h
?? kernel64/mem64.c
?? tests/test_mem64.py
```
