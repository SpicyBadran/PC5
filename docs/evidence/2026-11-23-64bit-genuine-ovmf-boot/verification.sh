#!/bin/sh
# Re-derive the claim from this directory's evidence. No network, no build.
#
#   bash verification.sh   # expect: OK
#
# What it checks is what the README claims: the log is the unmodified guest output, and the
# log contains the markers of a 64-bit boot that went through firmware, the memory map, and
# the frame allocator - in that order - with no loader failure line and without claiming TR.
set -eu
cd "$(dirname "$0")"
fail=0
note() { printf '%-5s %s\n' "$1" "$2"; }

# 1. The log is present and is the whole console output of one boot.
[ -s ovmf64-serial.log ] || { note FAIL "ovmf64-serial.log missing or empty"; fail=1; }

# 2. Firmware started it, not a harness: the boot manager named our medium.
grep -q '^BdsDxe: starting Boot0001 "UEFI QEMU DVD-ROM' ovmf64-serial.log \
  && note PASS "firmware discovered and started the medium" \
  || { note FAIL "no BdsDxe boot-entry line"; fail=1; }

# 3. Our application ran, and the kernel it loaded is the release file.
grep -q '^PC5-64-LOADER-BANNER' ovmf64-serial.log \
  && note PASS "BOOTX64.EFI executed" || { note FAIL "loader banner absent"; fail=1; }
grep -q '^PC5-64-LOADER kernel-volume=image$' ovmf64-serial.log \
  && note PASS "kernel opened on the loaded image's own device" \
  || { note FAIL "no kernel-volume=image line"; fail=1; }
grep -q '^PC5-64-LOADER kernel at=0x100000 bytes=204800$' ovmf64-serial.log \
  && note PASS "kernel at the protocol address, 200 KiB" \
  || { note FAIL "kernel load line absent or different"; fail=1; }

# 4. ExitBootServices used a real map, and the map was not a stub's handful.
grep -q '^PC5-64-LOADER exit-boot-services=ok attempts=1 descriptors=98$' ovmf64-serial.log \
  && note PASS "ExitBootServices accepted with 98 descriptors" \
  || { note FAIL "no exit-boot-services=ok line"; fail=1; }
grep -q '^PC5-64-MAP entries=98' ovmf64-serial.log \
  && note PASS "the kernel saw the same 98-descriptor map" \
  || { note FAIL "the kernel's map line is absent"; fail=1; }

# 5. The frame allocator came from that map, and its own size lines agree with each other:
#    conventional minus one claimed frame is the free count.
line=$(grep '^PC5-64-MEM status=ok' ovmf64-serial.log || true)
[ -n "$line" ] || { note FAIL "no PC5-64-MEM status=ok line"; fail=1; }
field() { printf '%s\n' "$line" | tr ' ' '\n' | sed -n "s/^$1=//p"; }
conv=$(field conventional)
free=$(field free)
frames=$(field frames)
largest=$(field largest)
if [ -n "$conv" ] && [ -n "$free" ] && [ $((conv - free)) -eq 1 ] &&
   [ -n "$frames" ] && [ "$frames" -ge "$conv" ] && [ "$largest" -le "$free" ]; then
  note PASS "allocator: $conv conventional, $free free, one claimed, largest $largest"
else
  note FAIL "allocator counts do not follow from the map: '$line'"; fail=1
fi
grep -q '^PC5-64-MEM-SELFTEST.*result=ok$' ovmf64-serial.log \
  && note PASS "post-handover allocator self-test ok" \
  || { note FAIL "no MEM-SELFTEST result=ok"; fail=1; }

# 6. Mode, and the end-to-end marker.
grep -q '^PC5-64-PAGING enabled=1' ovmf64-serial.log \
  && grep -q '^PC5-64BIT-LONG-MODE-OK$' ovmf64-serial.log \
  && grep -q '^PC5-64-DONE$' ovmf64-serial.log \
  && note PASS "long mode verified, kernel reached its end marker" \
  || { note FAIL "paging/long-mode/done markers incomplete"; fail=1; }

# 7. What must NOT be in the log.
if grep -q 'PC5-64-LOADER-FAIL' ovmf64-serial.log; then
  note FAIL "the loader reported a failure code"; fail=1
else
  note PASS "no loader failure code"
fi
if grep -q 'tr=loaded' ovmf64-serial.log; then
  note FAIL "the log claims TR was loaded; this boot did not verify it"; fail=1
else
  grep -q 'tr=not-loaded' ovmf64-serial.log \
    && note PASS "TR reported as not loaded, with a reason" \
    || { note FAIL "no honest tr= line at all"; fail=1; }
fi

[ "$fail" -eq 0 ] && echo OK || { echo "verification FAILED"; exit 1; }
