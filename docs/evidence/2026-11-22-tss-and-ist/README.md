# The frame pool gets a consumer: a TSS, two interrupt stacks, and TR left alone

Date: 2026-11-22 · Milestone: M2b (`F64M` in the capability matrix) · Change: `kernel64/tss64.c`,
`kernel64/tss64.h`, the IST fields of `kernel64/idt64.c`, one new boot line in
`kernel64/main.c`, one new host suite, nine new checks in the executed 64-bit gate.

## What this is evidence for

`docs/ARCHITECTURE.md` §7.5c states the claim, and it is a narrow one:

> The 64-bit kernel now allocates through the physical-frame pool for its own data
> structure: it takes three frames - a task-state segment and two interrupt stacks - builds
> the TSS, encodes its descriptor into a GDT the kernel owns, installs that table, and
> verifies the install by reading GDTR back.

Everything below either supports that sentence or says plainly what the sentence does not
cover. The one thing it does *not* claim, and the reason this directory exists at all, is
that **TR is never loaded here**, so the interrupt stacks are built, checked and reported
but not in use.

## What was measured, and how

| command | what it can prove | result |
| --- | --- | --- |
| `make kernel64` | the new sources compile with the kernel's own `-Wall -Wextra -Werror`, `-ffreestanding`, `-mno-red-zone` | exit 0 |
| `make stubtest64` | the boot path *executes* on the stub firmware with the TSS step in it, and the nine new checks hold against what the kernel printed while running | `64-BIT STUB FIRMWARE OK: 41/41 checks passed` (was 31/31) |
| `python3 -m unittest tests.test_tss64` | the allocation and encoding rules, host-side, against the real `tss64.c`, plus five mutants of it | `Ran 7 tests … OK` |
| `make boot-test`, `make iso`, `make verify` | the 32-bit path and the image the previous milestone produced are unaffected | exit 0 |
| `make roadmap`, `make docs-check`, `make citations-check`, `make presentation-check` | the generated documents still match the tree they describe | exit 0 |

The full gate table, with each exit code as recorded, is in
[`commands-and-exit-codes.txt`](commands-and-exit-codes.txt). The serial text the executed
gate read is in [`serial-64bit-stub.txt`](serial-64bit-stub.txt); it is copied out of
`build/stubtest64-report.json`, which is the only place that text survives.

## The line this milestone is about

```
PC5-64-TSS status=ok arm-rules=1 tss=0x1000 gdt=0x129280 limit=47 gdtr=0x129280 cpl=0 ist1=0x3000 ist2=0x4000 ist-df=0 ist-nmi=0 frames=3 free-after=617 tr=not-loaded reason=ltr-unverifiable-here
```

Read against the pool's own line from the same boot:

```
PC5-64-MEM status=ok reason=ok frames=768 conventional=671 conventional_total=927 claimed=51 free=620 largest=512 bitmap=0x20000 span=0x100000000
```

* `free 620 -> 617` with `frames=3`: the pool lost exactly what the TSS says it took. This
  is the cross-check the gate computes rather than the one the kernel asserts - a report
  that printed `frames=3` while taking nothing, or taking four, fails.
* `tss=0x1000`, `ist1=0x3000`, `ist2=0x4000`: three different frames below the 4 GiB
  `span` the pool reports, each `ist*` being a page top, which is what the IST fields
  require (`RSP0..RSP2` stay zero: nothing switches to the TSS for a privilege change).
* `gdt=0x129280 gdtr=0x129280`: the table the kernel wrote and the table the CPU was told
  about are the same, because `sgdt` read back what `lgdt` was handed.
* `cpl=0`: read from `cs` at runtime, not assumed. This is what makes the harness worth
  running at all - it used to hand over with ring-3 selectors, which made every privileged
  instruction in the kernel illegal (`tests/boot64_stub.py`, `fw_cpl`).
* `ist-df=0 ist-nmi=0 tr=not-loaded`: the honest half. The gates are not armed, because TR
  does not name this TSS.
* `limit=47`: six 8-byte slots, the null/code/data pair the handover selectors already
  mean, and the TSS descriptor pair at slots 4 and 5 - so the selector a caller would hand
  to `ltr` is `0x28`.

## Controls: what fails if the code is wrong

Both halves of the claim are mutation-tested, and the point of doing it here is that a
check nobody tried to break is a check nobody needs.

* Five mutants of `kernel64/tss64.c` (misaligned IST, ignored ready flag, a release that
  forgets the stacks, a refusal that keeps frames, a descriptor whose base bytes land in
  the wrong field) are each put through the *same* `battery()` the real source goes
  through; every one is caught, and the battery reports the real source with an empty
  problem list. Details, including each mutant's message:
  [`negative-controls.md`](negative-controls.md).
* Two mutants of the *boot path* were run against the executed gate: arming an IDT gate's
  IST without loading TR, and releasing the frames after installing the table. The gate
  went to `40/41` and `37/41` respectively, each failing on the check written for exactly
  that mistake, with the disagreeing values quoted in the message. `main.c` was restored
  and the gate returned to `41/41` before this was committed.

## What is not claimed

* **No loaded task register.** `ltr` raises an exception under the emulator this repository
  can run, in every descriptor encoding tried; the measurement, and the six hypotheses
  ruled out one by one, are in [`ltr-measurement.md`](ltr-measurement.md). The kernel
  reports `installed` for the table and `tr=not-loaded` for the register, and refuses to arm
  the routing in between. A future change that loads TR has to change both the kernel line
  and the gate check that pins it, in the same commit.
* **No delivered fault.** Nothing here demonstrates a double fault or an NMI running on its
  own stack. That needs firmware or hardware that delivers the exception
  (`docs/evidence/validation/ovmf-test.json` still reads `not-tested`), so `F64` stays
  host-tested-and-stub-tested only, and `M2a` and `M2b` both stay `in_progress`.
* **No general-purpose heap.** Three frames are taken for the life of the boot and never
  returned; there is one consumer, it is fixed-size, and it runs once at boot. The pool's
  `free` count is the proof that nothing else is leaking, not a claim that anything reuses.
* **No claim about `qemu-system-i386`.** The 32-bit boot test is an emulator with a hand-built
  memory map, and the 64-bit stub gate is Unicorn, not firmware. Both are PC5's code on a
  model of a machine, which is the most this repository can currently show.
