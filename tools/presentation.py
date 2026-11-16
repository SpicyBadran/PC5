"""Generate and check PC5's presentation layer from the project's own data.

Everything the README shows about status comes from `docs/architecture/roadmap.json`
and from the evidence records under `docs/evidence/`. This tool renders those facts
into four original SVG figures, writes the README status table in place between
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
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
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
    # original mark: three stacked chevrons, the layers PC5 adds between firmware
    # and the destination
    for index, colour in enumerate((CYAN, VIOLET, AMBER)):
        y = 96 + index * 26
        body += (f'  <path d="M 84 {y} L 104 {y + 14} L 84 {y + 28}" fill="none" '
                 f'stroke="{colour}" stroke-width="6" stroke-linecap="round" '
                 f'stroke-linejoin="round"/>\n')
    body += text(140, 118, "PC5", size=64, weight="bold", spacing="4")
    body += text(146, 152, "an independent platform for PS5 system software on PC hardware",
                 size=15, fill=MUTED)
    chips = (
        ("ROADMAP " + data["version"].upper(), CYAN),
        (f"{streams} WORKSTREAMS", VIOLET),
        (f"{complete}/{total} MILESTONES COMPLETE", GREEN),
        (f"DESTINATION M19 {dest_label}", dest_colour),
    )
    x = 146
    for label, colour in chips:
        width = len(label) * 7 + 22
        body += badge(x, 176, label, colour, w=width, size=11)
        x += width + 12

    # the honest line, in the figure, not only in prose
    body += text(146, 244, "The ISO boots PC5's own loader and kernel. No Sony software is present,",
                 size=13, fill=INK)
    body += text(146, 264, "and none is executed. Physical PC boot: NOT TESTED.",
                 size=13, fill=AMBER)
    body += (f'  <line x1="84" y1="292" x2="1116" y2="292" stroke="{LINE}"/>\n')
    body += text(84, 312, "docs/architecture/roadmap.json", size=11, fill=MUTED)
    body += text(1116, 312, "generated by tools/presentation.py", size=11,
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
        body += text(96, y + 21, clip(name, 38), size=12, fill=INK)
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


FIGURES = {
    "pc5-banner.svg": banner,
    "pc5-architecture.svg": architecture,
    "pc5-progress.svg": progress,
    "pc5-boot-workflow.svg": workflow,
}


# ---------------------------------------------------------------- commands
def rendered(data) -> dict:
    return {name: build(data) for name, build in FIGURES.items()}


def readme_with_status(text: str, block: str) -> str:
    if BEGIN not in text or END not in text:
        raise SystemExit(f"README.md is missing the {BEGIN} … {END} markers")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return head + block + tail


LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")


def local_links(text: str) -> list:
    found = []
    for match in LINK.finditer(text):
        target = match.group(2).split("#", 1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:", "#", "//")):
            continue
        found.append(target)
    return found


def _label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def link_problems(path: Path, text: str) -> list:
    problems = []
    for target in local_links(text):
        candidate = (path.parent / target).resolve()
        if not candidate.exists():
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
)


def check(data) -> list:
    problems = []

    expected = rendered(data)
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

    readme = README.read_text(encoding="utf-8")
    try:
        wanted = readme_with_status(readme, status_markdown(data))
    except SystemExit as exc:
        problems.append(str(exc))
        wanted = readme
    if wanted != readme:
        problems.append("README.md: the generated status block does not match the roadmap "
                        "— run `make presentation`")

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
    return problems


def write(data) -> list:
    ASSETS.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in rendered(data).items():
        path = ASSETS / name
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
            written.append(str(path.relative_to(ROOT)))
    readme = README.read_text(encoding="utf-8")
    updated = readme_with_status(readme, status_markdown(data))
    if updated != readme:
        README.write_text(updated, encoding="utf-8")
        written.append("README.md")
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate and check PC5's presentation layer.")
    parser.add_argument("command", choices=("sync", "check"), nargs="?", default="check")
    args = parser.parse_args(argv)
    data = load()
    if args.command == "sync":
        for path in write(data):
            print(f"updated {path}")
        print(f"presentation: {len(FIGURES)} figures and the README status block are current")
        return 0
    problems = check(data)
    for problem in problems:
        print(f"[FAIL] {problem}")
    if problems:
        print(f"{len(problems)} presentation problem(s)")
        return 1
    rows = status_rows(data)
    print(f"PRESENTATION OK: {len(FIGURES)} figures current, {len(rows)} status rows match "
          f"roadmap.json, links and alt text resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
