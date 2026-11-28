# PS5 firmware and the PUP package — verified as an object, not assumed

| | |
|---|---|
| Question | is the file PC5 treats as its target input actually present, and what is it, as opposed to what is hoped about it? |
| Answer | **the repository contains a 135-byte Git LFS pointer, not a firmware package.** The object it names is not in the repository's LFS store, and its absence has already broken CI |
| Measured on | 2026-11-28, at baseline `9b20228` (see [`docs/TRANSFORMATION_BASELINE.md`](../TRANSFORMATION_BASELINE.md)) |
| Related | [`../PS5_SYSTEM_SOFTWARE_TARGET.md`](../PS5_SYSTEM_SOFTWARE_TARGET.md) (rules of engagement), [`../FIRMWARE_ANALYSIS.md`](../FIRMWARE_ANALYSIS.md) (the analysis tool's contract), [`../architecture/PS5_PLATFORM_CONTRACT.md`](../architecture/PS5_PLATFORM_CONTRACT.md) (what the platform must present), [`../playstation/BOOT_FEASIBILITY.md`](../playstation/BOOT_FEASIBILITY.md) (the dependency chain) |

Division of labour, stated so this page duplicates nothing: `PS5_SYSTEM_SOFTWARE_TARGET.md`
defines **what may be done** with the package; `FIRMWARE_ANALYSIS.md` defines **how the
tool reports** what it finds. This page establishes **what is actually in hand**, and
registers the external evidence about the format, with its reliability marked.

---

## 1. What is in the tree — every fact with its measurement

| Property | Value | How it was established |
|---|---|---|
| Path | `PS5UPDATE.PUP` (repository root) | `git ls-tree origin/main PS5UPDATE.PUP` |
| Git object | blob `f76e9560542a1fee578f1f286fedb85e05dea0b5` | same command, mode `100644` |
| Size of that blob | **135 bytes** | `git cat-file -p "$(git rev-parse HEAD:PS5UPDATE.PUP)" \| wc -c` |
| Its contents | `version https://git-lfs.github.com/spec/v1`<br>`oid sha256:42ea0e5aad1361e098837d40157863c4c519f0bbc66eb03c04dbaaf339b063fb`<br>`size 1423697408` | `git cat-file -p` on the blob |
| Is it routed through LFS? | **Yes.** `.gitattributes` carries `*.pup filter=lfs diff=lfs merge=lfs -text` and an explicit `PS5UPDATE.PUP` line; `git check-attr -a -- PS5UPDATE.PUP` → `filter: lfs`, `diff: lfs`, `merge: lfs` | `git check-attr -a`, `git show origin/main:.gitattributes` |
| Does the named object exist locally? | **No.** `git cat-file -t 42ea0e5a…` → `fatal: Not a valid object name` | measured |
| Does it exist in the remote LFS store? | **No.** CI died on exactly this: `remote missing object 42ea0e5…`, `fatal: PS5UPDATE.PUP: smudge filter lfs failed`, exit 128, recorded in [`../../ci/README.md`](../../ci/README.md) as the cause of two red runs, with `docs/BUILDING.md` holding a `404` for it since the provenance work | repository's own incident record |
| Can `git lfs` be consulted here? | No — `git: 'lfs' is not a git command` in this sandbox, so the store cannot even be probed from the development environment | `git lfs ls-files` |
| Is any firmware payload anywhere in the tree? | No. The extension census of `origin/main` (442 files) contains no `.bin`, `.elf`, `.self`, `.iso`, `.pkg`, `.img` or `.pup` payload — the only `.pup`-related entry is the pointer | `git ls-tree -r --name-only origin/main \| sed 's/.*\.//' \| sort \| uniq -c` |

**Therefore: `PS5UPDATE.PUP` is a reference to a 1.33 GiB object that this project cannot
read.** Any statement in any file about what that package contains is, by construction, a
statement about third-party reports — never about PC5's own input.

One arithmetic note, because "1.42 GB" and "1.33 GiB" both appear in the corpus and both
are correct: `1,423,697,408 / 1000³ = 1.42 GB` and `/ 1024³ = 1.33 GiB`. The declared size
in the pointer is the single number to cite; the unit is what changes.

## 2. What is publicly reported about the format

All of this is **third-party reporting**, listed with the tier defined in §4. None of it
has been confirmed against PC5's own input, because PC5 has no input.

**Container.** Every PS5 PUP begins with the magic bytes `53 4C 42 32` — ASCII `SLB2` —
with a file table at offset `0x30` on the PS5 developer wiki. A community write-up of a
5.50 *update* PUP reports the full outer header: `0x04` format version, `0x08` mode,
`0x0C` entry count, `0x10` header size, `0x18` data size, then one **48-byte record per
entry** (`0x00` entry ID, `0x08` compressed size, `0x10` uncompressed size, `0x18` data
offset, `0x20` flags where bit 0 = compressed, bit 1 = signed, bit 2 = encrypted). That
report counts **95 entries across 14 component types** in the 5.50 update package, with
duplicate IDs explained as primary/backup copies and per-hardware-revision builds.

**Two package kinds.** Sony's update page and community documentation distinguish an
**update PUP** (only the components that changed) from a **recovery/reinstallation PUP**
(the complete system software, full reinstall). Entry names such as `PS5UPDATE1.PUP` and
`PS5UPDATE2.PUP` appear in the outer header of the reinstallation package, per a public
research thread that also warns that a modified PUP must pass authentication PC5 does not
control ([Issue #73](https://github.com/ZigZagBoi1/PS5-14.10-Research/issues/73)).

**Two layers of protection.** The reports are consistent on this, and it is the load-bearing
fact for the whole feasibility question:

1. The **outer SLB2/BLS container** — some tooling parses it and, with keys obtained
   outside this project, decrypts it to reveal the entry table.
2. The **inner per-component SELF containers** (`ET_SCE_EXEC`, type `0xFE00`), whose
   headers are plaintext (ELF structure, segment layout, entry points readable) while
   **each segment's code and data are AES-encrypted with keys bound to on-die secure
   storage**, i.e. not derivable from the package.
   A public tool-development thread reports the container key as *almost certainly burned
   into ROM or silicon* — which is exactly why every console can decrypt every package —
   and reports that its own tool still needs one key dumped from a running console on
   13.60 to go further. PC5 does **not** have that key, does not seek it, and does not
   build a tool whose purpose would be to obtain it.

**Sizes reported publicly, and where ours would sit.**

| Report | Size | Source |
|---|---|---|
| Retail PS5 firmware 14.00 `PS5UPDATE.PUP` | 1,260,600,832 B | gbatemp PUP-decrypt thread |
| File described in research Issue #73 | 1,418,890,240 B | that issue's own text |
| Sony's support page, order of magnitude | ~1.1 GB (update) / ~1.2 GB+ (reinstallation) | playstation.com system-software page |
| **Our pointer's declared size** | **1,423,697,408 B** | `PS5UPDATE.PUP` |

The declared size matches none of the reported figures. That is a fact, not an accusation:
different firmware versions have different sizes, so **size alone identifies no version**,
and with the payload absent the package type and version of PC5's reference are
**unidentified**. `docs/PS5_COMPATIBILITY_STATUS.md` keeps that as `NO`, and
`README.md`'s input table already says "package type and version **unverified**".

## 3. What PC5 may and may not do with this input

Inherited verbatim from ADR-007, ADR-022 and ADR-026, restated here so the Windows
application cannot drift away from it:

| | |
|---|---|
| **Permitted** | receiving a package the owner lawfully holds; computing and recording its SHA-256; identifying the container by signature; parsing *plaintext* structural metadata; recording entry counts/sizes/offsets; comparing against public reports; building PC5's own components that present the documented platform contract |
| **Refused** | publishing or committing the package or any part of it; extracting and redistributing proprietary code or system assets; bypassing firmware signatures, encryption, secure boot, authentication or any access control; producing a modified package intended to pass authentication; claiming any of the above works when it has not been tested |
| **Recorded, never asserted** | identity of the input: hash, declared size, container signature, and the fact of whether PC5 could read it at all |

`.gitignore` excludes `firmware/`, so a locally supplied package can be analysed without
ever becoming a repository object. The analysis path already exists and is the one to use:
`python3 tools/firmware.py analyze --input /secure/PS5UPDATE.PUP …` (see
[`../FIRMWARE_ANALYSIS.md`](../FIRMWARE_ANALYSIS.md)).

## 4. Source reliability tiers

Applied so that a later reader can weigh each claim in §2 rather than inherit it.

| Tier | Meaning | Sources used here |
|---|---|---|
| **A — primary, checkable** | the artefact itself, or the project's own record | `PS5UPDATE.PUP`, `.gitattributes`, `ci/README.md`, `git cat-file`/`check-attr` output |
| **B — reference documentation** | maintained project documentation with a revision history | PS5 Developer Wiki `[PUP](https://www.psdevwiki.com/ps5/PUP)` page; Sony's own support page for the USB layout `PS5/UPDATE/PS5UPDATE.PUP` |
| **C — first-hand technical threads** | named researchers publishing parsed structure; not independently reproducible here | gbatemp PUP-decrypt thread; the 5.50 format breakdown on Se7enSins/SickGaming; [Issue #73](https://github.com/ZigZagBoi1/PS5-14.10-Research/issues/73) |
| **D — secondary reporting / SEO content** | consistent with A–C or not, but unverifiable and sometimes AI-generated | aggregator pages on emulator status; assorted GitHub "dossier" repositories that read as generated. **Not load-bearing:** nothing in this project's conclusions rests on tier D alone |

## 5. Procedure: turning the assumption back into an object

The owner runs this on a machine that already holds a lawfully obtained package. Nothing in
it belongs in the repository.

```
# 1. identity, before anything else
sha256sum /secure/PS5UPDATE.PUP                      # expect 42ea0e5a… to match, or not to
stat -c %s /secure/PS5UPDATE.PUP                     # expect 1423697408 if it is "the" file
head -c 16 /secure/PS5UPDATE.PUP | od -An -tx1       # expect 53 4c 42 32 ("SLB2") at 0x00

# 2. structural metadata only — no decryption, no modification
python3 tools/firmware.py analyze --input /secure/PS5UPDATE.PUP \
        --report build/firmware-report.json

# 3. record what was measured, in the tree, as a hash-and-structure record
#    (the file itself stays outside the repository; .gitignore already keeps firmware/ out)
```

Expected outcomes and what each one means:

| Outcome | Conclusion PC5 may then state |
|---|---|
| hash matches, `SLB2` present, structure parses | "the repository's declared input object exists and was read; entry count/types recorded" — and ADR-030 can be amended with the report digest |
| hash does not match | the pointer names something else; the pointer is corrected in a dated commit, and every earlier statement about "the package" is annotated as referring to the absent object |
| no package lawfully available | **status quo**, stated plainly: genuine-execution milestones stay `blocked` on a lawful input, and PC5's own implementation continues on category-2 evidence |

The last row is the currently true one, and it is not a defeat: M1–M7 of the
transformation are defined so that the parts that depend on nothing proprietary proceed,
while `M18`–`M21` stay blocked with their reason visible in the graph.

## 6. Open questions this page does not answer

1. Which firmware version, if any, the declared object corresponds to — unresolvable
   without the payload.
2. Whether the pointer should remain at the root at all, given D-1 in the baseline (a
   broken LFS reference in the default checkout is a hazard for every new contributor;
   a `PS5UPDATE.PUP.md` beside it, or the pointer's fields moved into
   `docs/evidence/`, removes the hazard without losing the record).
3. Whether a lawful development path exists that does not require dumping an on-die key.
   Nothing published suggests one; the burden is on evidence, not on optimism — see
   [`../architecture/EXECUTION_FEASIBILITY.md`](../architecture/EXECUTION_FEASIBILITY.md).
4. Whether the two-layer encryption model still holds on firmware past 14.x. Reported
   only from tier C for 5.50/14.x.

## 7. External reading

* PS5 Developer Wiki, *PUP* — <https://www.psdevwiki.com/ps5/PUP>
* gbatemp, *Built a PC-based PUP decrypt tool…* — <https://gbatemp.net/threads/built-a-pc-based-pup-decrypt-tool-one-key-away-from-decrypting-any-firmware-forever-need-someone-on-13-60-to-run-an-extraction-payload.685380/>
* Se7enSins, *PS5 PUP Format Reverse Engineered — Full Breakdown, Tools & 5.50 Analysis* — <https://www.se7ensins.com/forums/threads/ps5-pup-format-reverse-engineered-full-breakdown-tools-5-50-analysis.1923472/>
* `ZigZagBoi1/PS5-14.10-Research`, [Issue #73](https://github.com/ZigZagBoi1/PS5-14.10-Research/issues/73) — the `SLB2` / `PS5UPDATE1.PUP` / `PS5UPDATE2.PUP` description and the authentication warning
* Sony, *How to update the PS5 console system software* — <https://www.playstation.com/en-us/support/hardware/ps5/system-software/>
