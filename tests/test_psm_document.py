"""The contract document must not drift from the registry or the doubles.

``psm.render`` regenerates the data tables between markers, and this file fails the
build when the document and the renderers disagree. Prose is checked for the sections
the contract requires; data is checked byte-for-byte against the renderers.

The remaining checks are about honesty rather than formatting: the document must not
cite a dead URL, must name the ADR it depends on, and must state plainly that no
genuine PS5 software has been executed.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from psm import render

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs/architecture/PS5_PLATFORM_CONTRACT.md"

REQUIRED_SECTIONS = (
    "## 1. What this document is, and what it is not",
    "## 2. Source discipline",
    "## 3. Subsystem contracts",
    "### 3.1 CPU and privileged execution",
    "### 3.2 Memory",
    "### 3.3 Interrupts, timers and system services",
    "### 3.4 Storage and I/O",
    "### 3.5 Graphics",
    "### 3.6 Audio and USB",
    "### 3.7 Security",
    "## 4. Status vocabulary",
    "## 5. Interfaces, doubles and the eight-group mapping",
    "## 6. Conformance plan",
    "## 7. What a passing suite proves",
    "## 8. Ownership, lifetime and determinism",
    "## 9. Dependencies on `M11`–`M17`",
    "## 10. Unresolved questions",
    "## 11. Corrections to the previous revision",
    "## 12. Change history",
    "## 13. Running it",
)


class DocumentBlocksTest(unittest.TestCase):
    def test_the_document_exists(self):
        self.assertTrue(DOC.exists(), DOC)

    def test_every_rendered_block_matches_its_renderer(self):
        problems = render.verify(DOC)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_document_has_the_sections_the_contract_requires(self):
        text = DOC.read_text()
        missing = [heading for heading in REQUIRED_SECTIONS if heading not in text]
        self.assertEqual(missing, [], f"missing sections: {missing}")

    def test_every_subsystem_has_its_claim_table(self):
        text = DOC.read_text()
        for subsystem in ("cpu", "memory", "interrupts", "storage", "graphics",
                          "audio", "usb", "services", "security"):
            self.assertIn(f"psm:begin claims-{subsystem}", text, subsystem)

    def test_no_block_marker_is_left_unbalanced(self):
        text = DOC.read_text()
        begins = re.findall(r"<!-- psm:begin ([A-Za-z0-9-]+) -->", text)
        ends = re.findall(r"<!-- psm:end ([A-Za-z0-9-]+) -->", text)
        self.assertEqual(sorted(begins), sorted(ends))
        self.assertEqual(len(begins), len(set(begins)), "a block appears twice")


class DocumentHonestyTest(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text()
        # Prose is wrapped, so phrases are matched across line breaks.
        self.flat = " ".join(self.text.split())

    def test_the_document_does_not_cite_the_dead_specs_page(self):
        self.assertNotIn("playstation.com/en-us/ps5/tech-specs", self.text)

    def test_the_document_states_that_no_sony_software_has_run(self):
        self.assertIn("Nothing in this repository has ever executed Sony's system software",
                      self.flat)

    def test_the_document_distinguishes_pc5_conformance_from_sony_acceptance(self):
        self.assertIn("does not prove that Sony's system software accepts", self.flat)

    def test_the_document_names_its_adrs(self):
        self.assertIn("ADR-019", self.text)
        self.assertIn("ADR-022", self.text)
        self.assertIn("ADR-024", self.text)
        decisions = (REPO / "docs/TECHNICAL_DECISIONS.md").read_text()
        self.assertIn("## ADR-024", decisions)

    def test_the_document_forbids_the_same_things_the_roadmap_forbids(self):
        for phrase in ("signature bypass", "key extraction", "secure-boot defeat",
                       "decryption of protected Sony firmware"):
            self.assertIn(phrase, self.flat)

    def test_the_document_does_not_claim_genuine_ps5_execution_evidence(self):
        forbidden = ("PHYSICAL-HARDWARE-TESTED", "genuine PS5 system software has been",
                     "we loaded Sony")
        for phrase in forbidden:
            self.assertNotIn(phrase, self.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
