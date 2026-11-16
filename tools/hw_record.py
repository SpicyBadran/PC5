"""Validate a physical-boot record before it is committed as M1 evidence.

The point of this tool is to make a half-filled or self-contradictory record fail.
A record that says "booted" without a serial log, a record that leaves a field as
`TBD`, a record that reports an emulator run as if it were a PC boot, or a record
whose SHA-256 fields are not SHA-256 fields — all of those are rejected here rather
than discovered months later in a document nobody re-read.

It does not judge whether the boot happened: only the log and the machine can do
that. It judges whether the record is complete enough to be worth committing.

    python3 tools/hw_record.py docs/evidence/2026-11-20-example-x570.json
    python3 tools/hw_record.py --check-template docs/evidence/M1_RECORD_TEMPLATE.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LEVELS = ("structure", "emulator", "ovmf", "physical-uefi", "physical-bios")
PHYSICAL = ("physical-uefi", "physical-bios")
OUTCOMES = ("booted", "partial", "failed")
BOOT_MODES = ("uefi", "bios")
SECURE_BOOT = ("enabled", "disabled", "unknown")
CSM = ("enabled", "disabled", "absent", "unknown")
CAPTURE = ("none", "file", "piped")
MEDIA = ("usb", "sd", "optical")

SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UNFILLED = ("", "tbd", "todo", "unknown-machine", "n/a")


def _fill(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in UNFILLED
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, list):
        return len(value) > 0
    return True


def problems(record: dict, base: Path = ROOT) -> list[str]:
    found: list[str] = []

    def require(path: str, value) -> None:
        if not _fill(value):
            found.append(f"{path}: not filled in")

    for field in ("date", "operator", "level", "outcome"):
        require(field, record.get(field))

    if not DATE.match(str(record.get("date", ""))):
        found.append(f"date: {record.get('date')!r} is not YYYY-MM-DD")
    if record.get("level") not in LEVELS:
        found.append(f"level: {record.get('level')!r} is not one of {LEVELS}")
    if record.get("outcome") not in OUTCOMES:
        found.append(f"outcome: {record.get('outcome')!r} is not one of {OUTCOMES}")

    machine = record.get("machine") or {}
    for field in ("vendor", "model", "cpu", "board", "chipset", "gpu", "display",
                  "storage"):
        require(f"machine.{field}", machine.get(field))
    require("machine.ram_gb", machine.get("ram_gb"))

    firmware = record.get("firmware") or {}
    for field in ("vendor", "version", "date"):
        require(f"firmware.{field}", firmware.get(field))
    if firmware.get("secure_boot") not in SECURE_BOOT:
        found.append(f"firmware.secure_boot: {firmware.get('secure_boot')!r} is not one of "
                     f"{SECURE_BOOT}")
    if firmware.get("boot_mode") not in BOOT_MODES:
        found.append(f"firmware.boot_mode: {firmware.get('boot_mode')!r} is not one of "
                     f"{BOOT_MODES}")
    if firmware.get("csm") not in CSM:
        found.append(f"firmware.csm: {firmware.get('csm')!r} is not one of {CSM}")

    medium = record.get("medium") or {}
    if medium.get("kind") not in MEDIA:
        found.append(f"medium.kind: {medium.get('kind')!r} is not one of {MEDIA}")
    require("medium.capacity_gb", medium.get("capacity_gb"))

    image = record.get("image") or {}
    for field in ("commit", "sha256", "manifest_sha256", "medium_sha256"):
        require(f"image.{field}", image.get(field))
    for field in ("sha256", "manifest_sha256", "medium_sha256"):
        value = str(image.get(field, ""))
        if value and not (SHA256.match(value) or value.lower() in UNFILLED):
            found.append(f"image.{field}: {value!r} is not a lowercase SHA-256")
    require("image.source_date_epoch", image.get("source_date_epoch"))
    if str(image.get("medium_sha256", "")).lower() not in UNFILLED and \
            str(image.get("sha256", "")).lower() not in UNFILLED and \
            image.get("medium_sha256") != image.get("sha256"):
        found.append("image.medium_sha256 differs from image.sha256: the medium does not "
                     "hold the image that was verified")

    serial = record.get("serial") or {}
    if serial.get("capture") not in CAPTURE:
        found.append(f"serial.capture: {serial.get('capture')!r} is not one of {CAPTURE}")
    if serial.get("capture") in ("file", "piped"):
        log = str(serial.get("log", ""))
        if not log:
            found.append("serial.log: capture is recorded but no log path is given")
        else:
            candidate = (base / log) if not Path(log).is_absolute() else Path(log)
            if not candidate.exists():
                found.append(f"serial.log: {log!r} does not exist")
        require("serial.final_line", serial.get("final_line"))
    if serial.get("capture") == "none" and record.get("outcome") == "booted":
        found.append("serial.capture: a boot cannot be recorded as 'booted' without a log")
    if serial.get("baud") != 115200:
        found.append(f"serial.baud: {serial.get('baud')!r}; PC5 prints at 115200 8N1")

    level = record.get("level")
    if level in ("emulator", "ovmf"):
        found.append(f"level: {level!r} is a host gate, not a physical boot; those have "
                     f"their own evidence in docs/TEST_RESULTS.md")
    if level == "structure":
        found.append("level: 'structure' is proven by `make verify`, not by a machine record")
    if level in PHYSICAL and record.get("outcome") == "booted" and \
            str(serial.get("capture")) == "none":
        found.append("level: a physical boot cannot be claimed without a serial capture")

    if record.get("outcome") == "booted" and record.get("blockers"):
        found.append("blockers: a record cannot be 'booted' while listing blockers; use "
                     "'partial' and say what is missing")

    if not isinstance(record.get("observations"), list) or not record.get("observations"):
        found.append("observations: at least one observation is required (what the screen "
                     "and the log showed)")
    return found


def template_problems(path: Path) -> list[str]:
    """The template must fail validation loudly: it is not a filled record."""
    record = json.loads(path.read_text())
    found = problems(record, base=path.parent)
    if not found:
        return [f"{path}: the template validates as a real record, which means the "
                f"validator is not checking the fields it claims to check"]
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate an M1 physical-boot record.")
    ap.add_argument("record", nargs="?")
    ap.add_argument("--check-template", metavar="PATH")
    args = ap.parse_args(argv)

    if args.check_template:
        found = template_problems(Path(args.check_template))
        for problem in found:
            print(f"PROBLEM: {problem}")
        print("template: " + ("OK (rejected as expected)" if not found else "problem"))
        return 1 if found else 0

    if not args.record:
        ap.error("give a record path, or --check-template PATH")
    path = Path(args.record)
    if not path.exists():
        print(f"hw-record: {path}: no such file", file=sys.stderr)
        return 2
    try:
        record = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"hw-record: {path}: not JSON ({exc})", file=sys.stderr)
        return 2
    found = problems(record, base=path.parent)
    for problem in found:
        print(f"[FAIL] {problem}")
    if found:
        print(f"{len(found)} problem(s): this record is not evidence yet")
        return 1
    print(f"RECORD VALID: {path} ({record['level']}, {record['outcome']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
