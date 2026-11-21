"""Adversarial tests for the `pc5ctl usb` storage guard.

The guard exists for one failure: writing the image to the disk that carries
the running root filesystem, or to anything underneath it.  It used to compare
path strings, which misses `/dev/root` (the name `/proc/mounts` reports for the
root source on many systems), so `--yes` could reach `dd` with the system disk.

These tests drive `check_target_safe` — the same function `cmd_usb` calls —
against a fixture tree of sysfs-like device metadata.  No real disk is touched
and no write is performed; the fixtures are ordinary directories and files.

The two things that must both hold:
  * every root-related topology is refused;
  * an unrelated removable device is *not* refused, because a guard that
    refuses everything is not a guard.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import pc5ctl  # noqa: E402


class FakeTree:
    """Builds a sysfs-shaped fixture tree on disk.

    Mirrors the kernel's layout:
        <sys>/<maj>:<min>            symlink -> devices/<dir>
        <sys>/devices/<dir>/dev      "maj:min"
        <sys>/devices/<dir>/partition  present only for a partition
        <sys>/devices/<dir>/holders/<n>/dev
        <sys>/devices/<dir>/slaves/<n>/dev

    A partition is created *inside* its disk's directory (`devices/sda/sda1`),
    which is how the kernel nests them and how `parent_of` finds the disk: it
    reads the partition's sysdir and looks at the containing directory's dev.
    """

    def __init__(self, base):
        self.base = Path(base)
        (self.base / "devices").mkdir(parents=True, exist_ok=True)
        self._unreadable = []

    def make_unreadable(self, path):
        """Deny reads on a fixture path, so the production reader sees EACCES.

        The test user is not root, so chmod 000 genuinely blocks the read;
        `unreadable_fixture_actually_denies_reads` asserts that rather than
        assuming it.
        """
        os.chmod(path, 0o000)
        self._unreadable.append(path)

    def _link(self, dev, path):
        link = self.base / f"{dev[0]}:{dev[1]}"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(os.path.relpath(path, self.base))

    def device(self, maj, min, name=None, under=None):
        """A whole disk. Returns its devnum."""
        parent = (self.base / "devices" / under) if under else (self.base / "devices")
        d = parent / (name or f"d{maj}-{min}")
        d.mkdir(parents=True, exist_ok=True)
        (d / "dev").write_text(f"{maj}:{min}\n")
        (d / "holders").mkdir(exist_ok=True)
        (d / "slaves").mkdir(exist_ok=True)
        self._link((maj, min), d)
        return (maj, min)

    def partition(self, maj, min, disk_name, name):
        """A partition, nested inside its disk's sysdir."""
        d = self.base / "devices" / disk_name / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "dev").write_text(f"{maj}:{min}\n")
        (d / "partition").write_text("1\n")
        (d / "holders").mkdir(exist_ok=True)
        (d / "slaves").mkdir(exist_ok=True)
        self._link((maj, min), d)
        return (maj, min)

    def _list(self, dev, kind, items):
        d = Path(os.path.realpath(self.base / f"{dev[0]}:{dev[1]}")) / kind
        d.mkdir(exist_ok=True)
        for i in items:
            e = d / f"{kind}{i[0]}-{i[1]}"
            e.mkdir(exist_ok=True)
            (e / "dev").write_text(f"{i[0]}:{i[1]}\n")

    def holders(self, dev, *above):
        self._list(dev, "holders", above)

    def slaves(self, dev, *below):
        self._list(dev, "slaves", below)



def _topology(**kwargs):
    """Build a Topology, tolerating the pre-remediation constructor.

    The old implementation takes no `mountinfo` argument.  Dropping it here
    means a regression test run against the old code fails on an assertion
    about the guard's behaviour, not on a constructor signature mismatch --
    otherwise "the test fails" would prove nothing about the defect.
    """
    try:
        return pc5ctl.Topology(**kwargs)
    except TypeError:
        kwargs.pop("mountinfo", None)
        return pc5ctl.Topology(**kwargs)

class TopologyCase(unittest.TestCase):
    """Base: a fixture sysfs tree plus a stubbed root device number.

    Only `devnum_for_path` is stubbed, because a fixture tree has no real block
    devices to stat.  Everything that decides the answer -- parent_of,
    partitions_of, slaves, holders, root_closure -- is the production code
    reading the fixture.
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tree = FakeTree(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._fixed_root = None
        self._fixed_target = None

    def topology(self):
        topo = _topology(sys_block=str(self.tree.base),
                         mounts="/nonexistent",
                         mountinfo="/nonexistent")
        target = self._fixed_target
        root = self._fixed_root
        topo.devnum_for_path = (
            lambda path: None if target is None else target)
        topo.root_devnum = lambda: root
        return topo

    def decide(self, root, target):
        self._fixed_root = root
        self._fixed_target = target
        return pc5ctl.check_target_safe("/dev/target", self.topology())


class RootDiskRefused(TopologyCase):
    """The defect this task exists for."""

    def test_target_is_the_root_disk_itself(self):
        root = self.tree.device(254, 0, name="vda")
        ok, why = self.decide(root, root)
        self.assertFalse(ok)
        self.assertIn("backing '/'", why)

    def test_dev_root_alias_resolves_to_the_same_device(self):
        # /dev/root is not a device node; the kernel reports the root's devnum.
        # Once the target resolves to that devnum the path names are irrelevant.
        root = self.tree.device(254, 0, name="vda")
        ok, why = self.decide(root, root)
        self.assertFalse(ok, "an alias of the root device must be refused")

    def test_partition_containing_root_is_refused(self):
        self.tree.device(8, 0, name="sda")
        part = self.tree.partition(8, 1, "sda", "sda1")
        ok, why = self.decide(part, part)
        self.assertFalse(ok)
        self.assertIn("backing '/'", why)

    def test_parent_disk_of_root_partition_is_refused(self):
        disk = self.tree.device(8, 0, name="sda")
        part = self.tree.partition(8, 1, "sda", "sda1")
        ok, why = self.decide(part, disk)
        self.assertFalse(ok)
        self.assertIn("whole disk carrying the root filesystem", why)

    def test_other_partition_on_the_root_disk_is_refused(self):
        disk = self.tree.device(8, 0, name="sda")
        rootpart = self.tree.partition(8, 1, "sda", "sda1")
        other = self.tree.partition(8, 2, "sda", "sda2")
        ok, why = self.decide(rootpart, other)
        self.assertFalse(ok)
        self.assertIn("partition of the disk carrying '/'", why)

    def test_device_mapper_backing_the_root_is_refused(self):
        disk = self.tree.device(8, 0, name="sda")
        sda2 = self.tree.partition(8, 2, "sda", "sda2")
        dm0 = self.tree.device(253, 0, name="dm-0")
        self.tree.slaves(dm0, sda2)
        self.tree.holders(sda2, dm0)
        ok, why = self.decide(dm0, disk)
        self.assertFalse(ok, "the physical disk under a dm root must be refused")

    def test_lvm_physical_volume_under_the_root_is_refused(self):
        self.tree.device(8, 0, name="sda")
        sda3 = self.tree.partition(8, 3, "sda", "sda3")
        lv = self.tree.device(253, 2, name="dm-2")
        self.tree.slaves(lv, sda3)
        ok, why = self.decide(lv, sda3)
        self.assertFalse(ok)

    def test_holder_of_a_root_member_is_refused(self):
        root = self.tree.device(9, 0, name="md0")
        inner = self.tree.device(8, 16, name="sdb")
        self.tree.slaves(root, inner)
        self.tree.holders(inner, root)
        ok, why = self.decide(root, inner)
        self.assertFalse(ok)

    def test_multi_layer_dm_stack_is_refused(self):
        # root on dm-1, which sits on dm-0, which sits on sda2
        self.tree.device(8, 0, name="sda")
        sda2 = self.tree.partition(8, 2, "sda", "sda2")
        dm0 = self.tree.device(253, 0, name="dm-0")
        dm1 = self.tree.device(253, 1, name="dm-1")
        self.tree.slaves(dm0, sda2)
        self.tree.slaves(dm1, dm0)
        self.tree.holders(sda2, dm0)
        self.tree.holders(dm0, dm1)
        ok, why = self.decide(dm1, sda2)
        self.assertFalse(ok, "a two-layer dm stack must still reach the disk")


class AmbiguityFailsClosed(TopologyCase):
    """If the relationship cannot be established, refuse."""

    def test_unresolvable_root_is_refused(self):
        target = self.tree.device(8, 16, name="sdb")
        ok, why = self.decide(None, target)
        self.assertFalse(ok)
        self.assertIn("cannot determine which device backs '/'", why)

    def test_target_without_a_device_number_is_refused(self):
        root = self.tree.device(254, 0, name="vda")
        self._fixed_root, self._fixed_target = root, None
        ok, why = pc5ctl.check_target_safe("/dev/target", self.topology())
        self.assertFalse(ok)
        self.assertIn("identity cannot be established", why)

    def test_root_outside_sysfs_cannot_be_closed_over(self):
        # Root resolves to a devnum the kernel has no sysfs entry for, so the
        # closure is empty: nothing about the layout can be proved, so refuse.
        self.tree.device(8, 16, name="sdb")
        ok, why = self.decide((99, 99), (8, 16))
        self.assertFalse(ok, "an unreadable root topology must fail closed")

    def test_tmpfs_root_has_no_backing_device(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mounts = os.path.join(tmp, "mounts")
            with open(mounts, "w", encoding="utf-8") as fh:
                fh.write("tmpfs / tmpfs rw 0 0\n")
            topo = pc5ctl.Topology(sys_block=tmp, mounts=mounts)
            topo.root = os.path.join(tmp, "not-a-device")
            os.makedirs(topo.root, exist_ok=True)
            ok, why = pc5ctl.check_target_safe("/dev/target", topo)
            self.assertFalse(ok)
            self.assertTrue("cannot" in why or "refusing" in why)


class SafeTargetAccepted(TopologyCase):
    """The guard must not refuse a genuinely unrelated device."""

    def test_separate_usb_disk_is_allowed(self):
        self.tree.device(8, 0, name="sda")
        rootpart = self.tree.partition(8, 1, "sda", "sda1")
        usb = self.tree.device(8, 32, name="sdc")
        ok, why = self.decide(rootpart, usb)
        self.assertTrue(ok, why)
        self.assertIn("not part of the root storage", why)

    def test_usb_partition_is_allowed_when_unrelated(self):
        self.tree.device(8, 0, name="sda")
        rootpart = self.tree.partition(8, 1, "sda", "sda1")
        self.tree.device(8, 32, name="sdc")
        usbpart = self.tree.partition(8, 33, "sdc", "sdc1")
        ok, why = self.decide(rootpart, usbpart)
        self.assertTrue(ok, why)

    def test_second_disk_on_a_multidisk_root_is_allowed(self):
        # root on md0 built from sda1; sdb is spare and must stay writable
        self.tree.device(8, 0, name="sda")
        sda1 = self.tree.partition(8, 1, "sda", "sda1")
        md0 = self.tree.device(9, 0, name="md0")
        self.tree.slaves(md0, sda1)
        sdb = self.tree.device(8, 16, name="sdb")
        ok, why = self.decide(md0, sdb)
        self.assertTrue(ok, why)


class CommandRefusesBeforeWriting(unittest.TestCase):
    """cmd_usb itself: refusal must precede any destructive call."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.iso = os.path.join(self.dir, "pc5.iso")
        with open(self.iso, "wb") as fh:
            fh.write(b"ISO")
        import hashlib
        digest = hashlib.sha256(b"ISO").hexdigest()
        with open(self.iso + ".sha256", "w", encoding="ascii") as fh:
            fh.write(f"{digest}  pc5.iso\n")

    def _args(self, device, yes=False):
        class A:
            pass
        a = A()
        a.iso = self.iso
        a.device = device
        a.yes = yes
        return a

    def test_root_device_refused_and_no_dd_called(self):
        calls = []
        real_call = pc5ctl.subprocess.call
        pc5ctl.subprocess.call = lambda *a, **k: calls.append(a) or 0
        try:
            with unittest.mock.patch.object(
                    pc5ctl, "check_target_safe",
                    return_value=(False, "refusing: fixture root device")):
                rc = pc5ctl.cmd_usb(self._args("/dev/vda", yes=True))
        finally:
            pc5ctl.subprocess.call = real_call
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [], "dd must not be invoked for a refused target")

    def test_yes_cannot_bypass_the_guard(self):
        import unittest.mock as mock
        calls = []
        real_call = pc5ctl.subprocess.call
        pc5ctl.subprocess.call = lambda *a, **k: calls.append(a) or 0
        try:
            with mock.patch.object(pc5ctl, "check_target_safe",
                                   return_value=(False, "refusing: fixture")):
                rc_without = pc5ctl.cmd_usb(self._args("/dev/vda", yes=False))
                rc_with = pc5ctl.cmd_usb(self._args("/dev/vda", yes=True))
        finally:
            pc5ctl.subprocess.call = real_call
        self.assertEqual(rc_without, rc_with,
                         "--yes must not change a refusal into a write")
        self.assertEqual(calls, [])

    def test_refusal_never_becomes_a_write_even_if_target_opens(self):
        """The decisive --yes test: a refusal must not become dd.

        The other --yes tests use a real `/dev/vda`, which this process cannot
        open, so they would pass even if the guard were bypassed -- the write
        fails on permissions rather than on the check.  Here the open succeeds
        and the identity matches, so nothing downstream stops the write: the
        only thing that can refuse is the safety decision itself.
        """
        import unittest.mock as mock
        calls = []
        real_call = pc5ctl.subprocess.call
        pc5ctl.subprocess.call = lambda *a, **k: calls.append(a) or 0
        topo = mock.MagicMock()
        topo.devnum_for_path.return_value = (8, 9)
        topo.root_devnum.return_value = (254, 0)
        topo.root_closure.return_value = {(254, 0)}
        st = mock.Mock()
        st.st_mode = stat.S_IFBLK | 0o660
        st.st_rdev = os.makedev(8, 9)
        try:
            with mock.patch.object(pc5ctl, "check_target_safe",
                                   return_value=(False, "refusing: fixture")), \
                    mock.patch.object(pc5ctl, "Topology", return_value=topo), \
                    mock.patch.object(pc5ctl.os.path, "exists", return_value=True), \
                    mock.patch.object(pc5ctl.os, "open", return_value=3), \
                    mock.patch.object(pc5ctl.os, "close"), \
                    mock.patch.object(pc5ctl.os, "fstat", return_value=st), \
                    mock.patch.object(pc5ctl.shutil, "which", return_value="/bin/dd"):
                rc = pc5ctl.cmd_usb(self._args("/dev/sdz9", yes=True))
        finally:
            pc5ctl.subprocess.call = real_call
        self.assertEqual(rc, 1, "a refusal must return 1, not a success")
        self.assertEqual(calls, [],
                         "dd must never be invoked once the guard has refused")

    def test_target_that_becomes_related_before_the_write_is_refused(self):
        """TOCTOU: a device that joins the root topology is caught.

        The guard is consulted twice: once before the confirmation and once
        after the target is held open, immediately before dd.  Here the first
        decision permits and the second refuses -- exactly a topology that
        changed in the interval.  dd must not run.

        This also pins the --yes path: if either consultation were skipped for
        `--yes`, only one call would happen and the write would proceed.
        """
        import unittest.mock as mock
        calls = []
        real_call = pc5ctl.subprocess.call
        pc5ctl.subprocess.call = lambda *a, **k: calls.append(a) or 0
        decisions = [(True, "ok"), (False, "refusing: became related")]
        seen = []

        def sequencing(device, topology=None):
            seen.append(device)
            return decisions[min(len(seen) - 1, len(decisions) - 1)]

        st = mock.Mock()
        st.st_mode = stat.S_IFBLK | 0o660
        st.st_rdev = os.makedev(8, 9)
        topo = mock.MagicMock()
        topo.devnum_for_path.return_value = (8, 9)
        # A real writable descriptor, so the flow reaches the *second* guard
        # consultation instead of stopping at the first interrogation of the fd.
        # Nothing about the assertions changes: two consultations, a refusal, and
        # no dd -- which is the whole content of this test.
        _tmp = tempfile.TemporaryDirectory()
        _backing = os.open(os.path.join(_tmp.name, "backing.bin"),
                           os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            with mock.patch.object(pc5ctl, "check_target_safe", side_effect=sequencing), \
                    mock.patch.object(pc5ctl, "Topology", return_value=topo), \
                    mock.patch.object(pc5ctl.os.path, "exists", return_value=True), \
                    mock.patch.object(pc5ctl.os, "open",
                                      side_effect=lambda *a, **k: os.dup(_backing)), \
                    mock.patch.object(pc5ctl.os, "close"), \
                    mock.patch.object(pc5ctl.os, "fstat", return_value=st), \
                    mock.patch.object(pc5ctl.shutil, "which", return_value="/bin/dd"):
                rc = pc5ctl.cmd_usb(self._args("/dev/sdz9", yes=True))
        finally:
            pc5ctl.subprocess.call = real_call
            os.close(_backing)
            _tmp.cleanup()
        self.assertGreaterEqual(len(seen), 2,
                                "the guard must be re-consulted before the write")
        self.assertEqual(rc, 1, "a device that became related must refuse")
        self.assertEqual(calls, [], "dd must never run")

    def test_safe_target_passes_check_without_writing(self):
        """A resolvable, unrelated device reaches the confirmation and stops."""
        import tempfile
        import unittest.mock as mock
        calls = []
        real_call = pc5ctl.subprocess.call
        pc5ctl.subprocess.call = lambda *a, **k: calls.append(a) or 0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tree = FakeTree(tmp)
                tree.device(8, 0, name="sda")
                rootpart = tree.partition(8, 1, "sda", "sda1")
                usb = tree.device(8, 32, name="sdc")
                topo = _topology(sys_block=tmp, mounts="/nonexistent",
                                 mountinfo="/nonexistent")
                topo.devnum_for_path = lambda p: usb
                topo.root_devnum = lambda: rootpart
                # Patch only the device-path existence probe, not every
                # os.path.exists call: the topology readers must keep seeing
                # the fixture, or they silently resolve against the host sysfs.
                real_exists = os.path.exists
                def fake_exists(path):
                    return True if path == "/dev/sdc" else real_exists(path)
                with mock.patch.object(pc5ctl.os.path, "exists", fake_exists), \
                        mock.patch.object(pc5ctl, "Topology", return_value=topo):
                    rc = pc5ctl.cmd_usb(self._args("/dev/sdc", yes=False))
        finally:
            pc5ctl.subprocess.call = real_call
        self.assertEqual(rc, 2, "without --yes it must stop at the confirmation")
        self.assertEqual(calls, [], "no write without --yes")


import unittest.mock  # noqa: E402  (used by the tests above)

if __name__ == "__main__":
    unittest.main()


class UnknownTopologyRefuses(TopologyCase):
    """The audit's critical findings: unreadable must never mean absent.

    Each test fails against the pre-remediation implementation, which treated
    an unreadable `partition` attribute as "whole disk" and an unreadable
    `slaves` directory as "no backing devices" - in both cases permitting a
    write to a disk connected to the running root.
    """

    def _sda_with_root_on_sda2(self):
        self.tree.device(8, 0, name="sda")
        return self.tree.partition(8, 2, "sda", "sda2")

    # -- 1. unreadable `partition` metadata, root is /dev/sda2 ---------------
    def test_unreadable_partition_metadata_refuses_the_parent_disk(self):
        rootpart = self._sda_with_root_on_sda2()
        self.tree.make_unreadable(
            self.tree.base / "devices" / "sda" / "sda2" / "partition")
        ok, why = self.decide(rootpart, (8, 0))       # target = /dev/sda
        self.assertFalse(ok, "an unreadable partition attribute must refuse")
        self.assertIn("cannot establish the full storage topology", why)

    def test_malformed_partition_metadata_is_unknown_not_whole_disk(self):
        # The attribute exists but holds nonsense.  It is not "absent", so it
        # must not be read as "this is a whole disk".
        rootpart = self._sda_with_root_on_sda2()
        (self.tree.base / "devices" / "sda" / "sda2" / "partition").write_text("nonsense\n")
        self._fixed_root, self._fixed_target = rootpart, (8, 0)
        topo = self.topology()
        # A contradictory attribute is not the whole-disk answer: the root
        # must not be promoted to "the disk it is only part of".
        self.assertNotEqual(topo.root_disk(), rootpart)
        self.assertNotEqual(topo.root_closure(), {rootpart})

    # -- 2b. unresolvable parent -------------------------------------------
    def test_unresolvable_parent_refuses_instead_of_falling_back(self):
        # Root is a partition, but the containing directory cannot be
        # identified, so the whole disk holding it cannot be named.
        self.tree.device(8, 0, name="sda")
        orphan = self.tree.partition(8, 4, "sda", "sda4")
        (self.tree.base / "devices" / "sda" / "dev").unlink()
        self._fixed_root, self._fixed_target = orphan, orphan
        topo = self.topology()
        self.assertNotEqual(topo.root_disk(), orphan,
                            "root_disk must never fall back to the partition "
                            "whose parent could not be established")
        ok, why = self.decide(orphan, orphan)
        self.assertFalse(ok)
        self.assertIn("cannot establish the full storage topology", why)

    # -- 3/4. unreadable or malformed `slaves` under a dm root --------------
    def _dm_root_over_sda2(self):
        self.tree.device(8, 0, name="sda")
        sda2 = self.tree.partition(8, 2, "sda", "sda2")
        dm0 = self.tree.device(253, 0, name="dm-0")
        return sda2, dm0

    def test_unreadable_slaves_refuses_the_physical_backing_partition(self):
        sda2, dm0 = self._dm_root_over_sda2()
        self.tree.make_unreadable(self.tree.base / "devices" / "dm-0" / "slaves")
        ok, why = self.decide(dm0, sda2)
        self.assertFalse(ok, "unreadable slaves must refuse the backing partition")
        self.assertIn("cannot establish the full storage topology", why)

    def test_malformed_slaves_entry_refuses(self):
        sda2, dm0 = self._dm_root_over_sda2()
        entry = self.tree.base / "devices" / "dm-0" / "slaves" / "bad"
        entry.mkdir()
        (entry / "dev").write_text("not-a-devnum\n")
        ok, why = self.decide(dm0, sda2)
        self.assertFalse(ok, "an unidentifiable slave must refuse")

    # -- 5. a holder that is the ONLY route into the protected topology -----
    def test_holder_only_relationship_refuses(self):
        # md0 is the root; loop0 is built on md0 and is reachable only through
        # holders/. With holder traversal removed this target is permitted.
        md0 = self.tree.device(9, 0, name="md0")
        loop0 = self.tree.device(7, 0, name="loop0")
        self.tree.holders(md0, loop0)
        ok, why = self.decide(md0, loop0)
        self.assertFalse(ok, "a holder reachable only via holders/ must be refused")
        self.assertIn("storage stack under '/'", why)

    # -- 6. absent vs unreadable `partition` are distinguishable -----------
    def test_absent_partition_attribute_means_whole_disk(self):
        # No attribute at all: the device IS the whole disk, so the disk is
        # its own parent and a genuinely unrelated disk stays writable.
        disk = self.tree.device(8, 0, name="sda")
        other = self.tree.device(8, 32, name="sdc")
        self._fixed_root, self._fixed_target = disk, disk
        topo = self.topology()
        self.assertEqual(topo.root_disk(), disk,
                         "a whole disk is its own whole-disk answer")
        self.assertEqual(topo.root_closure(), {disk})
        ok, _why = self.decide(disk, other)
        self.assertTrue(ok, "an unrelated disk must stay writable")

    def test_unreadable_partition_attribute_is_unknown_not_absent(self):
        # The same missing-information situation, but the attribute is there
        # and unreadable.  It must NOT produce the whole-disk answer.
        rootpart = self._sda_with_root_on_sda2()
        self.tree.make_unreadable(
            self.tree.base / "devices" / "sda" / "sda2" / "partition")
        self._fixed_root, self._fixed_target = rootpart, rootpart
        topo = self.topology()
        self.assertNotEqual(topo.root_disk(), rootpart,
                            "an unreadable partition must not be reported as "
                            "the whole disk it is only part of")
        self.assertIsNot(topo.root_closure(), {rootpart})
        self.assertNotEqual(topo.root_closure(), {rootpart})

    # -- 7. empty vs unreadable `slaves` -----------------------------------
    def test_empty_slaves_is_an_empty_list_not_unknown(self):
        self.tree.device(8, 0, name="sda")
        sda2 = self.tree.partition(8, 2, "sda", "sda2")
        dm0 = self.tree.device(253, 0, name="dm-0")
        self._fixed_root, self._fixed_target = dm0, dm0
        topo = self.topology()
        # dm-0 has no slaves recorded and nothing says otherwise: treat it as
        # a leaf, so an unrelated target is still writable.
        self.assertEqual(topo.slaves(dm0), [],
                         "a genuine leaf must report [] so unrelated writes work")
        unrelated = self.tree.device(8, 32, name="sdc")
        ok, _why = self.decide(dm0, unrelated)
        self.assertTrue(ok, "an empty slaves list must keep unrelated targets safe")
        del sda2

    def test_unreadable_slaves_is_unknown_not_empty(self):
        _sda2, dm0 = self._dm_root_over_sda2()
        self.tree.make_unreadable(self.tree.base / "devices" / "dm-0" / "slaves")
        self._fixed_root, self._fixed_target = dm0, dm0
        topo = self.topology()
        self.assertNotEqual(topo.slaves(dm0), [],
                            "unreadable slaves must never be reported as a leaf")
        self.assertIsNot(topo.root_closure(), {dm0},
                         "an unreadable relationship must not yield a closure "
                         "that merely contains the root")

    # -- 8. --yes cannot turn unknown into safe ----------------------------
    def test_yes_does_not_rescue_unknown_topology(self):
        import tempfile
        import unittest.mock as mock
        calls = []
        real_call = pc5ctl.subprocess.call
        pc5ctl.subprocess.call = lambda *a, **k: calls.append(a) or 0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tree = FakeTree(tmp)
                tree.device(8, 0, name="sda")
                rootpart = tree.partition(8, 2, "sda", "sda2")
                tree.make_unreadable(
                    tree.base / "devices" / "sda" / "sda2" / "partition")
                topo = _topology(sys_block=tmp, mounts="/nonexistent",
                                 mountinfo="/nonexistent")
                topo.devnum_for_path = lambda p: (8, 0)   # /dev/sda
                topo.root_devnum = lambda: rootpart
                args = mock.Mock()
                args.iso = self._iso()
                args.device = "/dev/sda"
                args.yes = True
                real_exists = os.path.exists
                with mock.patch.object(
                        pc5ctl.os.path, "exists",
                        lambda p: True if p == "/dev/sda" else real_exists(p)), \
                        mock.patch.object(pc5ctl, "Topology", return_value=topo):
                    rc = pc5ctl.cmd_usb(args)
        finally:
            pc5ctl.subprocess.call = real_call
        self.assertEqual(rc, 1, "unknown topology must refuse even with --yes")
        self.assertEqual(calls, [], "dd must not be reached for unknown topology")

    def _iso(self):
        import hashlib
        import tempfile
        d = tempfile.mkdtemp()
        iso = os.path.join(d, "pc5.iso")
        with open(iso, "wb") as fh:
            fh.write(b"ISO")
        with open(iso + ".sha256", "w", encoding="ascii") as fh:
            fh.write(hashlib.sha256(b"ISO").hexdigest() + "  pc5.iso\n")
        return iso


class MountinfoIsInjectable(unittest.TestCase):
    """Root resolution must read the fixture, never the host's mount table."""

    def test_root_devnum_uses_the_supplied_mountinfo(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mi = os.path.join(tmp, "mountinfo")
            with open(mi, "w", encoding="utf-8") as fh:
                fh.write("36 25 8:2 / / rw - ext4 /dev/sda2 rw\n")
            topo = _topology(sys_block=os.path.join(tmp, "sys"),
                             mounts=os.path.join(tmp, "none"),
                             mountinfo=mi)
            self.assertEqual(topo.root_devnum(), (8, 2),
                             "must resolve from the fixture, not the host")

    def test_host_mountinfo_is_not_consulted_when_a_fixture_is_given(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mi = os.path.join(tmp, "mountinfo")
            with open(mi, "w", encoding="utf-8") as fh:
                fh.write("36 25 99:99 / / rw - tmpfs none rw\n")
            topo = _topology(sys_block=os.path.join(tmp, "sys"),
                             mounts=os.path.join(tmp, "none"),
                             mountinfo=mi)
            got = topo.root_devnum()
            self.assertEqual(got, (99, 99))
            self.assertNotEqual(got, pc5ctl.Topology().root_devnum(),
                                "a fixture must not return the host's root device")

    def test_missing_mountinfo_falls_through_to_mounts_source(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            topo = _topology(sys_block=os.path.join(tmp, "sys"),
                             mounts=os.path.join(tmp, "none"),
                             mountinfo=os.path.join(tmp, "absent"))
            self.assertIsNone(topo.root_devnum(),
                              "no mountinfo and no device source means unresolved")

    def test_unreadable_fixture_actually_denies_reads(self):
        """Guards the tests themselves: chmod must really block the read.

        Running as root makes permission bits advisory, and every unreadable
        fixture above would silently become readable - turning the critical
        regression tests into vacuous passes.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "f")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("x")
            os.chmod(p, 0o000)
            with self.assertRaises(PermissionError):
                open(p, encoding="utf-8").read()


def _is_unknown(value):
    """True when a relationship was reported as 'could not be established'.

    Defensive so these assertions stay *behavioural* against the two earlier
    revisions: `3834bf1` has no UNKNOWN at all, and an AttributeError would
    prove nothing about whether the guard would have refused.  A revision that
    cannot represent uncertainty is itself the defect, so the accompanying
    refusal assertion carries the weight.
    """
    sentinel = getattr(pc5ctl, "UNKNOWN", None)
    return sentinel is not None and value is sentinel


class UsbPathCase(unittest.TestCase):
    """Drives the real `cmd_usb` path with a fixture sysfs and a fake dd.

    `subprocess.call` is replaced, so `dd_calls` is the number of times the
    destructive command would have run.  Every prohibited case asserts it is
    zero -- refusing after invoking dd would be no protection at all.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = FakeTree(self._tmp.name)
        self.dd_calls = []
        self._real_call = pc5ctl.subprocess.call
        pc5ctl.subprocess.call = lambda *a, **k: self.dd_calls.append(a) or 0
        self.addCleanup(self._restore)

    def _restore(self):
        pc5ctl.subprocess.call = self._real_call

    def iso(self):
        iso = os.path.join(self._tmp.name, "pc5.iso")
        with open(iso, "wb") as fh:
            fh.write(b"ISO")
        with open(iso + ".sha256", "w", encoding="ascii") as fh:
            fh.write(hashlib.sha256(b"ISO").hexdigest() + "  pc5.iso\n")
        return iso

    def run_usb(self, device, target_devnum, root_devnum, yes=True):
        """Invoke cmd_usb with the topology resolved from the fixture tree."""
        topo = _topology(sys_block=str(self.tree.base), mounts="/nonexistent",
                         mountinfo="/nonexistent")
        topo.devnum_for_path = lambda p: target_devnum
        topo.root_devnum = lambda: root_devnum
        args = mock.Mock()
        args.iso = self.iso()
        args.device = device
        args.yes = yes
        real_exists = os.path.exists
        with mock.patch.object(
                pc5ctl.os.path, "exists",
                lambda p: True if p == device else real_exists(p)), \
                mock.patch.object(pc5ctl, "Topology", return_value=topo):
            return pc5ctl.cmd_usb(args)


class WholeDiskRootProtectsPartitions(UsbPathCase):
    """A1: a whole-disk root must protect the partitions on it.

    On `7abed02` the closure enumerated partitions only when `parent_of` named
    a parent.  A whole-disk root has none, so the disk was protected and its
    partitions were writable -- a regression against `3834bf1`, which seeded
    the closure from the root disk.
    """

    def disk_two_partitions(self):
        self.tree.device(8, 0, name="sda")
        one = self.tree.partition(8, 1, "sda", "sda1")
        two = self.tree.partition(8, 2, "sda", "sda2")
        return (8, 0), one, two

    def topo(self, root):
        t = _topology(sys_block=str(self.tree.base), mounts="/nonexistent",
                      mountinfo="/nonexistent")
        t.root_devnum = lambda: root
        return t

    def test_closure_includes_disk_and_both_partitions(self):
        disk, one, two = self.disk_two_partitions()
        self.assertEqual(self.topo(disk).root_closure(), {disk, one, two},
                         "a whole-disk root must protect its partitions")

    def test_each_partition_refused_through_cmd_usb(self):
        disk, one, two = self.disk_two_partitions()
        for part, name in ((one, "/dev/sda1"), (two, "/dev/sda2")):
            self.dd_calls.clear()
            rc = self.run_usb(name, part, disk, yes=True)
            self.assertEqual(rc, 1, f"{name} must refuse even with --yes")
            self.assertEqual(self.dd_calls, [], "dd must never be invoked")

    def test_sibling_partition_refused(self):
        disk, _one, two = self.disk_two_partitions()
        rc = self.run_usb("/dev/sda2", two, disk)
        self.assertEqual(rc, 1)
        self.assertEqual(self.dd_calls, [])

    def test_unrelated_device_still_permitted(self):
        disk, _one, _two = self.disk_two_partitions()
        usb = self.tree.device(8, 32, name="sdc")
        topo = self.topo(disk)
        topo.devnum_for_path = lambda p: usb
        ok, why = pc5ctl.check_target_safe("/dev/sdc", topo)
        self.assertTrue(ok, why)

    def test_partition_root_protects_parent_and_all_siblings(self):
        disk, one, two = self.disk_two_partitions()
        topo = self.topo(one)
        self.assertEqual(topo.root_closure(), {disk, one, two})
        for name, target in (("/dev/sda", disk), ("/dev/sda2", two)):
            self.dd_calls.clear()
            rc = self.run_usb(name, target, one)
            self.assertEqual(rc, 1, f"{name} must refuse")
            self.assertEqual(self.dd_calls, [])

    def test_three_partitions_all_protected(self):
        self.tree.device(8, 0, name="sda")
        parts = [self.tree.partition(8, n, "sda", f"sda{n}") for n in (1, 2, 3)]
        self.assertEqual(self.topo((8, 0)).root_closure(), {(8, 0), *parts})


class PartitionEnumerationFailsClosed(UsbPathCase):
    """A2: an incomplete enumeration must never become a shorter safety answer.

    `partitions_of` selected entries with `os.path.isfile(.../partition)`,
    which answers False for EACCES as readily as for ENOENT, so a partition it
    could not inspect was dropped from the protected set.  Present in
    `3834bf1` as well as `7abed02`.

    The root is a partition here, so enumeration runs through the parent-disk
    branch that exists in every revision -- this isolates A2 from A1.
    """

    def root_on_sda1(self):
        self.tree.device(8, 0, name="sda")
        one = self.tree.partition(8, 1, "sda", "sda1")
        two = self.tree.partition(8, 2, "sda", "sda2")
        return (8, 0), one, two

    def topo(self, root):
        t = _topology(sys_block=str(self.tree.base), mounts="/nonexistent",
                      mountinfo="/nonexistent")
        t.root_devnum = lambda: root
        return t

    def assert_refuses(self, root, target, label):
        topo = self.topo(root)
        self.assertTrue(_is_unknown(topo.root_closure()),
                        f"{label}: closure must be UNKNOWN")
        self.dd_calls.clear()
        rc = self.run_usb("/dev/target", target, root, yes=True)
        self.assertEqual(rc, 1, f"{label}: must refuse even with --yes")
        self.assertEqual(self.dd_calls, [], f"{label}: dd must never run")

    def test_absent_partition_attribute_stays_absent(self):
        disk, one, two = self.root_on_sda1()
        (self.tree.base / "devices" / "sda" / "queue").mkdir()
        self.assertEqual(sorted(self.topo(one).partitions_of(disk)),
                         [(8, 1), (8, 2)],
                         "a genuine absence must stay an absence")
        ok, why = pc5ctl.check_target_safe(
            "/dev/x", self._with_target(self.topo(one), (8, 32)))
        self.assertTrue(ok, why)

    @staticmethod
    def _with_target(topo, devnum):
        topo.devnum_for_path = lambda p: devnum
        return topo

    def test_unreadable_partition_attribute_is_unknown(self):
        _disk, one, two = self.root_on_sda1()
        self.tree.make_unreadable(
            self.tree.base / "devices" / "sda" / "sda2" / "partition")
        self.assert_refuses(one, two, "unreadable partition attribute")

    def test_unreadable_partition_directory_is_unknown(self):
        _disk, one, two = self.root_on_sda1()
        self.tree.make_unreadable(self.tree.base / "devices" / "sda" / "sda2")
        self.assert_refuses(one, two, "unreadable partition directory")

    def test_three_discovered_fourth_unreadable_is_unknown(self):
        """Atomicity: three partitions resolve, the fourth cannot be read.

        The requirement is explicit -- if three partitions are discovered and
        inspection of the fourth fails, the answer is UNKNOWN, never the three
        known ones.  A list of three here would be indistinguishable from a
        complete enumeration at the call site, and `check_target_safe` would
        then permit a target that is the fourth partition.
        """
        disk = (8, 0)
        self.tree.device(8, 0, name="sda")
        three = [self.tree.partition(8, n, "sda", f"sda{n}") for n in (1, 2, 3)]
        root = three[0]
        four = self.tree.partition(8, 4, "sda", "sda4")
        self.tree.make_unreadable(self.tree.base / "devices" / "sda" / "sda4")
        topo = self.topo(root)

        got = topo.partitions_of(disk)
        self.assertTrue(_is_unknown(got),
                        "three discovered and a fourth unreadable must be "
                        "UNKNOWN, not the three found so far")
        self.assertNotEqual(got, three,
                            "the partial list must never be returned")
        self.assertNotEqual(sorted(got or []), three)

        # And it must not reach the destructive command for the fourth one.
        self.assert_refuses(root, four, "three-of-four enumeration")

    def test_failure_after_entries_discovered_is_unknown(self):
        """The same property holds when the failure is an error, not a status.

        `_read_text` exists in every revision, so this needs no new helper.
        sda1 and sda2 resolve normally; reading sda3 raises.  Either the error
        propagates (nothing returned, so nothing permitted) or it is caught and
        reported UNKNOWN -- and in neither case may a list be returned.
        """
        disk = (8, 0)
        self.tree.device(8, 0, name="sda")
        one = self.tree.partition(8, 1, "sda", "sda1")
        self.tree.partition(8, 2, "sda", "sda2")
        self.tree.partition(8, 3, "sda", "sda3")
        topo = self.topo(one)

        real_read = pc5ctl._read_text

        def failing_on_sda3(path, *a, **k):
            if "sda3" in str(path):
                raise OSError(5, "Input/output error")
            return real_read(path, *a, **k)

        with mock.patch.object(pc5ctl, "_read_text", failing_on_sda3):
            try:
                got = topo.partitions_of(disk)
            except OSError:
                # An error escaping the enumeration also fails closed: nothing
                # is returned, so nothing can be permitted.  Represent that as
                # the sentinel where the module has one, so the assertion below
                # still runs on revisions that do not.
                got = getattr(pc5ctl, "UNKNOWN", object())
        self.assertTrue(_is_unknown(got),
                        "a failure after earlier entries resolved must be "
                        "UNKNOWN, not a list of what was found")
        self.assertNotEqual(got, [one])

    def test_failed_directory_listing_is_unknown(self):
        """A disk directory that cannot be listed at all is UNKNOWN."""
        _disk, one, _two = self.root_on_sda1()
        self.tree.make_unreadable(self.tree.base / "devices" / "sda")
        self.assertTrue(_is_unknown(self.topo(one).partitions_of((8, 0))))

    def test_malformed_partition_metadata_is_unknown(self):
        _disk, one, two = self.root_on_sda1()
        (self.tree.base / "devices" / "sda" / "sda2" / "dev").write_text("garbage\n")
        self.assert_refuses(one, two, "malformed dev")

    def test_malformed_partition_number_is_unknown(self):
        _disk, one, two = self.root_on_sda1()
        (self.tree.base / "devices" / "sda" / "sda2" / "partition").write_text("nonsense\n")
        self.assert_refuses(one, two, "malformed partition number")

    def test_device_disappearing_during_enumeration_is_unknown(self):
        _disk, one, _two = self.root_on_sda1()
        topo = self.topo(one)
        real_listdir = os.listdir
        state = {"done": False}

        def vanishing(path):
            names = real_listdir(path)
            if not state["done"] and "sda2" in names:
                state["done"] = True
                shutil.rmtree(os.path.join(path, "sda2"))
            return names

        with mock.patch.object(pc5ctl.os, "listdir", vanishing):
            got = topo.partitions_of((8, 0))
        self.assertTrue(_is_unknown(got),
                        "a device that disappears mid-enumeration must be "
                        "UNKNOWN, not a shorter list")

    def test_unresolvable_devnum_is_unknown(self):
        _disk, one, two = self.root_on_sda1()
        (self.tree.base / "devices" / "sda" / "sda2" / "dev").unlink()
        self.assert_refuses(one, two, "unresolvable devnum")

    def test_unresolvable_sysfs_link_is_unknown(self):
        _disk, one, two = self.root_on_sda1()
        os.unlink(self.tree.base / "8:2")     # dangling link for sda2
        self.assert_refuses(one, two, "dangling sysfs link")

    def test_partial_list_never_permits_a_write(self):
        _disk, one, two = self.root_on_sda1()
        self.tree.make_unreadable(self.tree.base / "devices" / "sda" / "sda2")
        topo = self.topo(one)
        closure = topo.root_closure()
        self.assertTrue(_is_unknown(closure))
        self.assertNotEqual(closure, {(8, 0), one},
                            "never a partial set that looks complete")
        self.assert_refuses(one, two, "partial enumeration")

    def test_yes_cannot_bypass_unknown_enumeration(self):
        _disk, one, two = self.root_on_sda1()
        self.tree.make_unreadable(self.tree.base / "devices" / "sda" / "sda2")
        for yes in (False, True):
            self.dd_calls.clear()
            rc = self.run_usb("/dev/target", two, one, yes=yes)
            self.assertEqual(rc, 1, f"yes={yes} must refuse")
            self.assertEqual(self.dd_calls, [])


class TopologyConsistency(UsbPathCase):
    """The check-to-write interval (TOCTOU).

    A userspace sysfs read cannot make the topology atomic until the write.
    These tests pin what the implementation actually establishes: the target is
    held open, its identity is re-read through the descriptor, and the safety
    decision is repeated before dd is invoked.
    """

    def test_dd_is_written_through_the_held_descriptor(self):
        self.tree.device(8, 0, name="sda")
        usb = self.tree.device(8, 32, name="sdc")
        topo = _topology(sys_block=str(self.tree.base), mounts="/nonexistent",
                         mountinfo="/nonexistent")
        topo.devnum_for_path = lambda p: usb
        topo.root_devnum = lambda: (8, 0)
        args = mock.Mock()
        args.iso = self.iso()
        args.device = "/dev/sdc"
        args.yes = True
        real_exists = os.path.exists
        # The descriptor is a real, writable one (an ordinary temporary file),
        # not a fabricated number.  A fixture that hands back `7` cannot reach the
        # code under test at all once the write path interrogates the descriptor it
        # holds, and a test that stops early would "pass" without proving that the
        # validated object is the written one.
        _tmp = tempfile.TemporaryDirectory()
        _backing = os.open(os.path.join(_tmp.name, "backing.bin"),
                           os.O_WRONLY | os.O_CREAT, 0o600)
        try:
          with mock.patch.object(
                pc5ctl.os.path, "exists",
                lambda p: True if p == "/dev/sdc" else real_exists(p)), \
                mock.patch.object(pc5ctl, "Topology", return_value=topo), \
                mock.patch.object(pc5ctl.os, "open",
                                  side_effect=lambda *a, **k: os.dup(_backing)), \
                mock.patch.object(pc5ctl.os, "fstat") as fstat, \
                mock.patch.object(pc5ctl.os, "close"):
            fstat.return_value = os.stat_result(
                (stat.S_IFBLK | 0o660, 0, 0, 0, 0, 0, 0, 0, 0, 0))
            with mock.patch.object(pc5ctl.os, "major", return_value=8), \
                    mock.patch.object(pc5ctl.os, "minor", return_value=32):
                rc = pc5ctl.cmd_usb(args)
        finally:
            os.close(_backing)
            _tmp.cleanup()
        self.assertEqual(rc, 0, "an unrelated device should reach dd")
        self.assertEqual(len(self.dd_calls), 1)
        of_arg = [a for a in self.dd_calls[0][0] if a.startswith("of=")][0]
        self.assertTrue(of_arg.startswith("of=/proc/self/fd/"),
                        f"dd must write through the pinned fd, got {of_arg}")
        self.assertIn("conv=fsync", self.dd_calls[0][0],
                      "the write must still be fsync-ed, descriptor or not")
