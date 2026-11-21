#!/usr/bin/env python3
"""Adversarial audit of the USB-descriptor remediation evidence package.

Written by the independent reviewer.  Read-only against the workspace: every action is
`open`, `stat`, `sha256`, `tar -tzvf`, or a write into a scratch directory under /tmp.
Nothing in the repository under review is modified, and nothing is deleted.

Checks
  1. HASHES.txt — parse every `<path> <bytes> <sha256>` record, confirm the path exists,
     the byte count matches, and the hash matches.  Report missing, size-drifted and
     hash-drifted entries separately, plus any record path that does not exist at all.
  2. The patch — size, sha256, `git apply --check` and `git apply` against a fresh clone
     of the recorded base, and a byte comparison of every resulting file.
  3. The v3 continuity archive — sidecar sha256, member inventory against the manifest,
     every member hash, extraction round trip against the workspace bytes, and the
     archived patch re-applied to a fresh clone and its suite re-run.
  4. Timestamp and provenance claims — what the records say versus what the records show,
     including the two places where a document contradicts the manifest it describes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

WS = Path("/home/user/pc5")
EVID = WS / "evidence/usb-descriptor-remediation-v1"
HASHES = EVID / "HASHES.txt"
PATCH = EVID / "REMEDIATION.patch"
ARCHIVE = WS / "recovery/pc5-usb-descriptor-remediation-20261121-v3.tar.gz"
MANIFEST = WS / "recovery/pc5-usb-descriptor-remediation-20261121-v3-manifest.json"
SIDE = WS / "recovery/pc5-usb-descriptor-remediation-20261121-v3.tar.gz.sha256"
BASE = "56c58521580e88c4339ceb6eb40ce724a2bb533b"

RECORD = re.compile(r"^(\S.*?)\s+(\d+)\s+([0-9a-f]{64})$")
problems: list[str] = []
notes: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------------------------------------------------------------- 1. HASHES.txt
section("1. HASHES.txt record verification")
lines = HASHES.read_text(encoding="utf-8").splitlines()
records, unparsed = [], []
for i, line in enumerate(lines, 1):
    s = line.strip()
    if not s:
        continue
    m = RECORD.match(s)
    if m:
        records.append((i, m.group(1), int(m.group(2)), m.group(3)))
    else:
        unparsed.append((i, s))
print(f"lines={len(lines)} records={len(records)} non-record lines={len(unparsed)}")

ok = missing = bad_size = bad_hash = 0
for lineno, raw, claimed_size, claimed_sha in records:
    # records are relative to the workspace root; some carry a directory prefix
    candidates = [WS / raw, EVID / raw, WS / raw.split(" ", 1)[-1]]
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        missing += 1
        problems.append(f"HASHES.txt:{lineno}: recorded path does not exist: {raw!r}")
        continue
    size = path.stat().st_size
    if size != claimed_size:
        bad_size += 1
        problems.append(f"HASHES.txt:{lineno}: size drift for {raw}: recorded {claimed_size}, "
                        f"measured {size}")
    digest = sha256(path)
    if digest != claimed_sha:
        bad_hash += 1
        problems.append(f"HASHES.txt:{lineno}: hash drift for {raw}: recorded {claimed_sha[:16]}"
                        f"…, measured {digest[:16]}…")
    if size == claimed_size and digest == claimed_sha:
        ok += 1
    elif "HASHES.txt" in raw:
        problems.append(
            f"HASHES.txt:{lineno}: the manifest records its own bytes "
            f"({claimed_size} B, {claimed_sha[:12]}…) while the file measures "
            f"{size} B / {digest[:12]}…, and its own header says it 'cannot hash "
            "itself' - a self-record that is stale by construction")
print(f"verified={ok}  missing={missing}  size-drift={bad_size}  hash-drift={bad_hash}")

# does every artifact in the evidence directory appear in the manifest?
recorded = {r[1].split()[-1] for r in records} | {r[1] for r in records}
present = []
for p in sorted(EVID.rglob("*")):
    if p.is_file() and "__pycache__" not in str(p):
        rel = str(p.relative_to(WS))
        short = p.name
        if rel not in recorded and short not in recorded:
            present.append(rel)
if present:
    notes.append("files in the evidence directory with no HASHES.txt record: "
                 + ", ".join(present))
print(f"unrecorded files under evidence/usb-descriptor-remediation-v1: {len(present)}")
for rel in present:
    print(f"    {rel}")

# ---------------------------------------------------------------- 2. the patch
section("2. REMEDIATION.patch — identity, application, byte equality")
size, digest = PATCH.stat().st_size, sha256(PATCH)
print(f"bytes={size} sha256={digest}")
sidecar = PATCH.with_suffix(".patch.sha256")
if sidecar.exists():
    txt = sidecar.read_text().split()
    print(f"sidecar: {txt[0]} {txt[1] if len(txt) > 1 else ''}")
    if txt[0] != digest:
        problems.append(f"patch sidecar disagrees: {txt[0]} vs {digest}")
files = [l for l in PATCH.read_text().splitlines() if l.startswith("diff --git ")]
hunks = sum(1 for l in PATCH.read_text().splitlines() if l.startswith("@@"))
print(f"files={len(files)} hunks={hunks}")

scratch = Path(tempfile.mkdtemp(prefix="pc5-review-audit-"))
clone = scratch / "apply"
rc, out, err = run(["git", "clone", "-q", "--no-hardlinks", str(WS), str(clone)])
if rc:
    problems.append(f"clone failed: {err}")
else:
    run(["git", "checkout", "-q", BASE], cwd=clone)
    rc1, o1, e1 = run(["git", "apply", "--check", "--verbose", str(PATCH)], cwd=clone)
    rc2, o2, e2 = run(["git", "apply", "--verbose", str(PATCH)], cwd=clone)
    print(f"git apply --check exit={rc1}  git apply exit={rc2}")
    if "Hunk" in (e1 + e2) and "FAILED" in (e1 + e2):
        problems.append("a hunk failed to apply cleanly")
    if "fuzz" in (e1 + e2).lower() or "context" in (e1 + e2).lower():
        notes.append(f"apply reported offset/fuzz: {(e1 + e2).strip()[:200]}")
    rc3, stat_out, _ = run(["git", "diff", "--stat"], cwd=clone)
    print("tracked diff after applying:\n" + "\n".join("    " + l for l in stat_out.splitlines()))
    rc4, status_out, _ = run(["git", "status", "--porcelain", "-uall"], cwd=clone)
    print("status after applying:\n" + "\n".join("    " + l for l in status_out.splitlines()))
    # byte equality against the delivered workspace copies
    for line in status_out.splitlines():
        marker, rel = line[:2].strip(), line[3:].strip()
        a, b = clone / rel, WS / rel
        if a.is_file() and b.is_file():
            same = sha256(a) == sha256(b)
            print(f"    {'IDENTICAL' if same else 'DIFFERS    '} {rel}")
            if not same:
                problems.append(f"patched {rel} differs from the workspace copy")

# ---------------------------------------------------------------- 3. v3 archive
section("3. recovery/…-v3.tar.gz — sidecar, members, round trip")
a_size, a_sha = ARCHIVE.stat().st_size, sha256(ARCHIVE)
print(f"archive bytes={a_size} sha256={a_sha}")
side_txt = SIDE.read_text().split() if SIDE.exists() else []
print(f"sidecar: {' '.join(side_txt) if side_txt else 'MISSING'}")
if side_txt and side_txt[0] != a_sha:
    problems.append(f"archive sidecar disagrees: {side_txt[0]} vs measured {a_sha}")
man = json.loads(MANIFEST.read_text(encoding="utf-8"))
print(f"manifest member_count={man.get('member_count')}  len(members)={len(man.get('members', []))}")
print(f"manifest generated={man.get('generated')!r}")
with tarfile.open(ARCHIVE, "r:gz") as tf:
    members = [m for m in tf.getmembers() if m.isfile()]
names = [m.name.lstrip("./") for m in members]
print(f"tar file members={len(names)} (directories and links excluded)")
listed = {mm["path"].lstrip("./") for mm in man["members"]}
unlisted = sorted(set(names) - listed)
notarchived = sorted(listed - set(names))
print(f"unlisted in the archive: {unlisted or 'none'}")
print(f"listed but absent: {notarchived or 'none'}")
if [u for u in unlisted if not u.endswith("-manifest.json")]:
    problems.append(f"archive contains unattested members: {unlisted}")
if notarchived:
    problems.append(f"manifest lists members missing from the archive: {notarchived}")
if man.get("member_count") != len(listed):
    problems.append(f"member_count {man.get('member_count')} != distinct listed {len(listed)}")

extract = scratch / "extract"
extract.mkdir()
with tarfile.open(ARCHIVE, "r:gz") as tf:
    tf.extractall(extract)
checked = absent = drift = 0
superseded: list[str] = []
for member in man["members"]:
    rel = member["path"].lstrip("./")
    p = extract / rel
    if not p.is_file():
        absent += 1
        problems.append(f"manifest member not in the extracted archive: {rel}")
        continue
    measured_size, measured_sha = p.stat().st_size, sha256(p)
    if measured_size != member["size"] or measured_sha != member["sha256"]:
        drift += 1
        problems.append(f"member drift {rel}: manifest {member['size']}/{member['sha256'][:12]}… "
                        f"measured {measured_size}/{measured_sha[:12]}…")
    else:
        checked += 1
    # and against the live workspace copy.  A difference here is only a finding if
    # it is undisclosed: the archive is an earlier snapshot, and HASHES.txt states
    # that its own copy predates the 17:20Z gate capture.
    w = WS / rel
    if w.is_file() and sha256(w) != measured_sha:
        superseded.append(rel)
print(f"members verified against the manifest: {checked}  absent={absent}  drift={drift}")
print(f"members whose workspace copy has moved on since the archive (superseded): {len(superseded)}")
for rel in superseded:
    print(f"    {rel}")
disclosed = (EVID / "HASHES.txt").read_text()
for key in ("HASHES.txt", "MAKE-CHECK.log", "REMOTE-CONTINUITY.md"):
    if key in " ".join(superseded) and key.split(".")[0] not in disclosed:
        notes.append(f"superseded archived member {key} is not named in HASHES.txt's "
                     "disclosure sentence; the archive-vs-workspace difference is "
                     "measurable but only partly explained in prose")

# the archived patch, applied where nobody has touched it
arch_patch = extract / "evidence/usb-descriptor-remediation-v1/REMEDIATION.patch"
if arch_patch.is_file():
    print(f"archived REMEDIATION.patch bytes={arch_patch.stat().st_size} "
          f"sha256={sha256(arch_patch)}")
    if sha256(arch_patch) != man.get("remediation_patch_sha256"):
        problems.append("manifest remediation_patch_sha256 disagrees with the archived patch")
    rt = scratch / "roundtrip"
    run(["git", "clone", "-q", "--no-hardlinks", str(WS), str(rt)])
    run(["git", "checkout", "-q", BASE], cwd=rt)
    rcx, _, ex = run(["git", "apply", str(arch_patch)], cwd=rt)
    print(f"round-trip `git apply` of the archived patch: exit={rcx} {ex.strip()[:120]}")
    if rcx == 0:
        rcy, outy, erry = run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                               "-p", "test_pc5ctl_usb*.py"], cwd=rt)
        tail = (outy + erry).strip().splitlines()[-3:]
        print("round-trip suite: " + " | ".join(tail))
        if rcy != 0:
            problems.append("the archived patch does not reproduce the 91-test suite")
else:
    problems.append("REMEDIATION.patch is not inside the v3 archive at the manifest path")

# ---------------------------------------------------------------- 4. provenance
section("4. provenance and internal consistency")
rc, head, _ = run(["git", "rev-parse", "HEAD"], cwd=WS)
rc, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=WS)
print(f"workspace HEAD={head.strip()} branch={branch.strip()}")
if man.get("local_head") != head.strip():
    notes.append(f"manifest local_head {man.get('local_head')} != current workspace HEAD "
                 f"{head.strip()} (HEAD moves; recorded, not corrected)")
for key in ("branch", "local_head", "remediation_patch_sha256", "remediation_patch_bytes"):
    print(f"  manifest {key} = {man.get(key)}")
patch_in_ws = sha256(PATCH)
print(f"  workspace patch sha256 {patch_in_ws}  bytes {PATCH.stat().st_size}")
if patch_in_ws != man.get("remediation_patch_sha256"):
    problems.append("the workspace patch no longer matches the manifest's claim")

# every claim that something was committed / pushed / deployed
print("\ndocs and reports: any claim of commit / push / deploy?")
docs = [WS / "docs/engineering/USB-DESCRIPTOR-REMEDIATION-20261121.md",
        WS / "docs/engineering/PROJECT_STATE.md",
        WS / "docs/engineering/EVIDENCE_INDEX.md",
        EVID / "HASHES.txt", EVID / "REMOTE-CONTINUITY.md", MANIFEST]
pat = re.compile(r"\b(commit(ted|s|ing)?|push(ed|es|ing)?|deploy(ed|s|ing)?|merged|landed)\b", re.I)
for d in docs:
    if not d.is_file():
        print(f"  (absent) {d}")
        continue
    for i, line in enumerate(d.read_text(encoding="utf-8").splitlines(), 1):
        if pat.search(line):
            print(f"  {d.relative_to(WS)}:{i}: {line.strip()[:150]}")

# timestamps: mtimes are not evidence in this sandbox; say so from measurement
print("\nmtime sanity (the sandbox normalises mtimes; recorded so nobody re-reads them as order):")
for p in (PATCH, HASHES, ARCHIVE, MANIFEST, WS / "docs/engineering/PROJECT_STATE.md"):
    print(f"  {p.relative_to(WS)} mtime={p.stat().st_mtime:.0f}")

section("RESULT")
print(f"problems={len(problems)} notes={len(notes)}")
for p in problems:
    print(f"  PROBLEM {p}")
for n in notes:
    print(f"  NOTE    {n}")
print(f"\nscratch kept at {scratch} (removed by the OS only if TMPDIR is cleaned)")
sys.exit(1 if problems else 0)
