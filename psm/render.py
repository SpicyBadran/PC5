"""Render the contract document's data tables from the registry and the doubles.

The document is prose; the tables in it are *not* written by hand. Each table sits
between a pair of markers::

    <!-- psm:begin sources -->
    ...
    <!-- psm:end sources -->

``--write`` regenerates every block in place, and :func:`verify` (run by
``tests/test_psm_document.py`` and ``make check``) fails the build when a block and
its renderer disagree. That is the whole anti-drift mechanism: a claim cannot be
corrected in the registry while the document keeps the old sentence, because the
block check compares bytes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import registry
from .contract import SUBSYSTEMS
from .audit import EXTRA_INTERFACES, REQUIRED_GROUPS, interfaces
from .conformance import fresh_interfaces, suite_audio, suite_cpu, suite_graphics, \
    suite_irq_timer_ipc, suite_memory, suite_services, suite_storage, suite_usb
from .psm import Status

DOC = Path("docs/architecture/PS5_PLATFORM_CONTRACT.md")

BEGIN = "<!-- psm:begin {} -->"
END = "<!-- psm:end {} -->"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_interfaces() -> str:
    rows = []
    for name, iface in interfaces().items():
        info = iface.info
        rows.append([f"`{name}`", info.version, info.subsystem, info.milestone,
                     str(len(iface.op_specs())), info.authored_by, info.note])
    return _table(["Interface", "Version", "Subsystem", "Milestone", "Ops",
                   "Author", "Note"], rows)


def render_groups() -> str:
    rows = [[group, " + ".join(f"`{n}`" for n in names)]
            for group, names in REQUIRED_GROUPS.items()]
    for name, note in EXTRA_INTERFACES.items():
        rows.append([f"extra: {name}", note])
    return _table(["Required group", "Interface(s)"], rows)


def render_operations(interface_name: str) -> str:
    iface = interfaces()[interface_name]
    rows = []
    for op_name, spec in iface.op_specs().items():
        inputs = ", ".join(f"`{name}`" for name in spec.inputs) or "—"
        errors = ", ".join(f"`{e.value}`" for e in spec.errors) or "—"
        classification = ("**UNSUPPORTED by design**" if spec.unsupported
                          else "implemented")
        cites = ", ".join(f"`{c}`" for c in spec.claim_ids) or "—"
        rows.append([f"`{op_name}`", spec.summary, inputs, f"`{spec.outputs}`",
                     errors, classification, cites])
    return _table(["Operation", "Behaviour", "Inputs", "Outputs", "Documented failures",
                   "Status", "Claim"], rows)


def render_invariants(interface_name: str) -> str:
    iface = interfaces()[interface_name]
    rows = [[f"`{op_name}`", "<br>".join(spec.invariants) or "—"]
            for op_name, spec in iface.op_specs().items() if spec.invariants]
    return _table(["Operation", "Invariant the conformance suite checks"], rows)


def render_decisions() -> str:
    rows = [[f"`{d['id']}`", f"`{d['interface']}`", d["decision"], d["rationale"]]
            for d in registry.DECISIONS]
    return _table(["ID", "Interface", "PC5 decision", "Why"], rows)


def _case_counts() -> Dict[str, int]:
    made = fresh_interfaces()
    counts = {
        "PSM-CPU": len(suite_cpu(made["cpu"]).checks),
        "PSM-MEM": len(suite_memory(made["memory"]).checks),
        "PSM-STOR": len(suite_storage(made["storage"]).checks),
        "PSM-GFX": len(suite_graphics(made["graphics"]).checks),
        "PSM-AUD": len(suite_audio(made["audio"]).checks),
        "PSM-USB": len(suite_usb(made["usb"]).checks),
        "PSM-SVC": len(suite_services(made["services"]).checks),
    }
    for suite in suite_irq_timer_ipc(made["irq"], made["timer"], made["ipc"]):
        counts[suite.name] = len(suite.checks)
    return counts


def render_cases() -> str:
    counts = _case_counts()
    rows = [[f"`{name}`", str(count)] for name, count in counts.items()]
    rows.append(["**total**", f"**{sum(counts.values())}**"])
    return _table(["Interface", "Conformance checks"], rows)


def render_statuses() -> str:
    meanings = {
        Status.OK: "the operation did what its specification says",
        Status.INVALID_ARGUMENT: "the request was malformed; nothing changed",
        Status.UNSUPPORTED: "the contract marks this behaviour UNKNOWN or the operation "
                            "is not part of this interface",
        Status.NOT_READY: "the operation is valid but there is nothing to return yet",
        Status.BAD_STATE: "the object exists but the operation is not legal in its state",
        Status.NO_DEVICE: "no such device, queue, stream or service is present",
        Status.EXHAUSTED: "a documented limit is reached; the limit is unchanged",
        Status.TIMEOUT: "the virtual clock advanced and the event did not arrive",
        Status.OWNERSHIP: "the caller does not own what it is trying to use",
        Status.OVERFLOW: "a bounded buffer or queue is full",
        Status.DENIED: "a security-state refusal: distinct from UNSUPPORTED (ADR-007)",
    }
    rows = [[f"`{status.value}`", meanings[status]] for status in Status]
    return _table(["Status", "Means"], rows)


def blocks() -> Dict[str, str]:
    out: Dict[str, str] = {
        "sources": registry.CONTRACT.render_sources(),
        "summary": registry.CONTRACT.render_summary(),
        "interfaces": render_interfaces(),
        "groups": render_groups(),
        "decisions": render_decisions(),
        "cases": render_cases(),
        "statuses": render_statuses(),
    }
    for subsystem in SUBSYSTEMS:
        out[f"claims-{subsystem}"] = registry.CONTRACT.render_claims(subsystem)
    for name in interfaces():
        out[f"interfaces-{name}"] = render_operations(name)
        out[f"invariants-{name}"] = render_invariants(name)
    return out


_PATTERN = re.compile(r"<!-- psm:begin ([A-Za-z0-9-]+) -->\n?(.*?)<!-- psm:end \1 -->",
                      re.DOTALL)


def verify(path: Path = DOC) -> List[str]:
    """Every way the document's tables differ from their renderers."""
    if not path.exists():
        return [f"{path}: the contract document does not exist"]
    text = path.read_text()
    rendered = blocks()
    found: List[str] = []
    seen = set()
    for match in _PATTERN.finditer(text):
        name, body = match.group(1), match.group(2)
        seen.add(name)
        if name not in rendered:
            found.append(f"{path}: block {name!r} has no renderer")
        elif body.strip() != rendered[name].strip():
            found.append(f"{path}: block {name!r} differs from psm.render "
                         f"(run `python3 -m psm.render --write`)")
    for name in rendered:
        if name not in seen:
            found.append(f"{path}: block {name!r} is missing from the document")
    return found


def write(path: Path = DOC) -> int:
    text = path.read_text()
    rendered = blocks()

    def replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in rendered:
            return match.group(0)
        return f"<!-- psm:begin {name} -->\n{rendered[name]}\n<!-- psm:end {name} -->"

    path.write_text(_PATTERN.sub(replace, text))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--write" in argv:
        write()
        print(f"rewrote the rendered blocks in {DOC}")
        return 0
    if "--check" in argv:
        found = verify()
        for problem in found:
            print(f"PROBLEM: {problem}")
        print("document blocks: " + ("OK" if not found else f"{len(found)} problem(s)"))
        return 1 if found else 0
    for name in sorted(blocks()):
        print(f"== {name} ==")
        print(blocks()[name])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
