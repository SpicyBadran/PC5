/* The CPU feature report the destination depends on.  Every field is measured, never
 * assumed: a zero here means "not reported by this CPU", which is different from
 * "supported" and from "PC5 may use it". */
#ifndef PC5_CPU64_H
#define PC5_CPU64_H

#include "include/pc5_boot64.h"

struct pc5_cpu64 {
    char vendor[13];
    u32 family;
    u32 model;
    u32 stepping;
    u32 max_basic_leaf;
    u32 max_extended_leaf;
    u32 phys_addr_bits;
    u32 virt_addr_bits;
    u32 long_mode;        /* CPUID.80000001H:EDX[29] */
    u32 nx;               /* EDX[20]: no-execute */
    u32 pages_1g;         /* EDX[26]: 1 GiB pages */
    u32 apic;             /* CPUID.1H:EDX[9] */
    u32 max_apic_ids;     /* CPUID.1H:EBX[23:16] */
    u32 x2apic;           /* CPUID.1H:ECX[21] */
    u32 svm;              /* CPUID.80000001H:ECX[2]: AMD-V advertised */
    u32 svm_npt;          /* CPUID.8000000AH:EDX[0]: nested paging */
    u32 svm_asids;        /* CPUID.8000000AH:ECX[0:31] */
    u32 hypervisor;       /* CPUID.1H:ECX[31]: we are already a guest */
    u32 invariant_tsc;    /* CPUID.80000007H:EDX[8] */
};

/* CPUID is injected so the leaf-gating logic can be tested with controlled
 * results.  The production entry point passes the architectural `cpuid`
 * instruction; a test passes a table. */
typedef void (*pc5_cpuid_fn)(u32 leaf, u32 subleaf, u32 *out);

void cpu64_report_with(struct pc5_cpu64 *out, pc5_cpuid_fn cpuid_fn);
void cpu64_report(struct pc5_cpu64 *out);
void cpu64_print(const struct pc5_cpu64 *report);
#endif
