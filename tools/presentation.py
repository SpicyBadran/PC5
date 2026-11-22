"""Generate and check PC5's presentation layer from the project's own data.

Everything the README shows about status comes from `docs/architecture/roadmap.json`
and from the evidence records under `docs/evidence/`. This tool renders those facts
into seven original SVG figures, writes the README status table in place between
markers, and refuses to accept a repository whose figures or status block no longer
match the data.

    python3 tools/presentation.py sync     # regenerate assets + README block
    python3 tools/presentation.py check    # fail if anything is stale or dishonest

`check` is the point: a repository can always be made to *look* finished. The
figures are derived, the labels are a function of milestone state, and any hand edit
that overstates progress fails the check instead of shipping.

No network access, no third-party assets, no fonts beyond the reader's own: the SVGs
are plain geometry and `font-family="... monospace"` text, so they render on GitHub
and stay diffable.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import brand  # noqa: E402 -- same-directory sibling, shared logo geometry

ROADMAP = ROOT / "docs/architecture/roadmap.json"
README = ROOT / "README.md"
ASSETS = ROOT / "docs/assets"

BEGIN = "<!-- pc5:status:begin -->"
END = "<!-- pc5:status:end -->"

# ---------------------------------------------------------------- palette
BG = "#0b0f14"
PANEL = "#131a23"
PANEL_2 = "#0f151d"
LINE = "#243040"
INK = "#e6edf3"
MUTED = "#8b98a8"
CYAN = "#38bdf8"
GREEN = "#34d399"
AMBER = "#fbbf24"
RED = "#f87171"
VIOLET = "#a78bfa"

STATE_STYLE = {
    "complete": ("VERIFIED", GREEN),
    "in_progress": ("IN PROGRESS", CYAN),
    "not_started": ("NOT STARTED", MUTED),
    "blocked": ("BLOCKED", RED),
    # a layer whose code exists but which has never run on the hardware it targets
    "not_tested": ("NOT TESTED", AMBER),
}

STATE_ORDER = {"complete": 0, "in_progress": 1, "not_started": 2, "not_tested": 3,
               "blocked": 4}

FONT = "'JetBrains Mono','SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"


def load():
    with ROADMAP.open(encoding="utf-8") as handle:
        return json.load(handle)


def milestones(data) -> dict:
    return {m["id"]: m for m in data["milestones"]}


def workstreams(data) -> list:
    return data["workstreams"]


# ---------------------------------------------------------------- status rows
# Each row is either a milestone claim or a gate recorded in a document. A milestone
# row's label is a pure function of the milestone states, so it cannot say more than
# the graph does. A gate row names the document that has to still contain a token --
# if the evidence is reworded away, the row fails the check rather than lingering.
ROWS = (
    ("ISO build and structural verification", ("M1a", "M1h"), None),
    ("BIOS boot path (legacy firmware)", ("M1a",), None),
    ("UEFI boot path", ("M1c",), None),
    ("Emulated PC boot harness (v86)", (), "docs/TEST_RESULTS.md|PASSED|VERIFIED"),
    ("Real OVMF firmware gate on this machine", (),
     "docs/TEST_RESULTS.md|NOT TESTED|NOT TESTED"),
    ("Physical PC boot", ("M1",), None),
    ("Hardware drivers for the qualified list", ("M2",), None),
    ("Platform services (processes, syscalls)", ("M3",), None),
    ("PS5 platform contract", ("M10",), None),
    ("PS5 platform device models", ("M11", "M12", "M13", "M14", "M15", "M16"), None),
    ("Security and authenticity boundary", ("M17",), None),
    ("Genuine Sony system-software load", ("M18",), None),
    ("Genuine Sony system software executing", ("M19",), None),
)


def worst(states) -> str:
    return max(states, key=lambda state: STATE_ORDER[state])


def physical_evidence() -> list:
    """Records actually filed under docs/evidence, excluding the empty template."""
    filed = []
    for path in sorted((ROOT / "docs/evidence").glob("*.json")):
        if "TEMPLATE" in path.name:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(record.get("level", "")).startswith("physical"):
            filed.append(path)
    return filed


LABEL_COLOUR = {"VERIFIED": GREEN, "IN PROGRESS": CYAN, "TESTING REQUIRED": AMBER,
                "NOT TESTED": AMBER, "NOT STARTED": MUTED, "BLOCKED": RED}


def status_rows(data) -> list:
    by_id = milestones(data)
    filed = physical_evidence()
    rows = []
    for title, ids, gate in ROWS:
        if ids:
            states = [by_id[m]["state"] for m in ids if m in by_id]
            state = worst(states) if states else "not_started"
            if ids == ("M1",):
                # the graph says not_started; what that means in presentation terms is
                # that no machine has been tested, so say so rather than "not started"
                state = "complete" if filed else "not_tested"
            label, colour = STATE_STYLE[state]
            evidence = "`" + "` `".join(ids) + "`"
            if ids == ("M1",):
                evidence = (f"{len(filed)} record(s) filed" if filed
                            else "no record filed — `docs/evidence/`")
            rows.append((title, label, colour, evidence))
        else:
            document, token, declared = gate.split("|")
            body = (ROOT / document).read_text(encoding="utf-8")
            # the declared label is the honest one, but only while the evidence it
            # names is still in the document: a reworded test record cannot leave a
            # "VERIFIED" chip behind
            label = declared if token in body else "NOT TESTED"
            rows.append((title, label, LABEL_COLOUR[label], f"`{document}`"))
    return rows


def status_markdown(data) -> str:
    rows = status_rows(data)
    lines = [
        BEGIN,
        "| Capability | State | Where it is recorded |",
        "|---|---|---|",
    ]
    for title, label, _colour, evidence in rows:
        lines.append(f"| {title} | **{label}** | {evidence} |")
    lines.append("")
    lines.append(
        "States are generated from `docs/architecture/roadmap.json` by "
        "`tools/presentation.py` — `complete` renders as **VERIFIED** only when the "
        "milestone carries its evidence, and a physical claim needs a filed record "
        "under `docs/evidence/`. Nothing in this table is typed by hand."
    )
    lines.append(END)
    return "\n".join(lines)


# ---------------------------------------------------------------- svg helpers
def svg(width: int, height: int, body: str, title: str, desc: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d">\n'
        f'  <title id="t">{escape(title)}</title>\n'
        f'  <desc id="d">{escape(desc)}</desc>\n'
        f'  <rect width="{width}" height="{height}" fill="{BG}"/>\n'
        f"{body}"
        f"</svg>\n"
    )


def text(x, y, body, size=14, fill=INK, weight="normal", anchor="start", spacing=None):
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'  <text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}"{extra}>'
        f"{escape(body)}</text>\n"
    )


def panel(x, y, w, h, fill=PANEL, stroke=LINE, radius=10, dash=None):
    dashed = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
            f'fill="{fill}" stroke="{stroke}"{dashed}/>\n')


def badge(x, y, label, colour, w=None, size=11):
    width = w or (len(label) * 7 + 20)
    return (
        f'  <rect x="{x}" y="{y}" width="{width}" height="22" rx="11" fill="{colour}" '
        f'opacity="0.14" stroke="{colour}" stroke-opacity="0.55"/>\n'
        + text(x + width / 2, y + 15, label, size=size, fill=colour,
               weight="bold", anchor="middle")
    )


def arrow_up(x, y_bottom, y_top, colour=LINE):
    """Points from a lower layer to the one above it: "this is what it stands on"."""
    return (
        f'  <line x1="{x}" y1="{y_bottom}" x2="{x}" y2="{y_top + 7}" stroke="{colour}" '
        f'stroke-width="1.5" stroke-dasharray="4 4"/>\n'
        f'  <path d="M {x - 5} {y_top + 8} L {x} {y_top} L {x + 5} {y_top + 8}" fill="none" '
        f'stroke="{colour}" stroke-width="1.5"/>\n'
    )


def arrow(x, y1, y2, colour=LINE):
    return (
        f'  <line x1="{x}" y1="{y1}" x2="{x}" y2="{y2 - 7}" stroke="{colour}" '
        f'stroke-width="1.5" stroke-dasharray="4 4"/>\n'
        f'  <path d="M {x - 5} {y2 - 8} L {x} {y2} L {x + 5} {y2 - 8}" fill="none" '
        f'stroke="{colour}" stroke-width="1.5"/>\n'
    )


# ---------------------------------------------------------------- figures
def banner(data) -> str:
    by_id = milestones(data)
    complete = sum(1 for m in data["milestones"] if m["state"] == "complete")
    total = len(data["milestones"])
    streams = len(data["workstreams"])
    dest_label, dest_colour = STATE_STYLE[by_id["M19"]["state"]]

    body = ""
    # the project's own monogram, from the same geometry as brand/pc5-mark.svg
    body += f"  {brand.mark_paths(CYAN, brand.SYM_STROKE, 'translate(60,35) scale(0.234375)')}\n"
    body += text(200, 118, "PC5", size=64, weight="bold", spacing="4")
    body += text(206, 152,
                 "PC-side boot, hardware and compatibility infrastructure, built "
                 "toward genuine PS5 system software", size=15, fill=MUTED)
    chips = (
        ("ROADMAP DOC " + data["document_version"].upper(), CYAN),
        (f"{streams} WORKSTREAMS", VIOLET),
        (f"{complete}/{total} MILESTONES COMPLETE", GREEN),
        (f"DESTINATION M19 {dest_label}", dest_colour),
    )
    x = 206
    for label, colour in chips:
        width = len(label) * 7 + 22
        body += badge(x, 176, label, colour, w=width, size=11)
        x += width + 12

    # the honest line, in the figure, not only in prose
    body += text(206, 244, "The ISO boots PC5's own loader and kernel. No Sony software is present,",
                 size=13, fill=INK)
    body += text(206, 264, "and none is executed. Physical PC boot: NOT TESTED.",
                 size=13, fill=AMBER)
    body += (f'  <line x1="60" y1="292" x2="1140" y2="292" stroke="{LINE}"/>\n')
    body += text(60, 312, "docs/architecture/roadmap.json", size=11, fill=MUTED)
    body += text(1140, 312, "generated by tools/presentation.py", size=11,
                 fill=MUTED, anchor="end")
    return svg(1200, 340, body, "PC5",
               "PC5 wordmark and status strip. Destination milestone M19, "
               f"{dest_label.lower()}; physical PC boot not tested.")


def architecture(data) -> str:
    by_id = milestones(data)
    # (title, subtitle, milestone ids, forced state or None). A forced state is used
    # only where the graph's own label would overstate: `M1h` is complete because the
    # hybrid MBR was verified, but that says nothing about a real machine's firmware.
    layers = (
        ("Genuine Sony PS5 system software", "the destination — no lawful path yet",
         ("M19",), None),
        ("PS5 platform model", "CPU, memory, storage, graphics, audio, USB, services",
         ("M11", "M12", "M13", "M14", "M15", "M16"), None),
        ("PS5 platform contract", "claims, interfaces, conformance doubles (r1)",
         ("M10",), None),
        ("PC5 platform services", "processes, protection, syscalls, devices",
         ("M3",), None),
        ("PC5 kernel", "ATAPI/ISO9660, FAT32 read/write, PCI, ACPI, SMBIOS, partitions",
         ("M1d", "M1e", "M1f", "M1g", "M1i"), None),
        ("PC5 bootloader", "mbr.bin for BIOS, EFI/BOOT/BOOTX64.EFI for UEFI",
         ("M1a", "M1c"), None),
        ("Firmware", "UEFI, and legacy BIOS/CSM where the machine offers it; "
                     "executed in emulation and stub firmware only", (), "not_tested"),
        ("PC hardware", "x86-64 machines that would otherwise run Windows; "
                        "inventory implemented, drivers not started",
         ("M2",), None),
    )
    body = text(48, 46, "PC5 ARCHITECTURE", size=20, weight="bold")
    body += text(48, 70, "layers in dependency order, each labelled from the milestone graph",
                 size=12, fill=MUTED)
    top = 92
    height = 68
    gap = 12
    for index, (title, subtitle, ids, forced) in enumerate(layers):
        y = top + index * (height + gap)
        states = [by_id[m]["state"] for m in ids if m in by_id]
        state = forced or (worst(states) if states else "not_started")
        label, colour = STATE_STYLE[state]
        fill = PANEL if index % 2 == 0 else PANEL_2
        body += panel(48, y, 904, height, fill=fill,
                      stroke=colour if state == "blocked" else LINE,
                      dash="6 5" if state == "blocked" else None)
        body += text(72, y + 30, title, size=15, weight="bold",
                     fill=colour if state == "blocked" else INK)
        body += text(72, y + 52, subtitle, size=11.5, fill=MUTED)
        body += badge(776, y + 23, label, colour, size=10)
        if index > 0:  # arrow from this layer up to the one it supports
            body += arrow_up(500, y, y - gap)
    body += text(48, top + len(layers) * (height + gap) + 22,
                 "solid border: evidence in the repository   ·   dashed: blocked   ·   "
                 "grey: not started   ·   amber: code exists, never run on the target",
                 size=11, fill=MUTED)
    return svg(1000, top + len(layers) * (height + gap) + 40, body,
               "PC5 architecture, layer by layer",
               "Eight layers from PC hardware up to genuine Sony PS5 system software, "
               "each marked with the state recorded in the roadmap. Only the "
               "bootloader, kernel and platform contract have evidence; firmware has "
               "never run on a real machine; the destination is blocked.")


def clip(body: str, width: int) -> str:
    return body if len(body) <= width else body[: width - 1].rstrip() + "\u2026"


def wrap(body: str, width: int) -> list:
    words, lines, current = body.split(), [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


#: Width of one character of the monospace stack these figures request, in em.  Deliberately
#: an over-estimate: DejaVu Sans Mono, the widest fallback a reader is likely to get, is about
#: 0.60 em, so a label that measures here also fits in a narrower font.
CHAR_EM = 0.62

#: the gap `region_head` keeps between a region's title and its right-aligned figure
HEAD_GAP = 12


def measure(body: str, size: float) -> float:
    """How wide `body` is at `size`, in the units the layout guard already uses."""
    return len(body) * float(size) * CHAR_EM


def fit(body: str, room: float, size: float) -> str:
    """Shorten `body` only when it genuinely cannot fit `room` pixels.

    A caller who sees an ellipsis here has a wording problem, not a layout problem: the
    figure's own test (`tests/test_progress_matrix.py`) fails on any clipped label, so this
    stays a safety net rather than a way of hiding text.
    """
    budget = int(room / (float(size) * CHAR_EM))
    return body if len(body) <= budget or budget < 2 else clip(body, budget)


def fit_lines(body: str, room: float, size: float, limit: int = 2) -> list:
    """Wrap `body` onto at most `limit` lines of `room` pixels; no word is silently dropped."""
    per_line = max(4, int(room / (float(size) * CHAR_EM)))
    lines = wrap(body, per_line)
    if len(lines) > limit:
        lines = lines[:limit - 1] + [fit(" ".join(lines[limit - 1:]), room, size)]
    return lines or [fit(body, room, size)]


def progress(data) -> str:
    by_id = milestones(data)
    streams = workstreams(data)
    rows = []
    for stream in streams:
        members = [m for m in data["milestones"] if m["workstream"] == stream["id"]]
        counts = {state: sum(1 for m in members if m["state"] == state)
                  for state in ("complete", "in_progress", "blocked")}
        rows.append((stream["id"], stream["name"], counts, len(members)))

    complete = sum(1 for m in data["milestones"] if m["state"] == "complete")
    total = len(data["milestones"])

    body = text(48, 46, "MILESTONE GRAPH", size=20, weight="bold")
    body += text(48, 70, "milestones per workstream by state — "
                         f"{complete} of {total} complete in the graph", size=12, fill=MUTED)
    body += text(48, 88, "a count of graph nodes, not of effort; a blocked milestone is "
                         "not partial progress", size=11, fill=AMBER)

    top = 112
    step = 38
    bar_x = 372
    bar_w = 448
    for index, (sid, name, counts, count) in enumerate(rows):
        y = top + index * step
        if index % 2 == 0:
            body += panel(36, y, 928, step - 6, fill=PANEL_2, stroke="none", radius=6)
        body += text(52, y + 21, sid, size=12, fill=CYAN, weight="bold")
        body += text(96, y + 21, clip(name, 34), size=12, fill=INK)
        body += (f'  <rect x="{bar_x}" y="{y + 8}" width="{bar_w}" height="14" '
                 f'rx="7" fill="{LINE}"/>\n')
        offset = bar_x
        for state, colour in (("complete", GREEN), ("in_progress", CYAN),
                              ("blocked", RED)):
            if not counts[state]:
                continue
            width = max(4, round(bar_w * counts[state] / count))
            body += (f'  <rect x="{offset}" y="{y + 8}" width="{width}" height="14" '
                     f'rx="7" fill="{colour}" fill-opacity="'
                     f'{"1" if state == "complete" else "0.75"}"/>\n')
            offset += width
        body += text(bar_x + bar_w + 14, y + 21, f"{counts['complete']}/{count}", size=12,
                     fill=MUTED)
        if counts["blocked"]:
            body += text(bar_x + bar_w + 62, y + 21, f"{counts['blocked']} blocked",
                         size=11, fill=RED)

    footer = top + len(rows) * step + 14
    body += (f'  <line x1="48" y1="{footer}" x2="952" y2="{footer}" stroke="{LINE}"/>\n')
    legend = (f'  <rect x="48" y="{footer + 12}" width="10" height="10" rx="3" fill="{GREEN}"/>\n'
              + text(66, footer + 21, "complete", size=11, fill=MUTED)
              + f'  <rect x="140" y="{footer + 12}" width="10" height="10" rx="3" fill="{CYAN}"/>\n'
              + text(158, footer + 21, "in progress", size=11, fill=MUTED)
              + f'  <rect x="242" y="{footer + 12}" width="10" height="10" rx="3" fill="{RED}"/>\n'
              + text(260, footer + 21, "blocked", size=11, fill=MUTED)
              + f'  <rect x="330" y="{footer + 12}" width="10" height="10" rx="3" fill="{LINE}"/>\n'
              + text(348, footer + 21, "not started", size=11, fill=MUTED))
    body += legend
    dest = by_id["M19"]
    body += text(48, footer + 52, "DESTINATION  M19  " + dest["title"], size=13,
                 weight="bold", fill=RED)
    chain = "longest dependency chain: " + " \u2192 ".join(path_ids(data))
    y = footer + 74
    for line in wrap(chain, 104):
        body += text(48, y, line, size=11, fill=MUTED)
        y += 16
    body += text(48, y + 6, "print it from the graph with: python3 tools/roadmap.py path",
                 size=11, fill=MUTED)
    return svg(1000, y + 26, body, "PC5 milestone progress",
               "One bar per workstream showing how many of its milestones are complete, "
               "in progress or blocked, with the count of blocked milestones and the "
               "longest dependency chain to the destination milestone M19.")


def path_ids(data) -> list:
    """The longest dependency chain ending at the destination, computed here."""
    by_id = milestones(data)
    best = {}

    def longest(mid, seen=()):
        if mid in best:
            return best[mid]
        if mid in seen:
            return [mid]
        deps = by_id.get(mid, {}).get("depends_on", [])
        if not deps:
            best[mid] = [mid]
            return best[mid]
        chains = [longest(dep, seen + (mid,)) for dep in deps]
        best[mid] = max(chains, key=len) + [mid]
        return best[mid]

    return longest(data["product"]["destination"]["milestone"])


def workflow(data) -> str:
    steps = (
        ("1", "Build the ISO", "make — reproducible, SOURCE_DATE_EPOCH pinned",
         "VERIFIED", GREEN),
        ("2", "Verify image and manifest", "make verify — structure, files, boot entries",
         "VERIFIED", GREEN),
        ("3", "Write to USB media", "byte-for-byte; read the medium back and hash it",
         "NOT TESTED", MUTED),
        ("4", "Boot a PC via firmware menu", "UEFI, or legacy BIOS/CSM where offered",
         "NOT TESTED", MUTED),
        ("5", "PC5 initialises the machine", "inventory, storage, console — emulated only so far",
         "TESTING REQUIRED", AMBER),
        ("6", "Sony system software executes", "the destination; no lawful path exists yet",
         "BLOCKED", RED),
    )
    body = text(48, 46, "FROM COMMIT TO BOOT", size=20, weight="bold")
    body += text(48, 70, "what is proven today, and what is still only intended",
                 size=12, fill=MUTED)
    top = 94
    height = 62
    gap = 14
    for index, (number, title, subtitle, label, colour) in enumerate(steps):
        y = top + index * (height + gap)
        body += panel(48, y, 904, height, fill=PANEL if index % 2 == 0 else PANEL_2)
        body += (f'  <circle cx="86" cy="{y + height / 2}" r="17" fill="{PANEL_2}" '
                 f'stroke="{colour}" stroke-width="1.5"/>\n')
        body += text(86, y + height / 2 + 5, number, size=14, fill=colour,
                     weight="bold", anchor="middle")
        body += text(120, y + 27, title, size=14, weight="bold")
        body += text(120, y + 47, subtitle, size=11.5, fill=MUTED)
        body += badge(742, y + 20, label, colour, size=10)
        if index < len(steps) - 1:
            body += arrow(86, y + height, y + height + gap,
                          LINE if index < 2 else (LINE if index < 4 else RED))
    body += text(48, top + len(steps) * (height + gap) + 16,
                 "a PUP placed inside an ISO is not a bootable PS5 system — see "
                 "docs/PS5_SYSTEM_SOFTWARE_TARGET.md",
                 size=11, fill=AMBER)
    return svg(1000, top + len(steps) * (height + gap) + 40, body,
               "PC5 boot and installation workflow",
               "Six steps from building the ISO to genuine Sony system software. "
               "Steps one and two are verified; writing media and booting a physical "
               "PC are not tested; the destination is blocked.")


# ---------------------------------------------------------------- validation records
VALIDATION = ROOT / "docs/evidence/validation"
BLOCKS = ("stats", "status", "evidence", "blockers", "pup", "matrix")

STATE_LABEL = {"complete": "complete", "in_progress": "in progress",
               "not_started": "not started", "blocked": "blocked"}


def load_records(directory: Path | None = None) -> dict:
    """Every committed validation record, keyed by gate. Unreadable records are absent."""
    found = {}
    for path in sorted((directory or VALIDATION).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("schema") != "pc5.validation/1":
            continue
        record["file"] = f"docs/evidence/validation/{path.name}"
        found[record.get("gate", path.stem)] = record
    return found


def distribution(data) -> dict:
    counts = {state: 0 for state in STATE_ORDER}
    for milestone in data["milestones"]:
        counts[milestone["state"]] = counts.get(milestone["state"], 0) + 1
    return counts


def record_line(records: dict, gate: str) -> tuple:
    """(text, colour) for one gate's recorded result, and never a promotion."""
    record = records.get(gate)
    if not record:
        return ("no record", MUTED)
    result = record.get("result", "error")
    colour = {"passed": GREEN, "not-tested": AMBER, "skipped": AMBER,
              "failed": RED, "error": RED}.get(result, MUTED)
    checks = record.get("checks") or {}
    detail = f" {checks['passed']}/{checks['total']}" if checks else ""
    return (f"{result}{detail} @ {record.get('commit', '')[:7]}", colour)


def physical_records() -> list:
    records = []
    for path in sorted((ROOT / "docs/evidence").glob("*.json")):
        if "TEMPLATE" in path.name:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(record.get("level", "")).startswith("physical"):
            records.append((path.name, record))
    return records


def pup_facts() -> dict:
    """What the tracked package actually is, read from the pointer, not remembered."""
    import hashlib

    path = ROOT / "PS5UPDATE.PUP"
    raw = path.read_bytes()
    facts = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
             "pointer": raw.startswith(b"version https://git-lfs"), "oid": "", "size": ""}
    if facts["pointer"]:
        for line in raw.decode("ascii", "replace").splitlines():
            if line.startswith("oid sha256:"):
                facts["oid"] = line.split(":", 1)[1].strip()
            elif line.startswith("size "):
                facts["size"] = line.split(None, 1)[1].strip()
    return facts


def product_version(root=None) -> str:
    """Read the product version from the one place that defines it.

    The roadmap file has its own `document_version`, which is the revision of
    that document and not the product's.  Anything that shows a product version
    reads it from `Makefile`, so the two can never drift apart again.
    """
    path = (root or ROOT) / "Makefile"
    try:
        text = path.read_text()
    except OSError:
        return "unknown"
    match = re.search(r"^VERSION\s*[?:]?=\s*(\S+)\s*$", text, re.MULTILINE)
    return match.group(1) if match else "unknown"


# ---------------------------------------------------------------- generated blocks
def stats_markdown(data, records) -> str:
    counts = distribution(data)
    total = len(data["milestones"])
    destination = data["product"]["destination"]["milestone"]
    by_id = milestones(data)
    lines = [f"<!-- pc5:stats:begin -->",
             "| | |",
             "|---|---|",
             f"| Product version | `{product_version()}` (single source: `Makefile VERSION`) |",
             f"| Roadmap document | `{data['document_version']}`, updated {data['updated']} |",
             f"| Milestones | {total} in {len(data['workstreams'])} workstreams |",
             f"| Complete | {counts['complete']} |",
             f"| In progress | {counts['in_progress']} |",
             f"| Blocked | {counts['blocked']} |",
             f"| Not started | {counts['not_started']} |",
             f"| Destination | `{destination}` — {by_id[destination]['title']} |",
             f"| Destination state | **{STATE_STYLE[by_id[destination]['state']][0]}** |"]
    build, _colour = record_line(records, "verify")
    lines.append(f"| Last recorded ISO verification | {build} |")
    checks, _colour = record_line(records, "check")
    lines.append(f"| Last recorded test run | {checks} |")
    filed = physical_records()
    lines.append(f"| Physical boot evidence | "
                 f"{'filed: ' + ', '.join(name for name, _ in filed) if filed else '**none filed**'} |")
    lines.append("| Genuine Sony software execution | **BLOCKED — nothing executed** |")
    lines.append("")
    lines.append("Every cell is generated from `docs/architecture/roadmap.json` and "
                 "`docs/evidence/validation/*.json` by `tools/presentation.py`; a "
                 "statistic that is typed into the prose instead fails "
                 "`make presentation-check`.")
    lines.append("<!-- pc5:stats:end -->")
    return "\n".join(lines)


def evidence_markdown(records) -> str:
    filed = physical_records()
    rows = [
        ("Host and kernel tests", "check", "`make check`"),
        ("ISO structure and manifest", "verify", "`make verify`"),
        ("Emulator execution (v86)", "boot-test", "`make boot-test`"),
        ("Stub firmware (unicorn)", "stubtest", "`make stubtest`"),
        ("Real OVMF firmware", "ovmf-test", "`make ovmf-test`"),
    ]
    lines = ["<!-- pc5:evidence:begin -->",
             "| Evidence | Gate | Result | Recorded at | Date |",
             "|---|---|---|---|---|"]
    for title, gate, command in rows:
        record = records.get(gate)
        if not record:
            lines.append(f"| {title} | {command} | **no record** | — | — |")
            continue
        checks = record.get("checks") or {}
        detail = f", {checks['passed']}/{checks['total']}" if checks else ""
        lines.append(f"| {title} | {command} | **{record.get('result')}**{detail} | "
                     f"`{record.get('commit', '')[:7]}` | {record.get('date', '')} |")
    for name, record in filed:
        lines.append(f"| Physical PC boot (UEFI/BIOS) | `{name}` | "
                     f"**{record.get('outcome', 'filed')}** | — | {record.get('date', '')} |")
    if not filed:
        lines.append("| Physical PC boot (UEFI/BIOS) | `tools/hw_record.py` | "
                     "**NOT TESTED** — no record filed | — | — |")
    lines.append("| Genuine Sony system software | — | **BLOCKED** — not present, not executed | — | — |")
    lines.append("")
    lines.append("A record is a snapshot of one run at one commit. A run recorded against an "
                 "earlier commit is not evidence for the current one, and no gate here is "
                 "promoted by the existence of another gate's record.")
    lines.append("<!-- pc5:evidence:end -->")
    return "\n".join(lines)


def blockers_markdown(data) -> str:
    blocked = [m for m in data["milestones"] if m["state"] == "blocked"]
    lines = ["<!-- pc5:blockers:begin -->"]
    if not blocked:
        lines.append("No milestone is blocked.")
    else:
        lines.append("| Milestone | What is blocked | Why |")
        lines.append("|---|---|---|")
        for milestone in blocked:
            reason = " ".join(str(milestone.get("blocker", "")).split())
            if len(reason) > 220:
                reason = reason[:217].rstrip() + "…"
            lines.append(f"| `{milestone['id']}` | {milestone['title']} | {reason} |")
    lines.append("")
    lines.append("A blocker is a dependency that cannot be satisfied here — a machine, a "
                 "workflow permission, or a lawful path to Sony's authentication. None of "
                 "them is work in progress.")
    lines.append("<!-- pc5:blockers:end -->")
    return "\n".join(lines)


def pup_markdown() -> str:
    facts = pup_facts()
    lines = ["<!-- pc5:pup:begin -->",
             "| Property | Value |",
             "|---|---|",
             f"| working-tree content | {'a Git LFS pointer' if facts['pointer'] else 'unknown'} "
             f"({facts['bytes']} bytes, `sha256 {facts['sha256'][:12]}…`) |"]
    if facts["size"]:
        lines.append(f"| declared object | `oid {facts['oid'][:12]}…`, size "
                     f"{int(facts['size']):,} B |")
    lines.append("| payload in this checkout | **no** — only the pointer is tracked |")
    lines.append("| analysed, decrypted or executed | **no**, in any sense |")
    lines.append("")
    lines.append("Read from the tracked file on every generation, so the sizes cannot drift. "
                 "Package type and version remain **UNVERIFIED**: a pointer is not an analysis.")
    lines.append("<!-- pc5:pup:end -->")
    return "\n".join(lines)


def dashboard(data, records) -> str:
    counts = distribution(data)
    total = len(data["milestones"])
    destination = data["product"]["destination"]["milestone"]
    dest_state = milestones(data)[destination]["state"]
    dest_label, dest_colour = STATE_STYLE[dest_state]

    body = text(48, 44, "PC5 DASHBOARD", size=20, weight="bold")
    body += text(48, 68, "every number below is generated from roadmap.json and the "
                         "committed validation records", size=12, fill=MUTED)

    cards = [
        ("MILESTONES", f"{counts['complete']}/{total}", "complete",
         GREEN if counts["complete"] else MUTED),
        ("WORKSTREAMS", str(len(data["workstreams"])), "defined", CYAN),
        ("IN PROGRESS", str(counts["in_progress"]), "milestones", CYAN),
        ("BLOCKED", str(counts["blocked"]), "milestones", RED if counts["blocked"] else MUTED),
        ("NOT STARTED", str(counts["not_started"]), "milestones", MUTED),
        ("DESTINATION", destination, dest_label.lower(), dest_colour),
    ]
    top = 92
    width, height, gap = 356, 86, 18
    for index, (label, value, detail, colour) in enumerate(cards):
        column, row = index % 3, index // 3
        x = 48 + column * (width + gap)
        y = top + row * (height + gap)
        body += panel(x, y, width, height, fill=PANEL if row % 2 == 0 else PANEL_2,
                      stroke=colour if colour != MUTED else LINE)
        body += text(x + 20, y + 26, label, size=11, fill=MUTED, spacing="1.5")
        body += text(x + 20, y + 62, value, size=26, weight="bold", fill=colour)
        body += text(x + 20, y + 78, detail, size=11, fill=MUTED)

    footer = top + 2 * (height + gap) + 18
    body += (f'  <line x1="48" y1="{footer}" x2="1152" y2="{footer}" stroke="{LINE}"/>\n')
    verify_text, verify_colour = record_line(records, "verify")
    check_text, check_colour = record_line(records, "check")
    ovmf_text, ovmf_colour = record_line(records, "ovmf-test")
    body += text(48, footer + 24, "ISO VERIFICATION", size=10, fill=MUTED, spacing="1.5")
    body += text(48, footer + 44, verify_text, size=13, fill=verify_colour, weight="bold")
    body += text(328, footer + 24, "TEST RUN", size=10, fill=MUTED, spacing="1.5")
    body += text(328, footer + 44, check_text, size=13, fill=check_colour, weight="bold")
    body += text(608, footer + 24, "REAL OVMF", size=10, fill=MUTED, spacing="1.5")
    body += text(608, footer + 44, ovmf_text, size=13, fill=ovmf_colour, weight="bold")
    filed = physical_records()
    body += text(888, footer + 24, "PHYSICAL BOOT", size=10, fill=MUTED, spacing="1.5")
    body += text(888, footer + 44, f"{len(filed)} record(s)" if filed else "not tested",
                 size=13, fill=GREEN if filed else AMBER, weight="bold")
    return svg(1200, footer + 64, body, "PC5 project dashboard",
               f"{counts['complete']} of {total} milestones complete across "
               f"{len(data['workstreams'])} workstreams, {counts['in_progress']} in progress, "
               f"{counts['blocked']} blocked, destination {destination} {dest_label.lower()}; "
               "the latest recorded verification and test results are stamped with the commit "
               "they ran at.")


def evidence_ladder(data, records) -> str:
    filed = physical_records()
    rows = [
        ("Host and kernel tests", "check", GREEN),
        ("ISO structure and manifest", "verify", GREEN),
        ("Emulator execution (v86 + SeaBIOS)", "boot-test", GREEN),
        ("Stub firmware (unicorn)", "stubtest", GREEN),
        ("Real OVMF firmware", "ovmf-test", AMBER),
    ]
    body = text(48, 44, "WHAT HAS ACTUALLY BEEN DEMONSTRATED", size=20, weight="bold")
    body += text(48, 68, "one row per evidence level; a stronger row is not implied by a "
                         "weaker one below or above it", size=12, fill=MUTED)
    top, height, gap = 94, 54, 10
    index = 0
    for title, gate, fallback in rows:
        textline, colour = record_line(records, gate)
        y = top + index * (height + gap)
        body += panel(48, y, 904, height, fill=PANEL if index % 2 == 0 else PANEL_2,
                      stroke=colour if colour in (AMBER, RED) else LINE)
        body += text(72, y + 24, title, size=14, weight="bold")
        body += text(72, y + 42, record_hint(records, gate), size=11, fill=MUTED)
        body += badge(770, y + 16, textline, colour, size=10)
        index += 1
    for title, colour, hint, label in (
            ("Physical PC boot (BIOS or UEFI)",
             GREEN if filed else AMBER,
             (f"{len(filed)} record(s) filed under docs/evidence/" if filed
              else "no record filed — the procedure exists, the machine does not"),
             "verified" if filed else "not tested"),
            ("Genuine Sony system software executing", RED,
             "no lawful path exists; nothing has been loaded or executed", "blocked")):
        y = top + index * (height + gap)
        body += panel(48, y, 904, height, fill=PANEL if index % 2 == 0 else PANEL_2,
                      stroke=colour if colour in (AMBER, RED) else LINE,
                      dash="6 5" if colour == RED else None)
        body += text(72, y + 24, title, size=14, weight="bold", fill=RED if colour == RED else INK)
        body += text(72, y + 42, hint, size=11, fill=MUTED)
        body += badge(770, y + 16, label, colour, size=10)
        index += 1
    body += text(48, top + index * (height + gap) + 10,
                 "records live in docs/evidence/validation/ and carry the command, exit code, "
                 "commit and date of the run", size=11, fill=MUTED)
    return svg(1000, top + index * (height + gap) + 30, body,
               "PC5 evidence ladder",
               "Six rows from host tests to genuine Sony system software. The first four "
               "have recorded passes, real OVMF is not tested here, physical boot has no "
               "record, and Sony software execution is blocked.")


def record_hint(records: dict, gate: str) -> str:
    record = records.get(gate)
    if not record:
        return "no record has been committed for this gate"
    checks = record.get("checks") or {}
    detail = f" — {checks['passed']}/{checks['total']} checks" if checks else ""
    return (f"{record.get('command', gate)} at {record.get('commit', '')[:7]}"
            f" on {record.get('date', '')}{detail}")


# ---------------------------------------------------------------- progress matrix
#
# A dense, treemap-style status board in the same idiom as the figures above, but
# counting more of the repository.  The rule that governs every cell: a cell is
# green only when a *file in this repository* records the thing as done — a
# milestone state of `complete`, a validation record with `passed`, a deliverable
# path that exists, an operation the doubles implement.  Nothing here is inferred
# from a neighbour: documentation progress, synthetic-test coverage, host and
# emulator validation, firmware-backed validation, physical-hardware validation
# and genuine target-system-software execution are separate regions with separate
# denominators, and the last two have no green cells because the repository holds
# no evidence that could put one there.

CELL = "#2b3646"          # gray: not started, unknown, unverified
CELL_DIM = "#1b2330"      # darker gray: nothing to count at all
CELL_STROKE = BG          # the thin separator between cells is the background
GREEN_INK = "#06251c"     # text on a green cell
AMBER_INK = "#2a1d02"     # text on an amber cell
CYAN_INK = "#04222f"

#: fill, ink, extra stroke and a label suffix per milestone state.
MATRIX_STATE = {
    "complete": (GREEN, GREEN_INK, None, ""),
    "in_progress": (CYAN, CYAN_INK, None, ""),
    "not_started": (CELL, INK, None, ""),
    "blocked": (CELL, "#f4b9bd", RED, " !"),
}

MATRIX_CLASSIFICATION = {
    "PUBLIC": (GREEN, GREEN_INK, None),
    "INFERRED": (CELL, INK, CYAN),
    "UNKNOWN": (CELL_DIM, MUTED, None),
    "RESTRICTED": (AMBER, AMBER_INK, None),
}

#: the level statements, so the ladder in the figure and the block say the same thing
LEVEL_SHORT = {
    1: "boots on qualified PC hardware",
    2: "controls its own hardware",
    3: "core platform services work",
    4: "PS5 interfaces validated",
    5: "genuine system software",
    6: "platform experience",
}

PATH_SHAPED = re.compile(r"^[a-z0-9_][a-z0-9_.-]*/[^\s]+\.(?:md|json|py|c|h|sh|yml)$")


#: `M1` < `M1a` < `M1b` < `M2` < `M10`: milestone ids are read as a number and a tail.
MILESTONE_ID = re.compile(r"^(\D*?)(\d+)(.*)$")


def milestone_sort_key(milestone_id: str):
    match = MILESTONE_ID.match(milestone_id)
    if not match:
        return (milestone_id, 0, "")
    prefix, digits, tail = match.groups()
    return (prefix, int(digits), tail)


def pct(numerator: int, denominator: int) -> str:
    """One decimal, always with the fraction it came from."""
    if not denominator:
        return "n/a (0/0)"
    return f"{100.0 * numerator / denominator:.1f}% ({numerator}/{denominator})"


def progress_metrics(data, records=None) -> dict:
    """Every number the matrix draws, computed from the files that own it.

    Single source for the figure and for the README block, so the two cannot
    disagree.  Nothing is cached, dated or environment-dependent: the same
    `roadmap.json` and the same committed records give the same dict.
    """
    records = load_records() if records is None else records
    milestones = data["milestones"]
    by_id = {m["id"]: m for m in milestones}
    states = distribution(data)

    acceptance_total = sum(len(m.get("acceptance") or []) for m in milestones)
    acceptance_by_state = {state: 0 for state in MATRIX_STATE}
    for m in milestones:
        acceptance_by_state[m["state"]] = acceptance_by_state.get(m["state"], 0) + len(
            m.get("acceptance") or [])

    deliverables = [x for m in milestones for x in (m.get("deliverables") or [])]
    path_shaped = [x for x in deliverables if PATH_SHAPED.match(x)]
    existing = [x for x in path_shaped if (ROOT / x).exists()]

    evidence_entries = [e for m in milestones for e in (m.get("evidence") or [])]
    evidence_present = [e for e in evidence_entries if (ROOT / e.split("#")[0]).exists()]
    # A path in `evidence` is a citation the roadmap makes; `docs/evidence/` is where a file
    # that *records a run* is filed.  Counting them separately is the difference between
    # "someone wrote this down" and "someone ran this".
    filed = [e for e in evidence_entries if e.split("#")[0].startswith("docs/evidence/")]
    milestones_filed = sum(1 for m in milestones
                           if any(x.split("#")[0].startswith("docs/evidence/")
                                  for x in (m.get("evidence") or [])))

    levels = {}
    for level in sorted({m["level"] for m in milestones}):
        rows = [m for m in milestones if m["level"] == level]
        levels[level] = {
            "total": len(rows),
            "complete": sum(1 for m in rows if m["state"] == "complete"),
            "ids": [m["id"] for m in sorted(rows, key=lambda m: milestone_sort_key(m["id"]))],
            "states": {m["id"]: m["state"] for m in rows},
        }

    streams = []
    for stream in data["workstreams"]:
        rows = [m for m in milestones if m.get("workstream") == stream["id"]]
        counts = {state: sum(1 for m in rows if m["state"] == state)
                  for state in MATRIX_STATE}
        streams.append({"id": stream["id"], "name": stream["name"], "total": len(rows),
                        "complete": counts["complete"], "counts": counts})

    blocked = []
    for m in sorted([m for m in milestones if m["state"] == "blocked"],
                    key=lambda m: milestone_sort_key(m["id"])):
        unmet = []
        for dep in m.get("depends_on") or []:
            state = by_id.get(dep, {}).get("state")
            if state and state != "complete":
                unmet.append((dep, state))
        blocked.append({"id": m["id"], "title": m["title"], "unmet": unmet,
                        "level": m["level"]})

    gates = sorted(records)
    gate_passed = sum(1 for g in gates if records[g].get("result") == "passed")

    contract = contract_inventory()
    claims = claim_inventory()

    return {
        "milestone_total": len(milestones),
        "states": {state: states.get(state, 0) for state in MATRIX_STATE},
        "acceptance_total": acceptance_total,
        "acceptance_by_state": acceptance_by_state,
        "deliverable_total": len(deliverables),
        "deliverable_path_shaped": len(path_shaped),
        "deliverable_existing": len(existing),
        "evidence_total": len(evidence_entries),
        "evidence_present": len(evidence_present),
        "filed_record_paths": len(filed),
        "milestones_with_filed_record": milestones_filed,
        "milestones_with_evidence": sum(1 for m in milestones if m.get("evidence")),
        "levels": levels,
        "streams": streams,
        "blocked": blocked,
        "gates": gates,
        "gate_records": {g: records[g].get("result", "error") for g in gates},
        "gate_commits": {g: str(records[g].get("commit", ""))[:7] for g in gates},
        "gate_passed": gate_passed,
        "physical_records": len(physical_records()),
        "destination": data["product"]["destination"]["milestone"],
        "destination_state": by_id[data["product"]["destination"]["milestone"]]["state"],
        "package_note": ("package: LFS pointer only, payload absent"
                         if pup_facts().get("pointer")
                         else "package: the tracked file is not an LFS pointer"),
        "contract": contract,
        "claims": claims,
    }


def contract_inventory() -> dict:
    """Operations per PSM interface, read from the contract's generated tables.

    The tables between the `psm:begin`/`psm:end` markers are rendered from `psm/`
    and `python3 -m psm.render --check` fails if they drift, so reading them here
    is reading the code by way of a mirror the project already verifies.
    """
    path = ROOT / "docs/architecture/PS5_PLATFORM_CONTRACT.md"
    try:
        document = path.read_text(encoding="utf-8")
    except OSError:
        return {"available": False, "interfaces": [], "ops": 0, "unsupported": 0}
    found = []
    for name, block in re.findall(
            r"<!-- psm:begin interfaces-(PSM-[A-Z]+) -->(.*?)<!-- psm:end interfaces-\1 -->",
            document, re.S):
        rows = [line for line in block.splitlines() if line.startswith("| `")]
        unsupported = sum(1 for line in rows if "UNSUPPORTED" in line)
        found.append({"name": name, "ops": len(rows), "unsupported": unsupported})
    return {"available": bool(found),
            "interfaces": sorted(found, key=lambda row: row["name"]),
            "ops": sum(row["ops"] for row in found),
            "unsupported": sum(row["unsupported"] for row in found)}


def claim_inventory() -> dict:
    """The classification of every claim in the platform-contract registry."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from psm import registry  # noqa: WPS433 -- same repository, no third party
        rows = [(claim.id, str(getattr(claim, "classification", "")).split(".")[-1])
                for claim in registry.CONTRACT.claims]
    except Exception:                                    # pragma: no cover
        return {"available": False, "total": 0, "counts": {}, "ids": []}
    counts: dict = {}
    for _cid, classification in rows:
        counts[classification] = counts.get(classification, 0) + 1
    return {"available": True, "total": len(rows), "counts": counts,
            "ids": [{"id": cid, "classification": classification}
                    for cid, classification in sorted(rows, key=lambda r: milestone_sort_key(r[0]))]}


def region_head(x, y, w, title, right="", caption="", colour=INK):
    """A region's title on the left, its figure on the right, and the two cannot collide.

    Both are 13px bold, so they share one column: the right-hand figure is measured first and
    the title is given whatever room is left, minus HEAD_GAP.  Measuring each label against the
    canvas on its own (which is all `overflow` did) let a 45-character title and a 26-character
    figure both "fit" a 564px column and overlap by four pixels of ink in the middle.
    """
    right_room = measure(right, 13) + HEAD_GAP if right else 0.0
    shown = fit(title, w - right_room, 13) if right else title
    body = text(x, y + 15, shown, size=13, weight="bold")
    if right:
        body += text(x + w, y + 15, right, size=13, weight="bold", fill=colour,
                     anchor="end")
    if caption:
        body += text(x + w, y + 31, fit(caption, w, 10), size=10, fill=MUTED, anchor="end")
    return body, y + 40


def matrix_cell(x, y, w, h, label, fill=CELL, ink=INK, stroke=None, size=11):
    outline = stroke or CELL_STROKE
    body = (f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{fill}" '
            f'stroke="{outline}" stroke-width="{2 if stroke else 1}"/>\n')
    body += text(x + w / 2, y + h / 2 + size * 0.36, label, size=size, fill=ink,
                 anchor="middle")
    return body


def matrix_row(x, y, w, h, parts, gap=3):
    """A 100%-width stacked bar whose segment widths are the counts, exactly.

    Integer geometry with the remainder carried forward, so the segments always
    sum to `w` and a bar of the same data is byte-identical on any machine.
    """
    total = sum(count for count, _colour in parts)
    if not total:
        return (f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" '
                f'fill="{CELL_DIM}" stroke="{CELL_STROKE}"/>\n')
    drawn = [(count, colour) for count, colour in parts if count]
    body = ""
    used = 0
    cursor = x
    for index, (count, colour) in enumerate(drawn):
        last = index == len(drawn) - 1
        share = w * count / total
        width = (w - used) if last else max(1, int(round(share)))
        if last and width <= 0:
            continue
        body += (f'  <rect x="{cursor}" y="{y}" width="{width}" height="{h}" rx="3" '
                 f'fill="{colour}" stroke="{CELL_STROKE}"/>\n')
        cursor += width + (0 if last else gap)
        used += width + (0 if last else gap)
    return body


def progress_matrix(data, records) -> str:
    """The dense status board: one region per countable question, one cell per item.

    Geometry is integer and the drawing is two columns of regions whose height is
    computed from the content, so the canvas has no dead band at the bottom.
    """
    metrics = progress_metrics(data, records)
    left_x, right_x, col_w = 24, 612, 564
    body = text(24, 34, "PC5 PROGRESS MATRIX", size=20, weight="bold")
    body += text(24, 54, "every figure is a count over a named set in this repository; "
                         "a green cell means a file records it, never that a machine ran it",
                 size=11, fill=MUTED)
    body += text(1176, 34, "source: docs/architecture/roadmap.json", size=11, fill=MUTED,
                 anchor="end")

    # ---------------------------------------------------------- milestones by state
    y = 76
    head, y = region_head(
        left_x, y, col_w, "MILESTONES BY STATE",
        right=pct(metrics["states"]["complete"], metrics["milestone_total"]),
        caption="green verified · cyan in progress · gray not started · outlined blocked",
        colour=GREEN)
    body += head
    cells = []
    for milestone in sorted(data["milestones"],
                            key=lambda m: (STATE_ORDER.get(m["state"], 9),
                                           milestone_sort_key(m["id"]))):
        cells.append((milestone["id"] + MATRIX_STATE[milestone["state"]][3],
                      milestone["state"]))
    cols = 7
    width = (col_w - 3 * (cols - 1)) // cols
    for index, (label, state) in enumerate(cells):
        fill, ink, stroke, _suffix = MATRIX_STATE[state]
        body += matrix_cell(left_x + (index % cols) * (width + 3), y + (index // cols) * 29,
                            width, 26, label, fill=fill, ink=ink, stroke=stroke)
    y += ((len(cells) - 1) // cols + 1) * 29 + 16

    # ---------------------------------------------------------- the evidence ladder
    head, y = region_head(
        left_x, y, col_w, "EVIDENCE LADDER — ONE ROW PER LEVEL",
        right=f"{len([1 for level in metrics['levels'] if metrics['levels'][level]['complete']])}"
              f"/{len(metrics['levels'])} levels verified",
        caption="a level is reached by its own evidence; the two top levels have none")
    body += head
    for level in sorted(metrics["levels"]):
        info = metrics["levels"][level]
        body += text(left_x, y + 15, f"L{level}", size=11, weight="bold",
                     fill=INK if info["complete"] else MUTED)
        body += text(left_x + 32, y + 15,
                 fit(LEVEL_SHORT.get(level, ""), 248 - 32 - 12, 10),
                 size=10, fill=MUTED)
        area = col_w - 248 - 44
        cell_w = min(30, int(area / max(1, info["total"])))
        for index, milestone_id in enumerate(info["ids"]):
            fill, ink, stroke, suffix = MATRIX_STATE[info["states"][milestone_id]]
            body += matrix_cell(left_x + 248 + index * (cell_w + 3), y, cell_w, 22,
                                milestone_id.replace("M", "") + suffix, fill=fill, ink=ink,
                                stroke=stroke, size=10)
        body += text(left_x + col_w, y + 15,
                     f"{info['complete']}/{info['total']}", size=10, anchor="end",
                     fill=GREEN if info["complete"] else MUTED)
        y += 27
    y += 16

    # ------------------------------------- requirements, deliverables, filed evidence
    head, y = region_head(
        left_x, y, col_w, "REQUIREMENTS, DELIVERABLES, CITED PATHS",
        right=f"{metrics['deliverable_existing']}/{metrics['deliverable_total']} deliverables "
              f"present", caption="bar segments are the counts named at the right of each row")
    body += head
    acceptance = metrics["acceptance_by_state"]
    parts = [(acceptance.get(state, 0), MATRIX_STATE[state][0]) for state in MATRIX_STATE]
    body += text(left_x, y + 12, "acceptance items, by the state of the owning milestone",
                 size=10, fill=MUTED)
    body += text(left_x + col_w, y + 12,
                 f"{acceptance.get('complete', 0)}/{metrics['acceptance_total']} verified",
                 size=10, fill=GREEN, anchor="end")
    body += matrix_row(left_x, y + 18, col_w, 16, parts)
    y += 44
    rows = [
        ("deliverables named as a path that exists in this checkout",
         [(metrics["deliverable_existing"], GREEN),
          (metrics["deliverable_path_shaped"] - metrics["deliverable_existing"], CELL),
          (metrics["deliverable_total"] - metrics["deliverable_path_shaped"], CELL_DIM)],
         f"{metrics['deliverable_existing']}/{metrics['deliverable_total']}"),
        ("deliverables named at all (the rest are prose descriptions)",
         [(metrics["deliverable_path_shaped"], CYAN),
          (metrics["deliverable_total"] - metrics["deliverable_path_shaped"], CELL_DIM)],
         f"{metrics['deliverable_path_shaped']}/{metrics['deliverable_total']}"),
        ("cited paths that resolve to a file in this checkout",
         [(metrics["evidence_present"], CYAN),
          (metrics["evidence_total"] - metrics["evidence_present"], CELL)],
         f"{metrics['evidence_present']}/{metrics['evidence_total']}"),
        ("milestones citing at least one in-tree path",
         [(metrics["milestones_with_evidence"], CYAN),
          (metrics["milestone_total"] - metrics["milestones_with_evidence"], CELL)],
         f"{metrics['milestones_with_evidence']}/{metrics['milestone_total']}"),
        ("milestones with a record filed under docs/evidence/",
         [(metrics["milestones_with_filed_record"], GREEN),
          (metrics["milestone_total"] - metrics["milestones_with_filed_record"], CELL)],
         f"{metrics['milestones_with_filed_record']}/{metrics['milestone_total']}"),
    ]
    for label, bar, count in rows:
        body += text(left_x, y + 11, fit(label, col_w - measure(count, 10) - 12, 10),
                     size=10, fill=MUTED)
        body += text(left_x + col_w, y + 11, count, size=10, anchor="end", fill=INK)
        body += matrix_row(left_x, y + 16, col_w, 10, bar)
        y += 34
    body += text(left_x, y + 8,
                 fit("a citation names a path; a record proves a run — the last row "
                     "counts records only", col_w, 10),
                 size=10, fill=MUTED)
    y += 20

    # --------------------------------------------------------------- the workstreams
    head, y = region_head(left_x, y, col_w, "WORKSTREAMS",
                          right=f"{len([s for s in metrics['streams'] if not s['complete']])}"
                                f"/{len(metrics['streams'])} with nothing verified",
                          caption="one cell per milestone, so a one-milestone stream is one cell")
    body += head
    for stream in metrics["streams"]:
        rows_in_stream = [m for m in data["milestones"]
                          if m.get("workstream") == stream["id"]]
        rows_in_stream.sort(key=lambda m: milestone_sort_key(m["id"]))
        cell_w, cell_gap = 16, 3
        used = len(rows_in_stream) * (cell_w + cell_gap) - cell_gap
        start = left_x + col_w - 44 - max(used, 0)
        room = start - (left_x + 42) - 12
        name_lines = fit_lines(stream["name"], room, 10)
        body += text(left_x, y + 13, stream["id"], size=10, weight="bold",
                     fill=INK if stream["complete"] else MUTED)
        for offset, line in enumerate(name_lines):
            body += text(left_x + 42, y + 13 + offset * 15, line, size=10, fill=MUTED)
        for index, milestone in enumerate(rows_in_stream):
            state = milestone["state"]
            fill, ink, stroke, _suffix = MATRIX_STATE[state]
            body += matrix_cell(start + index * (cell_w + cell_gap), y, cell_w, 20,
                                "", fill=fill, ink=ink, stroke=stroke)
        body += text(left_x + col_w, y + 13, f"{stream['complete']}/{stream['total']}",
                     size=10, anchor="end", fill=GREEN if stream["complete"] else MUTED)
        y += 24 if len(name_lines) == 1 else 40
    left_bottom = y

    # ------------------------------------------------- right column: claim classes
    y = 76
    claims = metrics["claims"]
    if claims["available"]:
        counts = claims["counts"]
        public = counts.get("PUBLIC", 0)
        head, y = region_head(right_x, y, col_w, "PLATFORM-CONTRACT CLAIMS ABOUT THE CONSOLE",
                              right=pct(public, claims["total"]),
                              caption=f"green public · outlined inferred · dim unknown · "
                                      f"amber restricted ({counts.get('RESTRICTED', 0)})",
                              colour=CYAN)
        body += head
        cols = 10
        width = (col_w - 3 * (cols - 1)) // cols
        cells = claims["ids"]
        for index, entry in enumerate(cells):
            fill, ink, stroke = MATRIX_CLASSIFICATION.get(
                entry["classification"], (CELL, INK, None))
            short = entry["id"].replace("C-", "")
            body += matrix_cell(right_x + (index % cols) * (width + 3),
                                y + (index // cols) * 25, width, 22, short,
                                fill=fill, ink=ink, stroke=stroke, size=10)
        y += ((len(cells) - 1) // cols + 1) * 25 + 8
        body += text(right_x, y + 10, "the class says how well a statement is documented, "
                                       "not whether PC5 built anything",
                     size=10, fill=MUTED)
        y += 26

    # ------------------------------------------------------ modelled operations (PSM)
    contract = metrics["contract"]
    if contract["available"]:
        implemented = contract["ops"] - contract["unsupported"]
        head, y = region_head(right_x, y, col_w, "PLATFORM-MODEL OPERATIONS (PSM)",
                              right=pct(implemented, contract["ops"]),
                              caption="an implemented op is PC5's own double; amber is "
                                      "withheld by design", colour=GREEN)
        body += head
        bar_x = right_x + 214
        bar_w = col_w - 214 - 74
        for row in contract["interfaces"]:
            done = row["ops"] - row["unsupported"]
            body += text(right_x, y + 11, row["name"], size=10, weight="bold", fill=INK)
            body += text(right_x + 84, y + 11, f"{done}/{row['ops']} implemented", size=10,
                         fill=MUTED)
            body += matrix_row(bar_x, y + 3, bar_w, 14, [(done, GREEN),
                                                          (row["unsupported"], AMBER)])
            body += text(right_x + col_w, y + 11,
                         f"{row['unsupported']} withheld" if row["unsupported"] else "—",
                         size=10, anchor="end",
                         fill=AMBER if row["unsupported"] else MUTED)
            y += 24
        body += text(right_x, y + 10,
                     f"{len(contract['interfaces'])} interfaces · {contract['ops']} operations · "
                     f"{contract['unsupported']} refused by design",
                     size=10, fill=MUTED)
        y += 26

    # ------------------------------------------------------------------- blocked rows
    blocked = metrics["blocked"]
    head, y = region_head(right_x, y, col_w, "BLOCKED, AND WHAT IT IS BLOCKED ON",
                          right=f"{len(blocked)}/{metrics['milestone_total']} milestones",
                          caption="a dependency that cannot be met here, not work in progress",
                          colour=AMBER)
    body += head
    for row in blocked:
        body += (f'  <rect x="{right_x - 8}" y="{y}" width="4" height="24" rx="2" '
                 f'fill="{AMBER}"/>\n')
        body += text(right_x, y + 11, row["id"], size=10, weight="bold", fill=INK)
        row_room = col_w - 38 - measure(f"L{row['level']}", 10) - 12
        body += text(right_x + 38, y + 11, fit(row["title"], row_room, 10), size=10,
                     fill=MUTED)
        deps = (", ".join(f"{dep} {state.replace('_', ' ')}" for dep, state in row["unmet"])
                if row["unmet"] else "the blocker itself is the reason")
        body += text(right_x + 38, y + 23, fit(f"blocked on: {deps}", row_room, 10),
                     size=10, fill="#f4b9bd")
        body += text(right_x + col_w, y + 11, f"L{row['level']}", size=10, anchor="end",
                     fill=MUTED)
        y += 32
    y += 10

    # ------------------------------------------- committed validation records + rungs
    gates = metrics["gates"]
    passed = metrics["gate_passed"]
    head, y = region_head(right_x, y, col_w, "COMMITTED VALIDATION RECORDS",
                          right=pct(passed, len(gates)) if gates else "no records",
                          caption="one cell per gate: its result, and the commit it ran at",
                          colour=GREEN if gates else AMBER)
    body += head
    result_style = {"passed": (GREEN, GREEN_INK), "not-tested": (AMBER, AMBER_INK),
                    "skipped": (AMBER, AMBER_INK), "failed": (RED, INK),
                    "error": (RED, INK)}
    cols = 4
    width = (col_w - 3 * (cols - 1)) // cols
    for index, gate in enumerate(gates):
        result = metrics["gate_records"][gate]
        fill, ink = result_style.get(result, (CELL, INK))
        cell_x = right_x + (index % cols) * (width + 3)
        cell_y = y + (index // cols) * 37
        body += (f'  <rect x="{cell_x}" y="{cell_y}" width="{width}" height="34" rx="3" '
                 f'fill="{fill}" stroke="{CELL_STROKE}"/>\n')
        body += text(cell_x + width / 2, cell_y + 14, fit(gate, width - 8, 10), size=10,
                     fill=ink, weight="bold", anchor="middle")
        body += text(cell_x + width / 2, cell_y + 27, metrics["gate_commits"][gate],
                     size=10, fill=ink, anchor="middle")
    y += ((max(0, len(gates) - 1) // cols) + 1) * 37 + 4
    for label, value, note, colour in [
            ("physical PC boot",
             f"{metrics['physical_records']} record(s)" if metrics["physical_records"]
             else "none filed",
             "a record from a real machine", AMBER),
            (f"genuine Sony system software ({metrics['destination']})",
             STATE_LABEL[metrics["destination_state"]].upper(),
             "not executed, therefore not counted", RED)]:
        value_room = measure(value, 10) + 14
        note_x = right_x + 232 + value_room
        body += text(right_x, y + 12, fit(label, 232 - 12, 10), size=10, fill=INK)
        body += text(right_x + 232, y + 12, value, size=10, weight="bold", fill=colour)
        body += text(note_x, y + 12, fit(note, right_x + col_w - note_x - 4, 10), size=10,
                     fill=MUTED)
        y += 20
    right_bottom = y

    # ------------------------------------------------------------------- legend/footer
    bottom = max(left_bottom, right_bottom) + 4
    body += f'  <line x1="24" y1="{bottom}" x2="1176" y2="{bottom}" stroke="{LINE}"/>\n'
    legend = [("verified by a file in this repository", GREEN),
              ("in progress", CYAN),
              ("not started, unknown or unverified", CELL),
              ("withheld by design or awaiting a machine", AMBER),
              ("blocked on a dependency", CELL)]
    x = 24
    for label, colour in legend:
        body += (f'  <rect x="{x}" y="{bottom + 12}" width="12" height="12" rx="3" '
                 f'fill="{colour}" stroke="{LINE}"/>\n')
        body += text(x + 18, bottom + 22, label, size=10, fill=MUTED)
        x += 24 + int(measure(label, 10)) + 22
    body += text(24, bottom + 44, "counted from roadmap.json, the contract and the committed "
                                  "records — not from a booted machine, not from a PS5, and "
                                  "not from an open pull request", size=10, fill=MUTED)
    body += text(1176, bottom + 44, metrics["package_note"], size=10,
                 fill=MUTED, anchor="end")
    height = bottom + 58
    return svg(1200, height, body, "PC5 progress matrix",
               f"milestones complete {metrics['states']['complete']} of "
               f"{metrics['milestone_total']}; levels 1 and 2 hold the only verified "
               f"milestones, levels 3 to 6 none; deliverables present "
               f"{metrics['deliverable_existing']} of {metrics['deliverable_total']}; "
               f"{metrics['milestones_with_filed_record']} of {metrics['milestone_total']} "
               f"milestones have a record filed under docs/evidence/, and "
               f"{metrics['milestones_with_evidence']} merely cite a path that exists; "
               f"{len(metrics['blocked'])} milestones blocked; validation records "
               f"{metrics['gate_passed']} of {len(metrics['gates'])} passed; physical boot "
               f"and genuine system-software execution carry no evidence.")


def matrix_markdown(data, records) -> str:
    """The same numbers as text, so the README works with images switched off."""
    metrics = progress_metrics(data, records)
    states = metrics["states"]
    rows = [
        ("Milestones verified", pct(states["complete"], metrics["milestone_total"]),
         "milestone `state` in `roadmap.json`"),
        ("Levels with a verified milestone",
         f"{len([1 for level in metrics['levels'] if metrics['levels'][level]['complete']])}"
         f"/{len(metrics['levels'])}",
         "milestones grouped by their `level` field"),
        ("Acceptance items inside verified milestones",
         f"{metrics['acceptance_by_state'].get('complete', 0)}/{metrics['acceptance_total']}",
         "`acceptance` arrays, counted per owning milestone state"),
        ("Deliverables that exist as named paths",
         f"{metrics['deliverable_existing']}/{metrics['deliverable_total']}",
         "`deliverables` strings matched as a repository path, then checked on disk"),
        ("Milestones citing at least one in-tree path",
         f"{metrics['evidence_present']}/{metrics['evidence_total']} cited paths resolve, in "
         f"{metrics['milestones_with_evidence']}/{metrics['milestone_total']} milestones",
         "`evidence` arrays resolved against the tree — a citation, not a run"),
        ("Milestones with a record filed under `docs/evidence/`",
         f"{metrics['milestones_with_filed_record']}/{metrics['milestone_total']}",
         "`docs/evidence/**` (the directory the milestone graph cites, once, for a record)"),
    ]
    contract = metrics["contract"]
    if contract["available"]:
        implemented = contract["ops"] - contract["unsupported"]
        rows.append(("Modelled operations implemented",
                     pct(implemented, contract["ops"]),
                     "the generated tables in `docs/architecture/PS5_PLATFORM_CONTRACT.md`"))
    claims = metrics["claims"]
    if claims["available"]:
        rows.append(("Contract claims backed by public documentation",
                     pct(claims["counts"].get("PUBLIC", 0), claims["total"]),
                     "`psm/registry.py` classifications, enforced by `psm/contract.py`"))
    if metrics["gates"]:
        rows.append(("Committed validation records passing",
                     pct(metrics["gate_passed"], len(metrics["gates"])),
                     "`docs/evidence/validation/*.json`"))
    rows.append(("Blocked milestones", f"{len(metrics['blocked'])}",
                 "state `blocked` and its `blocker` field"))
    rows.append(("Physical-hardware boot evidence",
                 f"{metrics['physical_records']} record(s) filed",
                 "`docs/evidence/*.json`, validated by `tools/hw_record.py`"))
    rows.append(("Genuine target-system-software execution",
                 f"**{STATE_LABEL[metrics['destination_state']].upper()}** — nothing executed",
                 f"state of `{metrics['destination']}` in `roadmap.json`"))
    lines = ["<!-- pc5:matrix:begin -->",
             "| Progress region | Figure | Where the denominator comes from |",
             "|---|---|---|"]
    for label, figure, source in rows:
        lines.append(f"| {label} | {figure} | {source} |")
    levels = " · ".join(
        f"L{level} {metrics['levels'][level]['complete']}/{metrics['levels'][level]['total']}"
        for level in sorted(metrics["levels"]))
    blocked = ", ".join(f"`{row['id']}`" for row in metrics["blocked"]) or "none"
    lines.append("")
    lines.append(f"Levels: {levels}. Blocked: {blocked}. A cell is green in "
                 "`docs/assets/pc5-progress-matrix.svg` only where a file in this "
                 "repository records the thing as done: documentation progress, "
                 "synthetic-test coverage and host or emulator validation are counted "
                 "separately, and no row above them is inferred from a row below.")
    lines.append("<!-- pc5:matrix:end -->")
    return "\n".join(lines)


# ---------------------------------------------------------------- block plumbing
BLOCK_RENDERERS = {
    "stats": lambda data, records: stats_markdown(data, records),
    "status": lambda data, records: status_markdown(data),
    "evidence": lambda data, records: evidence_markdown(records),
    "blockers": lambda data, records: blockers_markdown(data),
    "pup": lambda data, records: pup_markdown(),
    "matrix": lambda data, records: matrix_markdown(data, records),
}


def block_markers(name: str) -> tuple:
    return f"<!-- pc5:{name}:begin -->", f"<!-- pc5:{name}:end -->"


def apply_block(text: str, name: str, body: str) -> str:
    begin, end = block_markers(name)
    if begin not in text or end not in text:
        raise SystemExit(f"README.md is missing the {begin} … {end} markers")
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return head + body + tail


def render_blocks(data, records) -> dict:
    return {name: renderer(data, records) for name, renderer in BLOCK_RENDERERS.items()}


def readme_with_status(text: str, block: str) -> str:  # kept for callers/tests
    return apply_block(text, "status", block)


STRAY = (
    re.compile(r"\b\d+\s+checks\b", re.I),
    re.compile(r"\b\d+\s+milestones\b", re.I),
    re.compile(r"\b\d+\s+workstreams\b", re.I),
    re.compile(r"\b\d+\s*[- ]byte", re.I),
    re.compile(r"\b\d\.\d+\s*GB\b", re.I),
)


def stray_statistics(readme: str) -> list:
    """Statistics outside a generated block are the ones that go stale."""
    stripped = readme
    for name in BLOCKS:
        begin, end = block_markers(name)
        while begin in stripped and end in stripped:
            head, rest = stripped.split(begin, 1)
            _, tail = rest.split(end, 1)
            stripped = head + tail
    found = []
    for pattern in STRAY:
        for match in pattern.finditer(stripped):
            found.append(f"README.md: {match.group(0)!r} is a statistic outside a generated "
                         f"block — put it in one so it cannot go stale")
    return found

TEXT_ELEMENT = re.compile(
    r'<text x="([\d.]+)" y="[\d.]+"[^>]*font-size="([\d.]+)"[^>]*'
    r'text-anchor="(\w+)"[^>]*>(.*?)</text>', re.S)


TEXT_WITH_POSITION = re.compile(
    r'<text x="([\d.]+)" y="([\d.]+)"[^>]*font-size="([\d.]+)"[^>]*'
    r'text-anchor="(\w+)"[^>]*>(.*?)</text>', re.S)


def collisions(body: str, gap: float = 1.0) -> list:
    """Report labels on one baseline whose measured boxes overlap horizontally.

    `overflow` can only ask whether a label fits the canvas, which is how a region's title and
    its right-aligned figure both passed while sharing four pixels of ink in the middle.  Text
    in these figures is placed by baseline, so two labels within one pixel of each other's y
    are a pair, and the pair either fits together or it does not.
    """
    lines = {}
    for x, y, size, anchor, content in TEXT_WITH_POSITION.findall(body):
        label = re.sub(r"<[^>]+>", "", content)
        if not label.strip():
            continue
        estimate = measure(label, float(size))
        left = float(x)
        if anchor == "end":
            left = float(x) - estimate
        elif anchor == "middle":
            left = float(x) - estimate / 2
        lines.setdefault(round(float(y)), []).append((left, left + estimate, label))
    problems = []
    for _y, items in sorted(lines.items()):
        items.sort()
        for (left, right, label), (next_left, _next_right, next_label) in zip(items, items[1:]):
            if next_left < right - gap:
                problems.append(
                    f"labels on one baseline overlap by {right - next_left:.0f}px: "
                    f"{label[:40]!r} and {next_label[:40]!r}")
    return problems


def overflow(body: str, width: int, margin: int = 12) -> list:
    """Estimate rendered text width and report anything that would leave the canvas.

    Monospace makes this arithmetic rather than guesswork: 0.62 em per character is a
    deliberate over-estimate, so a figure that passes here has room to spare when the
    reader's font is narrower than the estimate.
    """
    problems = []
    for x, size, anchor, content in TEXT_ELEMENT.findall(body):
        label = re.sub(r"<[^>]+>", "", content)
        if not label:
            continue
        estimate = measure(label, float(size))     # the same ruler `fit` laid out with
        left, right = float(x), float(x) + estimate
        if anchor == "middle":
            left, right = float(x) - estimate / 2, float(x) + estimate / 2
        elif anchor == "end":
            left, right = float(x) - estimate, float(x)
        if right > width - margin or left < margin:
            problems.append(f"text runs outside the figure: {label[:48]!r} "
                            f"(x={x}, ~{int(estimate)}px wide)")
    return problems


FIGURES = {
    "pc5-banner.svg": lambda data, records: banner(data),
    "pc5-dashboard.svg": dashboard,
    "pc5-architecture.svg": lambda data, records: architecture(data),
    "pc5-progress.svg": lambda data, records: progress(data),
    "pc5-boot-workflow.svg": lambda data, records: workflow(data),
    "pc5-evidence.svg": evidence_ladder,
    "pc5-progress-matrix.svg": progress_matrix,
}


# ---------------------------------------------------------------- commands
def rendered(data, records=None) -> dict:
    records = load_records() if records is None else records
    return {name: build(data, records) for name, build in FIGURES.items()}


LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")


def local_links(text: str) -> list:
    found = []
    for match in LINK.finditer(text):
        target = match.group(2).split("#", 1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:", "#", "//")):
            continue
        # GitHub resolves `[x](a%20b.md)` to the file `a b.md`; so must this.
        found.append(urllib.parse.unquote(target))
    return found


def _label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _resolves_to_a_file(parent: Path, target: str) -> bool:
    """Whether ``parent / target`` names something on disk.

    ``Path.resolve()`` raises ``ValueError("embedded null byte")`` for a target
    containing NUL, which ``urllib.parse.unquote("a%00b.md")`` produces.  A link
    check must report that as a broken target, not stop with a traceback.  Only
    these two exceptions are caught.
    """
    try:
        return (parent / target).resolve().exists()
    except (ValueError, OSError):
        return False


def link_problems(path: Path, text: str) -> list:
    problems = []
    for target in local_links(text):
        if not _resolves_to_a_file(path.parent, target):
            problems.append(f"{_label(path)}: link target missing: {target}")
    for match in IMAGE.finditer(text):
        alt, target = match.group(1).strip(), match.group(2)
        if not alt:
            problems.append(f"{_label(path)}: image has no alt text: {target}")
    return problems


#: Claims the presentation layer must never make. Each entry is (pattern, why).
FORBIDDEN = (
    ("runs ps5 games", "game compatibility is not claimed; the destination is the system software"),
    ("boots on any pc", "the qualification list is finite and no universal support is claimed"),
    ("secure boot supported", "PC5 ships no signature; Secure Boot blocks the image"),
    ("ci passing", "the pipeline definition is not an activated workflow"),
    ("ps5 firmware included", "no Sony payload is in the tree; the PUP is an LFS pointer"),
    ("production ready", "no physical boot has happened"),
    ("fully compatible", "no compatibility matrix has been executed on hardware"),
)


def check(data, records=None) -> list:
    problems = []
    records = load_records() if records is None else records

    expected = rendered(data, records)
    for name, body in expected.items():
        path = ASSETS / name
        if not path.exists():
            problems.append(f"docs/assets/{name}: missing — run `make presentation`")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != body:
            problems.append(f"docs/assets/{name}: stale — regenerate with `make presentation`")
        if "<title" not in actual or "<desc" not in actual:
            problems.append(f"docs/assets/{name}: no accessible title/description")
        measured = re.search(r'width="(\d+)"', actual)
        if measured:  # an unreadable figure is already reported as stale above
            problems += [f"docs/assets/{name}: {problem}"
                         for problem in overflow(actual, int(measured.group(1)))]
            problems += [f"docs/assets/{name}: {problem}"
                         for problem in collisions(actual)]

    readme = README.read_text(encoding="utf-8")
    for name, body in render_blocks(data, records).items():
        begin, end = block_markers(name)
        if begin not in readme or end not in readme:
            problems.append(f"README.md: the {name} block has lost its markers")
            continue
        if apply_block(readme, name, body) != readme:
            problems.append(f"README.md: the generated {name} block does not match the "
                            f"data — run `make presentation`")

    for document in (README, ROOT / "docs/PRESENTATION.md"):
        if document.exists():
            problems += link_problems(document, document.read_text(encoding="utf-8"))

    lowered = " ".join(readme.lower().split())
    for pattern, why in FORBIDDEN:
        if pattern in lowered:
            problems.append(f"README.md: claim {pattern!r} is not true ({why})")
    for token in ("genuine Sony PlayStation 5 system software", "NOT TESTED",
                  "BLOCKED", "Git LFS pointer"):
        if token.lower() not in lowered:
            problems.append(f"README.md: the honesty token {token!r} is missing")

    for name in expected:
        if f"docs/assets/{name}" not in readme:
            problems.append(f"README.md does not embed docs/assets/{name}")

    problems += stray_statistics(readme)
    # the records the dashboard quotes have to be readable and internally honest
    sys.path.insert(0, str(ROOT / "tools"))
    import validation  # noqa: WPS433 -- same-directory sibling, imported on demand
    problems += [f"docs/evidence/validation: {problem}"
                 for problem in validation.problems(list(records.values()))]
    return problems


def write(data, records=None) -> list:
    records = load_records() if records is None else records
    ASSETS.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in rendered(data, records).items():
        path = ASSETS / name
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
            written.append(_label(path))
    readme = README.read_text(encoding="utf-8")
    updated = readme
    for name, body in render_blocks(data, records).items():
        updated = apply_block(updated, name, body)
    if updated != readme:
        README.write_text(updated, encoding="utf-8")
        written.append("README.md")
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate and check PC5's presentation layer.")
    parser.add_argument("command", choices=("sync", "check"), nargs="?", default="check")
    args = parser.parse_args(argv)
    data = load()
    records = load_records()
    if args.command == "sync":
        for path in write(data, records):
            print(f"updated {path}")
        print(f"presentation: {len(FIGURES)} figures and {len(BLOCKS)} README blocks are current")
        return 0
    problems = check(data, records)
    for problem in problems:
        print(f"[FAIL] {problem}")
    if problems:
        print(f"{len(problems)} presentation problem(s)")
        return 1
    rows = status_rows(data)
    print(f"PRESENTATION OK: {len(FIGURES)} figures and {len(BLOCKS)} blocks current, "
          f"{len(rows)} status rows match roadmap.json, "
          f"{len(records)} validation record(s) read, links and alt text resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
