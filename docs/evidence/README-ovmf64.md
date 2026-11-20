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
far enough to persist its variables; subsequent boots read it back. A freshly
built image has root entries `PC5EFI`, `EFI`, `KERNEL64BIN`; after one boot it
also has `NVVARS`.

The two transitions cost different amounts, measured by diffing the image either
side of a boot:

| transition | bytes changed | FAT | root directory | data area |
|---|---|---|---|---|
| first boot (creates `NVVARS`) | ~881 | 8 | 46 | 827 |
| later boot (updates it) | ~201 | 0 | 1 | 200 |

So the mutations span the FAT, the root directory and the data area - not one
region. The first boot is the larger change because it allocates clusters and
writes a new directory entry; later boots rewrite the variable contents in place.

These are **observed measurements from one OVMF build**, not a promised byte
count: a different firmware version, a different variable set, or a run that
persists more or less state will produce different numbers. What generalises is
the direction - the image is modified on boot, and it is modified again on every
subsequent boot.

So do not assume the image is pristine after a run. The *variable-store template*
(`--ovmf-vars`) is copied fresh per run, which keeps runs independent of each
other, but that says nothing about the disk image: it is a mutable FAT12 volume
that firmware may modify. Where a pristine starting state matters, rebuild it
(`make uefi64`) rather than reusing one that has already been booted.

What it does **not** prove: nothing about physical hardware, nothing about the
Sony boot chain, and nothing about the 32-bit path. `make ovmf-test` is a
different target and this result says nothing about it.

Reproduce with a QEMU and OVMF on the machine:

    make ovmf-test64 PC5_QEMU=/path/to/qemu-system-x86_64 \
        PC5_OVMF_CODE=/usr/share/OVMF/OVMF_CODE.fd \
        PC5_OVMF_VARS=/usr/share/OVMF/OVMF_VARS.fd

Add `PC5_QEMU_DATA_DIR` if QEMU cannot find its own ROM images.
