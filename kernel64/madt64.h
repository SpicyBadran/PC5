/* CPU enumeration from ACPI's MADT (the "APIC" table).
 *
 * Why this and not CPUID. CPUID tells you what *this* core can do; it says nothing
 * about how many cores exist. The only firmware-published count is ACPI's MADT:
 * RSDP -> XSDT (or RSDT) -> the table whose signature is "APIC" -> one
 * `processor local APIC` record per enabled CPU
 * (ACPI 6.x, §5.2.12.1 for the RSDP, §5.2.5 for XSDT, §5.2.12.3 for the MADT).
 *
 * `M2a` asks for "a second CPU is brought up through the same path, or the absence
 * of one is reported". This module supplies the *enumeration* half: an honest count
 * and an identity list. Starting a secondary processor needs an INIT-SIPI sequence
 * and a trampoline in low memory, neither of which exists here, so this reports the
 * absence rather than claiming bring-up.
 *
 * Every read is bounds-checked against the table's own length field and then against
 * the memory map. A firmware table is untrusted input: it is a pointer published by
 * something this kernel did not write, and a length field that overruns its buffer is
 * the classic way to turn enumeration into a kernel fault.
 *
 * What this deliberately does not do: no APIC ID interpretation beyond recording it,
 * no x2APIC record handling (those are a different record type and are counted
 * separately rather than silently folded in), and no assumption that processor
 * records are contiguous.
 */
#ifndef PC5_MADT64_H
#define PC5_MADT64_H

#include "include/pc5_boot64.h"
#include "include/pc5_map.h"

#define PC5_MADT_MAX_CPUS 64

struct pc5_cpu_entry {
    u8  acpi_processor_id;   /* MADT local-APIC processor id */
    u8  apic_id;
    u32 enabled;             /* 1 when the enabled bit was set */
};

struct pc5_madt64 {
    u32 parsed;              /* 1 when a MADT was found and its header accepted */
    u32 acpi_revision;       /* RSDP revision that was walked */
    u32 used_xsdt;           /* 1 for XSDT (64-bit entries), 0 for RSDT */
    u32 local_apic_address;  /* MADT bytes 36..39 */
    u32 cpu_count;           /* enabled processor records */
    u32 cpu_total;           /* every processor record seen, enabled or not */
    u32 x2apic_total;        /* x2APIC records seen, recorded but not folded in */
    u32 truncated;           /* 1 when more CPUs exist than the array holds */
    u32 table_bytes;         /* MADT length as declared, for the log */
    struct pc5_cpu_entry cpus[PC5_MADT_MAX_CPUS];
};

/* Walk RSDP -> XSDT/RSDT -> MADT and fill `out`. Returns 0 on success, or a stable
 * token describing why the walk stopped. Never reads outside the map. */
const char *madt64_scan(struct pc5_madt64 *out, u64 rsdp_address,
                        const struct pc5_map_view *map);

void madt64_print(const struct pc5_madt64 *madt);

#endif
