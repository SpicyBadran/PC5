#include "include/pc5_boot64.h"
#include "include/pc5_io.h"
#include "cpu64.h"
#include "serial64.h"

static void copy(char *dst, const char *src, u32 max)
{
    u32 i = 0;
    while (i + 1 < max && src[i]) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

void cpu64_report_with(struct pc5_cpu64 *out, pc5_cpuid_fn cpuid_fn)
{
    u32 regs[4];
    for (u32 i = 0; i < (u32)sizeof(*out); i++)
        ((u8 *)out)[i] = 0;

    cpuid_fn(0, 0, regs);
    out->max_basic_leaf = regs[0];
    char vendor[13];
    *(u32 *)&vendor[0] = regs[1];
    *(u32 *)&vendor[4] = regs[3];
    *(u32 *)&vendor[8] = regs[2];
    vendor[12] = '\0';
    copy(out->vendor, vendor, sizeof(out->vendor));

    cpuid_fn(1, 0, regs);
    out->stepping = regs[0] & 0xF;
    out->model = (regs[0] >> 4) & 0xF;
    out->family = (regs[0] >> 8) & 0xF;
    u32 ext_model = (regs[0] >> 16) & 0xF;
    u32 ext_family = (regs[0] >> 20) & 0xFF;
    if (out->family == 0xF)
        out->family += ext_family;
    if (out->family == 0x6 || out->family == 0xF)
        out->model |= ext_model << 4;
    out->apic = (regs[3] >> 9) & 1;
    out->max_apic_ids = (regs[1] >> 16) & 0xFF;
    out->x2apic = (regs[2] >> 21) & 1;
    out->hypervisor = (regs[2] >> 31) & 1;

    cpuid_fn(0x80000000u, 0, regs);
    out->max_extended_leaf = regs[0];
    /* Every extended leaf is gated on the *saved* maximum, not on regs[0]: the
     * basic-leaf calls above leave other values in regs, and the 0x80000001 call
     * in particular overwrites regs[0] with that leaf's EAX.  Testing regs[0]
     * there queried a leaf the CPU might not implement and left the address-width
     * fields at zero when it did. */
    u32 max_extended = regs[0];
    if (max_extended >= 0x80000001u) {
        cpuid_fn(0x80000001u, 0, regs);
        out->long_mode = (regs[3] >> 29) & 1;
        out->nx = (regs[3] >> 20) & 1;
        out->pages_1g = (regs[3] >> 26) & 1;
        out->svm = (regs[2] >> 2) & 1;
    }
    if (max_extended >= 0x80000008u) {
        cpuid_fn(0x80000008u, 0, regs);
        out->phys_addr_bits = regs[0] & 0xFF;
        out->virt_addr_bits = (regs[0] >> 8) & 0xFF;
    }
    if (max_extended >= 0x8000000Au && out->svm) {
        cpuid_fn(0x8000000Au, 0, regs);
        out->svm_asids = regs[0];
        out->svm_npt = regs[3] & 1;
    }
    if (max_extended >= 0x80000007u) {
        cpuid_fn(0x80000007u, 0, regs);
        out->invariant_tsc = (regs[3] >> 8) & 1;
    }
}

void cpu64_report(struct pc5_cpu64 *out)
{
    cpu64_report_with(out, cpuid);
}

static void field(const char *name, u64 value, int decimal)
{
    serial64_write("PC5-64-CPU ");
    serial64_write(name);
    serial64_write("=");
    if (decimal)
        serial64_dec(value);
    else
        serial64_hex(value);
    serial64_write("\n");
}

void cpu64_print(const struct pc5_cpu64 *cpu)
{
    serial64_write("PC5-64-CPU vendor=");
    serial64_write(cpu->vendor);
    serial64_write("\n");
    field("family", cpu->family, 1);
    field("model", cpu->model, 1);
    field("stepping", cpu->stepping, 1);
    field("phys_bits", cpu->phys_addr_bits, 1);
    field("virt_bits", cpu->virt_addr_bits, 1);
    field("long_mode", cpu->long_mode, 1);
    field("nx", cpu->nx, 1);
    field("pages_1g", cpu->pages_1g, 1);
    field("apic", cpu->apic, 1);
    field("max_apic_ids", cpu->max_apic_ids, 1);
    field("x2apic", cpu->x2apic, 1);
    field("svm_advertised", cpu->svm, 1);
    field("svm_npt", cpu->svm_npt, 1);
    field("svm_asids", cpu->svm_asids, 1);
    field("hypervisor", cpu->hypervisor, 1);
    field("invariant_tsc", cpu->invariant_tsc, 1);
}
