#!/usr/bin/env python3
"""The commands PC5's progress maintenance runs, in one place so they can be tested.

`.github/workflows/progress.yml` is deliberately thin: it checks out the commit that a
completed verification ran on, calls these subcommands, and pushes whatever they produce.
Everything with a decision in it lives here, because a decision in YAML cannot be
imported, unit-tested, or re-run on a laptop -- and this repository's rule for the
dashboard is that a claim must be checkable by whoever reads it.

Subcommands
    guard     refuse to act on the automation's own commit, which is what stops a
              dashboard update from triggering another dashboard update forever
    record    write `docs/evidence/ci/<run-id>.json` from a run's job list
    decide    classify what the merge changed and whether an update is owed
    commit    stage, commit and push, but only when `decide` said so and a diff exists

Offline use and testing both go through `--jobs-json` / `--paths-file`, which is how the
demonstration in `tests/test_completion.py` can drive a whole merge cycle without
touching GitHub: the fixtures are synthetic, the code path is the real one.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import completion  # noqa: E402  sibling: the model and the policy, imported not restated

CI_DIR = "docs/evidence/ci"
CI_SCHEMA = "pc5.ci/1"
PREFIX = "docs: refresh the progress dashboard"


class ActionError(RuntimeError):
    """A refused action.  Refusing is a normal, successful outcome for this tool."""


def _run(argv: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(argv, cwd=cwd or ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise ActionError(f"{' '.join(argv)}: exit {completed.returncode}: "
                          f"{(completed.stderr or completed.stdout).strip()[:300]}")
    return completed.stdout


# ---------------------------------------------------------------- guard
def guard(subject: str, author: str = "") -> dict:
    """Would acting here create the commit that acts here again?

    GitHub already refuses to start `push`-triggered workflows for a commit made with
    the run's own `GITHUB_TOKEN`, so the primary loop break is structural.  This is the
    second lock, and the one that is also correct when a human runs the same commands:
    a progress commit is never a reason to make another progress commit.
    """
    text = (subject or "").strip()
    if text.startswith(PREFIX):
        return {"proceed": False,
                "reason": f"HEAD is itself a progress commit ({text[:48]}…); nothing to refresh"}
    if author in {"github-actions[bot]", "github-actions"}:
        return {"proceed": False, "reason": f"HEAD was authored by {author}"}
    return {"proceed": True, "reason": "HEAD is an ordinary commit"}


# ---------------------------------------------------------------- record
def build_record(run: dict, jobs: list[dict]) -> dict:
    """One hosted run, described faithfully: conclusion, tallies, and what was skipped.

    A skipped job is recorded as skipped, and the reason is kept.  This file is the only
    way hosted CI reaches the README, so it must not be a place where a `skipped` can
    become a pass or a `cancelled` can become a silence.
    """
    jobs_out = []
    host_tests: dict = {}
    for job in jobs:
        entry = {
            "name": job.get("name"),
            "conclusion": job.get("conclusion"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
        }
        steps = job.get("steps") or []
        if steps:
            entry["steps_success"] = sum(1 for s in steps if s.get("conclusion") == "success")
            entry["steps_skipped"] = sum(1 for s in steps if s.get("conclusion") == "skipped")
            entry["steps_failed"] = sum(
                1 for s in steps if s.get("conclusion") in ("failure", "cancelled", "timed_out"))
        jobs_out.append(entry)
        for annotation in job.get("annotations") or []:
            body = annotation.get("body") or annotation.get("message") or ""
            if "Ran " in body and " tests" in body:
                host_tests["ran"] = int(body.split("Ran ")[1].split()[0])
            if body.startswith("OK") or body.startswith("FAILED"):
                host_tests["result"] = "OK" if body.startswith("OK") else "FAILED"
                if "skipped=" in body:
                    host_tests["skipped"] = int(body.split("skipped=")[1].rstrip(") ").split()[0])
    record = {
        "schema": CI_SCHEMA,
        "run_id": run.get("id"),
        "workflow": Path(str(run.get("path") or "build.yml")).name,
        "event": run.get("event"),
        "branch": run.get("head_branch"),
        "head_sha": run.get("head_sha"),
        "conclusion": run.get("status") if run.get("status") != "completed" else run.get("conclusion"),
        "attempt": run.get("run_attempt", 1),
        "started_at": run.get("run_started_at"),
        "completed_at": run.get("updated_at"),
        "jobs": jobs_out,
        "recorded_at": datetime.now(timezone.utc).date().isoformat(),
        "recorded_by": "tools/progress_record.py",
        "source": (f"gh api repos/{{owner}}/{{repo}}/actions/runs/{run.get('id')}"
                   "/jobs with annotations"),
    }
    if host_tests:
        record["host_tests"] = host_tests
    return record


def write_record(record: dict, root: Path | None = None) -> Path:
    target = (root or ROOT) / CI_DIR / f"{record['run_id']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == payload:
        return target
    target.write_text(payload, encoding="utf-8")
    return target


# ---------------------------------------------------------------- decide
def changed_paths(diff_stdout: str) -> list[str]:
    return [line.strip() for line in diff_stdout.splitlines() if line.strip()]


def decide(conclusion: str, paths: list[str], changed: bool) -> dict:
    """The one policy the workflow follows, delegated to `completion.decide`."""
    return completion.decide(conclusion, completion.triage(paths), changed)


# ---------------------------------------------------------------- commit
def commit_update(message: str, dry_run: bool = True, push: bool = True,
                  branch: str = "main", root: Path | None = None) -> dict:
    """Stage, commit, push -- only if there is a diff.  No force, no tag, no release."""
    work = root or ROOT
    status = subprocess.run(["git", "status", "--porcelain"], cwd=work,
                            capture_output=True, text=True, check=False).stdout
    touched = [line[3:] for line in status.splitlines()
               if line[1:3] != "??" or line[3:].startswith(CI_DIR)]
    if not touched:
        return {"committed": False, "reason": "nothing changed", "paths": []}
    if dry_run:
        return {"committed": False, "reason": "dry run", "paths": sorted(touched)}
    _run(["git", "add", "--"] + sorted(touched), cwd=work)
    _run(["git", "commit", "-m", message], cwd=work)
    sha = _run(["git", "rev-parse", "HEAD"], cwd=work).strip()
    pushed = False
    if push:
        _run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=work)
        pushed = True
    return {"committed": True, "sha": sha, "pushed": pushed, "paths": sorted(touched),
            "reason": "presentation regenerated from verified state"}


def git_output(argv: list[str], root: Path | None = None) -> str:
    return subprocess.run(argv, cwd=root or ROOT, capture_output=True, text=True,
                          check=False).stdout


# ---------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("guard", "record", "decide", "commit"))
    parser.add_argument("--subject", default="", help="for guard: HEAD's commit subject")
    parser.add_argument("--author", default="", help="for guard: HEAD's commit author login")
    parser.add_argument("--run-id", default=None,
                        help="for record: fetch a run and its jobs with `gh api`")
    parser.add_argument("--run-json", default=None,
                        help="for record: the Actions run object, or - for stdin")
    parser.add_argument("--jobs-json", default=None,
                        help="for record: the run's jobs array (offline, and how tests drive it)")
    parser.add_argument("--root", default=None, help="repository to write into")
    parser.add_argument("--conclusion", default="success", help="for decide")
    parser.add_argument("--paths-file", default=None, help="for decide: changed paths, one per line")
    parser.add_argument("--changed", action="store_true", help="for decide: regeneration differed")
    parser.add_argument("--message", default=f"{PREFIX} at HEAD", help="for commit")
    parser.add_argument("--branch", default="main", help="for commit")
    parser.add_argument("--apply", action="store_true", help="for commit: actually write and push")
    parser.add_argument("--no-push", action="store_true", help="for commit: commit locally only")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else None

    if args.command == "guard":
        verdict = guard(args.subject, args.author)
        print(json.dumps(verdict) if args.json else verdict["reason"])
        return 0 if verdict["proceed"] else 3

    if args.command == "record":
        run, jobs = {}, []
        if args.run_id:
            # CI path: the API is the source, and the annotations are where a run's
            # test tally actually survives, because the raw log is not reachable from
            # a reader's shell.  `record_line` below records what came back; nothing here
            # synthesises a conclusion the API did not report.
            run = json.loads(_run(["gh", "api", f"repos/{{owner}}/{{repo}}"
                                          f"/actions/runs/{args.run_id}"]))
            jobs = json.loads(_run(["gh", "api",
                                    f"repos/{{owner}}/{{repo}}/actions/runs/{args.run_id}/jobs"
                                    " --paginate --jq '.jobs'"]))
            for job in jobs:
                try:
                    job["annotations"] = json.loads(_run(
                        ["gh", "api", f"repos/{{owner}}/{{repo}}/check-runs/{job['id']}"
                                      "/annotations"]))
                except ActionError:
                    job["annotations"] = []
        elif args.run_json:
            run = json.loads(Path(args.run_json).read_text(encoding="utf-8"))
        else:
            run = json.loads(sys.stdin.read())
        if args.jobs_json:
            jobs = json.loads(Path(args.jobs_json).read_text(encoding="utf-8")
                              if args.jobs_json != "-" else sys.stdin.read())
        record = build_record(run, jobs if isinstance(jobs, list) else [])
        path = write_record(record, root)
        print(f"recorded run {record['run_id']} ({record['conclusion']}) -> {path.relative_to(root or ROOT)}")
        return 0

    if args.command == "decide":
        raw = Path(args.paths_file).read_text(encoding="utf-8") if args.paths_file else ""
        paths = completion.parse_paths(raw)
        outcome = decide(args.conclusion, paths, args.changed)
        print(json.dumps(outcome, indent=2, sort_keys=True) if args.json
              else f"{outcome['action']}: {outcome['reason']}")
        return {"commit": 0, "noop": 0, "refuse": 1}[outcome["action"]]

    # commit
    if not args.apply:
        outcome = commit_update(args.message, dry_run=True, root=root)
    else:
        outcome = commit_update(args.message, dry_run=False, push=not args.no_push,
                                branch=args.branch, root=root)
    print(json.dumps(outcome, indent=2, sort_keys=True) if args.json
          else f"{'committed' if outcome['committed'] else 'no commit'}: {outcome['reason']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ActionError as exc:
        print(f"progress: {exc}", file=sys.stderr)
        sys.exit(2)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"progress: refused, input unreadable: {exc}", file=sys.stderr)
        sys.exit(2)
