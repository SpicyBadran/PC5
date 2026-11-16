"""Tests for the experimental 64-bit foundation.

Four things are checked here, and they are deliberately independent:

  * the build: the kernel is really ELF64 at the address it is linked for, and
    the loader is a valid PE32+ EFI application;
  * the protocol: `struct pc5_boot64` has the size and offsets both sides were
    compiled against, checked against a host build of the same header;
  * the page tables: `pc5_pt_build_identity` is compiled for the host and its
    entries are inspected, so the map is verified instead of assumed;
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
        self.assertEqual(len(binary), 0xA000,
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
            "int main(void){printf(\"%zu %zu %zu %zu %zu %zu %zu %zu %zu\\n\",\n"
            " sizeof(struct pc5_boot64), sizeof(struct pc5_memdesc64),\n"
            " offsetof(struct pc5_boot64, memmap_addr),\n"
            " offsetof(struct pc5_boot64, framebuffer_addr),\n"
            " offsetof(struct pc5_boot64, acpi_rsdp),\n"
            " offsetof(struct pc5_boot64, kernel_base),\n"
            " offsetof(struct pc5_boot64, stack_top),\n"
            " offsetof(struct pc5_boot64, exit_boot_services),\n"
            " offsetof(struct pc5_boot64, time_stamp));\n"
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
        self.assertEqual(values, [112, 32, 16, 40, 64, 72, 88, 96, 104])

    def test_python_packer_agrees_with_the_header(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import kernel64_machine as machine
        self.assertEqual(len(machine.pack_boot_info()), 112)
        self.assertEqual(len(machine.pack_memmap(machine.DEFAULT_MEMMAP)), 96)


class PageTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build()
        cls.library = ctypes.CDLL(str(_CACHE.setdefault("pt_lib", _compile_pagetables())))
        cls.library.pc5_pt_build_identity.restype = ctypes.c_ulong
        cls.library.pc5_pt_build_identity.argtypes = [ctypes.c_ulonglong, ctypes.c_ulonglong]
        cls.library.pc5_pt_mark_uncached.restype = ctypes.c_ulong
        cls.library.pc5_pt_mark_uncached.argtypes = [ctypes.c_ulonglong] * 3
        cls.library.pc5_pt_lookup.restype = ctypes.c_int
        cls.library.pc5_pt_lookup.argtypes = [ctypes.c_ulonglong, ctypes.c_ulonglong,
                                              ctypes.POINTER(ctypes.c_ulonglong)]

    def setUp(self):
        # Keep the buffer alive: ctypes gives no ownership back, and a freed
        # buffer would make every entry read look like a missing page.
        self.buffer = ctypes.create_string_buffer(6 * 4096 + 4096)
        raw = ctypes.addressof(self.buffer)
        self.base = (raw + 4095) & ~4095       # the builder refuses unaligned input

    def entry_at(self, index):
        return ctypes.c_ulonglong.from_address(self.base + 0x2000 + index * 8).value

    def test_identity_map_covers_four_gib_with_2m_pages(self):
        mapped = self.library.pc5_pt_build_identity(self.base, 4 << 30)
        self.assertEqual(mapped, 2048)
        pml4 = ctypes.c_ulonglong.from_address(self.base).value
        pdpt = ctypes.c_ulonglong.from_address(self.base + 0x1000).value
        self.assertEqual(pml4 & 0xFFF, 0x3)
        self.assertEqual(pdpt & 0xFFF, 0x3)
        self.assertEqual(self.entry_at(0), 0x83)
        self.assertEqual(self.entry_at(511), (511 << 21) | 0x83)
        self.assertEqual(self.entry_at(2047), (2047 << 21) | 0x83)

    def test_every_mapped_page_is_present_writable_and_2mib(self):
        self.library.pc5_pt_build_identity(self.base, 4 << 30)
        for index in (0, 1, 300, 1024, 2047):
            entry = self.entry_at(index)
            self.assertTrue(entry & 1, f"page {index} not present")
            self.assertTrue(entry & 2, f"page {index} not writable")
            self.assertTrue(entry & (1 << 7), f"page {index} is not a 2 MiB leaf")
            self.assertEqual(entry & ((1 << 21) - 1) & ~0x1FF, 0,
                             f"page {index} has a non-zero reserved field")

    def test_lookup_finds_the_leaf_and_rejects_unmapped_space(self):
        self.library.pc5_pt_build_identity(self.base, 4 << 30)
        entry = ctypes.c_ulonglong(0)
        self.assertEqual(self.library.pc5_pt_lookup(self.base, 0x100000,
                                                    ctypes.byref(entry)), 1)
        self.assertEqual(entry.value, 0x83)
        self.assertEqual(self.library.pc5_pt_lookup(self.base, 4 << 30,
                                                    ctypes.byref(entry)), 0)

    def test_uncached_marking_touches_only_the_framebuffer_pages(self):
        self.library.pc5_pt_build_identity(self.base, 4 << 30)
        changed = self.library.pc5_pt_mark_uncached(self.base, 0x04000000,
                                                    0x04000000 + 0x200000)
        self.assertEqual(changed, 1)
        self.assertEqual(self.entry_at(32) & 0x18, 0x18)   # PWT|PCD
        self.assertEqual(self.entry_at(31) & 0x18, 0)
        self.assertEqual(self.entry_at(33) & 0x18, 0)

    def test_bad_arguments_are_refused_instead_of_building_a_partial_map(self):
        self.assertEqual(self.library.pc5_pt_build_identity(self.base | 1, 4 << 30), 0)
        self.assertEqual(self.library.pc5_pt_build_identity(self.base, 0), 0)
        self.assertEqual(self.library.pc5_pt_build_identity(self.base, 8 << 30), 0)
        self.assertEqual(self.library.pc5_pt_mark_uncached(self.base, 0x1000, 0x1000), 0)


def _compile_pagetables():
    build()
    directory = tempfile.mkdtemp(prefix="pc5-pagetables-")
    output = pathlib.Path(directory) / "libpagetables.so"
    source = ROOT / "boot" / "uefi64" / "pagetables.c"
    result = subprocess.run(["gcc", "-std=gnu11", "-O2", "-fPIC", "-shared",
                             "-Wall", "-Wextra", "-Werror",
                             "-I", str(source.parent), str(source), "-o", str(output)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"pagetables.c did not build for the host:\n{result.stderr}")
    return output


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
        self.assertEqual(protocol, 1)
        self.assertEqual(boot_info_size, 112)
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
        self.assert_refuses(self.run_kernel(version=2), "bootinfo-version")

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
        memmap = [(0, 0, 7)] + list(self.machine_module.DEFAULT_MEMMAP[1:])
        self.assert_refuses(self.run_kernel(memmap=memmap), "bootinfo-memmap-empty")

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
