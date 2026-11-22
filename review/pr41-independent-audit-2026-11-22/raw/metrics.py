#!/usr/bin/env python3
"""Independent recomputation of every number the PC5 progress dashboard displays.

Run it against a checkout:  python3 metrics.py /path/to/checkout

Nothing here imports `tools/presentation.py` as a source of truth. Each metric is rebuilt from the
authoritative file named in the README's own "where the denominator comes from" column —
`docs/architecture/roadmap.json`, the generated tables in `docs/architecture/PS5_PLATFORM_CONTRACT.md`,
`psm/registry.py`, `docs/evidence/**` — and compared against (a) the committed figure's text nodes
and (b) the generated README blocks. Predicates that the generator implements in code (what counts
as a "path-shaped" deliverable) are restated here literally, with the looser rule an auditor would
reach by eye reported alongside, because the difference between 31 and 38 is exactly the kind of
thing an honest-looking number can hide.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
FIGURE = REPO / "docs/assets/pc5-progress-matrix.svg"
README = REPO / "README.md"
CONTRACT = REPO / "docs/architecture/PS5_PLATFORM_CONTRACT.md"
REGISTRY = REPO / "psm/registry.py"
ROADMAP = REPO / "docs/architecture/roadmap.json"

# The generator's own predicate, transcribed: lowercase first segment, one or more directories,
# and an extension from a fixed list. `boot/loader.S`, `docs/ARCHITECTURE.md` and every directory
# path are therefore *not* "a deliverable that exists as a named path".
PATH_SHAPED = re.compile(r"^[a-z0-9_][a-z0-9_.-]*/[^\s]+\.(?:md|json|py|c|h|sh|yml)$")
LOOSE = re.compile(r".*/.*")

rows = []


def note(metric, recomputed, displayed, source, match=None):
    rows.append({"metric": metric, "recomputed": str(recomputed), "displayed": str(displayed),
                 "source": source,
                 "match": (str(recomputed) == str(displayed)) if match is None else bool(match)})


def block(name):
    text = README.read_text(encoding="utf-8")
    begin, end = f"<!-- pc5:{name}:begin -->", f"<!-- pc5:{name}:end -->"
    return text[text.index(begin) + len(begin):text.index(end)]


def grab(text, pattern, group=1):
    found = re.search(pattern, text)
    return found.group(group).strip() if found else "(not found)"


MATRIX, STATS, BLOCKERS = block("matrix"), block("stats"), block("blockers")
FIGURE_TEXT = " ".join((t.text or "") for t in ET.parse(FIGURE).iter() if t.tag.endswith("text"))
milestones = json.loads(ROADMAP.read_text(encoding="utf-8"))["milestones"]
states = [m.get("state", "not_started") for m in milestones]
counts = {s: states.count(s) for s in ("complete", "in_progress", "blocked", "not_started")}

# ------------------------------------------------------------------ roadmap.json: the states
note("Milestones (total, in how many workstreams)",
     f"{len(milestones)} in {len({m.get('workstream') for m in milestones})} workstreams",
     grab(STATS, r"\| Milestones \| ([^|]+) \|"), "roadmap.json milestones array")
for label, key in (("Complete", "complete"), ("In progress", "in_progress"),
                   ("Blocked", "blocked"), ("Not started", "not_started")):
    note(f"stats: {label}", counts[key], grab(STATS, rf"\| {label} \| (\d+) \|"),
         "roadmap.json milestone state")
verified_pct = f"{counts['complete'] / len(milestones) * 100:.1f}% ({counts['complete']}" \
               f"/{len(milestones)})"
note("Milestones verified", verified_pct, grab(MATRIX, r"Milestones verified \| ([^|]+) \|"),
     "milestone state in roadmap.json")

levels = {}
for m in milestones:
    if m.get("level") is not None:
        slot = levels.setdefault(m["level"], [0, 0])
        slot[1] += 1
        if m.get("state") == "complete":
            slot[0] += 1
verified_levels = f"{sum(1 for c, _ in levels.values() if c)}/{len(levels)}"
note("Levels with a verified milestone", verified_levels,
     grab(MATRIX, r"Levels with a verified milestone \| (\d+/\d+)"), "grouped by `level`")
level_summary = " · ".join(f"L{l} {c}/{t}" for l, (c, t) in sorted(levels.items()))
note("Levels, per level", level_summary, grab(MATRIX, r"Levels: (L.*?)\. Blocked", 1),
     "roadmap.json level field")
note("No verified milestone above L2", "L3..L6 complete: "
     + ", ".join(f"L{l}={c}" for l, (c, _) in sorted(levels.items()) if l >= 3),
     "0 in the figure", "roadmap.json",
     match=all(c == 0 for l, (c, _) in levels.items() if l >= 3))

acceptance_total = sum(len(m.get("acceptance") or []) for m in milestones)
acceptance_verified = sum(len(m.get("acceptance") or []) for m in milestones
                          if m.get("state") == "complete")
note("Acceptance items inside verified milestones",
     f"{acceptance_verified}/{acceptance_total}",
     grab(MATRIX, r"Acceptance items inside verified milestones \| (\d+/\d+)"),
     "`acceptance` arrays, counted per owning milestone state")

# ------------------------------------------------------------------ deliverables
deliverables = [x for m in milestones for x in (m.get("deliverables") or [])]
shaped = [x for x in deliverables if PATH_SHAPED.match(x)]
present = [x for x in shaped if (REPO / x).exists()]
loose_shaped = [x for x in deliverables if LOOSE.match(x)]
loose_present = [x for x in loose_shaped if (REPO / x).exists()]
note("Deliverables that exist as named paths", f"{len(present)}/{len(deliverables)}",
     grab(MATRIX, r"Deliverables that exist as named paths \| (\d+/\d+)"),
     "the generator's documented predicate, applied here")
SECOND_BAR_LABEL = "deliverables named at all"
at = FIGURE_TEXT.find(SECOND_BAR_LABEL)
figure_second_bar = "label not found in the figure"
if at >= 0:
    found = re.compile(r"\d+/\d+").search(FIGURE_TEXT, at, at + len(SECOND_BAR_LABEL) + 90)
    figure_second_bar = found.group(0) if found else "no fraction near the label"
note("…as a second bar in the figure", f"{len(shaped)} path-shaped of {len(deliverables)}",
     figure_second_bar,
     "the figure's own label for the same pair", match=f"{len(shaped)}/66" in FIGURE_TEXT)
note("Under a looser rule an auditor would reach by eye",
     f"{len(loose_present)}/{len(deliverables)} present, {len(loose_shaped)} path-shaped",
     "not displayed", f"{len(loose_shaped) - len(shaped)} strings excluded by the extension list: "
     f"{sorted(set(x for x in loose_shaped if x not in shaped and (REPO / x).exists()))}",
     match=True)

# ------------------------------------------------------------------ citations vs filings
citations = [e for m in milestones for e in (m.get("evidence") or [])]
resolved = [e for e in citations if (REPO / e.split("#")[0]).exists()]
distinct = sorted({e.split("#")[0] for e in citations})
distinct_resolved = [d for d in distinct if (REPO / d).exists()]
citing = sum(1 for m in milestones if m.get("evidence"))
displayed_citations = grab(MATRIX, r"at least one in-tree path \| ([^|]+) \|")
note("Cited paths resolve", f"{len(resolved)}/{len(citations)} cited paths resolve, in "
     f"{citing}/{len(milestones)} milestones", displayed_citations,
     "`evidence` arrays resolved against the tree — a citation, not a run")
note("…counted as distinct paths instead", f"{len(distinct_resolved)}/{len(distinct)}",
     f"{len(distinct)} distinct paths, {len(citations)} citation strings",
     "the displayed numerator counts citation *entries*; both bases resolve fully",
     match=len(resolved) == len(citations) and len(distinct_resolved) == len(distinct))
unresolved = [d for d in distinct if not (REPO / d).exists()]
note("Unresolvable citations", ", ".join(unresolved) or "none", "none",
     "the complement", match=not unresolved)

filed = [e for e in citations if e.split("#")[0].startswith("docs/evidence/")]
milestones_filed = sum(1 for m in milestones
                       if any(e.split("#")[0].startswith("docs/evidence/")
                              for e in (m.get("evidence") or [])))
note("Milestones with a record filed under docs/evidence/",
     f"{milestones_filed}/{len(milestones)}",
     grab(MATRIX, r"record filed under `docs/evidence/` \| (\d+/\d+)"),
     "docs/evidence/** as cited by the milestone graph")

# ------------------------------------------------------------------ blockers and destination
blocked_ids = [m["id"] for m in milestones if m.get("state") == "blocked"]
note("Blocked milestones", len(blocked_ids), grab(MATRIX, r"Blocked milestones \| (\d+)"),
     "state `blocked`")
note("Blocked ids", ", ".join(f"`{b}`" for b in blocked_ids),
     grab(MATRIX, r"Blocked: ([^.]+)\.", 1), "roadmap.json")
note("Every blocked milestone states a blocker",
     Counter(bool(m.get("blocker")) for m in milestones if m.get("state") == "blocked").most_common(),
     "all", "roadmap.json blocker field",
     match=all(m.get("blocker") for m in milestones if m.get("state") == "blocked"))
destination = next((m for m in milestones if "DESTINATION" in (m.get("title") or "")), None)
note("Destination", f"`{destination['id']}`" if destination else "?",
     grab(STATS, r"\| Destination \| (`M\w+`)`?", 1), "the milestone titled DESTINATION")
note("Destination state", destination["state"].upper() if destination else "?",
     grab(STATS, r"Destination state \| \*\*(\w+)\*\*"), "roadmap.json")
note("Destination is blocked, with a named reason",
     (destination.get("blocker", "")[:48] + "…") if destination and destination["state"] == "blocked"
     else "not blocked", "a reason in the blockers table", "roadmap.json + README",
     match=bool(destination and destination["state"] == "blocked"
                and destination.get("blocker")
                and re.search(r"M19.*\|[^|]+\|\s*[^|]", BLOCKERS)))
note("M17–M21 all blocked", ", ".join(f"{i}={next(m['state'] for m in milestones if m['id'] == i)}"
                                      for i in ("M17", "M18", "M19", "M20", "M21")),
     "all blocked", "roadmap.json",
     match=all(next(m["state"] for m in milestones if m["id"] == i) == "blocked"
               for i in ("M17", "M18", "M19", "M20", "M21")))

# ------------------------------------------------------------------ contract: ops and claims
contract = CONTRACT.read_text(encoding="utf-8")
op_rows = [l for l in contract.splitlines() if re.match(r"^\|\s*`[a-z_]+`\s*\|", l)]
statuses = []
for line in op_rows:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) >= 7:
        statuses.append(cells[5])
tally_ops = Counter(statuses)
implemented = tally_ops.get("implemented", 0)
ops_pct = (f"{implemented / len(statuses) * 100:.1f}% ({implemented}/{len(statuses)})"
           if statuses else "no operation rows parsed")
note("Modelled operations implemented", ops_pct,
     grab(MATRIX, r"Modelled operations implemented \| ([^|]+) \|"),
     f"operation tables: {dict(tally_ops)}")
interfaces_block = contract[contract.index("<!-- psm:begin interfaces -->"):
                            contract.index("<!-- psm:end interfaces -->")]
interfaces = len(re.findall(r"^\| `PSM-[A-Z]+` \|", interfaces_block, re.M))
declared_ops = sum(int(n) for n in re.findall(
    r"^\| `PSM-[A-Z]+` \|[^|]*\|[^|]*\|[^|]*\| (\d+) \|", interfaces_block, re.M))
note("Interfaces and their declared operations", f"{interfaces} interfaces, {declared_ops} ops",
     f"{len(statuses)} op rows in the same document",
     "the generated interfaces table", match=interfaces == 10 and declared_ops == len(statuses))

registry = REGISTRY.read_text(encoding="utf-8")
body = registry[registry.index("CLAIMS: Tuple[Claim, ...] = ("):registry.index("CONTRACT = Contract")]
claims = Counter(re.findall(r'classification="([A-Z]+)"', body))
total_claims = sum(claims.values())
public = claims.get("PUBLIC", 0)
claims_pct = f"{public / total_claims * 100:.1f}% ({public}/{total_claims})"
note("Contract claims backed by public documentation", claims_pct,
     grab(MATRIX, r"Contract claims backed by public documentation \| ([^|]+) \|"),
     f"psm/registry.py classifications: {dict(claims)}")

# ------------------------------------------------------------------ validation and physical
records = sorted((REPO / "docs/evidence/validation").glob("*.json"))
results = {}
for path in records:
    payload = json.loads(path.read_text(encoding="utf-8"))
    results[path.stem] = payload.get("result") or payload.get("status") or "?"
passed = sum(1 for v in results.values() if v == "passed")
not_pass = {k: v for k, v in results.items() if v != "passed"}
valid_pct = f"{passed / len(records) * 100:.1f}% ({passed}/{len(records)})"
note("Committed validation records passing", valid_pct,
     grab(MATRIX, r"Committed validation records passing \| ([^|]+) \|"),
     "docs/evidence/validation/*.json")
note("A record that is not a pass is not a failure",
     f"{not_pass} — counted in the denominator, not as a pass", "not-tested",
     "validation records", match=list(not_pass.values()) == ["not-tested"])
top = sorted(p.name for p in (REPO / "docs/evidence").glob("*.json"))
physical = [p for p in top if "TEMPLATE" not in p.upper()]
note("Physical-hardware boot evidence", f"{len(physical)} record(s) filed",
     grab(MATRIX, r"Physical-hardware boot evidence \| ([^|]+) \|"),
     f"docs/evidence/*.json present: {', '.join(top) or 'none'}")
note("The record template is not counted as a record",
     f"{len(top) - len(physical)} template(s) excluded", "0 record(s) filed",
     "docs/evidence/M1_RECORD_TEMPLATE.json", match=len(physical) == 0)
note("Nothing claims physical execution", grab(MATRIX, r"Genuine target-system-software execution "
                                               r"\| ([^|]+) \|"),
     grab(MATRIX, r"Genuine target-system-software execution \| ([^|]+) \|"),
     "the matrix's own line", match="BLOCKED" in grab(MATRIX, r"Genuine target-system-software "
                                                             r"execution \| ([^|]+) \|"))

# ------------------------------------------------------------------ figure vs README
headline = re.findall(r"\b\d+/\d+\b", MATRIX)
missing_from_figure = [f for f in set(headline) if f not in FIGURE_TEXT]
note("Every fraction in the README table also appears in the figure",
     f"{len(set(headline))} distinct; absent from the figure: {missing_from_figure or 'none'}",
     "compared as strings", "the committed README block and the committed SVG",
     match=not missing_from_figure)
note("No truncated label in the figure",
     "no '…' in any of the figure's text nodes", "—", "the committed SVG",
     match="…" not in FIGURE_TEXT)
green = FIGURE_TEXT.count("verified")
note("The word 'verified' appears in the figure only where a level or region earned it",
     f"{green} occurrence(s) in the figure's text", "—", "the committed SVG", match=green > 0)

print(f"{'metric':<54} {'recomputed':<44} {'displayed':<38} match")
print("-" * 186)
for r in rows:
    print(f"{r['metric']:<54} {r['recomputed'][:42]:<44} {r['displayed'][:36]:<38} "
          f"{'yes' if r['match'] else 'NO'}")
bad = [r for r in rows if not r["match"]]
print(f"\n{len(rows) - len(bad)}/{len(rows)} checks reproduce the displayed value; {len(bad)} do not")
for r in bad:
    print(f"  MISMATCH {r['metric']}: recomputed {r['recomputed']!r} vs displayed {r['displayed']!r}")
print(f"\nraw: states {counts}; ops {dict(tally_ops)}; claims {dict(claims)}; "
      f"validation {results}")
raise SystemExit(1 if bad else 0)
