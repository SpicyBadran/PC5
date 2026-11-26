#!/bin/sh
# Re-derive this directory's claims from the bytes on file. No network, no build, no QEMU.
#
#   bash docs/evidence/2026-11-23-64bit-boot-gate/verification.sh   # expect: OK
#
# What is being verified is what README.md states: the four logs are the complete console
# output of four boots, the two 64-bit ones show PC5's loader running on the release medium
# under EDK II firmware (not merely firmware initialising), the two 32-bit ones still show
# what they showed before the gate learned a second vocabulary, and summary.json's numbers
# agree with the artifacts they were taken from -- including the two copies of the image's
# build record agreeing with each other, which is the attribution chain.
#
# python3 is used for JSON because the repository's toolchain requires it anyway.
set -eu
cd "$(dirname "$0")"
fail=0
notes=0
note() { printf '%-5s %s\n' "$1" "$2"; }
ok() { note PASS "$1"; notes=$((notes + 1)); }
bad() { note FAIL "$1"; fail=1; notes=$((notes + 1)); }
expect() { # expect <file> <pattern> <what it proves>
    if grep -q "$2" "$1"; then ok "$3"; else bad "$3 (no /$2/ in $1)"; fi
}
refuse() { # refuse <file> <pattern> <what must be absent>
    if grep -q "$2" "$1"; then bad "$3"; else ok "$3"; fi
}

# 1. The four logs are here and non-empty.
for f in ovmf64-serial-cold.log ovmf64-serial-warm.log \
         ovmf32-serial-cold.log ovmf32-serial-warm.log; do
    [ -s "$f" ] && ok "$f is present and non-empty" || bad "$f missing or empty"
done

# 2. The 64-bit boots went through firmware *and* PC5's loader.
for f in ovmf64-serial-cold.log ovmf64-serial-warm.log; do
    expect "$f" '^BdsDxe: starting Boot00' "firmware started the medium ($f)"
    expect "$f" '^PC5-64-LOADER firmware=EDK II' "the loader named its firmware ($f)"
    expect "$f" '^PC5-64-LOADER-BANNER' "BOOTX64.EFI executed ($f)"
    expect "$f" '^PC5-64-LOADER kernel at=0x100000 bytes=204800' \
           "the release KERNEL64.BIN was read at its recorded size ($f)"
    expect "$f" 'watchdog=disabled\|watchdog=disable-refused=' \
           "the firmware watchdog was settled one way or the other ($f)"
    expect "$f" '^PC5-64BIT-LONG-MODE-OK' "long mode verified ($f)"
    expect "$f" '^PC5-64-DONE' "the kernel reached its own end marker ($f)"
    refuse "$f" 'PC5-64-LOADER-FAIL' "no loader failure code ($f)"
done

# 3. Boot services left, the map handed over, the allocator sized from it.
line=$(grep '^PC5-64-LOADER exit-boot-services=ok' ovmf64-serial-cold.log)
case "$line" in
  *"attempts=1 descriptors=98"*) ok "ExitBootServices accepted first try with 98 descriptors" ;;
  *) bad "unexpected exit-boot-services line: $line" ;;
esac
expect ovmf64-serial-cold.log '^PC5-64-MAP entries=98' \
       "the kernel saw the same 98-descriptor map"
expect ovmf64-serial-cold.log '^PC5-64-MEM status=ok' "the frame allocator came from that map"
expect ovmf64-serial-cold.log '^PC5-64-MEM-SELFTEST' "post-handover allocator self-test ran"

# 4. TSS/IST: what is claimed, and what is deliberately not.
tss=$(grep '^PC5-64-TSS' ovmf64-serial-cold.log)
case "$tss" in *"status=ok"*) ;; *) bad "TSS line is not a success line: $tss" ;; esac
for field in ist1 ist2; do
    value=$(printf '%s\n' "$tss" | tr ' ' '\n' | sed -n "s/^$field=//p")
    case "$value" in
        0x0 | '' ) bad "$field is zero or absent, but the log is quoted as evidence of the stacks" ;;
        0x* ) ok "$field names an allocated stack ($value)" ;;
        * ) bad "$field is not printed in hex as expected: $value" ;;
    esac
done
if printf '%s\n' "$tss" | grep -q 'tr=loaded'; then
    bad "the log claims TR was loaded; this build does not load it"
else
    expect ovmf64-serial-cold.log 'tr=not-loaded reason=' \
           "TR reported as not loaded, with a reason"
fi

# 5. Cold and warm agree on everything that is not timing.
if diff <(grep '^PC5-64' ovmf64-serial-cold.log | grep -v 'spins=') \
        <(grep '^PC5-64' ovmf64-serial-warm.log | grep -v 'spins=') >/dev/null; then
    ok "the warm boot proved the same 64-bit stages as the cold boot"
else
    bad "the two 64-bit boots disagree beyond the timing lines"
fi

# 6. The 32-bit path is still verified, not merely un-regressed.
for f in ovmf32-serial-cold.log ovmf32-serial-warm.log; do
    expect "$f" '^PC5 UEFI LOADER' "the 32-bit loader banner is present ($f)"
    expect "$f" '^PC5-BOOT-OK' "the 32-bit kernel reached its end marker ($f)"
    refuse "$f" 'PC5-UEFI-FAILURE' "no 32-bit failure code ($f)"
done

# 7. The captured run logs, because the exit codes in README.md's table were read off a
#    terminal once and should not have to be trusted again.
for leg in x64 ia32; do
    expect "$leg-gate.log" 'GATE_EXIT=0' "the $leg gate recorded exit 0 in its own log"
    refuse "$leg-gate.log" '^\[FAIL' "no failed check in the $leg gate log"
    expect "$leg-build.log" 'MANIFEST VALID' "the $leg release image passed manifest verification"
    expect "$leg-metadata.log" 'verdict=PASS' "the $leg release record embedded a passing verdict"
done

# 8. summary.json, against the claims in README.md.
if python3 - <<'PY'
import json, sys

data = json.load(open("summary.json"))
gates = data["gates"]
problems = []


def eq(what, got, want):
    if got != want:
        problems.append("%s: got %r, expected %r" % (what, got, want))


for leg, rows, verified in (("x64", 43, 37), ("ia32", 35, 30)):
    g = gates[leg]
    eq("%s gate exit" % leg, g["gate_exit"], 0)
    eq("%s build exit" % leg, g["build_exit"], 0)
    eq("%s metadata exit" % leg, g["metadata_exit"], 0)
    eq("%s metadata verdict" % leg, g["metadata_verdict"], "PASS")
    eq("%s failed checks" % leg, g["checks_fail"], 0)
    eq("%s matrix rows" % leg, g["matrix_rows"], rows)
    eq("%s verified rows" % leg, g["matrix"]["verified"], verified)
    eq("%s ISO embedded the release ESP" % leg, g["release_esp_identical"], True)
    # The attribution chain: the record the boot report read off the medium, and the
    # record measured from the build tree, have to be the same record -- and they are only
    # worth anything if they name a commit.
    quoted, measured = g["iso_buildinfo_quoted_by_report"], g["iso_buildinfo_measured_from_file"]
    eq("%s build record agrees" % leg, quoted, measured)
    commit = (quoted or {}).get("commit") or ""
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        problems.append("%s: the ISO's build record does not name a commit: %r" % (leg, commit))
    eq("%s record names the mode it booted" % leg,
       quoted.get("uefi-path"), "64" if leg == "x64" else "32")
    if not quoted.get("build") or not quoted["build"].startswith(commit[:7]):
        problems.append("%s: build label %r does not match commit %r"
                        % (leg, quoted.get("build"), commit[:7]))
    if "-dirty" in (quoted.get("build") or ""):
        problems.append("%s: the recorded image was built from a dirty tree (%r); this "
                        "page claims a clean commit" % (leg, quoted["build"]))
    for name, digest in (g.get("serial_sha256") or {}).items():
        if len(digest) != 64:
            problems.append("%s: serial digest for %s is truncated" % (leg, name))

# The 64-bit leg's one honest gap: the corrupted-header row is NOT TESTED because the
# image format has no header. If that ever changes and this fails, the doc and the gate
# have to be updated together.
x64 = gates["x64"]
eq("x64 not-tested rows", x64["checks_not_tested"], 2)
if not any("no header field to corrupt" in name for name in x64["not_tested_names"]):
    problems.append("x64: the header control is not in the NOT TESTED list; if "
                    "KERNEL64.BIN has a header now, README.md and the gate both changed")
eq("ia32 not-tested rows", gates["ia32"]["checks_not_tested"], 1)

print("PASS summary.json agrees with the claim (%d legs)" % len(gates)) if not problems \
    else [print("FAIL summary.json: %s" % p) for p in problems]
sys.exit(1 if problems else 0)
PY
then
    ok "summary.json agrees with the claims above, for both legs"
else
    bad "summary.json disagrees with the claims in README.md"
fi

[ "$fail" -eq 0 ] && { echo "OK ($notes checks)"; exit 0; } || {
    echo "verification FAILED ($notes notes)"; exit 1; }
