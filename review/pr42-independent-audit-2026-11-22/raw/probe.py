#!/usr/bin/env python3
"""Independent, command-level re-verification of F-1..F-8 plus new challenges against PR #42.

Run once per tree:  AUDIT_TREE=<dir> AUDIT_LABEL=pr41|pr42 python3 probe.py

Every check invokes the *public interface* — `python3 tools/dashboard_reconcile.py …` exactly as
AGENTS.md tells an operator to run it, and `make -s dashboard-reconcile` where a Make target covers
the same question. No helper or module function is ever called directly, because a test that asserts
a helper's output proves nothing about the command. Each repository is a real Git history (bare
origin + clone, real merges, real pushes, real forgeries, real `git rm`), and the two gates the tool
verifies are the real verifiers running over a real copy of the tree — so a scenario that expects a
red gate makes one actually go red.

`expect` is what each PR claims the tool does; `ideal` is what it must do. Verdicts come out as
`fixed` / `not fixed` / `fixed but misattributed` rather than a pass count. Each result records exit
code, whether the working tree changed, whether the receipt changed, and the reproduction command.
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
CLI = "python3 tools/dashboard_reconcile.py --repo <work>"


# ------------------------------------------------------------------ primitives
def run_read(r):
    return r.run()


def run_json(r):
    code, out = r.run("--json")
    try:
        payload = json.loads(out)
        return code, "PARSE-OK " + json.dumps({k: payload[k] for k in sorted(payload)
                                               if k in ("outstanding", "exempt_generated_only",
                                                         "merges_cleared_by_reviewed_content",
                                                         "recorded_in_receipt",
                                                         "generated_output_agreement")})
    except json.JSONDecodeError as exc:
        return code, f"PARSE-FAIL {exc}"


def run_record(r):
    return r.record()


def run_make(r):
    done = subprocess.run(("make", "-s", "dashboard-reconcile"), cwd=str(r.work),
                          capture_output=True, text=True, env=lib.ENV)
    return done.returncode, done.stdout + done.stderr


def payload_of(code_out_receipt):
    return code_out_receipt[2]


def probe(name, build, command, expect, ideal, note=""):
    import re
    repo = Repo(re.sub(r'[^A-Za-z0-9]+', '-', name)[:20].strip('-').lower())
    verdict = {}
    try:
        ctx = build(repo) or {}
        if isinstance(ctx, (tuple, list)):
            ctx = next((x for x in reversed(ctx) if isinstance(x, dict)), {})
        repo.push()
        before = repo.fingerprint()
        receipt_before = json.dumps(repo.receipt_json(), sort_keys=True)
        code, out = command(repo)
        after = repo.fingerprint()
        receipt_after = json.dumps(repo.receipt_json(), sort_keys=True)
        exp_code, exp_needle = expect[LABEL]
        code_ok = (code != 0) if exp_code is None else (code == exp_code)
        text_ok = all((n not in out) if n.startswith("!") else (n in out)
                      for n in ([exp_needle] if exp_needle else []))
        ideal_code, ideal_needle = ideal[LABEL]
        ideal_ok = ((code != 0) if ideal_code is None else (code == ideal_code)) and (
            ideal_needle is None or ideal_needle in out)
        RESULTS.append({
            "name": name, "tree": LABEL, "command": ctx.get("command", CLI),
            "expected_exit": exp_code, "exit": code, "needle": exp_needle,
            "matches_expected": code_ok and text_ok, "matches_ideal": ideal_ok,
            "tree_mutated": before != after, "git_status": repo.status(),
            "receipt_changed": receipt_before != receipt_after,
            "diagnostic": " ⏎ ".join(l.strip() for l in out.splitlines() if l.strip())[:420],
            "note": note,
        })
    finally:
        for path in (repo.work / "docs/evidence",):
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
        repo.cleanup()


# ------------------------------------------------------------------ builders
def bootstrapped(repo, first="c1: the tree the dashboard reads from"):
    repo.commit(first)
    repo.push()
    repo.bootstrap(inspect=[sha(repo.work, "HEAD")])
    g(repo.work, "add", "-A")
    g(repo.work, "commit", "-q", "-m", "record the reconciliation baseline")
    repo.push()
    return sha(repo.work, "HEAD")


def forge(repo, *, boundary=None, extra_named=(), summary="hand-written receipt", regenerate=False):
    """Write a receipt by hand — the way `--record` would not allow — and commit it."""
    if regenerate:
        subprocess.run((sys.executable, "tools/presentation.py", "sync"), cwd=str(repo.work),
                       capture_output=True, text=True, env=lib.ENV)
        g(repo.work, "add", "-A")
        g(repo.work, "commit", "-q", "-m", "docs: regenerate the figure from its data")
    previous = repo.receipt_json() or {}
    named = set(previous.get("inspected_commits") or []) | set(extra_named)
    payload = dict(previous,
                   schema="pc5.dashboard-reconciliation/1",
                   repository=previous.get("repository"),
                   target_ref="origin/main",
                   reconciled_at=boundary or sha(repo.work, "HEAD"),
                   inspected_commits=sorted(named),
                   summary=summary, complete=True,
                   verification=[{"label": "presentation figures and README blocks are current",
                                  "exit_code": 0},
                                 {"label": "documentation links, anchors and commands resolve",
                                  "exit_code": 0}])
    if payload.get("repository") is None:
        payload.pop("repository")
    tip = repo.write_receipt(payload, "docs: the receipt that names the commit that wrote it")
    repo.push()
    return tip


def break_figure(repo):
    fig = repo.work / "docs/assets/pc5-progress-matrix.svg"
    fig.write_text(fig.read_text(encoding="utf-8").replace(
        "</svg>", "<!-- hand-written, not generated -->\n</svg>", 1), encoding="utf-8")
    return repo.commit("docs: a hand-written figure")


def driver_change(repo, label="milestone state moves"):
    repo.touch("docs/architecture/roadmap.json", label)
    return repo.commit("feat: a milestone closes, the figure is not regenerated")


def noop_merge(repo):
    tree = sh("git", "rev-parse", "HEAD^{tree}", cwd=repo.work)
    parents = sh("git", "rev-list", "--parents", "-n1", "HEAD", cwd=repo.work).split()
    merge = sh("git", "commit-tree", tree, "-p", parents[0], "-p", parents[1],
               "-m", "Merge a branch that had nothing main did not already have", cwd=repo.work).strip()
    sh("git", "update-ref", "refs/heads/main", merge, cwd=repo.work)
    repo.push()
    return merge



def build_pr_flow(r):
    bootstrapped(r)
    r.branch("pr/42")
    driver_change(r, "milestone moves on the branch")
    work = sha(r.work, "HEAD")
    # the honest flow: regenerate, so the gate the receipt will vouch for actually passes
    subprocess.run((sys.executable, "tools/presentation.py", "sync"), cwd=str(r.work),
                   capture_output=True, text=True, env=lib.ENV)
    g(r.work, "add", "-A")
    g(r.work, "commit", "-q", "-m", "docs: regenerate the figure for the branch change")
    r.record(extra=("--here",))
    g(r.work, "add", "-A")
    g(r.work, "commit", "-q", "-m", "record on the branch")
    r.push_branch("pr/42")
    r.checkout("main")
    subprocess.run(("git", "merge", "-q", "--no-ff", "pr/42", "-m", "Merge pull request 42"),
                   cwd=str(r.work), capture_output=True, text=True, env=lib.ENV)
    r.push()
    return {"tip": work}



def build_cycles(r):
    bootstrapped(r)
    for i in range(3):
        r.branch(f"f{i}")
        driver_change(r, f"cycle {i}")
        r.push_branch(f"f{i}")
        r.checkout("main")
        subprocess.run(("git", "merge", "-q", "--no-ff", f"f{i}", "-m", f"Merge f{i}"),
                       cwd=str(r.work), capture_output=True, text=True, env=lib.ENV)
        r.push()
        subprocess.run((sys.executable, "tools/presentation.py", "sync"), cwd=str(r.work),
                       capture_output=True, text=True, env=lib.ENV)
        g(r.work, "add", "-A")
        g(r.work, "commit", "-q", "-m", f"docs: regenerate for cycle {i}")   # step 4 before step 6
        r.push()
        code, _ = r.record()
        if code == 0:
            g(r.work, "add", "-A")
            g(r.work, "commit", "-q", "-m", f"record cycle {i}")
            r.push()
    return {"tip": sha(r.work, "HEAD")}



def build_side_commit(r):
    bootstrapped(r)
    r.branch("side")
    driver_change(r, "a change that only exists on the side branch")
    r.push_branch("side")
    r.checkout("main")
    subprocess.run(("git", "merge", "-q", "--no-ff", "side", "-m", "Merge side"),
                   cwd=str(r.work), capture_output=True, text=True, env=lib.ENV)
    r.push()
    return {"tip": sha(r.work, "HEAD")}



def certifying_receipt(repo, *, regenerate):
    """Land a receipt by hand, in the same push as the change it vouches for.

    Git cannot express "a commit that names its own SHA" — a commit hash covers the receipt, so
    self-naming is arithmetically impossible. The constructible forgery is the one that matters:
    commit X (real work), then commit R (the receipt) whose `reconciled_at` is X and whose
    `inspected_commits` names X. The range R then contains only R, which is receipt-only.
    """
    bootstrapped(repo)
    driver_change(repo)
    work = sha(repo.work, "HEAD")
    if regenerate:
        subprocess.run((sys.executable, "tools/presentation.py", "sync"), cwd=str(repo.work),
                       capture_output=True, text=True, env=lib.ENV)
        g(repo.work, "add", "-A")
        g(repo.work, "commit", "-q", "-m", "docs: regenerate the figure so both gates pass")
    else:
        break_figure(repo)
    forge(repo, boundary=work, extra_named=[work],
          summary="hand-written: names the change below the boundary, no --record run")
    return {"tip": work}


def shallow(r):
    dest = r.root / "shallow"
    subprocess.run(("git", "clone", "-q", "--depth", "1", f"file://{r.origin}", str(dest)),
                   capture_output=True, text=True, env=lib.ENV, check=False)
    r.run = (lambda *a, **k: (lambda d: (d.returncode, d.stdout + d.stderr))(
        subprocess.run([sys.executable, str(dest / "tools/dashboard_reconcile.py"), "--repo", str(dest),
                        *[str(x) for x in a]], capture_output=True, text=True, env=lib.ENV,
                       cwd=str(dest))))
    return str(dest)


# ------------------------------------------------------------------ F-1 .. F-8
probe("F-1  forged receipt in the same push, figure left stale: read",
      lambda r: certifying_receipt(r, regenerate=False),
      run_read,
      expect={"pr41": (0, "CURRENT"), "pr42": (1, "OUTSTANDING")},
      ideal={"pr41": (1, None), "pr42": (1, None)},
      note="this is the case #42 closes: the only thing holding the answer up is the exemption, "
           "and #42 tests the premise before it will say CURRENT")

probe("F-1  forged receipt in the same push, figure regenerated to match: read",
      lambda r: certifying_receipt(r, regenerate=True),
      run_read,
      expect={"pr41": (0, "CURRENT"), "pr42": (0, "CURRENT")},
      ideal={"pr41": (0, None), "pr42": (0, None)},
      note="the residual, stated exactly: a self-consistent forged receipt (work committed, figures "
           "regenerated so both gates genuinely pass, boundary moved to the work) reads CURRENT on "
           "both trees. No code can see an inspection that did not happen: the boundary is a claim "
           "about review, not about files. Recorded so #42's F-1 wording is not over-read")

probe("F-1  same forgery, JSON verdict and text agree",
      lambda r: certifying_receipt(r, regenerate=False),
      run_json,
      expect={"pr41": (0, '"outstanding": []'), "pr42": (1, "generated_output_agreement")},
      ideal={"pr41": (1, None), "pr42": (1, "generated_output_agreement")},
      note="a machine reader must get the same verdict the human does, including the reason field")

probe("F-2  number typed into a generated README block, then --record",
      lambda r: (bootstrapped(r),
                 r.work.joinpath("README.md").write_text(
                     (r.work / "README.md").read_text(encoding="utf-8")
                     .replace("| Complete | 9 |", "| Complete | 10 |", 1), encoding="utf-8"),
                 r.commit("docs: a hand-written number inside a generated block"), {"tip": None}),
      run_record,
      expect={"pr41": (0, "nothing to record"), "pr42": (2, "refusing to record")},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="verification used to sit below the early return, so the gates were never run on this path")

probe("F-3  committed truncated figure, through the Make target",
      lambda r: (bootstrapped(r), break_figure(r), {"tip": None}),
      run_make,
      expect={"pr41": (0, None), "pr42": (1, None)},
      ideal={"pr41": (1, None), "pr42": (1, None)},
      note="`make dashboard-reconcile` is the operator's read command; a damaged asset must not read clean")

probe("F-3  committed deleted figure, read path",
      lambda r: (bootstrapped(r),
                 sh("git", "rm", "-q", "docs/assets/pc5-progress-matrix.svg", cwd=r.work),
                 r.commit("docs: delete the figure"), {"tip": None}),
      run_read,
      expect={"pr41": (0, "CURRENT"), "pr42": (1, "OUTSTANDING")},
      ideal={"pr41": (1, None), "pr42": (1, None)})

def named_then_stale(repo):
    """Driver work that *was* recorded, then a figure damaged afterwards.

    Only a clearable range can show whether the tool blames the wrong commit: here every commit in
    the range is either named in the receipt or confined to generated output, so `outstanding` must
    stay empty while the stale asset is reported as its own finding.
    """
    bootstrapped(repo)
    driver_change(repo)
    work = sha(repo.work, "HEAD")
    subprocess.run((sys.executable, "tools/presentation.py", "sync"), cwd=str(repo.work),
                   capture_output=True, text=True, env=lib.ENV)
    g(repo.work, "add", "-A")
    g(repo.work, "commit", "-q", "-m", "docs: regenerate")
    repo.push()                       # the target is origin/main: it must contain the work
    repo.record()
    g(repo.work, "add", "-A")
    g(repo.work, "commit", "-q", "-m", "record the work")
    repo.push()
    break_figure(repo)
    repo.push()
    return {"tip": work}


probe("F-3  a stale asset must not be blamed on commits that are properly cleared",
      named_then_stale,
      lambda r: r.run("--json"),
      expect={"pr41": (0, '"outstanding": []'), "pr42": (1, '"outstanding": []')},
      ideal={"pr41": (1, '"outstanding": []'), "pr42": (1, '"outstanding": []')},
      note="#42 has to report the output as its own finding: the receipt-named commit stays cleared, "
           "and nobody is sent to inspect a commit that changed only generated output")

probe("F-4  no-op empty two-parent merge",
      lambda r: (bootstrapped(r), noop_merge(r), {"tip": None}),
      run_read,
      expect={"pr41": (0, "CURRENT"), "pr42": (1, "no-op merge")},
      ideal={"pr41": (1, None), "pr42": (1, None)},
      note="AGENTS.md step 2: a no-op merge is still a merge, recorded rather than skipped")

probe("F-5  --record --boundary --inspect without --bootstrap",
      lambda r: (bootstrapped(r), {"tip": None}),
      lambda r: r.run("--record", "--boundary", "0" * 40, "--inspect", "0" * 40),
      expect={"pr41": (0, "nothing to record"), "pr42": (2, "--bootstrap")},
      ideal={"pr41": (2, None), "pr42": (2, None)})

probe("F-5  --note without --record",
      lambda r: (bootstrapped(r), {"tip": None}),
      lambda r: r.run("--note", "a summary nobody asked for"),
      expect={"pr41": (0, None), "pr42": (2, "--note writes into a receipt")},
      ideal={"pr41": (2, None), "pr42": (2, None)})

probe("F-5  --boundary/--inspect given to a read run (no --record at all)",
      lambda r: (bootstrapped(r), {"tip": None}),
      lambda r: r.run("--boundary", "0" * 40),
      expect={"pr41": (0, None), "pr42": (2, "--bootstrap")},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="#42 guards the --record path; this asks the same question of the read path")

probe("F-5  --bootstrap without --record",
      lambda r: (bootstrapped(r), {"tip": None}),
      lambda r: r.run("--bootstrap", "--boundary", sha(r.work, "HEAD")),
      expect={"pr41": (0, None), "pr42": (0, None)},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="--bootstrap writes nothing without --record, and says nothing: the same class as --note")

probe("F-6  --target accepts an explicitly named rev (short SHA)",
      lambda r: (bootstrapped(r), {"tip": None}),
      lambda r: r.run("--target", sha(r.work, "HEAD")[:9]),
      expect={"pr41": (0, None), "pr42": (0, None)},
      ideal={"pr41": (0, None), "pr42": (0, None)},
      note="the prior report's claim that --target cannot name a commit is the inaccuracy being corrected")

probe("F-6  --target HEAD, and a nonexistent ref",
      lambda r: (bootstrapped(r), {"tip": None}),
      lambda r: (lambda a, b: (a[0], f"HEAD:exit={a[0]} / missing:exit={b[0]} {b[1][:60]}"))(
          r.run("--target", "HEAD"), r.run("--target", "refs/heads/nope")),
      expect={"pr41": (0, "missing:exit=2"), "pr42": (0, "missing:exit=2")},
      ideal={"pr41": (0, "missing:exit=2"), "pr42": (0, "missing:exit=2")})

probe("F-7  does the report ever say a commit was named in the receipt?",
      lambda r: (build_pr_flow(r), {"tip": None}),
      lambda r: r.run("--json"),
      expect={"pr41": (0, '"merges_cleared_by_reviewed_content": []'),
              "pr42": (0, '"merges_cleared_by_reviewed_content": [\n    "')},
      ideal={"pr41": (0, None), "pr42": (0, None)},
      note="#41's two statistics were structurally always empty; measured here on a real PR-shaped flow")


probe("F-8  --json on a repository with no receipt",
      lambda r: (r.commit("c0: the only commit, no receipt anywhere"), {"tip": None}),
      run_json,
      expect={"pr41": (1, "PARSE-FAIL"), "pr42": (1, "PARSE-OK")},
      ideal={"pr41": (1, "PARSE-OK"), "pr42": (1, "PARSE-OK")},
      note="a scripted check has to be able to read the refusal")

# ------------------------------------------------------------------ new challenges
def asset_only_commit(repo, name="docs/assets/pc5-review-note.txt"):
    """A commit confined to generated output: the only kind the exemption clears by path."""
    (repo.work / name).parent.mkdir(parents=True, exist_ok=True)
    (repo.work / name).write_text("written by hand\n", encoding="utf-8")
    repo.commit("docs: a file dropped into the generated-output directory")
    repo.push()
    return sha(repo.work, "HEAD")


probe("NEW-1  a verifier cannot be executed, and the exemption would clear the range",
      lambda r: (bootstrapped(r), asset_only_commit(r),
                 os.remove(r.work / "tools/docs_check.py"), {"tip": None}),
      run_read,
      expect={"pr41": (0, "CURRENT"), "pr42": (1, None)},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="the premise cannot be tested at all when the verifier is gone: that is *cannot answer* "
           "(exit 2), the same code the tool uses for an unreadable history — reporting a verdict "
           "here, in either direction, is the class of error this tool exists to remove")

probe("NEW-2  docs-check-only failure: is the diagnostic attributed to the right thing?",
      lambda r: (bootstrapped(r), asset_only_commit(r),
                 (r.work / "docs/BROKEN-LINK.md").write_text("see [nowhere](does-not-exist.md)\n",
                                                             encoding="utf-8"), {"tip": None}),
      run_read,
      expect={"pr41": (0, "CURRENT"), "pr42": (1, "documentation links")},
      ideal={"pr41": (1, None), "pr42": (1, "documentation links")},
      note="the failure is real but is not about generated output; #42's wording claims it is — "
           "misattribution in a tool whose whole job is precise attribution")

probe("NEW-3  interrupted recording: the receipt directory is read-only",
      lambda r: (bootstrapped(r), driver_change(r),
                 os.chmod(r.work / "docs/evidence", 0o500), {"tip": None}),
      run_record,
      expect={"pr41": (None, None), "pr42": (None, None)},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="a write failure must be an honest error: not a traceback, and not the success line")

probe("NEW-4  three merge→record→land cycles in a row",
      lambda r: (build_cycles(r), {"tip": None}),
      lambda r: (lambda code, out: (code, out + f" STATUS={r.status()}"))(*run_read(r)),
      expect={"pr41": (0, "CURRENT !OUTSTANDING"), "pr42": (0, "CURRENT !OUTSTANDING")},
      ideal={"pr41": (0, "CURRENT"), "pr42": (0, "CURRENT")},
      note="repeated reconciliation must not accumulate debt, must not leave the tree dirty")


probe("NEW-5  a merge whose side commits are not on the first-parent line",
      lambda r: (build_side_commit(r), {"tip": None}),
      lambda r: r.run("--json"),
      expect={"pr41": (1, '"outstanding": []') if False else (1, "OUTSTANDING"),
              "pr42": (1, "OUTSTANDING")},
      ideal={"pr41": (1, None), "pr42": (1, None)},
      note="the range is first-parent, so a driver change that only a side commit made must still be "
           "reached through the merge's own diff")


probe("NEW-6  regenerated-but-uncommitted figures do not block the receipt",
      lambda r: (bootstrapped(r), driver_change(r),
                 subprocess.run((sys.executable, "tools/presentation.py", "sync"), cwd=str(r.work),
                                capture_output=True, text=True, env=lib.ENV),
                 {"tip": None}),
      lambda r, *a: (lambda res: (res[0], res[1] + f" STATUS={r.status()}"))(r.record()),
      expect={"pr41": (0, None), "pr42": (0, None)},
      ideal={"pr41": (0, None), "pr42": (0, None)},
      note="step 6 writes the receipt and the figures together: a dirty *figure* is allowed, a dirty "
           "*source* is refused. The command must still not commit anything itself.")

probe("NEW-7  shallow clone cannot answer",
      lambda r: (bootstrapped(r), driver_change(r), shallow(r)),
      run_read,
      expect={"pr41": (2, None), "pr42": (2, None)},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="exit 1 must never be reachable from a history the tool could not read")


probe("NEW-8  receipt names a commit that has not landed",
      lambda r: (bootstrapped(r), r.branch("unlanded"), driver_change(r), future := sha(r.work, "HEAD"),
                 r.checkout("main"), forge(r, extra_named=[future]), {"tip": future}),
      run_read,
      expect={"pr41": (2, "not an ancestor"), "pr42": (2, "not an ancestor")},
      ideal={"pr41": (2, None), "pr42": (2, None)},
      note="a receipt must not pre-approve a merge that has not happened")



path = Path(f"/tmp/audit42/probe-results-{LABEL}.json")
path.write_text(json.dumps(RESULTS, indent=2))
print(f"{LABEL}: {len(RESULTS)} probes, tree={TREE}")
for rec in RESULTS:
    if rec["matches_expected"] and rec["matches_ideal"]:
        flag = "as-claimed + ideal"
    elif rec["matches_expected"]:
        flag = "as-claimed, NOT ideal"
    elif rec["matches_ideal"]:
        flag = "ideal but not what was claimed"
    else:
        flag = "DIVERGES FROM BOTH"
    print(f"  {rec['name'][:58]:60} exit={rec['exit']} tree_mut={int(rec['tree_mutated'])} "
          f"receipt_chg={int(rec['receipt_changed'])}  {flag}")
print(f"written {path}")
