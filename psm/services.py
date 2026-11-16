"""PSM-SVC v0 — system services test double (contract subsystem ``services``).

The platform services PC5 itself owns: a monotonic service clock, bounded
structured logging, a service registry a guest can look up, and a panic path. The
console's own service layer is the thing `M16`/`M20` have to meet; this is the
PC5 side of the meeting point, and the contract marks the console's side
``UNKNOWN``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .psm import Clock, Interface, InterfaceInfo, OpSpec, PsmError, Status, ok

LEVELS = ("debug", "info", "warn", "error", "fatal")
LOG_CAPACITY = 256


class PsmServices(Interface):
    info = InterfaceInfo(
        name="PSM-SVC", version="0.1.0", subsystem="services", milestone="M16",
        note="PC5-authored service layer; not the console's service interface.")

    OPS = {
        "clock_get": OpSpec(
            name="clock_get",
            summary="Read the platform service clock",
            outputs="nanoseconds, source",
            errors=(),
            invariants=("the clock never runs backwards",),
            claim_ids=("C-SVC-01",)),
        "sleep": OpSpec(
            name="sleep",
            summary="Advance the virtual clock by a duration",
            inputs=("nanoseconds",),
            outputs="now",
            errors=(Status.INVALID_ARGUMENT,),
            invariants=("a negative duration is INVALID_ARGUMENT",),
            claim_ids=("C-SVC-01",)),
        "log_write": OpSpec(
            name="log_write",
            summary="Append one structured log record",
            inputs=("level", "message"),
            outputs="records, dropped",
            errors=(Status.INVALID_ARGUMENT, Status.OVERFLOW),
            invariants=("an unknown level is INVALID_ARGUMENT",
                        "a full ring is OVERFLOW and is counted, never silently dropped"),
            claim_ids=("C-SVC-02",)),
        "log_read": OpSpec(
            name="log_read",
            summary="Read up to N records, oldest first",
            inputs=("count",),
            outputs="records",
            errors=(Status.INVALID_ARGUMENT,),
            invariants=("reading does not reorder or invent records",),
            claim_ids=("C-SVC-02",)),
        "service_register": OpSpec(
            name="service_register",
            summary="Register a PC5 service name and ABI version",
            inputs=("name", "version"),
            outputs="name, version",
            errors=(Status.INVALID_ARGUMENT, Status.BAD_STATE),
            invariants=("re-registering a name is BAD_STATE",),
            claim_ids=("C-SVC-01",)),
        "service_lookup": OpSpec(
            name="service_lookup",
            summary="Find a registered service",
            inputs=("name",),
            outputs="name, version",
            errors=(Status.NO_DEVICE,),
            invariants=("an unknown service is NO_DEVICE, not an empty record",),
            claim_ids=("C-SVC-01",)),
        "panic": OpSpec(
            name="panic",
            summary="Stop the platform with a reason",
            inputs=("reason",),
            outputs="stopped",
            errors=(Status.INVALID_ARGUMENT,),
            invariants=("after a panic every other call is BAD_STATE",),
            claim_ids=("C-SVC-01",)),
    }

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__()
        self.clock = clock or Clock()
        self._records: List[Dict[str, Any]] = []
        self._dropped = 0
        self._services: Dict[str, str] = {}
        self._panicked = False

    def _stopped(self, op: str) -> None:
        if self._panicked:
            raise PsmError(Status.BAD_STATE,
                           f"{op}: the platform is stopped; a call after a panic is a bug")

    def op_clock_get(self) -> Any:
        self._stopped("clock_get")
        return ok({"nanoseconds": self.clock.now(), "source": "PC5 virtual clock"})

    def op_sleep(self, nanoseconds: int) -> Any:
        self._stopped("sleep")
        return ok({"now": self.clock.advance(int(nanoseconds))})

    def op_log_write(self, level: str, message: str) -> Any:
        self._stopped("log_write")
        if level not in LEVELS:
            raise PsmError(Status.INVALID_ARGUMENT, f"level must be one of {LEVELS}")
        if not isinstance(message, str) or not message:
            raise PsmError(Status.INVALID_ARGUMENT, "a record needs a message")
        if len(self._records) >= LOG_CAPACITY:
            self._dropped += 1
            raise PsmError(Status.OVERFLOW,
                           f"the log ring ({LOG_CAPACITY}) is full; {self._dropped} record(s) "
                           f"have been refused rather than lost silently")
        self._records.append({"level": level, "message": message, "at": self.clock.now()})
        return ok({"records": len(self._records), "dropped": self._dropped})

    def op_log_read(self, count: int) -> Any:
        self._stopped("log_read")
        count = int(count)
        if count <= 0:
            raise PsmError(Status.INVALID_ARGUMENT, "count must be positive")
        return ok({"records": [dict(record) for record in self._records[:count]]})

    def op_service_register(self, name: str, version: str) -> Any:
        self._stopped("service_register")
        if not isinstance(name, str) or not name:
            raise PsmError(Status.INVALID_ARGUMENT, "a service needs a name")
        if not isinstance(version, str) or not version:
            raise PsmError(Status.INVALID_ARGUMENT, "a service needs an ABI version")
        if name in self._services:
            raise PsmError(Status.BAD_STATE, f"service {name!r} is already registered")
        self._services[name] = version
        return ok({"name": name, "version": version})

    def op_service_lookup(self, name: str) -> Any:
        self._stopped("service_lookup")
        version = self._services.get(name)
        if version is None:
            raise PsmError(Status.NO_DEVICE, f"no service named {name!r}")
        return ok({"name": name, "version": version})

    def op_panic(self, reason: str) -> Any:
        if not isinstance(reason, str) or not reason:
            raise PsmError(Status.INVALID_ARGUMENT, "a panic needs a reason")
        self._panicked = True
        return ok({"stopped": reason})
