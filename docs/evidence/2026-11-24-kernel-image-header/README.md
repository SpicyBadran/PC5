# The 64-bit kernel image header, enforced by real firmware

**Date:** 2026-11-24 · **Commit built and booted:** `1fdb24e` (`build:` label
`1fdb24e-dirty`, because the header work was still uncommitted when the run started — the
dirty marker is the mechanism working, not a gap in it) · **Result:** both modes green.

| Command | Exit | Checks | Matrix rows |
| --- | --- | --- | --- |
| `make PC5_UEFI=64 ovmf-test` | **0** | 88 PASS, 0 FAIL, 1 NOT TESTED (89) | **49** |
| `make ovmf-test` (32-bit) | **0** | 63 PASS, 0 FAIL, 1 NOT TESTED (64) | 35 |

The 32-bit numbers are identical to the previous run
([`2026-11-23-64bit-boot-gate`](../2026-11-23-64bit-boot-gate/README.md)): 63 PASS / 35 rows,
and the x64 leg gained 11 rows (43 → 49) and 10 checks (78 → 88 pass). The added rows are the
kernel image's own header: three good-path stages per boot (validated before the jump, declared
length matches the file *and* the FAT record, jump target equals `load_base + entry_offset`),
one vocabulary check, and four mutant boots.

## What this run establishes

`KERNEL64.BIN` now carries a 128-byte header — magic, format version, exact length, load
address, entry offset, alignment requirements, architecture, `.bss` size, payload checksum,
header CRC — and `boot/uefi64/kimage64.c` validates it before computing an entry address. The
format, the refusal vocabulary and the compatibility policy are
[`docs/engineering/KERNEL-IMAGE64-HEADER-DESIGN-v1.md`](../../engineering/KERNEL-IMAGE64-HEADER-DESIGN-v1.md);
this directory is the firmware evidence that the validation runs on real TianoCore, in the
release artifact, and that a corrupted image is refused by name.

From `ovmf64-serial-cold.log`, the line the loader now prints before it jumps:

```
PC5-64-LOADER kernel-header version=1 header=128 image=208896 entry=0x1000 bss=172032 arch=x86-64 machine=62 checksum=ok
```

and the jump it makes because of it:

```
PC5-64-LOADER-JUMP entry=0x101000 bootinfo=0xe5b6000 stack=0xe1fa000
```

`0x101000` is `0x100000 + 0x1000`: the entry address is **read from the image** instead of being
a constant in the loader. The header page is why the image grew from 204800 to 208896 bytes and
why `.text` sits one page in — the padding exists so that no instruction address moved, and
`PC5-64-IMAGE base=0x100000 size=0x33000 range=ok` is the kernel confirming the extent from its
own side.

The two boots are independent: cold with an empty variable store, warm reusing the NVRAM the
cold boot wrote, and each has its own three header stages in the report (`[cold] …` / `[warm] …`).

## Four corrupted images, refused by name, under the same firmware

Each mutant ISO is the release ISO with one field of the ESP's `KERNEL64.BIN` changed — the
mutation applied by `tools/kimage64.py --mutate`, whose `mutate_bytes` refuses to hand back a
mutant that would still validate or that fails for a *different* field's reason.

| Log | Refusal on the serial | Also asserted |
| --- | --- | --- |
| `ovmf64-serial-bad-signature.log` | `PC5-64-LOADER-FAIL=kimage64-magic` | no `LOADER-JUMP` |
| `ovmf64-serial-kernel-arch.log` | `PC5-64-LOADER-FAIL=kimage64-arch` | no `LOADER-JUMP` |
| `ovmf64-serial-kernel-image-size.log` | `PC5-64-LOADER-FAIL=kimage64-image-size` | no `LOADER-JUMP` |
| `ovmf64-serial-kernel-checksum.log` | `PC5-64-LOADER-FAIL=kimage64-checksum` | no `LOADER-JUMP` |

The `image-size` mutant is truncated *and* has its FAT directory record updated, so no
filesystem-level check can see it — only the image contradicting itself does. The `checksum`
mutant flips one byte of code in the middle of `.text`: right length, right magic, right
architecture, right header CRC. The two rows that matter for honesty here are `kimage64-magic`
on the bad-signature path, which used to be reported **NOT TESTED** with the reason "the 64-bit
kernel file has no header field to corrupt" (`docs/KNOWN_LIMITATIONS.md`, and
`HEADER_VOCABULARY_CHECK` used to exist only to keep that reason honest), and the three refusals
that did not exist at all before. The vocabulary check is still there — it now pins the x64
profile's `kimage64` kind and its refusal set against the bytes on the medium, and it still
fails the run if the gate's claim and the artifact disagree.

One control the firmware does **not** cover, and cannot: `small-memory`, `deadline` and
`disk-style UEFI boot` keep the same status as the previous run, and the 43 → 49 row growth is
all kernel-header rows, not new coverage of the hardware. `disk-style UEFI boot` remains the one
NOT TESTED path.

## Reproducing this

```sh
export TMPDIR=/tmp
export PC5_QEMU=… PC5_OVMF_CODE=… PC5_OVMF_VARS=…      # see env.txt for what was used here
SOURCE_DATE_EPOCH=1700000000 make PC5_UEFI=64 iso verify
make PC5_UEFI=64 ovmf-test                              # 89 checks, 49 matrix rows
python3 -m unittest tests.test_kimage64                  # 21 tests, the host's half
TMPDIR=/tmp PATH=/tmp/pc5-venv/bin:$PATH make stubtest64 # 45/45, the emulator's half
bash docs/evidence/2026-11-24-kernel-image-header/verification.sh
```

**Provenance of the two raw 64-bit logs, stated exactly.** The gate's own `--serial-dir`
copies were overwritten an hour later, by the 32-bit leg, which shares `build/ovmf-serial/` — so
`ovmf64-serial-cold.log` and `ovmf64-serial-warm.log` here were captured by re-running the
command line recorded in `x64-report.json` (`data.cold_command`, verbatim, with `-serial
file:` instead of `-serial stdio`) against a fresh `make PC5_UEFI=64 BUILD=… iso` at the
commit that lands this header. That artifact is `1810432` bytes, sha256
`1fa3f66d2ff63a9372a21304e3f19f0a23efe46a9fb0fde9b43cdca047e12e7f`, and its own build record is
in `x64-buildinfo-recaptured.txt`: `build: d6b97dc`, `commit: d6b97dcbb21679b5…`, `uefi-path:
64`, `esp-loader: 5a99ad0424898578` — clean, because the header work had just been committed.
The ISO the *gate* booted is a different set of bytes only in its timestamp and label
(`1806336`-plus-header, `build: 1fdb24e-dirty`, digest `ae38b319d293ad529bd065100340b355bc76ead7baa29b8f04b1c7e47e5a203d`
as recorded in `x64-report.json`), and what the gate's run established is in that report: its
derived markers agree with these logs line for line (`kernel_header_image == kernel_bytes_read
== handoff_bytes == 208896`, `kernel_jump_entry == 1052672 == 1048576 + 4096`). Both
boot-to-boot-deterministic values — the same `bootinfo=0xe5b6000 stack=0xe1fa000` — are why
this substitution is stated rather than hidden: same code, same sizes, different label.

`verification.sh` re-derives every number and marker on this page from the files in this
directory — no QEMU, no build, no network.

## What this run does not settle

`make check` covers the unit and tool suite; the firmware legs above were run separately
because they need QEMU. Priority 1 of this milestone — executing `ltr`, and showing `#DF` and
`NMI` arriving *on* their IST stacks — is **not** in this run: the 64-bit log above still ends
its TSS line with `tr=not-loaded reason=ltr-unverifiable-here` and `ist-df=0 ist-nmi=0`, which
is the pre-existing state, faithfully recorded rather than improved by editing the marker.
