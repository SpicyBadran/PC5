#!/usr/bin/env python3
"""Boot the real PC5 ISO under genuine OVMF/UEFI firmware and judge the result.

What this harness is for
------------------------
Everything else in this repository that talks about UEFI either inspects bytes
(tools/eficheck.py), executes the loader against a *stub* firmware
(tests/uefi_stubtest.py), or boots the BIOS path (tests/boot_test.py).  None of
those can tell you whether real firmware hands the machine over to the PC5
kernel.  This harness does exactly that and nothing else:

    build/pc5.iso  +  OVMF_CODE.fd (real TianoCore firmware)
                   +  a fresh writable OVMF_VARS.fd
                   ->  QEMU  ->  serial log  ->  verdict

It never substitutes a stub, never fakes a success marker and never treats a
missing firmware as a pass: with no firmware or no emulator the run ends with
exit code 3 ("NOT TESTED") and a report that says so.

Firmware is an external dependency owned by the machine that runs the test.  No
firmware image is committed to this repository, copied into the ISO or linked
into the kernel; look for it in the usual places or point the harness at it:

    PC5_OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
    PC5_OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
    PC5_QEMU=qemu-system-x86_64

Usage
-----
    python3 tests/ovmf_boot.py --iso build/pc5.iso --json-out build/ovmf-report.json
    python3 tests/ovmf_boot.py --list-firmware      # what discovery can see

Exit codes: 0 = real UEFI boot verified, 1 = verified failure, 3 = NOT TESTED
(no emulator, no firmware, or a firmware that is not OVMF).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")

# Firmware candidates, most specific first.  These are the paths Debian,
# Fedora, Arch and openSUSE use for the Open Virtual Machine Firmware.
CODE_CANDIDATES = [
    os.environ.get("PC5_OVMF_CODE", ""),
    "/usr/share/OVMF/OVMF_CODE_4M.fd",
    "/usr/share/OVMF/OVMF_CODE.fd",
    "/usr/share/ovmf/OVMF_CODE.fd",
    "/usr/share/edk2/ovmf/OVMF_CODE.fd",
    "/usr/share/edk2/x64/OVMF_CODE.4m.fd",
    "/usr/share/qemu/OVMF_CODE.fd",
    "/usr/share/OVMF/OVMF_CODE.secboot.fd",
    "/usr/share/edk2-ovmf/x64/OVMF_CODE.fd",
]
SECBOOT_CODE_CANDIDATES = [
    os.environ.get("PC5_OVMF_SECBOOT_CODE", ""),
    "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd",
    "/usr/share/OVMF/OVMF_CODE.secboot.fd",
    "/usr/share/edk2/ovmf/OVMF_CODE.secboot.fd",
    "/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd",
    "/usr/share/ovmf/OVMF_CODE.secboot.fd",
    "/usr/share/edk2-ovmf/x64/OVMF_CODE.secboot.fd",
    "/usr/share/qemu/OVMF_CODE.secboot.fd",
]
SECBOOT_VARS_CANDIDATES = [
    os.environ.get("PC5_OVMF_SECBOOT_VARS", ""),
    "/usr/share/OVMF/OVMF_VARS_4M.ms.fd",
    "/usr/share/OVMF/OVMF_VARS.secboot.fd",
    "/usr/share/edk2/ovmf/OVMF_VARS.secboot.fd",
    "/usr/share/edk2/x64/OVMF_VARS.secboot.4m.fd",
    "/usr/share/ovmf/OVMF_VARS.secboot.fd",
    "/usr/share/edk2-ovmf/x64/OVMF_VARS.secboot.fd",
    "/usr/share/qemu/OVMF_VARS.secboot.fd",
]
VARS_CANDIDATES = [
    os.environ.get("PC5_OVMF_VARS", ""),
    "/usr/share/OVMF/OVMF_VARS_4M.fd",
    "/usr/share/OVMF/OVMF_VARS.fd",
    "/usr/share/ovmf/OVMF_VARS.fd",
    "/usr/share/edk2/ovmf/OVMF_VARS.fd",
    "/usr/share/edk2/x64/OVMF_VARS.4m.fd",
    "/usr/share/qemu/OVMF_VARS.fd",
    "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd",
]

# The marker the harness prints when *its own* judgement is a real UEFI boot.
# It is emitted by this process, after the checks pass, and never by the guest.
SUCCESS_MARKER = "PC5-OVMF-BOOT-OK"

# Firmware-level text EDK II prints when it cannot start anything.  Used to
# attribute a negative run to the firmware rather than to the bootloader.
FIRMWARE_NO_BOOT = (
    "no bootable option",
    "failed to load Boot0001",
    "not found",
    "BdsDxe: failed",
    "returned from EfiBootManagerBoot",
)


# ---------------------------------------------------------------- utilities
def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_serial(text):
    """De-duplicate the firmware console mirror.

    OVMF builds its console out of the same 16550 the loader writes to, so
    every line the loader prints before ExitBootServices appears twice, in
    order, once from each sink.  Consecutive duplicates are therefore firmware
    behaviour, not a loader bug; collapse them before matching.
    """
    lines = []
    for raw in re.split(r"[\r\n]+", text):
        line = raw.strip("\x00 ")
        if not line:
            continue
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return "\n".join(lines)


def find_first(candidates):
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def qemu_data_dir(qemu):
    """Where QEMU keeps its option ROMs, when it is not using the built-in path."""
    real = os.path.realpath(qemu)
    base = os.path.dirname(real)
    for candidate in (os.path.join(base, "..", "share", "qemu"),
                      os.path.join(base, "..", "lib", "qemu")):
        if os.path.isdir(candidate):
            return os.path.normpath(candidate)
    return None


# ---------------------------------------------------------------- firmware
def describe_firmware(code_path):
    """Why we believe this file is real OVMF, or why we do not.

    A firmware volume starts with a zeroed 16-byte header followed by the FFS
    signature and a GUID: a real CODE image carries `_FVH` at offset 0x28.  The
    check exists so a truncated download, an empty file or (worst of all) the
    PC5 ESP passed in by mistake is rejected instead of producing a meaningless
    "boot" result.
    """
    info = {"path": code_path, "size": os.path.getsize(code_path),
            "sha256": sha256_file(code_path)}
    with open(code_path, "rb") as handle:
        head = handle.read(0x40)
    info["is_firmware_volume"] = head[0x28:0x2C] == b"_FVH"
    info["volume_size"] = struct.unpack_from("<Q", head, 0x20)[0] if len(head) >= 0x30 else 0
    problems = []
    if not info["is_firmware_volume"]:
        problems.append("no '_FVH' firmware-volume signature at offset 0x28")
    if info["size"] < 512 * 1024 or info["size"] > 16 * 1024 * 1024:
        problems.append(f"implausible firmware size {info['size']} bytes")
    # Our own boot application must never be mistaken for firmware.
    with open(code_path, "rb") as handle:
        if b"PC5 UEFI LOADER" in handle.read():
            problems.append("the file contains the PC5 loader banner")
    info["problems"] = problems
    info["ok"] = not problems
    return info


def discover(args):
    qemu = args.qemu or os.environ.get("PC5_QEMU") or shutil.which("qemu-system-x86_64")
    code = args.ovmf_code or find_first(CODE_CANDIDATES)
    vars_template = args.ovmf_vars or find_first(VARS_CANDIDATES)
    return {"qemu": qemu, "code": code, "vars": vars_template}


def qemu_version(qemu):
    if not qemu:
        return None
    try:
        out = subprocess.run([qemu, "-version"], capture_output=True, timeout=60,
                             text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"error: {exc}"
    first = (out.stdout or out.stderr or "").strip().splitlines()
    return first[0] if first else "unknown"


# ---------------------------------------------------------------- QEMU run
def run_qemu(qemu, data_dir, iso, vars_path, serial_path, timeout, memory_mb=256,
             stop_markers=("PC5-BOOT-OK", "PC5-UEFI-FAILURE"), extra_wait=None,
             secure=False):
    """Boot the ISO once and stop the machine as soon as the log settles.

    Serial goes to stdout rather than to a file because QEMU buffers a file
    chardev: a harness that reads the file while the guest runs sees nothing and
    would have to wait for the whole deadline every time.  Stopping on the
    evidence is also what "clean shutdown" means here - the machine is powered
    off on purpose, and the log is written out either way.
    """
    # Secure Boot needs SMM and a "secure" flash, otherwise the firmware
    # silently runs with its policy disabled and a refusal becomes meaningless.
    machine = "q35,smm=on" if secure else "q35"
    command = [
        qemu,
        "-machine", machine,
        "-m", str(memory_mb),
        "-smp", "1",
        "-no-reboot",
        "-display", "none",
        "-monitor", "none",
        "-nic", "none",
        "-rtc", "base=utc",
        "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=%s" % iso["code"],
        "-drive", "if=pflash,format=raw,unit=1,file=%s" % vars_path,
        "-cdrom", iso["iso"],
        "-serial", "stdio",
    ]
    if secure:
        command += ["-global", "driver=cfi.pflash01,property=secure,value=on"]
    if data_dir:
        command += ["-L", data_dir]

    started = time.time()
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    chunks = []

    def drain(stream, sink):
        for line in iter(stream.readline, b""):
            sink.append(line.decode("latin-1", "replace"))

    import threading
    stdout_thread = threading.Thread(target=drain, args=(process.stdout, chunks), daemon=True)
    stderr_chunks = []
    stderr_thread = threading.Thread(target=drain, args=(process.stderr, stderr_chunks), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    outcome = "timeout"
    deadline = started + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            outcome = "exited"
            break
        text = "".join(chunks)
        if any(marker in text for marker in stop_markers):
            outcome = "evidence"
            break
        time.sleep(0.5)
    else:
        outcome = "timeout"

    if process.poll() is None:
        if extra_wait:
            time.sleep(extra_wait)
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    stdout_thread.join(timeout=10)
    stderr_thread.join(timeout=10)
    elapsed = time.time() - started

    raw = "".join(chunks)
    with open(serial_path, "w") as handle:
        handle.write(raw)
    return {"command": command, "returncode": process.returncode,
            "killed": outcome == "timeout", "stopped_on": outcome,
            "seconds": round(elapsed, 1),
            "stderr": "".join(stderr_chunks).strip()[-400:],
            "raw_serial": raw, "serial": normalise_serial(raw)}



# --------------------------------------------------------------------------
# Boot profiles: what each release artifact prints, and so what it can be asked
# --------------------------------------------------------------------------
#
# One tree builds two release ISOs.  `PC5_UEFI=32` hands the kernel to a 32-bit
# protected-mode entry; `PC5_UEFI=64` enters long mode and runs `kernel64`.  Both
# ESPs carry `EFI/BOOT/BOOTX64.EFI` -- the *loader* is a 64-bit PE on both paths,
# and the 32-bit label describes the kernel -- so what distinguishes the two
# artifacts on the medium is the kernel file: `KERNEL.BIN` against `KERNEL64.BIN`.
#
# The marker sets do not overlap, so a gate written against one cannot see the
# other: until this file knew the `PC5-64-*` words, a 64-bit ISO run through the
# 32-bit checks would have "passed" by finding no `PC5-BOOT-OK`... which is the
# *failure* condition, reported as a pass.  Each profile below therefore owns its
# banner, its success markers, its stop markers, its failure-code pattern, and the
# mapping from its log to the stage names the gate asserts on.

class Profile:
    """One boot path's vocabulary."""

    def __init__(self, key, label, kernel_name, kernel_fat, success, stop,
                 failure_pattern, protocol_version, no_kernel_code,
                 bad_signature_code, uefi_path):
        self.key = key
        self.label = label
        self.kernel_name = kernel_name          # name inside the ISO/ESP
        self.kernel_fat = kernel_fat            # same, in FAT dir form
        self.success = success                   # all must appear: PC5 said so
        self.stop = stop                        # stop the machine on these
        self.failure_pattern = failure_pattern
        self.protocol_version = protocol_version
        # The code each path prints for these two faults.  `None` means the path
        # cannot report it yet, and the control is then recorded as NOT TESTED
        # rather than quietly dropped or passed on absence of evidence.
        self.no_kernel_code = no_kernel_code
        self.bad_signature_code = bad_signature_code
        # How the build record inside the ISO spells this path (`uefi-path: 32`),
        # which is the only place an image says what it is once it has left the tree.
        self.uefi_path = uefi_path

    def no_kernel_check(self):
        return ("loader reports the missing %s as %s when no volume has it "
                "(bootloader fault)" % (self.kernel_name, self.no_kernel_code))

    def bad_signature_check(self):
        """The corrupt-kernel control's name, phrased in what this path can say.

        A path whose kernel image has no header yet gets a name that says so: the
        control still appears in the matrix, as NOT TESTED, so the gap is a finding
        rather than an omission.
        """
        if self.bad_signature_code is None:
            return ("loader refuses a kernel with a corrupted header (bootloader "
                    "fault): %s has no header field to corrupt yet" % self.kernel_name)
        return ("loader refuses a kernel with a corrupted header (bootloader fault) "
                "as %s" % self.bad_signature_code)

    def markers(self, text):
        if self.key == "x64":
            return _markers_x64(text, self)
        return _markers_ia32(text, self)


IA32 = Profile(
    key="ia32",
    label="32-bit kernel path (KERNEL.BIN)",
    kernel_name="KERNEL.BIN",
    kernel_fat="\\KERNEL.BIN",
    success=("PC5-BOOT-OK",),
    stop=("PC5-BOOT-OK", "PC5-UEFI-FAILURE"),
    failure_pattern=r"PC5-UEFI-FAILURE ([A-Z0-9]+)",
    protocol_version=1,
    no_kernel_code="E2",
    bad_signature_code="E4",
    uefi_path="32",
)

X64 = Profile(
    key="x64",
    label="64-bit kernel path (KERNEL64.BIN)",
    kernel_name="KERNEL64.BIN",
    kernel_fat="\\KERNEL64.BIN",
    # Both lines, in this order: LONG-MODE-OK is the mode contract, DONE is the
    # kernel reaching its own end.  Requiring both is what keeps a half-boot -- a
    # kernel that prints the mode banner and then wedges -- from counting.
    success=("PC5-64BIT-LONG-MODE-OK", "PC5-64-DONE"),
    stop=("PC5-64-DONE", "PC5-64-LOADER-FAIL"),
    failure_pattern=r"PC5-64-LOADER-FAIL=([a-z0-9-]+)",
    protocol_version=2,
    no_kernel_code="kernel-not-found",
    # Set when the 64-bit image format carries a header the loader can check;
    # until then the corrupt-kernel control on this path has nothing to assert.
    bad_signature_code=None,
    uefi_path="64",
)

PROFILES = {profile.key: profile for profile in (IA32, X64)}

#: The ESP image each path was released from, when the caller named none.
#:
#: Both flavors are written to `$(BUILD)/pc5.iso`: the release target is
#: `make PC5_UEFI=64 iso`, which changes the ESP *contents*, not the file name.  So
#: the name cannot say which artifact this is and the medium has to be read -- one of
#: the two reasons `detect_profile` exists.
#:
#: The images are not interchangeable, and neither is the pair the 32-bit gate used
#: before there was a 64-bit path: `build/efi.img` is the 32-bit release ESP, and
#: `build/efi64.img` is the *bare* `make uefi64` artifact the stub gate runs, which
#: lacks README.TXT.  What the release ISO embeds is `build/efi64-release.img`, so
#: that is what an ESP-level check has to compare against.  Grading the 64-bit ISO
#: against the 32-bit ESP -- or the 64-bit gate against a stub-only image -- is the
#: substitution this profile machinery exists to make impossible.
ARTIFACTS = {
    "ia32": {"iso": "pc5.iso", "efi_image": "efi.img"},
    "x64": {"iso": "pc5.iso", "efi_image": "efi64-release.img"},
}


def profile_by_key(name):
    try:
        return PROFILES[name]
    except KeyError:
        raise SystemExit("unknown boot profile %r (choose %s)"
                         % (name, ", ".join(sorted(PROFILES))))


def esp_directory(iso_path):
    """The El Torito ESP's file names, read the way firmware reads them.

    The release ISO9660 root always carries a flat `KERNEL.BIN;1` -- it is the copy
    the BIOS path uses -- so the ISO9660 directory says nothing about which boot path
    the medium is.  The ESP does: it is the volume firmware actually loads, and it
    holds exactly the kernel file its loader looks for.

    Raises `ValueError` when the medium cannot be parsed: the caller has to say so.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import iso_manifest  # noqa: E402  (path set above)
        import mkefiimg  # noqa: E402
    except ImportError as exc:
        raise ValueError("the ISO reader is unavailable: %s" % exc)
    with open(iso_path, "rb") as handle:
        blob = handle.read()
    try:
        volume = mkefiimg.FatVolume(esp_image_in_iso(blob))
    except Exception as exc:
        # A medium we cannot walk is a finding for the caller to report.  Raising here
        # would exit 1 with a traceback -- indistinguishable in CI from an
        # infrastructure accident -- so it becomes a value the gate has to handle.
        raise ValueError("cannot read the ESP of %s: %s" % (iso_path, exc))
    return {name: meta["data"] for name, meta in volume.walk().items()}


def esp_image_in_iso(blob):
    """The ESP image the El Torito catalog points at, exactly as firmware loads it.

    The catalog addresses the boot image in 2048-byte sectors but sizes it in 512-byte
    ones; reading the length as 2048-blocks asks for four times the volume and walks off
    the end of the image.  Returning the bytes rather than only the names is what lets
    `check_release_esp` compare the medium against the file the build produced.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import iso_manifest  # noqa: E402  (path set above)
    entries = iso_manifest.boot_catalog(blob)
    uefi = [entry for entry in entries
            if entry.get("platform") == "uefi" and entry.get("bootable")]
    if not uefi:
        raise ValueError("the boot catalog has no bootable UEFI entry")
    entry = uefi[0]
    start = entry["load_rba"] * 2048
    return blob[start:start + entry["sector_count"] * 512]


def check_release_esp(report, iso_path, efi_image):
    """The medium has to carry the ESP the build produced, byte for byte.

    Without this, a mismatch between the two is only visible as several unrelated
    failures -- the sizes of two different kernels disagreeing, a mutant that changes
    nothing -- and each of those reads like a defect in the boot rather than in the
    pairing.  This is the check that says what actually went wrong: the gate was pointed
    at one artifact and booted another.

    Byte equality, not "same file names, same sizes": the release ESP is embedded
    verbatim by `tools/mkiso.py --uefi-image`, so padding, a directory order, or a
    stale README is a different artifact, and a release that cannot say which one it
    ships is a release whose digest describes nothing.
    """
    if not (efi_image and os.path.isfile(efi_image) and os.path.isfile(iso_path)):
        report.check("the ISO embeds the release ESP the build produced", False,
                     "no ESP image or no ISO to compare", applicable=False)
        return
    with open(iso_path, "rb") as handle:
        blob = handle.read()
    try:
        embedded = esp_image_in_iso(blob)
    except Exception as exc:
        report.check("the ISO embeds the release ESP the build produced", False,
                     "the medium's ESP cannot be read: %s" % exc)
        return
    with open(efi_image, "rb") as handle:
        built = handle.read()
    same = hashlib.sha256(embedded).hexdigest() == hashlib.sha256(built).hexdigest()
    report.check("the ISO embeds the release ESP the build produced", same,
                 "in the ISO: %d bytes sha256=%s; at --efi-image: %d bytes sha256=%s%s"
                 % (len(embedded), hashlib.sha256(embedded).hexdigest()[:16],
                    len(built), hashlib.sha256(built).hexdigest()[:16],
                    "" if same else " -- this run booted a different image than the "
                                    "gate was pointed at"))
    report.data["release_esp"] = {
        "iso_sha256": hashlib.sha256(embedded).hexdigest(),
        "built_sha256": hashlib.sha256(built).hexdigest(),
        "identical": same,
    }


def detect_profile(iso_path):
    """`(profile, note)` for this medium.  Read the ESP; do not trust the file name.

    Both flavors of the release are written to `build/pc5.iso`, and `make check` builds
    the two into different `BUILD` directories in the same tree, so the file name is no
    evidence.  The ESP root directory is: it is the volume firmware loads, and it holds
    exactly the kernel file the loader on that medium looks for.  Deciding from the
    artifact rather than from a label is what stops the gate from asserting the wrong
    vocabulary over the wrong image.

    `(None, why)` when the medium cannot be read.  Not a guess, and not a crash: a
    caller that silently picked a profile would produce a verdict about a boot path
    nobody ran, and a caller that died would leave no record of what it refused.
    """
    try:
        names = esp_directory(iso_path)
    except ValueError as exc:
        return None, str(exc)
    if "KERNEL64.BIN" in names:
        return X64, "KERNEL64.BIN in the ESP root directory"
    if "KERNEL.BIN" in names:
        return IA32, "KERNEL.BIN in the ESP root directory"
    return None, ("%s has an ESP with neither KERNEL.BIN nor KERNEL64.BIN: %s"
                  % (iso_path, sorted(names)))


#: The image's own claim about what it is, graded against the image.
BUILDINFO_CHECK = "the ISO's build record names the medium it describes"


def iso_buildinfo(iso_path):
    """The `BUILDINFO.TXT` fields inside the image, as a dict.

    Raises `ValueError` when the medium or the reader cannot be used, so the caller
    reports it as a failed check instead of the run dying.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import iso_manifest  # noqa: E402  (path set above)
    except ImportError as exc:
        raise ValueError("the ISO reader is unavailable: %s" % exc)
    try:
        manifest = iso_manifest.read(pathlib.Path(iso_path))
    except Exception as exc:
        raise ValueError("cannot read %s: %s" % (iso_path, exc))
    return dict(manifest.get("buildinfo") or {})


def check_buildinfo(report, iso_path, profile):
    """Does the label in the image match the image?

    A release ISO is attributable only through what it carries: `build:`, `commit:`,
    `uefi-path:`, `esp-loader:` are written by the build into `BUILDINFO.TXT`, which is
    why `$(ISO)` rebuilds when the source commit changes.  None of that can be trusted
    just because it is present, and three mismatches are worth failing for -- none of
    which needs git, a build directory, or the gate's own command line:

    * the record calls the image one architecture while its ESP carries the other's
      kernel, which is a label describing somebody else's artifact;
    * the `esp-loader` digest is not the digest of the loader on the volume, which is
      how a record from a previous build looks;
    * no commit is named, because a release that cannot say what it was built from
      cannot have its evidence checked against anything.

    The parsed record is also copied into `data.environment`, so a saved report says
    which commit it booted even after the workspace has moved on -- and
    `tools/buildmeta.py` refuses to publish a boot verdict for a different image than
    the one it is describing.
    """
    try:
        info = iso_buildinfo(iso_path)
        files = esp_directory(iso_path)
    except ValueError as exc:
        report.data.setdefault("environment", {})["buildinfo"] = None
        report.check(BUILDINFO_CHECK, False, str(exc), applicable=False)
        return
    report.data.setdefault("environment", {})["buildinfo"] = info
    problems = []
    says = (info.get("uefi-path") or "").strip()
    if not says:
        problems.append("the record names no UEFI path")
    elif says != profile.uefi_path:
        problems.append("the record says uefi-path=%s but the ESP carries %s (%s)"
                        % (says, profile.kernel_name, profile.label))
    loader = files.get("EFI/BOOT/BOOTX64.EFI")
    quotes = (info.get("esp-loader") or "").strip()
    if loader is None:
        problems.append("the ESP has no EFI/BOOT/BOOTX64.EFI to compare the record with")
    elif not quotes:
        problems.append("the record quotes no esp-loader digest")
    else:
        actual = hashlib.sha256(loader).hexdigest()[:len(quotes)]
        if quotes != actual:
            problems.append("the record's esp-loader=%s is not the loader on the volume "
                            "(%s)" % (quotes, actual))
    commit = (info.get("commit") or info.get("build") or "").strip()
    if not re.match(r"^[0-9a-f]{7,40}(-dirty)?$", commit):
        problems.append("the record's build label %r does not name a source commit"
                        % commit)
    elif info.get("commit") and not re.match(r"^[0-9a-f]{40}$", commit):
        # `commit:` is the field the build writes from `git rev-parse HEAD`; a short
        # or suffixed value there is a label, not an identifier, and an evidence file
        # that quotes one cannot be re-derived later.
        problems.append("the record's commit=%r is not a full object id" % commit)
    report.check(BUILDINFO_CHECK, not problems,
                 "; ".join(problems) if problems else
                 "build=%s commit=%s uefi-path=%s esp-loader=%s, as the medium has it"
                 % (info.get("build"), info.get("commit"), says, quotes))


def _common_markers(text):
    """Firmware-side facts: the same evidence on either path."""
    return {
        "firmware_banner": ("BdsDxe:" in text) or ("TianoCore" in text) or
                           ("EDK II" in text),
        "seabios": "SeaBIOS" in text,
        "el_torito_boot_option": "UEFI QEMU DVD-ROM" in text or "BdsDxe: starting" in text,
    }


def _markers_ia32(text, profile):
    """The 32-bit kernel path's own lines."""
    return {
        "loader_banner": "PC5 UEFI loader" in text or "PC5 UEFI LOADER" in text,
        "loader_firmware_edk2": "PC5-UEFI: firmware=EDK II" in text,
        "kernel_volume_image": "PC5-UEFI: kernel_volume=image" in text,
        "kernel_read": "PC5-UEFI: kernel_bytes_read=" in text,
        "boot_services_exited": "PC5-UEFI: boot_services_exited" in text,
        "exit_retries": _number(text, r"PC5-UEFI: ExitBootServices_retries=(\d+)"),
        "memory_map_entries": _number(text, r"PC5-UEFI: memory_map_entries=(\d+)"),
        "handover_window_checked": _number(text, r"PC5-UEFI: handover_window_checked=(\d+)"),
        "handoff_verified": "PC5-UEFI: kernel_handoff_verified=1" in text,
        "handoff_bytes": _number(text, r"PC5-UEFI: handoff_bytes=(\d+)"),
        "kernel_bytes_read": _number(text, r"PC5-UEFI: kernel_bytes_read=(\d+)"),
        "conventional_mib": _number(text, r"PC5-UEFI: memory_conventional_mib=(\d+)"),
        "watchdog": ("disabled" if "PC5-UEFI: watchdog_disabled" in text else
                     "refused" if "PC5-UEFI: watchdog_disable_refused=" in text else None),
        # the kernel's own report of what it was handed: independent of the
        # loader's claims, because it is produced after the handover by code
        # the loader never runs.
        "kernel_memory_entries": _number(text, r"PC5-LOG memory_mib=\d+ entries=(\d+)"),
        "kernel_memory_mib": _number(text, r"PC5-LOG memory_mib=(\d+)"),
        "kernel_entry": "PC5-LOG kernel_entry eip=0x10000" in text,
        "kernel_protocol": "PC5-LOG protocol=1 magic=ok" in text,
        # `PC5-UEFI-FAILURE` anywhere vetoes the success marker, whatever the order:
        # a log that says both is a contradiction, and the safe reading of a
        # contradiction is "this boot did not succeed".
        "boot_ok": ("PC5-BOOT-OK" in text and
                    not re.search(r"PC5-UEFI-FAILURE [A-Z0-9]+", text)),
        "failure_codes": sorted(set(re.findall(r"PC5-UEFI-FAILURE ([A-Z0-9]+)", text))),
        "firmware_refused": any(marker.lower() in text.lower()
                                for marker in FIRMWARE_NO_BOOT),
    }


def _markers_x64(text, profile):
    """The 64-bit kernel path's own lines, mapped onto the same stage names.

    The names are shared because the *stages* are shared -- a loader that found its
    kernel, a memory map that was handed over, boot services that were exited --
    even though the two paths print them in different words.  Where the 64-bit path
    has no equivalent line, the key is left out and the gate reports that stage as
    not applicable instead of inventing a pass.
    """
    map_entries = _number(text, r"PC5-64-MAP entries=(\d+)")
    conventional_pages = _number(text, r"PC5-64-MAP entries=\d+ conventional_pages=(\d+)")
    kernel_size = _hex(text, r"PC5-64-IMAGE base=0x[0-9a-f]+ size=(0x[0-9a-f]+) range=ok")
    return {
        "loader_banner": "PC5-64-LOADER-BANNER" in text,
        "loader_firmware_edk2": "PC5-64-LOADER firmware=EDK II" in text,
        "kernel_volume_image": "PC5-64-LOADER kernel-volume=image" in text,
        "kernel_read": "PC5-64-LOADER kernel at=" in text,
        "kernel_bytes_read": _number(text, r"PC5-64-LOADER kernel at=0x[0-9a-f]+ bytes=(\d+)"),
        "boot_services_exited": "PC5-64-LOADER exit-boot-services=ok" in text,
        # The 64-bit loader prints the descriptor count and its own retry count on
        # one line, in the same meaning the 32-bit path splits across two.
        "memory_map_entries": _number(text, r"exit-boot-services=ok attempts=\d+ descriptors=(\d+)"),
        "exit_retries": _number(text, r"exit-boot-services=ok attempts=(\d+)"),
        "handover_window_checked": 1 if "below-span=1" in text else 0,
        "watchdog": ("disabled" if "PC5-64-LOADER watchdog=disabled" in text else
                     "refused" if "watchdog=disable-refused=" in text else None),
        # No loader-side conventional total on this path: the kernel's own
        # `PC5-64-MAP` line is the one that says what the map contained.
        "conventional_mib": None,
        # The kernel re-reads the handover record and verifies the copy it was
        # given: `range=ok` is that verdict, and `PC5-64-BOOTINFO magic=ok
        # version=2` is the contract being accepted.
        "handoff_verified": "range=ok" in text and "PC5-64-IMAGE base=" in text,
        # `size=0x32000` is printed in hex by the kernel; reading it as a decimal
        # is a comparison that silently never matches, so it goes through _hex.
        "handoff_bytes": kernel_size,
        "kernel_memory_entries": map_entries,
        "kernel_memory_mib": (conventional_pages // 256) if conventional_pages else None,
        "kernel_entry": "PC5-64BIT-KERNEL-ENTRY" in text,
        "kernel_protocol": ("PC5-64-BOOTINFO magic=ok version=%d"
                            % profile.protocol_version) in text,
        "boot_ok": (all(marker in text for marker in profile.success) and
                    not re.search(profile.failure_pattern, text)),
        "failure_codes": sorted(set(re.findall(profile.failure_pattern, text))),
        "firmware_refused": any(marker.lower() in text.lower()
                                for marker in FIRMWARE_NO_BOOT),
        # 64-bit-only stages, asserted by check_x64_stages.
        "idt_installed": _number(text, r"PC5-64-IDT installed=(\d+)"),
        "paging_root": _hex(text, r"PC5-64-PAGING enabled=\d+ root=(0x[0-9a-f]+)"),
        "loader_pt_root": _hex(text, r"PC5-64-LOADER pagetables=(0x[0-9a-f]+)"),
        "paging_pages": _number(text, r"PC5-64-PAGING .*? pages=(\d+)"),
        "loader_pt_pages": _number(text, r"PC5-64-LOADER pagetables=0x[0-9a-f]+ pages=(\d+)"),
        "paging_enabled": _number(text, r"PC5-64-PAGING enabled=(\d+)"),
        "mem_status": ("ok" if "PC5-64-MEM status=ok" in text else
                       re.search(r"PC5-64-MEM status=(\w+)", text).group(1)
                       if re.search(r"PC5-64-MEM status=(\w+)", text) else None),
        "mem_conventional": _number(text, r"PC5-64-MEM status=\w+ reason=\w+ frames=\d+ conventional=(\d+)"),
        "mem_free": _number(text, r"PC5-64-MEM status=\w+ .*? conventional(?:_total)?=\d+ claimed=\d+ free=(\d+)"),
        "mem_selftest_ok": "PC5-64-MEM-SELFTEST" in text and "result=ok" in text,
        "cpu_vendor": re.search(r"PC5-64-CPU vendor=(\S+)", text).group(1)
                      if re.search(r"PC5-64-CPU vendor=(\S+)", text) else None,
        "cpu_long_mode": _number(text, r"PC5-64-CPU long_mode=(\d+)"),
        "tr_state": re.search(r"tr=(not-loaded|loaded)", text).group(1)
                    if re.search(r"tr=(not-loaded|loaded)", text) else None,
    }


def classify(run, profile=IA32):
    """What the serial log proves was executed, in this profile's words."""
    text = run["serial"]
    seen = _common_markers(text)
    seen.update(profile.markers(text))
    return seen


def _hex(text, pattern):
    """The first 0x-prefixed number a pattern matches, as an int (None if absent).

    Kept separate from `_number` because these logs mix decimal and hex fields on
    one line (`root=0x1e1f4000 pages=6`), and a decimal reader of a hex field is a
    comparison that can only ever be wrong.
    """
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return int(match.group(1), 16)
    except ValueError:
        return None


def _number(text, pattern):
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------- checks
class Report:
    """Three verdicts, never two.

    A check is PASS, FAIL, or NOT APPLICABLE.  The third state exists because a
    boot path this machine or this ISO does not offer must not be reported as a
    pass - and must not be hidden either.  `fail_count` only counts real
    failures; NOT APPLICABLE is printed and counted separately so a reader
    cannot mistake "we did not test it" for "it worked".
    """

    def __init__(self):
        self.checks = []
        self.data = {}

    def check(self, name, ok, detail="", applicable=True):
        state = "skip" if not applicable else ("pass" if ok else "fail")
        # A reason belongs to a failure *and* to a NOT APPLICABLE verdict.  For a skip
        # the reason is the whole content of the row -- "why was this not tested?" is
        # exactly what a reader of the matrix asks, and `build_matrix` copies it into
        # the row's note -- so dropping it would leave an unexplained gap, which is the
        # thing this harness exists to avoid.  A pass needs no explanation, and
        # printing one for every verified stage would bury the rows that matter.
        self.checks.append({"name": name, "state": state,
                            "detail": str(detail) if state in ("fail", "skip") else ""})

    def fail_count(self):
        return sum(1 for check in self.checks if check["state"] == "fail")

    def skip_count(self):
        return sum(1 for check in self.checks if check["state"] == "skip")

    def as_dict(self):
        return {"checks": self.checks, "data": self.data,
                "passed": self.fail_count() == 0,
                "not_applicable": self.skip_count()}


# ---------------------------------------------------------------------------
# Stages: one table per profile, driving both the checks and the matrix rows
# ---------------------------------------------------------------------------
#
# A row of the required matrix is worth nothing unless it names the check that
# produced it.  Before this table the two were separate lists that happened to agree
# in prose, and `build_matrix` judged every cold row with one blanket predicate
# (`boot_ok and loader_banner and kernel_entry`) -- so a run could print a row as
# VERIFIED while the specific check behind it had failed, and a requirement could be
# renamed in one place without the other noticing.
#
# Now a stage is declared once, as (requirement, check name, proof).  `check_boot`
# emits the check; `build_matrix` reads that check's own state.  A row whose check
# did not run is NOT TESTED, and a row whose check failed is FAILED -- which is what
# makes "missing markers must fail" and "a firmware-only boot is never a success"
# structural rather than a hope: the proof for the kernel-side rows is a `PC5-64-*`
# line, and a boot that only got as far as the firmware has no such line to read.
#
# The proof is a function of the parsed log, not of a counter or an exit status.  It
# reads what the guest said and nothing else.

class Stage:
    """One required stage: the milestone's wording, the check's wording, the proof."""

    def __init__(self, requirement, check, proof):
        self.requirement = requirement
        self.check = check
        self.proof = proof

    def name(self, stage):
        return "[%s] %s" % (stage, self.check)

    def judge(self, seen, ctx):
        return self.proof(seen, ctx)


def _seen(key):
    """The plain case: the log has to contain this fact, and say what it found."""
    def proof(seen, ctx):
        value = seen.get(key)
        return bool(value), "" if value else "%s=%r" % (key, value)
    return proof


def _positive(key):
    """A count that has to exist and be greater than zero."""
    def proof(seen, ctx):
        value = seen.get(key)
        return (value or 0) > 0, "%s=%r" % (key, value)
    return proof


def _equal_to(*keys):
    """Two independent printings of one fact -- the loader's and the kernel's.

    Agreement is the point: the loader can claim any page-table root or descriptor
    count it likes in its own line, and only the kernel, running through them and
    re-reading the map it was handed, says what they actually were.  A number missing
    on either side fails, so a stage cannot pass because the emulator never got far
    enough to print it.
    """
    def proof(seen, ctx):
        values = [seen.get(key) for key in keys]
        ok = all(value is not None for value in values) and len(set(values)) == 1
        return ok, " vs ".join("%s=%r" % (key, value)
                               for key, value in zip(keys, values))
    return proof


def _at_least_256(key):
    """The IDT has to be complete, not merely present: 256 gates, all of them."""
    def proof(seen, ctx):
        value = seen.get(key)
        return value == 256, "%s=%r" % (key, value)
    return proof


#: The two facts the loader's own lines establish.  `_seen` is not enough here:
#: the line is a fact about the *loader*, so it can be reported as satisfied while the
#: boot never got past the firmware.  A negative control whose mutant was not even
#: built therefore fails this row instead of passing it -- which is what makes "no
#: evidence of a failure" unusable as evidence of success.
def _no_failure_codes(seen, ctx):
    """A quiet log is not a healthy one: the absence of a code proves nothing."""
    codes = seen["failure_codes"]
    if not seen["loader_banner"]:
        return False, "the loader never ran, so it had nothing to report"
    return not codes, "failure_codes=%s" % (codes or "none")


def _watchdog_reported(key):
    """The attempt has to be reported; a refusal is a pass with a footnote.

    The property this gate owns is "the watchdog was disarmed *while boot services
    were still callable*", i.e. that the loader tried at the right time and recorded
    what the firmware said.  Whether EDK II then accepted it is the firmware's
    business, so `refused` is reported in the detail and does not fail the run --
    and an absent line, which would mean the loader never tried, does.
    """
    def proof(seen, ctx):
        value = seen.get(key)
        if value not in ("disabled", "refused"):
            return False, "%s=%r (no attempt was reported)" % (key, value)
        return True, "firmware accepted the disarm" if value == "disabled" \
            else "the attempt was reported and refused; see the serial log"
    return proof


#: A proof returns True, False, or NOT_APPLICABLE when the *evidence* is missing
#: for a reason outside the boot: no kernel file was handed to the harness, say.
#: That is recorded as a question nobody answered -- it keeps an incomplete run
#: from being reported as a passing one, and keeps it from being blamed on the boot.
NOT_APPLICABLE = None


def _kernel_size_matches_file(key):
    """The bytes read must be the file's real length, and no more than fits.

    `expected_bytes` comes from the kernel file on disk, so this is a comparison
    against the artifact, not against the loader's own claim.
    """
    def proof(seen, ctx):
        expected = ctx.get("expected_bytes")
        value = seen.get(key)
        if not expected:
            return NOT_APPLICABLE, "no kernel file was given to compare with"
        return value == expected, "%r vs %r" % (value, expected)
    return proof


#: The firmware-side stages, which both paths have to answer for in the same terms.
#: (The paths share `classify`'s `_common_markers`, so these rows are identical; a
#: path that could not produce them is not booting under real firmware at all.)
COMMON_STAGES = [
    Stage("firmware recognises the PC5 boot medium",
          "genuine firmware produced a boot manager log", _seen("firmware_banner")),
    Stage("the firmware is not a BIOS shim",
          "the machine booted UEFI, not SeaBIOS",
          lambda seen, ctx: (seen["firmware_banner"] and not seen["seabios"],
                              "SeaBIOS text present" if seen["seabios"] else "")),
    Stage("the EFI boot entry is discovered",
          "firmware started the El Torito UEFI image",
          lambda seen, ctx: (seen["el_torito_boot_option"] or seen["loader_banner"],
                             "no UEFI boot option in the log")),
    Stage("serial output proves the execution path",
          "the machine was stopped on evidence, not on the clock",
          lambda seen, ctx: (bool(ctx.get("stopped_on")) and not ctx.get("killed"),
                             "%s after %ss" % (ctx.get("stopped_on"), ctx.get("seconds")))),
]


def ia32_stages():
    """The 32-bit kernel path's stages, in the order its loader prints them."""
    return COMMON_STAGES + [
        Stage("the PC5 EFI loader executes",
              "the PC5 UEFI loader executed under it", _seen("loader_banner")),
        Stage("the loader names the firmware it is running on",
              "the loader identified EDK II as its firmware",
              _seen("loader_firmware_edk2")),
        Stage("required files are located and loaded",
              "the kernel was found on the volume we booted from",
              _seen("kernel_volume_image")),
        Stage("the kernel image is read through the file protocol",
              "the kernel image was read through the file protocol", _seen("kernel_read")),
        Stage("the loader read the kernel file at its real size",
              "the loader read the kernel file at its real size",
              _kernel_size_matches_file("kernel_bytes_read")),
        Stage("memory allocation and ownership are correct",
              "a memory map was handed over", _positive("memory_map_entries")),
        # This row used to pin the loader's total to exactly 1 MiB, which was the
        # number the E15 bug produced -- so the assertion would have failed the *fix*
        # and passed the bug.  A stale literal is worse than no assertion.  What is
        # defensible is a bound: the count has to come from the map, so it cannot be
        # zero, and it cannot exceed the memory the machine was given.  The loader's
        # number and the kernel's are both recorded in the detail so a reader can see
        # the two spans (they measure different things: the loader reports the
        # conventional region, the kernel the total usable RAM it was handed), and
        # neither is allowed to silently become a constant again.
        Stage("the conventional-memory report is a real count, not a default",
              "the loader's conventional-memory total is a real count within the "
              "memory the machine was given",
              lambda seen, ctx: _in_machine(seen, ctx, "conventional_mib")),
        Stage("the kernel's own memory report is a real count too",
              "the kernel's memory total is a real count within the memory the "
              "machine was given",
              lambda seen, ctx: _in_machine(seen, ctx, "kernel_memory_mib")),
        Stage("the load window was checked against the memory map (the E15 guard)",
              "the load window was checked against that map (E15 guard)",
              lambda seen, ctx: (seen["handover_window_checked"] == 1,
                                 "handover_window_checked=%r" % seen["handover_window_checked"])),
        Stage("the firmware watchdog is disarmed before handover",
              "firmware watchdog was disabled while services were callable",
              _watchdog_reported("watchdog")),
        Stage("ExitBootServices succeeds with a current map key",
              "boot services were exited exactly once", _seen("boot_services_exited")),
        Stage("ExitBootServices reports how many attempts it needed",
              "ExitBootServices reported its retry count",
              lambda seen, ctx: (seen["exit_retries"] is not None,
                                 "exit_retries=%r" % seen["exit_retries"])),
        Stage("the kernel handoff succeeds",
              "the kernel copy was read back at the handover address",
              _seen("handoff_verified")),
        Stage("the handover carried exactly the kernel file",
              "the handover carried exactly the kernel file",
              _kernel_size_matches_file("handoff_bytes")),
        Stage("the kernel reached its entry point",
              "the kernel reached its entry point", _seen("kernel_entry")),
        Stage("the kernel accepted the boot contract",
              "the kernel accepted the boot contract", _seen("kernel_protocol")),
        Stage("the kernel reaches its expected initialisation point",
              "the kernel reached PC5-BOOT-OK", _seen("boot_ok")),
        Stage("no loader failure was reported",
              "no loader failure code was reported", _no_failure_codes),
    ]


def x64_stages():
    """The 64-bit path's stages: its own words, plus the kernel-side ones only it has.

    The added rows are the ones the milestone asked to be verified, and each is an
    agreement between the loader's line and the kernel's, so none of them can be
    satisfied by firmware initialisation alone: `paging_root`, `mem_conventional` and
    `idt_installed` are read out of `PC5-64-PAGING`, `PC5-64-MEM` and `PC5-64-IDT`,
    which only exist if long mode was entered and the kernel ran far enough to print
    them.  The TR row asserts only what the log can support: the kernel must *state*
    its TR state, and the word it states is recorded verbatim, because a `loaded`
    claim without an architectural read-back is a defect, not a pass.
    """
    return COMMON_STAGES + [
        Stage("the 64-bit PC5 loader executes",
              "the PC5 UEFI loader executed under it", _seen("loader_banner")),
        Stage("the loader names the firmware it is running on",
              "the loader identified EDK II as its firmware",
              _seen("loader_firmware_edk2")),
        Stage("required files are located and loaded",
              "the kernel was found on the volume we booted from",
              _seen("kernel_volume_image")),
        Stage("the kernel image is read through the file protocol",
              "the kernel image was read through the file protocol", _seen("kernel_read")),
        Stage("the loader read the kernel file at its real size",
              "the loader read the kernel file at its real size",
              _kernel_size_matches_file("kernel_bytes_read")),
        Stage("memory allocation and ownership are correct",
              "a memory map was handed over", _positive("memory_map_entries")),
        Stage("the kernel's own map agrees with the loader's count",
              "the kernel's own map agrees with the loader's count",
              _equal_to("kernel_memory_entries", "memory_map_entries")),
        Stage("the load window was checked against the memory map (the E15 guard)",
              "the load window was checked against that map (E15 guard)",
              lambda seen, ctx: (seen["handover_window_checked"] == 1,
                                 "handover_window_checked=%r" % seen["handover_window_checked"])),
        Stage("the firmware watchdog is disarmed before handover",
              "firmware watchdog was disabled while services were callable",
              _watchdog_reported("watchdog")),
        Stage("ExitBootServices succeeds with a current map key",
              "boot services were exited exactly once", _seen("boot_services_exited")),
        Stage("ExitBootServices reports how many attempts it needed",
              "ExitBootServices reported its retry count",
              lambda seen, ctx: (seen["exit_retries"] is not None,
                                 "exit_retries=%r" % seen["exit_retries"])),
        Stage("the kernel handoff succeeds",
              "the kernel copy was read back at the handover address",
              _seen("handoff_verified")),
        Stage("the handover carried exactly the kernel file",
              "the handover carried exactly the kernel file",
              _kernel_size_matches_file("handoff_bytes")),
        Stage("the handover was verified by the kernel that received it",
              "the kernel accepted the boot contract", _seen("kernel_protocol")),
        Stage("the kernel reached its entry point",
              "the kernel reached its entry point", _seen("kernel_entry")),
        Stage("the kernel reaches its 64-bit success marker",
              "the kernel reached PC5-64BIT-LONG-MODE-OK and PC5-64-DONE",
              _seen("boot_ok")),
        Stage("no loader failure was reported",
              "no loader failure code was reported", _no_failure_codes),
        # ---- 64-bit only: paging, exceptions, memory, CPU state
        Stage("the kernel runs on the page tables the loader built",
              "the page-table root the kernel runs on is the one the loader built",
              _equal_to("paging_root", "loader_pt_root")),
        Stage("the page tables the kernel counts are the ones the loader built",
              "the loader and the kernel agree on the page-table size",
              _equal_to("paging_pages", "loader_pt_pages")),
        Stage("paging is enabled in long mode",
              "the kernel reported paging enabled in long mode",
              lambda seen, ctx: (seen["paging_enabled"] == 1,
                                 "paging_enabled=%r" % seen["paging_enabled"])),
        Stage("the IDT is installed and complete before anything can fault",
              "the IDT was installed before anything could fault",
              _at_least_256("idt_installed")),
        Stage("the frame allocator is initialised from the real memory map",
              "the frame allocator was built from the firmware map",
              lambda seen, ctx: (seen["mem_status"] == "ok" and
                                 (seen["mem_conventional"] or 0) > 0,
                                 "status=%r conventional=%r"
                                 % (seen["mem_status"], seen["mem_conventional"]))),
        Stage("the allocator's accounting is consistent with the map",
              "the allocator's free count is its conventional total less what was claimed",
              lambda seen, ctx: (seen["mem_conventional"] is not None and
                                 seen["mem_free"] is not None and
                                 seen["mem_free"] <= seen["mem_conventional"],
                                 "conventional=%r free=%r"
                                 % (seen["mem_conventional"], seen["mem_free"]))),
        Stage("the allocator's self-test runs after the handover",
              "the allocator's own self-test passed after handover",
              _seen("mem_selftest_ok")),
        Stage("the kernel reads real CPU state",
              "the kernel read real CPU state in long mode",
              lambda seen, ctx: (bool(seen["cpu_vendor"]) and seen["cpu_long_mode"] == 1,
                                 "vendor=%r long_mode=%r"
                                 % (seen["cpu_vendor"], seen["cpu_long_mode"]))),
        Stage("the TSS/IST state is reported as measured, never as assumed",
              "the TSS line reports its own state, and does not over-claim TR",
              lambda seen, ctx: (seen["tr_state"] in ("loaded", "not-loaded"),
                                 "tr=%r" % seen["tr_state"])),
    ]


#: The warm boot's own row: it is not a second copy of the cold list, it is the
#: comparison between the two -- the machine must come up the same way twice.
WARM_STAGES = [
    Stage("rebooting the same machine boots identically",
          "the warm boot proved the same stages as the cold boot", None),
]


def profile_stages(profile):
    return x64_stages() if profile.key == "x64" else ia32_stages()


def _in_machine(seen, ctx, key):
    """A memory count that exists, is not zero, and does not exceed the guest.

    `ctx["memory_mb"]` is what the harness asked QEMU for, so this compares the
    loader's and the kernel's claims against the machine that was actually built --
    not against a number remembered from an older bug.
    """
    value = seen.get(key)
    limit = ctx.get("memory_mb")
    if value is None:
        return NOT_APPLICABLE, "%s was not reported" % key
    if limit is None:
        return value > 0, "%s=%r (no machine size to bound it)" % (key, value)
    return 0 < value <= limit, "%s=%r of %r MiB given" % (key, value, limit)


def check_boot(report, run, stage, expected_kernel_bytes=None, profile=IA32,
               memory_mb=None):
    """Judge one boot against the profile's stages, and emit one check per stage."""
    seen = classify(run, profile)
    ctx = {"expected_bytes": expected_kernel_bytes, "killed": run["killed"],
           "stopped_on": run["stopped_on"], "seconds": run["seconds"],
           "memory_mb": memory_mb}
    report.data[stage] = {"markers": seen, "seconds": run["seconds"],
                          "stopped_on": run["stopped_on"],
                          "returncode": run["returncode"],
                          "stages": {}}
    for stage_def in profile_stages(profile):
        ok, detail = stage_def.judge(seen, ctx)
        report.check(stage_def.name(stage), ok is True, detail,
                     applicable=ok is not NOT_APPLICABLE)
        report.data[stage]["stages"][stage_def.requirement] = {
            "check": stage_def.name(stage),
            "state": {True: "pass", False: "fail"}[ok is True]
                     if ok is not NOT_APPLICABLE else "not-applicable"}
    return seen


def check_isolation(report, iso_path, code_info, efi_image=None):
    """Firmware must stay outside the repository and outside the ISO."""
    firmware_bytes = open(code_info["path"], "rb").read(0x30)
    with open(iso_path, "rb") as handle:
        blob = handle.read()
    report.check("the ISO does not embed the firmware volume",
                 firmware_bytes[0x28:0x30] not in blob or
                 blob.count(firmware_bytes[0x28:0x30]) < 2,
                 "firmware signature bytes in the ISO")
    committed = []
    for name in ("boot", "kernel", "tools", "tests", "docs"):
        directory = os.path.join(ROOT, name)
        for base, _dirs, files in os.walk(directory):
            for entry in files:
                if entry.lower().endswith((".fd", ".fv", ".rom", ".bin")) and \
                        entry.lower() not in ("kernel.bin", "loader.bin", "mbr.bin", "bootx64.efi"):
                    committed.append(os.path.join(base, entry))
    report.check("no firmware image is carried in the repository",
                 not committed, committed[:4])
    if efi_image and os.path.isfile(efi_image):
        report.check("the ESP is not a firmware image",
                     sha256_file(efi_image) != code_info["sha256"],
                     "ESP and firmware are the same file")
        with open(efi_image, "rb") as handle:
            report.check("the ESP contains no firmware volume signature",
                         b"_FVH" not in handle.read(),
                         "ESP carries a firmware volume header")


# ---------------------------------------------------------------- mutants
def _build_kernel_path(profile):
    """Where the build puts the kernel file this profile loads."""
    if profile.key == "x64":
        return os.path.join(BUILD, "kernel64", "KERNEL64.BIN")
    return os.path.join(BUILD, "kernel.bin")


def build_mutant_iso(name, efi_image, kernel=None, drop_kernel=False,
                     drop_boot_app=False, boot_app_mutation=None, profile=IA32):
    """Build an ISO whose ESP (and optionally kernel) is deliberately wrong.

    Mutants are built with the repository's own tools, so what is tested is the
    firmware and the loader reacting to a real image - not a code path called
    directly.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import mkefiimg  # noqa: E402  (path set above)

    out_dir = os.path.join(BUILD, "ovmf-mutants")
    os.makedirs(out_dir, exist_ok=True)
    files = {}
    esp_source = efi_image
    if drop_boot_app or boot_app_mutation or drop_kernel or kernel is not None:
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        from uefi_stubtest import esp_files_from_image  # noqa: E402
        files = esp_files_from_image(efi_image)
        if drop_boot_app:
            files.pop("\\EFI\\BOOT\\BOOTX64.EFI", None)
        if boot_app_mutation:
            for key in list(files):
                if key.upper().endswith("BOOTX64.EFI"):
                    files[key] = boot_app_mutation(files[key])
        if drop_kernel:
            files.pop(profile.kernel_fat, None)
        if kernel is not None:
            files[profile.kernel_fat] = kernel
        esp_source = os.path.join(out_dir, f"{name}-esp.img")
        builder = mkefiimg.EfiImageBuilder(size_kb=1440)
        for fat_path, data in files.items():
            builder.add_file(fat_path, data)
        image, _info = builder.build()
        with open(esp_source, "wb") as handle:
            handle.write(image)
    kernel_path = _build_kernel_path(profile) if kernel is None else None
    iso_path = os.path.join(out_dir, f"{name}.iso")
    command = [
        sys.executable, os.path.join(ROOT, "tools", "mkiso.py"),
        "--volume-id", "PC5",
        "--boot-image", os.path.join(BUILD, "loader.bin"),
        "--mbr-code", os.path.join(BUILD, "mbr.bin"),
        "--desc-offset", "0xE00",
        "--uefi-image", esp_source,
        "--output", iso_path,
    ]
    if kernel is None and not drop_kernel:
        command += ["--file", "%s=%s" % (profile.kernel_name, kernel_path)]
    subprocess.run(command, check=True, capture_output=True)
    return iso_path


# ---------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    # Both default to the artifact the resolved profile builds, and are deliberately
    # not hardcoded to the 32-bit names here: a `pc5.iso`/`efi.img` default would let
    # a 64-bit gate silently boot and inspect the 32-bit loader.
    parser.add_argument("--iso", help="release ISO to boot")
    parser.add_argument("--efi-image", help="the ESP image whose BOOTX64.EFI this ISO boots")
    parser.add_argument("--kernel",
                        help="the kernel file to compare the loader's byte count "
                             "with; defaults to the one this profile loads")
    parser.add_argument("--qemu", help="qemu-system-x86_64 (or a wrapper script)")
    parser.add_argument("--qemu-data-dir", default=os.environ.get("PC5_QEMU_DATA_DIR"),
                        help="QEMU data dir (-L) holding its ROMs, if not beside the "
                             "binary; also read from PC5_QEMU_DATA_DIR")
    parser.add_argument("--ovmf-code")
    parser.add_argument("--ovmf-vars")
    parser.add_argument("--timeout", type=int, default=300,
                        help="seconds per boot before the run is called a timeout")
    parser.add_argument("--memory-mb", type=int, default=256)
    parser.add_argument("--json-out")
    parser.add_argument("--serial-dir")
    parser.add_argument("--skip-negative", action="store_true",
                        help="only boot the good ISO (no mutant machines)")
    parser.add_argument("--list-firmware", action="store_true")
    parser.add_argument("--secure-boot", action="store_true",
                        help="also boot with Secure Boot enabled, and assert that an "
                             "unsigned PC5 image is refused rather than accepted")
    parser.add_argument("--matrix-out", help="write the required test matrix here as JSON")
    parser.add_argument("--profile", default="auto", choices=("auto", "ia32", "x64"),
                        help="which boot path the ISO is: 'auto' reads the ESP and "
                             "finds out, so a gate cannot be told to grade the 64-bit "
                             "artifact with the 32-bit vocabulary")
    args = parser.parse_args(argv)

    found = discover(args)
    found["secure_code"] = find_first(SECBOOT_CODE_CANDIDATES)
    found["secure_vars"] = find_first(SECBOOT_VARS_CANDIDATES)
    if args.list_firmware:
        print("qemu:", found["qemu"] or "not found")
        print("OVMF CODE candidates:")
        for path in CODE_CANDIDATES:
            if path:
                print("  %-52s %s" % (path, "present" if os.path.isfile(path) else "-"))
        print("OVMF VARS candidates:")
        for path in VARS_CANDIDATES:
            if path:
                print("  %-52s %s" % (path, "present" if os.path.isfile(path) else "-"))
        print("OVMF Secure Boot CODE candidates:")
        for path in SECBOOT_CODE_CANDIDATES:
            if path:
                print("  %-52s %s" % (path, "present" if os.path.isfile(path) else "-"))
        return 0

    report = Report()
    # ------------------------------------------------- which artifact is this?
    # Detected from the medium -- the ESP root directory holds the kernel file's
    # 8.3 name, which is what the loader looks for -- and then cross-checked against
    # anything the caller asserted.  A `--profile` that disagrees with the ISO is a
    # mismatched artifact: booting it would produce a verdict about the wrong path,
    # so the disagreement is its own failure and the medium wins.
    requested = None if args.profile == "auto" else profile_by_key(args.profile)
    if args.iso is None:
        args.iso = os.path.join(BUILD, ARTIFACTS[(requested or IA32).key]["iso"])
    if args.efi_image is None:
        args.efi_image = os.path.join(
            BUILD, ARTIFACTS[(requested or IA32).key]["efi_image"])
    PROFILE_READ_CHECK = "the boot profile can be read from the medium"
    PROFILE_MATCH_CHECK = "the ISO matches the boot profile it was graded with"
    if not os.path.isfile(args.iso):
        # Nothing to read a profile from.  Saying "matched" here would be a pass
        # earned out of an absence, so it is recorded as not executed, and the missing
        # ISO itself stays the blocker.
        profile, note = (requested or IA32), "no ISO at %s" % args.iso
        report.check(PROFILE_READ_CHECK, False, note, applicable=False)
        report.check(PROFILE_MATCH_CHECK, False, note, applicable=False)
    else:
        detected, note = detect_profile(args.iso)
        if detected is None:
            # An unreadable medium is reported and then graded as its *asserted*
            # profile, so the run still produces the stage verdicts instead of dying;
            # the failed read above means it can never be taken as a clean pass.
            profile = requested or IA32
            report.check(PROFILE_READ_CHECK, False,
                         "%s; grading against %s as %s, which cannot be a clean pass"
                         % (note, profile.kernel_name,
                            "--profile" if requested else "the default"))
        else:
            profile = detected
            report.check(PROFILE_READ_CHECK, True, note)
            if requested is not None and requested.key != profile.key:
                report.check(PROFILE_MATCH_CHECK, False,
                             "--profile %s, but the medium holds %s (%s)"
                             % (requested.key, profile.kernel_name, note))
            else:
                report.check(PROFILE_MATCH_CHECK, True,
                             "%s: %s" % (profile.key, note))
    args.kernel = args.kernel or _build_kernel_path(profile)
    expected_kernel = (os.path.getsize(args.kernel)
                       if os.path.isfile(args.kernel) else None)

    # Recorded after the paths are resolved, and with the ISO's own digest: the
    # verdict is worth nothing if it cannot be pinned to the bytes that were booted.
    report.data["environment"] = {
        "qemu": found["qemu"], "qemu_version": qemu_version(found["qemu"]),
        "ovmf_code": found["code"], "ovmf_vars": found["vars"],
        "iso": os.path.abspath(args.iso),
        "iso_sha256": sha256_file(args.iso) if os.path.isfile(args.iso) else None,
        "efi_image": os.path.abspath(args.efi_image),
        "efi_image_sha256": (sha256_file(args.efi_image)
                             if os.path.isfile(args.efi_image) else None),
        "host": {"machine": os.uname().machine, "release": os.uname().release},
    }
    report.data["environment"]["profile"] = {
        "key": profile.key, "label": profile.label, "kernel": profile.kernel_name,
        "success_markers": list(profile.success),
        "read_from": "the ESP root directory" if args.profile == "auto"
                     else "asserted by --profile and confirmed against the medium",
    }
    # The medium's own label, graded against the medium.  Done before the tooling
    # probe below so that even a run that cannot boot anything says which commit its
    # artifact claims to be.
    check_buildinfo(report, args.iso, profile)
    report.data["environment"]["kernel_file"] = {
        "path": os.path.abspath(args.kernel),
        "sha256": sha256_file(args.kernel) if os.path.isfile(args.kernel) else None,
        "size": expected_kernel,
    }

    # ------------------------------------------------------ can we even try?
    blockers = []
    if not os.path.isfile(args.iso):
        blockers.append(f"no ISO at {args.iso} (run 'make iso')")
    if not found["qemu"]:
        blockers.append("no qemu-system-x86_64 (set PC5_QEMU)")
    if not found["code"]:
        blockers.append("no OVMF CODE image found (set PC5_OVMF_CODE)")
    if not found["vars"]:
        blockers.append("no OVMF VARS image found (set PC5_OVMF_VARS)")
    code_info = None
    if found["code"]:
        code_info = describe_firmware(found["code"])
        report.data["firmware"] = code_info
        if not code_info["ok"]:
            blockers.append("the file at %s is not usable OVMF: %s"
                            % (found["code"], "; ".join(code_info["problems"])))
    if found["vars"] and os.path.isfile(found["vars"]):
        report.data["vars_template"] = {
            "path": found["vars"], "size": os.path.getsize(found["vars"]),
            "sha256": sha256_file(found["vars"]),
        }
        if os.path.getsize(found["vars"]) < 32 * 1024:
            blockers.append("the VARS template is smaller than a variable store")

    if blockers:
        report.data["status"] = "not-tested"
        report.data["blockers"] = blockers
        for blocker in blockers:
            report.check(blocker, False, "external dependency missing")
        build_matrix(report, profile)
        if args.matrix_out:
            with open(args.matrix_out, "w") as handle:
                json.dump(report.data["matrix"], handle, indent=2)
                handle.write("\n")
        _emit(report, args, exit_code=3)
        return 3

    # --------------------------------------------------------------- boot 1
    work = args.serial_dir or os.path.join(BUILD, "ovmf")
    os.makedirs(work, exist_ok=True)
    iso = {"code": found["code"], "iso": args.iso}
    data_dir = args.qemu_data_dir or qemu_data_dir(found["qemu"])

    cold_vars = os.path.join(work, "vars-cold.fd")
    shutil.copyfile(found["vars"], cold_vars)
    cold_serial = os.path.join(work, "serial-cold.log")
    cold = run_qemu(found["qemu"], data_dir, iso, cold_vars, cold_serial,
                    args.timeout, args.memory_mb, stop_markers=profile.stop)
    report.data["cold_command"] = cold["command"]
    report.data["cold_stderr"] = cold["stderr"]
    check_boot(report, cold, "cold", expected_kernel, profile, args.memory_mb)
    report.check("[cold] a fresh variable store was used and written by firmware",
                 os.path.getsize(cold_vars) == os.path.getsize(found["vars"]),
                 os.path.getsize(cold_vars))

    # --------------------------------------------------------------- boot 2
    # Same firmware, same ISO, the variable store the cold boot just wrote: the
    # warm path must boot identically (no "only works once" state).
    warm_serial = os.path.join(work, "serial-warm.log")
    warm = run_qemu(found["qemu"], data_dir, iso, cold_vars, warm_serial,
                    args.timeout, args.memory_mb, stop_markers=profile.stop)
    report.data["warm_command"] = warm["command"]
    check_boot(report, warm, "warm", expected_kernel, profile, args.memory_mb)
    # "cold and warm independently validated" means the warm boot is judged on its own
    # log -- it is, above -- and that the two logs agree.  Comparing the per-stage
    # verdicts, not the byte counts (a second boot can legitimately differ in a
    # descriptor count if the firmware changed its mind), is what says the boot path is
    # reproducible rather than merely twice-successful.
    cold_stages = report.data["cold"]["stages"]
    warm_stages = report.data["warm"]["stages"]
    differing = sorted(name for name in set(cold_stages) | set(warm_stages)
                       if cold_stages.get(name, {}).get("state")
                       != warm_stages.get(name, {}).get("state"))
    report.check("[warm] " + WARM_COMPARISON_CHECK, not differing,
                 "stages that disagree: " + "; ".join(differing[:4]) if differing
                 else "%d stages, same verdict on both boots" % len(cold_stages))
    report.check("the second boot reused the variable store firmware had written",
                 os.path.getsize(cold_vars) == os.path.getsize(found["vars"]))

    # ------------------------------------------------------ disk-style path
    # UEFI can also be booted with the ESP as a plain block device.  This ISO
    # carries its ESP as an El Torito image inside ISO9660, which no firmware
    # exposes as a disk; claiming a pass here would be a lie.
    report.data["disk_style"] = "not-applicable"
    report.check("disk-style UEFI boot (ESP attached as a block device)",
                 False, "the ISO carries its ESP as an El Torito image inside "
                        "ISO9660, so there is no ESP partition to attach",
                 applicable=False)

    # ------------------------------------------------------ negative runs
    if not args.skip_negative:
        negative_runs(report, args, found, data_dir, work, profile)

    # ----------------------------------------------------------- isolation
    check_isolation(report, args.iso, code_info, efi_image=args.efi_image)
    check_release_esp(report, args.iso, args.efi_image)

    # ------------------------------------------------------- Secure Boot
    if args.secure_boot:
        if found["secure_code"] and found["secure_vars"]:
            secure_boot_run(report, args, found["qemu"], data_dir, work, args.iso,
                            found["secure_code"], found["secure_vars"], profile)
        else:
            report.check("Secure Boot: firmware enforced its trust policy on the "
                         "unsigned image", False,
                         "no Secure Boot OVMF build found (set "
                         "PC5_OVMF_SECBOOT_CODE / PC5_OVMF_SECBOOT_VARS)", applicable=False)

    build_matrix(report, profile)
    matrix_failures(report, report.data["matrix"])
    if args.matrix_out:
        with open(args.matrix_out, "w") as handle:
            json.dump(report.data["matrix"], handle, indent=2)
            handle.write("\n")

    report.data["status"] = "passed" if report.fail_count() == 0 else "failed"
    code = 0 if report.fail_count() == 0 else 1
    _emit(report, args, exit_code=code)
    return code


#: The two negatives whose verdict is not "firmware or the loader said no".
#: `deadline` succeeds by killing the machine on the clock, and `small-memory`
#: succeeds when the boot stops with a diagnostic *or* boots and reports no more
#: memory than the guest has -- a successful boot on 64 MiB is the good outcome
#: there.  Both are named once, used as the check's text and as the matrix row's
#: text, and the run records the name in `negative:<key>["check"]` so the matrix
#: inherits the verdict instead of guessing it from markers.
# One name per control, defined once and used by both the check that reports it and
# the matrix row that vouches for it -- the same discipline as the stage table above:
# a row naming a check that does not exist is a row reported as not tested, which is
# the only honest outcome for it.
NO_BOOT_APP_CHECK = "firmware refuses an ESP without BOOTX64.EFI (firmware fault)"
BAD_PE_CHECK = "firmware rejects a boot image with a broken PE32+ header (firmware fault)"
TRUNCATED_CHECK = "firmware refuses a truncated ISO (firmware fault)"
DEADLINE_CHECK = "a boot that outlives its deadline is reported as a timeout, not as a pass"
SMALL_MEMORY_CHECK = ("a small machine stops with a diagnostic or boots with its "
                      "own memory")

#: The row that compares the two boots, and the check that answers it.
WARM_COMPARISON_CHECK = "the warm boot proved the same stages as the cold boot"

#: Does the gate's claim about the kernel header match the kernel image?
#:
#: A derived claim, so it has to be checked against the thing it was derived from.
#: The corrupted-header control can only be enforced on a path whose kernel file
#: actually carries a header; if `KERNEL64.BIN` grows one and the profile still
#: says "no header field", that control would sit in NOT TESTED forever and
#: nothing would notice.  This row fails instead, naming both sides.
HEADER_VOCABULARY_CHECK = ("the gate's kernel-header claims match the kernel image "
                           "on the medium")


def required_rows(profile):
    """(requirement, source, check name) for every row this profile must answer for.

    The requirement text is the milestone's; the check name is the harness's; the row
    is only verified when *that* check passed.  Deriving both halves from the stage
    table is the point -- a stage added to the boot's judgement shows up as a row that
    has to be answered, and a row cannot be satisfied by some other stage's success.
    Negative controls are rows too, so a control that did not run reads as NOT TESTED
    instead of quietly not being asked about.
    """
    rows = [(stage.requirement, "OVMF cold boot", stage.name("cold"))
            for stage in profile_stages(profile)]
    rows.append(("rebooting the same machine boots identically", "OVMF warm boot",
                 "[warm] " + WARM_COMPARISON_CHECK))
    rows.extend([
        (profile.no_kernel_check(), "OVMF negative run (no-kernel)",
         profile.no_kernel_check()),
        (profile.bad_signature_check(), "OVMF negative run (bad-signature)",
         profile.bad_signature_check()),
        (BAD_PE_CHECK, "OVMF negative run (bad-pe)", BAD_PE_CHECK),
        (NO_BOOT_APP_CHECK, "OVMF negative run (no-boot-app)", NO_BOOT_APP_CHECK),
        (TRUNCATED_CHECK, "OVMF negative run (truncated-iso)", TRUNCATED_CHECK),
        (SMALL_MEMORY_CHECK, "OVMF negative run (small-memory)", SMALL_MEMORY_CHECK),
        (DEADLINE_CHECK, "OVMF negative run (deadline)", DEADLINE_CHECK),
    ])
    return rows


def build_matrix(report, profile=IA32):
    """The required matrix: each row's state is the state of its own check.

    Rows are never judged here.  A row is VERIFIED when the check it names passed in
    this run, FAILED when that check failed, NOT TESTED when the check did not run at
    all -- including a row whose check exists only for the other boot profile.  The
    rows whose honest evidence lives in another gate (the stub firmware, the host unit
    test, the BIOS regression test) are listed as EXTERNAL and name that gate; they do
    not inherit this run's result.
    """
    by_name = {}
    for check in report.checks:
        by_name.setdefault(check["name"], check)
    states = {"pass": "verified", "fail": "failed", "skip": "not-tested"}

    rows = []
    for requirement, source, check_name in required_rows(profile):
        check = by_name.get(check_name)
        if check is None:
            rows.append({"requirement": requirement, "source": source,
                         "check": check_name, "state": "not-tested",
                         "note": "no check of this name ran"})
        else:
            row = {"requirement": requirement, "source": source,
                   "check": check_name, "state": states[check["state"]]}
            if check["state"] != "pass" and check["detail"]:
                row["note"] = check["detail"]
            rows.append(row)

    rows.extend([
        {"requirement": "a stale memory map key is retried, not fatal",
         "source": "tests/uefi_stubtest.py (executed against the real loader image)",
         "state": "external"},
        {"requirement": "a firmware that never accepts the key fails without a false success",
         "source": "tests/uefi_stubtest.py (executed)",
         "state": "external"},
        {"requirement": "invalid boot information is rejected by the kernel",
         "source": "make unit (host unit test of the same predicate kmain runs)",
         "state": "external"},
        {"requirement": "the loader never calls a boot service after ExitBootServices",
         "source": "tests/uefi_stubtest.py (executed)",
         "state": "external"},
        {"requirement": "the BIOS boot path still boots and passes its own test",
         "source": "make boot-test (v86, BIOS/SeaBIOS)",
         "state": "external"},
    ])
    report.data["matrix"] = rows
    return rows


def matrix_failures(report, rows):
    """Turn a row nobody verified into a check, so the exit status cannot disagree.

    `build_matrix` used to be cosmetic: two rows read `failed` in
    `ovmf-matrix.json` while `make ovmf-test` exited 0 on the same run, because
    the run's status came only from the check list.  A summary that can be green
    over a matrix that says otherwise is not a gate.
    """
    broken = [row["requirement"] for row in rows if row["state"] == "failed"]
    if broken:
        report.check("every required matrix row this run claims to verify is verified",
                     False, "matrix rows failed: " + "; ".join(broken))


def secure_boot_verdict(seen):
    """Did the firmware refuse an unsigned image, as its policy requires?

    True means the policy was in force.  PC5 has no signing key and no shim, so
    "the image booted with Secure Boot enabled" is not a feature: it is evidence
    that Secure Boot was not actually enabled.
    """
    if seen.get("boot_ok"):
        return False, "the unsigned PC5 image booted; the policy was not in force"
    return True, ""


def secure_boot_run(report, args, qemu, data_dir, work, iso_path, code_path, profile,
                    vars_template):
    """Boot with Secure Boot enabled and see what the firmware's policy does.

    PC5 ships no signing key and no shim, so the honest expectation is that the
    firmware refuses this image.  The check therefore asserts the *refusal*: a
    run that produced PC5-BOOT-OK under a policy that should have stopped it
    means the policy was not actually in force, and the harness must not report
    that as compatibility.
    """
    secure_vars = os.path.join(work, "vars-secure.fd")
    shutil.copyfile(vars_template, secure_vars)
    serial = os.path.join(work, "serial-secure.log")
    run = run_qemu(qemu, data_dir, {"code": code_path, "iso": iso_path},
                   secure_vars, serial, min(args.timeout, 240), args.memory_mb,
                   secure=True, stop_markers=profile.stop)
    seen = classify(run, profile)
    report.data["secure_boot"] = {
        "command": run["command"], "markers": seen,
        "seconds": run["seconds"], "stopped_on": run["stopped_on"],
        "firmware": describe_firmware(code_path),
    }
    refused, detail = secure_boot_verdict(seen)
    report.check("Secure Boot: firmware enforced its trust policy on the unsigned image",
                 refused, detail)
    report.check("Secure Boot: the run is attributed (firmware refusal or loader stop)",
                 seen["firmware_refused"] or bool(seen["failure_codes"]) or
                 not seen["kernel_entry"],
                 seen["failure_codes"])
    return seen


#: The kernel image header, as the kernel itself defines it.
#:
#: `kernel/include/pc5.h` is the source of truth and the 32-bit loader applies
#: exactly this test (`boot/uefi/uefi_main.c`, "the kernel"); the numbers are repeated
#: here because the gate runs on the host with no compiler involved, and a unit test
#: (`tests.test_ovmf_gate_profiles.HeaderVocabulary`) re-reads the header file and
#: fails if the two ever disagree.  Offset 0 is a two-byte jump over the header, so
#: reading the signature at offset 0 would reject every kernel the BIOS path boots.
PC5_KERNEL_SIG = b"PC5K"
PC5_KERNEL_SIG_OFFSET = 2
PC5_KERNEL_VERSION_OFFSET = 6
PC5_KERNEL_HEADER_SIZE = 8


def kernel_has_header(kernel_bytes):
    """Does this kernel image carry the signature the loader can refuse?

    Measured from the bytes, not from a flag or a profile name: `KERNEL.BIN` carries
    the 8-byte header, `KERNEL64.BIN` carries none (it begins with `cli; mov rbx,rdi`
    -- the first instructions of `kernel64/start.S`).  A file without the field has
    nothing for a mutant to corrupt, so the control would prove nothing about it in
    either direction, and "the check passed" would really mean "the write landed in
    the middle of an instruction stream nobody reads".
    """
    return (len(kernel_bytes) >= PC5_KERNEL_HEADER_SIZE and
            kernel_bytes[PC5_KERNEL_SIG_OFFSET:PC5_KERNEL_SIG_OFFSET + 4] ==
            PC5_KERNEL_SIG)


def negative_runs(report, args, found, data_dir, work, profile=IA32):
    """Boot machines that are broken on purpose, and check who notices."""
    kernel_bytes = open(args.kernel, "rb").read()
    # The release file the ESP carries -- the two are held equal by the extent
    # checks in `check_isolation`, so the medium and the mutant input are the
    # same bytes here and this measurement is a statement about the artifact.
    has_header = kernel_has_header(kernel_bytes)
    report.check(HEADER_VOCABULARY_CHECK,
                 has_header == (profile.bad_signature_code is not None),
                 "%s in the boot ESP %s the PC5K header (first bytes %r); the %s "
                 "profile claims %s" % (profile.kernel_name,
                                        "carries" if has_header else "has",
                                        bytes(kernel_bytes[:8]), profile.key,
                                "a header the loader must refuse"
                                if profile.bad_signature_code is not None
                                else "no header field to refuse"))
    iso = {"code": found["code"], "iso": args.iso}

    def boot(name, iso_path, timeout=None):
        vars_path = os.path.join(work, f"vars-{name}.fd")
        shutil.copyfile(found["vars"], vars_path)
        serial = os.path.join(work, f"serial-{name}.log")
        run = run_qemu(found["qemu"], data_dir, {"code": iso["code"], "iso": iso_path},
                       vars_path, serial, timeout or min(args.timeout, 180),
                       args.memory_mb, stop_markers=profile.stop)
        report.data.setdefault("negative", {})[name] = {
            "iso": os.path.basename(iso_path),
            "markers": classify(run, profile),
            "seconds": run["seconds"],
            "serial_tail": run["serial"][-600:],
        }
        return run

    # 1. ESP without the boot application: firmware has nothing to start.
    try:
        path = build_mutant_iso("no-boot-app", args.efi_image, drop_boot_app=True,
                                drop_kernel=False, profile=profile)
        run = boot("no-boot-app", path)
        seen = classify(run, profile)
        report.check(NO_BOOT_APP_CHECK,
                     seen["firmware_banner"] and not seen["loader_banner"] and
                     seen["firmware_refused"],
                     "fw_ran=%s loader_ran=%s refused=%s" % (
                         seen["firmware_banner"], seen["loader_banner"],
                         seen["firmware_refused"]))
    except (OSError, subprocess.CalledProcessError) as exc:
        report.check(NO_BOOT_APP_CHECK, False, f"mutant could not be built: {exc}")

    # 2. A PE32+ image with its 'MZ' header destroyed: the firmware image loader
    #    must reject it before any PC5 code runs.
    def break_pe(blob):
        out = bytearray(blob)
        out[0:2] = b"ZZ"
        return bytes(out)

    try:
        path = build_mutant_iso("bad-pe", args.efi_image, boot_app_mutation=break_pe,
                            profile=profile)
        run = boot("bad-pe", path)
        seen = classify(run, profile)
        report.check(BAD_PE_CHECK,
                     seen["firmware_banner"] and not seen["loader_banner"] and
                     seen["firmware_refused"],
                     "fw_ran=%s loader_ran=%s refused=%s" % (
                         seen["firmware_banner"], seen["loader_banner"],
                         seen["firmware_refused"]))
    except (OSError, subprocess.CalledProcessError) as exc:
        report.check(BAD_PE_CHECK, False, f"mutant could not be built: {exc}")

    # 3. No kernel anywhere: the loader must stop with its own code, not hang,
    #    and not invent a handover.
    try:
        path = build_mutant_iso("no-kernel", args.efi_image, drop_kernel=True,
                            profile=profile)
        run = boot("no-kernel", path)
        seen = classify(run, profile)
        # The code is the profile's: `E2` on the 32-bit path, `kernel-not-found` on
        # the 64-bit one.  Asserting one path's spelling on the other is a check
        # that can only fail, and the first 64-bit run proved it -- the boot had
        # reported the missing file, under a name the gate had not learned.
        report.check(profile.no_kernel_check(),
                     profile.no_kernel_code in seen["failure_codes"] and
                     not seen["kernel_entry"],
                     seen["failure_codes"])
    except (OSError, subprocess.CalledProcessError) as exc:
        report.check(profile.no_kernel_check(), False,
                     f"mutant could not be built: {exc}")

    # 4. A kernel whose PC5K signature is wrong: the loader must refuse it.
    #
    # Whether this can be run is decided by the image, not by a flag -- see
    # `HEADER_VOCABULARY_CHECK`, which fails if the gate's claim and the medium ever
    # disagree.  On a path whose kernel file has no header there is nothing to
    # corrupt: the row is recorded NOT TESTED with that reason, which keeps the
    # milestone's rule (no control passes because its mutant was never built) while
    # making the gap a finding instead of a failure that hides the real one.  Give
    # the image a header and the row becomes enforced with no flag to remember.
    if has_header:
        try:
            broken = bytearray(kernel_bytes)
            broken[2:6] = b"XXXX"
            path = build_mutant_iso("bad-signature", args.efi_image, kernel=bytes(broken),
                                profile=profile)
            run = boot("bad-signature", path)
            seen = classify(run, profile)
            report.check(profile.bad_signature_check(),
                         profile.bad_signature_code in seen["failure_codes"] and
                         not seen["kernel_entry"],
                         seen["failure_codes"])
        except (OSError, subprocess.CalledProcessError) as exc:
            report.check(profile.bad_signature_check(), False,
                         f"mutant could not be built: {exc}")
    else:
        report.check(profile.bad_signature_check(), False,
                     "there is no header field to corrupt: %s starts with %r, not with "
                     "the PC5K signature at offset %d that the 32-bit loader reads.  "
                     "The control is enforced as soon as the 64-bit image format "
                     "carries a header (milestone 7 priority 2); today "
                     "`boot/uefi64/uefi64_main.c` has nothing to reject"
                     % (profile.kernel_name, bytes(kernel_bytes[:8]),
                        PC5_KERNEL_SIG_OFFSET),
                     applicable=False)

    # 5. A truncated ISO: firmware must fail to start anything at all.
    truncated = os.path.join(BUILD, "ovmf-mutants", "truncated.iso")
    os.makedirs(os.path.dirname(truncated), exist_ok=True)
    with open(args.iso, "rb") as src, open(truncated, "wb") as dst:
        # 64 KiB: past the ISO9660 system area, inside the El Torito image, so
        # the firmware finds a boot catalog that points at sectors the medium no
        # longer has.
        dst.write(src.read(64 * 1024))
    run = boot("truncated-iso", truncated)
    seen = classify(run, profile)
    report.check(TRUNCATED_CHECK,
                 seen["firmware_banner"] and not seen["loader_banner"] and
                 seen["firmware_refused"],
                 "fw_ran=%s loader_ran=%s refused=%s" % (
                     seen["firmware_banner"], seen["loader_banner"],
                     seen["firmware_refused"]))

    # 6. The deadline itself: a one-second budget on the good ISO must be
    #    reported as a timeout, never as a pass.
    vars_path = os.path.join(work, "vars-deadline.fd")
    shutil.copyfile(found["vars"], vars_path)
    serial = os.path.join(work, "serial-deadline.log")
    run = run_qemu(found["qemu"], data_dir, iso, vars_path, serial, 1,
                   args.memory_mb)
    report.data["negative"]["deadline"] = {
        "killed": run["killed"], "seconds": run["seconds"],
        "markers": classify(run, profile),
    }
    # Deliberately no stop markers here: this control asks what the harness does when
    # the *clock* wins, so the profile's markers must not end the run first.
    report.check(DEADLINE_CHECK, run["killed"] and run["stopped_on"] == "timeout",
                 "stopped_on=%r killed=%s after %ss"
                 % (run["stopped_on"], run["killed"], run["seconds"]))
    report.data["negative"]["deadline"]["check"] = DEADLINE_CHECK

    # 7. A machine with far less memory than the development host.  The first
    #    version of this control starved the guest to 24 MiB and required a PC5
    #    diagnostic; on real firmware that run produced an EMPTY log, because 24
    #    MiB is below OVMF's own floor -- the check was therefore measuring the
    #    firmware's limit and calling it PC5's safety, and it failed whenever the
    #    firmware simply refused to start.  An empty log is not evidence about PC5.
    #    So the control now asks the question that is actually PC5's, on a machine
    #    small enough to be a constraint and large enough for firmware to run:
    #    either the boot stops with a PC5 diagnostic, or it boots and reports only
    #    the memory this machine has.  Over-claiming memory is the failure that
    #    matters -- a loader that mis-parses the map hands the kernel a frame
    #    allocator full of pages that do not exist.
    vars_path = os.path.join(work, "vars-small.fd")
    shutil.copyfile(found["vars"], vars_path)
    serial = os.path.join(work, "serial-small-memory.log")
    small_mb = 64
    run = run_qemu(found["qemu"], data_dir, iso, vars_path, serial,
                   min(args.timeout, 180), small_mb, stop_markers=profile.stop)
    # Judged in this artifact's own words: on the 64-bit path the loader prints no
    # conventional total (the kernel's `PC5-64-MAP` line is the report), and its
    # failure codes are words rather than `E`-numbers, so classifying it with the
    # 32-bit profile would record "nothing was refused" for a boot that had in fact
    # stopped with `PC5-64-LOADER-FAIL=...`.
    seen = classify(run, profile)
    reported = seen["conventional_mib"]
    kernel_reported = seen["kernel_memory_mib"]
    refused = bool(seen["failure_codes"])
    # None means the line was never printed, which is not the same as zero.
    over_claims = (reported is not None and reported > small_mb) or \
                  (kernel_reported is not None and kernel_reported > small_mb)
    report.data["negative"]["small-memory"] = {
        "memory_mb": small_mb, "markers": seen, "seconds": run["seconds"],
        "conventional_mib": reported, "kernel_memory_mib": kernel_reported,
        "refused_with_code": refused, "over_claims": over_claims,
        "serial_bytes": len(run["raw_serial"]),
    }
    if not seen["firmware_banner"]:
        report.check(SMALL_MEMORY_CHECK, False,
                     "firmware printed nothing in %d bytes of log, so the run says "
                     "nothing about PC5 (memory_mb=%d); raise the floor until the "
                     "firmware itself boots" % (len(run["raw_serial"]), small_mb))
    else:
        report.check(SMALL_MEMORY_CHECK,
                     (refused or seen["boot_ok"]) and not over_claims,
                     "memory_mb=%d reported_conventional=%s kernel_reported=%s "
                     "failures=%s boot_ok=%s" % (
                         small_mb, reported, kernel_reported,
                         seen["failure_codes"], seen["boot_ok"]))
    report.data["negative"]["small-memory"]["check"] = SMALL_MEMORY_CHECK


def _emit(report, args, exit_code):
    payload = report.as_dict()
    payload["exit_code"] = exit_code
    payload["success_marker"] = SUCCESS_MARKER if exit_code == 0 else None
    if args.json_out:
        with open(args.json_out, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    for check in report.checks:
        state = {"pass": "PASS", "fail": "FAIL", "skip": "N/A "}[check["state"]]
        detail = f"  ({check['detail']})" if check["detail"] else ""
        print(f"[{state}] {check['name']}{detail}")
    if exit_code == 3:
        print("\nNOT TESTED: genuine OVMF firmware and/or QEMU is not available "
              "on this machine.")
        print("Required matrix rows: %d, none executed here."
              % len(report.data.get("matrix", [])))
        print("The UEFI integration gate is NOT passed.  Install OVMF "
              "(edk2-ovmf / ovmf) and QEMU, or set PC5_OVMF_CODE, PC5_OVMF_VARS "
              "and PC5_QEMU.")
    elif exit_code == 0:
        print(f"\n{SUCCESS_MARKER}: real OVMF firmware booted the PC5 kernel.")
        if report.skip_count():
            print(f"{report.skip_count()} path(s) NOT TESTED (see the N/A lines): "
                  "they are neither passed nor failed.")
    else:
        failures = [c for c in report.checks if c["state"] == "fail"]
        print(f"\nUEFI BOOT VERIFICATION FAILED: {len(failures)} check(s) "
              "did not hold.")
    if args.json_out:
        print(f"report: {os.path.relpath(args.json_out, ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
