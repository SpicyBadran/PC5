/* pc5_boot64.h -- the 64-bit boot-information protocol (version 1).
 *
 * This is deliberately *not* `struct pc5_boot_info`, the 32-bit structure the BIOS and
 * UEFI loaders hand to the existing kernel.  The 64-bit kernel is a separate execution
 * environment with its own contract, because a 32-bit flat-mode structure cannot carry
 * a memory map with 64-bit addresses, an RSDP above 4 GiB, or a framebuffer base that
 * the firmware actually chose.
 *
 * Ownership rules:
 *   - the loader owns every buffer it points at, until the kernel has copied what it
 *     needs; after ExitBootServices nothing may be reached through firmware services;
 *   - the kernel owns its own page tables, stack and heap; it does not inherit the
 *     firmware's page tables;
 *   - every pointer here is a *physical* address, identity-mapped by the loader, and
 *     the kernel validates that it is inside a conventional-memory descriptor before
 *     dereferencing it.
 *
 * Version policy: new fields may only be appended, and `size` tells the kernel how many
 * of them are present.  A kernel refuses a structure whose magic, version or size it
 * does not recognise rather than guessing.
 */
#ifndef PC5_BOOT64_H
#define PC5_BOOT64_H

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;

#define PC5_BOOT64_MAGIC 0x42354350u /* "PC5B" in little-endian */

/* The 64-bit kernel is a flat image linked at this physical address and loaded
 * there unchanged: it is not relocatable, so every absolute reference in it is
 * resolved by the linker.  Both sides check it - the loader by allocating exactly
 * this address, the kernel by refusing to run anywhere else - so a loader and a
 * kernel built from different trees cannot silently disagree. */
#define PC5_KERNEL64_LINK_BASE 0x100000ull
#define PC5_BOOT64_VERSION 1u

#define PC5_BOOT64_F_MEMMAP (1u << 0)
#define PC5_BOOT64_F_FRAMEBUFFER (1u << 1)
#define PC5_BOOT64_F_RSDP (1u << 2)
#define PC5_BOOT64_F_EXIT_BOOT_SERVICES (1u << 3)

/* Memory-map descriptor types.  These are the firmware's own EFI_MEMORY_TYPE values,
 * copied verbatim on purpose: a translation table is one more place for the loader and
 * the kernel to disagree, and a wrong entry here becomes a write into live firmware. */
#define PC5_MMAP_RESERVED 0u          /* EfiReservedMemoryType */
#define PC5_MMAP_LOADER_CODE 1u       /* EfiLoaderCode */
#define PC5_MMAP_LOADER_DATA 2u       /* EfiLoaderData */
#define PC5_MMAP_BOOT_SERVICES_CODE 3u
#define PC5_MMAP_BOOT_SERVICES_DATA 4u
#define PC5_MMAP_RUNTIME_CODE 5u
#define PC5_MMAP_RUNTIME_DATA 6u
#define PC5_MMAP_CONVENTIONAL 7u      /* EfiConventionalMemory: the only free memory */
#define PC5_MMAP_UNUSABLE 8u
#define PC5_MMAP_ACPI_RECLAIM 9u
#define PC5_MMAP_ACPI_NVS 10u
#define PC5_MMAP_MMIO 11u
#define PC5_MMAP_MMIO_PORT_SPACE 12u
#define PC5_MMAP_PAL_CODE 13u
#define PC5_MMAP_PERSISTENT 14u

struct pc5_memdesc64 {
    u64 physical_start;
    u64 page_count;
    u64 attributes;
    u32 type;
    u32 reserved;
} __attribute__((packed));

struct pc5_boot64 {
    u32 magic;               /* PC5_BOOT64_MAGIC */
    u32 version;             /* PC5_BOOT64_VERSION */
    u32 size;                /* sizeof(struct pc5_boot64) as built by the loader */
    u32 flags;               /* PC5_BOOT64_F_* */

    u64 memmap_addr;         /* physical address of struct pc5_memdesc64[] */
    u64 memmap_bytes;        /* total bytes of the descriptor array */
    u32 memmap_desc_size;    /* sizeof(struct pc5_memdesc64) */
    u32 memmap_desc_version; /* UEFI descriptor version, informational */

    u64 framebuffer_addr;    /* physical, 0 when absent */
    u32 framebuffer_width;
    u32 framebuffer_height;
    u32 framebuffer_pitch;   /* bytes per scan line */
    u32 framebuffer_format;  /* 0 = unknown, 1 = BGRA8888, 2 = BGR888 */

    u64 acpi_rsdp;           /* physical address of the RSDP, 0 when absent */

    u64 kernel_base;         /* physical load address of this image */
    u64 kernel_size;         /* bytes loaded */
    u64 stack_top;           /* physical address of the initial stack top */

    u64 exit_boot_services;  /* 1 when the loader already called ExitBootServices */
    u64 time_stamp;          /* loader's monotonic count, informational only */
} __attribute__((packed));

#endif
