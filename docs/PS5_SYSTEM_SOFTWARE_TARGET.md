# The PS5 system-software target — what the protected input is, and what it is not

**PC5 — An independent operating-system project for PC hardware, built toward genuine Sony PlayStation 5 system software.** This document
is part of the destination workstream (WS10): it establishes what the protected
input is before anything is built around it. Architecture authority:
[`architecture/PC5_ARCHITECTURE_SPEC_v2.md`](architecture/PC5_ARCHITECTURE_SPEC_v2.md).

| | |
|---|---|
| Status | baseline record and rules of engagement. **No capability is claimed here.** |
| Milestone | `M9` (baseline and provenance), `M17` (security boundary), `M18`–`M21` (load, boot, runtime, install) |
| Decisions | ADR-019 (destination), ADR-022 (protected input), ADR-007 (no firmware, keys or derived binaries) |
| Related | [`playstation/BOOT_FEASIBILITY.md`](playstation/BOOT_FEASIBILITY.md), [`architecture/PS5_PLATFORM_CONTRACT.md`](architecture/PS5_PLATFORM_CONTRACT.md), [`SECURITY.md`](SECURITY.md) |

This document exists so that the project cannot drift into treating a file it
has never read as a solved problem, and cannot drift the other way either: the
package is the **target input of the mission**, not a curiosity.

---

## 1. What is in the repository (measured, 2026-11-15)

| Property | Value | How it was established |
|---|---|---|
| path | `PS5UPDATE.PUP` (repository root) | `ls` |
| what the working tree contains | a **Git LFS pointer**, 135 bytes | `od -c`, `wc -c` |
| pointer SHA-256 | `6e8f5a459551316397f1946cce4d540a56c0b790854c2c0d94c442ca86e73b42` | `sha256sum` |
| declared object id | `42ea0e5aad1361e098837d40157863c4c519f0bbc66eb03c04dbaaf339b063fb` | pointer content |
| declared size | **1,423,697,408 bytes** (≈ 1.33 GiB) | pointer content |
| object present in LFS storage | **yes** — the batch API answers with a `download` action for that oid and size | `POST /SpicyBadran/pc5.git/info/lfs/objects/batch` |
| payload present in this checkout | **no** | `tools/firmware.py analyze` → `size 135 bytes`, `kind unidentified`, entropy 4.924 |
| consumed by any tool in the tree | **no** | no reference outside documentation |

The pointer is not the package. Every statement in this repository about the
package's *contents* is therefore `NOT VERIFIED`, and stays that way until the
bytes are lawfully available — which, per ADR-022, means not by pulling them into
this repository.

### 1.1 What is not established, and must not be assumed

| Question | Status |
|---|---|
| Is this the **full fresh-installation / reinstallation** package rather than an incremental update? | **NOT VERIFIED** — cannot be told from a filename or a pointer |
| Which system-software version does it carry? | **NOT VERIFIED** |
| Is it retail-signed, or a devkit/testkit variant? | **NOT VERIFIED** |
| What is its internal structure (container, segments, table of contents)? | **NOT VERIFIED** — no bytes were read |
| Does it contain a kernel, a root filesystem, or only a delta? | **NOT VERIFIED** |
| Is it complete enough for a first boot without another source? | **NOT VERIFIED**, and the working assumption for the architecture is *no* |
| Does its declared hash match the bytes? | **NOT VERIFIED** — the object was not fetched |

The declared size is consistent with the size class of a full PS5
system-software update/reinstallation package, and that is the *only* thing the
size supports. The directive describes it as the fresh-installation package; the
architecture records that as the **intended** role (`M21`) and not as a measured
property (`M9`).

---

## 2. What the package is, architecturally

It is a **console updater's payload**, not a disk image and not a bootable medium:

* a console's own updater validates it, writes it, and the console's platform
  firmware decides what runs. The package carries the system software; it does
  not describe how to *start* it on arbitrary hardware.
* a PC has no updater for it and no platform firmware that would accept it. That
  is the whole distance between "a file exists" and "the system software runs".
* therefore the PC5 architecture treats it as **an input to a platform model**
  (ADR-019 §6), not as something to be written to a USB stick and booted.

---

## 3. Rules of engagement (ADR-022, summarised)

1. The payload is never committed, extracted into the tree, redistributed or
   modified. Identity is recorded by hash and size only.
2. No decryption, no key handling, no signature bypass, no authentication
   circumvention — for this package or any Sony artifact.
3. Any analysis produces **hashes and structure**, runs in a workspace outside
   this repository, and is recorded as a summary in `docs/playstation/`.
4. The original artifact is never overwritten. A record states its hash before
   and after any operation that touches it.
5. Nothing here is distributed to a user; PC5 ships no Sony bytes at all.

### 3.1 Analysis runbook (for an owner-supplied copy, outside the tree)

```sh
# 1. Establish identity first, in a workspace outside the repository.
sha256sum /secure/PS5UPDATE.PUP
stat -c '%s bytes' /secure/PS5UPDATE.PUP

# 2. Identify the container from documented signatures. No decryption.
python3 tools/firmware.py analyze --input /secure/PS5UPDATE.PUP \
    --json-out /secure/pup-analysis.json

# 3. Record the summary only: hash, size, container kind, entropy, entry names
#    that are visible in a plaintext header. Never the payload.
```

A run is **inconclusive** unless the hash matches the declared oid; if it does
not, the file is not the object the repository points at and no claim may cite it.

---

## 4. What running the genuine system software would require

Nine requirements, each classified. "Adaptable" means *there is engineering to
do*; it does not mean it is done, easy, or lawful to obtain.

| # | Requirement | Class | Where it is addressed |
|---|---|---|---|
| 1 | The system software's outer container understood | adaptable with lawful input | `M9`, `tools/firmware.py` |
| 2 | A lawful, authenticated way to obtain its inner components | **restricted** | `M17`; no circumvention (ADR-022) |
| 3 | A trust anchor equivalent to the console's security processor | **Sony-specific silicon** — may be impossible | `M17` |
| 4 | Platform initialisation matching the console's expectations | **undocumented** — contract must be reconstructed | `M10`, `M11` |
| 5 | CPU execution: Zen 2, SMT, privilege model, CPUID surface | **adaptable** — same ISA family, different platform contract | `M11` |
| 6 | Unified memory and GPU-visible mappings | **adaptable**, with a genuine architectural difference | `M12` |
| 7 | Storage: the console's NVMe-class device and its I/O contract | **adaptable**, partly undocumented | `M13` |
| 8 | GPU: command processor, firmware interface, shaders | **adaptable in shape, undocumented in detail** | `M14` |
| 9 | Audio, USB/controller, service devices (timers, IPC, RTC), system services | **adaptable** | `M15`, `M16`, `M19`–`M20` |

Two consequences follow, and both are recorded in the architecture rather than
left implicit:

* requirements 1–9 are all **PC5 work**, and most of them are useful on their own
  (a platform model is a platform model);
* requirement 2 and requirement 3 are **not** PC5 work. They are external
  preconditions. The architecture builds up to them and states plainly that
  level 5 stays `blocked` until one of them is satisfied lawfully.

---

## 5. The clean-installation sequence, and which steps are console-bound

The directive asks for the genuine installation process to be understood. This is
what is public, what follows from it, and what is unknown — in the order a console
performs it.

| # | Step | Public knowledge | Console-bound? | PC5 relevance |
|---|---|---|---|---|
| 1 | Platform and firmware requirements | the console's platform firmware validates the medium and the package | **yes** | the model must present an equivalent platform (M17, M10) |
| 2 | Package recognition | the updater recognises the package by its header and signature | **yes** | recognition is `M9`/`M18` work; signature validation is `M17` |
| 3 | Storage preparation | the console prepares its own internal storage | partly | `M21` prepares PC5 storage and hands it to the guest |
| 4 | System-software installation | the updater writes the system software into that storage | **yes**, in the console's own layout | the model must expose a device the guest's updater can drive (M13) |
| 5 | Required boot components | the platform firmware boots what was installed | **yes** | `M19` |
| 6 | Kernel and system initialisation | the installed system's kernel starts and initialises devices | partly (device contracts) | `M11`–`M16` |
| 7 | System-service initialisation | services start in the console's order over its IPC | **undocumented** | `M16`, `M20` |
| 8 | First successful system boot | the home interface appears | **yes** | `M20` |

Steps 1, 2, 4, 5 and 8 are where a console's identity is checked. PC5 cannot
"skip" them: either the model satisfies them lawfully (`M17`) or level 5 does not
happen. What PC5 *can* do without them is everything in §6.

## 6. What PC5 can build before any of that is resolved

This is the point of the reorganisation: the destination is blocked, the work
towards it is not.

1. The platform contract, device by device, with unknowns marked
   ([`architecture/PS5_PLATFORM_CONTRACT.md`](architecture/PS5_PLATFORM_CONTRACT.md)).
2. The virtual-platform core: CPU, privilege model, memory, interrupts (`M11`,
   `M12`), exercised with PC5-authored conformance payloads.
3. Storage, graphics, audio, USB and service device models (`M13`–`M16`), each
   with a conformance suite.
4. A loader that validates an input it is given and refuses a tampered one, using
   a synthetic package PC5 builds itself (`M18`).
5. The trust analysis (`M17`) — the one that decides whether level 5 is reachable
   at all, published whatever the answer is.

---

## 7. What evidence would count as "the genuine system software is running"

Fixed now, before anyone is tempted by a screenshot:

1. output produced by the **software itself** (its console, its own version/build
   string, its own service list), captured from the guest, not drawn by PC5;
2. the platform model identified, configured and versioned in the record;
3. the input's hash recorded, matching the object the run used;
4. a procedure another engineer can follow without private assets, except the
   lawful package itself;
5. a falsification attempt: the same procedure on a configuration the model does
   not support fails in the predicted way.

Satisfying 1–5 is level 5. A PC5 kernel boot, a modelled environment running PC5
payloads, an interface that merely resembles the console's, or an emulator menu PC5 drew, are **not**
level 5, and `product.destination.must_not_be_satisfied_by` in
`docs/architecture/roadmap.json` lists exactly that.

---

## 8. Current answer to the three questions the directive asks most directly

| Question | Answer today |
|---|---|
| What does the package contain? | **unknown** — a 1.33 GiB LFS object, never fetched, never analysed here |
| Could PC5 install it on a PC today? | **no**, and nothing in the tree claims it could |
| What is the most practical next milestone? | `M10` (platform contract) alongside `M11` (virtual-platform core), because both are buildable without Sony bytes and both are required by everything after them |
