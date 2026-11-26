# The 64-bit kernel image header (design v1)

Status: implemented at `1fdb24e`, enforced by the unit, emulator and firmware gates.
Source of truth for the layout: [`kernel64/include/pc5_kimage64.h`](../../kernel64/include/pc5_kimage64.h).
Nothing in this document is authoritative over that file; where they disagree, the header
is the format and this page is a bug.

## The defect this closes

`KERNEL64.BIN` was bytes on a FAT directory entry. The loader read the whole file and
jumped to a constant address:

```c
static const u64 kernel_load_base = 0x100000ull;      /* PC5_KERNEL64_LOAD_BASE */
static const u64 kernel_entry_off = 0x1000ull;
```

`PC5_BOOT_SIG` (`"PC5K"`) at byte 2 of `kernel/bin/kernel.bin` had been worth checking only
because that path *parses* the value at bytes `0x02..0x06` and refuses a mismatch. The
64-bit path had no such signature, so the two honest answers to "is this our kernel" were
"the file was present" and "I read the number of bytes the FAT directory claimed" — a number
that comes from the same untrusted record.

That left one property unprovable and one limitation unfixedable: a corrupted
`KERNEL64.BIN` could not be distinguished from a valid image by the firmware, and no
negative control for kernel integrity could exist.
`docs/KNOWN_LIMITATIONS.md` said so. It is the reason this format exists and not a detail of
how the format works.

The consequence is what a gate has to be able to say. Before: *the image was 204800 bytes,
and the log said it was* — which is a restatement of the log's own arithmetic. Now: *the
image declared it was 208896 bytes with a 128-byte header, an entry at +0x1000, 172032 bytes
of `.bss`, a payload checksum, and an architecture; the bytes, the FAT record, the linker's
symbols and the runtime measurement all agree.* That is a statement the loader can verify
from more than one source, and it is the statement the OVMF gate checks.

## The format

The image begins with 128 bytes describing the image that follows. All fields are
little-endian.

| Offset | Size | Field | Meaning |
| ---: | ---: | --- | --- |
| 0 | 8 | `magic` | `PC5K64IM`. Deliberately not the 32-bit path's `PC5K` at byte 2: one byte of difference between two architectures is a coincidence waiting to be read, and this path must never accept an image built for the other one. |
| 8 | 4 | `header_version` | `1`. The *format* version, not the kernel's. |
| 12 | 4 | `header_size` | `128`. The payload begins here. |
| 16 | 8 | `image_size` | The file's length **including** this header. |
| 24 | 8 | `load_base` | `0x100000`, the address the linker script was told to use. |
| 32 | 8 | `entry_offset` | `_start64` minus `load_base`; must satisfy `header_size <= offset < image_size`. |
| 40 | 4 | `entry_align` | Required alignment of `entry_offset`: `16`. |
| 44 | 4 | `load_align` | Required alignment of `load_base`: `4096`. |
| 48 | 4 | `arch` | `1` = x86-64, `2` = i386. |
| 52 | 4 | `machine` | The PE/COFF `IMAGE_FILE_MACHINE_*` of the object that produced the image (`62`). |
| 56 | 8 | `bss_size` | Bytes of `.bss`, which live above the loaded payload. |
| 64 | 8 | `checksum` | FNV-1a-64 over the payload (see below). |
| 72 | 4 | `reserved0` | Must be zero. |
| 76 | 4 | `reserved1` | Must be zero. |
| 80 | 4 | `header_crc` | FNV-1a-32 over bytes `[0, 80)`. |
| 84 | 44 | `reserved2[]` | Must be zero. |

Two hashes, because they answer different questions and the mutation battery below needs to
hit one without tripping the other. `header_crc` covers the header's own fields up to the
word that holds it, and answers *are these declarations self-consistent*; the stamper
recomputes it, and a hand-edited header that left it stale is refused as
`kimage64-header-crc`. `checksum` covers the loaded code — the file from `header_size` to
`image_size`, with the header bytes that the checksum itself lives in treated as zero — and
answers *is the payload still what it was when the header was written*.

`image_size` includes the header, and `entry_offset` is measured from `load_base`. The two
choices are what let the image be a flat binary and still carry a header:
`kernel64/kernel64.ld` puts `.head` first and pads it to a page, so file offset 0 is address
`0x100000` and every instruction keeps the address the linker gave it. A header prepended in
front of an unrelocatable image would move `_start64` by 128 bytes; the padding exists so
that it does not.

`.bss` is counted in the file (the linker emits it as zeros) and cleared again by the kernel's own
startup code.  That is belt-and-braces and it is deliberate: the zeroing in `kernel64/start.S` is
the reason nothing depends on whether a filesystem handed back the tail of a sparse file.

The sentence that used to sit here claimed `bss_size` was declared so the extent of the *cleared*
memory would be "a checked number rather than a symbol only the link knew", and it outran the rule:
the validator bounds `bss_size` above by the file and never compares the declaration with the extent
the boot actually clears, so at v1 the clearing is still done from `__bss_start`/`__bss_end` and the
declaration is information that nobody acts on.  `docs/TEST_RESULTS.md` §35 measured the consequence —
an image declaring its whole self as `.bss` is stamped, validated, booted and forgotten.  What makes
that inert is precisely what makes it a v2 item: the day the boot trusts a declared `bss_start`, this
field stops being a record and starts being a decision, and the comparison has to exist first.

## Validation and refusal names

`pc5_kimage64_validate()` in [`boot/uefi64/kimage64.c`](../../boot/uefi64/kimage64.c) returns
a reason string; `pc5_kimage64_reason_name()` reduces it to a token; the loader's abort line
is `PC5-64-LOADER-FAIL=<token>`. The order below is the check order, and it is load-bearing:
a control is only evidence if the refusal it expects is the refusal the implementation
produces *for that fault*, so one mutation must not be able to trip an earlier check and earn
an accidental pass.

| Refusal | Condition |
| --- | --- |
| `kimage64-truncated` | fewer bytes than the header size |
| `kimage64-magic` | first eight bytes are not `PC5K64IM` |
| `kimage64-version` | `header_version` is not a version this loader implements |
| `kimage64-header-size` | `header_size` is not exactly `PC5_KIMAGE64_HEADER_SIZE` |
| `kimage64-image-size` | declared length is not the file's length, or the payload would not fit |
| `kimage64-header-crc` | the header's own checksum does not match its fields |
| `kimage64-load-base` | `load_base` is not the address this path loads at, or is misaligned |
| `kimage64-arch` | `arch` is not x86-64 |
| `kimage64-machine` | `machine` disagrees with `arch`, or is not a 64-bit x86 machine |
| `kimage64-entry-align` | `entry_offset` is not a multiple of the declared `entry_align` |
| `kimage64-load-align` | `load_base` is not a multiple of the declared `load_align` |
| `kimage64-bss-size` | `bss_size` overflows the load extent — an upper bound only; see the paragraph above and §35 of `docs/TEST_RESULTS.md` |
| `kimage64-entry-range` | `entry_offset` is inside the header, or beyond the image |
| `kimage64-reserved` | a field defined as zero is not zero |
| `kimage64-checksum` | the payload does not match `checksum` |

`tools/kimage64.py` is the host's end of the same contract: `--stamp` (the Makefile runs it,
and it refuses to write a header that would not validate), `--verify --json`, and
`--mutate --kind K` for exactly the faults above. `EXPECTED_REFUSAL` maps each mutation to
the refusal it must earn, and `mutate_bytes` checks that mapping before returning, so a
mutant that stays valid — or fails for the wrong reason — is a tool failure rather than a
test that quietly proves nothing.

Three layers enforce it, each with a job the others cannot do:

* [`tests/test_kimage64.py`](../../tests/test_kimage64.py) cross-pins the Python
  descriptions against the `#define`s, compiles the loader's validator for the host and drives
  all fifteen refusals through it, and requires the C verdict and the Python verdict to agree
  over a corpus of hand-built images. Breadth, in about a second.
* [`tests/test_boot64_stub.py`](../../tests/test_boot64_stub.py)
  (`KernelImageHeaderTests`) runs the **release** `BOOTX64.EFI` in an emulated CPU over every
  mutation, asserting the abort names the expected refusal and that the run never reaches
  `PC5-64-LOADER-JUMP`. That last half is what separates a validated refusal from an
  accidental one: a build that crashed before the header mattered would also produce no
  banner.
* [`tests/ovmf_boot.py`](../../tests/ovmf_boot.py) boots four mutants under real OVMF —
  `magic`, `arch`, `image-size`, `checksum` — the ones whose *consequence* differs (wrong
  architecture, wrong extent, corrupt payload) and each of which must also never jump, plus
  three stages on the good path: the header was validated, the declared length equals the
  file and the FAT record, and the jump target is `load_base + entry_offset` *as declared*.
  Four rather than fifteen because a firmware boot costs minutes.

`tools/verify_iso.py` re-validates the image out of the ISO's own FAT directory, with no
build record consulted: a release whose `KERNEL64.BIN` contradicts its own header is an
invalid artifact even when every digest matches the files that produced it.

## Compatibility

* The magic is version-independent, so the version field decides compatibility — a v2 image
  read by a v1 loader is `kimage64-version`, not a misparse.
* `header_size` must be exactly `128`. A future version may *grow* the header; a v1 loader
  that saw `header_size = 192` would otherwise read a layout it does not have. Growing the
  header therefore means adding version support first, never by editing a size.
* `reserved0`, `reserved1` and `reserved2` must be zero on a version-1 image. That is a hard
  rule and it is what makes those fields usable later: a field defined by a future version
  cannot have been set by anything that predates its definition, so a v2 that borrows
  `reserved0` cannot be silently misread as zero by a v1 loader.
* A change to a field's *meaning* is a version bump. Renaming a field, repurposing it, or
  changing what `image_size` counts are all the same kind of change.
* The 32-bit path is untouched: `BOOTIA32.EFI` still validates `kernel.bin`'s `PC5K`
  signature and length against the directory entry. Adding a header to that image would be a
  separate change with its own version number, and it is deliberately not part of this one.

## What this does not do

* **It is not a signature.** The checksums are integrity, not authenticity: corruption and
  carelessness are refused, a deliberate rewrite that recomputes both is not. The boundary
  for that is a cryptographic one, and `docs/SECURITY.md` keeps the Secure Boot item open.
* **It does not tell the loader which file to read.** The ESP path and the FAT directory entry
  still come from firmware; the header binds the *contents* of whatever was read, and the two
  extents must agree, which is why both are checked.
* **The declared `load_base` is not a request.** The image is unrelocatable, so the field
  documents what the link assumed and the loader refuses anything else; real relocation is
  the thing that would make it a request, and it is not implemented.
* **`arch`/`machine` are two encodings of one fact, on purpose.** They catch different
  accidents — a kernel built for the other architecture, and a host toolchain producing the
  wrong object type — and a v1 loader refuses either mismatch. They are not an ABI promise.

## After the jump: what the kernel can re-check, and what it cannot

The loader's validation happens before it transfers control, so it describes the bytes *it*
read. Two things can still be true afterwards: the copy in memory can have been damaged by
whatever filled it, and the `kernel_size` handed to the kernel in the boot info can disagree
with the image the kernel is running in. Neither is visible to a check that runs before the
jump, and neither was visible to any check at all until `validate_image_self()` existed — the
kernel printed the size it was told, and compared it only with the memory map.

So the kernel re-reads the header at its own load address, through the *same* validator the
loader uses (`boot/uefi64/kimage64.c` is linked into `KERNEL64.BIN`;
`tests/test_kimage64.py` asserts that both link lines name the object, because a kernel holding
its own copy of these rules would make the agreement meaningless), and prints one line:

```
PC5-64-KIMAGE status=ok reason=ok image=0x34000 handover=0x34000 bss=0x2a000 entry=0x1000 crc=ok
```

`image=` is what the loaded header says, `handover=` is what the boot info claimed, and the row
in the firmware gate compares both with the length of the file the gate built — three sources,
of which the boot controls two.

**The payload checksum is deliberately not recomputed here, and the reason is a property of
this kernel rather than of the format.** `checksum` covers the whole image; `.bss` travels
inside the image; and `_start64` adopts a stack out of `.bss` and clears it before `kmain`
runs. By the time any C executes, part of the checked range is in use as a stack — so a runtime
payload sum would not be measuring the integrity of the load, it would be measuring the ordering
of the boot code, and it would fail. `header_crc` is the opposite case: 128 bytes in `.head`,
which nothing writes, so it can be re-derived at any point during the boot.

That is the whole of what a runtime check can reach without a format change, and the boundary is
pinned in tests rather than left as a comment: `tests/test_kimage64.py` feeds a mutated body to
both entry points and requires the full validator to refuse it and the header half to accept it,
and `tests/test_kernel64.py` boots a kernel with a byte of `.head`'s padding edited in memory and
requires the boot to *succeed*. Closing that gap means a v2 header carrying a second digest over
a defined prefix (`[0, __bss_start)`) — a format change with a version bump, not a bug fix.

The same review found the boundary on the other side of `.bss`, and it is a v2 item for the same
reason.  The rules above refuse `bss_size > image_size` and do not refuse the converse absurdity: a
header that declares the *whole file* to be `.bss`, entry point included, is accepted by this tool
and by the kernel's half-validator alike (`docs/TEST_RESULTS.md` §35 measured it end to end, with
both digests re-stamped, and the boot ran to completion).  It is inert while the zeroing comes from
the linker's symbols.  It becomes a fault the moment a v2 lets the boot trust a declared
`bss_start`.  So v2 carries `bss_start` explicitly, and the shared validator gains one comparison —
the declared `.bss` must not contain the declared entry:

    entry_offset < image_size - bss_size

— with a `bss-size-whole-file` row in `MUTATIONS` beside the existing `bss-size`, so both halves of
the validator are required to refuse it and the reject table stops testing the overshoot alone.
Both are format-adjacent and change the image, which is why they ride with the version bump rather
than arriving as a quiet tightening of a rule the gates never read.

A refusal from this check reuses the format's reason names, which is correct and slightly
confusing: the same string, `kimage64-image-size`, means *"the file I read is not the length you
declared"* when the loader prints it (`PC5-64-LOADER-FAIL=`) and *"the size I was handed is not
the length my own header declares"* when the kernel prints it (`PC5-64BIT-FAIL=`). The prefix
distinguishes them, and the gate's negative runs — which mutate the image on the medium and so
never reach the kernel — are only ever the first.

## Changing it

1. Edit `kernel64/include/pc5_kimage64.h` (layout) and, if semantics moved, bump
   `PC5_KIMAGE64_VERSION`.
2. Update `tools/kimage64.py`'s `FIELDS` and `LAYOUT` — `tests/test_kimage64.py` fails if a
   name, offset or format letter drifts from the `#define`s, and it is the test to read first.
3. Add a mutation and an `EXPECTED_REFUSAL` entry, and if the field is in the header, teach
   `pc5_kimage64_validate_header()` about it too — the kernel's runtime re-check covers
   everything that half checks, so a field added to the full validator alone is a field nothing
   verifies after the jump. Then wire the new refusal into
   `Profile.kernel_refusals` in `tests/ovmf_boot.py` if the firmware gate must distinguish
   it. `tests/test_ovmf_gate_profiles.py` asserts that every declared refusal has a row in
   the matrix, and that the matrix's rows and the profiles' vocabularies stay in step.
4. Rebuild the release artifacts (`make iso` re-stamps) and re-measure the evidence run:
   `image_size` changes with the kernel's size, so a quoted digest or byte count from an
   earlier run describes an older artifact.
