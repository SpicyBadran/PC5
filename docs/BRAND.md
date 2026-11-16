# PC5 brand identity

PC5's own identity: the logo, its variants, the colours and type it is drawn
with, and the rules for using them. This document is about **PC5 the project**.
It is not about the guest operating system PC5 is built to run, and nothing here
may be read as a description of Sony's software, its interface, or its marks.

The assets are generated, not hand-drawn: `tools/brand.py` writes every file in
`brand/` from the geometry in that module. `make brand-check` (part of
`make check`) fails if a file on disk no longer matches, so the artwork cannot
drift away from the rules below.

## The mark

An original monogram built from three strokes of one weight:

* a vertical stem;
* a bowl on the upper right of that stem — **P**;
* a larger arc centred on the same stem line, open on the right — **C**.

The **C** embraces the **P** rather than sitting beside it, so the two letters
read as one form. Everything is geometry: no gradient, no shadow, no outline,
no clip art, no controller silhouette, no flame, wing or shield.

The design is an original construction. It is not copied, traced, redrawn or
modified from any existing games-console brand mark, and it must not be
presented as related to one. The construction was chosen from four explored
alternatives — interlocked P-in-C (selected), side-by-side P and C, a
negative-space tile, and a continuous single-stroke form.

## Assets

| File | Use |
| --- | --- |
| `brand/pc5-mark.svg` | Primary symbol, brand cyan, transparent background |
| `brand/pc5-mark-black.svg` | Monochrome black, transparent background |
| `brand/pc5-mark-white.svg` | Monochrome white, transparent background |
| `brand/pc5-logo.svg` | Horizontal lockup: symbol + `PC5` wordmark, brand cyan |
| `brand/pc5-logo-black.svg` | Horizontal lockup, monochrome black |
| `brand/pc5-logo-white.svg` | Horizontal lockup, monochrome white |
| `brand/pc5-icon.svg` | Compact icon: mark knocked out of a rounded cyan tile |
| `brand/pc5-icon-black.svg` | Compact icon, black tile |
| `brand/pc5-icon-white.svg` | Compact icon, white tile |
| `brand/pc5-mark-boot.svg` | Symbol, white on pure black, for the boot screen |
| `brand/pc5-logo-boot.svg` | Lockup, white on pure black |
| `brand/pc5-mark-boot.txt` | The symbol as text-mode ASCII for a VGA console |

The wordmark is drawn as paths, not `<text>`: the logo renders identically on
GitHub, in a browser, in an image viewer and in a PDF without shipping a font.
Every SVG carries a `<title>` and a `<desc>`.

## Colour

The logo uses the project palette already defined in `tools/presentation.py`;
it introduces no new colours.

| Token | Value | Role |
| --- | --- | --- |
| PC5 cyan | `#38bdf8` | Primary brand colour |
| PC5 violet | `#a78bfa` | Secondary |
| PC5 amber | `#fbbf24` | Accent |
| Ink | `#e6edf3` | Text on dark |
| Brand background | `#0b0f14` | Approved dark background |
| Mono black | `#000000` | Single-ink variant for light backgrounds |
| Mono white | `#ffffff` | Single-ink variant for dark backgrounds |

Approved backgrounds, with measured contrast:

| Combination | Ratio | Verdict |
| --- | --- | --- |
| Cyan `#38bdf8` on `#0b0f14` | 8.97:1 | Approved (primary usage) |
| Cyan `#38bdf8` on `#000000` | 9.80:1 | Approved |
| Cyan `#38bdf8` on `#ffffff` | 2.14:1 | **Not approved** — use monochrome black |
| White `#ffffff` on `#0b0f14` | 19.22:1 | Approved |
| Black `#000000` on `#ffffff` | 21.00:1 | Approved |
| Ink `#e6edf3` on `#0b0f14` | 16.27:1 | Approved |
| Violet `#a78bfa` on `#0b0f14` | 7.06:1 | Approved | 
| Amber `#fbbf24` on `#0b0f14` | 11.51:1 | Approved |

Rules: the primary cyan is for dark backgrounds only. On a light background use
`pc5-mark-black.svg` / `pc5-logo-black.svg`. Never place the mark on a
photograph, a gradient, or a colour not listed above.

## Typography

The wordmark is geometric paths, so it needs no font. For text around it, the
project uses its existing monospace stack, unchanged:

`'JetBrains Mono','SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace`

Headings are bold; body text is regular. Nothing in the brand adds a second
typeface.

## Clear space and minimum size

* **Clear space**: keep a margin around the mark of at least 1/4 of the mark's
  height on all sides, free of text, rules and other marks.
* **Minimum size — mark**: 24 px wide. Below that, use
  `brand/pc5-mark-boot.txt` or omit the mark.
* **Minimum size — lockup**: 96 px wide.
* **Minimum size — icon**: 16 px wide.

## Boot screen

`brand/pc5-mark-boot.svg` and `brand/pc5-logo-boot.svg` are white on pure black
(`#000000`), matching the boot console. The release kernel's banner is
text-only and **is not modified** by the brand work: the shipping 32-bit boot
path is frozen, and adding artwork there would change the release image after
it was validated. To put the mark on a real boot screen, render
`brand/pc5-mark-boot.txt` — a 40x16 text-mode version — or draw the SVG from a
future graphical boot path.

## Usage

Do:

* use the lockup for the README header and presentation figures;
* use the mark alone once the lockup is already on the page;
* use the icon for avatars, favicons and small squares;
* keep the background and ink pairs listed above.

Do not:

* recolour the mark, or add a gradient, outline, shadow or 3D effect;
* rotate, skew, stretch or redraw it, or swap the letters' positions;
* place it on a busy or unlisted background;
* place it next to, or inside, anything that depicts Sony's software or marks;
* use it to imply that PC5 is affiliated with, endorsed by, or derived from
  Sony or its products.

## Regenerating and checking

```
make brand          # rewrite brand/*.svg from tools/brand.py
make brand-check    # fail if any file is missing or stale
```

`make check` runs `brand-check`. The generator refuses to emit a `<text>`
element, an external reference, or a file without an accessible title and
description.

## QA performed

* Every SVG parses as XML and renders in a standalone rasteriser.
* The transparent-background files contain no background rectangle; the icon's
  letters are a real mask, not a painted colour.
* The mark's ink bounding box is centred in its canvas.
* Legibility checked by rasterising at 512, 96, 64, 48, 32 and 24 px; the mark
  stays readable down to 24 px, below which the icon or the text-mode version
  should be used.
* Contrast measured as above.
* `brand-check` passes on a clean tree.
