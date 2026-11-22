"""The release ISO's ESP carries the UEFI path it claims to, byte for byte.

Why this exists
---------------
`make iso` puts exactly one FAT ESP on the El Torito UEFI entry, and until `PC5_UEFI`
existed that ESP always held the 32-bit bring-up loader.  The 64-bit loader and
`KERNEL64.BIN` lived in `build/efi64.img`, produced by `make uefi64`, executed by
`make stubtest64` -- and shipped nowhere.  So "the release ISO boots PC5" and "the 64-bit
path is executed and gated" were both true, and neither implied the other.

Choosing a mode is easy; *proving which one shipped* is the part that mattered, and the
verifier could not do it.  `tools/verify_iso.py` parsed the ESP the way firmware would
mount it -- a FAT12/16 boot sector, `EFI/BOOT/BOOTX64.EFI` reachable through a cluster
chain, an MZ/PE header, a machine type, a subsystem -- and stopped.  Corrupting bytes in
the middle of the loader inside the ESP changed nothing about that verdict, because a
truncated `.text` is still a well-formed PE.  The only check that noticed was
`image SHA-256 matches build info`, which compares the ISO against a file the same build
just wrote: it says "this image is self-consistent", not "this image contains what the
gates tested".

So this file asserts three things, in this order of importance:

  * the ESP's loader and kernel bytes are identical to the *individually built*
    `build/uefi{,64}/BOOTX64.EFI` and `build/kernel{,64}/KERNEL{,64}.BIN`, which are the
    files `make stubtest` / `make stubtest64` execute -- the release artifact is the
    tested artifact, in both modes;
  * asking for one file *as* the other **fails** -- the mode's own kernel offered as the
    ESP loader, and vice versa, both files present and real -- so the gate is comparing
    content and not merely confirming that something exists at a name;
  * a corrupted ESP that is then rebuilt into the release is caught.  This is the case
    the whole-image digest cannot serve: the rebuild rewrites `build-info.json` too, so
    `image SHA-256 matches build info` goes green on the corrupted image while the
    content hash fails.

Building is done into a scratch `BUILD` directory with the reproducible-input variables
pinned, and both release modes are built twice so the digest claim is not just asserted
in `docs/BUILDING.md` but executed.  Each build is a few seconds; nothing here touches
the tree the reader is working in.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTOR = 2048          # the ISO's logical block size; El Torito's own LBA field is 512

MODES = {
    "32": {"esp": "efi.img", "loader": "uefi/BOOTX64.EFI",
           "kernel": "kernel.bin", "kernel_name": "KERNEL.BIN"},
    "64": {"esp": "efi64-release.img", "loader": "uefi64/BOOTX64.EFI",
           "kernel": "kernel64/KERNEL64.BIN", "kernel_name": "KERNEL64.BIN"},
}


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class IsoBuild:
    """One `make BUILD=<tmp> PC5_UEFI=<mode> iso verify` run, and its artefacts."""

    def __init__(self, mode, tag="build", extra_env=None):
        self.mode = mode
        self.dir = tempfile.TemporaryDirectory(prefix="pc5-iso-%s-" % mode)
        self.build = os.path.join(self.dir.name, tag)
        self.env = dict(os.environ)
        self.env["TMPDIR"] = "/tmp"
        # Pinned so a reproducible digest is a claim about the build, not about the
        # clock.  `docs/BUILDING.md` says the two embedded inputs are the build id and
        # the build timestamp; this pins both, and one case below unpins the timestamp
        # to show the pinning is what earns the digest.
        self.env["SOURCE_DATE_EPOCH"] = "1700000000"
        self.env["BUILD_ID"] = "gated"
        self.env["BUILD_DATE"] = "2023-11-14T22:13:20Z"
        self.env.update(extra_env or {})

    def make(self, targets=("iso", "verify"), mode=None, env=None, drop=()):
        """Run make into this build's private BUILD directory.

        `drop` removes a variable the constructor pinned, which is how the
        reproducibility control asks "what happens if the timestamp is not pinned".
        """
        cmd = ["make", "-C", ROOT, "BUILD=" + self.build,
               "PC5_UEFI=" + (mode or self.mode)]
        cmd += list(targets)
        run_env = dict(self.env)
        for key in drop:
            run_env.pop(key, None)
        run_env.update(env or {})
        proc = subprocess.run(cmd, capture_output=True, text=True, env=run_env,
                              timeout=900)
        self.last_output = proc.stdout + proc.stderr
        return proc

    def path(self, *parts):
        return os.path.join(self.build, *parts)

    def iso(self):
        return self.path("pc5.iso")

    def layout(self):
        with open(self.path("build-info.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def esp_bytes(self):
        """The UEFI image exactly as the El Torito entry refers to it."""
        info = self.layout()["layout"]
        # `uefi_image_lba` counts 2048-byte CD sectors (that is how mkiso records it
        # and how `tools/verify_iso.py` carves it); `uefi_image_bytes` is a byte count.
        # Confusing the two puts the image at the wrong offset -- which, measured, is
        # how an earlier draft of this file "discovered" that ESP corruption anywhere
        # in the image went unnoticed.
        start = info["uefi_image_lba"] * SECTOR
        end = start + info["uefi_image_bytes"]
        with open(self.iso(), "rb") as handle:
            handle.seek(start)
            return handle.read(end - start)

    def verify_with(self, loader, kernel_name, kernel):
        """Run the verifier with an explicit expectation, without `--info`.

        Dropping `--info` is the point: with the build's own build-info present, the
        whole-ISO digest check catches any edit anywhere in the image, and the test
        could not tell a content check from a self-consistency check.
        """
        cmd = [sys.executable, os.path.join(ROOT, "tools", "verify_iso.py"),
               self.iso(), "--expect-esp-loader", loader,
               "--expect-esp-file", "%s=%s" % (kernel_name, kernel)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                              cwd=ROOT)
        return proc

    def cleanup(self):
        self.dir.cleanup()


class ReleaseEspContents(unittest.TestCase):
    """Which loader and which kernel the release image actually carries."""

    builds = []

    @classmethod
    def setUpClass(cls):
        for mode in ("32", "64"):
            build = IsoBuild(mode)
            cls.builds.append(build)
            proc = build.make()
            if proc.returncode != 0:
                raise AssertionError("make PC5_UEFI=%s iso verify failed:\n%s"
                                     % (mode, build.last_output[-4000:]))

    @classmethod
    def tearDownClass(cls):
        for build in cls.builds:
            build.cleanup()
        cls.builds = []

    def build_for(self, mode):
        return next(b for b in self.builds if b.mode == mode)

    def test_each_mode_ships_the_artifacts_it_gated(self):
        for mode, spec in MODES.items():
            with self.subTest(mode=mode):
                build = self.build_for(mode)
                # The verifier's own checks are what `make verify` runs; re-run them
                # here against the paths, so a Makefile edit that quietly drops the
                # expectations from the recipe fails here too rather than only in CI.
                proc = build.verify_with(build.path(*spec["loader"].split("/")),
                                         spec["kernel_name"],
                                         build.path(*spec["kernel"].split("/")))
                self.assertEqual(proc.returncode, 0, proc.stdout[-2500:])
                self.assertIn("ESP loader is byte-identical", proc.stdout)
                self.assertIn("ESP %s is byte-identical" % spec["kernel_name"],
                              proc.stdout)
                # A FAT directory stores names upper-cased, so a request spelled
                # differently must still find the entry -- otherwise the useful failure
                # mode ("file missing") would be reachable by a typo in the Makefile.
                lower = build.verify_with(build.path(*spec["loader"].split("/")),
                                          spec["kernel_name"].lower(),
                                          build.path(*spec["kernel"].split("/")))
                self.assertEqual(lower.returncode, 0, lower.stdout[-1500:])
                self.assertNotIn("the loader reads it from here", lower.stdout,
                                 "a lower-cased name was reported as missing")

    def test_the_esp_region_is_the_esp_image_that_was_built(self):
        for mode, spec in MODES.items():
            with self.subTest(mode=mode):
                build = self.build_for(mode)
                esp = build.esp_bytes()
                image = build.path(spec["esp"])
                self.assertEqual(len(esp), os.path.getsize(image),
                                 "the El Torito entry's byte count disagrees with the "
                                 "ESP image on disk")
                self.assertEqual(hashlib.sha256(esp).hexdigest(), sha256_file(image),
                                 "the ESP inside the release ISO is not the ESP the "
                                 "build produced")

    def test_a_different_real_artifact_is_refused_and_says_so(self):
        """The gate compares content; it is not an existence check.

        The strongest available control is to hand it two files that exist in this very
        build and ask for them the wrong way round: the ESP's loader must not verify
        against the kernel, and vice versa.  An earlier version of this test pointed the
        expectation at the *other mode's* loader, which in mode 64 is not even built --
        so it proved only that a missing file fails, which the report already says.
        Without this case, a Makefile edit that pinned `PC5_UEFI` to one value
        regardless of the command line would still verify clean, and the release would
        quietly stop being the 64-bit one while every check kept passing.
        """
        for mode in ("32", "64"):
            with self.subTest(mode=mode):
                build = self.build_for(mode)
                spec = MODES[mode]
                right_loader = build.path(*spec["loader"].split("/"))
                right_kernel = build.path(*spec["kernel"].split("/"))
                # Ask for the kernel as the loader and the loader as the kernel: both
                # files exist, both are real PC5 artifacts, and neither belongs where
                # the other does.
                proc = build.verify_with(right_kernel, spec["kernel_name"], right_loader)
                self.assertNotEqual(proc.returncode, 0,
                                    "the %s-bit release ESP verified clean with its "
                                    "loader and kernel expectations swapped, so the "
                                    "comparison is not checking content after all"
                                    % mode)
                self.assertIn("FAIL", proc.stdout)
                self.assertRegex(proc.stdout, r"in-iso [0-9a-f]{12}\s+built [0-9a-f]{12}")

    def test_kernel_of_the_other_mode_is_reported_absent(self):
        build = self.build_for("64")
        proc = build.verify_with(build.path("uefi64/BOOTX64.EFI"), "KERNEL.BIN",
                                build.path("kernel.bin"))
        self.assertNotEqual(proc.returncode, 0, proc.stdout[-1500:])
        # The failure detail lists what *is* there, which is what makes the message
        # actionable: a missing file and a mismatching file must not read the same.
        self.assertRegex(proc.stdout, r"present: KERNEL64\.BIN")

    def test_a_byte_changed_inside_the_loader_file_is_caught(self):
        """The regression this whole check exists for, as a control.

        A single flipped byte in the middle of the ESP copy of the loader leaves a
        mountable FAT, a readable cluster chain and a well-formed PE header, so the
        structural checks -- the ones that were there before -- still pass.  The hash
        check is what fails.  If a later edit makes `verify_esp` accept the tampered
        image *with* the expectation, or reject it *without* one, this test says which
        of the two behaviours moved.
        """
        build = self.build_for("64")
        loader = build.path("uefi64/BOOTX64.EFI")
        with open(loader, "rb") as handle:
            original = handle.read()
        esp = bytearray(build.esp_bytes())
        probe = original[0x400:0x440]
        offset = esp.find(probe)
        self.assertGreater(offset, 0, "could not locate the loader inside the ESP")
        target = offset + 0x420                    # inside .text, not a header field
        esp[target] ^= 0xFF
        with open(build.iso(), "rb") as handle:
            iso = bytearray(handle.read())
        info = build.layout()["layout"]
        start = info["uefi_image_lba"] * SECTOR
        iso[start:start + len(esp)] = esp
        tampered = os.path.join(build.dir.name, "tampered.iso")
        with open(tampered, "wb") as handle:
            handle.write(bytes(iso))

        structural = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "verify_iso.py"), tampered],
            capture_output=True, text=True, timeout=300, cwd=ROOT)
        self.assertEqual(structural.returncode, 0,
                         "the structural ESP checks now notice mid-file corruption "
                         "too, which is fine -- but then this test's second half is "
                         "the only thing left proving the hash check works")

        hashed = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "verify_iso.py"), tampered,
             "--expect-esp-loader", loader,
             "--expect-esp-file", "KERNEL64.BIN=" + build.path("kernel64/KERNEL64.BIN")],
            capture_output=True, text=True, timeout=300, cwd=ROOT)
        self.assertNotEqual(hashed.returncode, 0,
                            "a corrupted loader inside the release ESP verified clean")
        self.assertIn("ESP loader is byte-identical", hashed.stdout)
        self.assertIn("FAIL", hashed.stdout)

    def test_the_two_modes_ship_different_images(self):
        """A digest that cannot tell the paths apart would be evidence of nothing.

        Asserted against the same pinned inputs, so the only difference is the mode --
        which is what makes the pair meaningful next to the reproducibility case below.
        """
        digests = {}
        for mode in ("32", "64"):
            build = self.build_for(mode)
            digests[mode] = sha256_file(build.iso())
        self.assertNotEqual(digests["32"], digests["64"],
                            "both PC5_UEFI modes produced a byte-identical release "
                            "image, so one of them is not what it claims to be")

    def test_a_tampered_esp_rebuilt_into_the_release_is_still_caught(self):
        """The control that shows these pins are not a second way of saying `--info`.

        `make verify` already compares the finished image against
        `build/build-info.json`. That comparison is green by construction whenever the
        tampering happens *upstream of the record*: corrupt the ESP image, re-run
        `make iso`, and the build happily describes the image it just made. So the
        digest check can only say "this image is not the one on record" -- never "this
        image does not contain what the gates executed".  A corrupted ESP that is
        rebuilt with its own build-info is the case that separates the two, and it is
        measured here rather than argued.

        The corruption is deliberately not aimed at a particular file: whichever ESP
        file the flipped byte lands in, some pinned comparison must notice.
        """
        build = IsoBuild("64", tag="tampered")
        try:
            self.assertEqual(build.make().returncode, 0, build.last_output[-1500:])
            esp = build.path("efi64-release.img")
            with open(esp, "r+b") as handle:
                handle.seek(0x9000)
                original = handle.read(1)
                handle.seek(0x9000)
                handle.write(bytes([original[0] ^ 0xFF]))
            self.assertEqual(build.make(targets=("iso",)).returncode, 0,
                             build.last_output[-1500:])
            proc = build.make(targets=("verify",))
            self.assertNotEqual(
                proc.returncode, 0,
                "a release whose ESP had been corrupted, then rebuilt with fresh "
                "build-info, verified clean -- which means `make verify` trusts the "
                "build's own record and nothing else")
            self.assertRegex(proc.stdout, r"\[FAIL\] ESP (loader|KERNEL64\.BIN)")
            self.assertIn("image SHA-256 matches build info", proc.stdout)
            self.assertNotIn("[FAIL] image SHA-256 matches build info", proc.stdout,
                             "the whole-image digest also failed, so this proves "
                             "nothing about the ESP checks being the discriminating ones")
        finally:
            build.cleanup()

    def test_readme_in_the_esp_is_the_file_it_claims(self):
        """A cheap check that the ESP reader is right, not merely self-consistent."""
        build = self.build_for("32")
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "verify_iso.py"), build.iso(),
             "--json-out", build.path("verify-report.json"), "-q"],
            capture_output=True, text=True, timeout=300, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(build.path("verify-report.json"), encoding="utf-8") as handle:
            report = json.load(handle)
        readme = report["data"]["uefi_files"]["README.TXT"]
        with open(os.path.join(ROOT, "docs", "ISO_README.txt"), "rb") as handle:
            source = handle.read()
        self.assertEqual(readme["bytes"], len(source))
        self.assertEqual(readme["sha256"], hashlib.sha256(source).hexdigest())


class ReleaseReproducibility(unittest.TestCase):
    """The release digest is a property of the source, for both UEFI paths."""

    def test_each_mode_rebuilds_byte_identically(self):
        """`make clean iso` twice, which is the snippet `docs/BUILDING.md` prints.

        The second build is asked to wipe its own BUILD directory first, so this is a
        clean-tree rebuild and not a no-op: an incremental build reproducing itself
        would say very little, since the point of the documented recipe is that no
        leftover object from an earlier build can leak into the digest.
        """
        for mode in ("32", "64"):
            with self.subTest(mode=mode):
                first = IsoBuild(mode, tag="a")
                second = IsoBuild(mode, tag="b")
                try:
                    self.assertEqual(first.make().returncode, 0,
                                     first.last_output[-2000:])
                    self.assertEqual(second.make(targets=("clean", "iso", "verify")).returncode,
                                     0, second.last_output[-2000:])
                    self.assertEqual(sha256_file(first.iso()),
                                     sha256_file(second.iso()),
                                     "two builds of the same source with pinned "
                                     "SOURCE_DATE_EPOCH/BUILD_ID/BUILD_DATE differ")
                finally:
                    first.cleanup()
                    second.cleanup()

    def test_the_pin_is_what_buys_it(self):
        """The digest is stable only because the embedded inputs are pinned.

        This is the control for the test above: if nothing time-dependent were embedded
        in the image, pinning would be theatre and the reproducibility claim in
        `docs/BUILDING.md` would be describing a property the build already had.
        """
        pinned = IsoBuild("32", tag="pinned")
        unpinned = IsoBuild("32", tag="unpinned")
        try:
            self.assertEqual(pinned.make().returncode, 0, pinned.last_output[-1500:])
            proc = unpinned.make(drop=("BUILD_DATE", "SOURCE_DATE_EPOCH", "BUILD_ID"))
            self.assertEqual(proc.returncode, 0, unpinned.last_output[-1500:])
            self.assertNotEqual(sha256_file(pinned.iso()), sha256_file(unpinned.iso()),
                                "nothing time- or VCS-dependent is embedded, so "
                                "pinning buys nothing and docs/BUILDING.md should say "
                                "the build is reproducible unconditionally")
        finally:
            pinned.cleanup()
            unpinned.cleanup()


    def test_the_iso64_shorthand_does_the_whole_job(self):
        """`make iso64` is what a release engineer will actually type.

        A convenience target is easy to add and easy to leave broken: it lives in a
        recipe line, so nothing checks it until someone runs it.  It is tested by
        running it, which also proves the recursive `$(MAKE)` forwards `BUILD=` --
        without that, the target would build into the repository's own `build/`
        directory no matter where the caller asked for output, and this file's other
        cases would start trampling a real tree.
        """
        build = IsoBuild("64", tag="shorthand")
        try:
            proc = build.make(targets=("iso64",), mode=None)
            self.assertEqual(proc.returncode, 0, proc.stdout[-2500:] + proc.stderr[-1500:])
            # `iso64` is only worth having if the shorthand really produced *and*
            # checked a 64-bit image in this clean BUILD directory; the two lines below
            # are the ones only mode 64 prints.
            self.assertIn("VALID:", proc.stdout, proc.stdout[-1500:] + proc.stderr[-1500:])
            self.assertIn("ESP KERNEL64.BIN is byte-identical to KERNEL64.BIN", proc.stdout)
            self.assertIn("ESP loader is byte-identical", proc.stdout)
            self.assertTrue(os.path.exists(build.path("kernel64", "KERNEL64.BIN")),
                            "the recursive $(MAKE) did not forward BUILD=, so the "
                            "shorthand built into the repository tree instead")
        finally:
            build.cleanup()


class ModeSelection(unittest.TestCase):
    """`PC5_UEFI` is an option, not a suggestion, and it refuses nonsense."""

    def plan(self, *args):
        """`make -n` into a BUILD directory that does not exist yet.

        With the default `BUILD=build` and an up-to-date tree, make prints "Nothing to
        be done" and there is no recipe to inspect, which would make these three tests
        pass or fail on the reader's build state rather than on the Makefile.
        """
        tmp = tempfile.mkdtemp(prefix="pc5-plan-")
        try:
            proc = subprocess.run(
                ["make", "-C", ROOT, "-n", "BUILD=" + tmp] + list(args),
                capture_output=True, text=True, timeout=300, cwd=ROOT,
                env=dict(os.environ, TMPDIR="/tmp"))
            return proc.stdout + proc.stderr, proc.returncode
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_default_is_the_historical_path(self):
        out, code = self.plan("iso")
        self.assertEqual(code, 0, out[-1500:])
        self.assertIn("--uefi-image ", out)
        self.assertRegex(out, r"--uefi-image \S*/efi\.img")
        self.assertNotIn("KERNEL64.BIN=", out.replace("kernel64/KERNEL64", ""),
                        "the default release must not start carrying the 64-bit kernel")

    def test_sixty_four_selects_the_long_mode_esp(self):
        out, code = self.plan("PC5_UEFI=64", "iso")
        self.assertEqual(code, 0, out[-1500:])
        self.assertRegex(out, r"--uefi-image \S*/efi64-release\.img")
        self.assertIn("--file KERNEL64.BIN=", out)

    def test_an_unknown_mode_is_refused_at_parse_time(self):
        out, code = self.plan("PC5_UEFI=128", "iso")
        self.assertNotEqual(code, 0)
        self.assertIn("must be 32 or 64", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
