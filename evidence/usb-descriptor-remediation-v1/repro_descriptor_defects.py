#!/usr/bin/env python3
"""Independent reproduction of the two defects in the *shipped* `pc5ctl usb` write path.

Run it as-is: it imports `tools/pc5ctl.py` from this repository and drives the real
production entry point (`pc5ctl.main(["usb", ...])`).  Nothing is patched in the product
source; the only substitutions are the seams the product itself reads, and they exist
because this sandbox cannot create a block device node (`mknod` is not permitted):

  * `Topology.sys_block` points at a sysfs-shaped fixture tree built by the repository's
    own `FakeTree` (the same fixture the tracked safety tests use), so the root storage
    and the "removable stick" are synthetic device identities;
  * `os.open` hands back a *real* descriptor — a `dup()` of a *real* ordinary file — so
    the open file description, `fstat`, `fcntl(F_GETFL)` and `/proc/self/fd/N` semantics
    are the kernel's;
  * `os.fstat` is made to report `S_IFBLK` with a chosen `major:minor`, which is the one
    thing a synthetic stand-in cannot produce by itself.

Everything downstream of those seams is genuine: the real `/bin/dd`, the real fd table,
the real `/proc`, the real exit status, and a real file that either receives bytes or does
not.  No block device is opened, created or written anywhere in this script.

Two reproductions:

  defect-1  the write child never receives the descriptor (`subprocess.call` without
            `pass_fds`), so the "write through /proc/self/fd/N" design cannot work;
  defect-2  the final safety decision re-resolves the *pathname*, so it can describe a
            different device than the object the descriptor (and therefore the write)
            refers to — a fail-open shape.
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

_REAL_EXISTS = os.path.exists

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))

import pc5ctl                                                    # noqa: E402
from test_pc5ctl_usb_safety import FakeTree, _topology          # noqa: E402

ROOT_DISK = (8, 0)          # the whole disk carrying '/'
ROOT_PARTITION = (8, 1)     # a partition of that same disk  -> must never be written
STICK = (8, 32)             # the removable device the operator named


def block_stat(devnum):
    """What `os.fstat` would answer for a real open block-device descriptor.

    Only the two fields production reads are supplied, and `st_rdev` is built with
    the kernel's own `makedev`, so `os.major`/`os.minor` do real work here.
    """
    from types import SimpleNamespace
    return SimpleNamespace(st_mode=stat.S_IFBLK | 0o660, st_rdev=os.makedev(*devnum))


class Fixture:
    """Synthetic sysfs + a real file standing behind a real descriptor."""

    def __init__(self, dd_calls=None):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.tree = FakeTree(base)
        self.tree.device(*ROOT_DISK, name="sda")
        self.tree.partition(*ROOT_PARTITION, "sda", "sda1")
        self.tree.device(*STICK, name="sdc")
        # The object the held descriptor really refers to.  An ordinary file, so the
        # kernel's own dup/fcntl/fstat/proc semantics apply to it.
        self.backing = base / "device-contents.img"
        self.backing.write_bytes(b"")
        self.backing_fd = os.open(self.backing, os.O_WRONLY)
        self.iso = base / "pc5.iso"
        self.iso.write_bytes(b"PC5-IMAGE-CONTENTS")
        digest = hashlib.sha256(self.iso.read_bytes()).hexdigest()
        (base / "pc5.iso.sha256").write_text(f"{digest}  pc5.iso\n")
        # One identity per pathname lookup, in call order; the last entry repeats so a
        # build that looks the name up fewer (or more) times still gets a coherent answer.
        self.lookups = []
        self.path_sequence = [STICK]
        self.dd_calls = dd_calls if dd_calls is not None else []
        self.held = []

    def close(self):
        try:
            os.close(self.backing_fd)
        except OSError:
            pass
        self.tmp.cleanup()

    def topology(self):
        topo = _topology(sys_block=str(self.tree.base), mounts="/nonexistent",
                         mountinfo="/nonexistent")
        topo.root_devnum = lambda: ROOT_DISK

        def devnum_for_path(path):
            i = len(self.lookups)
            self.lookups.append(path)
            return self.path_sequence[min(i, len(self.path_sequence) - 1)]

        topo.devnum_for_path = devnum_for_path
        return topo

    def open_target(self, path, flags, *mode):
        fd = os.dup(self.backing_fd)
        self.held.append(fd)
        return fd

    def record_dd(self, argv, **kwargs):
        self.dd_calls.append((list(argv), kwargs))
        return 0

    def run(self, use_real_dd=False):
        """Invoke the production CLI. Returns (rc, production stdout).

        With `use_real_dd` the child is the real /bin/dd; otherwise the launch is
        recorded, which is what makes the fail-open assertion safe to run.
        """
        import io
        out = io.StringIO()
        seams = [
            mock.patch.object(pc5ctl, "Topology", return_value=self.topology()),
            mock.patch.object(pc5ctl.os, "open", side_effect=self.open_target),
            mock.patch.object(pc5ctl.os, "fstat", return_value=block_stat(self.held_identity)),
            mock.patch.object(pc5ctl.shutil, "which", return_value="/bin/dd"),
            mock.patch.object(pc5ctl.os.path, "exists",
                              side_effect=lambda p: True if p == self.device
                              else _REAL_EXISTS(p)),
        ]
        if not use_real_dd:
            seams.append(mock.patch.object(pc5ctl.subprocess, "call",
                                          side_effect=self.record_dd))
        with ExitStack() as stack:
            for seam in seams:
                stack.enter_context(seam)
            with redirect_stdout(out):
                rc = pc5ctl.main(["usb", "--iso", str(self.iso),
                                  "--device", self.device, "--yes"])
        return rc, out.getvalue()

    # identity the descriptor reports; overridden per reproduction
    held_identity = ROOT_PARTITION
    device = "/dev/pc5-synthetic"


def child_output(fn):
    """Run fn with fd 2 redirected to a temp file, so a real child's stderr is captured.

    Returns ((production_rc, production_stdout), child_stderr_text): the production
    status line and the child's own complaint are separate evidence and both matter.
    """
    saved = os.dup(2)
    buf = tempfile.TemporaryFile(mode="w+b")
    os.dup2(buf.fileno(), 2)
    try:
        result = fn()
    finally:
        os.dup2(saved, 2)
        os.close(saved)
    buf.seek(0)
    text = buf.read().decode("utf-8", "replace")
    buf.close()
    return result, text


def banner(text):
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)


def defect_1():
    banner("defect-1  the child is never given the descriptor (no pass_fds)")
    print("The device the operator named resolves to a safe, unrelated stick, the open")
    print("succeeds, the identity check passes, and dd is launched for real.  The output")
    print("target the product chooses is /proc/self/fd/<fd>, but `subprocess.call` is")
    print("called without `pass_fds`, so Python closes that descriptor in the child.")
    fix = Fixture()
    fix.path_sequence = [STICK]
    fix.held_identity = STICK
    (rc, prod), dderr = child_output(lambda: fix.run(use_real_dd=True))
    written = fix.backing.stat().st_size
    print("\nproduction stdout (verbatim):")
    for line in prod.strip().splitlines():
        print("    | " + line)
    print(f"real /bin/dd child: production exit status = {rc}")
    print("child stderr (verbatim):")
    print("    | " + (dderr.strip().replace("\n", "\n    | ") if dderr.strip() else "<none>"))
    print(f"bytes received by the device stand-in: {written}")
    print(f"observed: the real child {'could not open its output target and nothing was written' if written == 0 else 'wrote through the descriptor'};")
    print("        the verdict is in `DEFECT-REPRO-pristine.log` (shipped source) versus")
    print("        `DEFECT-REPRO-corrected.log` (this repository's remediated source).")
    fix.close()

    banner("defect-1 control  the same code path, with the descriptor actually inherited")
    fix = Fixture()
    fix.path_sequence = [STICK]
    fix.held_identity = STICK
    real_call = subprocess.call

    def with_pass_fds(argv, **kwargs):
        # What the product would have to do: hand the validated descriptor to the child.
        fd = fix.held[-1] if fix.held else None
        return real_call(argv, close_fds=True, pass_fds=(fd,) if fd is not None else ())

    with mock.patch.object(pc5ctl.subprocess, "call", side_effect=with_pass_fds):
        (rc, prod), dderr = child_output(lambda: fix.run(use_real_dd=True))
    data = fix.backing.read_bytes()
    print(f"\nproduction exit status = {rc}")
    print(f"child stderr           = {dderr.strip() or '<none>'}")
    print(f"bytes received by the device stand-in: {len(data)}")
    print(f"content: {data!r}")
    print(f"sha256 of image on disk   : {hashlib.sha256(data).hexdigest()}")
    print(f"sha256 of image (expected): {hashlib.sha256(fix.iso.read_bytes()).hexdigest()}")
    print("observed: the only difference between the two runs above is whether the child")
    print("        inherited the descriptor, which isolates the failure to fd inheritance")
    print("        rather than to the file, the dd invocation, the offsets or the fixture.")
    fix.close()


def defect_2():
    banner("defect-2  the final safety decision is about the pathname, not the held object")
    print("Timeline the guard cannot see (all three lookups are separate `stat` calls):")
    print("  1. check_target_safe(device)      name -> stick (8,32)     => permitted")
    print("  2. os.open(device)                name -> root partition (8,1)  [swap in]")
    print("  3. devnum_for_path(device)        name -> root partition (8,1)  => matches fd")
    print("  4. check_target_safe(device)      name -> stick (8,32)     [swap back] => OK")
    print("  5. dd of=/proc/self/fd/<fd>       fd     -> root partition (8,1)")
    print("The descriptor and the *last* safety decision disagree about which device is")
    print("being written.  dd is mocked here so nothing is written anywhere; the assertion")
    print("is about whether the destructive call was made at all.")
    for label, sequence, held in [
        ("interleaved swaps (the 5-step timeline above)", [STICK, ROOT_PARTITION, STICK],
         ROOT_PARTITION),
        ("name pointing at the safe stick for every lookup while the fd holds the root partition",
         [STICK, STICK, STICK], ROOT_PARTITION),
    ]:
        fix = Fixture()
        fix.path_sequence = sequence
        fix.held_identity = held
        rc, prod = fix.run()
        dd = fix.dd_calls
        print(f"\n--- {label}")
        print(f"    exit status          : {rc}")
        print(f"    dd invocations      : {len(dd)}")
        if dd:
            argv, kwargs = dd[0]
            of = [a for a in argv if a.startswith("of=")]
            print(f"    dd output argument   : {of}")
            print(f"    pass_fds            : {kwargs.get('pass_fds', 'not passed')}")
        print(f"    stdout tail          : {prod.strip().splitlines()[-1] if prod.strip() else '<none>'}")
        print(f"    bytes on the device stand-in: {fix.backing.stat().st_size}")
        verdict = ("FAIL-OPEN: the write was authorised for a device the guard never examined"
                   if dd else
                   "REFUSED: the decision and the write target are the same object")
        print(f"    verdict              : {verdict}")
        fix.close()


def main():
    print(f"product under test : {REPO / 'tools/pc5ctl.py'}")
    print(f"source blob        : {os.environ.get('PC5_BLOB', 'working tree')}")
    print(f"pc5ctl sha256      : {hashlib.sha256((REPO / 'tools/pc5ctl.py').read_bytes()).hexdigest()}")
    print(f"python             : {sys.version.split()[0]}")
    print(f"dd                 : {subprocess.run(['/bin/dd', '--version'], capture_output=True, text=True).stdout.splitlines()[0]}")
    defect_1()
    defect_2()
    print("\nno block device was created, opened or written by this script.")


if __name__ == "__main__":
    main()
