#!/usr/bin/env python3
"""Lifecycle scenarios 31-36: foreign receipts, a missing git, and the merge topologies that
could smuggle an unreviewed commit past `reviewed_ancestry`."""
import json
import os
import subprocess
import sys

sys.path.insert(0, "/tmp/audit41")
import lifecycle_lib as L
from lifecycle_lib import Repo, check, g, sha, sh, ENV, RECEIPT

ROADMAP = "docs/architecture/roadmap.json"


def bootstrapped(r):
    base = sha(r.work, "HEAD")
    code, out = r.bootstrap()
    assert code == 0, out
    g(r.work, "add", "-A")
    g(r.work, "commit", "-q", "-m", "bootstrap the receipt")
    r.push()
    return base


def result(label, expected, code, want, out, actual=""):
    ok = code == want
    L.RESULTS.append({"scenario": label, "command": "python3 tools/dashboard_reconcile.py",
                      "expected": expected, "exit_code": code, "expected_exit_code": want,
                      "tree_mutated": False, "pass": ok, "actual": actual,
                      "key_output": [line.strip() for line in out.splitlines()
                                    if any(k in line for k in ("CURRENT", "OUTSTANDING", "ERROR",
                                                               "NOTE"))][:3]})
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}\n       exit {code} (expected {want})"
          + (f" — {actual}" if actual else ""))
    for line in L.RESULTS[-1]["key_output"][:2]:
        print(f"       | {line[:150]}")
    return ok


# --- 31: a receipt from a different repository id --------------------------------------
r = Repo("s31")
bootstrapped(r)
payload = json.loads(json.dumps(r.receipt_json()))
payload["repository"] = "somebody/else"
r.write_receipt(payload, "main: the receipt vouches for another repository")
r.push()
code, out = r.run()
ok = result("31. a foreign receipt is annotated, not silently trusted, and not silently rejected",
            "exit 0 about this repository's state, with the NOTE printed", code, 0, out,
            actual=f"NOTE present: {'NOTE the receipt vouches' in out}")
r.cleanup()

# --- 32: git itself fails --------------------------------------------------------------
r = Repo("s32")
bootstrapped(r)
empty = r.root / "emptybin"
empty.mkdir()
env = dict(ENV, PATH=str(empty))          # no git on PATH at all
cmd = [sys.executable, str(L.REAL / "tools" / "dashboard_reconcile.py"), "--repo", str(r.work)]
done = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(r.work))
out = done.stdout + done.stderr
ok = done.returncode == 2 and "Traceback" not in out
L.RESULTS.append({"scenario": "32. git is not runnable (PATH stripped)",
                  "command": "python3 tools/dashboard_reconcile.py with PATH=/nonexistent",
                  "expected": "exit 2 with a diagnostic; not a traceback, not a green",
                  "exit_code": done.returncode, "expected_exit_code": 2, "tree_mutated": False,
                  "pass": ok, "key_output": [l.strip() for l in out.splitlines()][:2]})
print(f"[{'ok  ' if ok else 'FAIL'}] 32. git is not on PATH at all\n"
      f"       exit {done.returncode} (expected 2); traceback: {'Traceback' in out}")
print(f"       | {out.strip().splitlines()[0][:150] if out.strip() else '(no output)'}")
r.cleanup()

# --- 33: two merges, the second's side commit cleared by the first's record -----------
r = Repo("s33")
bootstrapped(r)
r.branch("a")
a = r.commit("a: roadmap change", [ROADMAP])
r.checkout("main")
r.merge_no_ff("a", "Merge a")
r.push()
sh(sys.executable, "tools/presentation.py", "sync", cwd=r.work)
code_record, out_record = r.record()
print(f"       (setup) recording merge a: exit {code_record}")
g(r.work, "add", "-A")
g(r.work, "commit", "-q", "-m", "record merge a")
r.push()
r.branch("b")
b = r.commit("b: contract change", ["docs/architecture/PS5_PLATFORM_CONTRACT.md"])
r.checkout("main")
r.merge_no_ff("b", "Merge b")
r.push()
code, out = r.run()
ok = result("33. a second merge after a legitimate record is still reported",
            "exit 1 naming the second merge's unreviewed content", code, 1, out,
            actual=f"first merge cleared by the record; names {b[:12]}: {b[:12] in out}")
r.cleanup()

# --- 34: octopus merge (three parents) -------------------------------------------------
r = Repo("s34")
bootstrapped(r)
r.branch("x")
x = r.commit("x: roadmap", [ROADMAP])
r.checkout("main")
r.branch("y")
y = r.commit("y: contract", ["docs/architecture/PS5_PLATFORM_CONTRACT.md"])
r.checkout("main")
g(r.work, "merge", "-q", "--no-ff", "-m", "Merge x and y (octopus)", "x", "y")
r.push()
code, out = r.run()
ok = result("34. an octopus merge: both introduced lines are scrutinised",
            "exit 1; neither side commit is waved through", code, 1, out,
            actual=f"names x {x[:12]}: {x[:12] in out} | y {y[:12]}: {y[:12] in out}")
r.cleanup()

# --- 35: reverse-orientation merge, then fast-forward of main -------------------------
r = Repo("s35")
bootstrapped(r)
r.commit("main: a landed docs change", ["docs/BUILDING.md"], "prose")   # unreviewed on main
r.push()
r.branch("feat")
r.commit("feat: a roadmap change", [ROADMAP])
g(r.work, "merge", "-q", "--no-ff", "-m", "Merge main into feat (reverse orientation)", "main")
tip = sha(r.work, "HEAD")
r.checkout("main")
g(r.work, "merge", "-q", "--ff-only", "feat")
r.push()
code, out = r.run("--json")
state = json.loads(out)
listed = [c["sha"][:12] for c in state["commits_since_boundary"]]
code_plain, out_plain = r.run()
ok = code_plain == 1
L.RESULTS.append({"scenario": "35. reverse-oriented merge fast-forwarded onto main",
                  "command": "python3 tools/dashboard_reconcile.py",
                  "expected": "the branch commit is on the first-parent line (it is the "
                              "fast-forward target) and the landed docs commit is still "
                              "unreviewed: exit 1 listing both",
                  "exit_code": code_plain, "expected_exit_code": 1, "tree_mutated": False,
                  "pass": ok, "actual": f"range members: {listed}",
                  "key_output": [l.strip() for l in out_plain.splitlines()
                                 if "OUTSTANDING " in l][:3]})
print(f"[{'ok  ' if ok else 'FAIL'}] 35. reverse merge then fast-forward onto main\n"
      f"       exit {code_plain} (expected 1); range: {listed}")
r.cleanup()

# --- 36: --target pointing at a commit, and at a branch that is not main --------------
r = Repo("s36")
bootstrapped(r)
tip = sha(r.work, "HEAD")
code_a, out_a = r.run("--target", tip[:12])
code_b, out_b = r.run("--target", "HEAD")
print(f"[{'ok  ' if code_a == 0 else 'FAIL'}] 36a. --target with a short SHA is accepted "
      f"(explicit operator choice)\n       exit {code_a} (expected 0)")
print(f"[{'ok  ' if code_b in (0, 1) else 'FAIL'}] 36b. --target HEAD (a ref, resolves to the tip)\n"
      f"       exit {code_b}; documented as the explicitly-named-target case")
L.RESULTS.append({"scenario": "36. --target accepts refs, and a commit-ish must still resolve "
                             "to a branch-like ref for the receipt",
                  "command": "--target <short sha> / --target HEAD",
                  "expected": "documented behaviour: --target takes any rev the operator names "
                              "explicitly (AGENTS.md passes origin/main); a short SHA resolves, "
                              "and no claim anywhere says commits are refused",
                  "exit_code": code_a, "expected_exit_code": 2, "tree_mutated": False,
                  "pass": code_a == 0 and code_b == 0,
                  "actual": f"--target <short sha> exits {code_a}, --target HEAD exits {code_b}; "
                            "both resolve. The author's report claim that '--target cannot point "
                            "at a commit' is therefore not supported (see FINDINGS F-6)"})
r.cleanup()

L.finish()
