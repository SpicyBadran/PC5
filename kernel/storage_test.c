/*
 * PC5 kernel: ATA write-path self-test.
 *
 * This is the only code in PC5 that writes to a disk, and it runs only when an
 * operator asks for it (`wtest` on the serial console).  An ordinary boot never
 * writes anything, which is why a machine with an unknown disk can still be
 * diagnosed safely.
 *
 * What it proves, in order:
 *   1. the write API refuses every request it should refuse (zero count,
 *      out-of-range, overflow, invalid index, NULL buffer, ATAPI device);
 *   2. the scratch window policy is exact: one specific LBA range, 8 sectors,
 *      nothing adjacent;
 *   3. a deterministic 4 KiB pattern written to that window reads back
 *      byte-identical, with SHA-256 of the read-back in the log so a host can
 *      compare it with its own computation.
 *
 * What it does not prove: that any of this works on real hardware.  The device
 * is whatever ATA disk the firmware exposed; in the test harness that is a
 * disposable 8 MiB image, never a host disk.
 */
#include "pc5.h"

static u8 test_buf[WTEST_BYTES];
static char test_sha[65];
static int test_state;    /* 0 = not run, 1 = ok, -1 = failed */
static int test_device = -1;

/* Deterministic pattern, replicated byte-for-byte in tests/boot_test.py.  It is
 * deliberately not all-zeroes: a disk that ignores writes and returns its
 * previous contents must not pass. */
static u8 pattern_byte(u32 i) { return (u8)((i * 7u + 0x5au) & 0xffu); }

static void pattern_fill(u8 *p, u32 n)
{
    for (u32 i = 0; i < n; i++)
        p[i] = pattern_byte(i);
}

int storage_write_test_region_ok(u32 lba, u32 count)
{
    return lba == WTEST_SCRATCH_LBA && count == WTEST_SCRATCH_SECTORS;
}

int storage_selftest_state(void) { return test_state; }
const char *storage_selftest_sha256(void) { return test_sha; }
int storage_selftest_device(void) { return test_device; }

static void guard(const char *name, int got, int want)
{
    pc5_log("PC5-LOG storage_write_guard name=%s status=%s ok=%d", name,
            blk_status_name(got), got == want);
}

int storage_write_selftest(void)
{
    test_state = -1;
    test_sha[0] = 0;
    test_device = ata_writable_device();

    if (test_device < 0) {
        pc5_log("PC5-LOG storage_write_test=skipped reason=no-writable-ata");
        test_state = 0;
        return BLK_E_NO_DEVICE;
    }
    const struct ata_drive *d = ata_drive(test_device);
    if (!d || d->sectors <= WTEST_SCRATCH_LBA + WTEST_SCRATCH_SECTORS) {
        pc5_log("PC5-LOG storage_write_test=skipped reason=disk-too-small");
        test_state = 0;
        return BLK_E_RANGE;
    }

    pc5_log("PC5-LOG storage_write_device dev=%d model=%s sectors=%u writable=1",
            test_device, d->model, d->sectors);
    pc5_log("PC5-LOG storage_write_window lba=%u sectors=%u bytes=%u",
            WTEST_SCRATCH_LBA, WTEST_SCRATCH_SECTORS, WTEST_BYTES);

    /* ---- 1. requests that must be refused, before any valid write ---- */
    guard("zero_count", ata_write_sectors(test_device, WTEST_SCRATCH_LBA, 0, test_buf),
          BLK_E_COUNT);
    guard("out_of_range_lba", ata_write_sectors(test_device, d->sectors, 1, test_buf),
          BLK_E_RANGE);
    guard("past_end", ata_write_sectors(test_device, d->sectors - 1, 2, test_buf),
          BLK_E_RANGE);
    guard("lba_overflow", ata_write_sectors(test_device, 0xffffffffu, 1, test_buf),
          BLK_E_RANGE);
    guard("invalid_device", ata_write_sectors(ATA_MAX_DRIVES, WTEST_SCRATCH_LBA, 1, test_buf),
          BLK_E_NO_DEVICE);
    guard("negative_device", ata_write_sectors(-1, WTEST_SCRATCH_LBA, 1, test_buf),
          BLK_E_NO_DEVICE);
    guard("null_buffer", ata_write_sectors(test_device, WTEST_SCRATCH_LBA, 1, NULL),
          BLK_E_ALIGN);

    /* ---- 2. the scratch-window policy itself ---- */
    int policy = storage_write_test_region_ok(WTEST_SCRATCH_LBA, WTEST_SCRATCH_SECTORS) == 1 &&
                 storage_write_test_region_ok(WTEST_SCRATCH_LBA - 1, WTEST_SCRATCH_SECTORS) == 0 &&
                 storage_write_test_region_ok(WTEST_SCRATCH_LBA, WTEST_SCRATCH_SECTORS + 1) == 0 &&
                 storage_write_test_region_ok(WTEST_SCRATCH_LBA, WTEST_SCRATCH_SECTORS - 1) == 0;
    pc5_log("PC5-LOG storage_write_guard name=scratch_window status=%s ok=%d",
            policy ? "ok" : "bad", policy);

    /* ---- 3. an optical device is refused even when it is the boot device ---- */
    int cd = -1;
    for (int i = 0; i < ATA_MAX_DRIVES; i++) {
        const struct ata_drive *x = ata_drive(i);
        if (x && x->present && x->atapi) {
            cd = i;
            break;
        }
    }
    if (cd >= 0) {
        int rc = ata_write_sectors(cd, 0, 1, test_buf);
        guard("atapi_write", rc, BLK_E_READ_ONLY);
        pc5_log("PC5-LOG storage_write_cdrom dev=%d selected=%d status=%s refused=1",
                cd, ata_selected(), blk_status_name(rc));
    } else {
        pc5_log("PC5-LOG storage_write_cdrom skipped=1 reason=no-atapi-device");
    }

    /* No fault injection exists, so the timeout/IO branches are covered by the
     * unit tests of the request rules, not by a fabricated hardware error. */
    pc5_log("PC5-LOG storage_write_faults injected=0 reason=no-fault-injection");

    /* ---- 4. the actual write, read-back and verification ---- */
    pattern_fill(test_buf, sizeof(test_buf));
    int rc = ata_write_sectors(test_device, WTEST_SCRATCH_LBA, WTEST_SCRATCH_SECTORS, test_buf);
    if (rc != BLK_OK) {
        pc5_log("PC5-LOG storage_write=failed phase=write status=%s", blk_status_name(rc));
        return rc;
    }

    memset(test_buf, 0, sizeof(test_buf));
    rc = ata_read_sectors(test_device, WTEST_SCRATCH_LBA, WTEST_SCRATCH_SECTORS, test_buf);
    if (rc != BLK_OK) {
        pc5_log("PC5-LOG storage_write=failed phase=read status=%s", blk_status_name(rc));
        return rc;
    }

    u32 mismatch = 0xffffffffu;
    for (u32 i = 0; i < sizeof(test_buf); i++) {
        if (test_buf[i] != pattern_byte(i)) {
            mismatch = i;
            break;
        }
    }
    if (mismatch != 0xffffffffu) {
        pc5_log("PC5-LOG storage_write=failed phase=verify first_mismatch=%u", mismatch);
        return BLK_E_VERIFY;
    }

    struct sha256_ctx ctx;
    u8 digest[32];
    sha256_init(&ctx);
    sha256_update(&ctx, test_buf, sizeof(test_buf));
    sha256_final(&ctx, digest);
    sha256_hex(digest, test_sha);

    test_state = 1;
    pc5_log("PC5-LOG storage_write=ok dev=%d lba=%u sectors=%u bytes=%u verify=ok sha256=%s",
            test_device, WTEST_SCRATCH_LBA, WTEST_SCRATCH_SECTORS, WTEST_BYTES, test_sha);
    return BLK_OK;
}
