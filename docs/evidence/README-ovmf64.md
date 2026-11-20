# 64-bit OVMF evidence

Produced by `make ovmf-test64` on the trees at five commits, by an independent
audit of PR #31. This is **firmware-level** evidence for the 64-bit path only.

**Read the status column before trusting a file.** Only `dbd60c1` describes the
driver in this tree. The other four are history, kept because they record what the
gate did at each point in the audit, and because deleting an inconvenient earlier
result is exactly the habit evidence files exist to prevent.

| Commit | Status | Check count | Report | Report sha256 | Serial | Serial sha256 |
|---|---|---|---|---|---|---|
| `dbd60c1` | **current** | 35 passed, 0 failed, 0 unavailable, 0 not tested | 16,004 B | `b5700017a102…` | 4,427 B | `9d56d3a51062…` |
| `d74ae5d` | superseded | 34 passed | 13,349 B | `78192a5ac52e…` | 4,427 B | `c766fa561d9d…` |
| `6fa62ec` | superseded | 33 passed | 12,894 B | `f95c77313a8a…` | 4,427 B | `9d56d3a51062…` |
| `d669b7c` | **pre-fix, see below** | 30 passed | 10,965 B | `2a455664e082…` | 4,336 B | `2c7c846dfc98…` |
| `71f3cbb` | **pre-fix, see below** | 30 passed | 10,965 B | `5ffc40769d1c…` | 4,336 B | `2c7c846dfc98…` |

Why each is superseded:

* **`d74ae5d`** predates the raw-marker accounting (`6fa62ec` made every raw
  detailed marker account for a record) and the recomputed D1 identity
  (`dbd60c1`). A malformed *extra* probe beside fourteen correct ones, or a
  `pa_equals_va` flag that lies, could not have been caught by it.
* **`6fa62ec`** predates the recomputed-identity and single-summary rules of
  `dbd60c1`, so it could not show a lying identity flag or a duplicated summary.
* **`d669b7c`** and **`71f3cbb`** predate `6fa62ec`, which introduced the rule that
  a `PASS` record carries no `detail`.

## The two pre-fix reports, and why they look wrong

`ovmf64-report-71f3cbb.json` and `ovmf64-report-d669b7c.json` each hold **24 `PASS`
entries with a non-empty `detail`**, and several of those details read like failure
text:

| check (state `PASS`) | detail |
|---|---|
| `the machine was stopped on evidence, not on the clock` | `timed out after 2.5s with no terminal marker` |
| `the machine booted UEFI, not SeaBIOS` | `SeaBIOS output present` |
| `the kernel reached its final marker` | `no PC5-64-DONE` |
| `[D1] every probed address translated to itself` | `a probe resolved somewhere other than its own address` |

That is not a passing check with a failure reason, and it is not a corrupt file.
Both are the **pre-fix driver's** output: `detail` was written for the failing
branch and stored unconditionally, so it survived next to a `PASS`. `6fa62ec`
fixed exactly this ("drop PASS-timeout details"), and `record()` in
`tests/ovmf_boot64.py` now forces `detail: ""` whenever the state is `PASS`.

The state, not the prose, is what those records mean: the boot itself succeeded,
and the serial log in this directory proves it independently — it contains
`PC5-64-LOADER-BANNER`, `PC5-64-LOADER-JUMP`, fifteen `PC5-64-PTWALK` records and
`PC5-64-DONE`, with no `PC5-64BIT-FAIL` and no `SeaBIOS`. Anyone regenerating a
report from this tree will get empty details, because the driver no longer emits
them. **These two files are labelled history, not rewritten.**

## Identical serial logs are expected, not duplicated evidence

Two pairs share a serial log byte for byte:

* `71f3cbb` and `d669b7c` — both `2c7c846dfc98…`, 4,336 B
* `6fa62ec` and `dbd60c1` — both `9d56d3a51062…`, 4,427 B

That is expected rather than a duplicate: each pair's two commits changed only the
driver and its tests, never the loader or kernel, so the same binaries produced the
same boot output. What differs between the pairs is the *report*, which grew as the
driver learned to check more (12,894 → 16,004 B).

The two pairs differ from each other in the addresses firmware chose, for example
the boot-info allocation `returned-base=0x1e23a000` versus `0x1e23c000`. They also
differ in one memory-map count: `conventional_pages` is 119,146 in the
`71f3cbb`/`d74ae5d` logs and 119,148 in the `6fa62ec`/`dbd60c1` logs. Firmware
picks addresses per boot, so a repeat of the same boot on this firmware reproduces
its own pair's snapshot but not the other pair's; a log byte-identical across
*different* firmware-selected addresses would be the suspicious one.

Environment: QEMU 10.0.0 with `edk2-x86_64-code.fd` (3,653,632 B, sha256
`33090cc0…`), variable store sha256 `67bce200…`, `-machine q35 -m 512 -smp 1`,
`efi64.img` served as a FAT12 superfloppy on `ich9-ahci`. The `71f3cbb`/`d669b7c`
runs recorded 30 checks; the current `dbd60c1` run records 35, all passed, exit 0.

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
