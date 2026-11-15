/*
 * PC5 kernel: block-device request rules.
 *
 * This file contains no port I/O on purpose.  Every decision that can be made
 * without touching hardware — is this device writable, is the range inside the
 * disk, does the arithmetic overflow, how much can one command carry — lives
 * here so the host test suite can prove it (`tests/kernel_unit.c`) instead of
 * trusting a driver that only ever sees one emulated disk.
 *
 * The unit split between 512-byte sectors and 2048-byte optical blocks is a
 * decision, not an accident: see ADR-013.
 */
#include "pc5.h"

const char *blk_status_name(int status)
{
    switch (status) {
    case BLK_OK: return "ok";
    case BLK_E_NO_DEVICE: return "no-device";
    case BLK_E_READ_ONLY: return "read-only";
    case BLK_E_RANGE: return "range";
    case BLK_E_COUNT: return "count";
    case BLK_E_ALIGN: return "alignment";
    case BLK_E_IO: return "io-error";
    case BLK_E_TIMEOUT: return "timeout";
    case BLK_E_VERIFY: return "verify";
    default: return "unknown";
    }
}

/* Validate one disk-sector request against a specific device.
 *
 * `capacity` is in 512-byte sectors.  Zero means "the device did not tell us",
 * which is never treated as "unlimited": reads and writes fail closed.
 * LBA28 addressing is the only mode implemented, so the range is additionally
 * bounded to 2^28 sectors. */
int blk_check_disk_request(u32 lba, u32 count, u32 capacity, int writable, int atapi)
{
    if (atapi && writable)
        return BLK_E_READ_ONLY;      /* optical media is never a write target */
    if (count == 0)
        return BLK_E_COUNT;
    if (capacity == 0)
        return BLK_E_RANGE;          /* capacity unknown: refuse, never guess */
    if (lba >= capacity)
        return BLK_E_RANGE;
    if (count > capacity - lba)      /* subtraction avoids lba+count overflow */
        return BLK_E_RANGE;
    if (count > (0x10000000u - lba))
        return BLK_E_RANGE;          /* beyond LBA28 */
    return BLK_OK;
}

u32 blk_chunk_sectors(u32 remaining, u32 max_per_command)
{
    if (!max_per_command || remaining <= max_per_command)
        return max_per_command ? remaining : 0;
    return max_per_command;
}
