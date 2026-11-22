# Negative controls for the TSS / IST change

Each entry is one edit to `kernel64/tss64.c` or `kernel64/main.c`, compiled from a copy of
the real source, run through the *same* checks that the real source has to pass.  A mutant
that survives means the property it removes is not tested, so the point of this file is the
list of things that did not survive.

## Host battery — `tests/test_tss64.py`

The battery is one function, `battery(lib)`, that returns a list of problems; empty means
every property held.  Run against the real source and against each mutant:

```
REAL SOURCE battery problems: []

MUTANT ist-top-unaligned:
   - the pool can satisfy a TSS and two stacks: build returned 0, reason=ist-not-16-aligned
   (1 problem(s) reported by the same battery)

MUTANT ignore-ready:
   - and says so as an unready pool: tss-frame-refused
   (1 problem(s) reported by the same battery)

MUTANT release-short:
   - release hands every frame back: free=14 expected 16
   - release clears what it handed back
   (2 problem(s) reported by the same battery)

MUTANT no-unwind:
   - the refusal for ist1-frame-refused takes nothing: free=0 vs 1
   - ist1-frame-refused leaves no TSS address behind
   - the refusal for ist2-frame-refused takes nothing: free=0 vs 2
   - ist2-frame-refused leaves no TSS address behind
   (4 problem(s) reported by the same battery)

MUTANT desc-shift:
   - the pool can satisfy a TSS and two stacks: build returned 0, reason=tss-descriptor-mismatch
   (1 problem(s) reported by the same battery)
```

What each mutant stands for, in the same order:

| mutant | the edit | why someone would write it | what catches it |
| --- | --- | --- | --- |
| `ist-top-unaligned` | `ist_top = base + PAGE - 4` | leaving a "red zone" under the stack top | the 16-byte alignment rule refuses, so the build no longer succeeds |
| `ignore-ready` | `if (!pc5_mem64_ready(pool))` → `if (0)` | trusting the caller to have checked | the refusal is now mislabelled `tss-frame-refused`, which the battery reads as a lie |
| `release-short` | the release loop starts at index 3 | "the TSS is the only frame that matters" | the two IST stacks stay held; `free` never comes back |
| `no-unwind` | the unwind loop never runs | a refusal "changed nothing, so nothing to undo" | a refusal keeps frames the pool thinks are free, in both `ist1` and `ist2` refusals |
| `desc-shift` | `base[31:24]` written into byte 6 instead of byte 7 | an off-by-one in a hand-rolled encoding | the descriptor no longer decodes to the granted frame |

`python3 -m unittest tests.test_tss64` → `Ran 7 tests … OK` on the source that ships; five
of those seven tests are the mutant assertions above.

The two exhaustion cases the `no-unwind` mutant leans on are real, not simulated: the
battery builds a pool whose window is one page, then two, so the TSS grant succeeds and
the IST grant is the one that fails.  A refusal that "forgot" to hand a frame back is
therefore visible as a `free` count that stopped moving, which is the only way a leak in a
boot-once path could ever be seen at all.

## Executed gate — `make stubtest64`

The gate reads the `PC5-64-TSS` line the kernel prints while running and cross-checks it
against the pool's own accounting.  Two mutants of `kernel64/main.c`, each run once against
the real firmware-stub harness:

```
$ # MUTANT A: arm IST even though TR was never loaded
  [FAIL] TR is reported unloaded and the gates say the same  (tr=not-loaded ist-df=1 ist-nmi=2)
  64-BIT STUB FIRMWARE FAILED (1 check(s)): 40/41 checks passed
  exit=2

$ # MUTANT B: hand the frames back after installing the table
  [FAIL] it took three frames and the pool lost three  (frames=0 free 620 -> 620)
  64-BIT STUB FIRMWARE FAILED (4 check(s)): 37/41 checks passed
  exit=2

$ # source as committed
  64-BIT STUB FIRMWARE OK: 41/41 checks passed
  exit=0
```

Mutant A is the one that matters most: it is the realistic mistake (set the IST fields,
assume the CPU will use them), and the gate fails on exactly the check that exists to catch
it, quoting the two fields that disagree with `tr=not-loaded`.  Mutant B is the accounting
mistake — the report says three frames, the pool says none — and it fails the cross-check
between the two.

Both mutants were reverted before the commit; the tree this directory documents is the tree
the gates ran against (`git status --porcelain` after the last run lists only the files this
commit adds).
