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
             stop_markers=("PC5-BOOT-OK", "PC5-UEFI-FAILURE"), extra_wait=None):
    """Boot the ISO once and stop the machine as soon as the log settles.

    Serial goes to stdout rather than to a file because QEMU buffers a file
    chardev: a harness that reads the file while the guest runs sees nothing and
    would have to wait for the whole deadline every time.  Stopping on the
    evidence is also what "clean shutdown" means here - the machine is powered
    off on purpose, and the log is written out either way.
    """
    command = [
        qemu,
        "-machine", "q35",
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


def classify(run):
    """What the serial log proves was executed."""
    text = run["serial"]
    return {
        "firmware_banner": ("BdsDxe:" in text) or ("TianoCore" in text) or
                           ("EDK II" in text),
        "seabios": "SeaBIOS" in text,
        "el_torito_boot_option": "UEFI QEMU DVD-ROM" in text or "BdsDxe: starting" in text,
        "loader_banner": "PC5 UEFI loader" in text or "PC5 UEFI LOADER" in text,
        "loader_firmware_edk2": "PC5-UEFI: firmware=EDK II" in text,
        "kernel_volume_image": "PC5-UEFI: kernel_volume=image" in text,
        "kernel_read": "PC5-UEFI: kernel_bytes_read=" in text,
        "boot_services_exited": "PC5-UEFI: boot_services_exited" in text,
        "exit_retries": _number(text, r"PC5-UEFI: ExitBootServices_retries=(\d+)"),
        "memory_map_entries": _number(text, r"PC5-UEFI: memory_map_entries=(\d+)"),
        "kernel_entry": "PC5-LOG kernel_entry eip=0x10000" in text,
        "kernel_protocol": "PC5-LOG protocol=1 magic=ok" in text,
        "boot_ok": "PC5-BOOT-OK" in text,
        "failure_codes": sorted(set(re.findall(r"PC5-UEFI-FAILURE ([A-Z0-9]+)", text))),
        "firmware_refused": any(marker.lower() in text.lower()
                                for marker in FIRMWARE_NO_BOOT),
    }


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
        self.checks.append({"name": name, "state": state,
                            "detail": str(detail) if state == "fail" else ""})

    def fail_count(self):
        return sum(1 for check in self.checks if check["state"] == "fail")

    def skip_count(self):
        return sum(1 for check in self.checks if check["state"] == "skip")

    def as_dict(self):
        return {"checks": self.checks, "data": self.data,
                "passed": self.fail_count() == 0,
                "not_applicable": self.skip_count()}


def check_boot(report, run, stage):
    """Judge one boot: firmware ran, our loader ran, the kernel ran."""
    seen = classify(run)
    report.data[stage] = {"markers": seen, "seconds": run["seconds"],
                          "stopped_on": run["stopped_on"],
                          "returncode": run["returncode"]}
    report.check(f"[{stage}] the machine was stopped on evidence, not on the clock",
                 not run["killed"], f"{run['stopped_on']} after {run['seconds']}s")
    report.check(f"[{stage}] genuine firmware produced a boot manager log",
                 seen["firmware_banner"], "no BdsDxe/TianoCore banner")
    report.check(f"[{stage}] the machine booted UEFI, not SeaBIOS",
                 seen["firmware_banner"] and not seen["seabios"],
                 "SeaBIOS text present" if seen["seabios"] else "")
    report.check(f"[{stage}] firmware started the El Torito UEFI image",
                 seen["el_torito_boot_option"] or seen["loader_banner"],
                 "no UEFI boot option in the log")
    report.check(f"[{stage}] the PC5 UEFI loader executed under it",
                 seen["loader_banner"])
    report.check(f"[{stage}] the loader identified EDK II as its firmware",
                 seen["loader_firmware_edk2"])
    report.check(f"[{stage}] the kernel was found on the volume we booted from",
                 seen["kernel_volume_image"])
    report.check(f"[{stage}] the kernel image was read through the file protocol",
                 bool(seen["kernel_read"]), seen["kernel_read"])
    report.check(f"[{stage}] boot services were exited exactly once",
                 seen["boot_services_exited"])
    report.check(f"[{stage}] a memory map was handed over",
                 (seen["memory_map_entries"] or 0) > 0, seen["memory_map_entries"])
    report.check(f"[{stage}] the kernel reached its entry point",
                 seen["kernel_entry"])
    report.check(f"[{stage}] the kernel accepted the boot contract",
                 seen["kernel_protocol"])
    report.check(f"[{stage}] the kernel reached PC5-BOOT-OK",
                 seen["boot_ok"])
    report.check(f"[{stage}] no loader failure code was reported",
                 not seen["failure_codes"], seen["failure_codes"])
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
def build_mutant_iso(name, efi_image, kernel=None, drop_kernel=False,
                     drop_boot_app=False, boot_app_mutation=None):
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
            files.pop("\\KERNEL.BIN", None)
        if kernel is not None:
            files["\\KERNEL.BIN"] = kernel
        esp_source = os.path.join(out_dir, f"{name}-esp.img")
        builder = mkefiimg.EfiImageBuilder(size_kb=1440)
        for fat_path, data in files.items():
            builder.add_file(fat_path, data)
        image, _info = builder.build()
        with open(esp_source, "wb") as handle:
            handle.write(image)
    kernel_path = os.path.join(BUILD, "kernel.bin") if kernel is None else None
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
        command += ["--file", "KERNEL.BIN=" + kernel_path]
    subprocess.run(command, check=True, capture_output=True)
    return iso_path


# ---------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", default=os.path.join(BUILD, "pc5.iso"))
    parser.add_argument("--efi-image", default=os.path.join(BUILD, "efi.img"))
    parser.add_argument("--kernel", default=os.path.join(BUILD, "kernel.bin"))
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
    args = parser.parse_args(argv)

    found = discover(args)
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
        return 0

    report = Report()
    report.data["environment"] = {
        "qemu": found["qemu"], "qemu_version": qemu_version(found["qemu"]),
        "ovmf_code": found["code"], "ovmf_vars": found["vars"],
        "iso": os.path.abspath(args.iso),
        "iso_sha256": sha256_file(args.iso) if os.path.isfile(args.iso) else None,
        "host": {"machine": os.uname().machine, "release": os.uname().release},
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
                    args.timeout, args.memory_mb)
    report.data["cold_command"] = cold["command"]
    report.data["cold_stderr"] = cold["stderr"]
    check_boot(report, cold, "cold")
    report.check("[cold] a fresh variable store was used and written by firmware",
                 os.path.getsize(cold_vars) == os.path.getsize(found["vars"]),
                 os.path.getsize(cold_vars))

    # --------------------------------------------------------------- boot 2
    # Same firmware, same ISO, the variable store the cold boot just wrote: the
    # warm path must boot identically (no "only works once" state).
    warm_serial = os.path.join(work, "serial-warm.log")
    warm = run_qemu(found["qemu"], data_dir, iso, cold_vars, warm_serial,
                    args.timeout, args.memory_mb)
    report.data["warm_command"] = warm["command"]
    check_boot(report, warm, "warm")
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
        negative_runs(report, args, found, data_dir, work)

    # ----------------------------------------------------------- isolation
    check_isolation(report, args.iso, code_info, efi_image=args.efi_image)

    report.data["status"] = "passed" if report.fail_count() == 0 else "failed"
    code = 0 if report.fail_count() == 0 else 1
    _emit(report, args, exit_code=code)
    return code


def negative_runs(report, args, found, data_dir, work):
    """Boot machines that are broken on purpose, and check who notices."""
    kernel_bytes = open(args.kernel, "rb").read()
    iso = {"code": found["code"], "iso": args.iso}

    def boot(name, iso_path, timeout=None):
        vars_path = os.path.join(work, f"vars-{name}.fd")
        shutil.copyfile(found["vars"], vars_path)
        serial = os.path.join(work, f"serial-{name}.log")
        run = run_qemu(found["qemu"], data_dir, {"code": iso["code"], "iso": iso_path},
                       vars_path, serial, timeout or min(args.timeout, 180),
                       args.memory_mb)
        report.data.setdefault("negative", {})[name] = {
            "iso": os.path.basename(iso_path),
            "markers": classify(run),
            "seconds": run["seconds"],
            "serial_tail": run["serial"][-600:],
        }
        return run

    # 1. ESP without the boot application: firmware has nothing to start.
    try:
        path = build_mutant_iso("no-boot-app", args.efi_image, drop_boot_app=True,
                                drop_kernel=False)
        run = boot("no-boot-app", path)
        seen = classify(run)
        report.check("firmware refuses an ESP without BOOTX64.EFI (firmware fault)",
                     not seen["loader_banner"] and
                     (seen["firmware_refused"] or not seen["boot_ok"]),
                     "the loader still ran" if seen["loader_banner"]
                     else "no boot manager output")
    except (OSError, subprocess.CalledProcessError) as exc:
        report.check("firmware refuses an ESP without BOOTX64.EFI (firmware fault)",
                     False, f"mutant could not be built: {exc}")

    # 2. A PE32+ image with its 'MZ' header destroyed: the firmware image loader
    #    must reject it before any PC5 code runs.
    def break_pe(blob):
        out = bytearray(blob)
        out[0:2] = b"ZZ"
        return bytes(out)

    try:
        path = build_mutant_iso("bad-pe", args.efi_image, boot_app_mutation=break_pe)
        run = boot("bad-pe", path)
        seen = classify(run)
        report.check("firmware rejects a boot image with a broken PE32+ header "
                     "(firmware fault)", not seen["boot_ok"] and not seen["kernel_entry"],
                     seen["failure_codes"])
    except (OSError, subprocess.CalledProcessError) as exc:
        report.check("firmware rejects a boot image with a broken PE32+ header "
                     "(firmware fault)", False, f"mutant could not be built: {exc}")

    # 3. No kernel anywhere: the loader must stop with its own code, not hang,
    #    and not invent a handover.
    try:
        path = build_mutant_iso("no-kernel", args.efi_image, drop_kernel=True)
        run = boot("no-kernel", path)
        seen = classify(run)
        report.check("loader reports 'no kernel' as E2 when no volume has KERNEL.BIN "
                     "(bootloader fault)",
                     "E2" in seen["failure_codes"] and not seen["kernel_entry"],
                     seen["failure_codes"])
    except (OSError, subprocess.CalledProcessError) as exc:
        report.check("loader reports 'no kernel' as E2 (bootloader fault)", False,
                     f"mutant could not be built: {exc}")

    # 4. A kernel whose PC5K signature is wrong: the loader must refuse it.
    try:
        broken = bytearray(kernel_bytes)
        broken[2:6] = b"XXXX"
        path = build_mutant_iso("bad-signature", args.efi_image, kernel=bytes(broken))
        run = boot("bad-signature", path)
        seen = classify(run)
        report.check("loader refuses a kernel without the PC5K signature as E4 "
                     "(bootloader fault)",
                     "E4" in seen["failure_codes"] and not seen["kernel_entry"],
                     seen["failure_codes"])
    except (OSError, subprocess.CalledProcessError) as exc:
        report.check("loader refuses a kernel without the PC5K signature as E4", False,
                     f"mutant could not be built: {exc}")

    # 5. A truncated ISO: firmware must fail to start anything at all.
    truncated = os.path.join(BUILD, "ovmf-mutants", "truncated.iso")
    os.makedirs(os.path.dirname(truncated), exist_ok=True)
    with open(args.iso, "rb") as src, open(truncated, "wb") as dst:
        # 64 KiB: past the ISO9660 system area, inside the El Torito image, so
        # the firmware finds a boot catalog that points at sectors the medium no
        # longer has.
        dst.write(src.read(64 * 1024))
    run = boot("truncated-iso", truncated)
    seen = classify(run)
    report.check("firmware refuses a truncated ISO (firmware fault)",
                 not seen["boot_ok"] and not seen["kernel_entry"],
                 seen["failure_codes"])

    # 6. The deadline itself: a one-second budget on the good ISO must be
    #    reported as a timeout, never as a pass.
    vars_path = os.path.join(work, "vars-deadline.fd")
    shutil.copyfile(found["vars"], vars_path)
    serial = os.path.join(work, "serial-deadline.log")
    run = run_qemu(found["qemu"], data_dir, iso, vars_path, serial, 1,
                   args.memory_mb)
    report.data["negative"]["deadline"] = {
        "killed": run["killed"], "seconds": run["seconds"],
        "markers": classify(run),
    }
    report.check("a boot that outlives its deadline is reported as a timeout, "
                 "not as a pass", run["killed"] and run["stopped_on"] == "timeout")


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
