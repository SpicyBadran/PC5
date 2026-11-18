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

What this harness genuinely exercises, and what it does not:

  * it *does* exercise the map switch.  Unicorn in 64-bit mode translates every
    fetch and data access through CR3 whenever CR0.PG is set, and this harness
    sets it, so once the loader installs PC5's PML4 an address that PC5's tables
    do not map faults the emulation.  Instructions executed after the switch are
    therefore covered by PC5's own entries, not by anything the harness arranged;
  * it does *not* prove the map is correct for memory nothing touches during the
    run, and it does not model TLB invalidation, caching or permission faults in
    the way hardware does (Unicorn checks present/writable/user bits, not, for
    example, the PAT bit or the effect of CR0.WP);
  * it is not firmware.  It emulates the services the loader calls; `make
    ovmf-test` is the only gate that can speak about real firmware.

The map's *bounds* are checked structurally by `tests/test_pagetables64.py`, which
builds the real `pagetables.c` and inspects every entry it writes.
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
EFI_ALLOCATE_MAX_ADDRESS = 1
EFI_ALLOCATE_ADDRESS = 2
EFI_SUCCESS = 0
EFI_INVALID_PARAMETER = 0x8000000000000002
EFI_OUT_OF_RESOURCES = 0x8000000000000009

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

# Size of the revision-2 RSDP this stub publishes: the 36-byte header the ACPI
# specification defines, with the extended checksum at offset 32.
RSDP_BYTES = 36


class StubFirmware64(fw.StubFirmware):
    """A long-mode UEFI machine that can execute the 64-bit loader and kernel."""

    # The kernel is linked at 0x100000, outside the 32-bit harness's low-memory
    # window, so the machine gets that RAM as well.
    KERNEL64_WINDOW = (0x00100000, 0x00200000)
    # Where firmware places page allocations when a test asks it to allocate "just
    # below 4 GiB".  This window is 0x0FF00000..0x10000000 - the top of the first
    # 256 MiB, *not* the top of PC5's 4 GiB identity span.  It used to be described
    # as ending "exactly at the top of the identity span", which is 16x further up;
    # the comment was wrong, and the test that relied on it ("...just below the span
    # limit") therefore never approached the boundary it was named for.  The window
    # is still useful - it proves the loader copes with allocations near the top of
    # the low region - but the name and comment now say what it is.
    HIGH_WINDOW = (0x0FF00000, 0x00100000)

    # The last page of the 4 GiB identity span and the first page above it, so a
    # test can ask firmware to hand out memory that straddles the limit.
    LIMIT_WINDOW = (0x0FFFFFF000, 0x2000)

    def __init__(self, *args, high_allocations=False, allocations_at_limit=False,
                 pool_reserve=None, **options):
        # Set before the base constructor maps memory and loads the image.
        self.high_allocations = high_allocations
        self.high_next = self.HIGH_WINDOW[0]
        # When set, MaxAddress requests are served from the top of the span and the
        # pool starts there, so a multi-page request necessarily crosses 4 GiB.
        self.allocations_at_limit = allocations_at_limit
        # Bytes to leave for the loader once firmware structures exist.
        # None means the whole pool; 0 models a firmware with no memory.
        self.pool_reserve = pool_reserve
        super().__init__(*args, **options)

    def _map(self):
        super()._map()
        base, size = self.KERNEL64_WINDOW
        self.uc.mem_map(base, size)
        if self.high_allocations:
            high, high_size = self.HIGH_WINDOW
            self.uc.mem_map(high, high_size)
        if self.allocations_at_limit:
            limit, limit_size = self.LIMIT_WINDOW
            self.uc.mem_map(limit, limit_size)

    def svc_allocate_pages(self, alloc_type, memory_type, pages, memory_ptr, *_rest):
        """Honour every allocation mode, including the MaxAddress input bound."""
        if self.allocate_return is not None:
            return self.allocate_return
        if (self.allocations_at_limit
                and alloc_type == EFI_ALLOCATE_MAX_ADDRESS):
            # Firmware hands out pages from the last mapped page of the span.  Any
            # request of more than one page therefore crosses the 4 GiB limit, which
            # is exactly the lie the loader must detect for itself.
            size = pages * 4096
            address = self.LIMIT_WINDOW[0]
            if size > self.LIMIT_WINDOW[1]:
                return fw.EFI_OUT_OF_RESOURCES
            if address + size > self.LIMIT_WINDOW[0] + self.LIMIT_WINDOW[1]:
                return fw.EFI_OUT_OF_RESOURCES
            self.uc.mem_write(address, bytes(size))
            self.allocations.append((alloc_type, pages, address))
            self.write_u64(memory_ptr, address)
            self.map_generation += 1
            return EFI_SUCCESS
        if (self.high_allocations and alloc_type == EFI_ALLOCATE_MAX_ADDRESS
                and not self.ignore_max_address):
            # Firmware may satisfy a "below 4 GiB" request anywhere inside the
            # caller's bound, including the far end of it.  Honour the bound: a
            # request whose maximum is below this window cannot be served from it.
            bound = self.read_u64(memory_ptr)
            size = pages * 4096
            address = self.high_next
            if address + size - 1 > bound:
                return fw.EFI_OUT_OF_RESOURCES
            if address + size > self.HIGH_WINDOW[0] + self.HIGH_WINDOW[1]:
                return fw.EFI_OUT_OF_RESOURCES
            self.high_next = address + size
            self.uc.mem_write(address, bytes(size))
            self.allocations.append((alloc_type, pages, address))
            self.write_u64(memory_ptr, address)
            self.map_generation += 1
            return EFI_SUCCESS
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

    def pool_ceiling(self):
        """The high window is outside the base pool, so it needs its own ceiling."""
        if self.allocation_limit is not None:
            return self.allocation_limit
        return fw.POOL_END

    def memory_descriptors(self):
        """Firmware's map, widened to describe the memory this machine hands out.

        The 32-bit harness reports 1 MiB of conventional memory at 0x100000; the
        64-bit kernel is linked there *and* the loader allocates its boot info,
        page tables and stack from the same window, so the map must describe the
        whole window or the kernel would be right to reject a stack it cannot see.
        Widening the descriptor is what real firmware does - the map describes
        memory that exists, not memory the loader happens to ask for.
        """
        descriptors = super().memory_descriptors()
        window = [entry for entry in descriptors if entry[1] == self.KERNEL64_WINDOW[0]
                  and entry[0] == 7]
        assert len(window) == 1, window
        descriptors[descriptors.index(window[0])] = (
            7, self.KERNEL64_WINDOW[0], 0x0, self.KERNEL64_WINDOW[1] // 4096)
        if self.high_allocations:
            # Firmware retypes what it hands out; these pages are LoaderData, and
            # the kernel must accept them as loader-owned rather than demanding
            # conventional memory it no longer is.
            descriptors.append((2, self.HIGH_WINDOW[0], 0x0,
                                self.HIGH_WINDOW[1] // 4096))
        return descriptors

    def _build_tables(self):
        super()._build_tables()
        # --- ACPI configuration table: one entry pointing at a real RSDP.
        # A genuinely well-formed revision-2 RSDP, not merely a signature.  The
        # kernel validates both checksums and the declared length, so a stub that
        # only wrote "RSD PTR " would be modelling a corrupt table - and the old
        # version of this code did exactly that: it put 0x02 in the checksum byte
        # (offset 8) and left revision 0 at offset 15 with no length field, so the
        # "valid" RSDP the loader handed over could not have passed a real check.
        #
        # Offsets: 0 signature, 8 checksum, 9 OEMID(6), 15 revision, 16 RSDT,
        #          20 length, 24 XSDT, 32 extended checksum, 33.. reserved.
        rsdp = self.alloc_or_raise(RSDP_BYTES)
        table = bytearray(b"\x00" * RSDP_BYTES)
        table[0:8] = b"RSD PTR "
        table[9:15] = b"PC5STB"                         # OEMID
        table[15] = 2                                   # revision 2 (ACPI 2.0+)
        table[20:24] = struct.pack("<I", RSDP_BYTES)    # length of this structure
        table[32] = 0                                   # extended checksum, fixed below
        # Every byte of the first 20 summed into byte 8 must be zero, and likewise
        # every byte of the declared length summed into byte 32.
        table[8] = (-sum(table[0:20])) & 0xFF
        table[32] = (-sum(table[0:RSDP_BYTES])) & 0xFF
        self.write(rsdp, bytes(table))
        entry = self.alloc_or_raise(24)
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
        # Firmware's own structures are built by now, so a test may restrict what
        # is left for the loader.  Until this ran, exhausting the pool would have
        # broken the harness rather than modelling a firmware without memory.
        if self.pool_reserve is not None:
            self.pool_next = max(self.pool_next, self.pool_ceiling() - self.pool_reserve)
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
    def _firmware_regions(self):
        """(base, page_count) pairs firmware's own map must cover.

        Firmware maps what it uses: the first 4 GiB (where the image, pool,
        framebuffer, low memory and kernel window live) plus, when the test placed
        the application higher, the 2 MiB window containing it.  Firmware has no
        reason to map more than that, and neither does this harness.
        """
        regions = [("low", 0, 2048)]
        image = self.image_load
        if image + fw.IMAGE_WINDOW > 4 << 30:
            regions.append(("image", image & ~0x1FFFFF, 1))
        return regions

    def _install_firmware_paging(self):
        """Give the emulated machine firmware's own identity map; return its PML4.

        Unicorn walks the guest page tables whenever CR0.PG is set, exactly as a
        machine does, which is why this harness cannot invent a CR3.  The map
        built here is firmware's, covering only the windows listed above with
        2 MiB pages.  When the loader installs PC5's PML4, translation switches to
        that table: an address PC5 did not map faults, and that fault is evidence
        about PC5's map rather than about this harness.
        """
        base = self.alloc_or_raise(6 * 4096, align=4096)
        pml4 = base        # one PDPT (pre-allocated) plus a PD per 1 GiB chunk
        pdpts = {}
        tables = 0
        for name, region_base, pages in self._firmware_regions():
            remaining = pages
            cursor = region_base
            while remaining:
                chunk = min(remaining, 512)
                pml4_index = (cursor >> 39) & 0x1FF
                pdpt_index = (cursor >> 30) & 0x1FF
                if pml4_index not in pdpts:
                    pdpts[pml4_index] = self.alloc_or_raise(4096, align=4096)
                    self.write_u64(pml4 + pml4_index * 8, pdpts[pml4_index] | 0x3)
                pd = self.alloc_or_raise(4096, align=4096)
                self.write_u64(pdpts[pml4_index] + pdpt_index * 8, pd | 0x3)
                entries = [((cursor + index * 0x200000) | 0x83) for index in range(chunk)]
                self.write(pd, struct.pack("<%dQ" % chunk, *entries))
                tables += 1
                cursor += chunk * 0x200000
                remaining -= chunk
        self.firmware_pml4 = pml4
        self.trace.note("firmware_paging", pml4=hex(pml4), tables=tables,
                        regions=[(name, hex(base_), pages)
                                 for name, base_, pages in self._firmware_regions()])
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
