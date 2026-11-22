#!/usr/bin/env python3
"""Second batch: flag-combination and history gaps the first batch did not cover.

Same rules as probe.py: public commands only, real Git histories, exit codes and tree/receipt
mutation recorded per case. Expectations are written from the documented behaviour, and `ideal` is
what the tool must do; a case where #41 and #42 agree but both differ from ideal is a *new* finding.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/audit42")
import lib
from lib import Repo, RECEIPT, sha, g, sh

TREE = Path(os.environ["AUDIT_TREE"])
LABEL = os.environ["AUDIT_LABEL"]
lib.REAL = TREE
lib.TOOL = str(TREE / "tools" / "dashboard_reconcile.py")
RESULTS = []


def receipt_bytes(repo):
    """The receipt as committed on origin/main, compared as bytes — a truncated receipt is the
    subject of one probe, so the harness must not be able to parse it."""
    import subprocess as sp
    done = sp.run(("git", "show", f"origin/main:{RECEIPT}"), cwd=str(repo.work),
                  capture_output=True, text=True, env=lib.ENV)
    return done.stdout if done.returncode == 0 else "<absent>"


def probe(name, build, command, expect, ideal, note=""):
    import re
    repo = Repo(re.sub(r"[^A-Za-z0-9]+", "-", name)[:20].strip("-").lower())
    try:
        ctx = build(repo) or {}
        if isinstance(ctx, (tuple, list)):
            ctx = next((x for x in reversed(ctx) if isinstance(x, dict)), {})
        try:
            repo.push()
        except Exception:
            pass                       # a probe that removes `origin` is testing exactly that
        before = repo.fingerprint()
        receipt_before = receipt_bytes(repo)
        code, out = command(repo)
        after = repo.fingerprint()
        receipt_after = receipt_bytes(repo)
        exp_code, exp_needle = expect[LABEL]
        code_ok = (code != 0) if exp_code is None else (code == exp_code)
        text_ok = all((n not in out) if n.startswith("!") else (n in out)
                      for n in ([exp_needle] if exp_needle else []))
        ideal_code, ideal_needle = ideal[LABEL]
        ideal_ok = ((code != 0) if ideal_code is None else (code == ideal_code)) and (
            ideal_needle is None or ideal_needle in out)
        RESULTS.append({"name": name, "tree": LABEL, "exit": code,
                        "expected_exit": exp_code, "needle": exp_needle,
                        "matches_expected": code_ok and text_ok, "matches_ideal": ideal_ok,
                        "tree_mutated": before != after, "git_status": repo.status(),
                        "receipt_changed": receipt_before != receipt_after,
                        "diagnostic": " ⏎ ".join(l.strip() for l in out.splitlines() if l.strip())[:400],
                        "note": note})
    finally:
        repo.cleanup()


def regenerate(r):
    """AGENTS.md step 4 before step 6, so a refusal is not mistaken for the thing under test."""
    subprocess.run((sys.executable, "tools/presentation.py", "sync"), cwd=str(r.work),
                   capture_output=True, text=True, env=lib.ENV, check=True)
    g(r.work, "add", "-A")
    r.commit("docs: regenerate the figures")
    r.push()


def boot(r):
    r.commit("c1: baseline")
    r.push()
    r.bootstrap(inspect=[sha(r.work, "HEAD")])
    g(r.work, "add", "-A")
    g(r.work, "commit", "-q", "-m", "record the baseline")
    r.push()
    return sha(r.work, "HEAD")


def driver(r, msg="feat: milestone state moves"):
    r.touch("docs/architecture/roadmap.json", "state moves")
    out = r.commit(msg)
    r.push()
    return out


probe("G-1   --dry-run on a read run does nothing and says nothing",
      lambda r: (boot(r), driver(r), {}),
      lambda r: r.run("--dry-run"),
      expect={"pr41": (1, None), "pr42": (1, None)},
      ideal={"pr41": (2, "--dry-run"), "pr42": (2, "--dry-run")},
      note="--dry-run is only meaningful with --record; silently accepted here, the F-5 class again")

probe("G-2   --record --dry-run prints the receipt and writes nothing",
      lambda r: (boot(r), driver(r), regenerate(r), {}),
      lambda r: (lambda res: (res[0], res[1] + f" STATUS={r.status()}"))(r.run("--record", "--dry-run")),
      expect={"pr41": (0, "dry run"), "pr42": (0, "dry run")},
      ideal={"pr41": (0, "dry run !docs/evidence/dashboard-reconciliation.json"),
             "pr42": (0, "dry run !docs/evidence/dashboard-reconciliation.json")},
      note="the preview must not touch the tree; git status is included in the needle check")

probe("G-3   --json --record: does the write path answer in JSON?",
      lambda r: (boot(r), driver(r), {}),
      lambda r: (lambda res: (res[0], res[1]))(r.run("--record", "--json")),
      expect={"pr41": (0, None), "pr42": (0, None)},
      ideal={"pr41": (0, "PARSE"), "pr42": (0, "PARSE")},
      note="F-8's class: --json is honoured on the read path and ignored on the record path")

probe("G-4   --here and --target together: which one wins, and is it said?",
      lambda r: (boot(r), driver(r), r.branch("side"), driver(r, "feat: side work"), r.push_branch("side"),
                 r.checkout("main"), {}),
      lambda r: r.run("--here", "--target", "origin/main"),
      expect={"pr41": (0, None), "pr42": (0, None)},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="contradictory selection flags; whatever wins must not win silently")

probe("G-5   --boundary and --inspect given a short SHA",
      lambda r: (r.commit("c1: only commit"), r.push(), {}),
      lambda r: (lambda res: res)(r.run("--record", "--bootstrap", "--boundary", "abc1234",
                                        "--inspect", "abc1234")),
      expect={"pr41": (2, "40-hex"), "pr42": (2, "40-hex")},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="short SHAs must stay unacceptable, or a receipt could name an ambiguous commit")

probe("G-6   --target pointing at a tree object",
      lambda r: (boot(r), {}),
      lambda r: r.run("--target", "HEAD^{tree}"),
      expect={"pr41": (2, None), "pr42": (2, None)},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="must not be read as a commit; a TypeError traceback would be a defect")

def truncate_receipt(r):
    path = r.work / RECEIPT
    text = path.read_text(encoding="utf-8")
    path.write_text(text[:60], encoding="utf-8")      # valid prefix, not valid JSON
    r.commit("docs: truncate the receipt")
    r.push()


probe("G-7   truncated receipt on the target",
      lambda r: (boot(r), driver(r), truncate_receipt(r), {}),
      lambda r: r.run(),
      expect={"pr41": (2, None), "pr42": (2, None)},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="a receipt that cannot be parsed is cannot-answer, never an empty range")

def edit_receipt(r, mutate, message):
    payload = mutate(r.receipt_json())
    (r.work / RECEIPT).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
    r.commit(message)
    r.push()


probe("G-8   receipt from another repository",
      lambda r: (boot(r), driver(r),
                 edit_receipt(r, lambda prev: dict(prev, repository="someone/else"),
                              "docs: a receipt copied from a fork"), {}),
      lambda r: r.run(),
      expect={"pr41": (1, "NOTE the receipt vouches for"), "pr42": (1, "NOTE the receipt vouches for")},
      ideal={"pr41": (1, "NOTE"), "pr42": (1, "NOTE")},
      note="the SHA is still honoured where it resolves; the mismatch is stated, not hidden")

probe("G-9   remote gone entirely",
      lambda r: (boot(r), driver(r), sh("git", "remote", "remove", "origin", cwd=r.work), {}),
      lambda r: r.run(),
      expect={"pr41": (2, None), "pr42": (2, None)},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="no origin/main and no origin/HEAD: it must refuse, not fall back to a local branch")

probe("G-9b  same repository, but asked about the checked-out branch",
      lambda r: (boot(r), driver(r), sh("git", "remote", "remove", "origin", cwd=r.work), {}),
      lambda r: r.run("--here"),
      expect={"pr41": (1, "OUTSTANDING"), "pr42": (1, "OUTSTANDING")},
      ideal={"pr41": (1, None), "pr42": (1, None)},
      note="--here is the documented escape hatch and must still answer")

def four_driver_commits(r):
    """Four separate commits that each move a dashboard driver, then step 4, then step 6.

    The files are evidence notes under `docs/evidence/` — a driver path the reconciler must see —
    rather than synthesised milestones, because a milestone object the generator cannot fully
    consume makes the *fixture* disagree with itself and stops being a test of reconciliation.
    """
    for i in range(4):
        (r.work / f"docs/evidence/audit-note-{i}.md").write_text(f"note {i}\n", encoding="utf-8")
        r.commit(f"docs: evidence note {i}")
    r.push()
    regenerate(r)
    return sha(r.work, "HEAD")


def record_and_land(r):
    code, out = r.record()
    if code == 0:
        g(r.work, "add", "-A")
        g(r.work, "commit", "-q", "-m", "record the four")
        r.push()
    return code, out


def g10(r):
    record_code, record_out = record_and_land(r)
    read_code, read_out = r.run()
    return read_code, (f"record exit={record_code} :: {record_out.strip()[:220]} :: read: {read_out}"
                       f" :: STATUS={r.status()}")


probe("G-10  four driver commits cleared by one record",
      lambda r: (boot(r), four_driver_commits(r), {}),
      g10,
      expect={"pr41": (0, "CURRENT"), "pr42": (0, "CURRENT")},
      ideal={"pr41": (0, "4 on the first-parent line"), "pr42": (0, "4 on the first-parent line")},
      note="one record must clear a run of commits, and the report must still *count* them")

probe("G-11  docs-only merge changes no metric and is still named",
      lambda r: (boot(r), r.branch("docs"),
                 (r.work / "docs/NOTES.md").write_text("prose only\n", encoding="utf-8"),
                 r.commit("docs: prose"), r.push_branch("docs"), r.checkout("main"),
                 subprocess.run(("git", "merge", "-q", "--no-ff", "docs", "-m", "Merge docs"),
                                cwd=str(r.work), capture_output=True, text=True, env=lib.ENV),
                 r.push(), {}),
      lambda r: r.run(),
      expect={"pr41": (1, "OUTSTANDING"), "pr42": (1, "OUTSTANDING")},
      ideal={"pr41": (1, None), "pr42": (1, None)},
      note="a merge that moves no number is a successful change on main: reported, not hidden")

probe("G-12  repeated --record with nothing new is idempotent",
      lambda r: (boot(r), driver(r),
                 subprocess.run((sys.executable, "tools/presentation.py", "sync"), cwd=str(r.work),
                                capture_output=True, text=True, env=lib.ENV),
                 g(r.work, "add", "-A"), g(r.work, "commit", "-q", "-m", "docs: regenerate"),
                 r.push(), r.record(), g(r.work, "add", "-A"),
                 g(r.work, "commit", "-q", "-m", "record"), r.push(), {}),
      lambda r: (lambda a, b: (a[0], a[1] + f" SECOND exit={b[0]} identical={r.fingerprint()}"))(
          r.run(), r.run()),
      expect={"pr41": (0, "SECOND exit=0"), "pr42": (0, "SECOND exit=0")},
      ideal={"pr41": (0, "SECOND exit=0"), "pr42": (0, "SECOND exit=0")},
      note="reading twice, or recording twice, must not drift")

probe("G-13  receipt marked incomplete",
      lambda r: (boot(r), driver(r),
                 edit_receipt(r, lambda prev: dict(prev, complete=False,
                                                   incomplete_reason="generation was interrupted"),
                              "docs: mark the receipt incomplete"), {}),
      lambda r: r.run(),
      expect={"pr41": (1, "incomplete"), "pr42": (1, "incomplete")},
      ideal={"pr41": (1, None), "pr42": (1, None)},
      note="an admitted-incomplete receipt must not read as a clean one")

probe("G-14  receipt verification claims a failing gate",
      lambda r: (boot(r), driver(r),
                 edit_receipt(r, lambda prev: dict(prev, verification=[
                     {"label": "presentation figures and README blocks are current",
                      "exit_code": 1}]), "docs: a receipt admitting a red gate"), {}),
      lambda r: r.run(),
      expect={"pr41": (2, "red gate"), "pr42": (2, "red gate")},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="the receipt admits it was written over a red gate: that is an error, not a green")


path = Path(f"/tmp/audit42/probe2-results-{LABEL}.json")
path.write_text(json.dumps(RESULTS, indent=2))
print(f"{LABEL}: {len(RESULTS)} probes")
for rec in RESULTS:
    flag = ("as-claimed + ideal" if rec["matches_expected"] and rec["matches_ideal"] else
            "as-claimed, NOT ideal" if rec["matches_expected"] else
            "ideal, not as claimed" if rec["matches_ideal"] else "DIVERGES FROM BOTH")
    print(f"  {rec['name'][:58]:60} exit={rec['exit']} tree_mut={int(rec['tree_mutated'])} "
          f"receipt_chg={int(rec['receipt_changed'])}  {flag}")
print(f"written {path}")
