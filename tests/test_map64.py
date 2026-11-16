"""Host-compiled tests for the shared 64-bit map and geometry helpers.

These compile the real `kernel64/include/pc5_map.h` and call the real static
inline functions.  They are not a Python re-implementation of the arithmetic: a
change to the header changes what these tests execute, which is the point - the
loader and the kernel both include this header, so a defect here is a defect in
both.
"""
from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HEADER = ROOT / "kernel64" / "include"

PROLOGUE = '#include "pc5_boot64.h"\n#include "pc5_map.h"\n#include <stdio.h>\n'


def run_probe(body: str) -> str:
    source = PROLOGUE + body
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "probe.c"
        path.write_text(source)
        binary = pathlib.Path(directory) / "probe"
        compiled = subprocess.run(
            ["gcc", "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror",
             "-I", str(HEADER), str(path), "-o", str(binary)],
            capture_output=True, text=True)
        assert compiled.returncode == 0, compiled.stderr
        done = subprocess.run([str(binary)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    return done.stdout


class MultiplyOverflowTests(unittest.TestCase):
    def test_checked_multiplication_matches_the_boundary(self):
        cases = [
            (0, 0, "ok 0"),
            (1, 1, "ok 1"),
            (1, 0xFFFFFFFFFFFFFFFF, "ok ffffffffffffffff"),
            (0xFFFFFFFFFFFFFFFF, 1, "ok ffffffffffffffff"),
            (0xFFFFFFFFFFFFFFFF, 2, "overflow"),
            (0x8000000000000000, 2, "overflow"),
            (0x8000000000000000, 1, "ok 8000000000000000"),
            (0x4000000000000000, 4, "overflow"),
            (0x4000000000000000, 2, "ok 8000000000000000"),
            (0x100000000, 0x100000000, "overflow"),
            (0x100000000, 0xFFFFFFFF, "ok ffffffff00000000"),
            (3, 0x5555555555555555, "ok ffffffffffffffff"),
            (3, 0x5555555555555556, "overflow"),
        ]
        lines = "\n".join(
            f"    {{ unsigned long long out = 0; int bad = pc5_mul_overflows("
            f"{left}ULL, {right}ULL, &out); if (bad) printf(\"overflow\\n\"); "
            f"else printf(\"ok %llx\\n\", out); }}"
            for left, right, _ in cases)
        output = run_probe(f"int main(void){{\n{lines}\n    return 0;\n}}\n")
        expected = []
        for _left, _right, text in cases:
            expected.extend(text.split())
        self.assertEqual(output.split(), expected)


class FramebufferGeometryTests(unittest.TestCase):
    def test_line_pitch_is_checked_and_bounded_by_the_protocol_field(self):
        cases = [
            (0, "reject"),
            (1, "ok 4"),
            (0x3FFFFFFF, "ok fffffffc"),
            (0x40000000, "reject"),          # 4 * 2^30 = 2^32, does not fit u32
            (0x40000001, "reject"),
            (0xFFFFFFFF, "reject"),
        ]
        lines = "\n".join(
            f"    {{ unsigned pitch = 0; if (pc5_framebuffer_line({pixels}, "
            f"&pitch)) printf(\"ok %x\\n\", pitch); else printf(\"reject\\n\"); }}"
            for pixels, _ in cases)
        output = run_probe(f"int main(void){{\n{lines}\n    return 0;\n}}\n")
        expected = []
        for _pixels, text in cases:
            expected.extend(text.split())
        self.assertEqual(output.split(), expected)

    def test_byte_size_rejects_zero_dimensions_and_short_pitch(self):
        cases = [
            # width, height, pitch, expected
            (1024, 768, 4096, "ok 300000"),
            (1024, 768, 4095, "reject"),
            (1024, 768, 4092, "reject"),
            (1024, 768, 0, "reject"),
            (0, 768, 4096, "reject"),
            (1024, 0, 4096, "reject"),
            (1, 0xFFFFFFFF, 4, "ok 3fffffffc"),
            (1, 0xFFFFFFFF, 0xFFFFFFFF, "ok fffffffe00000001"),
            (0xFFFFFFFF, 1, 0xFFFFFFFF, "reject"),   # pitch < width * 4
        ]
        lines = "\n".join(
            f"    {{ unsigned long long bytes = 0; if (pc5_framebuffer_bytes("
            f"{width}, {height}, {pitch}, &bytes)) printf(\"ok %llx\\n\", bytes); "
            f"else printf(\"reject\\n\"); }}"
            for width, height, pitch, _ in cases)
        output = run_probe(f"int main(void){{\n{lines}\n    return 0;\n}}\n")
        expected = []
        for *_dims, text in cases:
            expected.extend(text.split())
        self.assertEqual(output.split(), expected)


class PixelFormatTests(unittest.TestCase):
    def test_only_the_two_direct_colour_layouts_are_supported(self):
        cases = [(0, "ok"), (1, "ok"), (2, "reject"), (3, "reject"),
                 (4, "reject"), (0xFFFFFFFF, "reject")]
        lines = "\n".join(
            f"    printf(\"%s\\n\", pc5_pixel_format_supported({value}) "
            f"? \"ok\" : \"reject\");"
            for value, _ in cases)
        output = run_probe(f"int main(void){{\n{lines}\n    return 0;\n}}\n")
        self.assertEqual(output.split(), [expected for _v, expected in cases])


class AddressWidthTests(unittest.TestCase):
    def test_a_missing_width_is_not_treated_as_unlimited(self):
        body = """
int main(void){
    unsigned widths[] = {0, 31, 32, 36, 48, 52, 53};
    unsigned long long addresses[] = {0xFFFFFFFFull, 0x100000000ull,
                                      0xFFFFFFFFFull, 0x1000000000ull,
                                      0x1000000000000ull};
    unsigned i, j;
    for (i = 0; i < sizeof(widths)/sizeof(widths[0]); i++) {
        printf("width %u reported=%u", widths[i],
               pc5_physical_width_reported(widths[i]) ? 1 : 0);
        for (j = 0; j < sizeof(addresses)/sizeof(addresses[0]); j++)
            printf(" %u", pc5_address_fits_width(addresses[j], widths[i]) ? 1 : 0);
        printf("\\n");
    }
    return 0;
}
"""
        output = run_probe(body)
        lines = output.strip().splitlines()
        self.assertEqual(len(lines), 7)
        # 0 and 31 are not reported: nothing fits, because nothing was proven.
        self.assertEqual(lines[0].split(),
                         ["width", "0", "reported=0", "0", "0", "0", "0", "0"])
        self.assertEqual(lines[1].split(),
                         ["width", "31", "reported=0", "0", "0", "0", "0", "0"])
        # 32 bits covers [0, 2^32): 0xFFFFFFFF fits, 2^32 does not.
        self.assertEqual(lines[2].split(),
                         ["width", "32", "reported=1", "1", "0", "0", "0", "0"])
        # 36 bits covers [0, 2^36): 2^32-1, 2^32 and 2^36-1 fit; 2^36 does not.
        self.assertEqual(lines[3].split(),
                         ["width", "36", "reported=1", "1", "1", "1", "0", "0"])
        # 48 bits covers [0, 2^48); 52 bits covers [0, 2^52).
        self.assertEqual(lines[4].split(),
                         ["width", "48", "reported=1", "1", "1", "1", "1", "0"])
        self.assertEqual(lines[5].split(),
                         ["width", "52", "reported=1", "1", "1", "1", "1", "1"])
        # 53 is outside the architectural range the kernel accepts.
        self.assertEqual(lines[6].split(),
                         ["width", "53", "reported=0", "0", "0", "0", "0", "0"])


class OverlapTests(unittest.TestCase):
    def test_pairwise_overlap_detection(self):
        body = """
static void report(unsigned long long a0, unsigned long long a1,
                   unsigned long long b0, unsigned long long b1) {
    printf("%u\\n", pc5_ranges_overlap(a0, a1, b0, b1) ? 1 : 0);
}
int main(void){
    report(0, 0x1000, 0x1000, 0x2000);          /* adjacent: no */
    report(0, 0x1001, 0x1000, 0x2000);          /* one byte shared: yes */
    report(0, 0x4000, 0x1000, 0x2000);          /* nested: yes */
    report(0x1000, 0x2000, 0, 0x4000);          /* nested, reversed: yes */
    report(0x1000, 0x2000, 0x1000, 0x2000);     /* duplicate: yes */
    report(0, 0x1000, 0x2000, 0x3000);          /* disjoint: no */
    report(~0ull - 0xfff, ~0ull, ~0ull - 0xfff, ~0ull);  /* top of space: yes */
    report(0, 0x1000, ~0ull - 0xfff, ~0ull);    /* far apart: no */
    return 0;
}
"""
        output = run_probe(body)
        self.assertEqual(output.split(),
                         ["0", "1", "1", "1", "1", "0", "1", "0"])

    def _map_probe(self, descriptors):
        entries = ",\n".join(
            f"        {{{start}ULL, {pages}ULL, 0, {kind}, 0}}"
            for start, pages, kind in descriptors)
        count = len(descriptors)
        body = f"""
static const struct pc5_memdesc64 map[] = {{
{entries}
}};
int main(void){{
    printf("%s\\n", pc5_map_overlaps(map, {count}) ? "overlap" : "clean");
    printf("%s\\n", pc5_map_validate(map, {count}) ? "valid" : "invalid");
    return 0;
}}
"""
        return run_probe(body).split()

    def test_adjacent_and_unsorted_valid_maps_are_accepted(self):
        descriptors = [
            (0x500000, 0x20, 2),      # page tables, listed first
            (0x000000, 0x9F, 7),      # low conventional
            (0x100000, 0x100, 2),     # kernel window
            (0x200000, 0x100, 11),    # framebuffer, adjacent to the window
            (0x300000, 0x20, 7),      # adjacent again, conventional
        ]
        self.assertEqual(self._map_probe(descriptors), ["clean", "valid"])

    def test_same_type_overlap_is_rejected(self):
        descriptors = [
            (0x100000, 0x100, 7),
            (0x108000, 0x100, 7),     # partial overlap, same type
        ]
        self.assertEqual(self._map_probe(descriptors), ["overlap", "invalid"])

    def test_conflicting_types_overlap_is_rejected(self):
        descriptors = [
            (0x100000, 0x100, 7),     # conventional
            (0x100000, 0x100, 2),     # loader data, same bytes
        ]
        self.assertEqual(self._map_probe(descriptors), ["overlap", "invalid"])

    def test_duplicate_descriptor_is_rejected(self):
        descriptors = [
            (0x100000, 0x100, 7),
            (0x100000, 0x100, 7),
        ]
        self.assertEqual(self._map_probe(descriptors), ["overlap", "invalid"])

    def test_nested_range_is_rejected(self):
        descriptors = [
            (0x100000, 0x100, 7),
            (0x100000 + 0x1000, 0x10, 2),
        ]
        self.assertEqual(self._map_probe(descriptors), ["overlap", "invalid"])

    def test_arithmetic_boundary_ranges_are_rejected(self):
        descriptors = [
            (~0 - 0xFFF, 0x10, 7),    # wraps 64 bits: invalid on its own
            (0x100000, 0x100, 7),
        ]
        self.assertEqual(self._map_probe(descriptors), ["clean", "invalid"])


if __name__ == "__main__":
    unittest.main()
