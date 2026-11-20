"""Local APIC bring-up tests for the 64-bit path (`M2a`).

`apic64_bring_up()` talks to MMIO, so it cannot be exercised against the real APIC
from a host test: no writing SVR on the machine's own interrupt controller from a
unit test.  The register window is injected instead - the idiom
`cpu64_report_with()` already established for CPUID - and the **real**
`kernel64/apic64.c` is compiled and run against a mock register file, so the code
under test is the code the kernel runs, with only the bus replaced.

The properties, in the order the driver's own comment claims them:

1. A window that does not answer is refused, and refused *before* anything is
   written to it.
2. The enable bit is written, and a write that does not stick is a refusal rather
   than a silent success.
3. ESR is read and cleared rather than read and forgotten.
4. The ordering holds: TPR and ESR are settled before SVR is enabled, so the
   controller is never made live in an unknown state.
5. Reported fields are derived from registers, not guessed (ID from bits 24..31,
   `max_lvt` from VERSION bits 16..23 plus one).

Each test names the mutation it is designed to catch.
"""
from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
KERNEL64 = ROOT / "kernel64"

# Restated rather than imported, so a change to the header's numbering is caught by
# these tests instead of silently followed.
ID, VERSION, TPR, SVR = 0x020, 0x030, 0x080, 0x0F0
ESR = 0x280
SVR_ENABLE = 1 << 8
SVR_RESET = 0x000000FF

DEFAULT_REGS = {ID: 3 << 24, VERSION: 0x00050014, TPR: 0, ESR: 0, SVR: SVR_RESET}

# apic64.c also carries apic64_print(); the port is stubbed rather than emulated,
# since printing is covered by the kernel build itself, not here.
SERIAL_STUB = """
#include "include/pc5_boot64.h"
void serial64_write(const char *text) { (void)text; }
void serial64_hex(unsigned long long value) { (void)value; }
void serial64_dec(unsigned long long value) { (void)value; }
"""


def _compile(source, tmp):
    """Compile a test translation unit together with the real apic64.c."""
    src = pathlib.Path(tmp) / "t.c"
    stub = pathlib.Path(tmp) / "serial_stub.c"
    exe = pathlib.Path(tmp) / "t"
    src.write_text(source)
    stub.write_text(SERIAL_STUB)
    subprocess.run(
        ["cc", "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror",
         f"-I{KERNEL64}", str(src), str(stub), str(KERNEL64 / "apic64.c"),
         "-o", str(exe)],
        check=True, capture_output=True)
    return exe


def _run(regs=None, *, read_returns=None, svr_sticks=True):
    """Run the real driver against a mock register file; return its output."""
    regs = dict(DEFAULT_REGS if regs is None else regs)
    table = "\n".join(f"    {{0x{off:03X}u, 0x{val:08X}u}}," for off, val in regs.items())
    overrides = "\n".join(
        f"    if (offset == 0x{off:03X}u) return 0x{val:08X}u;"
        for off, val in (read_returns or {}).items())

    source = f"""
#include "include/pc5_boot64.h"
#include "apic64.h"
#include <stdio.h>

static struct {{ unsigned off, val; }} g_regs[] = {{
{table}
    }};
#define NREGS (sizeof g_regs / sizeof g_regs[0])

static unsigned g_writes[64];
static unsigned g_write_vals[64];
static unsigned g_write_count;

static unsigned *slot(unsigned offset) {{
    unsigned i;
    for (i = 0; i < NREGS; i++)
        if (g_regs[i].off == offset)
            return &g_regs[i].val;
    return 0;
}}

static unsigned mock_read(void *ctx, unsigned offset) {{
    unsigned *p;
    (void)ctx;
{overrides}
    p = slot(offset);
    return p ? *p : 0xFFFFFFFFu;
}}

static void mock_write(void *ctx, unsigned offset, unsigned value) {{
    unsigned *p;
    (void)ctx;
    if (g_write_count < 64) {{
        g_writes[g_write_count] = offset;
        g_write_vals[g_write_count] = value;
        g_write_count++;
    }}
    if (offset == 0x{ESR:03X}u) {{
        p = slot(offset);          /* write-one-to-clear */
        if (p) *p &= ~value;
        return;
    }}
    if (offset == 0x{SVR:03X}u && !{1 if svr_sticks else 0})
        return;
    p = slot(offset);
    if (p) *p = value;
}}

int main(void) {{
    struct pc5_apic64 a;
    const char *why = apic64_bring_up(&a, mock_read, mock_write, 0);
    unsigned i;

    printf("why=%s\\n", why ? why : "OK");
    printf("present=%u\\n", a.present);
    printf("enabled=%u\\n", a.enabled);
    printf("id=%u\\n", a.id);
    printf("version=%u\\n", a.version);
    printf("max_lvt=%u\\n", a.max_lvt);
    printf("svr=%u\\n", a.svr);
    printf("error_status=%u\\n", a.error_status);
    for (i = 0; i < g_write_count; i++)
        printf("write=%u:%u\\n", g_writes[i], g_write_vals[i]);
    return 0;
}}
"""
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run([str(_compile(source, tmp))], check=True,
                             capture_output=True, text=True).stdout

    result = {"writes": [], "why": None}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        if key == "write":
            off, _, val = value.partition(":")
            result["writes"].append((int(off), int(val)))
        elif key == "why":
            result["why"] = None if value == "OK" else value
        else:
            result[key] = int(value)
    return result


class WindowProbeTests(unittest.TestCase):
    def test_unbacked_window_is_refused(self):
        """MUTATION: dropping the all-ones/zero check would report present=1."""
        r = _run(read_returns={VERSION: 0xFFFFFFFF})
        self.assertIsNotNone(r["why"])
        self.assertEqual(r["present"], 0)
        self.assertEqual(r["enabled"], 0)

    def test_zero_version_is_refused(self):
        r = _run(read_returns={VERSION: 0})
        self.assertIsNotNone(r["why"])
        self.assertEqual(r["enabled"], 0)

    def test_nothing_is_written_when_the_window_does_not_answer(self):
        """MUTATION: moving the TPR/SVR writes before the probe would fail here."""
        r = _run(read_returns={VERSION: 0xFFFFFFFF})
        self.assertEqual(r["writes"], [])


class EnableTests(unittest.TestCase):
    def test_enable_bit_is_set(self):
        r = _run()
        svr_writes = [v for off, v in r["writes"] if off == SVR]
        self.assertEqual(len(svr_writes), 1)
        self.assertTrue(svr_writes[0] & SVR_ENABLE)

    def test_enable_that_does_not_stick_is_refused(self):
        """MUTATION: dropping the SVR read-back would report enabled=1 here.

        This is also the test that caught the reset constant being 0x1FF: with the
        enable bit already set in the written value, a controller that ignores the
        write still reads back as enabled.
        """
        r = _run(svr_sticks=False)
        self.assertEqual(r["enabled"], 0)
        self.assertIsNotNone(r["why"])
        self.assertIn("spurious-vector", r["why"] or "")

    def test_successful_bring_up_reports_ready(self):
        r = _run()
        self.assertIsNone(r["why"])
        self.assertEqual(r["present"], 1)
        self.assertEqual(r["enabled"], 1)


class ErrorStatusTests(unittest.TestCase):
    def test_clean_esr_reports_zero_and_is_not_written(self):
        r = _run()
        self.assertEqual(r["error_status"], 0)
        self.assertEqual([v for off, v in r["writes"] if off == ESR], [])

    def test_stale_esr_is_reported_and_cleared(self):
        """MUTATION: reading ESR without clearing it leaves the write list empty."""
        r = _run(regs={**DEFAULT_REGS, ESR: 0x05})
        self.assertEqual(r["error_status"], 0x05)
        self.assertEqual([v for off, v in r["writes"] if off == ESR], [0x05])

    def test_esr_write_is_masked_to_the_writable_bits(self):
        r = _run(regs={**DEFAULT_REGS, ESR: 0xFF})
        self.assertEqual([v for off, v in r["writes"] if off == ESR], [0xEF])


class OrderingTests(unittest.TestCase):
    def test_tpr_and_esr_are_settled_before_svr_is_enabled(self):
        """The controller must never be made live in an unknown state."""
        r = _run(regs={**DEFAULT_REGS, ESR: 0x05})
        offsets = [off for off, _ in r["writes"]]
        self.assertIn(TPR, offsets)
        self.assertIn(ESR, offsets)
        self.assertLess(offsets.index(TPR), offsets.index(SVR))
        self.assertLess(offsets.index(ESR), offsets.index(SVR))

    def test_tpr_is_cleared_to_zero(self):
        r = _run(regs={**DEFAULT_REGS, TPR: 0x70})
        self.assertEqual([v for off, v in r["writes"] if off == TPR], [0])


class DerivedFieldTests(unittest.TestCase):
    def test_id_comes_from_the_high_byte(self):
        r = _run(regs={**DEFAULT_REGS, ID: 0x2A << 24})
        self.assertEqual(r["id"], 0x2A)

    def test_max_lvt_is_version_bits_16_to_23_plus_one(self):
        r = _run()  # VERSION 0x00050014 -> 5 + 1
        self.assertEqual(r["max_lvt"], 6)

    def test_max_lvt_does_not_leak_the_revision_byte(self):
        """MUTATION: unmasked (version >> 16) would report 0xFFFF here.

        VERSION[31:24] is the implementation revision and is often non-zero, so an
        unmasked shift folds it in.  A revision byte of zero cannot tell the
        difference, which is why this case uses 0xFF.
        """
        r = _run(regs={**DEFAULT_REGS, VERSION: 0xFF1F0014})
        self.assertEqual(r["max_lvt"], 0x1F + 1)


class RefusalContractTests(unittest.TestCase):
    def test_null_output_is_refused_not_crashed(self):
        source = """
#include "include/pc5_boot64.h"
#include "apic64.h"
#include <stdio.h>
static unsigned r(void *c, unsigned o) { (void)c; (void)o; return 0x14; }
static void w(void *c, unsigned o, unsigned v) { (void)c; (void)o; (void)v; }
int main(void) {
    const char *why = apic64_bring_up(0, r, w, 0);
    printf("%s\\n", why ? why : "OK");
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([str(_compile(source, tmp))], check=True,
                                 capture_output=True, text=True).stdout
        self.assertNotIn("OK", out.strip())

    def test_missing_accessors_are_refused(self):
        source = """
#include "include/pc5_boot64.h"
#include "apic64.h"
#include <stdio.h>
int main(void) {
    struct pc5_apic64 a;
    const char *why = apic64_bring_up(&a, 0, 0, 0);
    printf("%s enabled=%u\\n", why ? why : "OK", a.enabled);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([str(_compile(source, tmp))], check=True,
                                 capture_output=True, text=True).stdout
        self.assertIn("enabled=0", out)
        self.assertNotIn("OK", out.split("enabled")[0])


if __name__ == "__main__":
    unittest.main()
