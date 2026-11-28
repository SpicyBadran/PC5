#!/usr/bin/env python3
"""Turn one `dotnet test` run into PC5's app-dimension evidence record.

The transformation's rule is that a subsystem's health is *derived*, never asserted: the
legacy pipeline already files `docs/evidence/ci/<run>.json` from the Actions API, and this
is the equivalent for the Windows application. It reads the TRX the test host wrote and
prints a record. It has no argument that can say "passed" on its own, and it refuses a
`passed` verdict over zero executed tests, which is what a green no-op looks like.

    python3 tools/app_evidence.py --trx build/app-tests.trx --out docs/evidence/app/1.json \
        --subsystem app --commit "$GITHUB_SHA" --run "$GITHUB_RUN_ID"

Statuses are exactly the vocabulary the roadmap is allowed to show: passed, failed, blocked,
not_run, unknown. `not_run` never becomes `failed`, and `unknown` never becomes `passed`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "pc5.app-evidence/1"
OUTCOMES = ("Passed", "Failed", "NotExecuted", "Timeout", "Aborted", "Inconclusive",
            "Pre-init failure", "Post-init failure")
STATUSES = ("passed", "failed", "blocked", "not_run", "unknown")

#: The most a CI job that only builds and unit-tests a portable library may be read as.
#: Genuine guest execution is not reachable from here, and this constant is the proof
#: that the tool cannot be talked into claiming it.
MAXIMUM_CLAIM = "application-builds (E1); no guest of any kind ran here"

TRX_NS = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"


def parse_trx(path: Path) -> tuple[dict, str | None]:
    """`({"executed","passed","failed","skipped"}, error)`, either half allowed to be empty."""
    if not path.exists():
        return {"executed": 0, "passed": 0, "failed": 0, "skipped": 0}, (
            f"no TRX at {path}: the test step produced no result file")
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return {"executed": 0, "passed": 0, "failed": 0, "skipped": 0}, f"unreadable TRX: {exc}"

    counts = {"Passed": 0, "Failed": 0, "NotExecuted": 0, "other": 0}
    for result in root.iter(f"{TRX_NS}UnitTestResult"):
        outcome = (result.get("outcome") or "").strip()
        if outcome in counts:
            counts[outcome] += 1
        else:
            counts["other"] += 1

    tests = {
        "executed": sum(counts.values()),
        "passed": counts["Passed"],
        "failed": counts["Failed"],
        "skipped": counts["NotExecuted"] + counts["other"],
    }
    return tests, None


def decide(tests: dict, error: str | None, forced: str | None) -> tuple[str, str]:
    """The verdict, with the sentence that explains it. Never inferred from optimism."""
    if forced:
        if forced not in STATUSES:
            raise SystemExit(f"--status must be one of {', '.join(STATUSES)}; found {forced!r}")
        if forced == "passed" and tests["executed"] == 0:
            raise SystemExit("refusing to record 'passed' with zero executed tests; a green no-op is not evidence")
        return forced, f"status supplied by the caller ({forced}) and cross-checked against the result file"
    if error:
        return "not_run", error
    if tests["failed"] > 0:
        return "failed", f"{tests['failed']} of {tests['executed']} tests failed"
    if tests["executed"] == 0:
        return "unknown", "the result file held no test results at all"
    if tests["passed"] == 0:
        return "unknown", f"{tests['executed']} tests ran and none passed; nothing was established"
    return "passed", f"{tests['passed']} of {tests['executed']} tests passed ({tests['skipped']} skipped)"


def build(args: argparse.Namespace) -> dict:
    tests, error = parse_trx(Path(args.trx)) if args.trx else (
        {"executed": 0, "passed": 0, "failed": 0, "skipped": 0}, "no --trx was given")
    status, why = decide(tests, error, args.status)
    record = {
        "schema": SCHEMA,
        "subsystem": args.subsystem,
        "status": status,
        "status_reason": why,
        "repository": os.environ.get("GITHUB_REPOSITORY", "SpicyBadran/PC5"),
        "commit": args.commit or os.environ.get("GITHUB_SHA", ""),
        "workflow": args.workflow or os.environ.get("GITHUB_WORKFLOW", ""),
        "run": args.run or os.environ.get("GITHUB_RUN_ID", ""),
        "attempt": args.attempt or os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runner": args.runner or os.environ.get("RUNNER_OS", "unknown"),
        "tests": tests,
        "artifacts": sorted(a for a in args.artifact or []),
        "limitations": [
            MAXIMUM_CLAIM,
            "a green app dimension says nothing about the legacy kernel/image, the research "
            "record, or release readiness; those are separate records by design",
        ],
        "milestones": sorted(set(args.milestone or [])),
        "recorded_by": "tools/app_evidence.py",
    }
    if args.dimensions:
        # The other three columns, echoed only as far as their own records say. Absent stays
        # absent: the app job may not fill in another subsystem's status.
        record["other_dimensions_read_only"] = {
            name: (json.loads(Path(spec).read_text(encoding="utf-8"))["status"]
                   if spec and Path(spec).exists() else "not_run")
            for name, spec in (pair.split("=", 1) for pair in args.dimensions)
        }
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trx", default="", help="path to the dotnet test .trx result file")
    parser.add_argument("--out", default="-", help="where to write the record; '-' is stdout")
    parser.add_argument("--subsystem", default="app", help="which dimension this record speaks for")
    parser.add_argument("--status", default="", help=f"override, one of {', '.join(STATUSES)}; still checked")
    parser.add_argument("--commit", default="")
    parser.add_argument("--run", default="")
    parser.add_argument("--attempt", default="")
    parser.add_argument("--workflow", default="")
    parser.add_argument("--runner", default="")
    parser.add_argument("--artifact", action="append", default=[], help="repeatable artifact reference")
    parser.add_argument("--milestone", action="append", default=[], help="repeatable milestone id")
    parser.add_argument("--dimensions", action="append", default=[],
                        help="read-only echoes like legacy-kernel=path.json; absent files read not_run")
    args = parser.parse_args(argv)

    record = build(args)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        print(f"{record['subsystem']}: {record['status']} ({record['status_reason']}) -> {destination}")
    return 0 if record["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
