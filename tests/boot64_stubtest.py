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


def _number(serial, key, line="PC5-64-MEM", hex_ok=False):
    """The value of `key=` on `line`, as an int, or None.

    Parsed rather than pattern-matched on the whole line: the gate has to fail with a
    named reason when the kernel omits a field, not with a traceback.  `hex_ok` is for
    the fields a kernel prints as an address - and a field it cannot parse is None, which
    every caller below treats as a failure, never as a zero.
    """
    text = fields(serial, line, key)
    if text is None:
        return None
    try:
        return int(text, 16) if (hex_ok and text.startswith("0x")) else int(text)
    except ValueError:
        return None


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

    # The kernel image has to account for itself before anything jumps into it, and the four
    # rows below read the loader's verdict out of the log and recompute it from the *other*
    # two lines.  Checking the numbers against each other rather than against constants is
    # the difference between "the header line appeared" and "the thing the header says is the
    # thing the load did": a kernel that grew by a page, or an entry point that moved, keeps
    # these rows true only if both sides moved together.
    header = fields(serial, "PC5-64-LOADER kernel-header", "version")
    check("the loader validated the kernel image's own header",
          header == "1", f"kernel-header line reported version={header}")
    declared = fields(serial, "PC5-64-LOADER kernel-header", "image")
    measured = fields(serial, "PC5-64-LOADER kernel at=", "bytes")
    check("the image is exactly as long as its header declares",
          declared is not None and declared == measured,
          f"the header declares {declared} bytes, the file read {measured}")
    entry = fields(serial, "PC5-64-LOADER kernel-header", "entry")
    jumped = fields(serial, "PC5-64-LOADER-JUMP entry=", "entry")
    check("execution started at the declared entry, not at byte zero of the file",
          entry is not None and jumped is not None
          and int(jumped, 16) == 0x100000 + int(entry, 16),
          f"header entry={entry}, jump target={jumped}")
    check("the payload checksum was verified, not merely present",
          fields(serial, "PC5-64-LOADER kernel-header", "checksum") == "ok",
          "a header whose checksum field was never compared would still print")

    # The four rows above are all the loader's verdict: what the code that *decides whether to
    # jump* observed.  They cannot say anything about the bytes as the kernel finds them (the
    # load could be damaged after that check), nor about the `kernel_size` the kernel is handed,
    # which is a second claim about the same object that the loader computes for itself.  The
    # kernel re-runs the header half of the same validator (`validate_image_self`), and these two
    # rows are its account -- which is why the numbers are compared across lines rather than
    # against constants: a kernel that grew by a page keeps both rows true only if the header,
    # the handover and the file all grew together.
    self_status = fields(serial, "PC5-64-KIMAGE", "status")
    self_reason = fields(serial, "PC5-64-KIMAGE", "reason")
    self_size = fields(serial, "PC5-64-KIMAGE", "image")
    self_handover = fields(serial, "PC5-64-KIMAGE", "handover")
    check("the running kernel re-read its own image header",
          self_status == "ok" and self_reason == "ok"
          and fields(serial, "PC5-64-KIMAGE", "crc") == "ok",
          "PC5-64-KIMAGE status=%s reason=%s" % (self_status, self_reason))
    sizes = [declared, measured, self_size, self_handover]
    check("the loader's read, the header, and the handover name one length",
          None not in sizes and len({int(v, 10 if not v.startswith("0x") else 16)
                                     for v in sizes}) == 1,
          "header=%s file-read=%s kernel-saw=%s handover=%s" % tuple(sizes))

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
    # The stub firmware publishes an RSDP but no MADT, and has no PIT that answers.
    # Both subsystems must therefore reach a *classified* refusal rather than a fault
    # or a hang: a boot that stops here is the failure mode these checks exist for.
    check("the timer either calibrated or said why not",
          "PC5-64-TIMER calibrated=" in serial
          and (fields(serial, "PC5-64-TIMER", "calibrated") == "1"
               or 'not-calibrated reason=' in serial),
          "an uncalibrated clock must be reported, not presented as working")
    check("CPU topology was either enumerated or its absence reported",
          "PC5-64-TOPOLOGY " in serial
          and ("not-parsed reason=" in serial or "usable=" in serial))
    check("the kernel ran to its verdict", "PC5-64-DONE" in serial)

    # ------------------------------------------------------------- frame allocator
    # Two numbers from two places.  `PC5-64-MAP conventional_pages=` is the sum the
    # kernel took while validating the descriptor array; `PC5-64-MEM conventional=` is
    # what the pool marked free before any claim was subtracted.  They are computed by
    # different code over the same map, so a pool that lost, duplicated, or clipped
    # conventional memory is caught here rather than at the first allocation.
    map_pages = fields(serial, "PC5-64-MAP", "conventional_pages")
    mem_status = fields(serial, "PC5-64-MEM", "status")
    check("the kernel built a frame pool it can allocate from", mem_status == "ok",
          f"status={mem_status}")
    check("the frame pool counts the same conventional memory the map states",
          map_pages is not None
          and fields(serial, "PC5-64-MEM", "conventional_total") == map_pages,
          f"map says {map_pages}, pool says "
          f"{fields(serial, 'PC5-64-MEM', 'conventional_total')}")
    inside = _number(serial, "conventional")
    whole = _number(serial, "conventional_total")
    check("clipping the map to the identity span can only reduce what is managed",
          inside is not None and whole is not None and 0 < inside <= whole,
          f"in-span={inside} total={whole}")
    claimed = _number(serial, "claimed")
    free = _number(serial, "free")
    conventional = _number(serial, "conventional")
    frames = _number(serial, "frames")
    check("every conventional frame is either free or claimed, and neither is negative",
          claimed is not None and free is not None and conventional is not None
          and claimed >= 0 and free > 0 and claimed + free == conventional,
          f"claimed={claimed} free={free} conventional={conventional}")
    check("the pool describes at least as many frames as it found usable",
          frames is not None and conventional is not None and frames >= conventional > 0,
          f"frames={frames} conventional={conventional}")
    check("the allocation self-test passed at runtime",
          fields(serial, "PC5-64-MEM-SELFTEST", "result") == "ok",
          f"result={fields(serial, 'PC5-64-MEM-SELFTEST', 'result')}")
    check("the self-test refused every way of cheating the pool",
          all(fields(serial, "PC5-64-MEM-SELFTEST", key) == "1" for key in (
              "disjoint", "validated", "claims-clean", "zero-page-reserved",
              "zero-refused", "oversized-refused", "refusal-clears-base",
              "over-largest-refused", "double-free-refused", "unaligned-free-refused",
              "first-fit-reuse", "blocks-apart", "free-restored",
              "bitmap-lie-refused")),
          " ".join(f"bitmap-lie-refused={fields(serial, 'PC5-64-MEM-SELFTEST', key)}" for key in (
              "bitmap-lie-refused", "refusal-counted", "disjoint", "validated",
              "claims-clean", "zero-page-reserved", "double-free-refused",
              "free-restored")))


    # ------------------------------------------------------------------ TSS / IST
    # The frame pool's first consumer.  Every value below is read off the line the kernel
    # printed while running and cross-checked against the pool's own accounting: three
    # frames reported as taken must be exactly three frames the pool no longer offers.
    tss_line = "PC5-64-TSS"
    tss_status = fields(serial, tss_line, "status")
    check("the kernel gave the frame pool a consumer", tss_status == "ok",
          f"status={tss_status} reason={fields(serial, tss_line, 'reason')}")
    tss_frame = _number(serial, "tss", line=tss_line, hex_ok=True)
    ist1 = _number(serial, "ist1", line=tss_line, hex_ok=True)
    ist2 = _number(serial, "ist2", line=tss_line, hex_ok=True)
    taken = _number(serial, "frames", line=tss_line)
    free_after = _number(serial, "free-after", line=tss_line)
    check("it took three frames and the pool lost three",
          taken == 3 and free is not None and free_after == free - 3,
          f"frames={taken} free {free} -> {free_after}")
    check("the TSS and both interrupt stacks are three different frames",
          tss_frame and ist1 and ist2 and len({tss_frame, ist1, ist2}) == 3,
          f"tss={tss_frame} ist1={ist1} ist2={ist2}")
    check("each IST is a page top, which is the only alignment the CPU is promised",
          ist1 is not None and ist2 is not None
          and (ist1 & 0xFFF) == 0 and (ist2 & 0xFFF) == 0
          and (ist1 >> 12) != (ist2 >> 12),
          f"ist1={ist1 if ist1 is None else hex(ist1)} ist2={ist2 if ist2 is None else hex(ist2)}")
    # The span is read off the pool's own line rather than assumed: a grant the identity
    # map does not cover is reachable only with paging changes, and the pool refuses it,
    # so a report that names an address above the span is a report that is lying about
    # something the pool already guaranteed.
    span = _number(serial, "span", line="PC5-64-MEM", hex_ok=True)
    check("every frame the TSS holds is inside the identity span the kernel mapped",
          span is not None
          and all(value is not None and 0 < value < span for value in (tss_frame, ist1, ist2)),
          f"tss={tss_frame} ist1={ist1} ist2={ist2} span={span}")
    check("the GDT the CPU reads is the GDT the kernel wrote",
          fields(serial, tss_line, "gdt") == fields(serial, tss_line, "gdtr"),
          f"gdt={fields(serial, tss_line, 'gdt')} gdtr={fields(serial, tss_line, 'gdtr')}")
    # Only the positive case is assertible here: a handover at CPL 3 dies at the kernel's
    # first LIDT, before the TSS is reached, so the refusal path has no serial line to
    # check.  tests/test_tss64.py covers that rule by calling the guard from a ring-3
    # process, which is the same privilege level, reached without the fault.
    check("the table was installed at CPL 0",
          fields(serial, tss_line, "cpl") == "0",
          f"cpl={fields(serial, tss_line, 'cpl')}")
    check("the IST arming rules hold on the live table",
          fields(serial, tss_line, "arm-rules") == "1",
          f"arm-rules={fields(serial, tss_line, 'arm-rules')}")
    # The rule that keeps a fault honest, in both directions now that LTR is executed on this
    # path: the IDT may name an interrupt stack if and only if the CPU's own readbacks say TR
    # names this TSS.  Written as an equivalence rather than two assertions, because the whole
    # point is that the two halves cannot drift: `tr=loaded` with the gates unarmed is a kernel
    # that loaded a TSS nobody will ever use, and `tr=not-loaded` with them armed is a fault
    # that reads an IST slot out of whatever TSS TR does name.  Both fail here; neither can be
    # fixed by editing the other half, and neither was detectable at all while the left side
    # was hard-wired to "not-loaded" because nobody executed the instruction.
    tr = fields(serial, tss_line, "tr")
    ist_df = fields(serial, tss_line, "ist-df")
    ist_nmi = fields(serial, tss_line, "ist-nmi")
    check("the IDT routes to an IST slot if and only if TR names this TSS",
          (tr == "loaded") == (ist_df == "1" and ist_nmi == "2"),
          f"tr={tr} ist-df={ist_df} ist-nmi={ist_nmi}")
    # What this emulator specifically established, so that the sentence above is not the only
    # thing standing between a regression and a green gate.  STR's readback, the descriptor as
    # it is found through GDTR, the busy transition that only a loading CPU performs in memory,
    # and the I/O-map base read back through the descriptor - all four are the kernel quoting
    # the machine rather than quoting itself, and Unicorn 2.1.4 answers all of them: the fault
    # this path used to take was the kernel's own wrong selector (0x28, the descriptor's high
    # half), not an emulator that refuses the instruction.  `docs/evidence/2026-11-22-tss-and-ist/`
    # recorded the fault and could not name the cause; the cause was in tss64.h.
    check("LTR executed and STR read back the selector the kernel built",
          tr == "loaded" and fields(serial, tss_line, "str") == "0x20",
          f"tr={tr} str={fields(serial, tss_line, 'str')} "
          f"reason={fields(serial, tss_line, 'reason')}")
    check("the descriptor the CPU was given names the granted TSS frame with a 103-byte limit",
          fields(serial, tss_line, "desc") == fields(serial, tss_line, "tss") + "/103",
          f"desc={fields(serial, tss_line, 'desc')} tss={fields(serial, tss_line, 'tss')}")
    check("the TSS descriptor went from available to busy in memory",
          fields(serial, tss_line, "busy") == "available-to-busy",
          f"busy={fields(serial, tss_line, 'busy')}")
    check("the TSS's own I/O-map base says there is no I/O permission map",
          fields(serial, tss_line, "iomap") == "104",
          f"iomap={fields(serial, tss_line, 'iomap')}")
    # The delivery probe is deliberately *not* run here, and the reason is a measured property of
    # the tool rather than a story about the kernel: Unicorn does not deliver a guest software
    # interrupt to the guest's own IDT at all, which tests/test_unicorn_soft_int.py demonstrates by
    # faulting the shipped image at the address of its single `int $8` -- read out of the built
    # file, not written here.  That is the same shape of claim the `ltr` note carried for three
    # milestones, and the difference is that this one is tested against the emulator: when Unicorn
    # learns to do it, that test fails and the stub gains a real IST measurement, at which point the
    # check below turns into an assertion on the probe's fields.
    check("the delivery probe is unreachable in this emulator, by measurement not by assumption",
          "PC5-64-TSS-DF" not in serial and "PC5-64-DONE" in serial,
          "the run stops at the marker, before the probe; if a probe line ever appears here the "
          "emulator has changed and tests/test_unicorn_soft_int.py must be updated with it")

    check("no exception was reported by the kernel across the boot that installed it",
          "PC5-64-EXCEPTION" not in serial,
          "the handler prints this line for any delivered fault, so its absence covers "
          "the install and everything else on this stack")

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
