"""USB CLI boundaries around the descriptor-bound write.

These eight cases pin the *command* contract rather than the identity contract:
what is refused, in which order, with which exit status, and that a refusal
never leaves the process holding or closing the wrong descriptor.  They share
the harness of ``test_pc5ctl_usb_descriptor_identity`` on purpose -- the same
real descriptors, the same synthetic sysfs -- so a build cannot pass one file by
accidentally testing the other's seams.

Status: synthetic.  Ordinary temporary files, a recorded write child, no block
device opened or written.
"""

from __future__ import annotations

import os
import stat
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

# Order matters: the identity module inserts `tools/` into sys.path, and the
# path setup happens at import time.
#
# This sibling import must work under BOTH loaders, because both are used:
# `make check` runs `unittest discover -s tests`, which puts `tests/` on the
# path, while `python3 -m unittest tests.test_pc5ctl_usb_cli_boundaries` does
# not. Without the insertion below the second form failed with
# `ModuleNotFoundError` (independent review, finding F-4) -- and a suite that
# cannot be imported by dotted path is a suite that silently disappears from a
# reviewer's command line rather than one that fails loudly.
import sys

_TEST_DIR = str(Path(__file__).resolve().parent)
if _TEST_DIR not in sys.path:
    sys.path.insert(0, _TEST_DIR)

from test_pc5ctl_usb_descriptor_identity import (  # noqa: E402
    DescriptorBoundaryCase, USB_TARGET, ROOT_PARTITION, block)

import pc5ctl  # noqa: E402


class UsbCliBoundaryTests(DescriptorBoundaryCase):
    def _main(self, *argv, which="/bin/dd"):
        """Run the real CLI through the shared harness, capturing stdout."""
        rc = self.run_argv(list(argv), which=which)
        self.out = self.stdout.getvalue()
        return rc, self.out

    def test_1_absent_image_is_refused_before_the_device_is_considered(self):
        missing = Path(self.tmp.name) / "no-such.iso"
        rc, out = self._main("usb", "--iso", str(missing),
                             "--device", self.device, "--yes")
        self.assertEqual(rc, 1, out)
        self.assertIn("no image at", out)
        self.assertEqual(self.opened_fds, [], "no descriptor may be taken for a missing image")
        self.assertEqual(self.dd_calls, [])

    def test_2_checksum_mismatch_refuses_before_any_descriptor_is_taken(self):
        (Path(self.tmp.name) / "image.iso.sha256").write_text(
            "0" * 64 + "  image.iso\n", encoding="ascii")
        rc, out = self._main("usb", "--iso", str(self.iso),
                             "--device", self.device, "--yes")
        self.assertEqual(rc, 1, out)
        self.assertIn("checksum mismatch", out)
        self.assertEqual(self.opened_fds, [])
        self.assertEqual(self.dd_calls, [])

    def test_3_device_outside_dev_is_refused_without_topology_or_open(self):
        rc, out = self._main("usb", "--iso", str(self.iso),
                             "--device", "sdc", "--yes")
        self.assertEqual(rc, 1, out)
        self.assertIn("device must be a block device path", out)
        self.assertEqual(self.opened_fds, [])
        self.assertEqual(self.lookups, 0,
                         "the topology must not be consulted for a path that cannot be one")

    def test_4_missing_dd_refuses_before_the_descriptor_is_taken(self):
        with mock.patch.object(pc5ctl.shutil, "which", return_value=None):
            rc, out = self._main("usb", "--iso", str(self.iso),
                             "--device", self.device, "--yes", which=None)
        self.assertEqual(rc, 1, out)
        self.assertIn("dd not found", out)
        self.assertEqual(self.dd_calls, [])
        self.assertEqual(self.opened_fds, [],
                         "a missing write child must not leave the device open")

    def test_5_without_yes_the_command_stops_at_confirmation(self):
        rc, out = self._main("usb", "--iso", str(self.iso), "--device", self.device)
        self.assertEqual(rc, 2, out)
        self.assertIn("THIS ERASES EVERYTHING", out)
        self.assertEqual(self.dd_calls, [], "--yes is what separates a plan from a write")
        self.assertEqual(self.opened_fds, [],
                         "confirmation must precede taking the device open")

    def test_6_yes_cannot_bypass_a_refusal_even_with_a_writable_descriptor(self):
        # The descriptor is real and writable and the image verifies, so nothing
        # downstream can stop the write: only the safety decision itself can.
        self.fstat_sequence = [block(ROOT_PARTITION)]
        self.path_sequence = [USB_TARGET]
        rc, out = self._main("usb", "--iso", str(self.iso),
                             "--device", self.device, "--yes")
        # Whichever guard stops it, the refusal must be about the object the
        # descriptor refers to (8:1), never about the harmless name (8:32).
        self.assertIn("8:1", out)
        self.assertIn("refusing to write", out)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.dd_calls, [], "--yes must never turn a refusal into a write")
        self.assertIn("nothing was written", out)
        self.assertEqual(self.backing.read_bytes(), b"")

    def test_7_argv_keeps_the_image_input_and_the_descriptor_output_distinct(self):
        rc, out = self._main("usb", "--iso", str(self.iso),
                             "--device", self.device, "--yes")
        self.assertEqual(rc, 0, out)
        argv = self.dd_calls[0][0]
        self.assertEqual([a for a in argv if a.startswith("if=")], [f"if={self.iso}"],
                         "the verified image is the only input")
        of = [a for a in argv if a.startswith("of=")]
        self.assertEqual(len(of), 1, argv)
        self.assertTrue(of[0].startswith("of=/proc/self/fd/"), of)
        self.assertNotIn(f"of={self.device}", argv,
                         "the pathname must never be the write target")
        self.assertIn("bs=4M", argv)
        self.assertIn("conv=fsync", argv,
                      "the write must be durable before the command reports success")

    def test_8_descriptor_is_closed_once_on_both_paths_and_never_a_std_stream(self):
        # success path
        rc, out = self._main("usb", "--iso", str(self.iso),
                             "--device", self.device, "--yes")
        self.assertEqual(rc, 0, out)
        fd = self.held_fd()
        self.assertEqual(self.close_calls, [fd],
                         "exactly one close, of the descriptor that was opened")
        self.assertNotIn(0, self.close_calls)
        self.assertNotIn(1, self.close_calls)
        self.assertNotIn(2, self.close_calls)
        # and the descriptor really is closed afterwards: no leak past the command
        self.assertRaises(OSError, os.fstat, fd)


if __name__ == "__main__":
    unittest.main()
