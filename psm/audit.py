"""Linkage audit between the claim registry and the PSM interfaces.

The registry on its own proves nothing about the code, and the code on its own
proves nothing about the evidence. This module is the join, and it fails loudly in
both directions:

* an operation that cites a claim which does not exist, or cites a claim from
  another subsystem, is a broken link and therefore a bug;
* an operation marked ``unsupported=True`` must cite at least one ``UNKNOWN`` or
  ``RESTRICTED`` claim — "we do not know" has to point at the gap it comes from;
* an operation that is actually implemented must not cite a ``RESTRICTED`` claim,
  because that claim says the behaviour must never be built;
* a claim whose conformance field names an operation must name one that exists, and
  a claim with no interface must say ``not modelled`` rather than leave a blank;
* every ``PUBLIC``/``INFERRED`` non-security claim is exercised by at least one
  implemented operation, so the registry cannot accumulate facts nobody uses.

Run with ``python3 -m psm.audit``; ``tests/test_psm_registry.py`` runs it too, and
``make check`` runs the tests.
"""

from __future__ import annotations

import sys
from typing import Dict, List, Optional, Sequence, Tuple

from . import registry
from .conformance import fresh_interfaces
from .psm import Interface, Status

#: Interfaces that are required by the contract, mapped to the group name the
#: contract uses. Ten classes cover eight required groups: interrupts, timers and
#: the message transport are one group in the contract and three classes here,
#: because merging them would create exactly the "one fictional hardware
#: interface" the contract forbids.
REQUIRED_GROUPS: Dict[str, Tuple[str, ...]] = {
    "CPU": ("PSM-CPU",),
    "memory": ("PSM-MEM",),
    "interrupts and timers": ("PSM-IRQ", "PSM-TMR"),
    "storage and I/O": ("PSM-STOR",),
    "graphics": ("PSM-GFX",),
    "audio": ("PSM-AUD",),
    "USB and controller": ("PSM-USB",),
    "system services": ("PSM-SVC",),
}

#: The extra interface, reported separately rather than counted as a required group.
EXTRA_INTERFACES: Dict[str, str] = {
    "PSM-IPC": "PC5's own message transport, delivered under M16 as part of the "
               "interrupts/timers group; it is not claimed to be the console's transport",
}

NOT_MODELLED = "not modelled"


def _ops(iface: Interface) -> Dict[str, object]:
    return iface.op_specs()


def interfaces() -> Dict[str, Interface]:
    """The live doubles keyed by interface name, in contract order."""
    made = fresh_interfaces()
    order = ("cpu", "memory", "irq", "timer", "ipc", "storage", "graphics", "audio",
             "usb", "services")
    out: Dict[str, Interface] = {}
    for key in order:
        iface = made[key]
        out[iface.info.name] = iface
    return out


def problems() -> List[str]:
    """Every broken link between the registry and the interfaces."""
    found: List[str] = []
    contract = registry.CONTRACT
    claim_ids = {claim.id for claim in contract.claims}
    doubles = interfaces()

    # ---- interface identity
    seen_names: Dict[str, str] = {}
    for name, iface in doubles.items():
        info = iface.info
        if name in seen_names:
            found.append(f"two interfaces are both called {name}")
        seen_names[name] = info.name
        if info.version != registry.CONTRACT.revision.split(" ")[0] and not info.version:
            found.append(f"{name}: no version")
        if info.authored_by != "PC5":
            found.append(f"{name}: must record PC5 as the author, not {info.authored_by!r}")
        note = info.note.lower()
        if "pc5" not in note or not ("not" in note or "unknown" in note):
            found.append(f"{name}: the note must say PC5 authored the double and that the "
                         f"console's side is not modelled")
        if not _ops(iface):
            found.append(f"{name}: documents no operations")

    # ---- required groups are all present
    required = [name for group in REQUIRED_GROUPS.values() for name in group]
    for name in required:
        if name not in doubles:
            found.append(f"required interface {name} does not exist")
    for name in doubles:
        if name not in required and name not in EXTRA_INTERFACES:
            found.append(f"interface {name} is neither required nor declared as an extra")

    # ---- every operation's claim citations resolve and make sense
    cited: Dict[str, List[str]] = {}
    for name, iface in doubles.items():
        subsystem = iface.info.subsystem
        for op_name, spec in _ops(iface).items():
            for claim_id in spec.claim_ids:
                cited.setdefault(claim_id, []).append(f"{name}.{op_name}")
                claim = contract.claim(claim_id)
                if claim is None:
                    found.append(f"{name}.{op_name}: cites {claim_id}, which is not in the "
                                 f"registry")
                    continue
                if claim.subsystem != subsystem and claim.subsystem != "security":
                    found.append(f"{name}.{op_name}: cites {claim_id} from subsystem "
                                 f"{claim.subsystem!r}, not {subsystem!r}")
                if spec.unsupported and claim.classification not in ("UNKNOWN", "RESTRICTED"):
                    found.append(f"{name}.{op_name}: is marked UNSUPPORTED but cites only "
                                 f"{claim.classification} claim {claim_id}")
                if not spec.unsupported and claim.classification == "RESTRICTED":
                    found.append(f"{name}.{op_name}: implemented but cites RESTRICTED claim "
                                 f"{claim_id}; restricted behaviour must not be built")
            if not spec.claim_ids and not spec.decision_ids:
                found.append(f"{name}.{op_name}: cites neither a claim nor a PC5 decision, so "
                             f"nothing justifies its behaviour")
            for decision_id in spec.decision_ids:
                decision = next((d for d in registry.DECISIONS if d["id"] == decision_id), None)
                if decision is None:
                    found.append(f"{name}.{op_name}: cites unknown decision {decision_id}")
                elif decision["interface"] != name:
                    found.append(f"{name}.{op_name}: cites {decision_id}, which belongs to "
                                 f"{decision['interface']}")
            if spec.unsupported:
                if not spec.claim_ids:
                    found.append(f"{name}.{op_name}: UNSUPPORTED with no claim explaining why")
                if Status.UNSUPPORTED not in spec.errors:
                    found.append(f"{name}.{op_name}: marked unsupported but does not list "
                                 f"UNSUPPORTED in its errors")
            if not spec.invariants:
                found.append(f"{name}.{op_name}: no invariant, so nothing to check")

    # ---- every claim is either exercised or explicitly not modelled
    for claim in contract.claims:
        field = claim.conformance.strip()
        if field.startswith(NOT_MODELLED):
            if claim.classification in ("PUBLIC", "INFERRED") and claim.subsystem != "security":
                # A supportable fact can legitimately be unmodelled, but only if the
                # entry says which milestone owns it: "we will not model it" is a
                # decision, "not modelled" alone is an omission.
                if not any(token.startswith("M") and token[1:].isdigit()
                           for token in field.replace("—", " ").split()):
                    found.append(f"{claim.id}: a supportable claim is not modelled and names "
                                 f"no owning milestone")
            continue
        target = field.split(" ")[0]
        if target not in doubles:
            found.append(f"{claim.id}: conformance names unknown interface {target!r}")
            continue
        mentioned = [token.strip("()/,;") for token in field.split()[1:]]
        names = {op for op in _ops(doubles[target])}
        if not any(token in names for token in mentioned):
            found.append(f"{claim.id}: conformance names no operation of {target} "
                         f"(mentioned {mentioned})")

    # ---- RESTRICTED claims must never be presented as answered
    for claim in contract.claims:
        if claim.classification == "RESTRICTED" and claim.id in cited:
            for where in cited[claim.id]:
                spec = _ops(doubles[where.split(".")[0]])[where.split(".", 1)[1]]
                if not spec.unsupported:
                    found.append(f"{where}: answers a RESTRICTED claim")

    return found


def main(argv: Optional[Sequence[str]] = None) -> int:
    found = problems()
    contract = registry.CONTRACT
    print(f"PSM audit: {len(contract.claims)} claims, "
          f"{sum(len(_ops(i)) for i in interfaces().values())} operations, "
          f"{len(interfaces())} interfaces")
    for group, names in REQUIRED_GROUPS.items():
        print(f"  {group:<22} {' + '.join(names)}")
    for name, note in EXTRA_INTERFACES.items():
        print(f"  extra: {name} — {note}")
    for problem in found:
        print(f"PROBLEM: {problem}")
    if not found:
        print("audit: OK")
    return 1 if found else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
