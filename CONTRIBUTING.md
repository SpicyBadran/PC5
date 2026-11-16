# Contributing to PC5

PC5 is one platform with one destination: genuine Sony PlayStation 5 system software
running on qualified PC hardware, through PC5's own boot path and platform model. Work
that serves that destination is welcome; work that redefines it is not.

## Before you start

Read [`docs/architecture/PC5_ARCHITECTURE_SPEC_v2.md`](docs/architecture/PC5_ARCHITECTURE_SPEC_v2.md)
and [`docs/ROADMAP.md`](docs/ROADMAP.md). The milestone graph in
[`docs/architecture/roadmap.json`](docs/architecture/roadmap.json) is the authority on
what exists, what is next and what is blocked; `python3 tools/roadmap.py path` prints
the chain to the destination.

## Non-negotiable rules

1. **Do not weaken evidence.** No skipped tests, no relaxed gates, no placeholder
   driver that reports success without controlling the device. A new claim needs a
   test that would fail if the claim were false.
2. **Keep the levels apart.** An emulator pass is not a firmware boot, a firmware boot
   is not a physical boot, and a PC5 kernel boot is not PS5 system software executing.
3. **Never claim Sony software ran.** PC5 has not loaded the system software, and
   nothing in a contribution may imply it has. The PUP in the tree is a pointer; it is
   never extracted, decrypted, modified or redistributed (ADR-022).
4. **No circumvention.** No key extraction, signature bypass, secure-boot defeat,
   firmware decryption or authentication bypass — in code, tests or documentation. If a
   Sony requirement cannot lawfully be met, it is recorded as a blocker.
5. **Original work only.** No vendored archives, no borrowed project identity, no
   imported assets (ADR-025). `tests/test_project_independence.py` enforces it.
6. **Facts, not adjectives.** A published specification does not establish an internal
   register, a firmware interface or a boot process. Cite what you measured.

## The loop

```sh
make            # build
make check      # host + kernel tests, platform contract, documentation guards
make verify     # structural verification of the image and its manifest
make roadmap    # the graph still validates
```

If you touch the milestone graph, run `python3 tools/roadmap.py sync` so the rendered
document matches the data. If you touch the README or an architecture figure, run
`make presentation` — it regenerates the status table and figures from `roadmap.json`
and fails if a hand edit overstates progress.

If you have physical hardware, the most valuable contribution is
[`docs/hardware/M1_PHYSICAL_BOOT_PROCEDURE.md`](docs/hardware/M1_PHYSICAL_BOOT_PROCEDURE.md):
a filled record validated by `tools/hw_record.py`, with the serial log committed
verbatim. A screenshot is not a substitute.

## Commit and review

Focused commits with a message that says what changed and what it proves. Branches
never rewrite published history. A pull request states the evidence it adds, the gates
it ran and what it does not establish; a claim without the command that produced it is
reviewed as a claim, not as a result.
