#!/usr/bin/env python3
"""Architecture tests: the milestone graph, release isolation and the documents.

These tests exist because the architecture makes promises that prose cannot keep:
that the destination is genuine PS5 system software and it is on the critical
path, that a workstream cannot quietly vanish, that an experiment cannot gate a
release, that a completed milestone has evidence, and that the documents point at
files that exist. Each promise is checked mechanically here and therefore in
`make check`.

The graph fixtures are deliberately small and synthetic where a rule is being
tested, so a failure names the rule rather than a real milestone.
"""

from __future__ import annotations

import copy
import importlib.util
import os
import re
import urllib.parse
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCH = os.path.join(ROOT, "docs", "architecture")
GRAPH = os.path.join(ARCH, "roadmap.json")
DOC = os.path.join(ARCH, "PC5_MILESTONE_GRAPH.md")


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
        "schema": "pc5.roadmap/2",
        "document_version": "test",
        "product_version_source": "Makefile VERSION",
        "updated": "2026-11-15",
        "authority": "docs/architecture/PC5_ARCHITECTURE_SPEC_v2.md",
        "product": {
            "name": "PC5",
            "identity": "A native PlayStation 5 operating system for PC hardware.",
            "mission": "run the genuine Sony PS5 system software on compatible PC hardware",
            "destination": {
                "statement": "genuine system software executing on PC hardware",
                "milestone": "M2",
                "level": 2,
                "evidence_class": "execution evidence from the software itself",
                "must_not_be_satisfied_by": ["a PC5 kernel booting"],
            },
            "foundation": {
                "role": "hardware-enablement",
                "name": "test substrate",
                "adr": "ADR-020",
                "product_surface": "the PC5 platform",
                "required_for_product": False,
                "exit_milestone": "M1",
            },
            "non_goals": ["a Linux distribution with a PlayStation theme"],
            "forbidden_claims": [
                {"pattern": "PlayStation-inspired", "why": "wrong product", "allowed_in": []},
            ],
            "required_milestones": ["M2"],
            "levels": [
                {"level": 1, "statement": "boots", "acceptance": "boot log", "evidence_class": "log"},
                {"level": 2, "statement": "genuine", "acceptance": "its own output", "evidence_class": "trace"},
            ],
        },
        "workstreams": [
            {"id": "WS0", "name": "governance", "purpose": "keep it honest", "serves_level": "all"},
            {"id": "WS1", "name": "ps5", "purpose": "the destination", "serves_level": "2"},
        ],
        "roles": ["lead-architect", "kernel-engineer"],
        "milestones": [
            {
                "id": "M0", "workstream": "WS0", "kind": "governance", "level": 1,
                "title": "root", "state": "complete", "owner_role": "lead-architect",
                "depends_on": [], "advances": "everything", "release_gating": True,
                "deliverables": ["something"], "acceptance": ["measurable"],
                "evidence": ["README.md"],
            },
            {
                "id": "M1", "workstream": "WS0", "kind": "native-platform", "level": 1,
                "title": "release", "state": "not_started", "owner_role": "kernel-engineer",
                "depends_on": ["M0"], "advances": "level 1", "release_gating": True,
                "deliverables": ["something"], "acceptance": ["measurable"], "evidence": [],
            },
            {
                "id": "M2", "workstream": "WS1", "kind": "ps5-compatibility", "level": 2,
                "title": "destination", "state": "blocked", "owner_role": "kernel-engineer",
                "depends_on": ["M0"], "advances": "the destination",
                "blocker": "external precondition", "experimental": True,
                "deliverables": ["something"], "acceptance": ["measurable"], "evidence": [],
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

    def test_the_product_identity_is_the_mission(self):
        product = self.data["product"]
        self.assertIn("PlayStation 5", product["identity"])
        self.assertIn("PC hardware", product["identity"])
        self.assertIn("genuine", product["mission"].lower())
        self.assertEqual(product["foundation"]["role"], "hardware-enablement")
        self.assertIs(product["foundation"]["required_for_product"], False)

    def test_the_destination_is_genuine_execution_not_a_lookalike(self):
        dest = self.data["product"]["destination"]
        self.assertEqual(dest["level"], 5)
        target = milestone(self.data, dest["milestone"])
        self.assertEqual(target["level"], 5)
        self.assertNotEqual(target["state"], "complete", "level 5 is blocked; it must not be claimed")
        self.assertTrue(target.get("blocker"))
        # What the destination may never be satisfied by is stated as data.
        forbidden = " ".join(dest["must_not_be_satisfied_by"]).lower()
        for phrase in ("kernel booting", "simulated", "interface"):
            self.assertIn(phrase, forbidden)

    def test_the_destination_is_on_the_critical_path(self):
        path = roadmap.destination_path(self.data)
        self.assertEqual(path[0], "M0")
        self.assertEqual(path[-1], self.data["product"]["destination"]["milestone"])
        # The destination must lie on the longest chain in the graph, not on a
        # side branch: this is the property the old two-track graph inverted.
        longest = roadmap.critical_path(self.data)
        self.assertIn(
            self.data["product"]["destination"]["milestone"], longest,
            f"the destination is not on the critical path ({longest})",
        )
        # And it must run through the PS5 workstream, not around it.
        workstreams = {m["id"]: m["workstream"] for m in self.data["milestones"]}
        self.assertIn("WS10", [workstreams[mid] for mid in path])

    def test_the_destination_workstream_cannot_be_deleted(self):
        required = self.data["product"]["required_milestones"]
        by_id = {m["id"]: m for m in self.data["milestones"]}
        self.assertTrue(required)
        for mid in required:
            self.assertIn(mid, by_id, f"required milestone {mid} is missing")
        # Every required milestone is destination work: either in WS10 or a
        # PS5-facing installation milestone that depends on WS10.
        for mid in required:
            self.assertIn(
                by_id[mid]["kind"], ("ps5-compatibility", "research"),
                f"{mid} is required but is not destination work",
            )

    def test_level_five_is_blocked_and_says_why(self):
        by_id = {m["id"]: m for m in self.data["milestones"]}
        level5 = [m for m in by_id.values() if m["level"] == 5]
        self.assertTrue(level5, "the graph must contain level-5 milestones")
        for m in level5:
            self.assertEqual(m["state"], "blocked", f"{m['id']} claims level 5 without evidence")
            self.assertTrue(m.get("blocker"), f"{m['id']} is blocked without a named blocker")

    def test_no_ps5_milestone_is_complete_without_execution_evidence(self):
        for m in self.data["milestones"]:
            if m["kind"] == "ps5-compatibility" and m["state"] == "complete":
                self.assertTrue(m["evidence"], f"{m['id']} claims completion without evidence")

    def test_states_are_honest_about_what_is_complete(self):
        for m in self.data["milestones"]:
            if m["state"] == "complete":
                self.assertTrue(m["evidence"], f"{m['id']} is complete without evidence")
                for path in m["evidence"]:
                    self.assertTrue(
                        os.path.exists(os.path.join(ROOT, path)),
                        f"{m['id']} cites a missing path: {path}",
                    )

    def test_the_completed_milestones_are_the_ones_that_were_measured(self):
        # Nothing that had evidence before the realignment lost its state.
        for mid in ("M0", "M1a", "M1c", "M1d", "M1e", "M1f", "M1g", "M1h", "M1i"):
            self.assertEqual(milestone(self.data, mid)["state"], "complete", mid)
        # And nothing that was never measured gained one.
        self.assertEqual(milestone(self.data, "M1")["state"], "not_started")
        self.assertEqual(milestone(self.data, "M3")["state"], "not_started")
        self.assertEqual(milestone(self.data, "M1b")["state"], "blocked")

    def test_the_uefi_milestone_keeps_saying_what_is_not_done(self):
        uefi = milestone(self.data, "M1c")
        unfinished = [entry for entry in uefi["acceptance"] if "NOT done" in entry]
        self.assertTrue(unfinished, "M1c must keep saying what is not done")

    def test_every_workstream_owns_a_milestone(self):
        owners = {m["workstream"] for m in self.data["milestones"]}
        for ws in self.data["workstreams"]:
            self.assertIn(ws["id"], owners, f"{ws['id']} exists as a heading with no work")

    def test_every_milestone_serves_the_mission(self):
        for m in self.data["milestones"]:
            self.assertTrue(m.get("advances", "").strip(), f"{m['id']} does not say what it advances")

    def test_superseded_milestones_point_at_real_milestones(self):
        by_id = {m["id"]: m for m in self.data["milestones"]}
        m6 = by_id["M6"]
        self.assertTrue(m6.get("superseded_by"))
        for mid in m6["superseded_by"]:
            self.assertIn(mid, by_id)


class ReleaseIsolationTests(unittest.TestCase):
    """ADR-021: an experiment may not gate a release, the destination may."""

    def test_an_experimental_dependency_of_a_release_is_rejected(self):
        graph = base_graph()
        milestone(graph, "M1")["depends_on"] = ["M0", "M2"]
        errors = roadmap.validate(graph)
        self.assertTrue(
            any("release isolation violated" in e and "M1" in e and "M2" in e for e in errors),
            errors,
        )

    def test_transitive_experimental_dependency_is_rejected(self):
        graph = base_graph()
        graph["milestones"].append({
            "id": "M3", "workstream": "WS1", "kind": "ps5-compatibility", "level": 2,
            "title": "deeper model", "state": "not_started", "owner_role": "kernel-engineer",
            "depends_on": ["M2"], "advances": "the destination", "experimental": True,
            "deliverables": ["x"], "acceptance": ["y"], "evidence": [],
        })
        milestone(graph, "M1")["depends_on"] = ["M0", "M3"]
        errors = roadmap.validate(graph)
        self.assertTrue(any("release isolation violated" in e for e in errors), errors)

    def test_the_destination_may_depend_on_experiments(self):
        # The whole point of the realignment: the mission is allowed to be a
        # dependency of the product, it is experiments gating *releases* that is
        # forbidden.
        graph = base_graph()
        milestone(graph, "M2")["depends_on"] = ["M0", "M1"]
        self.assertEqual(roadmap.validate(graph), [])

    def test_the_shipped_graph_satisfies_release_isolation(self):
        data = roadmap.load(GRAPH)
        by_id = {m["id"]: m for m in data["milestones"]}
        for m in by_id.values():
            if not (m.get("release_gating") and m.get("kind") in roadmap.RELEASE_KINDS):
                continue
            closure = roadmap._closure(m["id"], by_id)
            for dep in closure:
                self.assertFalse(
                    by_id[dep].get("experimental"),
                    f"release-gating {m['id']} depends on experimental {dep}",
                )


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

    def test_unknown_workstream_is_rejected(self):
        graph = base_graph()
        milestone(graph, "M1")["workstream"] = "WS99"
        self.assert_rejected(graph, "unknown workstream")

    def test_unknown_kind_is_rejected(self):
        graph = base_graph()
        milestone(graph, "M1")["kind"] = "vibes"
        self.assert_rejected(graph, "kind must be one of")

    def test_missing_mission_link_is_rejected(self):
        graph = base_graph()
        milestone(graph, "M1")["advances"] = "  "
        self.assert_rejected(graph, "advances")

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

    def test_missing_destination_is_rejected(self):
        graph = base_graph()
        graph["product"]["destination"]["milestone"] = "M99"
        self.assert_rejected(graph, "does not exist in the graph")

    def test_destination_level_must_match_its_milestone(self):
        graph = base_graph()
        graph["product"]["destination"]["level"] = 1
        self.assert_rejected(graph, "is level 2")

    def test_removing_a_required_milestone_is_rejected(self):
        graph = base_graph()
        graph["product"]["required_milestones"] = ["M7"]
        self.assert_rejected(graph, "required milestone 'M7' is missing")

    def test_a_workstream_without_work_is_rejected(self):
        graph = base_graph()
        graph["workstreams"].append(
            {"id": "WS2", "name": "orphan", "purpose": "nothing", "serves_level": "1"})
        self.assert_rejected(graph, "has no milestone")

    def test_the_substrate_may_not_be_declared_the_product(self):
        graph = base_graph()
        graph["product"]["foundation"]["role"] = "product"
        self.assert_rejected(graph, "must not make the foundation the product")

    def test_the_substrate_may_not_be_required_for_the_product(self):
        graph = base_graph()
        graph["product"]["foundation"]["required_for_product"] = True
        self.assert_rejected(graph, "required_for_product must be false")

    def test_a_linux_distribution_identity_is_rejected(self):
        graph = base_graph()
        graph["product"]["identity"] = "A Linux distribution with a PlayStation theme."
        self.assert_rejected(graph, "describes a Linux distribution")

    def test_foundation_adr_must_be_recorded(self):
        graph = base_graph()
        graph["product"]["foundation"]["adr"] = "ADR-999"
        self.assert_rejected(graph, "is not recorded in docs/TECHNICAL_DECISIONS.md")

    def test_render_is_deterministic(self):
        graph = base_graph()
        self.assertEqual(roadmap.render(graph), roadmap.render(copy.deepcopy(graph)))
        self.assertIn("DESTINATION PATH", roadmap.render(graph))


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
        # GitHub resolves `[x](a%20b.md)` to the file `a b.md`; so must this.
        yield urllib.parse.unquote(target)


class DocumentationTests(unittest.TestCase):
    DELIVERABLES = {
        "ARCHITECTURE_DISCREPANCY_REPORT.md": 100,
        "PC5_ARCHITECTURE_SPEC_v2.md": 150,
        "PS5_PLATFORM_CONTRACT.md": 80,
        "PC5_MILESTONE_GRAPH.md": 40,
        "REPOSITORY_AUDIT_2026-11-15.md": 100,
        "LINUX_VS_FREESTANDING.md": 80,
        "HARDWARE_STRATEGY.md": 80,
        "MIGRATION_PLAN.md": 80,
        "RISK_REGISTER.md": 60,
        "FIRST_THREE_TASKS.md": 60,
    }

    def test_deliverables_exist_and_are_substantive(self):
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
        for expected in ("ADR-009", "ADR-010", "ADR-019", "ADR-020", "ADR-021", "ADR-022"):
            self.assertIn(expected, ids)
        for required in ("ADR-019", "ADR-020", "ADR-021", "ADR-022"):
            section = text.split(f"## {required}", 1)[1]
            self.assertGreater(len(section.splitlines()), 12, f"{required} is too thin")

    def test_superseded_decisions_say_so(self):
        with open(os.path.join(ROOT, "docs", "TECHNICAL_DECISIONS.md"), "r", encoding="utf-8") as fh:
            text = fh.read()
        for adr, successor in (("ADR-009", "ADR-020"), ("ADR-010", "ADR-021")):
            section = text.split(f"## {adr}", 1)[1].split("\n## ", 1)[0]
            self.assertIn("superseded", section.lower(), f"{adr} does not say it was superseded")
            self.assertIn(successor, section, f"{adr} does not name {successor}")

    def test_spec_v2_references_the_deliverables(self):
        with open(os.path.join(ARCH, "PC5_ARCHITECTURE_SPEC_v2.md"), "r", encoding="utf-8") as fh:
            spec = fh.read()
        for name in ("ARCHITECTURE_DISCREPANCY_REPORT.md", "PS5_PLATFORM_CONTRACT.md",
                     "PC5_MILESTONE_GRAPH.md"):
            self.assertIn(name, spec, f"the spec does not account for {name}")
        self.assertIn("PS5_SYSTEM_SOFTWARE_TARGET.md", spec)

    def test_the_old_spec_is_marked_superseded_and_kept(self):
        path = os.path.join(ARCH, "PC5_ARCHITECTURE_SPEC_v1.md")
        self.assertTrue(os.path.exists(path), "history must be kept, not deleted")
        with open(path, "r", encoding="utf-8") as fh:
            head = fh.read(1200)
        self.assertIn("SUPERSEDED", head)
        self.assertIn("PC5_ARCHITECTURE_SPEC_v2.md", head)

    def test_documents_do_not_claim_ps5_execution(self):
        with open(os.path.join(ARCH, "PC5_ARCHITECTURE_SPEC_v2.md"), "r", encoding="utf-8") as fh:
            spec = " ".join(fh.read().split())
        self.assertIn("nothing here is called firmware execution", spec)
        self.assertIn("does not claim that genuine PS5 system software has been executed", spec)


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
        self.assertIn("destination", result.stdout)


if __name__ == "__main__":
    unittest.main()
