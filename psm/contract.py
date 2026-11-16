"""The platform-contract registry: claims, sources, and the rules that keep them honest.

Every technical statement in ``docs/architecture/PS5_PLATFORM_CONTRACT.md`` is a
:class:`Claim`. A claim names what supports it (zero or more :class:`Source`
records), how strongly, what is still uncertain, what engineering decision hangs
on it, and which conformance case exercises the PC5 side. The document is not
written by hand: the tables in it are rendered from this registry and a test
fails when the two disagree.

Four classifications, and only four:

``PUBLIC``
    A public primary source states it. Cited with title, publisher, date, URL
    and location. If a source cannot be named, the claim is not PUBLIC.
``INFERRED``
    Follows from public facts plus an assumption stated in the claim itself.
``UNKNOWN``
    Nothing public supports a value. The registry stores no value, and the PSM
    interface returns ``UNSUPPORTED``.
``RESTRICTED``
    Reaching it would require circumvention. Stored so that the refusal is
    explicit and testable (ADR-007, ADR-022); never implemented.

The rules in :meth:`Contract.problems` are enforced by
``tests/test_platform_contract.py``, so a claim that loses its source fails the
build rather than quietly becoming folklore.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CLASSIFICATIONS = ("PUBLIC", "INFERRED", "UNKNOWN", "RESTRICTED")
SUBSYSTEMS = ("cpu", "memory", "interrupts", "storage", "graphics", "audio", "usb", "services", "security")

VERIFICATIONS = (
    "verified",          # fetched and read in this environment
    "located",           # publisher, title and URL confirmed; body not read here
    "not-verified",      # secondary reporting or unconfirmed pointer
)


@dataclass(frozen=True)
class Source:
    """One citable document."""

    id: str
    title: str
    publisher: str
    date: str
    url: str
    verification: str = "not-verified"
    location: str = ""
    notes: str = ""

    def as_row(self) -> str:
        return (f"| `{self.id}` | {self.title} | {self.publisher} | {self.date} | "
                f"[{_short_url(self.url)}]({self.url}) | {self.verification} |")


def _short_url(url: str) -> str:
    text = url.replace("https://", "").replace("http://", "")
    return text if len(text) <= 56 else text[:53] + "..."


@dataclass(frozen=True)
class Claim:
    """One statement about what PC5 must present to the genuine system software."""

    id: str
    subsystem: str
    statement: str
    classification: str
    sources: Tuple[str, ...] = ()
    establishes: str = ""          # "direct" or "supports an inference"
    confidence: str = ""           # free text, must be present
    uncertainty: str = ""          # must be present, and non-empty for UNKNOWN
    decision: str = ""             # the engineering decision that depends on it
    conformance: str = ""          # the PSM operation or case that exercises it
    value: Optional[str] = None    # only ever for PUBLIC, never for UNKNOWN/RESTRICTED

    @property
    def is_implementable(self) -> bool:
        return self.classification in ("PUBLIC", "INFERRED")


@dataclass
class Contract:
    sources: Tuple[Source, ...] = ()
    claims: Tuple[Claim, ...] = ()
    revision: str = ""
    problems_extra: Tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------ lookups
    def source(self, source_id: str) -> Optional[Source]:
        return next((s for s in self.sources if s.id == source_id), None)

    def claim(self, claim_id: str) -> Optional[Claim]:
        return next((c for c in self.claims if c.id == claim_id), None)

    def by_subsystem(self, subsystem: str) -> List[Claim]:
        return [c for c in self.claims if c.subsystem == subsystem]

    def counts(self) -> Dict[str, int]:
        out = {name.lower(): 0 for name in CLASSIFICATIONS}
        for claim in self.claims:
            out[claim.classification.lower()] += 1
        return out

    # ------------------------------------------------------------------- rules
    def problems(self) -> List[str]:
        """Every way this registry is not yet trustworthy, as sentences."""
        found: List[str] = list(self.problems_extra)

        if not self.revision:
            found.append("the contract has no revision string")

        seen_sources: Dict[str, Source] = {}
        for source in self.sources:
            if source.id in seen_sources:
                found.append(f"duplicate source id {source.id!r}")
            seen_sources[source.id] = source
            if not source.url.startswith("https://"):
                found.append(f"source {source.id}: URL is not https ({source.url})")
            if source.verification not in VERIFICATIONS:
                found.append(f"source {source.id}: unknown verification {source.verification!r}")
            if not source.title or not source.publisher or not source.date:
                found.append(f"source {source.id}: title, publisher and date are all required")
            if source.verification == "verified" and not source.location:
                found.append(f"source {source.id}: claims to be verified but names no location")

        seen_claims: Dict[str, Claim] = {}
        for claim in self.claims:
            if claim.id in seen_claims:
                found.append(f"duplicate claim id {claim.id!r}")
            seen_claims[claim.id] = claim

            if claim.subsystem not in SUBSYSTEMS:
                found.append(f"claim {claim.id}: unknown subsystem {claim.subsystem!r}")
            if claim.classification not in CLASSIFICATIONS:
                found.append(f"claim {claim.id}: unknown classification {claim.classification!r}")
            if not claim.confidence:
                found.append(f"claim {claim.id}: no confidence recorded")
            if not claim.uncertainty:
                found.append(f"claim {claim.id}: no remaining uncertainty recorded")
            if not claim.decision:
                found.append(f"claim {claim.id}: no engineering decision depends on it, "
                             f"so it does not belong in the contract")
            if not claim.conformance:
                found.append(f"claim {claim.id}: no conformance case or explicit "
                             f"'not modelled' marker")

            for source_id in claim.sources:
                if source_id not in seen_sources:
                    found.append(f"claim {claim.id}: cites unknown source {source_id!r}")

            # A public claim needs a source that was not merely seen in passing.
            if claim.classification == "PUBLIC":
                usable = [self.source(s) for s in claim.sources]
                if not any(s and s.verification in ("verified", "located") for s in usable):
                    found.append(f"claim {claim.id}: PUBLIC without a verified or located source")
                if not claim.value:
                    found.append(f"claim {claim.id}: PUBLIC without a value")
            if claim.classification == "INFERRED":
                if not claim.sources:
                    found.append(f"claim {claim.id}: INFERRED with no source to infer from")
                if not claim.establishes:
                    found.append(f"claim {claim.id}: INFERRED without saying what it assumes")
            if claim.classification in ("UNKNOWN", "RESTRICTED"):
                if claim.value is not None:
                    found.append(f"claim {claim.id}: {claim.classification} must not carry a value")
                if claim.classification == "UNKNOWN" and not claim.uncertainty.startswith(
                        ("nothing public", "no public", "not known", "undocumented", "no source")):
                    found.append(f"claim {claim.id}: UNKNOWN must say why nothing public supports it")
        return found

    # ---------------------------------------------------------------- rendering
    def render_sources(self) -> str:
        lines = ["| ID | Title | Publisher | Date | URL | Verification |",
                 "|---|---|---|---|---|---|"]
        for source in self.sources:
            lines.append(source.as_row())
        return "\n".join(lines)

    def render_claims(self, subsystem: str) -> str:
        claims = self.by_subsystem(subsystem)
        lines = ["| ID | Claim | Class | Source | Confidence | Remaining uncertainty | Conformance |",
                 "|---|---|---|---|---|---|---|"]
        for claim in claims:
            sources = ", ".join(f"`{s}`" for s in claim.sources) or "— (no public source)"
            lines.append(
                f"| `{claim.id}` | {claim.statement} | **{claim.classification}** | {sources} | "
                f"{claim.confidence} | {claim.uncertainty} | {claim.conformance} |")
        return "\n".join(lines)

    def render_summary(self) -> str:
        counts = self.counts()
        lines = ["| Classification | Claims |", "|---|---|"]
        for name in CLASSIFICATIONS:
            lines.append(f"| {name} | {counts[name.lower()]} |")
        lines.append(f"| **total** | **{len(self.claims)}** |")
        return "\n".join(lines)

    def as_dict(self) -> Dict[str, object]:
        return {
            "revision": self.revision,
            "counts": self.counts(),
            "sources": [
                {"id": s.id, "title": s.title, "publisher": s.publisher, "date": s.date,
                 "url": s.url, "verification": s.verification, "location": s.location}
                for s in self.sources
            ],
            "claims": [
                {"id": c.id, "subsystem": c.subsystem, "statement": c.statement,
                 "classification": c.classification, "sources": list(c.sources),
                 "establishes": c.establishes, "confidence": c.confidence,
                 "uncertainty": c.uncertainty, "decision": c.decision,
                 "conformance": c.conformance, "value": c.value}
                for c in self.claims
            ],
        }


def main(argv: Optional[List[str]] = None) -> int:
    from . import registry

    argv = list(sys.argv[1:] if argv is None else argv)
    contract = registry.CONTRACT
    problems = contract.problems()
    if "--json" in argv:
        print(json.dumps(contract.as_dict(), indent=2, sort_keys=True))
    elif "--sources" in argv:
        print(contract.render_sources())
    elif "--summary" in argv:
        print(contract.render_summary())
    else:
        print(f"PS5 platform contract registry, revision {contract.revision}")
        print(f"  sources: {len(contract.sources)}   claims: {len(contract.claims)}")
        for name, count in contract.counts().items():
            print(f"  {name:<10} {count}")
        for problem in problems:
            print(f"PROBLEM: {problem}")
        if not problems:
            print("registry: OK")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
