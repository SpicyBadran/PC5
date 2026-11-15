/*
 * Host-side driver for kernel/fat32.c.
 *
 * It mounts a real disk image from a file, lists the root directory and reads
 * named files, printing sizes and SHA-256 digests.  The test suite compares
 * those digests with an independent FAT32 reader written in Python and with
 * the bytes the fixture was built from.  This is how the kernel's filesystem
 * code is checked without booting anything.
 *
 * Build (the test does this):
 *   gcc -DPC5_HOST_TEST -Ikernel/include -o build/test/fat32-check \
 *       tests/fat32_check.c kernel/fat32.c kernel/sha256.c kernel/lib.c kernel/mem.c kernel/chain.c
 *
 * Usage: fat32-check IMAGE BASE_LBA SECTORS [NAME...]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pc5.h"

static FILE *image;
static long image_bytes;

static int file_read(void *ctx, u32 lba, u32 count, void *buf)
{
    (void)ctx;
    long off = (long)lba * 512L;
    long want = (long)count * 512L;
    if (off < 0 || want < 0 || off + want > image_bytes)
        return -1;
    if (fseek(image, off, SEEK_SET) != 0)
        return -1;
    return fread(buf, 1, (size_t)want, image) == (size_t)want ? 0 : -1;
}

int main(int argc, char **argv)
{
    if (argc < 4) {
        fprintf(stderr, "usage: %s IMAGE BASE_LBA SECTORS [NAME...]\n", argv[0]);
        return 2;
    }
    image = fopen(argv[1], "rb");
    if (!image) {
        perror(argv[1]);
        return 2;
    }
    if (fseek(image, 0, SEEK_END) != 0) {
        fclose(image);
        return 2;
    }
    image_bytes = ftell(image);

    u32 base = (u32)strtoul(argv[2], NULL, 0);
    u32 sectors = (u32)strtoul(argv[3], NULL, 0);

    struct fat32_io io = { .read = file_read, .ctx = NULL };
    struct fat32_volume vol;
    int rc = fat32_mount(&io, base, sectors, &vol);
    printf("mount status=%s base=%u sectors=%u clusters=%u label=%s fs=%s\n",
           fat32_status_name(rc), base, sectors, vol.cluster_count, vol.label,
           vol.fs_type);
    if (rc != FAT32_OK) {
        fclose(image);
        return 1;
    }

    struct fat32_entry list[64];
    int count = 0;
    rc = fat32_list_root(&io, &vol, list, 64, &count);
    printf("list status=%s count=%d\n", fat32_status_name(rc), count);
    if (rc == FAT32_OK) {
        for (int i = 0; i < count && i < 64; i++)
            printf("entry name=%s size=%u attr=0x%02x\n", list[i].name,
                   list[i].size, list[i].attr);
    }

    const u32 max = 1u << 20;
    u8 *buf = (u8 *)malloc(max);
    if (!buf) {
        fclose(image);
        return 2;
    }
    for (int i = 4; i < argc; i++) {
        u32 len = 0;
        rc = fat32_read_file(&io, &vol, argv[i], buf, max, &len);
        char hex[65];
        hex[0] = 0;
        if (rc == FAT32_OK) {
            struct sha256_ctx ctx;
            u8 digest[32];
            sha256_init(&ctx);
            sha256_update(&ctx, buf, len);
            sha256_final(&ctx, digest);
            sha256_hex(digest, hex);
        }
        printf("file name=%s status=%s size=%u sha256=%s\n", argv[i],
               fat32_status_name(rc), len, hex);
    }

    free(buf);
    fclose(image);
    return 0;
}
