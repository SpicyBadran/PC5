"""The README progress matrix has to be generated, honest, and reproducible.

`tests/test_presentation.py` already holds the presentation layer to its general rules,
so these tests cover what the matrix adds, and hold it to four:

  * traceability — every percentage in the figure and in the README block carries the
    fraction it came from, and the fraction is what an independent count over the same
    files gives (the recomputations here deliberately do not call `progress_metrics`);
  * evidence — a green cell means a file in this repository records the thing as done:
    the state-to-colour mapping is checked, and a `blocked` milestone is never green;
  * liveness — promote a milestone in a copy of the roadmap and the drawing changes;
    paint a figure greener than the data and `presentation.py check` fails;
  * geometry — the same inputs give the same bytes, no label leaves the canvas, nothing
    is drawn below the mobile floor, and there is no dead band under the last region.

The last class checks the policy that keeps this figure from rotting: `AGENTS.md` makes
post-merge regeneration a default duty of any agent working in this repository.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import presentation as P  # noqa: E402

MATRIX_ASSET = Path("docs/assets/pc5-progress-matrix.svg")
BEGIN = "<!-- pc5:matrix:begin -->"
END = "<!-- pc5:matrix:end -->"
PERCENTAGE = re.compile(r"(\d+(?:\.\d+)?)%")
FRACTION = re.compile(r"\((\d+)/(\d+)\)")


def roadmap() -> dict:
    return json.loads((REPO / "docs/architecture/roadmap.json").read_text(encoding="utf-8"))


def matrix_block() -> str:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    return readme.split(BEGIN, 1)[1].split(END, 1)[0].strip()


def render(data=None):
    data = roadmap() if data is None else data
    records = P.load_records()
    return P.progress_matrix(data, records), P.progress_metrics(data, records)


class PercentageTraceabilityTest(unittest.TestCase):
    def test_every_percentage_in_the_figure_carries_its_fraction(self):
        body, _metrics = render()
        for match in re.finditer(r">([^<>]*?\d+(?:\.\d)?%[^<>]*?)<", body):
            label = match.group(1)
            self.assertRegex(label, r"\(\d+/\d+\)",
                             f"a percentage without n/d: {label!r}")

    def test_every_percentage_in_the_readme_block_carries_its_fraction(self):
        for line in matrix_block().splitlines():
            if "%" not in line:
                continue
            self.assertRegex(line, r"\(\d+/\d+\)", f"a percentage without n/d: {line!r}")

    def test_the_fractions_are_what_an_independent_count_gives(self):
        data = roadmap()
        milestones = data["milestones"]
        body, metrics = render(data)

        complete = sum(1 for m in milestones if m["state"] == "complete")
        total = len(milestones)
        self.assertIn(f"{100.0 * complete / total:.1f}% ({complete}/{total})", body)
        self.assertEqual((complete, total),
                        (metrics["states"]["complete"], metrics["milestone_total"]))

        acceptance = sum(len(m.get("acceptance") or []) for m in milestones)
        verified_acceptance = sum(len(m.get("acceptance") or []) for m in milestones
                                  if m["state"] == "complete")
        self.assertEqual(acceptance, metrics["acceptance_total"])
        self.assertEqual(verified_acceptance, metrics["acceptance_by_state"]["complete"])

        deliverables = [x for m in milestones for x in (m.get("deliverables") or [])]
        present = [x for x in deliverables
                   if P.PATH_SHAPED.match(x) and (REPO / x).exists()]
        self.assertEqual(len(deliverables), metrics["deliverable_total"])
        self.assertEqual(len(present), metrics["deliverable_existing"])

        evidence = [e for m in milestones for e in (m.get("evidence") or [])]
        resolving = [e for e in evidence if (REPO / e.split("#")[0]).exists()]
        self.assertEqual(len(evidence), metrics["evidence_total"])
        self.assertEqual(len(resolving), metrics["evidence_present"])
        self.assertEqual(sum(1 for m in milestones if m.get("evidence")),
                         metrics["milestones_with_evidence"])

        records = P.load_records()
        passed = sum(1 for record in records.values() if record.get("result") == "passed")
        self.assertEqual(passed, metrics["gate_passed"])
        self.assertEqual(len(records), len(metrics["gates"]))
        self.assertIn(f"{100.0 * passed / len(records):.1f}% ({passed}/{len(records)})", body)

    def test_pct_formats_one_decimal_and_refuses_an_empty_denominator(self):
        self.assertEqual(P.pct(1, 3), "33.3% (1/3)")
        self.assertEqual(P.pct(9, 35), "25.7% (9/35)")
        self.assertEqual(P.pct(35, 35), "100.0% (35/35)")
        self.assertEqual(P.pct(0, 0), "n/a (0/0)")

    def test_the_readme_block_agrees_with_the_data_row_by_row(self):
        _body, metrics = render()
        block = matrix_block()
        self.assertIn(P.pct(metrics["states"]["complete"], metrics["milestone_total"]),
                      block)
        self.assertIn(f"{metrics['deliverable_existing']}/{metrics['deliverable_total']}",
                      block)
        self.assertIn(f"{metrics['milestones_with_evidence']}/{metrics['milestone_total']}",
                      block)
        for level, info in metrics["levels"].items():
            self.assertIn(f"L{level} {info['complete']}/{info['total']}", block)
        for milestone in metrics["blocked"]:
            self.assertIn(f"`{milestone['id']}`", block)


class NothingIsGreenWithoutAFileTest(unittest.TestCase):
    def test_only_a_verified_state_is_painted_verified(self):
        self.assertEqual(P.MATRIX_STATE["complete"][0], P.GREEN)
        self.assertNotEqual(P.MATRIX_STATE["blocked"][0], P.GREEN)
        self.assertNotEqual(P.MATRIX_STATE["not_started"][0], P.GREEN)
        self.assertNotEqual(P.MATRIX_STATE["in_progress"][0], P.GREEN)
        self.assertTrue(P.MATRIX_STATE["blocked"][3],
                        "a blocked cell must carry a mark, not only another colour")

    def test_the_figure_marks_every_blocked_milestone_in_both_relevant_regions(self):
        _body, metrics = render()
        body = MATRIX_BYTES()
        for row in metrics["blocked"]:
            self.assertIn(row["id"] + " !", body,
                          f"{row['id']} is blocked and has to read as blocked")
        self.assertIn(f"{len(metrics['blocked'])}/{metrics['milestone_total']} milestones",
                      body)

    def test_levels_that_need_a_target_hold_no_verified_milestone(self):
        _body, metrics = render()
        for level in (3, 4, 5, 6):
            self.assertEqual(metrics["levels"][level]["complete"], 0,
                             f"level {level} has no evidence and must not claim any")
        reached = [level for level, info in metrics["levels"].items() if info["complete"]]
        self.assertEqual(sorted(reached), [1, 2])

    def test_no_row_of_the_block_claims_physical_or_genuine_execution(self):
        block = matrix_block()
        self.assertIn("0 record(s) filed", block)
        self.assertIn("**BLOCKED** — nothing executed", block)
        lowered = block.lower()
        for banned in ("runs ps5 games", "production ready", "fully compatible",
                       "ci passing", "secure boot supported"):
            self.assertNotIn(banned, lowered)

    def test_host_work_being_verified_does_not_lift_the_levels_above_it(self):
        _body, metrics = render()
        self.assertGreater(metrics["states"]["complete"], 0)
        self.assertEqual(sum(1 for level, info in metrics["levels"].items()
                             if info["complete"]), 2)
        self.assertEqual(metrics["physical_records"], 0)

    def test_the_package_row_still_says_the_payload_is_absent(self):
        body = MATRIX_BYTES()
        self.assertIn("payload absent", body)
        self.assertIn("package: LFS pointer only", body)


class GeneratedNotGuessedTest(unittest.TestCase):
    def test_the_committed_figure_is_a_fresh_render(self):
        body, _metrics = render()
        self.assertEqual((REPO / MATRIX_ASSET).read_text(encoding="utf-8"), body,
                         f"{MATRIX_ASSET} is stale — run `make presentation`")

    def test_generation_is_deterministic_to_the_byte(self):
        first, _ = render()
        second, _ = render()
        self.assertEqual(first, second)
        self.assertEqual(zlib.crc32(first.encode()), zlib.crc32(second.encode()))

    def test_promoting_a_milestone_changes_the_drawing_without_editting_the_drawing(self):
        data = roadmap()
        promoted = copy.deepcopy(data)
        for milestone in promoted["milestones"]:
            if milestone["id"] == "M19":
                milestone["state"] = "complete"
        original, _ = render(data)
        rendered, metrics = render(promoted)
        self.assertNotEqual(original, rendered)
        complete = metrics["states"]["complete"]
        self.assertEqual(complete, len([m for m in promoted["milestones"]
                                        if m["state"] == "complete"]))
        self.assertIn(P.pct(complete, metrics["milestone_total"]), rendered)
        self.assertNotIn("M19 !", rendered, "a promoted milestone must lose its blocked mark")

    def test_the_workstream_cells_count_milestones_and_not_effort(self):
        data = roadmap()
        body, metrics = render(data)
        for stream in metrics["streams"]:
            rows = [m for m in data["milestones"] if m.get("workstream") == stream["id"]]
            self.assertEqual(len(rows), stream["total"])
            self.assertEqual(sum(1 for m in rows if m["state"] == "complete"),
                             stream["complete"])
            self.assertIn(f"{stream['complete']}/{stream['total']}", body)

    def test_operations_are_counted_from_the_contract_document_itself(self):
        document = (REPO / "docs/architecture/PS5_PLATFORM_CONTRACT.md").read_text(
            encoding="utf-8")
        counted = {}
        for name, block in re.findall(
                r"<!-- psm:begin interfaces-(PSM-[A-Z]+) -->(.*?)<!-- psm:end interfaces-\1 -->",
                document, re.S):
            rows = [line for line in block.splitlines() if line.startswith("| `")]
            counted[name] = (len(rows), sum(1 for line in rows if "UNSUPPORTED" in line))
        _body, metrics = render()
        contract = metrics["contract"]
        self.assertTrue(contract["available"])
        self.assertEqual(sorted(row["name"] for row in contract["interfaces"]),
                         sorted(counted))
        for row in contract["interfaces"]:
            self.assertEqual((row["ops"], row["unsupported"]), counted[row["name"]])
        self.assertEqual(contract["ops"], sum(pair[0] for pair in counted.values()))
        self.assertLess(contract["unsupported"], contract["ops"])

    def test_claim_classes_sum_to_the_registry(self):
        _body, metrics = render()
        claims = metrics["claims"]
        if not claims["available"]:
            self.skipTest("the platform-contract registry is not importable here")
        self.assertEqual(sum(claims["counts"].values()), claims["total"])
        self.assertEqual(len(claims["ids"]), claims["total"])
        for entry in claims["ids"]:
            self.assertIn(entry["classification"], P.MATRIX_CLASSIFICATION)

    def test_a_missing_source_data_file_is_reported_not_invented(self):
        """The generator degrades to an absent region instead of drawing a zero as a result."""
        with unittest.mock.patch.object(P, "contract_inventory",
                                        return_value={"available": False, "interfaces": [],
                                                      "ops": 0, "unsupported": 0}), \
             unittest.mock.patch.object(P, "claim_inventory",
                                        return_value={"available": False, "total": 0,
                                                      "counts": {}, "ids": []}):
            body = P.progress_matrix(roadmap(), P.load_records())
        self.assertNotIn("PLATFORM-MODEL OPERATIONS", body)
        self.assertNotIn("PLATFORM-CONTRACT CLAIMS", body)
        self.assertIn("MILESTONES BY STATE", body)


class LayoutGuardTest(unittest.TestCase):
    def setUp(self):
        self.body, _metrics = render()
        width, height = re.search(r'width="(\d+)" height="(\d+)"', self.body).groups()
        self.width, self.height = int(width), int(height)

    def test_the_canvas_is_the_size_github_renders_without_shrinking(self):
        self.assertEqual(self.width, 1200)

    def test_no_text_is_smaller_than_the_mobile_floor(self):
        sizes = [float(value) for value in re.findall(r'font-size="([\d.]+)"', self.body)]
        self.assertTrue(sizes)
        self.assertGreaterEqual(min(sizes), 10, "text below 10px is unreadable on a phone")
        self.assertLessEqual(max(sizes), 64)

    def test_the_canvas_has_no_dead_band_below_the_last_region(self):
        lowest = 0.0
        for match in re.finditer(r'<rect x="[\d.]+" y="([\d.]+)" width="[\d.]+" '
                                 r'height="([\d.]+)"', self.body):
            lowest = max(lowest, float(match.group(1)) + float(match.group(2)))
        for match in re.finditer(r'<text x="[\d.]+" y="([\d.]+)"', self.body):
            lowest = max(lowest, float(match.group(1)))
        self.assertLessEqual(self.height - lowest, 90,
                             f"{self.height - lowest:.0f}px of empty canvas at the bottom")

    def test_the_region_heads_are_titled_and_counted(self):
        for needle in ("MILESTONES BY STATE", "EVIDENCE LADDER", "WORKSTREAMS",
                       "BLOCKED, AND WHAT IT IS BLOCKED ON", "COMMITTED VALIDATION RECORDS",
                       "PLATFORM-MODEL OPERATIONS", "PLATFORM-CONTRACT CLAIMS"):
            self.assertIn(needle, self.body)
        titles = re.findall(r'<text[^>]*font-weight="bold"[^>]*>([A-Z][^<]{6,60})</text>',
                            self.body)
        self.assertGreaterEqual(len(titles), 7)

    def test_the_figure_is_dense_enough_to_be_the_dashboard_it_claims_to_be(self):
        self.assertGreaterEqual(self.body.count("<rect"), 150)
        self.assertGreaterEqual(self.body.count("<text"), 200)
        self.assertLess(self.body.count("<rect"), 600, "a wall of cells is not legible")

    def test_the_figure_explains_itself_to_a_screen_reader(self):
        title = re.search(r"<title[^>]*>(.*?)</title>", self.body, re.S).group(1)
        desc = re.search(r"<desc[^>]*>(.*?)</desc>", self.body, re.S).group(1)
        self.assertEqual(title, "PC5 progress matrix")
        self.assertIn("complete", desc)
        self.assertIn("physical boot", desc)
        self.assertRegex(desc, r"\d+ of \d+",
                         "the description must carry counts, not adjectives")

    def test_the_readme_embeds_the_image_with_descriptive_alt_text(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        link = re.search(r"!\[([^\]]+)\]\(docs/assets/pc5-progress-matrix\.svg\)", readme)
        self.assertIsNotNone(link, "the matrix has to be embedded with alt text")
        self.assertGreaterEqual(len(link.group(1).split()), 12)
        self.assertIn(BEGIN, readme)
        self.assertIn(END, readme)

    def test_the_readme_still_carries_the_text_fallback(self):
        block = matrix_block()
        self.assertIn("| Progress region | Figure |", block)
        self.assertGreaterEqual(len(block.splitlines()), 10,
                                "the block has to survive images being switched off")


class DriftIsCaughtTest(unittest.TestCase):
    """The guard must fail on the things it claims to fail on, in a scratch copy."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.mkdtemp(prefix="pc5-matrix-drift-")
        cls.scratch = Path(cls.directory) / "repo"
        shutil.copytree(REPO, cls.scratch,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        cls.assets = {path: (cls.scratch / path).read_text(encoding="utf-8")
                      for path in (str(MATRIX_ASSET), "README.md")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.directory, ignore_errors=True)

    def setUp(self):
        for path, text in self.assets.items():
            (self.scratch / path).write_text(text, encoding="utf-8")

    def check(self):
        return subprocess.run([sys.executable, "tools/presentation.py", "check"],
                              cwd=self.scratch, capture_output=True, text=True,
                              env={**os.environ, "TMPDIR": self.directory})

    def test_the_untampered_scratch_passes(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_figure_painted_greener_than_the_data_is_rejected(self):
        asset = self.scratch / MATRIX_ASSET
        text = asset.read_text(encoding="utf-8")
        blocked = f'fill="{P.MATRIX_STATE["blocked"][0]}" stroke="{P.RED}"'
        tampered = text.replace(blocked, f'fill="{P.GREEN}" stroke="{P.GREEN}"')
        self.assertNotEqual(tampered, text, "the control needs a real substitution")
        asset.write_text(tampered, encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{MATRIX_ASSET}: stale", result.stdout)

    def test_a_hand_edited_readme_block_is_rejected(self):
        readme = self.scratch / "README.md"
        text = readme.read_text(encoding="utf-8")
        inflated = text.replace("| Milestones verified | ",
                                "| Milestones verified | 99.0% (34/35) · ", 1)
        self.assertNotEqual(inflated, text)
        readme.write_text(inflated, encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("matrix block does not match", result.stdout)

    def test_a_deleted_figure_is_rejected(self):
        (self.scratch / MATRIX_ASSET).unlink()
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("pc5-progress-matrix.svg: missing", result.stdout)

    def test_sync_repairs_a_stale_figure_and_check_then_passes(self):
        asset = self.scratch / MATRIX_ASSET
        asset.write_text("<svg/>\n", encoding="utf-8")
        synced = subprocess.run([sys.executable, "tools/presentation.py", "sync"],
                                cwd=self.scratch, capture_output=True, text=True,
                                env={**os.environ, "TMPDIR": self.directory})
        self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
        self.assertIn("progress-matrix", synced.stdout)
        self.assertEqual(asset.read_text(encoding="utf-8"),
                         (REPO / MATRIX_ASSET).read_text(encoding="utf-8"),
                         "the scratch render must match the committed one")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_check_does_not_rewrite_the_tree(self):
        before = {path: (self.scratch / path).read_bytes() for path in self.assets}
        self.check()
        after = {path: (self.scratch / path).read_bytes() for path in self.assets}
        self.assertEqual(before, after)


class PostMergePolicyTest(unittest.TestCase):
    """The rule that keeps this figure current is a tracked instruction, not a habit."""

    @classmethod
    def setUpClass(cls):
        path = REPO / "AGENTS.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        # a rule read by a person is the rule with its line wrapping folded away; the
        # tests quote phrases, and a phrase split over two source lines is one sentence
        cls.flat = re.sub(r"\s+", " ", text).lower()

    def assertPhrase(self, needle):
        self.assertIn(needle, self.flat, f"the policy has to state {needle!r}")

    def test_the_repository_carries_an_agents_instruction_file(self):
        self.assertTrue((REPO / "AGENTS.md").exists(),
                        "AGENTS.md must exist where an agent reads it before starting")

    def test_the_policy_names_the_trigger_and_every_action(self):
        for needle in ("after every successful merge", "full sha", "recalculate",
                       "regenerate", "make presentation-check", "roadmap.json",
                       "without waiting to be asked"):
            self.assertPhrase(needle)

    def test_the_policy_covers_merges_the_agent_did_not_make(self):
        self.assertPhrase("another person or agent")
        self.assertPhrase("multiple merges")

    def test_the_policy_states_that_a_merge_is_the_only_trigger(self):
        for needle in ("never merge", "opening a pull request", "passing tests"):
            self.assertPhrase(needle)

    def test_the_policy_forbids_inflating_the_figure(self):
        for needle in ("never inflate", "acceptance criteria", "blocked",
                       "never estimate a number to fill a cell"):
            self.assertPhrase(needle)

    def test_the_rule_is_reachable_from_the_contributor_entry_point(self):
        contributing = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("AGENTS.md", contributing)

    def test_no_competing_instruction_file_was_invented(self):
        for name in ("CLAUDE.md", ".cursorrules", ".github/copilot-instructions.md",
                     "docs/AGENTS-POLICY.md", ".github/AGENTS.md"):
            self.assertFalse((REPO / name).exists(),
                             f"{name} would compete with AGENTS.md for the same rule")

# ---------------------------------------------------------------- the artwork itself
CELL_ELEMENT = re.compile(
    r'<rect x="([\d.]+)" y="([\d.]+)" width="[\d.]+" height="(\d+)" rx="3" '
    r'fill="(#[0-9a-f]{6})" stroke="(#[0-9a-f]{6})"[^>]*/>\s*'
    r'(?:<text[^>]*>([^<]*)</text>)?')
HEAD_ELEMENT = re.compile(
    r'<text x="(\d+)" y="(\d+)"[^>]*font-size="13"[^>]*font-weight="bold"[^>]*>'
    r'([A-Z][^<]{5,60}?)</text>')
ROW_LABEL = re.compile(r'<text x="(24)" y="(\d+)"[^>]*font-weight="bold"[^>]*>(L\d)</text>')


def drawn_cells(body: str) -> list:
    """Every painted cell in the figure, read out of the artwork: (x, y, height, fill,
    stroke, label)."""
    cells = []
    for x, y, height, fill, stroke, label in CELL_ELEMENT.findall(body):
        if int(height) < 18:                       # legend swatches and separators
            continue
        cells.append((float(x), float(y), int(height), fill, stroke, (label or "").strip()))
    return cells


def region_bands(body: str) -> dict:
    """{title: (column x, first y, last y)} — the span a region's cells live in.

    Two columns of regions share the canvas, so a band is bounded by the next head *in the same
    column*; reading the bands from the artwork is what keeps these tests independent of the
    generator's internal constants.
    """
    heads = sorted(((int(x), int(y), title) for x, y, title in HEAD_ELEMENT.findall(body)))
    bands = {}
    for column in sorted({h[0] for h in heads}):
        same = [h for h in heads if h[0] == column]
        for index, (_x, y, title) in enumerate(same):
            stop = same[index + 1][1] if index + 1 < len(same) else 10 ** 6
            bands[title.strip()] = (column, y, stop)
    return bands


def cells_in(body: str, region: str, width: int = 576) -> list:
    """The cells drawn inside one region: its own column, its own vertical span.

    Both bounds matter.  A region's span runs to the next head *in its column*, so without the
    column bound the last region on the left would also claim every cell on the right.
    """
    bands = region_bands(body)
    if region not in bands:
        raise AssertionError(f"{region!r} is not a region head in the figure: {sorted(bands)}")
    column, low, high = bands[region]
    return [cell for cell in drawn_cells(body)
            if column - 1 <= cell[0] < column + width and low <= cell[1] < high]


def ladder_rows(body: str, region: str) -> dict:
    """{level: [fill, …]} — the ladder's cells collected per level, matched on baseline."""
    labels = {int(level[1:]): float(y) for _x, y, level in ROW_LABEL.findall(body)}
    rows = {level: [] for level in labels}
    for _x, y, _h, fill, _stroke, _label in cells_in(body, region):
        for level, label_y in labels.items():
            if abs(y - (label_y - 15)) < 9:          # a row's text sits 15px under its cells
                rows[level].append(fill)
                break
    return rows


def artwork_mismatches(body: str, data: dict, metrics: dict) -> list:
    """Every way the painted cells can contradict the files they are supposed to come from.

    Kept as a function so the same rules can be pointed at a doctored figure in
    `ArtworkGuardFalsifiabilityTest`: a check that has never been seen to fail is not a check.
    """
    problems = []
    states = {m["id"]: m["state"] for m in data["milestones"]}
    green = P.GREEN

    for _x, _y, _h, fill, stroke, label in cells_in(body, "MILESTONES BY STATE"):
        if not label:
            continue
        milestone = label.replace(" !", "")
        state = states.get(milestone)
        if state is None:
            problems.append(f"a cell is labelled {milestone!r}, which is not a milestone")
            continue
        if fill == green and state != "complete":
            problems.append(f"{milestone} is {state} but painted verified")
        if state == "complete" and fill != green:
            problems.append(f"{milestone} is complete but not painted verified")
        if state == "blocked" and (fill == green or stroke != P.RED or not label.endswith(" !")):
            problems.append(f"{milestone} is blocked and is not drawn as blocked")

    for level, fills in sorted(ladder_rows(body, "EVIDENCE LADDER — ONE ROW PER LEVEL").items()):
        want = metrics["levels"].get(level, {}).get("complete")
        if want is None:
            continue
        got = sum(1 for fill in fills if fill == green)
        if got != want:
            problems.append(f"L{level} paints {got} verified cells; {want} milestones are "
                            "complete at that level in roadmap.json")

    grid = [c for c in cells_in(body, "MILESTONES BY STATE") if c[5]]
    if len(grid) != len(data["milestones"]):
        problems.append(f"the state grid draws {len(grid)} cells for {len(data['milestones'])} "
                        "milestones")
    streams = [c for c in cells_in(body, "WORKSTREAMS")]
    green_streams = sum(1 for c in streams if c[3] == green)
    want_streams = sum(s["complete"] for s in metrics["streams"])
    if green_streams != want_streams:
        problems.append(f"the workstream rows paint {green_streams} verified cells, the "
                        f"roadmap supports {want_streams}")
    gates = [c for c in cells_in(body, "COMMITTED VALIDATION RECORDS") if c[5]]
    if sum(1 for c in gates if c[3] == green) != metrics["gate_passed"]:
        problems.append("a validation cell is green without a passed record behind it")
    if len(gates) != len(metrics["gates"]):
        problems.append(f"{len(gates)} gate cells drawn for {len(metrics['gates'])} records")

    for problem in P.collisions(body):
        problems.append(problem)
    for clipped in re.findall(r">([^<>]*\u2026[^<>]*)<", body):
        problems.append(f"a label is truncated: {clipped!r}")
    return problems


class TheFigureSaysWhatTheFilesSayTest(unittest.TestCase):
    """The drawing, not only the colour table, has to answer for every green cell.

    The tests above check `MATRIX_STATE` and that the committed asset equals a fresh render.
    Those two facts together still permit a figure that paints verified cells for milestones no
    file records: change `fill=` at the drawing site and both stay green, because the render and
    the asset agree and the colour table is untouched.  These tests read the painted rectangles
    and hold them against `roadmap.json`, the records and the tree.
    """

    @classmethod
    def setUpClass(cls):
        cls.body = MATRIX_BYTES()
        cls.data = roadmap()
        cls.metrics = P.progress_metrics(cls.data, P.load_records())

    def test_the_artwork_agrees_with_the_data_it_claims_to_be_drawn_from(self):
        self.assertEqual(artwork_mismatches(self.body, self.data, self.metrics), [],
                         "the figure and the files disagree")

    def test_every_green_milestone_cell_is_a_milestone_the_roadmap_calls_complete(self):
        states = {m["id"]: m["state"] for m in self.data["milestones"]}
        offenders = []
        for _x, _y, _h, fill, _stroke, label in cells_in(self.body, "MILESTONES BY STATE"):
            milestone = label.replace(" !", "")
            if milestone and fill == P.GREEN and states.get(milestone) != "complete":
                offenders.append((milestone, states.get(milestone)))
        self.assertEqual(offenders, [])

    def test_levels_three_to_six_cannot_show_a_verified_cell(self):
        rows = ladder_rows(self.body, "EVIDENCE LADDER — ONE ROW PER LEVEL")
        self.assertEqual(sorted(rows), [1, 2, 3, 4, 5, 6],
                         "the six ladder rows were not all found in the figure")
        for level, fills in rows.items():
            verified = sum(1 for fill in fills if fill == P.GREEN)
            self.assertEqual(verified, self.metrics["levels"][level]["complete"],
                             f"L{level} paints {verified} cells but the roadmap verifies "
                             f"{self.metrics['levels'][level]['complete']}")
            if level >= 3:
                self.assertEqual(verified, 0, f"L{level} is above what a host can prove")

    def test_the_verified_cell_count_is_the_verified_milestone_count(self):
        grid = [c for c in cells_in(self.body, "MILESTONES BY STATE") if c[5]]
        self.assertEqual(sum(1 for c in grid if c[3] == P.GREEN),
                         self.metrics["states"]["complete"])
        self.assertEqual(len(grid), self.metrics["milestone_total"])

    def test_every_ratio_in_the_text_fallback_is_drawn_in_the_figure(self):
        """The README table and the SVG are the same census, so neither may carry a stray count.

        Compared as `a/b` pairs rather than as sentences, because the two render the same number
        with different words around it.
        """
        block = matrix_block()
        drawn = re.sub(r"<[^>]+>", " ", self.body)
        pairs = set(re.findall(r"\b(\d+)/(\d+)\b", block))
        self.assertGreaterEqual(len(pairs), 9, f"the fallback table lost rows: {sorted(pairs)}")
        for numerator, denominator in sorted(pairs):
            needle = f"{numerator}/{denominator}"
            self.assertIn(needle, drawn,
                          f"{needle!r} is asserted in README.md's pc5:matrix block but the "
                          f"figure does not show it")

    def test_a_citation_and_a_filed_record_are_not_labelled_the_same_thing(self):
        figure = self.body.lower()
        self.assertIn("cited paths", figure)
        self.assertIn("milestones citing at least one in-tree path", figure)
        self.assertIn("milestones with a record filed under docs/evidence/", figure)
        self.assertIn("a citation names a path", figure)
        self.assertNotIn("filed evidence", figure.split("</desc>")[-1],
                         "a path the roadmap names is not filed evidence")
        block = matrix_block()
        self.assertIn("Milestones citing at least one in-tree path", block)
        self.assertIn("a citation, not a run", block)
        self.assertNotIn("carrying a filed evidence path", block)

    def test_nothing_in_the_figure_is_clipped_or_collides(self):
        self.assertEqual(re.findall(r">[^<>]*\u2026[^<>]*<", self.body), [],
                         "a clipped label hides a number or a dependency")
        self.assertEqual(P.collisions(self.body), [],
                         "two labels share a line; region_head must split the column")


class ArtworkGuardFalsifiabilityTest(unittest.TestCase):
    """The rules above must fail on a doctored figure, or they are decoration."""

    def setUp(self):
        self.data = roadmap()
        self.metrics = P.progress_metrics(self.data, P.load_records())
        self.body = MATRIX_BYTES()

    def test_the_rules_catch_a_green_cell_the_data_does_not_support(self):
        """A doctored figure, built rather than string-surgically edited, must be reported.

        A check nobody has seen fail is not evidence of anything, so the ladder rule is pointed
        at a small figure whose L6 row — a level with no verified milestone anywhere in this
        repository — carries one green cell.
        """
        heads = (P.text(24, 91, "MILESTONES BY STATE", size=13, weight="bold")
                 + P.text(24, 200, "EVIDENCE LADDER — ONE ROW PER LEVEL", size=13,
                          weight="bold")
                 + P.text(24, 400, "WORKSTREAMS", size=13, weight="bold")
                 + P.text(24, 500, "COMMITTED VALIDATION RECORDS", size=13, weight="bold"))
        row_label = P.text(24, 245, "L6", size=11, weight="bold")
        forged = P.matrix_cell(300, 232, 30, 22, "20", fill=P.GREEN, ink=P.GREEN_INK, size=10)
        body = P.svg(1200, 700, heads + row_label + forged, "t", "d")
        problems = artwork_mismatches(body, self.data, self.metrics)
        self.assertTrue(any("L6" in problem and "verified" in problem for problem in problems),
                        f"the forged cell was not reported: {problems}")
        self.assertTrue(any("grid draws 0 cells" in problem for problem in problems),
                        "an empty state grid was not reported either")

    def test_the_collision_guard_fires_on_two_labels_that_share_a_line(self):
        wide = P.text(24, 40, "A" * 60, size=13, weight="bold")
        right = P.text(588, 40, "B" * 30, size=13, weight="bold", anchor="end")
        self.assertTrue(P.collisions(P.svg(1200, 100, wide + right, "t", "d")))

    def test_the_collision_guard_stays_quiet_when_the_pair_fits(self):
        body = P.svg(1200, 100,
                     P.text(24, 40, "A" * 20, size=13)
                     + P.text(588, 40, "B" * 20, size=13, anchor="end"), "t", "d")
        self.assertEqual(P.collisions(body), [])

    def test_the_gate_refuses_a_colliding_figure_even_when_it_is_up_to_date(self):
        """`presentation-check` is the gate CI would be if CI existed, so it needs teeth.

        Byte equality alone cannot see a layout defect: change the drawing code so a heading
        prints over its own figure, and every regenerated asset still matches its render. The
        geometry rule therefore has to be enforced inside `check`, not only here — and that
        claim is only worth making if it can fail, which is what handing `check` a current and
        colliding figure does.
        """
        body = P.svg(1200, 120,
                     P.text(24, 40, "E" * 70, size=13, weight="bold")
                     + P.text(588, 40, "F" * 40, size=13, weight="bold", anchor="end"),
                     "colliding", "a heading and its figure on one line")
        directory = Path(tempfile.mkdtemp(prefix="pc5-gate-collision-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        (directory / "collide.svg").write_text(body, encoding="utf-8")
        with unittest.mock.patch.object(P, "rendered", return_value={"collide.svg": body}), \
             unittest.mock.patch.object(P, "ASSETS", directory), \
             unittest.mock.patch.object(P, "render_blocks", return_value={}):
            problems = P.check(roadmap(), {})
        self.assertTrue(any("labels on one baseline overlap" in problem for problem in problems),
                        f"the gate waved a colliding figure through: {problems}")

    def test_region_head_clips_the_title_instead_of_letting_the_pair_overlap(self):
        body, _y = P.region_head(24, 20, 564, "T" * 70, right="R" * 40)
        self.assertIn("\u2026", body, "an over-long title must be clipped, not allowed to collide")
        self.assertEqual(P.collisions(P.svg(1200, 100, body, "t", "d")), [])

    def test_measure_fit_and_the_guard_share_one_ruler(self):
        self.assertEqual(P.measure("x" * 10, 10), 10 * 10 * P.CHAR_EM)
        self.assertEqual(P.fit("x" * 10, 100, 10), "x" * 10)
        self.assertTrue(P.fit("x" * 100, 100, 10).endswith("\u2026"))
        self.assertEqual(P.fit("x" * 100, 2, 10), "x" * 100, "a 1-character room is no room")

    def setUp_body(self):
        return MATRIX_BYTES()


class EvidenceCensusIsFalsifiableTest(unittest.TestCase):
    """`evidence_present` is a claim about the disk, so it has to move when the disk moves.

    Every cited path resolves in the committed repository, which is exactly why the earlier
    recomputation could not fail: numerator and denominator were the same 39.  A fixture tree
    with one deleted file makes the assertion falsifiable.
    """

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="pc5-evidence-fixture-"))
        self.addCleanup(lambda: shutil.rmtree(self.directory, ignore_errors=True))
        (self.directory / "docs/architecture").mkdir(parents=True, exist_ok=True)
        (self.directory / "docs/evidence").mkdir(parents=True, exist_ok=True)
        (self.directory / "PS5UPDATE.PUP").write_bytes(b"pointer\n")

    def fixture(self, evidence: dict, existing: list) -> dict:
        """A two-milestone roadmap, so numerator and denominator are known exactly.

        The committed roadmap cannot be used for this: all 39 of its cited paths resolve, which
        makes "the census verified the disk" and "the census trusted the roadmap" indistinguishable.
        """
        for path in existing:
            file = self.directory / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("{}\n", encoding="utf-8")
        milestones, seen = [], set()
        source = {m["id"]: m for m in roadmap()["milestones"]}
        # M19 is `product.destination.milestone`, so it has to survive the pruning.
        for milestone in list(evidence) + [roadmap()["product"]["destination"]["milestone"]]:
            if milestone in seen:
                continue
            seen.add(milestone)
            row = copy.deepcopy(source[milestone])
            row["evidence"] = list(evidence.get(milestone, []))
            row["dependencies"] = []
            milestones.append(row)
        data = copy.deepcopy(roadmap())
        data["milestones"] = milestones
        return data

    def metrics_for(self, data: dict) -> dict:
        absent = {"available": False, "interfaces": [], "ops": 0, "unsupported": 0}
        no_claims = {"available": False, "total": 0, "counts": {}, "ids": []}
        with unittest.mock.patch.object(P, "ROOT", self.directory), \
             unittest.mock.patch.object(P, "contract_inventory", return_value=absent), \
             unittest.mock.patch.object(P, "claim_inventory", return_value=no_claims):
            return P.progress_metrics(data, {})

    def test_deleting_a_cited_path_lowers_the_count_by_one(self):
        data = self.fixture({"M0": ["docs/TEST_RESULTS.md", "docs/never-filed-anywhere.md"],
                             "M1a": ["docs/TEST_RESULTS.md"]},
                            ["docs/TEST_RESULTS.md"])
        metrics = self.metrics_for(data)
        self.assertEqual(metrics["evidence_total"], 3, "three citations were made in the fixture")
        self.assertEqual(metrics["evidence_present"], 2,
                         "one cited path was not created; the count must not include it")
        self.assertEqual(metrics["milestones_with_evidence"], 2)

    def test_the_figure_draws_the_unresolved_citation_as_unresolved(self):
        data = self.fixture({"M0": ["docs/absent.md"]}, [])
        metrics = self.metrics_for(data)
        self.assertEqual((metrics["evidence_present"], metrics["evidence_total"]), (0, 1))
        absent = {"available": False, "interfaces": [], "ops": 0, "unsupported": 0}
        no_claims = {"available": False, "total": 0, "counts": {}, "ids": []}
        with unittest.mock.patch.object(P, "ROOT", self.directory), \
             unittest.mock.patch.object(P, "contract_inventory", return_value=absent), \
             unittest.mock.patch.object(P, "claim_inventory", return_value=no_claims):
            body = P.progress_matrix(data, {})
            block = P.matrix_markdown(data, {})
        for where, text in (("figure", body), ("README block", block)):
            self.assertIn("0/1", text,
                          f"a citation whose file is absent still reads as resolved in the {where}")

    def test_a_filed_record_is_counted_apart_from_a_citation(self):
        data = self.fixture({"M0": ["docs/evidence/boot.json"],
                             "M1a": ["docs/TEST_RESULTS.md"]},
                            ["docs/evidence/boot.json", "docs/TEST_RESULTS.md"])
        metrics = self.metrics_for(data)
        self.assertEqual((metrics["evidence_present"], metrics["evidence_total"]), (2, 2),
                         "both citations exist, so the count must not hide that")
        self.assertEqual(metrics["filed_record_paths"], 1)
        self.assertEqual(metrics["milestones_with_filed_record"], 1)
        self.assertEqual(metrics["milestones_with_evidence"], 2,
                         "both milestones cite a path that exists; only one filed a record")

    def test_the_counted_sets_are_not_interchangeable(self):
        data = self.fixture({"M0": ["docs/TEST_RESULTS.md"]}, ["docs/TEST_RESULTS.md"])
        metrics = self.metrics_for(data)
        self.assertEqual(metrics["milestones_with_evidence"], 1)
        self.assertEqual(metrics["milestones_with_filed_record"], 0,
                         "a path outside docs/evidence/ is a citation, not a filed record")
        absent = {"available": False, "interfaces": [], "ops": 0, "unsupported": 0}
        no_claims = {"available": False, "total": 0, "counts": {}, "ids": []}
        with unittest.mock.patch.object(P, "ROOT", self.directory), \
             unittest.mock.patch.object(P, "contract_inventory", return_value=absent), \
             unittest.mock.patch.object(P, "claim_inventory", return_value=no_claims):
            body = P.progress_matrix(data, {})
        self.assertIn("1/1", body)
        self.assertIn("0/1", body)



def MATRIX_BYTES() -> str:
    return (REPO / MATRIX_ASSET).read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
