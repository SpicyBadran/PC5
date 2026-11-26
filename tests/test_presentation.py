"""The presentation layer has to be generated, and it has to stay honest.

The README is the first thing anybody reads, which makes it the easiest place in this
repository to overstate progress. These tests hold it to three rules:

  * the figures and the status table are *derived* from `roadmap.json` — change a
    milestone state and the drawing changes, without editing a single SVG;
  * the checks that guard the README actually fire: a stale figure, a hand-edited
    status row, a broken link, an image without alt text and a banned claim each have
    a test that expects a failure;
  * nothing in the presentation claims evidence the repository does not have — no
    screenshot, no percentage of compatibility, no "CI passing".
"""

from __future__ import annotations

import copy
import contextlib
import json
import re
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import presentation as P  # noqa: E402

SVG_NS = "{http://www.w3.org/2000/svg}"


def roadmap():
    return json.loads((REPO / "docs/architecture/roadmap.json").read_text(encoding="utf-8"))


def with_state(data, milestone, state):
    changed = copy.deepcopy(data)
    for entry in changed["milestones"]:
        if entry["id"] == milestone:
            entry["state"] = state
    return changed


class FigureTest(unittest.TestCase):
    def setUp(self):
        self.data = roadmap()

    def test_every_figure_is_well_formed_svg(self):
        for name, body in P.rendered(self.data).items():
            root = ET.fromstring(body)
            self.assertEqual(root.tag, SVG_NS + "svg", name)
            self.assertTrue(root.get("viewBox"), f"{name} has no viewBox")
            self.assertTrue(int(root.get("width")) > 0 and int(root.get("height")) > 0)
            self.assertIsNotNone(root.find(f"{SVG_NS}title"), f"{name} has no title")
            self.assertIsNotNone(root.find(f"{SVG_NS}desc"), f"{name} has no desc")

    def test_figures_are_deterministic(self):
        first = P.rendered(roadmap())
        second = P.rendered(roadmap())
        self.assertEqual(first, second)

    def test_figures_carry_no_external_references(self):
        for name, body in P.rendered(self.data).items():
            # the SVG namespace is the only URI allowed to appear in a figure
            for bad in ("https://", "<image", "@font-face", "xlink:href", "url("):
                self.assertNotIn(bad, body, f"{name} reaches outside the repository: {bad}")

    def test_a_milestone_change_changes_the_drawing(self):
        """Derivation, proven: no SVG is edited by hand for a status change."""
        before = P.rendered(self.data)
        after = P.rendered(with_state(self.data, "M1a", "blocked"))
        self.assertNotEqual(before["pc5-architecture.svg"], after["pc5-architecture.svg"])

    def test_the_destination_is_drawn_as_blocked(self):
        body = P.rendered(self.data)["pc5-architecture.svg"]
        self.assertIn("BLOCKED", body)
        self.assertIn("Genuine Sony PS5 system software", body)

    def test_firmware_is_not_drawn_as_verified(self):
        """M1h completing must not promote firmware on a real machine."""
        body = P.rendered(self.data)["pc5-architecture.svg"]
        firmware = body.split("Firmware", 1)[1][:900]
        self.assertIn("NOT TESTED", firmware)
        self.assertNotIn("VERIFIED", firmware)

    def test_the_progress_figure_counts_states_from_the_graph(self):
        data = self.data
        complete = sum(1 for m in data["milestones"] if m["state"] == "complete")
        body = P.rendered(data)["pc5-progress.svg"]
        self.assertIn(f"{complete} of {len(data['milestones'])} complete", body)
        for stream_id in ("WS10", "WS12"):
            blocked = sum(1 for m in data["milestones"]
                          if m["workstream"] == stream_id and m["state"] == "blocked")
            self.assertTrue(blocked > 0, stream_id)
            self.assertIn(f"{blocked} blocked", body)

    def test_long_workstream_names_are_clipped_not_overflowing(self):
        body = P.rendered(self.data)["pc5-progress.svg"]
        for text in re.findall(r">([^<>]{45,})</text>", body):
            if "PS5 platform compatibility" in text or "Hardware discovery" in text:
                self.assertIn("\u2026", text, f"unclipped label: {text!r}")


class StatusTableTest(unittest.TestCase):
    def setUp(self):
        self.data = roadmap()
        self.rows = P.status_rows(self.data)
        self.labels = {title: label for title, label, _colour, _evidence in self.rows}

    def test_one_row_per_declared_capability(self):
        self.assertEqual(len(self.rows), len(P.ROWS))
        self.assertEqual(len({row[0] for row in self.rows}), len(P.ROWS))

    def test_labels_come_from_the_roadmap(self):
        by_id = {m["id"]: m["state"] for m in self.data["milestones"]}
        expected = P.STATE_STYLE[by_id["M10"]][0]
        self.assertEqual(self.labels["PS5 platform contract"], expected)
        self.assertEqual(self.labels["Genuine Sony system software executing"], "BLOCKED")

    def test_physical_boot_is_not_tested_and_not_verified(self):
        self.assertEqual(self.labels["Physical PC boot"], "NOT TESTED")

    def test_a_filed_physical_record_promotes_the_row(self):
        """The row is evidence-driven: the label follows the records, not the prose."""
        original = P.physical_evidence
        try:
            P.physical_evidence = lambda: [REPO / "docs/evidence/M1_RECORD_TEMPLATE.json"]
            rows = dict((t, l) for t, l, _c, _e in P.status_rows(self.data))
            self.assertEqual(rows["Physical PC boot"], "VERIFIED")
        finally:
            P.physical_evidence = original

    def test_the_readme_block_matches_the_generator(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn(P.status_markdown(self.data), readme)

    def test_the_block_lists_every_row(self):
        block = P.status_markdown(self.data)
        for title, _label, _colour, _evidence in self.rows:
            self.assertIn(f"| {title} |", block)


def mirror(root: Path) -> None:
    """Link the parts of the real tree a fixture lacks, except the files a test edits.

    `docs/ABOUT.md` cites code by path (`../kernel/main.c`), so a fixture that mirrors
    only `docs/` reports every citation as a dead link and the test then passes or fails
    on the shape of the fixture rather than on the rule it is checking.  Linking the top
    level costs nothing and keeps the link rules honest; the two generated pages stay
    copies, because a test edits them and must not write through a link into the
    repository.
    """
    for entry in REPO.iterdir():
        if entry.name in (".git", ".arena", "build", "docs", "README.md", ".pytest_cache"):
            continue
        target = root / entry.name
        if target.exists() or target.is_symlink():
            continue
        target.symlink_to(entry, target_is_directory=entry.is_dir())


def page_copy(source: Path, destination: Path) -> None:
    """Replace a symlinked page with a copy, so an edit stays in the fixture."""
    if destination.is_symlink():
        destination.unlink()
    shutil.copy(source, destination)


@contextlib.contextmanager
def redirected(root: Path, patch_root: bool = False):
    """Point the checker at a temporary copy of the files it edits and reads.

    `pages()` reads these module attributes when it is called, which is why they are
    patched here rather than snapshotted: a test that only redirects the README would have
    the About assertions run against the real repository, and would edit it.

    `patch_root` moves the module's idea of the repository root as well, which is what a
    test needs when the file under study is one the module reads by default — `LICENSE`,
    `PS5UPDATE.PUP` — rather than a page it writes.  Off by default, because a fixture that
    only swaps the pages keeps the real `docs/evidence/` under it and several tests rely on
    that.
    """
    saved = (P.README, P.ABOUT, P.ASSETS, P.ROOT)
    P.README = root / "README.md"
    P.ABOUT = root / "docs/ABOUT.md"
    P.ASSETS = root / "docs/assets"
    if patch_root:
        P.ROOT = root
    try:
        yield
    finally:
        P.README, P.ABOUT, P.ASSETS, P.ROOT = saved


class CheckTest(unittest.TestCase):
    """Each guard gets a failure it is supposed to catch."""

    def setUp(self):
        self.data = roadmap()
        self.tmp = Path(tempfile.mkdtemp())
        # the checker resolves README links against the file's own directory, so the
        # temporary copy needs the real documentation tree, linked not copied
        (self.tmp / "docs").mkdir()
        for entry in (REPO / "docs").iterdir():
            if entry.name == "assets":
                continue
            (self.tmp / "docs" / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
        # requirements-harness.txt is linked too: the README documents it at
        # the repository root, and a fixture without it would report a dead link.
        for name in ("CONTRIBUTING.md", "LICENSE", "requirements-harness.txt", "ci"):
            (self.tmp / name).symlink_to(REPO / name, target_is_directory=(REPO / name).is_dir())
        (self.tmp / "docs/assets").mkdir()
        for name, body in P.rendered(self.data).items():
            (self.tmp / "docs/assets" / name).write_text(body, encoding="utf-8")
        # docs/ABOUT.md is copied rather than left as the symlink the loop
        # created: a test that rewrites the page must not follow the link into the
        # real repository and edit the file it is supposed to be checking.
        about_link = self.tmp / "docs/ABOUT.md"
        if about_link.is_symlink():
            about_link.unlink()
        shutil.copy(REPO / "docs/ABOUT.md", about_link)
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        # the copy has no docs/PRESENTATION.md; the checker skips a missing file
        (self.tmp / "README.md").write_text(readme, encoding="utf-8")
        mirror(self.tmp)
        page_copy(REPO / "docs/ABOUT.md", self.tmp / "docs/ABOUT.md")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self) -> list:
        with redirected(self.tmp):
            return P.check(self.data)

    def test_the_real_repository_passes(self):
        self.assertEqual(P.check(self.data), [])

    def test_a_stale_figure_is_caught(self):
        path = self.tmp / "docs/assets/pc5-banner.svg"
        path.write_text(path.read_text().replace("PC5", "PC5!"), encoding="utf-8")
        problems = self.check()
        self.assertTrue(any("stale" in problem for problem in problems), problems)

    def test_a_hand_edited_status_row_is_caught(self):
        path = self.tmp / "README.md"
        text = path.read_text()
        good = P.status_markdown(self.data)
        self.assertIn("| Physical PC boot | **NOT TESTED** |", good)
        path.write_text(text.replace("| Physical PC boot | **NOT TESTED** |",
                                     "| Physical PC boot | **VERIFIED** |"))
        problems = self.check()
        self.assertTrue(any("status block" in problem for problem in problems), problems)

    def test_a_broken_image_path_is_caught(self):
        path = self.tmp / "README.md"
        path.write_text(path.read_text().replace("docs/assets/pc5-progress.svg",
                                                 "docs/assets/missing.svg"))
        problems = self.check()
        self.assertTrue(any("missing.svg" in problem for problem in problems), problems)

    def test_a_percent_encoded_nul_link_is_caught_not_raised(self):
        """`a%00b.md` decodes to a path with an embedded NUL.

        `Path.resolve()` raises `ValueError` for it. A link check must report the
        broken target rather than die on it - `link_problems` is the whole reason
        the presentation gate exists.
        """
        path = self.tmp / "README.md"
        path.write_text(path.read_text() + "\nSee [nul](docs/a%00b.md).\n")
        problems = self.check()
        self.assertTrue(any("link target missing" in problem for problem in problems),
                        problems)

    def test_an_image_without_alt_text_is_caught(self):
        path = self.tmp / "README.md"
        text = path.read_text()
        text = re.sub(r"!\[[^\]]*\]\(docs/assets/pc5-banner\.svg\)",
                      "![](docs/assets/pc5-banner.svg)", text)
        path.write_text(text)
        problems = self.check()
        self.assertTrue(any("alt text" in problem for problem in problems), problems)

    def test_a_banned_claim_is_caught(self):
        path = self.tmp / "README.md"
        path.write_text(path.read_text() + "\nPC5 boots on any PC.\n")
        problems = self.check()
        self.assertTrue(any("boots on any pc" in problem for problem in problems), problems)

    def test_a_missing_honesty_token_is_caught(self):
        path = self.tmp / "README.md"
        text = path.read_text().replace("NOT TESTED", "fine").replace("not tested", "fine")
        path.write_text(text)
        problems = self.check()
        self.assertTrue(any("NOT TESTED" in problem for problem in problems), problems)

    def test_a_figure_dropped_from_the_readme_is_caught(self):
        path = self.tmp / "README.md"
        path.write_text(path.read_text().replace("docs/assets/pc5-boot-workflow.svg",
                                                 "docs/assets/pc5-banner.svg"))
        problems = self.check()
        self.assertTrue(any("does not embed docs/assets/pc5-boot-workflow.svg" in problem
                            for problem in problems), problems)


class AboutPageTest(unittest.TestCase):
    """`docs/ABOUT.md` is generated into, checked, and not allowed to embellish."""

    def setUp(self):
        self.data = roadmap()
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "docs").mkdir()
        for entry in (REPO / "docs").iterdir():
            if entry.name == "assets":
                continue
            (self.tmp / "docs" / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
        (self.tmp / "docs/assets").mkdir()
        for name, body in P.rendered(self.data).items():
            (self.tmp / "docs/assets" / name).write_text(body, encoding="utf-8")
        for name in ("CONTRIBUTING.md", "LICENSE", "requirements-harness.txt"):
            source = REPO / name
            (self.tmp / name).symlink_to(source, target_is_directory=source.is_dir())
        mirror(self.tmp)
        # docs/ is linked as a tree, so both owned pages become copies here: a test that
        # rewrites a page must not follow a link into the repository and edit the file it
        # is supposed to be checking
        page_copy(REPO / "docs/ABOUT.md", self.tmp / "docs/ABOUT.md")
        page_copy(REPO / "README.md", self.tmp / "README.md")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self) -> list:
        with redirected(self.tmp):
            return P.check(self.data)

    def about(self) -> str:
        return (self.tmp / "docs/ABOUT.md").read_text(encoding="utf-8")

    def write_about(self, text: str) -> None:
        (self.tmp / "docs/ABOUT.md").write_text(text, encoding="utf-8")

    def test_the_shipped_page_is_current(self):
        self.assertEqual(P.check(self.data), [], "docs/ABOUT.md is out of date in the tree")

    def test_the_real_page_carries_every_generated_block(self):
        text = (REPO / "docs/ABOUT.md").read_text(encoding="utf-8")
        for name in P.ABOUT_BLOCKS:
            self.assertIn(f"<!-- pc5:{name}:begin -->", text)
            self.assertIn(f"<!-- pc5:{name}:end -->", text)

    def test_a_stale_about_block_is_caught(self):
        text = self.about()
        begin = "<!-- pc5:about-progress:begin -->"
        end = "<!-- pc5:about-progress:end -->"
        head, rest = text.split(begin, 1)
        _, tail = rest.split(end, 1)
        self.write_about(head + begin + "\n| | |\n|---|---|\n| **Overall completion** | **99.9%** |\n"
                         + end + tail)
        problems = self.check()
        self.assertTrue(any("ABOUT.md" in problem and "about-progress" in problem
                            for problem in problems), problems)

    def test_a_removed_marker_is_reported_against_the_right_page(self):
        """The failure has to name the file to fix, not the front page."""
        self.write_about(self.about().replace("<!-- pc5:about-identity:begin -->", ""))
        problems = self.check()
        self.assertTrue(any("ABOUT.md" in problem and "about-identity" in problem
                            for problem in problems), problems)
        self.assertFalse(any("README.md is missing" in problem for problem in problems),
                         problems)

    def test_a_hand_typed_percentage_in_the_about_prose_is_caught(self):
        text = self.about()
        self.write_about(text.replace("## 9. What this page will not say",
                                      "**PC5 is 71% complete.**\n\n## 9. What this page will not say", 1))
        problems = self.check()
        self.assertTrue(any("ABOUT.md" in problem and "71%" in problem
                            and "outside a generated block" in problem
                            for problem in problems), problems)

    def test_a_percentage_inside_a_generated_block_is_not_flagged(self):
        """The rule targets prose. Inside a block the number is the model's own output."""
        with redirected(self.tmp):
            blocks = P.render_blocks(self.data, P.load_records())
        self.assertIn("%", blocks["about-progress"])
        self.assertEqual([problem for problem in self.check() if "outside a generated block" in problem],
                         [])

    def test_a_forbidden_claim_on_the_about_page_is_caught(self):
        text = self.about()
        self.write_about(text.replace("## 9. What this page will not say",
                                      "PC5 runs PS5 games today.\n\n## 9. What this page will not say", 1))
        problems = self.check()
        self.assertTrue(any("ABOUT.md" in problem and "runs ps5 games" in problem
                            for problem in problems), problems)

    def test_the_about_page_states_the_missing_evidence_it_owes(self):
        for token in ("NOT TESTED", "Git LFS pointer", "nothing published"):
            self.assertIn(token, self.about())
        text = self.about().replace("nothing published", "shipping is close")
        self.write_about(text)
        self.assertTrue(any("honesty token" in problem for problem in self.check()),
                        "a page that drops its own caveat must fail")

    def test_the_identity_block_quotes_the_roadmap_verbatim(self):
        with redirected(self.tmp):
            block = P.render_blocks(self.data, P.load_records())["about-identity"]
        identity = self.data["product"]["identity"]
        self.assertIn(identity, block)
        self.assertIn(self.data["product"]["destination"]["statement"], block)
        for goal in self.data["product"]["non_goals"]:
            self.assertIn(goal, block)
        for forbidden in self.data["product"]["destination"]["must_not_be_satisfied_by"]:
            self.assertIn(forbidden, block)

    def test_the_licence_row_is_read_from_the_licence_file(self):
        """Not from memory, not from a badge: from `LICENSE`, whatever it happens to say.

        Asserted as an equality with the derivation rather than with a literal identifier, so
        the test pins the mechanism and cannot go red on a tree whose `LICENSE` states no
        grant (which is what `main` looked like while this was written).  The literal ids are
        pinned in the fixture-level tests above, where the file under test is the test's own.
        """
        licence = P.licence_identity()
        with redirected(self.tmp):
            block = P.render_blocks(self.data, P.load_records())["about-identity"]
        self.assertIn(P.licence_row(licence), block)
        self.assertIn(licence["spdx"] or "no identifier is printed", block)

    def test_a_licence_that_states_no_grant_is_reported_not_parsed(self):
        """The licence *text* is not a grant, and the two must not be confused.

        A repository can hold a verbatim copy of the FSF header — which is what `main` held
        while this page was being written — and the boilerplate inside it names a version
        nobody in this project chose.  Deriving an id from that copy would put a guess on a
        page a distributor may rely on; and raising over it would have taken the whole
        presentation layer down for a file the presentation layer cannot decide.
        """
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "LICENSE").write_text(
                "                    GNU GENERAL PUBLIC LICENSE\n"
                "                       Version 3, 29 June 2007\n\n"
                " Everyone is permitted to copy and distribute verbatim copies of this\n"
                " license document, but changing it is not allowed.\n\n"
                " ... either version 3 of the License, or (at your option) any later version.\n",
                encoding="utf-8")
            licence = P.licence_identity(tmp)
            self.assertIsNone(licence["spdx"])
            self.assertIn("without being a", licence["problem"])
            (tmp / "LICENSE").write_text("Copyright PC5 contributors. All rights reserved.\n",
                                         encoding="utf-8")
            self.assertIn("no sentence", P.licence_identity(tmp)["problem"])
            (tmp / "LICENSE").unlink()
            self.assertIn("unreadable", P.licence_identity(tmp)["problem"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_an_ambiguous_licence_still_generates_and_checks(self):
        """The property main's `LICENSE` broke: one unclear file, one reported gap.

        Asserted against a fixture tree rather than the repository, because whether *this*
        branch's `LICENSE` states a grant is a fact about the branch, and it must not decide
        whether the figures and the other eight blocks can be rendered at all.
        """
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "docs").mkdir()
            for entry in (REPO / "docs").iterdir():
                if entry.name == "assets":
                    continue
                (tmp / "docs" / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
            (tmp / "docs/assets").mkdir()
            for name, body in P.rendered(roadmap()).items():
                (tmp / "docs/assets" / name).write_text(body, encoding="utf-8")
            (tmp / "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007"
                                         "\n\nverbatim text, no grant sentence.\n",
                                         encoding="utf-8")
            for name in ("README.md", "CONTRIBUTING.md", "requirements-harness.txt", "ci"):
                source = REPO / name
                (tmp / name).symlink_to(source, target_is_directory=source.is_dir())
            page_copy(REPO / "docs/ABOUT.md", tmp / "docs/ABOUT.md")
            mirror(tmp)
            with redirected(tmp, patch_root=True):
                blocks = P.render_blocks(roadmap(), P.load_records())
            self.assertIn("no identifier is printed", blocks["about-identity"])
            self.assertNotIn("GPL-3.0", blocks["about-identity"],
                             "the boilerplate's version must not become the page's id")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_other_licence_refusals_each_name_their_own_reason(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE\nVersion 2, June 1991\n\n"
                                         "PC5 is distributed under GPL version 2.\n",
                                         encoding="utf-8")
            self.assertIn("the id cannot be derived", P.licence_identity(tmp)["problem"])
            (tmp / "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007"
                                         "\n\nPC5 is distributed under GPL version 2 only.\n",
                                         encoding="utf-8")
            self.assertIn("one of the two is stale", P.licence_identity(tmp)["problem"])
            # a file that argues with itself is refused; a file that says "only" while
            # quoting the later-version phrase is not
            (tmp / "LICENSE").write_text(
                "PC5 is distributed under GPL version 2 only (not \"or any later version\").\n",
                encoding="utf-8")
            self.assertEqual(P.licence_identity(tmp)["spdx"], "GPL-2.0-only")
            (tmp / "LICENSE").write_text("PC5 is released under the MIT License.\n",
                                         encoding="utf-8")
            self.assertEqual(P.licence_identity(tmp)["spdx"], "MIT")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_licence_row_cites_each_clause_to_its_own_source(self):
        """`LICENSE` may grant the licence without restating the vendoring rule."""
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "LICENSE").write_text("PC5 is distributed under GPL version 2 only.\n",
                                         encoding="utf-8")
            row = P.licence_row(P.licence_identity(tmp))
            self.assertIn("**GPL-2.0-only**", row)
            self.assertIn("not restated by `LICENSE`", row)
            (tmp / "LICENSE").write_text("PC5 is distributed under GPL version 2 only.\n"
                                         "No third-party source archive is vendored here.\n",
                                         encoding="utf-8")
            row = P.licence_row(P.licence_identity(tmp))
            self.assertIn("no third-party source archive is vendored", row)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_capability_buckets_are_the_roadmaps_own_fields(self):
        with redirected(self.tmp):
            block = P.render_blocks(self.data, P.load_records())["about-capabilities"]
        states = {state: sum(1 for m in self.data["milestones"] if m["state"] == state)
                  for state in ("complete", "in_progress", "not_started", "blocked")}
        for state, count in states.items():
            self.assertIn(f"| {count} |", block, f"{state} count did not reach the page")
        self.assertEqual(block.count("Not completed:"),
                         sum(len(f.get("does_not_complete") or []) for f in self.data["foundations"]),
                         "every recorded non-completion has to appear")

    def test_a_cited_evidence_link_resolves_from_the_about_page(self):
        """A generated block is written into docs/, so its links are docs-relative."""
        with redirected(self.tmp):
            block = P.render_blocks(self.data, P.load_records())["about-capabilities"]
        targets = re.findall(r"\]\(([^)\s#]+)", block)
        self.assertTrue(targets)
        for target in targets:
            if target.startswith(("http://", "https://")):
                continue
            resolved = (REPO / "docs" / target) if not target.startswith("../") \
                else (REPO / target[3:])
            self.assertTrue(resolved.exists(), f"docs/ABOUT.md would link at a missing file: {target}")

    def test_sync_is_idempotent_across_both_pages(self):
        with redirected(self.tmp):
            first = P.write(self.data)
            second = P.write(self.data)
        self.assertEqual(second, [], "a second sync changed a page it should have left alone")
        self.assertIsInstance(first, list)


class CommandContractTest(unittest.TestCase):
    """`make presentation` writes; `make presentation-check` reads; `make check` reads.

    The repository shipped the opposite for one release: the documentation said
    `presentation` regenerated the figures, while the target only checked them and the
    write lived in an undocumented, non-phony `presentation-sync`. These tests make the
    three-way contract explicit, so a future rename cannot quietly invert it again.
    """

    def setUp(self):
        self.makefile = (REPO / "Makefile").read_text(encoding="utf-8")
        self.targets = dict()
        current = None
        for line in self.makefile.splitlines():
            if line and not line[0].isspace() and line.rstrip().endswith(":") or (
                    line and not line[0].isspace() and ":" in line and "=" not in line.split(":")[0]):
                if ":" in line and not line.startswith("#"):
                    name = line.split(":")[0].strip()
                    if name and " " not in name and "=" not in name:
                        current = name
                        self.targets.setdefault(current, [])
                        continue
            if line.startswith("\t") and current:
                self.targets[current].append(line.strip())

    def recipe(self, name: str) -> str:
        return "\n".join(self.targets.get(name, []))

    def test_presentation_writes(self):
        self.assertIn("presentation.py sync", self.recipe("presentation"))

    def test_presentation_check_reads(self):
        self.assertIn("presentation.py check", self.recipe("presentation-check"))
        self.assertNotIn("sync", self.recipe("presentation-check"))

    def test_make_check_uses_the_read_only_target(self):
        """`check` runs the read-only gate and never the writer.

        Asserted across the whole rule -- prerequisite line plus recipe.  It used to
        look at the `check:` line alone, which stopped being the right place to look
        when the legs moved into the recipe so that a failed gate could not pre-empt
        the suite (tests/test_make_check_contract.py).  A one-line check would now be
        satisfied by a rule that runs the gate nowhere at all.
        """
        prereqs = [line for line in self.makefile.splitlines()
                   if line.startswith("check:")]
        self.assertTrue(prereqs)
        rule = prereqs[0] + "\n" + self.recipe("check")
        self.assertIn("presentation-check", rule,
                      "check must run the read-only presentation gate")
        self.assertNotIn("presentation.py sync", rule,
                         "check must not regenerate figures as a side effect")
        # The legs live in a shell loop now, so look at the loop's own list: the
        # writer must not be one of them.  (Checking only that the *string*
        # `$(MAKE) presentation` is absent would pass even if the gate had been
        # dropped from the loop entirely, which is the failure being guarded.)
        # `; do`, not ` do`: the latter silently matched the space before
        # "docs-check" and reported the loop as holding two gates.
        legs = re.search(r"for target in ([^;]+); do", rule)
        self.assertTrue(legs, "check no longer runs its gates through a loop; "
                              "update this test to read the new shape rather than "
                              "letting the guard go quietly stale")
        names = legs.group(1).split()
        self.assertEqual(names, ["unit", "psm", "docs-check", "citations-check",
                                 "presentation-check", "brand-check"])
        self.assertNotIn("presentation", names,
                         "check must not invoke the writing target")

    def test_every_documented_presentation_command_is_phony(self):
        phony = [line for line in self.makefile.splitlines() if line.startswith(".PHONY:")][0]
        for name in ("presentation", "presentation-check", "docs-check"):
            self.assertIn(name, phony.split(), f"{name} is not declared .PHONY")

    def test_the_old_alias_is_gone(self):
        self.assertNotIn("presentation-sync:", self.makefile)

    def test_generation_updates_stale_output_and_check_notices(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "docs").mkdir()
            for entry in (REPO / "docs").iterdir():
                if entry.name != "assets":
                    (tmp / "docs" / entry.name).symlink_to(
                        entry, target_is_directory=entry.is_dir())
            for name in ("CONTRIBUTING.md", "LICENSE", "requirements-harness.txt",
                         "ci", "brand"):
                (tmp / name).symlink_to(REPO / name,
                                        target_is_directory=(REPO / name).is_dir())
            (tmp / "docs/assets").mkdir(parents=True)
            for name, body in P.rendered(roadmap()).items():
                (tmp / "docs/assets" / name).write_text("stale\n", encoding="utf-8")
            (tmp / "README.md").write_text(
                (REPO / "README.md").read_text().replace(
                    P.status_markdown(roadmap()), P.BEGIN + "\n" + P.END),
                encoding="utf-8")
            mirror(tmp)
            # the About page is a copy for the same reason the README is: `write()` below
            # has to be able to fix it without fixing the repository with it
            page_copy(REPO / "docs/ABOUT.md", tmp / "docs/ABOUT.md")
            with redirected(tmp):
                self.assertTrue(P.check(roadmap()), "stale output must fail the check")
                P.write(roadmap())
                self.assertEqual(P.check(roadmap()), [], "generation must fix it")
                before = {p: p.read_bytes() for p in (tmp / "docs/assets").iterdir()}
                self.assertEqual(P.write(roadmap()), [], "a clean generation writes nothing")
                self.assertEqual(before, {p: p.read_bytes() for p in (tmp / "docs/assets").iterdir()})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_check_does_not_modify_the_repository(self):
        tracked = [REPO / "README.md"] + sorted((REPO / "docs/assets").iterdir())
        before = {path: path.read_bytes() for path in tracked}
        P.check(roadmap())
        self.assertEqual(before, {path: path.read_bytes() for path in tracked})

    def test_generation_is_deterministic_byte_for_byte(self):
        first = P.rendered(roadmap())
        second = P.rendered(roadmap())
        self.assertEqual({k: v.encode() for k, v in first.items()},
                         {k: v.encode() for k, v in second.items()})


class LayoutGuardTest(unittest.TestCase):
    """Figures are geometry; text that leaves its box is a defect, not a taste question."""

    def test_no_text_runs_outside_a_figure(self):
        data = roadmap()
        for name, body in P.rendered(data).items():
            width = int(re.search(r'width="(\d+)"', body).group(1))
            self.assertEqual(P.overflow(body, width), [], name)

    def test_the_overflow_guard_actually_fires(self):
        long_line = P.text(10, 20, "x" * 200, size=14)
        body = P.svg(400, 60, long_line, "t", "d")
        self.assertTrue(P.overflow(body, 400))

    def test_workstream_labels_stay_clear_of_the_bars(self):
        body = P.rendered(roadmap())["pc5-progress.svg"]
        bar_x = 372
        for x, size, anchor, content in P.TEXT_ELEMENT.findall(body):
            if float(x) != 96.0:
                continue
            label = re.sub(r"<[^>]+>", "", content)
            self.assertLess(float(x) + len(label) * float(size) * 0.62, bar_x - 6,
                            f"label would overlap its bar: {label!r}")

    def test_every_figure_scales_for_mobile(self):
        for name, body in P.rendered(roadmap()).items():
            width = int(re.search(r'width="(\d+)"', body).group(1))
            self.assertLessEqual(width, 1200, name)
            largest = max(float(size) for _x, size, _a, _c in P.TEXT_ELEMENT.findall(body))
            smallest = min(float(size) for _x, size, _a, _c in P.TEXT_ELEMENT.findall(body))
            self.assertGreaterEqual(smallest, 10, f"{name} has text too small to read")
            self.assertLessEqual(largest, 64, name)


class DynamicDashboardTest(unittest.TestCase):
    """A change in the data must change the page. That is the whole contract."""

    def setUp(self):
        self.data = roadmap()
        self.records = P.load_records()
        self.assertTrue(self.records, "no validation records committed: the dashboard would "
                                      "have nothing to quote")

    def test_changing_a_milestone_changes_the_statistics(self):
        before = P.stats_markdown(self.data, self.records)
        after = P.stats_markdown(with_state(self.data, "M1a", "blocked"), self.records)
        self.assertNotEqual(before, after)
        self.assertIn("| Blocked | 7 |", after)

    def test_changing_a_workstream_changes_the_dashboard(self):
        changed = copy.deepcopy(self.data)
        changed["workstreams"].append({"id": "WS99", "name": "probe", "serves_level": "1",
                                       "purpose": "test fixture"})
        self.assertNotEqual(P.dashboard(self.data, self.records),
                            P.dashboard(changed, self.records))

    def test_changing_the_destination_state_changes_the_figures(self):
        before = P.dashboard(self.data, self.records)
        after = P.dashboard(with_state(self.data, "M19", "in_progress"), self.records)
        self.assertNotEqual(before, after)
        self.assertIn("in progress", after)

    def test_a_changed_record_updates_the_evidence_block(self):
        before = P.evidence_markdown(self.records)
        changed = copy.deepcopy(self.records)
        changed["boot-test"]["result"] = "not-tested"
        after = P.evidence_markdown(changed)
        self.assertNotEqual(before, after)
        self.assertIn("**not-tested**", after)

    def test_an_old_record_is_stamped_with_its_own_commit(self):
        """It is a snapshot, never a statement about the current tree."""
        block = P.evidence_markdown(self.records)
        commit = self.records["check"]["commit"][:7]
        self.assertIn(f"`{commit}`", block)
        self.assertNotIn("current commit", block)
        self.assertIn("not evidence for the current one", block)

    def test_missing_evidence_never_becomes_verified(self):
        block = P.evidence_markdown({})
        self.assertIn("**no record**", block)
        self.assertNotIn("passed", block)
        self.assertIn("**NOT TESTED**", block)
        self.assertIn("**BLOCKED**", block)

    def test_the_physical_row_follows_the_filed_records(self):
        # no physical record filed in this repository, so the row says so
        self.assertIn("NOT TESTED", P.evidence_markdown(self.records))
        original = P.physical_records
        try:
            P.physical_records = lambda: [("2026-11-20-example.json", {"outcome": "booted",
                                                                       "date": "2026-11-20"})]
            block = P.evidence_markdown(self.records)
            self.assertIn("**booted**", block)
        finally:
            P.physical_records = original

    def test_every_block_states_the_numbers_the_data_holds(self):
        counts = P.distribution(self.data)
        block = P.stats_markdown(self.data, self.records)
        self.assertIn(f"| Complete | {counts['complete']} |", block)
        self.assertIn(f"| Blocked | {counts['blocked']} |", block)
        self.assertIn(f"| In progress | {counts['in_progress']} |", block)
        self.assertIn(f"| Not started | {counts['not_started']} |", block)

    def test_blocked_milestones_are_listed_with_their_blocker(self):
        block = P.blockers_markdown(self.data)
        for milestone in self.data["milestones"]:
            if milestone["state"] == "blocked":
                self.assertIn(f"`{milestone['id']}`", block)
                self.assertIn("| `", block)

    def test_the_pup_block_is_read_from_the_pointer(self):
        facts = P.pup_facts()
        block = P.pup_markdown()
        self.assertTrue(facts["pointer"])
        self.assertIn(f"{facts['bytes']} bytes", block)
        self.assertIn(facts["sha256"][:12], block)
        self.assertIn("UNVERIFIED", block)


class StrayStatisticTest(unittest.TestCase):
    """A number in the prose is a number that will be wrong in a month."""

    def test_a_typed_check_count_is_caught(self):
        found = P.stray_statistics("It passes 119 checks today.\n")
        self.assertTrue(found)

    def test_a_typed_milestone_count_is_caught(self):
        self.assertTrue(P.stray_statistics("35 milestones in 14 workstreams.\n"))

    def test_a_typed_byte_count_is_caught(self):
        self.assertTrue(P.stray_statistics("a 135-byte pointer\n"))
        self.assertTrue(P.stray_statistics("a 1.42 GB payload\n"))

    def test_numbers_inside_a_generated_block_are_fine(self):
        block = "<!-- pc5:stats:begin -->\n9 of 35 milestones\n<!-- pc5:stats:end -->"
        self.assertEqual(P.stray_statistics(block), [])

    def test_the_real_readme_has_no_stray_statistics(self):
        self.assertEqual(P.stray_statistics((REPO / "README.md").read_text()), [])


class ValidationRecordTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(REPO / "tools"))
        import validation
        self.validation = validation

    def test_committed_records_are_internally_consistent(self):
        self.assertEqual(self.validation.problems(), [])

    def test_a_record_cannot_claim_passed_without_exit_zero(self):
        bogus = [{"file": "x.json", "schema": "pc5.validation/1", "gate": "check",
                  "result": "passed", "exit_code": 2, "commit": "0" * 40,
                  "date": "2026-11-16"}]
        self.assertTrue(self.validation.problems(bogus))

    def test_a_failed_result_with_exit_zero_is_caught(self):
        bogus = [{"file": "x.json", "schema": "pc5.validation/1", "gate": "check",
                  "result": "failed", "exit_code": 0, "commit": "0" * 40,
                  "date": "2026-11-16"}]
        self.assertTrue(self.validation.problems(bogus))

    def test_an_uncommitted_or_malformed_sha_is_caught(self):
        bogus = [{"file": "x.json", "schema": "pc5.validation/1", "gate": "check",
                  "result": "failed", "exit_code": 1, "commit": "abc", "date": "2026-11-16"}]
        self.assertTrue(self.validation.problems(bogus))

    def test_a_missing_dependency_is_not_a_pass(self):
        self.assertEqual(self.validation.classify(
            "ovmf-test", 2, "NOT TESTED: no QEMU"), "not-tested")
        self.assertEqual(self.validation.classify("stubtest", 2, "install unicorn"), "skipped")
        self.assertEqual(self.validation.classify("boot-test", 77, "SKIP: no node"), "skipped")
        self.assertEqual(self.validation.classify("check", 1, "FAILED"), "failed")
        self.assertEqual(self.validation.classify("check", 0, "OK"), "passed")

    def test_test_counts_are_read_from_the_run_line(self):
        output = ("registry OK\nPASSED: 165 checks, 0 failures\n"
                  "Ran 501 tests in 51s\n\nOK (skipped=2)\n")
        self.assertEqual(self.validation.counts(output),
                         {"passed": 499, "total": 501, "skipped": 2})

    def test_the_summary_prefers_the_test_verdict(self):
        output = "registry OK\nPASSED: 165 checks, 0 failures\nRan 501 tests in 51s\n"
        self.assertEqual(self.validation.summarise(output), "Ran 501 tests in 51s")


class PresentationHygieneTest(unittest.TestCase):
    def test_no_screenshots_are_shipped_as_evidence(self):
        """There is no physical boot to photograph, so there are no photographs."""
        assets = sorted(path.name for path in (REPO / "docs/assets").iterdir())
        self.assertEqual(assets, sorted(P.FIGURES),
                         "docs/assets must contain exactly the generated figures")

    def test_the_build_targets_exist(self):
        makefile = (REPO / "Makefile").read_text(encoding="utf-8")
        self.assertIn("presentation:", makefile)
        self.assertIn("tools/presentation.py", makefile)

    def test_the_presentation_document_explains_the_generation(self):
        text = (REPO / "docs/PRESENTATION.md").read_text(encoding="utf-8")
        for token in ("make presentation", "roadmap.json", "docs/assets/pc5-banner.svg",
                      "pipeline definition"):
            self.assertIn(token, text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
