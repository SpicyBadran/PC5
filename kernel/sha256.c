/*
 * PC5 kernel: SHA-256 (FIPS 180-4).
 *
 * The write self-test has to prove that what came back from the disk is what
 * was sent, in a form a log reader can compare with a host-side computation.
 * A 32-bit checksum would be enough to catch a stuck bus; SHA-256 is the
 * primitive the update manifest will need anyway (docs/UPDATE_SYSTEM.md), so
 * it is implemented once, with known-answer tests in `tests/kernel_unit.c`.
 *
 * Freestanding: no libc, no allocation, no tables beyond the round constants.
 */
#include "pc5.h"

static const u32 K[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u,
    0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
    0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
    0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
    0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
    0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u,
    0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u,
    0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
    0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u,
};

static u32 ror(u32 x, u32 n) { return (x >> n) | (x << (32 - n)); }

static void sha256_block(struct sha256_ctx *ctx, const u8 *p)
{
    u32 w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = ((u32)p[i * 4] << 24) | ((u32)p[i * 4 + 1] << 16) |
               ((u32)p[i * 4 + 2] << 8) | (u32)p[i * 4 + 3];
    }
    for (int i = 16; i < 64; i++) {
        u32 s0 = ror(w[i - 15], 7) ^ ror(w[i - 15], 18) ^ (w[i - 15] >> 3);
        u32 s1 = ror(w[i - 2], 17) ^ ror(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }

    u32 a = ctx->state[0], b = ctx->state[1], c = ctx->state[2], d = ctx->state[3];
    u32 e = ctx->state[4], f = ctx->state[5], g = ctx->state[6], h = ctx->state[7];

    for (int i = 0; i < 64; i++) {
        u32 s1 = ror(e, 6) ^ ror(e, 11) ^ ror(e, 25);
        u32 ch = (e & f) ^ (~e & g);
        u32 t1 = h + s1 + ch + K[i] + w[i];
        u32 s0 = ror(a, 2) ^ ror(a, 13) ^ ror(a, 22);
        u32 maj = (a & b) ^ (a & c) ^ (b & c);
        u32 t2 = s0 + maj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }

    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;
}

void sha256_init(struct sha256_ctx *ctx)
{
    ctx->state[0] = 0x6a09e667u; ctx->state[1] = 0xbb67ae85u;
    ctx->state[2] = 0x3c6ef372u; ctx->state[3] = 0xa54ff53au;
    ctx->state[4] = 0x510e527fu; ctx->state[5] = 0x9b05688cu;
    ctx->state[6] = 0x1f83d9abu; ctx->state[7] = 0x5be0cd19u;
    ctx->bytes = 0;
    ctx->used = 0;
}

void sha256_update(struct sha256_ctx *ctx, const void *data, u32 len)
{
    const u8 *p = (const u8 *)data;
    ctx->bytes += len;
    while (len) {
        u32 room = 64 - ctx->used;
        u32 take = len < room ? len : room;
        memcpy(ctx->block + ctx->used, p, take);
        ctx->used += take;
        p += take;
        len -= take;
        if (ctx->used == 64) {
            sha256_block(ctx, ctx->block);
            ctx->used = 0;
        }
    }
}

void sha256_final(struct sha256_ctx *ctx, u8 out[32])
{
    u64 bits = ctx->bytes * 8;
    u8 pad = 0x80;

    sha256_update(ctx, &pad, 1);
    pad = 0;
    while (ctx->used != 56)
        sha256_update(ctx, &pad, 1);
    for (int i = 0; i < 8; i++)
        ctx->block[56 + i] = (u8)(bits >> (56 - i * 8));
    sha256_block(ctx, ctx->block);
    ctx->used = 0;

    for (int i = 0; i < 8; i++) {
        out[i * 4] = (u8)(ctx->state[i] >> 24);
        out[i * 4 + 1] = (u8)(ctx->state[i] >> 16);
        out[i * 4 + 2] = (u8)(ctx->state[i] >> 8);
        out[i * 4 + 3] = (u8)(ctx->state[i]);
    }
}

void sha256_hex(const u8 digest[32], char out[65])
{
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[i * 2] = hex[digest[i] >> 4];
        out[i * 2 + 1] = hex[digest[i] & 0x0f];
    }
    out[64] = 0;
}
