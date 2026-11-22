/*
 * PC5 UEFI loader: the slice of the UEFI specification this loader uses.
 *
 * Only three things are needed to boot a kernel from firmware: console
 * output, a linear framebuffer, and a way to read the file we came from.
 * Everything else in UEFI (events, protocols beyond these, runtime services)
 * is deliberately absent: the loader runs once, hands over, and never
 * returns.  Field offsets follow the UEFI 2.10 specification and are pinned
 * by _Static_assert, so a wrong guess is a compile error rather than a
 * machine check at boot.
 *
 * ABI notes (these are the two mistakes that break hand-written UEFI
 * binaries most often, so they are spelled out):
 *
 *   - firmware calls into this application with the Microsoft x64 calling
 *     convention, so `efi_main` is declared __attribute__((ms_abi)); the
 *     entry stub in bootx64.S reads the two arguments out of RCX and RDX.
 *   - when the loader calls back into firmware it must do the same, from
 *     code that never lets the compiler assume a Linux red zone.
 */
#ifndef PC5_EFI_H
#define PC5_EFI_H

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef signed int s32;
typedef signed long long s64;
typedef signed short s16;

typedef u64 efi_status;
typedef void *efi_handle;
struct efi_system_table;
struct efi_device_path;

#define EFIAPI __attribute__((ms_abi))

#define EFI_SUCCESS 0ULL
#define EFI_LOAD_ERROR 0x8000000000000001ULL
#define EFI_INVALID_PARAMETER 0x8000000000000002ULL
#define EFI_UNSUPPORTED 0x8000000000000003ULL
#define EFI_BUFFER_TOO_SMALL 0x8000000000000005ULL
#define EFI_NOT_FOUND 0x800000000000000EULL
#define EFI_OUT_OF_RESOURCES 0x8000000000000009ULL

#define EFI_ERROR(status) ((s64)(status) < 0)
#define EFI_WARN_UNKNOWN_GLYPH 1ULL
#define EFI_WARN_DELETE_FAILURE 2ULL

/* ------------------------------------------------------------- basics */
struct efi_table_header {
    u64 signature;
    u32 revision;
    u32 header_size;
    u32 crc32;
    u32 reserved;
};

struct efi_guid {
    u32 data1;
    u16 data2;
    u16 data3;
    u8 data4[8];
};

struct efi_time {
    u16 year;
    u8 month, day, hour, minute, second, pad1;
    u32 nanosecond;
    s16 timezone;
    u8 daylight;
    u8 pad2;
};

/* Text output: Simple Text Output Protocol. */
struct efi_simple_text_output {
    void *reset;
    efi_status(EFIAPI *output_string)(struct efi_simple_text_output *self, u16 *string);
    void *test_string;
    void *query_mode;
    void *set_mode;
    void *set_attribute;
    void *clear_screen;
    void *set_cursor_position;
    void *enable_cursor;
    void *mode;
};

/* Graphics: Graphics Output Protocol, mode 0 is always 32-bit line drawing. */
struct efi_gop_mode_info {
    u32 version;
    u32 horizontal_resolution;
    u32 vertical_resolution;
    u32 pixel_format;      /* 0 = RGB, 1 = BGR */
    u32 pixel_information[4];
    u32 pixels_per_scan_line;
};

struct efi_gop_mode {
    u32 max_mode;
    u32 mode;
    struct efi_gop_mode_info *info;
    u64 size_of_info;
    u64 frame_buffer_base;
    u64 frame_buffer_size;
};

struct efi_gop {
    efi_status(EFIAPI *query_mode)(struct efi_gop *self, u32 mode, u64 *info_size,
                                   struct efi_gop_mode_info **info);
    efi_status(EFIAPI *set_mode)(struct efi_gop *self, u32 mode);
    void *blt;
    struct efi_gop_mode *mode;
};

/* Files: Simple File System Protocol, File Protocol and the identity GUIDs
 * used to open the volume the application was loaded from. */
struct efi_file;
struct efi_simple_file_system {
    u64 revision;
    efi_status(EFIAPI *open_volume)(struct efi_simple_file_system *self,
                                    struct efi_file **root);
};

struct efi_file {
    u64 revision;
    efi_status(EFIAPI *open)(struct efi_file *self, struct efi_file **new_handle,
                             u16 *file_name, u64 open_mode, u64 attributes);
    efi_status(EFIAPI *close)(struct efi_file *self);
    efi_status(EFIAPI *delete_file)(struct efi_file *self);
    efi_status(EFIAPI *read)(struct efi_file *self, u64 *buffer_size, void *buffer);
    efi_status(EFIAPI *write)(struct efi_file *self, u64 *buffer_size, void *buffer);
    efi_status(EFIAPI *get_position)(struct efi_file *self, u64 *position);
    efi_status(EFIAPI *set_position)(struct efi_file *self, u64 position);
    efi_status(EFIAPI *get_info)(struct efi_file *self, struct efi_guid *info_type,
                                 u64 *buffer_size, void *buffer);
    efi_status(EFIAPI *set_info)(struct efi_file *self, struct efi_guid *info_type,
                                 u64 buffer_size, void *buffer);
    efi_status(EFIAPI *flush)(struct efi_file *self);
};

/* EFI_FILE_INFO: the only way the file protocol reports a size.  Firmware
 * accepts a SetPosition beyond EOF and reports that same position back, so the
 * "seek to the end to learn the size" trick returns the constant it was given,
 * not the file size - OVMF does exactly that. */
struct efi_file_info {
    u64 size;                 /* structure size, filled in by firmware */
    u64 file_size;
    u64 physical_size;
    u64 create_time[2];
    u64 last_access_time[2];
    u64 modification_time[2];
    u64 attribute;
    u16 file_name[1];
} __attribute__((packed));

#define EFI_FILE_MODE_READ 0x0000000000000001ULL
#define EFI_FILE_MODE_WRITE 0x0000000000000002ULL
#define EFI_FILE_MODE_CREATE 0x8000000000000000ULL
#define EFI_FILE_DIRECTORY 0x0000000000000010ULL

/* Memory: allocation types and the descriptor layout of the memory map. */
#define EFI_ALLOCATE_ANY_PAGES 0
#define EFI_ALLOCATE_MAX_ADDRESS 1
#define EFI_ALLOCATE_ADDRESS 2
#define EFI_LOADER_DATA 2
#define EFI_LOADER_CODE 1
#define EFI_CONVENTIONAL_MEMORY 7

struct efi_memory_descriptor {
    u32 type;
    u32 pad;
    u64 physical_start;
    u64 virtual_start;
    u64 number_of_pages;
    u64 attribute;
};

/* Runtime-ish services the loader needs after ExitBootServices: none, but
 * allocating the kernel buffer and keeping the memory map valid needs
 * AllocatePool, AllocatePages, FreePool, GetMemoryMap and the two timer
 * services used to back off while polling. */
/* Boot services in specification order.  The loader only calls a handful of
 * them, but every slot has to be the right width so the offsets stay correct:
 * `void *` is 8 bytes on x86-64, which is why the unused slots are harmless,
 * and the used ones carry their real prototype. */
struct efi_boot_services {
    struct efi_table_header header;
    void *raise_tpl;                       /* +24  */
    void *restore_tpl;                     /* +32  */
    efi_status(EFIAPI *allocate_pages)(u32 type, u32 memory_type, u64 pages,
                                       u64 *memory);                 /* +40  */
    efi_status(EFIAPI *free_pages)(u64 memory, u64 pages);           /* +48  */
    efi_status(EFIAPI *get_memory_map)(u64 *map_size,
                                       struct efi_memory_descriptor *map,
                                       u64 *map_key, u64 *descriptor_size,
                                       u32 *descriptor_version);     /* +56  */
    efi_status(EFIAPI *allocate_pool)(u32 pool_type, u64 size, void **buffer);
    efi_status(EFIAPI *free_pool)(void *buffer);
    void *create_event;
    void *set_timer;
    void *wait_for_event;
    void *signal_event;
    void *close_event;
    void *check_event;
    void *install_protocol_interface;
    void *reinstall_protocol_interface;
    void *uninstall_protocol_interface;
    efi_status(EFIAPI *handle_protocol)(efi_handle handle, struct efi_guid *protocol,
                                        void **interface);            /* +152 */
    void *reserved;
    void *register_protocol_notify;
    /* LocateHandle, in the order UEFI 2.10 7.3.6 defines it:
     *
     *     LocateHandle(SearchType, Protocol, SearchKey, BufferSize, Buffer)
     *
     * SearchType comes FIRST and BufferSize FOURTH.  This declaration used to
     * have BufferSize and SearchType swapped, and every caller was written to
     * match, so the two errors cancelled inside this tree and the stub firmware
     * - which mirrors this header - agreed with them.
     *
     * Under real OVMF the consequence was EFI_INVALID_PARAMETER on every call:
     * the size pointer's address arrived as SearchType, and SearchType 2 arrived
     * as the BufferSize pointer.  Volume enumeration never worked on genuine
     * firmware in either the 32-bit or the 64-bit loader; the 64-bit boot died
     * as "kernel-not-found" with the kernel on the volume it had just been
     * loaded from.
     *
     * SearchType 2 = ByProtocol; the protocol is passed in `Protocol`, which is
     * the second argument and non-NULL for that search type. */
    efi_status(EFIAPI *locate_handle)(u32 search_type, struct efi_guid *protocol,
                                      void *search_key, u64 *buffer_size,
                                      efi_handle *buffer);
    efi_status(EFIAPI *locate_device_path)(struct efi_guid *protocol,
                                           struct efi_device_path **device_path,
                                           efi_handle *device);       /* +184 */
    void *install_configuration_table;
    void *load_image;
    void *start_image;
    void *exit;
    void *unload_image;
    efi_status(EFIAPI *exit_boot_services)(efi_handle image, u64 map_key);
    void *get_next_monotonic_count;
    void *stall;
    efi_status(EFIAPI *set_watchdog_timer)(u64 timeout_seconds, u64 watchdog_code,
                                            u64 data_size, void *data);  /* +256 */
    /* The rest are never called; they are named only to keep the structure
     * complete, so a future caller cannot silently read the wrong slot. */
    efi_status(EFIAPI *connect_controller)(efi_handle, void *, void *, u32);
    efi_status(EFIAPI *disconnect_controller)(efi_handle, efi_handle, efi_handle);
    efi_status(EFIAPI *open_protocol)(efi_handle handle, struct efi_guid *protocol,
                                      void **interface, efi_handle agent,
                                      efi_handle controller, u32 attributes);
    efi_status(EFIAPI *close_protocol)(efi_handle handle, struct efi_guid *protocol,
                                       efi_handle agent, efi_handle controller);
    efi_status(EFIAPI *open_protocol_information)(void);
    efi_status(EFIAPI *protocols_per_handle)(void);
    efi_status(EFIAPI *locate_handle_buffer)(void);
};

struct efi_runtime_services {
    struct efi_table_header header;
    /* the loader uses none of these; the pointer is kept for the trace */
};

struct efi_system_table {
    struct efi_table_header header;
    u16 *firmware_vendor;
    u32 firmware_revision;
    efi_handle console_in_handle;
    void *con_in;
    efi_handle console_out_handle;
    struct efi_simple_text_output *con_out;
    efi_handle standard_error_handle;
    struct efi_simple_text_output *std_err;
    struct efi_runtime_services *runtime_services;
    struct efi_boot_services *boot_services;
    u64 number_of_table_entries;
    void *configuration_table;
};

/* Device paths: a generic node, and the media-device node that ends the path
 * of any block device carrying a file system. */
#define EFI_DEVICE_PATH_TYPE_HARDWARE 1
#define EFI_DEVICE_PATH_TYPE_ACPI 2
#define EFI_DEVICE_PATH_TYPE_MESSAGING 3
#define EFI_DEVICE_PATH_TYPE_MEDIA 4
#define EFI_DEVICE_PATH_TYPE_END 0x7F
#define EFI_DEVICE_PATH_SUBTYPE_MEDIA_CDROM 2
#define EFI_DEVICE_PATH_SUBTYPE_END_ENTIRE 0xFF
#define EFI_DEVICE_PATH_SUBTYPE_END_INSTANCE 0x01

struct efi_device_path {
    u8 type;
    u8 sub_type;
    u16 length;            /* whole node, header included */
} __attribute__((packed));

struct efi_device_path_media {
    struct efi_device_path header;
    u8 reserved;
} __attribute__((packed));

struct efi_file_path {
    void *type;            /* EFI_GUID * */
    u16 path_name[1];
};

/* What firmware tells an application about itself: the device it was loaded
 * from, and (for a boot option) the path of the file it was loaded from. */
struct efi_loaded_image {
    u32 revision;
    efi_handle parent_handle;
    struct efi_system_table *system_table;
    efi_handle device_handle;
    struct efi_file_path *file_path;
    void *reserved;
    u32 load_options_size;
    void *load_options;
    void *image_base;
    u64 image_size;
    u32 image_code_type;
    u32 image_data_type;
    efi_status(EFIAPI *unload)(efi_handle image_handle);
};

/* ------------------------------------------------------- protocol GUIDs */
#define GUID(a, b, c, d0, d1, d2, d3, d4, d5, d6, d7) \
    { 0x##a, 0x##b, 0x##c, { 0x##d0, 0x##d1, 0x##d2, 0x##d3, 0x##d4, 0x##d5, 0x##d6, 0x##d7 } }

/* 9042a9de-23dc-4a38-96fb-7aded080516a */
static const struct efi_guid EFI_GRAPHICS_OUTPUT_PROTOCOL_GUID =
    GUID(9042a9de, 23dc, 4a38, 96, fb, 7a, de, d0, 80, 51, 6a);
/* 09576e92-6d3f-11d2-8e39-00a0c969723b */
static const struct efi_guid EFI_FILE_INFO_GUID =
    GUID(09576e92, 6d3f, 11d2, 8e, 39, 00, a0, c9, 69, 72, 3b);
/* 964e5b22-6459-11d2-8e39-00a0c969723b */
static const struct efi_guid EFI_SIMPLE_FILE_SYSTEM_PROTOCOL_GUID =
    GUID(964e5b22, 6459, 11d2, 8e, 39, 00, a0, c9, 69, 72, 3b);
/* 5b1b31a1-9562-11d2-8e3f-00a0c969723b: the image handle's own description,
 * which names the device the application was loaded from. */
static const struct efi_guid EFI_LOADED_IMAGE_PROTOCOL_GUID =
    GUID(5b1b31a1, 9562, 11d2, 8e, 3f, 00, a0, c9, 69, 72, 3b);
/* 09576e91-6d3f-11d2-8e39-00a0c969723b */
static const struct efi_guid EFI_DEVICE_PATH_PROTOCOL_GUID =
    GUID(09576e91, 6d3f, 11d2, 8e, 39, 00, a0, c9, 69, 72, 3b);
/* open_protocol attributes used below. */
#define EFI_OPEN_PROTOCOL_GET_PROTOCOL 0x00000002
#define EFI_OPEN_PROTOCOL_BY_HANDLE_PROTOCOL 0x00000001

/* Search types for LocateHandle.  0x02 is EFI_LOCATE_BY_PROTOCOL: "every handle
 * that supports this protocol", and with a NULL protocol, every handle at all -
 * which is what this loader wants (it filters by HandleProtocol itself). */
#define EFI_LOCATE_BY_PROTOCOL 0x02

#endif /* PC5_EFI_H */
