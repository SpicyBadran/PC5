"""Generate PC5's logo and brand assets from plain geometry.

The mark is an original monogram: a geometric ``P`` nested inside an open
``C``, drawn as three strokes of one weight.  It is not derived from, copied
from, or a modification of Sony's PlayStation symbol -- the construction is
different (an interlocked P-in-C, not a P with an S), and every path here is
generated from the numbers below.

    python3 tools/brand.py sync     # write brand/*.svg
    python3 tools/brand.py check    # fail if the files on disk are stale

Like `tools/presentation.py`, this keeps the artwork diffable and free of
third-party assets: no fonts, no raster sources, no network.  The wordmark is
drawn as paths rather than ``<text>`` so the logo renders identically anywhere.

Geometry is expressed on a 512x512 design grid for the symbol and on a 200-unit
cap height for the wordmark; both are emitted at the sizes the README, the
presentation figures and the docs need.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "brand"

# ------------------------------------------------------------------ palette
# The project's own palette (see tools/presentation.py); the logo adds no new
# colours.  PRIMARY is a light cyan and is only approved on dark backgrounds.
BG = "#0b0f14"
INK = "#e6edf3"
PRIMARY = "#38bdf8"
SECONDARY = "#a78bfa"
BLACK = "#000000"
WHITE = "#ffffff"

# ----------------------------------------------------------------- geometry
# Symbol: a C (open on the right) whose centre is the P's stem line.
SYM_VIEW = 512
SYM_STROKE = 48
SYM = {
    "stem_x": 283.0,
    "top": 96.0,
    "bottom": 416.0,
    "bowl_r": 72.0,
    "c_r": 168.0,
    "c_gap": 46.0,
}

# Wordmark: cap height 200, one stroke weight, three letters on a common
# baseline.  Each letter's ink starts at its own x; the gaps are equal.
WORD_CAP = 200.0
WORD_STROKE = 40.0
WORD_GAP = 66.0
WORD_RB = 60.0      # P bowl radius
WORD_CR = 72.0      # C radius
WORD_CGAP = 46.0    # C opening, degrees
WORD_5W = 130.0     # figure-5 width


def _c_arc(cx: float, cy: float, r: float, gap_deg: float) -> str:
    """Arc of a circle open on the right; the opening is +/- gap_deg."""
    t = math.radians(gap_deg)
    x = cx + r * math.cos(t)
    return (f"M {x:.1f} {cy + r * math.sin(t):.1f} "
            f"A {r:.1f} {r:.1f} 0 1 1 {x:.1f} {cy - r * math.sin(t):.1f}")


def mark_paths(fg: str, stroke: int = SYM_STROKE, matrix: str = "") -> str:
    """The PC monogram: stem, P bowl, and the C that embraces it."""
    s = SYM
    body = (
        f'<path d="M {s["stem_x"]:.1f} {s["top"]:.1f} L {s["stem_x"]:.1f} {s["bottom"]:.1f}"/>'
        f'<path d="M {s["stem_x"]:.1f} {s["top"]:.1f} '
        f'A {s["bowl_r"]:.1f} {s["bowl_r"]:.1f} 0 0 1 '
        f'{s["stem_x"]:.1f} {s["top"] + 2 * s["bowl_r"]:.1f}"/>'
        f'<path d="{_c_arc(s["stem_x"], (s["top"] + s["bottom"]) / 2, s["c_r"], s["c_gap"])}"/>'
    )
    return (f'<g transform="{matrix}" stroke="{fg}" stroke-width="{stroke}" '
            f'fill="none" stroke-linecap="round" stroke-linejoin="round">{body}</g>')


def wordmark_paths(fg: str, stroke: int = WORD_STROKE, x: float = 0.0,
                   baseline: float = 0.0, scale: float = 1.0) -> str:
    """The PC5 wordmark drawn with the same stroke language as the mark.

    Geometry is laid out on the 200-unit cap grid with the baseline at y=0, then
    scaled and placed at ``(x, baseline)``.  Passing the placement separately
    from the scale keeps the strokes and the advances in the same proportion.
    """
    def sx(v: float) -> str:
        return f"{x + v * scale:.1f}"

    def sy(v: float) -> str:
        return f"{baseline + v * scale:.1f}"

    top = -WORD_CAP
    ym = top + 0.48 * WORD_CAP
    rb = 0.26 * WORD_CAP
    bcx = WORD_5W - rb
    c_cx = WORD_RB + WORD_GAP + WORD_CR
    five_x = (WORD_RB + WORD_GAP
              + WORD_CR * (1.0 + math.cos(math.radians(WORD_CGAP)))
              + WORD_GAP)
    p = (f'<path d="M {sx(0)} {sy(top)} L {sx(0)} {sy(0)} '
         f'M {sx(0)} {sy(top)} A {WORD_RB * scale:.1f} {WORD_RB * scale:.1f} '
         f'0 0 1 {sx(0)} {sy(top + 2 * WORD_RB)}"/>')
    c = (f'<path d="M {sx(c_cx + WORD_CR * math.cos(math.radians(WORD_CGAP)))} '
         f'{sy(-WORD_CAP / 2 + WORD_CR * math.sin(math.radians(WORD_CGAP)))} '
         f'A {WORD_CR * scale:.1f} {WORD_CR * scale:.1f} 0 1 1 '
         f'{sx(c_cx + WORD_CR * math.cos(math.radians(WORD_CGAP)))} '
         f'{sy(-WORD_CAP / 2 - WORD_CR * math.sin(math.radians(WORD_CGAP)))}"/>')
    five = (
        f'<path d="M {sx(five_x)} {sy(top)} L {sx(five_x + WORD_5W)} {sy(top)} '
        f'M {sx(five_x)} {sy(top)} L {sx(five_x)} {sy(ym)} '
        f'M {sx(five_x)} {sy(ym)} L {sx(five_x + bcx)} {sy(ym)} '
        f'M {sx(five_x + bcx)} {sy(ym)} A {rb * scale:.1f} {rb * scale:.1f} 0 1 1 '
        f'{sx(five_x + bcx)} {sy(ym + 2 * rb)} '
        f'M {sx(five_x + bcx)} {sy(ym + 2 * rb)} '
        f'L {sx(five_x + 0.10 * WORD_CAP)} {sy(ym + 2 * rb)}"/>'
    )
    body = p + c + five
    return (f'<g stroke="{fg}" stroke-width="{stroke}" '
            f'fill="none" stroke-linecap="round" stroke-linejoin="round">{body}</g>')


def wordmark_width() -> float:
    """Ink width of the wordmark, including the round caps at both ends."""
    return (WORD_RB + WORD_GAP
            + WORD_CR * (1.0 + math.cos(math.radians(WORD_CGAP)))
            + WORD_GAP + WORD_5W + WORD_STROKE)


# ------------------------------------------------------------------- figures
def _svg(width: int, height: int, body: str, title: str, desc: str,
         background: str | None = None) -> str:
    rect = f'<rect width="{width}" height="{height}" fill="{background}"/>' if background else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d">\n'
        f'  <title id="t">{title}</title>\n'
        f'  <desc id="d">{desc}</desc>\n'
        f'  {rect}\n'
        f"{body}\n"
        f"</svg>\n"
    )


def _symbol(title: str, desc: str, fg: str, background: str | None) -> str:
    return _svg(SYM_VIEW, SYM_VIEW, f"  {mark_paths(fg)}", title, desc, background)


def _lockup(title: str, desc: str, fg: str, background: str | None,
            mark: int = 280, pad: int = 60, gap: int = 69) -> str:
    """Symbol followed by the wordmark, both centred in the canvas.

    The symbol's ink is measured inside its 512 grid (91..423 x 64..448), so
    the canvas is built from the ink boxes rather than the nominal view box:
    the mark's empty left bearing would otherwise push the lockup off centre.
    """
    scale = mark / SYM_VIEW
    word_cap = 180.0
    word_scale = word_cap / WORD_CAP
    word_w = wordmark_width() * word_scale
    ink_l, ink_r = 91.0 * scale, 423.0 * scale
    ink_t, ink_b = 64.0 * scale, 448.0 * scale
    box_x, box_y = pad - ink_l, pad - ink_t
    word_x = box_x + ink_r + gap + WORD_STROKE * word_scale / 2
    baseline = box_y + (ink_t + ink_b) / 2 + word_cap / 2
    width = int(round(word_x + word_w - WORD_STROKE * word_scale / 2 + pad))
    height = int(round(max(box_y + ink_b, baseline) + pad))
    matrix = f"translate({box_x:.1f},{box_y:.1f}) scale({scale:.5f})"
    body = (
        f"  {mark_paths(fg, int(round(SYM_STROKE * scale)), matrix)}\n"
        f"  {wordmark_paths(fg, int(round(WORD_STROKE * word_scale)), word_x, baseline, word_scale)}"
    )
    return _svg(width, height, body, title, desc, background)


def _icon(title: str, desc: str, tile: str) -> str:
    """Compact app-style icon: a rounded tile with the mark knocked out.

    The knockout is a mask, so the letters are genuinely transparent rather
    than painted in whatever colour the surrounding page happens to be.
    """
    body = (
        f'  <mask id="pc5-icon-knockout">\n'
        f'    <rect width="{SYM_VIEW}" height="{SYM_VIEW}" fill="#ffffff"/>\n'
        f'    {mark_paths("#000000")}\n'
        f'  </mask>\n'
        f'  <rect x="40" y="40" width="432" height="432" rx="96" fill="{tile}" '
        f'mask="url(#pc5-icon-knockout)"/>'
    )
    return _svg(SYM_VIEW, SYM_VIEW, body, title, desc)


#: Assets that must have no background rectangle at all: their canvas is the
#: page they are placed on.
TRANSPARENT = {
    "pc5-mark.svg", "pc5-mark-black.svg", "pc5-mark-white.svg",
    "pc5-logo.svg", "pc5-logo-black.svg", "pc5-logo-white.svg",
    "pc5-icon.svg", "pc5-icon-black.svg", "pc5-icon-white.svg",
}

FIGURES = {
    # primary symbol -- brand cyan, transparent background
    "pc5-mark.svg": lambda: _symbol(
        "PC5 monogram",
        "Original PC5 monogram: a geometric P nested inside an open C.",
        PRIMARY, None),
    "pc5-mark-black.svg": lambda: _symbol(
        "PC5 monogram, monochrome black",
        "PC5 monogram in a single black ink for use on light backgrounds.",
        BLACK, None),
    "pc5-mark-white.svg": lambda: _symbol(
        "PC5 monogram, monochrome white",
        "PC5 monogram in a single white ink for use on dark backgrounds.",
        WHITE, None),
    # horizontal lockup
    "pc5-logo.svg": lambda: _lockup(
        "PC5 logo",
        "PC5 horizontal logo: the PC monogram followed by the PC5 wordmark.",
        PRIMARY, None),
    "pc5-logo-black.svg": lambda: _lockup(
        "PC5 logo, monochrome black",
        "PC5 horizontal logo in a single black ink for light backgrounds.",
        BLACK, None),
    "pc5-logo-white.svg": lambda: _lockup(
        "PC5 logo, monochrome white",
        "PC5 horizontal logo in a single white ink for dark backgrounds.",
        WHITE, None),
    # compact icon
    "pc5-icon.svg": lambda: _icon(
        "PC5 icon",
        "PC5 compact icon: the monogram knocked out of a rounded brand-cyan tile.",
        PRIMARY),
    "pc5-icon-black.svg": lambda: _icon(
        "PC5 icon, monochrome black",
        "PC5 compact icon as a solid black tile with the monogram knocked out.",
        BLACK),
    "pc5-icon-white.svg": lambda: _icon(
        "PC5 icon, monochrome white",
        "PC5 compact icon as a solid white tile with the monogram knocked out.",
        WHITE),
    # boot screen: black background, white ink
    "pc5-mark-boot.svg": lambda: _symbol(
        "PC5 monogram for the boot screen",
        "PC5 monogram in white on black, sized for the boot screen.",
        WHITE, BLACK),
    "pc5-logo-boot.svg": lambda: _lockup(
        "PC5 logo for the boot screen",
        "PC5 horizontal logo in white on black, sized for the boot screen.",
        WHITE, BLACK),
}


def write() -> list[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for name, render in FIGURES.items():
        path = OUT / name
        body = render()
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
            written.append(f"brand/{name}")
    return written


def check() -> list[str]:
    problems = []
    for name, render in FIGURES.items():
        path = OUT / name
        if not path.exists():
            problems.append(f"brand/{name}: missing -- run `python3 tools/brand.py sync`")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != render():
            problems.append(f"brand/{name}: stale -- run `python3 tools/brand.py sync`")
        try:
            ET.fromstring(actual)
        except ET.ParseError as exc:
            problems.append(f"brand/{name}: not well-formed XML: {exc}")
        if "<title" not in actual or "<desc" not in actual:
            problems.append(f"brand/{name}: no accessible title/description")
        if name in TRANSPARENT and re.search(r"\n  <rect width=", actual):
            problems.append(f"brand/{name}: must be transparent, but carries a "
                            f"background rectangle")
        if "<text" in actual:
            problems.append(f"brand/{name}: wordmark must be paths, not <text>")
        # the SVG namespace is not a reference; nothing else may point outward
        without_ns = actual.replace("http://www.w3.org/2000/svg", "")
        external = [ref for ref in re.findall(r"url\(([^)]*)\)", without_ns)
                    if not ref.startswith("#")]
        if "http://" in without_ns or "https://" in without_ns or external:
            problems.append(f"brand/{name}: references an external resource")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate PC5's logo and brand assets.")
    parser.add_argument("command", choices=("sync", "check"), nargs="?", default="check")
    args = parser.parse_args(argv)
    if args.command == "sync":
        for name in write():
            print(f"wrote {name}")
        print(f"brand: {len(FIGURES)} assets current")
        return 0
    problems = check()
    for problem in problems:
        print(f"[FAIL] {problem}")
    if problems:
        print(f"{len(problems)} brand problem(s)")
        return 1
    print(f"BRAND OK: {len(FIGURES)} assets current, no text elements, no external references")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
