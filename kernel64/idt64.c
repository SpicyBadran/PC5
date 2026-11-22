#include "idt64.h"
#include "serial64.h"

/* One stub per vector in 0..31, defined in idt64.S. */
extern void pc5_vector_0(void);
extern void pc5_vector_1(void);
extern void pc5_vector_2(void);
extern void pc5_vector_3(void);
extern void pc5_vector_4(void);
extern void pc5_vector_5(void);
extern void pc5_vector_6(void);
extern void pc5_vector_7(void);
extern void pc5_vector_8(void);
extern void pc5_vector_9(void);
extern void pc5_vector_10(void);
extern void pc5_vector_11(void);
extern void pc5_vector_12(void);
extern void pc5_vector_13(void);
extern void pc5_vector_14(void);
extern void pc5_vector_15(void);
extern void pc5_vector_16(void);
extern void pc5_vector_17(void);
extern void pc5_vector_18(void);
extern void pc5_vector_19(void);
extern void pc5_vector_20(void);
extern void pc5_vector_21(void);
extern void pc5_vector_22(void);
extern void pc5_vector_23(void);
extern void pc5_vector_24(void);
extern void pc5_vector_25(void);
extern void pc5_vector_26(void);
extern void pc5_vector_27(void);
extern void pc5_vector_28(void);
extern void pc5_vector_29(void);
extern void pc5_vector_30(void);
extern void pc5_vector_31(void);

/* A 64-bit IDT gate: the handler address is split across three fields. Packed,
 * because the CPU reads this layout from memory and a compiler is entitled to pad
 * a struct. */
struct idt_gate {
    u16 offset_low;
    u16 selector;
    u8  ist;
    u8  type_attr;
    u16 offset_mid;
    u32 offset_high;
    u32 reserved;
} __attribute__((packed));

#define PC5_GATE_PRESENT 0x80u
#define PC5_GATE_DPL0    0x00u
#define PC5_GATE_INTR    0x0Eu  /* 64-bit interrupt gate: clears IF on entry */
#define PC5_KERNEL_CS    0x08u

static struct idt_gate g_idt[PC5_IDT_ENTRIES];
static struct pc5_idt_ptr g_idtr;

static void set_gate(u64 vector, void (*handler)(void))
{
    u64 address = (u64)(unsigned long)handler;

    g_idt[vector].offset_low = (u16)(address & 0xFFFFu);
    g_idt[vector].selector = PC5_KERNEL_CS;
    g_idt[vector].ist = 0;
    g_idt[vector].type_attr = PC5_GATE_PRESENT | PC5_GATE_DPL0 | PC5_GATE_INTR;
    g_idt[vector].offset_mid = (u16)((address >> 16) & 0xFFFFu);
    g_idt[vector].offset_high = (u32)((address >> 32) & 0xFFFFFFFFu);
    g_idt[vector].reserved = 0;
}

void idt64_install(void)
{
    void (*const handlers[32])(void) = {
        pc5_vector_0,  pc5_vector_1,  pc5_vector_2,  pc5_vector_3,
        pc5_vector_4,  pc5_vector_5,  pc5_vector_6,  pc5_vector_7,
        pc5_vector_8,  pc5_vector_9,  pc5_vector_10, pc5_vector_11,
        pc5_vector_12, pc5_vector_13, pc5_vector_14, pc5_vector_15,
        pc5_vector_16, pc5_vector_17, pc5_vector_18, pc5_vector_19,
        pc5_vector_20, pc5_vector_21, pc5_vector_22, pc5_vector_23,
        pc5_vector_24, pc5_vector_25, pc5_vector_26, pc5_vector_27,
        pc5_vector_28, pc5_vector_29, pc5_vector_30, pc5_vector_31,
    };
    u32 i;

    /* Entries 32..255 stay zeroed on purpose: not present, so a hardware interrupt
     * that cannot legitimately arrive yet raises #GP, which this table captures. A
     * present gate pointing at a made-up address would be far worse. */
    for (i = 0; i < 32u; i++)
        set_gate(i, handlers[i]);

    g_idtr.limit = (u16)(sizeof g_idt - 1u);
    g_idtr.base = (u64)(unsigned long)&g_idt[0];

    __asm__ volatile ("lidt %0" : : "m"(g_idtr));
}

int idt64_set_ist(u64 vector, u64 ist)
{
    if (vector >= 32u || ist == 0u || ist > 7u)
        return 0;
    if ((g_idt[vector].type_attr & PC5_GATE_PRESENT) == 0)
        return 0;
    g_idt[vector].ist = (u8)ist;
    return 1;
}

int idt64_clear_ist(u64 vector)
{
    if (vector >= 32u)
        return 0;
    g_idt[vector].ist = 0;
    return 1;
}

u64 idt64_gate_ist(u64 vector)
{
    if (vector >= PC5_IDT_ENTRIES)
        return 0;
    return g_idt[vector].ist;
}

/* A reader of the serial log should not need a table to know that vector 13 is a
 * general-protection fault. Only the vectors a bring-up kernel can plausibly hit
 * are named; the rest print as numbers. */
static const char *vector_name(u64 vector)
{
    switch (vector) {
    case 0:  return "divide-error";
    case 1:  return "debug";
    case 3:  return "breakpoint";
    case 4:  return "overflow";
    case 5:  return "bound-range";
    case 6:  return "invalid-opcode";
    case 7:  return "device-not-available";
    case 8:  return "double-fault";
    case 10: return "invalid-tss";
    case 11: return "segment-not-present";
    case 12: return "stack-segment-fault";
    case 13: return "general-protection";
    case 14: return "page-fault";
    case 16: return "x87-floating-point";
    case 17: return "alignment-check";
    case 18: return "machine-check";
    case 19: return "simd-floating-point";
    case 21: return "control-protection";
    default: return "unnamed";
    }
}

void idt64_report(u64 vector, u64 error_code, u64 rip, u64 cs, u64 rflags)
{
    serial64_write("PC5-64-EXCEPTION vector=");
    serial64_dec(vector);
    serial64_write(" name=");
    serial64_write(vector_name(vector));

    serial64_write(" error=");
    if (error_code == PC5_NOERR)
        serial64_write("none");
    else
        serial64_hex(error_code);

    serial64_write(" rip=");
    serial64_hex(rip);
    serial64_write(" cs=");
    serial64_hex(cs);
    serial64_write(" rflags=");
    serial64_hex(rflags);
    serial64_write("\n");

    /* A page fault says which access failed in the error code; decoding it saves
     * the operator from doing it by hand from a hex number. */
    if (vector == 14u && error_code != PC5_NOERR) {
        serial64_write("PC5-64-EXCEPTION page-fault");
        serial64_write((error_code & 0x1u) ? " protection-violation" : " not-present");
        serial64_write((error_code & 0x2u) ? " write" : " read");
        serial64_write((error_code & 0x4u) ? " user" : " supervisor");
        serial64_write((error_code & 0x10u) ? " instruction-fetch" : "");
        serial64_write("\n");
    }

    /* No recovery, deliberately: there is nothing to return to. */
    serial64_write("PC5-64-EXCEPTION halted\n");

    for (;;)
        __asm__ volatile ("cli; hlt");
}
