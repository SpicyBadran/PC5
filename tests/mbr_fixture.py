"""Hybrid-MBR fixtures shared by the unit tests.

Two things the tests need and neither of them is a production code path:

  synthetic()   a 440-byte stand-in for boot/mbr.S, for tests that only care
                that the builder was given *something* of the right size;
  assemble()    the real boot/mbr.S, assembled with the same as/ld invocation
                the Makefile uses, for tests that inspect or boot it.
"""

import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MBR_CODE_BYTES = 440


def synthetic():
    """440 deterministic bytes that are not all zero (nor all 0x90)."""
    return b"\xfa\xf4" + bytes(range(1, 256)) + bytes(MBR_CODE_BYTES - 257)


def assemble(dest_dir=None):
    """Assemble and link boot/mbr.S; returns the 440 bytes it produces."""
    tmp = None
    if dest_dir is None:
        tmp = tempfile.mkdtemp()
        dest_dir = tmp
    try:
        obj = os.path.join(dest_dir, "mbr.o")
        binary = os.path.join(dest_dir, "mbr.bin")
        subprocess.run(["as", "--32", os.path.join(ROOT, "boot", "mbr.S"), "-o", obj],
                       check=True, capture_output=True)
        subprocess.run(["ld", "-m", "elf_i386", "-Ttext", "0x7c00", "--oformat", "binary",
                        "-e", "_start", "-o", binary, obj], check=True, capture_output=True)
        with open(binary, "rb") as fh:
            return fh.read()
    finally:
        if tmp is not None:
            for name in ("mbr.o", "mbr.bin"):
                path = os.path.join(tmp, name)
                if os.path.exists(path):
                    os.unlink(path)
            os.rmdir(tmp)
