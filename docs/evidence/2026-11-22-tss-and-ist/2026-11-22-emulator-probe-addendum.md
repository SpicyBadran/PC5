# Addendum, same day: the `ltr` paragraph became a command

`ltr-measurement.md` next to this file records six hypotheses about why `ltr` traps under
Unicorn, and each one was ruled out by an experiment. It ended without a cause, which was
the honest thing to write at the time. This addendum says what changed later the same day,
so that the earlier document is read as the record it is rather than as the current
state of the investigation.

**What changed.** `tools/emulator_capabilities.py` (added in #45, run as
`make emulator-probe`) measures the boundary instead of inferring it, and the numbers are
kept in [`../2026-11-22-emulator-capabilities/README.md`](../2026-11-22-emulator-capabilities/README.md).
Three findings bear on this document:

* Processor exceptions are not delivered at all under Unicorn 2.1.4 — `ud2` produces no
  interrupt-hook call and the run stops with `UC_ERR_INSN_INVALID`, while `int 3` and
  `int 7` are delivered with their own vectors and the guest continues. So the missing
  thing is the exception path, not the harness, and no arrangement of IDT or TSS bytes
  would have changed that. Six experiments were aimed at the wrong component.
* `ltr` is not "trapping because of a bad descriptor" in a way the host can observe: it
  neither populates TR nor, in the seeded case, faults — TR simply never changes hands.
* The host *can* write and read TR as a 4-tuple `(selector, base, limit, flags)`. The
  opposite used to be assumed here (the binding exposes no scalar accessor, so it looked
  like no access at all), and it matters: it means a test could fake a loaded TR
  trivially. The rule in `kernel64/tss64.c` — refuse to arm IDT entries while TR does not
  name the TSS, and take the value only from what the guest established — is what keeps
  that door shut.

**One discrepancy is left open on purpose.** `docs/ARCHITECTURE.md` §7.5c reports the
kernel reading GDTR back as equal to the table it installed (`gdt=0x129280 gdtr=0x129280`),
i.e. `sgdt` reflecting a guest `lgdt` inside the stub harness. The probe's own
`lgdt`/`sgdt` pair reads back zeros, with and without the `lgdt`. Both were observed;
neither was reconciled, and the probe therefore reports its `ltr` rows as observations
about the engine rather than as a verdict on a descriptor. Resolving it means either
comparing the two environments' CR0/CR4/segment state or accepting that `sgdt` in this
Unicorn build reports something other than the guest's GDTR — and the answer changes
nothing about the conclusion, which is why it is left here rather than hunted further.

**Also in this directory:** `suite-count-attribution.md` reconciles the suite totals quoted
around this work (971, 994, 1011, 1019) against the trees they were measured on.

**What is unchanged.** The arming rule stays; `tr=not-loaded`, `ist-df=0` and `ist-nmi=0`
stay the expected boot line, and the gate still asserts all three. Double fault and NMI
running on their IST stacks remains an OVMF-or-hardware criterion: `make ovmf-test` still
exits 3 with `NOT TESTED`, because this sandbox has no QEMU, no OVMF image, no `/dev/kvm`,
and no root with which to install any of them.
