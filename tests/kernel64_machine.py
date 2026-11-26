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

# The conventional RAM the default map advertises at the bottom of memory.  It is
# mapped because the kernel now *uses* what the map says exists: the frame allocator
# hands a low frame to the TSS builder, which writes 104 bytes into it, and a fixture
# that describes memory without providing it reports that as `UC_ERR_WRITE_UNMAPPED` -
# an emulator boundary wearing the same clothes as a kernel bug.
LOW_CONVENTIONAL = (0x00000000, 0x000A0000)
KERNEL_WINDOW = (0x00100000, 0x00200000)
BOOT_AREA = (0x00400000, 0x00020000)
TABLE_AREA = (0x00500000, 0x00020000)

BOOT_INFO = BOOT_AREA[0]
MEMMAP = BOOT_AREA[0] + 0x1000
STACK_TOP = BOOT_AREA[0] + 0x18000
PML4 = TABLE_AREA[0]

MAGIC = 0x42354350
VERSION = 2
BOOT_INFO_SIZE = 160
DESC_SIZE = 32

# Where the machine puts a synthetic framebuffer and RSDP.  Only the RSDP is
# dereferenced by the kernel (it checks the signature), so only that page has to be
# real memory; the framebuffer is validated by range, not read.
FB_BASE = 0x04000000
FB_SIZE = 0x00400000
RSDP = FB_BASE + 0x1000
RSDP_SIGNATURE = b"RSD PTR "
LINK_BASE = KERNEL_BASE

PC5_MMAP_CONVENTIONAL = 7
PC5_MMAP_LOADER_DATA = 2
PC5_MMAP_MMIO = 11

# The kernel checks that every handoff region is covered by memory of an acceptable
# type, so the map has to describe the kernel window, the boot area, the page-table
# area and the framebuffer - which is what firmware does for memory it has handed out.
DEFAULT_MEMMAP = [
    (0x00000000, 0x9F, PC5_MMAP_CONVENTIONAL),          # 0 .. 0x9F000
    (0x00100000, 0x320, PC5_MMAP_LOADER_DATA),          # kernel + boot area + stack
    (0x00500000, 0x020, PC5_MMAP_LOADER_DATA),          # page tables
    (FB_BASE, FB_SIZE // 4096, PC5_MMAP_MMIO),          # framebuffer + RSDP
]


def pack_boot_info(**overrides):
    """Build a `struct pc5_boot64` image, with per-field overrides for tests.

    The layout is written as a sequence of (code, field) pairs rather than one
    hand-written format string: the format string is derived, so a missing or
    duplicated field cannot silently shift every field after it.  The order is the
    C structure's order, and tests/test_kernel64.py checks the total size against a
    host build of the header.
    """
    fields = dict(
        magic=MAGIC, version=VERSION, size=BOOT_INFO_SIZE, flags=0xF,
        memmap_addr=MEMMAP, memmap_bytes=DESC_SIZE * len(DEFAULT_MEMMAP),
        memmap_desc_size=DESC_SIZE, memmap_desc_version=1,
        framebuffer_addr=FB_BASE, framebuffer_width=1024,
        framebuffer_height=768, framebuffer_pitch=4096, framebuffer_format=0,
        # A layout default, not a claim about any image: `run_kernel` replaces it with the
        # length of the bytes it actually loaded, because the kernel compares the two.
        acpi_rsdp=RSDP, kernel_base=LINK_BASE, kernel_size=0xA000,
        stack_top=STACK_TOP, stack_size=0x8000,
        pagetable_addr=PML4, pagetable_pages=6, identity_span=4 << 30,
        loader_image_addr=LINK_BASE, loader_image_size=0xA000,
        exit_boot_services=1, time_stamp=0,
    )
    fields.update(overrides)
    layout = [
        ("I", "magic"), ("I", "version"), ("I", "size"), ("I", "flags"),
        ("Q", "memmap_addr"), ("Q", "memmap_bytes"),
        ("I", "memmap_desc_size"), ("I", "memmap_desc_version"),
        ("Q", "framebuffer_addr"),
        ("I", "framebuffer_width"), ("I", "framebuffer_height"),
        ("I", "framebuffer_pitch"), ("I", "framebuffer_format"),
        ("Q", "acpi_rsdp"),
        ("Q", "kernel_base"), ("Q", "kernel_size"),
        ("Q", "stack_top"), ("Q", "stack_size"),
        ("Q", "pagetable_addr"), ("Q", "pagetable_pages"),
        ("Q", "identity_span"),
        ("Q", "loader_image_addr"), ("Q", "loader_image_size"),
        ("Q", "exit_boot_services"), ("Q", "time_stamp"),
    ]
    known = {name for _, name in layout}
    unknown = set(fields) - known
    assert not unknown, f"unknown boot-info field(s): {sorted(unknown)}"
    return struct.pack("<" + "".join(code for code, _ in layout),
                       *[fields[name] for _, name in layout])


def pack_memmap(descriptors):
    # struct pc5_memdesc64: three u64 fields and two u32 fields, packed, so the
    # stride is 32 bytes and must match the kernel's memmap_desc_size.
    return b"".join(struct.pack("<QQQII", start, pages, 8, kind, 0)
                    for start, pages, kind in descriptors)


def build_rsdp(revision=2, length=36, legacy_checksum=None, extended_checksum=None,
               signature=RSDP_SIGNATURE):
    """A well-formed RSDP, with each field overridable to make one thing wrong.

    The default is a revision-2 table of exactly 36 bytes with both checksums
    correct, which is the shape a conforming firmware publishes.  Setting
    `length` to 20 makes it a revision-0 style table with no length field at all;
    the checksums are then computed over whatever prefix the caller asked for.

    Both checksums are defaults of "compute the correct value", so a test that
    wants a bad checksum passes an explicit number (usually 0, or a deliberately
    wrong one) rather than relying on an accident of the bytes it wrote.
    """
    size = max(int(length), 36)
    table = bytearray(bytes(size))
    table[0:8] = signature[:8].ljust(8, b"\x00")
    table[9:15] = b"PC5K64"                      # OEMID
    table[15] = revision & 0xFF
    if revision >= 2:
        table[20:24] = struct.pack("<I", length)
    # Legacy checksum covers the first 20 bytes; the extended checksum covers the
    # declared length.  A revision-0 table has neither a length nor an extended
    # checksum, and bytes past 20 are not part of it.
    lo = (sum(table[0:20])) & 0xFF
    table[8] = ((legacy_checksum if legacy_checksum is not None
                 else (-sum(table[0:8]) - sum(table[9:20]))) & 0xFF)
    if revision >= 2:
        covered = max(int(length), 20)
        table[32] = ((extended_checksum if extended_checksum is not None
                      else (-(sum(table[0:32]) + sum(table[33:covered])))
                      & 0xFF))
    assert lo is not None
    return bytes(table)


class Machine:
    def __init__(self, kernel, rsdp_signature=RSDP_SIGNATURE, cpuid=None,
                 rsdp_bytes=None, rsdp_address=None):
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE, \
            UC_HOOK_INSN
        from unicorn.x86_const import UC_X86_INS_IN, UC_X86_INS_OUT, \
            UC_X86_INS_CPUID

        self.uc = Uc(UC_ARCH_X86, UC_MODE_64)
        # An optional CPUID table: leaf -> (eax, ebx, ecx, edx).  Without it the
        # emulator's own CPUID runs, which is what the other tests want.  With it,
        # the kernel's leaf gating and address-width validation can be driven with
        # controlled results instead of whatever the host CPU happens to report.
        self.cpuid = cpuid
        # Where the table is published.  A test that needs the declared length to
        # run past the end of the memory map moves the RSDP to the end of its
        # page; the map is page-granular, so that is the only way to express a
        # range that the map cannot cover.
        self.rsdp_address = RSDP if rsdp_address is None else rsdp_address
        self.uc.mem_map(*LOW_CONVENTIONAL)
        self.uc.mem_map(*KERNEL_WINDOW)
        self.uc.mem_map(*BOOT_AREA)
        self.uc.mem_map(*TABLE_AREA)
        # The RSDP page and the one after it.  Only the RSDP page is ever meant to
        # be read; the second is mapped so that a test which places the table at
        # the end of its page can prove the kernel stops at the map boundary
        # instead of faulting on the *emulator's* boundary, which would be a
        # different failure wearing the same clothes.
        self.uc.mem_map(FB_BASE, 0x3000)
        # `rsdp_bytes` is a complete table when a test needs control over the
        # revision, declared length or checksums.  `rsdp_signature` keeps the
        # original behaviour: a table with only the first eight bytes chosen.
        if rsdp_bytes is None:
            self.uc.mem_write(self.rsdp_address,
                              rsdp_signature[:8].ljust(8, b" ") + bytes(28))
        else:
            self.uc.mem_write(self.rsdp_address, bytes(rsdp_bytes))
            if len(rsdp_bytes) < 36:
                self.uc.mem_write(self.rsdp_address + len(rsdp_bytes),
                                  bytes(36 - len(rsdp_bytes)))
        self.kernel = bytes(kernel)
        self.uc.mem_write(KERNEL_BASE, self.kernel)
        self.serial = ""
        self.stop_markers = ("PC5-64-DONE", "PC5-64BIT-FAIL=")
        self.stopped_on = None
        self._fixup = None
        self._insn = (0, 1)
        self.uc.hook_add(UC_HOOK_INSN, self._hook_out, None, 1, 0, UC_X86_INS_OUT)
        self.uc.hook_add(UC_HOOK_INSN, self._hook_in, None, 1, 0, UC_X86_INS_IN)
        self.uc.hook_add(UC_HOOK_CODE, self._hook_code)
        if cpuid is not None:
            self.uc.hook_add(UC_HOOK_INSN, self._hook_cpuid, None, 1, 0,
                             UC_X86_INS_CPUID)

    def _hook_cpuid(self, uc, userdata):
        from unicorn.x86_const import (
            UC_X86_REG_RAX, UC_X86_REG_RBX, UC_X86_REG_RCX, UC_X86_REG_RDX)
        leaf = uc.reg_read(UC_X86_REG_RAX)
        values = self.cpuid.get(leaf, (0, 0, 0, 0))
        for register, value in zip(
                (UC_X86_REG_RAX, UC_X86_REG_RBX, UC_X86_REG_RCX, UC_X86_REG_RDX),
                values):
            uc.reg_write(register, value)
        return True

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
    def entry(self):
        """Where execution begins: the address the *image* declares, not a constant.

        This used to be `KERNEL_BASE`, which was right only because the flat binary began with
        `cli; mov rbx, rdi` at the load address.  `KERNEL64.BIN` now starts with its own
        128-byte header (`docs/engineering/KERNEL-IMAGE64-HEADER-DESIGN-v1.md`), so entering at
        the load address means executing the magic bytes and getting a #UD at 0x100000 -- which
        is exactly what this harness did when the header landed, and the reason the fix here is
        to read the declaration rather than to move a constant: the loader under test jumps by
        the same field, so a harness that assumed the address would drift away from the artifact
        again at the next format change.  A blob without the magic -- one of the hand-built
        images several cases here pass in -- keeps entering at the base, which is what "no
        header, no declared entry" means.
        """
        image = self.kernel
        if image[:8] != b"PC5K64IM":
            return KERNEL_BASE
        return KERNEL_BASE + struct.unpack_from("<Q", image, 32)[0]

    def run(self, boot_info, paging=True, max_instructions=4_000_000, memmap=None,
            patch=None):
        from unicorn import UcError
        from unicorn.x86_const import (
            UC_X86_REG_CR0, UC_X86_REG_CR3, UC_X86_REG_CR4, UC_X86_REG_RDI,
            UC_X86_REG_RIP)

        uc = self.uc
        uc.mem_write(MEMMAP, pack_memmap(memmap if memmap is not None
                                        else DEFAULT_MEMMAP))
        uc.mem_write(BOOT_INFO, boot_info)
        if patch:
            # Written after the image is in place and before the first instruction runs, so
            # nothing but the kernel itself can notice.  That is the property a caller wants: a
            # corruption applied to the *loaded* bytes is what a truncated read, a partial write
            # or firmware that overran its buffer look like from the inside -- and a fixture that
            # edited the file instead would only prove the stamper's arithmetic.
            for offset, payload in patch:
                uc.mem_write(KERNEL_BASE + offset, payload)
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
        entry = self.entry()
        uc.reg_write(UC_X86_REG_RIP, entry)
        error = None
        fault_rip = None
        try:
            uc.emu_start(entry, 0, 0, max_instructions)
        except UcError as exc:
            fault_rip = uc.reg_read(UC_X86_REG_RIP)
            # The address comes back as a number as well as inside the sentence, because a caller
            # that wants to know *where* the guest stopped should not have to parse wording that
            # belongs to the harness.  `tests/test_unicorn_soft_int.py` is the reason: it has to
            # compare this address with one read out of the built image, and a format change here
            # must not be able to turn that comparison into a pass.
            error = f"{exc} at {fault_rip:#x}"
        return dict(serial=self.serial, stopped_on=self.stopped_on, error=error,
                    fault_rip=fault_rip)


def pack_boot_info_for(kernel, **overrides):
    """`pack_boot_info`, with `kernel_size` set to the image that is actually being booted.

    Two kinds of caller build a handover here: the shared `run_kernel` below, and tests that need
    a `Machine` of their own first (backing an MMIO page has to happen before the run).
    `pack_boot_info`'s own default for that field is a *layout* fixture, and the kernel now
    compares it with the length its header declares -- so a caller that forgets to say otherwise
    does not get a sloppy fixture, it gets a refused boot.  That is the check working; this
    helper is the place that knows both sides, so no caller has to remember.
    """
    overrides.setdefault("kernel_size", len(kernel))
    return pack_boot_info(**overrides)


def run_kernel(kernel, memmap=None, paging=True, rsdp_signature=RSDP_SIGNATURE,
               cpuid=None, rsdp_bytes=None, rsdp_address=None, stop_markers=None,
               max_instructions=None, patch=None, **overrides):
    """Boot the kernel in the harness and report what it printed.

    `stop_markers` replaces the default pair, and passing `()` means "do not stop early": the
    harness's markers are how an ordinary run ends, so clearing them is the only way to ask what
    the guest does *after* the boot completes -- which is where the IST delivery probe lives, and
    where an environment that cannot take a software interrupt shows it.  With no markers the run
    is bounded by `max_instructions` instead, and a caller that expects the guest to fault must
    say how far to let it go rather than inherit the boot's budget.
    """
    machine = Machine(kernel, rsdp_signature=rsdp_signature, cpuid=cpuid,
                      rsdp_bytes=rsdp_bytes, rsdp_address=rsdp_address)
    if memmap is not None:
        overrides.setdefault("memmap_bytes", DESC_SIZE * len(memmap))
    if stop_markers is not None:
        machine.stop_markers = tuple(stop_markers)
    run_kwargs = {}
    if max_instructions is not None:
        run_kwargs["max_instructions"] = max_instructions
    result = machine.run(pack_boot_info_for(kernel, **overrides), paging=paging,
                         memmap=memmap, patch=patch, **run_kwargs)
    result["machine"] = machine
    return result
