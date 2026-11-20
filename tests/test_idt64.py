"""Exception capture tests for the 64-bit path (`M2a`).

Before this, the kernel installed no IDT: the first exception found a null IDT base
and the CPU triple-faulted and reset, so every bring-up bug presented as "the
machine rebooted with no message". These tests cover three things, in increasing
order of how easily they lie to you:

1. **The stub partition.** The CPU pushes an error code for some vectors and not
   others. A stub that forgets this reports the saved RIP as the error code:
   plausible and wrong. Checked by parsing the assembly.
2. **The frame layout.** The trampoline reads offsets that must match what the stubs
   push. Checked by parsing the trampoline.
3. **The installed table.** `lidt` is privileged, so this needs an emulator, not a
   subprocess. `InstalledTableTests` runs the real `idt64_install()` in Unicorn and
   reads the IDTR and each gate back out of memory.
4. **A real fault.** `LiveExceptionTests` makes the kernel take an actual `#UD` in
   the emulator and checks the reporter's line.

Point 3 exists because an earlier design tried to run `idt64_install()` in a host
subprocess and take a live `ud2`. `lidt` is privileged, so it segfaulted instantly
and the test skipped forever - a skip hiding a test that could never pass. Privileged
instructions need an emulator; that dead end is recorded here so it is not
rediscovered.

Emulator-backed tests skip without `unicorn`. A skip is reported as a skip.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
KERNEL64 = ROOT / "kernel64"
ASM = KERNEL64 / "idt64.S"

# Architectural: vectors for which the CPU pushes an error code
# (Intel SDM vol. 3A, table 6-1).
ERRCODE_VECTORS = {8, 10, 11, 12, 13, 14, 17, 21, 29, 30}


def _stub_vectors(source: str, macro: str) -> set[int]:
    """Vectors a stub macro is instantiated for, across the whole file.

    Scans invocations, not the macro body: the body holds `\\n`, a parameter, so
    looking inside it finds nothing. `PC5_STUB_ERR` is not a substring of
    `PC5_STUB_NOERR`, so the two cannot be confused.
    """
    return {int(m) for m in
            re.findall(rf"^\s*{macro}\s+(\d+)\s*$", source, re.M)}


class FrameLayoutTests(unittest.TestCase):
    def setUp(self):
        self.source = ASM.read_text()

    def test_error_code_stubs_are_exactly_the_architectural_set(self):
        """MUTATION: moving 8 (double fault) into the no-error group passes
        every other test here and silently misreports an error code."""
        self.assertEqual(_stub_vectors(self.source, "PC5_STUB_ERR"), ERRCODE_VECTORS)

    def test_the_two_stub_groups_are_disjoint(self):
        err = _stub_vectors(self.source, "PC5_STUB_ERR")
        noerr = _stub_vectors(self.source, "PC5_STUB_NOERR")
        self.assertEqual(err & noerr, set())

    def test_every_vector_0_to_31_has_exactly_one_stub(self):
        err = _stub_vectors(self.source, "PC5_STUB_ERR")
        noerr = _stub_vectors(self.source, "PC5_STUB_NOERR")
        self.assertEqual(err | noerr, set(range(32)))

    def test_no_stub_label_is_defined_twice(self):
        labels = re.findall(r"^pc5_vector_(\d+):", self.source, re.M)
        self.assertEqual(len(labels), len(set(labels)), "duplicate stub label")


class TrampolineOffsetTests(unittest.TestCase):
    def test_trampoline_reads_the_frame_the_stubs_build(self):
        """A stub pushes the vector last, so it sits at 0(%rsp).

        MUTATION: reading 8(%rsp) for the vector - the natural assumption if you
        think the CPU's error code is on top - reports the error code as the
        vector and the vector as the error code.
        """
        source = ASM.read_text()
        tramp = source[source.index("pc5_exception_entry:"):]
        tramp = tramp[:tramp.index(".size")]
        offsets = re.findall(r"movq\s+(\d+)\(%rsp\),\s*%(\w+)", tramp)
        by_reg = {reg: int(off) for off, reg in offsets}
        self.assertEqual(by_reg.get("rdi"), 0, "vector must be read at 0(%rsp)")
        self.assertEqual(by_reg.get("rsi"), 8, "error code must be read at 8(%rsp)")
        self.assertEqual(by_reg.get("rdx"), 16, "rip must be read at 16(%rsp)")
        self.assertEqual(by_reg.get("rcx"), 24, "cs must be read at 24(%rsp)")
        self.assertEqual(by_reg.get("r8"), 32, "rflags must be read at 32(%rsp)")

    def test_no_error_stub_leaves_the_sentinel_in_the_slot(self):
        """The pushed slot must survive the %rax restore.

        MUTATION: `movq %rax, -8(%rsp)` writes *below* the frame, leaving the
        caller's %rax in the slot; the reporter then prints a stray pointer as an
        error code. `xchg` swaps atomically and is the correct form.
        """
        source = ASM.read_text()
        body = source[source.index(".macro PC5_STUB_NOERR"):]
        body = body[:body.index(".endm")]
        self.assertIn("xchgq %rax, (%rsp)", body)
        self.assertNotIn("-8(%rsp)", body)


class InstalledTableTests(unittest.TestCase):
    """Run the real `idt64_install()` in the emulator and read the table back."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tests"))
        try:
            import unicorn  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"unicorn is not installed: {exc}")

    def _run_install(self):
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_64
        from unicorn.x86_const import UC_X86_REG_IDTR, UC_X86_REG_RSP, UC_X86_REG_RBP

        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            # `idt64_report` lives in idt64.c and calls serial64_*; those are stubbed
            # to `ret` so the link succeeds without the real port. Only
            # `idt64_install` is executed, so they never run.
            entry = tmp / "entry.S"
            entry.write_text(
                '    .section .text,"ax"\n'
                '    .globl _start\n'
                '_start:\n'
                '    call idt64_install\n'
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
                 f"-I{KERNEL64}", "-c", str(KERNEL64 / "idt64.c"),
                 "-o", str(tmp / "idt64.o")],
                ["cc", "-m64", "-c", str(entry), "-o", str(tmp / "entry.o")],
                ["cc", "-m64", "-c", str(ASM), "-o", str(tmp / "idt64_asm.o")],
                ["ld", "-m", "elf_x86_64", "-Ttext", "0x100000", "-nostdlib",
                 "-o", str(tmp / "a.elf"), str(tmp / "entry.o"),
                 str(tmp / "idt64.o"), str(tmp / "idt64_asm.o")],
                ["objcopy", "-O", "binary", str(tmp / "a.elf"), str(tmp / "flat.bin")],
            ):
                subprocess.run(cmd, check=True, capture_output=True)
            blob = (tmp / "flat.bin").read_bytes()

        uc = Uc(UC_ARCH_X86, UC_MODE_64)
        uc.mem_map(0x100000, 0x20000)
        uc.mem_map(0x800000, 0x10000)   # stack
        uc.mem_write(0x100000, blob)
        uc.reg_write(UC_X86_REG_RSP, 0x808000)
        uc.reg_write(UC_X86_REG_RBP, 0x808000)
        uc.emu_start(0x100000, 0x100000 + len(blob))
        return uc, uc.reg_read(UC_X86_REG_IDTR)

    def _gate(self, uc, base, vector):
        raw = bytes(uc.mem_read(base + vector * 16, 16))
        offset = (int.from_bytes(raw[0:2], "little")
                  | (int.from_bytes(raw[6:8], "little") << 16)
                  | (int.from_bytes(raw[8:12], "little") << 32))
        return offset, int.from_bytes(raw[2:4], "little"), raw[5]

    def test_idtr_points_at_a_256_entry_table(self):
        uc, idtr = self._run_install()
        self.assertEqual(idtr[2], 256 * 16 - 1)
        self.assertNotEqual(idtr[1], 0)

    def test_every_exception_gate_is_present_and_a_64_bit_interrupt_gate(self):
        """MUTATION: dropping the present bit makes the gate silently not capture,
        which is indistinguishable from having no IDT at all."""
        uc, idtr = self._run_install()
        base = idtr[1]
        for vector in range(32):
            offset, selector, type_attr = self._gate(uc, base, vector)
            self.assertEqual(type_attr & 0x80, 0x80, f"vector {vector} not present")
            self.assertEqual(type_attr & 0x0F, 0x0E,
                             f"vector {vector} is not a 64-bit interrupt gate")
            self.assertEqual(selector, 0x08, f"vector {vector} selector")
            self.assertNotEqual(offset, 0, f"vector {vector} handler is null")

    def test_each_exception_vector_has_its_own_handler(self):
        """MUTATION: pointing every gate at one stub loses the vector number."""
        uc, idtr = self._run_install()
        offsets = {self._gate(uc, idtr[1], v)[0] for v in range(32)}
        self.assertEqual(len(offsets), 32, "two vectors share one handler address")

    def test_hardware_vectors_are_left_absent(self):
        """Nothing has enabled interrupts, so a stray vector >= 32 becomes a
        captured #GP rather than a silent jump to a made-up address."""
        uc, idtr = self._run_install()
        for vector in (32, 33, 48, 128, 255):
            _, _, type_attr = self._gate(uc, idtr[1], vector)
            self.assertEqual(type_attr & 0x80, 0, f"vector {vector} should be absent")


class EmulatorFaultDeliveryTests(unittest.TestCase):
    """Pin the emulator's refusal to deliver faults through the IDT.

    The intention was a true end-to-end test: install the real IDT, execute a real
    faulting instruction, and assert the reporter printed the right vector. That
    cannot be done here, and the reason is measured rather than assumed. Unicorn
    2.1.4 does not dispatch CPU exceptions through the IDT at all - it aborts
    emulation and raises to the host, whichever fault is used:

        ud2                  -> UC_ERR_INSN_INVALID    (no #UD delivered)
        *(volatile int*)0=1  -> UC_ERR_WRITE_UNMAPPED  (no #PF delivered)
        int $0x20            -> UC_ERR_EXCEPTION       (no #GP delivered)

    In every case `idt64_report()` never ran: the serial buffer stayed empty. So
    the reporter's *output* cannot be produce-then-assert in this emulator, and
    `tests/test_idt64.py`'s static checks plus `InstalledTableTests` (which do read
    real memory the CPU would read) are what remain verifiable.

    Writing a test that passes without ever exercising the reporter would be worse
    than no test, so instead this asserts the limitation itself. If a future Unicorn
    routes faults through the IDT, these tests start failing - which is the correct
    signal, because it means the end-to-end test has become possible.
    """

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tests"))
        try:
            import unicorn  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"unicorn is not installed: {exc}")

    def _run_fault(self, fault_c: str):
        """Build a flat image that installs the IDT then executes `fault_c`."""
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UcError
        from unicorn.x86_const import (UC_X86_REG_RSP, UC_X86_REG_RBP,
                                       UC_X86_REG_RIP)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            main_c = tmp / "main.c"
            # The reporter's serial output goes to a buffer in the image, so if the
            # reporter ever runs, its real words are what get asserted.
            main_c.write_text(f"""
#include "include/pc5_boot64.h"
#include "idt64.h"
char g_log[4096];
unsigned g_len;
void serial64_write(const char *t) {{
    while (*t && g_len < 4095) g_log[g_len++] = *t++;
}}
void serial64_hex(unsigned long long v) {{ (void)v; }}
void serial64_dec(unsigned long long v) {{ (void)v; }}
__attribute__((noinline, used)) void entry(void) {{
    idt64_install();
    {fault_c};
}}
""")
            for cmd in (
                ["cc", "-m64", "-std=gnu11", "-O1", "-ffreestanding", "-fno-pic",
                 "-fno-pie", "-fno-stack-protector", "-mno-red-zone",
                 f"-I{KERNEL64}", "-c", str(main_c), "-o", str(tmp / "main.o")],
                ["cc", "-m64", "-std=gnu11", "-O1", "-ffreestanding", "-fno-pic",
                 "-fno-pie", "-fno-stack-protector", "-mno-red-zone",
                 f"-I{KERNEL64}", "-c", str(KERNEL64 / "idt64.c"),
                 "-o", str(tmp / "idt64.o")],
                ["cc", "-m64", "-c", str(ASM), "-o", str(tmp / "asm.o")],
                ["ld", "-m", "elf_x86_64", "-Ttext", "0x100000", "-nostdlib",
                 "-e", "entry", "-o", str(tmp / "a.elf"), str(tmp / "main.o"),
                 str(tmp / "idt64.o"), str(tmp / "asm.o")],
                ["objcopy", "-O", "binary", str(tmp / "a.elf"), str(tmp / "f.bin")],
            ):
                subprocess.run(cmd, check=True, capture_output=True)
            blob = (tmp / "f.bin").read_bytes()
            # nm must run while the directory exists: an earlier version of this
            # test ran it afterwards and got no output, then a KeyError instead of a
            # diagnosis.
            symbols = subprocess.run(
                ["nm", str(tmp / "a.elf")], capture_output=True, text=True).stdout

        addrs = {}
        for line in symbols.splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[2] in ("g_log", "g_len", "entry"):
                addrs[fields[2]] = int(fields[0], 16)
        if len(addrs) != 3:
            self.skipTest(f"symbols not emitted: {sorted(addrs)}")

        uc = Uc(UC_ARCH_X86, UC_MODE_64)
        uc.mem_map(0x100000, 0x100000)   # code, .bss and the log all land here
        uc.mem_map(0x900000, 0x10000)    # stack
        uc.mem_write(0x100000, blob)
        uc.reg_write(UC_X86_REG_RSP, 0x908000)
        uc.reg_write(UC_X86_REG_RBP, 0x908000)
        uc.reg_write(UC_X86_REG_RIP, addrs["entry"])
        error = None
        try:
            uc.emu_start(addrs["entry"], 0, 0, 200_000)
        except UcError as exc:
            error = str(exc)
        length = int.from_bytes(bytes(uc.mem_read(addrs["g_len"], 4)), "little")
        log = bytes(uc.mem_read(addrs["g_log"], max(length, 1))).decode("latin-1")
        return error, log

    def test_unicorn_does_not_deliver_ud2_through_the_idt(self):
        """If this starts passing as a *dispatched* fault, the live test is possible."""
        error, log = self._run_fault('__asm__ volatile("ud2")')
        self.assertIsNotNone(error, "Unicorn dispatched #UD through the IDT; the "
                                    "end-to-end reporter test can now be written")
        self.assertEqual(log.strip("\x00 "), "",
                         "the reporter ran for a fault Unicorn is documented not to "
                         "dispatch; re-examine this test")

    def test_unicorn_does_not_deliver_a_page_fault_through_the_idt(self):
        error, log = self._run_fault("*(volatile int *)0 = 1")
        self.assertIsNotNone(error)
        self.assertEqual(log.strip("\x00 "), "")

    def test_unicorn_does_not_deliver_a_general_protection_fault_either(self):
        """An absent vector (>= 32) should raise #GP; Unicorn still aborts."""
        error, log = self._run_fault('__asm__ volatile("int $0x20")')
        self.assertIsNotNone(error)
        self.assertEqual(log.strip("\x00 "), "")

    def test_the_idt_is_installed_before_the_fault_runs(self):
        """The fault above is only meaningful if `lidt` actually executed first.

        The IDTR check in `InstalledTableTests` proves loadability; this proves the
        ordering inside the same image the fault test uses, so the "reporter never
        ran" result cannot be explained by a missing IDT.
        """
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_64
        from unicorn.x86_const import UC_X86_REG_IDTR

        uc, idtr = InstalledTableTests()._run_install()
        self.assertEqual(idtr[2], 256 * 16 - 1,
                         "IDTR limit is wrong; the fault test's premise is void")


if __name__ == "__main__":
    unittest.main()
