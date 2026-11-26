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
        data = copy.deepcopy(self.data)
        for milestone in data["milestones"]:
            if milestone["id"] == "M2a":
                milestone["criteria_met"] = 2
                milestone["state"] = "in_progress"
        model = C.compute(data)
        weight = len(next(m for m in data["milestones"] if m["id"] == "M2a")["acceptance"])
        self.assertAlmostEqual(model["weighting"]["earned_weight"]
                               - self.model["weighting"]["earned_weight"], 2.0 / weight * weight)
        self.assertEqual(model["milestones"]["partial_credit"], ["M2a"])

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
        inflated = block.replace("| **Overall completion** | **26.7%**",
                                 "| **Overall completion** | **99.9%**")
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
        before = P.completion_chart(self.data)
        moved = copy.deepcopy(self.data)
        for milestone in moved["milestones"]:
            if milestone["id"] == "M2a":
                milestone["state"] = "complete"
                milestone.setdefault("evidence", ["docs/TEST_RESULTS.md"])
        after = P.completion_chart(moved)
        self.assertNotEqual(before, after)
        self.assertGreater(C.compute(moved)["percent"], C.compute(self.data)["percent"])

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

    def test_it_commits_only_when_something_changed(self):
        self.assertIn("git diff --exit-code", self.text)

    def test_the_generator_is_the_same_command_locally_and_in_ci(self):
        self.assertIn("tools/presentation.py sync", self.text)
        self.assertIn("tools/completion.py check", self.text)

    def test_it_does_not_disturb_the_expensive_build(self):
        # nothing here can add a step to build.yml or re-run it
        self.assertNotIn("workflow_call", self.body)
        for expensive in ("make iso", "make check", "make boot-test", "make ovmf-test"):
            self.assertNotIn(expensive, self.body, "the refresh must not re-run the build")
        self.assertNotIn("workflow_dispatch: {}", self.body)


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
