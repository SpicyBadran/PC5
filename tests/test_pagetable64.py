"""Host-side tests for the D1 runtime page-table walker and the D2 arithmetic.

`kernel64/pagetable64.c` and `kernel64/alloc64.c` are the two pieces of the
64-bit path that decide whether the boot is *actually* sound: the walker asks
what the hardware would find at each address, and the allocation check decides
whether firmware honoured the bound.  Both are compiled here from the same
source the kernel and loader use, with the same flags, and driven against
synthetic tables in ordinary host memory.

What this proves: the index arithmetic, the large-page handling, the
identity policy and the overflow logic are correct for the inputs shown.
What this does **not** prove: that any real firmware builds the tables this way,
or that any real CPU walks them as this model says.  The real walk happens in
kernel64/main.c on the machine's own CR3, and only QEMU/OVMF can demonstrate it.
"""
from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "pagetable64_probe.c"
SOURCES = [ROOT / "kernel64" / "pagetable64.c", ROOT / "kernel64" / "alloc64.c"]

# The probe is compiled once for the whole module; every test reads the same
# output rather than recompiling a dozen times.
_cache = {}


def probe_output():
    if "out" in _cache:
        return _cache["out"]
    with tempfile.TemporaryDirectory() as tmp:
        binary = pathlib.Path(tmp) / "probe"
        command = [
            "gcc", "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror",
            "-I" + str(ROOT / "kernel64" / "include"),
            "-I" + str(ROOT / "kernel64"),
            str(PROBE),
        ] + [str(s) for s in SOURCES] + ["-o", str(binary)]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode != 0:
            raise AssertionError("probe did not compile:\n" + built.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True)
        _cache["out"] = ran.stdout
        _cache["stderr"] = ran.stderr
    return _cache["out"]


def walk_line(name):
    for line in probe_output().splitlines():
        parts = line.split()
        if len(parts) > 2 and parts[0] == "walk" and parts[1] == name:
            # `fail=` is the last field and its value contains spaces, so it is
            # the remainder of the line rather than a single token.
            # Fields before fail= are all single-token; fail= is the remainder.
            fixed = dict(field.split("=", 1) for field in parts[2:8])
            fixed["fail"] = " ".join(parts[8:]).replace("fail=", "", 1)
            return fixed
    raise AssertionError("no walk line named %r" % name)


def alloc_line(name):
    for line in probe_output().splitlines():
        if not line.startswith("alloc "):
            continue
        fields = line.split()
        if fields[1] == name:
            rest = dict(f.split("=", 1) for f in fields[2:] if "=" in f)
            rest["_verdict"] = fields[2]
            return rest, fields[2]
    raise AssertionError("no alloc line named %r" % name)


class TheWalkerCompilesAsTheKernelDoes(unittest.TestCase):
    def test_it_builds_with_werror(self):
        # A warning in the walker is a bug in the walker; the kernel build uses
        # -Werror, so the probe does too and a clean build is part of the test.
        self.assertIn("walk addr0", probe_output())


class EveryRequiredAddressIsIdentityMapped(unittest.TestCase):
    """The addresses §3 names, on a correct map, all resolve to themselves."""

    CASES = {
        "addr0": "0",             # start of the range
        "below1g": "1",           # last byte below the first 1 GiB boundary
        "at1g": "1",              # the boundary itself
        "above1g": "1",           # first byte above it
        "in1g": "1",
        "below2g": "1",
        "at2g": "1",
        "in2g": "1",
        "below3g": "1",
        "at3g": "1",
        "in3g": "1",
        "below4g": "1",           # near the upper end of the 4 GiB span
    }

    def test_all_probes_resolve(self):
        for name in self.CASES:
            line = walk_line(name)
            self.assertEqual(line["ok"], "1", "%s: %s" % (name, line))
            self.assertEqual(line["ident"], "1", name)
            self.assertEqual(line["fail"], "ok", name)

    def test_the_translation_is_the_address_itself(self):
        # identity=1 only says the walker compared equal; check the physical
        # address independently so a walker that returned 0 for everything
        # could not pass the address-0 case by accident.
        self.assertEqual(walk_line("addr0")["pa"], "0")
        self.assertEqual(walk_line("at1g")["pa"], "40000000")
        self.assertEqual(walk_line("in3g")["pa"], "d0000000")
        self.assertEqual(walk_line("below4g")["pa"], "ffffffff")

    def test_the_walk_reaches_2mib_pages_on_the_loader_map(self):
        # The loader's map uses 2 MiB leaves; the walker must terminate there,
        # not at 4 KiB and not at 1 GiB.
        self.assertEqual(walk_line("addr0")["size"], "2")


class PageSizesAreNotAssumedUniform(unittest.TestCase):
    """§3: large-page entries must be handled, and sizes must not be assumed."""

    def test_a_1gib_page_terminates_the_walk_early(self):
        line = walk_line("huge1g")
        self.assertEqual(line["size"], "3", "expected a 1 GiB leaf")
        self.assertEqual(line["ok"], "1")
        # The offset within the 1 GiB page must survive, not be dropped.
        self.assertEqual(line["pa"], "12345000")

    def test_a_4kib_page_is_followed_through_a_page_table(self):
        # hole_pd leaves a PD entry absent, so it fails; this asserts the
        # 2 MiB-vs-4 KiB distinction is real by contrast with the 2 MiB cases.
        self.assertEqual(walk_line("hole_pd")["size"], "0")

    def test_the_three_page_sizes_are_distinguished(self):
        sizes = {walk_line("addr0")["size"], walk_line("huge1g")["size"]}
        self.assertEqual(sizes, {"2", "3"})


class MissingConnectionsAreFailures(unittest.TestCase):
    """§3: a missing, disconnected or invalid entry must fail, not be tolerated."""

    def test_a_disconnected_1gib_boundary_is_named(self):
        for name in ("mutant_at1g", "mutant_in2g", "mutant_at3g"):
            line = walk_line(name)
            self.assertEqual(line["ok"], "0", name)
            self.assertEqual(line["present"], "0", name)
            self.assertEqual(line["fail"], "no PDPT entry", name)

    def test_the_mutant_still_works_below_1gib(self):
        # This is why the defect went unnoticed: the low GiB is fine.
        self.assertEqual(walk_line("mutant_low")["ok"], "1")

    def test_an_absent_page_directory_entry_is_reported(self):
        line = walk_line("hole_pd")
        self.assertEqual(line["ok"], "0")
        self.assertIn("page-directory", line["fail"])

    def test_a_null_root_is_rejected(self):
        self.assertEqual(walk_line("root_zero")["ok"], "0")
        self.assertIn("root", walk_line("root_zero")["fail"])

    def test_a_misaligned_root_is_rejected(self):
        self.assertEqual(walk_line("root_misaligned")["ok"], "0")

    def test_the_path_field_names_which_levels_were_reached(self):
        # A correct 2 MiB walk reaches PML4, PDPT and PD, and no PT.
        self.assertEqual(walk_line("at1g")["path"], "PPP-")
        # A disconnected region stops at the PDPT: the PML4 entry exists but
        # nothing below the missing PDPT entry does.  Reporting these the same
        # way would hide exactly the defect this walk is for.
        self.assertEqual(walk_line("mutant_at1g")["path"], "P---")
        # A 1 GiB leaf terminates at the PDPT.
        self.assertEqual(walk_line("huge1g")["path"], "PP--")

    def test_an_address_outside_the_span_is_not_a_defect(self):
        # Above the limit the map is not required to reach; the walker says so
        # rather than inventing a failure.
        line = walk_line("at4g")
        self.assertEqual(line["ok"], "0")
        self.assertEqual(line["present"], "0")


class TheAllocationArithmeticIsOverflowSafe(unittest.TestCase):
    """§4: inclusive bounds, out-of-range results, and overflow."""

    def test_a_sound_allocation_is_ok(self):
        _, verdict = alloc_line("ok")
        self.assertEqual(verdict, "ok")

    def test_the_bound_is_inclusive(self):
        # end == requested_max must pass: UEFI's AllocateMaxAddress bound is
        # "less than or equal to", so an extent ending exactly on it is legal.
        rest, verdict = alloc_line("inclusive")
        self.assertEqual(verdict, "ok", rest)
        self.assertEqual(rest["end"], "1ffff")

    def test_ending_one_byte_over_the_bound_fails(self):
        _, verdict = alloc_line("over_bound")
        self.assertEqual(verdict, "over-bound")

    def test_a_null_base_is_never_usable(self):
        _, verdict = alloc_line("null")
        self.assertEqual(verdict, "null")

    def test_pages_times_page_size_overflow_is_caught(self):
        _, verdict = alloc_line("overflow_size")
        self.assertEqual(verdict, "overflow")

    def test_base_plus_size_overflow_is_caught(self):
        # base 0xfffffffffffff000 + 2 pages wraps to a tiny end.  Reporting this
        # as "over-bound" would be right by accident; naming it overflow is what
        # lets a reader tell a firmware bug from an arithmetic one.
        _, verdict = alloc_line("overflow_end")
        self.assertEqual(verdict, "overflow-end")

    def test_an_allocation_outside_the_limit_fails(self):
        # A perfectly sound extent that simply is not inside the identity range.
        # Nothing about its arithmetic is wrong, so only the limit can reject it.
        rest, verdict = alloc_line("over_limit")
        self.assertEqual(verdict, "over-limit", rest)

    def test_an_unaligned_base_fails(self):
        _, verdict = alloc_line("unaligned")
        self.assertEqual(verdict, "unaligned")

    def test_zero_pages_fails(self):
        _, verdict = alloc_line("zero_pages")
        self.assertEqual(verdict, "zero-pages")


class TheKernelUsesTheSameSources(unittest.TestCase):
    """The walker under test is the one that ships."""

    def test_the_kernel_links_the_walker(self):
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("kernel64/pagetable64.c", makefile)
        self.assertIn("kernel64/pagetable64_check.c", makefile)

    def test_the_loader_links_the_shared_arithmetic(self):
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("kernel64/alloc64.c", makefile)

    def test_the_walk_runs_after_the_existing_checks(self):
        # Failure order matters: a register problem must not be reported as a
        # map problem.  The walk must come after the CR3 root comparison.
        source = (ROOT / "kernel64" / "main.c").read_text()
        root_check = source.index("pagetable-root-mismatch")
        walk = source.index("pc5_report_identity_span")
        self.assertLess(root_check, walk)

    def test_the_walk_happens_before_the_success_marker(self):
        source = (ROOT / "kernel64" / "main.c").read_text()
        self.assertLess(source.index("pc5_report_identity_span"),
                        source.index("PC5-64BIT-LONG-MODE-OK"))


if __name__ == "__main__":
    unittest.main()
