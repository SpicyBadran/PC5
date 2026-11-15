# Firmware analysis

## 1. What firmware is, and is not, in this project

* **Not supplied.** The repository was received with no Sony firmware image; the
  only supplied material was the AnyPS5 source archive. Nothing has been
  analysed yet.
* **Not distributed.** PC5 will never commit, release or embed Sony firmware,
  keys or derived binaries. `.gitignore` excludes `firmware/`.
* **A primary research input.** When the owner supplies an image, it is analysed
  locally and only its hashes, container identification and structure are
  recorded.

## 2. What a PS5 firmware image is expected to be

Public documentation and Sony's own update instructions describe the shape of
the update file:

* Sony's support page documents the USB layout `PS5/UPDATE/PS5UPDATE.PUP` and
  the ~1.1 GB (update) / ~1.2 GB+ (reinstallation) sizes
  (<https://www.playstation.com/en-us/support/hardware/ps5/system-software/>).
* Community analysis describes the outer container as starting with the ASCII
  marker `SLB2`, containing entry names such as `PS5UPDATE1.PUP` and
  `PS5UPDATE2.PUP`, with a plaintext header followed by signed and/or encrypted
  entries whose layout is version specific
  (<https://gbatemp.net/threads/built-a-pc-based-pup-decrypt-tool-one-key-away-from-decrypting-any-firmware-forever-need-someone-on-13-60-to-run-an-extraction-payload.685380/>).
* A separate research thread records a `SLB2` file of 1,418,890,240 bytes
  containing those names and explicitly warns that no readable version string
  was found and that a modified PUP must pass authentication it does not control
  (<https://github.com/ZigZagBoi1/PS5-14.10-Research/issues/73>).

PC5 treats these as **third-party reports**, not as verified facts about the
owner's image. `tools/firmware.py` identifies `SLB2` by signature and reports
what it sees; it does not claim to know the entry table layout, because that
layout has not been verified here and varies by version.

**Do not assume a PUP is bootable on a PC.** It is an input to a console updater,
not an operating-system image. Public research shows the console validates it
and writes it to NAND; nothing in it is designed to be executed by a generic
x86 machine, and its components are signed for the console's security
processor.

## 3. What the tool does

```sh
python3 tools/firmware.py analyze --input /path/PS5UPDATE.PUP \
    --json-out /tmp/fw-report.json --manifest-out firmware/manifest.json
```

| Step | Output |
|---|---|
| hash | SHA-256 of the whole image |
| identify | container kind from documented signatures: `SLB2`, `\x7fFIH`, `\x7fCNT`, `ELF`, `SELF/NPXS`, `PKG`, `ZIP`, or `unidentified` |
| structure | header bytes (hex), printable entry names, PFS offset for `FIH` images |
| entropy | 1 MiB-window Shannon entropy, mean; > 7.5 bits/byte means encrypted or compressed payload |
| manifest | `pc5.firmware-manifest/1` JSON, embeddable into a build as a short text summary |

It performs **no decryption**, key handling or patching. Anything it cannot
identify is reported as unidentified — which is a result, not a failure.

## 4. What the running system reports

The Firmware page of the boot environment states:

* whether a manifest was compiled in and which image hash it refers to,
* that the payload is not distributed with PC5,
* that the genuine-PS5 stages of the compatibility chain have not started.

There is no code path in the kernel that could pretend firmware executed: the
chain's execution stages are hard-blocked until a tested loader exists
(`kernel/chain.c`, covered by `tests/kernel_unit.c`).

## 5. What would have to be true to execute genuine PS5 system software

In order of dependency:

1. **A verified loader path.** Sony's boot chain roots trust in the console's
   security processor and fuses. Executing the system software on a PC would
   require either Sony-authorised hardware, or a full-system emulation of the
   console SoC including its security processor — an enormous and legally
   constrained undertaking, and neither exists publicly today.
2. **SoC-level device emulation.** Zen 2 CPU, RDNA 2 GPU (36 CUs), the custom
   SSD controller/Kraken decompression path, Tempest audio DSP, the wireless
   and USB controllers, and the platform security block
   (specs per public documentation, e.g.
   <https://www.psdevwiki.com/ps5/> and Sony's published specifications).
3. **A storage and file-system implementation** for the package format
   (`\x7fFIH` finalised images with an AES-XTS `PFS` inner image, per public
   research such as <https://github.com/SvenGDK/LibProsperoPKG>).
4. **A legal basis** for using and, if necessary, decrypting material — which
   PC5 does not have and does not assume.

Because step 1 has no public solution, PC5 does not plan around it. It invests
instead in the path that can produce real results with legitimate inputs: the
host platform, the driver work, and the PS5-binary execution route documented in
`docs/GAMING_COMPATIBILITY.md` and studied with AnyPS5.

## 6. Integrity record

For each analysed image, record (never publish the image):

```
sha256     <64 hex characters>
size       <bytes>
kind       <container identification>
oem/version <string if a version string is present, otherwise "not identified">
source     <how the owner obtained it>
date       <analysis date>
tool       pc5-firmware 0.1.0
```

`docs/playstation/FIRMWARE_VERSIONS.md` is where these rows go.

## 7. Planned intake of an owner-supplied `.PUP` (design now, nothing assumed)

The owner expects to obtain an **officially obtained** Sony installation/update
package later. This section fixes the workflow now so the arrival of the file is
a test, not a redesign. It is a specification of handling, not a claim that the
package is bootable on a PC — it is not (`§2`).

### 7.1 Two different packages, never treated as interchangeable

Sony distributes more than one kind of package, and the naming from public
documentation is *update* versus *reinstallation*. PC5 records which one it was
given and refuses to infer the other:

| Property | Update package | Reinstallation package |
|---|---|---|
| Intended console action | update an existing system | restore a system from scratch |
| Expected size (public documentation, approximate) | ~1.1 GB | ~1.2 GB and up |
| Contents | version-to-version payload | complete system image set, per public reports |
| PC5's assumption | none about layout | none about layout |

Any statement about which one the owner supplied is recorded as *reported by the
owner*, with the file's own hash — never as a verified Sony claim.

### 7.2 Handling rules (the tooling already follows them)

1. **The original is opened read-only and never modified.** No in-place patching,
   no re-writing, no "cleaned" copy in place of the original. Analysis writes
   only separate report files.
2. **Identity is recorded before interpretation:** SHA-256, byte size, container
   signature, and the date of analysis.
3. **Version strings are reported only when found by evidence.** If the file
   carries none in plaintext, the report says `version: not identified`. No
   version is inferred from a filename, a marketing page or a size.
4. **Structure is reported at the level actually verified.** `tools/firmware.py`
   identifies `SLB2` and prints the header bytes and printable entry names; it
   does not decode an entry table whose layout has not been confirmed.
5. **No key material, no decryption, no circumvention.** If a component is
   encrypted, the report says so (entropy, signature) and stops there.
6. **Distribution is a separate, deliberate act.** The package and every derived
   component stay outside Git (`.gitignore` covers `firmware/`), outside CI
   artefacts and outside releases. Nothing is pushed, published or uploaded
   automatically; before any upload the owner verifies that the use is
   authorised and that repository access controls fit.
7. **The PC5 installation medium never contains it.** `docs/INSTALLATION.md`
   defines the four image targets and only the first three may be distributed.

### 7.3 The sequence when the file arrives

```sh
# 1. hash and identify, writing reports beside — never over — the input
sha256sum /secure/PS5UPDATE.PUP
python3 tools/firmware.py analyze --input /secure/PS5UPDATE.PUP \
    --json-out /secure/analysis/report.json \
    --manifest-out /secure/analysis/manifest.json
# 2. record the row in docs/playstation/FIRMWARE_VERSIONS.md (hash, size, kind,
#    version if identified, source, date, tool version)
# 3. stop and report: format, integrity, version, and what can be researched
#    next. No loading attempt is made from an unanalysed package.
```

Only after step 3, and only if the evidence supports it, is a research question
framed — for example "does entry X have a documented consumer on PC hardware".
The answer may be "no", and that is a valid result.

### 7.4 What is deliberately *not* designed

* no assumption that the package can be turned into an ISO,
* no assumption that a reinstallation package contains a bootable PC image,
* no firmware "installer" in PC5,
* no path in the kernel that consumes a `.PUP` (the kernel's firmware stage is
  BLOCKED by design; `kernel/chain.c` enforces it and `tests/kernel_unit.c`
  proves it).
