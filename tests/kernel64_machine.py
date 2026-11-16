"""A minimal long-mode machine that boots the PC5 64-bit kernel directly.

The stub firmware harness (`boot64_stub.py`) tests the *loader*: it reads a
kernel from a file, builds the memory map, calls ExitBootServices and hands
over.  This module tests the *kernel's contract* on its own, which is the only
way to exercise the failure paths: an invalid boot-info structure cannot be
produced by a correct loader, so the kernel's refusal to boot on one can only
be checked by handing it the broken structure deliberately.

Nothing here stands in for firmware: there is no UEFI, no file system and no
ExitBootServices.  It maps RAM, installs an identity map, sets the registers a
loader would set, and jumps to the kernel's entry point.
"""
from __future__ import annotations

import struct

KERNEL_BASE = 0x100000

KERNEL_WINDOW = (0x00100000, 0x00200000)
BOOT_AREA = (0x00400000, 0x00020000)
TABLE_AREA = (0x00500000, 0x00020000)

BOOT_INFO = BOOT_AREA[0]
MEMMAP = BOOT_AREA[0] + 0x1000
STACK_TOP = BOOT_AREA[0] + 0x18000
PML4 = TABLE_AREA[0]

MAGIC = 0x42354350
VERSION = 1
BOOT_INFO_SIZE = 112
DESC_SIZE = 32
LINK_BASE = KERNEL_BASE

PC5_MMAP_CONVENTIONAL = 7
PC5_MMAP_LOADER_DATA = 2
PC5_MMAP_MMIO = 11

DEFAULT_MEMMAP = [
    (0x00000000, 0x9F, PC5_MMAP_CONVENTIONAL),
    (0x00100000, 0x100, PC5_MMAP_LOADER_DATA),
    (0x04000000, 0x400, PC5_MMAP_MMIO),
]


def pack_boot_info(**overrides):
    """Build a `struct pc5_boot64` image, with per-field overrides for tests."""
    fields = dict(
        magic=MAGIC, version=VERSION, size=BOOT_INFO_SIZE, flags=0xF,
        memmap_addr=MEMMAP, memmap_bytes=DESC_SIZE * len(DEFAULT_MEMMAP),
        memmap_desc_size=DESC_SIZE, memmap_desc_version=1,
        framebuffer_addr=0x04000000, framebuffer_width=1024,
        framebuffer_height=768, framebuffer_pitch=4096, framebuffer_format=0,
        acpi_rsdp=0x04001000, kernel_base=LINK_BASE, kernel_size=0xA000,
        stack_top=STACK_TOP, exit_boot_services=1, time_stamp=0,
    )
    fields.update(overrides)
    return struct.pack(
        "<IIIIQQIIQIIIIQQQQQQ",
        fields["magic"], fields["version"], fields["size"], fields["flags"],
        fields["memmap_addr"], fields["memmap_bytes"],
        fields["memmap_desc_size"], fields["memmap_desc_version"],
        fields["framebuffer_addr"], fields["framebuffer_width"],
        fields["framebuffer_height"], fields["framebuffer_pitch"],
        fields["framebuffer_format"], fields["acpi_rsdp"],
        fields["kernel_base"], fields["kernel_size"], fields["stack_top"],
        fields["exit_boot_services"], fields["time_stamp"])


def pack_memmap(descriptors):
    # struct pc5_memdesc64: three u64 fields and two u32 fields, packed, so the
    # stride is 32 bytes and must match the kernel's memmap_desc_size.
    return b"".join(struct.pack("<QQQII", start, pages, 8, kind, 0)
                    for start, pages, kind in descriptors)


class Machine:
    def __init__(self, kernel):
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE, \
            UC_HOOK_INSN
        from unicorn.x86_const import UC_X86_INS_IN, UC_X86_INS_OUT

        self.uc = Uc(UC_ARCH_X86, UC_MODE_64)
        self.uc.mem_map(*KERNEL_WINDOW)
        self.uc.mem_map(*BOOT_AREA)
        self.uc.mem_map(*TABLE_AREA)
        self.uc.mem_write(KERNEL_BASE, bytes(kernel))
        self.serial = ""
        self.stop_markers = ("PC5-64-DONE", "PC5-64BIT-FAIL=")
        self.stopped_on = None
        self._fixup = None
        self._insn = (0, 1)
        self.uc.hook_add(UC_HOOK_INSN, self._hook_out, None, 1, 0, UC_X86_INS_OUT)
        self.uc.hook_add(UC_HOOK_INSN, self._hook_in, None, 1, 0, UC_X86_INS_IN)
        self.uc.hook_add(UC_HOOK_CODE, self._hook_code)

    # -------------------------------------------------------------- serial
    def _hook_out(self, uc, port, size, value, userdata):
        if port == 0x3F8:
            self.serial += chr(value & 0xFF)
        for marker in self.stop_markers:
            if marker in self.serial:
                if marker.endswith("=") and not self.serial.endswith("\n"):
                    continue
                self.stopped_on = marker
                uc.emu_stop()
                break
        return True

    def _hook_in(self, uc, port, size, userdata):
        # The kernel polls the UART's line-status register.  Unicorn performs
        # the port read itself and overwrites AL, so the value has to be
        # applied at the next instruction; 0x60 marks the transmitter empty.
        address, length = self._insn
        self._fixup = (address + length, 0x60 if port == 0x3FD else 0x00)
        return True

    def _hook_code(self, uc, address, size, userdata):
        from unicorn.x86_const import UC_X86_REG_RAX
        self._insn = (address, size)
        if self._fixup is not None and address == self._fixup[0]:
            value = uc.reg_read(UC_X86_REG_RAX)
            uc.reg_write(UC_X86_REG_RAX, (value & ~0xFF) | self._fixup[1])
            self._fixup = None

    # -------------------------------------------------------------- paging
    def install_identity_map(self, span=(4 << 30)):
        entries_per_page = 512
        pd_pages = span // (2 << 20) // entries_per_page
        pdpt = PML4 + 0x1000
        pd = PML4 + 0x2000
        self.uc.mem_write(PML4, struct.pack("<Q", pdpt | 0x3))
        self.uc.mem_write(pdpt, struct.pack("<Q", pd | 0x3))
        for page in range(pd_pages):
            first = page * entries_per_page
            values = [((index << 21) | 0x83) for index in range(first, first + 512)]
            self.uc.mem_write(pd + page * 0x1000, struct.pack("<512Q", *values))
        return PML4

    # --------------------------------------------------------------- run
    def run(self, boot_info, paging=True, max_instructions=4_000_000, memmap=None):
        from unicorn import UcError
        from unicorn.x86_const import (
            UC_X86_REG_CR0, UC_X86_REG_CR3, UC_X86_REG_CR4, UC_X86_REG_RDI,
            UC_X86_REG_RIP)

        uc = self.uc
        uc.mem_write(MEMMAP, pack_memmap(memmap if memmap is not None
                                        else DEFAULT_MEMMAP))
        uc.mem_write(BOOT_INFO, boot_info)
        pml4 = self.install_identity_map()
        uc.reg_write(UC_X86_REG_CR4, uc.reg_read(UC_X86_REG_CR4) | 0x20)
        uc.reg_write(UC_X86_REG_CR3, pml4)
        cr0 = uc.reg_read(UC_X86_REG_CR0) | 0x1
        if paging:
            cr0 |= 1 << 31
        else:
            cr0 &= ~(1 << 31)
        uc.reg_write(UC_X86_REG_CR0, cr0)
        uc.reg_write(UC_X86_REG_RDI, BOOT_INFO)
        uc.reg_write(UC_X86_REG_RIP, KERNEL_BASE)
        error = None
        try:
            uc.emu_start(KERNEL_BASE, 0, 0, max_instructions)
        except UcError as exc:
            error = f"{exc} at {uc.reg_read(UC_X86_REG_RIP):#x}"
        return dict(serial=self.serial, stopped_on=self.stopped_on, error=error)


def run_kernel(kernel, memmap=None, paging=True, **overrides):
    machine = Machine(kernel)
    if memmap is not None:
        overrides.setdefault("memmap_bytes", DESC_SIZE * len(memmap))
    result = machine.run(pack_boot_info(**overrides), paging=paging,
                         memmap=memmap)
    result["machine"] = machine
    return result
