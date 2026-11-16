# First three tasks

| | |
|---|---|
| Status | current. Replaces the v1 task list, which began with the Linux foundation and treated PS5 work as a later track |
| Authority | [`PC5_ARCHITECTURE_SPEC_v2.md`](PC5_ARCHITECTURE_SPEC_v2.md) |
| Graph | [`PC5_MILESTONE_GRAPH.md`](PC5_MILESTONE_GRAPH.md) |

Three tasks, in dependency order, each with acceptance criteria that a reviewer
can check without asking the author what was meant. They are chosen because they
are **unblocked** and because each one is on the destination path.

---

## Task 1 — `M1`: boot the image on one real machine

**Why first.** Every level above 1 is emulated today. A platform model for a
console cannot be validated against a machine that has never been booted by the
platform it is modelled on.

**Do**

1. Write `build/pc5.iso` to a USB stick and boot a machine from the
   qualification list (AMD reference configuration preferred: closest analogue to
   the PS5 SoC).
2. Record: machine, firmware vendor and version, Secure Boot state, boot mode
   (BIOS/CSM and UEFI), the full serial log, and the on-screen hardware page.
3. Repeat in the second firmware mode on the same machine.
4. File both logs under `docs/evidence/` and add the qualification rows.

**Acceptance**

- [ ] the machine reaches the PC5 environment, and the serial log is committed
      with the machine named;
- [ ] BIOS and UEFI configurations are both recorded, or the absence of one is
      explained by the machine's firmware;
- [ ] every device class the environment reports has a qualification row with
      the measurement that produced it;
- [ ] anything that fails is recorded as failing, with its failure code.

**Falsifies**: nothing in the architecture — but it settles whether level 1 is
real, which the graph currently marks `not_started`.

---

## Task 2 — `M10`: write the PS5 platform contract

**Why second.** The device models (`M11`–`M16`) cannot be built from guesses. The
contract is written from public documentation with every unknown marked, and it
needs no Sony bytes, so it is the highest-value unblocked work in the destination
workstream.

**Do**

1. Fill [`PS5_PLATFORM_CONTRACT.md`](PS5_PLATFORM_CONTRACT.md) for every device
   class: CPU and privilege model, memory, interrupts and timers, storage,
   graphics, audio, USB, service devices, security state.
2. For each row: mark `PUBLIC`, `INFERRED` or `UNKNOWN`, and cite the source for
   `PUBLIC` rows. An `UNKNOWN` row must say what observation would settle it.
3. Define the `PC5-PSM` interface shapes and the test double for each contract.
4. Define the conformance suite: what "the model works" means for each device,
   including the mutation runs that prove the suite can fail.

**Acceptance**

- [ ] every device class has a contract, and every row is classified;
- [ ] no row is invented: `UNKNOWN` appears wherever the public record stops;
- [ ] each contract has a test-double interface and a conformance-suite sketch;
- [ ] the security-state contract states what PC5 will not do, not what it might.

**Falsifies**: the assumption that the PC5 platform model can be specified from
public material at all. If large parts of the device set are `UNKNOWN`, that is a
finding to record in `M17`, not a gap to paper over.

---

## Task 3 — `M2a` + `M2b`: the CPU and memory foundation

**Why third.** These are the first two rows of the platform contract implemented
on the host side, and they are level-2 evidence in their own right: long mode,
APIC, SMP, paging, protection, DMA-visible memory.

**Do**

1. Move the kernel to 64-bit long mode with paging enabled, keeping the 32-bit
   path as the recovery environment until the new one boots under OVMF.
2. APIC initialisation, MSI delivery to a CPU vector, a high-resolution timer and
   a second CPU brought up through the same path.
3. A per-process address space with page-level protection, a physical allocator
   with fragmentation accounting, and DMA-visible buffers with an ownership
   protocol.
4. Record CPU virtualisation-extension availability per machine (SVM/VMX) — the
   platform model will need the answer.

**Acceptance**

- [ ] the kernel reports long mode in the boot log and a self-test proves paging
      is active;
- [ ] a write to a read-only page faults, and the test asserts the fault;
- [ ] a second CPU is either brought up or its absence is reported;
- [ ] a device driver round-trip through a DMA-visible buffer passes;
- [ ] `make boot-test`, `make stubtest` and `make ovmf-test` still pass.

---

## What is deliberately not next

| Not now | Why |
|---|---|
| Attempting to load the Sony package (`M18`) | blocked on `M17` and on a lawful input; attempting it would produce a failure with no information |
| A PC5 console interface (`M4`) | level 6, and the graph forbids using it as a proxy for progress |
| A Linux-based userspace as the product | ADR-020: the substrate is optional and must not become the visible product |
| Rebuilding the ISO/boot chain again | it is complete and verified; polishing it is displacement activity |

## Order of work from here

```
M1 (physical boot)  ->  M2/M2a/M2b (CPU, memory, drivers)  ->  M3 (services)
        \-> M10 (contract) -> M11 -> M12 -> M13 -> M14 -> M19 (destination, blocked)
                                   \-> M16 -> M17 (boundary analysis, blocked)
```

Tasks 1 and 2 can run in parallel; task 3 depends on task 1 for the hardware
answers and on nothing else.
