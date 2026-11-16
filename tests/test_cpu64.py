"""Controlled-CPUID tests for the 64-bit feature report.

`cpu64_report()` reads CPUID directly, so its leaf-gating logic cannot be checked
against a real CPU: the interesting cases are the ones where the maximum extended
leaf is *below* a leaf the report wants, and a host CPU either implements all of
them or none.  The report is therefore compiled for the host with a table-driven
`cpuid` callback (`cpu64_report_with`), which is the same code the kernel runs -
only the instruction is replaced by a controlled result.

The regression these tests pin down: the 0x80000008 query used to be gated on
`regs[0]` after the 0x80000001 call had overwritten it, so the address-width leaf
was queried whenever that call's EAX happened to be large enough, and skipped when
it was not, regardless of what the CPU actually advertised.
"""
from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
KERNEL64 = ROOT / "kernel64"

# CPUID leaf values used by the report.
L_BASIC_MAX = 0x0D
L_BASIC_FEATURES = 1
L_EXT_MAX = 0x80000000
L_EXT_FEATURES = 0x80000001
L_EXT_ADDR = 0x80000008
L_EXT_TSC = 0x80000007
L_EXT_SVM = 0x8000000A

# A mock table entry: (leaf, subleaf, eax, ebx, ecx, edx).
BASIC_ENTRIES = [
    (0, 0, L_BASIC_MAX, 0x756E6547, 0x6C65746E, 0x49656E69),  # "GenuineIntel"
    (1, 0, 0x000306A4, 0x00100800, 0x7FBAE3BF, 0xBFEBFBFF),
]


def _compile_and_run(entries):
    """Compile the real cpu64.c with a mock CPUID and return its report fields."""
    table = "\n".join(
        "    {%d, %d, 0x%Xu, 0x%Xu, 0x%Xu, 0x%Xu}," % entry for entry in entries)
    source = f"""
#include "include/pc5_boot64.h"
#include "cpu64.h"
#include <stdio.h>
#include <string.h>

static const struct {{ unsigned leaf, subleaf, eax, ebx, ecx, edx; }}
    g_entries[] = {{
{table}
    }};
static unsigned g_queried[64];
static unsigned g_queried_count;

static void mock_cpuid(unsigned leaf, unsigned subleaf, unsigned *out) {{
    unsigned i;
    g_queried[g_queried_count++] = leaf;
    memset(out, 0, 16);
    for (i = 0; i < sizeof(g_entries) / sizeof(g_entries[0]); i++) {{
        if (g_entries[i].leaf == leaf && g_entries[i].subleaf == subleaf) {{
            out[0] = g_entries[i].eax; out[1] = g_entries[i].ebx;
            out[2] = g_entries[i].ecx; out[3] = g_entries[i].edx;
            return;
        }}
    }}
}}

void serial64_write(const char *text) {{ (void)text; }}
void serial64_hex(unsigned long long value) {{ (void)value; }}
void serial64_dec(unsigned long long value) {{ (void)value; }}

int main(void) {{
    struct pc5_cpu64 cpu;
    unsigned i;
    cpu64_report_with(&cpu, mock_cpuid);
    printf("max_basic=%u\\n", cpu.max_basic_leaf);
    printf("max_extended=0x%x\\n", cpu.max_extended_leaf);
    printf("vendor=%s\\n", cpu.vendor);
    printf("family=%u\\n", cpu.family);
    printf("model=%u\\n", cpu.model);
    printf("long_mode=%u\\n", cpu.long_mode);
    printf("nx=%u\\n", cpu.nx);
    printf("pages_1g=%u\\n", cpu.pages_1g);
    printf("svm=%u\\n", cpu.svm);
    printf("svm_npt=%u\\n", cpu.svm_npt);
    printf("svm_asids=%u\\n", cpu.svm_asids);
    printf("phys_bits=%u\\n", cpu.phys_addr_bits);
    printf("virt_bits=%u\\n", cpu.virt_addr_bits);
    printf("invariant_tsc=%u\\n", cpu.invariant_tsc);
    printf("hypervisor=%u\\n", cpu.hypervisor);
    printf("queried=");
    for (i = 0; i < g_queried_count; i++)
        printf("%s0x%x", i ? "," : "", g_queried[i]);
    printf("\\n");
    return 0;
}}
"""
    with tempfile.TemporaryDirectory() as directory:
        source_path = pathlib.Path(directory) / "probe.c"
        source_path.write_text(source)
        binary = pathlib.Path(directory) / "probe"
        compiled = subprocess.run(
            ["gcc", "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror",
             "-I", str(KERNEL64), str(source_path), str(KERNEL64 / "cpu64.c"),
             "-o", str(binary)],
            capture_output=True, text=True)
        assert compiled.returncode == 0, compiled.stderr
        done = subprocess.run([str(binary)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    fields = {}
    for line in done.stdout.splitlines():
        key, _, value = line.partition("=")
        fields[key] = value
    fields["_queried"] = [leaf for leaf in fields["queried"].split(",") if leaf]
    return fields


def _entries(max_extended, ext_features_eax=0, ext_features_ecx=0, ext_features_edx=0,
             addr_eax=0, tsc_edx=0, svm_eax=0, svm_edx=0):
    """A mock CPUID table for one maximum-extended-leaf scenario.

    The optional leaves are entered whenever a caller supplies a distinctive
    result for them, *even when the advertised maximum is below them*: that is what
    makes a wrongful query observable.  A real CPU returns zeros for a leaf above
    its maximum, so the gating is the only thing standing between the report and a
    value it was never entitled to read.
    """
    entries = list(BASIC_ENTRIES)
    entries.append((L_EXT_MAX, 0, max_extended, 0, 0, 0))
    if max_extended >= L_EXT_FEATURES:
        entries.append((L_EXT_FEATURES, 0, ext_features_eax, 0, ext_features_ecx,
                        ext_features_edx))
    if addr_eax:
        entries.append((L_EXT_ADDR, 0, addr_eax, 0, 0, 0))
    if tsc_edx:
        entries.append((L_EXT_TSC, 0, 0, 0, 0, tsc_edx))
    if svm_eax or svm_edx:
        entries.append((L_EXT_SVM, 0, svm_eax, 0, 0, svm_edx))
    return entries


class CpuLeafGatingTests(unittest.TestCase):
    """The maximum extended leaf, and only it, decides which leaves are read."""

    def assert_queried_within(self, fields, max_extended):
        for leaf in fields["_queried"]:
            value = int(leaf, 16)
            self.assertLessEqual(value, max_extended,
                                 f"queried {leaf} above the advertised maximum "
                                 f"0x{max_extended:x}")

    def test_maximum_below_0x80000001_reads_no_feature_leaf(self):
        fields = _compile_and_run(_entries(0x80000000))
        self.assertEqual(fields["max_extended"], "0x80000000")
        self.assertEqual(fields["long_mode"], "0")
        self.assertEqual(fields["nx"], "0")
        self.assertEqual(fields["svm"], "0")
        self.assertEqual(fields["phys_bits"], "0")
        self.assertEqual(fields["virt_bits"], "0")
        self.assertEqual(fields["invariant_tsc"], "0")
        self.assertEqual(fields["_queried"],
                         ["0x0", "0x1", "0x80000000"])
        self.assert_queried_within(fields, 0x80000000)

    def test_maximum_exactly_0x80000001_reads_features_but_no_address_width(self):
        fields = _compile_and_run(_entries(
            0x80000001,
            ext_features_edx=(1 << 29) | (1 << 20) | (1 << 26),
            ext_features_ecx=1 << 2))
        self.assertEqual(fields["long_mode"], "1")
        self.assertEqual(fields["nx"], "1")
        self.assertEqual(fields["pages_1g"], "1")
        self.assertEqual(fields["svm"], "1")
        # 0x80000008 is above the maximum, so the widths stay at their zero
        # "not reported" value rather than being filled from a leaf that was never
        # read.
        self.assertEqual(fields["phys_bits"], "0")
        self.assertEqual(fields["virt_bits"], "0")
        self.assertEqual(fields["_queried"],
                         ["0x0", "0x1", "0x80000000", "0x80000001"])
        self.assert_queried_within(fields, 0x80000001)

    def test_maximum_below_0x80000007_does_not_read_the_invariant_tsc_leaf(self):
        fields = _compile_and_run(_entries(0x80000006, tsc_edx=1 << 8))
        self.assertEqual(fields["invariant_tsc"], "0")
        self.assertNotIn("0x80000007", fields["_queried"])
        self.assert_queried_within(fields, 0x80000006)

    def test_maximum_exactly_0x80000007_reads_the_invariant_tsc_leaf(self):
        fields = _compile_and_run(_entries(0x80000007, tsc_edx=1 << 8))
        self.assertEqual(fields["invariant_tsc"], "1")
        self.assertIn("0x80000007", fields["_queried"])
        self.assert_queried_within(fields, 0x80000007)

    def test_maximum_below_0x80000008_ignores_a_large_feature_leaf_eax(self):
        # This is the regression: the old code tested regs[0] after the 0x80000001
        # call, so a feature leaf whose EAX was >= 0x80000008 caused the
        # address-width leaf to be queried even though the CPU advertised a lower
        # maximum.  The mock deliberately makes that EAX large.
        fields = _compile_and_run(_entries(
            0x80000007, ext_features_eax=0x80000008, addr_eax=36 | (48 << 8)))
        self.assertEqual(fields["max_extended"], "0x80000007")
        self.assertEqual(fields["phys_bits"], "0")
        self.assertEqual(fields["virt_bits"], "0")
        self.assertNotIn("0x80000008", fields["_queried"])
        self.assert_queried_within(fields, 0x80000007)

    def test_maximum_exactly_0x80000008_reports_the_address_widths(self):
        fields = _compile_and_run(_entries(
            0x80000008, ext_features_eax=0x80000008,
            addr_eax=36 | (48 << 8)))
        self.assertEqual(fields["phys_bits"], "36")
        self.assertEqual(fields["virt_bits"], "48")
        self.assertIn("0x80000008", fields["_queried"])
        self.assert_queried_within(fields, 0x80000008)

    def test_maximum_above_0x8000000a_reads_the_svm_leaf_only_when_svm_is_set(self):
        fields = _compile_and_run(_entries(
            0x8000000B, ext_features_ecx=1 << 2, addr_eax=36 | (48 << 8),
            tsc_edx=1 << 8, svm_eax=7, svm_edx=1))
        self.assertEqual(fields["svm"], "1")
        self.assertEqual(fields["svm_asids"], "7")
        self.assertEqual(fields["svm_npt"], "1")
        self.assertEqual(fields["invariant_tsc"], "1")
        self.assertEqual(fields["phys_bits"], "36")
        self.assertIn("0x8000000a", fields["_queried"])
        self.assert_queried_within(fields, 0x8000000B)

    def test_svm_leaf_is_not_read_when_svm_is_clear(self):
        fields = _compile_and_run(_entries(
            0x8000000B, ext_features_ecx=0, addr_eax=36 | (48 << 8),
            svm_eax=7, svm_edx=1))
        self.assertEqual(fields["svm"], "0")
        self.assertEqual(fields["svm_asids"], "0")
        self.assertEqual(fields["svm_npt"], "0")
        self.assertNotIn("0x8000000a", fields["_queried"])

    def test_nx_is_preserved_from_the_feature_leaf(self):
        fields = _compile_and_run(_entries(
            0x80000001, ext_features_edx=(1 << 29) | (1 << 20)))
        self.assertEqual(fields["nx"], "1")
        self.assertEqual(fields["long_mode"], "1")


if __name__ == "__main__":
    unittest.main()
