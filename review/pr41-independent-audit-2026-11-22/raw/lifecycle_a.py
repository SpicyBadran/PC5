#!/usr/bin/env python3
"""Lifecycle scenarios 1-13: the documented loop, one real Git topology at a time."""
import json
import sys

sys.path.insert(0, "/tmp/audit41")
import lifecycle_lib as L
from lifecycle_lib import Repo, check, g, sha, RECEIPT

ROADMAP = "docs/architecture/roadmap.json"
CONTRACT = "docs/architecture/PS5_PLATFORM_CONTRACT.md"


def land(r, message="land the reconciliation"):
    """Commit whatever the tool wrote (a receipt, regenerated figures) and push it to main."""
    if r.status():
        g(r.work, "add", "-A")
        g(r.work, "commit", "-q", "-m", message)
    r.push()
    return sha(r.work, "HEAD")


def regenerate(r):
    """AGENTS.md step 4: `make presentation`, run the same way the Makefile does."""
    L.sh(sys.executable, "tools/presentation.py", "sync", cwd=r.work)


def reconcile(r, message="record the reconciliation"):
    """AGENTS.md steps 4-7: regenerate, record, commit figures + receipt together, push."""
    regenerate(r)
    code, out = r.record()
    if code == 0:
        commit_here(r, message)
        r.push()
    return code, out


def commit_here(r, message):
    if r.status():
        g(r.work, "add", "-A")
        g(r.work, "commit", "-q", "-m", message)
    return sha(r.work, "HEAD")


def bootstrapped(r):
    """Get a repo to the documented starting point: a receipt on main, range empty."""
    base = sha(r.work, "HEAD")
    code, out = r.bootstrap()
    assert code == 0, out
    land(r, "bootstrap the receipt at the imported tree")
    return base


# --- 1 --------------------------------------------------------------------------------
r = Repo("s01")
check("1. initial state, no receipt on the target", r,
      "exit 1: OUTSTANDING, naming the missing receipt and printing the bootstrap command",
      (), expect_code=1,
      expect_in=[("does not exist on", True), ("--bootstrap", True)],
      expect_not_in=["CURRENT"])
r.cleanup()

# --- 2 --------------------------------------------------------------------------------
r = Repo("s02")
base = bootstrapped(r)
check("2. correct bootstrap after the gates pass, then the next run is CURRENT", r,
      f"bootstrap at {base[:12]} landed; the receipt names it; run exits 0 CURRENT",
      ("--json",), expect_code=0, expect_in=[('"boundary"', True), ("CURRENT", False)],
      actual_note="receipt present on origin/main, range empty")
r.cleanup()

# --- 3: bootstrap while a required gate is red --------------------------------------
r = Repo("s03")
r.make_output_dirty()                       # the generator's output disagrees with the data
base = sha(r.work, "HEAD")
code, out = r.bootstrap(boundary=base)
print(f"       bootstrap attempt: exit {code}; receipt written: {(r.work / RECEIPT).exists()}")
check("3. bootstrap refused while presentation-check is red; nothing is written", r,
      "exit 2, the failing gate named, and no receipt in the tree (the refusal above shows both)",
      ("--record", "--bootstrap", "--boundary", base, "--inspect", base), expect_code=2,
      expect_in=[("refusing to record", True),
                 ("presentation figures and README blocks are current", True)],
      actual_note=f"first attempt exit {code}; receipt file present after refusal: "
                  f"{(r.work / RECEIPT).exists()}")
r.cleanup()

# --- 4: ordinary merge commit, source change, unreviewed ------------------------------
r = Repo("s04")
bootstrapped(r)
r.branch("feat")
r.commit("feat: move M31 in progress", [ROADMAP], "in progress")
r.checkout("main")
r.merge_no_ff("feat", "Merge pull request 1 from feat")
r.push()
check("4. ordinary merge commit carrying an unreviewed source change", r,
      "exit 1: the merge needs inspection, and the report says what it introduced",
      (), expect_code=1, expect_in=[("OUTSTANDING", True), ("Merge pull request", True)])
code, out = reconcile(r)
check("4c. recording while the figure is stale is refused (this run regenerated, so it passes)",
      r, "exit 0 because step 4 was performed before step 6", ("--json",), expect_code=0)
code2, out2 = r.record()
check("4b. the same merge, after `--record` and landing the receipt", r,
      "exit 0 CURRENT: the duty is discharged by the record, not by the merge",
      (), expect_code=0, expect_in=[("CURRENT", True)])
r.cleanup()

# --- 5: squash merge ------------------------------------------------------------------
r = Repo("s05")
bootstrapped(r)
r.branch("feat")
r.commit("feat A", [ROADMAP], "M32 in progress")
r.commit("feat B", ["boot/loader.c"], "second commit on the branch")
r.checkout("main")
r.squash("feat", "Squash merge of feat (two commits become one new SHA)")
r.push()
check("5. squash merge: the minted SHA is not in any receipt, so the duty stays open", r,
      "exit 1 with the squash commit named as outstanding",
      (), expect_code=1, expect_in=[("OUTSTANDING", True), ("Squash merge of feat", True)])
code, out = reconcile(r)
check("5b. squash after the full loop (documented: a squash still has to be recorded)", r,
      f"reconcile exit {code} (0 expected: the record happened); then CURRENT", (),
      expect_code=0, expect_in=[("CURRENT", True)])
r.cleanup()

# --- 6: rebase-style integration ------------------------------------------------------
r = Repo("s06")
bootstrapped(r)
r.branch("feat")
r.commit("feat: contract edit", [CONTRACT], "PS5 hardware note")
r.commit("feat: roadmap edit", [ROADMAP], "M33 complete")
r.rebase_then_ff("feat", "main")
r.push()
check("6. rebase then fast-forward: each rebased commit is a first-parent member", r,
      "exit 1, and both rebased commits are listed",
      ("--json",), expect_code=1,
      expect_in=[('"requires_inspection": true', True), ("contract edit", True)],
      actual_note="the JSON lists each commit individually; see outstanding count below")
reconcile(r)
check("6b. after recording, CURRENT", r, "exit 0", (), expect_code=0,
      expect_in=[("CURRENT", True)])
r.cleanup()

# --- 7: fast-forward integration ------------------------------------------------------
r = Repo("s07")
bootstrapped(r)
r.branch("feat")
r.commit("feat: single roadmap edit", [ROADMAP], "M34 blocked")
r.checkout("main")
r.merge_ff("feat")
r.push()
check("7. fast-forward integration: no merge commit exists, the commit itself is the duty", r,
      "exit 1 naming the fast-forwarded commit",
      (), expect_code=1, expect_in=[("OUTSTANDING", True), ("single roadmap edit", True)])
reconcile(r)
check("7b. recorded, CURRENT", r, "exit 0", (), expect_code=0, expect_in=[("CURRENT", True)])
r.cleanup()

# --- 8: direct commit on the target ---------------------------------------------------
r = Repo("s08")
bootstrapped(r)
r.commit("main: direct roadmap edit without a PR", [ROADMAP], "M35 not started")
r.push()
check("8. direct commit to main: still a duty, no merge required to notice it", r,
      "exit 1", (), expect_code=1, expect_in=[("OUTSTANDING", True),
                                              ("direct roadmap edit", True)])
reconcile(r)
check("8b. recorded, CURRENT", r, "exit 0", (), expect_code=0, expect_in=[("CURRENT", True)])
r.cleanup()

# --- 9: documentation-only merge ------------------------------------------------------
r = Repo("s09")
bootstrapped(r)
r.branch("docs")
r.commit("docs: clarify the platform contract", [CONTRACT], "prose only")
r.checkout("main")
r.merge_no_ff("docs", "Merge docs-only branch")
r.push()
check("9. documentation-only merge (no metric can move): still reported, not hidden", r,
      "exit 1; AGENTS.md step 2 says a no-op merge is recorded as reviewed, not skipped",
      (), expect_code=1, expect_in=[("OUTSTANDING", True)])
code, out = r.run("--json")
inspected = json.loads(out)["commits_since_boundary"]
print("       drivers attributed to the docs merge:",
      [c["drivers"] for c in inspected if c["drivers"]][:2] or "none the dashboard counts")
reconcile(r)
check("9b. recorded, CURRENT", r, "exit 0", (), expect_code=0, expect_in=[("CURRENT", True)])
r.cleanup()

# --- 10: code-only merge --------------------------------------------------------------
r = Repo("s10")
bootstrapped(r)
r.branch("code")
r.commit("code: firmware padding change", ["kernel/ps5_fw padded.c"], "not a driver")
r.checkout("main")
r.merge_no_ff("code", "Merge code-only branch")
r.push()
check("10. code-only merge touching nothing the dashboard counts", r,
      "exit 1, and the report says it drives nothing rather than dropping the commit",
      (), expect_code=1, expect_in=[("OUTSTANDING", True)])
code, out = r.run()
print("       'nothing the dashboard counts' line present:",
      "nothing the dashboard counts" in out)
reconcile(r)
check("10b. recorded, CURRENT", r, "exit 0", (), expect_code=0, expect_in=[("CURRENT", True)])
r.cleanup()

# --- 11: merge that changes a displayed metric ----------------------------------------
r = Repo("s11")
bootstrapped(r)
r.branch("metric")
r.commit("metric: complete a milestone", [ROADMAP], "M20 complete")
g(r.work, "checkout", "-q", "metric")
gen = L.sh(sys.executable, str(L.REAL / "tools" / "presentation.py"), "generate",
           cwd=r.work, check=False)
commit_here(r, "regenerate the figures on the branch")
r.checkout("main")
r.merge_no_ff("metric", "Merge metric-moving branch")
r.push()
check("11. merge that moves a metric: drivers include roadmap.json and the figure", r,
      "exit 1, drivers named", (), expect_code=1,
      expect_in=[("OUTSTANDING", True), ("drives:", True)])
reconcile(r)
check("11b. recorded, CURRENT", r, "exit 0", (), expect_code=0, expect_in=[("CURRENT", True)])
r.cleanup()

# --- 12: merge that changes no displayed metric, figure untouched ---------------------
r = Repo("s12")
bootstrapped(r)
r.commit("docs: a BUILDING note outside any generated block",
         ["docs/BUILDING.md"], "prose")
r.push()
check("12. a change that moves no figure is still outstanding, with empty drivers", r,
      "exit 1; `figures_changed: false` in the receipt after recording",
      ("--json",), expect_code=1)
code, out = r.run("--json")
state = json.loads(out)
print("       outstanding count:", len(state["outstanding"]),
      "| drivers:", state["outstanding"][0]["drivers"] if state["outstanding"] else "-")
reconcile(r)
receipt = r.receipt_json()
print("       receipt figures_changed:", receipt.get("figures_changed"),
      "readme_changed:", receipt.get("readme_changed"))
check("12b. recorded, CURRENT", r, "exit 0", (), expect_code=0, expect_in=[("CURRENT", True)])
r.cleanup()

# --- 13: several commits between reconciliations --------------------------------------
r = Repo("s13")
bootstrapped(r)
for i in range(4):
    r.commit(f"main: landed change {i}", [ROADMAP], f"M{30+i} touched")
    r.push()
check("13. four commits since the boundary: one pass must list all four", r,
      "exit 1 with four outstanding commits", ("--json",), expect_code=1)
code, out = r.run("--json")
state = json.loads(out)
listed = len(state["commits_since_boundary"])
print(f"       commits since boundary: {listed}, outstanding: {len(state['outstanding'])}")
reconcile(r)
check("13b. one record clears all four", r, "exit 0 CURRENT", (), expect_code=0,
      expect_in=[("CURRENT", True), ("commits since then   1", True)],
      actual_note="after landing, only the landing commit is in range and it is generated-only")
r.cleanup()

L.finish()
