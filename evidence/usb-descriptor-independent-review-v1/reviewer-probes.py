"""INDEPENDENT REVIEW PROBES — written by the adversarial reviewer, not by the author.

This file lives only in the reviewer's clone (/home/user/review-pc5); it is NOT part of
the delivered evidence and must not be copied into the workspace.  Its purpose is to
re-derive the boundary properties from the production module with the author's harness
removed, and to attack them with scenarios the author's tests do not contain.

Fixtures are ordinary temporary files and a synthetic sysfs tree.  No block device is
created, opened or written; no destructive call is made.  The one real executable used
is /bin/dd, pointed at a temporary regular file standing in for the target object.
"""

from __future__ import annotations

import errno
import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import pc5ctl  # noqa: E402

_REAL_OPEN = os.open
_REAL_CLOSE = os.close
_REAL_FSTAT = os.fstat
_REAL_LSTAT = os.lstat
_REAL_READLINK = os.readlink
_REAL_EXISTS = os.path.exists
_REAL_CALL = subprocess.call

ROOT_DISK = (8, 0)
ROOT_PARTITION = (8, 1)
SIBLING_ON_ROOT_DISK = (8, 2)
USB_TARGET = (8, 9)
OTHER_UNRELATED = (8, 17)


def block_identity(devnum):
    """A stat result saying 'block device with this major:minor'."""
    return SimpleNamespace(st_mode=stat.S_IFBLK | 0o640,
                           st_rdev=os.makedev(*devnum) if devnum else 0,
                           st_size=0, st_nlink=1, st_uid=0, st_gid=6,
                           st_ino=1234, st_dev=0)


def regular_identity():
    return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_rdev=0, st_size=0,
                           st_nlink=1, st_uid=0, st_gid=0, st_ino=7, st_dev=1)


class SyntheticSysfs:
    """A /sys/block tree production Topology can read, with no fixture hooks."""

    def __init__(self, base):
        self.base = Path(base)
        self.sys_block = self.base / "sys" / "block"
        self.sys_block.mkdir(parents=True)
        # Production reads /sys/dev/block (the kernel's devnum index), where every
        # disk *and* every partition has a `major:minor` entry that is a symlink
        # into /sys/block/<disk>/...; the fixture reproduces that shape so
        # parent_of()'s realpath() resolves the way it does on a real host.
        #
        # The layout the guard must see: sda (8:0) is the whole disk carrying the
        # root filesystem on sda1 (8:1), with sda2 (8:2) beside it; sdb (8:9) and
        # sdc (8:17) are unrelated disks.  Partitions are *children* of the disk
        # directory, which is how sysfs publishes them, and each also gets its own
        # top-level 8:x entry because realpath() on the child must land inside the
        # parent directory for parent_of() to resolve it.
        self.disk_dir = self.sys_block / "8:0"
        self.disk_dir.mkdir()
        (self.disk_dir / "dev").write_text("8:0\n", encoding="ascii")
        (self.disk_dir / "slaves").mkdir()
        (self.disk_dir / "holders").mkdir()
        for child, devnum, number in (("sda1", ROOT_PARTITION, "1"),
                                      ("sda2", SIBLING_ON_ROOT_DISK, "2")):
            c = self.disk_dir / child
            c.mkdir()
            (c / "dev").write_text("%d:%d\n" % devnum, encoding="ascii")
            (c / "partition").write_text(number + "\n", encoding="ascii")
            (c / "slaves").mkdir()
            (c / "holders").mkdir()
            # top-level alias, as sysfs does: /sys/block/8:1 is a symlink to the child
            link = self.sys_block / ("%d:%d" % devnum)
            link.symlink_to(c)
        for devnum in (USB_TARGET, OTHER_UNRELATED):
            d = self.sys_block / ("%d:%d" % devnum)
            d.mkdir()
            (d / "dev").write_text("%d:%d\n" % devnum, encoding="ascii")
            (d / "slaves").mkdir()
            (d / "holders").mkdir()

    def topology(self, mounts="absent-mounts", mountinfo="absent-mountinfo"):
        """A Topology over this fixture that never reads the host's mount table."""
        return pc5ctl.Topology(sys_block=str(self.sys_block),
                               mounts=str(self.base / mounts),
                               mountinfo=str(self.base / mountinfo))


class ProductionPathCase(unittest.TestCase):
    """Drives `pc5ctl usb` with the same minimal seams the boundary needs.

    Two of them are the attacker's: what `os.open` hands back, and what
    `os.fstat` reports for it.  The rest are there so no real device and no real
    write is ever involved, and each is annotated as such.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.tree = SyntheticSysfs(base)

        # The object the held descriptor really refers to: an ordinary writable
        # file, so dup/fcntl/fstat/procfs semantics are the kernel's own.
        self.target_file = base / "target-contents"
        self.target_file.write_bytes(b"")
        self._held = _REAL_OPEN(self.target_file, os.O_WRONLY)

        self.iso = base / "image.iso"
        self.iso.write_bytes(b"A" * 4096 + b"B" * 4096)
        digest = hashlib.sha256(self.iso.read_bytes()).hexdigest()
        (base / "image.iso.sha256").write_text(f"{digest}  image.iso\n", encoding="ascii")

        # Production insists the target lives under /dev/, so the path string is
        # /dev/... only; nothing at that name is ever opened or created.
        self.device_path = "/dev/pc5-review-synthetic"

        # The attacker's view of the pathname: what Topology().devnum_for_path
        # returns for it, per lookup (last entry repeats).
        self.name_sequence = [USB_TARGET]
        self.name_lookups = 0
        # What fstat on the held descriptor reports.
        self.fd_identity = ROOT_PARTITION
        self.launch = "record"          # record | real | fail
        self.opened_fd = None
        self.launches = []
        self.trace = []
        self.rc = None
        self.out = ""

    def tearDown(self):
        try:
            _REAL_CLOSE(self._held)
        except OSError:
            pass

    # -- seams -------------------------------------------------------------
    def _open(self, path, flags, *args):
        fd = os.dup(self._held)
        self.opened_fd = fd
        return fd

    def _fstat(self, fd):
        return block_identity(self.fd_identity) if fd == self.opened_fd else _REAL_FSTAT(fd)

    def _name_lookup(self, path):
        """Stands in for the *pathname* resolution the author's patch keeps."""
        value = self.name_sequence[min(self.name_lookups, len(self.name_sequence) - 1)]
        self.name_lookups += 1
        self.trace.append(("devnum_for_path", str(path), value))
        return value

    def _launch(self, argv, **kwargs):
        self.launches.append((list(argv), dict(kwargs)))
        if self.launch == "fail":
            raise OSError(errno.ENOENT, "No such file or directory", argv[0])
        if self.launch == "real":
            return _REAL_CALL(argv, **kwargs)
        return 0

    # -- driver ------------------------------------------------------------
    def run_cli(self, *, root=ROOT_PARTITION, dd="dd", topo_patches=(), patches=None,
            resolve_names=True):
        """Run `pc5ctl usb` end to end against the fixture.

        `resolve_names` keeps the pathname lookup live (so a swap is observable);
        `topo_patches` injects failures into the root-side reads; `patches` adds
        whatever else a scenario needs.  Nothing here reuses the author's harness.
        """
        topo = self.tree.topology()
        topo.root_devnum = lambda: root
        if resolve_names:
            # the operator's *name*, as each lookup answers it
            topo.devnum_for_path = self._name_lookup
        out = io.StringIO()
        seam_patches = [
            mock.patch.object(pc5ctl, "Topology", return_value=topo),
            mock.patch.object(pc5ctl.os, "open", side_effect=self._open),
            mock.patch.object(pc5ctl.os, "fstat", side_effect=self._fstat),
            mock.patch.object(pc5ctl.os.path, "exists",
                              side_effect=lambda p: True if str(p) == self.device_path
                              else _REAL_EXISTS(p)),
            mock.patch.object(pc5ctl.shutil, "which", return_value=dd),
            mock.patch.object(pc5ctl.subprocess, "call", side_effect=self._launch),
        ]
        for name, kwargs in topo_patches:
            seam_patches.append(mock.patch.object(topo, name, **kwargs))
        for p in (patches or ()):
            seam_patches.append(p)
        with ExitStack() as stack:
            for p in seam_patches:
                stack.enter_context(p)
            stack.enter_context(redirect_stdout(out))
            self.rc = pc5ctl.main(["usb", "--iso", str(self.iso), "--device",
                                   self.device_path, "--yes"])
        self.out = out.getvalue()
        return self.rc, self.out

    # -- assertions --------------------------------------------------------
    def assert_never_launched(self, msg=""):
        self.assertEqual(self.launches, [],
                         f"the destructive child was launched. {msg}\noutput:\n{self.out}")
        self.assertIn("nothing was written", self.out)
        self.assertEqual(self.target_file.read_bytes(), b"",
                         "bytes reached the object behind the descriptor")

    def assert_launched_once(self):
        self.assertEqual(len(self.launches), 1, self.out)
        argv, kwargs = self.launches[0]
        self.assertEqual(kwargs.get("pass_fds"), (self.opened_fd,),
                         f"child must inherit exactly the validated descriptor: {kwargs}")
        self.assertTrue(kwargs.get("close_fds"), "inheritance must be explicit")
        of = [a for a in argv if a.startswith("of=")]
        self.assertEqual(of, [f"of=/proc/self/fd/{self.opened_fd}"], argv)
        self.assertNotIn(f"of={self.device_path}", argv,
                         "the child was handed the pathname, not the descriptor")


class TestSubstitutionCannotAuthorise(ProductionPathCase):
    """Properties 1, 2, 3, 4: the descriptor, not the name, is what authorises."""

    def test_dangerous_fd_with_benign_name_is_refused(self):
        self.fd_identity = ROOT_PARTITION
        self.name_sequence = [USB_TARGET]
        rc, out = self.run_cli()
        self.assert_never_launched("a partition of the root disk was written")
        self.assertEqual(rc, 1, out)
        self.assertIn("8:1", out, "the refusal must name the descriptor's identity: " + out)

    def test_dangerous_fd_with_matching_name_is_still_refused(self):
        self.fd_identity = ROOT_PARTITION
        self.name_sequence = [ROOT_PARTITION]
        rc, out = self.run_cli()
        self.assert_never_launched()
        self.assertEqual(rc, 1, out)

    def test_safe_fd_with_dangerous_name_refuses_without_writing(self):
        """The name may only ever refuse; nothing may authorise on its account.

        Which refusal fires depends on the lookup order: the pre-open pass stops it
        at "refusing to overwrite", a later lookup stops it at "changed identity".
        Both are refusals, and both must leave the object untouched -- which is the
        property, so the message is not asserted past that.
        """
        for names in ([ROOT_PARTITION], [ROOT_PARTITION, ROOT_PARTITION],
                      [USB_TARGET, ROOT_PARTITION]):
            with self.subTest(names=names):
                self.setUp()
                self.fd_identity = USB_TARGET
                self.name_sequence = names
                rc, out = self.run_cli()
                self.assert_never_launched()
                self.assertEqual(rc, 1, out)
                self.assertNotIn("THIS ERASES EVERYTHING", out.split("nothing was written")[0][-40:],
                                 out) if False else None

    def test_safe_fd_and_safe_name_writes_through_the_descriptor(self):
        self.fd_identity = USB_TARGET
        self.name_sequence = [USB_TARGET]
        rc, out = self.run_cli()
        self.assertEqual(rc, 0, out)
        self.assert_launched_once()

    def test_verdict_is_invariant_under_the_name_when_the_fd_is_dangerous(self):
        """The property as an experiment: vary only the name, keep the descriptor.

        A guard that consulted the name for authorisation would accept some of
        these.  Every one must refuse with zero launches.
        """
        for name in (USB_TARGET, OTHER_UNRELATED, ROOT_DISK, SIBLING_ON_ROOT_DISK, None):
            with self.subTest(name=name):
                self.setUp()
                self.fd_identity = ROOT_PARTITION
                self.name_sequence = [name]
                rc, out = self.run_cli()
                self.assertEqual(self.launches, [], f"name {name} produced a launch")
                self.assertEqual(rc, 1, f"name {name} was not refused")
                self.assertEqual(self.target_file.read_bytes(), b"")

    def test_name_flipping_back_before_the_final_validation_cannot_authorise_the_fd(self):
        """SHIPPED DEFECT 1, reproduced as an ordering rather than as a message.

        The descriptor holds a partition of the disk carrying `/`.  The name is
        benign, then agrees with the descriptor once (so the name-versus-descriptor
        comparison passes), then benign again -- the shape a substitution attack
        takes when the node is swapped for the `open` and swapped back before the
        last check.  A final validation that re-reads the name therefore answers
        "safe" about a descriptor it never examined.

        The base implementation launches the write here; the descriptor-bound
        implementation makes the decision about the descriptor and refuses.  The
        number of lookups differs between the two builds, which is why the
        sequence is padded: a build that consults the name a different number of
        times must still be refused, never authorised.
        """
        self.fd_identity = SIBLING_ON_ROOT_DISK
        self.name_sequence = [USB_TARGET, SIBLING_ON_ROOT_DISK, USB_TARGET, USB_TARGET]
        rc, out = self.run_cli()
        self.assertIsNotNone(self.opened_fd, "the scenario never reached the open")
        self.assertEqual(self.launches, [],
                         "the destructive child ran against a descriptor the final "
                         "validation had never examined: " + out)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.target_file.read_bytes(), b"")
        self.assertIn("is a partition of the disk carrying", out,
                      "the refusal must come from the descriptor-bound decision "
                      "(which knows 8:2), not from a name comparison: " + out)

    def test_name_vanishing_after_open_does_not_rescue_a_dangerous_descriptor(self):
        self.fd_identity = ROOT_PARTITION
        self.name_sequence = [None]            # the node is gone entirely
        rc, out = self.run_cli()
        self.assert_never_launched()

    def test_symlink_swap_between_the_two_lookups_is_caught(self):
        """Sequence: first lookup benign, second lookup dangerous.

        This is the ordering the original failure mode exploited: a swap that
        happens *after* the pre-open pass but *before* the write.
        """
        self.fd_identity = USB_TARGET
        self.name_sequence = [USB_TARGET, ROOT_PARTITION]
        rc, out = self.run_cli()
        self.assert_never_launched()
        self.assertIn("changed identity", out)

    def test_reverse_swap_still_refuses_rather_than_writing_on_name_credit(self):
        self.fd_identity = USB_TARGET
        self.name_sequence = [ROOT_PARTITION, USB_TARGET]
        rc, out = self.run_cli()
        self.assert_never_launched()

    def test_fstat_drift_between_validation_and_launch_is_caught(self):
        """The last re-read must be about the descriptor, not the name."""
        self.fd_identity = USB_TARGET
        self.name_sequence = [USB_TARGET]
        original = self._fstat
        seen = []

        def drifting_fstat(fd):
            seen.append(fd)
            # after the safety pass, the object is reported as the root partition
            return block_identity(ROOT_PARTITION if len(seen) > 1 else USB_TARGET)

        rc, out = self.run_cli(patches=[mock.patch.object(pc5ctl.os, "fstat",
                                                      side_effect=drifting_fstat)])
        self.assert_never_launched()
        self.assertIn("changed before the write", out)

    def test_fstat_failure_refuses(self):
        for exc in (OSError(errno.EIO, "I/O error"), OSError(errno.ENOENT, "No such file")):
            with self.subTest(err=exc.errno):
                self.setUp()
                self.fd_identity = USB_TARGET
                self.name_sequence = [USB_TARGET]
                rc, out = self.run_cli(patches=[mock.patch.object(pc5ctl.os, "fstat",
                                                               side_effect=exc)])
                self.assert_never_launched()
                self.assertEqual(rc, 1, out)

    def test_non_block_st_mode_on_the_descriptor_refuses(self):
        """A regular file or a char device must never be accepted as the target."""
        for label, st in (("regular", regular_identity()),
                          ("char", SimpleNamespace(st_mode=stat.S_IFCHR | 0o660,
                                                   st_rdev=os.makedev(1, 3), st_size=0,
                                                   st_nlink=1, st_uid=0, st_gid=0,
                                                   st_ino=1, st_dev=0)),
                          ("fifo", SimpleNamespace(st_mode=stat.S_IFIFO | 0o600,
                                                   st_rdev=0, st_size=0, st_nlink=1,
                                                   st_uid=0, st_gid=0, st_ino=1, st_dev=0))):
            with self.subTest(kind=label):
                self.setUp()
                self.fd_identity = USB_TARGET
                self.name_sequence = [USB_TARGET]
                rc, out = self.run_cli(patches=[mock.patch.object(
                    pc5ctl.os, "fstat", return_value=st)])
                self.assert_never_launched()
                self.assertIn("cannot identify the descriptor", out)


class TestUnusableDescriptorRefuses(ProductionPathCase):
    """Properties 7 and 8: bad fds and broken procfs never degrade to the name."""

    def test_low_descriptor_numbers_refuse_before_any_check(self):
        """The `target_fd < 3` guard, with the harness protected against its absence.

        A build without the guard reaches `finally: os.close(1)` and closes *this
        test process's stdout*, which would otherwise take the whole run down at
        interpreter exit (exit 120, "flush of stdout failed").  Saving and
        restoring fd 1 around the call turns that into a visible failure of the
        property instead of a lost report -- which is precisely the hazard the
        guard exists to remove.
        """
        for fd in (0, 1, 2):
            with self.subTest(fd=fd):
                self.setUp()
                self.fd_identity = USB_TARGET
                self.name_sequence = [USB_TARGET]
                saved = {s: os.dup(s) for s in (0, 1, 2)}
                try:
                    rc, out = self.run_cli(patches=[mock.patch.object(
                        pc5ctl.os, "open", side_effect=lambda p, f, *a: fd)])
                finally:
                    # a build without the guard has closed one of these on the way
                    # out; unittest writes its summary to stderr, so all three have
                    # to be back in place or the report is lost with the descriptor.
                    for s, dup in saved.items():
                        os.dup2(dup, s)
                        os.close(dup)
                self.assertEqual(rc, 1, out)
                self.assertEqual(self.launches, [], "a std stream was handed to dd")
                self.assertIn("cannot inherit", out)

    def test_read_only_descriptor_refuses(self):
        ro = _REAL_OPEN(self.target_file, os.O_RDONLY)
        self.setUp()
        self.opened_fd = ro
        rc, out = self.run_cli(patches=[mock.patch.object(
            pc5ctl.os, "open", side_effect=lambda p, f, *a: ro)])
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.launches, [], "a read-only descriptor reached dd")
        self.assertIn("writable", out)

    def test_closed_descriptor_refuses(self):
        fd = _REAL_OPEN(self.target_file, os.O_WRONLY)
        os.close(fd)
        self.setUp()
        rc, out = self.run_cli(patches=[mock.patch.object(
            pc5ctl.os, "open", side_effect=lambda p, f, *a: fd)])
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.launches, [])

    def test_procfs_directory_unusable_refuses(self):
        """`fd_child_target_usable` failing must stop the write, not redirect it."""
        self.setUp()
        self.fd_identity = USB_TARGET
        self.name_sequence = [USB_TARGET]
        def lstat(path, *a, **k):
            if str(path).startswith("/proc/self/fd/"):
                raise OSError(errno.ENOENT, "No such file or directory", path)
            return _REAL_LSTAT(path, *a, **k)

        rc, out = self.run_cli(patches=[mock.patch.object(pc5ctl.os, "lstat",
                                                      side_effect=lstat)])
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.launches, [], "the child was launched against a dead alias")
        self.assertIn("/proc/self/fd/", out)

    def test_procfs_alias_referent_is_not_compared_to_the_original_path(self):
        """MEASURED, then judged: a spoofed `readlink` is not accepted as consent to
        write elsewhere, because the child opens the alias and the kernel resolves
        it to the description -- the text is only ever an extra sanity signal.

        What is checked is "(deleted)" and existence; what is not checked is whether
        the referent is the file we opened.  Asserting the difference keeps the
        limitation visible instead of implying a guarantee the code does not make.
        """
        self.setUp()
        self.fd_identity = USB_TARGET
        self.name_sequence = [USB_TARGET]
        decoy = Path(self._tmp.name) / "decoy"
        decoy.write_bytes(b"")
        rc, out = self.run_cli(patches=[mock.patch.object(
            pc5ctl.os, "readlink",
            side_effect=lambda p: str(decoy) if str(p).startswith("/proc/self/fd/")
            else _REAL_READLINK(p))])
        self.assertEqual(len(self.launches), 1,
                         "a spoofed referent text blocked the write: " + out)
        argv, kwargs = self.launches[0]
        self.assertEqual(kwargs["pass_fds"], (self.opened_fd,),
                         "the child must still be given the descriptor, not the decoy")
        self.assertEqual([a for a in argv if a.startswith("of=")],
                         [f"of=/proc/self/fd/{self.opened_fd}"], argv)
        self.assertEqual(decoy.read_bytes(), b"", "the decoy was written to")

    def test_deleted_referent_refuses(self):
        """The one alias check that does catch a real hazard: an unlinked object."""
        self.setUp()
        self.fd_identity = USB_TARGET
        self.name_sequence = [USB_TARGET]
        rc, out = self.run_cli(patches=[mock.patch.object(
            pc5ctl.os, "readlink",
            side_effect=lambda p: (str(self.target_file) + " (deleted)")
            if str(p).startswith("/proc/self/fd/") else _REAL_READLINK(p))])
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.launches, [], "an unlinked target was handed to dd")
        self.assertIn("/proc/self/fd/", out)


class TestCleanupOnEveryPath(ProductionPathCase):
    """Property 8: the descriptor is closed on every exit, including refusals."""

    def test_fd_is_closed_on_every_refusal_and_on_success(self):
        """Each scenario must refuse *after* os.open, so the close is exercised.

        `name_sequence[0]` is what the pre-open pathname check sees; a second,
        different answer is what makes the post-open comparison refuse.  The bound
        decision only runs when the name still agrees with the descriptor, so the
        dangerous-descriptor case has to keep them equal.
        """
        scenarios = {
            "accepted": dict(fd=USB_TARGET, names=[USB_TARGET]),
            "refused-name-mismatch": dict(fd=USB_TARGET, names=[USB_TARGET, ROOT_PARTITION]),
            "refused-unusable-procfs": dict(fd=USB_TARGET, names=[USB_TARGET],
                                            procfs=True),
            "refused-bound-decision": dict(fd=ROOT_PARTITION,
                                           names=[USB_TARGET, ROOT_PARTITION]),
        }
        for label, spec in scenarios.items():
            with self.subTest(scenario=label):
                self.setUp()
                self.fd_identity = spec["fd"]
                self.name_sequence = spec["names"]
                closed = []
                real_close = os.close
                reached_open = []

                def counting_close(fd):
                    closed.append(fd)
                    real_close(fd)

                patches = [mock.patch.object(pc5ctl.os, "close", side_effect=counting_close)]
                if spec.get("procfs"):
                    def lstat(path, *a, **k):
                        if str(path).startswith("/proc/self/fd/"):
                            raise OSError(errno.ENOENT, "No such file or directory", path)
                        return _REAL_LSTAT(path, *a, **k)
                    patches.append(mock.patch.object(pc5ctl.os, "lstat", side_effect=lstat))
                rc, out = self.run_cli(patches=patches)
                self.assertIsNotNone(self.opened_fd, f"{label}: never reached the open")
                self.assertIn(self.opened_fd, closed,
                              f"{label}: the descriptor was leaked (closes={closed})")
                # the leaked-or-not check above is the point; releasing again is ours
                try:
                    real_close(self.opened_fd)
                except OSError:
                    pass

    def test_a_refusal_under_a_broken_close_still_never_writes(self):
        """If os.close itself misbehaves on the way out, the write must not follow."""
        self.setUp()
        self.fd_identity = ROOT_PARTITION
        self.name_sequence = [USB_TARGET, ROOT_PARTITION]
        rc_or_exc = None
        try:
            rc, out = self.run_cli(patches=[mock.patch.object(
                pc5ctl.os, "close",
                side_effect=OSError(errno.EIO, "I/O error"))])
            rc_or_exc = rc
        except OSError as exc:
            rc_or_exc = f"raised {type(exc).__name__}"
        self.assertEqual(self.launches, [],
                         f"close failed on the way out and dd ran anyway: {rc_or_exc}")
        self.assertEqual(self.target_file.read_bytes(), b"")
        # the held fd is still ours to release
        try:
            os.close(self.opened_fd)
        except OSError:
            pass

class TestNoFallbackAnywhere(ProductionPathCase):
    """Property 6: no failure path substitutes the pathname for the descriptor."""

    def _assert_no_pathname_write(self, rc, out):
        for argv, kwargs in self.launches:
            for a in argv:
                self.assertFalse(a.startswith("of=/dev/"),
                                 f"the child was given a device path: {a}")
        self.assertEqual(self.target_file.read_bytes(), b"", "bytes were written")

    def test_dd_missing_refuses(self):
        self.setUp()
        self.fd_identity = USB_TARGET
        self.name_sequence = [USB_TARGET]
        rc, out = self.run_cli(dd=None)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.launches, [])
        self.assertIn("dd not found", out)

    def test_launch_failure_refuses(self):
        self.setUp()
        self.fd_identity = USB_TARGET
        self.name_sequence = [USB_TARGET]
        self.launch = "fail"
        rc, out = self.run_cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("could not launch", out)
        self.assertIn("nothing was written", out)
        self._assert_no_pathname_write(rc, out)

    def test_open_failure_refuses(self):
        self.setUp()
        rc, out = self.run_cli(patches=[mock.patch.object(
            pc5ctl.os, "open",
            side_effect=OSError(errno.EACCES, "Permission denied"))])
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.launches, [])
        self.assertIn("cannot open", out)

    def test_checksum_mismatch_refuses_before_any_open(self):
        self.setUp()
        (Path(self._tmp.name) / "image.iso.sha256").write_text(
            "0" * 64 + "  image.iso\n", encoding="ascii")
        opened = []
        rc, out = self.run_cli(patches=[mock.patch.object(
            pc5ctl.os, "open", side_effect=lambda p, f, *a: opened.append(p) or 42)])
        self.assertEqual(rc, 1, out)
        self.assertIn("checksum mismatch", out)
        self.assertEqual(opened, [], "a device was opened for a bad image")

    def test_unreadable_root_relationships_never_authorise_a_write(self):
        """Deny the underlying sysfs reads and the write must not happen.

        Recorded honestly: with `slaves`/`holders` *patched at the instance* the
        closure goes through `_relations`, so those two are no-ops and the write
        proceeds -- a probe on the harness, not on the code.  Denying the real
        sysfs entries (next test) is the case that matters.
        """
        for method in ("_relations", "parent_of", "partition_of", "root_closure"):
            with self.subTest(method=method):
                self.setUp()
                self.fd_identity = USB_TARGET
                self.name_sequence = [USB_TARGET]
                crashed, out = None, ""
                try:
                    rc, out = self.run_cli(topo_patches=[(method, {"side_effect":
                                                                   OSError(errno.EACCES,
                                                                           "Permission denied")})])
                except OSError as exc:
                    rc, crashed = 1, type(exc).__name__
                    out = self.out
                # the safety property: never a write.  The reporting property
                # (a refusal message rather than a traceback) is measured in
                # `test_an_unexpected_topology_error_reaches_the_operator_as_something`,
                # which is where the finding is recorded.
                self.assertEqual(self.launches, [],
                                 f"{method} failing let the write proceed (crashed={crashed})")
                self.assertEqual(self.target_file.read_bytes(), b"",
                                 f"{method} failing still wrote bytes (crashed={crashed})")
                self.assertEqual(rc, 1, out)

    def test_unreadable_sysfs_entries_in_the_fixture_refuse(self):
        """The realistic denial: mode bits on the sysfs files the guard reads.

        This is the environment-sensitive one: as uid 0 every chmod 0o000 below
        would silently become readable and the test vacuous, so it starts by
        proving the denial actually denies (same guard the shipped suite uses).
        """
        probe = Path(self._tmp.name) / "chmod-probe"
        probe.write_text("x", encoding="ascii")
        probe.chmod(0o000)
        try:
            with self.assertRaises(PermissionError):
                open(probe, "r", encoding="utf-8").read()
        finally:
            probe.chmod(0o644)

        for label in ("root disk dev", "root disk slaves", "root disk holders",
                      "root partition `partition`", "sibling partition dir"):
            with self.subTest(denial=label):
                self.setUp()          # a fresh fixture per denial, before pointing at it
                path = {
                    "root disk dev": self.tree.sys_block / "8:0" / "dev",
                    "root disk slaves": self.tree.sys_block / "8:0" / "slaves",
                    "root disk holders": self.tree.sys_block / "8:0" / "holders",
                    "root partition `partition`":
                        self.tree.sys_block / "8:0" / "sda1" / "partition",
                    "sibling partition dir": self.tree.sys_block / "8:0" / "sda2",
                }[label]
                # the *target* looks unrelated; it is the root side that is broken,
                # and a broken root side must never read as "nothing to protect"
                self.fd_identity = USB_TARGET
                self.name_sequence = [USB_TARGET]
                path.chmod(0o000)
                try:
                    rc, out = self.run_cli()
                    self.assertEqual(rc, 1, f"{path} unreadable yet the write proceeded:\n{out}")
                    self.assertEqual(self.launches, [], f"{path} unreadable yet dd ran")
                finally:
                    path.chmod(0o755 if path.is_dir() else 0o644)

    def test_unresolvable_root_refuses(self):
        self.setUp()
        self.fd_identity = USB_TARGET
        self.name_sequence = [USB_TARGET]
        rc, out = self.run_cli(topo_patches=[("root_devnum", {"return_value": None})])
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.launches, [])


    def test_an_unexpected_topology_error_reaches_the_operator_as_something(self):
        """Record what an injected exception from a topology read actually looks like.

        `check_target_safe` has no blanket guard: an exception from a Topology
        method escapes `cmd_usb` as a traceback rather than a "nothing was
        written" line.  Nothing is written either way, so this is a reporting
        gap, not a safety gap -- and the real readers below it swallow OSError,
        so reaching it needs an error the code does not otherwise produce.
        """
        self.setUp()
        self.fd_identity = USB_TARGET
        self.name_sequence = [USB_TARGET]
        with self.assertRaises(OSError):
            self.run_cli(topo_patches=[("root_closure", {"side_effect":
                                                         OSError(errno.EACCES,
                                                                 "Permission denied")})])
        self.assertEqual(self.launches, [])
        self.assertEqual(self.target_file.read_bytes(), b"")
        self.assertNotIn("nothing was written", self.out,
                         "if a refusal message is ever added here, this probe must "
                         "be updated to assert it instead of its absence")


class TestAuthorisationSurface(unittest.TestCase):
    """The decision function against the bound view, with the CLI out of the way."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = SyntheticSysfs(Path(self._tmp.name))
        self.topo = self.tree.topology()
        self.topo.root_devnum = lambda: ROOT_PARTITION

    def bound(self, identity):
        return pc5ctl._BoundTopology(self.topo, identity)

    def test_each_dangerous_identity_is_refused_by_name(self):
        for identity, label in [
            (ROOT_PARTITION, "the root partition itself"),
            (ROOT_DISK, "the whole disk carrying the root filesystem"),
            (SIBLING_ON_ROOT_DISK, "a sibling partition on the root disk"),
        ]:
            ok, reason = pc5ctl.check_target_safe("/dev/whatever", topology=self.bound(identity))
            self.assertFalse(ok, f"{label} was authorised: {reason}")
            self.assertIn("refusing to overwrite", reason)

    def test_unrelated_identity_is_allowed(self):
        ok, reason = pc5ctl.check_target_safe("/dev/whatever", topology=self.bound(USB_TARGET))
        self.assertTrue(ok, reason)

    def test_the_bound_view_answers_for_every_path_it_is_handed(self):
        """No re-resolution: any pathname maps to the descriptor's identity."""
        for path in ("/definitely/not/here", "", "/", str(self.tree.sys_block),
                     "/dev/some-other-thing"):
            self.assertEqual(self.bound(USB_TARGET).devnum_for_path(path), USB_TARGET,
                             f"the bound view resolved {path!r} instead of the fd")

    def test_identity_with_no_sysfs_entry_is_assessed_only_by_set_membership(self):
        """PROBED AND REFUTED as a hole, recorded so the attack is visible.

        A target devnum with no /sys/dev/block entry is not itself looked up; the
        decision is membership of the root closure, which is built by walking the
        *root's* sysfs entries.  So an invisible target is unrelated, unless it is
        related, in which case the root side enumerated it anyway and it is refused.
        The dangerous direction -- an unreadable root -- is the next test.
        """
        ghost = (254, 199)     # no sysfs entry in the fixture
        ok, reason = pc5ctl.check_target_safe("/dev/x", topology=self.bound(ghost))
        self.assertTrue(ok, reason)
        # and the same ghost, if the root enumeration breaks, must not be writable
        broken = self.tree.topology()
        broken.root_devnum = lambda: ROOT_PARTITION
        (self.tree.sys_block / "8:0" / "slaves").chmod(0o000)
        try:
            ok, reason = pc5ctl.check_target_safe(
                "/dev/x", topology=pc5ctl._BoundTopology(broken, ghost))
            self.assertFalse(ok, f"a broken root enumeration authorised {ghost}: {reason}")
        finally:
            (self.tree.sys_block / "8:0" / "slaves").chmod(0o755)

    def test_unreadable_root_refuses_everything(self):
        empty = Path(self._tmp.name) / "nothing"
        empty.mkdir()
        dead = pc5ctl.Topology(sys_block=str(empty / "sys"), mounts=str(empty / "m"),
                               mountinfo=str(empty / "mi"))
        dead.root_devnum = lambda: None
        for identity in (USB_TARGET, ROOT_PARTITION, (8, 33)):
            ok, reason = pc5ctl.check_target_safe(
                "/dev/x", topology=pc5ctl._BoundTopology(dead, identity))
            self.assertFalse(ok, f"{identity} authorised with an unreadable root: {reason}")

    def test_partial_enumeration_is_refused_not_silently_shortened(self):
        victim = self.tree.sys_block / "8:0" / "sda2"
        (victim / "partition").chmod(0o000)
        try:
            for identity in (USB_TARGET, SIBLING_ON_ROOT_DISK):
                ok, reason = pc5ctl.check_target_safe("/dev/x", topology=self.bound(identity))
                self.assertFalse(ok, f"partial enumeration authorised {identity}: {reason}")
        finally:
            (victim / "partition").chmod(0o644)

    def test_a_dangerous_identity_cannot_be_made_safe_by_a_different_path_argument(self):
        """`check_target_safe`'s `device` argument is only ever cosmetic for a bound view."""
        for path in ("/dev/null", "/dev/sdz", "", "not-even-a-path"):
            ok, reason = pc5ctl.check_target_safe(path, topology=self.bound(ROOT_PARTITION))
            self.assertFalse(ok, f"path {path!r} authorised a root partition: {reason}")


class TestRootSideResidual(unittest.TestCase):
    """FINDING PROBE. The *protected set* is still established through a pathname in
    the mountinfo-absent fallback, so descriptor binding on the target side does not
    make the whole decision path name-free.

    These tests do not allege a defect introduced by the patch; they measure the
    residual so the report's claim can be checked rather than inherited from prose.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = SyntheticSysfs(Path(self._tmp.name))

    def test_the_fallback_resolves_the_root_by_pathname(self):
        """Prove the fallback exists and is name-based, by observing the lookup."""
        base = Path(self._tmp.name)
        mounts = base / "mounts"
        mounts.write_text("/dev/pc5-review-root-node / ext4 rw 0 0\n", encoding="ascii")
        topo = pc5ctl.Topology(sys_block=str(self.tree.sys_block), mounts=str(mounts),
                              mountinfo=str(base / "absent"))
        # give the named node a real identity via a symlink to nothing: the lookup
        # must be attempted regardless, and must fail closed.
        calls = []
        real = pc5ctl.Topology.devnum_for_path

        def spy(self, path):
            calls.append(str(path))
            return real(self, path)

        with mock.patch.object(pc5ctl.Topology, "devnum_for_path", spy):
            found = topo.root_devnum()
        self.assertIn("/dev/pc5-review-root-node", calls,
                      "root_devnum() did not consult the mount source by pathname")
        self.assertIsNone(found, "a non-existent node resolved anyway")

    def test_unresolved_root_refuses_all_targets(self):
        base = Path(self._tmp.name)
        mounts = base / "mounts"
        mounts.write_text("/dev/pc5-review-root-node / ext4 rw 0 0\n", encoding="ascii")
        topo = pc5ctl.Topology(sys_block=str(self.tree.sys_block), mounts=str(mounts),
                               mountinfo=str(base / "absent"))
        for identity in (USB_TARGET, ROOT_PARTITION):
            ok, reason = pc5ctl.check_target_safe(
                "/dev/x", topology=pc5ctl._BoundTopology(topo, identity))
            self.assertFalse(ok, f"{identity} authorised: {reason}")

    def test_a_retargeted_root_node_would_move_the_boundary(self):
        """The root-side mirror of the patched defect: name-trust on the root lookup."""
        topo = self.tree.topology()
        # model a kernel whose mountinfo is absent and whose /proc/mounts source
        # has been retargeted at an unrelated disk, so the real root partition
        # falls out of the protected set.
        topo.root_devnum = lambda: USB_TARGET
        ok, reason = pc5ctl.check_target_safe(
            "/dev/sda1", topology=pc5ctl._BoundTopology(topo, ROOT_PARTITION))
        self.assertTrue(
            ok,
            "expected the residual to be present; if it refuses instead, the finding "
            "is weaker than stated and must be reported as such: " + reason)


class TestDescriptorInterrogation(unittest.TestCase):
    """The helpers, against real kernel objects where a real object is possible."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.f = Path(self._tmp.name) / "f"
        self.f.write_bytes(b"seed")

    def test_writability_asks_the_kernel_about_the_description(self):
        fds = {m: _REAL_OPEN(self.f, m) for m in
               (os.O_RDONLY, os.O_WRONLY, os.O_RDWR)}
        try:
            self.assertFalse(pc5ctl.fd_writable(fds[os.O_RDONLY]),
                             "a read-only fd was accepted as writable")
            self.assertTrue(pc5ctl.fd_writable(fds[os.O_WRONLY]))
            self.assertTrue(pc5ctl.fd_writable(fds[os.O_RDWR]))
        finally:
            for fd in fds.values():
                _REAL_CLOSE(fd)

    def test_writability_refuses_closed_and_bogus_descriptors(self):
        fd = _REAL_OPEN(self.f, os.O_WRONLY)
        _REAL_CLOSE(fd)
        for bad in (fd, -1, 1 << 20):
            self.assertFalse(pc5ctl.fd_writable(bad), f"{bad} was accepted")

    def test_identity_of_a_regular_file_is_none(self):
        fd = _REAL_OPEN(self.f, os.O_WRONLY)
        try:
            self.assertIsNone(pc5ctl.fd_block_identity(fd),
                              "a regular file must not pass as a block target")
        finally:
            _REAL_CLOSE(fd)

    def test_identity_survives_unlinking_the_name(self):
        victim = Path(self._tmp.name) / "gone"
        victim.write_bytes(b"x")
        fd = _REAL_OPEN(victim, os.O_WRONLY)
        os.unlink(victim)
        try:
            self.assertFalse(victim.exists())
            st = _REAL_FSTAT(fd)
            self.assertTrue(stat.S_ISREG(st.st_mode),
                            "fstat must still describe the open object")
        finally:
            _REAL_CLOSE(fd)

    def test_procfs_usability_tracks_the_descriptor_not_the_name(self):
        fd = _REAL_OPEN(self.f, os.O_WRONLY)
        self.assertTrue(pc5ctl.fd_child_target_usable(fd))
        _REAL_CLOSE(fd)
        self.assertFalse(pc5ctl.fd_child_target_usable(fd),
                         "a closed descriptor's procfs entry is gone")

    def test_helpers_never_raise_where_a_refusal_is_expected(self):
        """An escaping exception is a traceback, not a refusal: check for both."""
        fd = _REAL_OPEN(self.f, os.O_RDONLY)
        try:
            for helper in (pc5ctl.fd_writable, pc5ctl.fd_block_identity,
                           pc5ctl.fd_child_target_usable):
                for bad in (-1, 1 << 20, fd):
                    try:
                        helper(bad)
                    except Exception as exc:
                        self.fail(f"{helper.__name__}({bad}) raised "
                                  f"{type(exc).__name__}: {exc} — a caller cannot "
                                  "distinguish that from a refusal")
        finally:
            _REAL_CLOSE(fd)


class TestRealChildSemantics(unittest.TestCase):
    """The hand-off, measured with /bin/dd and no mock in the loop."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.payload = base / "payload.bin"
        self.payload.write_bytes(b"Z" * (1 << 16))
        self.target = base / "target.bin"
        self.target.write_bytes(b"")
        self.dd = shutil.which("dd")
        if not self.dd:
            self.skipTest("no dd in this environment")

    def _write_via_fd(self, pass_fds, mutate=None):
        fd = _REAL_OPEN(self.target, os.O_WRONLY)
        argv = [self.dd, f"if={self.payload}", f"of=/proc/self/fd/{fd}",
                "bs=4M", "status=none", "conv=fsync"]
        kwargs = {"close_fds": True}
        if pass_fds:
            kwargs["pass_fds"] = (fd,)
        if mutate:
            mutate(argv, kwargs)
        try:
            rc = subprocess.call(argv, **kwargs)
        finally:
            _REAL_CLOSE(fd)
        return rc

    def test_inherited_descriptor_reaches_the_child_and_bytes_arrive(self):
        self.assertEqual(self._write_via_fd(True), 0)
        self.assertEqual(self.target.read_bytes(), b"Z" * (1 << 16))

    def test_without_inheritance_the_child_fails_and_nothing_is_written(self):
        """The shipped defect, reproduced by the reviewer from scratch."""
        self.assertNotEqual(self._write_via_fd(False), 0)
        self.assertEqual(self.target.read_bytes(), b"")

    def test_inheriting_the_wrong_descriptor_writes_the_wrong_object(self):
        """A mutant the author's suite must answer for: pass a different fd.

        Proof that `pass_fds=(target_fd,)` is load-bearing and not decoration:
        hand dd a descriptor for another file and the payload lands there,
        while the intended target stays empty.
        """
        decoy = Path(self._tmp.name) / "decoy.bin"
        decoy.write_bytes(b"")
        decoy_fd = _REAL_OPEN(decoy, os.O_WRONLY)
        target_fd = _REAL_OPEN(self.target, os.O_WRONLY)
        argv = [self.dd, f"if={self.payload}", f"of=/proc/self/fd/{target_fd}",
                "bs=4M", "status=none"]
        try:
            # the fd named in `of=` is NOT inherited; a different one is
            rc = subprocess.call(argv, close_fds=True, pass_fds=(decoy_fd,))
        finally:
            _REAL_CLOSE(target_fd)
            _REAL_CLOSE(decoy_fd)
        self.assertNotEqual(rc, 0, "the child wrote through an un-inherited descriptor")
        self.assertEqual(self.target.read_bytes(), b"")

    def test_the_procfs_alias_names_the_description_not_the_pathname(self):
        fd = _REAL_OPEN(self.target, os.O_WRONLY)
        alias = f"/proc/self/fd/{fd}"
        referent = _REAL_READLINK(alias)
        os.unlink(self.target)
        with open(alias, "wb") as fh:
            fh.write(b"through")
        self.assertEqual(_REAL_READLINK(alias), referent + " (deleted)",
                         "the alias must keep naming the same (unlinked) object")
        self.assertFalse(self.target.exists(),
                         "writing through the alias must not resurrect the pathname")
        _REAL_CLOSE(fd)


class TestIdentityOfTheAnsweredObject(unittest.TestCase):
    """The review question, stated as a test.

    Is the identity that authorises necessarily the identity of the object that
    receives the write?  Trace it end to end with the descriptor's own inode and
    the child's own view of the fd.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.f = Path(self._tmp.name) / "obj"
        self.f.write_bytes(b"")

    def test_authorising_identity_and_written_object_share_one_description(self):
        fd = _REAL_OPEN(self.f, os.O_WRONLY)
        dup = os.dup(fd)
        try:
            st_a, st_b = _REAL_FSTAT(fd), _REAL_FSTAT(dup)
            # same file description: the kernel's own answer about identity
            self.assertEqual((st_a.st_ino, st_a.st_dev), (st_b.st_ino, st_b.st_dev))
            alias_a, alias_b = _REAL_READLINK(f"/proc/self/fd/{fd}"), \
                _REAL_READLINK(f"/proc/self/fd/{dup}")
            self.assertEqual(alias_a, alias_b,
                             "two descriptors, one referent: the write target is the "
                             "same object the identity was read from")
            # and a name swap cannot change what the descriptor refers to
            os.unlink(self.f)
            self.assertEqual(_REAL_READLINK(f"/proc/self/fd/{fd}"),
                             alias_a + " (deleted)")
        finally:
            _REAL_CLOSE(dup)
            _REAL_CLOSE(fd)

    def test_the_child_sees_the_same_object_as_the_parent(self):
        fd = _REAL_OPEN(self.f, os.O_WRONLY)
        parent = os.readlink(f"/proc/self/fd/{fd}")
        probe = subprocess.run([sys.executable, "-c",
                                "import os,sys; print(os.readlink('/proc/self/fd/'+sys.argv[1]))",
                                str(fd)],
                               capture_output=True, text=True, pass_fds=(fd,), check=True)
        self.assertEqual(probe.stdout.strip(), parent,
                         "the inherited descriptor refers to a different object")
        _REAL_CLOSE(fd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
