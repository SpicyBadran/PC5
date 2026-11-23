/* See tss64.h for what this claims and what it does not.
 *
 * Split deliberately in two: `pc5_tss64_build` is ordinary C over the pool's API and
 * is tested on the host, including every refusal; `pc5_tss64_install` executes LGDT and
 * reads GDTR back, which only makes sense in ring 0 with paging already up, so it is
 * tested by the gate that runs the linked kernel.  Neither function pretends to have
 * checked the other's work: a build that succeeded says nothing about whether the CPU
 * accepted the table, and an install is not attempted unless the build succeeded.
 *
 * The split is also where the boundary of this file's reach sits.  LGDT changes which
 * descriptor table the CPU reads, and its effect is visible to SGDT, so the host cannot
 * test it and the executed gate can.  LTR would change which TSS the CPU reads, and its
 * effect is visible only to a CPU that takes a fault - so the instruction is here in
 * spirit and not in the code, and `installed` means the table, not the register.
 */
#include "tss64.h"

/* The GDT the kernel lives on.  Slots 1 and 2 are the code and data selectors the IDT
 * gates name (0x08, 0x10) written the same way the loader wrote them, slot 3 is left
 * zero because a null selector is what a NULL pointer looks like, and slots 4 and 5 are
 * the two halves of the TSS descriptor, named by the selector of its FIRST qword.

 * The selector used to be 0x28, on the reasoning that a 16-byte descriptor "occupies two
 * slots so it starts at the second one".  That is inverted: a GDT selector is the byte offset
 * of the descriptor divided by 8 -- which is exactly why the code segment at slot 1 is 0x08 and
 * the data segment at slot 2 is 0x10, both of which this kernel uses correctly from the IDT.
 * Slot 4 therefore begins at byte 32, which is 0x20; 0x28 names slot 5, the descriptor's high
 * half, whose low bits are base[63:32] and read as a not-present descriptor with type 0.
 *
 * The bug was invisible for as long as it existed because nothing executed LTR: the descriptor
 * itself was built correctly, `build()` decodes the same pair it encodes, and the gate asserted
 * the unloaded TR that a never-attempted load necessarily produces.  It surfaced the moment
 * `pc5_tss64_arm()` started reading the descriptor back through GDTR before believing it.
 *
 * It is in .bss, not in an allocated frame, on purpose: the CPU reads the GDT while
 * taking a fault, so it has to be up before the first fault, and the image claim covers
 * it without any allocation. */
static u64 g_gdt[PC5_TSS64_GDT_SLOTS] __attribute__((aligned(64)));

/* Long-mode code and data descriptors: P, DPL0, S, type 0xA / 0x2, G and L set, a full
 * 4 GiB flat limit.  The base field is ignored in 64-bit mode, so it is written zero. */
#define PC5_GDT_CODE64 0x00AF9A000000FFFFull
#define PC5_GDT_DATA   0x00CF92000000FFFFull
#define PC5_TSS_DESC_ACCESS 0x89ull     /* P=1, DPL=0, S=0, type=9 (available TSS) */
#define PC5_TSS_DESC_FLAGS  0x20ull     /* in byte 6: L=1, so a 64-bit TSS; G, D/B, AVL are 0 */

static u64 tss_ist_offset(u64 index)
{
    /* IST1 is the first field after the three RSPs and their reserved word. */
    return 32ull + (index - 1ull) * 8ull;
}

static u64 tss_iomap_offset(void)
{
    return 104ull - 4ull;
}

/* The 16 bytes of a TSS descriptor, built and taken apart here by the same table of
 * bit positions, so that what the kernel checks is the encoding the CPU reads:
 *
 *   bits  0..15  limit[15:0]        bits 40..47  access (P, DPL, S, type)
 *   bits 16..39  base[23:0]         bits 48..51  limit[19:16]
 *   bits 56..63  base[31:24]        bit  53      L - set means a 64-bit TSS
 *   bits 64..95  base[63:32]        (in the high half)
 *
 * `tss_desc_decode` is deliberately written from those positions rather than by
 * inverting the build: the two agree only if the layout comment is right, and the
 * host battery asserts the decoded values against the grant. */
static u64 tss_desc_build_low(u64 base, u64 limit)
{
    return (limit & 0xFFFFull)
        | ((base & 0xFFFFFFull) << 16)
        | (PC5_TSS_DESC_ACCESS << 40)
        | (((limit >> 16) & 0xFull) << 48)
        | (PC5_TSS_DESC_FLAGS << 48)   /* byte 6, bit 5: L, the 64-bit TSS flag */
        | (((base >> 24) & 0xFFull) << 56);
}

static void tss_desc_decode(u64 low, u64 high, u64 *base, u64 *limit)
{
    *base = ((low >> 16) & 0xFFull)
        | (((low >> 24) & 0xFFull) << 8)
        | (((low >> 32) & 0xFFull) << 16)
        | (((low >> 56) & 0xFFull) << 24)
        | ((high & 0xFFFFFFFFull) << 32);
    *limit = (low & 0xFFFFull) | (((low >> 48) & 0xFull) << 16);
}

int pc5_tss64_build(struct pc5_mem64 *pool, struct pc5_tss64 *out)
{
    u64 taken[PC5_TSS64_FRAMES];
    u64 frames = 0;
    u64 base;
    u64 i;
    u8 *tss;

    for (i = 0; i < PC5_TSS64_FRAMES; i++)
        taken[i] = 0;
    out->tss_base = 0;
    out->frames_taken = 0;
    out->built = 0;
    out->installed = 0;
    out->cpl = 0;
    out->tr_armed = 0;
    out->tr_selector = 0;
    out->desc_type_before = 0;
    out->desc_type_after = 0;
    out->selector = PC5_TSS64_SELECTOR;
    for (i = 0; i < 8; i++) {
        out->ist_base[i] = 0;
        out->ist_top[i] = 0;
    }

    if (out == 0)
        return 0;
    if (pool == 0) {
        out->reason = "no-pool";
        return 0;
    }
    /* Not ready is not the same as empty: an ignored return value cannot get past this,
     * because every entry point in the pool refuses a pool that is not ready. */
    if (!pc5_mem64_ready(pool)) {
        out->reason = "pool-not-ready";
        return 0;
    }

    if (!pc5_mem64_alloc(pool, 1, &base)) {
        out->reason = "tss-frame-refused";
        return 0;
    }
    taken[frames++] = base;
    out->tss_base = base;
    if (!pc5_mem64_validate(pool, base, 1)) {
        out->reason = "tss-grant-invalid";
        goto unwind;
    }

    /* IST1 then IST2.  A slot that cannot be had makes the whole exercise pointless: a
     * double-fault handler with no dedicated stack is a double fault that corrupts the
     * stack while reporting it, so one of the two is not half a feature. */
    if (!pc5_mem64_alloc(pool, PC5_TSS64_STACK_PAGES, &base)) {
        out->reason = "ist1-frame-refused";
        goto unwind;
    }
    taken[frames++] = base;
    out->ist_base[PC5_TSS64_IST_DOUBLE_FAULT] = base;
    out->ist_top[PC5_TSS64_IST_DOUBLE_FAULT] = base + PC5_MEM64_PAGE;
    if (!pc5_mem64_validate(pool, base, PC5_TSS64_STACK_PAGES)) {
        out->reason = "ist1-grant-invalid";
        goto unwind;
    }

    if (!pc5_mem64_alloc(pool, PC5_TSS64_STACK_PAGES, &base)) {
        out->reason = "ist2-frame-refused";
        goto unwind;
    }
    taken[frames++] = base;
    out->ist_base[PC5_TSS64_IST_NMI] = base;
    out->ist_top[PC5_TSS64_IST_NMI] = base + PC5_MEM64_PAGE;
    if (!pc5_mem64_validate(pool, base, PC5_TSS64_STACK_PAGES)) {
        out->reason = "ist2-grant-invalid";
        goto unwind;
    }

    if (out->tss_base == 0) {
        out->reason = "tss-base-zero";
        goto unwind;
    }
    /* A stack top has to be 16-byte aligned: the AMD manual says so, and a CPU that
     * disagrees with the manual does it by faulting inside the fault handler.  A page
     * top is 4096-byte aligned by construction, so this is a check on the arithmetic
     * above, not on the allocator - which is exactly the kind of check that stops being
     * obvious when someone makes the stack smaller than a page. */
    if ((out->ist_top[1] & 0xFull) != 0 || (out->ist_top[2] & 0xFull) != 0) {
        out->reason = "ist-not-16-aligned";
        goto unwind;
    }
    /* The TSS must not run past the frame it was given, and the IST stacks must not be
     * the TSS's own frame: an IST pointing into the TSS means the first push overwrites
     * the IST the next fault reads. */
    if (PC5_TSS64_SIZE > PC5_MEM64_PAGE) {
        out->reason = "tss-larger-than-frame";
        goto unwind;
    }
    if (out->ist_base[1] == out->tss_base || out->ist_base[2] == out->tss_base
        || out->ist_base[1] == out->ist_base[2]) {
        out->reason = "ist-shares-tss-frame";
        goto unwind;
    }

    tss = (u8 *)(unsigned long)out->tss_base;
    for (i = 0; i < PC5_MEM64_PAGE; i++)
        tss[i] = 0;
    for (i = 1; i <= 2; i++) {
        u64 top = out->ist_top[i];
        u64 offset = tss_ist_offset(i);
        u64 b;

        for (b = 0; b < 8; b++)
            tss[offset + b] = (u8)((top >> (b * 8)) & 0xFFull);
    }
    /* An I/O map base past the limit is how "there is no I/O permission map" is
     * written: any I/O access then raises #GP instead of reading whatever follows the
     * TSS in memory. */
    {
        u64 offset = tss_iomap_offset();
        u64 value = PC5_TSS64_SIZE;
        u64 b;

        for (b = 0; b < 4; b++)
            tss[offset + b] = (u8)((value >> (b * 8)) & 0xFFull);
    }

    g_gdt[0] = 0;
    g_gdt[1] = PC5_GDT_CODE64;
    g_gdt[2] = PC5_GDT_DATA;
    g_gdt[3] = 0;
    g_gdt[4] = tss_desc_build_low(out->tss_base, PC5_TSS64_SIZE - 1ull);
    g_gdt[5] = (out->tss_base >> 32) & 0xFFFFFFFFull;
    out->gdt_base = (u64)(unsigned long)&g_gdt[0];
    out->gdt_limit = sizeof(g_gdt) - 1ull;
    tss_desc_decode(g_gdt[4], g_gdt[5], &out->desc_base, &out->desc_limit);
    if (out->desc_base != out->tss_base || out->desc_limit != PC5_TSS64_SIZE - 1ull) {
        out->reason = "tss-descriptor-mismatch";
        goto unwind;
    }

    out->frames_taken = frames;
    out->built = 1;
    out->reason = "ok";
    return 1;

unwind:
    /* The pool never sees a half-built state: every frame this function took is
     * returned, so a caller that ignores the return value still cannot get a TSS that
     * points at memory which is on offer again. */
    for (i = frames; i > 0; i--) {
        u64 grant = taken[i - 1ull];
        u64 index;

        for (index = 0; index < 8; index++) {
            if (out->ist_base[index] == grant) {
                out->ist_base[index] = 0;
                out->ist_top[index] = 0;
            }
        }
        if (out->tss_base == grant)
            out->tss_base = 0;
        pc5_mem64_free(pool, grant, 1);
    }
    out->frames_taken = 0;
    g_gdt[4] = 0;
    g_gdt[5] = 0;
    return 0;
}

/* 16-byte aligned on purpose: LGDT's memory operand is `m16align`, and the stack slot
 * GCC picks for a local is only as aligned as the stack itself - which the handover
 * deliberately leaves at RSP % 16 == 8. */
struct pc5_desc_ptr {
    u16 limit;
    u64 base;
} __attribute__((packed, aligned(16)));

/* The GDT the CPU reads: a privilege check, LGDT, and an SGDT readback that has to agree.
 *
 * It stops short of LTR, and that belongs to `pc5_tss64_arm` rather than being absent: the
 * table has to be live before the descriptor in it can be handed to the CPU, and the two steps
 * fail for different reasons, so they report separately.
 *
 * The comment that stood here for three milestones explained the missing instruction as an
 * environment's refusal -- "the only 64-bit execution environment this repository can run
 * refuses it - measured, not assumed" -- and `docs/evidence/2026-11-22-tss-and-ist/` recorded
 * the fault with six hypotheses ruled out and the cause unnamed.  The cause was in this file:
 * the selector named 0x28, which is the *second* qword of the 16-byte TSS descriptor at g_gdt[4]
 * and reads as a not-present type-0 descriptor, so the CPU raised #TS on a request to load
 * something that is not a TSS at all.  The emulator behaved correctly; the kernel asked
 * incorrectly; and nothing could see it because the instruction was never executed, the builder
 * and its own decoder agreed with each other about the pair, and the gate asserted the unloaded
 * TR that a never-attempted load necessarily produces.  Recorded here because the lesson is not
 * "use 0x20": it is that a limitation attributed to a tool outlives the tool's usefulness as an
 * explanation, and the instruction that documents it is the one worth executing. */
int pc5_tss64_install(struct pc5_mem64 *pool, struct pc5_tss64 *state)
{
    static struct pc5_desc_ptr gdtr;
    struct pc5_desc_ptr readback;
    u16 cs = 0;

    if (!state->built) {
        state->installed = 0;
        state->reason = "not-built";
        return 0;
    }
    /* LGDT is privileged, and CPL is the low two bits of the selector the kernel is
     * executing from.  Asking before executing is the difference between a report and a
     * fault inside the setup of the thing meant to catch faults. */
    __asm__ volatile("mov %%cs, %0" : "=a"(cs));
    state->cpl = cs & 3u;
    if (state->cpl != 0u) {
        state->installed = 0;
        state->reason = "not-ring-0";
        return 0;
    }
    /* The frames have to still be ours: a table the CPU reads during a fault cannot
     * point at memory that is on offer again. */
    if (!pc5_mem64_validate(pool, state->tss_base, 1)) {
        state->installed = 0;
        state->reason = "tss-frame-lost";
        return 0;
    }

    gdtr.limit = (u16)state->gdt_limit;
    gdtr.base = state->gdt_base;
    __asm__ volatile("lgdt %0" : : "m"(gdtr));
    __asm__ volatile("sgdt %0" : "=m"(readback));

    state->gdtr_base = readback.base;
    state->gdtr_limit = readback.limit;
    if (readback.base != state->gdt_base || readback.limit != (u16)state->gdt_limit) {
        state->installed = 0;
        state->reason = "gdtr-mismatch";
        return 0;
    }
    state->installed = 1;
    state->reason = "ok";
    return 1;
}

/* The pieces of the access/flags bytes, at their architectural positions.
 *
 * Positions are spelled from the bit numbering in the Intel manual rather than by inverting
 * `tss_desc_build_low`, on the same principle the encode/decode pair above follows: a shared
 * helper would let one wrong bit position be both written and believed. */
#define PC5_DESC_TYPE_MASK   0xFull     /* bits 40..43 */
#define PC5_DESC_TYPE_SHIFT  40
#define PC5_DESC_S_SHIFT     44         /* bit 44: 0 for a system descriptor such as a TSS */
#define PC5_DESC_DPL_SHIFT   45         /* bits 45..46 */
#define PC5_DESC_P_SHIFT     47         /* bit 47: present */
#define PC5_DESC_L_SHIFT     53         /* bit 53: the 64-bit TSS flag */
#define PC5_DESC_G_SHIFT     55         /* bit 55: 4 KiB granularity of the limit */

/* The two types a TSS descriptor may have in long mode.  9 is "available 64-bit TSS", which
 * is what a fresh GDT entry holds and the only one LTR accepts; 11 is "busy", which is what
 * the descriptor reads as *after* a CPU has loaded it, and which LTR refuses a second time
 * with #TS.  So 9 -> 11 across the instruction is the signature of a real load: it is a write
 * into the GDT that nothing else in this kernel performs.  Both ends are recorded, rather than
 * the after-value alone, because "the CPU did not mark it busy" and "the CPU refused to load
 * it" are different facts about the machine the boot ran on, and no prose should be allowed to
 * blur them. */
#define PC5_DESC_TYPE_TSS_AVAIL 9ull
#define PC5_DESC_TYPE_TSS_BUSY  11ull

static u64 desc_field(u64 low, u64 shift, u64 mask)
{
    return (low >> shift) & mask;
}

/* Read the descriptor at `selector` out of the table GDTR names.  Returns 0 when the selector
 * does not address a complete descriptor inside the limit GDTR reports -- the same bounds test
 * the CPU applies, so a refusal here and a fault there cannot disagree about *whether* the read
 * is possible, only about what the read finds. */
static int gdt_descriptor_at(u64 gdtr_base, u64 gdtr_limit, u64 selector,
                             u64 *low, u64 *high)
{
    u64 byte_off = selector & ~7ull;          /* a GDT selector is the byte offset over 8, +RPL */

    if (gdtr_base == 0)
        return 0;
    if (byte_off < 16ull)                      /* slot 0 is null, slot 1 is the code segment */
        return 0;
    if (selector & 4u)                         /* table bit: LTR takes a GDT selector only */
        return 0;
    if (byte_off + 16ull > gdtr_limit + 1ull)   /* a 16-byte descriptor has to fit */
        return 0;
    if ((gdtr_base & 7ull) != 0)                /* qword reads, so the table must be 8-aligned */
        return 0;
    *low = *(volatile u64 *)(unsigned long)(gdtr_base + byte_off);
    *high = *(volatile u64 *)(unsigned long)(gdtr_base + byte_off + 8ull);
    return 1;
}

static u64 tss_byte(u64 base, u64 offset)
{
    return (u64) * (volatile u8 *)(unsigned long)(base + offset);
}

static u64 tss_qword(u64 base, u64 offset)
{
    u64 value = 0;
    u64 i;

    for (i = 0; i < 8ull; i++)
        value |= tss_byte(base, offset + i) << (i * 8ull);
    return value;
}

static u32 tss_dword(u64 base, u64 offset)
{
    u64 value = 0;
    u64 i;

    for (i = 0; i < 4ull; i++)
        value |= tss_byte(base, offset + i) << (i * 8ull);
    return (u32)value;
}

/* Everything about the table and the memory that LTR is about to be handed, or has just been
 * handed, without executing an instruction:
 *
 *   1. the selector addresses a real descriptor in the table GDTR names -- read *through*
 *      GDTR, not out of the builder's own array, because the builder agreeing with itself is
 *      worth nothing while the CPU reads a different table;
 *   2. that descriptor is a present, ring-0, available (or, after a load, busy) 64-bit TSS
 *      whose base and limit name the frame the pool granted, with byte granularity;
 *   3. what it points at is the TSS that was built: IST1 and IST2 read back as the tops the
 *      grant implies, the I/O-map base sits past the limit, and RSP0..2 are the zeros that
 *      say "no privilege transition is offered" rather than a stack nobody owns.
 *
 * `after_load` selects which type is expected, and the two calls bracket the instruction, so
 * the same function is what proves the descriptor did or did not change hands.
 *
 * Returns 1 and leaves `reason` at "ok", or returns 0 naming the first thing that disagreed.
 * It is deliberately callable without loading TR, which is what lets the host battery drive
 * every refusal below from a doctored table -- and it is why `tests/test_tss64.py` is where the
 * negative controls live, since a control that could only be run by faulting a real CPU is a
 * control for the environment rather than for the code. */
int pc5_tss64_check_loaded(struct pc5_tss64 *state, int after_load)
{
    u64 low = 0, high = 0, base = 0, limit = 0;
    u64 type;
    u64 selector = state->selector;

    state->reason = "ok";
    if (!state->built) {
        state->reason = "check-before-build";
        return 0;
    }
    /* The three selector faults are named apart, in the order the CPU would notice them,
     * because they mean different things to whoever reads the line: an RPL of 3 or the LDT bit
     * is a *corrupted* selector field -- something wrote into the state after the build set it
     * -- while a wrong-but-plausible value is the build's own idea of where the descriptor
     * lives, which is the defect this file used to carry.  Checking the bits before the
     * constant is what keeps those two stories apart. */
    if ((selector & 3ull) != 0ull) {
        state->reason = "check-selector-rpl";
        return 0;
    }
    if (selector & 4ull) {
        state->reason = "check-selector-table-bit";
        return 0;
    }
    if (selector != PC5_TSS64_SELECTOR) {
        state->reason = "check-selector-unexpected";
        return 0;
    }
    if ((state->tss_base & 7ull) != 0ull) {
        state->reason = "check-tss-unaligned";
        return 0;
    }
    if (!gdt_descriptor_at(state->gdtr_base, state->gdtr_limit, selector, &low, &high)) {
        state->reason = "check-descriptor-outside-gdt";
        return 0;
    }
    type = desc_field(low, PC5_DESC_TYPE_SHIFT, PC5_DESC_TYPE_MASK);
    if (after_load)
        state->desc_type_after = type;
    else
        state->desc_type_before = type;
    if (desc_field(low, PC5_DESC_P_SHIFT, 1ull) != 1ull) {
        state->reason = "check-descriptor-not-present";
        return 0;
    }
    if (desc_field(low, PC5_DESC_S_SHIFT, 1ull) != 0ull) {
        state->reason = "check-descriptor-is-code-or-data";
        return 0;
    }
    if (desc_field(low, PC5_DESC_DPL_SHIFT, 3ull) != 0ull) {
        state->reason = "check-descriptor-dpl";
        return 0;
    }
    if (type != (after_load ? PC5_DESC_TYPE_TSS_BUSY : PC5_DESC_TYPE_TSS_AVAIL)
        && !(after_load && type == PC5_DESC_TYPE_TSS_AVAIL)) {
        state->reason = after_load ? "check-descriptor-type-after" : "check-descriptor-type";
        return 0;
    }
    if (desc_field(low, PC5_DESC_L_SHIFT, 1ull) != 1ull) {
        state->reason = "check-descriptor-l-bit";
        return 0;
    }
    if (desc_field(low, PC5_DESC_G_SHIFT, 1ull) != 0ull) {
        state->reason = "check-descriptor-granularity";
        return 0;
    }
    tss_desc_decode(low, high, &base, &limit);
    state->desc_base = base;
    state->desc_limit = limit;
    if (base != state->tss_base) {
        state->reason = "check-descriptor-base";
        return 0;
    }
    if (limit != (u64)(PC5_TSS64_SIZE - 1u)) {
        state->reason = "check-descriptor-limit";
        return 0;
    }
    if (tss_qword(base, 32ull) != state->ist_top[PC5_TSS64_IST_DOUBLE_FAULT]) {
        state->reason = "check-ist1-field";
        return 0;
    }
    if (tss_qword(base, 40ull) != state->ist_top[PC5_TSS64_IST_NMI]) {
        state->reason = "check-ist2-field";
        return 0;
    }
    if (tss_dword(base, 100ull) != PC5_TSS64_SIZE) {
        state->reason = "check-iomap-field";
        return 0;
    }
    if (tss_qword(base, 4ull) != 0ull || tss_qword(base, 12ull) != 0ull
        || tss_qword(base, 20ull) != 0ull) {
        state->reason = "check-rsp-fields";
        return 0;
    }
    return 1;
}

/* LTR, and the two readbacks that make it a claim rather than an assembly statement.
 *
 * The instruction is executed here, on the boot path, by the kernel that owns the TSS -- not
 * by a harness on its behalf, and not behind a capability check: an environment that cannot
 * run it is *measured* by what the readbacks say afterwards, which is the difference between
 * "unverifiable here" and "refused here".
 *
 * Note what is checked before the instruction as well as after it.  Loading a descriptor whose
 * type is already busy raises #TS on a real CPU, so a kernel that only looked afterwards would
 * turn a stale TSS into a fault inside the setup of the thing meant to catch faults -- and the
 * boot log would end mid-line rather than name the reason.
 */
int pc5_tss64_arm(struct pc5_tss64 *state)
{
    u16 selector = (u16)state->selector;
    u16 readback = 0xFFFFu;

    state->tr_armed = 0;
    state->tr_selector = 0;
    state->desc_type_before = 0;
    state->desc_type_after = 0;
    if (!state->installed) {
        state->reason = "arm-before-install";
        return 0;
    }
    if (!pc5_tss64_check_loaded(state, 0))
        return 0;

    /* LTR's operand is a register holding the selector in 64-bit mode (`ltr %ax`).  `ltr m16`
     * is not a form this assembler accepts, and hand-encoding the opcode would be the kind of
     * thing that assembles into something else while looking right in a diff. */
    __asm__ volatile("ltr %0" : : "r"(selector));

    /* STR's readback is the CPU's own statement about TR, and it is the value the gate
     * compares against.  Asking for 0xFFFF first means "TR still says nothing" cannot be
     * confused with "TR says what we set". */
    __asm__ volatile("str %0" : "=r"(readback));
    state->tr_selector = readback;
    if (readback != selector) {
        state->reason = "arm-tr-readback";
        return 0;
    }
    if (!pc5_tss64_check_loaded(state, 1))
        return 0;

    state->tr_armed = 1;
    state->reason = "ok";
    return 1;
}

/* What to print for the busy transition: words, not numbers, because the distinction the field
 * encodes is the point of the whole bracket and a hex digit hides it. */
const char *pc5_tss64_busy_transition(const struct pc5_tss64 *state)
{
    if (state->desc_type_before == PC5_DESC_TYPE_TSS_AVAIL
        && state->desc_type_after == PC5_DESC_TYPE_TSS_BUSY)
        return "available-to-busy";
    if (state->desc_type_before == PC5_DESC_TYPE_TSS_AVAIL
        && state->desc_type_after == PC5_DESC_TYPE_TSS_AVAIL)
        return "still-available";
    if (state->desc_type_before == 0ull && state->desc_type_after == 0ull)
        return "unreadable";
    return "other";
}

/* The I/O-map base field, re-read from the TSS.  104 (`PC5_TSS64_SIZE`) is the encoding for
 * "there is no I/O permission map", which is what this kernel wants: the field sits past the
 * descriptor's limit, so any I/O access raises #GP instead of reading whatever follows the
 * structure in memory.  A 0 means the field was never written, and an IST stack behind an
 * unwritten TSS is the configuration this whole file exists to refuse. */
u64 pc5_tss64_iomap_base(const struct pc5_tss64 *state)
{
    if (!state->built || state->desc_base == 0)
        return 0;
    return (u64)tss_dword(state->desc_base, 100ull);
}

void pc5_tss64_release(struct pc5_mem64 *pool, struct pc5_tss64 *state)
{
    u64 i;

    for (i = 0; i < 8; i++) {
        if (state->ist_base[i] != 0) {
            pc5_mem64_free(pool, state->ist_base[i], PC5_TSS64_STACK_PAGES);
            state->ist_base[i] = 0;
            state->ist_top[i] = 0;
        }
    }
    if (state->tss_base != 0) {
        pc5_mem64_free(pool, state->tss_base, 1);
        state->tss_base = 0;
    }
    g_gdt[4] = 0;
    g_gdt[5] = 0;
    state->frames_taken = 0;
    state->built = 0;
    state->installed = 0;
}

u64 pc5_tss64_gdt_word(u64 index)
{
    if (index >= PC5_TSS64_GDT_SLOTS)
        return 0;
    return g_gdt[index];
}

const char *pc5_tss64_reason(const struct pc5_tss64 *state)
{
    if (state->reason == 0)
        return "unreported";
    return state->reason;
}
