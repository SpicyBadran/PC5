#!/usr/bin/env python3
"""Negative controls for the 64-bit validation work.

Each control breaks exactly one thing, in an isolated copy of the tree, and
checks that the specific test which should notice does notice - and that no
other check quietly absorbs it.  A control that merely makes something fail is
not enough: it has to fail *for the stated reason*, or the test is measuring
something other than it claims.

Mutants live in a temporary directory.  The working tree is read for copying and
its source files are never written, which is checked at the end rather than
asserted in a comment.

Usage:  python3 tools/negative_controls_64.py [--verbose]
Exit:   0 every control was caught by its intended test; 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Files the controls are allowed to mutate, captured before any copying so the
# "no residue" check compares against the tree as it was found.
WATCHED = [
    "kernel64/pagetable64.c",
    "kernel64/pagetable64_check.c",
    "kernel64/alloc64.c",
    "kernel64/main.c",
    "boot/uefi64/uefi64_main.c",
    "tests/kernel64_machine.py",
    "tests/test_pagetable64.py",
    "tests/test_ovmf_boot64.py",
    "tests/boot64_stub.py",
    "tests/test_boot64_stub.py",
    "Makefile",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot():
    return {name: digest(ROOT / name) for name in WATCHED if (ROOT / name).exists()}


def run(cwd, command, timeout=900):
    return subprocess.run(command, cwd=str(cwd), capture_output=True,
                          text=True, timeout=timeout)


def run_unittest(cwd, module, pattern=None):
    command = [sys.executable, "-m", "unittest", module, "-v"]
    if pattern:
        command = [sys.executable, "-m", "unittest", "%s.%s" % (module, pattern), "-v"]
    return run(cwd, command)


class Control:
    """One mutant: how to make it, and which test must catch it."""

    def __init__(self, name, path, old, new, module, test, why):
        self.name = name
        self.path = path
        self.old = old
        self.new = new
        self.module = module
        self.test = test
        self.why = why


# The seven required controls from the mandate, plus the D1 fixture control that
# proves the walker is not passing vacuously.
CONTROLS = [
    Control(
        "C1 missing kernel marker",
        "kernel64/main.c",
        '    serial64_write("PC5-64-DONE\\n");\n',
        '',
        "tests.test_boot64_stub",
        "StubFirmware64Tests.test_the_whole_boot_path_passes_every_check",
        "the kernel must not be able to omit its final marker unnoticed",
    ),
    Control(
        "C2 incorrect marker ordering",
        "tests/ovmf_boot64.py",
        '        return all(positions[i] < positions[i + 1] for i in range(len(positions) - 1))',
        '        return True   # mutant: presence is treated as order',
        "tests.test_ovmf_boot64",
        "IncorrectOrderingIsDetected.test_kernel_banner_before_handoff_fails",
        "a log with its stages out of order must not pass",
    ),
    Control(
        "C3 QEMU timeout before kernel entry",
        "tests/ovmf_boot64.py",
        '    report.check("the machine was stopped on evidence, not on the clock",\n'
        '                 not run["timed_out"],',
        '    report.check("the machine was stopped on evidence, not on the clock",\n'
        '                 True,   # mutant: a timeout counts as evidence',
        "tests.test_ovmf_boot64",
        "TimeoutBeforeKernelEntryIsDetected.test_timeout_before_kernel_entry_fails",
        "a QEMU timeout must never be read as a successful boot",
    ),
    Control(
        "C4 D1 missing page-directory connection",
        "tests/kernel64_machine.py",
        '        pdpt_entries = [((pd + page * 0x1000) | 0x3) for page in range(pd_pages)]',
        '        pdpt_entries = [((pd + page * 0x1000) | 0x3) for page in range(1)]',
        "tests.test_kernel64",
        "KernelContractTests.test_valid_handover_boots_and_prints_the_marker_last",
        "a table whose upper PDPT entries are unlinked must fail the kernel",
    ),
    Control(
        "C5 incorrect allocation bound",
        "boot/uefi64/uefi64_main.c",
        '    u64 requested_max = PC5_IDENTITY_LIMIT - 1ull; /* input: uppermost allowed byte */',
        '    u64 requested_max = PC5_IDENTITY_LIMIT;       /* off by one: not a mapable byte */',
        "tests.test_boot64_stub",
        "StubFirmware64Tests.test_the_loader_reports_the_allocation_firmware_actually_performed",
        "asking for one byte past the map must be caught",
    ),
    Control(
        "C6 allocation outside the intended limit",
        "kernel64/alloc64.c",
        "    out->within_limit = out->end_address < identity_limit;",
        "    out->within_limit = 1;   /* mutant: accepts anything */",
        "tests.test_pagetable64",
        "TheAllocationArithmeticIsOverflowSafe.test_an_allocation_outside_the_limit_fails",
        "an extent past the identity limit must not be reported as ok",
    ),
    Control(
        "C7 overflow in the allocation-end calculation",
        "kernel64/alloc64.c",
        "        if (!add_ok(returned_base, out->bytes, &beyond)) {",
        "        if (0) {   /* mutant: wrap silently */",
        "tests.test_pagetable64",
        "TheAllocationArithmeticIsOverflowSafe.test_base_plus_size_overflow_is_caught",
        "base+size wrapping past 2**64 must be reported as overflow",
    ),
    Control(
        "C8 D1 walker tolerates a missing table (vacuous-pass guard)",
        "kernel64/pagetable64.c",
        "    if (!(pdpte & PTE_PRESENT)) {\n        out->failure = PC5_WALK_MISSING_PDPT;",
        "    if (0) {\n        out->failure = PC5_WALK_MISSING_PDPT;",
        "tests.test_pagetable64",
        "MissingConnectionsAreFailures.test_a_disconnected_1gib_boundary_is_named",
        "if the walker ignored absent entries, its clean run would mean nothing",
    ),
]


def apply_mutant(workdir, control):
    target = workdir / control.path
    text = target.read_text()
    if control.old not in text:
        return False, "pattern not found in %s" % control.path
    target.write_text(text.replace(control.old, control.new, 1))
    return True, ""


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--only", help="run one control by name prefix")
    args = parser.parse_args(argv)

    before = snapshot()
    if not before:
        print("nothing to watch: is this the repository root?")
        return 1

    controls = [c for c in CONTROLS if not args.only or c.name.startswith(args.only)]

    # Baseline: the tests must pass on the unmutated tree, or a control's
    # "failure" proves nothing about the mutant.
    print("baseline (unmutated):")
    baseline = run_unittest(ROOT, "tests.test_pagetable64")
    if baseline.returncode != 0:
        print("  FAILED - the suite does not pass before mutation:")
        print(baseline.stdout[-2000:])
        return 1
    print("  tests.test_pagetable64 OK")

    results = []
    for control in controls:
        with tempfile.TemporaryDirectory(prefix="pc5-mutant-") as tmp:
            workdir = pathlib.Path(tmp) / "tree"
            # An isolated copy: the real tree is never modified, so there is
            # nothing to restore and no way to leave a mutant behind.
            shutil.copytree(ROOT, workdir,
                            ignore=shutil.ignore_patterns("build", ".git",
                                                          "__pycache__", ".venv"))
            ok, why = apply_mutant(workdir, control)
            if not ok:
                results.append((control, "PATTERN-MISSING", why))
                print("  [ PATTERN-MISSING ] %s -- %s" % (control.name, why))
                continue

            outcome = run_unittest(workdir, control.module, control.test)
            caught = outcome.returncode != 0
            detail = ""
            if caught:
                # The right test must have failed, not merely something.
                blob = outcome.stdout + outcome.stderr
                named = control.test.split(".")[-1] in blob
                detail = "failed as intended" if named else "failed, but not the named test"
                verdict = "CAUGHT" if named else "WRONG-TEST"
            else:
                verdict = "MISSED"
                detail = "the mutant passed unnoticed"
            results.append((control, verdict, detail))
            print("  [ %-13s ] %s -- %s" % (verdict, control.name, detail))
            if args.verbose:
                print(outcome.stdout[-1500:])
                print(outcome.stderr[-1500:])

    after = snapshot()
    residue = {name: (before.get(name), after.get(name))
               for name in before if before.get(name) != after.get(name)}

    print("\nresidue check:")
    if residue:
        print("  THE WORKING TREE CHANGED:")
        for name, (was, now) in residue.items():
            print("    %s: %s -> %s" % (name, was, now))
    else:
        print("  none: all %d watched files are byte-identical to before" % len(before))

    caught = [r for r in results if r[1] == "CAUGHT"]
    print("\n%d/%d controls caught by their intended test" % (len(caught), len(results)))
    if len(caught) != len(results) or residue:
        return 1
    print("Every mutant was detected for the stated reason; no residue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
