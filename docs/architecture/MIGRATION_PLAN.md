# Migration plan — current tree to the dual-track structure

The directive is explicit: do not reorganise the repository before mapping it.
The mapping is in `REPOSITORY_AUDIT_2026-11-15.md`; this is the move plan.

## 1. Principles

1. **Nothing moves without a test protecting the move.** Every phase ends with
   `make verify`, `make check`, `make boot-test` and — where the boot path is
   touched — `make stubtest`.
2. **One phase, one concern, one revertible commit.** A phase that renames a
   directory *and* changes a driver is two phases pretending to be one.
3. **No empty destination directories.** `desktop/` appears when the first shell
   code lands, not before. An empty directory is a claim.
4. **The build interface is stable.** `make iso`, `make check`, `make unit`,
   `make verify`, `make boot-test`, `make stubtest`, `make metadata` and
   `make release-assets` keep their names and behaviour throughout. New targets
   may be added; existing ones are never renamed.
5. **Preservation is a hard requirement** (ADR-009, invariant I4). The loader,
   the diagnostic kernel and their evidence must still work after every phase,
   for the same reason the recovery environment exists at all.

## 2. Current → target mapping

Target tree from the directive, with the real origin of each destination.

| Target | Comes from | Exists now | Phase |
|---|---|---|---|
| `boot/uefi/` | `boot/uefi/` | yes | — (already correct) |
| `boot/bios/` | `boot/loader.S` | file exists, path differs | 2 |
| `boot/recovery/` | new: recovery entry, tools, docs | no | 4 |
| `kernel/legacy-pc5/` | `kernel/` | yes, path differs | 2 |
| `include/pc5/` | `kernel/include/pc5.h` (contract split) | no | 1 |
| `system/linux/` | new: Linux config, image recipe | no | 4 |
| `system/services/` | `kernel/ui.c` reporting, later session manager | partial | 4–5 |
| `system/hardware/` | inventory sources (`cpu.c`, `pci.c`, `acpi.c`, `smbios.c`, `mem.c`) | no | 2 |
| `system/updates/` | `tools/buildmeta.py`, release manifest | partial | 5 |
| `drivers/` | new: out-of-tree drivers only (not present) | no | 5 |
| `desktop/{shell,launcher,settings}` | new | no | 5 |
| `playstation/compatibility/` | Track B runtime (new) | no | 6 |
| `playstation/prospero-research/` | `docs/playstation/`, `docs/FIRMWARE_ANALYSIS.md` | docs only | 6 |
| `playstation/firmware-analysis/` | `tools/firmware.py` | tool exists at another path | 6 |
| `tests/{unit,integration,emulator,hardware}` | `tests/` (flat, working) | flat | 3 |
| `docs/{architecture,roadmap,compatibility}` | `docs/` (flat), this directory | partial | 0, 3 |
| `releases/` | release artefacts (never committed) | no — and stays out of git | — |

Note the last row: the directive's `releases/` is a distribution directory, not a
tracked one. Build outputs stay in `build/` and are published as release assets;
committing binaries would break reproducibility and repository hygiene.

## 3. Phases

### Phase 0 — Architecture freeze (this change)

**Move:** none. Adds `docs/architecture/`, ADR-009 … ADR-012, `tools/roadmap.py`,
`tests/test_architecture.py`, and corrects nine documented contradictions.

**Why no move:** moving files now would invalidate every evidence path quoted in
the audit and every test that reads a source path, for no functional gain.

**Gate:** `make check` passes with the new tests; `make verify` and
`make boot-test` unchanged; `python3 tools/roadmap.py check` reports zero errors.

**Rollback:** delete the new files and ADRs; nothing existing was modified except
documentation corrections, each of which is one hunk.

### Phase 1 — Split the boot contract out of the kernel header

**Move:** the parts of `kernel/include/pc5.h` that both loaders and the kernel
need (`struct pc5_boot_info`, VBE and E820 structures, the `PC5K` header macros)
to `include/pc5/boot.h`; `kernel/include/pc5.h` includes it.

**Why first:** it is the smallest possible move that touches the boot path, so it
proves the preservation process before anything large depends on it. `PC5-BOOT`
is frozen by invariant I3, which makes this a pure relocation.

**Gate:** `make verify` (53 checks), `make check` (all host tests), `make
boot-test` (25 checks), `make stubtest` (42 checks), and the `_Static_assert`
offsets still compile. Byte-identical ISO for the same `SOURCE_DATE_EPOCH` is
not required (include paths change debug info), so the gate is behavioural, not
bitwise.

**Rollback:** revert the commit; no data exists to lose.

### Phase 2 — Separate the legacy kernel and its hardware inventory

**Move:**

```
boot/loader.S              -> boot/bios/loader.S
kernel/*.c, kernel/*.S     -> kernel/legacy-pc5/
kernel/include/pc5.h       -> kernel/legacy-pc5/include/pc5.h
kernel/kernel.ld           -> kernel/legacy-pc5/kernel.ld
kernel/cpu.c, mem.c, pci.c, acpi.c, smbios.c
                           -> system/hardware/ (shared inventory sources)
```

The inventory sources move to `system/hardware/` only if they can still be built
into the legacy kernel without a shim. If they cannot, they stay under
`kernel/legacy-pc5/` and the migration is recorded as "not worth the coupling" —
a destination is not an obligation.

**Reference updates:** `Makefile` source lists and include paths; `tests/*.py`
files that read source text (the UEFI tests read the Makefile and loader sources
by path); every `docs/` reference; `README.md` tree references.

**Gate:** the full suite of Phase 1, plus `grep -r "kernel/" docs README.md` returns
no stale path, plus the documentation link check (`tests/test_architecture.py`).

**Rollback:** single revert; the phase contains no logic change, so the suites
must pass bit-for-bit identically.

### Phase 3 — Test and documentation layout

**Move:**

```
tests/kernel_unit.c, test_tools.py, test_uefi.py, test_release.py -> tests/unit/
tests/boot_test.py, uefi_stubtest.py, uefi_stub_firmware.py,
v86_runner.mjs                                                   -> tests/emulator/
tests/test_architecture.py                                       -> tests/integration/
docs/ARCHITECTURE.md, TECHNICAL_DECISIONS.md, TESTING_STRATEGY.md -> docs/architecture/
docs/ROADMAP.md, docs/architecture/M0_M8_DEPENDENCY_GRAPH.md     -> docs/roadmap/
docs/{PS5_*,GAMING_COMPATIBILITY,FIRMWARE_ANALYSIS}.md           -> docs/compatibility/
docs/playstation/                                                -> docs/compatibility/research/
```

`tests/hardware/` stays empty until the first hardware record exists (§6 of the
hardware strategy defines its format).

**Gate:** `make check` discovers the moved tests from their new locations — the
Makefile's `unittest discover` invocation and every hard-coded path must be
updated in the same commit. This phase is the one most likely to break CI-style
discovery, so it is deliberately after the boot path is stable.

### Phase 4 — Linux foundation and recovery entry

**Add (no move):**

```
system/linux/        kernel config fragment, firmware list, image recipe
system/services/     PC5 platform services as they are implemented
boot/recovery/       recovery entry, tool set, documentation
```

**Gate:** the Linux image builds reproducibly on a capable host, boots on
REF-AMD or REF-INTEL, and produces `PC5-LOG` lines. The legacy medium must still
boot on the same machine with the recovery entry selected. No existing target may
change behaviour.

**Precondition (blocking today):** a build host with `flex`, `bison`, `bc`,
libssl/zlib headers and either KVM or a reference machine. See ADR-009's exit
condition.

### Phase 5 — OS surface

**Add:** `desktop/`, `system/updates/`, `drivers/` (only for drivers that are
genuinely out-of-tree), `system/services/` growth.

**Gate:** M3 acceptance criteria; installer and update rollback proven; the
recovery entry still boots the machine that the new system just failed on.

### Phase 6 — Track B extraction

**Move/add:**

```
tools/firmware.py, docs research material -> playstation/firmware-analysis/, playstation/prospero-research/
Track B runtime (new)                     -> playstation/compatibility/{elf,prx,syscalls,graphics}
vendor/anyps5/README.md                   -> playstation/compatibility/anyps5/
```

**Gate:** a Track A build with `playstation/` removed from the tree still builds
and passes every suite. Until that is demonstrated, Track B may not be described
as isolated.

## 4. Preservation guarantees

| Guarantee | Checked by | Phase that could break it |
|---|---|---|
| BIOS boot works | `make boot-test` | 1, 2 |
| UEFI loader logic works | `make stubtest` | 1, 2 |
| Boot contract offsets hold | compile-time `_Static_assert`s | 1 |
| ISO structure and checksums | `make verify` | 1, 2, 3 |
| Kernel reads its own medium | boot-test storage assertions | 2 |
| Chain reporting stays honest | `tests/kernel_unit.c` | 2 |
| Reproducible artefacts | `tests/test_tools.py` | 2, 3 |
| Documentation paths resolve | `tests/test_architecture.py` | 2, 3, 4 |
| Track B does not gate Track A | `tools/roadmap.py` track gate | 6 |

## 5. Things this plan refuses to do

* **Create the target tree before its contents exist.** Placeholder directories
  and "coming soon" READMEs are how projects acquire structure without progress.
* **Move `kernel/` because the target says `kernel/legacy-pc5/`.** The rename
  happens with the inventory split, when there is a second kernel to distinguish
  it from. Until then `kernel/` is the only kernel and the name is honest.
* **Delete the 19 MB AnyPS5 archive as part of "cleanup".** Its provenance is
  audit evidence. Recommendation: publish it as a release asset or record its
  upstream commit and remove it, as a separate owner decision (phase 5 at the
  earliest).
* **Rename build targets.** Documentation, CI and muscle memory depend on them.
* **Split the repository** into Track A / Track B repos. A separate repository
  would make the Track A/B gate invisible; a dependency graph plus a build
  boundary is stronger and cheaper.

## 6. Rollback

Every phase is a single commit that changes paths or adds files; none rewrites
history and none deletes evidence. Reverting the commit restores the previous
tree exactly. The only irreversible actions in this plan are deleting the zip
archive and force-rewriting history — both are excluded.
