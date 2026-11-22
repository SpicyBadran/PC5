#!/usr/bin/env python3
"""Independent lifecycle audit of tools/dashboard_reconcile.py.

Nothing here is mocked: each scenario builds a real repository (bare `origin` + working clone),
makes real commits with real merge topologies, and runs the public command
`python3 tools/dashboard_reconcile.py …` exactly as AGENTS.md tells an operator to run it. The
two gates the tool verifies (`presentation.py check`, `docs_check.py`) are the real verifiers
running over a real copy of the repository, so a scenario that expects a red gate makes one
actually go red.

Results carry expected, actual, exit code, and whether the working tree was mutated.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REAL = Path("/tmp/audit41/pr41")
OUT = Path(f"/tmp/audit41/lifecycle-results-{Path(sys.argv[0]).stem.rsplit('_', 1)[-1]}.json")
TOOL = str(REAL / "tools" / "dashboard_reconcile.py")
ENV = dict(os.environ,
           GIT_AUTHOR_NAME="Audit", GIT_AUTHOR_EMAIL="audit@example.invalid",
           GIT_COMMITTER_NAME="Audit", GIT_COMMITTER_EMAIL="audit@example.invalid",
           GIT_AUTHOR_DATE="2026-11-20T12:00:00+00:00",
           GIT_COMMITTER_DATE="2026-11-20T12:00:00+00:00",
           TMPDIR="/tmp")
RECEIPT = "docs/evidence/dashboard-reconciliation.json"


def sh(*args, cwd=None, check=True):
    done = subprocess.run([str(a) for a in args], cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, env=ENV)
    if check and done.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, args))} -> exit {done.returncode}\n"
                           f"{done.stdout}\n{done.stderr}")
    return done.stdout.strip()


def g(runner, *args):
    return sh("git", *args, cwd=runner)


def sha(runner, ref):
    return g(runner, "rev-parse", ref)


class Repo:
    """A disposable repository with a bare origin and one working clone."""

    def __init__(self, name):
        self.root = Path(tempfile.mkdtemp(prefix=f"pc5life-{name}-"))
        self.work = self.root / "work"
        self.origin = self.root / "origin.git"
        self.name = name
        self.origin.mkdir(parents=True)
        shutil.copytree(REAL, self.work, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        (self.work / RECEIPT).unlink(missing_ok=True)
        sh("git", "init", "-q", "-b", "main", cwd=self.work)
        g(self.work, "config", "user.email", "audit@example.invalid")
        g(self.work, "config", "user.name", "Audit")
        sh("git", "init", "-q", "--bare", "-b", "main", cwd=self.origin)
        g(self.work, "remote", "add", "origin", str(self.origin))
        g(self.work, "add", "-A")
        g(self.work, "commit", "-q", "-m", "c0: import the tree the dashboard reads")
        g(self.work, "push", "-q", "origin", "main")

    # -- history building ---------------------------------------------------------
    def touch(self, rel, label):
        """Make a real content change at `rel`.

        JSON sources are edited *validly* — a roadmap state is cycled to the next legal value —
        because appending text to `roadmap.json` would corrupt it, and a generator that cannot
        parse its own input is a broken fixture, not a test result.
        """
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json" and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            milestones = data.get("milestones")
            if isinstance(milestones, list) and milestones:
                order = ["not_started", "in_progress", "complete", "blocked"]
                target = next((m for m in milestones if m.get("id") == "M31"), milestones[0])
                current = target.get("state", "not_started")
                target["state"] = order[(order.index(current) + 1) % len(order)] \
                    if current in order else "in_progress"
            else:
                data["audit_note"] = label
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            return path
        if path.exists():
            path.write_text(path.read_text(encoding="utf-8") + f"\n// {label}\n",
                            encoding="utf-8")
        else:
            path.write_text(f"// {label}\n", encoding="utf-8")
        return path

    def commit(self, message, paths=(), label=None):
        for rel in paths:
            self.touch(rel, label or message)
        g(self.work, "add", "-A")
        g(self.work, "commit", "-q", "--allow-empty", "-m", message)
        return sha(self.work, "HEAD")

    def branch(self, name):
        g(self.work, "checkout", "-q", "-b", name)

    def checkout(self, name):
        g(self.work, "checkout", "-q", name)

    def merge_no_ff(self, other, message):
        g(self.work, "merge", "-q", "--no-ff", "-m", message, other)
        return sha(self.work, "HEAD")

    def merge_ff(self, other):
        g(self.work, "merge", "-q", "--ff-only", other)
        return sha(self.work, "HEAD")

    def squash(self, other, message):
        g(self.work, "merge", "-q", "--squash", other)
        g(self.work, "add", "-A")
        g(self.work, "commit", "-q", "-m", message)
        return sha(self.work, "HEAD")

    def rebase_then_ff(self, branch, base="main"):
        g(self.work, "checkout", "-q", branch)
        g(self.work, "rebase", "-q", base)
        head = sha(self.work, "HEAD")
        g(self.work, "checkout", "-q", base)
        g(self.work, "merge", "-q", "--ff-only", branch)
        return head

    def push(self, ref="main"):
        g(self.work, "push", "-q", "-f", "origin", f"HEAD:refs/heads/{ref}")

    def push_branch(self, name):
        g(self.work, "push", "-q", "origin", f"HEAD:refs/heads/{name}")

    # -- receipts -----------------------------------------------------------------
    def receipt_json(self, ref="origin/main"):
        done = subprocess.run(("git", "show", f"{ref}:{RECEIPT}"), cwd=str(self.work),
                              capture_output=True, text=True, env=ENV)
        return json.loads(done.stdout) if done.returncode == 0 else None

    def write_receipt(self, payload, message="record the receipt"):
        path = self.work / RECEIPT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        g(self.work, "add", "-A")
        g(self.work, "commit", "-q", "-m", message)
        return sha(self.work, "HEAD")

    def record(self, extra=(), note="audit reconciliation"):
        return self.run("--record", "--note", note, *extra)

    def bootstrap(self, boundary=None, inspect=None, extra=()):
        boundary = boundary or sha(self.work, "HEAD")
        inspect = inspect or [boundary]
        args = ["--record", "--bootstrap", "--boundary", boundary]
        for sha_value in inspect:
            args += ["--inspect", sha_value]
        return self.run(*args, *extra)

    def land_record(self, message="record the reconciliation", regenerate=False):
        """The legitimate loop: record, commit the receipt (and any regenerated figures), push.

        Deliberately does not assert: a scenario needs to see the refusal, not crash on it.
        """
        before = sha(self.work, "HEAD")
        code, out = self.record()
        if code == 0:
            g(self.work, "add", "-A")
            g(self.work, "commit", "-q", "-m", message)
            self.push()
        return code, out, before, sha(self.work, "HEAD")

    def make_output_dirty(self):
        """Simulate `make presentation` having rewritten a figure: generated output only."""
        figure = self.work / "docs" / "assets" / "pc5-progress-matrix.svg"
        body = figure.read_text(encoding="utf-8")
        assert "</svg>" in body
        figure.write_text(body.replace("</svg>", "<!-- regenerated -->\n</svg>", 1),
                          encoding="utf-8")
        return "docs/assets/pc5-progress-matrix.svg"

    # -- running the tool ---------------------------------------------------------
    def run(self, *args, repo=None, env=None):
        cmd = [sys.executable, TOOL, "--repo", str(repo or self.work), *[str(a) for a in args]]
        done = subprocess.run(cmd, capture_output=True, text=True, env=env or ENV,
                              cwd=str(repo or self.work))
        return done.returncode, (done.stdout + done.stderr)

    def status(self):
        return [line for line in g(self.work, "status", "--porcelain").splitlines()
                if line.strip()]

    def fingerprint(self):
        h = hashlib.sha256()
        for rel in (RECEIPT, "README.md", "docs/assets/pc5-progress-matrix.svg"):
            p = self.work / rel
            h.update(p.read_bytes() if p.exists() else b"<absent>")
        return h.hexdigest()[:16]

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def lines_of(text, needle, limit=3):
    return [l.strip() for l in text.splitlines() if needle in l][:limit]


RESULTS = []


def check(label, repo, expected, args, *, expect_code, expect_in=None, expect_not_in=None,
          actual_note=""):
    """Run the public command, record what happened, and score it against the contract."""
    before_fp = repo.fingerprint()
    before_status = repo.status()
    code, out = repo.run(*args)
    after_fp = repo.fingerprint()
    entry = {
        "scenario": label,
        "command": "python3 tools/dashboard_reconcile.py " + " ".join(map(str, args)),
        "expected": expected,
        "exit_code": code,
        "expected_exit_code": expect_code,
        "key_output": [line.strip() for line in out.splitlines()
                       if any(k in line for k in ("CURRENT", "OUTSTANDING", "ERROR", "wrote ",
                                                  "nothing to record", "refusing", "verify ",
                                                  "NOTE", "local `main`"))][:8],
        "tree_mutated": (after_fp != before_fp) or (repo.status() != before_status),
        "actual": actual_note,
    }
    ok = code == expect_code
    for needle, want in (expect_in or []):
        present = needle in out
        entry.setdefault("assertions", []).append(
            f"{'must contain' if want else 'must not contain'} {needle!r}: "
            f"{'ok' if present == want else 'FAILED'}")
        ok = ok and present == want
    for needle in (expect_not_in or []):
        present = needle in out
        entry.setdefault("assertions", []).append(
            f"must not contain {needle!r}: {'ok' if not present else 'FAILED'}")
        ok = ok and not present
    entry["pass"] = ok
    RESULTS.append(entry)
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}\n       exit {code} "
          f"(expected {expect_code}); tree mutated: {entry['tree_mutated']}")
    for line in entry["key_output"][:3]:
        print(f"       | {line[:150]}")
    for line in entry.get("assertions", []):
        if "FAILED" in line:
            print(f"       ! {line}")
    return entry


def finish():
    OUT.write_text(json.dumps(RESULTS, indent=2))
    bad = [r for r in RESULTS if not r["pass"]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} checks behave as documented; "
          f"{len(bad)} diverge")
    for r in bad:
        print("  DIVERGENCE:", r["scenario"], "exit", r["exit_code"], r.get("assertions", []))
