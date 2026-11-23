"""The emulator's own limit, measured instead of remembered.

`make stubtest64` runs the shipped 64-bit kernel in Unicorn.  That harness can therefore be
used for one of two very different sentences, and the difference is the whole content of this
file:

  * "the IST delivery probe did not run, because the kernel does not need it" -- a claim about
    the kernel, which would be false: `report_tss_ist_probe()` runs whenever TR was loaded; or
  * "the IST delivery probe cannot run here, because Unicorn does not deliver software
    interrupts to a guest IDT at all" -- a claim about the *tool*, which is what this file
    demonstrates, in the only way a claim about a tool can be demonstrated: by running the tool.

Why that distinction is worth a test module.  The previous three milestones of this work carried
a marker reading `tr=not-loaded reason=ltr-unverifiable-here`, whose stated cause was that the
emulator faults on `ltr`.  The emulator did not fault on `ltr`; the kernel was passing the wrong
selector, and the fault was the kernel's own.  The explanation survived because the claim was
never tested against the tool, only against the kernel's expectations -- so the limitation hardened
into architecture and the IDT gates stayed unarmed on purpose.  See
`docs/evidence/2026-11-22-tss-and-ist/ltr-measurement.md` and the erratum at its end.

The test below is deliberately about the *emulator*: no kernel code is consulted for the verdict,
and the guest whose instruction it fails to deliver is the one the build produced, so the site of
the fault is read out of the shipped image rather than hand-written here.  If Unicorn ever learns
to do this, the test fails and the stub gate gains a real IST measurement -- which is the
direction a passing test like this one exists to make impossible to ignore.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

try:
    import unicorn  # noqa: F401
    HAVE_UNICORN = True
except ImportError:
    HAVE_UNICORN = False

REQUIRE_UNICORN = unittest.skipUnless(
    HAVE_UNICORN, "unicorn is not installed (pip install -r requirements-harness.txt)")

#: `int $8`, the only encoding of a software interrupt the 64-bit kernel contains.  Two bytes,
#: and the whole point: the guest reaches this and the emulator decides what an interrupt gate
#: means.
INT_DOUBLE_FAULT = bytes([0xCD, 0x08])


def int_sites(image: bytes):
    """Every offset in the flat image where `int $8` appears, as virtual addresses.

    Reported as a list because the count is itself an assertion: one site is the probe, two
    would mean something else started raising double faults, and zero would mean the probe was
    removed and this file is proving nothing about the kernel any more.
    """
    found = []
    start = 0
    while True:
        at = image.find(INT_DOUBLE_FAULT, start)
        if at < 0:
            return found
        found.append(at)
        start = at + 1


@REQUIRE_UNICORN
class UnicornSoftInterruptTests(unittest.TestCase):
    """What the emulator does when the guest executes `int $8` with a live IDT."""

    @classmethod
    def setUpClass(cls):
        import test_kernel64

        cls.image = test_kernel64.kernel_binary()

    def kernel_base(self):
        """Where the flat image is loaded, read from the harness rather than repeated here.

        The virtual addresses in the log and the offsets in the file differ by exactly this, and
        a test that hard-coded 0x100000 would silently compare the wrong pair the next time the
        load address moves -- which is the same class of drift that made `entry()` read the image
        header instead of assuming.
        """
        import kernel64_machine

        return kernel64_machine.KERNEL_BASE

    def run_to_the_probe(self):
        """Boot the shipped kernel with the stop markers taken away.

        `stop_markers` is what the harness normally uses to end a run at `PC5-64-DONE`; clearing
        it lets execution walk off the end of the boot and into the probe.  Nothing else is
        changed: same memory map, same page tables, same boot info, so the state at the `int` is
        the state a real boot reaches, and a hand-built miniature guest could not have been used
        for this -- it would have proven what my own setup said rather than what the kernel's
        says.
        """
        import kernel64_machine

        # 40 million is the boot's own cost doubled, measured: the run reaches `PC5-64-DONE` in
        # roughly a tenth of that, so a fault reported here is the interrupt and not the
        # instruction ceiling.  Stating the budget is the point -- an unbounded "no fault" would
        # otherwise be indistinguishable from a guest that simply never got there.
        return kernel64_machine.run_kernel(self.image, stop_markers=(), max_instructions=40_000_000)

    def test_the_kernel_contains_exactly_one_software_interrupt(self):
        sites = int_sites(self.image)
        self.assertEqual(len(sites), 1,
                         "expected the IST delivery probe's single `int $8` in %s, found %d at "
                         "%s; this test is only evidence about a kernel with exactly one"
                         % (test_kernel64_bin_name(), len(sites),
                            [hex(self.kernel_base() + s) for s in sites]))

    def test_the_emulator_refuses_to_deliver_it_and_says_so_at_the_instruction(self):
        """The measured fact: a guest `int $8` is not delivered, and the fault is *at* the int.

        Two halves, and the second is what makes the first usable.  "Unicorn reported an
        exception" would be a claim about anything at all -- the kernel could have faulted for a
        reason of its own, as it did once before for exactly that reason.  Matching the reported
        RIP against the address of the `int $8` in the shipped image says the emulator got as far
        as the instruction and then had no way in: the guest never reached an IDT handler, so the
        refusal is in the interrupt path, not in the TSS, the GDT, or the selectors.

        Unicorn reports the RIP *after* the two-byte instruction (the front-end has already
        advanced when the exception is raised); QEMU's own guest reports the instruction itself.
        Both offsets are accepted, and the pair is checked rather than one of them so that the
        test does not encode a coincidence of version 2.1.4.
        """
        result = self.run_to_the_probe()
        site = self.kernel_base() + int_sites(self.image)[0]
        error = result["error"]
        self.assertIsNotNone(
            error,
            "the guest ran past `int $8` with no fault: the emulator now delivers software "
            "interrupts, so the stub gate has an IST measurement available and every note that "
            "says it cannot must be rewritten (see tests/boot64_stubtest.py and §30)")
        self.assertIn("UC_ERR_EXCEPTION", error,
                      "the run failed for a reason other than an undeliverable interrupt: %r"
                      % error)
        rip = result["fault_rip"]
        self.assertIsNotNone(rip, "the harness reported a fault without an address")
        self.assertIn(rip, (site, site + len(INT_DOUBLE_FAULT)),
                      "the fault was at %#x, not at the probe's `int $8` at %#x -- so the "
                      "refusal this test reports is not about the interrupt path" % (rip, site))
        # And the handler never ran: the probe prints its line only after the trap returns, and
        # the emulator stopped the guest before that.  A `status=` in the serial here would mean
        # the refusal above was reached some other way than by not delivering.
        self.assertNotIn("PC5-64-TSS-DF status=", result["serial"],
                         "the emulator both refused the interrupt and produced a verdict")

    def test_the_boot_itself_is_unaffected_by_the_probe_being_unreachable(self):
        """Everything up to `PC5-64-DONE` still happens, so the stub's 49 checks are honest.

        The probe is emitted after the marker for exactly this reason; if a later change moves it
        back, the stub run dies before the marker and every check that follows goes red for an
        environmental reason.  This is the tripwire that makes that move cost something.
        """
        result = self.run_to_the_probe()
        serial = result["serial"]
        self.assertIn("PC5-64-DONE", serial)
        self.assertLess(serial.index("PC5-64-DONE"),
                        serial.index("PC5-64-TSS-DF") if "PC5-64-TSS-DF" in serial else
                        len(serial),
                        "the probe line appeared before the marker, so a run that dies at the "
                        "probe loses the marker too")


def test_kernel64_bin_name():
    import test_kernel64

    return str(test_kernel64.KERNEL_BIN.relative_to(ROOT))


if __name__ == "__main__":
    unittest.main()
