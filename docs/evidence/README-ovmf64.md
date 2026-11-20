# 64-bit OVMF evidence

Produced by `make ovmf-test64` on the trees at commits `71f3cbb` and `d669b7c`,
by an independent audit of PR #31. This is **firmware-level** evidence for the 64-bit path only.

| File | What it is |
|---|---|
| `ovmf64-serial-71f3cbb.log` | Complete serial output, 4336 bytes, nothing removed |
| `ovmf64-report-71f3cbb.json` | The driver's machine-readable verdict |
| `ovmf64-serial-d669b7c.log` | The same boot after the exit-status and stale-log fixes; 4336 bytes |
| `ovmf64-report-d669b7c.json` | Verdict for that run |

Environment: QEMU 10.0.0 with `edk2-x86_64-code.fd` (3,653,632 B, sha256
`33090cc0…`), variable store sha256 `67bce200…`, `-machine q35 -m 512 -smp 1`,
`efi64.img` served as a FAT12 superfloppy on `ich9-ahci`. Verdict: 30 passed,
0 failed, 0 unavailable, 0 not tested; exit 0.

What this proves: genuine OVMF loaded `BOOTX64.EFI`, the loader found and read the
kernel from the volume it was loaded from, called ExitBootServices successfully on
the first attempt, entered the kernel, and the kernel's live page-table walk passed
all 14 identity probes and both allocation checks before printing `PC5-64-DONE`.

What it does **not** prove: nothing about physical hardware, nothing about the
Sony boot chain, and nothing about the 32-bit path. `make ovmf-test` is a
different target and this result says nothing about it.

Reproduce with a QEMU and OVMF on the machine:

    make ovmf-test64 PC5_QEMU=/path/to/qemu-system-x86_64 \
        PC5_OVMF_CODE=/usr/share/OVMF/OVMF_CODE.fd \
        PC5_OVMF_VARS=/usr/share/OVMF/OVMF_VARS.fd

Add `PC5_QEMU_DATA_DIR` if QEMU cannot find its own ROM images.
