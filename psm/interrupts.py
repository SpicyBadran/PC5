"""PSM-IRQ / PSM-TMR / PSM-IPC v0 — interrupt, timer and IPC test doubles.

Three interfaces, deliberately separate. The contract names interrupt delivery,
timers and clocks, and inter-process message transport as distinct mechanisms;
folding them into one "service device" would invent a hardware interface the
console does not document, which is the specific failure this repository exists
to avoid (see the contract, `interrupts` subsystem).

All three run on the caller's :class:`~psm.psm.Clock`, so timeouts are exact and
a run is reproducible from its inputs.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .psm import Clock, Interface, InterfaceInfo, OpSpec, PsmError, Status, ok


class PsmIrq(Interface):
    """Interrupt delivery: vector acceptance, pending state, and delivery order."""

    info = InterfaceInfo(
        name="PSM-IRQ", version="0.1.0", subsystem="interrupts", milestone="M16",
        note="PC5-authored test double; the console's interrupt controller is UNKNOWN.")

    OPS = {
        "claim": OpSpec(
            name="claim",
            summary="Bind a vector to a PC5-owned handler slot",
            inputs=("vector",),
            outputs="vector, slot",
            errors=(Status.INVALID_ARGUMENT, Status.EXHAUSTED, Status.BAD_STATE),
            invariants=("a vector is claimed once", "re-claiming the same vector is BAD_STATE"),
            claim_ids=("C-IRQ-01",)),
        "raise": OpSpec(
            name="raise",
            summary="Deliver an interrupt on a claimed vector",
            inputs=("vector",),
            outputs="pending",
            errors=(Status.INVALID_ARGUMENT, Status.BAD_STATE),
            invariants=("raising an unclaimed vector is BAD_STATE, never a silent drop",
                        "deliveries are counted, not coalesced silently"),
            claim_ids=("C-IRQ-02",)),
        "take": OpSpec(
            name="take",
            summary="Take the next pending delivery",
            outputs="vector, sequence",
            errors=(Status.NOT_READY,),
            invariants=("nothing pending is NOT_READY, not vector 0",
                        "delivery order is first-raised-first-taken"),
            claim_ids=("C-IRQ-02",)),
        "release": OpSpec(
            name="release",
            summary="Unbind a vector",
            inputs=("vector",),
            outputs="dropped",
            errors=(Status.BAD_STATE,),
            invariants=("releasing an unclaimed vector is BAD_STATE",
                        "pending deliveries for a released vector are counted and dropped"),
            claim_ids=("C-IRQ-02",)),
        "controller_info": OpSpec(
            name="controller_info",
            summary="Report what PC5 presents instead of the console's controller",
            outputs="model, vectors, unknowns",
            errors=(),
            invariants=("the returned model describes PC5's own abstraction, not console hardware",),
            claim_ids=("C-IRQ-01",)),
    }

    def __init__(self, vectors: int = 256) -> None:
        super().__init__()
        self._vectors = int(vectors)
        self._claimed: Dict[int, int] = {}
        self._pending: List[int] = []
        self._sequence = 0
        self._dropped = 0

    def op_claim(self, vector: int) -> Any:
        vector = int(vector)
        if not 0 <= vector < self._vectors:
            raise PsmError(Status.INVALID_ARGUMENT, f"vectors are 0..{self._vectors - 1}")
        if vector in self._claimed:
            raise PsmError(Status.BAD_STATE, f"vector {vector} is already claimed")
        slot = len(self._claimed)
        self._claimed[vector] = slot
        return ok({"vector": vector, "slot": slot})

    def op_raise(self, vector: int) -> Any:
        vector = int(vector)
        if not 0 <= vector < self._vectors:
            raise PsmError(Status.INVALID_ARGUMENT, f"vectors are 0..{self._vectors - 1}")
        if vector not in self._claimed:
            raise PsmError(Status.BAD_STATE,
                           f"vector {vector} is not claimed: a raised interrupt with no handler "
                           f"is a firmware bug an emulator must not hide")
        self._pending.append(vector)
        self._sequence += 1
        return ok({"pending": len(self._pending)})

    def op_take(self) -> Any:
        if not self._pending:
            raise PsmError(Status.NOT_READY, "no pending delivery")
        vector = self._pending.pop(0)
        return ok({"vector": vector, "sequence": self._sequence})

    def op_release(self, vector: int) -> Any:
        vector = int(vector)
        if vector not in self._claimed:
            raise PsmError(Status.BAD_STATE, f"vector {vector} is not claimed")
        del self._claimed[vector]
        dropped = sum(1 for pending in self._pending if pending == vector)
        self._pending = [pending for pending in self._pending if pending != vector]
        self._dropped += dropped
        return ok({"dropped": dropped})

    def op_controller_info(self) -> Any:
        return ok({
            "model": "PC5 event-queue abstraction (not the console's controller)",
            "vectors": self._vectors,
            "unknowns": ("the console's interrupt controller and its programming interface",),
        })


class PsmTimer(Interface):
    """Monotonic timer and real-time clock on a virtual clock."""

    info = InterfaceInfo(
        name="PSM-TMR", version="0.1.0", subsystem="interrupts", milestone="M16",
        note="PC5-authored test double; timer and RTC values on the console are not public.")

    OPS = {
        "now": OpSpec(
            name="now",
            summary="Monotonic nanoseconds since platform start",
            outputs="nanoseconds",
            errors=(),
            invariants=("the value never decreases",),
            claim_ids=("C-TMR-01",)),
        "schedule": OpSpec(
            name="schedule",
            summary="Arm a deadline that raises a claimed vector",
            inputs=("deadline", "vector"),
            outputs="deadline, vector",
            errors=(Status.INVALID_ARGUMENT, Status.BAD_STATE),
            invariants=("a deadline in the past is INVALID_ARGUMENT",
                        "a vector that is not claimed is BAD_STATE",
                        "deadlines fire in order"),
            claim_ids=("C-TMR-02",)),
        "advance": OpSpec(
            name="advance",
            summary="Advance the virtual clock and fire due deadlines",
            inputs=("nanoseconds",),
            outputs="fired, now",
            errors=(Status.INVALID_ARGUMENT,),
            invariants=("fired deadlines are removed", "the clock never runs backwards"),
            claim_ids=("C-TMR-02",)),
        "rtc_read": OpSpec(
            name="rtc_read",
            summary="Read the real-time clock as configured by the platform",
            outputs="seconds, source",
            errors=(),
            invariants=("the source is named, so a synthetic clock is never mistaken for a "
                        "firmware RTC",),
            claim_ids=("C-TMR-02",)),
    }

    def __init__(self, clock: Clock | None = None, rtc_epoch: int = 0) -> None:
        super().__init__()
        self.clock = clock or Clock()
        self._deadlines: List[Dict[str, int]] = []
        self._rtc_epoch = int(rtc_epoch)
        self.irq: PsmIrq | None = None

    def bind_irq(self, irq: PsmIrq) -> None:
        self.irq = irq

    def op_now(self) -> Any:
        return ok({"nanoseconds": self.clock.now()})

    def op_schedule(self, deadline: int, vector: int) -> Any:
        deadline, vector = int(deadline), int(vector)
        if deadline <= self.clock.now():
            raise PsmError(Status.INVALID_ARGUMENT,
                           "a deadline must be in the future; a timer cannot fire backwards")
        if self.irq is None or vector not in self.irq._claimed:
            raise PsmError(Status.BAD_STATE,
                           f"vector {vector} is not claimed: a deadline with no handler would "
                           f"silently do nothing")
        self._deadlines.append({"deadline": deadline, "vector": vector})
        self._deadlines.sort(key=lambda item: item["deadline"])
        return ok({"deadline": deadline, "vector": vector})

    def op_advance(self, nanoseconds: int) -> Any:
        nanoseconds = int(nanoseconds)
        self.clock.advance(nanoseconds)
        fired = [item for item in self._deadlines if item["deadline"] <= self.clock.now()]
        self._deadlines = [item for item in self._deadlines if item["deadline"] > self.clock.now()]
        for item in fired:
            if self.irq is not None:
                self.irq._pending.append(item["vector"])
        return ok({"fired": [item["vector"] for item in fired], "now": self.clock.now()})

    def op_rtc_read(self) -> Any:
        return ok({"seconds": self._rtc_epoch + self.clock.now() // 1_000_000_000,
                   "source": "platform-configured; not a read of the machine's RTC"})


class PsmIpc(Interface):
    """Opaque message transport between guest components (depth-bounded queues)."""

    info = InterfaceInfo(
        name="PSM-IPC", version="0.1.0", subsystem="interrupts", milestone="M16",
        note="PC5-authored test double; the console's IPC mechanism is UNKNOWN.")

    OPS = {
        "create": OpSpec(
            name="create",
            summary="Create a named queue of a given depth",
            inputs=("name", "depth"),
            outputs="name, depth",
            errors=(Status.INVALID_ARGUMENT, Status.BAD_STATE),
            invariants=("a duplicate name is BAD_STATE", "depth is 1..1024"),
            claim_ids=("C-IPC-01",)),
        "send": OpSpec(
            name="send",
            summary="Enqueue one message",
            inputs=("name", "message"),
            outputs="depth_used",
            errors=(Status.NO_DEVICE, Status.OVERFLOW, Status.INVALID_ARGUMENT),
            invariants=("a full queue is OVERFLOW, never a silent drop",
                        "an unknown queue is NO_DEVICE"),
            claim_ids=("C-IPC-01",)),
        "recv": OpSpec(
            name="recv",
            summary="Dequeue one message, waiting on the virtual clock",
            inputs=("name", "timeout"),
            outputs="message, waited",
            errors=(Status.NO_DEVICE, Status.TIMEOUT, Status.INVALID_ARGUMENT),
            invariants=("an empty queue after the timeout is TIMEOUT",),
            claim_ids=("C-IPC-01",)),
        "destroy": OpSpec(
            name="destroy",
            summary="Remove a queue and count what it still held",
            inputs=("name",),
            outputs="discarded",
            errors=(Status.NO_DEVICE,),
            invariants=("messages still queued are counted, not hidden",),
            claim_ids=("C-IPC-01",)),
    }

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__()
        self.clock = clock or Clock()
        self._queues: Dict[str, Dict[str, Any]] = {}

    def _queue(self, name: str) -> Dict[str, Any]:
        queue = self._queues.get(name)
        if queue is None:
            raise PsmError(Status.NO_DEVICE, f"no queue named {name!r}")
        return queue

    def op_create(self, name: str, depth: int) -> Any:
        if not isinstance(name, str) or not name:
            raise PsmError(Status.INVALID_ARGUMENT, "a queue needs a name")
        depth = int(depth)
        if not 1 <= depth <= 1024:
            raise PsmError(Status.INVALID_ARGUMENT, "depth is 1..1024")
        if name in self._queues:
            raise PsmError(Status.BAD_STATE, f"queue {name!r} already exists")
        self._queues[name] = {"depth": depth, "items": []}
        return ok({"name": name, "depth": depth})

    def op_send(self, name: str, message: str) -> Any:
        queue = self._queue(name)
        if len(queue["items"]) >= queue["depth"]:
            raise PsmError(Status.OVERFLOW,
                           f"queue {name!r} is full ({queue['depth']}); a dropped message would "
                           f"be a silent protocol failure")
        queue["items"].append(message)
        return ok({"depth_used": len(queue["items"])})

    def op_recv(self, name: str, timeout: int) -> Any:
        queue = self._queue(name)
        timeout = int(timeout)
        if timeout < 0:
            raise PsmError(Status.INVALID_ARGUMENT, "timeout cannot be negative")
        if queue["items"]:
            return ok({"message": queue["items"].pop(0), "waited": 0})
        self.clock.advance(timeout)
        raise PsmError(Status.TIMEOUT,
                       f"no message within {timeout} ns; the clock advanced and nothing arrived")

    def op_destroy(self, name: str) -> Any:
        queue = self._queue(name)
        discarded = len(queue["items"])
        del self._queues[name]
        return ok({"discarded": discarded})
