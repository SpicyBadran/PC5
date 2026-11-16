"""PSM-GFX v0 — graphics test double (contract subsystem ``graphics``).

The GPU's public description (an RDNA 2-based engine with ray tracing, variable
frequency) is all the contract has. Command encoding, the firmware interface and
the shader formats the system software submits are not public, so this double
models the *shape* PC5 controls - context lifetime, fences, presentation of
PC5-owned surfaces - and returns ``UNSUPPORTED`` for anything that would require
guessing an encoding.

That includes command submission itself. A test double that "succeeds" at
submitting a command buffer it does not understand would be the most damaging
kind of success this repository could produce.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .psm import Clock, Interface, InterfaceInfo, OpSpec, PsmError, Status, ok


class PsmGraphics(Interface):
    info = InterfaceInfo(
        name="PSM-GFX", version="0.1.0", subsystem="graphics", milestone="M14",
        note="PC5-authored test double; the console's command encoding is UNKNOWN.")

    OPS = {
        "info": OpSpec(
            name="info",
            summary="Report the graphics device as the contract describes it",
            outputs="engine, ray_tracing, clock_policy, unknowns",
            errors=(),
            invariants=("the unknowns list names the encoding and firmware interfaces",),
            claim_ids=("C-GFX-01", "C-GFX-02")),
        "create_context": OpSpec(
            name="create_context",
            summary="Create a PC5-owned graphics context",
            inputs=("owner",),
            errors=(Status.INVALID_ARGUMENT, Status.EXHAUSTED),
            outputs="context, surface",
            invariants=("contexts are owned by the caller that created them",
                        "the context limit is enforced with EXHAUSTED",
                        "a context owns exactly one scanout surface for its lifetime"),
            decision_ids=("D-PSM-09",)),
        "destroy_context": OpSpec(
            name="destroy_context",
            summary="Destroy a context and release its fences and surface",
            inputs=("context", "owner"),
            outputs="fences_released, surface_released",
            errors=(Status.OWNERSHIP,),
            invariants=("another owner cannot destroy it",),
            decision_ids=("D-PSM-09",)),
        "create_fence": OpSpec(
            name="create_fence",
            summary="Create a fence that a modelled submission can signal",
            inputs=("context", "owner"),
            outputs="fence",
            errors=(Status.OWNERSHIP, Status.EXHAUSTED),
            invariants=("a fence belongs to one context",),
            decision_ids=("D-PSM-09",)),
        "signal_fence": OpSpec(
            name="signal_fence",
            summary="Signal a fence (modelled completion, not a GPU event)",
            inputs=("fence", "owner"),
            outputs="signalled_at",
            errors=(Status.OWNERSHIP, Status.BAD_STATE),
            invariants=("signalling twice is BAD_STATE",
                        "the signal time comes from the virtual clock"),
            decision_ids=("D-PSM-09",)),
        "wait_fence": OpSpec(
            name="wait_fence",
            summary="Wait for a fence on the virtual clock",
            inputs=("fence", "timeout"),
            outputs="waited",
            errors=(Status.OWNERSHIP, Status.TIMEOUT, Status.INVALID_ARGUMENT),
            invariants=("an unsignalled fence after its timeout is TIMEOUT",),
            claim_ids=("C-GFX-03",)),
        "present": OpSpec(
            name="present",
            summary="Present a PC5-owned surface to the display",
            inputs=("surface", "owner"),
            outputs="sequence",
            errors=(Status.OWNERSHIP, Status.INVALID_ARGUMENT),
            invariants=("presentation sequence numbers are monotonic",
                        "an unregistered surface is INVALID_ARGUMENT, never a blank frame",
                        "a destroyed context's surface can no longer be presented"),
            decision_ids=("D-PSM-09",)),
        "submit": OpSpec(
            name="submit",
            summary="Submit a guest command buffer to the modelled GPU",
            inputs=("context", "commands", "owner"),
            outputs="none",
            errors=(Status.UNSUPPORTED,),
            unsupported=True,
            invariants=("returns UNSUPPORTED: the command encoding is UNKNOWN",),
            claim_ids=("C-GFX-03",)),
        "gpu_firmware": OpSpec(
            name="gpu_firmware",
            summary="Talk to the GPU firmware interface",
            inputs=("message",),
            outputs="none",
            errors=(Status.UNSUPPORTED,),
            unsupported=True,
            invariants=("returns UNSUPPORTED: the interface is RESTRICTED/UNKNOWN",),
            claim_ids=("C-GFX-04",)),
    }

    def __init__(self, clock: Clock | None = None, contexts: int = 4) -> None:
        super().__init__()
        self.clock = clock or Clock()
        self._limit = int(contexts)
        self._contexts: Dict[int, Dict[str, Any]] = {}
        self._fences: Dict[int, Dict[str, Any]] = {}
        self._next_context = 1
        self._next_fence = 1
        self._sequence = 0
        self._surfaces: Dict[int, str] = {}

    def op_info(self) -> Any:
        return ok({
            "engine": "RDNA 2-based, ray tracing acceleration, variable frequency",
            "ray_tracing": True,
            "clock_policy": "variable; the model never assumes a fixed frequency",
            "unknowns": ("command encoding", "GPU firmware interface", "shader formats",
                         "compute unit count as reported by the console's own interface"),
        })

    def op_create_context(self, owner: str) -> Any:
        if not isinstance(owner, str) or not owner:
            raise PsmError(Status.INVALID_ARGUMENT, "a context needs an owner")
        if len(self._contexts) >= self._limit:
            raise PsmError(Status.EXHAUSTED, f"the context limit ({self._limit}) is reached")
        context = self._next_context
        self._next_context += 1
        self._contexts[context] = {"context": context, "owner": owner, "fences": set()}
        # A context owns its scanout surface for exactly as long as the context
        # exists: presentation is a context operation, not a global one.
        self._surfaces[context] = owner
        return ok({"context": context, "surface": context})

    def op_destroy_context(self, context: int, owner: str) -> Any:
        entry = self._contexts.get(int(context))
        if entry is None:
            raise PsmError(Status.OWNERSHIP, f"context {context} is not live")
        if entry["owner"] != owner:
            raise PsmError(Status.OWNERSHIP,
                           f"context {context} belongs to {entry['owner']!r}, not {owner!r}")
        released = len(entry["fences"])
        for fence in entry["fences"]:
            self._fences.pop(fence, None)
        surface_released = self._surfaces.pop(entry["context"], None) is not None
        del self._contexts[entry["context"]]
        return ok({"fences_released": released, "surface_released": surface_released})

    def op_create_fence(self, context: int, owner: str) -> Any:
        entry = self._contexts.get(int(context))
        if entry is None or entry["owner"] != owner:
            raise PsmError(Status.OWNERSHIP, f"context {context} is not {owner!r}'s to use")
        if len(self._fences) >= 4096:
            raise PsmError(Status.EXHAUSTED, "fence table is full")
        fence = self._next_fence
        self._next_fence += 1
        self._fences[fence] = {"fence": fence, "context": entry["context"], "owner": owner,
                               "signalled_at": None}
        entry["fences"].add(fence)
        return ok({"fence": fence})

    def op_signal_fence(self, fence: int, owner: str) -> Any:
        record = self._fences.get(int(fence))
        if record is None or record["owner"] != owner:
            raise PsmError(Status.OWNERSHIP, f"fence {fence} is not {owner!r}'s")
        if record["signalled_at"] is not None:
            raise PsmError(Status.BAD_STATE, f"fence {fence} was already signalled")
        record["signalled_at"] = self.clock.now()
        return ok({"signalled_at": record["signalled_at"]})

    def op_wait_fence(self, fence: int, timeout: int) -> Any:
        record = self._fences.get(int(fence))
        if record is None:
            raise PsmError(Status.OWNERSHIP, f"fence {fence} does not exist")
        timeout = int(timeout)
        if timeout < 0:
            raise PsmError(Status.INVALID_ARGUMENT, "timeout cannot be negative")
        if record["signalled_at"] is not None:
            return ok({"waited": 0})
        self.clock.advance(timeout)
        raise PsmError(Status.TIMEOUT,
                       f"fence {fence} was not signalled within {timeout} ns; the clock advanced "
                       f"and nothing arrived")

    def op_present(self, surface: int, owner: str) -> Any:
        surface = int(surface)
        holder = self._surfaces.get(surface)
        if holder is None:
            raise PsmError(Status.INVALID_ARGUMENT,
                           f"surface {surface} is not a PC5-owned surface")
        if holder != owner:
            raise PsmError(Status.OWNERSHIP, f"surface {surface} belongs to {holder!r}")
        self._sequence += 1
        return ok({"sequence": self._sequence})
