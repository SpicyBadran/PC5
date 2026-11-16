"""Mutation tests: the conformance suite must be able to fail.

A suite that has only ever seen working code proves nothing about its own
sensitivity, so each test here replaces one double with a deliberately broken
variant and requires the suite to catch it. A surviving mutation is a hole in the
suite, and the test fails rather than the hole shipping.

Every mutation below is a mistake a real implementation could plausibly make: an
allocator that hands out the same block twice, a refcount that lets go twice, a
timeout that returns a plausible message, a log that drops records when full.
"""

from __future__ import annotations

import dataclasses
import unittest
from typing import Callable, Dict, List, Tuple

from psm import conformance
from psm.audio import PsmAudio
from psm.cpu import PsmCpu
from psm.graphics import PsmGraphics
from psm.interrupts import PsmIpc, PsmIrq, PsmTimer
from psm.memory import PsmMemory
from psm.psm import Interface, OpSpec, Status, fail, ok
from psm.services import PsmServices
from psm.storage import PsmStorage
from psm.usb import PsmUsb


def _support(spec: OpSpec) -> OpSpec:
    """Turn a documented refusal into an implemented operation, so it can lie."""
    return dataclasses.replace(spec, unsupported=False, errors=(Status.OK,))


class OverlappingAllocator(PsmMemory):
    """Every allocation reports the same device address."""

    def op_allocate(self, size: int, alignment: int, purpose: str, engine: str):
        outcome = super().op_allocate(size, alignment, purpose, engine)
        if outcome.ok:
            outcome.value["device_address"] = 0x1000_0000
        return outcome


class DoubleReleaseAllocator(PsmMemory):
    """Releasing twice succeeds silently."""

    def op_release(self, handle: int):
        try:
            return super().op_release(handle)
        except Exception:
            return ok({"released": True, "note": "already gone"})


class OptimisticMsr(PsmCpu):
    """Returns a zero for a security-state MSR instead of refusing."""

    def op_msr_read(self, address: int):
        try:
            return super().op_msr_read(address)
        except Exception:
            return ok({"value": 0, "semantics": "unknown"})


class FrozenReset(PsmCpu):
    """Reset does not clear state and does not advance the epoch."""

    def op_reset(self):
        outcome = super().op_reset()
        outcome.value["reset_epoch"] = 0
        return outcome


class ChattyTimeout(PsmIpc):
    """A timed-out receive returns a fabricated message."""

    def op_recv(self, name: str, timeout: int):
        try:
            return super().op_recv(name, timeout)
        except Exception:
            return ok({"message": "", "waited": timeout})


class ReorderingQueue(PsmIpc):
    """Messages come out last-in-first-out."""

    def op_recv(self, name: str, timeout: int):
        queue = self._queue(name)
        if len(queue["items"]) > 1:
            queue["items"].reverse()
        return super().op_recv(name, timeout)


class SloppyCompletion(PsmStorage):
    """Completes a tag for anyone who asks."""

    def op_complete(self, device: str, tag: int, owner: str):
        try:
            return super().op_complete(device, tag, owner)
        except Exception:
            self._queues[device][0]["outstanding"].discard(tag) if False else None
            return ok({"status": "success", "sectors": 0, "tag": tag})


class SilentDurability(PsmStorage):
    """Reports zero durable sectors after a flush that did write data."""

    def op_flush(self, device: str, queue_id: int):
        outcome = super().op_flush(device, queue_id)
        outcome.value["flushed_sectors"] = 0
        return outcome


class StaticSequence(PsmGraphics):
    """Presentation numbers never advance."""

    def op_present(self, surface: int, owner: str):
        outcome = super().op_present(surface, owner)
        outcome.value["sequence"] = 1
        return outcome


class LyingSubmit(PsmGraphics):
    """An UNKNOWN command submission reports success."""

    OPS = dict(PsmGraphics.OPS)
    OPS["submit"] = _support(PsmGraphics.OPS["submit"])

    def op_submit(self, context: int, commands: bytes, owner: str):
        return ok({"submitted": True})


class UnboundedAudio(PsmAudio):
    """A write larger than the buffer is accepted."""

    def op_write(self, stream: int, frames: int, owner: str):
        try:
            return super().op_write(stream, frames, owner)
        except Exception:
            return ok({"queued_frames": frames})


class SloppyHid(PsmUsb):
    """An oversized HID report is accepted and truncated."""

    def op_hid_report(self, handle: int, report: bytes):
        try:
            return super().op_hid_report(handle, report)
        except Exception:
            return ok({"accepted": len(report[:64])})


class DroppingLog(PsmServices):
    """The log silently drops records once full."""

    def op_log_write(self, level: str, message: str):
        try:
            return super().op_log_write(level, message)
        except Exception:
            return ok({"written": True, "dropped": True})


class ForgetfulInterrupts(PsmIrq):
    """A released vector's pending deliveries are not counted."""

    def op_release(self, vector: int):
        outcome = super().op_release(vector)
        outcome.value["dropped"] = 0
        return outcome


class BackwardsClock(PsmTimer):
    """A negative advance moves the clock backwards instead of refusing."""

    def op_advance(self, nanoseconds: int):
        try:
            return super().op_advance(nanoseconds)
        except Exception:
            self.clock._now += int(nanoseconds)      # the bug: no guard
            return ok({"fired": [], "now": self.clock.now()})


class LeakyServices(PsmServices):
    """Panic reports success but leaves the interface running."""

    def op_panic(self, reason: str):
        self._panicked = False
        return ok({"stopped": reason})


MUTATIONS: List[Tuple[str, str, Callable[[], Dict[str, Interface]]]] = [
    ("memory", "allocations overlap", OverlappingAllocator),
    ("memory", "release succeeds twice", DoubleReleaseAllocator),
    ("cpu", "a restricted MSR returns zero", OptimisticMsr),
    ("cpu", "reset keeps its epoch", FrozenReset),
    ("ipc", "a timeout returns a message", ChattyTimeout),
    ("ipc", "messages are reordered", ReorderingQueue),
    ("storage", "a foreign owner completes a tag", SloppyCompletion),
    ("storage", "flush forgets what it made durable", SilentDurability),
    ("graphics", "presentation numbers freeze", StaticSequence),
    ("graphics", "an unknown submission reports success", LyingSubmit),
    ("audio", "a write overflows silently", UnboundedAudio),
    ("usb", "an oversized report is truncated", SloppyHid),
    ("services", "the log drops records when full", DroppingLog),
    ("irq", "released vectors forget their deliveries", ForgetfulInterrupts),
    ("timer", "the clock can run backwards", BackwardsClock),
    ("services", "panic does not stop the interface", LeakyServices),
]


def run_mutant(key: str, replacement: type) -> List[str]:
    made = conformance.fresh_interfaces()
    made[key] = replacement()
    return [str(check) for check in conformance.run_all(made) if not check.ok]


class MutationTest(unittest.TestCase):
    def test_every_mutation_is_caught(self):
        survivors = []
        for key, description, replacement in MUTATIONS:
            with self.subTest(mutation=description):
                failures = run_mutant(key, replacement)
                if not failures:
                    survivors.append(f"{key}: {description}")
                else:
                    self.assertTrue(failures)
        self.assertEqual(survivors, [], "the suite did not notice: " + "; ".join(survivors))

    def test_the_baseline_is_green_so_failures_mean_something(self):
        made = conformance.fresh_interfaces()
        self.assertEqual([str(c) for c in conformance.run_all(made) if not c.ok], [])

    def test_a_mutation_is_reported_with_its_check_name(self):
        failures = run_mutant("memory", OverlappingAllocator)
        self.assertTrue(any("PSM-MEM" in failure for failure in failures), failures)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
