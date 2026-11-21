"""USB identity boundary: the object authorised is the object written.

Status — synthetic on purpose.  No real block device is created, opened or
written; no destructive call is ever made.  What *is* real in every test here:

  * the production entry point (``pc5ctl.main(["usb", ...])``), so argument
    parsing, the checksum step, the guard order and the launch are the shipped
    ones;
  * real descriptor semantics on an ordinary temporary file: ``os.open``/
    ``os.dup``, ``fcntl(F_GETFL)``, ``fstat``, ``/proc/self/fd/N``;
  * the real sysfs-shaped topology reader (the repository's own ``FakeTree``);
  * in ``RealChildWrites``, the real ``/bin/dd`` as the write child.

The only substitution is the *identity* seam: this sandbox cannot ``mknod``, so
``fstat`` is made to report ``S_IFBLK`` with a chosen ``major:minor`` for a
descriptor that really exists.  Every refusal case therefore still asserts that
the destructive call never happened, which is the property that matters.

Requirement map (continuation brief, "Required race and descriptor tests")
=========================================================================
 1. missing descriptor inheritance ................. test_03, test_25 (real child)
 2. descriptor closed before the launch ............ test_02
 3. wrong descriptor passed to the child ........... test_16
 4. descriptor identity mismatch ................... test_06
 5. pathname replaced after opening ................ test_07
 6. symlink replaced after opening ................. test_08   <- critical
 7. pathname replaced between the identity
    comparison and the final topology validation .. test_09   <- critical
 8. root-device descriptor, name substituted to an
    unrelated device ............................. test_10   <- critical
 9. topology change immediately before the final
    safety decision .............................. test_11
10. ``fstat`` failure .............................. test_12
11. child startup failure .......................... test_13
12. final safety refusal ........................... test_14
13. successful non-destructive control ............. test_15, test_24 (real child)

and the "a descriptor-backed path is not automatically safe" controls: a
non-writable open file description (test_17), an absent procfs entry (test_18),
an unlinked referent (test_19), plus the ``fd < 3`` inheritance guard (test_16b),
unreadable sysfs relationships (test_20, test_21), incomplete enumeration
(test_22) and the retained ``os.open`` refusal branch (test_23).

Case 7 (`test_09`) is the one that answers the review question directly: it is
the shipped code's own timeline, and running this file against the unmodified
`tools/pc5ctl.py` from `HEAD` makes it fail because the write is launched.
`DEFECT-REPRO-pristine.log` shows the same construction producing
`dd invocations: 1` on the shipped build.  Cases 6 and 8 pin the neighbouring
orderings (name swapped before the comparison; name never looks dangerous at
all): the shipped build refuses those two only through its incidental name-versus-
descriptor comparison, which is why the bound decision, not that comparison, is
what the fix is about -- `m5` in the mutation harness removes the incidental
check and both cases fail, so neither guarantee can be quietly dropped.
"""

from __future__ import annotations

import errno
import hashlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import pc5ctl  # noqa: E402
from test_pc5ctl_usb_safety import FakeTree, _topology  # noqa: E402

# Captured before any test patches the `os` module, so the harness can still do
# real I/O while production sees the patched one.
_REAL_OPEN = os.open
_REAL_CLOSE = os.close
_REAL_STAT = os.lstat
_REAL_READLINK = os.readlink
_REAL_CALL = subprocess.call
_REAL_EXISTS = os.path.exists

ROOT_DISK = (8, 0)           # the whole disk carrying '/'
ROOT_PARTITION = (8, 1)      # a partition of that same disk: never writable
USB_TARGET = (8, 32)         # the removable stick the operator named
FOREIGN = (8, 48)            # an unrelated device: legitimately safe to write


def block(devnum):
    """What `os.fstat` answers for a real open descriptor on a block device."""
    return SimpleNamespace(st_mode=stat.S_IFBLK | 0o660,
                           st_rdev=os.makedev(*devnum))


def regular_file():
    return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_rdev=0)


class DescriptorBoundaryCase(unittest.TestCase):
    """Drives `pc5ctl usb`: real CLI, real descriptor calls, synthetic topology."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = FakeTree(self.tmp.name)
        self.tree.device(*ROOT_DISK, name="sda")
        self.tree.partition(*ROOT_PARTITION, "sda", "sda1")
        self.tree.device(*USB_TARGET, name="sdc")

        # The object the held descriptor refers to.  An ordinary file, so dup /
        # fcntl / fstat / /proc/self/fd semantics are the kernel's real ones
        # while nothing resembling a disk is involved.
        self.backing = Path(self.tmp.name) / "device-contents.img"
        self.backing.write_bytes(b"")
        self.backing_flags = os.O_WRONLY
        self.backing_fd = _REAL_OPEN(self.backing, self.backing_flags)
        self.addCleanup(self._drop_backing)

        self.iso = Path(self.tmp.name) / "image.iso"
        self.iso.write_bytes(b"synthetic image contents")
        digest = hashlib.sha256(self.iso.read_bytes()).hexdigest()
        (Path(self.tmp.name) / "image.iso.sha256").write_text(
            f"{digest}  image.iso\n", encoding="ascii")
        self.device = "/dev/pc5-synthetic-never-opened"

        self.topo = _topology(sys_block=str(self.tree.base),
                              mounts="/nonexistent", mountinfo="/nonexistent")
        self.root = ROOT_DISK
        self.topo.root_devnum = lambda: self.root
        # What the pathname resolves to, per lookup.  A sequence so a test can
        # model the name changing while the descriptor is held; the last entry
        # repeats deliberately, so a build that resolves the name a different
        # number of times fails on behaviour, not on StopIteration.
        self.path_sequence = [USB_TARGET]
        self.lookups = 0
        self.topo.devnum_for_path = self._resolve_path

        self.fstat_sequence = [block(USB_TARGET)]
        self.fstat_calls = 0
        self.after_open = None
        self.open_override = None
        self.open_error = None
        self.opened_fds = []
        self.dd_calls = []
        self.dd_error = None
        self.dd_real = False
        self.close_calls = []
        self.procfs = True
        self.referent = None
        self.stdout = io.StringIO()
        self.rc = None
        self.out = ""

    # -- the seams production sees ----------------------------------------
    def _drop_backing(self):
        try:
            _REAL_CLOSE(self.backing_fd)
        except OSError:
            pass

    def _resolve_path(self, path):
        index = self.lookups
        self.lookups += 1
        if path != self.device:
            return self.path_sequence[-1]
        return self.path_sequence[min(index, len(self.path_sequence) - 1)]

    def _fake_fstat(self, fd):
        index = self.fstat_calls
        self.fstat_calls += 1
        found = self.fstat_sequence[min(index, len(self.fstat_sequence) - 1)]
        if isinstance(found, OSError):
            raise found
        return found

    def _open_target(self, path, flags, *args):
        if self.open_error is not None:
            raise self.open_error
        if self.open_override is not None:
            self.opened_fds.append(self.open_override)
            return self.open_override
        fd = os.dup(self.backing_fd)
        self.opened_fds.append(fd)
        if self.after_open is not None:
            self.after_open()
        return fd

    @staticmethod
    def _is_proc_fd(path):
        return str(path).startswith("/proc/self/fd/") or str(path).startswith("/proc/self/fd/")

    def _fake_lstat(self, path):
        # Scoped deliberately: `os.path.islink` and `os.path.realpath` also go
        # through lstat/readlink, and a blanket fake would break the fixture
        # topology rather than the descriptor check under test.
        if not self.procfs and self._is_proc_fd(path):
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", path)
        return _REAL_STAT(path)

    def _fake_readlink(self, path):
        if self.referent and self._is_proc_fd(path):
            return self.referent
        return _REAL_READLINK(path)

    def _launch(self, argv, **kwargs):
        """The write child.  Real `/bin/dd` in `RealChildWrites`, recorded here."""
        if self.dd_error is not None:
            raise self.dd_error
        if self.dd_real:
            return _REAL_CALL(argv, **kwargs)
        self.dd_calls.append((list(argv), kwargs))
        return 0

    def _launch_without_inheritance(self, argv, **kwargs):
        """The mutant shape: same argv, but the descriptor is not passed down."""
        return _REAL_CALL(argv, close_fds=True)

    def _record_close(self, fd):
        self.close_calls.append(fd)
        try:
            _REAL_CLOSE(fd)
        except OSError:
            pass

    def _seams(self, launch=None, which="/bin/dd"):
        """Every substitution production sees, in one place.

        `which` is a parameter rather than a patch applied outside this list,
        because a patch applied outside is overwritten by the one applied inside
        and the test would silently stop testing anything.
        """
        return [
            mock.patch.object(pc5ctl, "Topology", return_value=self.topo),
            mock.patch.object(pc5ctl.os.path, "exists",
                              side_effect=lambda p: True if p == self.device
                              else _REAL_EXISTS(p)),
            mock.patch.object(pc5ctl.os, "open", side_effect=self._open_target),
            mock.patch.object(pc5ctl.os, "fstat", side_effect=self._fake_fstat),
            mock.patch.object(pc5ctl.os, "close", side_effect=self._record_close),
            mock.patch.object(pc5ctl.os, "lstat", side_effect=self._fake_lstat),
            mock.patch.object(pc5ctl.os, "readlink", side_effect=self._fake_readlink),
            mock.patch.object(pc5ctl.shutil, "which", return_value=which),
            mock.patch.object(pc5ctl.subprocess, "call",
                              side_effect=launch or self._launch),
        ]

    def run_argv(self, argv, launch=None, which="/bin/dd"):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for seam in self._seams(launch, which=which):
                stack.enter_context(seam)
            with redirect_stdout(self.stdout):
                return pc5ctl.main(list(argv))

    def run_cli(self, launch=None, which="/bin/dd"):
        return self.run_argv(["usb", "--iso", str(self.iso),
                              "--device", self.device, "--yes"],
                             launch=launch, which=which)

    def cli(self, launch=None, which="/bin/dd"):
        self.rc = self.run_cli(launch=launch, which=which)
        self.out = self.stdout.getvalue()
        return self.rc, self.out

    # -- assertions --------------------------------------------------------
    def assert_refused(self, fragment=None):
        """Every unsafe case must refuse *and* not touch the object.

        Three independent facts, because a refusal that still wrote would be
        worse than no refusal at all: the exit status, the absence of any
        destructive launch, and the byte-for-byte emptiness of the object behind
        the descriptor.
        """
        self.assertEqual(self.rc, 1, f"expected a refusal, got exit {self.rc}: {self.out}")
        self.assertEqual([a for a, _ in self.dd_calls], [],
                         "the destructive write child must never be launched")
        self.assertIn("nothing was written", self.out)
        if fragment is not None:
            self.assertIn(fragment, self.out)
        self.assertEqual(self.backing.read_bytes(), b"",
                         "a refusal must not have written through the descriptor")

    def held_fd(self):
        self.assertEqual(len(self.opened_fds), 1,
                         "the target must be opened exactly once")
        return self.opened_fds[0]

    def dd_argv(self):
        return self.dd_calls[0][0]

    def dd_kwargs(self):
        return self.dd_calls[0][1]

    def of_arg(self):
        found = [a for a in self.dd_argv() if a.startswith("of=")]
        self.assertEqual(len(found), 1, f"exactly one output target expected: {self.dd_argv()}")
        return found[0]


class UsbIdentityBoundaryTests(DescriptorBoundaryCase):
    def test_01_valid_descriptor_and_matching_identity_writes_through_the_fd(self):
        rc, out = self.cli()
        self.assertEqual(rc, 0, out)
        fd = self.held_fd()
        self.assertEqual(len(self.dd_calls), 1)
        self.assertEqual(self.of_arg(), f"of=/proc/self/fd/{fd}")
        self.assertEqual(self.dd_kwargs().get("pass_fds"), (fd,),
                         "the validated descriptor must be the inherited one")
        self.assertIs(self.dd_kwargs().get("close_fds"), True)
        self.assertEqual(self.close_calls, [fd],
                         "the descriptor must be closed exactly once, after the child")

    def test_02_descriptor_closed_before_the_child_launches(self):
        # A descriptor number that stops referring to anything must not reach the
        # child: whatever the number is, the object is gone, and the only correct
        # behaviour is a refusal.  The first kernel interrogation (`fcntl` on the
        # fd) is what notices, so that is the message asserted -- the property
        # under test is "no launch, no bytes", not which of the two checks trips.
        def close_it():
            _REAL_CLOSE(self.opened_fds[-1])
        self.after_open = close_it
        self.cli()
        self.assert_refused("cannot hold a writable descriptor")

    def test_03_missing_descriptor_inheritance_is_explicit(self):
        # A launch that omits `pass_fds` cannot work: the child has no such
        # descriptor.  The production call must state the inheritance.
        rc, out = self.cli()
        self.assertEqual(rc, 0, out)
        self.assertIn("pass_fds", self.dd_kwargs(),
                      "the write child must be given the descriptor explicitly")
        self.assertEqual(self.dd_kwargs()["pass_fds"], (self.held_fd(),))

    def test_04_invalid_descriptor_number_is_refused_without_closing_a_standard_stream(self):
        self.open_override = 1        # a stdio descriptor, not inheritable
        self.fstat_sequence = [block(USB_TARGET)]
        self.cli()
        self.assert_refused("cannot inherit the target descriptor")
        self.assertEqual(self.close_calls, [],
                         "refusing must not close a descriptor the process still needs")

    def test_05_descriptor_redirected_to_a_different_object(self):
        # `os.dup` of a different file: the fd is real, writable and usable, but
        # it is not the device that was authorised.
        other = Path(self.tmp.name) / "other.img"
        other.write_bytes(b"")
        other_fd = _REAL_OPEN(other, os.O_WRONLY)
        self.addCleanup(_REAL_CLOSE, other_fd)
        self.fstat_sequence = [block(FOREIGN)]   # the kernel says: another device
        self.cli()
        # The bound decision is about FOREIGN, which is unrelated to the root: so
        # the *name* comparison is what refuses here, and it must.
        self.assert_refused("changed identity between validation and write")

    def test_06_descriptor_identity_mismatch_against_the_named_target(self):
        self.fstat_sequence = [block(ROOT_PARTITION)]
        self.cli()
        self.assert_refused("changed identity between validation and write")

    def test_07_pathname_replaced_after_opening(self):
        # A real device-node swap: the name is replaced by a node for the root
        # partition while the descriptor is held.
        def swap():
            self.path_sequence = [USB_TARGET, ROOT_PARTITION, ROOT_PARTITION]
        self.after_open = swap
        self.fstat_sequence = [block(ROOT_PARTITION)]
        self.cli()
        # The name and the descriptor agreed at the moment of comparison, so the
        # only thing left to decide on is the descriptor -- and it names a
        # partition of the root disk.
        self.assert_refused("is a partition of the disk carrying '/'")

    def test_08_symlink_substitution_after_opening_cannot_authorize_the_write(self):
        """THE CRITICAL CASE (pathname-based validation, variant 1).

        The fd is on the root partition and every later lookup of the name is
        attacker-controlled.  Deciding about `device` again -- as the shipped
        implementation did -- authorises the write; deciding about the descriptor
        identity cannot.  Nothing about this fixture makes the danger symbolic:
        `dd` is recorded, and it must not be recorded even once.
        """
        self.fstat_sequence = [block(ROOT_PARTITION)]
        self.path_sequence = [USB_TARGET, USB_TARGET, USB_TARGET]
        self.cli()
        self.assertEqual(self.dd_calls, [],
                         "a pathname swap must not authorise writing the root partition")
        self.assertEqual(self.rc, 1)
        self.assertIn("nothing was written", self.out)
        self.assertEqual(self.backing.read_bytes(), b"")

    def test_09_path_replaced_between_the_identity_check_and_final_validation(self):
        """THE CRITICAL CASE (the exact shipped-code timeline).

        lookup 1 (pre-open decision) -> the safe stick
        open()                        -> the root partition   [swap in]
        lookup 2 (name vs fd)        -> the root partition    => agrees, passes
        lookup 3 (final decision)    -> the safe stick       [swap back]
        dd                            -> writes the root partition

        With the shipped pathname-based final check this launches the write (see
        `evidence/usb-descriptor-remediation-v1/DEFECT-REPRO-pristine.log`); with
        the decision bound to the descriptor it must refuse, and the refusal must
        describe the device that was actually held.
        """
        self.fstat_sequence = [block(ROOT_PARTITION)]
        self.path_sequence = [USB_TARGET, ROOT_PARTITION, USB_TARGET]
        self.cli()
        self.assert_refused()
        self.assertIn("is a partition of the disk carrying '/'", self.out,
                      "the refusal must name the held device, not the pathname")
        self.assertEqual(self.backing.read_bytes(), b"")

    def test_10_root_descriptor_with_a_substituted_safe_name_still_refuses(self):
        """The name never looks dangerous at all; the object always is.

        Same construction as test_09 without the interleaving: three safe
        answers, a descriptor on a root partition.  The shipped code refused this
        one only by accident (its name-vs-fd comparison); the bound decision
        refuses it because the identity it examined is the identity it writes.
        """
        self.fstat_sequence = [block(ROOT_PARTITION)]
        self.path_sequence = [USB_TARGET]
        self.cli()
        self.assert_refused()
        self.assertIn("8:1", self.out,
                      "the refusal must report the descriptor's identity")

    def test_11_topology_change_immediately_before_the_final_safety_decision(self):
        # Nothing about the device moves; the *root* does.  A device that becomes
        # part of the root storage after the first decision must be caught by the
        # second one, which is why it is recomputed and not inherited.
        def grow():
            self.tree.holders(ROOT_DISK, USB_TARGET)   # the stick is now a holder
        self.after_open = grow
        self.cli()
        self.assert_refused()
        self.assertIn("part of the storage stack under '/'", self.out)

    def test_12_failed_descriptor_read(self):
        self.fstat_sequence = [OSError(errno.EIO, "I/O error")]
        self.cli()
        self.assert_refused("cannot identify the descriptor")
        self.assertEqual(self.close_calls, [self.held_fd()])

    def test_13_child_startup_failure_never_falls_back_to_the_pathname(self):
        self.dd_error = OSError(errno.ENOENT, "No such file or directory")
        self.cli()
        self.assertEqual(self.rc, 1, self.out)
        self.assertIn("could not launch", self.out)
        self.assertIn("nothing was written", self.out)
        self.assertEqual(self.dd_calls, [],
                         "a failed launch must not be retried against the pathname")
        self.assertEqual(self.backing.read_bytes(), b"")
        self.assertEqual(self.close_calls, [self.held_fd()])

    def test_14_final_validation_failure_immediately_before_writing(self):
        # Between the decision and the launch the descriptor stops naming a block
        # device: fail closed instead of trusting the earlier answer.
        self.fstat_sequence = [block(USB_TARGET), regular_file()]
        self.cli()
        self.assert_refused("changed before the write")

    def test_15_valid_non_destructive_control_with_a_populated_root(self):
        # A guard that refuses everything proves nothing: with a partition, a dm
        # holder and its slaves under the root disk, an unrelated stick must
        # still reach the write step -- through the descriptor.
        dm = self.tree.device(253, 0, name="dm-0")
        self.tree.holders(ROOT_DISK, dm)
        self.tree.slaves(dm, ROOT_PARTITION)
        rc, out = self.cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.dd_calls), 1)
        self.assertEqual([a for a in self.dd_argv() if a.startswith("if=")],
                         [f"if={self.iso}"])
        self.assertIn("conv=fsync", self.dd_argv())
        self.assertEqual(self.of_arg(), f"of=/proc/self/fd/{self.held_fd()}")
        self.assertEqual(self.backing.read_bytes(), b"",
                         "the recorded child must not have written anything")

    def test_16_child_receives_exactly_the_validated_descriptor(self):
        self.cli()
        fd = self.held_fd()
        self.assertEqual(len(self.dd_calls), 1, self.out)
        self.assertEqual(self.dd_kwargs().get("pass_fds"), (fd,),
                         "the inherited descriptor must be the one that was validated")
        self.assertEqual(self.of_arg(), f"of=/proc/self/fd/{fd}",
                         "the child must not be handed a different descriptor number")
        self.assertNotIn(f"of={self.device}", self.dd_argv(),
                         "the pathname must never be the output target")

    def test_16b_inherited_descriptor_number_is_the_closed_one(self):
        # `pass_fds` must name the fd that is actually open in us.  A build that
        # passes `target_fd + 1` gives the child a different object, or none.
        self.cli()
        fd = self.held_fd()
        passed = self.dd_kwargs().get("pass_fds")
        self.assertEqual(passed, (fd,))
        self.assertNotEqual(passed, (fd + 1,))
        self.assertNotIn(fd + 1, (0, 1, 2),
                         "a stdio descriptor must never be the inherited one")

    def test_17_descriptor_not_open_for_write_is_refused(self):
        # Real fcntl F_GETFL semantics on a genuinely read-only open file
        # description -- not a simulated denial.
        _REAL_CLOSE(self.backing_fd)
        self.backing_fd = _REAL_OPEN(self.backing, os.O_RDONLY)
        self.cli()
        self.assert_refused("cannot hold a writable descriptor")

    def test_18_missing_procfs_entry_cannot_fall_back_to_the_pathname(self):
        self.procfs = False
        self.cli()
        self.assert_refused("is not usable as a write target")
        self.assertNotIn(f"of={self.device}", [a for a, _ in self.dd_calls],
                         "there is no pathname fallback")

    def test_19_unlinked_referent_is_refused(self):
        self.referent = "/dev/sda (deleted)"
        self.cli()
        self.assert_refused("is not usable as a write target")

    def test_20_unreadable_sysfs_relationship_data(self):
        # An unreadable `slaves` directory is not an empty one: the protected set
        # is unknown, and unknown must refuse even though the target looks safe.
        target = Path(self.tree.base) / "devices" / "sda" / "slaves"
        os.chmod(target, 0o000)
        self.addCleanup(os.chmod, target, 0o755)
        self.cli()
        self.assert_refused()
        self.assertIn("cannot establish the full storage topology", self.out)
        with self.assertRaises(PermissionError):
            os.listdir(target)

    def test_21_unreadable_holders_directory(self):
        # Same property on the other relationship direction, which is the one a
        # dm/md stack exercises.
        target = Path(self.tree.base) / "devices" / "sda" / "holders"
        os.chmod(target, 0o000)
        self.addCleanup(os.chmod, target, 0o755)
        self.cli()
        self.assert_refused()
        self.assertIn("cannot establish the full storage topology", self.out)
        with self.assertRaises(PermissionError):
            os.listdir(target)

    def test_22_partial_topology_followed_by_an_enumeration_error(self):
        """A listing that stops describing the device must not become an answer.

        The target is the unrelated stick by *every* route, so nothing else in
        the command can refuse: the only thing that can stop the write is the
        partition enumeration of the root disk.  The first decision enumerates it
        correctly.  The listing is then frozen -- what a stale sysfs read looks
        like -- and the partition it names is moved away, so the second
        enumeration (the one immediately before the write) reads a child that is
        no longer there.  Skipping that child yields a set that is a subset of
        what could not be read, and at the call site it is indistinguishable from
        a complete one: the stick would then be written because a sibling of the
        root partition was invisible.  Refusal is the only correct answer.
        """
        link = Path(self.tree.base) / "devices" / "sda"
        disk_link = str(Path(self.tree.base) / f"{ROOT_DISK[0]}:{ROOT_DISK[1]}")
        real_listdir = os.listdir
        frozen = sorted(real_listdir(link))
        calls = []

        def stale_after_first(path):
            if str(path) == disk_link:
                calls.append(path)
                if len(calls) > 1:
                    return frozen       # the second enumeration is out of date
            return real_listdir(path)

        self.addCleanup(setattr, pc5ctl.os, "listdir", real_listdir)
        pc5ctl.os.listdir = stale_after_first

        child = link / "sda1"
        hidden = link / "sda1.hidden"
        os.rename(child, hidden)        # vanished mid-enumeration
        self.addCleanup(os.rename, hidden, child)

        # Name and descriptor agree on the safe stick: the enumeration is the
        # only remaining reason to refuse.
        self.fstat_sequence = [block(USB_TARGET)]
        self.path_sequence = [USB_TARGET]
        self.cli()
        self.assert_refused("cannot establish the full storage topology")
        self.assertGreaterEqual(len(calls), 2,
                                "the scenario must reach the second enumeration")

    def test_23_target_open_failure_refuses_before_any_descriptor_exists(self):
        """A refused ``os.open`` on the target must not become anything else.

        This branch predates the descriptor binding, so it passes either way --
        which is exactly why it is asserted permanently: a rewrite of the write
        path can drop the try/except and turn a legitimate EACCES into a
        traceback, or paper over it and fall back to naming the device to the
        write child.  Both are refusals this test would catch.
        """
        self.open_error = PermissionError(errno.EACCES, "Permission denied", self.device)
        self.cli()
        self.assert_refused("cannot open")
        self.assertEqual(self.opened_fds, [], "a failed open must not record a descriptor")
        self.assertEqual(self.close_calls, [],
                         "a failed open must not close anything, least of all a std stream")

    def test_24_unknown_topology_never_becomes_a_write_even_with_yes(self):
        # `--yes` is a confirmation of the operator's intent, never an override of
        # a safety unknown.  The CLI is invoked with --yes in every test here, so
        # this pins the property in the case where it is the only thing left.
        os.chmod(Path(self.tree.base) / "devices" / "sda" / "holders", 0o000)
        self.addCleanup(os.chmod, Path(self.tree.base) / "devices" / "sda" / "holders", 0o755)
        rc, out = self.cli()
        self.assertEqual(rc, 1, "an unknown topology must refuse even with --yes")
        self.assertEqual(self.dd_calls, [])


class RealChildWrites(DescriptorBoundaryCase):
    """The write child is the real `/bin/dd`, and the object is a real file.

    These two cases exist because argument construction can be asserted forever
    without proving that a child can act on it.  `subprocess.call` is *not*
    patched: the child runs, opens `/proc/self/fd/N`, and either writes bytes
    into the ordinary temporary file standing in for the device or fails.  Both
    outcomes are checked by reading the file afterwards, not by parsing output.
    """

    def _digest(self):
        return hashlib.sha256(self.iso.read_bytes()).hexdigest()

    def test_25_real_dd_writes_through_the_inherited_descriptor(self):
        self.dd_real = True
        saved = os.dup(2)
        noise = tempfile.TemporaryFile(mode="w+b")
        os.dup2(noise.fileno(), 2)
        try:
            rc = self.run_cli()
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            noise.close()
        self.assertEqual(rc, 0, self.stdout.getvalue())
        data = self.backing.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), self._digest(),
                         "the real child must have written the image through the fd")
        self.assertEqual(self.backing.read_bytes(), b"synthetic image contents")

    def test_26_real_dd_cannot_write_when_inheritance_is_dropped(self):
        """The contract `pass_fds` carries, measured rather than argued.

        The launch is deliberately downgraded to the shipped form (no inherited
        descriptor) inside the test, which is what the corresponding mutation
        does to production code; the real child must then fail and the object
        must be untouched.  If a future change ever stops passing the fd, this
        fails on the *product*, and the mutation harness shows the same
        assertion is what catches it.
        """
        self.dd_real = True
        saved = os.dup(2)
        noise = tempfile.TemporaryFile(mode="w+b")
        os.dup2(noise.fileno(), 2)
        try:
            rc = self.run_cli(launch=self._launch_without_inheritance)
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            noise.close()
        self.assertNotEqual(rc, 0,
                            "a child without the descriptor must fail, not write")
        self.assertEqual(self.backing.read_bytes(), b"",
                         "nothing may reach the object when the fd is not inherited")


if __name__ == "__main__":
    unittest.main()
