"""The progress figure has to be arithmetic, and the automation has to refuse.

Three obligations, each tested from the failing side:

  * **The percentage is derived.**  It is re-computed here straight out of
    `docs/architecture/roadmap.json` with independent arithmetic, so a bug in
    `tools/completion.py` cannot quietly move the headline number, and so a README
    that disagrees with the model is a test failure rather than a marketing choice.
  * **Nothing is invented.**  No partial credit without a recorded criterion, no
    promotion of a merge into a verification, no hosted status without a filed record,
    no figure that says something its own generated text does not.
  * **The maintenance cannot lie or loop.**  A failed, cancelled or absent verification
    produces no update; a green run whose regeneration changed nothing produces no
    commit; and the workflow's own contract -- least privilege, no cron churn, a guard
    against reacting to its own commits -- is asserted against the file that runs.
"""

from __future__ import annotations

import copy
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import completion as C  # noqa: E402
import presentation as P  # noqa: E402
import progress_record as R  # noqa: E402

SVG_NS = "{http://www.w3.org/2000/svg}"
WORKFLOW = REPO / ".github/workflows/progress.yml"


def roadmap() -> dict:
    return json.loads((REPO / "docs/architecture/roadmap.json").read_text(encoding="utf-8"))


def independent_percent(data: dict) -> float:
    """The same arithmetic, written again on purpose.

    If `compute()` ever credits a milestone that is not `complete`, or weights a
    milestone by something other than its declared acceptance criteria, this function
    disagrees and the headline figure test fails.  Two implementations that were not
    written together are cheaper than an audit of one.
    """
    total = earned = 0.0
    for milestone in data["milestones"]:
        weight = float(len(milestone.get("acceptance") or []))
        total += weight
        if milestone.get("state") == "complete":
            earned += weight
    return round(100.0 * earned / total, 1)


@contextmanager
def redirected(root: Path):
    """Point the generator at a fixture tree instead of the real repository."""
    saved = (P.README, P.ASSETS)
    P.README = root / "README.md"
    P.ASSETS = root / "docs/assets"
    try:
        yield
    finally:
        P.README, P.ASSETS = saved


def fixture_tree(keep: tuple[str, ...] = ()) -> Path:
    """A temporary README + assets pair, with the real docs symlinked for link checks."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "docs").mkdir()
    for entry in (REPO / "docs").iterdir():
        if entry.name == "assets" or entry.name in keep:
            continue
        (tmp / "docs" / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    for name in ("CONTRIBUTING.md", "LICENSE", "requirements-harness.txt", "ci"):
        (tmp / name).symlink_to(REPO / name, target_is_directory=(REPO / name).is_dir())
    (tmp / "docs/assets").mkdir()
    for name, body in P.rendered(roadmap()).items():
        (tmp / "docs/assets" / name).write_text(body, encoding="utf-8")
    (tmp / "README.md").write_text((REPO / "README.md").read_text(encoding="utf-8"),
                                   encoding="utf-8")
    return tmp


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.data = roadmap()
        self.model = C.compute(self.data)

    def test_matches_an_independent_recomputation(self):
        self.assertEqual(self.model["percent"], independent_percent(self.data))

    def test_the_real_repository_needs_no_refusal(self):
        self.assertEqual(C.validate(self.data), [])

    def test_percent_is_inside_its_bounds(self):
        self.assertGreaterEqual(self.model["percent"], 0.0)
        self.assertLessEqual(self.model["percent"], 100.0)

    def test_a_complete_milestone_is_required_for_credit(self):
        """Nothing here can be earned by code existing, or by a merge landing."""
        downgraded = copy.deepcopy(self.data)
        for milestone in downgraded["milestones"]:
            if milestone["state"] == "complete":
                milestone["state"] = "in_progress"
        model = C.compute(downgraded)
        self.assertEqual(model["percent"], 0.0)
        self.assertEqual(model["weighting"]["earned_weight"], 0.0)

    def test_partial_credit_is_refused_without_a_record(self):
        self.assertEqual(self.model["milestones"]["partial_credit"], [])
        earned = self.model["weighting"]["earned_weight"]
        by_complete = sum(len(m.get("acceptance") or []) for m in self.data["milestones"]
                          if m["state"] == "complete")
        self.assertEqual(earned, float(by_complete))

    def test_a_declared_criterion_is_credited_exactly(self):
        """A declared `criteria_met` of n earns exactly n, and nothing else moves.

        The milestone is chosen from the data rather than named, so this test is about
        the rule and not about which milestone happens to be open today.
        """
        candidates = [m["id"] for m in self.data["milestones"] if m["state"] == "in_progress"]
        if not candidates:
            self.skipTest("no in-progress milestone to declare a fraction on")
        target = candidates[0]
        data = copy.deepcopy(self.data)
        for milestone in data["milestones"]:
            if milestone["id"] == target:
                milestone["criteria_met"] = 2
                self.assertEqual(milestone["state"], "in_progress")
        model = C.compute(data)
        # contribution = (criteria_met / weight) * weight, so the earned weight grows by
        # exactly the declared count -- one criterion recorded, one criterion counted
        self.assertAlmostEqual(model["weighting"]["earned_weight"]
                               - self.model["weighting"]["earned_weight"], 2.0)
        self.assertEqual(model["milestones"]["partial_credit"], [target])
        self.assertEqual(model["counts"], self.model["counts"],
                         "declaring a fraction must not change a single state count")

    def test_two_models_agree_so_weighting_is_not_the_story(self):
        """The figure is not an artefact of the weight choice: say so with a number."""
        self.assertLessEqual(self.model["agreement_points"], 5.0)

    def test_the_model_is_deterministic(self):
        self.assertEqual(C.compute(self.data), self.model)

    def test_no_clock_and_no_git_state_enters_the_model(self):
        """Re-running must not change the answer, or the automation churns every hour."""
        first = io.StringIO()
        with redirect_stdout(first):
            self.assertEqual(C.main(["model", "--root", str(REPO)]), 0)
        second = io.StringIO()
        with redirect_stdout(second):
            self.assertEqual(C.main(["model", "--root", str(REPO)]), 0)
        self.assertEqual(first.getvalue(), second.getvalue())

    # ---- refusals: the data has to be able to make the generator stop
    def test_a_duplicated_milestone_is_refused(self):
        data = copy.deepcopy(self.data)
        data["milestones"].append(copy.deepcopy(data["milestones"][0]))
        with self.assertRaises(C.CompletionError) as caught:
            C.compute(data)
        self.assertIn("duplicated", str(caught.exception))

    def test_a_milestone_without_criteria_cannot_be_weighted(self):
        data = copy.deepcopy(self.data)
        data["milestones"][0]["acceptance"] = []
        with self.assertRaises(C.CompletionError):
            C.compute(data)
        self.assertTrue(any("weight is undefined" in problem for problem in C.validate(data)))

    def test_an_unknown_state_is_refused_not_rounded(self):
        data = copy.deepcopy(self.data)
        data["milestones"][0]["state"] = "mostly_done"
        self.assertTrue(any("state" in problem for problem in C.validate(data)))

    def test_complete_without_evidence_is_refused(self):
        """`complete` means evidenced here; a bare claim of completion is data corruption."""
        data = copy.deepcopy(self.data)
        for milestone in data["milestones"]:
            if milestone["state"] == "complete":
                milestone["evidence"] = []
                break
        self.assertTrue(any("no evidence filed" in problem for problem in C.validate(data)))

    def test_an_overstated_criterion_count_is_refused(self):
        data = copy.deepcopy(self.data)
        target = data["milestones"][0]
        target["criteria_met"] = len(target["acceptance"]) + 3
        self.assertTrue(any("outside" in problem for problem in C.validate(data)))

    def test_the_missing_file_fails_loudly(self):
        with self.assertRaises(C.CompletionError):
            C.load_roadmap(Path(tempfile.mkdtemp()) / "absent.json")


class StampTests(unittest.TestCase):
    def test_the_stamp_is_a_record_not_a_clock(self):
        stamp = C.verified_at(C.load_records())
        self.assertRegex(stamp["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(stamp["short"], r"^[0-9a-f]{7}$")
        self.assertGreater(stamp["gates"], 0)

    def test_the_newest_record_for_a_gate_wins_not_the_last_filename(self):
        """Two files, one gate: the stamp follows the evidence, not alphabetical order.

        A record named `a-late.json` must not lose to `verify.json` because of spelling --
        nor win because of it.  The directory is append-only history, so this is the
        ordinary case, not a curiosity.
        """
        tmp = Path(tempfile.mkdtemp())
        base = {"schema": "pc5.validation/1", "gate": "verify", "result": "passed",
                "checks": 11, "commit": "86b862f0000000000000000000000000000000", "date": "2026-11-23"}
        (tmp / "verify.json").write_text(json.dumps(base), encoding="utf-8")
        newer = dict(base, date="2026-12-01", checks=13, commit="abcdef1000000000000000000000000000000")
        (tmp / "a-earlier-filename-later-date.json").write_text(json.dumps(newer), encoding="utf-8")
        records = C.load_records(tmp)
        self.assertEqual(len(records), 1, "one gate, one record")
        self.assertEqual(C.verified_at(records)["date"], "2026-12-01")
        self.assertEqual(records["verify"]["_file"],
                         "docs/evidence/validation/a-earlier-filename-later-date.json")

    def test_the_figure_and_the_model_share_one_reader(self):
        """`presentation.load_records` is the model's reader, not a copy of it.

        The dashboard used to have its own: same directory, same schema filter, a
        different tie-break.  Pinning the delegation is what stops it coming back.
        """
        tmp = Path(tempfile.mkdtemp())
        base = {"schema": "pc5.validation/1", "gate": "iso", "result": "passed",
                "commit": "ccccccc00000000000000000000000000000000", "date": "2026-10-01",
                "checks": {"passed": 6, "total": 6}}
        (tmp / "a-old.json").write_text(json.dumps(base), encoding="utf-8")
        (tmp / "zz-newer.json").write_text(json.dumps(dict(base, date="2026-11-05")),
                                           encoding="utf-8")
        self.assertEqual(P.load_records(tmp), C.load_records(tmp))
        self.assertEqual(C.verified_at(P.load_records(tmp))["date"], "2026-11-05")

    def test_a_malformed_tally_cannot_crash_a_stat_card(self):
        """`checks` is a dict of counts or it is nothing; the generator never raises."""
        record = {"schema": "pc5.validation/1", "gate": "verify", "result": "passed",
                  "checks": 12, "date": "2026-11-27", "commit": "1" * 40}
        text, _colour = P.record_line({"verify": record}, "verify")
        self.assertIn("PASSED", text.upper())
        self.assertNotIn("12", text)

    def test_no_records_says_so_instead_of_defaulting_to_today(self):
        stamp = C.verified_at({})
        self.assertEqual(stamp["date"], "no record")
        self.assertEqual(stamp["gates"], 0)

    def test_the_hosted_row_reads_the_filed_record(self):
        hosted = C.hosted_summary()
        self.assertTrue(hosted["available"], "the repository files no hosted run record")
        self.assertEqual(hosted["conclusion"], "success")
        self.assertIn("host tests", hosted["text"])

    def test_a_recorded_failure_is_reported_as_a_failure(self):
        """No promotion, ever: the worst conclusion has to reach the README verbatim."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "docs" / "evidence" / "ci").mkdir(parents=True)
        record = {
            "schema": "pc5.ci/1", "run_id": 1, "head_sha": "0123456789abcdef",
            "conclusion": "cancelled", "completed_at": "2026-11-26T00:00:00Z",
        }
        (tmp / "docs/evidence/ci/1.json").write_text(json.dumps(record), encoding="utf-8")
        hosted = C.hosted_summary(tmp)
        self.assertEqual(hosted["light"], "amber")
        self.assertIn("CANCELLED", hosted["text"])
        self.assertNotIn("SUCCESS", hosted["text"])

    def test_an_absent_directory_is_an_absent_claim(self):
        hosted = C.hosted_summary(Path(tempfile.mkdtemp()))
        self.assertFalse(hosted["available"])
        self.assertIn("no hosted record", hosted["text"])


class BlockTests(unittest.TestCase):
    def setUp(self):
        self.data = roadmap()
        self.block = C.markdown(C.compute(self.data), C.verified_at(C.load_records()),
                                C.release_state(self.data), C.hosted_summary())

    def test_the_block_carries_the_headline_and_its_provenance(self):
        for needle in ("Overall completion", "Milestones complete", "Current milestone",
                       "Remaining", "Release readiness", "Last verified", "Hosted CI",
                       "tools/completion.py"):
            self.assertIn(needle, self.block)

    def test_release_readiness_and_publication_are_two_different_rows(self):
        """`ready` and `published` must never collapse into one green light."""
        self.assertIn("nothing published", self.block)
        self.assertIn("BLOCKED", self.block)

    def test_partial_credit_row_states_the_refusal(self):
        self.assertIn("in-progress work counts 0", self.block)

    def test_every_number_in_the_figure_is_in_the_text(self):
        """A reader on a phone, or with images off, loses nothing.

        This is the mobile check, expressed as something a machine can verify: the
        figure is 1200 units wide, so at a 390-point viewport its type is rendered at
        roughly a third of its size, and any statistic that existed *only* in the
        picture would stop being readable.  None may.
        """
        body = P.completion_chart(self.data)
        root = ET.fromstring(body)
        numbers = set()
        for node in root.iter(SVG_NS + "text"):
            if node.text:
                numbers.update(re.findall(r"\d+(?:\.\d+)?", node.text))
        self.assertTrue(numbers)
        missing = sorted(number for number in numbers if number not in self.block)
        self.assertEqual(missing, [], f"{missing} appear only inside the figure")


class AnimationContractTests(unittest.TestCase):
    def setUp(self):
        self.data = roadmap()

    def test_no_figure_loops_or_omits_its_resting_value(self):
        for name, body in P.rendered(self.data).items():
            self.assertEqual(P.animation_problems(name, body), [], name)

    def test_a_looping_animation_is_rejected(self):
        bad = ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
               '<rect x="0" y="0" width="4" height="4"><animate attributeName="width" '
               'from="0" to="4" dur="1s" repeatCount="indefinite"/></rect></svg>')
        self.assertTrue(P.animation_problems("x.svg", bad))

    def test_an_animation_that_disagrees_with_its_static_value_is_rejected(self):
        """The exact drift this rule exists to stop: a moving figure that lies when still."""
        bad = ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
               '<rect x="0" y="0" width="4" height="4"><animate attributeName="width" '
               'from="0" to="9" dur="1s" fill="freeze"/></rect></svg>')
        problems = P.animation_problems("x.svg", bad)
        self.assertTrue(any("without animation" in p or "rests at" in p for p in problems),
                        problems)

    def test_figures_carry_no_script_no_style_and_no_external_reference(self):
        for name, body in P.rendered(self.data).items():
            for token in ("<script", "<style", "javascript:", "xlink:href", "url(",
                          "https://", "<image"):
                self.assertNotIn(token, body, f"{name}: {token}")

    def test_the_assets_stay_small_enough_to_be_worth_loading(self):
        total = 0
        for path in sorted(P.ASSETS.glob("*.svg")):
            size = path.stat().st_size
            total += size
            self.assertLess(size, 32 * 1024, f"{path.name} is {size} bytes")
        self.assertLess(total, 128 * 1024, f"{total} bytes of figures is too much for a README")

    def test_nothing_sits_outside_the_canvas(self):
        for name, body in P.rendered(self.data).items():
            root = ET.fromstring(body)
            width, height = (int(root.get("width")), int(root.get("height")))
            for node in root.iter():
                if node.tag.endswith("rect"):
                    x, y = float(node.get("x", 0)), float(node.get("y", 0))
                    w = float(node.get("width", 0))
                    self.assertLessEqual(x + w, width + 0.5, f"{name}: rect at {x}+{w}")
                    self.assertLessEqual(y + float(node.get("height", 0)), height + 0.5, name)
                    self.assertGreaterEqual(min(x, y), 0, f"{name}: negative origin")


class CheckTest(unittest.TestCase):
    """Each guard of the generated presentation gets a failure it must catch."""

    def setUp(self):
        self.data = roadmap()
        self.tmp = fixture_tree()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self) -> list:
        with redirected(self.tmp):
            return P.check(self.data)

    def test_the_shipped_repository_is_clean(self):
        self.assertEqual(P.check(self.data), [])

    def test_a_stale_completion_figure_is_caught(self):
        path = self.tmp / "docs/assets/pc5-completion.svg"
        path.write_text(path.read_text().replace("OF DECLARED SCOPE", "OF DECLARED SCOPE!"),
                        encoding="utf-8")
        self.assertTrue(any("pc5-completion.svg: stale" in problem for problem in self.check()))

    def test_a_hand_edited_percentage_is_caught(self):
        path = self.tmp / "README.md"
        text = path.read_text(encoding="utf-8")
        block = re.search(r"<!-- pc5:completion:begin -->.*?<!-- pc5:completion:end -->",
                          text, re.S).group(0)
        percent = re.search(r"\| \*\*Overall completion\*\* \| \*\*([\d.]+)%\*\*", block)
        self.assertIsNotNone(percent, "the block no longer prints the headline percentage")
        inflated = block.replace(percent.group(0), "| **Overall completion** | **99.9%**")
        self.assertNotEqual(inflated, block, "the fixture did not find a percentage to inflate")
        path.write_text(text.replace(block, inflated), encoding="utf-8")
        problems = self.check()
        self.assertTrue(any("generated completion block does not match" in p for p in problems),
                        problems)

    def test_a_percentage_typed_into_prose_is_caught(self):
        """STRAY is why this README can be trusted: a hand-typed figure fails the gate."""
        path = self.tmp / "README.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("## Read this first",
                                     "**PC5 is 71% complete.**\n\n## Read this first", 1),
                        encoding="utf-8")
        problems = self.check()
        self.assertTrue(any("71%" in p and "outside a generated block" in p for p in problems),
                        problems)

    def test_an_unowned_asset_is_caught(self):
        (self.tmp / "docs/assets/pc5-old-progress.svg").write_text(
            "<svg/>", encoding="utf-8")
        self.assertTrue(any("no generator owns" in p for p in self.check()))

    def test_a_figure_the_readme_does_not_embed_is_caught(self):
        path = self.tmp / "README.md"
        path.write_text(path.read_text(encoding="utf-8")
                        .replace("(docs/assets/pc5-completion.svg)", "(docs/assets/gone.svg)"),
                        encoding="utf-8")
        problems = self.check()
        self.assertTrue(any("does not embed docs/assets/pc5-completion.svg" in p for p in problems),
                        problems)

    def test_a_milestone_flip_reaches_the_figure_without_touching_an_svg(self):
        """The change-in-graph-changes-the-figure proof, on whichever milestone is open."""
        before = P.completion_chart(self.data)
        moved = copy.deepcopy(self.data)
        flip = next((m for m in moved["milestones"] if m["state"] != "complete"), None)
        if flip is None:
            self.skipTest("every milestone is complete; nothing left to earn")
        flip["state"] = "complete"
        flip["evidence"] = flip.get("evidence") or ["docs/TEST_RESULTS.md"]
        after = P.completion_chart(moved)
        self.assertNotEqual(before, after, f"{flip['id']} completing changed nothing")
        self.assertGreater(C.compute(moved)["percent"], C.compute(self.data)["percent"])
        # and the figure says the new number, not merely a different number
        self.assertIn(f"{C.compute(moved)['percent']}%", after)

    def test_a_released_milestone_moves_the_release_row_only(self):
        """Publication is not implied by readiness, in either direction."""
        data = copy.deepcopy(self.data)
        for milestone in data["milestones"]:
            if milestone["id"] == "M1b":
                milestone["state"] = "complete"
                milestone["blocker"] = ""
                milestone["evidence"] = milestone.get("evidence") or ["ci/README.md"]
        release = C.release_state(data)
        self.assertTrue(release["ready"])
        self.assertFalse(release["published"], "nothing here published anything")


class SyncTests(unittest.TestCase):
    def test_writing_twice_writes_once(self):
        """The no-empty-commit property of the automation, at the layer it is cheap to prove."""
        tmp = fixture_tree()
        readme = tmp / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(re.sub(r"<!-- pc5:completion:begin -->.*?<!-- pc5:completion:end -->",
                                 "<!-- pc5:completion:begin -->\n<!-- pc5:completion:end -->",
                                 text, flags=re.S), encoding="utf-8")
        try:
            with redirected(tmp):
                first = P.write(P.load())
                second = P.write(P.load())
            self.assertIn("README.md", first)
            self.assertEqual(second, [], "a second sync changed something it should not have")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_generated_block_is_idempotent_text(self):
        data = roadmap()
        args = (C.compute(data), C.verified_at(C.load_records()), C.release_state(data),
                C.hosted_summary())
        self.assertEqual(C.markdown(*args), C.markdown(*args))


class TriageTests(unittest.TestCase):
    def test_a_roadmap_change_is_significant(self):
        self.assertIn("roadmap", C.triage(["docs/architecture/roadmap.json"]))

    def test_a_kernel_change_is_significant_even_when_the_readme_was_the_point(self):
        self.assertEqual(C.triage(["kernel64/paging.c", "README.md"]), ("source",))

    def test_prose_only_changes_move_nothing(self):
        self.assertEqual(C.triage(["README.md", "docs/BRAND.md"]), ())

    def test_evidence_and_pipeline_paths_are_significant(self):
        self.assertEqual(C.triage(["docs/evidence/validation/check.json",
                                  ".github/workflows/build.yml"]),
                         ("evidence", "pipeline"))

    def test_paths_parse_from_newline_and_nul_lists(self):
        self.assertEqual(C.parse_paths("a.py\nb.py\0\nc.py\n"), ["a.py", "b.py", "c.py"])
        self.assertEqual(C.parse_paths(""), [])


class DecisionTests(unittest.TestCase):
    """The whole automation policy, as one table of outcomes."""

    def test_a_green_merge_with_new_numbers_commits(self):
        decision = C.decide("success", ("roadmap",), True)
        self.assertEqual(decision["action"], "commit")

    def test_a_running_or_cancelled_run_cannot_publish(self):
        for conclusion in ("in_progress", "cancelled", "failure", "skipped", "action_required"):
            decision = C.decide(conclusion, ("roadmap", "source"), True)
            self.assertEqual(decision["action"], "refuse", conclusion)
            self.assertIn("never published", decision["reason"], conclusion)

    def test_nothing_significant_means_no_work_at_all(self):
        decision = C.decide("success", (), True)
        self.assertEqual(decision["action"], "noop")
        self.assertIn("no source", decision["reason"])

    def test_no_diff_means_no_commit(self):
        decision = C.decide("success", ("source",), False)
        self.assertEqual(decision["action"], "noop")
        self.assertIn("byte-identical", decision["reason"])

    def test_the_summary_line_quotes_the_evidence_it_used(self):
        data = roadmap()
        line = C.summary_line(C.compute(data), C.verified_at(C.load_records()),
                              C.release_state(data))
        self.assertIn(f"{C.compute(data)['percent']}%", line)
        self.assertIn("release blocked", line)


class TargetBindingTests(unittest.TestCase):
    """Which commit the dashboard may be regenerated from, and what makes that a refusal.

    The workflow runs with `contents: write` and pushes to `main`, so `progress_record.py
    target` is the boundary between "a verified commit" and "a string that said main".  These
    run against a real throwaway repository, because the decisive check is `git
    merge-base --is-ancestor`: a fake that always agrees would test the fixture.
    """

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", "-b", "main", str(cls.root)], check=True)
        for argv in (["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "Test"],
                     ["commit", "-q", "--allow-empty", "-m", "first"]):
            subprocess.run(["git"] + argv, cwd=cls.root, check=True, capture_output=True)
        cls.on_main = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cls.root,
                                     capture_output=True, text=True).stdout.strip()
        # a branch that is *called* main in somebody else's repository, and an unrelated commit
        subprocess.run(["git", "checkout", "-qb", "side"], cwd=cls.root, check=True,
                       capture_output=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "unmerged"], cwd=cls.root,
                       check=True, capture_output=True)
        cls.orphan = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cls.root,
                                     capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "branch", "-m", "side", "foreign-main"], cwd=cls.root,
                       check=True, capture_output=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", cls.on_main],
                       cwd=cls.root, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=cls.root, check=True,
                       capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def verdict(self, **over):
        fields = {"event": "workflow_run", "sha": self.on_main, "run_event": "push",
                  "run_repo": "pc5/PC5", "repository": "pc5/PC5", "conclusion": "success",
                  "main_ref": "refs/remotes/origin/main", "root": self.root}
        fields.update(over)
        return R.target_verdict(fields.pop("event"), fields.pop("sha"), **fields)

    def test_a_verified_commit_on_main_is_allowed(self):
        verdict = self.verdict()
        self.assertTrue(verdict["proceed"], verdict)
        self.assertIn("ancestor-of-main", verdict["checks"])

    def test_a_fork_head_named_main_is_refused_by_ancestry(self):
        """The hole this closes: a pull-request run whose branch is called `main`."""
        verdict = self.verdict(sha=self.orphan, run_event="pull_request",
                               run_repo="someone-else/PC5")
        self.assertFalse(verdict["proceed"])
        self.assertIn("not a push", verdict["reason"])
        # and with the event fields spoofed into shape, Git is the last line: the commit is
        # simply not on main
        verdict = self.verdict(sha=self.orphan)
        self.assertFalse(verdict["proceed"], verdict)
        self.assertIn("not an ancestor", verdict["reason"])

    def test_each_refusal_says_which_field_moved(self):
        cases = {
            "not a sha": ({"sha": "18a9d7a"}, "not a 40-character commit id"),
            "a non-push run": ({"run_event": "schedule"}, "not a push"),
            "another repository": ({"run_repo": "elsewhere/PC5"}, "belongs to"),
            "not a success": ({"conclusion": "cancelled"}, "concluded 'cancelled'"),
            "an unknown event": ({"event": "repository_dispatch"}, "started by"),
            "a dispatch off main": ({"event": "workflow_dispatch", "ref_name": "side"},
                                    "dispatched on 'side'"),
        }
        for label, (over, expected) in cases.items():
            with self.subTest(label):
                verdict = self.verdict(**over)
                self.assertFalse(verdict["proceed"], verdict)
                self.assertIn(expected, verdict["reason"], verdict["reason"])

    def test_a_dispatch_on_main_is_allowed_without_a_run(self):
        verdict = self.verdict(event="workflow_dispatch", ref_name="main")
        self.assertTrue(verdict["proceed"], verdict)
        self.assertEqual(verdict["checks"], ["sha-shape", "dispatch-on-main"])

    def test_an_unavailable_comparison_ref_is_a_refusal_not_a_pass(self):
        verdict = self.verdict(main_ref="refs/remotes/origin/nope")
        self.assertFalse(verdict["proceed"])
        self.assertIn("not available to compare against", verdict["reason"])

    def test_a_commit_the_checkout_does_not_have_is_refused(self):
        verdict = self.verdict(sha="0" * 40)
        self.assertFalse(verdict["proceed"])
        self.assertIn("cannot resolve", verdict["reason"])

    def test_the_cli_exits_four_on_a_refusal_and_zero_on_a_pass(self):
        out = subprocess.run([sys.executable, str(REPO / "tools" / "progress_record.py"),
                              "target", "--json", "--event", "workflow_run",
                              "--sha", self.on_main, "--run-event", "push",
                              "--run-repo", "pc5/PC5", "--repository", "pc5/PC5",
                              "--main-ref", "refs/remotes/origin/main", "--root", str(self.root)],
                             capture_output=True, text=True, check=False)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(json.loads(out.stdout)["proceed"])
        bad = subprocess.run([sys.executable, str(REPO / "tools" / "progress_record.py"),
                              "target", "--json", "--event", "workflow_run", "--sha", "nope"],
                             capture_output=True, text=True, check=False)
        self.assertEqual(bad.returncode, 4, bad.stdout + bad.stderr)
        self.assertFalse(json.loads(bad.stdout)["proceed"])


@unittest.skipUnless(WORKFLOW.exists(), "the maintenance workflow has not been installed yet")
class WorkflowContractTests(unittest.TestCase):
    """The YAML is part of the implementation, so it is tested like one.

    Comments are stripped before any assertion, because the file explains itself at
    length -- including the triggers it deliberately does not use -- and a check that
    read prose would be a check on the wording rather than on the behaviour.
    """

    @classmethod
    def setUpClass(cls):
        cls.full = WORKFLOW.read_text(encoding="utf-8")
        cls.text = "\n".join(line for line in cls.full.splitlines()
                              if not line.lstrip().startswith("#"))
        cls.body = cls.text.lower()

    def test_it_reacts_to_completed_verification_not_to_a_schedule(self):
        self.assertIn("workflow_run", self.text)
        self.assertNotIn("schedule:", self.text,
                         "a cron would restamp the README with nothing new to say")

    def test_it_only_publishes_after_a_successful_verification(self):
        self.assertRegex(self.text, r"conclusion\s*==\s*'success'")
        self.assertRegex(self.text, r"head_branch\s*==\s*'main'")
        # a branch name alone is not provenance: the trigger also requires a push, and the
        # repository match is re-checked in the tool, where a refusal is a tested exit code
        self.assertRegex(self.text, r"workflow_run\.event\s*==\s*'push'")
        self.assertIn("--run-repo", self.text)

    def test_the_trigger_never_reads_two_levels_below_the_event(self):
        """`github.event.workflow_run.x.y` throws when `workflow_run` is absent.

        A throwing `if:` fails the run before any job is created — a red CI run whose only
        defect is an expression, which is exactly what a shallow pre-filter is for.
        """
        condition = re.search(r"if: >-\n((?: {6}.*\n)+)", self.text)
        self.assertIsNotNone(condition, "the refresh job has no folded `if:` condition")
        for deep in re.finditer(r"workflow_run\.\w+\.\w+", condition.group(1)):
            self.fail(f"two-level access in the trigger: {deep.group(0)!r}")

    def test_it_binds_the_target_before_it_writes_anything(self):
        """The refusal step is *before* the guard, the regeneration and the commit."""
        self.assertIn("progress_record.py target", self.text)
        target = self.text.index("Refuse a target that is not verified main")
        for later in ("Refuse to refresh from a refresh", "Regenerate the figures",
                      "Commit and push the refresh"):
            self.assertLess(target, self.text.index(later), later)

    def test_no_event_field_is_interpolated_into_a_shell(self):
        """Every `github.event.*` reaches `run:` through `env:`, so none of it is command text."""
        for block in re.finditer(r"run: \|\n((?:          .*(?:\n|$))+)", self.text):
            for line in block.group(1).splitlines():
                self.assertNotIn("${{", line, f"injected expression in: {line.strip()}")

    def test_least_privilege(self):
        permissions = re.search(r"^permissions:\n((?:[ ]{2}\w+: \w+\n)+)", self.text, re.M)
        self.assertIsNotNone(permissions, "the workflow declares no top-level permissions block")
        declared = permissions.group(1)
        self.assertIn("contents: write", declared)
        for broader in ("admin", "security-events: write", "actions: write"):
            self.assertNotIn(broader, declared, broader)

    def test_it_does_not_react_to_its_own_kind_of_commit(self):
        # the loop break is the guard step, which calls the same function the unit tests
        # below drive directly; the release path is not reachable from here at all
        self.assertIn("progress_record.py guard", self.text)
        self.assertIn("Refuse to refresh from a refresh", self.text)
        for forbidden in ("git tag", "gh release", "--force", "--no-verify", "publish_release"):
            self.assertNotIn(forbidden, self.body, forbidden)

    def test_it_validates_before_it_commits(self):
        checks = re.search(r"- name: Verify the generated state\n(.*?)(?=\n      - name: )",
                           self.text, re.S)
        self.assertIsNotNone(checks, "no verification step before the commit step")
        for gate in ("presentation.py check", "completion.py check", "docs_check",
                     "test_completion.py"):
            self.assertIn(gate, checks.group(1), gate)
        # and the commit step comes after it, not before
        self.assertLess(checks.start(), self.text.index("Commit and push the refresh"))

    def test_a_refusal_is_reported_rather_than_crashing_the_run(self):
        """`refuse` is the policy speaking, not an error: the run stays green, loudly."""
        self.assertIn("|| true", self.text)
        self.assertIn('echo "::notice', self.text)

    def test_it_commits_only_when_something_changed(self):
        self.assertIn("git diff --quiet", self.text)

    def test_the_generator_is_the_same_command_locally_and_in_ci(self):
        self.assertIn("tools/presentation.py sync", self.text)
        self.assertIn("tools/completion.py check", self.text)

    def test_it_does_not_disturb_the_expensive_build(self):
        # nothing here can add a step to build.yml or re-run it
        self.assertNotIn("workflow_call", self.body)
        for expensive in ("make iso", "make check", "make boot-test", "make ovmf-test"):
            self.assertNotIn(expensive, self.body, "the refresh must not re-run the build")
        self.assertNotIn("workflow_dispatch: {}", self.body)


class WorkflowStructureTests(unittest.TestCase):
    """A workflow that does not parse is a red run with no jobs, no log, and no clue.

    GitHub's Actions parser rejects a mapping key repeated inside one mapping.  That is the
    whole story of a run that is created, fails in seconds, and lists no jobs: there is no step
    to point at, so the failure looks like infrastructure.  It is also invisible to the obvious
    local check -- `yaml.safe_load` accepts the duplicate and keeps the last value -- so the
    assertion has to be made by something that knows the parser's rule.  PyYAML is therefore
    not imported here at all, and nothing in this class can skip: the hosted host-test venv
    installs only `unicorn`, and a gate that skips there would gate nothing.

    The duplicate this class exists for was real: two `env:` keys on the evidence step, which
    made every attempt at this workflow jobless until the file reached `main` and the run
    stayed red there too.  The second half of the class pins the requirement that duplicate was
    hiding -- `record` shells out to `gh api`, and `gh` needs a token in the environment that
    `actions/checkout`'s credential store does not provide.
    """

    @staticmethod
    def duplicate_keys(text: str) -> list[tuple[int, str, int]]:
        """`(line, key, first-seen line)` for each mapping key repeated in its own mapping.

        Three rules carry it, and each is exercised by a case below:

        * block scalars (`run: |`, `if: >-`) are skipped by indentation, so a shell line that
          reads like a key -- `echo "skip=true" >> "$GITHUB_OUTPUT"` -- is not mistaken for one;
        * comments and blank lines are ignored, because prose quotes keys here;
        * a sequence item is measured at the indent of its `- ` marker, which is always
          shallower than the keys it introduces.  Popping the stack at that marker starts every
          step with a clean set, which is why `name:`/`run:`/`env:` repeating across *steps* --
          the normal shape of a workflow -- is not a duplicate, while two `env:` inside one step
          is.
        """
        findings: list[tuple[int, str, int]] = []
        stack: list[tuple[int, dict]] = []
        scalar_at: int | None = None
        for number, raw in enumerate(text.splitlines(), 1):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if scalar_at is not None:
                if indent > scalar_at:
                    continue
                scalar_at = None
            while stack and stack[-1][0] > indent:
                stack.pop()
            line = raw.strip()
            if line.startswith("- "):
                indent += 2
                line = line[2:].strip()
            match = re.match(r"^([A-Za-z0-9_.\"'-]+):(?:\s+(.*))?$", line)
            if match is None:
                continue
            key, value = match.group(1), (match.group(2) or "").strip()
            if not stack or stack[-1][0] != indent:
                stack.append((indent, {}))
            seen = stack[-1][1]
            if key in seen:
                findings.append((number, key, seen[key]))
            else:
                seen[key] = number
            if re.match(r"^[|>][-+]?\d*$", value):
                scalar_at = indent
        return findings

    def workflows(self) -> list[Path]:
        found = sorted((REPO / ".github/workflows").glob("*.yml"))
        self.assertTrue(found, "no workflow files in the tree: this gate has stopped guarding")
        return found

    def test_no_workflow_repeats_a_key_inside_one_mapping(self):
        for path in self.workflows():
            with self.subTest(workflow=path.name):
                self.assertEqual(
                    [], self.duplicate_keys(path.read_text(encoding="utf-8")),
                    f"{path.relative_to(REPO)} will not parse on GitHub: a mapping key is "
                    "repeated inside one mapping, which makes every run of it jobless",
                )

    def test_the_detector_fires_on_the_mistake_it_guards(self):
        broken = "\n".join([
            "jobs:",
            "  x:",
            "    steps:",
            "      - name: A",
            "        env:",
            "          GH_REPO: one",
            "        env:",
            "          PC5_RUN_ID: two",
            "        run: echo hi",
        ])
        self.assertEqual([(7, "env", 5)], self.duplicate_keys(broken))

    def test_the_detector_ignores_repetition_across_steps_and_shell_bodies(self):
        # a false positive here would push someone into renaming harmless YAML, so the shape
        # that recurs in every real workflow has to come back empty.
        fine = "\n".join([
            "jobs:",
            "  x:",
            "    steps:",
            "      - name: A",
            "        env:",
            "          TOKEN: one",
            "        run: |",
            "          echo \"name: not a key\" >> \"$GITHUB_OUTPUT\"",
            "          # env: not a key either",
            "          echo {}",
            "      - name: B",
            "        env:",
            "          TOKEN: two",
            "        run: |",
            "          echo \"name: not a key\"",
        ])
        self.assertEqual([], self.duplicate_keys(fine))

    def test_the_evidence_step_authenticates_the_gh_calls_it_makes(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        tool = (REPO / "tools/progress_record.py").read_text(encoding="utf-8")
        self.assertIn('"gh", "api"', tool,
                      "progress_record.py no longer calls `gh api`; the assertion below is "
                      "guarding a dependency that has gone, so delete it rather than keep it")
        step = re.search(r"      - name: File the verified run as evidence\n(.*?)(?=\n      - name: |\Z)",
                         text, re.S)
        self.assertIsNotNone(step, "the evidence step is gone from the workflow")
        body = step.group(1)
        self.assertEqual(1, len(re.findall(r"^        env:$", body, re.M)),
                         "exactly one `env:` block per step, or the file stops parsing")
        for needed in ("GH_REPO:", "GH_TOKEN:", "PC5_RUN_ID:"):
            self.assertIn(needed, body, f"{needed} belongs to the one step that calls `gh`")


class RecordToolTests(unittest.TestCase):
    """Part 11, as a runnable cycle: the fixtures are synthetic, the code path is real."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run = {"id": 900, "event": "push", "head_branch": "main",
                    "head_sha": "0123456789abcdef0123456789abcdef01234567",
                    "status": "completed", "conclusion": "success", "run_attempt": 1,
                    "run_started_at": "2026-11-27T00:00:00Z",
                    "updated_at": "2026-11-27T00:25:00Z",
                    "path": ".github/workflows/build.yml"}
        self.jobs = [
            {"id": 1, "name": "build and verify", "conclusion": "success",
             "started_at": "2026-11-27T00:00:03Z", "completed_at": "2026-11-27T00:25:00Z",
             "steps": [{"conclusion": "success"}] * 20 + [{"conclusion": "skipped"}] * 2,
             "annotations": [{"body": "Ran 1227 tests in 485.350s"},
                             {"body": "OK (skipped=13)"}]},
            {"id": 2, "name": "UEFI boot under genuine OVMF", "conclusion": "success",
             "steps": [{"conclusion": "success"}]},
            {"id": 3, "name": "publish release assets", "conclusion": "skipped", "steps": []},
        ]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_verified_run_is_recorded_with_its_real_tally(self):
        record = R.build_record(self.run, self.jobs)
        self.assertEqual(record["host_tests"], {"ran": 1227, "result": "OK", "skipped": 13})
        self.assertEqual(record["conclusion"], "success")
        publish = [j for j in record["jobs"] if j["name"] == "publish release assets"][0]
        self.assertEqual(publish["conclusion"], "skipped", "a skipped job must stay skipped")

    def test_a_cancelled_run_is_recorded_as_cancelled(self):
        run = dict(self.run, conclusion="cancelled", status="completed")
        jobs = [dict(self.jobs[0], conclusion="cancelled")]
        record = R.build_record(run, jobs)
        self.assertEqual(record["conclusion"], "cancelled")
        self.assertEqual(record["jobs"][0]["steps_failed"], 0)  # steps were not run, not failed

    def test_the_record_is_written_once_and_stable(self):
        record = R.build_record(self.run, self.jobs)
        first = R.write_record(record, self.tmp)
        payload = first.read_text(encoding="utf-8")
        R.write_record(record, self.tmp)
        self.assertEqual(first.read_text(encoding="utf-8"), payload)
        self.assertEqual(first.name, "900.json")

    def test_a_run_that_has_not_finished_is_never_drawn_as_green(self):
        """An incomplete verification is recorded faithfully and stays non-committal."""
        run = dict(self.run, status="in_progress", conclusion=None)
        record = R.build_record(run, self.jobs)
        self.assertEqual(record["conclusion"], "in_progress")
        tmp = self.tmp / "inflight"
        R.write_record(record, tmp)
        hosted = C.hosted_summary(tmp)
        self.assertNotEqual(hosted["light"], "green")
        self.assertNotIn("SUCCESS", hosted["text"])
        # and the policy refuses to publish from it
        self.assertEqual(R.decide(str(record["conclusion"]), ["kernel64/x.c"], True)["action"],
                         "refuse")

    def test_the_guard_refuses_a_progress_commit(self):
        self.assertFalse(R.guard("docs: refresh the progress dashboard at 0123456", "")["proceed"])

    # --- the live `--run-id` path -------------------------------------------------------
    # Everything above drives `record` through fixtures, which is why the bug these tests
    # exist for survived every gate: the CI-only branch that calls `gh api` was never
    # executed, and on the first real `workflow_run` (2026-11-27) it built the endpoint
    # `"…/jobs --paginate --jq '.jobs'"` -- one argv element, spaces and all -- which the
    # API answers with 404 because `subprocess.run` gets no shell to split it.  These cases
    # run that branch with `_run` replaced, so the assertion is on the arguments `gh` would
    # be handed, with no network and no credential.

    def drive_record(self, payloads: dict | None = None):
        """Run `record --run-id 900` with `_run` stubbed; return (argvs, exception-or-None).

        Responses are keyed by the *tail* of the endpoint (`run`, `jobs`, `annotations`),
        matched after the query string is dropped: `/actions/runs/900` is a substring of
        `/actions/runs/900/jobs`, so a substring search would answer the wrong question and
        the fixture would prove nothing.
        """
        argvs: list[list[str]] = []
        original = R._run
        table = {"run": dict(self.run, id=900),
                 "jobs": {"total_count": len(self.jobs), "jobs": self.jobs},
                 "annotations": []}
        table.update(payloads or {})

        def fake(argv, cwd=None):
            argvs.append(list(argv))
            tail = argv[-1].split("repos/{owner}/{repo}", 1)[-1].split("?", 1)[0]
            for kind in ("jobs", "annotations", "run"):
                if tail.endswith(f"/{kind}") or (kind == "run" and tail.endswith("/runs/900")):
                    return json.dumps(table[kind])
            raise AssertionError(f"unexpected endpoint: {argv[-1]!r}")

        R._run = fake
        try:
            error = None
            try:
                R.main(["record", "--run-id", "900", "--root", str(self.tmp)])
            except R.ActionError as exc:            # type: ignore[attr-defined]
                error = exc
        finally:
            R._run = original
        return argvs, error

    def test_every_live_read_hands_gh_exactly_one_endpoint(self):
        argvs, error = self.drive_record({
            "jobs": {"total_count": 1, "jobs": [{"id": 1, "name": "build and verify",
                                                 "conclusion": "success"}]},
        })
        self.assertIsNone(error, f"record refused: {error}")
        self.assertTrue(argvs, "the live path read nothing")
        for argv in argvs:
            self.assertEqual(3, len(argv), f"flags must not ride inside the endpoint: {argv!r}")
            self.assertEqual(["gh", "api"], argv[:2])
            endpoint = argv[2]
            self.assertNotIn(" ", endpoint, f"{endpoint!r} would be requested as one path")
            self.assertTrue(endpoint.startswith("repos/{owner}/{repo}/"),
                            f"{endpoint!r} is not template-qualified, so `gh` cannot resolve it")

    def test_an_option_glued_into_an_endpoint_is_refused_at_build_time(self):
        with self.assertRaises(R.ActionError):      # type: ignore[attr-defined]
            R.api_endpoint("/actions/runs/900/jobs --paginate --jq '.jobs'")

    def test_a_truncated_jobs_page_is_a_refusal_not_a_smaller_record(self):
        with self.assertRaises(R.ActionError):      # type: ignore[attr-defined]
            R.jobs_from_payload({"total_count": 9, "jobs": [{"id": 1}]}, 900)
        self.assertEqual(2, len(R.jobs_from_payload(
            {"total_count": 2, "jobs": [{"id": 1}, {"id": 2}]}, 900)))
        # and the refusal has to survive the whole live path, not just the helper
        _, error = self.drive_record({
            "jobs": {"total_count": 4, "jobs": [{"id": 1, "conclusion": "success"}]},
        })
        self.assertIsNotNone(error, "a partial run was accepted as evidence")
        self.assertIn("1 of 4", str(error))
        self.assertTrue(R.guard("feat(kernel64): enable SMEP", "Adham Badran")["proceed"])
        self.assertFalse(R.guard("anything", "github-actions[bot]")["proceed"])

    def test_decide_reflects_the_verification_that_actually_happened(self):
        owed = R.decide("success", ["docs/architecture/roadmap.json"], True)
        self.assertEqual(owed["action"], "commit")
        quiet = R.decide("success", ["docs/architecture/roadmap.json"], False)
        self.assertEqual(quiet["action"], "noop")
        red = R.decide("failure", ["kernel64/paging.c"], True)
        self.assertEqual(red["action"], "refuse")

    def test_a_commit_is_made_only_when_the_tree_actually_changed(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        for argv in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "Test"], ["add", "."]):
            subprocess.run(["git"] + argv, cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "seed"], cwd=repo,
                       check=True, capture_output=True)
        # nothing outstanding -> no commit, which is the property the workflow depends on
        outcome = R.commit_update("docs: refresh the progress dashboard at x", dry_run=False,
                                  push=False, root=repo)
        self.assertFalse(outcome["committed"])
        self.assertEqual(outcome["reason"], "nothing changed")
        # something outstanding, dry run -> reported, not written
        (repo / "docs").mkdir()
        (repo / "docs" / "evidence").mkdir(parents=True)
        (repo / "docs/evidence/ci").mkdir(parents=True)
        (repo / "docs/evidence/ci/901.json").write_text("{}\n", encoding="utf-8")
        preview = R.commit_update("docs: refresh", dry_run=True, push=False, root=repo)
        self.assertFalse(preview["committed"])
        self.assertEqual(preview["reason"], "dry run")
        message = "docs: refresh the progress dashboard at abc1234"
        applied = R.commit_update(message, dry_run=False, push=False, root=repo)
        self.assertTrue(applied["committed"])
        self.assertTrue(applied["sha"])
        self.assertFalse(applied["pushed"], "a test must never push")
        subject = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=repo,
                                 capture_output=True, text=True).stdout.strip()
        self.assertEqual(subject, message)
        # and the cycle closes: the commit it just made is exactly what the guard
        # refuses to act on, so a second pass over this repository makes no second commit
        self.assertFalse(R.guard(subject)["proceed"])
        again = R.commit_update(message, dry_run=False, push=False, root=repo)
        self.assertFalse(again["committed"], "the second pass found a diff it should not have")
