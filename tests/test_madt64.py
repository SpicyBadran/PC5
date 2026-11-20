"""MADT CPU enumeration tests for the 64-bit path (`M2a`).

`madt64_scan()` walks RSDP -> XSDT/RSDT -> MADT, all of which are firmware-published
structures in physical memory. The tests therefore build **synthetic ACPI tables** in
the emulator and let the real `kernel64/madt64.c` walk them, which is the only way to
exercise the case that matters: a table that is hostile or malformed.

A firmware table is untrusted input. It is a pointer published by something this
kernel did not write, and the classic ways to turn enumeration into a fault are:

* a length field larger than the buffer, so the walk reads past the table;
* a record length of zero, so the walk never advances;
* a record length that overlaps the next record, so records are misread;
* a pointer into unmapped memory, so the first dereference faults;
* more processors than the caller's array holds, so the copy overruns.

Each of those has a test below, and each names the mutation it catches. The success
path is tested too, because a walker that refuses everything would pass every
malformed-input test and be useless.
"""
from __future__ import annotations

import pathlib
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
KERNEL64 = ROOT / "kernel64"

# Where the synthetic tables live in the emulator. Inside a page the harness maps.
# Large enough that a correct walk always finishes, small enough that a hang is
# reported in well under a second.
INSTRUCTION_BUDGET = 200_000
HALT_ADDR = None   # resolved after linking, by disassembly


def _find_halt(elf_path):
    """Address of the `hlt` that follows the `call madt64_scan` in the entry shim."""
    import subprocess
    text = subprocess.run(["objdump", "-d", str(elf_path)], capture_output=True,
                          text=True, check=True).stdout
    seen_call = False
    for line in text.splitlines():
        if "call" in line and "madt64_scan" in line:
            seen_call = True
            continue
        if seen_call and "hlt" in line:
            return int(line.split(":")[0].strip(), 16)
    raise AssertionError("no `hlt` found after the call to madt64_scan")

RSDP_ADDR = 0x200000
XSDT_ADDR = 0x201000
MADT_ADDR = 0x202000
TABLES_BASE = 0x200000
TABLES_SIZE = 0x40000


def _rsdp(xsdt_addr, revision=2, length=36, rsdt_addr=0):
    """A revision-2 RSDP pointing at `xsdt_addr`."""
    body = bytearray(36)
    body[0:8] = b"RSD PTR "
    body[9] = revision
    body[16:20] = struct.pack("<I", rsdt_addr)   # RSDT address: present in every revision
    body[20:24] = struct.pack("<I", length)
    body[24:32] = struct.pack("<Q", xsdt_addr)
    # A correct checksum is not needed: madt64_scan trusts the caller to have
    # validated the RSDP, and main.c does that before calling it. Doing it here would
    # test the checksum code, which tests/test_kernel64.py already covers.
    return bytes(body)


def _sdt(signature, length, body=b""):
    """An ACPI system-description-table header plus body."""
    header = bytearray(36)
    header[0:4] = signature
    header[4:8] = struct.pack("<I", length)
    header[8] = 2
    return bytes(header) + body


def _xsdt(entries):
    """An XSDT: a 36-byte SDT header, then the 8-byte pointer array.

    The first version of this builder inserted an extra 4-byte revision field so the
    array began at offset 40. It only worked because the walker was *also* starting its
    scan at the wrong offset, so the two mistakes cancelled - and that is exactly how a
    walker that never parsed the array correctly passed its tests.
    """
    payload = b"".join(struct.pack("<Q", address) for address in entries)
    return _sdt(b"XSDT", 36 + len(payload), payload)


def _rsdt(entries):
    """An RSDT: 36-byte SDT header, then 4-byte pointers."""
    payload = b"".join(struct.pack("<I", address) for address in entries)
    return _sdt(b"RSDT", 36 + len(payload), payload)


def _madt(records, local_apic_address=0xFEE00000):
    body = struct.pack("<I", local_apic_address) + struct.pack("<I", 0)
    return _sdt(b"APIC", 36 + len(body) + len(records), body + records)


def _cpu_record(acpi_id, apic_id, enabled=True, size=8):
    rec = bytearray(size)
    rec[0] = 0                      # type: processor local APIC
    rec[1] = size
    rec[2] = acpi_id
    rec[3] = apic_id
    rec[4:8] = struct.pack("<I", 1 if enabled else 0)
    return bytes(rec)


def _x2apic_record(apic_id, enabled=True):
    rec = bytearray(16)
    rec[0] = 9
    rec[1] = 16
    rec[4:8] = struct.pack("<I", apic_id)
    rec[8:12] = struct.pack("<I", 1 if enabled else 0)
    return bytes(rec)


class MadtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import unicorn  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(f"unicorn is not installed: {exc}")
        sys.path.insert(0, str(ROOT / "tests"))
        cls._build()

    @classmethod
    def _build(cls):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            entry = tmp / "entry.S"
            # The entry shim reads the arguments from a fixed mailbox so the test can
            # call the real function with controlled values.
            entry.write_text(
                '    .section .text,"ax"\n'
                '    .globl _start\n'
                '_start:\n'
                '    movq 0x180000, %rdi        /* out */\n'
                '    movq 0x180008, %rsi        /* rsdp address */\n'
                '    movq 0x180010, %rdx        /* map pointer */\n'
                '    call madt64_scan\n'
                '    hlt\n'
                '    .globl serial64_write\n'
                'serial64_write:\n    ret\n'
                '    .globl serial64_hex\n'
                'serial64_hex:\n    ret\n'
                '    .globl serial64_dec\n'
                'serial64_dec:\n    ret\n'
                '    .section .note.GNU-stack,"",@progbits\n')
            for cmd in (
                ["cc", "-m64", "-std=gnu11", "-O1", "-ffreestanding", "-fno-pic",
                 "-fno-pie", "-fno-stack-protector", "-mno-red-zone",
                 f"-I{KERNEL64}", "-c", str(KERNEL64 / "madt64.c"),
                 "-o", str(tmp / "madt64.o")],
                ["cc", "-m64", "-c", str(entry), "-o", str(tmp / "entry.o")],
                ["ld", "-m", "elf_x86_64", "-Ttext", "0x100000", "-nostdlib",
                 "-e", "_start", "-o", str(tmp / "a.elf"), str(tmp / "entry.o"),
                 str(tmp / "madt64.o")],
                ["objcopy", "-O", "binary", str(tmp / "a.elf"), str(tmp / "flat.bin")],
            ):
                import subprocess as sp
                sp.run(cmd, check=True, capture_output=True)
            cls.blob = (tmp / "flat.bin").read_bytes()
            global HALT_ADDR
            HALT_ADDR = _find_halt(tmp / "a.elf")

    def _run(self, memmap_covers=((TABLES_BASE, TABLES_SIZE),), rsdp=RSDP_ADDR,
             writes=None):
        """Run madt64_scan() with a synthetic memory map and table set.

        `memmap_covers` lists the ranges the fake memory map claims to cover. Passing
        a narrower range is how the "table outside mapped memory" cases are built: the
        bytes are present in the emulator either way, so only the map check can
        catch it.
        """
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_64
        from unicorn.x86_const import UC_X86_REG_RSP, UC_X86_REG_RIP

        uc = Uc(UC_ARCH_X86, UC_MODE_64)
        uc.mem_map(0x100000, 0x100000)     # code, .bss, mailbox
        uc.mem_map(0x800000, 0x10000)      # stack
        # The table region is always *mapped in the emulator* - only the fake memory
        # map decides whether madt64_scan() is allowed to read it. Keeping the two
        # separate is what makes the "refuses a table the map does not cover" tests
        # meaningful: the bytes are genuinely there, so only the map check can catch
        # a missing bounds check.
        uc.mem_map(0x200000, 0x100000)
        uc.mem_write(0x100000, self.blob)

        # A map descriptor array in the emulator that mirrors `memmap_covers`, laid
        # out exactly as pc5_memdesc64 / pc5_map_view expect.
        # `struct pc5_memdesc64` is {u64 physical_start; u64 page_count; u64 attributes;
        # u32 type; u32 reserved} = 32 bytes packed. Type 9 is ACPI_RECLAIM, which is
        # in PC5_MMAP_FIRMWARE_DATA_TYPES - the set madt64_scan() accepts.
        desc_addr = 0x190000
        for index, (start, size) in enumerate(memmap_covers):
            pages = max(1, size // 4096)
            uc.mem_write(desc_addr + index * 32,
                         struct.pack("<QQQII", start, pages, 0, 9, 0))
        # `struct pc5_map_view` is {const struct pc5_memdesc64 *descriptors; u64 entries}.
        view_addr = 0x188000
        uc.mem_write(view_addr, struct.pack("<QQ", desc_addr, len(memmap_covers)))

        for address, payload in (writes or {}).items():
            uc.mem_write(address, payload)

        out_addr = 0x1A0000
        uc.mem_write(0x180000, struct.pack("<Q", out_addr))
        uc.mem_write(0x180008, struct.pack("<Q", rsdp))
        uc.mem_write(0x180010, struct.pack("<Q", view_addr))

        uc.reg_write(UC_X86_REG_RSP, 0x808000)
        uc.reg_write(UC_X86_REG_RIP, 0x100000)
        # Stop at the `hlt` that follows the `call`. Passing it as `until` means a walk
        # that never returns simply never reaches it, emulation runs to the
        # instruction budget and stops - which is checked below. The first version of
        # this harness passed a budget and then read the output regardless, so an
        # infinite loop (a zero-length MADT record) looked exactly like a pass. That
        # mutation survived because of it.
        try:
            uc.emu_start(0x100000, HALT_ADDR, 0, INSTRUCTION_BUDGET)
        except Exception as exc:            # a fault is a test failure, not a skip
            self.fail(f"madt64_scan faulted: {exc}")
        rip = uc.reg_read(UC_X86_REG_RIP)
        if rip != HALT_ADDR:
            self.fail(
                "madt64_scan did not return within the instruction budget "
                f"(stopped at rip=0x{rip:x}, expected the hlt at 0x{HALT_ADDR:x}); "
                "a zero-length or non-advancing record probably made the walk loop "
                "forever")

        # `struct pc5_madt64` is nine u32s (36 bytes) then `struct pc5_cpu_entry[64]`.
        # Each entry is {u8 acpi_processor_id; u8 apic_id; u32 enabled} = 8 bytes once
        # padded, so the array starts at 36 and the whole structure is 36 + 512.
        raw = bytes(uc.mem_read(out_addr, 36 + 64 * 8))
        parsed, revision, used_xsdt, lapic = struct.unpack_from("<IIII", raw, 0)
        cpu_count, cpu_total, x2, truncated = struct.unpack_from("<IIII", raw, 16)
        table_bytes = struct.unpack_from("<I", raw, 32)[0]
        cpus = []
        for index in range(cpu_count):
            base = 36 + index * 8
            acpi_id, apic_id = raw[base], raw[base + 1]
            cpus.append((acpi_id, apic_id))
        return dict(parsed=parsed, revision=revision, used_xsdt=used_xsdt,
                    lapic=lapic, cpu_count=cpu_count, cpu_total=cpu_total,
                    x2apic=x2, truncated=truncated, table_bytes=table_bytes,
                    cpus=cpus)

    def _standard_writes(self, madt_bytes):
        return {
            RSDP_ADDR: _rsdp(XSDT_ADDR),
            XSDT_ADDR: _xsdt([MADT_ADDR]),
            MADT_ADDR: madt_bytes,
        }

    # -- success path -----------------------------------------------------

    def test_one_cpu_is_reported_as_one_cpu(self):
        """A single-CPU machine must not be reported as multi-CPU.

        This is the `M2a` criterion that asks for "a second CPU ... or the absence of
        one reported", so getting 1 right is the whole point, not a trivial case.
        """
        madt = _madt(_cpu_record(0, 0))
        r = self._run(writes=self._standard_writes(madt))
        self.assertEqual(r["parsed"], 1)
        self.assertEqual(r["cpu_count"], 1)
        self.assertEqual(r["cpu_total"], 1)
        self.assertEqual(r["cpus"], [(0, 0)])
        self.assertEqual(r["lapic"], 0xFEE00000)

    def test_two_cpus_are_enumerated_by_apic_id(self):
        madt = _madt(_cpu_record(0, 3) + _cpu_record(1, 7))
        r = self._run(writes=self._standard_writes(madt))
        self.assertEqual(r["cpu_count"], 2)
        self.assertEqual(r["cpus"], [(0, 3), (1, 7)])

    def test_disabled_cpus_are_counted_but_not_listed(self):
        """A disabled record must not become a phantom CPU."""
        madt = _madt(_cpu_record(0, 0) + _cpu_record(1, 1, enabled=False))
        r = self._run(writes=self._standard_writes(madt))
        self.assertEqual(r["cpu_count"], 1)
        self.assertEqual(r["cpu_total"], 2)
        self.assertEqual(r["cpus"], [(0, 0)])

    # -- hostile input ----------------------------------------------------

    def test_a_zero_length_record_stops_the_walk(self):
        """MUTATION: `offset += record_bytes` with no minimum-length check spins
        forever on a zero-length record."""
        bad = _cpu_record(0, 0) + bytes([0, 0])   # type 0, length 0
        r = self._run(writes=self._standard_writes(_madt(bad)))
        # It parsed the header and one good record, then stopped rather than hanging.
        self.assertEqual(r["parsed"], 1)
        self.assertEqual(r["cpu_total"], 1)

    def test_a_record_running_past_the_table_stops_the_walk(self):
        """MUTATION: dropping the `offset + record_bytes > madt_bytes` check reads
        past the table into whatever follows it."""
        rec = bytearray(_cpu_record(0, 0))
        rec[1] = 64                               # claims to extend past the table
        r = self._run(writes=self._standard_writes(_madt(bytes(rec))))
        self.assertEqual(r["cpu_total"], 0,
                         "a record overrunning the table must be rejected")

    def test_a_madt_length_below_its_header_is_refused(self):
        """MUTATION: reading the local-APIC address before the length check reads
        bytes outside the table."""
        madt = bytearray(_madt(_cpu_record(0, 0)))
        madt[4:8] = struct.pack("<I", 20)          # shorter than a 44-byte header
        r = self._run(writes=self._standard_writes(bytes(madt)))
        self.assertEqual(r["parsed"], 0)

    def test_a_madt_longer_than_mapped_memory_is_refused(self):
        """MUTATION: trusting the length field without a map check lets firmware
        point the walk at memory it must not read.

        The map is built to cover everything the walk legitimately touches - the RSDP,
        the XSDT, and a window starting at the MADT - so the *only* thing that can
        fail is the MADT's own declared length reaching past that window. An earlier
        version of this test used a map that already excluded the XSDT, so the walk
        died in `find_table()` and never reached the MADT check at all: the mutation
        removing that check survived because the test was passing for the wrong
        reason.
        """
        madt = bytearray(_madt(_cpu_record(0, 0)))
        madt[4:8] = struct.pack("<I", 0x8000)      # claims 32 KiB
        # Covers RSDP + XSDT (0x200000..0x201fff) and only 4 KiB from the MADT.
        r = self._run(memmap_covers=((0x200000, 0x2000), (MADT_ADDR, 0x1000)),
                      writes=self._standard_writes(bytes(madt)))
        self.assertEqual(r["parsed"], 0,
                         "a MADT whose length exceeds mapped memory must be refused")

    def test_an_rsdp_in_unmapped_memory_is_refused(self):
        """MUTATION: without its own map check the RSDP is dereferenced on trust.

        Every *other* structure the walk touches stays mapped, so this test cannot be
        satisfied by a later check happening to fail - only by the RSDP check itself.
        The test that first covered this pointed the map at an unrelated range, which
        also excluded the XSDT, so the walk failed downstream and the mutation
        survived.
        """
        r = self._run(
            memmap_covers=((XSDT_ADDR, 0x2000),),   # everything except the RSDP page
            writes=self._standard_writes(_madt(_cpu_record(0, 0))))
        self.assertEqual(r["parsed"], 0,
                         "an RSDP outside mapped memory must be refused")

    def test_a_table_pointer_into_unmapped_memory_is_not_followed(self):
        """MUTATION: dropping the per-table map check in `find_table()` makes the walk
        read a firmware-supplied pointer before knowing it is readable.

        The pointer aims at memory that is neither in the memory map nor mapped in the
        emulator, so with the check removed the signature test faults outright. An
        earlier version of this test put a valid-looking decoy in emulator-mapped
        memory; that survived the mutation, because the final MADT length check
        rejected the decoy for an unrelated reason. The check is partly redundant with
        that final check - this test pins the property that matters, which is that the
        *first* read of an untrusted pointer is bounded.
        """
        unroutable = 0x50000000
        writes = {
            RSDP_ADDR: _rsdp(XSDT_ADDR),
            XSDT_ADDR: _xsdt([unroutable]),
        }
        r = self._run(memmap_covers=((RSDP_ADDR, 0x2000),), writes=writes)
        self.assertEqual(r["parsed"], 0)

    def test_a_revision_zero_rsdp_is_walked_through_the_rsdt(self):
        """The RSDT path must work: a revision-0 machine has no XSDT at all.

        Without this, the revision-0 branch could be deleted and every other test
        would still pass - the code would simply be unreachable, which tests cannot
        tell apart from correct.
        """
        rsdt_addr = 0x203000
        writes = {
            RSDP_ADDR: _rsdp(0, revision=0, length=20, rsdt_addr=rsdt_addr),
            rsdt_addr: _rsdt([MADT_ADDR]),
            MADT_ADDR: _madt(_cpu_record(0, 0) + _cpu_record(1, 2)),
        }
        r = self._run(memmap_covers=((RSDP_ADDR, 0x8000),), writes=writes)
        self.assertEqual(r["parsed"], 1)
        self.assertEqual(r["used_xsdt"], 0)
        self.assertEqual(r["cpu_count"], 2)

    def test_a_revision_zero_rsdp_outside_the_map_is_refused(self):
        """MUTATION: dropping the RSDP map check.

        A revision-0 RSDP takes the legacy path only, so the revision-2 check is never
        reached and cannot stand in for it. Everything the walk reads *after* the RSDP
        stays covered, so only this check can stop it. That makes it the one case where
        the legacy check is observable alone - for a revision-2 RSDP the two checks are
        redundant and removing either one still fails closed.
        """
        rsdt_addr = 0x203000
        writes = {
            RSDP_ADDR: _rsdp(0, revision=0, length=20, rsdt_addr=rsdt_addr),
            rsdt_addr: _rsdt([MADT_ADDR]),
            MADT_ADDR: _madt(_cpu_record(0, 0)),
        }
        r = self._run(memmap_covers=((XSDT_ADDR, 0x4000),), writes=writes)
        self.assertEqual(r["parsed"], 0,
                         "an RSDP outside mapped memory must be refused")

    def test_a_revision_two_rsdp_declaring_a_short_length_is_refused(self):
        """MUTATION: dropping the `rsdp_length < RSDP_V2_MIN_LENGTH` check.

        The memory map works in pages, so it cannot express "the first 20 bytes but
        not the next 16" - which means the *map* half of this check is not observable
        from here (and is redundant with the legacy check for any page-aligned RSDP).
        The *length* half is: a revision-2 RSDP whose own length field says 20 is
        self-contradictory, and the walk must refuse rather than trust a pointer that
        lies outside the region the structure claims for itself.
        """
        writes = {
            RSDP_ADDR: _rsdp(XSDT_ADDR, revision=2, length=20),
            XSDT_ADDR: _xsdt([MADT_ADDR]),
            MADT_ADDR: _madt(_cpu_record(0, 0)),
        }
        r = self._run(memmap_covers=((RSDP_ADDR, 0x8000),), writes=writes)
        self.assertEqual(r["parsed"], 0,
                         "a revision-2 RSDP declaring fewer than 36 bytes must be refused")

    def test_a_null_rsdp_is_refused(self):
        """MUTATION: no null check, so offset 16 is read from address zero."""
        r = self._run(rsdp=0, writes=self._standard_writes(_madt(_cpu_record(0, 0))))
        self.assertEqual(r["parsed"], 0)

    def test_more_cpus_than_the_array_holds_sets_truncated(self):
        """MUTATION: copying without a capacity check overruns the caller's array and
        corrupts whatever follows it in the structure."""
        records = b"".join(_cpu_record(i & 0xFF, i & 0xFF) for i in range(70))
        r = self._run(writes=self._standard_writes(_madt(records)))
        self.assertEqual(r["cpu_count"], 64)
        self.assertEqual(r["truncated"], 1)

    def test_all_cpus_fit_without_truncation(self):
        """The counterpart: exactly 64 must NOT be flagged truncated."""
        records = b"".join(_cpu_record(i & 0xFF, i & 0xFF) for i in range(64))
        r = self._run(writes=self._standard_writes(_madt(records)))
        self.assertEqual(r["cpu_count"], 64)
        self.assertEqual(r["truncated"], 0)

    def test_x2apic_records_are_counted_separately_not_folded_in(self):
        """MUTATION: treating an x2APIC record as a local-APIC record truncates a
        32-bit id into a byte and reports a plausible wrong apic_id."""
        records = _cpu_record(0, 0) + _x2apic_record(0x1234)
        r = self._run(writes=self._standard_writes(_madt(records)))
        self.assertEqual(r["cpu_count"], 1)
        self.assertEqual(r["x2apic"], 1)
        self.assertEqual(r["cpus"], [(0, 0)], "x2APIC must not add a local-APIC row")


if __name__ == "__main__":
    unittest.main()
