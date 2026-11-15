/*
 * PC5 kernel: ATA/ATAPI PIO block driver.
 *
 * Polled PIO is not fast, but it is the smallest correct thing that can read a
 * real optical drive or disk, and it makes the boot medium readable from the
 * kernel instead of only from firmware.  That is what turns compatibility chain
 * stage 4 from "the loader proved the firmware can read the medium" into "the
 * operating environment has a storage stack".
 *
 * Supported: ATA PIO (LBA28) reads and writes for hard disks, and ATAPI
 * PACKET (READ(10)) reads for CD/DVD, on both legacy channels, master and
 * slave.  Writes are refused for every ATAPI device by construction — a
 * packet device has no WRITE SECTORS command here and must never be treated
 * as one.  No DMA, no interrupts, no AHCI/NVMe (see docs/ROADMAP.md).
 *
 * Two interfaces, deliberately not interchangeable (ADR-013):
 *   ata_read_blocks()  2048-byte optical/logical blocks on the *selected* boot
 *                      device — what ISO9660 has always used.
 *   ata_read_sectors() / ata_write_sectors()
 *                      512-byte disk sectors on an *explicitly indexed* device —
 *                      what a filesystem, installer or self-test uses.
 * Writes cannot reach the boot medium implicitly: there is no write call that
 * takes no device index.
 */
#include "pc5.h"

#define ATA_PRIMARY_IO 0x1f0
#define ATA_PRIMARY_CTL 0x3f6
#define ATA_SECONDARY_IO 0x170
#define ATA_SECONDARY_CTL 0x376

#define REG_DATA 0
#define REG_ERROR 1
#define REG_FEATURES 1
#define REG_SECTOR_COUNT 2
#define REG_LBA_LO 3
#define REG_LBA_MID 4
#define REG_LBA_HI 5
#define REG_DRIVE 6
#define REG_STATUS 7
#define REG_COMMAND 7

#define ST_ERR 0x01
#define ST_DRQ 0x08
#define ST_DF 0x20
#define ST_DRDY 0x40
#define ST_BSY 0x80

static struct ata_drive drives[4];
static int drive_count;
static int selected = -1;

static u8 status(u16 io) { return inb(io + REG_STATUS); }

static int wait_not_busy(u16 io, u32 timeout)
{
    for (u32 i = 0; i < timeout; i++) {
        u8 st = status(io);
        if (!(st & ST_BSY))
            return (st & ST_ERR) ? -1 : 0;
    }
    return -2;
}

static int wait_drq(u16 io, u32 timeout)
{
    for (u32 i = 0; i < timeout; i++) {
        u8 st = status(io);
        if (st & ST_ERR)
            return -1;
        if (!(st & ST_BSY) && (st & ST_DRQ))
            return 0;
    }
    return -2;
}

static void delay_400ns(u16 ctl)
{
    for (int i = 0; i < 4; i++)
        (void)inb(ctl);
}

/* 512-byte sector read on an ATA drive (LBA28) */
static int ata_read(u16 io, u16 ctl, u8 drive, u32 lba, u8 count, void *buf)
{
    u16 *out = (u16 *)buf;
    if (wait_not_busy(io, 1000000))
        return -1;
    outb(io + REG_DRIVE, (u8)(0xe0 | ((drive & 1) << 4) | ((lba >> 24) & 0x0f)));
    delay_400ns(ctl);
    if (wait_not_busy(io, 1000000))
        return -1;
    outb(io + REG_FEATURES, 0);
    outb(io + REG_SECTOR_COUNT, count);
    outb(io + REG_LBA_LO, (u8)(lba & 0xff));
    outb(io + REG_LBA_MID, (u8)((lba >> 8) & 0xff));
    outb(io + REG_LBA_HI, (u8)((lba >> 16) & 0xff));
    outb(io + REG_COMMAND, 0x20); /* READ SECTORS (with retry) */
    for (u8 s = 0; s < count; s++) {
        if (wait_drq(io, 1000000))
            return -1;
        for (int i = 0; i < 256; i++)
            *out++ = inw(io + REG_DATA);
        delay_400ns(ctl);
    }
    return 0;
}

/* ATAPI packet read: the medium uses 2048-byte logical blocks */
static int atapi_read(u16 io, u16 ctl, u8 drive, u32 lba, u8 count, void *buf)
{
    u16 *out = (u16 *)buf;
    u8 packet[12];
    memset(packet, 0, sizeof(packet));
    packet[0] = 0x28; /* READ (10) */
    packet[2] = (u8)((lba >> 24) & 0xff);
    packet[3] = (u8)((lba >> 16) & 0xff);
    packet[4] = (u8)((lba >> 8) & 0xff);
    packet[5] = (u8)(lba & 0xff);
    packet[7] = (u8)((count >> 8) & 0xff);
    packet[8] = (u8)(count & 0xff);

    if (wait_not_busy(io, 1000000))
        return -1;
    outb(io + REG_DRIVE, (u8)(0xa0 | ((drive & 1) << 4)));
    delay_400ns(ctl);
    outb(io + REG_FEATURES, 0);
    outb(io + REG_LBA_MID, (u8)(2048 & 0xff));
    outb(io + REG_LBA_HI, (u8)(2048 >> 8));
    outb(io + REG_COMMAND, 0xa0); /* PACKET */
    if (wait_drq(io, 1000000))
        return -2;
    for (int i = 0; i < 6; i++) {
        outw(io + REG_DATA, (u16)(packet[i * 2] | ((u16)packet[i * 2 + 1] << 8)));
    }
    for (u8 block = 0; block < count; block++) {
        if (wait_drq(io, 2000000))
            return -3;
        for (int i = 0; i < 1024; i++) /* 2048 bytes */
            *out++ = inw(io + REG_DATA);
        delay_400ns(ctl);
    }
    return 0;
}

/* Map a wait result to a block status without losing the difference between
 * "the device reported an error" and "the device never answered". */
static int ata_wait_status(int rc)
{
    return rc == -2 ? BLK_E_TIMEOUT : BLK_E_IO;
}

/* Write one bounded chunk of 512-byte sectors (LBA28) and flush the device
 * cache.  The caller has already validated the range and the device type. */
static int ata_write_chunk(u16 io, u16 ctl, u8 drive, u32 lba, u8 count, const void *buf)
{
    const u16 *in = (const u16 *)buf;

    int rc = wait_not_busy(io, 1000000);
    if (rc)
        return ata_wait_status(rc);
    outb(io + REG_DRIVE, (u8)(0xe0 | ((drive & 1) << 4) | ((lba >> 24) & 0x0f)));
    delay_400ns(ctl);
    rc = wait_not_busy(io, 1000000);
    if (rc)
        return ata_wait_status(rc);
    outb(io + REG_FEATURES, 0);
    outb(io + REG_SECTOR_COUNT, count);
    outb(io + REG_LBA_LO, (u8)(lba & 0xff));
    outb(io + REG_LBA_MID, (u8)((lba >> 8) & 0xff));
    outb(io + REG_LBA_HI, (u8)((lba >> 16) & 0xff));
    outb(io + REG_COMMAND, 0x30); /* WRITE SECTORS (with retry) */

    for (u8 s = 0; s < count; s++) {
        rc = wait_drq(io, 1000000);
        if (rc)
            return ata_wait_status(rc);
        for (int i = 0; i < 256; i++)
            outw(io + REG_DATA, *in++);
        delay_400ns(ctl);
    }

    /* FLUSH CACHE: the write is not durable until the device says it is, and a
     * read-back that skips this would be testing the device's write buffer.  A
     * device that reports ERR here (write-protect, media error) fails the whole
     * operation rather than returning BLK_OK. */
    rc = wait_not_busy(io, 1000000);
    if (rc)
        return ata_wait_status(rc);
    outb(io + REG_COMMAND, 0xe7);
    rc = wait_not_busy(io, 1000000);
    if (rc)
        return ata_wait_status(rc);
    return BLK_OK;
}

/* Wait until the device is not busy.  Returns 0 even if the error bit is set:
 * ATAPI devices signal "ATAPI present" through ERR plus the LBA mid/high
 * signature, so ERR must not be treated as "no device" here. */
static int wait_idle(u16 io, u32 timeout)
{
    for (u32 i = 0; i < timeout; i++) {
        u8 st = status(io);
        if (!(st & ST_BSY))
            return st;
    }
    return -1;
}

static void probe_channel(u16 io, u16 ctl)
{
    for (u8 drive = 0; drive < 2; drive++) {
        struct ata_drive *d = &drives[drive_count];
        memset(d, 0, sizeof(*d));
        d->io = io;
        d->ctl = ctl;
        d->drive = drive;

        outb(io + REG_DRIVE, (u8)(0xa0 | (drive << 4)));
        delay_400ns(ctl);
        delay_400ns(ctl);
        if (!status(io))
            continue; /* nothing on this port */
        (void)wait_idle(io, 1000000);

        outb(io + REG_SECTOR_COUNT, 0);
        outb(io + REG_LBA_LO, 0);
        outb(io + REG_LBA_MID, 0);
        outb(io + REG_LBA_HI, 0);
        outb(io + REG_COMMAND, 0xec); /* IDENTIFY DEVICE */
        delay_400ns(ctl);
        delay_400ns(ctl);

        u8 mid = inb(io + REG_LBA_MID);
        u8 hi = inb(io + REG_LBA_HI);
        if (mid == 0x14 && hi == 0xeb) {
            d->atapi = 1;
        } else if (mid || hi) {
            continue; /* SATA/other signature: not handled */
        }

        if (!d->atapi && (wait_idle(io, 1000000) & ST_ERR))
            continue;

        if (d->atapi) {
            /* IDENTIFY PACKET DEVICE; a packet device rejects the plain
             * IDENTIFY above, so this is the command that answers. */
            outb(io + REG_COMMAND, 0xa1);
            delay_400ns(ctl);
        }

        if (wait_idle(io, 1000000) < 0)
            continue;
        if (!(status(io) & ST_DRQ))
            continue;

        u16 id[256];
        for (int i = 0; i < 256; i++)
            id[i] = inw(io + REG_DATA);
        for (int i = 0; i < 20; i++) {
            d->model[i * 2] = (char)(id[27 + i] >> 8);
            d->model[i * 2 + 1] = (char)(id[27 + i] & 0xff);
        }
        d->model[40] = 0;
        for (int i = 39; i > 0 && d->model[i] == ' '; i--)
            d->model[i] = 0;
        if (!d->atapi)
            d->sectors = (u32)id[60] | ((u32)id[61] << 16);
        d->present = 1;
        drive_count++;
    }
}

int ata_init(void)
{
    drive_count = 0;
    selected = -1;
    probe_channel(ATA_PRIMARY_IO, ATA_PRIMARY_CTL);
    probe_channel(ATA_SECONDARY_IO, ATA_SECONDARY_CTL);
    for (int i = 0; i < drive_count; i++) {
        if (selected < 0 && drives[i].atapi)
            selected = i;
    }
    if (selected < 0 && drive_count)
        selected = 0;
    return drive_count;
}

int ata_selected(void) { return selected; }

const struct ata_drive *ata_drive(int index)
{
    if (index < 0 || index >= drive_count)
        return NULL;
    return &drives[index];
}

/* Read 2048-byte logical blocks from the selected drive. */
int ata_read_blocks(u32 lba, u32 count, void *buf)
{
    if (selected < 0)
        return -1;
    const struct ata_drive *d = &drives[selected];
    u8 *out = (u8 *)buf;
    while (count) {
        u32 chunk = count > 8 ? 8 : count;
        int rc;
        if (d->atapi) {
            rc = atapi_read(d->io, d->ctl, d->drive, lba, (u8)chunk, out);
            lba += chunk;
            out += chunk * 2048;
        } else {
            /* a 2048-byte block is four 512-byte sectors */
            rc = ata_read(d->io, d->ctl, d->drive, lba * 4, (u8)(chunk * 4), out);
            lba += chunk;
            out += chunk * 2048;
        }
        if (rc)
            return rc;
        count -= chunk;
    }
    return 0;
}

/* ------------------------------------------------------- disk sectors ---- */

/* The first ATA (non-ATAPI) device that reported a capacity, or -1.  This is
 * the "explicit target" for writes: it is never the selected boot device by
 * accident, because selection prefers ATAPI. */
int ata_writable_device(void)
{
    for (int i = 0; i < drive_count; i++) {
        if (!drives[i].atapi && drives[i].sectors > 0)
            return i;
    }
    return -1;
}

int ata_read_sectors(int device, u32 lba, u32 count, void *buf)
{
    if (device < 0 || device >= drive_count || !drives[device].present)
        return BLK_E_NO_DEVICE;
    struct ata_drive *d = &drives[device];
    if (d->atapi)
        return BLK_E_RANGE; /* optical media has no 512-byte sector API */
    if (!buf)
        return BLK_E_ALIGN;
    int rc = blk_check_disk_request(lba, count, d->sectors, 0, 0);
    if (rc != BLK_OK)
        return rc;

    u8 *out = (u8 *)buf;
    while (count) {
        u32 chunk = blk_chunk_sectors(count, ATA_MAX_SECTORS_PER_CMD);
        if (ata_read(d->io, d->ctl, d->drive, lba, (u8)chunk, out))
            return BLK_E_IO;
        lba += chunk;
        count -= chunk;
        out += chunk * PC5_SECTOR_SIZE;
    }
    return BLK_OK;
}

int ata_write_sectors(int device, u32 lba, u32 count, const void *buf)
{
    if (device < 0 || device >= drive_count || !drives[device].present)
        return BLK_E_NO_DEVICE;
    struct ata_drive *d = &drives[device];
    if (!buf || ((u32)buf & 1u))
        return BLK_E_ALIGN;

    int rc = blk_check_disk_request(lba, count, d->sectors, 1, d->atapi);
    if (rc != BLK_OK)
        return rc;

    const u8 *in = (const u8 *)buf;
    while (count) {
        u32 chunk = blk_chunk_sectors(count, ATA_MAX_SECTORS_PER_CMD);
        rc = ata_write_chunk(d->io, d->ctl, d->drive, lba, (u8)chunk, in);
        if (rc != BLK_OK)
            return rc;
        lba += chunk;
        count -= chunk;
        in += chunk * PC5_SECTOR_SIZE;
    }
    return BLK_OK;
}

/* FLUSH CACHE on an explicit device.  ata_write_sectors() already ends every
 * command with a flush, so this exists for callers that want a separate
 * durability barrier *after* a sequence of writes (FAT32 create does: it flushes
 * once the directory entry is on the medium).  A device that reports ERR here,
 * or never answers, fails the call: "the write returned" is not "the data is
 * durable".  ATAPI is refused, as everywhere else on this path. */
int ata_flush(int device)
{
    if (device < 0 || device >= drive_count || !drives[device].present)
        return BLK_E_NO_DEVICE;
    struct ata_drive *d = &drives[device];
    if (d->atapi)
        return BLK_E_READ_ONLY;

    int rc = wait_not_busy(d->io, 1000000);
    if (rc)
        return ata_wait_status(rc);
    outb(d->io + REG_COMMAND, 0xe7); /* FLUSH CACHE */
    rc = wait_not_busy(d->io, 1000000);
    if (rc)
        return ata_wait_status(rc);
    return BLK_OK;
}
