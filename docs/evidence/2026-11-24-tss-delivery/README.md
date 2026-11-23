# The TSS, used: a double fault and an injected NMI, both delivered on their own stacks

**Date:** 2026-11-24 · **Milestone 8, PR D (evidence for PRs A–C)** · **Level:** genuine TianoCore
OVMF under QEMU 11.0.2 (TCG), the release ISO itself, cold and warm; Unicorn 2.1.4 for the stub.
Not physical hardware, not a PS5, and **not CI** — no GitHub Actions run backs anything here, and
none is claimed.

## Why this page exists

For three milestones the 64-bit kernel printed `tr=not-loaded reason=ltr-unverifiable-here` and
the sentence around it blamed the emulator.  The emulator was fine: the kernel was handing `ltr` the
wrong selector.  That correction is `docs/evidence/2026-11-22-tss-and-ist/ltr-measurement.md`'s
erratum and §30 of `docs/TEST_RESULTS.md`.  This directory is what came after it — the point at
which the TSS stopped being a configuration to be *described* and became a mechanism to be *asked
to do something*:

> **Claim.** A `#DF` raised by the kernel itself, and an NMI raised by the hypervisor, are each
> delivered onto the interrupt stack their IDT gate names, out of the TSS the CPU has loaded.

Both halves are needed and they are not redundant.  The `#DF` is *invited* (`int $8`), so the
kernel controls the moment and can check its own surroundings before and after; it cannot say
anything about a fault nobody asked for.  The NMI is *imposed* from outside the guest, so it says
something about delivery to an unexpected vector; but the kernel cannot look at its own stack
pointer at the moment of arrival, so the reporter has to carry that number out for it.  Two
different observation routes, two different stacks, one claim.

Found on the way, both invisible to every check that existed before: the wrong `ltr` selector
(§30), and IST slots written four bytes low in the TSS, which made a CPU switch to an address no
page table mapped (§31).

## The measured events, verbatim

From `ovmf64-serial-cold.log` (the warm boot is byte-for-byte the same in these fields):

```
PC5-64-TSS status=ok arm-rules=1 tss=0x1000 gdt=0x12a280 limit=47 gdtr=0x12a280 cpl=0
    ist1=0x3000 ist2=0x4000 ist-df=1 ist-nmi=2 frames=3 free-after=53746
    tr=loaded str=0x20 desc=0x1000/103 busy=available-to-busy iomap=104
PC5-64BIT-LONG-MODE-OK
PC5-64-DONE
PC5-64-TSS-DF status=ok reason=ok rsp=0x2fd8 rsp-before=0xe1f9c70 ist1=0x3000
    resume=0x10245c gate-ist=1 df=0 frame=0x10245c,0x38,0x2,0xe1f9c70,0x30
    stack-intact=1 gate-restored=1
PC5-64-EXCEPTION vector=2 name=non-maskable-interrupt error=none cs=0x8 rflags=0x10046
    rsp=0x3fc8 ist=2
```

Read as arithmetic rather than as words, which is what `verification.sh` re-derives:

| Fact | Where it comes from | Why it could not be printed by a kernel that did nothing |
| --- | --- | --- |
| `str=0x20` | the CPU's own `STR` readback | a log edit could say `tr=loaded`; only `STR` knows what the register holds |
| `desc=0x1000/103` | the descriptor decoded *through GDTR* | it is read from the table the CPU is using, not from the builder's array |
| `busy=available-to-busy` | the descriptor type before and after the instruction | marking busy is a *write to memory* only a loading CPU performs |
| `iomap=104` | the TSS's own last field, read through the descriptor's base | a builder that wrote nothing, or wrote at the wrong width, fails here |
| `rsp=0x2fd8` vs `rsp-before=0xe1f9c70` | the handler's own stack pointer | a CPU that ignored the gate's IST field leaves these equal |
| `frame=0x10245c,…` contains `resume=0x10245c` | the five words at the delivery stack | a number in a log is not a frame; this one holds the address the trap was taken at |
| `df=0` | RFLAGS after the probe returned | a handler that left DF set makes the next fault a real double fault |
| `stack-intact=1` | a sentinel the probe wrote to the *faulting* stack before trapping | a delivery onto the wrong stack would have trampled it |
| `gate-restored=1` | the gate's target, compared with the stub table inside the IDT code | a probe that left its handler installed would corrupt every later fault |
| `ist=2` with `rsp=0x3fc8` inside `[0x3000, 0x4000)` | the live gate for vector 2, and the reporter's stack pointer | the TSS line's `ist2=0x4000` is the *configuration*; this pair is the *event* |

The two events landed on **different** pages — `0x2fd8` in IST1's, `0x3fc8` in IST2's — which is
the only way to show that the slot number rather than the mere existence of an interrupt stack is
what moved the RSP.  `verification.sh` asserts that separation explicitly.

## The gate's numbers

Every total below belongs to the tree this page files (`50ebe7d`), and `verification.sh` reads
these artifacts rather than the repository's current build output — a later tree legitimately
prints more rows (the kernel image self-check added two, taking the 64-bit gate to 104 passing
rows and the stub gate to 52), and that is recorded in §34, not here.

| Command | Result |
| --- | --- |
| `make PC5_UEFI=64 ovmf-test` | exit 0 · **102 PASS / 0 FAIL / 1 NOT TESTED** across 103 check rows (21 control + 41 cold + 41 warm), of which 14 are the delivery rows — seven per boot · matrix **56 rows**, all `verified` or `external` |
| `make ovmf-test` (32-bit) | exit 0 · **63 PASS / 0 FAIL / 1 NOT TESTED**, 35 matrix rows · unchanged from before this work, so nothing moved off the 32-bit path |
| `make check` | **`make check: suite and all gates passed`** — 1143 tests, plus `psm`, docs, citations, presentation and brand checks |
| `make stubtest64` | **`50/50 checks passed`** |
| `python3 -m unittest tests.test_tss64 tests.test_unicorn_soft_int tests.test_ovmf_gate_profiles tests.test_ovmf_harness tests.test_idt64 tests.test_kernel64` | **OK, 199 tests**, no skips with the harness venv on `PATH` |

The one NOT TESTED row is still `disk-style UEFI boot` (the ISO carries its ESP as an El Torito
image, so there is no partition to attach).  `small-memory` and `deadline` behave as before.

**The two summaries are not interchangeable, and this page was wrong about them once while being
written.** The matrix is keyed by *requirement*, so a boot that repeats another boot's requirements
does not get its own copy of every row: its 56 rows are 40 from the cold boot, **one** warm row
("the warm boot proved the same stages as the cold boot"), ten negative runs and five rows owned by
other gates.  The per-boot list — `checks` in the report, and what the seven delivery rows are
actually repeated in — has 103 entries.  So "both boots" is a claim about `checks`, and the sentence
in the first draft of this page that counted "14 delivery rows in the matrix" was a guess about a
number nobody had opened.  `verification.sh` counts both, separately, and fails if either drifts.

## The page's own negative control

A verification script that cannot fail verifies nothing, so one run was spent proving it can.  The
directory was copied to a scratch path and a *single field* of both serial logs was changed — the
injected NMI's `rsp=0x3fc8` rewritten to `0x2fd8`, which is the address the `#DF` probe reported, a
plausible-looking number from the same log.  Seven of the 113 checks then failed, including six that
are about the claim and none that is about formatting:

```
FAIL cold: NMI — the handler woke up inside the IST2 page: rsp=2fd8 ist2=4000
FAIL cold: NMI — and not on the stack the double-fault probe used: rsp=2fd8 ist1=3000
FAIL cold: the gate read nmi_rsp from the same log: gate=16328 log=12248
```

…repeated for the warm boot, plus the provenance check that requires the repository above the
directory (which a scratch copy does not have).  Exit status 1.  The interesting failure is the third
line: editing the *log* is caught by the *report*, because the two were filed separately — which is
the whole reason both are filed.

## Negative controls — the part that makes the table above mean anything

A green row is worth nothing unless the same harness goes red on the failure it claims to exclude.
Each control below is *executed*, not argued, and each is in the suite so `make check` runs it.

**In the 64-bit gate** (`tests/test_ovmf_gate_profiles.py`), by editing one field of a good log and
requiring the row to fail: an `rsp` equal to `rsp-before` (no stack switch); a frame with no word
equal to the resume address; `df=1`; an `rsp` outside the granted page; the probe line deleted
entirely; the NMI arriving *before* `PC5-64-DONE`; the NMI's `ist=0`; the NMI's `rsp` moved into
IST1's page; `nmi.accepted` false (QEMU refused — which must not be reported as a guest failure);
no injection requested at all; and `tr=not-loaded` with `status=skipped` — the one case that must
**pass**, because declining to measure an IST nobody can use is the correct behaviour.

**In the host suite** (`tests/test_tss64.py`): the probe verdict's seven refusals, each reached by
damaging exactly one field; the frame-scan test, which places the resume address in each of the five
words in turn and accepts all five (the layout is not what is under test); a mutant that moves IST1
back to offset 32 — caught by the test that decodes the TSS at the SDM's offsets, and *not* caught
by `pc5_tss64_check_loaded`, which reads through the same helper the builder wrote with, which is
precisely the agreement that hid the bug; and a mutant that reads the I/O-map base as a dword at 100.

**On the emulator itself** (`tests/test_unicorn_soft_int.py`, artifact `unicorn-soft-int.txt`): the
stub does not run the probe, and the reason is measured rather than remembered — the harness boots
the *shipped image* with its stop markers cleared, finds the one `cd 08` in the built file (the
count is asserted), and requires the emulator's fault to land on that instruction.  When Unicorn
learns to deliver a guest software interrupt, that test fails and the stub gains a real IST
measurement.

## Artifacts in this directory

| File | What it is |
| --- | --- |
| `verification.sh` | Re-derives every claim above from the files here (and counts the `[PASS]` lines in the two gate logs): 114 checks, no build, no QEMU, no network — `bash docs/evidence/2026-11-24-tss-delivery/verification.sh` → `OK (114 checks re-derived from 16 files in this directory)`. It recomputes the stack windows from the serial text rather than trusting `status=ok`, and cross-checks the gate's parsed numbers against the log they were parsed from. |
| `ovmf64-serial-cold.log`, `ovmf64-serial-warm.log` | The two boots' raw serial, as QEMU wrote it (`\r` doubling included, unedited). |
| `x64-report.json`, `x64-matrix.json` | The gate's report and matrix for the 64-bit profile: every row, every parsed marker, and `data.<boot>.nmi` — what the harness asked QEMU for and what QEMU answered. |
| `stubtest64-report.json` | The stub gate's 50 checks with the full emulated serial, including the `tr=loaded` line and the *absence* of a probe verdict. |
| `x64-gate.log`, `ia32-gate.log` | The two gates' own console output, every `[PASS]` line in the order it was decided — the artifact that lets a reader check a row *name* rather than a row count. |
| `unicorn-soft-int.txt` | The emulator refusal, captured by `capture-unicorn-refusal.sh`. |
| `capture-unicorn-refusal.sh` | How that file is produced, so it can be re-made without trusting this directory's copy. |
| `ia32-report.json`, `ia32-matrix.json`, `ovmf32-serial-cold.log`, `ovmf32-serial-warm.log` | The 32-bit profile's artifacts from the same tree, filed to make "we did not disturb the other path" a comparison rather than an assertion. Its serial carries no `PC5-64-` marker, which `verification.sh` checks. |
| `env.txt` | Tool versions, paths, digests, the commit the artifacts came from, and the commands. Generated from `x64-report.json`'s own `data.environment` block, so no value in it is remembered rather than measured; the ESP and ISO digests in it are compared back against the report by `verification.sh`. |

## What is NOT claimed

* **No hardware.** QEMU's TCG is an implementation of the architecture, and two independent
  implementations of the same architecture (Unicorn for `ltr`/`str`/`SGDT`, QEMU for delivery)
  agreeing is evidence about the ISA, not about a PS5.
* **No NMI during a fault, and no nesting.** The injection lands after the boot has settled, with
  IF clear, one CPU, and nothing on the IST stacks.  A double fault arriving *while* an NMI is being
  handled — the situation IST exists for — is not exercised here; only the switch is.
* **No depth, only delivery.** Both windows are one page, and `verification.sh` pins the layout
  that makes an overrun dangerous rather than harmless: `tss=0x1000 ist1=0x3000 ist2=0x4000` means
  the page below IST1 is the TSS's own page and the page below IST2 is IST1's. So this evidence says
  *the CPU switched to the stack the TSS names*, and says nothing about what a frame larger than
  4 KiB would do — there is no guard page and no canary, and an overrunning fault would corrupt the
  TSS instead of faulting. See "the 64-bit IST stacks are 4 KiB deep" in
  `docs/KNOWN_LIMITATIONS.md`.
* **No returning NMI handler.** The reporter prints and halts.  A kernel with a scheduler would have
  to return, and the `IRETQ` path, NMIRE ordering, and the `IRETQ`-vs-`IST` interaction are out of
  scope and untested.
* **No `#DF` caused by a real failure.** The probe invites the vector; a genuine double fault pushes
  an error code first, which is the layout difference the design deliberately refuses to depend on.
* **Not CI.** No Actions run exists for this and none is asserted.
* The `CS` selector the boot leaves in use (`0x38`) is *not* a descriptor in the 48-byte GDT this
  kernel installs.  It is legal in 64-bit mode because CS bases and limits are ignored, so nothing
  faults today; reloading CS would need a far jump through the new table, which is a change to the
  handover rather than to the TSS, and it is recorded as a known hazard in `docs/KNOWN_LIMITATIONS.md`
  rather than papered over here.

## Reproducing it

```sh
export TMPDIR=/tmp
# the harness venv (unicorn) must be on PATH or the stub legs skip and say so
make check
make stubtest64
make PC5_UEFI=64 ovmf-test          # needs QEMU + an OVMF CODE/VARS pair; see env.txt
make ovmf-test                      # the 32-bit path, unchanged by this work
sh docs/evidence/2026-11-24-tss-delivery/capture-unicorn-refusal.sh
bash docs/evidence/2026-11-24-tss-delivery/verification.sh
```

To re-capture the two serial logs without the report, boot `build/pc5.iso` by hand with
`-serial file:…` and compare the `PC5-64-TSS`, `PC5-64-TSS-DF` and `PC5-64-EXCEPTION vector=2`
lines: they are the whole claim, and they are printed by the kernel, not by the harness.
