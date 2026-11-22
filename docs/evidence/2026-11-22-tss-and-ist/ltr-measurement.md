# `ltr` under the emulator: what was measured, and what was ruled out

The TSS is only *used* once the CPU's task register names it, and the instruction that loads
TR is `ltr`.  The boot path in this commit builds the TSS, installs the GDT that describes
it, and then **stops short of loading TR**.  This file records why that is a measurement
rather than a shortcut.

## The observation

Under `make stubtest64` — PC5's own loader and PC5's own kernel, executed by Unicorn 2.1.4
with PC5's page tables installed, paging on, CPL 0 — executing `ltr` raises a CPU exception,
and the harness stops the run:

```
64-bit stub firmware FAILED: emulation faulted: Unhandled CPU exception (UC_ERR_EXCEPTION)
(rip=0x105138)
```

The harness installs no interrupt hook, so *any* exception the guest takes is fatal to the
run.  That is a property of the test rig: it means the rig cannot observe the exception being
delivered, and cannot distinguish "#UD because the emulator does not implement this
instruction" from "#TS because it does not like this descriptor".  What it can establish is
that the instruction was reached and that everything before it was correct, which it does
below — and that is why the gate asserts `tr=not-loaded` rather than skipping the subject.

## Ruled out, one instruction at a time

Temporary instrumentation (removed before the commit) printed the state around the faulting
instruction, in the same run:

```
PC5-64-TSSDBG cs=0x8 gdt=0x129280 limit=47 d0=0x20890010000067 d1=0 tss=0x1000 rsp=0x2017c50
PC5-64-TSSDBG after-lgdt base=0x129280 limit=47 str=0
```

| hypothesis | test | result |
| --- | --- | --- |
| the CPU is not at CPL 0, so a privileged instruction faults | `mov %cs, %ax` read before the attempt: `cs=0x8`, low two bits clear | ruled out; the harness was also changed to hand over at CPL 0 (`fw_cpl=0`), which is what a UEFI application actually does — with the old `0x33`/`0x2b` selectors *every* privileged instruction in the kernel was ring-3 |
| LGDT did not take | `sgdt` immediately after reports `base=0x129280 limit=47`, equal to what was written | ruled out |
| the descriptor is malformed | `d0=0x20890010000067 d1=0` decodes to limit 103, base `0x1000` (the granted frame), access `0x89` (present, DPL 0, type 9) and bit 53 set (a 64-bit TSS) | ruled out — and the kernel itself refuses to proceed if its own decoder disagrees, so the value printed is a checked value |
| the TSS frame is not memory the CPU can read | the descriptor was pointed at a location inside the kernel image instead of the pool frame | faults identically, so the location is not the trigger |
| TR was already in a bad state | `str` before the attempt reads `0x0` | ruled out as a *cause*; TR was simply unloaded |
| the operand form | `ltr m16` and `ltr %ax` both tried | both fault at the same instruction |
| the GDTR operand's alignment | the pointer structure was given `aligned(16)` (`lgdt`'s operand is `m16align` and the handover deliberately leaves `RSP % 16 == 8`) | `lgdt` succeeds with either; not the trigger |

An isolated reproduction outside the kernel was attempted and does **not** reproduce the
fault: a small Unicorn blob with a hand-built GDT/TSS executing `lgdt; ltr; str; sgdt`
accepts the same descriptor (and correctly rejects a *busy* TSS descriptor with an
exception, which shows the check is being reached).  That probe runs with paging off, and
the probe with paging installed by hand cannot be built at all here — Unicorn 2.1.4 exposes
no `EFER`, so `CR0.PG` cannot be combined with `LME`/`LMA`, and the sequence faults at
`lgdt` instead.  The honest statement is therefore narrow: **with paging installed the way
the kernel installs it, this emulator faults on `ltr`, and the reason was not determined.**

## What follows from that, in the code

* `pc5_tss64_install()` does the CPL check, `lgdt`, and an `sgdt` readback, and stops.  The
  GDT and the TSS are real, and their installation is verified.
* `report_tss()` refuses to arm the IDT unless TR names the TSS.  A gate with a non-zero IST
  field makes fault delivery read `ISTn` out of *whatever TSS TR points at* — unloaded, or
  the firmware's — which turns a reportable fault into a lost one.  So `ist-df=0`,
  `ist-nmi=0` are asserted by the gate as the *correct* end state, and the arming rules are
  exercised and then unwound in the same run (`arm-rules=1`).
* The gate check that would let this rot is "TR is reported unloaded and the gates say the
  same": if someone wires `ltr` into the boot path without an environment that can run it, or
  arms the gates without loading TR, `make stubtest64` fails (verified — see
  `negative-controls.md`, mutant A).
* What remains genuinely unverified, and stays that way until it is not: that a double fault
  or an NMI runs on its own stack.  That needs firmware or hardware that delivers the
  exception, which this repository has never had access to.
