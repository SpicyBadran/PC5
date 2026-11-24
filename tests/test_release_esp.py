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
import time
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


    def test_a_kernel_image_that_contradicts_its_own_header_fails_the_release(self):
        """The verifier judges the kernel by its format, with no build record in sight.

        Every other ESP check in `tools/verify_iso.py` is a comparison -- against the
        structural rules firmware would apply, or against a digest this build recorded.  A
        corrupted `KERNEL64.BIN` satisfied both when the corruption was the whole story: the
        file was still readable, still the length the directory claimed, and without
        `--expect-esp-file` there was nothing to compare it against.  The image now carries a
        header, so a verdict exists that needs no provenance at all: the artifact
        contradicts itself, and that is enough to refuse the release.

        The corruption is the payload checksum, a single byte flipped in the code far from the
        header, because that is the class of damage with no signature to notice -- precisely
        what the 64-bit path could not previously state, and what `docs/KNOWN_LIMITATIONS.md`
        used to list as a control that could not be built.
        """
        # The build is the class's, so it is not cleaned up here -- and the tampered copy
        # goes into a directory this test owns, because writing it next to the release image
        # and then removing the directory is how one case makes every later one error out.
        scratch = tempfile.TemporaryDirectory(prefix="pc5-badkernel-")
        self.addCleanup(scratch.cleanup)
        build = self.build_for("64")
        try:
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            import kimage64
            with open(build.path("kernel64/KERNEL64.BIN"), "rb") as handle:
                kernel = handle.read()
            broken = kimage64.mutate_bytes(kernel, "checksum")
            self.assertEqual(len(broken), len(kernel),
                             "the mutation changed the length, so the FAT directory record "
                             "would fail first and the test would measure that instead")
            esp = bytearray(build.esp_bytes())
            start = esp.find(b"PC5K64IM")
            self.assertGreater(start, 0,
                               "the release ESP does not carry a header-stamped kernel, so "
                               "there is nothing here to corrupt")
            esp[start:start + len(broken)] = broken
            with open(build.iso(), "rb") as handle:
                iso = bytearray(handle.read())
            offset = build.layout()["layout"]["uefi_image_lba"] * SECTOR
            iso[offset:offset + len(esp)] = esp
            tampered = os.path.join(scratch.name, "bad-kernel.iso")
            with open(tampered, "wb") as handle:
                handle.write(bytes(iso))

            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "verify_iso.py"), tampered],
                capture_output=True, text=True, timeout=300, cwd=ROOT)
            self.assertNotEqual(proc.returncode, 0,
                                "a release ISO whose kernel image does not match its own "
                                "payload checksum verified clean")
            self.assertIn("KERNEL64.BIN in the ISO is a valid PC5 64-bit kernel image",
                          proc.stdout, proc.stdout[-800:])
            self.assertIn("kimage64-checksum", proc.stdout,
                          "the refusal should name the property that broke, the way the "
                          "loader's does")
        finally:
            sys.path.pop(0)


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


class ReleaseAttributionRebuild(unittest.TestCase):
    """The image's `build:`/`commit:` labels follow the commit, not the clock.

    `$(BUILD)/iso/BUILDINFO.TXT` lists a stamp file holding `git rev-parse HEAD`, and
    that stamp is the only thing making `$(ISO)` rebuild when the source commit moves.
    It is also easy to get wrong in a way nothing notices: a `:=` variable referenced
    above its own definition expands to nothing, the prerequisite silently disappears,
    and the ISO on disk goes on naming the commit *before* the one that produced it.
    That happened for real -- a `build/pc5.iso` labelled `e565dd4-dirty` while HEAD was
    `e7ff3ed` -- and every unit test in this file passed on it, because they compare an
    image against the build record written by the same run.  So the mechanism is
    executed here: clone, build, move HEAD, build again, require the label to move.
    """

    @staticmethod
    def _fields(path):
        out = {}
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if ":" in line:
                    key, value = line.split(":", 1)
                    out[key.strip()] = value.strip()
        return out

    @staticmethod
    def _git(git, tree, *args):
        proc = subprocess.run((git, "-C", tree) + args, capture_output=True,
                              text=True, timeout=300)
        if proc.returncode != 0:
            raise AssertionError("git %s: %s" % (" ".join(args),
                                                 (proc.stdout + proc.stderr)[-1500:]))
        return proc.stdout.strip()

    def _identity(self, git, tree):
        """Commit the scratch way as whoever owns this checkout.

        The test needs a commit to move HEAD with; it is created in a throwaway clone
        that is deleted with the temp directory.  The identity is taken from this
        repository rather than invented, because a test has no business attributing
        work to a name nobody owns.
        """
        def pick(*args):
            # `git config user.name` exits 1 when the key is simply unset, which is
            # not a failure of the assertion -- so lookups fall through quietly.
            proc = subprocess.run((git, "-C", tree) + args, capture_output=True,
                                  text=True, timeout=60)
            return proc.stdout.strip() if proc.returncode == 0 else ""

        name = pick("config", "user.name") or pick("log", "-1", "--format=%an")
        email = pick("config", "user.email") or pick("log", "-1", "--format=%ae")
        self.assertTrue(name and email, "no git identity to commit as, even in a scratch clone")
        return name, email

    @staticmethod
    def _settle():
        """Let the clock get clearly past a file that was just written.

        `make` rebuilds on a *strictly newer* prerequisite, so a source touched and a
        target built inside the same second can compare equal and skip the recipe --
        which would look like the dependency being tested here being absent.  Every
        case in this class writes a file or moves HEAD and then asks make what it
        thinks, so each one waits.
        """
        time.sleep(1.05)

    #: What never travels into a scratch clone, however `git status` labels it: build
    #: output, caches, and the evidence directory (which records digests of *other* runs and
    #: would make a scratch build look at somebody else's artifacts).
    SCRATCH_SKIP = ("build/", ".run-evidence/", "__pycache__/", ".git/")

    def _scratch_paths(self, git):
        """Every path in this working tree that HEAD does not have, or has differently.

        `git clone` hands out HEAD, so a clone alone grades the *committed* build rules and
        reports them as the ones under test -- which is how this class would have happily
        passed against the old Makefile for as long as the new one stayed uncommitted.  The
        set is taken from `git status` rather than from a hard-coded list of files, because a
        change that adds a new source file (`boot/uefi64/kimage64.c` did, when the kernel image
        format landed) is exactly the case a fixed list forgets, and the build then fails in
        the clone for a reason that has nothing to do with what is being asserted.
        """
        listing = subprocess.run(
            (git, "-C", ROOT, "status", "--porcelain=v1", "--untracked-files=normal"),
            capture_output=True, text=True, timeout=300)
        self.assertEqual(listing.returncode, 0, listing.stderr[-500:])
        # `listing.stdout`, deliberately *not* stripped: a porcelain line begins with two
        # status columns, so stripping the whole output eats the first line's leading space
        # and shifts that one path left by a character -- which reads as `kefile`, is not a
        # file, and is silently skipped.  A helper that trims for the convenience of the
        # other callers has no business deciding what a machine-readable listing looks like.
        paths = []
        for line in listing.stdout.splitlines():
            candidate = line[3:].split(" -> ")[-1].strip().strip('"')
            if not candidate or candidate.endswith("/"):
                continue
            if any(candidate.startswith(skip) for skip in self.SCRATCH_SKIP):
                continue
            paths.append(candidate)
        return paths

    def _scratch(self, git, tmp, tag):
        """A throwaway clone with the tree *under test* committed into it.

        Two things the copy has to do.  It has to exist, for the reason in
        `_scratch_paths`.  And it has to be *committed*: left as an uncommitted edit,
        whether the scratch tree looks dirty -- and so whether `build:` carries `-dirty`,
        which one case here asserts on and another asserts the absence of -- would depend on
        what the workspace happened to look like when the test ran.  Committing turns "clean"
        and "dirty" into states the test creates instead of inherits, and it makes the clone
        a tree whose HEAD contains the rules being judged, which is the closest thing to a
        release build this can be without touching `main`.  Being *committed* also means the
        clone is checked out, which is why the LFS smudge has to be switched off: see the
        note at the `git clone`.
        """
        tree = os.path.join(tmp, tag)
        # `GIT_LFS_SKIP_SMUDGE`, deliberately: `.gitattributes` routes `PS5UPDATE.PUP`
        # through LFS, the LFS store has no object for that pointer, and a clone
        # *fetches and smudges* by default -- so on any machine without the payload
        # cached, the clone dies in the filter (`remote missing object 42ea0e5…`,
        # `fatal: PS5UPDATE.PUP: smudge filter lfs failed`, exit 128) and every case in
        # this class fails before it builds anything.  That is what CI measured on the
        # hosted runner, while a developer machine with a warm LFS cache passes and
        # learns nothing is wrong.  The payload is irrelevant here: `make iso` reads no
        # `.pup`, and no assertion in this class is about the pointer's contents.  A
        # test may not make the network a prerequisite.
        clone = subprocess.run([git, "clone", "--no-hardlinks", ROOT, tree],
                               capture_output=True, text=True, timeout=600,
                               env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"))
        self.assertEqual(clone.returncode, 0, clone.stdout + clone.stderr)
        copied = []
        for relative in self._scratch_paths(git):
            source = os.path.join(ROOT, relative)
            if not os.path.isfile(source):
                continue
            target = os.path.join(tree, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)
            copied.append(relative)
        # The one file whose arrival is always required -- not because it is special, but
        # because a scratch tree whose Makefile is not the one under test would grade the
        # previous rules and report them as the new ones, which is the failure this whole
        # helper exists to prevent.
        with open(os.path.join(ROOT, "Makefile"), "rb") as handle:
            here = handle.read()
        with open(os.path.join(tree, "Makefile"), "rb") as handle:
            there = handle.read()
        self.assertEqual(there, here,
                         "the scratch clone's Makefile is not the working tree's")
        name, email = self._identity(git, tree)
        self._git(git, tree, "add", "-A")
        self._git(git, tree, "-c", "user.name=%s" % name, "-c", "user.email=%s" % email,
                  "commit", "-q", "--all", "--allow-empty",
                  "-m", "test: install the build rules under test")
        # `commit --all` cannot see an untracked file that became tracked here, so say
        # plainly if the scratch tree still thinks it has work pending: the dirty-label
        # case below would otherwise be asserting against a state this helper made.
        self.assertEqual(self._git(git, tree, "status", "--porcelain"), "",
                         "the scratch clone is dirty after installation, so `-dirty` in "
                         "this run is not the property under test")
        return tree

    def _make(self, tree, build, *targets, mode="64"):
        return subprocess.run(
            ["make", "-C", tree, "BUILD=" + build, "PC5_UEFI=" + mode] + list(targets),
            capture_output=True, text=True, timeout=1800,
            env=dict(os.environ, TMPDIR="/tmp", SOURCE_DATE_EPOCH="1700000000"))

    def test_the_scratch_clone_does_not_need_the_lfs_payload(self):
        """A clone that fetches is a clone that fails wherever the object is missing.

        Asserted as text rather than by running `git clone`: the failure this pins is a
        property of the *command*, and it has to hold on a machine where the LFS store is
        reachable too (mine), where an unguarded clone would succeed and a behavioural
        test would pass against the regression.
        """
        import inspect

        src = inspect.getsource(ReleaseAttributionRebuild._scratch)
        self.assertIn("GIT_LFS_SKIP_SMUDGE=\"1\"", src,
                      "the scratch clone must not depend on an LFS object being fetchable")
        self.assertIn("PS5UPDATE.PUP", src,
                      "and it must say why, in the file, or the next reader removes it")

    def test_moving_head_rebuilds_the_image_that_names_it(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory(prefix="pc5-attribution-") as tmp:
            tree = self._scratch(git, tmp, "clone")
            build = os.path.join(tmp, "b")
            stamp = os.path.join(build, "iso", "BUILDINFO.TXT")
            iso = os.path.join(build, "pc5.iso")

            first = self._make(tree, build, "iso")
            self.assertEqual(first.returncode, 0, (first.stdout + first.stderr)[-2000:])
            head = self._git(git, tree, "rev-parse", "HEAD")
            info = self._fields(stamp)
            self.assertEqual(info["commit"], head,
                             "the image does not name the commit it was built from")
            digest_one = sha256_file(iso)

            # An empty commit changes no source bytes and only the identity -- exactly
            # the case a stamp cannot be faked for, because nothing else in the tree
            # moved.
            name, email = self._identity(git, tree)
            self._git(git, tree, "-c", "user.name=%s" % name,
                      "-c", "user.email=%s" % email,
                      "commit", "--allow-empty", "-m",
                      "test: move HEAD for the attribution check")
            head_two = self._git(git, tree, "rev-parse", "HEAD")
            self.assertNotEqual(head_two, head)
            self._settle()
            second = self._make(tree, build, "iso")
            self.assertEqual(second.returncode, 0,
                             (second.stdout + second.stderr)[-2000:])
            self.assertIn("build record now names", second.stdout,
                          "the image kept a label naming the previous commit: the "
                          "stamp is not a prerequisite of BUILDINFO.TXT, or it does "
                          "not compare contents")
            info_two = self._fields(stamp)
            self.assertEqual(info_two["commit"], head_two)
            digest_two = sha256_file(iso)
            self.assertNotEqual(digest_two, digest_one,
                                "the label moved but the image did not: the stamp is "
                                "read by the recipe and not depended on")

            # ...and the other direction, which is what keeps the stamp from being a
            # rebuild-everything hammer: with HEAD unmoved, nothing is re-stamped and
            # nothing is rewritten.
            third = self._make(tree, build, "iso")
            self.assertEqual(third.returncode, 0, (third.stdout + third.stderr)[-2000:])
            self.assertNotIn("build record now names", third.stdout,
                             "an unmoved HEAD must not re-stamp anything")
            self.assertEqual(sha256_file(iso), digest_two)

    def test_the_stamps_are_defined_above_the_rules_that_list_them(self):
        """The cheapest half of the guard: a prerequisite list is frozen at parse time.

        The behavioural cases below are what actually prove the stamps work, and they
        cost a clone and a build each.  This one costs a string comparison and says why
        the lines are in this order: make expands a rule's prerequisites as it reads the
        makefile, so an assignment *below* the rule is invisible to it -- no error, no
        dependency, and a `make -n` that looks perfectly reasonable.
        """
        with open(os.path.join(ROOT, "Makefile"), encoding="utf-8") as handle:
            text = handle.read()
        rule = text.index("$(BUILD)/iso/BUILDINFO.TXT:")
        self.assertGreater(rule, 0)
        for name in ("PC5_UEFI ?=", "ISO_STAMP :=",
                     "ESP_LOADER      :=", "FORCE:", ".PHONY: FORCE"):
            position = text.rfind(name, 0, rule)
            self.assertGreater(position, -1,
                               "%s is assigned at or below the BUILDINFO.TXT rule, so "
                               "it cannot be a prerequisite of it" % name.strip(":="))

    def test_switching_modes_rewrites_the_record(self):
        """One build directory, two modes: the record follows the medium.

        After the first build both loaders exist, so a record that depends only on
        `$(ESP_LOADER)`'s timestamp sees nothing to do -- the 32-bit ESP goes into the
        image while `BUILDINFO.TXT` still says `uefi-path: 64` and quotes the other
        loader's digest.  `make ovmf-test` caught a build in exactly that state by
        reading the ISO instead of trusting the file; `$(ISO_STAMP)` is the fix,
        and this is the case that fails without it.
        """
        with tempfile.TemporaryDirectory(prefix="pc5-mode-stamp-") as tmp:
            build = os.path.join(tmp, "b")
            stamp = os.path.join(build, "iso", "BUILDINFO.TXT")
            iso = os.path.join(build, "pc5.iso")
            six = self._make(ROOT, build, "iso")
            self.assertEqual(six.returncode, 0, (six.stdout + six.stderr)[-2000:])
            first = self._fields(stamp)
            self.assertEqual(first["uefi-path"], "64")
            digest_64 = sha256_file(iso)

            self._settle()
            flipped = self._make(ROOT, build, "iso", mode="32")
            self.assertEqual(flipped.returncode, 0,
                             (flipped.stdout + flipped.stderr)[-2000:])
            self.assertIn("build record now names", flipped.stdout,
                          "the stamp did not move, so nothing downstream was told the "
                          "image had changed mode")
            second = self._fields(stamp)
            self.assertEqual(second["uefi-path"], "32")
            self.assertEqual(second["kernel"], "32-bit PC5 bring-up kernel")
            self.assertNotEqual(sha256_file(iso), digest_64,
                                "the record changed but the image did not")
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            import mkefiimg
            esp = open(os.path.join(build, "efi.img"), "rb").read()
            entry = mkefiimg.FatVolume(esp).walk().get("EFI/BOOT/BOOTX64.EFI") or {}
            self.assertEqual(second["esp-loader"],
                             hashlib.sha256(entry.get("data") or b"").hexdigest()[:16],
                             "the record quotes a loader that is not on the volume")

    def test_the_dirty_label_and_the_loader_digest_stay_honest(self):
        """An uncommitted source change is labelled, and its digest is re-quoted.

        `build:` carries `-dirty` so an artifact that no commit produced never looks
        like one it did; `commit:` still names HEAD, which remains the truth about
        where the tree forked; and `esp-loader:` is recomputed from the loader actually
        embedded, because the file carrying it depends on that loader.  The last of the
        three is what makes a dirty build auditable instead of merely labelled:
        `tests/ovmf_boot.py` refuses a record whose quoted digest is not the volume's
        loader, so a stale label cannot quietly travel with a rebuilt image.
        """
        git = shutil.which("git")
        if git is None:
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory(prefix="pc5-dirty-attribution-") as tmp:
            tree = self._scratch(git, tmp, "clone")   # clean by construction, see above
            build = os.path.join(tmp, "b")
            stamp = os.path.join(build, "iso", "BUILDINFO.TXT")
            self.assertEqual(self._make(tree, build, "iso").returncode, 0)
            head = self._git(git, tree, "rev-parse", "HEAD")
            clean = self._fields(stamp)
            self.assertEqual(clean["commit"], head)
            self.assertNotIn("-dirty", clean["build"],
                             "the scratch clone is committed, so its tree is clean")
            iso = os.path.join(build, "pc5.iso")
            digest_before = sha256_file(iso)

            source = os.path.join(tree, "boot", "uefi64", "uefi64_main.c")
            with open(source, "a", encoding="utf-8") as handle:
                handle.write("\n/* attribution fixture: changes the loader, nothing else */\n")
            self._settle()
            dirty_build = self._make(tree, build, "iso")
            self.assertEqual(dirty_build.returncode, 0,
                             (dirty_build.stdout + dirty_build.stderr)[-2000:])
            dirty = self._fields(stamp)
            self.assertEqual(dirty["commit"], clean["commit"],
                             "an uncommitted change must not claim a new commit")
            self.assertTrue(dirty["build"].endswith("-dirty"),
                            "the label hides that no commit produced this image: %r"
                            % dirty["build"])
            self.assertNotEqual(dirty, clean,
                                "the record was not regenerated when the loader it "
                                "names was relinked")
            self.assertNotEqual(sha256_file(iso), digest_before,
                                "the regenerated record never reached the image")
            # The source change above is a comment, chosen so the compiled loader is
            # byte-identical: what is under test is that touching a loader source
            # *propagates* -- the record regenerates and the ISO re-embeds it -- not
            # that some particular byte moves.  "Refreshes only when the digest happens
            # to differ" is the failure mode this case exists to catch.
            # The record's claim, checked the way the gate checks it.
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            import mkefiimg
            esp = open(os.path.join(build, "efi64-release.img"), "rb").read()
            entry = mkefiimg.FatVolume(esp).walk().get("EFI/BOOT/BOOTX64.EFI") or {}
            loader = entry.get("data") or entry.get("bytes")
            self.assertEqual(dirty["esp-loader"],
                             hashlib.sha256(loader).hexdigest()[:16])


if __name__ == "__main__":
    unittest.main(verbosity=2)
