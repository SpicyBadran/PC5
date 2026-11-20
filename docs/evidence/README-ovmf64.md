# 64-bit OVMF evidence

Produced by `make ovmf-test64` on the trees at commits `71f3cbb` and `d669b7c`,
by an independent audit of PR #31. This is **firmware-level** evidence for the 64-bit path only.

| File | What it is |
|---|---|
| `ovmf64-serial-71f3cbb.log` | Complete serial output, 4336 bytes, nothing removed |
| `ovmf64-report-71f3cbb.json` | The driver's machine-readable verdict |
| `ovmf64-serial-d669b7c.log` | The same boot after the exit-status and stale-log fixes; 4336 bytes |
| `ovmf64-report-d669b7c.json` | Verdict for that run |

The two serial logs are byte-identical (sha256 `2c7c846d…`). That is expected
rather than a duplicate: the commits `71f3cbb` and `d669b7c` changed only the
driver and its tests, never the loader or kernel, so the same binaries produced
the same boot output. Both are kept because each records a distinct point in the
audit - the run that first passed, and the run after the driver's exit-status and
stale-log rules changed. A repeat of this boot on this firmware also reproduces
`2c7c846d…` exactly; runs that do differ vary only in the addresses firmware
chooses and in one memory-map count (`conventional_pages` 119146 vs 119148).

Environment: QEMU 10.0.0 with `edk2-x86_64-code.fd` (3,653,632 B, sha256
`33090cc0…`), variable store sha256 `67bce200…`, `-machine q35 -m 512 -smp 1`,
`efi64.img` served as a FAT12 superfloppy on `ich9-ahci`. Verdict: 30 passed,
0 failed, 0 unavailable, 0 not tested; exit 0.

What this proves: genuine OVMF loaded `BOOTX64.EFI`, the loader found and read the
kernel from the volume it was loaded from, called ExitBootServices successfully on
the first attempt, entered the kernel, and the kernel's live page-table walk passed
all 14 identity probes and both allocation checks before printing `PC5-64-DONE`.

## The disk image is not immutable

A real OVMF run **writes into `efi64.img`**. Firmware stores variable data there,
creating an `NVVARS` file in the image's root directory once the boot progresses
far enough to persist its variables; subsequent boots read it back. This was
observed directly: a freshly built image has root entries `PC5EFI`, `EFI`,
`KERNEL64BIN`, and after one boot it also has `NVVARS`, with ~201 bytes changed
outside the BPB and root directory.

So do not assume the image is pristine after a run. The *variable-store template*
(`--ovmf-vars`) is copied fresh per run, which keeps runs independent of each
other, but that says nothing about the disk image: it is a mutable FAT12 volume
that firmware may modify. Where a pristine starting state matters, rebuild it
(`make uefi64`) rather than reusing one that has already been booted.

Not every run is claimed to produce identical mutations - what firmware writes
depends on its own variable handling, and this note records the effect that was
observed, not a guarantee that every run writes the same bytes.

What it does **not** prove: nothing about physical hardware, nothing about the
Sony boot chain, and nothing about the 32-bit path. `make ovmf-test` is a
different target and this result says nothing about it.

Reproduce with a QEMU and OVMF on the machine:

    make ovmf-test64 PC5_QEMU=/path/to/qemu-system-x86_64 \
        PC5_OVMF_CODE=/usr/share/OVMF/OVMF_CODE.fd \
        PC5_OVMF_VARS=/usr/share/OVMF/OVMF_VARS.fd

Add `PC5_QEMU_DATA_DIR` if QEMU cannot find its own ROM images.
