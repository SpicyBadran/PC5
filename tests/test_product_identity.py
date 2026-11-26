#!/usr/bin/env python3
"""Product identity tests: the destination cannot drift, and the tree stays clean.

`tests/test_architecture.py` tests the graph. This file tests the *story* the
repository tells about itself, and the artifact the mission rests on:

  * the product identity, mission and destination live in `roadmap.json`, and the
    documents are asserted against that data rather than against a sentence
    somebody liked in 2026;
  * forbidden claims (the product is a PlayStation lookalike, the substrate is
    the product, the old two-track rules as *current* architecture) may appear
    only in documents explicitly listed as historical — the list is data
    (`product.forbidden_claims`), not a habit;
  * the PS5 package is a Git LFS pointer whose payload is absent, and no Sony
    bytes are in the tree: this is the ADR-022 rule, checked.

Nothing here greps for one marketing sentence. The patterns come from
`roadmap.json`, so changing the identity means changing the data and the
documents together — which is exactly the review we want.
"""

from __future__ import annotations

import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH = os.path.join(ROOT, "docs", "architecture", "roadmap.json")

# Documents that must carry the product identity. Every user-facing entry point,
# plus the documents a new contributor reads first. `docs/ABOUT.md` is on this list
# because it is the page written *for* a newcomer, and the identity sentence it quotes
# is generated from this data — `tests/test_presentation.py::AboutPageTest` pins that.
IDENTITY_DOCUMENTS = (
    "README.md",
    "docs/ABOUT.md",
    "docs/ROADMAP.md",
    "docs/architecture/README.md",
    "docs/architecture/PC5_ARCHITECTURE_SPEC_v2.md",
    "docs/PS5_COMPATIBILITY_STATUS.md",
    "docs/PS5_SYSTEM_SOFTWARE_TARGET.md",
)

# Everything checked for forbidden claims (relative to the repository root).
SCANNED_SUFFIXES = (".md",)
SCANNED_TREES = ("docs", "boot", "kernel", "tools", "tests", "ci")


def normalise(text):
    """Lowercase, drop markdown emphasis and collapse whitespace."""
    return " ".join(text.replace("*", "").replace("`", "").lower().split())


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def roadmap():
    with open(GRAPH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def scanned_files():
    files = ["README.md"]
    for tree in SCANNED_TREES:
        for base, dirs, names in os.walk(os.path.join(ROOT, tree)):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__")]
            for name in sorted(names):
                if name.lower().endswith(SCANNED_SUFFIXES):
                    files.append(os.path.relpath(os.path.join(base, name), ROOT))
    return files


class IdentityTests(unittest.TestCase):
    """The destination is data, and the documents have to agree with it."""

    def setUp(self):
        self.data = roadmap()
        self.product = self.data["product"]

    def test_the_identity_is_what_the_mission_says(self):
        identity = self.product["identity"]
        mission = self.product["mission"].lower()
        self.assertIn("PlayStation 5", identity)
        self.assertIn("PC hardware", identity)
        self.assertIn("genuine", mission)
        self.assertIn("system software", mission)
        # The wrong product, stated as a non-goal rather than left implicit.
        non_goals = " ".join(self.product["non_goals"]).lower()
        self.assertIn("linux distribution", non_goals)
        self.assertIn("imitation", non_goals)

    def test_pc5_is_not_described_as_the_operating_system(self):
        """The mission's central distinction must survive an edit.

        Genuine Sony PS5 system software is the operating system; PC5 is the
        PC-side boot, hardware and compatibility infrastructure.  An earlier
        identity line called PC5 itself "an operating-system project", which
        reads as a claim to build a replacement OS -- the one thing the mission
        rules out.  The words are asserted here so a future revision cannot
        quietly reintroduce them.
        """
        identity = self.product["identity"].lower()
        note = self.product["identity_note"].lower()
        self.assertNotIn("operating-system project", identity)
        self.assertNotIn("operating system project", identity)
        # And the note has to say which is which, not merely be non-empty.
        self.assertIn("infrastructure", note)
        self.assertIn("operating system", note)
        self.assertIn("sony", note)
        # The non-goal that names the wrong product must be present as data.
        non_goals = " ".join(self.product["non_goals"]).lower()
        self.assertIn("imitation of the ps5 interface", non_goals)

    def test_the_foundation_is_not_the_product_and_not_required(self):
        foundation = self.product["foundation"]
        self.assertEqual(foundation["role"], "hardware-enablement")
        self.assertIs(foundation["required_for_product"], False)
        self.assertIn("PC5 platform", foundation["product_surface"])
        self.assertNotIn("linux", foundation["product_surface"].lower())

    def test_every_identity_document_states_the_destination(self):
        # The canonical identity line, from the data, must appear in each
        # entry-point document. It is assembled here, not typed twice.
        needle = normalise(self.product["identity"]).rstrip(".")
        for name in IDENTITY_DOCUMENTS:
            path = os.path.join(ROOT, name)
            self.assertTrue(os.path.exists(path), f"missing identity document: {name}")
            text = normalise(read(path))
            self.assertIn(
                needle, text,
                f"{name} does not state the product identity: {needle!r}",
            )

    def test_every_identity_document_links_the_architecture_authority(self):
        authority = self.data["authority"]
        basename = os.path.basename(authority)
        for name in IDENTITY_DOCUMENTS:
            if os.path.normpath(name) == os.path.normpath(authority):
                continue  # the authority is itself
            text = read(os.path.join(ROOT, name))
            self.assertIn(basename, text, f"{name} does not point at {authority}")

    def test_the_destination_is_not_what_the_foundation_provides(self):
        dest = self.product["destination"]
        self.assertIn("genuine", " ".join([dest["statement"]]).lower())
        self.assertIn("PlayStation 5", dest["statement"])
        for wrong in self.product["foundation"]["name"], "Linux":
            self.assertNotIn(wrong, dest["statement"])


class ForbiddenClaimTests(unittest.TestCase):
    """Patterns come from the data; the exemption list is data too."""

    def setUp(self):
        self.claims = roadmap()["product"]["forbidden_claims"]
        self.files = scanned_files()

    def test_every_forbidden_claim_has_a_reason_and_an_exemption_list(self):
        self.assertTrue(self.claims)
        for claim in self.claims:
            self.assertTrue(claim["pattern"].strip())
            self.assertTrue(claim["why"].strip(), claim["pattern"])
            self.assertIsInstance(claim["allowed_in"], list)

    def test_current_documents_do_not_carry_forbidden_claims(self):
        problems = []
        for claim in self.claims:
            pattern = claim["pattern"]
            allowed = {os.path.normpath(path) for path in claim["allowed_in"]}
            for name in self.files:
                if os.path.normpath(name) in allowed:
                    continue
                text = read(os.path.join(ROOT, name))
                for number, line in enumerate(text.splitlines(), 1):
                    if pattern in line:
                        problems.append(f"{name}:{number}: {pattern!r} ({claim['why']})")
        self.assertEqual(problems, [], "forbidden claim in a current document:\n" + "\n".join(problems))

    def test_the_exemption_list_only_names_historical_documents(self):
        # An exemption is a claim that a file is history. It must say so, or the
        # exemption becomes a hole anyone can widen.
        markers = ("SUPERSEDED", "superseded", "Historical", "historical", "withdrawn", "former")
        for claim in self.claims:
            for name in claim["allowed_in"]:
                path = os.path.join(ROOT, name)
                if not os.path.exists(path):
                    continue  # a file may have been removed; the entry is then inert
                text = read(path)
                self.assertTrue(
                    any(marker in text for marker in markers)
                    or name.endswith("ARCHITECTURE_DISCREPANCY_REPORT.md")
                    or name.endswith("TECHNICAL_DECISIONS.md"),
                    f"{name} is exempt from {claim['pattern']!r} but does not say it is historical",
                )

    def test_the_identity_documents_are_outside_every_exemption(self):
        allowed = set()
        for claim in self.claims:
            allowed |= {os.path.normpath(path) for path in claim["allowed_in"]}
        for name in IDENTITY_DOCUMENTS:
            self.assertNotIn(os.path.normpath(name), allowed, f"{name} is a current document")


class Ps5PackageTests(unittest.TestCase):
    """ADR-022: the package is a protected input, and the tree holds no Sony bytes."""

    POINTER = os.path.join(ROOT, "PS5UPDATE.PUP")
    OID = "42ea0e5aad1361e098837d40157863c4c519f0bbc66eb03c04dbaaf339b063fb"
    SIZE = 1423697408

    def test_the_file_in_the_tree_is_a_pointer_not_a_payload(self):
        self.assertTrue(os.path.exists(self.POINTER), "the package pointer is part of the record")
        raw = open(self.POINTER, "rb").read()
        self.assertLess(len(raw), 1024, "a real payload is ~1.33 GiB; this must be a pointer")
        text = raw.decode("ascii")
        self.assertIn("https://git-lfs.github.com/spec/v1", text)
        self.assertIn(f"oid sha256:{self.OID}", text)
        self.assertIn(f"size {self.SIZE}", text)

    def test_the_declared_size_is_recorded_in_the_documents(self):
        # The record and the pointer must agree; a mismatch means one of them
        # was edited without the other.
        text = read(os.path.join(ROOT, "docs", "PS5_SYSTEM_SOFTWARE_TARGET.md"))
        self.assertIn(self.OID, text)
        self.assertIn("1,423,697,408", text)
        self.assertIn("NOT VERIFIED", text)

    def test_nothing_in_the_tree_is_a_sony_binary(self):
        suspicious = []
        for tree in ("docs", "boot", "kernel", "tools", "tests", "ci"):
            for base, dirs, names in os.walk(os.path.join(ROOT, tree)):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in names:
                    path = os.path.join(base, name)
                    rel = os.path.relpath(path, ROOT)
                    if name.lower().endswith((".pup", ".self", ".prx", ".pkg", ".sprx")):
                        suspicious.append(rel)
                    if os.path.getsize(path) > 8 * 1024 * 1024:
                        suspicious.append(f"{rel} (large)")
        self.assertEqual(suspicious, [], f"proprietary or oversized artefacts in the tree: {suspicious}")

    def test_the_rules_of_engagement_are_written_down(self):
        decisions = read(os.path.join(ROOT, "docs", "TECHNICAL_DECISIONS.md"))
        self.assertIn("## ADR-022", decisions)
        section = decisions.split("## ADR-022", 1)[1]
        for rule in ("no decryption", "no key handling", "committed as bytes"):
            self.assertIn(rule, section.lower(), f"ADR-022 does not state {rule!r}")


class MissionCoverageTests(unittest.TestCase):
    """The destination must be present in the data, not only in prose."""

    def setUp(self):
        self.data = roadmap()
        self.by_id = {m["id"]: m for m in self.data["milestones"]}

    def test_a_workstream_exists_for_ps5_platform_compatibility(self):
        names = {ws["id"]: ws["name"].lower() for ws in self.data["workstreams"]}
        self.assertIn("WS10", names)
        self.assertIn("ps5", names["WS10"])

    def test_the_destination_workstream_holds_the_destination(self):
        dest = self.data["product"]["destination"]["milestone"]
        self.assertEqual(self.by_id[dest]["workstream"], "WS10")

    def test_the_levels_are_complete_and_level_five_is_not_satisfied_by_a_model(self):
        levels = {level["level"]: level for level in self.data["product"]["levels"]}
        self.assertEqual(sorted(levels), [1, 2, 3, 4, 5, 6])
        five = levels[5]["evidence_class"].lower()
        self.assertIn("itself", five)
        self.assertIn("never", five)
        for wrong in ("kernel booting", "simulated", "interface"):
            self.assertIn(wrong, five)

    def test_no_milestone_claims_a_level_above_its_evidence(self):
        for m in self.data["milestones"]:
            if m["state"] != "complete":
                continue
            self.assertLessEqual(
                m["level"], 2,
                f"{m['id']} is complete at level {m['level']}: only levels 1-2 have evidence",
            )

    def test_the_game_execution_workstream_is_represented(self):
        kinds = {m["kind"] for m in self.data["milestones"]}
        self.assertIn("ps5-compatibility", kinds)
        self.assertIn("research", kinds)
        self.assertTrue(any(m["workstream"] == "WS11" for m in self.data["milestones"]))


if __name__ == "__main__":
    unittest.main()
