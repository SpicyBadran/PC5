# Mission-alignment discrepancy report — 2026-11-16

**Scope.** This report records an independent check of the repository against the
mission statement *"PC5 exists to make genuine Sony PlayStation 5 system software
run on compatible ordinary PC hardware; Sony's software is the operating system and
the user experience, and PC5 is the PC-side infrastructure required to run it."*
It was written at PR #26 head `170e611dcdfa3fe12e9abe58c2e60ee323aa461c`, on branch
`arena/af939eb1-pc5`, and re-checked after the source corrections described below.

**Final state of this pass.** The three workstreams are committed on
`arena/17f0c4f8-pc5` on top of `170e611`: `92f0678` (the 64-bit fixes),
`71e55a5` (the first correction pass and this report), `687f127` (the brand),
`32f3a14` (the second correction pass) and two evidence commits. Every gate
was re-recorded at a clean `32f3a14`; `make check` runs 653 tests with zero
skips, and `make ovmf-test` remains NOT TESTED because no OVMF or QEMU exists
on this machine. PR #26 is still open and still an infrastructure change: it
moves no milestone.

The hierarchy the rest of the repository is judged against:

```
genuine Sony PS5 system software   = the operating system, services and user experience
PC5                               = the PC-side boot/hardware/compatibility infrastructure
PC5 diagnostic UI                 = engineering, recovery and troubleshooting only;
                                     never represented as the genuine PS5 interface
```

---

## 1. Branch, PR and artifact state (verified)

| Item | Value | How verified |
|---|---|---|
| Branch | `arena/af939eb1-pc5` | `gh pr view 26` |
| PR | #26, **OPEN**, mergeable | `gh pr view 26` |
| Head at review | `170e611dcdfa3fe12e9abe58c2e60ee323aa461c` | `gh pr view 26 --json headRefOid` |
| Base | `main` @ `8aab6d3864bb2e867a70bba28e7bfb52f43d7e35` | `gh pr view 26 --json baseRefName` |
| Release image | still 32-bit; `make -n all` names no 64-bit target | `tests/test_kernel64.py::ReleaseIsolationTests` |

## 2. The protected input (`PS5UPDATE.PUP`) — what is actually known

The working tree holds a 135-byte Git LFS pointer, not a payload. This was
re-verified directly, read-only:

```
$ wc -c PS5UPDATE.PUP
135 PS5UPDATE.PUP
$ sha256sum PS5UPDATE.PUP
6e8f5a459551316397f1946cce4d540a56c0b790854c2c0d94c442ca86e73b42  PS5UPDATE.PUP
$ cat PS5UPDATE.PUP
version https://git-lfs.github.com/spec/v1
oid sha256:42ea0e5aad1361e098837d40157863c4c519f0bbc66eb03c04dbaaf339b063fb
size 1423697408
```

A read-only LFS batch-metadata query (no payload bytes were requested or fetched)
answered:

```json
{"objects":[{"oid":"42ea0e5a…063fb","size":1423697408,
  "actions":{"download":{"href":"https://github-cloud.githubusercontent.com/…",
  "expires_at":"2026-11-16T11:51:00Z"}}}]}
```

Established by that query: the object **exists in LFS storage**, and its stored
`size` equals the pointer's declared `size` (1,423,697,408 bytes). Nothing else is
established. In particular:

| Question | Status |
|---|---|
| Payload fetched or read | **no** — only the pointer and a metadata record |
| SHA-256 of the payload | **unknown** — the object was not downloaded |
| Package type (full reinstallation vs incremental) | **unknown** |
| System-software version | **unknown** |
| Internal structure / components | **unknown** |
| Any claim that the PUP has been analysed | **false** |

The download host (`github-cloud.githubusercontent.com`) is not reachable from the
build environment, and fetching the payload is outside the authorised scope in any
case. The next lawful action is the one already written in
[`PS5_SYSTEM_SOFTWARE_TARGET.md`](../PS5_SYSTEM_SOFTWARE_TARGET.md) §3.1: an
owner-supplied copy, outside the tree, hash-recorded before and after, analysed
with `tools/firmware.py analyze` and no decryption or signature bypass. Until that
happens, every statement about the package's contents remains `NOT VERIFIED`.

## 3. Contradictions found, and what was corrected

The repository has already been through an architecture-discrepancy pass; the
remaining items were narrow.

| # | File | Before | After |
|---|---|---|---|
| 1 | `README.md` ("Read this first") | "no lawful path exists" | "no lawful route has been established yet" — the mission forbids declaring a route impossible without evidence; the accurate statement is that none has been established |
| 2 | `docs/ARCHITECTURE.md` §7.3 | memory-map row described only per-descriptor consistency | states the set-level overlap rule and that it runs before any coverage decision |
| 3 | `docs/ARCHITECTURE.md` §7.3 | framebuffer validation did not mention pixel format | states only the two direct-colour layouts are interpretable; bit-mask and `PixelBltOnly` are unusable |
| 4 | `docs/ARCHITECTURE.md` §7.6 | CPUID widths described without the gating rule | states every extended leaf is gated on the saved maximum, and an unreported width is reported as `not-reported`, never as "everything fits" |

No document was found that (a) claims PC5 is building a substitute OS, (b) presents
a PC5-authored shell as the PS5 experience, (c) describes a PC5 setup wizard as the
genuine Sony installation, or (d) treats a synthetic payload as Sony execution. The
authorities that pin those distinctions are
[`PC5_ARCHITECTURE_SPEC_v2.md`](PC5_ARCHITECTURE_SPEC_v2.md) §4/§6 ("the guest is
Sony's; the model is PC5's"; "PC5 never substitutes its own kernel, shell or
services for the guest's") and
[`PS5_SYSTEM_SOFTWARE_TARGET.md`](../PS5_SYSTEM_SOFTWARE_TARGET.md) §7 (what counts as
level 5). `docs/architecture/MIGRATION_PLAN.md` still names `desktop/{shell,…}` but
is explicitly marked **Historical** and is exempted as such in
`product.forbidden_claims`; it is not a current plan.

### 3.1 Second pass — the remaining documents

The first pass covered the entry-point documents. The second read every
`docs/playstation/*` file, `docs/ROADMAP.md` and
`docs/architecture/PS5_PLATFORM_CONTRACT.md` against `roadmap.json` proper. Seven
more contradictions were found, each checked against the data rather than
against another document.

| # | File | Before | After | Ground truth |
|---|---|---|---|---|
| 5 | `docs/ROADMAP.md` §2 | "Eight milestones are complete" | "Nine" | `roadmap.json` has 9 `complete` |
| 6 | `docs/ROADMAP.md` §4 | chain printed `M1 -> M2 -> M2a` | `M1 -> M2a -> M2` | `M2` depends on `M2a`; `tools/roadmap.py path` prints `M2a -> M2` |
| 7 | `docs/ROADMAP.md` §3, §5 | "twelve PS5 milestones"; WS6 owns `M14` | "thirteen"; WS6 owns `M7` only | WS10 has 13 ids; `M14` is WS10 |
| 8 | `docs/ROADMAP.md` §1; `PS5_COMPATIBILITY_STATUS.md` §1 | level 4 "…interfaces exist and are validated" | "…and are experimentally validated" | `product.levels[3].statement` |
| 9 | `docs/ROADMAP.md` §1 | level 6 "not started (`M4`, `M5`, `M20`)" | "`M4`, `M5` not started; `M20` blocked" | `M20.state == "blocked"` |
| 10 | `docs/RECOVERY.md` §5; `docs/UPDATE_SYSTEM.md` §1 | recovery environment is `M1b`, blocked on `M8` | belongs to `M8`; `M1b` is CI/release publication | `M1b` depends only on `M0` and is blocked on a token permission |
| 11 | `PC5_ARCHITECTURE_SPEC_v2.md` §3, §6 | "thirteen workstreams"; `M10` "complete" | fourteen; `M10` acceptance met, `in_progress` | 14 workstreams; `M10.state == "in_progress"` |
| 12 | `PC5_ARCHITECTURE_SPEC_v2.md`, `PC5_MILESTONE_GRAPH.md`, `ROADMAP.md`, `BOOT_FEASIBILITY.md` | destination path called "the longest chain in the graph" | "the longest chain that ends at the destination" | `critical_path()` is one longer — it continues to `M20` |
| 13 | `docs/architecture/README.md` | "ADR-001 … ADR-022" | "ADR-001 … ADR-025" | `TECHNICAL_DECISIONS.md` has 25 ADRs |
| 14 | `docs/PS5_COMPATIBILITY_STATUS.md` | "twelve project levels"; "16 commands"; "`M2` merged" | thirteen chain stages; 19 dispatchable commands; `M2` is `not_started` | `CHAIN_STAGE_COUNT 13`; 17 exact + 2 prefix handlers; `M2.state` |
| 15 | `docs/PS5_COMPATIBILITY_ARCHITECTURE.md` §1 | a sentence with no subject between two full stops | rejoined | the dropped clause was "The two paths are reported side by side" |
| 16 | `docs/architecture/PS5_PLATFORM_CONTRACT.md` §9 | "consumed by `M11`–`M17`" | 2 direct dependants named; the transitive chain stated | `M11` and `M17` depend on `M10` directly |

Two of these (#12, #15) were introduced by the documents themselves rather than
by the roadmap change, and one (#8) is a level statement that must be quoted
verbatim from `product.levels` because `tests/test_product_identity.py` pins the
level set. `tools/roadmap.py sync` regenerated the milestone graph after the
renderer's own label changed, so the generated block and the prose agree.

Nothing in this pass changed a milestone state, a dependency, a blocker or an
acceptance criterion. The data was treated as authoritative and the prose was
moved to it — never the reverse, which is the failure mode the governance
documents exist to prevent.

## 4. PR #26 reassessed against the mission

PR #26 is **infrastructure**, and its own text says so. It does not establish that
genuine PS5 software can execute. The distinct milestones, never combined:

1. PC firmware boots the PC5 loader — **done in emulation/stub firmware** (`M1c`,
   `make stubtest64`).
2. PC5's own kernel executes — **done in emulation** (`PC5-64BIT-LONG-MODE-OK`).
3. A platform compatibility environment is initialised — **not started** (`M11`–`M16`).
4. Genuine Sony firmware components are lawfully loaded — **blocked** (`M18`,
   payload absent, authenticity boundary `M17`).
5. The genuine Sony kernel executes — **blocked**.
6. Genuine Sony system services and shell run — **blocked**.

The 64-bit foundation is useful for the real objective only as the execution
environment the platform model (§6 of the spec) will run on; it is not evidence of
any kind that Sony software runs. Nothing in this PR promotes a milestone.

## 5. Source corrections made in this pass (summary)

The full account is in the PR and its regression tests. In short: the UEFI loader's
framebuffer arithmetic now uses checked multiplication and a 32-bit pitch bound;
CPUID extended leaves are gated on the saved maximum leaf; the memory map is
validated as a set for overlapping descriptors before any coverage decision; and
framebuffer pixel formats the protocol cannot describe are classified unusable.
The 32-bit release path is untouched.

## 5.1 Release isolation, measured rather than asserted

The contract at the top of this report is that PC5 is the PC-side
infrastructure and Sony's software is the operating system. A weaker but
related invariant is that none of this work may change the shipping 32-bit
release image, which is the only artefact a user can boot today. It was checked
by building the image on both trees rather than by reading the diff:

```
$ git checkout 8aab6d3 && make -s iso && sha256sum build/pc5.iso
e21506341fb1bd84c39831d163f4518d6a20b033f5b271871a165659f126768f

$ git checkout arena/17f0c4f8-pc5 && make -s iso && sha256sum build/pc5.iso
e21506341fb1bd84c39831d163f4518d6a20b033f5b271871a165659f126768f
```

Identical bytes. Only `boot/uefi64/` and `kernel64/` changed under `boot/` and
the kernel tree, both of which are outside the release closure;
`tests/test_kernel64.py::ReleaseIsolationTests` fails if that ever stops being
true. The brand work does not touch the kernel banner: the shipping 32-bit
boot output is unchanged, and `brand/pc5-mark-boot.txt` is a separate artefact
for a future console that does not exist yet.

## 6. Remaining limitations

- No physical hardware, no real OVMF, no genuine Sony software execution.
- The PUP payload is absent; no firmware analysis was performed and none is claimed.
- The PC5 diagnostic interface exists and is not the guest OS; it must stay labelled
  as diagnostic in every document and screen.
