#!/usr/bin/env python3
"""Architecture tests: the milestone graph, the track gate and the documents.

These tests exist because the dual-track architecture makes promises that prose
cannot keep: that Track B never gates Track A, that a completed milestone has
evidence, and that the architecture documents do not point at files that do not
exist. Each promise is checked mechanically here and therefore in `make check`.

The graph fixtures are deliberately small and synthetic where a rule is being
tested, so a failure names the rule rather than a real milestone.
"""

from __future__ import annotations

import copy
import importlib.util
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCH = os.path.join(ROOT, "docs", "architecture")
GRAPH = os.path.join(ARCH, "roadmap.json")
DOC = os.path.join(ARCH, "M0_M8_DEPENDENCY_GRAPH.md")


def _load_roadmap_module():
    path = os.path.join(ROOT, "tools", "roadmap.py")
    spec = importlib.util.spec_from_file_location("pc5_roadmap", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


roadmap = _load_roadmap_module()


def base_graph():
    """A minimal graph that passes validation, for mutation in the tests."""
    return {
        "schema": "pc5.roadmap/1",
        "version": "test",
        "updated": "2026-11-15",
        "tracks": {
            "A": {"name": "Native", "objective": "os", "may_depend_on": ["A"]},
            "B": {"name": "Research", "objective": "research", "may_depend_on": ["A", "B"]},
        },
        "roles": ["lead-architect", "kernel-engineer"],
        "milestones": [
            {
                "id": "M0",
                "track": "A",
                "title": "root",
                "state": "complete",
                "owner_role": "lead-architect",
                "depends_on": [],
                "deliverables": ["something"],
                "acceptance": ["measurable"],
                "evidence": ["README.md"],
            },
            {
                "id": "M1",
                "track": "A",
                "title": "native",
                "state": "not_started",
                "owner_role": "kernel-engineer",
                "depends_on": ["M0"],
                "deliverables": ["something"],
                "acceptance": ["measurable"],
                "evidence": [],
            },
            {
                "id": "M2",
                "track": "B",
                "title": "research",
                "state": "not_started",
                "owner_role": "kernel-engineer",
                "depends_on": ["M0"],
                "deliverables": ["something"],
                "acceptance": ["measurable"],
                "evidence": [],
            },
        ],
    }


def milestone(graph, mid):
    for item in graph["milestones"]:
        if item["id"] == mid:
            return item
    raise AssertionError(f"no milestone {mid}")


class RealGraphTests(unittest.TestCase):
    def setUp(self):
        self.data = roadmap.load(GRAPH)

    def test_the_shipped_graph_is_valid(self):
        self.assertEqual(roadmap.validate(self.data), [])

    def test_document_matches_the_data(self):
        self.assertEqual(roadmap.doc_block(DOC), roadmap.render(self.data))

    def test_critical_path_runs_from_the_audit_to_the_release(self):
        path = roadmap.critical_path(self.data)
        self.assertEqual(path[0], "M0")
        self.assertEqual(path[-1], "M8")
        self.assertIn("M1", path)
        self.assertIn("M2", path)

    def test_every_track_b_milestone_can_start_without_track_a_deliveries(self):
        # Track B may depend on Track A, but the dependency must not run the
        # other way (checked globally too). Here: each B milestone's closure
        # contains no B-gating of an A milestone, and B milestones are reachable
        # from M0 alone.
        by_id = {m["id"]: m for m in self.data["milestones"]}
        b_ids = [mid for mid, m in by_id.items() if m["track"] == "B"]
        self.assertTrue(b_ids, "the graph should have Track B milestones")
        for mid in b_ids:
            closure = roadmap._closure(mid, by_id)
            self.assertNotIn("M8", closure)

    def test_states_are_honest_about_what_is_complete(self):
        by_id = {m["id"]: m for m in self.data["milestones"]}
        for mid, m in by_id.items():
            if m["state"] == "complete":
                self.assertTrue(m["evidence"], f"{mid} is complete without evidence")
                for path in m["evidence"]:
                    self.assertTrue(
                        os.path.exists(os.path.join(ROOT, path)),
                        f"{mid} cites a missing path: {path}",
                    )

    def test_m1_is_not_claimed_as_started(self):
        # No physical installation has ever happened; the graph must say so.
        # M1c became complete when genuine OVMF firmware booted the kernel
        # (docs/TEST_RESULTS.md §16), and its acceptance list still carries the
        # two things that are not done (physical hardware, and a GOP-only
        # framebuffer that has never run on a real machine).
        self.assertEqual(milestone(self.data, "M1")["state"], "not_started")
        self.assertEqual(milestone(self.data, "M1c")["state"], "complete")
        self.assertEqual(milestone(self.data, "M1b")["state"], "blocked")
        uefi = milestone(self.data, "M1c")
        unfinished = [entry for entry in uefi["acceptance"] if "NOT done" in entry]
        self.assertTrue(unfinished, "M1c must keep saying what is not done")



class TrackGateTests(unittest.TestCase):
    def test_direct_track_a_dependency_on_track_b_is_rejected(self):
        graph = base_graph()
        milestone(graph, "M1")["depends_on"] = ["M2"]
        errors = roadmap.validate(graph)
        self.assertTrue(
            any("track gate violated" in e and "M1" in e and "M2" in e for e in errors),
            errors,
        )

    def test_transitive_track_a_dependency_on_track_b_is_rejected(self):
        graph = base_graph()
        graph["milestones"].append(
            {
                "id": "M3",
                "track": "B",
                "title": "deeper research",
                "state": "not_started",
                "owner_role": "kernel-engineer",
                "depends_on": ["M2"],
                "deliverables": ["x"],
                "acceptance": ["y"],
                "evidence": [],
            }
        )
        milestone(graph, "M1")["depends_on"] = ["M0", "M3"]
        errors = roadmap.validate(graph)
        self.assertTrue(any("track gate violated" in e for e in errors), errors)

    def test_track_b_depending_on_track_a_is_allowed(self):
        graph = base_graph()
        milestone(graph, "M2")["depends_on"] = ["M0", "M1"]
        self.assertEqual(roadmap.validate(graph), [])


class ValidationRuleTests(unittest.TestCase):
    def assert_rejected(self, graph, fragment):
        errors = roadmap.validate(graph)
        self.assertTrue(
            any(fragment in e for e in errors),
            f"expected an error containing {fragment!r}, got {errors}",
        )

    def test_clean_fixture_passes(self):
        self.assertEqual(roadmap.validate(base_graph()), [])

    def test_duplicate_ids_are_rejected(self):
        graph = base_graph()
        graph["milestones"].append(copy.deepcopy(milestone(graph, "M1")))
        self.assert_rejected(graph, "duplicate id")

    def test_unknown_dependency_is_rejected(self):
        graph = base_graph()
        milestone(graph, "M1")["depends_on"] = ["M99"]
        self.assert_rejected(graph, "unknown milestone")

    def test_cycles_are_rejected(self):
        graph = base_graph()
        milestone(graph, "M0")["depends_on"] = ["M1"]
        self.assert_rejected(graph, "cycle")

    def test_self_dependency_is_rejected(self):
        graph = base_graph()
        milestone(graph, "M1")["depends_on"] = ["M1"]
        self.assert_rejected(graph, "depends on itself")

    def test_unknown_state_is_rejected(self):
        graph = base_graph()
        milestone(graph, "M1")["state"] = "mostly done"
        self.assert_rejected(graph, "state must be one of")

    def test_unknown_track_is_rejected(self):
        graph = base_graph()
        milestone(graph, "M1")["track"] = "C"
        self.assert_rejected(graph, "unknown track")

    def test_owner_must_be_a_declared_role(self):
        graph = base_graph()
        milestone(graph, "M1")["owner_role"] = "intern"
        self.assert_rejected(graph, "is not in roles")

    def test_blocked_milestone_must_name_its_blocker(self):
        graph = base_graph()
        milestone(graph, "M1")["state"] = "blocked"
        self.assert_rejected(graph, "state is blocked but no blocker")

    def test_complete_milestone_must_cite_evidence(self):
        graph = base_graph()
        milestone(graph, "M0")["evidence"] = []
        self.assert_rejected(graph, "complete but no evidence")

    def test_evidence_path_must_exist(self):
        graph = base_graph()
        milestone(graph, "M0")["evidence"] = ["docs/does-not-exist.md"]
        self.assert_rejected(graph, "evidence path does not exist")

    def test_acceptance_criteria_are_mandatory(self):
        graph = base_graph()
        milestone(graph, "M1")["acceptance"] = []
        self.assert_rejected(graph, "acceptance")

    def test_bad_id_is_rejected(self):
        graph = base_graph()
        milestone(graph, "M1")["id"] = "milestone-one"
        self.assert_rejected(graph, "id must match")

    def test_render_is_deterministic(self):
        graph = base_graph()
        self.assertEqual(roadmap.render(graph), roadmap.render(copy.deepcopy(graph)))
        self.assertIn("Track A critical path", roadmap.render(graph))


LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def markdown_links(path):
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    for target in LINK_RE.findall(text):
        target = target.strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        yield target


class DocumentationTests(unittest.TestCase):
    DELIVERABLES = {
        "REPOSITORY_AUDIT_2026-11-15.md": 100,
        "PC5_ARCHITECTURE_SPEC_v1.md": 100,
        "LINUX_VS_FREESTANDING.md": 80,
        "HARDWARE_STRATEGY.md": 80,
        "MIGRATION_PLAN.md": 80,
        "M0_M8_DEPENDENCY_GRAPH.md": 60,
        "RISK_REGISTER.md": 60,
        "FIRST_THREE_TASKS.md": 60,
    }

    def test_eight_deliverables_exist_and_are_substantive(self):
        for name, minimum in self.DELIVERABLES.items():
            path = os.path.join(ARCH, name)
            self.assertTrue(os.path.exists(path), f"missing deliverable: {name}")
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            self.assertGreaterEqual(len(lines), minimum, f"{name} is too thin to be a deliverable")

    def test_index_links_every_document_and_nothing_dangles(self):
        index = os.path.join(ARCH, "README.md")
        linked = {os.path.normpath(os.path.join(ARCH, t)) for t in markdown_links(index)}
        for name in self.DELIVERABLES:
            self.assertIn(os.path.normpath(os.path.join(ARCH, name)), linked, f"{name} not in index")
        self.assertIn(os.path.normpath(GRAPH), linked, "roadmap.json not in index")

    def test_relative_links_in_architecture_docs_resolve(self):
        for name in list(self.DELIVERABLES) + ["README.md"]:
            path = os.path.join(ARCH, name)
            for target in markdown_links(path):
                resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
                self.assertTrue(
                    os.path.exists(resolved),
                    f"{name}: link target does not exist: {target}",
                )

    def test_readme_links_to_the_architecture(self):
        with open(os.path.join(ROOT, "README.md"), "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("docs/architecture/", text)
        for target in markdown_links(os.path.join(ROOT, "README.md")):
            resolved = os.path.normpath(os.path.join(ROOT, target))
            self.assertTrue(os.path.exists(resolved), f"README link target missing: {target}")

    def test_adrs_are_unique_and_present(self):
        path = os.path.join(ROOT, "docs", "TECHNICAL_DECISIONS.md")
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        ids = re.findall(r"^## (ADR-\d+)", text, flags=re.MULTILINE)
        self.assertEqual(len(ids), len(set(ids)), "duplicate ADR ids")
        for expected in ("ADR-009", "ADR-010", "ADR-011", "ADR-012"):
            self.assertIn(expected, ids)
        for required in ("ADR-010", "ADR-012"):
            section = text.split(f"## {required}", 1)[1]
            self.assertGreater(len(section.splitlines()), 8, f"{required} is too thin")

    def test_spec_references_all_eight_deliverables(self):
        with open(os.path.join(ARCH, "PC5_ARCHITECTURE_SPEC_v1.md"), "r", encoding="utf-8") as fh:
            spec = fh.read()
        for name in self.DELIVERABLES:
            self.assertIn(name, spec, f"the spec does not account for {name}")

    def test_documents_do_not_claim_ps5_execution(self):
        # The architecture must keep the honest framing even as it formalises
        # Track B: a relinked binary is never "firmware execution".
        with open(os.path.join(ARCH, "PC5_ARCHITECTURE_SPEC_v1.md"), "r", encoding="utf-8") as fh:
            spec = " ".join(fh.read().split())
        self.assertIn("nothing here is called firmware execution", spec)
        self.assertIn("no public path exists from generic x86 hardware", spec)
        with open(os.path.join(ARCH, "README.md"), "r", encoding="utf-8") as fh:
            index = " ".join(fh.read().split())
        self.assertIn("Track B has executed nothing", index)


class BuildIntegrationTests(unittest.TestCase):
    def test_makefile_exposes_the_roadmap_check(self):
        with open(os.path.join(ROOT, "Makefile"), "r", encoding="utf-8") as fh:
            makefile = fh.read()
        self.assertIn("roadmap:", makefile)
        self.assertIn("tools/roadmap.py", makefile)

    def test_tool_runs_as_a_program(self):
        import subprocess

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "roadmap.py"), "check"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("document in sync", result.stdout)


if __name__ == "__main__":
    unittest.main()
