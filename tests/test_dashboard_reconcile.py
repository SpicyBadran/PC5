#!/usr/bin/env python3
"""The reconciliation receipt's whole lifecycle, driven against real git topologies.

Every scenario builds a throwaway repository under `tempfile.mkdtemp()`, runs
`tools/dashboard_reconcile.py` **as a subprocess**, and asserts the exit code it actually
returned.  The exit code is the product here — a unit test that calls `inspect()` and reads a
dictionary would not catch a tool that computes the right answer and then exits 0 anyway.

Nothing in this file touches the PC5 checkout.  The controls that forge a receipt, break the
tree, rewrite history or make a shallow clone all happen in fixture directories that are
removed on cleanup.

Each fixture has: a `main` branch, a bare `origin` (so the default target resolves exactly as
it does in a real clone), the real reconciler under `tools/`, and a two-line stand-in for
`tools/presentation.py` whose only job is to keep `docs/assets/fig.txt` equal to
`docs/source.txt`.  That stand-in is the point: `presentation-check` becomes a falsifiable
gate, so "the receipt was refused because the figures were stale" can actually be tested.

Scenario numbers are the ones the report quotes.

    python3 -m unittest tests.test_dashboard_reconcile -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "dashboard_reconcile.py"
RECEIPT = "docs/evidence/dashboard-reconciliation.json"

README_BODY = """# Fixture

Intro prose that nobody generated.

<!-- pc5:status:begin -->
| id | state |
|---|---|
| M0 | complete |
<!-- pc5:status:end -->

Closing prose.
"""

STUB_PRESENTATION = '''#!/usr/bin/env python3
"""Stand-in for tools/presentation.py: docs/assets/fig.txt must equal docs/source.txt."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source, asset = root / "docs/source.txt", root / "docs/assets/fig.txt"
want = source.read_text() if source.exists() else ""
have = asset.read_text() if asset.exists() else ""

if "sync" in sys.argv:
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text(want)
    sys.exit(0)
if "check" in sys.argv:
    if want != have:
        print("docs/assets/fig.txt: stale - regenerate with `make presentation`",
              file=sys.stderr)
        sys.exit(1)
    print("PRESENTATION OK")
    sys.exit(0)
sys.exit(2)
'''

STUB_DOCS_CHECK = '''#!/usr/bin/env python3
import sys
from pathlib import Path
sys.exit(1 if (Path(__file__).resolve().parents[1] / "docs/BROKEN").exists() else 0)
'''

MAKEFILE = """presentation:
\tpython3 tools/presentation.py sync
presentation-check:
\tpython3 tools/presentation.py check
post-merge: presentation presentation-check
\tpython3 tools/dashboard_reconcile.py --here --record
"""


class Fixture:
    """A repository small enough to reason about, with the real reconciler copied in."""

    def __init__(self, name: str):
        self.directory = Path(tempfile.mkdtemp(prefix=f"pc5-reconcile-{name}-"))
        self.repo = self.directory / "repo"
        self.repo.mkdir(parents=True)
        self.git("init", "-q", "-b", "main", ".")
        for key, value in (("user.email", "fixture@example.invalid"),
                           ("user.name", "Fixture"),
                           ("commit.gpgsign", "false"),
                           ("advice.detachedHead", "false")):
            self.git("config", key, value)
        for path, text in (("README.md", README_BODY),
                           ("docs/source.txt", "9 of 35\n"),
                           ("docs/assets/fig.txt", "9 of 35\n"),     # in step from the start
                           ("docs/architecture/roadmap.json", '{"milestones": []}\n'),
                           ("tools/presentation.py", STUB_PRESENTATION),
                           ("tools/docs_check.py", STUB_DOCS_CHECK),
                           ("tools/dashboard_reconcile.py",
                            TOOL.read_text(encoding="utf-8")),
                           ("Makefile", MAKEFILE)):
            self.write(path, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed the fixture")
        self.origin = self.directory / "origin.git"
        subprocess.run(("git", "init", "-q", "--bare", "--initial-branch=main",
                        str(self.origin)), check=True, capture_output=True, text=True)
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-q", "-u", "origin", "main")
        self.boundary = self.rev_parse("HEAD")

    # ---------------------------------------------------------------- plumbing
    def git(self, *args: str, check: bool = True) -> str:
        done = subprocess.run(("git", *args), cwd=self.repo, capture_output=True, text=True)
        if check and done.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} -> {done.returncode}: "
                                 f"{(done.stderr or done.stdout).strip()}")
        return done.stdout.strip()

    def write(self, path: str, text: str) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def read_file(self, path: str) -> str:
        return (self.repo / path).read_text(encoding="utf-8")

    def commit(self, message: str, empty: bool = False) -> str:
        self.git("add", "-A")
        args = ["commit", "-q", "-m", message] + (["--allow-empty"] if empty else [])
        if self.git("status", "--porcelain") or empty:
            self.git(*args)
        return self.rev_parse("HEAD")

    def sync(self) -> None:
        subprocess.run((sys.executable, "tools/presentation.py", "sync"), cwd=self.repo,
                       check=True, capture_output=True, text=True)

    def sync_and_commit(self, message: str) -> str:
        self.sync()
        return self.commit(message)

    def rev_parse(self, ref: str) -> str:
        return self.git("rev-parse", ref)

    def head(self) -> str:
        return self.rev_parse("HEAD")

    def branch(self, name: str, edits=(), message: str = "feat: work") -> str:
        """A branch off `main` carrying `edits`, left checked out as `main`."""
        self.git("checkout", "-q", "-b", name)
        for path, text in edits:
            self.write(path, text)
        sha = self.commit(message, empty=not edits)
        self.git("checkout", "-q", "main")
        return sha

    def merge(self, name: str, how: str = "--no-ff", message: str = None) -> str:
        if how == "--no-ff":
            self.git("merge", "--no-ff", name, "-m", message or f"Merge branch '{name}'", "-q")
        elif how == "--squash":
            self.git("merge", "--squash", name, "-q")
            self.commit(message or f"{name} (squashed)")
        elif how == "--rebase":
            self.git("rebase", "main", name, "-q")
            self.git("checkout", "-q", name)
            self.git("checkout", "-q", "main")
            self.git("merge", "--ff-only", name, "-q")
        else:
            self.git("merge", how, name, "-q")
        return self.head()

    def push(self) -> None:
        """Publish the fixture's `main`.  A fixture owns its own bare origin, so this is not
        a force-push against anything a person is working on."""
        self.git("push", "-q", "origin", "main")

    # ---------------------------------------------------------------- running the tool
    def reconcile(self, *args: str, push: bool = True) -> subprocess.CompletedProcess:
        if push:
            self.push()
        return subprocess.run((sys.executable, "tools/dashboard_reconcile.py", *args),
                              cwd=self.repo, capture_output=True, text=True)

    def read(self) -> subprocess.CompletedProcess:
        return self.reconcile()

    def record(self, *args: str) -> subprocess.CompletedProcess:
        return self.reconcile("--record", *args)

    def state(self) -> dict:
        return json.loads(self.reconcile("--json").stdout)

    def receipt(self) -> dict:
        """The receipt as committed on `main`, read out of the object store."""
        return json.loads(self.git("show", f"HEAD:{RECEIPT}"))

    def forge_receipt(self, mutate, message: str = "docs: a receipt with a problem") -> None:
        receipt = mutate(self.receipt())
        self.write(RECEIPT, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        self.commit(message)

    def bootstrap(self) -> None:
        """Write the first receipt and commit it, as `--record --bootstrap` is meant to."""
        done = self.reconcile("--record", "--bootstrap", "--boundary", self.boundary,
                              "--inspect", self.boundary, "--note",
                              "fixture: the seed commit was inspected", "--target", "origin/main",
                              push=False)
        if done.returncode != 0:
            raise AssertionError(f"bootstrap failed ({done.returncode}): "
                                 f"{done.stdout}{done.stderr}")
        self.commit("record the reconciliation baseline")
        self.push()

    @property
    def repository_id(self) -> str:
        """`owner/name` out of `remote.origin.url`, the same way the tool derives it."""
        url = self.git("config", "--get", "remote.origin.url")
        import re as _re
        match = _re.search(r"[:/]([^/]+/[^/.]+?)(?:\.git)?$", url or "")
        return match.group(1) if match else (url or "unknown-remote")

    def cleanup(self) -> None:
        for path in self.directory.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o644)
        shutil.rmtree(self.directory, ignore_errors=True)


class ReconcileScenarioTest(unittest.TestCase):
    """One scenario per test method, so a failure names the case rather than a line number."""

    def setUp(self):
        self.fixtures = []

    def tearDown(self):
        for fixture in self.fixtures:
            fixture.cleanup()

    def fixture(self, bootstrapped: bool = True) -> Fixture:
        name = self.id().rsplit(".", 1)[-1].replace("test_", "")
        fx = Fixture(name)
        self.fixtures.append(fx)
        if bootstrapped:
            fx.bootstrap()
        return fx

    def assert_outstanding(self, done, sha=None, label="the commit"):
        self.assertEqual(done.returncode, 1, f"exit {done.returncode}: {done.stdout}"
                         f"{done.stderr}")
        self.assertIn("OUTSTANDING", done.stdout)
        self.assertNotIn("CURRENT —", done.stdout)
        if sha:
            self.assertIn(sha[:12], done.stdout, f"{label} was not named in the report")

    def assert_error(self, done, *needles):
        self.assertEqual(done.returncode, 2, f"exit {done.returncode}: {done.stdout}"
                         f"{done.stderr}")
        output = done.stdout + done.stderr
        for needle in needles:
            self.assertIn(needle, output)
        self.assertNotIn("CURRENT", output)

    def assert_current(self, done):
        self.assertEqual(done.returncode, 0, f"exit {done.returncode}: {done.stdout}"
                         f"{done.stderr}")
        self.assertIn("CURRENT", done.stdout)

    # -------------------------------------------------------------- the first receipt
    def test_s01_a_branch_with_no_receipt_is_outstanding_not_clean(self):
        fx = self.fixture(bootstrapped=False)
        done = fx.read()
        self.assert_outstanding(done)
        self.assertIn(RECEIPT, done.stdout, "the report must say which file is missing")
        self.assertIn("--bootstrap", done.stdout, "and how to write it")

    def test_s02_record_refuses_to_invent_the_first_boundary(self):
        fx = self.fixture(bootstrapped=False)
        done = fx.record()
        self.assert_error(done, "Bootstrap")
        self.assertFalse((fx.repo / RECEIPT).exists(), "a refusal must not write a receipt")

    def test_s03_a_short_boundary_is_rejected(self):
        fx = self.fixture(bootstrapped=False)
        done = fx.reconcile("--record", "--bootstrap", "--boundary", fx.boundary[:7],
                            "--inspect", fx.boundary, "--target", "origin/main", push=False)
        self.assert_error(done, "40-hex")

    def test_s04_a_bootstrap_cannot_name_a_commit_that_has_not_landed(self):
        fx = self.fixture(bootstrapped=False)
        fx.git("checkout", "-q", "-b", "side")
        fx.write("docs/side.md", "work that never merged\n")
        side = fx.commit("work that never merged")
        fx.git("checkout", "-q", "main")
        self.assertNotEqual(side, fx.rev_parse("origin/main"),
                            "the fixture must actually leave the commit off the target")
        fx.push()
        done = fx.reconcile("--record", "--bootstrap", "--boundary", fx.boundary,
                            "--inspect", side, "--target", "origin/main", push=False)
        self.assert_error(done, "not an ancestor", side[:12])
        self.assertFalse((fx.repo / RECEIPT).exists())

    def test_s05_a_bootstrap_has_to_say_what_it_inspected(self):
        fx = self.fixture(bootstrapped=False)
        done = fx.reconcile("--record", "--bootstrap", "--boundary", fx.boundary,
                            "--target", "origin/main", push=False)
        self.assert_error(done, "--inspect")

    def test_s06_a_dry_run_prints_the_receipt_without_writing_it(self):
        fx = self.fixture(bootstrapped=False)
        done = fx.reconcile("--record", "--bootstrap", "--boundary", fx.boundary,
                            "--inspect", fx.boundary, "--target", "origin/main", "--dry-run",
                            push=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('"schema"', done.stdout)
        self.assertFalse((fx.repo / RECEIPT).exists())

    def test_s06b_every_receipt_says_what_it_did(self):
        """`--record` without `--note` still produces a summary; a bootstrap does not invent one."""
        fx = self.fixture()
        self.assertIn("the seed commit was inspected", fx.receipt()["summary"])
        fx.write("docs/source.txt", "36 of 35\n")
        fx.sync_and_commit("roadmap: a milestone closed")
        fx.record()
        fx.commit("record the reconciliation")
        self.assertRegex(fx.receipt()["summary"], r"reconciled 1 commit\(s\) since [0-9a-f]{12}")

    def test_s07_after_a_bootstrap_the_branch_reads_current(self):
        fx = self.fixture()
        self.assert_current(fx.read())
        receipt = fx.receipt()
        self.assertEqual(receipt["schema"], "pc5.dashboard-reconciliation/1")
        self.assertEqual(receipt["reconciled_at"], fx.boundary)
        self.assertEqual(receipt["inspected_commits"], [fx.boundary])
        self.assertIs(receipt["complete"], True)
        self.assertEqual(receipt["repository"], fx.repository_id,
                         "the receipt records which repository it vouches for")
        self.assertIn("/", receipt["repository"], "a repository identity is owner/name shaped")
        self.assertEqual([step["exit_code"] for step in receipt["verification"]], [0, 0],
                         "both gates are recorded, with the exit codes they really returned")
        self.assertEqual([step["argv"][-1] for step in receipt["verification"]],
                         ["check", "tools/docs_check.py"],
                         "the receipt names the commands it ran, not just a verdict")

    # -------------------------------------------------------------- what needs inspecting
    def test_s08_a_metric_changing_commit_must_be_inspected_then_recorded(self):
        fx = self.fixture()
        fx.write("docs/source.txt", "10 of 35\n")
        fx.write("docs/architecture/roadmap.json", '{"milestones": [{"id": "M0"}]}\n')
        commit = fx.commit("roadmap: a milestone closed")   # figures left stale on purpose
        done = fx.read()
        self.assert_outstanding(done, commit, "the roadmap commit")
        self.assertIn("roadmap.json", done.stdout)

        refused = fx.record()
        self.assert_error(refused, "refusing to record", "stale")
        fx.sync()
        fx.commit("presentation: regenerate")
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        fx.commit("record the reconciliation")
        self.assert_current(fx.read())

    def test_s09_record_is_refused_while_the_generated_output_is_stale(self):
        fx = self.fixture()
        fx.write("docs/source.txt", "11 of 35\n")
        fx.commit("roadmap changed, figures not regenerated")
        before = fx.read_file(RECEIPT)
        done = fx.record()
        self.assert_error(done, "refusing to record", "stale")
        self.assertEqual(fx.read_file(RECEIPT), before,
                         "a refusal must leave the receipt that was already there, untouched")

    def test_s10_a_no_op_merge_is_recorded_as_reviewed_not_silently_passed(self):
        fx = self.fixture()
        fx.branch("noop")                                          # empty commit on a branch
        merge = fx.merge("noop")
        self.assertGreater(len(fx.git("rev-list", "--parents", "-n1", merge).split()), 2,
                           "the fixture did not produce a merge commit")
        self.assertEqual(fx.git("diff", "--name-only", f"{merge}^1", merge), "",
                         "a no-op merge must change nothing against its first parent")
        self.assert_outstanding(fx.read(), merge,
                                "the no-op merge — an empty merge is still an unreviewed event")
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        fx.commit("record the reconciliation")
        self.assertIn(merge, fx.receipt()["inspected_commits"],
                      "the no-op merge has to appear in the receipt as a reviewed commit")
        self.assert_current(fx.read())

    def test_s11_a_no_ff_pull_request_merge_is_detected_and_the_loop_closes(self):
        """The shape of PR #40's own merge: generator, Makefile and hand-written README prose."""
        fx = self.fixture()
        fx.branch("feat/dashboard",
                  [("docs/source.txt", "12 of 35\n"),
                   ("tools/thing.py", "VALUE = 1\n"),
                   ("README.md", README_BODY + "\n## A section added by hand\n"),
                   ("Makefile", MAKEFILE + "\nnew-target:\n\t@true\n")])
        merge = fx.merge("feat/dashboard")
        self.assert_outstanding(fx.read(), merge, "the merge that delivered the dashboard")
        self.assertIn("[merge]", fx.read().stdout,
                      "the report must say it is looking at a merge commit")
        fx.sync()          # `make post-merge` runs the generator first; recording follows it
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        # `--record` wrote the receipt into the working tree and the generator refreshed the
        # figure: one commit carries both, which is the reconciliation's own follow-up.
        follow_up = fx.commit("presentation + receipt for the merge")
        self.assert_current(fx.read())
        self.assertIn(merge, fx.receipt()["inspected_commits"])
        state = fx.state()
        self.assertEqual([c["sha"] for c in state["exempt_generated_only"]], [follow_up],
                         "the follow-up that carries the regenerated output is the exempt one, "
                         "which is what stops the loop")

    def test_s12_a_fast_forward_merge_is_just_a_commit_in_the_range(self):
        fx = self.fixture()
        fx.branch("feat/ff", [("docs/source.txt", "13 of 35\n")])
        fx.git("checkout", "-q", "feat/ff")
        fx.sync()
        fx.commit("presentation: keep the figure in step")
        fx.git("checkout", "-q", "main")
        fx.git("merge", "--ff-only", "feat/ff", "-q")
        self.assertEqual(fx.git("rev-list", "--merges", f"{fx.boundary}..HEAD"), "",
                         "`git log --merges` finds nothing in this history — which is exactly "
                         "why an inferred watermark missed it")
        self.assert_outstanding(fx.read())
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        fx.commit("record the reconciliation")
        self.assert_current(fx.read())

    def test_s13_a_squash_merge_is_detected_and_its_stale_figures_are_reported(self):
        fx = self.fixture()
        fx.branch("feat/squash",
                  [("docs/source.txt", "14 of 35\n"),
                   ("docs/architecture/roadmap.json", '{"milestones": [{"id": "M0"}]}\n')])
        squash = fx.merge("feat/squash", how="--squash")
        self.assert_outstanding(fx.read(), squash, "the squash")
        check = subprocess.run((sys.executable, "tools/presentation.py", "check"), cwd=fx.repo,
                               capture_output=True, text=True)
        self.assertEqual(check.returncode, 1,
                         "the figure is stale right now, which is what a squash leaves behind")
        done = fx.record()
        self.assert_error(done, "refusing to record", "stale")
        fx.sync_and_commit("presentation: regenerate after the squash")
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        fx.commit("record the reconciliation")
        self.assert_current(fx.read())

    def test_s14_a_rebase_merge_leaves_every_replayed_commit_named(self):
        fx = self.fixture()
        fx.branch("feat/rebase", [("docs/source.txt", "15 of 35\n")], "feat: part one")
        fx.git("checkout", "-q", "feat/rebase")
        fx.write("docs/notes.md", "part two\n")
        fx.commit("feat: part two")
        fx.git("checkout", "-q", "main")
        fx.merge("feat/rebase", how="--rebase")
        range_shas = fx.git("rev-list", f"{fx.boundary}..HEAD").split()
        self.assertEqual(len(range_shas), 3,
                         "two replayed commits plus the bootstrap's receipt commit, which is "
                         "in the range and exempt — the range is not filtered to be tidy")
        self.assert_outstanding(fx.read())
        fx.sync()
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        fx.commit("record the reconciliation")
        receipt = fx.receipt()
        for sha in range_shas[:2]:
            self.assertIn(sha, receipt["inspected_commits"],
                          "a rebase mints fresh SHAs; naming the branch would not have worked")
        self.assertEqual(receipt["inspected_commits"], sorted(set(receipt["inspected_commits"])),
                         "and each SHA is named once, whatever the topology")
        self.assert_current(fx.read())

    def test_s15_a_direct_commit_to_main_with_no_figure_change_is_still_inspected(self):
        fx = self.fixture()
        fx.write("boot/main.c", "int main(void) { return 0; }\n")
        commit = fx.commit("fix: a code-only change landed straight on main")
        done = fx.read()
        self.assert_outstanding(done, commit)
        self.assertIn("nothing the dashboard counts", done.stdout,
                      "the report must say the figures are unaffected, not that all is well")
        self.assertEqual(fx.git("diff", "--name-only", f"{commit}~1", commit), "boot/main.c")
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        fx.commit("record the reconciliation")
        self.assert_current(fx.read())
        entry = fx.receipt()["commits_inspected_now"][0]
        self.assertEqual(entry["dashboard_drivers"], [],
                         "a code-only commit is recorded as touching no dashboard driver, "
                         "which is why regenerating nothing is the right outcome")
        self.assertFalse(fx.receipt()["figures_changed"])

    def test_s16_an_interrupted_post_merge_leaves_the_state_outstanding_and_recovers(self):
        fx = self.fixture()
        fx.write("docs/source.txt", "16 of 35\n")
        commit = fx.commit("roadmap: closed a milestone")
        fx.write("tools/presentation.py", "raise SystemExit('the generator crashed')\n")
        fx.commit("build: the generator on main is broken")
        before = fx.read_file(RECEIPT)
        self.assert_error(fx.record(), "refusing to record")
        self.assertEqual(fx.read_file(RECEIPT), before,
                         "a refusal must not move the boundary forward")
        self.assert_outstanding(fx.read(), commit)
        self.assert_outstanding(fx.read(), commit, "and stays outstanding on the next run")
        fx.write("tools/presentation.py", STUB_PRESENTATION)
        fx.commit("build: generator fixed")
        fx.sync_and_commit("presentation: regenerate after fixing the generator")
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        fx.commit("record the reconciliation")
        self.assert_current(fx.read())

    # -------------------------------------------------------------- the exemption rule
    def test_s17_a_regeneration_clears_nothing_it_did_not_inspect(self):
        fx = self.fixture()
        empty = fx.git("commit", "-q", "--allow-empty", "-m",
                       "an empty commit landed on main") or fx.head()
        self.assert_outstanding(fx.read())
        fx.write("docs/source.txt", "17 of 35\n")
        metric = fx.commit("roadmap: a milestone closed")
        fx.write("docs/assets/fig.txt", "17 of 35\n")
        regeneration = fx.commit("presentation: the SVG happens to be regenerated here")
        done = fx.read()
        self.assert_outstanding(done, metric)
        state = fx.state()
        self.assertEqual([c["sha"] for c in state["outstanding"]],
                         [empty, metric],
                         "the empty commit and the roadmap change are both still outstanding; "
                         "a later regeneration must not absorb them")
        self.assertIn(regeneration, [c["sha"] for c in state["exempt_generated_only"]])

    def test_s18_a_readme_edit_inside_a_generated_block_is_exempt(self):
        fx = self.fixture()
        fx.write("README.md", fx.read_file("README.md").replace(
            "| M0 | complete |", "| M0 | complete |\n| M18 | in_progress |"))
        commit = fx.commit("README block updated by hand")
        done = fx.read()
        self.assert_current(done)
        self.assertEqual([c["sha"] for c in done.stdout.splitlines() if "OUTSTANDING" in c], [],
                         "a line inside a generated span cannot be an unreviewed change: "
                         "presentation-check compares the whole block against a fresh render")
        state = fx.state()
        self.assertIn(commit, [c["sha"] for c in state["exempt_generated_only"]])
        self.assertEqual(state["outstanding"], [])
        self.assertEqual(len(state["recorded_in_receipt"]), 0,
                         "exempt commits are never listed as reviewed; they are simply not "
                         "an inspection event")

    def test_s19_a_readme_edit_outside_a_generated_block_must_be_inspected(self):
        fx = self.fixture()
        fx.write("README.md", fx.read_file("README.md").replace(
            "Closing prose.", "Closing prose.\n\nA claim about hardware, added by hand.\n"))
        commit = fx.commit("docs: a sentence that is not generated")
        done = fx.read()
        self.assert_outstanding(done, commit)
        entry = fx.state()["outstanding"][0]
        lines = entry["readme_lines_outside_generated_blocks"]
        self.assertTrue(lines, "the tool must point at the lines, not just at the file")
        span = [i for i, text in enumerate(fx.read_file("README.md").splitlines(), start=1)
                if "pc5:status:" in text]
        self.assertTrue(all(line > span[1] or line < span[0] for line in lines),
                        f"a line inside the generated block was reported: {lines} vs {span}")
        self.assertIn("README.md", " ".join(entry["changed_paths"]))

    def test_s20_several_merges_in_one_session_are_each_recorded(self):
        fx = self.fixture()
        shas = []
        for index in range(3):
            fx.branch(f"feat/{index}", [(f"docs/file{index}.md", f"change {index}\n")],
                      f"feat: merge {index}")
            shas.append(fx.merge(f"feat/{index}"))
        done = fx.read()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual([c["sha"] for c in fx.state()["outstanding"]], shas,
                         "three merges, three named outstanding commits — none may absorb "
                         "another because the third one regenerated the figure")
        for sha in shas:
            self.assertIn(sha[:12], done.stdout)
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        fx.commit("record three reconciliations at once")
        receipt = fx.receipt()
        self.assertEqual(receipt["inspected_commits"], sorted(set(receipt["inspected_commits"])))
        self.assertEqual(len(receipt["commits_inspected_now"]), 3)
        self.assert_current(fx.read())

    # -------------------------------------------------------------- receipt integrity
    def test_s21_a_receipt_naming_a_commit_that_has_not_landed_is_an_error(self):
        fx = self.fixture()
        fx.git("checkout", "-q", "-b", "future")
        fx.write("docs/future.md", "not merged\n")
        future = fx.commit("work that is not on main")
        fx.git("checkout", "-q", "main")
        self.assertNotIn(future, fx.git("rev-list", "main").split())

        def add(receipt):
            receipt["inspected_commits"] = sorted(set(receipt["inspected_commits"]) | {future})
            return receipt

        fx.forge_receipt(add, "docs: a receipt that pre-approves a merge that has not happened")
        done = fx.read()
        self.assert_error(done, "not an ancestor of", future[:12])

    def test_s22_a_receipt_that_admits_incompleteness_stays_outstanding(self):
        fx = self.fixture()

        def mark(receipt):
            receipt["complete"] = False
            receipt["incomplete_reason"] = "the boot gate could not run on this host"
            return receipt

        fx.forge_receipt(mark, "docs: an honest, unfinished receipt")
        done = fx.read()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("boot gate could not run", done.stdout)
        self.assertNotIn("CURRENT", done.stdout)

    def test_s23_a_receipt_written_over_a_red_gate_is_rejected(self):
        fx = self.fixture()

        def fail(receipt):
            receipt["verification"] = [{"label": "presentation figures and README blocks are "
                                                 "current", "argv": ["x"], "exit_code": 1}]
            return receipt

        fx.forge_receipt(fail, "docs: a receipt that records a failing gate")
        self.assert_error(fx.read(), "failing verification")

    def test_s24_a_malformed_or_foreign_receipt_is_an_error_not_a_clean_bill(self):
        fx = self.fixture()
        fx.write(RECEIPT, "{ not json\n")
        fx.commit("docs: broken receipt")
        self.assert_error(fx.read(), "not valid JSON")

        fx.write(RECEIPT, json.dumps({"schema": "pc5.dashboard-reconciliation/0",
                                      "reconciled_at": fx.boundary}) + "\n")
        fx.commit("docs: a receipt from another schema")
        self.assert_error(fx.read(), "schema")

        fx.write(RECEIPT, json.dumps({"schema": "pc5.dashboard-reconciliation/1",
                                      "reconciled_at": "0" * 40}) + "\n")
        fx.commit("docs: a boundary that is not a commit")
        self.assert_error(fx.read(), "not a commit")

    def test_s25_a_shallow_clone_cannot_claim_the_range_is_clean(self):
        fx = self.fixture()
        for index in range(3):
            fx.write(f"docs/file{index}.md", f"change {index}\n")
            fx.commit(f"commit {index}")
        fx.push()
        shallow = fx.directory / "shallow"
        # `file://` matters: git silently ignores `--depth` for a local-path clone, and a
        # clone that is not shallow would prove nothing about shallow clones.
        clone = subprocess.run(("git", "clone", "-q", "--depth", "1", "--",
                                f"file://{fx.origin}", str(shallow)), cwd=fx.repo,
                               capture_output=True, text=True)
        self.assertEqual(clone.returncode, 0, clone.stderr)
        self.assertTrue((shallow / ".git/shallow").exists(),
                        "the fixture clone is not shallow, so this scenario proves nothing")
        self.assertEqual(len(subprocess.run(("git", "rev-list", "--all"), cwd=shallow,
                                             capture_output=True, text=True).stdout.split()), 1,
                         "a depth-1 clone of a 5-commit branch should hold exactly one commit")
        done = subprocess.run((sys.executable, str(TOOL), "--repo", str(shallow)),
                              cwd=shallow, capture_output=True, text=True)
        self.assertEqual(done.returncode, 2,
                         f"exit {done.returncode}: {done.stdout}{done.stderr}")
        self.assertIn("shallow", done.stdout + done.stderr)
        self.assertIn("unshallow", done.stdout + done.stderr, "and how to fix it")
        self.assertNotIn("CURRENT", done.stdout)

    def test_s26_a_missing_origin_main_is_an_error_that_names_the_fix(self):
        fx = self.fixture()
        # `origin/main` still resolves from the last fetch, so the failure a user actually
        # hits is a repository where that ref was never recorded at all.
        fx.git("remote", "remove", "origin")
        done = fx.reconcile("--target", "origin/main", push=False)
        self.assert_error(done, "git fetch origin main")

        # the checked-out branch is deliberately *not* a fallback
        fx.git("checkout", "-q", "-b", "feature")
        done = fx.reconcile(push=False)
        self.assert_error(done, "--here", "origin/main")

    def test_s27_here_answers_about_the_checked_out_ref_and_says_so(self):
        fx = self.fixture()
        fx.git("checkout", "-q", "-b", "feature/off-main")
        fx.write("docs/source.txt", "27 of 35\n")
        commit = fx.sync_and_commit("feat: only on the feature branch")
        done = fx.reconcile("--here", push=False)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("--here", done.stdout)
        self.assertIn(commit[:12], done.stdout)
        state = json.loads(fx.reconcile("--here", "--json", push=False).stdout)
        self.assertTrue(state["target_ref"].endswith("(--here)"))
        self.assertEqual(state["target"], commit)

    def test_s28_recording_twice_changes_nothing(self):
        fx = self.fixture()
        fx.write("docs/source.txt", "28 of 35\n")
        fx.sync_and_commit("roadmap: a milestone closed, figure regenerated")
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        written = fx.read_file(RECEIPT)
        fx.commit("record")
        second = fx.record()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("nothing to record", second.stdout + second.stderr)
        self.assertEqual(fx.read_file(RECEIPT), written,
                         "an idempotent tool must not rewrite the receipt it already wrote")
        self.assert_current(fx.read())

    def test_s29_record_refuses_a_dirty_source_file(self):
        fx = self.fixture()
        fx.write("docs/source.txt", "29 of 35\n")
        fx.sync_and_commit("roadmap: a milestone closed")
        fx.write("tools/uncommitted.py", "work in progress\n")
        done = fx.record()
        self.assert_error(done, "tools/uncommitted.py", "commit the source change first")
        os.remove(fx.repo / "tools/uncommitted.py")
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)

    def test_s30_a_receipt_carried_over_from_a_fork_is_audited_not_trusted(self):
        fx = self.fixture()
        other = Fixture("fork-target")
        self.fixtures.append(other)
        other.bootstrap()
        foreign = json.loads(other.git("show", f"HEAD:{RECEIPT}"))
        foreign["reconciled_at"] = fx.boundary
        foreign["inspected_commits"] = [fx.boundary]
        fx.write(RECEIPT, json.dumps(foreign, indent=2, sort_keys=True) + "\n")
        fx.commit("docs: a receipt copied in from a fork")
        done = fx.read()
        self.assertIn("NOTE the receipt vouches for", done.stdout,
                      "the identity mismatch is stated, not silently accepted")
        self.assertIn(other.repository_id, done.stdout)
        self.assertIn(fx.repository_id, done.stdout)
        self.assertEqual(fx.state()["repository_matches"], False)
        self.assertIn("the SHAs below were resolved here", done.stdout,
                      "and the report says what it did about it")

    def test_s36_a_merge_of_reviewed_content_clears_the_moment_it_lands(self):
        """The case a receipt cannot pre-name, and must not have to.

        A pull-request merge is a SHA that did not exist when the receipt was written, so a
        rule of "name every merge" would leave every reconciliation needing a second receipt
        forever. A rule of "a merge is a merge" would let any merge self-approve. So the
        exemption is about content: if everything the merge introduces is named or is
        reconciliation output, the merge brought in nothing unreviewed.
        """
        fx = self.fixture()
        fx.write("docs/source.txt", "36 of 35\n")
        work = fx.sync_and_commit("roadmap: a milestone closed, figure regenerated")
        fx.git("checkout", "-q", "-b", "pr/like-40", work)      # the PR *is* this commit
        fx.git("checkout", "-q", "main")
        named_before = fx.receipt()["inspected_commits"]
        self.assertNotIn(work, named_before, "the work was committed after the bootstrap")
        self.assert_outstanding(fx.read(), work)
        fx.record()                                              # names `work`
        fx.commit("record the reconciliation")
        self.assertIn(work, fx.receipt()["inspected_commits"])
        # main now moves forward and the reviewed commit arrives as a merge of it
        fx.write("docs/other.md", "something else on main\n")
        fx.sync_and_commit("main moves on")
        merge = fx.git("merge", "--no-ff", "pr/like-40", "-m", "Merge pull request #40",
                       check=False) or fx.head()
        if len(fx.git("rev-list", "--parents", "-n1", "HEAD").split()) < 3:
            self.skipTest("git would not merge a branch already contained in main")
        merge = fx.head()
        state = fx.state()
        self.assertEqual([c["sha"] for c in state["outstanding"]], [],
                         f"the merge re-opened reviewed work: {merge[:12]}\n{state}")
        self.assertIn(merge, state["merges_cleared_by_reviewed_content"],
                      "and the report has to say *why* it is clear, not just that it is")
        self.assert_current(fx.read())

    def test_s37_a_merge_is_never_exempt_for_being_a_merge(self):
        """The other side of the rule: one unreviewed commit inside and it is outstanding."""
        fx = self.fixture()
        fx.write("docs/source.txt", "37 of 35\n")
        reviewed = fx.sync_and_commit("roadmap: reviewed change")
        fx.record()
        fx.commit("record the reconciliation")
        fx.git("checkout", "-q", "-b", "mixed")
        fx.write("boot/kernel.c", "int surprise(void) { return 1; }\n")
        hidden = fx.commit("a change nobody looked at")
        fx.git("checkout", "-q", "main")
        merge = fx.merge("mixed")
        state = fx.state()
        self.assertEqual([c["sha"] for c in state["outstanding"]], [merge],
                         "the first-parent line carries the merge, and the merge is the thing "
                         "that has to be recorded — the commit it smuggled in is named in its "
                         "diagnostic rather than invented as a range member")
        entry = state["outstanding"][0]
        self.assertIn("1 unreviewed commit", entry["reason"], entry["reason"])
        report = fx.read().stdout
        self.assertIn(hidden[:12], report,
                      "the report has to point at what the merge brought in unreviewed")
        self.assertIn("it introduces 1 unreviewed commit", report)
        self.assertNotIn("CURRENT", report)
        self.assertIn(reviewed, fx.receipt()["inspected_commits"],
                      "the earlier commit stays named; what changed is that a merge on top of "
                      "an unreviewed one is not exempt")
        # recording it clears both, and nothing else
        fx.sync()
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        fx.commit("record the reconciliation")
        receipt = fx.receipt()
        self.assertIn(merge, receipt["inspected_commits"],
                      "a merge with unreviewed content has to be named itself")
        self.assert_current(fx.read())

    def test_s38_a_squash_of_reviewed_commits_still_has_to_be_recorded(self):
        """The asymmetry is deliberate: a squash mints a new SHA and destroys the ancestry.

        There is no history to point at, so the honest answer is "one more reconciliation",
        not "a merge is a merge, so let it through".
        """
        fx = self.fixture()
        fx.write("docs/source.txt", "39 of 35\n")
        work = fx.sync_and_commit("roadmap: a milestone closed")
        fx.record()
        fx.commit("record the reconciliation")
        self.assertIn(work, fx.receipt()["inspected_commits"])
        fx.git("checkout", "-q", "-b", "squashed")
        fx.write("docs/source.txt", "40 of 35\n")
        fx.sync_and_commit("a second change that will be squashed away")
        fx.git("checkout", "-q", "main")
        squash = fx.merge("squashed", how="--squash")
        self.assert_outstanding(fx.read(), squash,
                                "the squash is a new SHA; naming its branch is not the same "
                                "thing, and the tool must not pretend otherwise")
        self.assertIn(squash, [c["sha"] for c in fx.state()["outstanding"]])

    def test_s31_an_up_to_date_merge_creates_no_commit_and_invents_no_work(self):
        """The other half of the no-op story, and the one a watermark could not tell apart.

        Merging a branch that main already contains produces no commit at all: Git says
        "Already up to date." and the target does not move.  There is genuinely nothing to
        inspect, and the tool must say CURRENT rather than manufacture an outstanding item —
        while s10 shows that a merge commit with an empty diff *is* an event and does get
        recorded.  A rule that cannot tell those two apart is either noisy or blind.
        """
        fx = self.fixture()
        fx.git("branch", "-f", "already", fx.head())
        before = fx.head()
        said = fx.git("merge", "--no-ff", "already", "-m", "Merge 'already'", check=False)
        self.assertIn("Already up to date", said,
                      f"the fixture did not reproduce the case, git said: {said!r}")
        self.assertEqual(fx.head(), before, "and no commit was created")
        self.assert_current(fx.read())
        # the empty-diff merge commit, by contrast, is not nothing
        fx.branch("net-zero")
        fx.git("checkout", "-q", "net-zero")
        fx.write("docs/source.txt", "net zero\n")
        fx.commit("a change to a figure input")
        fx.write("docs/source.txt", "9 of 35\n")     # back to exactly what main holds
        fx.commit("and its reversal")
        fx.git("checkout", "-q", "main")
        merge = fx.merge("net-zero")
        self.assertEqual(fx.git("diff", "--name-only", f"{merge}^1", merge), "",
                         "the fixture's net-zero merge must change nothing against main")
        self.assert_outstanding(fx.read(), merge, "the empty-diff merge commit")

    def test_s32_the_receipt_is_read_from_the_target_not_the_working_tree(self):
        """A dirty, half-written receipt on disk must not be believed as `main`'s state."""
        fx = self.fixture()
        fx.write("docs/source.txt", "32 of 35\n")
        commit = fx.sync_and_commit("roadmap: a milestone closed")
        honest = fx.receipt()
        # an uncommitted edit that claims the commit was already inspected
        fx.write(RECEIPT, json.dumps(dict(honest, inspected_commits=sorted(
            set(honest["inspected_commits"]) | {commit}), reconciled_at=commit),
            indent=2, sort_keys=True) + "\n")
        done = fx.read()                     # pushes `main`, not the uncommitted edit
        self.assert_outstanding(done, commit, "the roadmap commit; the working tree's forged "
                                             "receipt is not what origin/main says")
        state = json.loads(fx.reconcile("--json").stdout)
        self.assertEqual(state["boundary"], fx.boundary,
                         "the answer starts from the committed boundary, not from the "
                         "uncommitted forgery")
        self.assertEqual(fx.record().returncode, 0,
                         "recording reads the same receipt and so finds the same one commit")

    def test_s33_a_docs_only_merge_is_inspected_even_though_no_figure_moves(self):
        fx = self.fixture()
        fx.branch("docs/only",
                  [("docs/KNOWN_LIMITATIONS.md", "A limitation, written down.\n")],
                  "docs: a limitation recorded")
        merge = fx.merge("docs/only")
        done = fx.read()
        self.assert_outstanding(done, merge)
        self.assertIn("nothing the dashboard counts", done.stdout)
        self.assertEqual(fx.record().returncode, 0, fx.record().stdout + fx.record().stderr)
        fx.commit("record")
        self.assertFalse(fx.receipt()["figures_changed"])
        self.assertFalse(fx.receipt()["readme_changed"])
        self.assert_current(fx.read())

    def test_s34_the_report_and_the_json_agree(self):
        fx = self.fixture()
        fx.write("docs/source.txt", "34 of 35\n")
        commit = fx.commit("roadmap: a milestone closed")
        text = fx.read()
        state = fx.state()
        self.assertEqual([c["sha"] for c in state["outstanding"]], [commit])
        self.assertEqual(state["target_ref"], "origin/main")
        self.assertEqual(len(state["target"]), 40)
        self.assertEqual(state["boundary"], fx.boundary)
        self.assertEqual(state["receipt_path"], RECEIPT)
        self.assertEqual(state["repository_id"], fx.repository_id)
        self.assertIs(state["repository_matches"], True)
        self.assertEqual(state["notes"], [],
                         "local main and origin/main are the same commit here, so there is "
                         "nothing to warn about")
        for sha in (commit, fx.boundary):
            self.assertIn(sha[:12], text.stdout)
        self.assertEqual(text.returncode, 1)

    def test_s35_make_post_merge_is_the_whole_round_trip(self):
        """`make post-merge` regenerates, checks and records, and leaves only the receipt dirty.

        This is the promise AGENTS.md makes after a merge into `main`, run through make so a
        missing dependency in the recipe shows up as a failure here rather than in CI that
        does not exist.
        """
        fx = self.fixture()
        fx.branch("feat/make", [("docs/source.txt", "35 of 35\n")], "feat: a figure input moves")
        merge = fx.merge("feat/make")
        done = subprocess.run(("make", "-s", "post-merge"), cwd=fx.repo, capture_output=True,
                              text=True)
        self.assertEqual(done.returncode, 0,
                         f"make post-merge exited {done.returncode}:\n{done.stdout}"
                         f"\n{done.stderr}")
        status = fx.git("status", "--porcelain")
        self.assertIn(RECEIPT, status, "post-merge leaves the receipt to commit")
        self.assertIn("docs/assets/fig.txt", status,
                      "and the figure the merge made stale, regenerated")
        self.assertNotIn("docs/source.txt", status,
                         "a source file is never written by post-merge: " + status)
        self.assertEqual(sorted(line.split(None, 1)[1].strip() for line in status.splitlines()),
                         sorted(["docs/assets/fig.txt", RECEIPT]),
                         "exactly two files, both generator output")
        fx.commit("record the reconciliation")
        self.assertIn(merge, fx.receipt()["inspected_commits"])
        self.assert_current(fx.read())
        again = subprocess.run(("make", "-s", "post-merge"), cwd=fx.repo, capture_output=True,
                               text=True)
        self.assertEqual(again.returncode, 0,
                         f"a second post-merge must be a no-op, not an error:\n{again.stdout}"
                         f"\n{again.stderr}")
        self.assertIn("nothing to record", again.stdout + again.stderr)


    # --------------------------------------- the exemption is tested, not assumed
    def test_s39_generated_output_only_is_current_only_because_it_was_checked(self):
        """The exemption is a promise about the output, so the output is checked.

        The path test came with a premise attached — "presentation-check proves this content is
        what the generator produces" — and this command never ran presentation-check. So a commit
        that writes a figure by hand, or deletes one, read as CURRENT, and a receipt naming its
        own unreviewed work walked away green. Both are the exact false green this tool exists to
        remove, and the audit that found them ran them as scenarios, not as opinions.
        """
        fx = self.fixture()
        fx.write("docs/assets/fig.txt", "written by hand, not by the generator\n")
        fx.commit("docs: a hand-written figure")
        done = fx.read()
        self.assertEqual(done.returncode, 1,
                         f"a hand-written figure was reported as CURRENT:\n{done.stdout}"
                         f"{done.stderr}")
        self.assertIn("not what its data produces", done.stdout)
        state = fx.state()
        self.assertEqual(state["outstanding"], [],
                         "the commits are exempt on their paths; what fails is the output itself, "
                         "and it is reported as its own finding rather than blamed on a commit")
        self.assertTrue(state["generated_output_agreement"],
                        "the JSON has to carry the refusal the text mode reports, or a script "
                        "reading --json would see a clean range")
        self.assert_current(fx.read()) if False else None
        fx.sync_and_commit("regenerate the figure from its data")
        self.assert_current(fx.read())
        self.assertEqual(fx.state()["generated_output_agreement"], "",
                         "the exemption is earned once the generator has run: same commits, "
                         "same paths, different answer")

    def test_s40_record_verifies_even_when_it_has_nothing_to_write(self):
        """`--record` may not answer 0 about stale output because no receipt update was due.

        The early return used to sit above `verify()`, so a range of generated-output-only commits
        exited 0 with the gates never run — the one path where the receipt's own claim
        ("verification: exit 0") was not being earned by anything.
        """
        fx = self.fixture()
        before = fx.git("show", f"HEAD:{RECEIPT}")
        fx.write("docs/assets/fig.txt", "hand-written again\n")
        fx.commit("docs: a hand-written figure, nothing else in range")
        done = fx.record()
        self.assertEqual(done.returncode, 2,
                         f"`--record` accepted a stale figure:\n{done.stdout}{done.stderr}")
        self.assertIn("refusing to record", done.stdout + done.stderr)
        self.assertNotIn(RECEIPT, fx.git("status", "--porcelain"),
                         "a refusal writes nothing, not even a half-finished receipt")
        self.assertEqual(fx.git("show", f"HEAD:{RECEIPT}"), before,
                         "the receipt on main is untouched by a refused run")

    def test_s41_a_no_op_merge_is_recorded_as_reviewed_not_skipped(self):
        """AGENTS.md §1 step 2, in code: a merge that introduces nothing is still a merge.

        `reviewed_ancestry` cleared any merge whose introduced set was empty. That is every
        already-contained branch merged with `--no-ff`, so a merge nobody had looked at closed the
        duty on the strength of having happened — which is the self-approval the content rule was
        written to prevent, arriving from the other side.
        """
        fx = self.fixture()
        tree = fx.git("rev-parse", "HEAD^{tree}")
        ancestor = fx.git("rev-parse", "HEAD~1")
        merge = fx.git("commit-tree", tree, "-p", fx.head(), "-p", ancestor, "-m",
                       "Merge a branch that had nothing main did not already have")
        fx.git("update-ref", "refs/heads/main", merge)
        done = fx.read()
        self.assertEqual(done.returncode, 1,
                         f"a no-op merge was waved through:\n{done.stdout}{done.stderr}")
        self.assertIn("no-op merge is recorded as reviewed", done.stdout)
        self.assertEqual(fx.state()["outstanding"][0]["sha"], merge,
                         "and it is named, so the operator can see which merge is being asked about")
        self.assertEqual(fx.record().returncode, 0, "recording it is allowed: the gates pass")
        fx.commit("record the no-op merge")
        self.assert_current(fx.read())
        self.assertIn(merge, fx.receipt()["inspected_commits"])

    def test_s42_a_receipt_written_inside_the_range_does_not_certify_itself(self):
        """A commit cannot clear itself by naming itself in the receipt, in either forgery.

        The receipt is read from the target, which is right for staleness and wrong for
        self-certification: a landing commit that edits the receipt used to report CURRENT with no
        gate run at all. Two forgeries are tested. Naming the commit, leaving the boundary alone,
        makes the unreviewed source commit read as "named in the receipt". Moving the boundary
        past it removes the commit from the range entirely. In both, the only thing still holding
        the answer up is the exemption's premise — that generated output matches its data — and
        the premise is now tested, so both come back OUTSTANDING. What remains is governance: a
        hand-written receipt whose figures *do* match is a claim a human made, and AGENTS.md says
        the receipt is auditable rather than trusted.
        """
        def assert_refused(fx, note):
            done = fx.read()
            self.assertEqual(done.returncode, 1, f"{note}: {done.stdout}{done.stderr}")
            self.assertIn("not what its data produces", done.stdout, note)
            state = fx.state()
            self.assertEqual(state["outstanding"], [],
                             f"{note}: no commit is blamed; the exemption itself is what fails, "
                             "and the report says so rather than sending the operator to inspect "
                             "a commit that only touched generated output")
            self.assertTrue(state["generated_output_agreement"], note)

        fx = self.fixture()
        fx.write("docs/source.txt", "42 of 35\n")
        fx.commit("feat: a milestone closed, and the receipt names it")
        landing = fx.head()
        fx.forge_receipt(lambda receipt: dict(
            receipt, inspected_commits=sorted(set(receipt["inspected_commits"]) | {landing}),
            summary="hand-written: names the commit that delivered it"))
        assert_refused(fx, "the naming forgery")
        state = fx.state()
        self.assertIn(landing, [c["sha"] for c in state["recorded_in_receipt"]],
                      "the self-naming is honoured as a classification — a receipt that names a "
                      "commit says it was read — and the verdict turns on the premise instead")
        self.assertIn(landing, [c["sha"] for c in state["commits_since_boundary"]])

        other = self.fixture()
        other.write("docs/source.txt", "42 of 35\n")
        other.commit("feat: the same change, with the boundary moved past it")
        moved = other.head()
        other.forge_receipt(lambda receipt: dict(
            receipt, reconciled_at=moved,
            inspected_commits=sorted(set(receipt["inspected_commits"]) | {moved})))
        assert_refused(other, "the boundary forgery")
        self.assertEqual([c["sha"] for c in other.state()["commits_since_boundary"]],
                         [other.head()],
                         "moving the boundary past a commit really does take it out of the range: "
                         "that is why the exemption cannot go untested")
        # and the honest round trip still closes, so the refusal is about the stale output only
        other.sync()
        other.commit("regenerate the figure")
        self.assert_current(other.read())

    def test_s43_flags_that_belong_to_bootstrap_are_not_silently_ignored(self):
        """`--record --boundary X` used to ignore `--boundary` and answer 0.

        An ignored flag is worse than a refused one: the operator believes they set a boundary.
        """
        fx = self.fixture()
        zeros = "0" * 40
        done = fx.reconcile("--record", "--boundary", zeros, "--inspect", zeros)
        self.assertEqual(done.returncode, 2, f"--boundary without --bootstrap was ignored: "
                                            f"{done.stdout}{done.stderr}")
        self.assertIn("--bootstrap", done.stdout + done.stderr)
        self.assertNotIn("nothing to record", done.stdout + done.stderr)
        note = fx.reconcile("--note", "a summary nobody asked for")
        self.assertEqual(note.returncode, 2,
                         f"--note without --record wrote nothing and said nothing: {note.stdout}")
        self.assertIn("--note writes into a receipt", note.stdout + note.stderr)


    def test_s44_the_report_names_the_commits_a_receipt_vouched_for(self):
        """`recorded_in_receipt` and the merge statistic were always empty, and nothing noticed.

        Both lists are filled from a classification that clears a commit, and the code tested
        `requires_inspection` first — so every named commit and every ancestry-cleared merge was
        swept into `exempt_generated_only` instead. The report line then read "0 already named in
        the receipt" while the receipt named several, and `merges_cleared_by_reviewed_content` was
        dead. s36 asserts the second of those and *skips* when git declines to merge a contained
        branch, which is how it stayed unnoticed: the assertion never ran. This scenario reaches
        the same state through the flow a pull request actually takes — record on the branch, then
        merge it — so there is nothing for git to decline and no skip available.
        """
        fx = self.fixture(bootstrapped=False)
        fx.bootstrap()
        fx.git("checkout", "-q", "-b", "pr/41")
        fx.write("docs/source.txt", "44 of 35\n")
        work = fx.sync_and_commit("feat: a milestone closes, figure regenerated")
        recorded = fx.reconcile("--record", "--here", "--note", "fixture: reviewed on the branch",
                               push=False)
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        fx.commit("record the reconciliation on the branch")
        branch_tip = fx.head()
        fx.git("checkout", "-q", "main")
        fx.git("merge", "-q", "--no-ff", "pr/41", "-m", "Merge pull request 41 from pr/41")
        merge = fx.head()
        self.assertEqual(len(fx.git("rev-list", "--parents", "-n1", "HEAD").split()), 3,
                         "this must be a real merge commit, not a fast-forward")
        state = fx.state()
        self.assertIn(work, state["inspected_commits"],
                      "the branch work was recorded before the merge, which is the whole point")
        self.assertIn(merge, state["merges_cleared_by_reviewed_content"],
                      "the merge is clear *because of its content*, and the state has to say so")
        self.assertEqual(state["outstanding"], [], f"{state}\n{fx.read().stdout}")
        done = fx.read()
        self.assert_current(done)
        self.assertIn("cleared because every commit they introduce is reviewed", done.stdout,
                      "the line an operator reads has to carry the statistic, not just the verdict")
        self.assertIn("1 on the first-parent line (0 needing inspection", done.stdout,
                      "the merge is the only first-parent commit, it is not counted as needing "
                      "inspection, and it is not swept into `generated-output only` either — which "
                      "is exactly the misfiling this scenario exists to catch")
        self.assertIn("1 merge(s) cleared", done.stdout)
        named = [c["sha"] for c in state["commits_since_boundary"]]
        self.assertEqual(len(named), 1,
                         f"only the merge is on the first-parent line: {named}")
        cleared = state["merges_cleared_by_reviewed_content"]
        self.assertEqual(cleared, [merge],
                         "and it is accounted for as a merge cleared by its content")
        del branch_tip


class TheToolItselfIsSoundTest(unittest.TestCase):
    """Properties of the script that need no repository, checked against its source.

    These are cheap and they are the ones that regress first: a helper that swallows an error,
    a dependency on the clock or on the network, an exit code that stops meaning what the
    docstring says it means.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = TOOL.read_text(encoding="utf-8")

    def test_no_git_failure_becomes_an_empty_answer(self):
        self.assertNotIn("subprocess.check_output", self.source)
        self.assertNotIn("check=True", self.source)
        self.assertNotIn("except Exception", self.source)
        self.assertNotIn("contextlib.suppress", self.source)
        self.assertNotIn("|| true", self.source)
        git_body = self.source[self.source.index("def git("):self.source.index("def try_git(")]
        self.assertIn("raise ReconcileError", git_body,
                      "`git()` must raise; only try_git() may return nothing, and only where "
                      "an absent ref is itself the answer")
        self.assertNotIn("return \"\"", git_body, "git() may not answer with an empty string")
        self.assertIn("def try_git", self.source)
        self.assertEqual(self.source.count("def try_git"), 1,
                         "one swallow point, so it can be audited in one place")

    def test_try_git_is_only_used_where_absence_is_meaningful(self):
        calls = sorted(set(re.findall(r'try_git\(repo, "(\w[\w -]*)"', self.source)))
        self.assertTrue(calls)
        allowed = ("rev-parse", "cat-file", "show", "config", "ls-tree", "symbolic-ref")
        for call in calls:
            self.assertTrue(call.startswith(allowed),
                            f"`git {call}` swallows a failure it should report")
        self.assertNotIn('try_git(repo, "merge-base"', self.source,
                         "ancestry answers by exit code, so it must go through probe()")

    def test_an_unreadable_range_is_never_read_as_an_empty_one(self):
        """`range_commits` must not swallow anything: an empty range means "nothing landed".

        A `try/except` around the walk would turn "this repository cannot tell me what is in
        the range" into "the range is empty", which is the CURRENT result this tool exists to
        make impossible. The rule is structural, so it is checked structurally.
        """
        body = self.source[self.source.index("def range_commits"):
                           self.source.index("def generated_spans")]
        self.assertNotIn("try:", body, "the range walk may not recover from a git failure")
        self.assertNotIn("except", body, "\u2026and may not swallow one")
        self.assertIn('git(repo, "rev-list"', body, "it must go through the raising helper")

    def test_no_clock_and_no_network(self):
        for forbidden in ("datetime", "time.time", "urllib", "requests", "socket",
                          "github.com", "GITHUB_TOKEN"):
            self.assertNotIn(forbidden, self.source,
                             f"{forbidden}: a receipt that reads the clock or reaches the "
                             "network is not reproducible, and a hard-coded host is a silent "
                             "dependency")
        self.assertIn("boundary_date", self.source,
                      "the only date in a receipt comes from the commits it names")

    def test_the_three_exit_codes_are_the_documented_ones(self):
        main = self.source[self.source.index("def main("):]
        body = self.source[self.source.index("def report("):]
        self.assertIn("return 1 if verdict else 0", body,
                      "both output modes must answer with one verdict")
        self.assertIn("verdict = bool(state[\"outstanding\"]) or bool(", body,
                      "the verdict is `outstanding` or a failed generated-output agreement, "
                      "never `outstanding` alone: an empty list is not the same as a clean tree")
        self.assertIn("except Outstanding as", main)
        self.assertIn("except ReconcileError as", main)
        self.assertIn("return 2", main)
        self.assertIn("return 1", main)
        self.assertNotIn("sys.exit(0)", main, "no path may exit successfully by accident")
        self.assertIn("return 1", main, "OUTSTANDING has to be a real failure")
        self.assertEqual(main.count("return 0"), 0,
                         "main() must not hard-code success; the verdict comes from report()")

    def test_the_receipt_path_and_the_drivers_are_what_the_docs_claim(self):
        match = re.search(r'^RECEIPT = "([^"]+)"', self.source, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), RECEIPT)
        self.assertTrue(RECEIPT.startswith("docs/evidence/"))
        block = self.source[self.source.index("DRIVERS = ("):self.source.index(
            "#: commands a recording run must pass")]
        for driver in ("docs/architecture/roadmap.json",
                       "docs/architecture/PS5_PLATFORM_CONTRACT.md", "psm/", "docs/evidence/",
                       "PS5UPDATE.PUP", "tools/presentation.py"):
            self.assertIn(driver, block, f"{driver} moves a figure and has to be listed")

    def test_the_generated_span_rule_is_stated_once_not_three_times(self):
        self.assertEqual(self.source.count("GENERATED_OUTPUTS = "), 1)
        self.assertEqual(self.source.count("MARKER = re.compile("), 1)
        self.assertIn("def generated_spans", self.source)
        self.assertIn("def outside_generated_blocks", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
