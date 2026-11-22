"""Tests for the experimental 64-bit foundation.

Four things are checked here, and they are deliberately independent:

  * the build: the kernel is really ELF64 at the address it is linked for, and
    the loader is a valid PE32+ EFI application;
  * the protocol: `struct pc5_boot64` has the size and offsets both sides were
    compiled against, checked against a host build of the same header;
  * (page-table construction and bounds live in tests/test_pagetables64.py)
  * the kernel's own contract: valid boot info boots, and every kind of invalid
    boot info stops the kernel *without* the success marker.

When the 32-bit suite runs, none of this replaces it: `TestReleaseIsolation`
proves the release image still does not depend on any of it.
"""
from __future__ import annotations

import ctypes
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

try:  # the emulator module the machine harness needs; absent in a plain clone
    import unicorn  # noqa: F401
    HAVE_UNICORN = True
except ImportError:
    HAVE_UNICORN = False

#: Decorator, not a `setUpClass` raise: a class skipped from `setUpClass` collapses
#: into one summary entry and makes the suite look smaller instead of showing the same
#: tests as skipped.  The reason is attached to each test, and it is never a pass.
REQUIRE_UNICORN = unittest.skipUnless(
    HAVE_UNICORN, "unicorn is not installed (pip install -r requirements-harness.txt)")
ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
KERNEL_ELF = BUILD / "kernel64" / "pc5-64.elf"
KERNEL_BIN = BUILD / "kernel64" / "KERNEL64.BIN"
LOADER_EFI = BUILD / "uefi64" / "BOOTX64.EFI"

MARKER = "PC5-64BIT-LONG-MODE-OK"
MASK64 = (1 << 64) - 1

# A controlled CPUID table for the kernel-contract tests: leaf -> (eax, ebx, ecx,
# edx).  It advertises long mode and the 0x80000008 address-width leaf, which the
# emulator's own CPUID does not.
CPUID_LONG_MODE = {
    0: (0x0D, 0x756E6547, 0x6C65746E, 0x49656E69),
    1: (0x000306A4, 0x00100800, 0x7FBAE3BF, 0xBFEBFBFF),
    0x80000000: (0x80000008, 0, 0, 0),
    0x80000001: (0, 0, 0, (1 << 29)),
    0x80000008: (36 | (48 << 8), 0, 0, 0),
}

_CACHE = {}


def build(force=False):
    """Build the 64-bit artifacts once per test run."""
    if not force and _CACHE.get("built"):
        return
    for tool in ("gcc", "ld", "objcopy"):
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            raise unittest.SkipTest(f"{tool} is not installed")
    result = subprocess.run(["make", "-s", "kernel64", "uefi64"],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"64-bit build failed:\n{result.stdout}\n{result.stderr}")
    _CACHE["built"] = True


def kernel_binary():
    build()
    return KERNEL_BIN.read_bytes()


def loader_image():
    build()
    return LOADER_EFI.read_bytes()


class KernelImageTests(unittest.TestCase):
    def test_kernel_is_elf64_for_x86_64(self):
        build()
        header = KERNEL_ELF.read_bytes()[:64]
        self.assertEqual(header[:4], b"\x7fELF")
        self.assertEqual(header[4], 2, "ELFCLASS64")
        self.assertEqual(header[5], 1, "little endian")
        self.assertEqual(struct.unpack_from("<H", header, 18)[0], 62, "EM_X86_64")
        self.assertEqual(struct.unpack_from("<H", header, 16)[0], 2, "ET_EXEC")
        entry = struct.unpack_from("<Q", header, 24)[0]
        self.assertEqual(entry, 0x100000, "entry must be the linked load address")

    def test_kernel_has_no_unresolved_symbols(self):
        build()
        result = subprocess.run(["nm", "-u", str(KERNEL_ELF)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "", "freestanding image links nothing")

    def test_kernel_binary_starts_with_its_entry_point(self):
        build()
        binary = KERNEL_BIN.read_bytes()
        text = subprocess.run(["objcopy", "-O", "binary", "--only-section=.text",
                               str(KERNEL_ELF), "/dev/stdout"],
                              capture_output=True).stdout
        self.assertTrue(binary.startswith(text[:4096]))
        # Derived from the linker script's own symbols, not hardcoded. It used to
        # say 0xC000, and that number stopped being true the moment the kernel
        # gained a 4 KiB IDT - the test then failed for a correct change, which is a
        # defect in the test. The invariant that matters is that the flat binary
        # spans exactly __kernel_start..__bss_end, because .bss holds the stack and
        # must travel with the image.
        symbols = subprocess.run(["nm", str(KERNEL_ELF)],
                                 capture_output=True, text=True).stdout
        addresses = {}
        for line in symbols.splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[2] in ("__kernel_start", "__bss_end"):
                addresses[fields[2]] = int(fields[0], 16)
        self.assertEqual(set(addresses), {"__kernel_start", "__bss_end"},
                         "the linker script no longer defines the span symbols")
        span = addresses["__bss_end"] - addresses["__kernel_start"]
        self.assertEqual(len(binary), span,
                         "the binary must span __kernel_start..__bss_end")
        self.assertGreaterEqual(span, 0x8000, "the 32 KiB stack window vanished")

    def test_link_address_is_the_protocol_address(self):
        build()
        nm = subprocess.run(["nm", str(KERNEL_ELF)], capture_output=True, text=True).stdout
        self.assertIn("0000000000100000 T __kernel_start", nm)
        self.assertIn("0000000000100000 T _start64", nm)


class BootInfoAbiTests(unittest.TestCase):
    def test_struct_size_and_offsets_match_the_documented_protocol(self):
        build()
        source = ROOT / "kernel64" / "include" / "pc5_boot64.h"
        probe = (
            '#include "pc5_boot64.h"\n'
            "#include <stdio.h>\n"
            "#include <stddef.h>\n"
            "int main(void){printf(\"%zu %zu %zu %zu %zu %zu %zu %zu %zu %zu %zu %zu\\n\",\n"
            " sizeof(struct pc5_boot64), sizeof(struct pc5_memdesc64),\n"
            " offsetof(struct pc5_boot64, memmap_addr),\n"
            " offsetof(struct pc5_boot64, framebuffer_addr),\n"
            " offsetof(struct pc5_boot64, acpi_rsdp),\n"
            " offsetof(struct pc5_boot64, kernel_base),\n"
            " offsetof(struct pc5_boot64, stack_top),\n"
            " offsetof(struct pc5_boot64, stack_size),\n"
            " offsetof(struct pc5_boot64, pagetable_addr),\n"
            " offsetof(struct pc5_boot64, identity_span),\n"
            " offsetof(struct pc5_boot64, loader_image_addr),\n"
            " offsetof(struct pc5_boot64, exit_boot_services));\n"
            " return 0;}\n")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "probe.c"
            path.write_text(probe)
            binary = pathlib.Path(directory) / "probe"
            compile_result = subprocess.run(
                ["gcc", "-std=gnu11", "-I", str(source.parent), str(path),
                 "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            output = subprocess.run([str(binary)], capture_output=True, text=True).stdout
        values = [int(x) for x in output.split()]
        self.assertEqual(values, [160, 32, 16, 40, 64, 72, 88, 96, 104, 120, 128, 144])

    def test_python_packer_agrees_with_the_header(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import kernel64_machine as machine
        self.assertEqual(len(machine.pack_boot_info()), 160)
        self.assertEqual(len(machine.pack_memmap(machine.DEFAULT_MEMMAP)),
                         32 * len(machine.DEFAULT_MEMMAP))


class LoaderImageTests(unittest.TestCase):
    def test_efi_image_passes_the_efi_checker(self):
        build()
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "eficheck.py"),
                                 str(LOADER_EFI), "--require-relocs",
                                 "--max-bytes", "196608"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_handover_record_states_the_protocol_it_emits(self):
        image = loader_image()
        pe = struct.unpack_from("<I", image, 0x3C)[0]
        sections = struct.unpack_from("<H", image, pe + 6)[0]
        optional_size = struct.unpack_from("<H", image, pe + 20)[0]
        table = pe + 24 + optional_size
        record = None
        for index in range(sections):
            name, virtual_size, virtual_address, raw_size, raw_pointer = \
                struct.unpack_from("<8sIIII", image, table + index * 40)
            if name.rstrip(b"\0") == b".data":
                record = image[raw_pointer:raw_pointer + 40]
        self.assertIsNotNone(record, "no .data section in the loader image")
        self.assertEqual(record[:8], b"PC5HND02")
        protocol, boot_info_size, pages, span = struct.unpack_from("<4Q", record, 8)
        self.assertEqual(protocol, 2)
        self.assertEqual(boot_info_size, 160)
        self.assertEqual(pages, 6)
        self.assertEqual(span, 4 << 30)


@REQUIRE_UNICORN
class KernelContractTests(unittest.TestCase):
    """Every failure path, driven through the kernel's real entry point."""

    @classmethod
    def setUpClass(cls):
        build()
        sys.path.insert(0, str(ROOT / "tests"))
        # `kernel64_machine` imports unicorn inside its functions, so importing the
        # module is not a test of the dependency and this guard was vacuous: with the
        # optional package absent every test in the class errored instead of
        # skipping.  Ask about the module the machinery actually needs.
        try:
            import unicorn  # noqa: F401
            import kernel64_machine  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(f"unicorn is not installed: {exc}")
        cls.machine_module = kernel64_machine
        cls.kernel = KERNEL_BIN.read_bytes()

    def run_kernel(self, **kwargs):
        return self.machine_module.run_kernel(self.kernel, **kwargs)

    def assert_boots(self, result):
        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(result["stopped_on"], "PC5-64-DONE")
        self.assertIn(MARKER, result["serial"])
        self.assertNotIn("FAIL=", result["serial"])
        self.assertTrue(result["serial"].rstrip().endswith("PC5-64-DONE"))

    def assert_refuses(self, result, reason):
        self.assertIsNone(result["error"], result["error"])
        self.assertIn(f"PC5-64BIT-FAIL={reason}", result["serial"])
        self.assertNotIn(MARKER, result["serial"],
                         "a kernel that refuses the handover must not print the marker")

    def test_valid_handover_boots_and_prints_the_marker_last(self):
        result = self.run_kernel()
        self.assert_boots(result)
        serial = result["serial"]
        self.assertLess(serial.index("PC5-64BIT-KERNEL-ENTRY"), serial.index(MARKER))
        for line in ("PC5-64-PAGING enabled=1", "aligned=16", "wide-operands=ok",
                     "PC5-64-CPU vendor="):
            self.assertIn(line, serial)

    def test_marker_follows_the_checks_not_the_banner(self):
        serial = self.run_kernel()["serial"]
        order = ["PC5-64BIT-KERNEL-ENTRY", "PC5-64-PAGING enabled=1",
                 "PC5-64-REGS", "PC5-64-STACK", "PC5-64-BOOTINFO magic=ok",
                 "PC5-64-CPU vendor=", MARKER, "PC5-64-DONE"]
        positions = [serial.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))

    def test_paging_disabled_stops_the_kernel(self):
        self.assert_refuses(self.run_kernel(paging=False), "paging-disabled")

    def test_bad_magic_is_refused(self):
        self.assert_refuses(self.run_kernel(magic=0xDEADBEEF), "bootinfo-magic")

    def test_unknown_version_is_refused(self):
        # The kernel accepts exactly the version it was compiled for.  Version 1 is
        # now the *old* protocol - it had no stack size, no page-table description
        # and no identity span, so it cannot be validated and must be refused just
        # like any other version the kernel does not know.
        self.assert_refuses(self.run_kernel(version=1), "bootinfo-version")
        self.assert_refuses(self.run_kernel(version=3), "bootinfo-version")

    def test_wrong_size_is_refused(self):
        self.assert_refuses(self.run_kernel(size=104), "bootinfo-size")

    def test_missing_memory_map_is_refused(self):
        self.assert_refuses(self.run_kernel(memmap_addr=0), "bootinfo-memmap-null")

    def test_misaligned_memory_map_is_refused(self):
        self.assert_refuses(self.run_kernel(memmap_addr=self.machine_module.MEMMAP + 4),
                            "bootinfo-memmap-misaligned")

    def test_wrong_descriptor_size_is_refused(self):
        self.assert_refuses(self.run_kernel(memmap_desc_size=48),
                            "bootinfo-memmap-descsize")

    def test_empty_descriptor_is_refused(self):
        # A descriptor with no pages describes nothing; accepting it would mean
        # silently dropping a slot of the map and miscounting everything after it.
        # The kernel's own reason for this is bootinfo-memmap-descriptor, because the
        # check is "is this descriptor self-consistent", not "is the map empty"
        # (bootinfo-memmap-empty is the case where the map has no entries at all).
        memmap = [(0, 0, 7)] + list(self.machine_module.DEFAULT_MEMMAP[1:])
        self.assert_refuses(self.run_kernel(memmap=memmap),
                            "bootinfo-memmap-descriptor")

    def test_no_conventional_memory_is_refused(self):
        memmap = [(0, 0x9F, 2), (0x100000, 0x100, 2)]
        self.assert_refuses(self.run_kernel(memmap=memmap),
                            "bootinfo-no-conventional-memory")

    def test_kernel_loaded_anywhere_else_is_refused(self):
        self.assert_refuses(self.run_kernel(kernel_base=0x200000),
                            "bootinfo-kernel-address")

    def test_misaligned_stack_is_refused(self):
        self.assert_refuses(self.run_kernel(stack_top=self.machine_module.STACK_TOP + 8),
                            "bootinfo-stack")

    def test_kernel_refuses_a_handover_without_a_stack(self):
        # A zero stack is a broken loader, and the kernel says so instead of
        # borrowing firmware's stack and losing the evidence.
        self.assert_refuses(self.run_kernel(stack_top=0), "bootinfo-stack")

    # ---------------------------------------------------- version 2 contract ----
    # The boot info now describes the stack, the page tables, the identity span and
    # the loader's own image, and the kernel proves each of them before it
    # dereferences anything.  Each test below breaks exactly one of those.

    def test_stack_size_is_validated(self):
        for size, reason in ((0, "bootinfo-stack-size"),
                             (0x1001, "bootinfo-stack-size"),
                             (1 << 21, "bootinfo-stack-size")):
            with self.subTest(stack_size=size):
                self.assert_refuses(self.run_kernel(stack_size=size), reason)

    def test_stack_that_underflows_is_refused(self):
        self.assert_refuses(self.run_kernel(stack_top=0x1000, stack_size=0x2000),
                            "bootinfo-stack-underflow")

    def test_stack_outside_the_span_is_refused(self):
        self.assert_refuses(self.run_kernel(stack_top=5 << 30, stack_size=0x2000),
                            "bootinfo-stack-span")

    def test_stack_in_memory_the_map_calls_unusable_is_refused(self):
        # 0x60000000 is inside the span but the map does not describe it at all.
        self.assert_refuses(self.run_kernel(stack_top=0x60000000, stack_size=0x2000),
                            "bootinfo-stack-range")

    def test_page_table_description_is_validated(self):
        for override, reason in (
                (dict(pagetable_addr=self.machine_module.PML4 + 8), "bootinfo-pagetable"),
                (dict(pagetable_pages=2), "bootinfo-pagetable-pages"),
                (dict(pagetable_pages=515), "bootinfo-pagetable-pages")):
            with self.subTest(**override):
                self.assert_refuses(self.run_kernel(**override), reason)

    def test_page_tables_at_the_top_of_the_span_are_refused(self):
        self.assert_refuses(
            self.run_kernel(pagetable_addr=(4 << 30) - 0x1000, pagetable_pages=6),
            "bootinfo-pagetable-span")

    def test_identity_span_must_match_the_tables_that_were_built(self):
        # 6 pages cannot describe 2 GiB: (pages-2)*512*2 MiB is the only capacity
        # those pages have, and the kernel recomputes it rather than trusting the
        # number it was handed.
        self.assert_refuses(self.run_kernel(identity_span=2 << 30),
                            "bootinfo-pagetable-span-mismatch")

    def test_identity_span_above_the_limit_is_refused(self):
        self.assert_refuses(self.run_kernel(identity_span=8 << 30,
                                            pagetable_pages=6),
                            "bootinfo-pagetable-span-mismatch")

    def test_loader_image_outside_the_map_is_refused(self):
        self.assert_refuses(self.run_kernel(loader_image_addr=0x60000000,
                                            loader_image_size=0x1000),
                            "bootinfo-loader-range")

    def test_loader_image_that_wraps_is_refused(self):
        self.assert_refuses(self.run_kernel(loader_image_addr=MASK64 - 0xFFF,
                                            loader_image_size=0x2000),
                            "bootinfo-loader-span")

    def test_kernel_size_that_does_not_match_the_image_is_refused(self):
        # The kernel knows its own size from the linker script, so it rejects any
        # other value outright - which is also what makes the span check below it
        # unreachable in practice (the linked image cannot leave the map).
        self.assert_refuses(self.run_kernel(kernel_size=4 << 30),
                            "bootinfo-kernel-size")

    def test_memory_map_size_must_be_a_multiple_of_the_stride(self):
        self.assert_refuses(self.run_kernel(memmap_bytes=100),
                            "bootinfo-memmap-bytes")

    def test_descriptor_that_wraps_is_refused(self):
        memmap = [(MASK64 - 0xFFF, 2, 7)] + list(self.machine_module.DEFAULT_MEMMAP[1:])
        self.assert_refuses(self.run_kernel(memmap=memmap),
                            "bootinfo-memmap-descriptor")

    def test_descriptor_with_an_impossible_type_is_refused(self):
        memmap = [(0x100000, 0x100, 99)] + list(self.machine_module.DEFAULT_MEMMAP[1:])
        self.assert_refuses(self.run_kernel(memmap=memmap),
                            "bootinfo-memmap-descriptor")

    # ------------------------------------------- optional resources (stage 4) ---
    # A framebuffer or RSDP that cannot be validated is reported as unsupported and
    # the boot continues: neither is dereferenced, and neither is called usable.

    def test_framebuffer_crossing_the_span_is_unsupported_not_fatal(self):
        result = self.run_kernel(framebuffer_addr=(4 << 30) - 0x1000)
        self.assert_boots(result)
        self.assertIn("PC5-64-FB", result["serial"])
        self.assertIn("usability=unsupported reason=outside-span", result["serial"])

    def test_framebuffer_above_the_span_is_never_called_usable(self):
        result = self.run_kernel(framebuffer_addr=4 << 30)
        self.assert_boots(result)
        self.assertNotIn("PC5-64-FB addr=0x100000000 width=1024 height=768 pitch=4096 usability=ok",
                         result["serial"])
        self.assertIn("usability=unsupported reason=outside-span", result["serial"])

    def test_framebuffer_with_a_short_pitch_is_unsupported(self):
        result = self.run_kernel(framebuffer_pitch=4)
        self.assert_boots(result)
        self.assertIn("usability=unsupported reason=pitch-too-small", result["serial"])

    def test_empty_framebuffer_is_unsupported(self):
        result = self.run_kernel(framebuffer_width=0)
        self.assert_boots(result)
        self.assertIn("usability=unsupported reason=empty", result["serial"])

    def test_huge_framebuffer_is_classified_not_dereferenced(self):
        # 2 GiB of pitch times 64 rows is 128 GiB: inside 64-bit arithmetic but far
        # outside the map, so it must be reported, not used.
        result = self.run_kernel(framebuffer_pitch=0x80000000,
                                 framebuffer_height=0x40)
        self.assert_boots(result)
        self.assertIn("usability=unsupported reason=outside-span", result["serial"])

    def test_rsdp_without_the_signature_is_invalid(self):
        result = self.run_kernel(rsdp_signature=b"NOPE NOPE")
        self.assert_boots(result)
        self.assertIn("PC5-64-RSDP addr=", result["serial"])
        self.assertIn("usability=invalid reason=signature", result["serial"])

    def test_rsdp_outside_the_map_is_invalid(self):
        result = self.run_kernel(acpi_rsdp=0x60000000)
        self.assert_boots(result)
        self.assertIn("usability=invalid reason=not-firmware-memory",
                      result["serial"])

    def test_a_loader_that_says_a_resource_is_unmapped_is_believed(self):
        # The loader zeroes the address and sets the flag when it cannot map the
        # resource.  The kernel must report that, not invent an address.
        flags = (0xF & ~0x2) | 0x10              # clear FB, set UNMAPPED_FRAMEBUFFER
        result = self.run_kernel(flags=flags, framebuffer_addr=0)
        self.assert_boots(result)
        self.assertIn("PC5-64-FB usability=unsupported reason=unmapped-by-loader",
                      result["serial"])

    def test_conventional_memory_is_accepted_for_loader_owned_ranges(self):
        # The loader's own allocations may still be typed Conventional (firmware is
        # not obliged to retype), so the kernel must accept them - but only for
        # ranges the loader owns, never for the framebuffer.
        memmap = [
            (0x00000000, 0x9F, 7),
            (0x00100000, 0x320, 7),
            (0x00500000, 0x020, 7),
            (self.machine_module.FB_BASE, self.machine_module.FB_SIZE // 4096, 11),
        ]
        self.assert_boots(self.run_kernel(memmap=memmap))

    # -------------------------------------------------- memory-map overlaps ----
    # Every descriptor was already checked for internal consistency; these tests
    # break the property of the *set*.  A map that assigns one physical address to
    # two owners cannot be used to decide what is safe, so the kernel refuses it
    # before it validates the kernel image, stack, page tables, framebuffer or
    # RSDP against it.

    def test_overlapping_descriptors_are_refused_before_any_coverage(self):
        base = list(self.machine_module.DEFAULT_MEMMAP)
        cases = [
            ("conflicting types", base + [(0x180000, 0x10, 7)]),
            ("same type", base + [(0x180000, 0x10, 2)]),
            ("duplicate", base + [base[1]]),
            ("nested", base + [(0x180000, 0x100, 2)]),
            ("partial", base + [(0x410000, 0x10, 2)]),
        ]
        for name, memmap in cases:
            with self.subTest(name=name):
                self.assert_refuses(self.run_kernel(memmap=memmap),
                                    "bootinfo-memmap-overlap")

    def test_adjacent_and_unsorted_valid_maps_are_accepted(self):
        memmap = list(reversed(self.machine_module.DEFAULT_MEMMAP))
        memmap.append((0x420000, 0x10, 2))   # adjacent to the kernel window
        self.assert_boots(self.run_kernel(memmap=memmap))

    # --------------------------------------------------- framebuffer format ----
    # The protocol records the firmware's pixel-format value.  Only the two direct
    # colour layouts can be interpreted from the structure alone; a bit-mask layout
    # needs masks the structure does not carry and PixelBltOnly has no linear
    # buffer, so both are unusable rather than usable.

    def test_framebuffer_format_is_validated(self):
        for fmt, reason in ((0, None), (1, None), (2, "unsupported-format"),
                            (3, "unsupported-format"), (4, "unsupported-format")):
            with self.subTest(format=fmt):
                result = self.run_kernel(framebuffer_format=fmt)
                self.assert_boots(result)
                if reason is None:
                    self.assertIn("PC5-64-FB addr=0x4000000 width=1024 height=768 "
                                  "pitch=4096 usability=ok", result["serial"])
                else:
                    self.assertIn(f"usability=unsupported reason={reason}",
                                  result["serial"])

    def test_a_loader_that_reports_an_unsupported_format_is_believed(self):
        flags = (0xF & ~0x2) | 0x40     # clear FB, set FRAMEBUFFER_UNSUPPORTED
        result = self.run_kernel(flags=flags, framebuffer_addr=0)
        self.assert_boots(result)
        self.assertIn("PC5-64-FB usability=unsupported reason=unsupported-by-loader",
                      result["serial"])

    # --------------------------------------------------------- CPUID report ----
    # The kernel's address-width check is driven by CPUID.  These runs replace the
    # emulator's CPUID with a controlled table, so the value the kernel reports and
    # the value its environment check uses are the same, observed one.

    def test_cpuid_reported_address_width_reaches_the_environment_check(self):
        result = self.run_kernel(cpuid=CPUID_LONG_MODE)
        self.assert_boots(result)
        self.assertIn("PC5-64-CPU phys_bits=36", result["serial"])
        self.assertIn("PC5-64-CPU virt_bits=48", result["serial"])
        self.assertIn("PC5-64-ENV physical-address-width=36 checked=1",
                      result["serial"])

    def test_a_missing_address_width_is_reported_not_assumed(self):
        cpuid = dict(CPUID_LONG_MODE)
        cpuid[0x80000000] = (0x80000001, 0, 0, 0)
        cpuid.pop(0x80000008, None)
        result = self.run_kernel(cpuid=cpuid)
        self.assert_boots(result)
        self.assertIn("PC5-64-CPU phys_bits=0", result["serial"])
        self.assertIn("PC5-64-ENV physical-address-width=not-reported checked=0",
                      result["serial"])
        self.assertNotIn("physical-address-width=36", result["serial"])

    def test_cpuid_invariant_tsc_is_reported(self):
        cpuid = dict(CPUID_LONG_MODE)
        cpuid[0x80000000] = (0x80000007, 0, 0, 0)
        cpuid[0x80000007] = (0, 0, 0, 1 << 8)
        result = self.run_kernel(cpuid=cpuid)
        self.assert_boots(result)
        self.assertIn("PC5-64-CPU invariant_tsc=1", result["serial"])


class PlacementPolicyTests(unittest.TestCase):
    """The loader's own placement rule, evaluated at the boundary.

    `require_below_span()` in the loader and every range check in the kernel call
    the same function, `pc5_range_within()` from kernel64/include/pc5_map.h.  The
    stub firmware can place the application at the top of the map (see
    tests/test_boot64_stub.py), but it cannot place it *above* the map and still
    execute it faithfully - a PE32+ image carries 32-bit relocations, so above
    4 GiB the emulated code would no longer be the code under test.  The rejection
    side is therefore checked here, against the function itself.
    """

    def _decisions(self, cases):
        header = ROOT / "kernel64" / "include"
        lines = ",\n".join("{ %sULL, %sULL }" % (base, size) for base, size in cases)
        probe = (
            '#include "pc5_boot64.h"\n'
            '#include "pc5_map.h"\n'
            "#include <stdio.h>\n"
            "static const unsigned long long cases[][2] = {\n%s\n};\n"
            "int main(void){\n"
            "  unsigned i;\n"
            "  for (i = 0; i < sizeof(cases)/sizeof(cases[0]); i++)\n"
            "    printf(\"%%s\\n\", pc5_range_within(cases[i][0], cases[i][1],\n"
            "                            PC5_IDENTITY_LIMIT) ? \"ok\" : \"reject\");\n"
            "  return 0;}\n" % lines)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "probe.c"
            path.write_text(probe)
            binary = pathlib.Path(directory) / "probe"
            compiled = subprocess.run(["gcc", "-std=gnu11", "-Wall", "-Wextra",
                                       "-Werror", "-I", str(header), str(path),
                                       "-o", str(binary)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            done = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.split()

    def test_placement_at_and_around_the_top_of_the_span(self):
        limit = 4 << 30
        cases = [
            (0x100000, 0x1000),            # ordinary: below the map
            (limit - 0x1000, 0x1000),      # ends exactly at the top: still mapped
            (limit, 0x1000),               # starts at the top: not mapped
            (limit - 0x1000, 0x2000),      # crosses the top
            (limit + 0x1000, 0x1000),      # entirely above
            (0, 0),                        # empty range
            (MASK64 - 0xFFF, 0x2000),       # wraps 64-bit
            (MASK64, 1),                    # last byte: inside no map
        ]
        self.assertEqual(self._decisions(cases),
                         ["ok", "ok", "reject", "reject", "reject", "reject",
                          "reject", "reject"])


class ReleaseIsolationTests(unittest.TestCase):
    """The experimental path must not leak into what ships."""

    def test_dry_run_of_the_release_build_does_not_build_64_bit_artifacts(self):
        result = subprocess.run(["make", "-n", "all"], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("kernel64", result.stdout)
        self.assertNotIn("uefi64", result.stdout)

    def test_release_iso_target_has_no_64_bit_prerequisites(self):
        makefile = (ROOT / "Makefile").read_text()
        self.assertNotIn("KERNEL64", makefile.split("iso: $(ISO)")[0])
        self.assertNotIn("\tkernel64", makefile.split("iso: $(ISO)")[0])

    def test_the_two_boot_paths_are_separate_sources(self):
        self.assertTrue((ROOT / "boot" / "uefi" / "uefi_main.c").exists(),
                        "the 32-bit UEFI loader must still be present")
        self.assertTrue((ROOT / "boot" / "uefi64" / "uefi64_main.c").exists())
        self.assertTrue((ROOT / "kernel" / "main.c").exists(),
                        "the 32-bit kernel must still be present")
        self.assertTrue((ROOT / "kernel64" / "main.c").exists())

@REQUIRE_UNICORN
class RsdpValidationTests(unittest.TestCase):
    """Revision-aware RSDP validation, driven through the real kernel.

    The kernel used to require only that 36 bytes be inside the identity span and
    covered by firmware memory, then compare eight signature bytes.  It read no
    checksum at all, so a corrupt table was reported `usability=ok`.  These tests
    pin the structure rules that replaced that:

      * legacy checksum over the first 20 bytes, always;
      * ``length`` validated before anything past offset 20 is read;
      * extended checksum over the declared length, revision 2 and later only;
      * the whole declared structure - not just its header - inside the span and
        inside firmware-owned memory;
      * revision 0 never summed past 20 bytes, because those bytes are not its own.

    The machine maps the RSDP page and the default map describes it as MMIO, so
    "not-firmware-memory" cases come from overriding the map, not from the buffer
    being unmapped - an unmapped read would fault the emulator instead of testing
    a check.
    """

    @classmethod
    def setUpClass(cls):
        build()
        sys.path.insert(0, str(ROOT / "tests"))
        # `kernel64_machine` imports unicorn inside its functions, so importing the
        # module is not a test of the dependency and this guard was vacuous: with the
        # optional package absent every test in the class errored instead of
        # skipping.  Ask about the module the machinery actually needs.
        try:
            import unicorn  # noqa: F401
            import kernel64_machine  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(f"unicorn is not installed: {exc}")
        cls.machine_module = kernel64_machine
        cls.kernel = KERNEL_BIN.read_bytes()

    def run_kernel(self, **kwargs):
        return self.machine_module.run_kernel(self.kernel, **kwargs)

    def rsdp(self, **kwargs):
        return self.machine_module.build_rsdp(**kwargs)

    def assert_boots(self, result):
        self.assertIsNone(result["error"], result["error"])
        self.assertEqual(result["stopped_on"], "PC5-64-DONE")
        self.assertIn(MARKER, result["serial"])
        self.assertNotIn("FAIL=", result["serial"])
        self.assertTrue(result["serial"].rstrip().endswith("PC5-64-DONE"))

    def assert_rsdp_line(self, result, expected):
        self.assertIsNone(result["error"], result["error"])
        lines = [line.strip() for line in result["serial"].splitlines()
                 if "PC5-64-RSDP" in line]
        self.assertTrue(lines, f"no RSDP line in {result['serial']!r}")
        self.assertIn(expected, lines[-1], lines[-1])
        return lines[-1]

    # ------------------------------------------------- 1. valid legacy -------
    def test_a_valid_legacy_rsdp_passes_the_legacy_checksum_only(self):
        result = self.run_kernel(rsdp_bytes=self.rsdp(revision=0, length=36))
        self.assert_boots(result)
        line = self.assert_rsdp_line(result, "usability=ok")
        self.assertIn("checks=signature,legacy-checksum", line)
        self.assertNotIn("extended-checksum", line,
                         "a revision-0 table has no extended checksum to verify")
        self.assertIn("tables=not-validated", line,
                      "the report must not imply the XSDT was validated")

    # ------------------------------------- 2. invalid legacy checksum -------
    def test_an_invalid_legacy_checksum_is_reported_and_the_boot_continues(self):
        result = self.run_kernel(rsdp_bytes=self.rsdp(revision=0,
                                                      legacy_checksum=0x5A))
        self.assert_boots(result)
        self.assert_rsdp_line(result, "usability=invalid reason=legacy-checksum")

    # ---------------------------------------- 3. valid revision 2 ------------
    def test_a_valid_revision_2_rsdp_passes_both_checksums(self):
        result = self.run_kernel(rsdp_bytes=self.rsdp(revision=2, length=36))
        self.assert_boots(result)
        line = self.assert_rsdp_line(result, "usability=ok")
        self.assertIn("legacy-checksum", line)
        self.assertIn("extended-checksum", line)
        self.assertIn("length", line)

    # --------------------------------------- 4. invalid extended checksum ----
    def test_an_invalid_extended_checksum_is_reported(self):
        result = self.run_kernel(rsdp_bytes=self.rsdp(revision=2, length=36,
                                                      extended_checksum=0x33))
        self.assert_boots(result)
        line = self.assert_rsdp_line(result,
                                     "usability=invalid reason=extended-checksum")
        self.assertNotIn("usability=ok", line)

    # ------------------------------------------- 5. invalid declared length --
    def test_a_declared_length_below_the_structure_minimum_is_refused(self):
        tall = self.rsdp(revision=2, length=36)
        for bad in (0, 4, 20, 32, 35):
            with self.subTest(length=bad):
                table = bytearray(tall)
                table[20:24] = bad.to_bytes(4, "little")
                # Fix the legacy checksum so the failure is the length and not it.
                table[8] = 0
                table[8] = (-sum(table[0:20])) & 0xFF
                result = self.run_kernel(rsdp_bytes=bytes(table))
                self.assert_boots(result)
                self.assert_rsdp_line(result, "usability=invalid reason=length")

    def test_a_length_above_the_supported_maximum_is_refused(self):
        """A declared length that is not an RSDP at all is refused on its face."""
        table = bytearray(self.rsdp(revision=2, length=36))
        table[20:24] = (0x10000).to_bytes(4, "little")   # 64 KiB: past RSDP_MAX_BYTES
        table[8] = 0
        table[8] = (-sum(table[0:20])) & 0xFF
        result = self.run_kernel(rsdp_bytes=bytes(table))
        self.assert_boots(result)
        self.assert_rsdp_line(result, "usability=invalid reason=length")

    def test_a_length_the_map_cannot_cover_is_refused_without_reading_past_it(self):
        """The declared length is checked before any of it is summed.

        The RSDP is placed 36 bytes before the end of its page, and the map
        describes that page as MMIO.  A declared length of 4096 therefore claims
        memory the map does not cover.  The emulator maps only that page, so a
        kernel that summed the declared length anyway would fault rather than
        report - which makes "reports outside-span" proof that the range check ran
        first, not merely that a checksum happened to fail.
        """
        module = self.machine_module
        # End of the map's firmware-owned window, with the table's last 36 bytes
        # just inside it.
        boundary = module.FB_BASE + 0x2000
        address = boundary - 36
        # End the firmware-type region at `boundary`.  Splitting a contiguous
        # MMIO run would not do it - two adjacent descriptors of the same type
        # still cover the range jointly - so the memory past the boundary is
        # re-typed as conventional, which firmware-type checks must reject.
        memmap = []
        for start, pages, kind in module.DEFAULT_MEMMAP:
            if start == module.FB_BASE:
                memmap.append((start, 2, kind))
                memmap.append((boundary, pages - 2,
                               module.PC5_MMAP_CONVENTIONAL))
            else:
                memmap.append((start, pages, kind))
        table = bytearray(self.rsdp(revision=2, length=36))
        table[20:24] = (4096).to_bytes(4, "little")
        table[8] = 0
        table[8] = (-sum(table[0:20])) & 0xFF
        result = self.run_kernel(rsdp_bytes=bytes(table), rsdp_address=address,
                                 acpi_rsdp=address, memmap=memmap)
        self.assert_boots(result)
        # The 36-byte header is itself no longer wholly inside firmware-owned
        # memory, so refusal happens there; either ground proves the declared
        # length was never summed.  A fault here would have meant it was.
        line = self.assert_rsdp_line(result, "usability=invalid")
        self.assertTrue("outside-span" in line or "not-firmware-memory" in line,
                        line)

    # --------------------------------------- 6. structure crosses the span ---
    def test_an_rsdp_at_the_end_of_the_map_is_refused(self):
        """A table may not be the last 20 bytes of the address space the map covers.

        Placed 20 bytes before the end of the identity span, the RSDP has its
        header wholly inside the span but is described by no firmware memory, so
        the kernel must refuse it on one of those two grounds rather than read a
        header it never proved was there.  Which check fires first is the kernel's
        business; that it refuses is the contract.  (Overriding `identity_span`
        alone is not a valid test here: the kernel cross-checks the span against
        `pagetable_pages`, so a span that disagrees with the page count stops the
        handover earlier, for a different reason.)
        """
        result = self.run_kernel(acpi_rsdp=(4 << 30) - 20)
        self.assert_boots(result)
        line = self.assert_rsdp_line(result, "usability=invalid")
        self.assertTrue("outside-span" in line or "not-firmware-memory" in line,
                        line)

    def test_an_rsdp_wholly_below_a_short_span_is_accepted(self):
        # Control for the test above: the same address with the default 4 GiB
        # span is inside it, so a refusal there would have been about the span
        # arithmetic rather than about the span edge.
        result = self.run_kernel(rsdp_bytes=self.rsdp())
        self.assert_boots(result)
        self.assert_rsdp_line(result, "usability=ok")

    # ------------------------------ 7. not fully firmware-owned memory -------
    def test_an_rsdp_not_covered_by_firmware_memory_is_refused(self):
        # Start from the machine's real default map - which describes the
        # framebuffer and the RSDP page together as MMIO - and re-type just the
        # RSDP page as conventional memory.  Conventional is the one type the
        # kernel must never accept for an RSDP: it is free RAM, not firmware data.
        module = self.machine_module
        rsdp_page = module.RSDP // 0x1000
        memmap = []
        for start, pages, kind in module.DEFAULT_MEMMAP:
            first = start // 0x1000
            if first <= rsdp_page < first + pages:
                memmap.append((start, rsdp_page - first, kind))
                memmap.append((rsdp_page * 0x1000, 1, module.PC5_MMAP_CONVENTIONAL))
                memmap.append(((rsdp_page + 1) * 0x1000,
                               first + pages - rsdp_page - 1, kind))
            else:
                memmap.append((start, pages, kind))
        result = self.run_kernel(memmap=memmap, rsdp_bytes=self.rsdp())
        self.assert_boots(result)
        self.assert_rsdp_line(result, "usability=invalid reason=not-firmware-memory")

    # ------------------------------------------------- 8. null / absent ------
    def test_a_null_rsdp_with_the_flag_set_is_reported_null(self):
        result = self.run_kernel(acpi_rsdp=0, flags=0xF)
        self.assert_boots(result)
        self.assert_rsdp_line(result, "usability=invalid reason=null")

    def test_an_absent_rsdp_is_distinct_from_an_invalid_one(self):
        # No RSDP flag and no unmapped flag: absent, not a failure.
        result = self.run_kernel(flags=0xF & ~0x4)
        self.assert_boots(result)
        self.assertIn("usability=absent", result["serial"])

    def test_a_loader_marked_unmapped_rsdp_is_distinct_again(self):
        result = self.run_kernel(acpi_rsdp=0, flags=(0xF & ~0x4) | 0x20)
        self.assert_boots(result)
        self.assertIn("usability=unsupported reason=unmapped-by-loader",
                      result["serial"])

    # ------------------------------------------------ malformed signature ----
    def test_a_malformed_signature_is_refused_before_any_checksum(self):
        result = self.run_kernel(rsdp_bytes=self.rsdp(signature=b"NOTRSDP!"))
        self.assert_boots(result)
        self.assert_rsdp_line(result, "usability=invalid reason=signature")

    def test_an_undefined_revision_is_refused(self):
        # Only revisions 0 and 2 are defined.  Revision 1 is not "probably legacy"
        # and revision 3 is not "probably revision 2": either assumption would
        # change how many bytes get summed.
        for revision in (1, 3, 0xFF):
            with self.subTest(revision=revision):
                result = self.run_kernel(
                    rsdp_bytes=self.rsdp(revision=revision, length=36))
                self.assert_boots(result)
                self.assert_rsdp_line(result, "usability=invalid reason=revision")


if __name__ == "__main__":
    unittest.main()
