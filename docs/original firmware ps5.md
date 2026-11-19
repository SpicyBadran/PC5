# Original PlayStation 5 system firmware

**Status: reference record. No capability is claimed here, and no firmware content
has been inspected.** This document identifies the original Sony PlayStation 5
system-software package that the repository carries, records what can and cannot be
established about it here, and states its role in PC5.

The requested title is `original firmware ps5`. The repository's documentation
convention is `docs/**` Markdown with a conventional heading, so the file is named
`docs/original firmware ps5.md` — the requested name, verbatim, with the extension
the rest of the tree uses.

| | |
|---|---|
| Status | reference record; identity by hash and size only |
| File | [`PS5UPDATE.PUP`](../PS5UPDATE.PUP) (repository root) |
| What the repository holds | a **Git LFS pointer**, not the package — see §2 |
| Authority for its use | ADR-022; [`PS5_SYSTEM_SOFTWARE_TARGET.md`](PS5_SYSTEM_SOFTWARE_TARGET.md) |
| Related | [`FIRMWARE_ANALYSIS.md`](FIRMWARE_ANALYSIS.md), [`playstation/FIRMWARE_VERSIONS.md`](playstation/FIRMWARE_VERSIONS.md), [`UPDATE_SYSTEM.md`](UPDATE_SYSTEM.md), [`SECURITY.md`](SECURITY.md) |

---

## 1. What this file identifies

`PS5UPDATE.PUP` is the **original Sony PlayStation 5 system-software package**: the
update/reinstallation payload that Sony distributes for the console, and the
central firmware reference for PC5. It is the genuine article, not a PC5-authored
stand-in, and it is not a disk image and not a bootable medium.

The repository does not contain those bytes. What it contains is a pointer to them
(§2), and this document does not go beyond that.

Sony's own support material documents the USB layout `PS5/UPDATE/PS5UPDATE.PUP`
that a console expects; that is a statement about Sony's published procedure and is
recorded as such in [`FIRMWARE_ANALYSIS.md`](FIRMWARE_ANALYSIS.md) §2, not as an
observation about this file.

## 2. What is actually in the repository — the payload is not present

**The working tree contains a Git LFS pointer, 135 bytes, and no firmware payload.**
Measured on this checkout:

| Property | Value | How established |
|---|---|---|
| path | `PS5UPDATE.PUP` (repository root) | `ls` |
| size in the working tree | **135 bytes** | `wc -c` |
| content | the three-line LFS pointer text | `cat` |
| pointer file SHA-256 | `6e8f5a459551316397f1946cce4d540a56c0b790854c2c0d94c442ca86e73b42` | `sha256sum` |
| declared LFS object id | `42ea0e5aad1361e098837d40157863c4c519f0bbc66eb03c04dbaaf339b063fb` | pointer content |
| declared payload size | **1,423,697,408 bytes** (≈ 1.33 GiB) | pointer content |
| payload in this checkout | **no** | `git lfs` is not installed here and the object was never fetched |
| analysed here | **no** | see below |

Because the bytes are absent, **no statement in this repository about the package's
contents is verified**, and nothing in it should be read as an analysis. That
includes its container format, its internal structure, the system-software version
it carries, whether it is retail- or devkit-signed, whether it is a full
reinstallation package or an incremental update, and whether its declared hash
matches the real bytes.

`git lfs` returned `git: 'lfs' is not a git command` on this machine, so the object
could not be resolved into a working file even to hash it. That is a limit of this
environment, and it is recorded rather than worked around: fetching a 1.33 GiB
console payload into this checkout is exactly what ADR-022 forbids.

### 2.1 Deliberately not done

No decryption, no key handling, no signature work, no extraction, no
reconstruction of Sony's installation flow, and no compatibility guesswork. Those
are not omissions to be filled in later from memory; they are out of scope for this
repository.

## 3. Its role in PC5

PC5 is intended to provide the **PC-side platform and compatibility
infrastructure** needed to run genuine PS5 system software on compatible PC
hardware. It is **not** an attempt to replace Sony's operating system with a
newly created imitation.

The distinction is the whole point of the project and it decides what PC5 may
build:

| | |
|---|---|
| What PC5 is | the platform and compatibility layer a PC lacks: boot path, privilege model, memory, device models, contracts, and the compatibility infrastructure around them |
| What PC5 is not | a replacement operating system, a reimplementation of Sony's system software, or an imitation of its interface |
| What the package is here | the **target input of the mission** — the genuine software that, if it ever runs, is what runs |

Consequences that follow, and that the architecture already records:

* a PC has no updater for this package and no platform firmware that would accept
  it, which is the entire distance between "a file exists" and "the system software
  runs";
* PC5 therefore treats the package as an **input to a platform model**, not as
  something to write to a USB stick and boot;
* PC5 ships no Sony bytes at all, and no PC5-authored screen is presented as a PS5
  screen;
* where the genuine system would run, it should be the genuine system — no
  imitation stands in for it.

The nine requirements such a layer must satisfy, each classified, are in
[`PS5_SYSTEM_SOFTWARE_TARGET.md`](PS5_SYSTEM_SOFTWARE_TARGET.md) §4. Two of them
are external preconditions rather than PC5 work, and the architecture says so
plainly instead of implying they are engineering to be done.

## 4. How the file is used, and how it is not

**Used:**

* as the named destination input — the artifact the compatibility work is aimed at;
* as a **hash and size reference**, so a run can later be tied to an exact object
  rather than to "a PUP file";
* as the reason the platform contract, device models and conformance suites exist.

**Not used:**

* never committed as a payload, extracted into the tree, redistributed or
  modified — the repository holds a pointer and nothing else;
* never a build input; nothing in the tree consumes it;
* never a source of keys, and never a target for circumventing authentication.

The full rules of engagement are ADR-022, summarised in
[`PS5_SYSTEM_SOFTWARE_TARGET.md`](PS5_SYSTEM_SOFTWARE_TARGET.md) §3.

## 5. Verified and assumed — kept separate

| Statement | Status |
|---|---|
| `PS5UPDATE.PUP` at the repository root is a 135-byte Git LFS pointer | **VERIFIED** — measured on this checkout |
| The pointer's own SHA-256 is `6e8f5a45…e73b42` | **VERIFIED** — `sha256sum` |
| The pointer declares object `42ea0e5a…b063fb`, size 1,423,697,408 bytes | **VERIFIED** — read from the pointer |
| The payload is absent from this checkout; `git lfs` is unavailable here | **VERIFIED** — the command is not installed |
| No tool in the tree consumes the file | **VERIFIED** — no reference outside documentation |
| `PS5UPDATE.PUP` is Sony's original PS5 system-software package | **STATED BY THE PROJECT DIRECTIVE** — the filename and the directive are the basis; the bytes were not read, so this is not independently confirmed here |
| The package is the central firmware reference for PC5 | **PROJECT DECISION** — recorded in ADR-019/ADR-022, not a measurement |
| Its declared size is consistent with a full PS5 update/reinstallation package | **CONSISTENT, NOT PROOF** — size alone establishes nothing about flashability, completeness or contents |
| Anything about container format, entries, version, signing, encryption or contents | **NOT VERIFIED** — no bytes were read |
| That it could be installed or booted on a PC | **NOT VERIFIED, and not claimed anywhere** |

Two labels above are used deliberately and are not interchangeable: "verified"
means measured in this checkout; "stated by the directive" means it is the
project's premise, recorded as such, and would need the lawful bytes to confirm.

## 6. If the payload ever becomes lawfully available

Analysis stays outside this repository, produces hashes and structure only, and is
recorded as a summary under [`docs/playstation/`](playstation/) — never as the
file. The runbook is in
[`PS5_SYSTEM_SOFTWARE_TARGET.md`](PS5_SYSTEM_SOFTWARE_TARGET.md) §3.1, the
version-record rules are in
[`playstation/FIRMWARE_VERSIONS.md`](playstation/FIRMWARE_VERSIONS.md), and the
policy is in [`SECURITY.md`](SECURITY.md). A run is inconclusive unless the hash
matches the declared object id; if it does not, no claim may cite it.

Until then this document says what it says: the file is identified, its payload is
absent, and its contents are unknown.
