/* pc5_boot64.h -- the 64-bit boot-information protocol, version 2.
 *
 * This is deliberately *not* `struct pc5_boot_info`, the 32-bit structure the BIOS
 * and UEFI loaders hand to the existing kernel.  The 64-bit kernel is a separate
 * execution environment with its own contract, because a 32-bit flat-mode structure
 * cannot carry a memory map with 64-bit addresses, the page tables the kernel is
 * running on, or an image and stack the kernel can validate before touching them.
 *
 * ---------------------------------------------------------------------------
 * What is guaranteed before the kernel dereferences anything
 * ---------------------------------------------------------------------------
 *
 * The kernel cannot validate the memory it is standing on before it reads it, so
 * the protocol is written to make the first read as narrow as possible:
 *
 *   1. the address of this structure is checked arithmetically (non-null, 8-byte
 *      aligned, and `address + size` inside the identity span) before the first
 *      byte is read; the loader allocates it below the span for exactly this
 *      reason;
 *   2. magic, version and size are checked immediately after that read, and an
 *      unrecognised structure stops the kernel before any other field is used;
 *   3. the memory map is the next thing dereferenced, and its *address* is
 *      validated arithmetically (non-zero, 8-byte aligned, no overflow); once it
 *      has been read, every other pointer in this structure - kernel image,
 *      stack, page tables, loader image, and the optional framebuffer and RSDP -
 *      is validated *against that map* for range and memory type before it is
 *      dereferenced or reported as usable;
 *   4. a resource that fails validation is never passed on as usable: the kernel
 *      either stops the boot (mandatory fields) or records the resource as
 *      unsupported and leaves its address zeroed (optional fields).
 *
 * ---------------------------------------------------------------------------
 * Address-space policy
 * ---------------------------------------------------------------------------
 *
 * Protocol version 2 maps exactly one region: the identity map the loader built,
 * `[0, identity_span)`, 2 MiB pages, `loader_page_table` at its root.  A pointer
 * in this structure is therefore *dereferenceable* only if it lies entirely
 * inside that span; anything else is either impossible (the loader fails the
 * boot before handing over, or zeroes the field and sets the matching
 * `_UNMAPPED_` flag) or rejected by the kernel.  Nothing here is a virtual
 * address, and nothing implies a mapping that was not built: the kernel maps
 * nothing new in version 2.
 *
 * ---------------------------------------------------------------------------
 * Memory-type rules for validated ranges
 * ---------------------------------------------------------------------------
 *
 * Loader-owned ranges (this structure, the memory map, the kernel image, the
 * kernel stack, the page tables, the loader's own image) must be covered by
 * descriptors of a type firmware can legitimately have handed to a loader:
 * LoaderCode, LoaderData, BootServicesCode, BootServicesData, RuntimeServicesData
 * *or* ConventionalMemory.  Conventional memory is accepted on purpose: firmware
 * that does not re-type a page it just allocated to us still reports the
 * underlying region, and requiring LoaderData would reject correct firmware.
 * Reserved, Unusable, MMIO and persistent memory are never accepted for these
 * ranges.
 *
 * The framebuffer is the opposite case: it is memory the loader must not treat as
 * RAM, so it is accepted only when it is *not* conventional - MMIO, MMIO port
 * space, LoaderData or BootServicesData (a boot-services mapping of the
 * framebuffer is still a device window, not free memory).  A framebuffer reported
 * as Reserved, Unusable or Conventional is recorded as unsupported rather than
 * trusted.
 *
 * The RSDP lives in firmware-owned memory: ACPI reclaim, ACPI NVS, MMIO, Reserved
 * or a loader/boot-services buffer are all accepted, and its "RSD PTR " signature
 * is then verified before the address is reported as usable.
 *
 * ---------------------------------------------------------------------------
 * Version policy
 * ---------------------------------------------------------------------------
 *
 * Fields may only be appended, and `size` says how many are present.  Version 2
 * appends the stack size, the page-table root, its page count and the span it
 * covers, and the loader's own image range; version 1 structures are refused rather than guessed at,
 * because v1 cannot describe the page tables the kernel is running on.
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
#define PC5_BOOT64_VERSION 2u
/* Address range that every dereferenceable pointer in this structure must fall
 * inside, and the largest span version 2 can describe (4 GiB: 4 PD pages). */
#define PC5_IDENTITY_LIMIT (4ull << 30)
#define PC5_PAGETABLE_MAX_PAGES 514ull

#define PC5_BOOT64_F_MEMMAP (1u << 0)
#define PC5_BOOT64_F_FRAMEBUFFER (1u << 1)         /* mapped, usable, address valid */
#define PC5_BOOT64_F_RSDP (1u << 2)                /* mapped, signature to be checked */
#define PC5_BOOT64_F_EXIT_BOOT_SERVICES (1u << 3)
/* The firmware offered the resource but it lies outside the identity map (or the
 * loader could not confirm it).  The address field is zero in that case, and the
 * kernel must not invent one: it reports the resource as unsupported. */
#define PC5_BOOT64_F_UNMAPPED_FRAMEBUFFER (1u << 4)
#define PC5_BOOT64_F_UNMAPPED_RSDP (1u << 5)

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
#define PC5_MMAP_TYPE_MAX PC5_MMAP_PERSISTENT

/* Bit masks over PC5_MMAP_* types, for "is this range covered by a range of an
 * acceptable type" checks. */
#define PC5_MMAP_BIT(type) (1u << (type))
#define PC5_MMAP_LOADER_OWNED_TYPES \
    (PC5_MMAP_BIT(PC5_MMAP_LOADER_CODE) | PC5_MMAP_BIT(PC5_MMAP_LOADER_DATA) | \
     PC5_MMAP_BIT(PC5_MMAP_BOOT_SERVICES_CODE) | \
     PC5_MMAP_BIT(PC5_MMAP_BOOT_SERVICES_DATA) | \
     PC5_MMAP_BIT(PC5_MMAP_RUNTIME_DATA) | PC5_MMAP_BIT(PC5_MMAP_CONVENTIONAL))
#define PC5_MMAP_FRAMEBUFFER_TYPES \
    (PC5_MMAP_BIT(PC5_MMAP_MMIO) | PC5_MMAP_BIT(PC5_MMAP_MMIO_PORT_SPACE) | \
     PC5_MMAP_BIT(PC5_MMAP_LOADER_DATA) | PC5_MMAP_BIT(PC5_MMAP_BOOT_SERVICES_DATA))
#define PC5_MMAP_FIRMWARE_DATA_TYPES \
    (PC5_MMAP_BIT(PC5_MMAP_ACPI_RECLAIM) | PC5_MMAP_BIT(PC5_MMAP_ACPI_NVS) | \
     PC5_MMAP_BIT(PC5_MMAP_MMIO) | PC5_MMAP_BIT(PC5_MMAP_MMIO_PORT_SPACE) | \
     PC5_MMAP_BIT(PC5_MMAP_RESERVED) | PC5_MMAP_BIT(PC5_MMAP_LOADER_DATA) | \
     PC5_MMAP_BIT(PC5_MMAP_BOOT_SERVICES_DATA))

struct pc5_memdesc64 {
    u64 physical_start;
    u64 page_count;
    u64 attributes;
    u32 type;
    u32 reserved;
} __attribute__((packed));

struct pc5_boot64 {
    u32 magic;                       /* PC5_BOOT64_MAGIC */
    u32 version;                     /* PC5_BOOT64_VERSION */
    u32 size;                        /* sizeof(struct pc5_boot64) as built by the loader */
    u32 flags;                       /* PC5_BOOT64_F_* */

    u64 memmap_addr;                 /* physical address of struct pc5_memdesc64[] */
    u64 memmap_bytes;                /* total bytes of the descriptor array */
    u32 memmap_desc_size;            /* sizeof(struct pc5_memdesc64) */
    u32 memmap_desc_version;         /* UEFI descriptor version, informational */

    u64 framebuffer_addr;            /* 0 unless PC5_BOOT64_F_FRAMEBUFFER is set */
    u32 framebuffer_width;
    u32 framebuffer_height;
    u32 framebuffer_pitch;           /* bytes per scan line */
    u32 framebuffer_format;          /* EFI_GRAPHICS_PIXEL_FORMAT value (0 = RGB, 1 = BGR) */

    u64 acpi_rsdp;                   /* 0 unless PC5_BOOT64_F_RSDP is set */

    u64 kernel_base;                 /* physical load address of this image */
    u64 kernel_size;                 /* bytes loaded, page-aligned up by the loader */
    u64 stack_top;                   /* physical address of the initial stack top */
    u64 stack_size;                  /* size of that stack, in bytes */

    u64 pagetable_addr;              /* physical address of the PML4 the kernel runs on */
    u64 pagetable_pages;             /* pages in that block: 2 + one PD page per GiB */
    u64 identity_span;               /* bytes the map covers, from 0; = (pages-2)*512*2MiB */

    u64 loader_image_addr;           /* physical base of the EFI application image */
    u64 loader_image_size;           /* its SizeOfImage */

    u64 exit_boot_services;          /* 1 when the loader already called ExitBootServices */
    u64 time_stamp;                  /* loader's monotonic count, informational only */
} __attribute__((packed));

#endif
