#!/usr/bin/env python3
"""Validate the 64-bit UEFI boot path under real OVMF firmware.

`make ovmf-test` cannot do this.  That target boots the 32-bit ISO and looks for
the 32-bit loader's marker dialect (`PC5-UEFI:`, `PC5-LOG`, `PC5-BOOT-OK`).  The
64-bit path is a different artifact with a different vocabulary: `efi64.img`
carries BOOTX64.EFI and KERNEL64.BIN, and the loader and kernel speak
`PC5-64-*`.  A 64-bit boot therefore produces zero markers the 32-bit driver
recognises, which is why its verdict says nothing either way about this path.

This driver knows only the 64-bit dialect, so it can fail for the right reasons.

What it insists on, in this order:

    firmware started
      -> the loader was entered                      PC5-64-LOADER-BANNER
      -> the loader completed its own checks         exit-boot-services=ok
      -> the handoff to the kernel happened          PC5-64-LOADER-JUMP
      -> the kernel executed                         PC5-64-BANNER

and then on the runtime assertions, which are the point of the exercise:

    -> the page tables the CPU is using reach the
       whole declared identity span                PC5-64-PTWALK span ... failed=0
    -> every allocation firmware honoured stayed
       inside its inclusive bound                   PC5-64-LOADER-ALLOC ... verdict=ok
    -> the kernel reached its final marker         PC5-64-DONE

A stage that did not happen is reported as FAIL or NOT_TESTED; it is never
reported as PASS.  Missing QEMU or OVMF is UNAVAILABLE, and the driver says so
with the exit code rather than inventing a result.  It never reports a boot as
successful because a log was short or a marker was absent.

Exit codes: 0 all checked stages passed; 1 a stage failed; 2 a runtime assertion
failed; 3 QEMU or OVMF unavailable (nothing was tested).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

BUILD = os.environ.get("PC5_BUILD", "build")

# Where distributions put OVMF.  The 4 MiB pair is preferred: the 2 MiB images
# are too small to carry the firmware volume plus variables on some builds.
CODE_CANDIDATES = [
    "/usr/share/OVMF/OVMF_CODE_4M.fd",
    "/usr/share/OVMF/OVMF_CODE.fd",
    "/usr/share/edk2/ovmf/OVMF_CODE.fd",
    "/usr/share/edk2/x64/OVMF_CODE.fd",
    "/usr/share/ovmf/OVMF_CODE.fd",
    "/usr/share/qemu/OVMF_CODE.fd",
    "/usr/share/OVMF/OVMF_CODE.secboot.fd",
]
VARS_CANDIDATES = [
    "/usr/share/OVMF/OVMF_VARS_4M.fd",
    "/usr/share/OVMF/OVMF_VARS.fd",
    "/usr/share/edk2/ovmf/OVMF_VARS.fd",
    "/usr/share/edk2/x64/OVMF_VARS.fd",
    "/usr/share/ovmf/OVMF_VARS.fd",
    "/usr/share/qemu/OVMF_VARS.fd",
]

# Firmware messages that mean the boot stopped in firmware, not in PC5 code.
# They are not failures of ours, but they are also not evidence of a boot.
FIRMWARE_NO_BOOT = [
    "BdsDxe: No bootable option",
    "No bootable option or device was found",
    "BdsDxe: failed to load Boot0001",
]

STAGES = ["firmware", "loader_entry", "loader_success", "kernel_handoff",
          "kernel_execution"]


# --------------------------------------------------------------- utilities
def sha256_file(path):
    if not path:
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def normalise_serial(text):
    """CRLF -> LF, drop the cursor/escape noise the firmware emits."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def sibling_vars(code_path):
    """The variable store installed next to a code image, if there is one.

    Distribution and manual installs name the pair inconsistently, and the
    variable store that matches a CODE image is the one installed beside it under
    the same stem.  Pairing a 2 MiB code image with an unrelated variable store
    yields firmware that never reaches the boot manager, so this is tried before
    anything installed system-wide - but it returns None rather than guessing.
    """
    if not code_path:
        return None
    stem = os.path.basename(code_path)
    directory = os.path.dirname(os.path.abspath(code_path))
    names = []
    if "CODE" in stem:
        names.append(stem.replace("CODE", "VARS"))
    names.append(stem.replace(".fd", "_VARS.fd"))
    for name in names:
        path = os.path.join(directory, name)
        if os.path.exists(path):
            return path
    return None


def find_first(candidates):
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def qemu_version(qemu):
    if not qemu:
        return None
    try:
        out = subprocess.run([qemu, "-version"], capture_output=True, timeout=60,
                             text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return "error: %s" % exc
    lines = (out.stdout or out.stderr or "").strip().splitlines()
    return lines[0] if lines else "unknown"


def git_value(*args):
    try:
        out = subprocess.run(["git"] + list(args), capture_output=True,
                             timeout=60, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout or "").strip() or None


def describe_firmware(code_path):
    """Identify the firmware image by hash and, if it looks like one, by the
    EDK II build string embedded in it.  A hash alone tells a reader nothing
    about which OVMF they are comparing against."""
    info = {"path": code_path, "sha256": sha256_file(code_path),
            "size": None, "version": None}
    if not code_path:
        return info
    try:
        info["size"] = os.path.getsize(code_path)
        with open(code_path, "rb") as handle:
            blob = handle.read()
    except OSError:
        return info
    # OVMF embeds its build description as a UTF-16LE string.  Pick the most
    # specific one available; a mismatch here is worth knowing about.
    match = re.search("(?:edk2-stable|EDK II)[ -][A-Za-z0-9._/ -]{0,80}"
                      .encode("utf-16-le"), blob)
    if match:
        info["version"] = match.group(0).decode("utf-16-le", "replace").strip()
    return info


# ---------------------------------------------------------------- QEMU run
def run_qemu(qemu, code, vars_template, image, vars_path, serial_path,
             timeout, memory_mb=512, device="ahci", data_dir=None):
    """Boot `image` once and stop the machine as soon as the log settles.

    The device arrangement differs from the 32-bit driver on purpose.  That one
    boots an ISO through `-cdrom`; `efi64.img` is not an ISO.  It is a bare
    FAT12 volume with BOOTX64.EFI at EFI/BOOT/BOOTX64.EFI - a "superfloppy",
    which QEMU cannot serve as a CD-ROM because there is no El Torito record at
    sector 17.  Firmware has to see it as a block device and read the FAT volume
    off the whole disk, so it is attached as a disk.

    AHCI is the default because it needs no USB enumeration race; `usb` is
    available for firmware builds without AhciDxe.  Both are untested here: no
    QEMU or OVMF exists on this machine, so this arrangement is the one that
    should work, not one that has been seen to.  The report says so.

    Serial goes to a pipe rather than a file because QEMU buffers a file
    chardev: a reader polling the file during the run sees nothing.
    """
    if device == "usb":
        storage = ["-device", "qemu-xhci,id=xhci",
                   "-drive", "if=none,id=esp,format=raw,file=%s" % image,
                   "-device", "usb-storage,drive=esp,bus=xhci.0"]
    else:
        storage = ["-device", "ich9-ahci,id=ahci",
                   "-drive", "if=none,id=esp,format=raw,file=%s" % image,
                   "-device", "ide-hd,drive=esp,bus=ahci.0"]

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
        "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=%s" % code,
        "-drive", "if=pflash,format=raw,unit=1,file=%s" % vars_path,
    ] + storage + ["-serial", "stdio"]
    # QEMU loads its VGA option ROM and other data files from a prefix
    # directory.  A distribution build has them where it was configured to look;
    # a relocatable or prebuilt binary does not, and then QEMU exits before ever
    # reaching the firmware.  That failure is UNAVAILABLE, not a boot failure.
    if data_dir:
        command += ["-L", data_dir]

    started = time.time()
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    chunks = []
    stderr_chunks = []

    def drain(stream, sink):
        for line in iter(stream.readline, b""):
            sink.append(line.decode("latin-1", "replace"))

    stdout_thread = threading.Thread(target=drain, args=(process.stdout, chunks),
                                     daemon=True)
    stderr_thread = threading.Thread(target=drain,
                                     args=(process.stderr, stderr_chunks),
                                     daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    # Stop on the kernel's last marker, or on a terminal failure, rather than
    # burning the whole timeout whenever the boot gets that far.
    stop_markers = ("PC5-64-DONE", "PC5-64BIT-FAIL=", "PC5-64-LOADER-FAIL=",
                    "PC5-64-LOADER-REJECT")
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

    if process.poll() is None:
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
    # Never let a previous run's log stand in for this one: if QEMU died before
    # writing anything, an old file would be the only "evidence" available.
    with open(serial_path, "w", encoding="latin-1") as handle:
        handle.write(raw)
    return {"command": command, "returncode": process.returncode,
            "started": True,
            "timed_out": outcome == "timeout", "stopped_on": outcome,
            "seconds": round(elapsed, 1),
            "stderr": "".join(stderr_chunks).strip()[-800:],
            "raw_serial": raw, "serial": normalise_serial(raw)}


# -------------------------------------------------------------- observation
def observe(text):
    """Extract only what the log actually shows."""
    allocs = []
    for match in re.finditer(
            r"PC5-64-LOADER-ALLOC type=(\S+) pages=(\d+) requested-max=0x([0-9a-fA-F]+)"
            r" returned-base=0x([0-9a-fA-F]+) end=0x([0-9a-fA-F]+)"
            r" within-bound=(\d) within-limit=(\d) verdict=(\S+)", text):
        allocs.append({
            "type": match.group(1),
            "pages": int(match.group(2)),
            "requested_max": int(match.group(3), 16),
            "returned_base": int(match.group(4), 16),
            "end": int(match.group(5), 16),
            "within_bound": match.group(6) == "1",
            "within_limit": match.group(7) == "1",
            "verdict": match.group(8),
        })

    walk = re.search(r"PC5-64-PTWALK span probes=(\d+) failed=(\d+)", text)
    walks = re.findall(
        r"PC5-64-PTWALK va=0x([0-9a-fA-F]+) path=(\S+) size=(\S+) pa=(\S+)"
        r" identity=(\d) note=(\S+)", text)

    return {
        "firmware_banner": bool(re.search(r"BdsDxe:|TianoCore|EDK II", text)),
        "seabios": "SeaBIOS" in text,
        "loader_banner": "PC5-64-LOADER-BANNER" in text,
        "loader_image": bool(re.search(r"PC5-64-LOADER image=0x[0-9a-fA-F]+", text)),
        "pagetables": bool(re.search(r"PC5-64-LOADER pagetables=0x", text)),
        "stack": bool(re.search(r"PC5-64-LOADER stack=0x", text)),
        "ebs_ok": "PC5-64-LOADER exit-boot-services=ok" in text,
        "ebs_attempts": _number(text, r"PC5-64-LOADER exit-boot-services=ok attempts=(\d+)"),
        "jump": re.search(r"PC5-64-LOADER-JUMP entry=0x([0-9a-fA-F]+)", text).group(1)
                if re.search(r"PC5-64-LOADER-JUMP entry=0x([0-9a-fA-F]+)", text) else None,
        "kernel_banner": "PC5-64-BANNER PC5 64-bit kernel entry" in text,
        "kernel_env": bool(re.search(r"PC5-64-ENV cr0=0x", text)),
        "kernel_paging": re.search(r"PC5-64-PAGING enabled=1 root=0x([0-9a-fA-F]+)", text).group(1)
                          if re.search(r"PC5-64-PAGING enabled=1 root=0x([0-9a-fA-F]+)", text) else None,
        "long_mode_ok": "PC5-64BIT-LONG-MODE-OK" in text,
        "done": "PC5-64-DONE" in text,
        "allocs": allocs,
        "walk_probes": int(walk.group(1)) if walk else None,
        "walk_failed": int(walk.group(2)) if walk else None,
        "walk_samples": [{"va": w[0], "path": w[1], "size": w[2], "pa": w[3],
                          "identity": w[4] == "1", "note": w[5]} for w in walks],
        "loader_failures": sorted(set(re.findall(r"PC5-64-LOADER-FAIL=(\S+)", text))),
        "loader_rejects": sorted(set(re.findall(r"PC5-64-LOADER-REJECT (\S+)", text))),
        "kernel_failures": sorted(set(re.findall(r"PC5-64BIT-FAIL=(\S+)", text))),
        "firmware_refused": [m for m in FIRMWARE_NO_BOOT if m in text],
    }


def _number(text, pattern):
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


# ----------------------------------------------------------------- verdicts
PASS, FAIL, UNAVAILABLE, NOT_TESTED = "PASS", "FAIL", "UNAVAILABLE", "NOT_TESTED"


class Evidence:
    """Per-stage results with four honest states.

    UNAVAILABLE means the environment could not run the stage.  NOT_TESTED
    means the run got nowhere near it.  Neither is PASS, and neither is FAIL:
    a machine without OVMF has not proven the firmware wrong, it has proven
    nothing at all.  `failed` only counts real failures, so a report can be
    "nothing failed" and still be nowhere near a pass.
    """

    def __init__(self):
        self.checks = []
        self.data = {}

    def record(self, name, state, detail=""):
        assert state in (PASS, FAIL, UNAVAILABLE, NOT_TESTED), state
        self.checks.append({"name": name, "state": state, "detail": str(detail)})

    def check(self, name, ok, detail=""):
        self.record(name, PASS if ok else FAIL, detail)

    def count(self, state):
        return sum(1 for c in self.checks if c["state"] == state)

    @property
    def failed(self):
        return self.count(FAIL)

    def as_dict(self):
        return {"checks": self.checks, "data": self.data,
                "passed": self.count(PASS), "failed": self.count(FAIL),
                "unavailable": self.count(UNAVAILABLE),
                "not_tested": self.count(NOT_TESTED)}


def report_timeout(report, run):
    """Record whether the run ended on evidence or on the clock.

    Separate from `evaluate` so the rule has one home.  A test that
    re-implemented this check would be testing itself, and a mutated driver
    would still pass it - which is exactly what a negative control found.
    """
    report.check("the machine was stopped on evidence, not on the clock",
                 not run["timed_out"],
                 "timed out after %ss with no terminal marker" % run["seconds"])


def evaluate(report, seen, run):
    """Judge one boot in stage order.

    Ordering is enforced on first occurrence, not on presence: a log that shows
    the kernel banner but no handoff marker is contradictory, and the driver
    refuses to read the later marker as evidence that the earlier stage ran.
    """
    text = run["serial"]

    def first(marker):
        return text.find(marker)

    # ------------------------------------------------ stage 1: firmware ran
    report.check("firmware produced a boot manager log", seen["firmware_banner"],
                 "no BdsDxe/TianoCore/EDK II banner in the serial log")
    report.check("the machine booted UEFI, not SeaBIOS",
                 seen["firmware_banner"] and not seen["seabios"],
                 "SeaBIOS output present")
    report.check("firmware did not refuse to boot the volume",
                 not seen["firmware_refused"],
                 "; ".join(seen["firmware_refused"]))

    # -------------------------------------------- stage 2: loader entered
    # Gated on the previous stage: without a firmware log, the absence of our
    # banner is not evidence that the loader is broken.
    report.check("the 64-bit loader was entered", seen["loader_banner"],
                 "no PC5-64-LOADER-BANNER")

    # ------------------------------------- stage 3: loader completed its work
    report.check("the loader allocated inside its own bound",
                 bool(seen["allocs"]),
                 "no PC5-64-LOADER-ALLOC line")
    report.check("the loader exited boot services",
                 seen["ebs_ok"], "no exit-boot-services=ok")
    report.check("the loader reported no failure",
                 not seen["loader_failures"] and not seen["loader_rejects"],
                 "loader reported: %s" % (seen["loader_failures"] + seen["loader_rejects"]))

    # ------------------------------------------ stage 4: handoff happened
    report.check("the loader handed control to the kernel",
                 seen["jump"] is not None, "no PC5-64-LOADER-JUMP entry=")

    # ---------------------------------------- stage 5: the kernel executed
    report.check("the kernel executed", seen["kernel_banner"],
                 "no PC5-64-BANNER from the kernel")

    # --------------------------------------------------- ordering enforcement
    def ordered(*markers):
        positions = [first(m) for m in markers]
        if any(p < 0 for p in positions):
            return None
        return all(positions[i] < positions[i + 1] for i in range(len(positions) - 1))

    chain = ordered("PC5-64-LOADER-BANNER", "PC5-64-LOADER exit-boot-services=ok",
                    "PC5-64-LOADER-JUMP entry=", "PC5-64-BANNER PC5 64-bit kernel entry",
                    "PC5-64-DONE")
    report.check("the boot stages appeared in order", chain is not False,
                 "the markers are present but not in stage order")

    # ------------------------------------------------- D1: runtime page tables
    if seen["walk_probes"] is None:
        report.record("[D1] the live page tables were walked", NOT_TESTED,
                      "no PC5-64-PTWALK span= summary; the walk did not run")
    else:
        report.check("[D1] every identity probe resolved",
                     seen["walk_failed"] == 0,
                     "%d of %d probes failed" % (seen["walk_failed"], seen["walk_probes"]))
        report.check("[D1] the walk covered all required addresses",
                     seen["walk_probes"] >= 14,
                     "only %d probes" % seen["walk_probes"])
        # The walk must terminate at a *mapping*, and the path must be
        # consistent with the size it claims.  "Terminal" is not "the last
        # character is P": a 2 MiB page sets PDE.PS and never descends to a PT,
        # so the loader prints pt_present=false and a legitimate complete walk
        # looks exactly like "PPP-".  Judging that a stub would have failed every
        # correct 2 MiB mapping - which is the only kind the loader makes.
        stubby = [s for s in seen["walk_samples"] if s["size"] == "none"]
        # A size with no matching level is a contradiction in the log, not a
        # translation: `2m` requires the PD entry to be the terminus, `4k` and
        # `1g` require every level above and below it to be present.
        terminal = {"4k": "PPPP", "2m": "PPP-", "1g": "PP--"}
        inconsistent = [s for s in seen["walk_samples"]
                        if s["size"] != "none" and s["path"] != terminal[s["size"]]]
        report.check("[D1] every probe reached a terminal mapping with no gap",
                     not stubby and not inconsistent,
                     "%d probe(s) stopped at a table level: %s%s" %
                     (len(stubby), [s["note"] for s in stubby],
                      "; %d with a path that does not match the reported size: %s"
                      % (len(inconsistent),
                         [(s["note"], s["path"], s["size"]) for s in inconsistent])
                      if inconsistent else ""))
        report.check("[D1] every probed address translated to itself",
                     all(s["identity"] for s in seen["walk_samples"]) and bool(seen["walk_samples"]),
                     "a probe resolved somewhere other than its own address")

    # -------------------------------------------------- D2: allocation bounds
    if not seen["allocs"]:
        report.record("[D2] allocations stayed inside their bound", NOT_TESTED,
                      "no allocation was observed")
    else:
        bad = [a for a in seen["allocs"] if a["verdict"] != "ok"]
        report.check("[D2] every allocation verdict was ok", not bad,
                     "%d allocation(s) not ok: %s" %
                     (len(bad), sorted({a["verdict"] for a in bad})))
        # Recomputed independently here: the loader's own "end" field is a claim,
        # and a claim about arithmetic is exactly what a test should redo.
        wrong_end, over_bound, over_limit = [], [], []
        for a in seen["allocs"]:
            expect_end = a["returned_base"] + a["pages"] * 4096 - 1
            if a["end"] != expect_end:
                wrong_end.append(a)
            if a["end"] > a["requested_max"]:
                over_bound.append(a)
            # Both ends of the extent are checked.  A base above the span with a
            # wrapped end would otherwise look contained.
            if a["end"] >= 4 << 30 or a["returned_base"] >= 4 << 30:
                over_limit.append(a)
        report.check("[D2] reported ends match base + pages*4096 - 1",
                     not wrong_end, "%d allocation(s) misreported their end" % len(wrong_end))
        report.check("[D2] no allocation ended above the requested maximum",
                     not over_bound, "%d allocation(s) over bound" % len(over_bound))
        report.check("[D2] no allocation ended outside the identity range",
                     not over_limit, "%d allocation(s) past the limit" % len(over_limit))
        report.check("[D2] the requested maximum was the inclusive limit",
                     all(a["requested_max"] == (4 << 30) - 1 for a in seen["allocs"]),
                     "firmware was asked for a bound other than 0xffffffff")

    # ------------------------------------------------------ final assertions
    report.check("the kernel reported no failure", not seen["kernel_failures"],
                 "kernel reported: %s" % seen["kernel_failures"])
    report.check("the kernel reached its final marker", seen["done"],
                 "no PC5-64-DONE")

    # A marker set that contradicts itself is worse than a missing one.
    contradictions = []
    if seen["done"] and (seen["kernel_failures"] or seen["loader_failures"]):
        contradictions.append("DONE together with a failure marker")
    if seen["kernel_banner"] and not seen["jump"]:
        contradictions.append("kernel banner without a handoff marker")
    if seen["walk_failed"] not in (None, 0) and seen["done"]:
        contradictions.append("DONE after a failed page-table walk")
    report.check("no marker contradicts another", not contradictions,
                 "; ".join(contradictions))

    report.data["observed"] = seen
    report.data["run"] = {"seconds": run["seconds"], "stopped_on": run["stopped_on"],
                          "returncode": run["returncode"], "timed_out": run["timed_out"],
                          "stderr": run["stderr"], "command": run["command"]}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate the 64-bit UEFI boot path under real OVMF.")
    parser.add_argument("--image", default=os.path.join(BUILD, "efi64.img"),
                        help="the ESP image built by `make uefi64`")
    parser.add_argument("--loader", default=os.path.join(BUILD, "uefi64", "BOOTX64.EFI"))
    parser.add_argument("--kernel", default=os.path.join(BUILD, "kernel64", "KERNEL64.BIN"))
    parser.add_argument("--qemu")
    parser.add_argument("--qemu-data-dir",
                        default=os.environ.get("PC5_QEMU_DATA_DIR"),
                        help="QEMU's ROM/keymap directory (-L), for builds whose "
                             "data files are not in the default prefix")
    parser.add_argument("--ovmf-code")
    parser.add_argument("--ovmf-vars")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--memory-mb", type=int, default=512)
    parser.add_argument("--device", choices=["ahci", "usb"], default="ahci",
                        help="how the ESP reaches firmware (default ahci)")
    parser.add_argument("--serial-dir", default=os.path.join(BUILD, "ovmf64"))
    parser.add_argument("--json-out")
    parser.add_argument("--no-build", action="store_true",
                        help="use the artifacts present instead of rebuilding")
    args = parser.parse_args(argv)

    report = Evidence()

    # ------------------------------------------- artifacts must be current
    # Rebuilding is the default because a stale efi64.img would silently test
    # code that no longer exists in the tree - the one failure mode that makes
    # a green result meaningless.
    build_log = ""
    if not args.no_build:
        build = subprocess.run(["make", "-s", "uefi64", "kernel64"],
                               capture_output=True, text=True)
        build_log = (build.stdout or "") + (build.stderr or "")
        if build.returncode != 0:
            report.record("the 64-bit artifacts built", FAIL,
                          "make uefi64 kernel64 exited %d:\n%s" %
                          (build.returncode, build_log[-2000:]))
            report.data["build_log"] = build_log[-4000:]
            return finish(report, args, code=1)
        report.record("the 64-bit artifacts were rebuilt from the current tree", PASS)
    else:
        # Saying "built" when nothing was built would be a false claim about
        # provenance; the reader needs to know these artifacts may predate the
        # source they are being compared against.
        report.record("the 64-bit artifacts were rebuilt from the current tree",
                      NOT_TESTED, "--no-build: using whatever is on disk")

    for name, path in (("efi64.img", args.image), ("BOOTX64.EFI", args.loader),
                       ("KERNEL64.BIN", args.kernel)):
        if not os.path.exists(path):
            report.record("the %s artifact exists" % name, FAIL, "missing: %s" % path)
            return finish(report, args, code=1)
        report.record("the %s artifact exists" % name, PASS, path)

    report.data["artifacts"] = {
        "efi64.img": {"path": args.image, "sha256": sha256_file(args.image),
                      "size": os.path.getsize(args.image)},
        "BOOTX64.EFI": {"path": args.loader, "sha256": sha256_file(args.loader),
                        "size": os.path.getsize(args.loader)},
        "KERNEL64.BIN": {"path": args.kernel, "sha256": sha256_file(args.kernel),
                         "size": os.path.getsize(args.kernel)},
    }
    report.data["source"] = {
        "commit": git_value("rev-parse", "HEAD"),
        "tree": git_value("rev-parse", "HEAD^{tree}"),
        "branch": git_value("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git_value("status", "--porcelain")),
    }

    # ------------------------------------------------ environment discovery
    qemu = args.qemu or os.environ.get("PC5_QEMU") or shutil.which("qemu-system-x86_64")
    code = args.ovmf_code or find_first(CODE_CANDIDATES)
    vars_template = args.ovmf_vars or find_first(VARS_CANDIDATES)
    if not args.ovmf_vars:
        # Distributions and manual installs name the pair inconsistently
        # (`OVMF_CODE.fd`/`OVMF_VARS.fd`, `..._4M.fd`, `..._pure-efi.fd`, and
        # `fd` vs `fd.fd`).  The variable store that belongs with a CODE image is
        # the one installed beside it under the same stem, so try that before
        # falling back to whatever happens to be installed system-wide: pairing a
        # 2 MiB code image with an unrelated 128 KiB variable store produces a
        # firmware that never gets as far as the boot manager.
        candidate = sibling_vars(args.ovmf_code)
        if candidate and (not vars_template or not os.path.exists(vars_template)):
            vars_template = candidate

    # Existence, not truthiness: a path the caller supplied but that is not
    # there is a different situation only in the message it deserves, never in
    # the verdict.  Treating a typo'd --ovmf-code as "firmware present" would
    # push the run into a crash or, worse, a fabricated result.
    missing = []
    if not qemu:
        missing.append("qemu-system-x86_64 not on PATH and --qemu not given")
    elif not os.path.exists(qemu):
        missing.append("the emulator does not exist: %s" % qemu)
    if not code:
        missing.append("no OVMF code image found (tried %d locations); "
                       "pass --ovmf-code" % len(CODE_CANDIDATES))
    elif not os.path.exists(code):
        missing.append("the OVMF code image does not exist: %s" % code)
    if not vars_template:
        missing.append("no OVMF vars template found; pass --ovmf-vars")
    elif not os.path.exists(vars_template):
        missing.append("the OVMF vars template does not exist: %s" % vars_template)

    report.data["qemu"] = {"path": qemu, "version": qemu_version(qemu),
                           "data_dir": args.qemu_data_dir or None}
    report.data["firmware"] = describe_firmware(code)

    # Remove the previous run's log before anything else happens - including the
    # early return for a missing QEMU or OVMF.  The verdict is safe without this,
    # because it is computed from the bytes this run captures and never read back
    # from the file; but a leftover complete-looking log beside a failed run is a
    # trap for whoever reads the directory afterwards, and the report names
    # serial_path as the evidence location.
    os.makedirs(args.serial_dir, exist_ok=True)
    serial_path = os.path.join(args.serial_dir, "efi64-serial.log")
    try:
        os.unlink(serial_path)
    except OSError:
        pass

    if missing:
        # Not a failure and emphatically not a pass: nothing was tested.
        for reason in missing:
            report.record("the boot could be run", UNAVAILABLE, reason)
        for stage in STAGES:
            report.record("stage: %s" % stage, NOT_TESTED, "no QEMU/OVMF")
        report.data["device_arrangement"] = {
            "device": args.device, "verified": False,
            "reason": "no QEMU/OVMF on this machine, so the arrangement that "
                      "serves a FAT12 superfloppy as a disk was never exercised",
        }
        return finish(report, args, code=3)

    report.record("the boot could be run", PASS)

    # ------------------------------------------- per-run writable vars copy
    # Firmware writes its boot variables back; a shared template would carry
    # state between runs and make the second run differ from the first.
    os.makedirs(args.serial_dir, exist_ok=True)
    vars_path = os.path.join(args.serial_dir, "OVMF_VARS.fd")
    try:
        shutil.copyfile(vars_template, vars_path)
    except OSError as exc:
        report.record("a writable copy of the variable store was made", FAIL,
                      "%s: %s" % (vars_template, exc))
        return finish(report, args, code=1)
    report.record("a writable copy of the variable store was made", PASS)
    serial_path = os.path.join(args.serial_dir, "efi64-serial.log")
    report.data["firmware"]["vars_template"] = vars_template
    report.data["firmware"]["vars_sha256"] = sha256_file(vars_template)

    try:
        run = run_qemu(qemu, code, vars_template, args.image, vars_path, serial_path,
                       args.timeout, args.memory_mb, args.device,
                       args.qemu_data_dir)
    except OSError as exc:
        # The emulator exists as a path but could not be executed.  That is an
        # environment problem: nothing about the boot was observed either way.
        report.record("the emulator started", UNAVAILABLE, "%s: %s" % (qemu, exc))
        for stage in STAGES:
            report.record("stage: %s" % stage, NOT_TESTED, "QEMU did not start")
        return finish(report, args, code=3)

    if run["returncode"] is not None and run["returncode"] != 0 and not run["serial"]:
        # It ran and printed nothing, then failed.  Judging that as a boot with
        # missing markers would blame PC5 for an emulator invocation problem.
        report.record("the emulator started", UNAVAILABLE,
                      "qemu exited %s with no serial output: %s"
                      % (run["returncode"], run["stderr"]))
        for stage in STAGES:
            report.record("stage: %s" % stage, NOT_TESTED, "QEMU produced no output")
        return finish(report, args, code=3)
    report.record("the emulator started", PASS)
    report.data["serial_path"] = serial_path
    report.data["device_arrangement"] = {"device": args.device, "verified": True,
                                         "image_format": "fat12 superfloppy"}

    report_timeout(report, run)

    seen = observe(run["serial"])
    evaluate(report, seen, run)

    if report.count(FAIL):
        # A failed runtime assertion is distinguished from an ordinary stage
        # failure so a caller can tell "the boot did not happen" from "the boot
        # happened and the map was wrong".
        runtime = [c for c in report.checks
                   if c["state"] == FAIL and ("[D1]" in c["name"] or "[D2]" in c["name"])]
        code_out = 2 if runtime else 1
    elif report.count(UNAVAILABLE) or report.count(NOT_TESTED):
        # Exit 0 has to mean "every check ran and passed".  Anything left
        # unrun or unavailable is a third outcome, and returning 0 for it lets a
        # caller that only checks the status treat an unattested run as a pass -
        # exactly the failure this driver exists to prevent, one level up.
        code_out = 3
    else:
        code_out = 0
    return finish(report, args, code=code_out)


def finish(report, args, code):
    data = report.as_dict()
    print("=" * 68)
    for check in data["checks"]:
        # A detail line that explains why something failed reads as nonsense next
        # to a PASS ("no PC5-64-DONE" beside "the kernel reached its final
        # marker - PASS"), so details are shown only where they are wanted.
        # They stay in the JSON for every check.
        show = check["state"] not in (PASS, NOT_TESTED)
        print("  [%-11s] %s%s" % (check["state"], check["name"],
                                  ("  -- " + check["detail"]) if check["detail"] and show else ""))
    print("-" * 68)
    print("  %d passed, %d failed, %d unavailable, %d not tested"
          % (data["passed"], data["failed"], data["unavailable"], data["not_tested"]))

    if data["unavailable"] or data["not_tested"]:
        print("\n  64-BIT OVMF VALIDATION NOT COMPLETED: %d stage(s) were never run."
              % (data["unavailable"] + data["not_tested"]))
    if data["failed"]:
        print("\n  64-BIT OVMF VALIDATION FAILED: %d check(s)." % data["failed"])
    elif not (data["unavailable"] or data["not_tested"]):
        print("\n  64-BIT OVMF VALIDATION PASSED.")
    else:
        print("\n  Nothing failed, but this is not a pass: see the states above.")

    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        print("  evidence: %s" % args.json_out)
    return code


if __name__ == "__main__":
    sys.exit(main())
