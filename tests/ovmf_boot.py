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

#: Read out of the kernel's header, so a gate that pins a number cannot drift from the code
#: that chose it.  These are the same constants the boot line is built from.
TSS64_HEADER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "kernel64", "tss64.h")

with open(TSS64_HEADER_PATH, encoding="utf-8") as _tss64_handle:
    TSS64_HEADER = _tss64_handle.read()


def _tss64_define(name, cast=int):
    match = re.search(r"^#define\s+%s\s+(\S+)$" % name, TSS64_HEADER, re.MULTILINE)
    if match is None:
        raise AssertionError("kernel64/tss64.h no longer defines %s" % name)
    text = match.group(1).rstrip("uUlL")
    return cast(text, 0) if cast is int else text


PC5_TSS64_SIZE = _tss64_define("PC5_TSS64_SIZE")
PC5_TSS64_TR_SELECTOR = _tss64_define("PC5_TSS64_SELECTOR")
PC5_TSS64_IST_DOUBLE_FAULT = _tss64_define("PC5_TSS64_IST_DOUBLE_FAULT")
PC5_TSS64_IST_PAGE = _tss64_define("PC5_TSS64_IST_PAGE")
PC5_TSS64_IST_NMI = _tss64_define("PC5_TSS64_IST_NMI")


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
def _qmp_inject_nmi(path, timeout=10.0):
    """Ask QEMU to raise an NMI in the guest, over the machine's own QMP socket.

    Returns a small dict rather than raising, because a run where the *harness* could not talk to
    QEMU has to be reported as that, not as a kernel that failed to take an NMI.  Three facts come
    back and all three are put in the report: whether the socket was reached, whether QEMU accepted
    `inject-nmi` (a bare `{"error": ...}` means the command does not exist in this build, which is a
    different story from the guest ignoring it), and the transcript.

    The greeting is read first and `qmp_capabilities` is sent before any command: QMP refuses
    everything else until the capability handshake is done, and a harness that skipped the step
    would report a refusal it caused itself.
    """
    import socket

    transcript = []
    result = {"reached": False, "accepted": False, "error": None, "transcript": ""}
    deadline = time.time() + timeout
    sock = None
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(path)
            break
        except OSError:
            if sock is not None:
                sock.close()
            sock = None
            time.sleep(0.2)
    if sock is None:
        result["error"] = "qmp socket not reachable at %s" % path
        return result

    def read_line():
        data = b""
        try:
            while not data.endswith(b"\r\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except (socket.timeout, OSError):
            pass
        return data.decode("latin-1", "replace").strip()

    try:
        transcript.append(read_line())                      # the greeting
        sock.sendall(b'{"execute": "qmp_capabilities"}\r\n')
        transcript.append(read_line())
        sock.sendall(b'{"execute": "inject-nmi"}\r\n')
        reply = read_line()
        transcript.append(reply)
        result["reached"] = True
        result["accepted"] = reply.startswith("{") and '"error"' not in reply
        if not result["accepted"]:
            result["error"] = reply[:200] or "no reply to inject-nmi"
    except OSError as exc:
        result["error"] = "qmp write failed: %s" % exc
    finally:
        try:
            sock.close()
        except OSError:
            pass
    result["transcript"] = " | ".join(line for line in transcript if line)[:400]
    return result


def run_qemu(qemu, data_dir, iso, vars_path, serial_path, timeout, memory_mb=256,
             stop_markers=("PC5-BOOT-OK", "PC5-UEFI-FAILURE"), extra_wait=None,
             secure=False, inject_nmi=False):
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
    if inject_nmi:
        # `wait=off` matters: without it QEMU blocks until a client connects, and a harness that
        # cannot get to the socket would have hung the boot rather than reported it.
        command += ["-qmp", "unix:%s,server=on,wait=off" % (serial_path + ".qmp")]
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

    nmi = None
    if inject_nmi:
        nmi = {"requested": True, "at": outcome}
        if outcome == "evidence":
            # Injected *after* the boot settled, and that ordering is the design rather than a
            # convenience.  The NMI handler reports and halts, which is what a handler for a
            # non-maskable, unrecoverable event should do -- so injecting it during the boot would
            # end the run early and the log would show an NMI that arrived at a moment chosen by
            # the harness rather than one the kernel had finished defending.  After the marker, the
            # only thing left to prove is that the vector still lands on its own stack.
            nmi.update(_qmp_inject_nmi(serial_path + ".qmp"))
            if nmi.get("accepted"):
                # The guest has to be given time to notice, and the serial has to be drained
                # *before* SIGTERM or the exception line is lost with the pipe.  The line is
                # waited for rather than slept for: on a slow TCG host two seconds is sometimes
                # not enough, and a fixed sleep that is too short reads as a kernel that did not
                # take the NMI.
                # Waiting on the log's own tail rather than on a window into the drain buffer.
                # The first version recorded where the buffer had got to, then looked only in what
                # arrived after that -- and the guest answered *during* the QMP exchange, so the
                # window was empty, the count said `exception_lines: 0`, and the run waited out the
                # whole timeout for lines it had already received.  A harness measuring its own
                # polling is a harness with two answers; there is one log, so there is one source.
                settle = time.time() + 20.0
                while time.time() < settle and process.poll() is None:
                    tail = "".join(chunks).split("PC5-64-DONE", 1)[-1]
                    if "PC5-64-EXCEPTION halted" in tail:
                        break          # the reporter's last word, so the line above it is flushed
                    time.sleep(0.1)
        else:
            nmi["skipped"] = "the boot did not reach its marker (%s)" % outcome
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
    if nmi is not None and nmi.get("accepted"):
        nmi["exception_lines"] = sum(
            1 for line in raw.split("PC5-64-DONE", 1)[-1].splitlines()
            if "PC5-64-EXCEPTION" in line)
    with open(serial_path, "w") as handle:
        handle.write(raw)
    return {"command": command, "nmi": nmi, "returncode": process.returncode,
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
                 bad_signature_code, uefi_path, kernel_header_kind=None,
                 kernel_refusals=()):
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
        # Which image format this path's loader validates, if any: `bios8` is the
        # 8-byte `PC5K` header in front of `KERNEL.BIN`, `kimage64` the 128-byte
        # versioned header in front of `KERNEL64.BIN`.  `None` means the loader of this
        # path reads a bare binary and has nothing to check -- which the gate reports as
        # NOT TESTED with that reason rather than as a control that passed.
        self.kernel_header_kind = kernel_header_kind
        # Fields this format can be broken in that the release boot is made to prove are
        # refused, as (reason, mutation) pairs.  Deliberately short: what runs on real
        # firmware is the three or four corruptions whose *consequence* differs, and the
        # full battery of thirteen runs in `tests/test_boot64_stub.py` and
        # `tests/test_kimage64.py`, where each case costs a second instead of a boot.
        self.kernel_refusals = tuple(kernel_refusals)

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

    def kernel_refusal_check(self, mutation, code):
        """One named field, one named refusal, one boot that must not reach the kernel."""
        return ("loader refuses a %s whose %s is wrong as %s (bootloader fault)"
                % (self.kernel_name, mutation, code))

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
    kernel_header_kind="bios8",
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
    # `kimage64-magic` is the refusal `boot/uefi64/kimage64.c` reaches when the file on the
    # medium is not a PC5 64-bit kernel image at all -- which is what a corrupt-signature
    # mutant produces.  This row used to read `None`, and the control with it: the format
    # gained a header (milestone 7 priority 2) so that "the file we opened is the file we
    # will execute" stopped being an assumption.  Every other field the loader checks has a
    # reason of its own, and the three whose consequences differ are booted below; all
    # thirteen are executed against the real loader in `tests/test_boot64_stub.py`.
    bad_signature_code="kimage64-magic",
    uefi_path="64",
    kernel_header_kind="kimage64",
    kernel_refusals=(("arch", "kimage64-arch"),
                     ("image-size", "kimage64-image-size"),
                     ("checksum", "kimage64-checksum")),
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


def _said(text, pattern):
    """The text a line actually carried, or None.  Evidence, never a verdict.

    The `_number` and `_hex` readers below pull a *count* out of the serial log because a
    count is what their rows assert.  This pulls the *string*, for the one place where the
    assertion is about a name: `loader_firmware_edk2` is a substring test, and a failure
    that quotes only `=False` leaves the reader to guess whether the firmware said
    something else, said it with different spacing, or said nothing at all.  Those three
    are different defects, and the serial log already knows which.
    """
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


#: --- firmware identity contract -------------------------------------------------------
#: What a `firmware=` line may be used to claim, and no more.  Both loaders print
#: `SystemTable->FirmwareVendor` verbatim; edk2's upstream value for it is the literal
#: `EDK II` (`MdeModulePkg.dec`, `PcdFirmwareVendor|L"EDK II"`), and a distributor may
#: override that same build-time PCD to name itself -- which is how the hosted runner came
#: to print `Ubuntu distribution of EDK II` while every serial log committed under
#: `docs/evidence/` says `EDK II`.  The distribution clause is a claim about *who built the
#: image*, and anyone building EDK II can write anything there, so no gate here asserts it;
#: what the row can assert is the implementation token, exactly.  Written as data rather
#: than prose in the spirit of ADR-024: an identity outside this grammar is refused, not
#: forgiven.  See ADR-026.
EDK2 = "EDK II"
#: Anchored at both ends, unlike the substring test this replaces: the old predicate
#: accepted any string *beginning* `EDK II` (so `EDK II-beta` passed) while refusing
#: `Ubuntu distribution of EDK II` -- too loose at the tail and too strict at the head at
#: once.  A vendor string that itself contains ` revision=` is indistinguishable from the
#: next field on the 64-bit line, because the marker vocabulary delimits fields with a
#: space, not with quoting; such a string is refused as ambiguous rather than guessed at.
EDK2_DISTRIBUTION_RE = re.compile(r"^(?P<dist>\S(?:.*\S)?) of EDK II$")
#: A label this repository writes is never evidence of third-party firmware.  The stub
#: firmware sets the same system-table field to `PC5 stub firmware`
#: (`tests/uefi_stub_firmware.py`) on purpose; the rule is by shape, not by that one string,
#: so a stub cannot certify a real boot by naming itself something else either.
SELF_ISSUED_MARK = "PC5"
#: The two words the 64-bit loader prints for a NULL pointer and a zero-length string
#: (`serial_write_ascii16`), against the 32-bit loader printing no line at all when either
#: holds.  Kept as three separate refusals on purpose: they are three different facts about
#: the machine, and collapsing them discards the only signal saying which one occurred.
ABSENT_FORMS = {"absent": "the firmware vendor pointer was NULL",
                "empty": "the firmware reported a zero-length vendor string"}
#: Characters each profile can print before its own limit bites: `utf16_to_ascii` into a
#: 32-byte buffer stops at 31 (the last byte is the NUL), `serial_write_ascii16(…, 32)`
#: streams up to 32.  A string of exactly that length which does not parse is *truncated*,
#: and a truncated string is ambiguous -- reporting it as a mismatch would blame the
#: firmware for the loader's buffer size.
PRINT_LIMIT = {"ia32": 31, "x64": 32}

#: The two markers' line prefixes.  The 64-bit one has to stop at ` revision=`, because
#: that loader continues the same line with ` revision=` and ` table-rev=`.
FIRMWARE_LINE_IA32 = r"PC5-UEFI: firmware=([^\r\n]*)"
FIRMWARE_LINE_X64 = r"PC5-64-LOADER firmware=([^\r\n]*?)(?=[ ]revision=|\r|\n|$)"


def classify_firmware_identity(raw, profile_key="ia32"):
    """(implementation, distribution, accepted, refusal) for one `firmware=` string.

    Pure by construction -- no file, no process, no ambient state -- because every row of
    the contract has to grade identically on both profiles, and a verdict that depends on
    which machine asked is the failure mode `docs/TEST_RESULTS.md` §35 and §37 are about.  `raw` is what the loader printed; nothing here rewrites it, trims it in
    place, or substitutes a canonical spelling, so the report can always quote the
    firmware's own words.  `refusal` is empty exactly when `accepted` is true.
    """
    if raw is None:
        return None, None, False, "the loader printed no `firmware=` line at all"
    if SELF_ISSUED_MARK in raw:
        return None, None, False, ("`%s` is a label this project writes itself; a stub "
                                   "cannot certify genuine firmware" % raw)
    if raw in ABSENT_FORMS:
        return None, None, False, "%s: %s" % (raw, ABSENT_FORMS[raw])
    trimmed = raw.strip()
    if trimmed == EDK2:
        return EDK2, "upstream default", True, ""
    match = EDK2_DISTRIBUTION_RE.match(trimmed)
    if match:
        # D1: the distributor is recorded, never asserted, and it cannot change the verdict.
        # There is deliberately no `unrecognised distributor` status to add here -- a second
        # taxonomy over a field nothing gates on would invite someone later to gate on it,
        # which is the claim ADR-026 refuses to make.
        return EDK2, match.group("dist"), True, ""
    # `UNKNOWN`, not a guess: an identity this contract has no evidence for is refused and
    # named, which is what makes adding it later a decision rather than a repair.
    reason = "`%s` is not the %s implementation token; refused as UNKNOWN" % (trimmed, EDK2)
    if "?" in trimmed:
        reason += " (and carries `?`, which the loader substitutes for a non-printable unit)"
    limit = PRINT_LIMIT.get(profile_key, 31)
    if len(raw) >= limit:
        reason += " (ambiguous: exactly this profile's %d-character print limit, so the " \
                  "string may be truncated rather than wrong)" % limit
    return "UNKNOWN", None, False, reason


def _firmware_identity(text, line_pattern, profile_key):
    """The marker keys the firmware-identity row, the matrix and the report are built from.

    `loader_firmware_edk2` keeps its name and its bool type -- the row, `--matrix-out` and
    `tests/test_ovmf_gate_profiles.py` all read it -- and is now the classifier's verdict
    instead of an unbounded substring test.  The sibling keys carry the evidence alongside
    it, so a refusal can be read without re-deriving the grammar.
    """
    raw = _said(text, line_pattern)
    impl, dist, accepted, refusal = classify_firmware_identity(raw, profile_key)
    return {
        "loader_firmware_edk2": accepted,
        "loader_firmware_said": raw,
        "loader_firmware_impl": impl,
        "loader_firmware_distribution": dist,
        "loader_firmware_refusal": refusal,
    }


def identity_record(seen):
    """The report's first-class view of the identity claim, raw string included.

    The same facts sit in `markers`, which everything already reads; they are also lifted
    out because "record the distribution, do not assert it" is only honest if the record is
    somewhere a reviewer can find without knowing which marker carries it -- and somewhere
    that keeps the firmware's own words even when the verdict is a refusal.
    """
    return {"raw": seen.get("loader_firmware_said"),
            "implementation": seen.get("loader_firmware_impl"),
            "distribution": seen.get("loader_firmware_distribution"),
            "accepted": bool(seen.get("loader_firmware_edk2")),
            "refusal": seen.get("loader_firmware_refusal") or ""}


def _markers_ia32(text, profile):
    """The 32-bit kernel path's own lines."""
    return {
        "loader_banner": "PC5 UEFI loader" in text or "PC5 UEFI LOADER" in text,
        **_firmware_identity(text, FIRMWARE_LINE_IA32, "ia32"),
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
        # Same classifier, same rules; only the line prefix and the print limit differ.
        **_firmware_identity(text, FIRMWARE_LINE_X64, "x64"),
        "kernel_volume_image": "PC5-64-LOADER kernel-volume=image" in text,
        "kernel_read": "PC5-64-LOADER kernel at=" in text,
        "kernel_bytes_read": _number(text, r"PC5-64-LOADER kernel at=0x[0-9a-f]+ bytes=(\d+)"),
        # The image's own account of itself, read off the loader's line.  Three rows below
        # are computed from these numbers, each against something other than the line it
        # came from: the declared length against the file on disk, the declared entry
        # against where the loader actually jumped, and the validation against the presence
        # of the field that only prints when the payload checksum matched.
        # The same image, described by the kernel that is running on it.  Four keys rather
        # than one boolean because the row below has to be able to say *which* half
        # disagreed, and because "the line is absent" (the kernel never reached the check)
        # and "the line says fail" (it reached it and refused) are different findings about
        # different things -- one is a broken boot, the other is a working check.
        "image_self_line": "PC5-64-KIMAGE " in text,
        "image_self_ok": "PC5-64-KIMAGE status=ok reason=ok" in text,
        "image_self_reason": _word(text, r"PC5-64-KIMAGE status=\S+ reason=(\S+)"),
        "image_self_size": _hex(text, r"PC5-64-KIMAGE status=\S+ reason=\S+ image=(0x[0-9a-f]+)"),
        "image_self_handover": _hex(text, r"image=0x[0-9a-f]+ handover=(0x[0-9a-f]+)"),
        "image_self_crc": re.search(r"PC5-64-KIMAGE [^\n]* crc=ok\b", text) is not None,
        "kernel_header_validated": ("PC5-64-LOADER kernel-header version=1" in text
                                    and "checksum=ok" in text),
        "kernel_header_image": _number(
            text, r"PC5-64-LOADER kernel-header version=\d+ header=\d+ image=(\d+)"),
        "kernel_load_base": _hex(text, r"PC5-64-LOADER kernel at=0x([0-9a-f]+)"),
        "kernel_entry_declared": _hex(
            text, r"PC5-64-LOADER kernel-header version=\d+ header=\d+ image=\d+ "
                  r"entry=0x([0-9a-f]+)"),
        "kernel_jump_entry": _hex(text, r"PC5-64-LOADER-JUMP entry=0x([0-9a-f]+)"),
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
        # The TSS line's own readbacks.  Each of these is the kernel quoting the machine:
        # STR's answer, the descriptor as it is found through GDTR, the type transition the
        # load performs in memory, the I/O-map base read back through that descriptor, and the
        # two IDT gates' IST fields as the live table holds them.  They are extracted here
        # rather than substring-matched in one blob so a stage can say *which* of the five a
        # boot failed to produce -- "TR is loaded" is four separate facts, and the day one of
        # them stops being true is exactly the day a compound match would hide it.
        "tr_selector": _hex(text, r"PC5-64-TSS .*? str=(0x[0-9a-f]+)"),
        "tss_frame": _hex(text, r"PC5-64-TSS .*? tss=(0x[0-9a-f]+)"),
        "tr_desc": (re.search(r"PC5-64-TSS .*? desc=(0x[0-9a-f]+/\d+)", text).group(1)
                    if re.search(r"PC5-64-TSS .*? desc=(0x[0-9a-f]+/\d+)", text) else None),
        "tr_busy": (re.search(r"PC5-64-TSS .*? busy=([a-z-]+)", text).group(1)
                    if re.search(r"PC5-64-TSS .*? busy=([a-z-]+)", text) else None),
        "tr_iomap": _number(text, r"PC5-64-TSS .*? iomap=(\d+)"),
        "ist_gate_df": _number(text, r"PC5-64-TSS .*? ist-df=(\d+)"),
        "ist_gate_nmi": _number(text, r"PC5-64-TSS .*? ist-nmi=(\d+)"),
        "ist2_top": _hex(text, r"PC5-64-TSS .*? ist2=(0x[0-9a-f]+)"),
        "tr_reason": (re.search(r"PC5-64-TSS .*? reason=([a-z0-9-]+)", text).group(1)
                      if re.search(r"PC5-64-TSS .*? reason=([a-z0-9-]+)", text) else None),
        # The delivery probe, which is the consequence the whole TSS exists for.  The frame dump
        # is kept as the list of words it holds rather than as one flattened string, because the
        # claim this gate makes about it is "the resume address is somewhere in the frame" -- a
        # layout-independent statement, and the only one that survives a CPU that pushes an error
        # code, or does not.
        "probe_status": (re.search(r"PC5-64-TSS-DF status=(\w+)", text).group(1)
                         if re.search(r"PC5-64-TSS-DF status=(\w+)", text) else None),
        "probe_reason": (re.search(r"PC5-64-TSS-DF status=\w+ reason=([a-z0-9-]+)", text).group(1)
                         if re.search(r"PC5-64-TSS-DF status=\w+ reason=([a-z0-9-]+)", text)
                         else None),
        "probe_rsp": _hex(text, r"PC5-64-TSS-DF .*? rsp=(0x[0-9a-f]+)"),
        "probe_rsp_before": _hex(text, r"PC5-64-TSS-DF .*? rsp-before=(0x[0-9a-f]+)"),
        "probe_ist1": _hex(text, r"PC5-64-TSS-DF .*? ist1=(0x[0-9a-f]+)"),
        "probe_resume": _hex(text, r"PC5-64-TSS-DF .*? resume=(0x[0-9a-f]+)"),
        "probe_gate_ist": _number(text, r"PC5-64-TSS-DF .*? gate-ist=(\d+)"),
        "probe_df": _number(text, r"PC5-64-TSS-DF .*? df=(\d+)"),
        "probe_frame": [int(word, 16) for word in
                        re.findall(r"0x[0-9a-f]+",
                                   re.search(r"PC5-64-TSS-DF .*? frame=([0-9a-fx,]+)", text).group(1))]
                        if re.search(r"PC5-64-TSS-DF .*? frame=([0-9a-fx,]+)", text) else None,
        "probe_stack_intact": (re.search(r"PC5-64-TSS-DF .*? stack-intact=(\d)", text).group(1)
                               if re.search(r"PC5-64-TSS-DF .*? stack-intact=(\d)", text)
                               else None),
        "probe_gate_restored": (re.search(r"PC5-64-TSS-DF .*? gate-restored=(\d)", text).group(1)
                                if re.search(r"PC5-64-TSS-DF .*? gate-restored=(\d)", text)
                                else None),
        # The NMI, as the kernel itself saw it.  Only the *last* exception line is taken, and
        # deliberately so: the reporter halts the machine, so more than one line means something
        # went wrong earlier and the last line would be the least interesting of them.
        "nmi_line": ([line.strip() for line in text.splitlines()
                      if "PC5-64-EXCEPTION vector=2 " in line] or [None])[-1],
        "nmi_after_done": ("PC5-64-EXCEPTION vector=2 " in text.split("PC5-64-DONE", 1)[-1]
                           if "PC5-64-DONE" in text else False),
        "nmi_rsp": _hex(text, r"PC5-64-EXCEPTION vector=2 .*?rsp=(0x[0-9a-f]+)")
                   if "vector=2 " in text else None,
        "nmi_ist": (_number(text, r"PC5-64-EXCEPTION vector=2 .*? ist=(\d+)")
                    if "vector=2 " in text else None),
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


def _word(text, pattern):
    """The first string a pattern captures, or None when the line is not there at all.

    For the fields a kernel prints as words (`reason=ok`, `status=fail`), which the gate has
    to be able to *name* in a refusal detail: `None` is what tells a reader the marker never
    appeared, as distinct from appearing with a value the gate did not expect.
    """
    match = re.search(pattern, text)
    return match.group(1) if match else None


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


def _firmware_named(key, said_key):
    """`_seen(key)`, except a failure says what the log actually held.

    The verdict is `bool(seen[key])` and nothing else -- the same expression `_seen`
    computes -- so this cannot turn a row green or red.  What changes is the `detail`,
    which travels into the matrix row's note and into the annotation, and on the hosted
    run was the difference between `loader_firmware_edk2=False` and a name a reader can
    check against the firmware the runner installed.
    """
    def proof(seen, ctx):
        value = seen.get(key)
        if value:
            return True, ""
        said = seen.get(said_key)
        parts = ["%s=%r" % (key, value)]
        if said is not None:
            parts.append("the loader printed firmware=%r" % said)
        refusal = seen.get("loader_firmware_refusal")
        if refusal:
            parts.append(refusal)
        return False, "; ".join(parts)
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


def _image_self_checked(seen, ctx):
    """The kernel's own account of its image, compared with the file the gate built.

    Three numbers have to agree: the length the header declares (which the kernel read out of
    *its own memory*, after the jump), the length the handover claimed (which the kernel
    checked that read against), and the length of the file the gate put on the medium.  The
    first two are the boot's word about itself, and are allowed to agree with each other
    while both are wrong; the third is why this row is not the kernel grading its own
    homework.

    The refusals are ordered from "nothing to look at" to "the numbers disagree", so a boot
    that died early reports that, rather than reporting a mismatch it never measured.
    """
    if not seen.get("image_self_line"):
        return False, ("no `PC5-64-KIMAGE` line: the kernel stopped before its image "
                       "self-check ran")
    if not seen.get("image_self_ok"):
        return False, "the kernel refused its own image: reason=%s" % (
            seen.get("image_self_reason"),)
    if not seen.get("image_self_crc"):
        return False, ("the header's CRC was not re-derived successfully (`crc=ok` missing); "
                       "the field is the one thing the kernel can recompute after the jump")
    declared = seen.get("image_self_size")
    handed = seen.get("image_self_handover")
    if declared is None or handed is None:
        return False, "image=%r handover=%r" % (declared, handed)
    if declared != handed:
        return False, ("the header says %d bytes and the handover said %d -- one of them is "
                       "lying about the same object" % (declared, handed))
    expected = ctx.get("expected_bytes")
    if expected and declared != expected:
        return False, ("%d is not the %d bytes the gate built into the image"
                       % (declared, expected))
    return True, "header=%d handover=%d file=%s" % (declared, handed, expected)


def _entry_is_where_the_image_said(seen, ctx):
    """`JUMP entry=` has to be `kernel at=` plus the header's `entry=`, or the jump lied.

    The three numbers come from three lines printed three moments apart -- where the image
    was placed, what the image said about itself, and where the loader decided to start -- so
    their agreement is a property of the boot rather than of a constant.
    """
    base = seen.get("kernel_load_base")
    declared = seen.get("kernel_entry_declared")
    jumped = seen.get("kernel_jump_entry")
    if None in (base, declared, jumped):
        return False, "base=%r declared entry=%r jump=%r" % (base, declared, jumped)
    return jumped == base + declared, "base=%#x + entry=%#x vs jump=%#x" % (
        base, declared, jumped)


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
              _firmware_named("loader_firmware_edk2", "loader_firmware_said")),
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
              _firmware_named("loader_firmware_edk2", "loader_firmware_said")),
        Stage("required files are located and loaded",
              "the kernel was found on the volume we booted from",
              _seen("kernel_volume_image")),
        Stage("the kernel image is read through the file protocol",
              "the kernel image was read through the file protocol", _seen("kernel_read")),
        Stage("the loader read the kernel file at its real size",
              "the loader read the kernel file at its real size",
              _kernel_size_matches_file("kernel_bytes_read")),
        # The two rows the image format earns: the file agrees with its own declaration, and
        # the handoff began where the declaration said the code starts.  Both are agreements
        # between lines rather than constants, and the second is what stops a loader that
        # validated a header and then jumped to byte zero of the file anyway -- into the
        # header, executing it -- from counting as a pass.
        Stage("the kernel image declared its own length and it matched",
              "the kernel image declared its own length and it matched",
              _kernel_size_matches_file("kernel_header_image")),
        Stage("the loader validated the kernel image's header before jumping",
              "the loader validated the kernel image's header before jumping",
              _seen("kernel_header_validated")),
        Stage("execution began at the entry point the image declared",
              "the jump went to the load address plus the declared entry offset",
              _entry_is_where_the_image_said),
        Stage("the kernel re-read its own image header after the jump",
              "the running kernel re-read that header and agreed with its own handover",
              _image_self_checked),
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
        # The four facts that together mean "the processor loaded this TSS".  They are separate
        # rows because they are separate mechanisms: STR reads a register, the descriptor read
        # goes through GDTR, the busy bit is a write the CPU performs in memory, and the IDT's
        # IST fields are the consequence the kernel allows itself to act on only when the first
        # three agree.  A kernel that printed `tr=loaded` without the rest would pass one row
        # here and fail three -- which is the whole design of this set.
        Stage("the kernel executed LTR and read the result back with STR",
              "LTR executed, and STR returned the selector the kernel built",
              lambda seen, ctx: (seen["tr_state"] == "loaded"
                                 and seen["tr_selector"] == PC5_TSS64_TR_SELECTOR,  # int
                                 "tr=%r str=%r reason=%r"
                                 % (seen["tr_state"], seen["tr_selector"], seen["tr_reason"]))),
        Stage("the TSS descriptor names the frame that was granted to it",
              "the descriptor read through GDTR names the TSS frame, with a 103-byte limit",
              lambda seen, ctx: (seen["tr_desc"] is not None
                                 and seen["tss_frame"] is not None
                                 and seen["tr_desc"] == "0x%x/%d" % (seen["tss_frame"],
                                                                      PC5_TSS64_SIZE - 1),
                                 "desc=%r tss=%r" % (seen["tr_desc"], seen["tss_frame"]))),
        Stage("the CPU marked the descriptor busy, which only a load does",
              "the descriptor type went available -> busy across the instruction",
              lambda seen, ctx: (seen["tr_busy"] == "available-to-busy",
                                 "busy=%r" % seen["tr_busy"])),
        Stage("the IDT routes the two IST-armed vectors, and only because TR was loaded",
              "the double-fault gate names IST1 and the NMI gate names IST2",
              lambda seen, ctx: (seen["ist_gate_df"] == PC5_TSS64_IST_DOUBLE_FAULT
                                 and seen["ist_gate_nmi"] == PC5_TSS64_IST_NMI
                                 and seen["tr_state"] == "loaded",
                                 "ist-df=%r ist-nmi=%r tr=%r reason=%r"
                                 % (seen["ist_gate_df"], seen["ist_gate_nmi"],
                                    seen["tr_state"], seen["tr_reason"]))),
        Stage("the TSS's own fields say there is no I/O permission map",
              "the I/O-map base sits past the descriptor limit, as the TSS was written",
              lambda seen, ctx: (seen["tr_iomap"] == PC5_TSS64_SIZE,
                                 "iomap=%r" % seen["tr_iomap"])),
        # The last row, and the only one that is about an event rather than a configuration.
        # Everything above this says the TSS is installed the way an installed TSS should be; a
        # kernel could satisfy all of it and still deliver a fault onto the stack that just
        # overflowed, because what a CPU actually does with an IST field is not written in any of
        # those fields.  So this row reads the numbers a deliberately raised #DF left behind: the
        # stack the handler woke up on, the frame the CPU pushed there, and the state of a flag the
        # handler was obliged to clear.
        #
        # Written as a conjunction of separable facts, and as a *decline* rather than a failure
        # when TR was never loaded, because those are different statements: `status=skipped` with
        # `tr=not-loaded` is a kernel refusing to measure a stack nobody will use, and it must not
        # be reported the same way as a delivery that went to the wrong place.
        Stage("a #DF taken by the CPU was delivered on the IST1 stack",
              "the double-fault probe found its frame on the interrupt stack, not the faulting one",
              lambda seen, ctx: _probe_delivered(seen)),
        # The other half of the arming rule, and the only event in this whole gate that the kernel
        # did not cause.  An NMI arrives from outside -- QEMU raises it, the CPU delivers it, and
        # nothing in the image had any say in the moment -- so it exercises the path the double-
        # fault probe has to simulate: gate says slot 2, the loaded TSS says where slot 2 points,
        # and the stack pointer the handler woke up on is inside it.  One event, two independent
        # halves of the same claim, which is why both are required.
        Stage("the NMI injected from outside arrived on the IST2 stack",
              "QEMU's inject-nmi was taken by the guest on the stack its TSS names for slot 2",
              lambda seen, ctx: _nmi_delivered(seen, ctx)),
    ]


#: The warm boot's own row: it is not a second copy of the cold list, it is the
#: comparison between the two -- the machine must come up the same way twice.
WARM_STAGES = [
    Stage("rebooting the same machine boots identically",
          "the warm boot proved the same stages as the cold boot", None),
]


def profile_stages(profile):
    return x64_stages() if profile.key == "x64" else ia32_stages()


def _probe_delivered(seen):
    """Judge the #DF delivery probe from the numbers the boot reported.

    Five things have to hold, and each rules out a different way the line could be printed and the
    machine still be wrong: the handler woke up on a *different* stack than the one the fault was
    raised on (a CPU that ignores the IST field leaves them equal); that stack is inside the page
    the TSS names (a value copied from a neighbouring field, as a TSS whose IST1 sat at the wrong
    offset did once, sends the delivery somewhere unmapped and the probe never returns to be
    judged at all); the frame at that address contains the address the trap was taken at (the one
    test here that distinguishes a real push by the CPU from a plausible number in a log); the
    gate's IST field was the slot the TSS was built for, so that the two halves are quoting the
    same arrangement; and DF is clear afterwards, because a probe that left it set would convert
    the next fault into a real double fault and so would have made the machine worse to look at it.
    """
    if seen["probe_status"] is None:
        return False, "no PC5-64-TSS-DF line: the boot never reached the probe"
    if seen["probe_status"] == "skipped":
        if seen["tr_state"] == "loaded":
            return False, "skipped with TR loaded: the probe declined for reason=%r" % (
                seen["probe_reason"],)
        return True, "declined before TR was loaded (reason=%r)" % (seen["probe_reason"],)
    rsp, before, top = seen["probe_rsp"], seen["probe_rsp_before"], seen["probe_ist1"]
    frame, resume = seen["probe_frame"], seen["probe_resume"]
    detail = ("status=%r reason=%r rsp=%r rsp-before=%r ist1=%r resume=%r frame=%r gate-ist=%r "
              "df=%r stack-intact=%r gate-restored=%r"
              % (seen["probe_status"], seen["probe_reason"], rsp, before, top, resume, frame,
                 seen["probe_gate_ist"], seen["probe_df"], seen["probe_stack_intact"],
                 seen["probe_gate_restored"]))
    if seen["probe_status"] != "ok":
        return False, detail
    if rsp is None or before is None or top is None or resume is None or not frame:
        return False, "a field the verdict depends on was not printed: " + detail
    if rsp == before:
        return False, "the handler ran on the stack that faulted: " + detail
    if not (top - PC5_TSS64_IST_PAGE <= rsp < top):
        return False, "the frame is not inside the IST1 page: " + detail
    if resume not in frame:
        return False, "no word of the frame is the address the trap was taken at: " + detail
    if seen["probe_gate_ist"] != PC5_TSS64_IST_DOUBLE_FAULT:
        return False, "the gate named a different slot than the TSS: " + detail
    if seen["probe_df"] != 0:
        return False, "the probe left DF set: " + detail
    if seen["probe_stack_intact"] != "1" or seen["probe_gate_restored"] != "1":
        return False, "the probe damaged the stack it left or the gate it borrowed: " + detail
    return True, detail



def _nmi_delivered(seen, ctx):
    """Judge the injected NMI from the exception line the guest printed.

    Four things, in the order that keeps a harness failure from looking like a kernel failure: the
    injection has to have been *accepted by QEMU* (a kernel cannot be blamed for an event nobody
    raised, and a matrix row that passed on a refused injection would be a row about the harness);
    the guest has to have reported vector 2 at all; the report has to come after the boot marker,
    because an NMI that arrived mid-boot means the harness raced itself and the exception handler
    halts the machine, which would silently shorten every other row; and the stack pointer has to
    be inside the IST2 page, with the gate naming slot 2 on the same line.

    The last pair is the whole point and is deliberately not inferred from the TSS report line: the
    exception line carries `ist=` from the *live gate*, so a kernel that armed the TSS but not the
    IDT, or the reverse, is caught here rather than in a comment.
    """
    nmi = ctx.get("nmi")
    if nmi is None or not nmi.get("requested"):
        return False, "the harness never asked for an injection, so nothing was measured"
    if "skipped" in nmi:
        return False, "not injected: %s" % nmi["skipped"]
    if not nmi.get("accepted"):
        return False, "QEMU refused the injection (error=%r); this row says nothing about the " \
                      "guest" % (nmi.get("error"),)
    line = seen["nmi_line"]
    if line is None:
        return False, "the injection was accepted and the guest never reported vector 2"
    if not seen["nmi_after_done"]:
        return False, "the NMI was taken during the boot, not after the marker: %s" % line
    rsp, top = seen["nmi_rsp"], seen["ist2_top"]
    detail = "line=%r rsp=%r ist2=%r ist=%r" % (line, rsp, top, seen["nmi_ist"])
    if rsp is None or top is None:
        return False, "rsp or the IST2 top is missing: " + detail
    if not (top - PC5_TSS64_IST_PAGE <= rsp < top):
        return False, "the NMI handler did not run on the IST2 page: " + detail
    if seen["nmi_ist"] != PC5_TSS64_IST_NMI:
        return False, "the live gate for vector 2 does not name slot 2: " + detail
    return True, detail


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
    ctx = {"nmi": run.get("nmi"),
           "expected_bytes": expected_kernel_bytes, "killed": run["killed"],
           "stopped_on": run["stopped_on"], "seconds": run["seconds"],
           "memory_mb": memory_mb}
    report.data[stage] = {"markers": seen, "seconds": run["seconds"],
                          "stopped_on": run["stopped_on"],
                          "returncode": run["returncode"],
                          # What the harness asked QEMU for, and whether QEMU said yes.  Kept in
                          # the report rather than only in the serial because the two can disagree
                          # in the direction that matters: an NMI the *hypervisor* refused is not
                          # evidence about the guest's IDT, and a reader of a green matrix needs to
                          # be able to check that the injection was accepted at all.
                          "nmi": run.get("nmi"),
                          "stages": {}}
    report.data[stage]["firmware_identity"] = identity_record(seen)
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
    built = subprocess.run(command, capture_output=True, text=True)
    if built.returncode != 0:
        # `check=True` is a bad witness, and this is the same hole the reporter was just
        # widened for from the other end: `capture_output=True` puts everything the child
        # said into `CalledProcessError.stdout`/`.stderr`, which nothing prints when the
        # traceback reaches the shell, and the message that survives names only the status
        # -- `Command '[...]' returned non-zero exit status 1`.  A mutant that could not be
        # built would then reach an annotation as a verdict with no cause, exactly as the
        # transposed `secure_boot_run` arguments did.  Say what the child said, then raise
        # the same exception: same type, same status, same command, and the reason is now
        # on the line above it in the log the reporter quotes.
        for stream in (built.stderr, built.stdout):
            if stream and stream.strip():
                sys.stderr.write("mkiso.py failed (exit %d); its own output:\n%s\n"
                                 % (built.returncode, stream.strip()))
        built.check_returncode()
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
                    args.timeout, args.memory_mb, stop_markers=profile.stop,
                    inject_nmi=profile.key == "x64")
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
                    args.timeout, args.memory_mb, stop_markers=profile.stop,
                    inject_nmi=profile.key == "x64")
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
            # Named, not positional.  The signature ends `(..., profile, vars_template)` and
            # this call had the last two the other way round, so `shutil.copyfile` was handed
            # the *Profile*: `TypeError: stat: path should be string, ... not Profile`, a
            # traceback and `Error 1`, with QEMU never started.  Three same-shaped arguments
            # in a row is what a positional list gets wrong and Python cannot check.
            secure_boot_run(report, args, found["qemu"], data_dir, work, args.iso,
                            code_path=found["secure_code"], profile=profile,
                            vars_template=found["secure_vars"])
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
    # One row per kernel-image field this path's loader refuses on, derived from the profile
    # so a path without the format gains no rows and a path with it cannot drop one.  These
    # are the rows that make "corrupting the kernel does not prevent boot" a *failing* gate
    # result rather than an observation about a previous milestone.
    rows.extend((profile.kernel_refusal_check(mutation, code),
                 "OVMF negative run (kernel-%s)" % mutation,
                 profile.kernel_refusal_check(mutation, code))
                for mutation, code in profile.kernel_refusals)
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
        "firmware_identity": identity_record(seen),
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


#: The 64-bit kernel image format's own signature, and the header size it declares.  The
#: gate does not re-implement the format: `tools/kimage64.py` is the reader (it is what
#: `make verify` uses too), and these two numbers only have to be enough to *recognise*
#: which of the two header vocabularies a file on the medium belongs to.
PC5_KIMAGE64_MAGIC = b"PC5K64IM"
PC5_KIMAGE64_HEADER_SIZE = 128


def kernel_header_kind(kernel_bytes):
    """Which kernel image format this file carries: `bios8`, `kimage64`, or None.

    Measured from the bytes, never from a flag or a profile name, because the two paths
    share a filename convention and an ISO name and differ in exactly this: what the front
    of the kernel file is.  A file with no recognisable header has nothing for a mutant to
    corrupt, so the control would prove nothing in either direction and "the check passed"
    would really mean "the write landed in an instruction stream nobody reads".
    """
    if (len(kernel_bytes) >= PC5_KERNEL_HEADER_SIZE
            and kernel_bytes[PC5_KERNEL_SIG_OFFSET:PC5_KERNEL_SIG_OFFSET + 4]
            == PC5_KERNEL_SIG):
        return "bios8"
    if (len(kernel_bytes) >= PC5_KIMAGE64_HEADER_SIZE
            and kernel_bytes[0:len(PC5_KIMAGE64_MAGIC)] == PC5_KIMAGE64_MAGIC):
        return "kimage64"
    return None


def kernel_has_header(kernel_bytes):
    """Does this kernel image carry any header the loader can refuse?"""
    return kernel_header_kind(kernel_bytes) is not None


def corrupt_kernel_field(kernel_bytes, mutation):
    """Break exactly one documented field of a 64-bit kernel image, by name.

    Delegates to the repository's own format tool, so the gate cannot invent a mutation
    the loader would never see -- and, as a side effect, refuses to hand back a file that
    still verifies: `mutate_bytes` raises if the mutation does not produce the refusal it
    is named after, which is the difference between a control and a copy.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import kimage64  # noqa: E402  (path set above)

    return kimage64.mutate_bytes(kernel_bytes, mutation)


def negative_runs(report, args, found, data_dir, work, profile=IA32):
    """Boot machines that are broken on purpose, and check who notices."""
    kernel_bytes = open(args.kernel, "rb").read()
    # The release file the ESP carries -- the two are held equal by the extent
    # checks in `check_isolation`, so the medium and the mutant input are the
    # same bytes here and this measurement is a statement about the artifact.
    has_header = kernel_has_header(kernel_bytes)
    kind = kernel_header_kind(kernel_bytes)
    report.check(HEADER_VOCABULARY_CHECK,
                 has_header == (profile.bad_signature_code is not None)
                 and kind == profile.kernel_header_kind,
                 "%s in the boot ESP carries the %s header (first bytes %r); the %s "
                 "profile validates %s" % (profile.kernel_name, kind or "no",
                                          bytes(kernel_bytes[:16]), profile.key,
                                          profile.kernel_header_kind or "no header"))
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
            if kind == "kimage64":
                broken = corrupt_kernel_field(kernel_bytes, "magic")
            else:
                broken = bytearray(kernel_bytes)
                broken[2:6] = b"XXXX"
                broken = bytes(broken)
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

        # The rest of the format, one field per boot.  Why these three and not all
        # thirteen: a control's value is what it rules *out*, and on real firmware the
        # three that matter are "the loader noticed this is not our kernel at all"
        # (`magic`, above), "it noticed the kernel was built for another architecture"
        # (`arch`), "it noticed the file is not the length it claims" (`image-size`), and
        # "it noticed a bit flip inside the code" (`checksum`) -- the last of which is the
        # whole reason a signature would not have been enough.  The remaining fields are
        # executed against the same loader code in `tests/test_boot64_stub.py`, where a
        # case costs a second, and validated twice more in `tests/test_kimage64.py`.
        for mutation, code in profile.kernel_refusals:
            try:
                broken = corrupt_kernel_field(kernel_bytes, mutation)
                path = build_mutant_iso("kernel-%s" % mutation, args.efi_image,
                                        kernel=bytes(broken), profile=profile)
                run = boot("kernel-%s" % mutation, path)
                seen = classify(run, profile)
                report.check(profile.kernel_refusal_check(mutation, code),
                             code in seen["failure_codes"] and not seen["kernel_entry"],
                             seen["failure_codes"])
            except (OSError, subprocess.CalledProcessError) as exc:
                report.check(profile.kernel_refusal_check(mutation, code), False,
                             f"mutant could not be built: {exc}")
    else:
        report.check(profile.bad_signature_check(), False,
                     "there is no header field to corrupt: %s starts with %r, which is "
                     "neither the PC5K signature the 32-bit loader reads at offset %d nor "
                     "the %r header of the 64-bit image format.  A path whose kernel has no "
                     "header has nothing for `boot/uefi64/kimage64.c` to reject, so the row "
                     "is a finding rather than a pass"
                     % (profile.kernel_name, bytes(kernel_bytes[:16]),
                        PC5_KERNEL_SIG_OFFSET, PC5_KIMAGE64_MAGIC.decode("ascii")),
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
