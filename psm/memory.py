"""PSM-MEM v0 — unified-memory test double (contract subsystem ``memory``).

The console's memory is one GDDR6 pool both engines see. That much is public.
How the system software allocates from it, and what protection domains exist, is
not: the contract marks those ``UNKNOWN``, so the double models *mapping and
ownership* - the part PC5 has to get right regardless - and refuses the rest.

The invariants this double enforces are the ones a guest would notice:
translation stays inside the allocation it was given, releasing twice is an
ownership error rather than a silent no-op, and exhaustion is reported as
``EXHAUSTED`` with no partial allocation left behind.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .psm import Interface, InterfaceInfo, OpSpec, PsmError, Status, ok

PURPOSES = ("guest", "framebuffer", "queue", "payload", "test")
ENGINES = ("cpu", "gpu", "io")
ALIGNMENT_MAX = 2 * 1024 * 1024


class PsmMemory(Interface):
    info = InterfaceInfo(
        name="PSM-MEM", version="0.1.0", subsystem="memory", milestone="M12",
        note="PC5-authored test double for the memory contract; not a Sony interface.")

    OPS = {
        "topology": OpSpec(
            name="topology",
            summary="Report the memory pool PC5 presents",
            outputs="pool_bytes, engines, coherent, unknowns",
            errors=(),
            invariants=("the pool is one address space both engines see",),
            claim_ids=("C-MEM-01", "C-MEM-02")),
        "allocate": OpSpec(
            name="allocate",
            summary="Own a range of the pool and return a handle",
            inputs=("size", "alignment", "purpose", "engine"),
            outputs="handle, device_address, size",
            errors=(Status.INVALID_ARGUMENT, Status.EXHAUSTED, Status.DENIED),
            invariants=("the device address is aligned to the requested alignment",
                        "two live handles never overlap",
                        "EXHAUSTED leaves no allocation behind"),
            claim_ids=("C-MEM-03", "C-MEM-04")),
        "release": OpSpec(
            name="release",
            summary="Give an allocation back to the pool",
            inputs=("handle",),
            outputs="freed_bytes",
            errors=(Status.OWNERSHIP,),
            invariants=("a released handle is never released again",
                        "an unknown handle is OWNERSHIP, not a silent success"),
            claim_ids=("C-MEM-05",)),
        "translate": OpSpec(
            name="translate",
            summary="Turn an offset inside an allocation into a device address",
            inputs=("handle", "offset"),
            outputs="device_address",
            errors=(Status.OWNERSHIP, Status.INVALID_ARGUMENT),
            invariants=("the result stays inside the allocation",),
            claim_ids=("C-MEM-05",)),
        "share": OpSpec(
            name="share",
            summary="Make an allocation visible to another engine",
            inputs=("handle", "engine"),
            outputs="engines",
            errors=(Status.OWNERSHIP, Status.INVALID_ARGUMENT),
            invariants=("sharing is idempotent",
                        "sharing never copies: the handle's address does not change"),
            claim_ids=("C-MEM-06",)),
        "stats": OpSpec(
            name="stats",
            summary="Pool accounting as the guest would see it",
            outputs="pool_bytes, allocated_bytes, live_handles, largest_free_bytes",
            errors=(),
            invariants=("allocated_bytes never exceeds pool_bytes",),
            decision_ids=("D-PSM-14",)),
        "protection_domain": OpSpec(
            name="protection_domain",
            summary="Create a guest protection domain",
            inputs=("name",),
            outputs="none",
            errors=(Status.UNSUPPORTED,),
            unsupported=True,
            invariants=("returns UNSUPPORTED: the console's protection model is UNKNOWN",),
            claim_ids=("C-MEM-05",)),
    }

    def __init__(self, pool_bytes: int = 16 * 1024 * 1024 * 1024,
                 base_address: int = 0x1_0000_0000) -> None:
        super().__init__()
        self._pool = int(pool_bytes)
        self._base = int(base_address)
        self._live: Dict[int, Dict[str, Any]] = {}
        self._next_handle = 1
        self._next_offset = 0

    # ------------------------------------------------------------- helpers
    def _handle(self, handle: int) -> Dict[str, Any]:
        entry = self._live.get(int(handle))
        if entry is None:
            raise PsmError(Status.OWNERSHIP,
                           f"handle {handle} is not a live allocation of this platform")
        return entry

    # ---------------------------------------------------------- operations
    def op_topology(self) -> Any:
        return ok({
            "pool_bytes": self._pool,
            "engines": list(ENGINES),
            "coherent": True,
            "unknowns": ("allocation policy the console's system software uses",
                         "protection domains", "cache-coherence guarantees per engine"),
        })

    def op_allocate(self, size: int, alignment: int, purpose: str, engine: str) -> Any:
        size, alignment = int(size), int(alignment)
        if size <= 0:
            raise PsmError(Status.INVALID_ARGUMENT, "size must be positive")
        # A request larger than the pool is not malformed, it is unsatisfiable:
        # the free-space check below reports EXHAUSTED and leaves the pool intact.
        if alignment <= 0 or alignment & (alignment - 1) or alignment > ALIGNMENT_MAX:
            raise PsmError(Status.INVALID_ARGUMENT,
                           f"alignment must be a power of two up to {ALIGNMENT_MAX}")
        if purpose not in PURPOSES:
            raise PsmError(Status.INVALID_ARGUMENT, f"purpose must be one of {PURPOSES}")
        if engine not in ENGINES:
            raise PsmError(Status.INVALID_ARGUMENT, f"engine must be one of {ENGINES}")
        if engine == "gpu" and purpose == "guest":
            # Not an error on the console - a PC5 policy, stated so it is visible.
            raise PsmError(Status.DENIED,
                           "guest ownership is allocated on the CPU engine and then shared; "
                           "allocating it directly on the GPU engine is a PC5 policy decision")
        offset = (self._next_offset + alignment - 1) & ~(alignment - 1)
        if offset + size > self._pool:
            raise PsmError(Status.EXHAUSTED,
                           f"{size} bytes do not fit: {self._pool - self._next_offset} free")
        handle = self._next_handle
        self._next_handle += 1
        self._next_offset = offset + size
        self._live[handle] = {
            "handle": handle, "offset": offset, "size": size, "purpose": purpose,
            "engine": engine, "engines": {engine}, "alignment": alignment,
        }
        return ok({"handle": handle, "device_address": self._base + offset, "size": size})

    def op_release(self, handle: int) -> Any:
        entry = self._handle(handle)
        del self._live[entry["handle"]]
        return ok({"freed_bytes": entry["size"]})

    def op_translate(self, handle: int, offset: int) -> Any:
        entry = self._handle(handle)
        offset = int(offset)
        if offset < 0 or offset >= entry["size"]:
            raise PsmError(Status.INVALID_ARGUMENT,
                           f"offset must be 0..{entry['size'] - 1}: a translation never leaves "
                           f"its allocation")
        return ok({"device_address": self._base + entry["offset"] + offset})

    def op_share(self, handle: int, engine: str) -> Any:
        entry = self._handle(handle)
        if engine not in ENGINES:
            raise PsmError(Status.INVALID_ARGUMENT, f"engine must be one of {ENGINES}")
        entry["engines"].add(engine)
        return ok({"engines": sorted(entry["engines"])})

    def op_stats(self) -> Any:
        allocated = sum(entry["size"] for entry in self._live.values())
        largest = self._pool - self._next_offset
        return ok({
            "pool_bytes": self._pool,
            "allocated_bytes": allocated,
            "live_handles": len(self._live),
            "largest_free_bytes": max(largest, 0),
        })
