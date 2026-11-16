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

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
KERNEL_ELF = BUILD / "kernel64" / "pc5-64.elf"
KERNEL_BIN = BUILD / "kernel64" / "KERNEL64.BIN"
LOADER_EFI = BUILD / "uefi64" / "BOOTX64.EFI"

MARKER = "PC5-64BIT-LONG-MODE-OK"
MASK64 = (1 << 64) - 1

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
        self.assertEqual(len(binary), 0xC000,
                         "text+rodata plus the 32 KiB .bss window (stack)")

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


class KernelContractTests(unittest.TestCase):
    """Every failure path, driven through the kernel's real entry point."""

    @classmethod
    def setUpClass(cls):
        build()
        sys.path.insert(0, str(ROOT / "tests"))
        try:
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

    def test_rsdp_without_the_signature_is_unsupported(self):
        result = self.run_kernel(rsdp_signature=b"NOPE NOPE")
        self.assert_boots(result)
        self.assertIn("PC5-64-RSDP addr=", result["serial"])
        self.assertIn("usability=unsupported reason=signature", result["serial"])

    def test_rsdp_outside_the_map_is_unsupported(self):
        result = self.run_kernel(acpi_rsdp=0x60000000)
        self.assert_boots(result)
        self.assertIn("usability=unsupported reason=not-firmware-memory",
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


if __name__ == "__main__":
    unittest.main()
