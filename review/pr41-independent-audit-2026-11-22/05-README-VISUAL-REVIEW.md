# 05 — README progress section vs the reference image

The reference (`/tmp/arena-coding-uploads/image-1.png`, 1.2 MB, the file uploaded with the audit
request) was **opened and read** for this review — measured values below come from that inspection
and from the committed files, not from an impression. It is a disassembly-coverage dashboard for a
different project (system libraries, `gfx1013`/`RDNA2` reference forms, unsupported-throw sites,
image formats). It is a **design** reference: it supplies layout conventions, and none of its
content, numbers or colour meanings transfer to PC5.

## What the reference actually does

| dimension | observed |
|---|---|
| hierarchy | the *figure is the heading*: `Large system libraries: 43.72% (12579/28771)`, `Small system libraries: 50.8% (1913/3781)`, `Unsupported-throw sites by area (126)`, `Image formats (of 159)`. Every region states percentage **and** numerator/denominator before any visual is shown; an overline (`System libraries*: 45.27% (17771/30253)`) aggregates its sub-regions |
| grouping | by *area*, not by metric type: two-column treemaps, then a flat dense grid, then two form-decoding treemaps beside a text list, then bar rows. Two columns are used to keep one screen dense rather than long |
| colour | green = done, grey = not done, **amber = partial/remaining** (`Unsupported-throw sites by area` bars are amber-only because those sites are all outstanding). Two tones of green, no gradients, no card chrome, no borders, no donuts, no iconography |
| typography | one monospace family for identifiers, size and weight alone for hierarchy; labels inside tiles are truncated rather than wrapped; counts right-aligned in the bar rows |
| compactness | ~7 regions, 1500+ labelled tiles, and a footnote in one continuous dark canvas; no sentence-length prose anywhere; a scope caveat lives in an italic footnote attached by an asterisk to the region name it qualifies (`System libraries*` → *"Still needed (counted from the code, not from playing games)"*) |

The load-bearing idea is the first row: **a number is never shown without the denominator that
makes it falsifiable, and the caveat travels with the number rather than sitting in a separate
section.**

## What PC5 has now, measured

`docs/assets/pc5-progress-matrix.svg`: `viewBox 0 0 1200 1223`, 335 `<text>` elements, one
`JetBrains Mono`-first stack, font sizes 10 (×240), 11 (×78), 13 (×16), 20 (×1 — the title). Fill
counts: `#8b98a8` muted ×126, `#e6edf3` foreground ×103, `#2b3646` panel ×88, `#34d399` green
×65, `#fbbf24` amber ×24, `#f4b9bd` blocked-text pink ×24, `#38bdf8` ×12, `#f87171` ×1.

That is, **the artwork already speaks the reference's language**: dark canvas, monospace, green for
what is done, amber for in-progress, a distinct tone for blocked, percentage-and-fraction in each
region heading (`docs/PRESENTATION.md` records that `region_head` lays the figure out first and
hands the remainder to the title, and `tests/test_progress_matrix.py` fails on any `…` in the
committed bytes). Every region's percentage carries its fraction; 16 fractions appear in the README
and all 16 appear verbatim in the SVG (recomputed in §04's sibling check: 35/35).

The **README text** is where the gap is. `## Progress` (README.md:155–195) is: a 43-word image alt
line; a 12-row × 3-column generated table inside `<!-- pc5:matrix:begin -->`; a one-line `Levels:
L1 4/6 · L2 5/8 …` paragraph; a three-sentence paragraph on why there is no headline percentage; an
`### Milestone progress by workstream` sub-heading with a second image; and a five-sentence
`**How the bars are computed:**` paragraph. `## Dashboard` (README.md:63–98) adds a 14-row table of
the same shape. So: **~40 lines of markdown, three tables, two images and seven prose sentences**
where the reference uses headings, tiles and one footnote.

GitHub renders a 3-column markdown table with generous cell padding and a header rule, so the
*reader's* density is far lower than the file's line count suggests, and the "Where the denominator
comes from" column — the single best feature of our section, because it makes each figure falsifiable
in one click — becomes a wall of small text rather than the footnote it resembles.

## Judgement: what to copy, what must not be copied

**Copy (presentation only, no new claims):**

1. **Figure in the heading.** Make each region read `Milestones verified — 25.7% (9/35)` as the
   left-hand text of a two-column table whose right column is the bar, and demote
   "where the denominator comes from" to a trailing clause, exactly as the reference's asterisk
   footnote does. This is *more* faithful to the SVG than the current table, which restates in prose
   what the artwork already labels.
2. **One asterisk footnote instead of prose paragraphs.** The `**How the bars are computed:**`
   paragraph and the "deliberately not one headline percentage" paragraph are both caveats; the
   reference's idiom puts caveats as one italic line attached to the region they qualify.
3. **Trim the alt text** to what the image is (a screen reader gets the table anyway, since the
   table is the text fallback of the same figure).
4. **Group by area**, not by metric type: `Milestones`, `Evidence`, `Contract`, `Validation` —
   matching the four regions the SVG already has, so the text fallback mirrors the picture's order
   rather than the generator's internal dict order.
5. **Amber strictly for partial.** The reference reserves amber for things that are partly done and
   uses grey for "not attempted"; our table currently has no colour at all, so adopting the rule
   costs nothing and prevents the artwork drifting later.

**Do not copy (these are the constraints, and two of them are why a literal transplant would be a
defect):**

- **The reference's binary green/grey would collapse `blocked` into `not_started`.** PC5 has 6
  blocked milestones (`M1b`, `M17`, `M18`, `M19`, `M20`, `M21`) and 18 not-started ones, and
  "blocked" is a claim about an *unmet dependency*, not about effort. Four states must stay
  distinguishable — complete, in progress, blocked, not started — which is why `pc5-progress.svg`
  draws four bar segments and why nothing here may be flattened to two.
- **Green must keep meaning "a file records it done", not "the code exists".** In the reference,
  green means decoded-from-the-source, which is a *derived* measurement of one artifact. If PC5
  adopted that density the figure would read far rosier than 25.7%, and levels L3–L6 would look
  partly complete. The existing rule — a cell is green only where a repository file records the
  thing as done, and no row is inferred from the row below it — is stricter than the reference's and
  must not be relaxed for the sake of visual similarity.
- **No single headline percentage.** The reference has one per region but no global number; PC5's
  mixed denominators (35 milestones / 116 acceptance items / 64 operations / 38 claims / 11 gates)
  make a global percentage actively misleading. The current sentence saying so stays.
- **Cited ≠ filed, synthetic ≠ physical.** Two of the twelve rows exist to keep those pairs apart
  (`39/39 cited paths resolve, in 19/35 milestones` vs `0/35 milestones with a record filed under
  docs/evidence/`; `Physical-hardware boot evidence — 0 record(s) filed` vs the validation row). The
  reference has no analogue, so any compaction must not merge these rows to look tidier.
- **A screenshot is not a layout spec.** Nothing in the reference licenses adding a region PC5 does
  not measure (no treemap of "libraries" here exists, and inventing one to match the picture would be
  inventing a metric).

## Was anything changed?

**No README presentation change was made in this audit**, and none was needed for the reconciliation
findings: #41's `README.md` diff already replaced the two rows that conflated citation with filing,
and the artwork's region-heading convention already matches the reference's core idea. Changing
`README.md`'s progress markup is a *presentation-layer* change, which by `AGENTS.md` step 5 means
changing `tools/presentation.py`'s text-fallback block, its byte-exactness tests and the committed
figure together — a second, separate piece of work from the correctness fixes on this branch. It is
recorded here as the concrete next change, with the five items above as its spec, rather than being
bundled into a security-relevant fix. Equivalence claim, stated precisely: **the artwork matches the
reference's visual language; the README's markdown is denser in file and looser on screen than the
reference, and items 1–4 would close most of that without touching a number.**
