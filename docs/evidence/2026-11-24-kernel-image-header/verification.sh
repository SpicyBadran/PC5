#!/bin/sh
# Re-derive this directory's claims from the bytes on file. No network, no build, no QEMU.
#
#   bash docs/evidence/2026-11-24-kernel-image-header/verification.sh   # expect: OK
#
# README.md states four things: the 64-bit gate passed with the kernel-header rows in it; the
# kernel image the loader validated is the image on the medium and in the report; four
# deliberately corrupted images were refused *by name* and never reached their entry point; and
# the 32-bit path is untouched. Each is checked here against the artifacts, and several against
# two independent sources at once (the report's derived markers and the raw serial text), which
# is the only kind of cross-check this format allows: a single source could always be edited
# together with the script that reads it.
#
# python3 is used for JSON because the repository's toolchain requires it anyway.
set -eu
cd "$(dirname "$0")"
fail=0
notes=0
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

note() { printf '%-5s %s\n' "$1" "$2"; }
ok() { note PASS "${2:-$1}"; notes=$((notes + 1)); }
bad() { note FAIL "${2:-$1}"; fail=1; notes=$((notes + 1)); }
expect() {            # expect <file> <pattern> <what it proves>
    if grep -q "$2" "$1"; then ok "$1" "$3"; else bad "$3 (no /$2/ in $1)"; fi
}
refuse() {            # refuse <file> <pattern> <what must be absent>
    if grep -q "$2" "$1"; then bad "$3 (found /$2/ in $1)"; else ok "$1" "$3"; fi
}
fact() {              # fact <exact line> <what it proves>   -- against the derived-facts file
    # Fixed-string, whole-line: these names contain `[cold]` and parentheses, which are
    # regex syntax, and `-F` is what makes a check about a *label* test the label.
    if grep -qxF "$1" "$tmp"; then ok "$tmp" "$2"; else bad "$2 (no fact /$1/)"; fi
}

# 1. The files this page cites are present and non-empty.
for f in x64-report.json x64-matrix.json ia32-report.json ia32-matrix.json \
         ovmf64-serial-cold.log ovmf64-serial-warm.log \
         ovmf64-serial-bad-signature.log ovmf64-serial-kernel-arch.log \
         ovmf64-serial-kernel-image-size.log ovmf64-serial-kernel-checksum.log \
         ovmf32-serial-cold.log ovmf32-serial-warm.log env.txt; do
    if [ -s "$f" ]; then ok "$f" "present and non-empty"; else bad "$f is missing or empty"; fi
done

# 2. The derived facts, in one pass, with the arithmetic done here rather than asserted by eye.
python3 - > "$tmp" <<'PY'
import json, re

def load(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)

def text(p):
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read().replace("\r", "")

x64 = load("x64-report.json")
ia32 = load("ia32-report.json")
rows = load("x64-matrix.json")
cold = text("ovmf64-serial-cold.log")
warm = text("ovmf64-serial-warm.log")

def counts(rep):
    st = [c["state"] for c in rep["checks"]]
    return (st.count("pass"), st.count("fail"), st.count("skip"), len(st))

for name, rep in (("x64", x64), ("ia32", ia32)):
    p, f, s, n = counts(rep)
    print("%s-exit %s" % (name, rep["exit_code"]))
    print("%s-pass %d" % (name, p))
    print("%s-fail %d" % (name, f))
    print("%s-skip %d" % (name, s))
    print("%s-checks %d" % (name, n))
    print("%s-marker %s" % (name, rep["success_marker"]))

print("x64-rows %d" % len(rows))
print("ia32-rows %d" % len(load("ia32-matrix.json")))
print("commit %s" % x64["data"]["environment"].get("commit",
      re.findall(r'"commit": "([0-9a-f]{40})"', json.dumps(x64))[0]))
print("iso-sha %s" % re.findall(r'"iso_sha256": "([0-9a-f]{64})"', json.dumps(x64))[0])
blob = re.findall(r'"sha256": "([0-9a-f]{64})"', json.dumps(x64["data"].get("release_esp", {})))
print("release-esp-digests %d" % len(blob))

# The header, from both sources: the report's derived markers and the raw serial line.
pat = re.compile(r"kernel-header version=(\d+) header=(\d+) image=(\d+) entry=0x([0-9a-f]+) "
                 r"bss=(\d+) arch=(\S+) machine=(\d+) checksum=(\w+)")
for label, body in (("cold", cold), ("warm", warm)):
    rep = x64["data"][label]
    m = rep["markers"]
    hit = pat.search(body)
    if hit is None:
        print("%s-HEADER-LINE-MISSING" % label)
        continue
    version, hsize, image, entry, bss, arch, machine, sumstate = hit.groups()
    print("%s-version %s" % (label, version))
    print("%s-header-size %s" % (label, hsize))
    print("%s-image %s" % (label, image))
    print("%s-entry %s" % (label, int(entry, 16)))
    print("%s-checksum %s" % (label, sumstate))
    print("%s-arch %s" % (label, arch))
    print("%s-machine %s" % (label, machine))
    print("%s-bss %s" % (label, bss))
    agree = (int(image) == m["kernel_header_image"] == m["kernel_bytes_read"]
             == m["handoff_bytes"])
    print("%s-image-sources-agree %s" % (label, agree))
    jump = re.search(r"LOADER-JUMP entry=0x([0-9a-f]+)", body)
    base = m["kernel_load_base"]
    print("%s-jump-arithmetic %s" % (label,
          jump is not None and int(jump.group(1), 16) == base + m["kernel_entry_declared"]))
    extent = re.search(r"PC5-64-IMAGE base=0x([0-9a-f]+) size=0x([0-9a-f]+)", body)
    print("%s-kernel-declares-same-extent %s" % (label, extent is not None
          and int(extent.group(2), 16) == int(image)))

# Four mutants: the named refusal, no entry point reached, no kernel banner after the refusal.
for name, expected in (("bad-signature", "kimage64-magic"), ("kernel-arch", "kimage64-arch"),
                       ("kernel-image-size", "kimage64-image-size"),
                       ("kernel-checksum", "kimage64-checksum")):
    neg = x64["data"]["negative"][name]
    marks = neg["markers"]
    body = text("ovmf64-serial-%s.log" % name)
    print("neg-%s-refusal %s" % (name, ",".join(marks["failure_codes"])))
    print("neg-%s-entry-false %s" % (name, marks["kernel_entry"] is False))
    print("neg-%s-no-jump %s" % (name, "LOADER-JUMP" not in body))
    print("neg-%s-no-banner %s" % (name, "PC5-64-BANNER" not in body))
    print("neg-%s-expected %s" % (name, expected))
    print("neg-%s-read-the-file %s" % (name, neg.get("iso", "")))

# The 32-bit path: same vocabulary as the previous run, and nothing from this one.
ia32_cold = text("ovmf32-serial-cold.log")
print("ia32-cold-boot-ok %s" % ("PC5-BOOT-OK" in ia32_cold))
print("ia32-cold-no-64-markers %s" % ("PC5-64-LOADER" not in ia32_cold))
names = [c["name"] for c in x64["checks"]]
stages = {c["name"]: c["state"] for c in x64["checks"]}
for wanted in ("[cold] the loader validated the kernel image's header before jumping",
               "[warm] the loader validated the kernel image's header before jumping",
               "[cold] execution began at the entry point the image declared",
               "[warm] execution began at the entry point the image declared",
               "[cold] the kernel image declared its own length and it matched",
               "[warm] the kernel image declared its own length and it matched",
               "the gate's kernel-header claims match the kernel image on the medium"):
    print("%s %s" % (wanted, "pass" if stages.get(wanted) == "pass" else "MISSING"))
# One row per refusal the x64 profile declares, each naming the refusal it expects.
hits = [n for n in names if " as kimage64-" in n]
print("kernel-controls %d" % len(hits))
for refusal in ("kimage64-magic", "kimage64-arch", "kimage64-image-size", "kimage64-checksum"):
    print("kernel-control-%s %s" % (refusal,
          "row" if any(refusal in n for n in hits) else "MISSING"))
PY

# 3. The numbers the summary table in README.md prints.
fact "x64-exit 0"                 "the 64-bit gate exited 0"
fact "x64-marker PC5-OVMF-BOOT-OK" "and its success marker is the real one, not absence of failure"
fact "x64-pass 88"                "89 checks, of which 88 pass"
fact "x64-fail 0"                 "none fail"
fact "x64-skip 1"                 "one path NOT TESTED (disk-style UEFI boot)"
fact "x64-checks 89"              "the check count README.md quotes"
fact "x64-rows 49"                "the matrix grew from 43 rows to 49"
fact "ia32-exit 0"                "the 32-bit gate still exits 0"
fact "ia32-pass 63"               "with the same 63 passing checks as before"
fact "ia32-fail 0"               "and no failures"
fact "ia32-rows 35"              "and the same 35 matrix rows: nothing was moved off the old path"
fact "ia32-cold-boot-ok True"    "the 32-bit log still reaches PC5-BOOT-OK"
fact "ia32-cold-no-64-markers True" "and still contains none of the 64-bit loader's vocabulary"
fact "commit 1fdb24e519d59b49152011ecc52e5da4ad57dadd" \
                                 "the report names the commit the artifacts were built from"
fact "iso-sha ae38b319d293ad529bd065100340b355bc76ead7baa29b8f04b1c7e47e5a203d" \
                                 "the ISO the gate booted is the digest `make iso verify` printed"

# 4. The header, both ways round.
for label in cold warm; do
    fact "$label-version 1"           "the $label boot validated header version 1"
    fact "$label-header-size 128"     "the $label boot validated a 128-byte header"
    fact "$label-image 208896"        "the $label boot's declared image length"
    fact "$label-checksum ok"         "the $label boot verified the payload checksum"
    fact "$label-arch x86-64"         "the $label boot accepted the x86-64 architecture id"
    fact "$label-machine 62"          "and the PE machine id that has to agree with it"
    fact "$label-image-sources-agree True" \
        "in $label: declared length == bytes read == handoff extent, and the serial line says the same"
    fact "$label-jump-arithmetic True" \
        "in $label: the jump address is load_base + the entry the image declared"
    fact "$label-kernel-declares-same-extent True" \
        "in $label: the kernel's own PC5-64-IMAGE size matches the header's declaration"
done
fact "[cold] the loader validated the kernel image's header before jumping pass" \
     "the cold run's header stage is a verified stage, not an unverified one"
fact "[warm] the loader validated the kernel image's header before jumping pass" \
     "and so is the warm run's -- the warm boot is not the cold one's echo"
fact "the gate's kernel-header claims match the kernel image on the medium pass" \
     "the vocabulary check compares the gate's claim with the artifact"
fact "kernel-controls 4" "four kernel-corruption controls, one per refusal the profile declares"
for refusal in kimage64-magic kimage64-arch kimage64-image-size kimage64-checksum; do
    fact "kernel-control-$refusal row" "a matrix row exists that expects exactly $refusal"
done

# 5. The mutants, from their own logs as well as the report.
for pair in "bad-signature kimage64-magic" "kernel-arch kimage64-arch" \
            "kernel-image-size kimage64-image-size" "kernel-checksum kimage64-checksum"; do
    set -- $pair
    log="ovmf64-serial-$1.log"
    expect "$log" "PC5-64-LOADER-FAIL=$2" "$1 is refused by name as $2"
    refuse "$log" "PC5-64-LOADER-JUMP" "$1 never reaches the jump into the kernel"
    refuse "$log" "PC5-64-BANNER" "$1 never prints the kernel's own banner"
    fact "neg-$1-refusal $2"          "the report's failure_codes name $2 and nothing else"
    fact "neg-$1-entry-false True"    "the report records the entry point as never reached for $1"
    fact "neg-$1-no-jump True"        "the $1 log has no jump line"
    fact "neg-$1-expected $2"         "and the gate expected exactly that refusal"
done
fact "neg-bad-signature-read-the-file bad-signature.iso" "the bad-signature mutant is its own ISO"

# 5b. The two raw 64-bit logs are a re-capture at the commit that lands the header (README.md
# says so, and says why: the gate's copies were overwritten by the 32-bit leg's run). What has
# to hold is that they describe the same artifact the report graded, byte for byte where the
# gate could see it.
expect x64-buildinfo-recaptured.txt "commit: d6b97dcbb21679b5b7d034df27da03f2b6bf7e4f" \
       "the re-captured ISO names the commit that added the header"
expect x64-buildinfo-recaptured.txt "esp-loader: 5a99ad0424898578" \
       "and the SHA-256 prefix of the loader inside its own ESP"
expect x64-buildinfo-recaptured.txt "1fa3f66d2ff63a9372a21304e3f19f0a23efe46a9fb0fde9b43cdca047e12e7f" \
       "with the ISO's own recorded digest beside it"
expect ovmf64-serial-cold.log "bootinfo=0xe5b6000 stack=0xe1fa000" \
       "the re-captured boot hands over at the same addresses the gate's report records"

# 6. What the 64-bit log shows about the TSS -- unchanged by this work, and recorded as such.
expect ovmf64-serial-cold.log "tr=not-loaded reason=ltr-unverifiable-here" \
       "Priority 1 is still open: TR is still not loaded, and the log says so"
expect ovmf64-serial-cold.log "ist-df=0 ist-nmi=0" \
       "and the IST stacks are still allocated but unused"

# 7. The good-path boot reached the end of the 64-bit path.
for log in ovmf64-serial-cold.log ovmf64-serial-warm.log; do
    expect "$log" "PC5-64BIT-LONG-MODE-OK" "$log completes long mode"
    expect "$log" "PC5-64-DONE"            "$log reports the kernel finished"
    refuse "$log" "PC5-64-LOADER-FAIL"    "$log reports no loader refusal"
    refuse "$log" "PC5-64-EXCEPTION"       "$log shows no unexpected exception"
done

printf '%s\n' "$(if [ "$fail" -eq 0 ]; then printf 'OK (%d checks)\n' "$notes"; else printf 'VERIFICATION FAILED\n'; exit 1; fi)"
