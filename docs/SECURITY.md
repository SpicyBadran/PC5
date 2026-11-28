# Security

## 1. Position

PC5 is an interoperability project. It does not break copy protection, does not
distribute Sony firmware or keys, and does not implement mechanisms to bypass
authentication, signature checks, DRM or secure boot. Where a capability cannot
be built legitimately, PC5 records the limitation instead of working around it.

Concretely, PC5 will not:

* include, embed or redistribute Sony firmware images, decryption keys, or
  derived binaries,
* patch signature checks in firmware, packages or executables,
* spoof a console identity to reach PlayStation Network or any other Sony
  service,
* claim a compatibility level it has not measured.

`tools/firmware.py` identifies containers and computes hashes. It contains no
decryption code and refuses to describe encrypted payloads as parsed.

## 2. Trust boundaries in the current build

| Component | Runs as | Trust |
|---|---|---|
| `boot/loader.S` | firmware-loaded real-mode code | trusted; 4 KiB, single file, auditable |
| kernel | ring 0, no paging, no user mode | trusted; no external input except keyboard/serial |
| serial console | operator | unauthenticated by design — physical access is required |
| host tools | user's account | `pc5ctl usb` re-verifies the checksum and refuses `/` |
| ISO contents | read-only medium | verified structurally by `make verify` |

There is no network stack, no user mode and no storage driver in any boot path
yet (the ATA write path from `M1d`/`M1g`/`M1i` runs only on explicit operator
commands against emulated disks), so the attack surface is the firmware handoff,
the serial console and the build tools.

## 3. Boot integrity

* The loader verifies a kernel signature (`PC5K`) before jumping.
* The boot descriptor carries a CRC32 over its own fields; the kernel reports it
  on the Diagnostics page and the verifier recomputes it from the image.
* `build-info.json` records SHA-256 per file; `make verify` re-checks the bytes.

These detect corruption and mistakes. They are **not** a signature chain: there
is no signing key and no Secure Boot integration yet, and the CRC32 is not a
security primitive.

## 4. Firmware and system-software handling policy

1. Firmware is never committed (`.gitignore` excludes `firmware/`).
2. A user's image is analysed locally; only a short summary string is compiled
   into a build.
3. Logs never contain firmware bytes, only hashes and container identifiers.
4. The public repository and its releases contain no restricted material, so
   the ISO is redistributable.
5. The PS5 system-software package follows the same rule and stricter (ADR-022):
   the repository holds a 135-byte git-lfs **pointer** and a hash-and-size
   record, never the 1.33 GiB payload. There is no decryption, no key handling
   and no signature bypass anywhere in the tree — `tools/firmware.py` identifies
   containers and computes hashes, and refuses to go further.
6. ADR-022 is enforced twice: by review, and by
   `tests/test_product_identity.py`, which asserts that the pointer's declared
   object id and size are what the documents say and that no payload is present.

## 5. USB writing safety

`pc5ctl usb` requires: an existing block device path, a matching checksum, an
explicit `--yes`, and refuses the device that backs `/`. It prints the target
before writing. `dd` is invoked with `conv=fsync`.

## 5b. The Windows application's surface (`app/`) — what it touches, on the host

Written as source, **never yet executed**: this environment has no Windows and no .NET SDK,
so the table describes the code as written, not behaviour observed on a machine.

| Surface | What PC5 does with it | Boundary as written |
|---|---|---|
| Settings file under `%LOCALAPPDATA%\PC5` | read at start-up, written on save | atomic write to a temporary file then a move; an unparseable file is quarantined as `.bad` and the failure is raised, never silently replaced with defaults |
| Log file `logs\pc5.log` | append with flush per line | the log holds text, hashes and identifiers — never firmware bytes, never a key, never a payload fragment (§4 rules apply unchanged) |
| Windows registry | one read: the display adapter's `DriverDesc`, for the diagnostics pane | read-only, under `HKLM\HARDWARE\DEVICEMAP\VIDEO`; no write, no elevation, and a failure yields `unknown` with a reason rather than an empty answer |
| `WinHvPlatform.dll` | `NativeLibrary.TryLoad`, to report that the API is present | loadability is reported as loadability. PC5 does not create a partition, does not enable the Windows feature, and does not treat a successful load as "virtualisation works" (ADR-037) |
| Packaging (MSIX) | the project declares `WindowsPackageType=MSIX` | no certificate, no signing identity and no key material exists in this repository, and signing is an owner action (`M29`) |
| Guest memory / guest code | none yet: the only shipped backend runs a PC5-authored test workload | a backend must declare `MaximumEvidenceLevel` before it can be selected, so no engine can report a capability the tree has not implemented |

Two rules that apply to anything added under `app/`: an over-claim in a diagnostics surface is
a **security defect**, not a cosmetic one (see `docs/engineering/WINDOWS_VALIDATION.md` §6),
and no path may read, write or hash a firmware payload inside the repository — the input stays
outside the tree and outside version control, exactly as in §4.

## 6. Planned security work

| Item | Status |
|---|---|
| Signed PC5 updates (Ed25519 manifest + signature) | RESEARCH REQUIRED |
| Signing key management and offline signing | RESEARCH REQUIRED |
| Secure Boot shim / enrolment instructions | RESEARCH REQUIRED |
| Cryptographic boot chain (loader → kernel) | RESEARCH REQUIRED |
| Service isolation (needs user mode and a real kernel) | BLOCKED on ADR-003 exit |
| Audit logging of privileged actions | IN PROGRESS (boot log exists) |

## 7. Reporting a security issue

Open a private security advisory on the repository rather than a public issue.
Do not attach firmware images, keys or dumps of proprietary code.
