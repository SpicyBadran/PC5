#!/usr/bin/env python3
"""Deliberate-mutation harness for the USB identity boundary (disposable copies).

Each mutation is applied to a private copy of ``tools/pc5ctl.py`` together with
the permanent USB regression tests, the suite is run there, and the result is
judged against what the mutation is supposed to break:

  * a mutation is DETECTED only when an *expected* test fails with an
    ``AssertionError`` -- an ``ERROR`` (setup/teardown exception) does not count;
  * the unmutated baseline must be green, otherwise the run proves nothing.

Nothing here writes to a device: the tests use synthetic sysfs fixtures,
ordinary temporary files and a mocked ``dd``.
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

# (name, description, old text, new text, tests that must catch it)
MUTATIONS = [
    ("m1-validation-follows-the-pathname",
     "Restore pathname-based validation: the pre-write decision re-resolves the "
     "device path instead of using the identity behind the held descriptor.",
     "        again = check_target_safe(device, topology=_BoundTopology(Topology(), opened_as))",
     "        again = check_target_safe(device)",
     ["test_08_symlink_substitution_after_opening_cannot_authorize_the_write"]),

    ("m2-child-writes-the-pathname",
     "Substitute the original pathname after validation: dd is handed the device "
     "path instead of the descriptor it was authorized for.",
     '        writing = f"/proc/self/fd/{target_fd}"',
     '        writing = device',
     ["test_01_valid_descriptor_and_matching_identity_writes_through_the_fd",
      "test_16_child_receives_exactly_the_validated_descriptor"]),

    ("m3-no-pass_fds",
     "Omit pass_fds: the child never receives the descriptor, so the "
     "descriptor-backed write silently cannot work.",
     "            return subprocess.call(argv, close_fds=True, pass_fds=(target_fd,))",
     "            return subprocess.call(argv)",
     ["test_03_descriptor_inheritance_is_explicit"]),

    ("m4-wrong-descriptor-inherited",
     "Pass the wrong descriptor: the child is told to write one number above the "
     "descriptor that was validated.",
     '        writing = f"/proc/self/fd/{target_fd}"',
     '        writing = f"/proc/self/fd/{target_fd + 1}"',
     ["test_01_valid_descriptor_and_matching_identity_writes_through_the_fd",
      "test_16_child_receives_exactly_the_validated_descriptor"]),

    ("m5-no-final-identity-comparison",
     "Skip the final identity comparison: the descriptor is not re-read after the "
     "decision and before the child starts.",
     """        if fd_block_identity(target_fd) != opened_as:
            print(f"descriptor for {device} changed before the write: refusing to write")
            print("nothing was written")
            return 1""",
     "        pass  # MUTANT: no last identity comparison",
     ["test_02_descriptor_closed_before_the_child_launches",
      "test_05_descriptor_redirected_to_a_different_object",
      "test_17_final_validation_failure_immediately_before_writing"]),

    ("m6-partial-enumeration-accepted",
     "Ignore incomplete partition enumeration: children that cannot be inspected "
     "are skipped instead of making the protected set unknown.",
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
     ["test_12_partial_topology_followed_by_an_enumeration_error"]),

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
     ["test_13_unreadable_sysfs_relationship_data",
      "test_13b_unreadable_holders_directory",
      "test_unreadable_slaves_is_unknown_not_empty",
      "test_unreadable_slaves_refuses_the_physical_backing_partition"]),

    ("m8-open-failure-writes-by-name",
     "Turn the target-open refusal into a fallback: when ``os.open`` fails, dd is "
     "launched against the device *pathname* instead of the command refusing. "
     "This is the fail-open shape the descriptor work exists to remove, and it "
     "also drops the ``nothing was written`` contract.",
     """    except OSError as exc:
        print(f"cannot open {device} for writing ({exc.strerror}): refusing to write")
        print("nothing was written")
        return 1""",
     """    except OSError as exc:
        print(f"cannot open {device} for writing ({exc.strerror}): writing by name")
        return subprocess.call(["dd", f"if={iso}", f"of={device}",
                                "bs=4M", "conv=fsync"])""",
     ["test_22_target_open_failure_refuses_before_any_descriptor_exists"]),
]

RESULT = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)")


def run_suite(cwd):
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover",
                           "-s", "tests", "-p", PATTERN, "-v"],
                          cwd=str(cwd), capture_output=True, text=True,
                          env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin",
                               "HOME": str(cwd), "TMPDIR": str(cwd)})
    return proc


def classify(text):
    """Return ({test id: 'FAIL'|'ERROR'}, {test id: last traceback line})."""
    outcomes, reasons = {}, {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = RESULT.match(line)
        if not m:
            continue
        outcome, method, module = m.groups()
        reason = ""
        for follow in lines[i + 1:i + 40]:
            if follow.startswith(("FAIL:", "ERROR:", "=======", "------")) or \
                    follow.startswith("Ran "):
                break
            if follow.startswith(("AssertionError", "OSError", "TypeError",
                                  "ValueError", "FileNotFoundError", "StopIteration")):
                reason = follow
                break
        outcomes[method] = outcome          # keyed by test method name
        reasons[method] = f"{module}: {reason}" if reason else module
    return outcomes, reasons


def main():
    print(f"repository {REPO}")
    failures = []
    with tempfile.TemporaryDirectory(prefix="pc5-usb-mutation-") as tmp:
        root = Path(tmp)
        for name, description, old, new, expected in MUTATIONS:
            work = root / name
            (work / "tools").mkdir(parents=True)
            (work / "tests").mkdir(parents=True)
            shutil.copy(REPO / TARGET, work / TARGET)
            for test in sorted((REPO / "tests").glob(PATTERN)):
                shutil.copy(test, work / "tests" / test.name)
            source = work / TARGET
            text = source.read_text()
            if text.count(old) != 1:
                print(f"\n### {name}\nSKIPPED: pattern occurs {text.count(old)} times")
                failures.append(name)
                continue
            source.write_text(text.replace(old, new, 1))
            proc = run_suite(work)
            outcomes, reasons = classify(proc.stdout + proc.stderr)
            caught = [t for t in expected if outcomes.get(t) == "FAIL"]
            errored = [t for t in expected if outcomes.get(t) == "ERROR"]
            total = re.search(r"^Ran (\d+) tests", proc.stdout + proc.stderr, re.M)
            print(f"\n### {name}\n{description}")
            print(f"tests run: {total.group(1) if total else '?'}; "
                  f"exit code: {proc.returncode}; "
                  f"assertion failures detected: {len([k for k, v in outcomes.items() if v == 'FAIL'])}")
            for t in expected:
                status = outcomes.get(t, "did not fail")
                print(f"  intended test {status}: {t}")
                if status == "FAIL":
                    print(f"    {reasons[t]}")
            others = sorted(k for k, v in outcomes.items() if v == "FAIL" and k not in expected)
            if others:
                print(f"  also failed: {others}")
            if errored:
                print(f"  NOT acceptable as detection (setup errors): {errored}")
            if proc.returncode == 0 or len(caught) < 1 or errored:
                failures.append(name)
        baseline = root / "baseline"
        (baseline / "tools").mkdir(parents=True)
        (baseline / "tests").mkdir(parents=True)
        shutil.copy(REPO / TARGET, baseline / TARGET)
        for test in sorted((REPO / "tests").glob(PATTERN)):
            shutil.copy(test, baseline / "tests" / test.name)
        proc = run_suite(baseline)
        print(f"\n### baseline (unmutated)\nexit code: {proc.returncode}")
        print("\n".join(l for l in (proc.stdout + proc.stderr).splitlines()
                        if l.startswith(("Ran ", "OK", "FAILED"))))
        if proc.returncode != 0:
            failures.append("baseline")
    print("\nRESULT:", "PASS: baseline green and every mutation detected by its intended assertion"
          if not failures else f"PROBLEM: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
