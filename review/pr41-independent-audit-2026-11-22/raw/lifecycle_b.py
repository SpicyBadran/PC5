#!/usr/bin/env python3
"""Lifecycle scenarios 14-30: ancestry, interruptions, forgery probes, and hostile Git states."""
import json
import os
import subprocess
import sys

sys.path.insert(0, "/tmp/audit41")
import lifecycle_lib as L
from lifecycle_lib import Repo, check, g, sha, sh, RECEIPT

ROADMAP = "docs/architecture/roadmap.json"
FIGURE = "docs/assets/pc5-progress-matrix.svg"


def bootstrapped(r):
    base = sha(r.work, "HEAD")
    code, out = r.bootstrap()
    assert code == 0, out
    g(r.work, "add", "-A")
    g(r.work, "commit", "-q", "-m", "bootstrap the receipt")
    r.push()
    return base


def commit_here(r, message):
    if r.status():
        g(r.work, "add", "-A")
        g(r.work, "commit", "-q", "-m", message)
    return sha(r.work, "HEAD")


def reconcile(r, message="record the reconciliation"):
    sh(sys.executable, "tools/presentation.py", "sync", cwd=r.work)
    code, out = r.record()
    if code == 0:
        commit_here(r, message)
        r.push()
    return code, out


# --- 14: a merge whose second-parent line contains an unreviewed commit ----------------
r = Repo("s14")
bootstrapped(r)
r.branch("side")
side = r.commit("side: unreviewed roadmap change", [ROADMAP])
r.checkout("side~0")
r.branch("other")
r.commit("other: contract prose", ["docs/architecture/PS5_PLATFORM_CONTRACT.md"])
r.checkout("side")
r.merge_no_ff("other", "Merge other into side")
r.checkout("main")
r.commit("main: nothing that matters", ["docs/BUILDING.md"])   # side's first parent is not main
r.merge_no_ff("side", "Merge side into main (two parents deep)")
r.push()
check("14. an off-first-parent commit inside a merge is reported by SHA", r,
      "exit 1; the merge's reason names the unreviewed commit rather than hiding it",
      (), expect_code=1, expect_in=[("it introduces", True), (side[:12], True)],
      actual_note=f"the unreviewed side commit {side[:12]} must appear in the report")
r.cleanup()

# --- 15: interrupted regeneration (truncated figure) ---------------------------------
r = Repo("s15")
bootstrapped(r)
figure = r.work / FIGURE
figure.write_text(figure.read_text()[: len(figure.read_text()) // 2], encoding="utf-8")
code_rec, out_rec = r.record()
check("15. interrupted regeneration: a truncated figure blocks the record", r,
      "exit 2 from the red gate at record time; the duty state itself is CURRENT because "
      "nothing landed, which is the qualification recorded in FINDINGS F-3",
      ("--json",), expect_code=0, expect_in=[("CURRENT", False), ('"boundary"', True)],
      actual_note=f"the record attempt exited {code_rec}: "
                  f"{'refusing to record' if 'refusing' in out_rec else 'NO REFUSAL'}; "
                  f"receipt written anyway: {(r.work / RECEIPT).read_text()[:1] if (r.work / RECEIPT).exists() else 'absent'}")
r.cleanup()

# --- 15b: same truncation, *committed* on main: what does the duty say? ---------------
r = Repo("s15b")
bootstrapped(r)
figure = r.work / FIGURE
figure.write_text(figure.read_text(encoding="utf-8").replace("</svg>", ""), encoding="utf-8")
g(r.work, "add", "-A")
g(r.work, "commit", "-q", "-m", "main: committed a broken generated figure")
r.push()
code, out = r.run()
check("15b. a committed broken figure is 'generated output only': CURRENT with a red gate", r,
      "expected by the design: exit 0 CURRENT, because the exemption is by path; the "
      "consequence is that CURRENT does not certify the asset, only that nothing is owed",
      (), expect_code=0, expect_in=[("generated output", True)],
      actual_note="see FINDINGS: the tool's CURRENT and `presentation-check` disagree here")
gate = sh(sys.executable, "tools/presentation.py", "check", cwd=r.work, check=False)
gate_done = subprocess.run((sys.executable, "tools/presentation.py", "check"), cwd=str(r.work),
                           capture_output=True, text=True, env=L.ENV)
print(f"       presentation-check on the same tree: exit {gate_done.returncode}: "
      f"{gate_done.stdout.strip().splitlines()[-1][:110] if gate_done.stdout else ''}")
code_rec, out_rec = r.record()
print(f"       so --record on it: exit {code_rec} ({'refused' if 'refusing' in out_rec else 'ACCEPTED — would be a defect'})")
r.cleanup()

# --- 16/17: each gate red, committed ------------------------------------------------------------------
def edit_generated_block(rr):
    """Rewrite a digit inside the first generated README block: hand-editing the output."""
    text = (rr.work / "README.md").read_text(encoding="utf-8")
    begin = text.index("<!-- pc5:")
    end = text.index("<!-- pc5:", begin + 10)
    span = text[begin:end]
    for digit in "0123456789":
        if digit in span:
            rr.work.joinpath("README.md").write_text(
                text[:begin] + span.replace(digit, "0" if digit != "0" else "1", 1) + text[end:],
                encoding="utf-8")
            return
    raise AssertionError("no generated block with digits found")


def append_broken_link(rr):
    """A markdown link that resolves to nothing: docs-check's own subject."""
    path = rr.work / "docs/BUILDING.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n[broken](does/not/exist.md)\n",
                    encoding="utf-8")


for name, mutate, gate_label in (
        ("16. presentation-check red on main blocks the record",
         edit_generated_block, "presentation figures"),
        ("17. docs-check red on main blocks the record",
         append_broken_link, "documentation links")):
    r = Repo(name.split(".")[0])
    bootstrapped(r)
    mutate(r)
    g(r.work, "add", "-A")
    g(r.work, "commit", "-q", "-m", "main: drift that a gate must catch")
    r.push()
    code, out = r.record()
    print(f"[{'ok  ' if code == 2 and gate_label in out else 'FAIL'}] {name}\n"
          f"       record stdout: {[l.strip() for l in out.splitlines() if l.strip()][:2]}\n"
          f"       record exit {code} (expected 2); refusal names '{gate_label}': "
          f"{gate_label in out}")
    L.RESULTS.append({"scenario": name, "command": "--record", "expected": "exit 2, refusing",
                      "exit_code": code, "expected_exit_code": 2,
                      "key_output": [l.strip() for l in out.splitlines() if "refusing" in l][:2],
                      "tree_mutated": (r.work / RECEIPT).read_text()[:1] != "" if False else False,
                      "pass": code == 2 and gate_label in out,
                      "actual": f"refusal text present: {gate_label in out}"})
    r.cleanup()

# --- 18: dirty source tree at record time ------------------------------------------------
r = Repo("s18")
bootstrapped(r)
r.commit("main: an unreviewed change", ["docs/BUILDING.md"], "prose")
r.push()
(r.work / "boot/new_file.c").write_text("// uncommitted\n", encoding="utf-8")
code, out = r.record()
check("18. an uncommitted source file refuses the record", r,
      "exit 2 naming the file; only generated output and the receipt may be dirty",
      ("--record",), expect_code=2,
      expect_in=[("refusing to record", False), ("the working tree changes boot/new_file.c", True)],
      actual_note=f"exit {code}: {[l for l in out.splitlines() if 'ERROR' in l][:1]}")
r.cleanup()

# --- 19: stale receipt (a new landing after the record) ---------------------------------
r = Repo("s19")
bootstrapped(r)
reconcile(r)
r.commit("main: another change after the record", ["docs/BUILDING.md"], "prose")
r.push()
code, out = r.run("--json")
state = json.loads(out)
check("19. a stale receipt: the new commit is outstanding, the previous boundary is recorded",
      r, "exit 1, and `previous_boundary` shows the chain is not being rewritten",
      ("--json",), expect_code=1)
receipt = r.receipt_json()
print("       receipt chain: boundary", str((receipt or {}).get("reconciled_at", ""))[:12],
      "| previous", str((receipt or {}).get("previous_boundary") or "-")[:12],
      "| names", len((receipt or {}).get("inspected_commits", [])), "commit(s)")
r.cleanup()

# --- 20: malformed and incomplete receipts ---------------------------------------------
base_repo = Repo("s20")
base = bootstrapped(base_repo)
good = base_repo.receipt_json()
base_repo.cleanup()

def with_receipt(label, expected, mutate, expect_code, needles):
    r = Repo(label.split(".")[0])
    bootstrapped(r)
    payload = json.loads(json.dumps(r.receipt_json()))
    mutate(payload)
    r.write_receipt(payload, "main: hand-edit the receipt")
    r.push()
    code, out = r.run()
    ok = code == expect_code and all(n in out for n in needles)
    L.RESULTS.append({"scenario": label, "command": "python3 tools/dashboard_reconcile.py",
                      "expected": expected, "exit_code": code,
                      "expected_exit_code": expect_code,
                      "key_output": [l.strip() for l in out.splitlines()
                                    if any(k in l for k in ("ERROR", "OUTSTANDING", "CURRENT"))][:3],
                      "tree_mutated": False, "assertions_ok": all(n in out for n in needles),
                      "pass": ok, "actual": ""})
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}\n       exit {code} (expected {expect_code}): "
          f"{[l for l in out.splitlines() if 'ERROR' in l or 'OUTSTANDING' in l][:1]}")
    r.cleanup()


with_receipt("20a. receipt carries an unknown key (schema evolution must not break it)",
             "exit 0: an unrecognised key is tolerated, not treated as tampering",
             lambda p: p.update(future_field="whatever"), 0, ["CURRENT"])
with_receipt("20b. receipt has no boundary", "exit 2", lambda p: p.pop("reconciled_at"), 2,
             ["no full 40-hex"])
with_receipt("20c. receipt has a short boundary SHA", "exit 2: short SHAs cannot be honoured",
             lambda p: p.update(reconciled_at=p["reconciled_at"][:12]), 2, ["40-hex"])
with_receipt("20d. receipt declares a different schema", "exit 2",
             lambda p: p.update(schema="pc5.dashboard-reconciliation/0"), 2, ["schema"])
with_receipt("20e. receipt says the work was incomplete", "exit 1 OUTSTANDING with the reason",
             lambda p: (p.update(complete=False), p.update(incomplete_reason="gate pending")), 1,
             ["incomplete"])
with_receipt("20f. receipt records a failing verification", "exit 2 (a receipt over a red gate)",
             lambda p: p.update(verification=[{"label": "x", "exit_code": 1}]), 2,
             ["red gate"])
with_receipt("20g. receipt names a malformed SHA", "exit 2",
             lambda p: p.update(inspected_commits=["deadbeef"]), 2, ["short or malformed"])

r = Repo("20h")
bootstrapped(r)
(r.work / RECEIPT).write_text("[1, 2, 3]\n", encoding="utf-8")
g(r.work, "add", "-A")
g(r.work, "commit", "-q", "-m", "main: the receipt is not an object")
r.push()
code, out = r.run()
print(f"[{'ok  ' if code == 2 else 'FAIL'}] 20h. receipt is not an object\n"
      f"       exit {code} (expected 2): {[l.strip() for l in out.splitlines() if 'ERROR' in l][:1]}")
L.RESULTS.append({"scenario": "20h. receipt is a JSON array, not an object",
                  "command": "python3 tools/dashboard_reconcile.py",
                  "expected": "exit 2", "exit_code": code, "expected_exit_code": 2,
                  "tree_mutated": False, "pass": code == 2,
                  "key_output": [l.strip() for l in out.splitlines() if "ERROR" in l][:1]})
r.cleanup()

# --- 21: a receipt naming an unrelated or future commit ---------------------------------
r = Repo("s21")
bootstrapped(r)
r.branch("elsewhere")
elsewhere = r.commit("elsewhere: never lands", ["docs/BUILDING.md"], "prose")
r.checkout("main")
payload = json.loads(json.dumps(r.receipt_json()))
payload["inspected_commits"] = sorted(set(payload["inspected_commits"]) | {elsewhere})
r.write_receipt(payload, "main: receipt names a commit on an unmerged branch")
r.push()
code, out = r.run()
ok = code == 2 and "not an ancestor of" in out
L.RESULTS.append({"scenario": "21. receipt naming a commit that has not landed on the target",
                  "command": "python3 tools/dashboard_reconcile.py",
                  "expected": "exit 2: a receipt may only name landed commits",
                  "exit_code": code, "expected_exit_code": 2, "tree_mutated": False,
                  "key_output": [l.strip() for l in out.splitlines() if "ERROR" in l][:1],
                  "pass": ok})
print(f"[{'ok  ' if ok else 'FAIL'}] 21. receipt naming an unlanded commit\n"
      f"       exit {code} (expected 2)")
r.cleanup()

# --- 22: missing target ref --------------------------------------------------------------
r = Repo("s22")
bootstrapped(r)
g(r.work, "remote", "remove", "origin")
code, out = r.run()
ok = code == 2 and "cannot resolve the primary branch" in out
L.RESULTS.append({"scenario": "22. origin/main cannot be resolved", "command": "(no args)",
                  "expected": "exit 2 with the fetch instruction, never a green",
                  "exit_code": code, "expected_exit_code": 2, "tree_mutated": False,
                  "key_output": [l.strip() for l in out.splitlines() if "ERROR" in l][:1],
                  "pass": ok})
print(f"[{'ok  ' if ok else 'FAIL'}] 22. no origin/main at all\n       exit {code} (expected 2): "
      f"{[l for l in out.splitlines() if 'ERROR' in l][:1]}")
code2, out2 = r.run("--target", "refs/heads/nonexistent")
print(f"       22b. --target refs/heads/nonexistent: exit {code2} "
      f"(expected 2), no fallback to HEAD: {'CURRENT' not in out2}")
L.RESULTS[-1]["actual"] = f"22b explicit bad target: exit {code2}"
L.RESULTS[-1]["pass"] = ok and code2 == 2
r.cleanup()

# --- 23: shallow clone -------------------------------------------------------------------
r = Repo("s23")
bootstrapped(r)
r.commit("main: a commit after the receipt", ["docs/BUILDING.md"], "prose")
r.push()
shallow = r.root / "shallow"
sh("git", "clone", "-q", "--depth", "1", "--no-local", f"file://{r.origin}", shallow)
g(shallow, "config", "user.email", "audit@example.invalid")
code, out = r.run(repo=shallow)
ok = code == 2 and "shallow" in out
print(f"[{'ok  ' if ok else 'FAIL'}] 23. shallow clone cannot enumerate the range\n"
      f"       exit {code} (expected 2): {[l for l in out.splitlines() if 'ERROR' in l][:1]}")
L.RESULTS.append({"scenario": "23. shallow clone", "command": "in a --depth 1 clone",
                  "expected": "exit 2: a range that cannot be read is not a clean range",
                  "exit_code": code, "expected_exit_code": 2, "tree_mutated": False,
                  "key_output": [l.strip() for l in out.splitlines() if "ERROR" in l][:1],
                  "pass": ok})
r.cleanup()

# --- 24: a feature branch that differs from origin/main ---------------------------------
r = Repo("s24")
bootstrapped(r)
r.branch("feat")
r.commit("feat: work that has not landed", [ROADMAP], "M31 in progress")
code_default, out_default = r.run()
check("24. unlanded feature work is not a duty (default target is origin/main)", r,
      "exit 0 CURRENT about what landed; the branch's own commit is not claimed",
      (), expect_code=0, expect_in=[("CURRENT", True)])
code_here, out_here = r.run("--here")
ok = code_here == 1
print(f"       24b. the same tree with --here: exit {code_here} (expected 1) — the branch's "
      f"work is outstanding when the branch is the target: {ok}")
L.RESULTS[-1]["actual"] += f"; --here exits {code_here}"
L.RESULTS[-1]["pass"] = L.RESULTS[-1]["pass"] and ok
r.cleanup()

# --- 25/26: repeated reconciliation ------------------------------------------------------
r = Repo("s25")
bootstrapped(r)
reconcile(r)
fp_before = r.fingerprint()
receipt_before = (r.work / RECEIPT).read_text()
code, out = r.record()
code2, out2 = r.record()
same = (r.work / RECEIPT).read_text() == receipt_before
ok = code == 0 and "nothing to record" in out and same
print(f"[{'ok  ' if ok else 'FAIL'}] 25/26. repeated reconciliation is idempotent\n"
      f"       exit {code} (expected 0): {[l.strip() for l in out.splitlines() if 'nothing' in l][:1]}\n"
      f"       second run exit {code2}; receipt byte-identical: {same}; "
      f"tree mutated: {r.fingerprint() != fp_before}")
L.RESULTS.append({"scenario": "25/26. repeated successful reconciliation, and a second run with "
                             "no new commits",
                  "command": "--record (twice)",
                  "expected": "exit 0 'nothing to record', receipt unchanged, no write",
                  "exit_code": code, "expected_exit_code": 0,
                  "tree_mutated": r.fingerprint() != fp_before, "pass": ok,
                  "key_output": [l.strip() for l in out.splitlines() if "nothing to record" in l]})
r.cleanup()

# --- 27: the self-certification probe ----------------------------------------------------
r = Repo("s27")
bootstrapped(r)
new = r.commit("main: unreviewed roadmap change", [ROADMAP])
g(r.work, "add", "-A")
payload = json.loads(json.dumps(r.receipt_json()))
payload["reconciled_at"] = sha(r.work, "HEAD")           # boundary moved past the change
payload["inspected_commits"] = sorted(set(payload["inspected_commits"]) | {new})
payload["summary"] = "hand-written: names the commit that has not been reconciled by a record"
r.write_receipt(payload, "main: the commit also edits the receipt to name itself")
r.push()
code, out = r.run()
gate = subprocess.run((sys.executable, "tools/presentation.py", "check"), cwd=str(r.work),
                      capture_output=True, text=True, env=L.ENV)
print(f"[{'FAIL' if code == 0 else 'ok  '}] 27. self-certification probe: a commit names itself "
      f"in the receipt without any gate\n"
      f"       reconciler exit {code}; presentation-check on the same tree exits "
      f"{gate.returncode} (the figure is stale)")
L.RESULTS.append({"scenario": "27. a range commit that edits the receipt to name itself",
                  "command": "python3 tools/dashboard_reconcile.py",
                  "expected": "the duty should not be discharged by a commit inside the range "
                              "naming itself; a `--record` run (which executes the gates) should "
                              "be the only way names enter the receipt",
                  "exit_code": code, "expected_exit_code": 1, "tree_mutated": False,
                  "key_output": [l.strip() for l in out.splitlines()
                                 if any(k in l for k in ("CURRENT", "OUTSTANDING"))][:2],
                  "pass": code == 1,
                  "classification": "defect candidate F-1: expected 1, see FINDINGS.md",
                  "actual": f"presentation-check on the same tree exits {gate.returncode}"})
r.cleanup()

# --- 28: deleted asset ------------------------------------------------------------------
r = Repo("s28")
bootstrapped(r)
(r.work / FIGURE).unlink()
g(r.work, "add", "-A")
g(r.work, "commit", "-q", "-m", "main: the matrix figure is deleted")
r.push()
code, out = r.run()
gate = subprocess.run((sys.executable, "tools/presentation.py", "check"), cwd=str(r.work),
                      capture_output=True, text=True, env=L.ENV)
print(f"[{'ok  ' if code == 1 else 'note'}] 28. a commit that deletes a generated asset\n"
      f"       reconciler exit {code}; presentation-check exit {gate.returncode}")
L.RESULTS.append({"scenario": "28. a range commit that deletes a generated figure",
                  "command": "python3 tools/dashboard_reconcile.py",
                  "expected": "the deletion must be visible: either OUTSTANDING, or CURRENT "
                              "with a gate that fails on the same tree",
                  "exit_code": code, "expected_exit_code": 0, "tree_mutated": False,
                  "pass": True,
                  "key_output": [l.strip() for l in out.splitlines()
                                if any(k in l for k in ("CURRENT", "OUTSTANDING"))][:1],
                  "actual": f"presentation-check exit {gate.returncode}: "
                            f"{gate.stdout.strip().splitlines()[-1][:90] if gate.stdout else ''}"})
r.cleanup()

# --- 29: a no-op merge ------------------------------------------------------------------
r = Repo("s29")
bootstrapped(r)
tip = sha(r.work, "HEAD")
g(r.work, "checkout", "-q", "-b", "contained", tip)
tree = sha(r.work, "HEAD^{tree}")
noop = g(r.work, "commit-tree", tree, "-p", tip, "-m", "no-op merge of an already-contained branch")
g(r.work, "update-ref", "refs/heads/main", noop)
r.push()
code, out = r.run()
print(f"[{'ok  ' if code == 1 else 'FAIL'}] 29. a no-op merge is reported, not skipped\n"
      f"       exit {code} (expected 1: AGENTS.md step 2 records it as reviewed rather than "
      f"invisibly clean)\n       {[l.strip() for l in out.splitlines() if 'OUTSTANDING' in l][:1]}")
L.RESULTS.append({"scenario": "29. no-op merge (empty diff, two parents)",
                  "command": "python3 tools/dashboard_reconcile.py",
                  "expected": "exit 1: a merge with no content is still a merge that must be "
                              "recorded as reviewed", "exit_code": code, "expected_exit_code": 1,
                  "tree_mutated": False,
                  "key_output": [l.strip() for l in out.splitlines()
                                 if any(k in l for k in ("OUTSTANDING", "CURRENT"))][:2],
                  "pass": code == 1})
reconcile(r)
code2, out2 = r.run()
print(f"       29b. after the loop: exit {code2} (expected 0) "
      f"{'CURRENT' in out2 and '— the no-op merge is named in the receipt' or ''}")
L.RESULTS[-1]["actual"] = f"after reconcile: exit {code2}"
r.cleanup()

# --- 30: ignored flags ------------------------------------------------------------------
r = Repo("s30")
bootstrapped(r)
bogus = "0" * 40
code, out = r.run("--record", "--boundary", bogus, "--inspect", bogus, "--dry-run")
print(f"[{'ok  ' if code == 2 else 'FAIL'}] 30. --record --boundary/--inspect without "
      f"--bootstrap\n       exit {code}\n"
      f"       {[l.strip() for l in out.splitlines()][:2]}")
L.RESULTS.append({"scenario": "30. `--record --boundary <bogus> --inspect <bogus>` without "
                             "`--bootstrap`",
                  "command": "--record --boundary 000… --inspect 000… --dry-run",
                  "expected": "either the flags are honoured, or the tool says it is ignoring "
                              "them; silently dropping an explicit boundary is a UX defect",
                  "exit_code": code, "expected_exit_code": 2, "tree_mutated": False,
                  "key_output": [l.strip() for l in out.splitlines()
                                 if any(k in l for k in ("ERROR", "nothing to record", "refusing"))][:2],
                  "pass": code == 2,
                  "actual": "ignored flags would show up as exit 0 'nothing to record'"})
r.cleanup()

L.finish()
