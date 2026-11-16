"""Run a gate and write down what actually happened.

The README used to quote check counts typed by hand. A number in a landing page is only
worth something if it comes with the command that produced it, the exit code, the commit
and the date — so this tool runs the gate, records those four things, and refuses to
write a record when it cannot tell what happened.

    python3 tools/validation.py run check verify
    python3 tools/validation.py run --all
    python3 tools/validation.py list

Records land in `docs/evidence/validation/<gate>.json` and are committed, which makes
them reviewable evidence rather than a claim in a pull-request comment. They are
deliberately **snapshots**: a record names the commit it ran at, and the presentation
layer will not present an older record as a result for the current commit.

Result vocabulary, and why it is not a boolean:

  passed       the command exited 0
  failed       the command exited non-zero for a reason other than "cannot test"
  not-tested   the command reported that its dependencies are absent (OVMF without QEMU)
  skipped      the harness declined to run (no node, no unicorn)
  error        the command could not be started at all

Anything that is not `passed` stays visible. Nothing here can turn a missing dependency
into a success, because the exit code is recorded next to the verdict.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "docs/evidence/validation"
SCHEMA = "pc5.validation/1"
PINNED_EPOCH = "1763251200"

#: gate -> (argv, evidence level, how to recognise "could not test", notes)
GATES = {
    "check": (["make", "check"], "host tests",
              "no skip", "unit tests, platform contract, docs and generated files"),
    "verify": (["make", "verify"], "ISO structure",
               "no skip", "structural verification and the independent manifest"),
    "boot-test": (["make", "boot-test"], "emulator execution",
                  "SKIP", "PC5's own boot path under the v86 emulator"),
    "stubtest": (["make", "stubtest"], "stub-firmware execution",
                 "unicorn", "the real BOOTX64.EFI against a stub firmware"),
    "kernel64": (["make", "kernel64"], "host build of the 64-bit kernel",
                 "gcc", "the flat long-mode kernel image"),
    "uefi64": (["make", "uefi64"], "host build of the 64-bit loader",
               "gcc", "the PE32+ long-mode loader and its ESP image"),
    "stubtest64": (["make", "stubtest64"], "stub-firmware execution (64-bit)",
                   "unicorn", "the 64-bit loader and kernel in long-mode emulation"),
    "ovmf-test": (["make", "ovmf-test"], "real firmware execution",
                  "NOT TESTED", "genuine TianoCore OVMF, if this machine has it"),
    "roadmap": (["make", "roadmap"], "governance", "no skip",
                "milestone graph, release isolation, destination reachability"),
    "docs-check": (["make", "docs-check"], "documentation", "no skip",
                   "links, anchors and documented commands across the tree"),
    "presentation-check": (["make", "presentation-check"], "documentation", "no skip",
                           "generated figures and README blocks are current"),
}

SUMMARY_PATTERNS = (
    re.compile(r"^Ran (\d+) tests"),
    re.compile(r"^OK"),
    re.compile(r"^FAILED"),
    re.compile(r"^(VALID|INVALID|MANIFEST VALID)"),
    re.compile(r"^PASSED[: ].*"),
    re.compile(r"^SKIP[: ].*"),
    re.compile(r"^NOT TESTED[: ].*"),
    re.compile(r"^(DOCS OK|PRESENTATION OK)[: ].*"),
    re.compile(r"^(KERNEL64|UEFI64) OK[: ].*"),
    re.compile(r"^64-BIT STUB FIRMWARE (OK|FAILED)[: ].*"),
    re.compile(r"^roadmap: (OK|FAILED).*"),
)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def summarise(output: str) -> str:
    """The line that best says what happened — the test verdict, not the first match."""
    best, best_rank = "", 0
    for line in output.splitlines():
        stripped = line.strip()
        for index, pattern in enumerate(SUMMARY_PATTERNS):
            if not pattern.match(stripped):
                continue
            rank = 3 if stripped.startswith("Ran ") else (
                2 if stripped.startswith(("OK", "FAILED")) else 1)
            if rank >= best_rank:
                best, best_rank = stripped[:200], rank
            break
        del index
    return best


def counts(output: str) -> dict | None:
    """Pick the check/test totals a gate prints, when it prints them."""
    ran = re.search(r"Ran (\d+) tests", output)
    passed = re.search(r"PASSED: (\d+)/(\d+) checks", output)
    if ran:
        # the verdict is the line after the run line, not any "OK" earlier in the log
        tail = output[ran.end():]
        verdict = re.search(r"^(OK|FAILED)(?: \(skipped=(\d+)\))?", tail, re.M)
        skipped = int(verdict.group(2)) if verdict and verdict.group(2) else 0
        total = int(ran.group(1))
        return {"passed": total - skipped if verdict and verdict.group(1) == "OK" else 0,
                "total": total, "skipped": skipped}
    if passed:
        return {"passed": int(passed.group(1)), "total": int(passed.group(2)), "skipped": 0}
    return None


def classify(gate: str, code: int, output: str) -> str:
    if code == 0:
        return "passed"
    _, _level, cannot_test, _notes = GATES[gate]
    if gate == "boot-test" and (code == 77 or "SKIP" in output):
        return "skipped"
    if gate == "ovmf-test" and ("NOT TESTED" in output or code == 3):
        return "not-tested"
    if gate == "stubtest" and ("unicorn" in output or code == 2):
        return "skipped"
    if cannot_test != "no skip" and cannot_test in output:
        return "not-tested"
    return "failed"


def iso_sha() -> str:
    report = ROOT / "build/verify-report.json"
    try:
        return json.loads(report.read_text())["sha256"]
    except (OSError, KeyError, json.JSONDecodeError):
        return ""


def run(gate: str, write: bool = True) -> dict:
    if gate not in GATES:
        raise SystemExit(f"unknown gate {gate!r}; known: {', '.join(sorted(GATES))}")
    argv, level, _cannot, notes = GATES[gate]
    env = dict(os.environ, SOURCE_DATE_EPOCH=os.environ.get("SOURCE_DATE_EPOCH", PINNED_EPOCH))
    try:
        completed = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, env=env)
    except OSError as exc:
        record = {"schema": SCHEMA, "gate": gate, "command": " ".join(argv),
                  "exit_code": None, "result": "error", "detail": str(exc),
                  "commit": git("rev-parse", "HEAD"),
                  "date": datetime.date.today().isoformat()}
    else:
        output = (completed.stdout or "") + (completed.stderr or "")
        record = {
            "schema": SCHEMA,
            "gate": gate,
            "command": " ".join(argv),
            "level": level,
            "exit_code": completed.returncode,
            "result": classify(gate, completed.returncode, output),
            "summary": summarise(output),
            "commit": git("rev-parse", "HEAD"),
            # a record is not dirty because it wrote itself
            "dirty": bool(git("status", "--porcelain", "--", ".",
                              ":(exclude)docs/evidence/validation")),
            "date": datetime.date.today().isoformat(),
            "source_date_epoch": int(env["SOURCE_DATE_EPOCH"]),
            "notes": notes,
        }
        measured = counts(output)
        if measured:
            record["checks"] = measured
        if gate == "verify":
            digest = iso_sha()
            if digest:
                record["iso_sha256"] = digest
    record["tool"] = "tools/validation.py"
    if write:
        RECORDS.mkdir(parents=True, exist_ok=True)
        path = RECORDS / f"{gate}.json"
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record["file"] = str(path.relative_to(ROOT))
    return record


def records() -> list:
    found = []
    if not RECORDS.exists():
        return found
    for path in sorted(RECORDS.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        record["file"] = str(path.relative_to(ROOT))
        found.append(record)
    return found


def problems(entries: list | None = None) -> list:
    """A record has to be readable, dated, committed and honest about its verdict."""
    found = []
    for record in entries if entries is not None else records():
        name = record.get("file", "?")
        if record.get("schema") != SCHEMA:
            found.append(f"{name}: schema is {record.get('schema')!r}, expected {SCHEMA!r}")
        if record.get("gate") not in GATES:
            found.append(f"{name}: unknown gate {record.get('gate')!r}")
        if record.get("result") not in ("passed", "failed", "not-tested", "skipped", "error"):
            found.append(f"{name}: unknown result {record.get('result')!r}")
        if not re.fullmatch(r"[0-9a-f]{40}", str(record.get("commit", ""))):
            found.append(f"{name}: commit is not a full SHA-1")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(record.get("date", ""))):
            found.append(f"{name}: date is not YYYY-MM-DD")
        if record.get("result") == "passed" and record.get("exit_code") != 0:
            found.append(f"{name}: claims passed with exit code {record.get('exit_code')!r}")
        if record.get("result") in ("failed", "not-tested", "skipped", "error") \
                and record.get("exit_code") == 0:
            found.append(f"{name}: claims {record['result']} with exit code 0")
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a gate and record what happened.")
    parser.add_argument("command", choices=("run", "list", "verify-records"))
    parser.add_argument("gates", nargs="*")
    parser.add_argument("--all", action="store_true", help="run every gate")
    parser.add_argument("--dry-run", action="store_true", help="run without writing records")
    args = parser.parse_args(argv)

    if args.command == "list":
        for record in records():
            checks = record.get("checks") or {}
            detail = f"{checks.get('passed', '?')}/{checks.get('total', '?')}" if checks else "-"
            print(f"{record['gate']:20} {record['result']:11} {detail:9} "
                  f"{record['commit'][:7]} {record['date']}")
        return 0

    if args.command == "verify-records":
        found = problems()
        for problem in found:
            print(f"[FAIL] {problem}")
        print("records: " + ("OK" if not found else f"{len(found)} problem(s)"))
        return 1 if found else 0

    gates = list(GATES) if args.all else args.gates
    if not gates:
        parser.error("name at least one gate, or pass --all")
    worst = 0
    for gate in gates:
        record = run(gate, write=not args.dry_run)
        checks = record.get("checks") or {}
        detail = f" {checks.get('passed')}/{checks.get('total')}" if checks else ""
        print(f"{gate:20} {record['result']:11} exit={record['exit_code']}{detail} "
              f"commit={record['commit'][:7]} {record.get('summary', '')}")
        if record["result"] == "failed" or record["result"] == "error":
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main())
