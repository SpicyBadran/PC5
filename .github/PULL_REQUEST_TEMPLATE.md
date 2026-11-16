## What this changes

<!-- One paragraph. What is different after this pull request, and why. -->

## Which milestone does it serve?

<!-- An id from docs/architecture/roadmap.json, e.g. M2a, or "none — host tooling".
     PC5's destination is genuine Sony PS5 system software on PC hardware; say how this
     connects to it, even if the answer is "as infrastructure for M11". -->

## Evidence, by level

Tick only what is actually true, and say where the evidence is. The levels are not
interchangeable — an emulator pass is not a firmware boot, and neither is a PC boot.

- [ ] **Implemented** — code exists and is reachable
- [ ] **Unit tested** — `make check`
- [ ] **Build verified** — `make` and `make verify` (give the ISO SHA-256)
- [ ] **Emulator executed** — `make boot-test` (give the check count)
- [ ] **Stub firmware executed** — `make stubtest`
- [ ] **Real OVMF executed** — `make ovmf-test` (say which machine and version, or `NOT TESTED`)
- [ ] **Physical hardware tested** — record validated by `tools/hw_record.py`, log committed
- [ ] **Genuine Sony system software executed** — almost certainly not: if you tick this, link the software's own output

## Commands actually run

```sh
# paste the commands and the summary lines they printed
```

## What this does **not** establish

<!-- The honest sentence. Every pull request in this repository has one. -->

## Checklist

- [ ] No test was weakened, skipped or deleted to make this pass.
- [ ] No placeholder driver, stub or double reports success without doing the work.
- [ ] No Sony firmware, keys, dumps or derived bytes were added, extracted or shared.
- [ ] No third-party source archive, asset or project identity was vendored (ADR-025).
- [ ] Generated files (the six figures and the five README blocks) were regenerated with `make presentation`, and `make presentation-check` passes.
- [ ] If I ran a gate, the run is recorded with `python3 tools/validation.py run …`, and `make validation` passes — no hand-written or edited records.
- [ ] `make docs-check` passes: links, anchors and documented commands resolve.
- [ ] Claims in documentation are no stronger than the evidence above.
