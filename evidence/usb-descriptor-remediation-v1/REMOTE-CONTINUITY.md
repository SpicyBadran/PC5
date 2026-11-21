# Remote continuity: the recovery path for this remediation is executed, not asserted

Measured 2026-11-21T16:57Z–17:03Z in this sandbox. Nothing here was transcribed from a manifest:
every number below is a measurement of a file or a command exit status.

## What was restored *into* this pass

The previous session's records (the identity-boundary design pass) were recovered from
`/PC5-continuity-arena-c8a76776-v1/pc5-identity-boundary-20261121-v5.tar.gz`, retrieved through the
Dropbox connector: 435,045 bytes, sha256
`1485d2e370a89eb7b5a11a138c3de9ed3238a0984319c170c7125b5f276dae0a` — identical to the recorded
value, and a re-hash of all 88 members against the archived manifest reported zero mismatches.
That retrieval is what made the cross-implementation checks in section 2 of
`USB-DESCRIPTOR-REMEDIATION-20261121.md` possible, and it disproves a claim in the previous
session's own record: binary payloads **do** survive the connector byte-for-byte (gzip was
reproduced exactly), so "independent download of the archive is not achievable in this sandbox" was
too strong. What remains unverified is the *public link* route: `www.dropbox.com` and the link hosts
still do not resolve from the sandbox, so only the connector path was exercised.

## What was published *from* this pass

| Object | Measured identity |
|---|---|
| `recovery/pc5-usb-descriptor-remediation-20261121-v1.tar.gz` | 525,090 B, sha256 `4b5c6b9faf9ff5ddd1a08b599a585e5d49741a9e17fc2051a602596926234233` |
| `recovery/pc5-usb-descriptor-remediation-20261121-v2.tar.gz` (supersedes v1: adds the final three logs and the docs-count refresh) | 526,859 B, sha256 `805848c3f8b4829306b8b8783363ff31add9ad1fb41c5830baac87cc715befc8`, 108 members |
| both manifests | `recovery/*-manifest.json`, archived inside their own tarball and uploaded beside it |

`make check` ran before v2 was built, so v2's members are the verified tree at that moment; the v2
manifest's `verification` block is the measured suite output, copied from `MAKE-CHECK.log` rather
than retyped. A tar.gz cannot contain its own SHA-256: the sidecar
`recovery/pc5-usb-descriptor-remediation-20261121-v2.tar.gz.sha256` carries it, and the manifest is
verified by re-hashing its members, which is exactly how an archive is checked after a restore.
`content_hash` values returned by the connector (`6caa8994af0ad096…` for v2,
`4fa881b52e5dea79…` for v1, `134a304a072773e8…` for the v1 manifest,
`46f4ea412179982a…` for the v2 manifest) are **Dropbox's BlockHash**, not SHA-256, and are recorded
only as metadata — they were not treated as content proof and were not confused with the sha256s.

## End-to-end round trip, executed

Upload → retrieve → extract → verify → apply, all in this session:

1. v1 uploaded, retrieved back into `dropbox/`, sha256 of the retrieved bytes equals the local
   archive: `4b5c6b9faf9ff5dd…` — recorded in `REMOTE-ROUND-TRIP.log`.
2. The retrieved archive extracted; 105 members re-hashed against the archived manifest: zero
   mismatches.
3. `evidence/usb-descriptor-remediation-v1/REMEDIATION.patch` taken *from the retrieved archive*
   applied cleanly to a fresh `git archive HEAD` export, and that restored tree ran the USB
   inventory: `Ran 91 tests … OK`.
4. v2 and both manifests then uploaded and retrieved as well: retrieved bytes match the local
   files (v2 `805848c3…`; manifest `21746` bytes retrieved for `21746` uploaded), and the retrieved
   v2 archive's 108 members re-hash clean against its own archived manifest.

So a session that starts with nothing but this repository and the connector can restore the
remediation, verify it, and re-run its tests. What the archive cannot do is make the result
accepted: it carries the evidence, not the approval.

## Recorded inaccuracy, not silently patched

Both manifests carry a `generated` field written from a guess before the upload: v1 says
`2026-11-21T17:05:00Z` and v2 says `2026-11-21T17:12:00Z`, whereas the measured file mtimes are
`16:57:43.178Z` and `17:02:29.625Z` and the connector's `server_modified` values are `16:57:50Z`
and `17:02:36Z`. The timestamps are wrong by minutes (v2's is in the future relative to its own
upload). Nothing was regenerated to hide it: rewriting an archive changes its sha256, and the sha256
is already published, so the honest record is this paragraph. No hash, size or test result depends
on the `generated` field.

## Remote state after this pass

`ls` on the folder shows five objects (v5 archive, v5 manifest, v1 archive, v1 manifest, v2 archive,
v2 manifest); `strict_conflict` was used on every upload, so no prior object was overwritten — the
v5 archive retrieved after these uploads still hashes to its recorded value. `origin` remains
untouched: `HEAD` = `56c58521580e88c4339ceb6eb40ce724a2bb533b` = `main`, the branch
`arena/c8a76776-pc5` = `ae9f3e25f66ac3d31b2b7b64dccef7695fef1d73`, PR31 open at
`ac17c5d3afa6fa3ca779c012c6aef36b64751b55` (base `46a9fda1`, 17 commits, 30 files, clean), PR34 open
at `547ebd5c`. Zero commits, zero pushes, zero PR comments or updates by me.
## Third continuity point (v3), and what changed since v2

`recovery/pc5-usb-descriptor-remediation-20261121-v3.tar.gz` — 927,493 B, sha256
`ea6329e4e4b8287317d126f59d62f82f3aaa7cd6ca9abd4b73c662fbe700eb41`, 193 members — supersedes v1 and
v2 because those were taken before the PR31 recovery, before `evidence/continuation-descriptor-v1/`,
`continuation-mission-v1/` and `candidate-recovery-v{1,2}/` were restored into the workspace, and
before `REMEDIATION.patch` was re-cut to match the tree (`19793a83…`, was `65944ecd…`).

Its `generated` field is `date -u` output taken immediately before the `tar`, which is the fix for
the guessed-timestamp error recorded above; the note is kept in `supersedes`/`generated_note` rather
than by editing v1/v2, whose published hashes must not move.

Round trip, executed: upload (`strict_conflict`) → `get_file_content` → retrieved bytes hash to the
same value as the local archive (`ea6329e4…`, `BYTE-IDENTICAL ROUND TRIP: True`); the retrieved
manifest matches the local one (`38,098` B both sides, identical hash); extracting the retrieved
archive and re-hashing every member against the manifest inside it gives **193/193, zero
mismatches**; and the `REMEDIATION.patch` taken out of the retrieved archive applies to a clean
`git archive HEAD` export, whose USB inventory then reports `Ran 91 tests … OK`. So the recovery
point is not "an archive exists somewhere" — it is an archive whose contents are known to rebuild a
tree that passes the tests named in it.

v3 deliberately excludes the retrieved archives under `dropbox/` (nine of them, ~2.6 MB): they are
byte-duplicates of objects already in the same Dropbox folder, and their sizes and sha256 values are
in table §8 of `../continuation-descriptor-v1/PR31-D1-V2-RECOVERY-AND-REVIEW-20261121.md`, so any
copy remains re-identifiable without nesting three megabytes inside an archive. The `recovery/*.tar.gz`
blobs themselves are excluded for the same reason; their sidecars and manifests are included.

`pc5-identity-boundary-20261121-v5.tar.gz` still has no `.sha256` sidecar in `recovery/` (it was
created before this practice started here); its integrity is carried by its manifest, which is how it
was verified twice in this session.
