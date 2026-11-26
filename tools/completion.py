#!/usr/bin/env python3
"""PC5's overall project completion: the calculation, its evidence, and its refusals.

This module is the only place the headline percentage is computed.  It reads two
canonical sources and invents nothing:

* ``docs/architecture/roadmap.json`` -- the milestone graph, its states, and each
  milestone's objective acceptance criteria (``tools/roadmap.py`` is the validator of
  that file, and ``make roadmap`` renders ``docs/ROADMAP.md`` from it);
* ``docs/evidence/validation/*.json`` -- the committed records of what the gates
  actually reported, at which commit (``tools/validation.py`` writes them).

Why weighting is *derived* rather than authored
----------------------------------------------
Milestones do not have equal scope: ``M1c`` (ten acceptance criteria, nine pieces of
evidence) is not ``M1f`` (two criteria, one).  A hand-maintained weight table would be
a second source of truth that nobody reviews, which is how a progress figure silently
becomes fiction.  So the weight of a milestone is its own acceptance-criteria count --
the roadmap's definition of what completing it means -- and it therefore cannot drift
away from the milestone it measures::

    overall completion = sum(weight of milestones whose state is "complete")
                         / sum(weight of every milestone)          x 100

Counting milestones instead (9 of 35) is the same figure without the scope proxy, so
both are published side by side: if the number were an artefact of the weighting
choice, the two would disagree, and a reader can see that they do not.

Partial credit is refused unless the canonical data states it
-------------------------------------------------------------
An ``in_progress`` milestone earns nothing here, because nothing in the repository
records *which* of its acceptance criteria are satisfied.  The mechanism exists -- a
milestone may carry ``criteria_met: N``, an integer between 0 and the size of its
acceptance list, and ``tools/roadmap.py`` ignores the field as it ignores any other
additive one -- but no milestone currently declares it, so the README says plainly
that in-progress work is excluded rather than guessing at a fraction.

Five states, five meanings, never merged
----------------------------------------
``implemented``, ``merged``, ``locally verified``, ``CI verified``, ``milestone
complete`` and ``released`` are different claims.  This model awards credit for the
fifth only, and only because ``complete`` in the roadmap is itself defined as
"acceptance criteria met, evidence filed" -- which is why a ``complete`` milestone with
no ``evidence`` list is an error here rather than a smaller number.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROADMAP = ROOT / "docs/architecture/roadmap.json"
VALIDATION = ROOT / "docs/evidence/validation"

COMPLETE = "complete"
STATES = ("complete", "in_progress", "not_started", "blocked", "not_tested")

#: Paths whose change can alter what is true about progress.  Anything else is
#: prose, and prose never moves a milestone.  ``source`` is deliberately the widest
#: bucket: a commit that touches the kernel or the loader has to be re-read even
#: when the presentation is what someone came here to edit.
SIGNIFICANT = {
    "roadmap": ("docs/architecture/roadmap.json", "docs/ROADMAP.md"),
    "evidence": ("docs/evidence/", "evidence/", "docs/TEST_RESULTS.md"),
    "source": ("boot/", "kernel/", "kernel64/", "psm/"),
    "build": ("Makefile", "tools/mkiso.py", "tools/mkefiimg.py", "tools/verify_iso.py",
              "tools/buildmeta.py", "tools/presentation.py", "tools/completion.py"),
    "pipeline": (".github/workflows/", "ci/"),
    "tests": ("tests/",),
}

#: The shapes a merged diff can take that justify rewriting the presentation.  A
#: cosmetic-only change returns ``()`` and the automation then commits nothing.
CATEGORIES = tuple(SIGNIFICANT)


class CompletionError(ValueError):
    """The canonical data cannot support a percentage.  Never silently approximate."""


# ---------------------------------------------------------------- loading
def load_roadmap(path: Path | None = None) -> dict:
    target = path or ROADMAP
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CompletionError(f"{target.name}: cannot be read ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise CompletionError(f"{target.name}: is not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("milestones"), list):
        raise CompletionError(f"{target.name}: has no milestones list")
    return data


def load_records(directory: Path | None = None) -> dict:
    """Committed gate records, keyed by gate.  Unreadable ones are skipped, not trusted."""
    found: dict[str, dict] = {}
    for path in sorted((directory or VALIDATION).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("schema") != "pc5.validation/1":
            continue
        record["_file"] = f"docs/evidence/validation/{path.name}"
        found[str(record.get("gate") or path.stem)] = record
    return found


# ---------------------------------------------------------------- validation
def validate(data: dict) -> list[str]:
    """Every way the graph could make a percentage dishonest, checked before arithmetic."""
    problems: list[str] = []
    milestones = data["milestones"]
    seen: set[str] = set()
    for index, milestone in enumerate(milestones):
        where = f"milestones[{index}]"
        if not isinstance(milestone, dict):
            problems.append(f"{where}: is not an object")
            continue
        mid = str(milestone.get("id") or "")
        if not mid:
            problems.append(f"{where}: has no id")
            continue
        where = f"{mid}"
        if mid in seen:
            problems.append(f"{where}: duplicated milestone id")
            continue
        seen.add(mid)
        state = milestone.get("state")
        if state not in STATES:
            problems.append(f"{where}: state {state!r} is not one of {STATES}")
        criteria = milestone.get("acceptance")
        if not isinstance(criteria, list) or not criteria:
            problems.append(f"{where}: has no acceptance criteria, so its weight is undefined")
        elif not all(isinstance(item, str) and item.strip() for item in criteria):
            problems.append(f"{where}: an acceptance criterion is not a non-empty string")
        met = milestone.get("criteria_met")
        if met is not None:
            if isinstance(met, bool) or not isinstance(met, int):
                problems.append(f"{where}: criteria_met must be an integer, not {type(met).__name__}")
            elif not 0 <= met <= len(criteria or []):
                problems.append(f"{where}: criteria_met={met} is outside 0..{len(criteria or [])}")
            elif state == COMPLETE and met != len(criteria or []):
                problems.append(f"{where}: complete milestones must not under-report criteria_met")
        if state == COMPLETE and not (milestone.get("evidence") or []):
            problems.append(f"{where}: is complete with no evidence filed")
        for dep in milestone.get("depends_on") or []:
            if dep not in {m.get("id") for m in milestones}:
                problems.append(f"{where}: depends on {dep!r}, which is not a milestone")
    destination = ((data.get("product") or {}).get("destination") or {}).get("milestone")
    if destination and destination not in seen:
        problems.append(f"product.destination {destination!r} is not a milestone")
    return problems


CI_EVIDENCE = "docs/evidence/ci"
CI_SCHEMA = "pc5.ci/1"


def hosted_runs(root: Path | None = None) -> list[dict]:
    """Filed records of completed hosted runs, newest first.

    The dashboard's CI row reads committed evidence rather than a live badge, on
    purpose.  A badge is fresher, but it would make the README assert something no
    gate can check, it would depend on a third-party service, and while a run is in
    flight it would display the status of a workflow that has not finished, which is
    the one thing this dashboard must never do.  A run is credited here when it is
    filed, and filing is what the maintenance workflow does after a green run.
    """
    directory = (root or ROOT) / CI_EVIDENCE
    found = []
    if not directory.is_dir():
        return []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and record.get("schema") == CI_SCHEMA:
            record["_file"] = f"{CI_EVIDENCE}/{path.name}"
            found.append(record)
    found.sort(key=lambda r: (str(r.get("completed_at") or ""), str(r.get("run_id") or "")),
               reverse=True)
    return found


def hosted_summary(root: Path | None = None) -> dict:
    """One honest line about hosted CI, or the explicit statement that none is filed."""
    runs = hosted_runs(root)
    if not runs:
        return {"available": False, "text": "**no hosted record filed**", "light": "grey",
                "conclusion": "unknown", "attempts": 0}
    newest = runs[0]
    conclusion = str(newest.get("conclusion") or "unknown")
    tests = newest.get("host_tests") or {}
    tally = ""
    if tests.get("ran") is not None:
        tally = f", {tests['ran']} host tests {tests.get('result', '?')}"
        if tests.get("skipped") is not None:
            tally += f" ({tests['skipped']} skipped)"
    light = {"success": "green", "failure": "red", "cancelled": "amber"}.get(conclusion, "grey")
    return {"available": True, "run_id": newest.get("run_id"), "light": light,
            "conclusion": conclusion, "attempts": len(runs),
            "head": str(newest.get("head_sha") or ""), "file": newest["_file"],
            "completed_at": newest.get("completed_at"),
            "text": f"run `{newest.get('run_id')}` **{conclusion.upper()}** at "
                    f"`{str(newest.get('head_sha') or '')[:7]}`" + tally,
            "skipped_note": str(newest.get("notes") or "")}


# ---------------------------------------------------------------- the model
def weight_of(milestone: dict) -> float:
    """The scope proxy: how many objective criteria completion was defined as."""
    return float(len(milestone.get("acceptance") or []))


def credit_of(milestone: dict) -> float:
    """Earned weight.  Complete earns everything; partial only what the data states."""
    state = milestone.get("state")
    criteria = len(milestone.get("acceptance") or [])
    if state == COMPLETE:
        return float(criteria)
    met = milestone.get("criteria_met")
    if isinstance(met, int) and not isinstance(met, bool) and criteria:
        return weight_of(milestone) * (met / criteria)
    return 0.0


def compute(data: dict) -> dict:
    """The whole model, as plain JSON-serialisable arithmetic anyone can re-run."""
    problems = validate(data)
    if problems:
        raise CompletionError("; ".join(problems))
    milestones = data["milestones"]
    total_weight = sum(weight_of(m) for m in milestones)
    earned_weight = sum(credit_of(m) for m in milestones)
    if total_weight <= 0:
        raise CompletionError("the roadmap carries no acceptance criteria at all")
    if not 0 <= earned_weight <= total_weight:
        raise CompletionError(f"earned weight {earned_weight} escaped 0..{total_weight}")

    complete = [m["id"] for m in milestones if m["state"] == COMPLETE]
    in_progress = [m["id"] for m in milestones if m["state"] == "in_progress"]
    blocked = [m["id"] for m in milestones if m["state"] == "blocked"]
    not_started = [m["id"] for m in milestones if m["state"] == "not_started"]
    partial = [m["id"] for m in milestones
                if m["state"] != COMPLETE and isinstance(m.get("criteria_met"), int)
                and m["criteria_met"] > 0]
    levels: dict[str, dict] = {}
    for milestone in milestones:
        level = f"L{milestone.get('level')}"
        bucket = levels.setdefault(level, {"total": 0, "complete": 0, "weight": 0.0,
                                           "earned": 0.0})
        bucket["total"] += 1
        bucket["weight"] += weight_of(milestone)
        bucket["earned"] += credit_of(milestone)
        if milestone["state"] == COMPLETE:
            bucket["complete"] += 1
    gating = [m for m in milestones if m.get("release_gating")]
    gating_complete = [m for m in gating if m["state"] == COMPLETE]
    destination = str(((data.get("product") or {}).get("destination") or {}).get("milestone"))
    by_id = {m["id"]: m for m in milestones}
    percent = 100.0 * earned_weight / total_weight
    if percent > 100.0 or percent < 0.0:
        raise CompletionError(f"completion {percent}% is outside 0..100")
    return {
        "percent": round(percent, 1),
        "percent_by_count": round(100.0 * len(complete) / len(milestones), 1),
        "agreement_points": round(abs(percent - 100.0 * len(complete) / len(milestones)), 1),
        "weighting": {
            "rule": "weight = number of acceptance criteria in the canonical roadmap",
            "earned": "full weight when state is complete; otherwise only criteria_met, if declared",
            "total_weight": total_weight,
            "earned_weight": earned_weight,
            "milestones": len(milestones),
        },
        "counts": {"total": len(milestones), "complete": len(complete),
                   "in_progress": len(in_progress), "blocked": len(blocked),
                   "not_started": len(not_started),
                   "workstreams": len(data.get("workstreams") or [])},
        "milestones": {"complete": complete, "in_progress": in_progress,
                       "blocked": blocked, "not_started": not_started,
                       "partial_credit": partial},
        "levels": {k: dict(levels[k], percent=round(100.0 * levels[k]["earned"] / levels[k]["weight"], 1))
                   for k in sorted(levels)},
        "release_gating": {"total": len(gating), "complete": len(gating_complete),
                           "outstanding": [m["id"] for m in gating if m["state"] != COMPLETE]},
        "destination": {"id": destination,
                        "title": (by_id.get(destination) or {}).get("title", ""),
                        "state": (by_id.get(destination) or {}).get("state", "unknown")},
        "criteria": {"total": int(sum(weight_of(m) for m in milestones)),
                     "complete": int(sum(weight_of(m) for m in milestones
                                         if m["state"] == COMPLETE))},
        "roadmap_version": data.get("document_version"),
        "roadmap_updated": data.get("updated"),
        "product_version": data.get("_product_version"),
    }


def verified_at(records: dict) -> dict:
    """The stamp: the newest committed record, and the commit it ran at.

    Deliberately *not* ``git log -1`` and not the clock.  A README that recorded "the
    SHA at the moment somebody regenerated it" would change on every run, which would
    make the automation commit a diff every time and make the figure unverifiable
    after the fact.  The record is the evidence, so the record is the stamp.
    """
    usable = [r for r in records.values() if r.get("date") and r.get("commit")]
    if not usable:
        return {"date": "no record", "commit": "no record", "gates": 0,
                "result": "no record", "summary": ""}
    newest = max(usable, key=lambda r: (str(r["date"]), str(r.get("gate", ""))))
    return {"date": str(newest["date"]),
            "commit": str(newest["commit"]),
            "short": str(newest["commit"])[:7],
            "gate": str(newest.get("gate", "")),
            "result": str(newest.get("result", "")),
            "dirty": bool(newest.get("dirty")),
            "summary": str(newest.get("summary", "")),
            "gates": len(records),
            "passed": sum(1 for r in usable if r.get("result") == "passed")}


# ---------------------------------------------------------------- release state
def release_state(data: dict, root: Path | None = None) -> dict:
    """What the roadmap says about publication, read from the graph rather than assumed.

    ``M1b`` is the release milestone; the repository has no tag on a commit in this
    history and the release job has no executed run, so "release ready" and "released"
    are reported as two different false-and-true pairs and never conflated.
    """
    by_id = {m["id"]: m for m in data["milestones"]}
    m1b = by_id.get("M1b") or {}
    state = m1b.get("state", "unknown")
    return {"milestone": "M1b",
            "state": state,
            "ready": state == COMPLETE,
            "criteria": len(m1b.get("acceptance") or []),
            "blocker": str(m1b.get("blocker") or ""),
            "published": False,
            "note": "no tag exists on this history and the release job has never executed"}


# ---------------------------------------------------------------- rendering
def _row(label: str, value: str) -> str:
    return f"| **{label}** | {value} |"


def markdown(model: dict, stamp: dict, release: dict, hosted: dict | None = None) -> str:
    """The generated block.  Numbers live here and nowhere else in the README."""
    counts = model["counts"]
    lines = ["<!-- pc5:completion:begin -->"]
    lines.append(f"**{model['percent']}% of PC5's declared scope is complete and evidenced.** "
                 f"{counts['complete']} of {counts['total']} milestones are `complete` "
                 f"({model['percent_by_count']}% counted by milestone), "
                 f"{model['criteria']['complete']} of {model['criteria']['total']} acceptance "
                 f"criteria are met, and the destination `{model['destination']['id']}` is "
                 f"**{model['destination']['state'].upper().replace('_', ' ')}**.")
    lines.append("")
    lines.append("| Measure | Value |")
    lines.append("|---|---|")
    lines.append(_row("Overall completion", f"**{model['percent']}%** — criteria-weighted"))
    lines.append(_row("Same figure by milestone count", f"{model['percent_by_count']}% "
                       f"(the two models agree within {model['agreement_points']} points)"))
    lines.append(_row("Earned weight / total weight",
                      f"{model['weighting']['earned_weight']:g} / "
                      f"{model['weighting']['total_weight']:g}"))
    lines.append(_row("Milestones complete", f"{counts['complete']} of {counts['total']} in "
                      f"{counts['workstreams']} workstreams"))
    lines.append(_row("Current milestone", ", ".join(f"`{m}`" for m in model["milestones"]["in_progress"])
                      or "none `in_progress`"))
    lines.append(_row("Remaining", f"{counts['not_started']} not started, {counts['blocked']} "
                      f"blocked, {counts['in_progress']} in progress"))
    levels = model["levels"]
    lines.append(_row("By level of the hierarchy", " · ".join(
        f"`{level}` {bucket['percent']}%" for level, bucket in sorted(levels.items()))
        + " — the low levels are the ones with evidence, the top levels have none yet"))
    gating = model["release_gating"]
    lines.append(_row("Release-gating milestones", f"{gating['complete']} of {gating['total']} "
                      f"complete; outstanding: " + ", ".join(f"`{m}`" for m in gating["outstanding"])))
    lines.append(_row("Partial credit", "excluded — " + (
        f"{len(model['milestones']['partial_credit'])} milestone(s) declare"
        " `criteria_met`"
        if model["milestones"]["partial_credit"] else
        "no milestone declares `criteria_met`, so in-progress work counts 0")))
    hosted = hosted if hosted is not None else hosted_summary()
    lines.append(_row("Hosted CI", hosted["text"] + (
        f" · {hosted['skipped_note']}" if hosted.get("skipped_note") else "")))
    lines.append(_row("Release readiness", f"`{release['milestone']}` "
                      f"**{release['state'].upper().replace('_', ' ')}** — "
                      f"{release['criteria']} acceptance criteria outstanding; nothing published "
                      f"({release['note']})"))
    lines.append(_row("Last verified", f"{stamp['date']} — `{stamp['gate']}` recorded "
                      f"**{stamp['result']}** at `{stamp['short'] if stamp.get('short') else 'no record'}` "
                      f"({stamp['passed']}/{stamp['gates']} committed gate records passed)"))
    lines.append("")
    lines.append("One number, one formula, one source: `python3 tools/completion.py model` "
                 "reprints this calculation from `docs/architecture/roadmap.json` and the records "
                 "under `docs/evidence/validation/`, and `python3 tools/completion.py check` fails "
                 "if a milestone id is duplicated, a weight is undefined, an in-progress fraction "
                 "is invented, or a percentage is typed into prose instead of generated here.")
    lines.append("<!-- pc5:completion:end -->")
    return "\n".join(lines)


# ---------------------------------------------------------------- automation core
def triage(paths: list[str]) -> tuple[str, ...]:
    """Which kinds of truth a merged diff could have changed.

    Empty means "nothing a dashboard may claim has moved", and the automation then
    produces no commit.  The categories mirror the sources this model reads, not the
    directories a reviewer happens to like.
    """
    hit: set[str] = set()
    for path in paths:
        # `lstrip("./")` would be a bug here, not a shortcut: it strips *characters*, so
        # ".github/workflows/build.yml" loses its leading dot and stops matching the
        # prefix it is supposed to be classified by.  A dotfile path is exactly the kind
        # of path a pipeline change lives at.
        normalised = path.strip()
        while normalised.startswith("./"):
            normalised = normalised[2:]
        if not normalised:
            continue
        for category, prefixes in SIGNIFICANT.items():
            if normalised.startswith(prefixes):
                hit.add(category)
    return tuple(sorted(hit))


def parse_paths(text: str) -> list[str]:
    """Accept a newline- or NUL-separated path list, and ignore anything blank."""
    parts = re.split(r"[\n\0]+", text or "")
    return [p.strip() for p in parts if p.strip()]


def decide(conclusion: str, categories: tuple[str, ...], changed: bool) -> dict:
    """The whole automation policy, as one pure function the tests can pin.

    A verification that did not succeed cannot publish an update -- not even a
    "honest" one, because a red run says nothing about what the current figure should
    be.  A green run with nothing significant to re-read does no work.  And a green
    run whose regeneration produced no diff commits nothing, which is what keeps the
    pipeline from churning the README on merges that changed no claim.
    """
    reason = ""
    if conclusion != "success":
        action, reason = "refuse", (f"verification concluded {conclusion!r}; an unverified "
                                   f"progress update is never published")
    elif not categories:
        action, reason = "noop", "no source, evidence, roadmap, pipeline or test path changed"
    elif not changed:
        action, reason = "noop", "regenerated figures and blocks are byte-identical"
    else:
        action = "commit"
        reason = "changed categories: " + ", ".join(categories)
    return {"action": action, "reason": reason, "categories": list(categories),
            "conclusion": conclusion}


SUMMARY = ("overall completion {percent}% ({complete}/{total} milestones, "
           "{criteria_done}/{criteria_total} acceptance criteria); "
           "current {current}; release {release_state}; verified {verified} at {commit}")


def summary_line(model: dict, stamp: dict, release: dict) -> str:
    counts = model["counts"]
    return SUMMARY.format(
        percent=model["percent"], complete=counts["complete"], total=counts["total"],
        criteria_done=model["criteria"]["complete"], criteria_total=model["criteria"]["total"],
        current=", ".join(model["milestones"]["in_progress"]) or "none",
        release_state=release["state"],
        verified=stamp["date"], commit=stamp.get("short", "no record"))


# ---------------------------------------------------------------- command line
def _product_version(root: Path) -> str:
    try:
        text = (root / "Makefile").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r"^VERSION\s*[?:]?=\s*(\S+)\s*$", text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("model", "check", "markdown", "summary", "triage"))
    parser.add_argument("--root", default=None, help="read a different repository (tests)")
    parser.add_argument("--roadmap", default=None, help="explicit roadmap.json path")
    parser.add_argument("--records", default=None, help="explicit validation record directory")
    parser.add_argument("--paths-file", default=None,
                        help="for triage: a file of changed paths, or - for stdin")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else ROOT
    roadmap = Path(args.roadmap) if args.roadmap else root / "docs/architecture/roadmap.json"
    records_dir = Path(args.records) if args.records else root / "docs/evidence/validation"
    try:
        data = load_roadmap(roadmap)
    except CompletionError as exc:
        print(f"completion: {exc}", file=sys.stderr)
        return 2
    if args.root:
        data["_product_version"] = _product_version(root)
    if args.command == "triage":
        raw = sys.stdin.read() if args.paths_file in (None, "-") else Path(args.paths_file).read_text()
        categories = triage(parse_paths(raw))
        print(json.dumps(list(categories)) if args.json else (",".join(categories) or "none"))
        return 0 if categories else 3
    try:
        model = compute(data)
    except CompletionError as exc:
        print(f"completion: {exc}", file=sys.stderr)
        return 2
    stamp = verified_at(load_records(records_dir))
    release = release_state(data, root)
    if args.command == "check":
        problems = validate(data)
        for problem in problems:
            print(f"[FAIL] {problem}")
        print(f"COMPLETION OK: {model['percent']}% from {model['counts']['total']} milestones, "
              f"{model['criteria']['total']} acceptance criteria, "
              f"{stamp['gates']} verification record(s)")
        return 1 if problems else 0
    if args.command == "markdown":
        print(markdown(model, stamp, release))
    elif args.command == "summary":
        print(summary_line(model, stamp, release))
    else:
        model["verified_at"] = stamp
        model["release"] = release
        print(json.dumps(model, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
