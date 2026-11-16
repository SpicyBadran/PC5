"""The registry's own rules, and the linkage audit that joins registry to code.

These tests do not check that PC5 is correct about the console — nothing can. They
check that the contract cannot quietly become wrong: a claim without a source, an
`UNKNOWN` row carrying a value, a `PUBLIC` claim resting on a snippet, an operation
citing a claim that does not exist, or an implemented operation citing a
`RESTRICTED` claim all fail here.

The rule engine is also tested against deliberately damaged copies of the registry, so
a rule that stops working is itself caught.
"""

from __future__ import annotations

import dataclasses
import unittest
from unittest import mock

from psm import audit, registry
from psm.contract import CLASSIFICATIONS, SUBSYSTEMS, Claim, Contract, Source


def _subsystem_contract(**changes) -> Contract:
    fields = dict(sources=registry.SOURCES, claims=registry.CLAIMS,
                  revision=registry.CONTRACT.revision)
    fields.update(changes)
    return Contract(**fields)


class RegistryRulesTest(unittest.TestCase):
    """What the shipped registry must satisfy."""

    def test_registry_has_no_problems(self):
        problems = registry.CONTRACT.problems()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_registry_is_not_empty(self):
        self.assertGreaterEqual(len(registry.CONTRACT.sources), 4)
        self.assertGreaterEqual(len(registry.CONTRACT.claims), 38)
        self.assertGreaterEqual(len(registry.DECISIONS), 14)

    def test_every_classification_is_in_the_vocabulary(self):
        for claim in registry.CONTRACT.claims:
            self.assertIn(claim.classification, CLASSIFICATIONS, claim.id)

    def test_every_claim_names_an_engineering_decision_and_conformance_case(self):
        for claim in registry.CONTRACT.claims:
            self.assertTrue(claim.decision.strip(), claim.id)
            self.assertTrue(claim.conformance.strip(), claim.id)

    def test_no_unknown_or_restricted_claim_carries_a_value(self):
        for claim in registry.CONTRACT.claims:
            if claim.classification in ("UNKNOWN", "RESTRICTED"):
                self.assertIsNone(claim.value, f"{claim.id} carries a value it cannot have")

    def test_public_claims_cite_a_verified_or_located_source(self):
        for claim in registry.CONTRACT.claims:
            if claim.classification != "PUBLIC":
                continue
            usable = [registry.CONTRACT.source(s) for s in claim.sources]
            self.assertTrue(
                any(source and source.verification in ("verified", "located")
                    for source in usable),
                f"{claim.id} is PUBLIC without a usable source")

    def test_sources_are_https_and_never_the_dead_specs_page(self):
        for source in registry.CONTRACT.sources:
            self.assertTrue(source.url.startswith("https://"), source.id)
            self.assertNotIn("playstation.com/en-us/ps5/tech-specs", source.url)

    def test_every_subsystem_has_at_least_one_claim(self):
        for subsystem in SUBSYSTEMS:
            self.assertTrue(registry.CONTRACT.by_subsystem(subsystem), subsystem)

    def test_decisions_are_not_claims(self):
        """A PC5 decision must never be presented as evidence about the console."""
        for decision in registry.DECISIONS:
            self.assertNotIn(decision["id"], {c.id for c in registry.CONTRACT.claims})
            self.assertTrue(decision["decision"].strip(), decision["id"])
            self.assertTrue(decision["rationale"].strip(), decision["id"])

    def test_security_is_restricted_and_has_no_interface(self):
        security = registry.CONTRACT.by_subsystem("security")
        self.assertTrue(security)
        self.assertTrue(all(c.classification in ("RESTRICTED", "UNKNOWN") for c in security))
        self.assertNotIn("security", {iface.info.subsystem for iface in audit.interfaces().values()})


class RuleEngineTest(unittest.TestCase):
    """A rule that cannot fail is not a rule."""

    def _claim(self, claim_id: str) -> Claim:
        claim = registry.CONTRACT.claim(claim_id)
        self.assertIsNotNone(claim)
        return claim

    def _with_replaced(self, claim: Claim) -> Contract:
        claims = tuple(c for c in registry.CLAIMS if c.id != claim.id) + (claim,)
        return _subsystem_contract(claims=claims)

    def test_public_claim_without_a_source_is_rejected(self):
        broken = dataclasses.replace(self._claim("C-CPU-01"), sources=())
        self.assertTrue(any("PUBLIC without a verified or located source" in problem
                            for problem in self._with_replaced(broken).problems()))

    def test_public_claim_without_a_value_is_rejected(self):
        broken = dataclasses.replace(self._claim("C-CPU-01"), value=None)
        self.assertTrue(any("PUBLIC without a value" in problem
                            for problem in self._with_replaced(broken).problems()))

    def test_inferred_claim_without_an_assumption_is_rejected(self):
        broken = dataclasses.replace(self._claim("C-MEM-02"), establishes="")
        self.assertTrue(any("INFERRED without saying what it assumes" in problem
                            for problem in self._with_replaced(broken).problems()))

    def test_unknown_claim_with_a_value_is_rejected(self):
        broken = dataclasses.replace(self._claim("C-IRQ-01"), value="vector 32")
        self.assertTrue(any("must not carry a value" in problem
                            for problem in self._with_replaced(broken).problems()))

    def test_unknown_claim_without_a_reason_is_rejected(self):
        broken = dataclasses.replace(self._claim("C-IRQ-01"),
                                     uncertainty="we did not get round to it")
        self.assertTrue(any("must say why nothing public supports it" in problem
                            for problem in self._with_replaced(broken).problems()))

    def test_claim_citing_an_unknown_source_is_rejected(self):
        broken = dataclasses.replace(self._claim("C-CPU-01"), sources=("S-NOWHERE",))
        self.assertTrue(any("cites unknown source" in problem
                            for problem in self._with_replaced(broken).problems()))

    def test_duplicate_claim_ids_are_rejected(self):
        claims = registry.CLAIMS + (registry.CLAIMS[0],)
        self.assertTrue(any("duplicate claim id" in problem
                            for problem in _subsystem_contract(claims=claims).problems()))

    def test_a_source_without_a_date_is_rejected(self):
        bad_source = Source(id="S-BAD", title="t", publisher="p", date="",
                            url="https://example.invalid/doc")
        self.assertTrue(any("title, publisher and date are all required" in problem
                            for problem in _subsystem_contract(sources=(bad_source,)).problems()))

    def test_a_verified_source_without_a_location_is_rejected(self):
        bad_source = Source(id="S-BAD", title="t", publisher="p", date="2026-01-01",
                            url="https://example.invalid/doc", verification="verified",
                            location="")
        self.assertTrue(any("names no location" in problem
                            for problem in _subsystem_contract(
                                sources=registry.SOURCES + (bad_source,)).problems()))

    def test_missing_revision_is_rejected(self):
        self.assertTrue(any("no revision string" in problem
                            for problem in _subsystem_contract(revision="").problems()))


class LinkageAuditTest(unittest.TestCase):
    """The join between the registry and the interfaces."""

    def test_audit_has_no_problems(self):
        problems = audit.problems()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_every_required_group_has_an_interface(self):
        doubles = audit.interfaces()
        for group, names in audit.REQUIRED_GROUPS.items():
            for name in names:
                self.assertIn(name, doubles, group)

    def test_ten_interfaces_cover_eight_groups(self):
        self.assertEqual(len(audit.interfaces()), 10)
        self.assertEqual(len(audit.REQUIRED_GROUPS), 8)
        self.assertEqual(sorted(audit.EXTRA_INTERFACES), ["PSM-IPC"])

    def test_every_operation_cites_a_claim_or_a_decision(self):
        for name, iface in audit.interfaces().items():
            for op_name, spec in iface.op_specs().items():
                self.assertTrue(spec.claim_ids or spec.decision_ids,
                                f"{name}.{op_name} justifies nothing")

    def test_unsupported_operations_cite_an_unknown_or_restricted_claim(self):
        contract = registry.CONTRACT
        for name, iface in audit.interfaces().items():
            for op_name, spec in iface.op_specs().items():
                if not spec.unsupported:
                    continue
                cited = [contract.claim(c) for c in spec.claim_ids]
                self.assertTrue(cited, f"{name}.{op_name}")
                self.assertTrue(
                    any(c and c.classification in ("UNKNOWN", "RESTRICTED") for c in cited),
                    f"{name}.{op_name} is unsupported but cites no gap")

    def test_no_implemented_operation_cites_a_restricted_claim(self):
        contract = registry.CONTRACT
        for name, iface in audit.interfaces().items():
            for op_name, spec in iface.op_specs().items():
                if spec.unsupported:
                    continue
                for claim_id in spec.claim_ids:
                    claim = contract.claim(claim_id)
                    self.assertNotEqual(
                        claim.classification, "RESTRICTED",
                        f"{name}.{op_name} implements restricted behaviour {claim_id}")

    def _audit_with(self, key: str, op: str, **changes):
        """Run the audit against one deliberately damaged interface."""
        doubles = audit.interfaces()
        damaged = doubles
        iface = doubles[key]
        iface.OPS = dict(iface.OPS)          # instance-level, never the class table
        iface.OPS[op] = dataclasses.replace(iface.OPS[op], **changes)
        damaged[key] = iface
        with mock.patch.object(audit, "interfaces", return_value=damaged):
            return audit.problems()

    def test_a_dangling_claim_reference_is_detected(self):
        """Prove the audit fails when an operation cites a claim that does not exist."""
        found = self._audit_with("PSM-CPU", "cpuid", claim_ids=("C-CPU-99",))
        self.assertTrue(any("C-CPU-99" in problem for problem in found), found)

    def test_a_fabricated_success_for_an_unknown_operation_is_detected(self):
        found = self._audit_with("PSM-GFX", "gpu_firmware", unsupported=False,
                                 errors=())
        self.assertTrue(any("gpu_firmware" in problem for problem in found), found)

    def test_an_operation_citing_a_foreign_subsystem_is_detected(self):
        found = self._audit_with("PSM-CPU", "cpuid", claim_ids=("C-MEM-01",))
        self.assertTrue(any("subsystem" in problem for problem in found), found)

    def test_a_required_group_without_an_interface_is_detected(self):
        doubles = audit.interfaces()
        del doubles["PSM-TMR"]
        with mock.patch.object(audit, "interfaces", return_value=doubles):
            found = audit.problems()
        self.assertTrue(any("PSM-TMR" in problem for problem in found), found)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
