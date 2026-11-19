/* Port I/O for the 64-bit kernel: COM1 and the CPU's own registers. */
#ifndef PC5_IO_H
#define PC5_IO_H

#include "pc5_boot64.h"

static inline void outb(u16 port, u8 value)
{
    __asm__ volatile("outb %0, %1" : : "a"(value), "Nd"(port));
}

static inline u8 inb(u16 port)
{
    u8 value;
    __asm__ volatile("inb %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}

static inline u64 read_cr0(void) { u64 v; __asm__ volatile("mov %%cr0, %0" : "=r"(v)); return v; }
static inline u64 read_cr3(void) { u64 v; __asm__ volatile("mov %%cr3, %0" : "=r"(v)); return v; }
static inline u64 read_cr4(void) { u64 v; __asm__ volatile("mov %%cr4, %0" : "=r"(v)); return v; }

static inline u64 read_msr(u32 index)
{
    u32 lo, hi;
    __asm__ volatile("rdmsr" : "=a"(lo), "=d"(hi) : "c"(index));
    return ((u64)hi << 32) | lo;
}

#define MSR_EFER 0xC0000080u
#define EFER_LME (1u << 8)
#define EFER_LMA (1u << 10)

static inline void cpuid(u32 leaf, u32 subleaf, u32 out[4])
{
    __asm__ volatile("cpuid"
                     : "=a"(out[0]), "=b"(out[1]), "=c"(out[2]), "=d"(out[3])
                     : "a"(leaf), "c"(subleaf));
}

#endif
