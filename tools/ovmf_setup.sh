#!/bin/sh
# Provision the external dependencies of the UEFI integration gate.
#
# The gate needs two things this repository deliberately does not contain:
# a real UEFI firmware (TianoCore OVMF) and a machine to run it (QEMU).  Both
# are external, both are versioned by the distribution, and neither is ever
# committed, copied into the ISO or downloaded into the tree by a build.
#
# This script is the reproducible way to get them:
#
#     sh tools/ovmf_setup.sh              # install, then verify
#     sh tools/ovmf_setup.sh --check      # verify only, install nothing
#
# It is idempotent and refuses to guess: if it cannot find a package manager it
# prints the package names and exits non-zero rather than pretending to have
# provisioned anything.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

say() { printf '%s\n' "$*"; }
fail() { printf 'ovmf-setup: %s\n' "$*" >&2; exit 1; }

install_packages() {
    if command -v apt-get >/dev/null 2>&1; then
        say "installing with apt-get: qemu-system-x86 ovmf"
        ${SUDO} apt-get update -qq
        ${SUDO} env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            qemu-system-x86 ovmf
    elif command -v dnf >/dev/null 2>&1; then
        say "installing with dnf: qemu-system-x86 edk2-ovmf"
        ${SUDO} dnf install -y -q qemu-system-x86 edk2-ovmf
    elif command -v yum >/dev/null 2>&1; then
        say "installing with yum: qemu-system-x86 edk2-ovmf"
        ${SUDO} yum install -y -q qemu-system-x86 edk2-ovmf
    elif command -v pacman >/dev/null 2>&1; then
        say "installing with pacman: qemu-system-x86 edk2-ovmf"
        ${SUDO} pacman -Sy --noconfirm --needed qemu-system-x86 edk2-ovmf
    elif command -v zypper >/dev/null 2>&1; then
        say "installing with zypper: qemu-x86 ovmf"
        ${SUDO} zypper --non-interactive install -y qemu-x86 ovmf
    else
        fail "no known package manager. Install these by hand:
      qemu-system-x86_64   (QEMU, version 6.0 or newer)
      OVMF_CODE*.fd        (EDK II firmware, CODE image)
      OVMF_VARS*.fd        (EDK II variable store template)
      OVMF_CODE*.secboot.fd / OVMF_VARS*.ms.fd   (only for --secure-boot)
    then re-run: sh tools/ovmf_setup.sh --check"
    fi
}

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
fi

if [ "$CHECK_ONLY" -eq 0 ]; then
    install_packages
fi

say ""
say "discovery:"
python3 "$ROOT/tests/ovmf_boot.py" --list-firmware || fail "discovery failed"

QEMU=$(command -v qemu-system-x86_64 || true)
[ -n "$QEMU" ] || fail "qemu-system-x86_64 is still not on PATH"

say ""
say "qemu:    $QEMU"
say "version: $("$QEMU" --version | head -n 1)"

python3 - "$ROOT" <<'PY'
import hashlib
import os
import sys

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "tests"))
import ovmf_boot  # noqa: E402

code = ovmf_boot.find_first(ovmf_boot.CODE_CANDIDATES)
vars_ = ovmf_boot.find_first(ovmf_boot.VARS_CANDIDATES)
if not code or not vars_:
    sys.exit("ovmf-setup: no OVMF CODE/VARS pair found after installation")

def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()

print("firmware (record these in the test report):")
for label, path in (("CODE", code), ("VARS", vars_)):
    print("  %-4s %-52s %9d bytes  sha256 %s"
          % (label, path, os.path.getsize(path), digest(path)))
info = ovmf_boot.describe_firmware(code)
print("  OVMF volume: %s" % ("recognised" if info["ok"] else info["problems"]))
PY

say ""
say "OVMF is provisioned. Nothing was copied into the repository."
say "Run the gate with:  make ovmf-test"
say "Include Secure Boot: make ovmf-test OVMF_EXTRA=--secure-boot"
