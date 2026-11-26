#!/bin/sh
# Produce `unicorn-soft-int.txt`: the emulator's refusal, measured against the shipped image.
#
# This is the artifact behind the sentence "Unicorn does not deliver a guest software interrupt to
# a guest IDT", which is why the delivery probe is not run by the stub gate.  The file exists so
# that claim can be checked without re-deriving it from a test's docstring: the numbers in it are
# the address of the `int $8` in the built image and the address the emulator stopped on, printed
# by the same code path the test uses.
#
# Run from the repository root:  sh docs/evidence/2026-11-24-tss-delivery/capture-unicorn-refusal.sh
# It writes nothing outside the file named by $OUT.
set -eu
ROOT=${ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}
OUT=${OUT:-/tmp/pc5-unicorn-refusal.txt}
cd "$ROOT"

PYTHON=${PYTHON:-python3}
TMPDIR=${TMPDIR:-/tmp}
export TMPDIR

"$PYTHON" - "$OUT" <<'PY'
"""Print the refusal, then the test that pins it.  Deliberately tiny and direct: the point of
this file is that the claim is checkable *outside* the suite, so it does not import the suite's
skip logic -- without Unicorn it says so and exits non-zero."""
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path.cwd() / "tests"))
out = pathlib.Path(sys.argv[1])

lines = []
try:
    import unicorn  # noqa: F401
except ImportError:
    lines.append("UNICORN NOT INSTALLED: this environment cannot reproduce the refusal; "
                 "install the harness venv (requirements-harness.txt) and re-run")
    out.write_text("\n".join(lines) + "\n")
    raise SystemExit(1)

import kernel64_machine
import test_kernel64
import test_unicorn_soft_int as soft

image = test_kernel64.kernel_binary()
sites = soft.int_sites(image)
base = kernel64_machine.KERNEL_BASE
result = kernel64_machine.run_kernel(image, stop_markers=(), max_instructions=40_000_000)

lines.append("Unicorn %s, %s" % (unicorn.__version__, subprocess.run(
    ["uname", "-srm"], capture_output=True, text=True).stdout.strip()))
lines.append("image: %s (%d bytes, sha256 %s)"
             % (test_kernel64.KERNEL_BIN.relative_to(pathlib.Path.cwd()), len(image),
                __import__("hashlib").sha256(image).hexdigest()))
lines.append("`int $8` sites in the image: %s"
             % ", ".join("0x%x (file offset %d)" % (base + s, s) for s in sites))
lines.append("harness stop markers: cleared, so the run walks past PC5-64-DONE into the probe")
lines.append("run result: stopped_on=%r error=%r" % (result["stopped_on"], result["error"]))
lines.append("fault_rip: %s" % (hex(result["fault_rip"]) if result["fault_rip"] is not None
                                else "None"))
if len(sites) == 1:
    site = base + sites[0]
    lines.append("site == fault_rip or fault_rip - 2: %s"
                 % (result["fault_rip"] in (site, site + 2)))
lines.append("serial ends with: %r" % result["serial"][-80:].replace("\r", ""))
lines.append("")
lines.append("and the suite, run verbosely:")
done = subprocess.run([sys.executable, "-m", "unittest", "-v", "tests.test_unicorn_soft_int"],
                      capture_output=True, text=True, cwd=pathlib.Path.cwd())
lines.append(done.stdout.strip())
lines.append(done.stderr.strip())
lines.append("exit: %d" % done.returncode)

out.write_text("\n".join(lines) + "\n")
print(out.read_text())
PY
