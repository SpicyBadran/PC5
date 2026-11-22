#!/usr/bin/env python3
"""Negative controls against the final committed code of PR #41.

Each control copies the tree to a scratch directory, applies one edit, and then asks the same two
questions: does the *gate* notice (`make presentation-check`, `make docs-check`), and does the
*test suite* notice. Where the control is a change to the generator, the run is repeated after
`presentation.py sync` — regenerating the assets so the committed bytes match the mutated render —
because that is the only state in which a presentation-layer lie can actually be committed. A
control that is caught only before the resync proves the byte comparison, not the data.

Nothing in /tmp/audit41/pr41 is modified by this script; the original is only ever read.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path("/tmp/audit41/pr41")
WORK = Path("/tmp/audit41/mut")
PY = sys.executable

CONTROLS = [
    # (label, file, needle, replacement, resync, suites)
    ("1. force every ladder cell green (the original M1 class)", "tools/presentation.py",
     'milestone_id.replace("M", "") + suffix, fill=fill, ink=ink,',
     'milestone_id.replace("M", "") + suffix, fill=GREEN, ink=GREEN_INK,',
     True, ["tests.test_progress_matrix"]),
    ("2. not_started rendered with the complete colour", "tools/presentation.py",
     '    "not_started": (CELL, INK, None, ""),',
     '    "not_started": (GREEN, GREEN_INK, None, ""),   # AUDIT CONTROL',
     True, ["tests.test_progress_matrix", "tests.test_presentation"]),
    ("3. the blocked marker removed, so blocked reads as not started", "tools/presentation.py",
     '    "blocked": (CELL, "#f4b9bd", RED, " !"),',
     '    "blocked": (CELL, INK, None, ""),               # AUDIT CONTROL',
     True, ["tests.test_progress_matrix"]),
    ("3b. a state label in the legend changed to a neutral word", "tools/presentation.py",
     '    "blocked": ("BLOCKED", RED),',
     '    "blocked": ("IN PROGRESS", RED),                # AUDIT CONTROL',
     False, ["tests.test_progress_matrix", "tests.test_presentation"]),
    ("4. inflate the milestones-verified metric in the README table", "tools/presentation.py",
     '        ("Milestones verified", pct(states["complete"], metrics["milestone_total"]),',
     '        ("Milestones verified", "100.0% (35/35)",  # AUDIT CONTROL: inflated',
     True, ["tests.test_progress_matrix", "tests.test_presentation"]),
    ("5. count a citation as a filed evidence record", "tools/presentation.py",
     '    filed = [e for e in evidence_entries if e.split("#")[0].startswith("docs/evidence/")]',
     '    filed = list(evidence_entries)  # AUDIT CONTROL: a citation counted as a record',
     True, ["tests.test_progress_matrix", "tests.test_presentation"]),
    ("6. skip the filesystem check of cited paths", "tools/presentation.py",
     '    evidence_present = [e for e in evidence_entries if (ROOT / e.split("#")[0]).exists()]',
     '    evidence_present = list(evidence_entries)  # AUDIT CONTROL: never touches the disk',
     True, ["tests.test_progress_matrix"]),
    ("7. an SVG text collision introduced into the committed asset",
     "docs/assets/pc5-progress-matrix.svg", None, "COLLIDE", False,
     ["tests.test_progress_matrix", "tests.test_presentation"]),
    ("8. a truncated label (ellipsis) in the committed figure", "docs/assets/pc5-progress-matrix.svg",
     None, "TRUNCATE", False, ["tests.test_progress_matrix"]),
    ("9. the dashboard asset deleted", None, None, "DELETE_ASSET", False,
     ["tests.test_progress_matrix", "tests.test_presentation"]),
    ("10. the generator itself made to fail", "tools/presentation.py",
     'def load():', 'def load():\n    raise RuntimeError("AUDIT CONTROL: the generator fails")',
     False, ["tests.test_presentation"]),
    ("11. a Git subprocess fails and the answer is swallowed", "tools/dashboard_reconcile.py",
     '''    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()''',
     '''    if result.returncode != 0:
        return ""
    if True:
        detail = (result.stderr or result.stdout or "").strip().splitlines()''',
     False, ["tests.test_dashboard_reconcile"]),
    ("12. verification removed from the record path altogether", "tools/dashboard_reconcile.py",
     "    results = verify(repo, log.append)", "    results = []  # AUDIT CONTROL",
     False, ["tests.test_dashboard_reconcile"]),
    ("13. generated output no longer exempted, so the loop never closes",
     "tools/dashboard_reconcile.py",
     '    if not paths:\n        return False',
     '    if not paths:\n        return True',
     False, ["tests.test_dashboard_reconcile"]),
]


def run(cmd, cwd):
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          env=dict(TMPDIR="/tmp", PATH="/usr/bin:/bin:/usr/local/bin"))
    tail = [l for l in (done.stdout + done.stderr).strip().splitlines() if l.strip()]
    return done.returncode, (tail[-1][:120] if tail else "")


def apply_control(control):
    label, rel, needle, replacement, resync, suites = control
    root = WORK / label.split(".")[0]
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(SRC, root, symlinks=True)
    subprocess.run(("git", "checkout", "--", "."), cwd=str(root), capture_output=True)
    if replacement == "DELETE_ASSET":
        (root / "docs/assets/pc5-progress-matrix.svg").unlink()
    elif replacement in {"COLLIDE", "TRUNCATE"}:
        path = root / "docs/assets/pc5-progress-matrix.svg"
        text = path.read_text(encoding="utf-8")
        if replacement == "COLLIDE":
            # move a region heading onto the same baseline as its right-hand figure
            text = text.replace('y="281"', 'y="261"', 1)
        else:
            first = text.index("<text")
            end = text.index("</text>", first)
            text = text[:end] + "…" + text[end:]
        path.write_text(text, encoding="utf-8")
    elif rel and needle:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        if text.count(needle) != 1:
            return root, f"PATCH-FAILED ({text.count(needle)} matches)"
        path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
    elif rel:
        return root, "NO-OP"
    if resync:
        code, out = run([PY, "tools/presentation.py", "sync"], root)
        if code != 0:
            return root, f"RESYNC-FAILED: {out}"
    return root, "applied"


print(f"{'control':<62} {'patch':<12} {'pres-check':<12} {'docs-check':<11} "
      f"{'tests':<10} caught by")
print("-" * 155)
rows = []
for control in CONTROLS:
    label, rel, needle, replacement, resync, suites = control
    root, patch = apply_control(control)
    if patch not in {"applied"}:
        print(f"{label[:60]:<62} {patch[:10]:<12} {'-':<12} {'-':<11} {'-':<10}")
        rows.append({"control": label, "patch": patch})
        continue
    pc, pc_out = run(["make", "presentation-check"], root)
    dc, dc_out = run(["make", "docs-check"], root)
    tests_code, tests_out = 0, ""
    for suite in suites:
        code, out = run([PY, "-m", "unittest", suite], root)
        tests_code = tests_code or code
        tests_out = tests_out or out
    caught = []
    if pc:
        caught.append(f"presentation-check exit {pc}")
    if dc:
        caught.append(f"docs-check exit {dc}")
    if tests_code:
        caught.append(f"tests exit {tests_code}")
    verdict = "CAUGHT" if caught else "MISSED"
    print(f"{label[:60]:<62} {patch[:10]:<12} {('exit ' + str(pc)):<12} "
          f"{('exit ' + str(dc)):<11} {('exit ' + str(tests_code)):<10} {verdict}: "
          f"{'; '.join(caught)[:70]}")
    rows.append({"control": label, "patch": patch, "resync": resync,
                 "presentation_check": {"exit": pc, "out": pc_out},
                 "docs_check": {"exit": dc, "out": dc_out},
                 "tests": {"exit": tests_code, "out": tests_out, "suites": suites},
                 "verdict": verdict, "caught_by": caught})
    shutil.rmtree(root, ignore_errors=True)

Path("/tmp/audit41/mutation-results.json").write_text(json.dumps(rows, indent=2))
missed = [r for r in rows if r.get("verdict") == "MISSED"]
bad_patch = [r for r in rows if r.get("patch") not in ("applied",)]
print(f"\n{len(rows) - len(missed) - len(bad_patch)}/{len(rows)} controls caught; "
      f"{len(missed)} missed; {len(bad_patch)} needles failed to patch")
for row in missed:
    print("  MISSED:", row["control"])
for row in bad_patch:
    print("  PATCH:", row["control"], "->", row["patch"])
