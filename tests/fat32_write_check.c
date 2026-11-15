/*
 * Host-side driver for the FAT32 *write* path in kernel/fat32.c.
 *
 * It loads a disk image into memory, creates one file through the kernel's own
 * `fat32_create_file()`, and writes the resulting image back out so the Python
 * suite can inspect it with a completely independent FAT32 reader.  Nothing
 * here re-implements FAT32: the sector callbacks are the only thing this file
 * provides, plus the fault injector below, which is what makes the failure
 * paths testable without a power switch.
 *
 * Build (the test does this):
 *   gcc -DPC5_HOST_TEST -Ikernel/include -o build/test/fat32-write-check \
 *       tests/fat32_write_check.c kernel/fat32.c kernel/sha256.c \
 *       kernel/lib.c kernel/mem.c kernel/chain.c
 *
 * Usage:
 *   fat32-write-check IMAGE BASE_LBA SECTORS PATH SIZE --out FILE [options]
 *
 * PATH may be a path ("DIR/FILE.TXT"), not just a name: kernel/fat32.c resolves
 * it.  The operation is `create` unless `--overwrite` or `--mkdir` is given.
 *
 * Options:
 *   --text TEXT          payload is TEXT (size must match, or be omitted=0)
 *   --pattern            payload is the deterministic pattern (default)
 *   --out FILE           write the (possibly modified) image here
 *   --fail-write N       fail the N-th write call (0-based), image untouched
 *   --fail-write-after N fail every write call from N on
 *   --corrupt-write N    flip one byte in the N-th write before storing it
 *   --fail-flush         the flush callback reports failure
 *   --no-flush           no flush callback at all (a source that cannot flush)
 *   --read-only-io       no write callback at all (a read-only source)
 *   --lba-log FILE       append every written LBA to FILE, one per line
 *   --overwrite          replace the content of an existing file (M1i)
 *   --mkdir              create a directory (M1i)
 *   --read               only read PATH through the kernel's read path and
 *                        report its status; nothing is written (M1i)
 *   --fail-lba LBA       fail the first write whose LBA equals LBA
 *   --fail-fat-copy N    fail the first write that lands in FAT copy N

 * Exit status: 0 when the operation returned FAT32_OK, 1 when it returned an
 * error, 2 on a usage or I/O problem.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pc5.h"

struct image {
    unsigned char *data;
    long bytes;
};

struct sink {
    struct image *img;
    long writes;
    long fail_write;         /* -1: never */
    long fail_write_after;   /* -1: never */
    long corrupt_write;      /* -1: never */
    int fail_flush;
    FILE *lba_log;
    /* M1i: region-targeted injection, so a fault lands in the *kind* of write
     * the test means (a FAT mirror, the switch of the directory entry) instead
     * of at a position the test has to guess from a write count. */
    long fail_lba;           /* -1: never; first write to this LBA fails */
    int fail_fat_copy;       /* -1: never; first write into this FAT copy fails */
    unsigned long base_lba;
    unsigned long reserved;
    unsigned long fat_sectors;
    unsigned long fat_count;
    int hit_lba, hit_fat;
};

static int bounded(const struct image *img, u32 lba, u32 count, long *off, long *len)
{
    long o = (long)lba * 512L;
    long n = (long)count * 512L;
    if (lba > 0x0FFFFFFFu || count == 0 || count > 0x10000000u)
        return -1;
    if (o < 0 || n < 0 || o + n > img->bytes)
        return -1;
    *off = o;
    *len = n;
    return 0;
}

static int mem_read(void *ctx, u32 lba, u32 count, void *buf)
{
    struct sink *s = (struct sink *)ctx;
    struct image *img = s->img;
    long off, len;
    if (bounded(img, lba, count, &off, &len))
        return -1;
    memcpy(buf, img->data + off, (size_t)len);
    return 0;
}

static int mem_write(void *ctx, u32 lba, u32 count, const void *buf)
{
    struct sink *s = (struct sink *)ctx;
    struct image *img = s->img;
    long off, len;
    if (bounded(img, lba, count, &off, &len))
        return -1;
    long index = s->writes++;
    if (s->lba_log)
        fprintf(s->lba_log, "%u %u\n", lba, count);
    if (s->fail_lba >= 0 && !s->hit_lba && (unsigned long)lba == (unsigned long)s->fail_lba) {
        s->hit_lba = 1;
        return -1;
    }
    if (s->fail_fat_copy >= 0 && !s->hit_fat) {
        unsigned long copy = (unsigned long)lba - s->base_lba - s->reserved;
        if ((long)copy >= 0 && (unsigned long)lba >= s->base_lba + s->reserved &&
            copy / s->fat_sectors == (unsigned long)s->fail_fat_copy &&
            copy / s->fat_sectors < s->fat_count) {
            s->hit_fat = 1;
            return -1;
        }
    }
    if (s->fail_write >= 0 && index == s->fail_write)
        return -1;
    if (s->fail_write_after >= 0 && index >= s->fail_write_after)
        return -1;
    memcpy(img->data + off, buf, (size_t)len);
    if (s->corrupt_write >= 0 && index == s->corrupt_write && len > 0)
        img->data[off + (len / 2)] ^= 0xFF;   /* stored, but not what was sent */
    return 0;
}

static int mem_flush(void *ctx)
{
    struct sink *s = (struct sink *)ctx;
    return s->fail_flush ? -1 : 0;
}

static unsigned char pattern_byte(long i) { return (unsigned char)((i * 17 + 5) & 0xFF); }

int main(int argc, char **argv)
{
    if (argc < 6) {
        fprintf(stderr,
                "usage: %s IMAGE BASE_LBA SECTORS NAME SIZE --out FILE [options]\n",
                argv[0]);
        return 2;
    }

    const char *image_path = argv[1];
    u32 base = (u32)strtoul(argv[2], NULL, 0);
    u32 sectors = (u32)strtoul(argv[3], NULL, 0);
    const char *name = argv[4];
    long long size_arg = strtoll(argv[5], NULL, 0);

    const char *text = NULL;
    const char *out_path = NULL;
    const char *lba_path = NULL;
    long fail_write = -1, fail_write_after = -1, corrupt_write = -1;
    long fail_lba = -1, fail_fat_copy = -1;
    int fail_flush = 0, no_flush = 0, read_only_io = 0;
    int op = 0;                   /* 0 create, 1 overwrite, 2 mkdir, 3 read */

    for (int i = 6; i < argc; i++) {
        if (!strcmp(argv[i], "--text") && i + 1 < argc) {
            text = argv[++i];
        } else if (!strcmp(argv[i], "--pattern")) {
            text = NULL;
        } else if (!strcmp(argv[i], "--out") && i + 1 < argc) {
            out_path = argv[++i];
        } else if (!strcmp(argv[i], "--fail-write") && i + 1 < argc) {
            fail_write = strtol(argv[++i], NULL, 0);
        } else if (!strcmp(argv[i], "--fail-write-after") && i + 1 < argc) {
            fail_write_after = strtol(argv[++i], NULL, 0);
        } else if (!strcmp(argv[i], "--corrupt-write") && i + 1 < argc) {
            corrupt_write = strtol(argv[++i], NULL, 0);
        } else if (!strcmp(argv[i], "--fail-flush")) {
            fail_flush = 1;
        } else if (!strcmp(argv[i], "--no-flush")) {
            no_flush = 1;
        } else if (!strcmp(argv[i], "--read-only-io")) {
            read_only_io = 1;
        } else if (!strcmp(argv[i], "--lba-log") && i + 1 < argc) {
            lba_path = argv[++i];
        } else if (!strcmp(argv[i], "--overwrite")) {
            op = 1;
        } else if (!strcmp(argv[i], "--mkdir")) {
            op = 2;
        } else if (!strcmp(argv[i], "--read")) {
            op = 3;
        } else if (!strcmp(argv[i], "--fail-lba") && i + 1 < argc) {
            fail_lba = strtol(argv[++i], NULL, 0);
        } else if (!strcmp(argv[i], "--fail-fat-copy") && i + 1 < argc) {
            fail_fat_copy = strtol(argv[++i], NULL, 0);
        } else {
            fprintf(stderr, "unknown option: %s\n", argv[i]);
            return 2;
        }
    }

    FILE *fh = fopen(image_path, "rb");
    if (!fh) {
        perror(image_path);
        return 2;
    }
    if (fseek(fh, 0, SEEK_END) != 0) {
        fclose(fh);
        return 2;
    }
    long bytes = ftell(fh);
    rewind(fh);
    struct image img;
    img.bytes = bytes;
    img.data = (unsigned char *)malloc((size_t)bytes);
    if (!img.data || fread(img.data, 1, (size_t)bytes, fh) != (size_t)bytes) {
        fprintf(stderr, "cannot read image\n");
        fclose(fh);
        free(img.data);
        return 2;
    }
    fclose(fh);

    unsigned char *payload = NULL;
    u32 size = 0;
    if (op == 3) {
        /* read-only probe: the payload is irrelevant and SIZE is ignored */
        payload = (unsigned char *)malloc(1);
    } else if (text) {
        size = (u32)strlen(text);
        if (size_arg > 0 && (u32)size_arg != size) {
            fprintf(stderr, "--text length %u does not match SIZE %lld\n", size, size_arg);
            free(img.data);
            return 2;
        }
        payload = (unsigned char *)malloc(size ? size : 1);
        memcpy(payload, text, size);
    } else {
        if (size_arg < 0 || size_arg > 0x7FFFFFFF) {
            fprintf(stderr, "bad SIZE\n");
            free(img.data);
            return 2;
        }
        size = (u32)size_arg;
        payload = (unsigned char *)malloc(size ? size : 1);
        for (u32 i = 0; i < size; i++)
            payload[i] = pattern_byte(i);
    }

    struct sink sink;
    memset(&sink, 0, sizeof(sink));
    sink.img = &img;
    sink.fail_write = fail_write;
    sink.fail_write_after = fail_write_after;
    sink.corrupt_write = corrupt_write;
    sink.fail_flush = fail_flush;
    sink.fail_lba = fail_lba;
    sink.fail_fat_copy = fail_fat_copy;
    sink.base_lba = base;
    sink.reserved = 0;
    sink.fat_sectors = 0;
    sink.fat_count = 0;
    if (lba_path) {
        sink.lba_log = fopen(lba_path, "w");
        if (!sink.lba_log) {
            perror(lba_path);
            free(img.data);
            free(payload);
            return 2;
        }
    }

    struct fat32_io io;
    memset(&io, 0, sizeof(io));
    io.read = mem_read;
    io.ctx = &sink;
    if (!read_only_io) {
        io.write = mem_write;
        if (!no_flush)
            io.flush = mem_flush;
    }

    struct fat32_volume vol;
    int rc = fat32_mount(&io, base, sectors, &vol);
    printf("mount status=%s base=%u sectors=%u clusters=%u\n",
           fat32_status_name(rc), base, sectors, vol.cluster_count);
    if (rc != FAT32_OK) {
        if (out_path) {
            FILE *out = fopen(out_path, "wb");
            if (out) {
                fwrite(img.data, 1, (size_t)bytes, out);
                fclose(out);
            }
        }
        if (sink.lba_log)
            fclose(sink.lba_log);
        free(img.data);
        free(payload);
        return 1;
    }

    sink.reserved = vol.reserved_sectors;
    sink.fat_sectors = vol.fat_sectors;
    sink.fat_count = vol.fat_count;

    if (op == 3) {
        /* The kernel's read path, with no write callback attached at all: this
         * is the same question the shell's `read` command asks. */
        u32 len = 0;
        unsigned char *back = (unsigned char *)malloc(1u << 20);
        int rc_read = FAT32_E_IO;
        if (back)
            rc_read = fat32_read_file(&io, &vol, name, back, 1u << 20, &len);
        char hex[65] = "-";
        if (back && rc_read == FAT32_OK) {
            struct sha256_ctx sctx;
            unsigned char digest[32];
            sha256_init(&sctx);
            sha256_update(&sctx, back, len);
            sha256_final(&sctx, digest);
            sha256_hex(digest, hex);
        }
        printf("read path=%s status=%s size=%u sha256=%s writes=%ld\n",
               name, fat32_status_name(rc_read), rc_read == FAT32_OK ? len : 0,
               hex, sink.writes);
        free(back);
        if (out_path) {
            FILE *out = fopen(out_path, "wb");
            if (out) {
                fwrite(img.data, 1, (size_t)bytes, out);
                fclose(out);
            }
        }
        if (sink.lba_log)
            fclose(sink.lba_log);
        free(img.data);
        free(payload);
        return rc_read == FAT32_OK ? 0 : 1;
    }

    struct fat32_write_report rep;
    memset(&rep, 0, sizeof(rep));
    int rc_op;
    const char *op_name = op == 1 ? "overwrite" : op == 2 ? "mkdir" : "create";
    if (op == 1)
        rc_op = fat32_overwrite_file(&io, &vol, name, payload, size, &rep);
    else if (op == 2)
        rc_op = fat32_create_dir(&io, &vol, name, &rep);
    else
        rc_op = fat32_create_file(&io, &vol, name, payload, size, &rep);

    printf("%s path=%s status=%s stage=%d size=%u clusters=%u first=%u "
           "old_first=%u old_clusters=%u freed=%u grew=%d grown=%u "
           "dir_sector=%u dir_offset=%u free_before=%u free_after=%u "
           "fsinfo_valid=%d fsinfo_claimed=%u fsinfo_updated=%d mirrors=%d "
           "flush=%d verified=%d writes=%u\n",
           op_name, name, fat32_status_name(rc_op), rep.stage, size, rep.clusters,
           rep.first_cluster, rep.old_first_cluster, rep.old_clusters, rep.freed,
           rep.grew, rep.grown_cluster,
           rep.dir_sector, rep.dir_offset, rep.free_before,
           rep.free_after, rep.fsinfo_valid, rep.fsinfo_claimed,
           rep.fsinfo_updated, rep.mirrors_equal, rep.flush_state, rep.verified,
           rep.writes);

    /* The kernel's own read path, used as a sanity check; the Python reader is
     * what the tests actually trust. */
    struct fat32_volume vol2;
    int read_rc = FAT32_E_IO;
    if (fat32_mount(&io, base, sectors, &vol2) == FAT32_OK) {
        u32 len = 0;
        unsigned char *back = (unsigned char *)malloc(1u << 20);
        if (back)
            read_rc = fat32_read_file(&io, &vol2, name, back, 1u << 20, &len);
        if (back && read_rc == FAT32_OK) {
            struct sha256_ctx sctx;
            unsigned char digest[32];
            char hex[65];
            sha256_init(&sctx);
            sha256_update(&sctx, back, len);
            sha256_final(&sctx, digest);
            sha256_hex(digest, hex);
            printf("readback name=%s status=ok size=%u sha256=%s\n", name, len, hex);
        } else {
            printf("readback name=%s status=%s\n", name,
                   back ? fat32_status_name(read_rc) : "no-memory");
        }
        free(back);
    }

    if (out_path) {
        FILE *out = fopen(out_path, "wb");
        if (!out) {
            perror(out_path);
            free(img.data);
            free(payload);
            return 2;
        }
        if (fwrite(img.data, 1, (size_t)bytes, out) != (size_t)bytes) {
            fprintf(stderr, "short write\n");
            fclose(out);
            free(img.data);
            free(payload);
            return 2;
        }
        fclose(out);
    }
    if (sink.lba_log)
        fclose(sink.lba_log);

    free(img.data);
    free(payload);
    return rc_op == FAT32_OK ? 0 : 1;
}
