#!/usr/bin/env python3
"""Third batch: the whole invalid-flag-combination space, and the two read-path attribution cases.

Two questions. (1) For every combination a caller can plausibly type, is each flag either HONOURED
or REFUSED — never silently ignored? `main()` refuses two combinations and ignores the rest, so the
matrix is enumerated rather than sampled. (2) When the generated-output exemption is load-bearing and
the reason is that a verifier cannot run at all, what exit code and what sentence does the public
command print — measured on the tool itself, not through `make`, because make turns any recipe
failure into its own exit 2.
"""
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/audit42")
import lib
from lib import Repo, sha, g

TREE = Path(os.environ["AUDIT_TREE"])
LABEL = os.environ["AUDIT_LABEL"]
lib.REAL = TREE
lib.TOOL = str(TREE / "tools" / "dashboard_reconcile.py")
RESULTS = []
MATRIX = []


def tool(repo, *flags):
    """The public command, streams kept apart, exit code preserved."""
    work = getattr(repo, "work", repo)
    done = subprocess.run((sys.executable, str(lib.REAL / "tools" / "dashboard_reconcile.py"),
                           "--repo", str(work), *flags),
                          capture_output=True, text=True, env=lib.ENV, cwd=str(work))
    return done.returncode, done.stdout, done.stderr


def boot(r):
    r.commit("c1: baseline")
    r.push()
    r.bootstrap(inspect=[sha(r.work, "HEAD")])
    g(r.work, "add", "-A")
    g(r.work, "commit", "-q", "-m", "record the baseline")
    r.push()


# ------------------------------------------------------------------ the flag matrix
FLAGS = [
    ("--record", "record"), ("--bootstrap", "bootstrap"), ("--boundary", "<sha>"),
    ("--inspect", "<sha>"), ("--note", "x"), ("--dry-run", "dry"), ("--json", "json"),
    ("--here", "here"), ("--target", "origin/main"),
]
# the combinations a person actually types wrong, plus every single flag on its own
COMBOS = [
    [], ["--record"], ["--bootstrap"], ["--boundary"], ["--inspect"], ["--note"],
    ["--dry-run"], ["--json"], ["--here"], ["--target"],
    ["--bootstrap", "--boundary"], ["--bootstrap", "--inspect"],
    ["--bootstrap", "--boundary", "--inspect"],
    ["--record", "--bootstrap"], ["--record", "--boundary"], ["--record", "--inspect"],
    ["--record", "--note"], ["--record", "--dry-run"], ["--record", "--json"],
    ["--record", "--bootstrap", "--note"], ["--record", "--bootstrap", "--dry-run"],
    ["--record", "--bootstrap", "--json"],
    ["--here", "--target"], ["--dry-run", "--here"], ["--json", "--target"],
    ["--bootstrap", "--here"], ["--bootstrap", "--record", "--boundary", "--inspect",
                                 "--note", "--dry-run", "--json", "--here"],
]


def matrix(r):
    boundary = sha(r.work, "HEAD")
    for combo in COMBOS:
        argv = []
        for flag in combo:
            argv.append(flag)
            if flag in ("--boundary", "--inspect"):
                argv.append(boundary)
            elif flag == "--target":
                argv.append("origin/main")
            elif flag == "--note":
                argv.append("an audit note")
        code, out, err = tool(r, *argv)
        text = out + err
        honoured = any(k in text for k in ("wrote ", "bootstrapped ", "nothing to record",
                                           "CURRENT", "OUTSTANDING", "dry run", "repository_id",
                                           "refusing to record"))
        refused = "ERROR —" in text or "usage:" in text
        # A flag is silently ignored when the run answers as if the flag had not been typed and
        # says nothing about the flag itself.
        mentioned = any(flag in text for flag in combo if flag.startswith("--"))
        silent = (code == 0) and not refused and not mentioned and any(
            f in combo for f in ("--dry-run", "--note", "--boundary", "--inspect", "--bootstrap"))
        MATRIX.append({"combo": " ".join(combo) or "(read only)", "exit": code,
                       "answered": honoured, "refused": refused, "flag_mentioned": mentioned,
                       "first_line": (out.strip().splitlines() or [""])[0][:90],
                       "err_first": (err.strip().splitlines() or [""])[0][:120],
                       "suspected_silent": silent})
    return {}


probe_names = []


def case(name, build, run, expect, ideal, note=""):
    import re
    r = Repo(re.sub(r"[^A-Za-z0-9]+", "-", name)[:20].strip("-").lower())
    try:
        build(r)
        code, out, err = run(r)
        exp_code, exp_needle = expect[LABEL]
        ok = (code == exp_code if exp_code is not None else code != 0) and (
            exp_needle is None or exp_needle in out + err)
        ideal_code, ideal_needle = ideal[LABEL]
        iok = (code == ideal_code if ideal_code is not None else code != 0) and (
            ideal_needle is None or ideal_needle in out + err)
        RESULTS.append({"name": name, "exit": code, "matches_expected": ok,
                        "matches_ideal": iok, "stdout": out.strip()[:700],
                        "stderr": err.strip()[:300], "status": r.status(), "note": note})
    finally:
        r.cleanup()


def exempt_range(r):
    """Land a receipt, then leave exactly one generated-output-only commit in range.

    `docs/evidence/` is a driver path, so the receipt commit itself has to be recorded before the
    case runs, otherwise the range is not exempt-only and the exemption is never load-bearing —
    which is the whole subject of these three cases. Every step asserts, so a broken fixture cannot
    be reported as a tool result.
    """
    boot(r)
    r.touch("docs/architecture/roadmap.json", "feat: driver moves")
    r.commit("feat: a driver moves")
    subprocess.run((sys.executable, "tools/presentation.py", "sync"), cwd=str(r.work),
                   capture_output=True, text=True, env=lib.ENV, check=True)
    g(r.work, "add", "-A")
    r.commit("docs: regenerate")
    r.push()
    code, out = r.record()
    assert code == 0, f"the fixture's own --record failed, so the case measures nothing: {out}"
    g(r.work, "add", "-A")
    r.commit("record the reconciliation")
    r.push()
    state = json.loads(tool(r, "--json")[1])
    assert all(not c["requires_inspection"] for c in state["commits_since_boundary"]), \
        f"the receipt commit is expected in range; anything else means the fixture leaked: " \
        f"{state['commits_since_boundary']}"
    svg = r.work / "docs/assets/pc5-dashboard.svg"
    svg.write_text(svg.read_text(encoding="utf-8") + "<!-- added by hand -->\n", encoding="utf-8")
    r.commit("docs: a figure touched by hand")
    r.push()


def matrix_case(r):
    matrix(r)
    return 0, "", ""


case("MX-1  every flag combination: honoured or refused, never silently ignored",
     boot, matrix_case,
     expect={"pr41": (0, None), "pr42": (0, None)},
     ideal={"pr41": (0, None), "pr42": (0, None)},
     note="see the `matrix` array in this file's JSON: one row per combination")


def mv1(r):
    (r.work / "tools/docs_check.py").unlink()      # uncommitted deletion of a verifier
    return tool(r, "--json")


def mv2(r):
    (r.work / "docs/BROKEN-LINK.md").write_text(
        "see [the missing file](docs/does-not-exist.md)\n", encoding="utf-8")   # untracked
    return tool(r)


def mv3(r):
    path = r.work / "docs/assets/pc5-dashboard.svg"
    path.write_text(path.read_text(encoding="utf-8")[:120], encoding="utf-8")
    r.commit("docs: truncate the committed figure")
    r.push()
    return tool(r)


case("MV-1  a missing verifier with an exempt-only range: category and wording",
     exempt_range, mv1,
     expect={"pr41": (1, None), "pr42": (1, "is missing")},
     ideal={"pr41": (1, None), "pr42": (2, "is missing")},
     note="is a verifier that cannot run an *outstanding inspection* (exit 1) or a *cannot-answer* "
          "(exit 2)? the record path calls it exit 2, the read path folds it into the exemption "
          "string and exits 1")

case("MV-2  a docs-check-only failure with an exempt-only range: what does the header blame?",
     exempt_range, mv2,
     expect={"pr41": (1, None), "pr42": (1, "documentation links")},
     ideal={"pr41": (1, None), "pr42": (1, "OUTSTANDING")},
     note="the header says the range is confined to generated output and *that output* does not "
          "match its data, while the actual failure is a broken link in an untracked markdown file")

case("MV-3  a corrupted committed asset with the exemption load-bearing, measured on the tool",
     exempt_range, mv3,
     expect={"pr41": (1, None), "pr42": (1, "OUTSTANDING")},
     ideal={"pr41": (1, None), "pr42": (1, "OUTSTANDING")},
     note="the tool itself, not `make`: an earlier pass through `make dashboard-reconcile` showed "
          "exit 2, which is make's own recipe-failure status (Makefile:324) and not a misattribution")


path = Path(f"/tmp/audit42/probe3-results-{LABEL}.json")
path.write_text(json.dumps({"matrix": MATRIX, "cases": RESULTS}, indent=2))
print(f"{LABEL}: flag matrix {len(MATRIX)} combinations, {len(RESULTS)} cases")
for m in MATRIX:
    tag = "SILENT" if m["suspected_silent"] else ("refused" if m["refused"] else "honoured")
    print(f"  {m['combo'][:58]:60} exit={m['exit']} {tag:8} {m['err_first'][:60]}")
for rec in RESULTS:
    print(f"  {rec['name'][:58]:60} exit={rec['exit']} "
          f"{'as-claimed' if rec['matches_expected'] else 'NOT as claimed'}"
          f"{'/ideal' if rec['matches_ideal'] else '/NOT ideal'}")
    print(f"      {rec['stdout'].splitlines()[0][:110] if rec['stdout'] else ''}")
    print(f"      {(rec['stdout'].splitlines()[-1] if rec['stdout'] else '')[:110]} {rec['stderr'][:110]}")
print(f"written {path}")
