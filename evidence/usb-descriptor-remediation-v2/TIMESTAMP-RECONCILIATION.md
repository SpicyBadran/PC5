# F-3 — what each timestamp in the continuity records actually means

Generated `2026-11-21T19:30:35Z`, during the closure pass; every number below was read
from the artifact named beside it in the same command, not copied from another
document. The command lines are at the bottom so the whole table can be rebuilt.

## The three events, and the one word used for all three

`HASHES.txt` in `evidence/usb-descriptor-remediation-v1/` uses "was built at" for a
time that is not a build time, and the manifests use `generated` for a time that is
not the archive's either. Separated:

| Event | Value | Source of the value | What it can support |
|---|---|---|---|
| **pre-archive clock reading** | `2026-11-21T17:16:48Z` | `HASHES.txt:24-25` prose | "the record-keeping for the v3 pass ran at 17:16:48" — nothing more |
| **manifest generation** | `2026-11-21T17:17:26Z` | `recovery/pc5-…-v3-manifest.json` `generated`, whose own `generated_note` says it was taken with `date -u` *immediately before archiving* | "the manifest text was finalised at or after 17:17:26" |
| **archive creation** | not recorded as a separate value; bounded by `tar tzvf` member times, `2026-11-21 17:17` for the manifest copy inside it | the tar's own member table of contents | `tar creation ≥ 17:17:26Z` |
| **verification** | per-run `# captured …` lines in `logs/` | `date -u` at run time | "this transcript was produced then" |

`17:16:48Z < 17:17:26Z ≤ archive creation`. The 38-second gap the review flagged is a
**mislabel in prose** — a pre-archive `date -u` reading described as "was built at" —
and not two builds, not a regenerated archive, and not a backdating. The archive's
identity is unaffected: `ea6329e4e4b8287317d126f59d62f82f3aaa7cd6ca9abd4b73c662fbe700eb41`,
927,493 B, 193 members, verified by `sha256sum -c` against its sidecar.

## Why filesystem mtimes cannot be used at all here

```
$ stat -c '%n %y' recovery/pc5-usb-descriptor-remediation-20261121-v3.tar.gz \
                  recovery/pc5-usb-descriptor-remediation-20261121-v3-manifest.json
recovery/pc5-usb-descriptor-remediation-20261121-v3.tar.gz        2026-11-21 18:04:50.195741291 +0000
recovery/pc5-usb-descriptor-remediation-20261121-v3-manifest.json 2026-11-21 18:04:50.195741291 +0000
```

Two files, different purposes, identical to the nanosecond, 48 minutes after both
events in the table. This sandbox rewrites mtimes when the workspace is restored, so
`recovery/`'s filesystem times record **the restore**, not the writing. Any ordering
argument must come from inside the archive (member times, which `tar` preserved) or
from a recorded clock reading, and this document says which of the two each row is.

This is also why the review's instruction "do not transcribe sizes/hashes — measure
them" had to be extended here: measured *mtimes* are not evidence of write order in
this environment, and a reader who runs `ls -la` and trusts it will reach a wrong
conclusion about which file came first.

## The same field, earlier passes — for context, not as an accusation

`evidence/usb-descriptor-remediation-v1/REMOTE-CONTINUITY.md`, section "Recorded
inaccuracy, not silently patched", already disclosed that v1's and v2's `generated`
values were guesses wrong by minutes (`17:05:00Z`, `17:12:00Z` — both round to the
minute, unlike v3's `17:17:26Z`), and that the measured file times and the remote
connector's `server_modified` values disagreed with them. v3 changed the *method* to
"`date -u` immediately before archiving" — which is exactly the method that produces a
time *earlier* than the build, and is why a sentence calling it a build time breaks.

`recovery/pc5-identity-boundary-20261121-v5-manifest.json` shows the third variant:
`generated: 2026-11-21T11:30:00Z`, a round minute, in a manifest for an archive whose
members carry 16:xx times. Read any `generated` field in this repository as "a clock
reading taken near the packaging step", never as "the archive's creation time", and
say so in the record.

## A second defect surfaced by the same comparison

All three remediation manifests set `archive` to the **manifest's** filename:

```sh
$ python3 -c "
import json
for v in ('v1', 'v2', 'v3'):
    m = json.load(open(f'recovery/pc5-usb-descriptor-remediation-20261121-{v}-manifest.json'))
    print(v, m['generated'], m['archive'], m['member_count'])"
v1 2026-11-21T17:05:00Z pc5-usb-descriptor-remediation-20261121-v1.json 105
v2 2026-11-21T17:12:00Z pc5-usb-descriptor-remediation-20261121-v2.json 108
v3 2026-11-21T17:17:26Z pc5-usb-descriptor-remediation-20261121-v3.json 193
```

The objects they document are `.tar.gz`. A consumer that reads `archive` to decide
what to verify would hash the manifest and call the archive verified. Recorded here
and in `FINDINGS-CLOSURE-20261121.md`; the manifests were not rewritten, because
rewriting them changes the hashes that the published records already name — the same
reason `REMOTE-CONTINUITY.md` gave for not fixing the earlier timestamps.

## Reproduce

```sh
cd /home/user/pc5
sha256sum -c recovery/pc5-usb-descriptor-remediation-20261121-v3.tar.gz.sha256
tar tzvf recovery/pc5-usb-descriptor-remediation-20261121-v3.tar.gz | grep -E 'manifest|tests/test_pc5ctl_usb'
python3 -c "import json;m=json.load(open('recovery/pc5-usb-descriptor-remediation-20261121-v3-manifest.json'));print(m['generated'],m['archive'],m['generated_note'])"
stat -c '%n %y' recovery/pc5-usb-descriptor-remediation-20261121-v3.tar.gz recovery/pc5-usb-descriptor-remediation-20261121-v3-manifest.json
sed -n '22,26p' evidence/usb-descriptor-remediation-v1/HASHES.txt
grep -n '17:16:48\|17:17:26' -r docs/engineering evidence/usb-descriptor-remediation-v1 | head
```
