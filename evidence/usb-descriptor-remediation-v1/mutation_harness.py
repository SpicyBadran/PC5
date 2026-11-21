#!/usr/bin/env python3
"""Deliberate-mutation harness for the USB descriptor identity boundary.

Nine mutants of `tools/pc5ctl.py`, each applied to a **disposable copy** in a
temporary directory together with the permanent USB regression tests, then the
suite is run there.  Judging rules, stated because they are the point:

  * a mutation counts as CAUGHT only when at least one of its intended tests
    fails with an ``AssertionError`` (unittest ``FAIL``);
  * an ``ERROR`` (an exception escaping the code under test, or a fixture
    blowing up) does **not** count as detection, and is reported as a problem --
    otherwise a mutant that merely crashes would look like a caught mutant;
  * the unmutated baseline must be green, or the run proves nothing;
  * nothing here can write to a device: the tests use synthetic sysfs fixtures,
    ordinary temporary files and a recorded write child (except the two
    real-``/bin/dd`` cases, which write to a temporary file).

Mutants 1, 2, 3, 4 and 5 are the five the remediation brief requires:
pathname-based validation, omitted descriptor inheritance, wrong descriptor,
skipped identity comparison, incomplete topology.  6-9 are additions that came
out of reading the surrounding failure handling.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGET = "tools/pc5ctl.py"
PATTERN = "test_pc5ctl_usb*.py"

MUTATIONS = [
    ("m1-validation-follows-the-pathname",
     "REQUIRED: restore pathname-based validation. The final safety decision "
     "re-resolves the device path instead of using the identity behind the held "
     "descriptor.",
     "        again = check_target_safe(device, topology=_BoundTopology(Topology(), opened_as))",
     "        again = check_target_safe(device)",
     ["test_09_path_replaced_between_the_identity_check_and_final_validation",
      "test_11_topology_change_immediately_before_the_final_safety_decision"]),

    ("m2-inheritance-omitted",
     "REQUIRED: omit descriptor inheritance. dd is launched with the descriptor-backed "
     "output path but the descriptor is not passed to the child, so the child cannot "
     "open it. This is the defect the shipped code has.",
     "            return subprocess.call(argv, close_fds=True, pass_fds=(target_fd,))",
     "            return subprocess.call(argv)",
     ["test_01_valid_descriptor_and_matching_identity_writes_through_the_fd",
      "test_03_missing_descriptor_inheritance_is_explicit",
      "test_16_child_receives_exactly_the_validated_descriptor",
      "test_25_real_dd_writes_through_the_inherited_descriptor"]),

    ("m3-wrong-descriptor-inherited",
     "REQUIRED: inherit the wrong descriptor: the child is told to write one number "
     "above the descriptor that was validated.",
     '        writing = f"/proc/self/fd/{target_fd}"',
     '        writing = f"/proc/self/fd/{target_fd + 1}"',
     ["test_01_valid_descriptor_and_matching_identity_writes_through_the_fd",
      "test_16_child_receives_exactly_the_validated_descriptor",
      "test_16b_inherited_descriptor_number_is_the_closed_one"]),

    ("m4-final-identity-reread-skipped",
     "REQUIRED: skip the last identity comparison. The descriptor is not re-read "
     "after the decision, so an object that changed underneath the check is written.",
     """        if fd_block_identity(target_fd) != opened_as:
            print(f"descriptor for {device} changed before the write: refusing to write")
            print("nothing was written")
            return 1""",
     "        pass  # MUTANT: no last identity re-read",
     ["test_14_final_validation_failure_immediately_before_writing"]),

    ("m5-name-versus-descriptor-comparison-skipped",
     "REQUIRED (second half of the identity comparison): drop the check that the "
     "name the operator typed still resolves to the object being held. The refusal "
     "that reports a substituted device node disappears.",
     """        named_as = Topology().devnum_for_path(device)
        if opened_as != named_as:""",
     """        named_as = Topology().devnum_for_path(device)
        if False:""",
     ["test_05_descriptor_redirected_to_a_different_object",
      "test_06_descriptor_identity_mismatch_against_the_named_target"]),

    ("m6-partial-enumeration-accepted",
     "REQUIRED: ignore incomplete partition enumeration. Children that cannot be "
     "inspected are skipped instead of making the protected set unknown.",
     """            entry = _probe(candidate)
            if entry is UNREADABLE:
                return UNKNOWN
            if entry is ABSENT:
                return UNKNOWN     # vanished mid-enumeration""",
     """            entry = _probe(candidate)
            if entry is UNREADABLE:
                continue           # MUTANT: ignore an unreadable child
            if entry is ABSENT:
                continue           # MUTANT: ignore a vanished child""",
     ["test_22_partial_topology_followed_by_an_enumeration_error",
      "test_device_disappearing_during_enumeration_is_unknown",
      "test_partial_list_never_permits_a_write"]),

    ("m7-unreadable-topology-is-empty",
     "Treat unreadable topology as an empty relationship set: a `slaves` or "
     "`holders` directory that cannot be listed becomes 'no relationships'.",
     """        try:
            names = sorted(os.listdir(base))
        except OSError:
            return UNKNOWN""",
     """        try:
            names = sorted(os.listdir(base))
        except OSError:
            return []              # MUTANT: unreadable means empty""",
     ["test_20_unreadable_sysfs_relationship_data",
      "test_21_unreadable_holders_directory",
      "test_unreadable_slaves_is_unknown_not_empty"]),

    ("m8-writability-not-checked",
     "Assume the descriptor is writable. The decision then authorises a write "
     "through an open file description that cannot carry one, which is a silent "
     "no-op rather than a refusal.",
     """        if not fd_writable(target_fd):
            print(f"cannot hold a writable descriptor for {device}: refusing to write")
            print("nothing was written")
            return 1""",
     "        pass  # MUTANT: no writability interrogation",
     ["test_17_descriptor_not_open_for_write_is_refused",
      "test_02_descriptor_closed_before_the_child_launches"]),

    ("m9-open-failure-writes-by-name",
     "Turn the target-open refusal into a fallback: when `os.open` fails, dd is "
     "launched against the device *pathname*. The fail-open shape the descriptor "
     "binding exists to remove, and the `nothing was written` contract is lost.",
     """    except OSError as exc:
        print(f"cannot open {device} for writing ({exc.strerror}): refusing to write")
        print("nothing was written")
        return 1""",
     """    except OSError as exc:
        print(f"cannot open {device} for writing ({exc.strerror}): writing by name")
        return subprocess.call([dd, f"if={iso}", f"of={device}", "bs=4M"])""",
     ["test_23_target_open_failure_refuses_before_any_descriptor_exists"]),
]

RESULT = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)")


def run_suite(cwd):
    return subprocess.run([sys.executable, "-m", "unittest", "discover",
                           "-s", "tests", "-p", PATTERN, "-v"],
                          cwd=str(cwd), capture_output=True, text=True,
                          env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin",
                               "HOME": str(cwd), "TMPDIR": str(cwd)})


def classify(text):
    """({test method name: 'FAIL'|'ERROR'}, {name: last traceback line})."""
    outcomes, reasons = {}, {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = RESULT.match(line)
        if not m:
            continue
        outcome, method, module = m.groups()
        reason = ""
        for follow in lines[i + 1:i + 40]:
            if follow.startswith(("FAIL:", "ERROR:", "=====", "-----")) or \
                    follow.startswith("Ran "):
                break
            if follow.startswith(("AssertionError", "OSError", "TypeError", "ValueError",
                                  "FileNotFoundError", "StopIteration")):
                reason = follow
                break
        outcomes[method] = outcome
        reasons[method] = f"{module}: {reason}" if reason else module
    return outcomes, reasons


def prepare(root, name):
    work = root / name
    (work / "tools").mkdir(parents=True)
    (work / "tests").mkdir(parents=True)
    shutil.copy(REPO / TARGET, work / TARGET)
    for test in sorted((REPO / "tests").glob(PATTERN)):
        shutil.copy(test, work / "tests" / test.name)
    return work


def main():
    print(f"repository {REPO}")
    problems = []
    with tempfile.TemporaryDirectory(prefix="pc5-usb-mutation-") as tmp:
        root = Path(tmp)
        for name, description, old, new, expected in MUTATIONS:
            work = prepare(root, name)
            source = work / TARGET
            text = source.read_text()
            if text.count(old) != 1:
                print(f"\n### {name}\nSKIPPED: pattern occurs {text.count(old)} times")
                problems.append(name)
                continue
            source.write_text(text.replace(old, new, 1))
            proc = run_suite(work)
            outcomes, reasons = classify(proc.stdout + proc.stderr)
            total = re.search(r"^Ran (\d+) tests", proc.stdout + proc.stderr, re.M)
            caught = [t for t in expected if outcomes.get(t) == "FAIL"]
            errored = [t for t in expected if outcomes.get(t) == "ERROR"]
            print(f"\n### {name}\n{description}")
            print(f"tests run: {total.group(1) if total else '?'}; exit code: {proc.returncode}; "
                  f"assertion failures overall: "
                  f"{len([k for k, v in outcomes.items() if v == 'FAIL'])}")
            for t in expected:
                print(f"  intended test {outcomes.get(t, 'did not fail')}: {t}")
                if outcomes.get(t) == "FAIL":
                    print(f"    {reasons[t]}")
            others = sorted(k for k, v in outcomes.items() if v == "FAIL" and k not in expected)
            if others:
                print(f"  also failed: {others}")
            if errored:
                print(f"  NOT acceptable as detection (setup/exception errors): {errored}")
            if proc.returncode == 0 or len(caught) < 1 or errored:
                problems.append(name)

        baseline = prepare(root, "baseline")
        proc = run_suite(baseline)
        print("\n### baseline (unmutated)")
        print(f"exit code: {proc.returncode}")
        print("\n".join(l for l in (proc.stdout + proc.stderr).splitlines()
                        if l.startswith(("Ran ", "OK", "FAILED"))))
        if proc.returncode != 0:
            problems.append("baseline")

    print("\nRESULT:", "PASS: baseline green and every mutation detected by its intended assertion"
          if not problems else f"PROBLEM: {problems}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
