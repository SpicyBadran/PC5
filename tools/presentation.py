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
import math
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
import completion  # noqa: E402 -- same-directory sibling, the progress arithmetic

ROADMAP = ROOT / "docs/architecture/roadmap.json"
README = ROOT / "README.md"
ABOUT = ROOT / "docs/ABOUT.md"
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


# ---------------------------------------------------------------- animation
#: Every animation in PC5's figures is a one-shot entrance that freezes on its true
#: value.  That is a correctness rule, not a taste rule: an SVG used as an image runs
#: in the browser's "secure animated mode", where declarative animation plays but
#: scripts do not, and where a renderer that ignores animation shows the *static*
#: attribute.  Drawing the base attribute at the final value therefore means the
#: figure tells the truth whether or not the reader's client animates it, and a
#: looping animation would be motion that carries no information, which the figures
#: here have never done.  `tests/test_completion.py` pins the rule.
ANIM_DUR = "1.15s"
EASE = ' keyTimes="0;1" keySplines="0.16 1 0.3 1" calcMode="spline"'


def animate(attr: str, final: str, start: str, delay: str = "0s",
            dur: str = ANIM_DUR) -> str:
    """A child element that draws ``attr`` in from ``start`` and freezes at ``final``."""
    return (f'<animate attributeName="{attr}" from="{start}" to="{final}" '
            f'begin="{delay}" dur="{dur}" fill="freeze"{EASE}/>')


def fade(body: str, delay: str = "0s", dur: str = "0.5s") -> str:
    """Wrap generated markup in a one-shot fade.  ``opacity`` stays at 1 either way."""
    return (f'  <g opacity="1">{body}{animate("opacity", "1", "0", delay, dur)}</g>\n')


def arc(cx: float, cy: float, radius: float, fraction: float, colour: str,
         thickness: float = 26.0) -> str:
    """A ring drawn clockwise from twelve o'clock, animated by ``stroke-dashoffset``.

    ``stroke-dashoffset`` is written at its final value first: the animation only
    tweens toward it.  A fraction of 0 renders as an empty track, never as a full one.
    """
    circumference = 2.0 * math.pi * radius
    offset = circumference * (1.0 - fraction)
    ring = (f'  <circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{LINE}"'
            f' stroke-width="{thickness}" stroke-opacity="0.5"/>\n')
    ring += (f'  <circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{colour}"'
             f' stroke-width="{thickness}" stroke-linecap="butt"'
             f' stroke-dasharray="{circumference:.2f}" stroke-dashoffset="{offset:.2f}"'
             f' transform="rotate(-90 {cx} {cy})">\n'
             + f'    {animate("stroke-dashoffset", f"{offset:.2f}", f"{circumference:.2f}")}\n'
             + '  </circle>\n')
    return ring


def grow_bar(x: float, y: float, width: float, height: float, colour: str,
             opacity: str = "1", delay: str = "0s", rx: float = 6.0) -> str:
    """A bar whose final width is the static attribute; it grows into place."""
    return (f'  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{rx}"'
            f' fill="{colour}" fill-opacity="{opacity}">'
            + animate("width", f"{width}", "0", delay) + '</rect>\n')


# ---------------------------------------------------------------- figures
def banner(data) -> str:
    by_id = milestones(data)
    complete = sum(1 for m in data["milestones"] if m["state"] == "complete")
    total = len(data["milestones"])
    streams = len(data["workstreams"])
    dest_label, dest_colour = STATE_STYLE[by_id["M19"]["state"]]

    body = ""
    # The mark and the wordmark arrive first, and the rule under the chips draws itself
    # left to right.  Each of these is a one-shot entrance that freezes: `stroke-dashoffset`
    # and `opacity` are written at their resting values, so a client that ignores SMIL
    # shows the finished banner rather than a half-drawn one, and `animation_problems`
    # below is what enforces that.
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
    delay = 0.0
    for label, colour in chips:
        width = len(label) * 7 + 22
        body += fade(badge(x, 176, label, colour, w=width, size=11), f"{delay:.2f}s")
        delay += 0.12
        x += width + 12

    # the honest line, in the figure, not only in prose
    body += text(206, 244, "The ISO boots PC5's own loader and kernel. No Sony software is present,",
                 size=13, fill=INK)
    body += text(206, 264, "and none is executed. Physical PC boot: NOT TESTED.",
                 size=13, fill=AMBER)
    body += (f'  <line x1="60" y1="292" x2="1140" y2="292" stroke="{LINE}"\n'
             + '    stroke-dasharray="1080" stroke-dashoffset="0">\n'
             + f'    {animate("stroke-dashoffset", "0", "1080", "0.45s", "0.9s")}\n'
             + '  </line>\n')
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
        # The state segments grow into place, one graph row behind the next, and each
        # `rect` carries its real width as a static attribute.  A reader whose client
        # does not animate SMIL therefore sees the finished bars, and a reader who
        # watches them land sees the same numbers: one truth, two renderings.
        bar_delay = f"{0.1 + index * 0.06:.2f}s"
        for state, colour in (("complete", GREEN), ("in_progress", CYAN),
                              ("blocked", RED)):
            if not counts[state]:
                continue
            width = max(4, round(bar_w * counts[state] / count))
            body += grow_bar(offset, y + 8, width, 14, colour,
                             "1" if state == "complete" else "0.75", bar_delay, 7.0)
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
BLOCKS = ("completion", "stats", "status", "evidence", "blockers", "pup")
# The About page gets its own blocks, and they read the same model as the README's.
# That is the point of putting them in a generator rather than in a paragraph: an About
# page that *quotes* the front page goes stale the first week, and a stale About page is
# worse than none, because it is the page a newcomer trusts.
ABOUT_BLOCKS = ("about-identity", "about-capabilities", "about-progress")
ALL_BLOCKS = BLOCKS + ABOUT_BLOCKS


def pages() -> dict:
    """Every document the generator owns, and the blocks it owns inside it.

    A function rather than a dict literal, so a test can redirect `README` / `ABOUT`
    at a fixture tree and have this follow.
    """
    return {README: BLOCKS, ABOUT: ABOUT_BLOCKS}


def page_label(path: Path) -> str:
    """How a problem message names an owned page.

    Inside this repository that is the repository-relative path.  Under a redirected
    fixture (`tests/test_presentation.py` points `README` / `ABOUT` at a temporary tree)
    there is no such prefix to strip, and printing `/tmp/tmpab12/ABOUT.md` would bury the
    one fact a reader needs — which of the two pages to open.  So the fallback is the file
    name, which distinguishes the pages in both directions and never collides here.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return path.name

STATE_LABEL = {"complete": "complete", "in_progress": "in progress",
               "not_started": "not started", "blocked": "blocked"}


def load_records(directory: Path | None = None) -> dict:
    """The committed gate records, read by the one reader this repository has.

    Deliberately a delegation, not a sibling: `tools/completion.py` owns the record
    shape, the schema filter and the rule that the newest date wins when a gate has
    more than one file.  Two loaders over one directory drift by construction -- this
    one used to key by gate and keep whichever filename sorted last, so the stamp the
    completion block printed and the stamp the stat cards printed could disagree about
    the same evidence -- and a dashboard whose rows come from different reads of the
    same directory is not a dashboard.
    """
    return completion.load_records(directory)


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
    # `tools/validation.py` writes `checks` as {"passed": n, "total": m}; anything else
    # (an int, a string, a hand-edited record) is not a tally, so it is dropped rather
    # than indexed -- a malformed record must not be able to raise inside the generator
    # and leave the README half-written.
    checks = record.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    detail = f" {checks['passed']}/{checks['total']}" if "passed" in checks and "total" in checks else ""
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




def completion_chart(data, records=None) -> str:
    """The signature figure: one honest number, drawn from the canonical graph.

    The ring is the weighted completion, the stacked bar is the milestone graph, and
    the six rows beneath it are the success hierarchy level by level, because a single
    percentage flattens the thing PC5 actually cares about: level 1 (does it boot a
    machine) and level 5 (does the system software run) are not the same distance from
    done.  Everything here is arithmetic on `roadmap.json`; the only judgement is the
    weight, and the weight is stated inside the figure so a reader can disagree with it.
    """
    model = completion.compute(data)
    stamp = completion.verified_at(records if records is not None else load_records())
    hosted = completion.hosted_summary()
    counts, levels = model["counts"], model["levels"]
    percent = model["percent"]

    body = text(48, 46, "OVERALL PROJECT COMPLETION", size=20, weight="bold")
    body += text(48, 70, "earned acceptance-criteria weight / total declared weight, from "
                         "docs/architecture/roadmap.json", size=12, fill=MUTED)
    body += text(48, 88, "in-progress milestones score 0 until their criteria are recorded; "
                         "a percentage typed into prose fails presentation-check",
                 size=11, fill=AMBER)

    # ---- the ring, and the number it draws
    body += fade(panel(48, 108, 320, 244), "0s")
    body += arc(208, 222, 92.0, percent / 100.0, GREEN, 24.0)
    body += text(208, 214, f"{percent}%", size=42, weight="bold", anchor="middle")
    body += text(208, 240, "OF DECLARED SCOPE", size=10, fill=MUTED, anchor="middle",
                 spacing="1.5")
    body += text(208, 272, f"{counts['complete']}/{counts['total']} milestones", size=13,
                 fill=INK, anchor="middle")
    body += text(208, 292, f"{model['criteria']['complete']}/{model['criteria']['total']} criteria",
                 size=11, fill=MUTED, anchor="middle")
    body += text(208, 336, f"by milestone count {model['percent_by_count']}%", size=11,
                 fill=CYAN, anchor="middle")

    # ---- the graph itself, as one stacked bar
    x0, span = 400, 752
    body += text(x0, 132, "MILESTONE GRAPH BY STATE", size=12, fill=INK, weight="bold",
                 spacing="1")
    segments = (("complete", counts["complete"], GREEN, "1"),
                ("in progress", counts["in_progress"], CYAN, "0.8"),
                ("blocked", counts["blocked"], RED, "0.8"),
                ("not started", counts["not_started"], MUTED, "0.45"))
    offset, delay = x0, "0.15s"
    body += f'  <rect x="{x0}" y="144" width="{span}" height="18" rx="9" fill="{LINE}" '
    body += 'stroke="none"/>\n'
    for label, count, colour, opacity in segments:
        if not count:
            continue
        width = round(span * count / counts["total"], 1)
        body += grow_bar(offset, 144, width, 18, colour, opacity, delay, 9.0)
        offset += width
        delay = f"{float(delay[:-1]) + 0.12:.2f}s"
    legend = " · ".join(f"{count} {label}" for label, count, _c, _o in segments)
    body += text(x0, 182, legend, size=11, fill=MUTED)

    # ---- the success hierarchy, level by level
    body += text(x0, 212, "THE SAME PROGRESS, BY LEVEL OF THE SUCCESS HIERARCHY", size=11,
                 fill=INK, weight="bold", spacing="1")
    row_y, row_delay = 222, "0.5s"
    for index, level in enumerate(sorted(levels)):
        bucket = levels[level]
        y = row_y + index * 21
        body += text(x0, y + 11, f"{level[1:]}  ", size=11, fill=CYAN, weight="bold")
        body += f'  <rect x="{x0 + 34}" y="{y}" width="520" height="12" rx="6" '
        body += f'fill="{LINE}" stroke="none"/>\n'
        width = round(520 * bucket["percent"] / 100.0, 1)
        if width:
            body += grow_bar(x0 + 34, y, max(width, 6), 12,
                             GREEN if bucket["percent"] >= 60 else CYAN, "0.9", row_delay, 6.0)
        body += text(x0 + 566, y + 10, f"{bucket['percent']}%", size=11, fill=INK)
        body += text(x0 + 620, y + 10, f"{bucket['complete']}/{bucket['total']} milestones",
                     size=11, fill=MUTED)
        row_delay = f"{float(row_delay[:-1]) + 0.1:.2f}s"

    # ---- the four states that must not be confused
    release = completion.release_state(data)
    dest_state = model["destination"]["state"]
    chips = ((f"DESTINATION {model['destination']['id']} "
               f"{STATE_STYLE.get(dest_state, ('UNKNOWN', MUTED))[0]}",
               STATE_STYLE.get(dest_state, (None, MUTED))[1]),
             (f"RELEASE {release['milestone']} {release['state'].upper()}", RED),
             (f"CI {hosted['conclusion'].upper()}" if hosted["available"] else "CI NO RECORD",
              GREEN if hosted.get("light") == "green" else AMBER),
             (f"VERIFIED {stamp['date']} @{stamp.get('short', 'none')}", CYAN))
    x = x0
    for label, colour in chips:
        width = len(label) * 7 + 22
        body += badge(x, 356, label, colour, w=width, size=11)
        x += width + 12

    body += text(48, 372, "tools/completion.py · weight = acceptance criteria per milestone · "
                          "partial credit refused without a recorded criterion",
                 size=10, fill=MUTED)
    body += text(1152, 372, "generated, do not hand-edit", size=10, fill=MUTED, anchor="end")
    return svg(1200, 386, body, "PC5 overall project completion",
               f"{percent} percent of PC5's declared scope is complete: {counts['complete']} of "
               f"{counts['total']} milestones, {model['criteria']['complete']} of "
               f"{model['criteria']['total']} acceptance criteria. In-progress milestones score "
               f"zero. Destination {model['destination']['id']} is "
               f"{model['destination']['state']}, and nothing has been released.")

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


# ---------------------------------------------------------------- block plumbing
BLOCK_RENDERERS = {
    "completion": lambda data, records: completion.markdown(
        completion.compute(data), completion.verified_at(records),
        completion.release_state(data), completion.hosted_summary()),
    "stats": lambda data, records: stats_markdown(data, records),
    "status": lambda data, records: status_markdown(data),
    "evidence": lambda data, records: evidence_markdown(records),
    "blockers": lambda data, records: blockers_markdown(data),
    "pup": lambda data, records: pup_markdown(),
    "about-identity": lambda data, records: about_identity_markdown(data),
    "about-capabilities": lambda data, records: about_capabilities_markdown(data),
    "about-progress": lambda data, records: about_progress_markdown(data, records),
}


# ---------------------------------------------------------------- the About page
#: The `LICENSE` file is the canonical statement; these two patterns are how it is read.
LICENCE_VERSION = re.compile(r"version\s+(\d+)", re.I)
LICENCE_LATER = re.compile(r"or\s+any\s+later\s+version", re.I)
LICENCE_ONLY = re.compile(r"version\s+\d+\s+only", re.I)


def licence_identity(root: Path | None = None) -> dict:
    """What `LICENSE` grants, read from the file rather than repeated from a badge.

    GitHub detects a repository licence by scanning the licence text's opening, and this
    repository's `LICENSE` is the GPL preamble plus an explicit pointer to the full text —
    so the platform's reported SPDX id can disagree with the file.  The file is canonical
    here: the About page prints what this parser reads, `tools/docs_check.py` fails any
    controlled document that names a different id, and a mismatch is therefore a build
    failure rather than a permanent discrepancy on the front page.

    An unclassifiable file is refused.  "Licence: unknown" is an honest sentence to print;
    a guessed one is not, and a guessed licence on an About page is the kind of claim that
    outlives its author.
    """
    path = (root or ROOT) / "LICENSE"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"LICENSE: unreadable ({exc}), so no licence can be stated")
    header = (text.splitlines() or [""])[0].strip().lower()
    if "general public license" not in header:
        raise SystemExit('LICENSE: the first line does not name the GNU General Public '
                         'License, so an SPDX id cannot be derived from it')
    version = LICENCE_VERSION.search(text)
    if not version:
        raise SystemExit("LICENSE: no version number in the file; refusing to print a guess")
    major = int(version.group(1))
    # "only" and "or any later version" are mutually exclusive in a well-formed grant;
    # a file that says neither is deliberately left unclassified rather than defaulted.
    if LICENCE_ONLY.search(text):
        spdx, qualifier = f"GPL-{major}.0-only", "only"
    elif LICENCE_LATER.search(text):
        spdx, qualifier = f"GPL-{major}.0-or-later", "or any later version"
    else:
        raise SystemExit(f"LICENSE: GPL version {major} with no 'only' or "
                         f"'or any later version' grant; the id cannot be derived")
    vendored = re.search(r"no third-party source archive is\s+\w+", text, re.I)
    return {"spdx": spdx, "major": major, "qualifier": qualifier,
            "no_vendored_third_party": bool(vendored),
            "source": "LICENSE", "file": "LICENSE"}


def about_link(target: str) -> str:
    """Rewrite a repository-relative path so it resolves from `docs/ABOUT.md`.

    A generated block is written into a specific file, and a link is relative to that
    file — not to the repository root.  The roadmap stores its evidence and authority
    paths root-relatively, so the block has to translate them, or every link in the
    About page would be a dead link that only a checker can see.
    """
    cleaned = str(target).strip().lstrip("./")
    if cleaned.startswith(("http://", "https://", "mailto:", "#", "/")):
        return cleaned
    if cleaned.startswith("docs/"):
        return cleaned[len("docs/"):]
    return f"../{cleaned}"


def _bullet_list(items, limit: int = 0) -> list:
    body = list(items)
    if limit:
        body = body[:limit]
    return [f"- {item}" for item in body]


def about_identity_markdown(data) -> str:
    """Who PC5 is, in the roadmap's own words — because the data is the authority.

    The identity, mission, destination, foundation, non-goals and levels are rendered from
    `docs/architecture/roadmap.json`.  `tests/test_product_identity.py` already requires
    every identity document to state `product.identity` and to link the architecture
    authority, so writing this block by hand would mean maintaining a paraphrase that a
    gate is guaranteed to break.
    """
    product = data["product"]
    destination = product["destination"]
    foundation = product["foundation"]
    by_id = milestones(data)
    licence = licence_identity()
    dest_milestone = by_id.get(destination["milestone"], {})
    lines = [f"<!-- pc5:about-identity:begin -->",
             "",
             f"> **{product['identity']}**",
             "",
             product["identity_note"],
             "",
             "| | |",
             "|---|---|",
             f"| **Mission** | {product['mission']} |",
             f"| **Destination** | `{destination['milestone']}` — "
             f"{dest_milestone.get('title', 'untitled')} (level {destination['level']}) |",
             f"| **Destination statement** | {destination['statement']} |",
             f"| **Evidence class at the destination** | {destination['evidence_class']} |",
             f"| **Hardware-enablement substrate** | {foundation['name']} — role "
             f"`{foundation['role']}`, `required_for_product: false`, exits at "
             f"`{foundation['exit_milestone']}` ({foundation['adr']}) |",
             f"| **Licence** | **{licence['spdx']}**, read from the repository's "
             f"`LICENSE` file; no third-party source archive is vendored "
             f"([ADR-025]({about_link('docs/TECHNICAL_DECISIONS.md')})) |",
             f"| **Roadmap authority** | [{data['authority'].split('/')[-1]}]"
             f"({about_link(data['authority'])}), document version `{data['document_version']}`, "
             f"updated {data['updated']} |",
             "",
             "**What the destination cannot be satisfied by** — stated in the roadmap so "
             "that no page, figure or release note can quietly substitute one of these for "
             "the real thing:",
             ""]
    lines += _bullet_list(destination["must_not_be_satisfied_by"])
    lines += ["", "**Non-goals** — the same list the identity test reads:", ""]
    lines += _bullet_list(product["non_goals"])
    lines += ["", "**The success hierarchy, in order** — a level is claimed only with the "
              "evidence class named beside it:", ""]
    for level in product["levels"]:
        lines.append(f"- **Level {level['level']} — {level['statement']}.**  "
                     f"*Evidence class:* {level['evidence_class']}")
    lines += ["", "Every row and bullet above is rendered from `docs/architecture/roadmap.json` "
              "and `LICENSE` by `tools/presentation.py`.  Nothing in this section is a "
              "paraphrase, and nothing in it can be edited without the gate in "
              "`tests/test_product_identity.py` noticing.", "<!-- pc5:about-identity:end -->"]
    return "\n".join(lines)


def about_capabilities_markdown(data) -> str:
    """What works, at what standing, with the source beside each claim.

    Five buckets, and every one of them is a field in `roadmap.json` rather than a
    judgement made here: `complete` and `in_progress` and `not_started` and `blocked` are
    the milestone states the roadmap gate enforces, and the middle bucket — implemented but
    not fully validated — is the `foundations[]` list, whose `state`, `delivers` and
    `does_not_complete` strings are the project's own words for how far each experimental
    path has actually gone.
    """
    counts = distribution(data)
    foundations = data.get("foundations") or []
    complete = [m for m in data["milestones"] if m["state"] == "complete"]
    in_progress = [m for m in data["milestones"] if m["state"] == "in_progress"]
    lines = [f"<!-- pc5:about-capabilities:begin -->",
             "| Standing | Count | How this bucket is decided |",
             "|---|---:|---|",
             f"| **Implemented and verified** | {len(complete)} | the milestone's state is "
             f"`complete`, which in this roadmap means its acceptance criteria are met and "
             f"evidence is filed in the repository |",
             f"| **Implemented, not fully validated** | {len(foundations)} | an entry in "
             f"`roadmap.json`'s `foundations[]`, whose recorded `state` says what has been "
             f"tested and what has not |",
             f"| **Experimental or in progress** | {counts['in_progress']} | state "
             f"`in_progress`; these earn nothing in the completion figure |",
             f"| **Planned** | {counts['not_started']} | state `not_started` |",
             f"| **Blocked** | {counts['blocked']} | state `blocked`, with the blocker named "
             f"in the roadmap |",
             "",
             "**Implemented and verified** — with the file that holds the evidence, so the "
             "claim can be checked rather than trusted:",
             ""]
    for milestone in complete:
        evidence = ", ".join(f"[`{e}`]({about_link(e)})"
                             for e in (milestone.get("evidence") or []))
        lines.append(f"- `{milestone['id']}` — {milestone['title']} "
                     f"({len(milestone.get('acceptance') or [])} acceptance "
                     f"criteria{' · evidence: ' + evidence if evidence else ''})")
    lines += ["", "**Implemented, not fully validated** — quoted from `foundations[]`, "
              "because the distinction *is* the sentence:", ""]
    for item in foundations:
        # One line per claim, verbatim. These sentences are long on purpose — they are the
        # boundary between "built" and "believed" — so they are stacked rather than joined
        # into a paragraph, which is how a reader ends up skimming the part that matters.
        lines.append(f"- **`{item['id']}`** — {item['title']}  \n"
                     f"  *Standing:* {item['state']}  \n"
                     f"  *Reaches milestone* `{item['milestone']}`, and does not complete it.")
        for delivers in item.get("delivers") or []:
            lines.append(f"  - Delivers: {delivers}")
        for missing in item.get("does_not_complete") or []:
            lines.append(f"  - Not completed: {missing}")
    lines += ["", "**Experimental or in progress** — where work is open right now:", ""]
    for milestone in in_progress:
        advances = milestone.get("advances") or ""
        lines.append(f"- `{milestone['id']}` — {milestone['title']}"
                     + (f"  \n  *Advances:* {advances}" if advances else ""))
    lines += ["", f"**Planned** — {counts['not_started']} milestones carry "
              f"`not_started`; the list is in "
              f"[docs/ROADMAP.md]({about_link('docs/ROADMAP.md')}) and in "
              f"`docs/architecture/roadmap.json`, and none of them is described as done "
              f"anywhere in this repository.",
              "",
              f"**Blocked** — {counts['blocked']} milestones, each with its blocker quoted: "
              f"see the generated table in [README → What is blocked]"
              f"({about_link('README.md')}#what-is-blocked).  This page does not repeat it, because two "
              f"copies of a blocker list are two things that can go stale.",
              "",
              "Generated by `tools/presentation.py` from `docs/architecture/roadmap.json`; "
              "the counts are the same `distribution()` the README prints, so the two pages "
              "cannot disagree about how much of PC5 exists.",
              "<!-- pc5:about-capabilities:end -->"]
    return "\n".join(lines)


def about_progress_markdown(data, records) -> str:
    """The progress row of the About page: the README's number, from the same function."""
    model = completion.compute(data)
    stamp = completion.verified_at(records)
    release = completion.release_state(data)
    hosted = completion.hosted_summary()
    counts = model["counts"]
    lines = [f"<!-- pc5:about-progress:begin -->",
             "| | |",
             "|---|---|",
             f"| **Overall completion** | **{model['percent']}%** — earned acceptance-criteria "
             f"weight ÷ declared weight |",
             f"| **Milestones** | {counts['complete']} complete · {counts['in_progress']} in "
             f"progress · {counts['blocked']} blocked · {counts['not_started']} not started, "
             f"in {counts['workstreams']} workstreams |",
             f"| **Current milestone** | "
             f"{', '.join(f'`{m}`' for m in model['milestones']['in_progress']) or 'none'} |",
             f"| **Last verified** | {stamp['date']} — `{stamp['gate']}` recorded "
             f"**{stamp['result']}** at `{stamp['short']}` "
             f"({stamp['passed']}/{stamp['gates']} committed gate records passed) |",
             f"| **Hosted CI** | {hosted['text']} |",
             f"| **Release** | `{release['milestone']}` "
             f"**{release['state'].upper().replace('_', ' ')}** — "
             f"{'nothing published' if not release['published'] else 'published'} |",
             f"| **Canonical roadmap** | [docs/ROADMAP.md]"
             f"({about_link('docs/ROADMAP.md')}) · [`roadmap.json`]"
             f"({about_link('docs/architecture/roadmap.json')}) |",
             "",
             f"The figure is criteria-weighted, not commit-weighted: {model['criteria']['complete']} "
             f"of {model['criteria']['total']} declared acceptance criteria are met.  Counting "
             f"milestones instead gives {model['percent_by_count']}%, and the two agree within "
             f"{model['agreement_points']} points — so the headline is not an artefact of the "
             f"weighting.  Progress is **never** computed from commits, files, lines or test "
             f"counts, and an `in_progress` milestone scores zero until it declares which of its "
             f"criteria it has met.  `python3 tools/completion.py model` reprints this "
             f"calculation; `python3 tools/completion.py check` refuses inconsistent roadmap data.",
             "<!-- pc5:about-progress:end -->"]
    return "\n".join(lines)


def block_markers(name: str) -> tuple:
    return f"<!-- pc5:{name}:begin -->", f"<!-- pc5:{name}:end -->"


def apply_block(text: str, name: str, body: str, label: str = "README.md") -> str:
    begin, end = block_markers(name)
    if begin not in text or end not in text:
        # Named by the caller, because the same failure on `docs/ABOUT.md` must not tell
        # somebody to go and edit the front page.
        raise SystemExit(f"{label} is missing the {begin} … {end} markers")
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
    # the completion figure is the one number a reader is most likely to hand-edit,
    # and the likeliest to be stale by a whole milestone: it may only ever come from
    # tools/completion.py inside a generated block.
    re.compile(r"\b\d{1,3}(?:\.\d+)?\s*%(?!\w)"),  # `\b` after `%` never matches
)


def stray_statistics(document: str, label: str = "README.md") -> list:
    """Statistics outside a generated block are the ones that go stale."""
    stripped = document
    for name in ALL_BLOCKS:
        begin, end = block_markers(name)
        while begin in stripped and end in stripped:
            head, rest = stripped.split(begin, 1)
            _, tail = rest.split(end, 1)
            stripped = head + tail
    found = []
    for pattern in STRAY:
        for match in pattern.finditer(stripped):
            found.append(f"{label}: {match.group(0)!r} is a statistic outside a generated "
                         f"block — put it in one so it cannot go stale")
    return found

TEXT_ELEMENT = re.compile(
    r'<text x="([\d.]+)" y="[\d.]+"[^>]*font-size="([\d.]+)"[^>]*'
    r'text-anchor="(\w+)"[^>]*>(.*?)</text>', re.S)


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
        estimate = len(label) * float(size) * 0.62
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
    "pc5-completion.svg": completion_chart,
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


#: Words each owned page has to contain, and why it is that page's job to say them.
#: The README is the front page, so it carries the full set; the About page is the long
#: form, so it is required to name the two distinctions a newcomer is most likely to get
#: wrong — what the ISO boots, and that the Sony package is a pointer with no payload.
#: Keyed by file name rather than by `README` / `ABOUT`, and that is deliberate: the
#: constants are rebound at import time by the test fixtures, and a dict built from the
#: paths would silently hold the wrong keys for the rest of the run — a missing-token rule
#: that quietly checks nothing is the worst kind of gate, because it looks like a pass.
HONESTY_TOKENS = {
    README.name: ("genuine Sony PlayStation 5 system software", "NOT TESTED",
                  "BLOCKED", "Git LFS pointer"),
    ABOUT.name: ("NOT TESTED", "Git LFS pointer", "nothing published"),
}

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


def animation_problems(name: str, body: str) -> list:
    """Every animation must freeze on a value the static markup already carries.

    This is the rule that makes the animated figures safe to publish.  A client that
    ignores SMIL, a proxy that flattens the image, a reader with motion disabled, and
    a screenshot of the run page all see the *static* attribute; if that attribute
    disagreed with the animation's end state, the same figure would tell two stories.
    So each ``<animate>`` has to freeze, and its parent has to already hold the value
    it animates toward.  A looping animation would fail here on purpose: motion that
    never settles is decoration, and these figures are not decoration.
    """
    import xml.etree.ElementTree as ET
    problems = []
    for token in ("<script", "<style", "onload", "onclick", "javascript:", "https://"):
        if token in body:
            problems.append(f"docs/assets/{name}: {token!r} is not allowed in a figure")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return [f"docs/assets/{name}: is not well-formed XML ({exc})"]
    for parent in root.iter():
        for child in list(parent):
            if not child.tag.endswith("animate") and not child.tag.endswith("animateTransform"):
                continue
            if child.get("fill") != "freeze":
                problems.append(f"docs/assets/{name}: an animation on "
                                f"{child.get('attributeName')} does not freeze on a value")
                continue
            attribute = child.get("attributeName")
            target = child.get("to")
            if attribute and target is not None and parent.get(attribute) != target:
                problems.append(
                    f"docs/assets/{name}: {attribute} rests at {parent.get(attribute)!r} but "
                    f"animates to {target!r}; a reader without animation would see a "
                    f"different figure")
    return problems


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
        problems += animation_problems(name, actual)

    blocks = render_blocks(data, records)
    for document, names in pages().items():
        label = page_label(document)
        if not document.exists():
            problems.append(f"{label}: missing — this page is generated into, not from")
            continue
        text = document.read_text(encoding="utf-8")
        for name in names:
            begin, end = block_markers(name)
            if begin not in text or end not in text:
                problems.append(f"{label}: the {name} block has lost its markers")
                continue
            if apply_block(text, name, blocks[name], label) != text:
                problems.append(f"{label}: the generated {name} block does not match the "
                                f"data — run `make presentation`")
        # Every owned page is held to the same two prose rules the README is held to:
        # no statistic outside a block, and no claim this repository cannot make.
        problems += link_problems(document, text)
        problems += stray_statistics(text, label)
        lowered = " ".join(text.lower().split())
        for pattern, why in FORBIDDEN:
            if pattern in lowered:
                problems.append(f"{label}: claim {pattern!r} is not true ({why})")
        for token in HONESTY_TOKENS.get(document.name, ()):
            if token.lower() not in lowered:
                problems.append(f"{label}: the honesty token {token!r} is missing")

    # Every figure has to be reachable from the front page.  A figure only the About page
    # embeds is still generated and still checked, but it is nobody's first sight of
    # anything — which is how a diagram becomes a footnote, then a lie nobody notices.
    front = README.read_text(encoding="utf-8") if README.exists() else ""
    for name in expected:
        if f"docs/assets/{name}" not in front:
            problems.append(f"README.md does not embed docs/assets/{name}")

    # an asset nobody generates is an asset nobody can regenerate: it would sit in the
    # README forever, quietly drifting from the graph it no longer reflects.
    for path in sorted(ASSETS.glob("*.svg")) if ASSETS.is_dir() else []:
        if path.name not in expected:
            problems.append(f"docs/assets/{path.name}: no generator owns this figure "
                            f"— add it to FIGURES or delete it")

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
    blocks = render_blocks(data, records)
    for document, names in pages().items():
        if not document.exists():
            # A page the generator owns but which is absent is not this function's job to
            # invent: `check` reports it, because silently creating a tracked document from
            # `sync` would let a deleted page come back on somebody else's run.
            continue
        text = document.read_text(encoding="utf-8")
        updated = text
        for name in names:
            updated = apply_block(updated, name, blocks[name], page_label(document))
        if updated != text:
            document.write_text(updated, encoding="utf-8")
            written.append(page_label(document))
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
            blocks = sum(len(names) for names in pages().values())
        blocks = sum(len(names) for names in pages().values())
        print(f"presentation: {len(FIGURES)} figures and {blocks} generated blocks "
              f"across {len(pages())} pages are current")
        return 0
    problems = check(data, records)
    for problem in problems:
        print(f"[FAIL] {problem}")
    if problems:
        print(f"{len(problems)} presentation problem(s)")
        return 1
    rows = status_rows(data)
    print(f"PRESENTATION OK: {len(FIGURES)} figures and "
          f"{sum(len(names) for names in pages().values())} blocks current, "
          f"{len(rows)} status rows match roadmap.json, "
          f"{len(records)} validation record(s) read, links and alt text resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
