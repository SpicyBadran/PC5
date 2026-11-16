"""The conformance suite itself: does it run, does it pass, is it deterministic?

What this file establishes: PC5's doubles behave as PC5's contract says, every
documented operation answers with a status it documented, an operation the contract
marks `UNKNOWN` never produces a value, and two runs of the suite are identical.

What it does not establish: anything at all about Sony's system software. No genuine
PS5 software has been executed against these interfaces, and a green run here is not
a compatibility claim.
"""

from __future__ import annotations

import unittest

from psm import conformance
from psm.psm import PsmError, Status

#: A well-formed argument for every documented input name. Used to prove that each
#: operation answers with a *documented* status instead of raising or inventing one.
SENTINELS = {
    "leaf": 0, "subleaf": 0, "address": 3, "vector": 14, "size": 4096,
    "alignment": 4096, "purpose": "queue", "engine": "cpu", "handle": 1, "offset": 0,
    "name": "pc5.probe", "timeout": 0, "depth": 1, "message": "probe", "deadline": 0,
    "nanoseconds": 0, "device": "nvme0", "queue_id": 0, "op": "read", "lba": 0,
    "sectors": 1, "owner": "pc5", "tag": 1, "context": 1, "fence": 1, "surface": 1,
    "commands": b"", "payload": b"", "stream": 1, "frames": 1, "rate": 48000,
    "channels": 2, "format": "pcm_s16le", "latency_class": "interactive",
    "device_class": "hid", "vid_pid": "054c:0ce6", "report": b"\x01",
    "program": b"", "compressed": b"", "algorithm": "zlib", "count": 1,
    "level": "info", "version": "0.1.0", "reason": "probe",
    "capacity_bytes": 1 << 20, "queue_depth": 1,
}


class ConformanceRunTest(unittest.TestCase):
    def test_the_suite_passes(self):
        checks = conformance.run_all()
        failures = [str(check) for check in checks if not check.ok]
        self.assertEqual(failures, [], "\n".join(failures))

    def test_the_suite_has_meaningful_size(self):
        self.assertGreaterEqual(len(conformance.run_all()), 250)

    def test_every_check_carries_a_label_and_an_interface(self):
        for check in conformance.run_all():
            self.assertTrue(check.name.strip())
            self.assertIn(check.interface, {f"PSM-{n}" for n in
                                            ("CPU", "MEM", "IRQ", "TMR", "IPC", "STOR",
                                             "GFX", "AUD", "USB", "SVC")})

    def test_every_interface_is_exercised(self):
        names = {check.interface for check in conformance.run_all()}
        self.assertEqual(names, set(audit_names()))

    def test_runs_are_deterministic(self):
        """Two fresh runs: identical checks in identical order, identical transcripts."""
        first, second = conformance.fresh_interfaces(), conformance.fresh_interfaces()
        first_checks = conformance.run_all(first)
        second_checks = conformance.run_all(second)
        self.assertEqual([(c.interface, c.name, c.ok) for c in first_checks],
                         [(c.interface, c.name, c.ok) for c in second_checks])
        self.assertEqual(conformance.transcript(first), conformance.transcript(second))

    def test_a_transcript_records_every_call_in_order(self):
        made = conformance.fresh_interfaces()
        conformance.run_all(made)
        for name, entries in conformance.transcript(made).items():
            self.assertTrue(entries, f"{name} recorded no calls")
            self.assertTrue(all(len(entry) == 2 for entry in entries), name)

    def test_unknown_operations_are_refused_without_a_value(self):
        for name, iface in conformance.fresh_interfaces().items():
            for op_name, spec in iface.op_specs().items():
                if not spec.unsupported:
                    continue
                kwargs = {field: SENTINELS[field] for field in spec.inputs}
                outcome = iface.call(op_name, **kwargs)
                self.assertIs(outcome.status, Status.UNSUPPORTED,
                              f"{name}.{op_name}: {outcome.status}")
                self.assertIsNone(outcome.value,
                                  f"{name}.{op_name} fabricated a value for an UNKNOWN op")

    def test_every_operation_answers_with_a_documented_status(self):
        for name, iface in conformance.fresh_interfaces().items():
            for op_name, spec in iface.op_specs().items():
                kwargs = {field: SENTINELS[field] for field in spec.inputs}
                with self.subTest(interface=name, op=op_name):
                    outcome = iface.call(op_name, **kwargs)
                    allowed = set(spec.errors) | {Status.OK}
                    self.assertIn(outcome.status, allowed)
                    if outcome.status is not Status.OK:
                        self.assertIsNone(outcome.value)

    def test_an_unknown_operation_name_is_unsupported_not_an_attribute_error(self):
        for name, iface in conformance.fresh_interfaces().items():
            outcome = iface.call("no_such_operation")
            self.assertIs(outcome.status, Status.UNSUPPORTED, name)
            self.assertIsNone(outcome.value)

    def test_call_arguments_must_match_the_documented_inputs(self):
        iface = conformance.fresh_interfaces()["cpu"]
        self.assertIs(iface.call("cpuid", leaf=0).status, Status.INVALID_ARGUMENT)
        self.assertIs(iface.call("cpuid", leaf=0, subleaf=0, extra=1).status,
                      Status.INVALID_ARGUMENT)

    def test_undocumented_operation_in_the_table_is_a_construction_error(self):
        from psm.psm import OpSpec

        iface = conformance.fresh_interfaces()["cpu"]

        class Broken(type(iface)):  # type: ignore[misc]
            OPS = dict(type(iface).OPS)
            OPS["documented_but_missing"] = OpSpec(
                name="documented_but_missing", summary="claims to exist",
                invariants=("x",), decision_ids=("D-PSM-13",))

        with self.assertRaises(AssertionError):
            Broken()

    def test_errors_raised_by_a_double_become_statuses(self):
        """A PsmError is a status, not a traceback."""
        iface = conformance.fresh_interfaces()["memory"]
        self.assertIs(iface.call("release", handle=999).status, Status.OWNERSHIP)


def audit_names():
    from psm.audit import interfaces
    return list(interfaces())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
