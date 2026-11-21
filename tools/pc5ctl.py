#!/usr/bin/env python3
"""pc5ctl — the PC5 command line.

  pc5ctl info   [--iso build/pc5.iso]        build and image summary
  pc5ctl verify [--iso build/pc5.iso]        structural verification
  pc5ctl boot   [--iso build/pc5.iso]        run the emulator boot test
  pc5ctl chain  --log FILE                   compatibility chain from a boot log
  pc5ctl fw     analyze --input IMAGE        firmware analysis (no decryption)
  pc5ctl usb    --device /dev/sdX --iso ...  write the image to a USB stick

`usb` refuses to touch a device without --yes, refuses the device that holds
the running root filesystem, and re-verifies the image checksum first.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ISO = os.path.join(ROOT, "build", "pc5.iso")
DEFAULT_INFO = os.path.join(ROOT, "build", "build-info.json")


def load_info(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except OSError:
        return {}


def cmd_info(args):
    info = load_info(args.info)
    if not info:
        print("no build info: run 'make iso' first")
        return 1
    print(f"image        {info['iso_bytes']} bytes ({info['iso_sectors']} blocks)")
    print(f"sha256       {info['sha256']}")
    print(f"volume id    {info['volume_id']}")
    layout = info["layout"]
    print(f"boot image   LBA {layout['bios_boot_image_lba']} "
          f"({layout['bios_boot_image_bytes']} bytes, "
          f"{layout['bios_boot_virtual_sectors']} virtual sectors)")
    print(f"kernel       LBA {info['kernel']['lba']} ({info['kernel']['bytes']} bytes)")
    for f in info["files"]:
        print(f"file         {f['identifier']} LBA {f['lba']} {f['bytes']} bytes")
    return 0


def cmd_verify(args):
    cmd = [sys.executable, os.path.join(ROOT, "tools", "verify_iso.py"), args.iso]
    if os.path.exists(args.info):
        cmd += ["--info", args.info]
    return subprocess.call(cmd)


def cmd_boot(args):
    cmd = [sys.executable, os.path.join(ROOT, "tests", "boot_test.py"), "--iso", args.iso]
    return subprocess.call(cmd)


def cmd_chain(args):
    try:
        with open(args.log, "r", encoding="utf-8", errors="replace") as fh:
            log = fh.read()
    except OSError as exc:
        print(f"cannot read log: {exc}")
        return 1
    stages = []
    for line in log.replace("\r", "\n").split("\n"):
        line = line.strip()
        if line.startswith("chain "):
            parts = line.split(None, 3)
            if len(parts) >= 4:
                stages.append((parts[1], parts[2], parts[3]))
    if not stages:
        print("no chain lines in this log (boot the PC5 ISO first)")
        return 1
    print(f"{'#':>3}  {'STATE':<12} STAGE")
    for num, state, name in stages:
        print(f"{num:>3}  {state:<12} {name}")
    blocked = [s for s in stages if s[1] == "BLOCKED"]
    if blocked:
        print(f"\nfirst blocked stage: {blocked[0][0]} ({blocked[0][2]})")
    print("\nPS5 system software execution: NOT RUNNING (see chain above)")
    return 0


def cmd_fw(args):
    cmd = [sys.executable, os.path.join(ROOT, "tools", "firmware.py")] + args.rest
    return subprocess.call(cmd)


def find_root_device():
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) > 1 and parts[1] == "/":
                    return parts[0]
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------- device safety
#
# The `usb` command must never write to storage that carries the running root
# filesystem, or to anything underneath it.  `/proc/mounts` names the root
# source as `/dev/root` on many systems (the legacy `root=` convention with an
# initramfs), which is not a device node at all: comparing path strings, as this
# tool used to, silently fails to match the real disk and lets `--yes` write to
# the system disk.
#
# So identity is taken from kernel metadata instead: the root mount's
# major:minor, the same number for the target after following symlinks, the
# partition-to-disk and device-mapper relationships in `sysfs`.  Nothing here
# trusts a path name.
#
# The rule that matters is "fail closed".  If the layout cannot be read well
# enough to prove the target is unrelated to the root storage, the write is
# refused.  A refusal that is occasionally inconvenient is worth more than a
# guarantee that is occasionally wrong.

SYS_BLOCK = "/sys/dev/block"


def _read_text(path, opener=open):
    """Read a small sysfs/procfs file, or return None if it cannot be read."""
    try:
        with opener(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _probe(path):
    """Classify a sysfs path as PRESENT, ABSENT or UNREADABLE.

    `os.path.exists` and `os.path.isfile` answer False for *any* OSError, so
    they cannot distinguish "the kernel does not publish this" from "we are not
    allowed to look".  For a guard that decides whether a disk is erased, that
    difference is the whole question: only a genuine ENOENT may be read as
    absence, and every other failure must stay unknown.

    errno is examined directly rather than the exception class, because
    `PermissionError` and `FileNotFoundError` are both OSError subclasses and
    the distinction that matters is ENOENT/ENOTDIR versus everything else.
    """
    try:
        os.stat(path)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return ABSENT
        return UNREADABLE
    except (TypeError, ValueError):
        return UNREADABLE       # malformed path, e.g. an embedded NUL
    return PRESENT


# A relationship that could not be read is NOT the same as one that is absent,
# and the difference decides whether a disk gets erased.  Every relationship
# reader below returns one of:
#
#   a value            the relationship was read, and this is it
#   None               the relationship is genuinely absent (the kernel says so)
#   UNKNOWN            it could not be read, or could not be understood
#
# Callers must treat UNKNOWN as "do not write".  Collapsing it into None is the
# defect this module exists to prevent.
class _Unknown:
    """Sentinel for 'this could not be established'."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "UNKNOWN"

    def __bool__(self):
        return False


UNKNOWN = _Unknown()


# The three answers a sysfs path can give.  Deliberately NOT booleans: `False`
# is what let an unreadable entry be mistaken for an absent one.
ABSENT = "ABSENT"           # the kernel genuinely does not publish this
PRESENT = "PRESENT"         # it is there
UNREADABLE = "UNREADABLE"   # it may be there, but we cannot look


def _is_int(text):
    """True if `text` is a plain non-negative integer (the `partition` format)."""
    try:
        return int(text, 10) >= 0
    except (TypeError, ValueError):
        return False


def parse_devnum(text):
    """'254:0' -> (254, 0); anything else -> None."""
    if not text:
        return None
    parts = text.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0], 10), int(parts[1], 10))
    except ValueError:
        return None


class Topology:
    """Reads block-device relationships from the kernel.

    All paths are attributes so tests can point them at a fixture tree; the
    production defaults are the real sysfs locations.
    """

    def __init__(self, sys_block=SYS_BLOCK, mounts="/proc/mounts", root="/",
                 mountinfo="/proc/self/mountinfo"):
        self.sys_block = sys_block
        self.mounts = mounts
        self.root = root
        # Injectable so tests never read the host's real mount table; the
        # production default is the kernel's own view.
        self.mountinfo = mountinfo

    # -- identity ---------------------------------------------------------
    def devnum_for_path(self, path):
        """major:minor for a device node, or None.

        Uses st_rdev, so symlinks and bind paths collapse to one identity.
        """
        try:
            st = os.stat(path)
        except OSError:
            return None
        if not stat.S_ISBLK(st.st_mode):
            return None
        return (os.major(st.st_rdev), os.minor(st.st_rdev))

    def link_for_devnum(self, devnum):
        """The sysfs directory for a devnum, or None if the kernel has none.

        Only a genuine absence returns None.  A path that cannot be stat'ed is
        reported as None too, and every caller turns None into UNKNOWN rather
        than into an empty relationship, so an unreadable sysfs entry still
        fails closed.
        """
        if devnum is None:
            return None
        try:
            path = os.path.join(self.sys_block, "%d:%d" % devnum)
        except (TypeError, ValueError):
            return None
        if _probe(path) is not PRESENT:
            return None
        return path

    # -- relationships ----------------------------------------------------
    #
    # Each returns one of the three states described at UNKNOWN.  `partition_of`
    # is the discriminator the audit found missing: a whole disk has no
    # `partition` attribute *and the kernel says so*, whereas an unreadable one
    # is unknown and must stop the write.
    def partition_of(self, devnum):
        """True if a partition, False if a whole disk, UNKNOWN if undecidable."""
        link = self.link_for_devnum(devnum)
        if link is None:
            return UNKNOWN
        path = os.path.join(link, "partition")
        if not os.path.exists(path):
            # A real kernel omits `partition` for a whole disk.  Before
            # believing that, confirm the sysfs entry itself is readable: a
            # directory we cannot list tells us nothing about what it contains.
            if not os.path.isdir(link):
                return UNKNOWN
            try:
                os.listdir(link)
            except OSError:
                return UNKNOWN
            return False
        text = _read_text(path)
        if text is None:
            return UNKNOWN          # present but unreadable
        if _is_int(text):
            return True
        # Present but not a partition number: the attribute contradicts what
        # the kernel is supposed to publish.  That is not "no attribute", so
        # it cannot mean "whole disk".
        return UNKNOWN

    def parent_of(self, devnum):
        """Disk holding this partition; None for a whole disk; UNKNOWN if unsure.

        Never returns the device itself as its own parent, and never guesses:
        if the device is a partition and the parent cannot be resolved, the
        result is UNKNOWN rather than a fallback.
        """
        kind = self.partition_of(devnum)
        if kind is UNKNOWN:
            return UNKNOWN
        if kind is False:
            return None             # confirmed whole disk: it has no parent

        link = self.link_for_devnum(devnum)
        try:
            target = os.path.realpath(link)
        except OSError:
            return UNKNOWN
        parent_dir = os.path.dirname(target)
        if parent_dir == target:
            return UNKNOWN          # no containing directory to read
        found = self.devnum_for_sysdir(parent_dir)
        if found is None:
            return UNKNOWN          # malformed or absent parent `dev`
        if found == devnum:
            return UNKNOWN          # contradictory: claims to be its own disk
        return found

    def devnum_for_sysdir(self, sysdir):
        return parse_devnum(_read_text(os.path.join(sysdir, "dev")))

    def is_partition(self, devnum):
        """True/False when known, UNKNOWN when the metadata cannot be read."""
        return self.partition_of(devnum)

    def _relations(self, devnum, kind):
        """Read a `slaves` or `holders` directory: list, or UNKNOWN.

        An empty list is a real answer (a leaf, or nothing built on it).  A
        directory that cannot be listed, or an entry whose `dev` cannot be
        parsed, is UNKNOWN: the set of related devices would be incomplete, and
        a partial set is what lets an unrelated-looking disk be erased.
        """
        link = self.link_for_devnum(devnum)
        if link is None:
            return UNKNOWN
        base = os.path.join(link, kind)
        if not os.path.isdir(base):
            return UNKNOWN          # a real kernel always provides this
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return UNKNOWN
        out = []
        for name in names:
            found = parse_devnum(_read_text(os.path.join(base, name, "dev")))
            if found is None:
                return UNKNOWN      # an entry we cannot identify
            out.append(found)
        return out

    def holders(self, devnum):
        """Devices built on top of this one (dm, md, loop over a file...)."""
        return self._relations(devnum, "holders")

    def slaves(self, devnum):
        """Devices this one is built from (dm target, md member)."""
        return self._relations(devnum, "slaves")

    # -- the root filesystem ---------------------------------------------
    def root_devnum(self):
        """Resolve the device backing `/` to a major:minor, or None.

        The operative source is **mountinfo**, whose third field is the
        device number the kernel actually mounted.  That is what makes the
        `/dev/root` alias work: `/proc/mounts` names the root source
        `/dev/root` on many systems, and that is not a device node at all.

        Order:
          1. mountinfo, field 3 (authoritative; independent of path names);
          2. the `/proc/mounts` source, but only when it stats as a block
             device node -- a fallback for kernels without mountinfo.
        `stat()` on the mountpoint is deliberately **not** used: `/` is a
        directory, so `st_rdev` is meaningless there and `S_ISBLK` rejects it.
        The filesystem identity would be `st_dev`, which is a different number
        from the block device's `major:minor` and is not interchangeable.

        A tmpfs/overlay/network root has no single backing device; that returns
        None and every target is then refused by the caller.
        """
        found = self._root_devnum_from_mountinfo()
        if found is not None:
            return found
        source = None
        for line in (_read_text(self.mounts) or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == self.root:
                source = parts[0]
                break
        if not source:
            return None
        return self.devnum_for_path(source)

    def _root_devnum_from_mountinfo(self):
        """major:minor straight out of mountinfo: field 3 is the device."""
        text = _read_text(self.mountinfo)
        if not text:
            return None
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[4] == self.root:
                return parse_devnum(parts[2])
        return None

    def root_disk(self):
        """Whole disk carrying `/`; None if unresolvable, UNKNOWN if a partition
        whose parent cannot be established.

        The old version fell back to returning the root device when the parent
        lookup failed, which silently reinterpreted a partition as a whole disk
        and dropped the real disk out of the protected set.
        """
        devnum = self.root_devnum()
        if devnum is None:
            return None
        parent = self.parent_of(devnum)
        if parent is UNKNOWN:
            return UNKNOWN
        return devnum if parent is None else parent

    def root_closure(self):
        """Every device that must never be written to, or UNKNOWN.

        Returns UNKNOWN — never a partial set — as soon as any relationship in
        the graph cannot be read.  A partial closure is indistinguishable from
        a complete one at the call site, and that is precisely how a disk
        connected to the running root becomes writable.
        """
        devnum = self.root_devnum()
        if devnum is None:
            return None

        members = set()
        pending = [devnum]
        seen = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            members.add(current)

            # A device relates to the running root in two directions, and each
            # root representation exercises a different one:
            #
            #   partition root -> its parent disk and that disk's siblings
            #   whole-disk root -> its own partitions
            #
            # `parent_of` is authoritative only for the first.  For a whole
            # disk it correctly returns None, so deriving "which disk's
            # partitions do I enumerate" from it alone left every partition of
            # a whole-disk root unprotected -- the A1 regression.  The two
            # cases are therefore handled independently: `disk` is the parent
            # when there is one, otherwise the device is a confirmed whole disk
            # and is its own disk.
            parent = self.parent_of(current)
            if parent is UNKNOWN:
                return UNKNOWN

            if parent is not None:
                members.add(parent)
                pending.append(parent)
                siblings = self.partitions_of(parent)
                if siblings is UNKNOWN:
                    return UNKNOWN
                for sibling in siblings:
                    members.add(sibling)
                    if sibling not in seen:
                        pending.append(sibling)
            else:
                # A confirmed whole disk (parent_of returned None), so its
                # partitions belong to the protected set.
                own = self.partitions_of(current)
                if own is UNKNOWN:
                    return UNKNOWN
                for part in own:
                    members.add(part)
                    if part not in seen:
                        pending.append(part)

            for kind in ("slaves", "holders"):
                related = self._relations(current, kind)
                if related is UNKNOWN:
                    return UNKNOWN
                for other in related:
                    if other not in seen:
                        pending.append(other)
        return members

    def partitions_of(self, disk):
        """Partitions of `disk`, or UNKNOWN if the complete set cannot be known.

        Enumeration is all-or-nothing.  Every child of the sysfs disk directory
        is classified before anything is returned, and the first thing that
        cannot be established aborts the whole result:

          `partition` PRESENT    -> a partition; its number and `dev` must parse
          `partition` ABSENT     -> not a partition (a file, or a directory such
                                    as `queue`)
          `partition` UNREADABLE -> UNKNOWN

        A child that was listed and then cannot be stat'ed has disappeared
        mid-enumeration: the snapshot no longer describes the device, so the
        result is UNKNOWN rather than the entries collected so far.  Returning
        three known partitions after failing on a fourth would hand back a
        partial set that is indistinguishable from a complete one at the call
        site, and that is precisely how a related device gets written.

        The previous version selected entries with
        `os.path.isfile(.../partition)`, which answers False for EACCES as
        readily as for ENOENT, and so silently omitted a partition it could not
        read.
        """
        link = self.link_for_devnum(disk)
        if link is None:
            return UNKNOWN
        try:
            names = sorted(os.listdir(link))
        except OSError:
            return UNKNOWN

        out = []
        for name in names:
            candidate = os.path.join(link, name)

            # It was just listed, so it must still be there.  If it is not, the
            # view changed under us and no list built from it can be trusted.
            entry = _probe(candidate)
            if entry is UNREADABLE:
                return UNKNOWN
            if entry is ABSENT:
                return UNKNOWN     # vanished mid-enumeration

            state = _probe(os.path.join(candidate, "partition"))
            if state is UNREADABLE:
                return UNKNOWN     # might be a partition we cannot see
            if state is ABSENT:
                continue           # genuinely not a partition

            number = _read_text(os.path.join(candidate, "partition"))
            if number is None or not _is_int(number):
                return UNKNOWN     # unreadable or contradictory partition number

            found = parse_devnum(_read_text(os.path.join(candidate, "dev")))
            if found is None:
                return UNKNOWN     # unresolvable major:minor
            if found == disk:
                return UNKNOWN     # contradictory: a partition of itself
            out.append(found)
        return out


def check_target_safe(device, topology=None):
    """Decide whether `device` may be written. Returns (ok, reason).

    ok=False means refuse.  `reason` is shown to the operator and always names
    the actual relationship found, or the exact thing that could not be read.
    """
    topology = topology or Topology()

    target = topology.devnum_for_path(device)
    if target is None:
        return False, (f"cannot read a block device number for {device}: refusing to "
                       f"write to a path whose identity cannot be established")

    root_dev = topology.root_devnum()
    if root_dev is None:
        return False, ("cannot determine which device backs '/': refusing to write, "
                       "because the target cannot be proved distinct from the root "
                       "storage")

    # If the kernel exposes no sysfs entry for the root device, its parent,
    # slaves and holders are all invisible: nothing about the layout can be
    # proved, so the guard must not consent.  This is the fail-closed case that
    # matters most -- an unreadable topology is not an empty one.
    if topology.link_for_devnum(root_dev) is None:
        return False, ("cannot read the storage topology for the root device "
                       f"{root_dev[0]}:{root_dev[1]} (no sysfs entry): refusing to "
                       "write, because the target cannot be proved distinct from it")

    members = topology.root_closure()
    if members is None:
        return False, ("cannot resolve the storage underlying '/': refusing to write")
    if members is UNKNOWN:
        # Some relationship in the root's storage graph could not be read. The
        # set of protected devices is therefore incomplete, and a target that
        # looks unrelated may not be.
        return False, ("cannot establish the full storage topology under '/' "
                       "(unreadable or malformed device relationship): refusing to "
                       "write, because the target cannot be proved distinct from the "
                       "root storage")

    if target in members:
        disk = topology.root_disk()
        if target == root_dev:
            why = f"{device} is the device backing '/'"
        elif disk is UNKNOWN:
            why = (f"{device} is part of the storage stack under '/' "
                   f"(root device {root_dev[0]}:{root_dev[1]})")
        elif target == disk:
            why = (f"{device} is the whole disk carrying the root filesystem "
                   f"(root device {root_dev[0]}:{root_dev[1]})")
        else:
            parts = topology.partitions_of(disk) if disk is not None else UNKNOWN
            if parts is not UNKNOWN and target in parts:
                why = f"{device} is a partition of the disk carrying '/'"
            else:
                why = (f"{device} is part of the storage stack under '/' "
                       f"(root device {root_dev[0]}:{root_dev[1]})")
        return False, f"refusing to overwrite {device}: {why}"
    return True, "target is not part of the root storage"


# ---------------------------------------------------------------- descriptor binding
#
# `check_target_safe` above answers a question about a *path*.  A path is not an
# object: between the answer and the write, the name can be unlinked, retargeted
# at another device node, or replaced by a symlink, and every further lookup of
# that name then describes a different device.  The write itself cannot follow
# the name, because a `write(2)` goes to whatever the open file description refers
# to -- so if the safety decision is made about the name while the write is made
# through a descriptor, the two can disagree, and the disagreement decides
# whether a disk gets erased.
#
# The rule the code below enforces is therefore narrow and absolute: once the
# target is open, the identity used for the safety decision comes from
# `fstat` on that descriptor and from nowhere else.  A pathname may only ever
# *refuse* (it is compared, and a mismatch stops the write); it can never
# authorise, because authorisation from a re-resolvable name is what the whole
# guard exists to avoid.
#
# What this does NOT buy, and no userspace code can: atomicity of the topology.
# `major:minor` describes an object the kernel reports *now*.  A root remount,
# a device-mapper table reload, an md add/remove, a partition-table rewrite or a
# replug that reuses a device number can all change what the same identity means
# after the last check has run.  The decision is therefore repeated immediately
# before the child is launched, which shortens that window to microseconds
# without closing it; and the honest residual is stated in
# docs/engineering/USB-IDENTITY-BOUNDARY-DESIGN-v1.md: never write to a device
# from a system whose own root storage could be rearranged underneath you.


def fd_writable(fd):
    """True only if this open file description itself permits writing.

    `os.access` and the file's mode bits describe the *path* and the calling
    privileges, not this descriptor: a descriptor can be opened read-only while
    the name looks writable, or inherited with the write bit stripped.  Reading
    `O_ACCMODE` out of `F_GETFL` asks the kernel about the object we hold.  A
    descriptor we cannot interrogate at all is a refusal, not a pass.
    """
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except (OSError, ValueError):
        return False
    return (flags & os.O_ACCMODE) != os.O_RDONLY


def fd_block_identity(fd):
    """`(major, minor)` of the block device behind `fd`; None if there is none.

    This is the identity that the safety decision is allowed to be about.  It
    comes from `fstat`, so it cannot be moved by renaming, unlinking or
    re-pointing any path -- including the `/dev/...` name the operator typed.
    """
    try:
        st = os.fstat(fd)
    except (OSError, ValueError):
        return None
    if not stat.S_ISBLK(st.st_mode):
        return None
    return (os.major(st.st_rdev), os.minor(st.st_rdev))


def fd_child_target_usable(fd):
    """True if a child can open `/proc/self/fd/<fd>` as its write target.

    A descriptor-backed path is not automatically usable: procfs can be
    unmounted or hidden, the entry can be gone, and the referent can have been
    unlinked (the `(deleted)` suffix the kernel appends is how that shows up).
    Each of those is a refusal.  There is deliberately no fallback to the
    `/dev/...` name in the failure paths: falling back is how a validated
    descriptor becomes an unvalidated pathname.

    What this does *not* do is verify that the alias points back at this
    descriptor: `readlink` answers with the referent's *text*, and text cannot
    authorise anything.  The write's identity comes from the descriptor the
    child inherits, never from what the link says (independent review, finding
    F-7: this check is advisory by design, and a spoofed referent was measured
    to leave the write bound to the held descriptor).
    """
    path = f"/proc/self/fd/{fd}"
    try:
        os.lstat(path)
        referent = os.readlink(path)
    except (OSError, ValueError):
        return False
    if referent.endswith(" (deleted)"):
        return False
    return True


class _BoundTopology:
    """A topology view in which the target's identity is the descriptor's.

    `check_target_safe` asks exactly one question about the target --
    `devnum_for_path(device)` -- and everything else it asks is about the root
    storage.  Replacing that one answer with the identity the kernel reported for
    the held descriptor makes the decision and the write share an object by
    construction: no change to any pathname, in any order, at any moment, can
    make this guard consent to writing something it did not examine.

    The `device` argument still flows through, but only into the message text, so
    a refusal can name what the operator typed.
    """

    def __init__(self, topology, devnum):
        self._topology = topology
        self._devnum = devnum

    def devnum_for_path(self, path):
        return self._devnum

    def __getattr__(self, name):
        # root_devnum, root_closure, root_disk, link_for_devnum, partitions_of,
        # parent_of: all about the root, never about the name we are holding.
        return getattr(self._topology, name)


def cmd_usb(args):
    iso = args.iso
    if not os.path.exists(iso):
        print(f"no image at {iso}")
        return 1
    sha_file = iso + ".sha256"
    expected = None
    if os.path.exists(sha_file):
        with open(sha_file, "r", encoding="ascii") as fh:
            expected = fh.read().split()[0]
    import hashlib
    h = hashlib.sha256()
    with open(iso, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    actual = h.hexdigest()
    print(f"image    {iso}")
    print(f"sha256   {actual}")
    if expected and actual != expected:
        print("checksum mismatch against the .sha256 file: refusing to write")
        return 1
    device = args.device
    if not device.startswith("/dev/"):
        print("device must be a block device path such as /dev/sdX")
        return 1
    if not os.path.exists(device):
        print(f"device {device} does not exist")
        return 1
    # Storage safety runs before anything destructive and before --yes is
    # consulted, so no combination of flags can reach dd with a root device.
    safe, reason = check_target_safe(device)
    if not safe:
        print(reason)
        print("nothing was written")
        return 1
    print(f"target   {device}")
    print("\nTHIS ERASES EVERYTHING ON THE TARGET DEVICE.")
    if not args.yes:
        print("re-run with --yes to confirm after checking the device path")
        return 2
    dd = shutil.which("dd")
    if not dd:
        print("dd not found")
        return 1

    # From here on, the target is the descriptor -- not the name.  Holding an
    # open file description is what makes "the object I checked" and "the object
    # I write" the same kernel object, and that is only true if three things all
    # hold: the safety decision is computed from the descriptor's identity, the
    # child is actually given that descriptor, and nothing falls back to the
    # pathname when any of it fails.
    try:
        target_fd = os.open(device, os.O_WRONLY)
    except OSError as exc:
        print(f"cannot open {device} for writing ({exc.strerror}): refusing to write")
        print("nothing was written")
        return 1
    # Checked before the cleanup block: refusing must not close a descriptor the
    # process still needs, and `pass_fds` cannot inherit one of 0/1/2.
    if target_fd < 3:
        print(f"cannot inherit the target descriptor for {device} (fd {target_fd}): "
              "refusing to write")
        print("nothing was written")
        return 1
    try:
        if not fd_writable(target_fd):
            print(f"cannot hold a writable descriptor for {device}: refusing to write")
            print("nothing was written")
            return 1
        opened_as = fd_block_identity(target_fd)
        if opened_as is None:
            print(f"cannot identify the descriptor for {device}: refusing to write")
            print("nothing was written")
            return 1
        if not fd_child_target_usable(target_fd):
            print(f"/proc/self/fd/{target_fd} is not usable as a write target: "
                  "refusing to write")
            print("nothing was written")
            return 1

        # The name the operator typed must still resolve to the object being
        # held.  This can only *refuse*: the decision below never trusts this
        # lookup, because a pathname lookup is exactly what a substitution
        # attack controls.  It exists so a swapped device node -- the stick
        # pulled and replugged as something else, the symlink retargeted -- is
        # reported to the operator instead of being silently written through.
        named_as = Topology().devnum_for_path(device)
        if opened_as != named_as:
            print(f"{device} changed identity between validation and write "
                  f"(opened {opened_as[0]}:{opened_as[1]}, "
                  f"path now {named_as}): refusing to write")
            print("nothing was written")
            return 1

        # The authoritative safety decision: the same complete check as above,
        # but about the device the descriptor refers to.  Because the topology
        # itself can move (dm/md reloads, partition-table rewrites, root
        # remounts), this is re-run here rather than assumed from the earlier
        # pass -- and it is re-run on the descriptor identity, so a pathname
        # swap between the two passes cannot make it describe a different
        # device than the one about to be written.
        again = check_target_safe(device, topology=_BoundTopology(Topology(), opened_as))
        if not again[0]:
            print(again[1])
            print("nothing was written")
            return 1
        if fd_block_identity(target_fd) != opened_as:
            print(f"descriptor for {device} changed before the write: refusing to write")
            print("nothing was written")
            return 1

        # The child gets exactly one thing from us: this validated descriptor.
        # `pass_fds` is not decoration -- without it Python closes every fd >= 3
        # in the child and `/proc/self/fd/N` simply does not exist there, which is
        # the state `usb` shipped in.  `close_fds=True` is stated explicitly so the
        # inheritance set is visible in the source: one descriptor, no others.
        writing = f"/proc/self/fd/{target_fd}"
        argv = [dd, f"if={iso}", f"of={writing}",
                "bs=4M", "status=progress", "conv=fsync"]
        try:
            return subprocess.call(argv, close_fds=True, pass_fds=(target_fd,))
        except OSError as exc:
            # A failed launch is a refusal, never a retry against the pathname.
            print(f"could not launch {dd} ({exc.strerror}): refusing to write")
            print("nothing was written")
            return 1
    finally:
        os.close(target_fd)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pc5ctl", description="PC5 control tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info")
    p.add_argument("--info", default=DEFAULT_INFO)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("verify")
    p.add_argument("--iso", default=DEFAULT_ISO)
    p.add_argument("--info", default=DEFAULT_INFO)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("boot")
    p.add_argument("--iso", default=DEFAULT_ISO)
    p.set_defaults(func=cmd_boot)

    p = sub.add_parser("chain")
    p.add_argument("--log", required=True)
    p.set_defaults(func=cmd_chain)

    p = sub.add_parser("fw")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_fw)

    p = sub.add_parser("usb")
    p.add_argument("--iso", default=DEFAULT_ISO)
    p.add_argument("--device", required=True)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_usb)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
