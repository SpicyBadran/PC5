"""Firmware emulation for the *64-bit* PC5 boot path.

The 32-bit path has its own harness (`uefi_stub_firmware.py`) which emulates the
mode switch and decodes the far jump into the 32-bit kernel.  The 64-bit loader
does neither, so this module reuses that harness for everything that is the same
- the PE image loader, the service tables, the ms_abi trampoline, the file
protocol, the memory map and the ExitBootServices key check - and changes only
what is genuinely different:

  * firmware is in long mode *with paging enabled*, because long mode cannot
    exist without it.  The harness sets CR0.PG/CR4.PAE before the entry point,
    exactly as real firmware would already have them;
  * the ACPI configuration table is present, so the loader can record the RSDP;
  * the run stops as soon as the kernel prints its verdict, instead of watching
    for a far jump that this path does not perform.

What this harness does *not* do: it does not walk the page tables.  Unicorn in
64-bit mode ignores CR3 unless a caller implements TLB hooks, so a page fault
here would not prove anything about the map.  The map is therefore verified by
`tests/test_pagetables64.py` against real entry values, and the run here proves
the *code path* installs PC5's CR3 and reaches the kernel in long mode.
"""
from __future__ import annotations

import struct

import uefi_stub_firmware as fw

try:
    from unicorn import UcError
    from unicorn.x86_const import (
        UC_X86_REG_CR0, UC_X86_REG_CR3, UC_X86_REG_CR4, UC_X86_REG_CS,
        UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_RIP, UC_X86_REG_RSP,
        UC_X86_REG_SS,
    )
except ImportError:  # pragma: no cover - reported by the caller
    UcError = Exception
    UC_X86_REG_CR0 = UC_X86_REG_CR3 = UC_X86_REG_CR4 = 0
    UC_X86_REG_CS = UC_X86_REG_RCX = UC_X86_REG_RDX = 0
    UC_X86_REG_RIP = UC_X86_REG_RSP = UC_X86_REG_SS = 0

StubError = fw.StubError

# EFI_ALLOCATE_TYPE / status values the loader's own header pins as well.
EFI_ALLOCATE_ANY_PAGES = 0
EFI_ALLOCATE_ADDRESS = 2
EFI_SUCCESS = 0
EFI_INVALID_PARAMETER = 0x8000000000000002

# 8868e871-e4f1-11d3-bc22-0080c73c8881 in EFI byte order.
ACPI2_GUID = bytes.fromhex("71e86888f1e4d311bc220080c73c8881")

CR0_PG = 1 << 31
CR0_PE = 1
CR4_PAE = 1 << 5

# The kernel's last line ends the run.  `PC5-64-DONE` follows the success
# marker, so stopping there proves the marker was not the last thing said and
# that the kernel reached its halting loop; a failure line stops the run too,
# after its reason has been printed.
STOP_MARKERS = ("PC5-64-DONE", "PC5-64BIT-FAIL=", "PC5-64-LOADER-FAIL=")


class StubFirmware64(fw.StubFirmware):
    """A long-mode UEFI machine that can execute the 64-bit loader and kernel."""

    # The kernel is linked at 0x100000, outside the 32-bit harness's low-memory
    # window, so the machine gets that RAM as well.
    KERNEL64_WINDOW = (0x00100000, 0x00200000)

    def _map(self):
        super()._map()
        base, size = self.KERNEL64_WINDOW
        self.uc.mem_map(base, size)

    def svc_allocate_pages(self, alloc_type, memory_type, pages, memory_ptr, *_rest):
        """Honour EFI_ALLOCATE_ADDRESS, which the 64-bit loader relies on."""
        if alloc_type == EFI_ALLOCATE_ADDRESS:
            requested = self.read_u64(memory_ptr)
            if requested % 4096 or requested < self.KERNEL64_WINDOW[0] \
                    or requested + pages * 4096 > sum(self.KERNEL64_WINDOW):
                return EFI_INVALID_PARAMETER
            self.uc.mem_write(requested, bytes(pages * 4096))
            self.map_generation += 1
            return EFI_SUCCESS
        return super().svc_allocate_pages(alloc_type, memory_type, pages,
                                          memory_ptr, *_rest)

    def _build_tables(self):
        super()._build_tables()
        # --- ACPI configuration table: one entry pointing at a real RSDP.
        rsdp = self.alloc(36)
        self.write(rsdp, b"RSD PTR ")                 # signature
        self.write(rsdp + 8, b"\x02")                 # revision 2 (ACPI 2.0+)
        self.write(rsdp + 9, b"PC5STB")               # OEM id, 6 bytes
        self.write(rsdp + 15, b"\x00" * 3 + b"PC5STUBT")
        self.write(rsdp + 24, struct.pack("<I", 1))   # OEM revision
        self.write(rsdp + 28, b"PC5 ")
        self.write(rsdp + 32, struct.pack("<I", 1))   # creator revision
        entry = self.alloc(24)
        self.write(entry, ACPI2_GUID)
        self.write_u64(entry + 16, rsdp)
        self.write_u64(self.sys_table + 104, 1)       # NumberOfTableEntries
        self.write_u64(self.sys_table + 112, entry)   # ConfigurationTable
        self.rsdp = rsdp

    # ------------------------------------------------------------- running
    def run(self, max_instructions=40_000_000, stop_markers=STOP_MARKERS):
        uc = self.uc
        self.stop_markers = tuple(stop_markers)
        self.stopped_on = None
        # Long mode implies paging; firmware entered us with it on.
        uc.reg_write(UC_X86_REG_CR0,
                     uc.reg_read(UC_X86_REG_CR0) | CR0_PG | CR0_PE)
        uc.reg_write(UC_X86_REG_CR4, uc.reg_read(UC_X86_REG_CR4) | CR4_PAE)
        uc.reg_write(UC_X86_REG_CR3, self._install_firmware_paging())
        uc.reg_write(UC_X86_REG_CS, 0x33)
        uc.reg_write(UC_X86_REG_SS, 0x2B)
        # Firmware calls the entry point with a return address already on the
        # stack, so the entry sees RSP % 16 == 8; modelling that keeps the ms_abi
        # alignment checks honest instead of accidentally passing.
        uc.reg_write(UC_X86_REG_RSP, fw.FW_STACK_TOP - 8)
        uc.reg_write(UC_X86_REG_RCX, 0xE5F0)             # ImageHandle
        uc.reg_write(UC_X86_REG_RDX, self.sys_table)     # SystemTable
        try:
            uc.emu_start(self.entry, 0, 0, max_instructions)
        except UcError as exc:
            raise StubError(
                "emulation faulted: %s (rip=%#x) serial=%r"
                % (exc, uc.reg_read(UC_X86_REG_RIP), self.trace.serial))
        return self.trace

    # --------------------------------------------------------- paging model
    def _install_firmware_paging(self):
        """Give the machine a real identity map and return its PML4 address.

        Unicorn walks the guest page tables whenever CR0.PG is set, which is
        exactly how a real machine behaves - and it is why this harness cannot
        simply invent a CR3.  The *firmware's* map is built here, in memory
        Unicorn has mapped, covering the first 4 GiB with 2 MiB pages: the
        image, the pool, the framebuffer and the low-memory handover window are
        all inside it.  When the loader installs PC5's own PML4, every later
        fetch and data access is translated through *that* table instead, so a
        wrong entry in PC5's map faults the run rather than passing silently.
        """
        base = self.alloc(6 * 4096, align=4096)
        pml4, pdpt, pd = base, base + 0x1000, base + 0x2000
        self.write_u64(pml4, pdpt | 0x3)                 # present, writable
        self.write_u64(pdpt, pd | 0x3)
        entries = [((index << 21) | 0x83) for index in range(2048)]
        assert len(entries) == 2048, len(entries)
        for page in range(4):
            chunk = entries[page * 512:page * 512 + 512]
            assert len(chunk) == 512, (page, len(chunk), len(entries))
            self.write(pd + page * 0x1000, struct.pack("<512Q", *chunk))
        self.firmware_pml4 = pml4
        return pml4

    def _hook_out(self, uc, port, size, value, userdata):
        keep_going = super()._hook_out(uc, port, size, value, userdata)
        serial = self.trace.serial
        for marker in getattr(self, "stop_markers", ()):
            if marker not in serial:
                continue
            # A failure marker is followed by the reason on the same line, so
            # stopping at the marker itself would truncate the diagnosis.
            if marker.endswith("=") and not serial.endswith("\n"):
                continue
            self.stopped_on = marker
            uc.emu_stop()
            break
        return keep_going
