"""PIT-calibrated TSC tests for the 64-bit path (`M2a`).

`timer64_init()` reads the PIT through port I/O and reads the TSC with `rdtsc`, so a
host test cannot call it directly: `inb` on a test process touches whatever the
machine has on that port, and the TSC cannot be made to advance by a controlled
amount. Both are therefore injected - `PC5_TIMER64_NO_CPUID` plus the `-D` shims
below - and the **real** `kernel64/timer64.c` is compiled and run against them.

What is worth testing here is not "does a TSC read return a number". It is the
properties that decide whether the kernel reports a trustworthy rate or a confident
wrong one:

1. A PIT that never responds leaves calibration **unset**, rather than turning a
   capped spin count into a bogus frequency.
2. `timer64_ms()` returns 0 when uncalibrated, not a made-up elapsed time.
3. The rate is an average over all samples, not one sample - a single sample is
   dominated by the cost of the port writes.
4. `tsc_per_us`, which is an integer, is allowed to be 0 for a slow clock instead of
   rounding up and overstating resolution.
5. A TSC reading below the boot value (a migrate to another core, a reset, a warp)
   yields 0 rather than a huge wrapped number.
"""
from __future__ import annotations

import pathlib
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
KERNEL64 = ROOT / "kernel64"

SERIAL_STUB = """
#include "include/pc5_boot64.h"
void serial64_write(const char *text) { (void)text; }
void serial64_hex(unsigned long long value) { (void)value; }
void serial64_dec(unsigned long long value) { (void)value; }
"""


def _run(*, spin_behaviour="ok", tsc_advance="per_outb", tsc_per_ms=2_500_000,
         invariant=0):
    """Compile the real timer64.c against injected port I/O and a synthetic TSC.

    `spin_behaviour`:
      "ok"        every PIT poll succeeds after a few spins
      "absent"    the gate never rises, so every poll hits the cap
      "one-bad"   the last sample hits the cap, the others succeed

    `tsc_advance`:
      "per_outb"  the fake clock advances one millisecond's worth per port write,
                  which is what programming the PIT divisor does
      "per_read"  it advances on every TSC read, a different arithmetic path
    """
    if spin_behaviour == "absent":
        spin_body = "    return 40000u;"
    elif spin_behaviour == "one-bad":
        spin_body = ("    static unsigned n; n++;\n"
                     "    return (n >= 20u) ? 2000000u : 7u;")
    else:
        spin_body = "    return 7u;"

    advance_on_outb = "1" if tsc_advance == "per_outb" else "0"
    advance_on_read = "1" if tsc_advance == "per_read" else "0"

    source = (
        '#include "include/pc5_boot64.h"\n'
        '#include "timer64.h"\n'
        '#include <stdio.h>\n'
        '\n'
        'static unsigned long long g_tsc = 1000000ull;\n'
        f'static unsigned long long g_step = {tsc_per_ms}ull;\n'
        '\n'
        'static void maybe_advance(int on) { if (on) g_tsc += g_step; }\n'
        '\n'
        # A PIT that behaves: once the divisor is programmed (port 0x42 writes),
        # port 0x61 bit 5 reads as "count expired". Returning 0 unconditionally - as
        # the first version of this mock did - models a gate that never rises, which
        # the driver correctly refuses to calibrate against.
        'static int g_armed;\n'
        'static int g_divisor_writes;\n'
        'static long g_polls;\n'
        f'static const int g_never_fires = {1 if spin_behaviour == "absent" else 0};\n'
        f'static const int g_dies_late = {1 if spin_behaviour == "one-bad" else 0};\n'
        'static unsigned char hw_inb(unsigned short port) {\n'
        '    (void)port;\n'
        '    g_polls++;\n'
        '    if (g_never_fires) return 0x00;\n'
        # The driver polls ~2000000 times per sample when the gate never rises, so
        # "dies late" means: after the first 19 samples' worth of polls, stay low.
        # Each successful sample costs a handful of polls (the gate rises on the
        # first one after the divisor is written), so "dies late" is keyed on the
        # sample number, not a poll count: after 19 samples, stop answering.
        '    if (g_dies_late && g_divisor_writes >= 19 * 2) return 0x00;\n'
        '    g_armed = 1;\n'
        '    return 0x20;\n'
        '}\n'
        # One millisecond elapses per *completed* sample, not per port write: each
        # `pit_wait_1ms()` performs four writes (gate, mode, divisor low, divisor
        # high). Advancing per write would make the measured rate 4x too high and
        # the test would then be asserting the mock's arithmetic, not the driver's.
        'static void hw_outb(unsigned short port, unsigned char v) {\n'
        '    (void)v;\n'
        f'    if ({advance_on_outb} && port == 0x42) {{ g_divisor_writes++;\n'
        '        if (g_divisor_writes % 2 == 0) maybe_advance(1); }\n'
        '}\n'
        'static unsigned long long hw_rdtsc(void) {\n'
        f'    maybe_advance({advance_on_read});\n'
        '    return g_tsc;\n'
        '}\n'
        f'static unsigned hw_invariant(void) {{ return {invariant}u; }}\n'
        '\n'
        'int main(void) {\n'
        '    struct pc5_timer64 t;\n'
        '    struct pc5_timer64_io io = { hw_inb, hw_outb, hw_rdtsc, hw_invariant };\n'
        '\n'
        '    timer64_init_with(&t, &io);\n'
        '    printf("calibrated=%u\\n", t.calibrated);\n'
        '    printf("pit_present=%u\\n", t.pit_present);\n'
        '    printf("invariant_tsc=%u\\n", t.invariant_tsc);\n'
        '    printf("tsc_per_ms=%u\\n", t.tsc_per_ms);\n'
        '    printf("tsc_per_us=%u\\n", t.tsc_per_us);\n'
        '    printf("spins=%u\\n", t.spins);\n'
        '    printf("boot_tsc_nonzero=%u\\n", t.boot_tsc != 0);\n'
        '    printf("ms_at_boot=%llu\\n", timer64_ms(&t, t.boot_tsc));\n'
        '    printf("ms_after_2s=%llu\\n", timer64_ms(&t, t.boot_tsc + 2000ull *'
        ' (unsigned long long)t.tsc_per_ms));\n'
        '    printf("ms_before_boot=%llu\\n", timer64_ms(&t, t.boot_tsc - 1ull));\n'
        '    printf("ms_null_timer=%llu\\n", timer64_ms(0, 12345ull));\n'
        '    return 0;\n'
        '}\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        src = tmp / "t.c"
        stub = tmp / "serial_stub.c"
        exe = tmp / "t"
        src.write_text(source)
        stub.write_text(SERIAL_STUB)
        subprocess.run(
            ["cc", "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror",
             f"-I{KERNEL64}", str(src), str(stub), str(KERNEL64 / "timer64.c"),
             "-o", str(exe)],
            check=True, capture_output=True)
        out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout

    return {line.split("=")[0]: int(line.split("=")[1])
            for line in out.splitlines() if "=" in line}


class CalibrationTests(unittest.TestCase):
    def test_a_responding_pit_produces_a_rate(self):
        r = _run(tsc_per_ms=2_500_000)
        self.assertEqual(r["calibrated"], 1)
        self.assertEqual(r["pit_present"], 1)
        # 20 samples of 2.5M ticks each, averaged: the per-sample advance is 2.5M.
        self.assertEqual(r["tsc_per_ms"], 2_500_000)

    def test_the_rate_is_an_average_not_one_sample(self):
        """MUTATION: dividing by 1 instead of PC5_PIT_SAMPLES gives 50M here."""
        r = _run(tsc_per_ms=2_500_000)
        self.assertNotEqual(r["tsc_per_ms"], 50_000_000)
        self.assertEqual(r["tsc_per_ms"], 2_500_000)

    def test_tsc_per_us_is_integer_division_not_rounded_up(self):
        """MUTATION: rounding up overstates the resolution of every later reading."""
        r = _run(tsc_per_ms=1_500)          # 1500 ticks/ms -> 1.5 ticks/us -> 1
        self.assertEqual(r["tsc_per_us"], 1)

    def test_a_pit_that_responds_but_measures_zero_is_not_calibrated(self):
        """A responding PIT whose samples show no TSC movement yields rate 0.

        MUTATION: setting `calibrated = 1` outside the non-zero-rate guard reports a
        usable clock with a zero rate, and every later `timer64_ms()` is then silently
        0 - a clock that looks fine and measures nothing. Reachable on a platform that
        virtualises the TSC to a constant.
        """
        r = _run(tsc_per_ms=0)
        self.assertEqual(r["pit_present"], 1, "the PIT did respond")
        self.assertEqual(r["tsc_per_ms"], 0)
        self.assertEqual(r["calibrated"], 0,
                         "a zero measured rate must not be reported as calibrated")

    def test_a_slow_clock_reports_zero_microseconds_rather_than_lying(self):
        """999 ticks/ms is under one tick per microsecond; 0 is the honest answer."""
        r = _run(tsc_per_ms=999)
        self.assertEqual(r["tsc_per_us"], 0)
        self.assertEqual(r["calibrated"], 1)


class AbsentPitTests(unittest.TestCase):
    def test_an_absent_pit_returns_instead_of_spinning_forever(self):
        """MUTATION: removing the spin cap makes `timer64_init_with()` never return.

        A hang here is the worst failure this code can have: the machine stops during
        boot with no output at all, and no later diagnostic can run. The cap is what
        converts that into a reported refusal, so the property worth asserting is
        that the call *terminates* - hence a wall-clock bound rather than a value
        check.
        """
        start = time.monotonic()
        r = _run(spin_behaviour="absent")
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 60.0,
                        "calibration with an absent PIT did not return; the spin cap "
                        "is missing or ineffective")
        self.assertEqual(r["calibrated"], 0)

    def test_an_absent_pit_leaves_calibration_unset(self):
        """MUTATION: removing the spin cap makes this hang instead of returning.

        A hang during calibration is the failure mode that costs most to diagnose,
        because the machine simply stops with no output.
        """
        r = _run(spin_behaviour="absent")
        self.assertEqual(r["calibrated"], 0)
        self.assertEqual(r["pit_present"], 0)
        self.assertEqual(r["tsc_per_ms"], 0)
        # Exactly the total budget: an absent PIT costs one bounded wait, not one per
        # sample. A per-sample cap made this 20x larger, which is minutes under
        # emulation and happens precisely in the case meant to fail fast.
        self.assertEqual(r["spins"], 40_000)

    def test_an_absent_pit_yields_zero_elapsed_time_not_a_guess(self):
        """MUTATION: returning a raw delta with no rate gives a huge wrong number."""
        r = _run(spin_behaviour="absent")
        self.assertEqual(r["ms_at_boot"], 0)
        self.assertEqual(r["ms_after_2s"], 0)

    def test_calibration_requires_a_nonzero_measured_rate(self):
        """MUTATION: setting `calibrated = 1` alongside `pit_present` reports success
        with a zero rate, and every later elapsed-time reading silently becomes 0."""
        r = _run(spin_behaviour="absent")
        self.assertEqual(r["pit_present"], 0,
                         "an unresponsive PIT must not be reported as present")
        self.assertEqual(r["calibrated"], 0)
        # And a zero rate must not be presented as a usable clock.
        self.assertEqual(r["tsc_per_ms"], 0)

    def test_a_pit_that_dies_mid_calibration_is_not_averaged_in(self):
        """One failed sample must abort, not contribute a bogus interval."""
        r = _run(spin_behaviour="one-bad")
        self.assertEqual(r["calibrated"], 0)
        self.assertEqual(r["pit_present"], 0)


class ElapsedTimeTests(unittest.TestCase):
    def test_elapsed_ms_is_computed_from_the_measured_rate(self):
        r = _run(tsc_per_ms=2_500_000)
        self.assertEqual(r["ms_at_boot"], 0)
        self.assertEqual(r["ms_after_2s"], 2000)

    def test_a_tsc_below_the_boot_value_yields_zero_not_a_wrap(self):
        """MUTATION: `(now - boot) / rate` on unsigned 64-bit wraps to ~7e16 ms if
        the underflow is not rejected first."""
        r = _run(tsc_per_ms=2_500_000)
        self.assertEqual(r["ms_before_boot"], 0)

    def test_a_null_timer_is_not_dereferenced(self):
        r = _run(tsc_per_ms=2_500_000)
        self.assertEqual(r["ms_null_timer"], 0)


if __name__ == "__main__":
    unittest.main()
