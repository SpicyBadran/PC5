"""PSM — the PC5 Platform Service Model.

What this package is
--------------------
A set of *PC5-authored* test-double interfaces for the subsystems the PS5
platform contract names: CPU and privileged execution, memory, interrupts and
timers, storage, graphics, audio, USB, and system services. They exist so that
the contract in ``docs/architecture/PS5_PLATFORM_CONTRACT.md`` can be tested
before any of it is modelled against real hardware, and so that "not known"
has somewhere to live that is not a guessed value.

What this package is not
------------------------
Not a PlayStation 5. Not a Sony interface, not a Sony ABI, and not evidence that
Sony's system software would accept any of it. Interface names are PC5's own
(``PSM-CPU``, ``PSM-MEM``, …), they are versioned independently of Sony, and
nothing here claims to reproduce the console's registers, firmware calls or
protocols. The console-facing truth is the contract document; this code is the
part of it PC5 can test against itself.

The one rule
------------
An operation whose behaviour the contract marks ``UNKNOWN`` or ``RESTRICTED``
returns :attr:`Status.UNSUPPORTED`. It never returns a fabricated success, a
zero-filled buffer, or a guess. A test double that invents a plausible answer is
worse than one that is absent, because the absent one fails loudly.

Determinism
-----------
Every double runs on a virtual clock and a virtual device inventory supplied by
the caller, so a run is reproducible from its inputs alone. That is what makes
the conformance suite meaningful: the same script produces the same transcript.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

PSM_VERSION = "0.1.0"
"""Version of the PC5 Platform Service Model itself (not of any Sony interface)."""


class Status(Enum):
    """Outcome of one interface call.

    The set is deliberately small and total: every call returns one of these,
    and every failure mode the contract names maps onto one of them.
    """

    OK = "ok"
    INVALID_ARGUMENT = "invalid_argument"
    UNSUPPORTED = "unsupported"
    NOT_READY = "not_ready"
    BAD_STATE = "bad_state"
    NO_DEVICE = "no_device"
    EXHAUSTED = "exhausted"
    TIMEOUT = "timeout"
    OWNERSHIP = "ownership"
    OVERFLOW = "overflow"
    DENIED = "denied"


@dataclass(frozen=True)
class Result:
    """A call outcome and, when it succeeded, its value."""

    status: Status
    value: Any = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    def __bool__(self) -> bool:  # a Result is truthy only when it succeeded
        return self.ok

    def __repr__(self) -> str:  # readable in test failures
        return f"Result({self.status.value}, value={self.value!r}, detail={self.detail!r})"


def ok(value: Any = None, detail: str = "") -> Result:
    return Result(Status.OK, value, detail)


def fail(status: Status, detail: str = "") -> Result:
    if status is Status.OK:
        raise ValueError("fail() cannot produce a success")
    return Result(status, None, detail)


class PsmError(Exception):
    """Raised by a double for a documented failure; converted to a Result."""

    def __init__(self, status: Status, detail: str = ""):
        if status is Status.OK:
            raise ValueError("PsmError cannot carry OK")
        super().__init__(detail or status.value)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class OpSpec:
    """One documented operation of an interface.

    ``errors`` is the exhaustive list of failure statuses the operation may
    return. ``unsupported`` marks an operation the contract does not know enough
    about to implement: it must return ``UNSUPPORTED`` and must not carry a
    fabricated value. ``invariants`` are the properties the conformance suite
    checks after the call.
    """

    name: str
    summary: str
    inputs: Tuple[str, ...] = ()
    outputs: str = "none"
    errors: Tuple[Status, ...] = (Status.INVALID_ARGUMENT,)
    unsupported: bool = False
    invariants: Tuple[str, ...] = ()
    claim_ids: Tuple[str, ...] = ()
    #: PC5's own design decisions this operation implements (``D-PSM-*``). An
    #: operation models either something the contract knows about the console or a
    #: PC5 bookkeeping rule; those are different things and are cited differently.
    decision_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class InterfaceInfo:
    """Identity of an interface: PC5's name, version and the contract rows it serves."""

    name: str
    version: str
    subsystem: str
    milestone: str
    authored_by: str = "PC5"
    note: str = "PC5-authored test double; not a Sony hardware interface."


class Interface:
    """Base class for the eight doubles: documentation, dispatch and discipline.

    Dispatch is by name so a conformance suite can drive every interface the same
    way, and so an unknown operation is a defined status (``UNSUPPORTED``) rather
    than an ``AttributeError``. Each ``op_<name>`` method raises
    :class:`PsmError` for documented failures; anything else escaping is a bug in
    the double and is allowed to propagate.
    """

    info: InterfaceInfo
    OPS: Mapping[str, OpSpec] = {}

    def __init__(self) -> None:
        self._calls: list = []
        missing = [name for name, spec in self.OPS.items()
                   if not spec.unsupported and not hasattr(self, f"op_{name}")]
        if missing:
            raise AssertionError(
                f"{type(self).__name__} documents operations it does not implement: {missing}")

    # ---------------------------------------------------------------- dispatch
    def call(self, operation: str, **kwargs: Any) -> Result:
        """Dispatch one documented operation by name.

        The operation name is positional-ish data, not a reserved keyword: several
        operations take an argument literally called ``op`` (storage queue entries),
        so the parameter here is named ``operation`` to leave ``kwargs`` free.
        """
        spec = self.OPS.get(operation)
        if spec is None:
            return fail(Status.UNSUPPORTED,
                        f"{self.info.name} has no operation {operation!r}; "
                        f"documented operations are {sorted(self.OPS)}")
        missing = [name for name in spec.inputs if name not in kwargs]
        unexpected = [name for name in kwargs if name not in spec.inputs]
        if missing or unexpected:
            return fail(Status.INVALID_ARGUMENT,
                        f"{operation}: missing={missing} unexpected={unexpected}")
        self._calls.append((operation, dict(kwargs)))
        if spec.unsupported:
            return fail(Status.UNSUPPORTED,
                        f"{operation}: the contract marks this behaviour UNKNOWN; "
                        f"PSM returns UNSUPPORTED rather than a fabricated value")
        try:
            return getattr(self, f"op_{operation}")(**kwargs)
        except PsmError as exc:
            return Result(exc.status, None, exc.detail)

    @property
    def transcript(self) -> Tuple[Tuple[str, Dict[str, Any]], ...]:
        """Every call made, in order: the deterministic-replay record."""
        return tuple(self._calls)

    # -------------------------------------------------------------- conformance
    def op_specs(self) -> Dict[str, OpSpec]:
        return dict(self.OPS)

    def conformance_cases(self) -> Iterable[Any]:
        """Cases for this interface; the default is none, each double adds its own."""
        return ()

    def _require(self, condition: bool, status: Status, detail: str) -> None:
        if not condition:
            raise PsmError(status, detail)


class Clock:
    """Virtual monotonic clock in nanoseconds. Tests advance it; nothing sleeps."""

    def __init__(self, now: int = 0) -> None:
        self._now = int(now)

    def now(self) -> int:
        return self._now

    def advance(self, delta: int) -> int:
        if delta < 0:
            raise PsmError(Status.INVALID_ARGUMENT, "the clock cannot run backwards")
        self._now += int(delta)
        return self._now


@dataclass
class Case:
    """One conformance case: call something, expect a status and a property.

    ``check`` receives the :class:`Result` and returns an optional complaint
    string; ``None`` means the property held. ``replay`` marks a case whose
    determinism is asserted by running the whole interface transcript twice.
    """

    interface: str
    name: str
    op: str
    kwargs: Dict[str, Any] = field(default_factory=dict)
    expect: Status = Status.OK
    check: Optional[Callable[[Result], Optional[str]]] = None
    replay: bool = False
    claim_ids: Tuple[str, ...] = ()


def require_value(keys: Iterable[str]) -> Callable[[Result], Optional[str]]:
    """Property helper: the result must be a mapping carrying these keys."""

    def check(result: Result) -> Optional[str]:
        if not isinstance(result.value, Mapping):
            return f"value is {type(result.value).__name__}, expected a mapping"
        absent = [key for key in keys if key not in result.value]
        return f"missing keys {absent}" if absent else None

    return check
