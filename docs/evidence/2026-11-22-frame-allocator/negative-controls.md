# Negative controls: what each layer can and cannot see

Each mutant is one defence deleted from `kernel64/mem64.c` in a copy of the landing tree
(`/tmp/audit43/negctl`, same base, same source otherwise), built with the same
`-Wall -Wextra -Werror`, and run against **both** verification layers. After each row the file
is restored, and the restored tree is green in both layers — so none of these are
stale-fixture artifacts.

| mutant | defence removed | executed gate (`make stubtest64`) | host battery (`tests.test_mem64`) |
|---|---|---|---|
| 1 | the zero page is no longer reserved by policy (`claim_size`'s default returns 0) | `FAILED (2 check(s)): 29/31` | `FAILED (failures=2)`, 8 properties named |
| 2 | the allocation-time re-check is gone (a grant no longer re-derives map coverage and claims) | `FAILED (2 check(s)): 29/31` | `FAILED (failures=2)`, 2 properties named |
| 3 | a double free is allowed (the "already free" check deleted) | `FAILED (2 check(s)): 29/31` | `FAILED (failures=2)`, 3 properties named |

## Verbatim, all three, against the committed source

Mutant 1 — and note `one=0`, the whole bug in three characters: the pool handed out physical
address zero as a successful grant, and the `bitmap-lie` probe fires too, because that probe
depends on the same claims check the mutant disabled.

```
PC5-64-MEM-SELFTEST one=0 three=0x1000 grant-in-map=1 bitmap-lie-refused=0 refusal-counted=0
  disjoint=1 validated=1 claims-clean=1 zero-page-reserved=0 zero-refused=1 oversized-refused=1
  refusal-clears-base=1 over-largest-refused=1 double-free-refused=1 unaligned-free-refused=1
  first-fit-reuse=1 blocks-apart=1 free-restored=0 result=failed
64-BIT STUB FIRMWARE FAILED (2 check(s)): 29/31 checks passed
battery: the allocator broke these properties: plain map has nothing claimed but the zero page;
  a single page is granted; first-fit moves forward; frame zero is claimed; the zero page overlaps
  a claim; double free counted once; accounting survived; claims are subtracted from the free set
```

Mutant 2 — the row that motivated a change to the gate rather than only to the test. The same
mutant, run **before** the `bitmap-lie` probe existed, printed `OK: 31/31 checks passed` while
the battery failed: the guard was only ever tested in one of the two layers, and it was the
layer that never runs on a real machine.

```
PC5-64-MEM-SELFTEST one=0x1000 three=0x2000 grant-in-map=1 bitmap-lie-refused=0
  refusal-counted=0 … free-restored=0 result=failed
64-BIT STUB FIRMWARE FAILED (2 check(s)): 29/31 checks passed
battery: the allocator broke these properties: an allocation outside the map is refused;
  the refusal is counted as a coverage refusal
```

Mutant 3 — the executed gate sees it because the battery's `double-free-refused` and
`free-restored` fields are the same property measured on a running kernel.

```
PC5-64-MEM-SELFTEST … double-free-refused=0 … free-restored=0 result=failed
64-BIT STUB FIRMWARE FAILED (2 check(s)): 29/31 checks passed
battery: the allocator broke these properties: double free refused; double free counted once;
  accounting survived
```

## Two things this table also shows

* **The kernel does not halt on a self-test failure, and that is deliberate.** In all three runs
  the boot reaches `PC5-64-DONE`: the pool disarms itself (`ready = 0`) and prints
  `status=unavailable reason=mem64-selftest-failed`. What turns a broken allocator into a hard
  failure is the *gate*, which is the layer a change actually has to pass. A kernel with no
  allocations to make has no business refusing to boot over a bookkeeping disagreement — but a
  pull request that introduces one does.
* **`make` masks the exit code; the gate does not.** Every failing row above shows `make`
  exiting 2 while `tests/boot64_stubtest.py` itself exits 1. That is make's behaviour for any
  failed recipe, and it is why the numbers quoted in `README.md` for `make check` (2) and for
  the tool it wraps are reported separately rather than collapsed into one.
