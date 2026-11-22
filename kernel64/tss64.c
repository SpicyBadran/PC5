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
 * the two halves of the TSS descriptor - which is why the selector is 0x28 and not 0x20.
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

/* The GDT the CPU reads.  Deliberately does NOT load TR, and that is not an
 * oversight: `ltr` is the instruction that would make the IST stacks live, and the only
 * 64-bit execution environment this repository can run refuses it - measured, not
 * assumed, and asserted by `tests/test_tss64.py` so the day it changes is a failing test
 * rather than a rumour.  Loading TR is also the one step that would make the IDT's IST
 * fields *used*, and an IDT gate that names an interrupt stack while TR points
 * somewhere else is a fault that reads a stack nobody allocated.  So the table and the
 * segment registers are switched over here, the TSS and its stacks are built and
 * validated, and arming IST is left to a caller that has loaded TR. */
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
