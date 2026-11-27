#!/usr/bin/env python3
"""The commands PC5's progress maintenance runs, in one place so they can be tested.

`.github/workflows/progress.yml` is deliberately thin: it checks out the commit that a
completed verification ran on, calls these subcommands, and pushes whatever they produce.
Everything with a decision in it lives here, because a decision in YAML cannot be
imported, unit-tested, or re-run on a laptop -- and this repository's rule for the
dashboard is that a claim must be checkable by whoever reads it.

Subcommands
    target    refuse a commit this workflow may not publish from: the refresh is a write
              to `main`, so its input has to be a commit *on* `main` whose *own*
              verification succeeded — a branch name is not provenance, and neither is a
              green run on somebody else's tree
    guard     refuse to act on the automation's own commit, which is what stops a
              dashboard update from triggering another dashboard update forever
    record    write `docs/evidence/ci/<run-id>.json` from a run's job list
    decide    classify what the merge changed and whether an update is owed
    commit    stage, commit and push, but only when `decide` said so and a diff exists

Offline use and testing both go through `--jobs-json` / `--paths-file`, which is how the
demonstration in `tests/test_completion.py` can drive a whole merge cycle without
touching GitHub: the fixtures are synthetic, the code path is the real one.  The live
`--run-id` path is covered too, by tests that assert the `gh api` argv it builds -- an
endpoint is one argument and never a path with options glued into it -- because a read
that only exists in CI is exactly the kind of code a fixture-driven test cannot see.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import completion  # noqa: E402  sibling: the model and the policy, imported not restated

#: Which ref the published dashboard may be regenerated from, and where the comparison
#: against it comes from in a CI checkout.
MAIN_REF = "refs/heads/main"
ORIGIN_MAIN = "refs/remotes/origin/main"

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


#: Every live read goes through `gh api` with the repository template that GitHub expands
#: (`{owner}`/`{repo}`, filled from `GH_REPO` in CI and from the git remote locally).
API = "repos/{owner}/{repo}"


def api_endpoint(path: str) -> str:
    """One endpoint for `gh api`, validated.  An option never belongs inside a path.

    `subprocess.run` is called without a shell, which is the whole point -- there is no
    word-splitting and no globbing, so nothing here can be injected.  The cost of that is
    that `"…/jobs --paginate --jq '.jobs'"` is not a command line with two options: it is a
    single *path* containing spaces, which the API answers with 404.  That is exactly how the
    first real `workflow_run` of `progress.yml` failed on 2026-11-27 -- the run object came
    back, the jobs read did not, and the step exited 2 -- and it is invisible to a test that
    drives `record` with `--jobs-json`, because the offline path never builds an endpoint.
    """
    if " " in path or "\t" in path:
        raise ActionError(
            f"endpoint {path!r} carries whitespace: `gh api` runs without a shell, so a flag "
            "glued to the path is requested *as* the path")
    return f"{API}{path}"


def gh_api(path: str) -> str:
    """`gh api` for one endpoint, arguments as separate argv entries."""
    return _run(["gh", "api", api_endpoint(path)])


#: A run's jobs in one page.  100 is the API's maximum; this repository's runs have 2-3.
JOBS_PAGE = 100


def jobs_from_payload(payload: dict, run_id: object) -> list[dict]:
    """The jobs array of a run, refusing a truncated page.

    `total_count` is in the response, so "some of the jobs" is detectable rather than
    invisible.  A record built from part of a run would understate it -- the one failure this
    file exists to prevent -- so a short page is an error, not a smaller file.
    """
    jobs = payload.get("jobs") or []
    total = payload.get("total_count")
    if isinstance(total, int) and total > len(jobs):
        raise ActionError(
            f"run {run_id}: the jobs read returned {len(jobs)} of {total} jobs; refusing to "
            "file evidence that describes part of a run")
    return jobs


# ---------------------------------------------------------------- target
def _git(argv: list[str], root: Path | None = None) -> tuple[int, str]:
    completed = subprocess.run(["git", *argv], cwd=root or ROOT, capture_output=True,
                               text=True, check=False)
    return completed.returncode, (completed.stdout or completed.stderr).strip()


def target_verdict(event: str, sha: str, ref_name: str = "", run_event: str = "",
                   run_repo: str = "", repository: str = "", conclusion: str = "success",
                   main_ref: str = ORIGIN_MAIN, root: Path | None = None) -> dict:
    """May this refresh be published from `sha`?  Returns a verdict; never raises.

    The workflow runs with `contents: write` and pushes to `main`, so the input it accepts
    is the whole security boundary.  Four checks, each refusing for its own reason, because
    "the trigger did not match" tells a maintainer nothing while four sentences tell them
    which field moved:

      1. **`sha` is shaped like a commit.**  Every value below arrives from an event payload
         through `env:`, so none of it is trusted text; a 40-hex-digit check is the cheapest
         way to keep a malformed field from reaching `git` at all.
      2. **the triggering run was a `push`** — not a `pull_request`.  A pull-request run's
         `head_branch` is the contributor's own branch name, so a fork with a branch called
         `main` satisfies a name match while pointing at code nobody merged.
      3. **the run belongs to this repository** — `head_repository.full_name` must equal the
         repository the dashboard is written to, which is the same test in a fork's terms
         rather than in a name's.
      4. **the commit is an ancestor of `main`** — the only one of the four that is a fact
         about the tree rather than about an event.  `git merge-base --is-ancestor` answers it
         against the ref the job just fetched; an unavailable ref is a refusal, never a pass,
         because a comparison that could not run proves nothing.

    A `workflow_dispatch` is allowed only when it ran *on* `main`: a dispatch can be started
    from any ref that carries this file, and the ref's own name is then the only claim about
    where the tree came from.
    """
    checks: list[str] = []

    def refusing(problem: str) -> dict:
        return {"proceed": False, "reason": problem, "checks": checks, "sha": sha,
                "event": event}

    def passed(what: str) -> None:
        checks.append(what)

    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        return refusing(f"`{sha!r}` is not a 40-character commit id, so it is not a sha")
    passed("sha-shape")
    if event == "workflow_dispatch":
        if ref_name != "main":
            return refusing(f"a manual refresh was dispatched on {ref_name!r}, not on main, "
                            f"so its tree is not the published one")
        passed("dispatch-on-main")
        return {"proceed": True, "reason": f"{sha[:12]} dispatched on main", "checks": checks,
                "sha": sha, "event": event}
    if event != "workflow_run":
        return refusing(f"the refresh was started by {event!r}, which has no verified "
                        f"verification behind it")
    if conclusion != "success":
        return refusing(f"the triggering run concluded {conclusion!r}; only a success is "
                        f"verified state")
    passed("conclusion")
    if run_event != "push":
        return refusing(f"the triggering run was a {run_event!r}, not a push to a branch; a "
                        f"pull-request head can be called main without being merged")
    passed("push-event")
    if repository and run_repo != repository:
        return refusing(f"the triggering run belongs to {run_repo!r}, not {repository!r}")
    passed("same-repository")
    code, out = _git(["rev-parse", "--verify", "-q", f"{sha}^{{commit}}"], root)
    if code != 0:
        return refusing(f"git cannot resolve {sha[:12]} in this checkout, so the commit this "
                        f"refresh would publish from is not the one that was verified")
    passed("object-present")
    code, _ = _git(["rev-parse", "--verify", "-q", main_ref], root)
    if code != 0:
        return refusing(f"{main_ref} is not available to compare against; refusing rather "
                        f"than assuming the commit is on main")
    code, _ = _git(["merge-base", "--is-ancestor", sha, main_ref], root)
    if code != 0:
        return refusing(f"{sha[:12]} is not an ancestor of {main_ref}: its gates are not "
                        f"main's, and a dashboard commit cannot be published from it")
    passed("ancestor-of-main")
    return {"proceed": True, "reason": f"{sha[:12]} is on main ({', '.join(checks)})",
            "checks": checks, "sha": sha, "event": event}


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
                   f"/jobs?per_page={JOBS_PAGE}, then /check-runs/<job id>/annotations"),
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
    parser.add_argument("command", choices=("target", "guard", "record", "decide", "commit"))
    parser.add_argument("--sha", default="", help="for target: the commit to publish from")
    parser.add_argument("--event", default="", help="for target: the triggering event name")
    parser.add_argument("--ref-name", default="", help="for target: the ref a dispatch ran on")
    parser.add_argument("--run-event", default="", help="for target: the completed run's event")
    parser.add_argument("--run-repo", default="", help="for target: the run's head repository")
    parser.add_argument("--repository", default="", help="for target: this repository's full name")
    parser.add_argument("--main-ref", default=ORIGIN_MAIN,
                        help="for target: the ref to test ancestry against")
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

    if args.command == "target":
        verdict = target_verdict(args.event, args.sha, ref_name=args.ref_name,
                                 run_event=args.run_event, run_repo=args.run_repo,
                                 repository=args.repository, conclusion=args.conclusion,
                                 main_ref=args.main_ref, root=root)
        print(json.dumps(verdict) if args.json else verdict["reason"])
        return 0 if verdict["proceed"] else 4

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
            run = json.loads(gh_api(f"/actions/runs/{args.run_id}"))
            jobs = jobs_from_payload(
                json.loads(gh_api(f"/actions/runs/{args.run_id}/jobs?per_page={JOBS_PAGE}")),
                args.run_id)
            for job in jobs:
                try:
                    job["annotations"] = json.loads(
                        gh_api(f"/check-runs/{job['id']}/annotations"))
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
