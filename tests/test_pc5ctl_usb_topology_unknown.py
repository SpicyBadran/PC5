"""F-6: an end-to-end guard for the unreadable-relationship rule itself.

The independent review of `evidence/usb-descriptor-remediation-v1/` noted
(`INDEPENDENT-REVIEW-20261121.md` §9, finding F-6) that mutating `_relations` so an
unreadable `slaves`/`holders` directory returns "no relationships" instead of UNKNOWN
(`m7-unreadable-topology-is-empty`) broke exactly one test, and it a unit-level one:

    $ python3 evidence/usb-descriptor-remediation-v2/measure_mutation_mapping.py
    m7  fails(1): ['test_unreadable_slaves_is_unknown_not_empty']

`test_20_unreadable_sysfs_relationship_data` and `test_21_unreadable_holders_directory`
survive the mutant. Measured reason, which is more interesting than "weak test": both of
them chmod `devices/sda/slaves` / `devices/sda/holders`, and the root-side walk reaches
those directories through `partitions_of()` as well — the disk's own directory is listed
and each child is interrogated, so an unreadable child directory makes *that* step return
UNKNOWN too. The refusal is therefore overdetermined in that fixture, and a CLI-level
assertion built on it can never localise the decision to `_relations`.

These four cases close that gap by moving the unreadable directory one level down:
`devices/sda/sda1/holders` (and `…/slaves`) is read by exactly one thing — the
`_relations` call the walk performs for the root partition — so the refusal either stands
or falls on that function alone. Measured, not asserted:

  * real code: `_relations((8,1),'holders')` is UNKNOWN, the closure is UNKNOWN, exit 1,
    no `dd` call;
  * with m7 applied to a scratch copy of `tools/pc5ctl.py`: `_relations` returns `[]`, the
    closure is `{(8,0), (8,1)}`, exit 0 and `dd` is launched — the case fails, which is the
    whole point. `logs/32-f6-end-to-end-guard-sensitivity.txt` in the remediation package
    holds both runs.

A control pins the other direction: with those same directories readable the fixture is
complete, the closure is a plain set, and the write proceeds. Without it, "refused" would
not distinguish the guard from an unrelated refusal, which is the failure mode the review
warned about in general ("a passing test is not proof it tests the property").

Status — synthetic. Same construction as `test_pc5ctl_usb_descriptor_identity.py`: the
production entry point and real descriptor semantics, an ordinary temporary file as the
object behind the descriptor, a sysfs-shaped fixture tree, and no block device anywhere.
The chmods are on fixture directories inside a `TemporaryDirectory` and are undone by
`addCleanup` so the cleanup can remove the tree.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Loader-independent import of the sibling harness (finding F-4): `discover` puts
# `tests/` on the path, `python3 -m unittest tests.<this>` does not, and a suite that
# silently fails to import is a suite that is not being run.
_TEST_DIR = Path(__file__).resolve().parent
if str(_TEST_DIR) not in sys.path:
    sys.path.insert(0, str(_TEST_DIR))
_ROOT = _TEST_DIR.parent
for _needed in (str(_ROOT / "tools"), str(_TEST_DIR)):
    if _needed not in sys.path:
        sys.path.insert(0, _needed)

import pc5ctl  # noqa: E402
from test_pc5ctl_usb_descriptor_identity import (  # noqa: E402
    DescriptorBoundaryCase,
    ROOT_DISK,
    ROOT_PARTITION,
)


class UnreadableRelationshipEndToEnd(DescriptorBoundaryCase):
    """The root-side walk, with the unreadable directory reachable by one reader only."""

    def deny(self, relative):
        path = Path(self.tree.base) / relative
        self.assertTrue(path.is_dir(), f"fixture lost its directory: {relative}")
        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o755)
        # The denial must be real, not assumed: a sandbox that ignored mode bits would
        # make every assertion below vacuously green.
        with self.assertRaises(PermissionError):
            os.listdir(path)
        return path

    def test_unreadable_root_partition_holders_refuses_end_to_end(self):
        # `holders` is the dm/md direction: what is stacked on top of the root
        # partition.  Unreadable means the protected set is unknown, and unknown
        # must stop the write even though the named target is an unrelated stick.
        self.deny(Path("devices") / "sda" / "sda1" / "holders")
        self.assertIs(self.topo._relations(ROOT_PARTITION, "holders"), pc5ctl.UNKNOWN,
                      "the fixture no longer isolates `_relations`: this case guards "
                      "the unreadable-relationship answer, not some other refusal")
        self.assertIs(self.topo.root_closure(), pc5ctl.UNKNOWN)
        rc, out = self.cli()
        self.assert_refused("cannot establish the full storage topology")
        self.assertEqual(rc, 1, out)

    def test_unreadable_root_partition_slaves_refuses_end_to_end(self):
        # The same property in the other direction (`slaves`: what the root partition
        # is stacked on), because the walk loops over both kinds and either answer
        # alone is a partial guarantee.
        self.deny(Path("devices") / "sda" / "sda1" / "slaves")
        self.assertIs(self.topo._relations(ROOT_PARTITION, "slaves"), pc5ctl.UNKNOWN)
        self.assertIs(self.topo.root_closure(), pc5ctl.UNKNOWN)
        rc, out = self.cli()
        self.assert_refused("cannot establish the full storage topology")
        self.assertEqual(rc, 1, out)

    def test_the_same_fixture_writes_when_the_directories_are_readable(self):
        # The control that makes the two cases above mean something: with nothing
        # unreadable, this fixture is complete, the closure is a plain set, and the
        # production path launches exactly one write.  If this ever refuses, the
        # refusal asserted above is not the guard's doing and these cases prove
        # nothing (they would also stop failing under `m7`, which is how F-6 arose).
        self.assertEqual(self.topo.root_closure(), {ROOT_DISK, ROOT_PARTITION})
        rc, out = self.cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.dd_calls), 1, out)
        self.assertNotIn("cannot establish", out)

    def test_unreadable_relationship_below_the_target_is_not_the_root_guard(self):
        # Boundary of the design, recorded rather than wished away: the closure is
        # rooted at the device backing `/`, so an unreadable relationship directory
        # belonging to the *target* does not make the closure unknown, and the write
        # proceeds on a target that the root side does not name.  That is not a hole in
        # the root-storage rule — a device the root depends on is reached through the
        # root's own `slaves`/`holders`, which the two cases above pin — but it does
        # mean the target side is guarded by the other checks (whole disk, sibling
        # partition, mounted, checksum, descriptor identity), not by this one.
        self.deny(Path("devices") / "sdc" / "holders")
        self.assertEqual(self.topo.root_closure(), {ROOT_DISK, ROOT_PARTITION})
        rc, out = self.cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.dd_calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
