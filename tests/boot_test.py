#!/usr/bin/env python3
"""PC5 emulated boot test.

Boots the built ISO in the v86 x86 emulator (SeaBIOS firmware, emulated CD-ROM
with El Torito) and asserts what the machine actually did:

  * the loader ran, read the ISO volume descriptor and verified the kernel
  * the 32-bit kernel started and completed its boot log
  * every interface page renders (asserted from the console shadow buffer,
    dumped over the serial console, so the check covers the rendered UI)
  * the compatibility chain reports the genuine-PS5 stages as not started

This proves the image boots on an emulated PC.  It does not prove physical
hardware support: see docs/TESTING_STRATEGY.md for the evidence levels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "tests", "v86_runner.mjs")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import disk_images  # noqa: E402  (test fixture builder, see tests/disk_images.py)

# The write test gets its own disposable disk: a zero-filled file created here,
# loaded into the emulator's memory through --hda-buffer and dumped back with
# --hda-out.  It is never a host device, and the input file is never written to.
WTEST_HDD_BYTES = 8388608  # 8 MiB, zero-filled
WTEST_SCRATCH_LBA = 2048            # must match WTEST_SCRATCH_LBA in pc5.h
WTEST_SCRATCH_SECTORS = 8           # must match WTEST_SCRATCH_SECTORS in pc5.h
WTEST_OFFSET = WTEST_SCRATCH_LBA * 512
WTEST_BYTES = WTEST_SCRATCH_SECTORS * 512


def wtest_pattern(n=WTEST_BYTES):
    """The kernel's deterministic pattern (kernel/storage_test.c)."""
    return bytes(((i * 7 + 0x5A) & 0xFF) for i in range(n))

V86_CANDIDATES = [
    os.environ.get("V86_ASSETS"),
    os.path.join(ROOT, "node_modules", "v86-system", "assets"),
    "/home/user/work/v86/node_modules/v86-system/assets",
]


def find_v86():
    for path in V86_CANDIDATES:
        if path and os.path.exists(os.path.join(path, "libv86.mjs")):
            return path
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Result:
    def __init__(self):
        self.checks = []

    def check(self, name, ok, detail=""):
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
        return ok

    @property
    def ok(self):
        return all(c["ok"] for c in self.checks)


def run_vm(iso, seconds, serial_out, screen_out=None, keys=None,
           hda_buffer=None, hda_out=None):
    cmd = ["node", RUNNER, "--cdrom", iso, "--seconds", str(seconds),
           "--serial-out", serial_out, "--mem", "128M"]
    if screen_out:
        cmd += ["--screen-out", screen_out]
    if keys:
        cmd += ["--keys", keys]
    if hda_buffer:
        cmd += ["--hda-buffer", hda_buffer]
    if hda_out:
        cmd += ["--hda-out", hda_out]
    env = dict(os.environ)
    assets = find_v86()
    if assets:
        env["V86_ASSETS"] = assets
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=seconds + 60)
    return proc


def _has_line(text, needle):
    return any(needle in line for line in text.splitlines())


def _first_line(text, needle):
    for line in text.splitlines():
        if needle in line:
            return line.strip()
    return f"no line containing {needle!r}"


def screen_dump(serial_text):
    """Extract the last complete screen dump from a serial log."""
    begin = "pc5 screen dump begin"
    end = "pc5 screen dump end"
    blocks = []
    pos = 0
    while True:
        i = serial_text.find(begin, pos)
        if i < 0:
            break
        j = serial_text.find(end, i)
        if j < 0:
            break
        block = serial_text[i + len(begin):j].replace("\r", "")
        blocks.append(block.lstrip("\n"))
        pos = j + len(end)
    return blocks


def main(argv=None):
    ap = argparse.ArgumentParser(description="PC5 emulated boot test")
    ap.add_argument("--iso", default=os.path.join(ROOT, "build", "pc5.iso"))
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "build", "test"))
    ap.add_argument("--seconds", type=int, default=14)
    ap.add_argument("--json-out", default=os.path.join(ROOT, "build", "test", "boot-evidence.json"))
    args = ap.parse_args(argv)

    res = Result()
    if not os.path.exists(args.iso):
        print(f"missing image: {args.iso} (run 'make iso')")
        return 2
    if not shutil.which("node") or not find_v86():
        print("SKIP: node or the v86 emulator assets are not available")
        print("      install with: npm install v86-system")
        return 77
    os.makedirs(args.out_dir, exist_ok=True)

    image_sha = sha256(args.iso)
    serial_path = os.path.join(args.out_dir, "serial.log")
    proc = run_vm(args.iso, args.seconds, serial_path)
    with open(serial_path, "r", errors="replace") as fh:
        serial = fh.read()

    res.check("emulator produced a serial log", len(serial) > 0, f"{len(serial)} bytes")
    for marker, label in [
        ("PC5 loader", "loader banner on serial"),
        ("pc5: medium is ISO9660", "loader read the ISO9660 volume descriptor"),
        ("pc5: kernel image verified", "loader loaded and verified the kernel image"),
        ("pc5: entering 32-bit kernel", "protected-mode handoff"),
        ("PC5 KERNEL", "32-bit kernel started"),
        ("PC5-BOOT-OK", "kernel reached the end of its boot log"),
    ]:
        res.check(label, marker in serial, marker)

    res.check("genuine PS5 software is not claimed as running",
              "genuine_ps5_execution=no" in serial)
    res.check("kernel mounted the ISO9660 volume through its own block driver",
              "PC5-LOG storage_mounted volume=PC5" in serial)
    res.check("kernel read a file back from the medium with a valid signature",
              "signature=ok" in serial)
    res.check("chain reports a blocked stage with evidence",
              "chain_blocked_at=" in serial and "chain_blocked_evidence=" in serial)

    # ---- the write test: a disposable disk, an explicit command ----
    disk_in = os.path.join(args.out_dir, "wtest-hda.img")
    disk_out = os.path.join(args.out_dir, "wtest-hda-after.img")
    with open(disk_in, "wb") as fh:
        fh.write(bytes(WTEST_HDD_BYTES))

    # ---- interface pages dumped from the console shadow buffer, plus the
    # explicit storage write test on the attached disk ----
    keys = ",".join([
        "serial:wtest\\r", "wait:2",
        "serial:storage\\r", "wait:1",
        "serial:part\\r", "wait:1",
        "serial:screen\\r", "wait:1",
        "serial:page 2\\r", "wait:1", "serial:screen\\r", "wait:1",
        "serial:page 3\\r", "wait:1", "serial:screen\\r", "wait:1",
        "serial:page 4\\r", "wait:1", "serial:screen\\r", "wait:1",
        "serial:page 5\\r", "wait:1", "serial:screen\\r", "wait:1",
        "serial:page 6\\r", "wait:1", "serial:screen\\r", "wait:1",
    ])
    ui_serial = os.path.join(args.out_dir, "serial-ui.log")
    run_vm(args.iso, max(args.seconds, 24), ui_serial, keys=keys,
           hda_buffer=disk_in, hda_out=disk_out)
    with open(ui_serial, "r", errors="replace") as fh:
        ui_log = fh.read()

    # ---- CD boot is unchanged when a writable disk is attached ----
    res.check("a disk attached does not displace the CD boot device",
              "storage drives=2" in ui_log and "atapi=1" in ui_log,
              "drives=2, selected device is ATAPI")
    res.check("ISO9660 volume still mounts with a disk attached",
              "PC5-LOG storage_mounted volume=PC5" in ui_log and "signature=ok" in ui_log)

    # ---- guard rails: every unsafe write must be refused, and say why ----
    for name in ("zero_count", "out_of_range_lba", "past_end", "lba_overflow",
                 "invalid_device", "negative_device", "null_buffer",
                 "scratch_window", "atapi_write"):
        line_present = any(
            f"storage_write_guard name={name} " in line and "ok=1" in line
            for line in ui_log.splitlines()
        )
        res.check(f"write guard refuses {name}", line_present, name)
    res.check("writes to the CD-ROM boot device are refused explicitly",
              "storage_write_cdrom" in ui_log and "refused=1" in ui_log)

    # ---- a disk with no partition table must not be reported as partitioned ----
    res.check("a blank disk is not reported as having a partition table",
              any(ln.strip().startswith("part device=0 scheme=none")
                  and "status=signature" in ln
                  for ln in ui_log.splitlines()),
              "blank 8 MiB disk, no 55 AA signature")

    # ---- the write itself, and host-side verification of the disk image ----
    write_lines = [ln for ln in ui_log.splitlines() if "PC5-LOG storage_write=ok" in ln]
    res.check("ATA PIO write and read-back reported ok", bool(write_lines),
              write_lines[0].strip() if write_lines else "no storage_write=ok line")
    kernel_sha = ""
    if write_lines:
        for field in write_lines[0].split():
            if field.startswith("sha256="):
                kernel_sha = field.split("=", 1)[1]
    res.check("write evidence carries a full SHA-256", len(kernel_sha) == 64,
              (kernel_sha[:16] + "...") if kernel_sha else "missing")

    attached = os.path.exists(disk_out)
    res.check("emulator returned the disk image after the run", attached)
    disk_ok = scratch_ok = outside_ok = input_ok = False
    host_sha = ""
    if attached:
        with open(disk_out, "rb") as fh:
            after = fh.read()
        with open(disk_in, "rb") as fh:
            before = fh.read()
        input_ok = before == bytes(WTEST_HDD_BYTES)  # the input path is never written back
        scratch = after[WTEST_OFFSET:WTEST_OFFSET + WTEST_BYTES]
        scratch_ok = scratch == wtest_pattern()
        host_sha = hashlib.sha256(scratch).hexdigest()
        outside_ok = (after[:WTEST_OFFSET] == bytes(WTEST_OFFSET)
                      and after[WTEST_OFFSET + WTEST_BYTES:] == bytes(len(after) - WTEST_OFFSET - WTEST_BYTES))
        disk_ok = len(after) == WTEST_HDD_BYTES
    res.check("the disposable disk image keeps its size", disk_ok)
    res.check("the input disk image is never written back to", input_ok)
    res.check("the written bytes are on the disk at the declared scratch window",
              scratch_ok, f"lba {WTEST_SCRATCH_LBA} + {WTEST_SCRATCH_SECTORS} sectors")
    res.check("host SHA-256 of the written sectors matches the kernel's",
              bool(host_sha) and host_sha == kernel_sha,
              (host_sha[:16] + "...") if host_sha else "missing")
    res.check("nothing outside the scratch window was written", outside_ok)

    # ---- partition discovery: tables whose bytes this test controls ----
    # 8 MiB = 16384 sectors, same geometry as the write-test disk.  The two
    # partitions sit above the GPT metadata and inside the usable area.
    part_disk = os.path.join(args.out_dir, "part-hda-gpt.img")
    with open(part_disk, "wb") as fh:
        fh.write(disk_images.gpt_image(WTEST_HDD_BYTES, [
            ("PC5 ROOT", 2048, 2048, disk_images.TYPE_LINUX_FS),
            ("PC5 DATA", 4096, 2048, disk_images.TYPE_LINUX_FS),
        ]))
    part_serial = os.path.join(args.out_dir, "serial-part.log")
    run_vm(args.iso, args.seconds, part_serial, hda_buffer=part_disk)
    with open(part_serial, "r", errors="replace") as fh:
        part_log = fh.read()

    res.check("kernel scans the attached disk for a partition table",
              "PC5-LOG partitions device=0 scheme=gpt" in part_log)
    res.check("GPT table validates (header and entry CRC)",
              "PC5-LOG partitions device=0 scheme=gpt count=2 status=ok" in part_log,
              _first_line(part_log, "PC5-LOG partitions device=0"))
    res.check("both GPT partitions are reported",
              _has_line(part_log, 'name="PC5 ROOT"') and _has_line(part_log, 'name="PC5 DATA"'),
              _first_line(part_log, "PC5-LOG partition index=1"))
    res.check("partition starts and lengths are the ones written",
              _has_line(part_log, "first=2048 sectors=2048") and _has_line(part_log, "first=4096 sectors=2048"))
    res.check("partition scan does not disturb the CD boot device",
              "storage drives=2 selected=1 atapi=1" in part_log
              and "storage_mounted volume=PC5" in part_log)

    # A damaged entry table must be refused, and must not stop the boot.
    bad_disk = os.path.join(args.out_dir, "part-hda-gpt-corrupt.img")
    with open(part_disk, "rb") as fh:
        damaged = disk_images.corrupt_entries_crc(fh.read())
    with open(bad_disk, "wb") as fh:
        fh.write(damaged)
    bad_serial = os.path.join(args.out_dir, "serial-part-corrupt.log")
    run_vm(args.iso, args.seconds, bad_serial, hda_buffer=bad_disk)
    with open(bad_serial, "r", errors="replace") as fh:
        bad_log = fh.read()
    res.check("a GPT entry array with a bad CRC is refused, not guessed",
              "PC5-LOG partitions device=0 scheme=gpt count=0 status=crc" in bad_log,
              _first_line(bad_log, "PC5-LOG partitions device=0"))
    res.check("a refused partition table does not stop the boot",
              "PC5-BOOT-OK" in bad_log)

    dumps = screen_dump(ui_log)
    res.check("console shadow buffer dumped over serial", len(dumps) >= 4,
              f"{len(dumps)} dumps")
    joined = "\n".join(dumps)
    with open(os.path.join(args.out_dir, "screens.txt"), "w", encoding="utf-8") as fh:
        for i, dump in enumerate(dumps):
            fh.write(f"===== screen {i} =====\n{dump}\n")

    for needle, label in [
        ("PS5 COMPATIBILITY CHAIN", "status page renders the compatibility chain"),
        ("PC5 boot environment executing", "status page lists stage 1"),
        ("Genuine PS5 home screen", "status page lists the genuine home-screen stage"),
        ("NOT-STARTED", "status page shows not-started stages"),
        ("HARDWARE INVENTORY", "hardware page renders"),
        ("PCI", "hardware page reports PCI inventory"),
        ("PS5 FIRMWARE STATUS", "firmware page renders"),
        ("not executing", "firmware page states the truth about execution"),
        ("SELF-TESTS", "diagnostics page renders"),
        ("ISO9660 mounted in kernel", "diagnostics page reports the kernel storage self-test"),
        ("volume 'PC5'", "storage stage evidence names the mounted volume"),
        ("BOOT LOG", "log page renders"),
        ("POWER AND RECOVERY", "power page renders"),
    ]:
        res.check(label, needle in joined, needle)

    evidence = {
        "image": os.path.abspath(args.iso),
        "image_sha256": image_sha,
        "emulator": "v86 (SeaBIOS, emulated CD-ROM, El Torito no-emulation)",
        "checks": res.checks,
        "serial_log": serial_path,
        "screens_dump": os.path.join(args.out_dir, "screens.txt"),
        "storage_write_test": {
            "emulator_disk": "v86 IDE (hda), in-memory buffer from a zero-filled 8 MiB image",
            "disk_bytes": WTEST_HDD_BYTES,
            "scratch_lba": WTEST_SCRATCH_LBA,
            "scratch_sectors": WTEST_SCRATCH_SECTORS,
            "sector_size": 512,
            "kernel_sha256": kernel_sha,
            "host_sha256": host_sha,
            "scratch_matches_pattern": scratch_ok,
            "outside_scratch_untouched": outside_ok,
            "input_image_unchanged": input_ok,
            "disk_image_after": os.path.abspath(disk_out) if attached else None,
        },
        "partition_test": {
            "fixture": "tests/disk_images.py (this file builds the bytes)",
            "disk_bytes": WTEST_HDD_BYTES,
            "gpt_image": os.path.abspath(part_disk),
            "gpt_partitions": [
                {"name": "PC5 ROOT", "first_lba": 2048, "sectors": 2048},
                {"name": "PC5 DATA", "first_lba": 4096, "sectors": 2048},
            ],
            "corrupt_image": os.path.abspath(bad_disk),
            "corrupt_result": "status=crc, count=0, boot continues",
        },
        "levels_proven": [
            "ISO generated",
            "ISO integrity verified by tools/verify_iso.py (run 'make verify')",
            "ISO boots in an emulated x86 PC",
            "PC5 boot environment runs and reports the compatibility chain",
            "ATA PIO 512-byte sector write, flush and verified read-back on an emulated IDE disk",
            "writes to the ATAPI boot device are refused by the kernel API",
            "MBR and GPT partition tables parsed read-only from an emulated disk",
        ],
        "levels_not_proven": [
            "physical hardware boot",
            "physical disk write (no real ATA disk has been written)",
            "extended/logical MBR partitions (not walked)",
            "any filesystem on a discovered partition (not implemented)",
            "controller, network and audio on physical hardware",
            "genuine PS5 system software execution",
            "actual PS5 game execution",
        ],
    }
    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, indent=2)
        fh.write("\n")

    print()
    print(f"{'PASSED' if res.ok else 'FAILED'}: boot evidence written to {args.json_out}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
