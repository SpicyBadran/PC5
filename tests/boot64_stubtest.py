#!/usr/bin/env python3
"""Run the experimental 64-bit boot path against the long-mode stub firmware.

This is *not* OVMF and never claims to be.  It executes the loader's own PE32+
code and the kernel's own binary, so the checks below are about PC5's code, not
about the firmware's: does the loader install its own page tables, does
ExitBootServices succeed with a key that firmware has invalidated, does the
kernel validate the handover, and does the success marker appear only after all
of that?

Exit status: 0 when every check passes, 1 when a check fails, 3 when the
emulator or the tooling needed to run it is missing (reported as not-tested).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MARKER = "PC5-64BIT-LONG-MODE-OK"


def run_once(image, kernel, race_map_key=0, seed=None):
    import boot64_stub

    trace = boot64_stub.StubFirmware64(
        image=image,
        esp_volume={"\\EFI\\BOOT\\BOOTX64.EFI": image, "\\KERNEL64.BIN": kernel},
        kernel_bytes=kernel,
        race_map_key=race_map_key,
    ).run()
    return trace


def fields(serial, marker, key):
    """Value of key=... on the line beginning with marker, or None."""
    for line in serial.splitlines():
        text = line.strip()
        if not text.startswith(marker):
            continue
        for token in text.split():
            if token.startswith(key + "="):
                return token[len(key) + 1:]
    return None


def checks_for(trace, expect_success=True):
    serial = trace.serial
    checks = []

    def check(name, condition, detail=""):
        checks.append(dict(name=name, passed=bool(condition), detail=detail))

    check("loader reached the C entry point",
          "PC5-64-LOADER-BANNER" in serial)
    check("kernel image was read from the ESP through the file protocol",
          "PC5-64-LOADER kernel at=" in serial)
    check("PC5 page tables were built and counted",
          "mapped_2mib=2048" in serial, "2048 x 2 MiB = 4 GiB")

    # The loader no longer reads CR3 back itself: after installing the root it
    # never runs loader code again.  The read-back now comes from the kernel, which
    # is executing on the new map - a stronger statement than the loader checking
    # its own write.  report_environment in kernel64/main.c fails the boot outright
    # if CR3 differs from the page-table address in the boot info.
    planned = fields(serial, "PC5-64-LOADER cr3-plan=", "cr3-plan")
    observed = fields(serial, "PC5-64-ENV", "cr3")
    check("CR3 was switched to PC5's own PML4 and read back",
          planned is not None and planned == observed,
          f"loader planned {planned}, kernel on the new map read {observed}")

    check("ExitBootServices succeeded with a live map key",
          "exit-boot-services=ok" in serial)
    check("the loader jumped to the kernel",
          "PC5-64-LOADER-JUMP entry=" in serial)
    check("the loader checked where firmware placed it",
          "PC5-64-LOADER image=" in serial and "below-span=1" in serial)
    check("the kernel reported its own entry", "PC5-64BIT-KERNEL-ENTRY" in serial)
    check("the kernel parsed the memory map", "PC5-64-MAP entries=" in serial)
    check("the kernel validated the boot info",
          "PC5-64-BOOTINFO magic=ok version=2 size=160 memdesc=32" in serial)
    check("the kernel validated the kernel image range",
          fields(serial, "PC5-64-IMAGE", "range") == "ok")
    check("the kernel validated the loader's own placement",
          fields(serial, "PC5-64-LOADER", "range") == "ok")
    check("the kernel observed paging enabled", "PC5-64-PAGING enabled=1" in serial)
    check("the kernel observed a 16-byte aligned stack",
          fields(serial, "PC5-64-STACK", "aligned") == "16"
          and fields(serial, "PC5-64-STACK", "range") == "ok")
    check("the kernel proved 64-bit operand size",
          "wide-operands=ok" in serial and "arithmetic=ok" in serial)
    check("the CPU feature report was collected", "PC5-64-CPU vendor=" in serial)
    check("the kernel classified the framebuffer",
          fields(serial, "PC5-64-FB", "usability") in ("ok", "unsupported", "absent"))
    check("the kernel classified the RSDP",
          fields(serial, "PC5-64-RSDP", "usability") in ("ok", "unsupported", "absent"))
    check("the kernel ran to its verdict", "PC5-64-DONE" in serial)

    if expect_success:
        check("the success marker is present", MARKER in serial)
        last_fail = serial.find("PC5-64BIT-FAIL=")
        check("no failure marker appears before the verdict", last_fail < 0)
        check("the marker is the last thing the kernel says",
              serial.rstrip().endswith("PC5-64-DONE"),
              serial.strip().splitlines()[-1] if serial.strip() else "")
    else:
        check("the success marker is absent", MARKER not in serial)
        check("a failure marker explains why",
              "PC5-64BIT-FAIL=" in serial or "PC5-64-LOADER-FAIL=" in serial)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--kernel", required=True, type=pathlib.Path)
    parser.add_argument("--json-out", type=pathlib.Path)
    parser.add_argument("--race-map-key", type=int, default=0,
                        help="invalidate the first N memory-map keys")
    args = parser.parse_args(argv)

    try:
        import unicorn  # noqa: F401
    except ImportError as exc:
        print(f"64-bit stub firmware NOT TESTED: {exc} (pip install unicorn)")
        return 3

    image = args.image.read_bytes()
    kernel = args.kernel.read_bytes()
    try:
        trace = run_once(image, kernel, race_map_key=args.race_map_key)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        print(f"64-bit stub firmware FAILED: {exc}")
        return 1

    checks = checks_for(trace)
    failed = [c for c in checks if not c["passed"]]
    for entry in checks:
        print(f"  [{'ok  ' if entry['passed'] else 'FAIL'}] {entry['name']}"
              + (f"  ({entry['detail']})" if entry["detail"] else ""))
    print("---- serial ----")
    print(trace.serial.rstrip())
    print("----------------")
    print("64-BIT STUB FIRMWARE "
          + ("OK" if not failed else f"FAILED ({len(failed)} check(s))")
          + f": {len(checks) - len(failed)}/{len(checks)} checks passed")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(dict(
            gate="stubtest64",
            level="stub-firmware-emulation",
            passed=not failed,
            checks=dict(passed=len(checks) - len(failed), total=len(checks)),
            serial=trace.serial,
            failures=[c["name"] for c in failed],
            note="Unicorn long-mode emulation of PC5's loader and kernel; "
                 "not OVMF, not hardware",
        ), indent=2) + "\n")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
