#!/usr/bin/env python3
"""Mutation harness v2 — the reviewed mutants, with an intention map that is measured.

Finding F-5 of `evidence/usb-descriptor-independent-review-v1/INDEPENDENT-REVIEW-20261121.md`
was that four entries in `mutation_harness.py`'s EXPECTED lists name tests that do not
fail under their mutant. Detection was never in doubt -- the reviewed harness requires at
least one intended `AssertionError`, and every mutant produced one -- but a reader of the
mapping would over-attribute sensitivity to named tests, and a named test that cannot fail
is not a guard.

This harness therefore does three things the reviewed one did not:

* it *imports* the mutation texts from `mutation_harness.py`, so the nine mutants are
  provably the same edits the review tested rather than a re-typed approximation;
* it asserts that every intended test **exists** in the inventory, so a rename can no
  longer look like a passing result (`test_20_unreadable_sysfs_relationship_data` naming
  drift was exactly how F-5 arose);
* it asserts that every intended test **fails** under its mutant, and prints any test
  that failed but was not listed, so the map cannot quietly fall behind the suite.

`INTENTIONS` below is not hand-maintained: it is the failing set measured on 2026-11-21
against the 91-test inventory (`logs/25-mutation-mapping-measured.txt`), trimmed to what
actually discriminates. Entries the v1 harness listed but that never failed are recorded
in `NOT_PROTECTIVE` with their mutant, so the removal is auditable rather than silent.

Status: synthetic. Temporary copies under a `tempfile` directory, no path outside it is
written, no device is opened, and the repository working tree is never mutated.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
V1 = REPO / "evidence/usb-descriptor-remediation-v1/mutation_harness.py"

#: Measured 2026-11-21 against `Ran 91` (tests/test_pc5ctl_usb_safety.py,
#: test_pc5ctl_usb_descriptor_identity.py, test_pc5ctl_usb_cli_boundaries.py).
INTENTIONS = {
    "m1-validation-follows-the-pathname": [
        "test_09_path_replaced_between_the_identity_check_and_final_validation",
    ],
    "m2-inheritance-omitted": [
        "test_01_valid_descriptor_and_matching_identity_writes_through_the_fd",
        "test_03_missing_descriptor_inheritance_is_explicit",
        "test_16_child_receives_exactly_the_validated_descriptor",
        "test_16b_inherited_descriptor_number_is_the_closed_one",
        "test_25_real_dd_writes_through_the_inherited_descriptor",
    ],
    "m3-wrong-descriptor-inherited": [
        "test_01_valid_descriptor_and_matching_identity_writes_through_the_fd",
        "test_15_valid_non_destructive_control_with_a_populated_root",
        "test_16_child_receives_exactly_the_validated_descriptor",
        "test_25_real_dd_writes_through_the_inherited_descriptor",
    ],
    "m4-final-identity-reread-skipped": [
        "test_14_final_validation_failure_immediately_before_writing",
    ],
    "m5-name-versus-descriptor-comparison-skipped": [
        "test_05_descriptor_redirected_to_a_different_object",
        "test_06_descriptor_identity_mismatch_against_the_named_target",
        "test_10_root_descriptor_with_a_substituted_safe_name_still_refuses",
        "test_6_yes_cannot_bypass_a_refusal_even_with_a_writable_descriptor",
    ],
    "m6-partial-enumeration-accepted": [
        "test_22_partial_topology_followed_by_an_enumeration_error",
        "test_device_disappearing_during_enumeration_is_unknown",
    ],
    "m7-unreadable-topology-is-empty": [
        "test_unreadable_slaves_is_unknown_not_empty",
        # Added by the F-6 closure: end-to-end cases whose refusal has exactly one
        # possible source, `_relations` answering UNKNOWN for a directory that exists
        # but cannot be listed.  Before them, `m7` removed a production guarantee and
        # only a unit-level assertion noticed.
        "test_unreadable_root_partition_holders_refuses_end_to_end",
        "test_unreadable_root_partition_slaves_refuses_end_to_end",
    ],
    "m8-writability-not-checked": [
        "test_17_descriptor_not_open_for_write_is_refused",
        "test_02_descriptor_closed_before_the_child_launches",
    ],
    "m9-open-failure-writes-by-name": [
        "test_23_target_open_failure_refuses_before_any_descriptor_exists",
    ],
}

#: Named by the v1 harness, measured not to fail under their mutant. Kept here so the
#: trim is visible: each of these is a test that does not depend on the guard the mutant
#: removes, which is precisely what F-5 said. `m7`'s two entries are the sharpest case --
#: the CLI-level refusal they assert survives `m7` through a *different* UNKNOWN branch
#: (an absent relationship directory, `if not os.path.isdir(base)`), not the one mutated.
#: That is finding F-6, and it is recorded as an open sensitivity gap, not closed here.
NOT_PROTECTIVE = {
    "m1-validation-follows-the-pathname": [
        "test_11_topology_change_immediately_before_the_final_safety_decision",
    ],
    "m3-wrong-descriptor-inherited": [
        "test_16b_inherited_descriptor_number_is_the_closed_one",
    ],
    "m6-partial-enumeration-accepted": [
        "test_partial_list_never_permits_a_write",
    ],
    "m7-unreadable-topology-is-empty": [
        "test_20_unreadable_sysfs_relationship_data",
        "test_21_unreadable_holders_directory",
    ],
}

RESULT = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)")


def load_v1():
    """The reviewed mutation texts, loaded from the delivered harness."""
    if not V1.is_file():
        raise SystemExit(f"BLOCKED: the reviewed harness is not here: {V1}")
    spec = importlib.util.spec_from_file_location("pc5_usb_mutation_harness_v1", V1)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)          # main() is __main__-guarded
    return module


COLLECT = """
import sys, unittest
sys.path[:0] = ["tools", "tests"]
suite = unittest.TestLoader().discover("tests", PATTERN)
names = []
def walk(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            walk(t)
        else:
            names.append(t.id().rsplit(".", 1)[-1])
walk(suite)
print("\\n".join(names))
""".replace("PATTERN", "%r")


def inventory(v1, work: Path) -> set:
    """Every test method the loader collects, by method name.

    Enumerated through `unittest.TestLoader` rather than parsed out of `-v`
    output: the CLI suites redirect `sys.stdout` inside the test, which strands
    the verbose `... ok` suffix in the captured buffer, so a parsed inventory
    would be short and every missing name would read as "intended test does not
    exist". The loader is what `discover` itself uses, so this is the same set
    the suite runs, and a rename or a deleted test still shows up here.
    """
    proc = subprocess.run(
        [sys.executable, "-c", COLLECT % v1.PATTERN],
        cwd=str(work), capture_output=True, text=True,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin",
             "HOME": str(work), "TMPDIR": str(work)})
    if proc.returncode != 0:
        print(proc.stdout, proc.stderr)
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def main() -> int:
    v1 = load_v1()
    problems: list[str] = []
    print(f"repository {REPO}")
    print(f"mutants imported from {V1.relative_to(REPO)}")
    with tempfile.TemporaryDirectory(prefix="pc5-usb-mutation-v2-") as tmp:
        root = Path(tmp)
        base = v1.prepare(root, "baseline")
        known = inventory(v1, base)
        proc = v1.run_suite(base)
        outcomes, _reasons = v1.classify(proc.stdout + proc.stderr)
        total = re.search(r"^Ran (\d+) tests", proc.stdout + proc.stderr, re.M)
        count = int(total.group(1)) if total else -1
        print(f"\n### baseline\nRan {count} tests, exit {proc.returncode}")
        if count <= 0 or proc.returncode != 0:
            problems.append("baseline is not green; nothing below means anything")
        if not known:
            problems.append("the suite's inventory could not be enumerated, so no "
                            "intended test name could be checked for existence")
        else:
            print(f"collected inventory: {len(known)} test methods")
        for name, description, old, new, v1_expected in v1.MUTATIONS:
            work = v1.prepare(root, name)
            source = work / v1.TARGET
            text = source.read_text()
            if text.count(old) != 1:
                problems.append(f"{name}: anchor occurs {text.count(old)} times")
                continue
            source.write_text(text.replace(old, new, 1))
            proc = v1.run_suite(work)
            outcomes, reasons = v1.classify(proc.stdout + proc.stderr)
            fails = sorted(k for k, v in outcomes.items() if v == "FAIL")
            errors = sorted(k for k, v in outcomes.items() if v == "ERROR")
            intended = INTENTIONS.get(name)
            print(f"\n### {name}\n{description}")
            print(f"tests run: {count}; exit {proc.returncode}; "
                  f"assertion failures: {len(fails)}; errors: {len(errors)}")
            if intended is None:
                problems.append(f"{name}: no measured intention recorded for this mutant")
                continue
            for test in intended:
                if test not in known:
                    problems.append(f"{name}: intended test does not exist: {test}")
                elif outcomes.get(test) == "FAIL":
                    print(f"  caught by {test}\n    {reasons[test]}")
                else:
                    problems.append(
                        f"{name}: intended test {test} did not fail "
                        f"(outcome {outcomes.get(test, 'absent')})")
            for test in NOT_PROTECTIVE.get(name, []):
                if outcomes.get(test) == "FAIL":
                    print(f"  note: {test} now discriminates; NOT_PROTECTIVE is stale")
            extra = [t for t in fails if t not in intended]
            if extra:
                print(f"  also failed (not claimed as intended): {', '.join(extra)}")
            if not fails:
                problems.append(f"{name}: NO assertion failure under the mutant")
    print()
    if problems:
        print("RESULT: PROBLEM")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("RESULT: PASS: baseline green, every mutant caught, and every intended "
          "test both present and discriminating")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
