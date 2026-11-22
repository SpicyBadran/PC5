"""The roadmap's dependency order, its foundations, and its version discipline.

Two questions are answered here, both of which the audit raised:

  * is the milestone order real?  Long mode, paging, the APIC and timers are
    prerequisites for PCIe MSI/MSI-X delivery and for NVMe, not consequences of
    them, so `M2` must depend on `M2a` and `M2b` on both;
  * which version is the product's?  `Makefile VERSION` is the only place a
    product version is defined; the roadmap file carries the revision of the
    roadmap document and says so in its field name.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH = os.path.join(ROOT, "docs", "architecture", "roadmap.json")
README = os.path.join(ROOT, "README.md")


def load_roadmap_module():
    path = os.path.join(ROOT, "tools", "roadmap.py")
    spec = importlib.util.spec_from_file_location("pc5_roadmap_deps", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


roadmap = load_roadmap_module()


def graph():
    with open(GRAPH, encoding="utf-8") as handle:
        return json.load(handle)


def by_id(data):
    return {milestone["id"]: milestone for milestone in data["milestones"]}


def reachable(data, start, wanted):
    """True when `wanted` can be reached from `start` through depends_on."""
    seen = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if current == wanted:
            return True
        stack.extend(by_id(data)[current]["depends_on"])
    return False


class DependencyOrderTests(unittest.TestCase):
    def test_the_cpu_and_long_mode_milestone_comes_before_pcie_and_nvme(self):
        data = graph()
        self.assertIn("M2a", by_id(data)["M2"]["depends_on"],
                      "M2 (PCIe/MSI-X/NVMe) cannot be attempted before the CPU "
                      "execution environment it delivers interrupts on")
        self.assertTrue(reachable(data, "M2", "M2a"))

    def test_long_mode_does_not_depend_on_the_driver_milestone(self):
        data = graph()
        deps = by_id(data)["M2a"]["depends_on"]
        self.assertNotIn("M2", deps)
        self.assertNotIn("M2b", deps)
        self.assertTrue(set(deps) <= {"M0", "M1"})

    def test_memory_management_depends_on_both(self):
        data = graph()
        deps = set(by_id(data)["M2b"]["depends_on"])
        self.assertEqual(deps, {"M2a", "M2"})

    def test_the_destination_still_depends_on_all_three(self):
        data = graph()
        self.assertTrue({"M2a", "M2", "M2b"} <= set(by_id(data)["M3"]["depends_on"]))

    def test_the_graph_is_still_acyclic_and_valid(self):
        data = graph()
        self.assertEqual(roadmap.validate(data, root=ROOT), [])
        for milestone in data["milestones"]:
            self.assertNotIn(milestone["id"], milestone["depends_on"],
                             f"{milestone['id']} depends on itself")


class FoundationTests(unittest.TestCase):
    def test_foundations_are_recorded_without_awarding_milestone_credit(self):
        data = graph()
        foundations = data["foundations"]
        self.assertTrue(foundations)
        for foundation in foundations:
            self.assertTrue(foundation["does_not_complete"],
                            "a foundation that completes everything is a milestone")
            for path in foundation["evidence"]:
                self.assertTrue(os.path.exists(os.path.join(ROOT, path)), path)

    def test_the_long_mode_work_is_a_foundation_not_a_completed_milestone(self):
        """The six levels of "done", measured, not asserted from prose.

        M2a's state used to be pinned to `not_started` because nothing on the boot
        path had run on real firmware.  That made the graph deny work that exists,
        which is its own kind of false report: the APIC, IDT, TSC and MADT code is
        in `kernel64/` and is tested on the host and under the emulated 64-bit stub
        firmware.  The rule that actually protects the reader is the ceiling, not the
        floor: while the OVMF record says `not-tested`, no 64-bit milestone may claim
        `complete`.
        """
        data = graph()
        self.assertIn("F64", {foundation["id"] for foundation in data["foundations"]})
        m2a = by_id(data)["M2a"]
        self.assertIn(m2a["state"], ("in_progress", "complete"))
        for path in m2a["evidence"]:
            self.assertTrue(os.path.exists(os.path.join(ROOT, path)),
                            f"M2a cites evidence that does not exist: {path}")

    def test_no_64bit_milestone_is_complete_while_ovmf_is_untested(self):
        ovmf = os.path.join(ROOT, "docs/evidence/validation/ovmf-test.json")
        if not os.path.exists(ovmf):
            self.skipTest("no OVMF validation record has been filed")
        with open(ovmf, encoding="utf-8") as handle:
            record = json.load(handle)
        if record.get("result") == "passed":
            self.skipTest("OVMF ran on this machine; the ceiling no longer applies")
        for milestone in graph()["milestones"]:
            if milestone["id"] in ("M2a", "M2b"):
                self.assertNotEqual(
                    milestone["state"], "complete",
                    f"{milestone['id']} is complete while the OVMF gate reports "
                    f"{record.get('result')!r}")

    def test_a_state_promotion_without_evidence_is_rejected(self):
        """Flipping M2a to `complete` with no evidence cited must fail the gate."""
        data = graph()
        broken = json.loads(json.dumps(data))
        for milestone in broken["milestones"]:
            if milestone["id"] == "M2a":
                milestone["state"] = "complete"
                milestone["evidence"] = []
        errors = roadmap.validate(broken, root=ROOT)
        self.assertTrue(any("complete but no evidence" in error for error in errors), errors)

    def test_a_cited_evidence_path_that_does_not_exist_is_rejected(self):
        data = graph()
        broken = json.loads(json.dumps(data))
        for milestone in broken["milestones"]:
            if milestone["id"] == "M2b":
                milestone["evidence"] = ["docs/evidence/no-such-record.md"]
        errors = roadmap.validate(broken, root=ROOT)
        self.assertTrue(any("evidence path does not exist" in error for error in errors),
                        errors)

    def test_a_foundation_without_a_completion_statement_is_rejected(self):
        data = graph()
        broken = json.loads(json.dumps(data))
        broken["foundations"][0]["does_not_complete"] = []
        errors = roadmap.validate(broken, root=ROOT)
        self.assertTrue(any("does not complete" in error for error in errors), errors)

    def test_a_foundation_pointing_at_missing_evidence_is_rejected(self):
        data = graph()
        broken = json.loads(json.dumps(data))
        broken["foundations"][0]["evidence"] = ["docs/does-not-exist.md"]
        errors = roadmap.validate(broken, root=ROOT)
        self.assertTrue(any("evidence does not exist" in error for error in errors), errors)


class VersionTests(unittest.TestCase):
    def makefile_version(self):
        text = pathlib.Path(ROOT, "Makefile").read_text(encoding="utf-8")
        match = re.search(r"^VERSION\s*[?:]?=\s*(\S+)\s*$", text, re.MULTILINE)
        self.assertIsNotNone(match, "Makefile has no VERSION")
        return match.group(1)

    def test_the_product_version_has_exactly_one_source(self):
        self.assertEqual(self.makefile_version(), "0.2.0")
        data = graph()
        self.assertNotIn("version", data,
                         "the roadmap must not publish a second 'version'")
        self.assertEqual(data["product_version_source"], "Makefile VERSION")
        self.assertTrue(data["document_version"])

    def test_the_readme_shows_the_makefile_version_and_says_where_it_comes_from(self):
        text = pathlib.Path(README).read_text(encoding="utf-8")
        version = self.makefile_version()
        self.assertIn(f"| Product version | `{version}`", text)
        self.assertIn("Makefile VERSION", text)
        self.assertIn("| Roadmap document |", text)

    def test_the_roadmap_version_is_not_presented_as_the_product_version(self):
        text = pathlib.Path(README).read_text(encoding="utf-8")
        data = graph()
        self.assertNotIn(f"| Roadmap | `{data['document_version']}`", text)

    def test_a_roadmap_with_an_ambiguous_version_is_rejected(self):
        data = graph()
        broken = json.loads(json.dumps(data))
        broken["version"] = "9.9.9"
        errors = roadmap.validate(broken, root=ROOT)
        self.assertTrue(any("document_version" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
