#!/usr/bin/env python3
"""Report whether the README progress dashboard still matches what has been merged.

`make presentation-check` proves the dashboard matches the *working tree*. This answers
the other question, the one that decides whether a dashboard is current at all: has every
merge into the primary branch since the dashboard was last regenerated been reflected in
it? It is read-only, it writes nothing, and it never merges anything.

    python3 tools/dashboard_reconcile.py            # exit 0 current, 1 outstanding
    python3 tools/dashboard_reconcile.py --json     # the same answer, machine-readable

The rule it implements is the one written in `AGENTS.md`: a successful merge into `main`
is the trigger, and an agent reconciles the dashboard without being asked.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

#: the files the dashboard is computed from; a merge touching any of them can move a figure
DRIVERS = (
    "docs/architecture/roadmap.json",
    "docs/architecture/PS5_PLATFORM_CONTRACT.md",
    "psm/",
    "docs/evidence/",
    "PS5UPDATE.PUP",
)

#: the files that *are* the dashboard
ASSETS = (
    "docs/assets/pc5-progress-matrix.svg",
    "README.md",
)

MERGE = re.compile(r"^Merge (pull request|branch)")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(("git", *args), cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def last_touching(repo: Path, path: str) -> str:
    return git(repo, "log", "-1", "--format=%H", "--", path)


def earlier(repo: Path, one: str, two: str) -> str:
    """The older of two commits, by ancestry rather than by date or by hex."""
    if not one or not two or one == two:
        return one or two
    if git(repo, "merge-base", "--is-ancestor", one, two):
        return one
    if git(repo, "merge-base", "--is-ancestor", two, one):
        return two
    # unrelated histories: neither contains the other, so the older date decides
    stamps = {sha: git(repo, "show", "-s", "--format=%ct", sha) for sha in (one, two)}
    return min(stamps, key=lambda sha: int(stamps[sha] or 0))


def merge_log(repo: Path, since: str) -> list:
    """First-parent merges at or after `since`, newest first."""
    span = f"{since}..HEAD" if since else "HEAD"
    out = git(repo, "log", "--first-parent", "--merges", f"--format=%H\t%ad\t%s",
              "--date=short", span)
    merges = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, date, subject = line.split("\t", 2)
        merges.append({"sha": sha, "date": date, "subject": subject,
                       "into_main": bool(MERGE.match(subject))})
    return merges


def touched(repo: Path, since: str) -> list:
    span = f"{since}..HEAD" if since else "HEAD"
    out = git(repo, "diff", "--name-only", span)
    return sorted({line for line in out.splitlines() if line.strip()})


def inspect(repo: Path) -> dict:
    head = git(repo, "rev-parse", "HEAD")
    dashboard = last_touching(repo, ASSETS[0])
    readme = last_touching(repo, ASSETS[1])
    # the dashboard is current only if the figure *and* the README block were written at
    # or after the same commit; the earlier of the two is the honest watermark
    stamp = earlier(repo, dashboard, readme)
    merges = merge_log(repo, stamp) if stamp else []
    changed = touched(repo, stamp) if stamp else []
    drivers = [path for path in changed if any(path.startswith(d) for d in DRIVERS)]
    return {
        "repository": str(repo),
        "head": head,
        "dashboard_last_written": stamp,
        "dashboard_figure_last_written": dashboard,
        "readme_block_last_written": readme,
        "unprocessed_merges": merges,
        "driver_files_since_then": drivers,
        "outstanding": stamp == "" or bool(merges),
        "reason": ("the dashboard figure or the README block has never been committed"
                   if stamp == "" else
                   (f"{len(merges)} merge commit(s) on the first-parent line since the "
                    "dashboard was written" if merges else "none")),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent),
                        help="repository to inspect (default: this checkout)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"dashboard-reconcile: {repo} is not a git repository", file=sys.stderr)
        return 2
    report = inspect(repo)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if report["outstanding"] else 0

    print(f"HEAD                 {report['head'][:12]}")
    print(f"dashboard written at {report['dashboard_last_written'][:12] or 'never'}")
    if report["unprocessed_merges"]:
        print(f"merges since then      {len(report['unprocessed_merges'])}")
        for merge in report["unprocessed_merges"]:
            mark = "into main" if merge["into_main"] else "merge commit"
            print(f"  {merge['sha'][:12]}  {merge['date']}  [{mark}]  {merge['subject']}")
    if report["driver_files_since_then"]:
        print(f"dashboard drivers touched: {len(report['driver_files_since_then'])}")
        for path in report["driver_files_since_then"]:
            print(f"  {path}")
    if report["outstanding"]:
        print(f"OUTSTANDING — {report['reason']}")
        print("  run: make presentation && make presentation-check && make docs-check")
        print("  then commit the regenerated README and figure (AGENTS.md, rule 1)")
        return 1
    print("CURRENT — every merge on the first-parent line is reflected in the dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
