"""PSM-CPU v0 — CPU and privileged execution test double (contract subsystem ``cpu``).

What it models is PC5's *side* of the CPU contract: the feature surface a guest
may ask about, the privilege levels PC5 will present, MSR visibility policy, and
exception semantics. What the console actually implements beyond the public
description is ``UNKNOWN`` and this double says so instead of inventing it.

Nothing here is a Sony interface. Register numbers are AMD's public
architectural numbers; leaves the contract does not cover return ``UNSUPPORTED``.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .psm import Interface, InterfaceInfo, OpSpec, PsmError, Status, ok, require_value

#: CPUID leaves the contract covers. Anything else is console-specific and
#: therefore UNKNOWN: the double refuses rather than returning plausible zeros.
DOCUMENTED_LEAVES = (0x0, 0x1, 0x80000000, 0x80000001, 0x80000002, 0x80000003,
                     0x80000004, 0x80000005, 0x80000006, 0x80000007, 0x80000008)

#: MSRs PC5 is willing to expose to a guest, with the semantics it exposes.
EXPOSED_MSRS = {
    0x10: "TSC: monotonic, virtualised, never the host's value",
    0xC0000080: "EFER: long mode, NX, SVM enable bits are PC5-owned",
    0xC0000100: "FS_BASE: per-context, saved and restored on switch",
    0xC0000101: "GS_BASE: per-context, saved and restored on switch",
}

#: MSRs that exist on the console but whose values are security state. PC5 will
#: not model them, and says DENIED rather than UNSUPPORTED so the refusal is
#: distinguishable from "not covered by the contract" (ADR-007, ADR-022).
RESTRICTED_MSRS = {
    0xC0010015: "hardware configuration: console-specific, not modelled",
    0xC0010114: "VM control: security state, not modelled",
}

#: x86 architectural exception vectors 0..31, by the names AMD64 uses.
EXCEPTION_NAMES = {
    0: "divide-error", 1: "debug", 2: "nmi", 3: "breakpoint", 4: "overflow",
    5: "bound-range", 6: "invalid-opcode", 7: "device-not-available",
    8: "double-fault", 10: "invalid-tss", 11: "segment-not-present",
    12: "stack-segment-fault", 13: "general-protection", 14: "page-fault",
    16: "x87-floating-point", 17: "alignment-check", 18: "machine-check",
    19: "simd-floating-point", 20: "virtualisation", 30: "security",
    31: "reserved",
}


class PsmCpu(Interface):
    info = InterfaceInfo(
        name="PSM-CPU", version="0.1.0", subsystem="cpu", milestone="M11",
        note="PC5-authored test double for the CPU contract; not a Sony interface.")

    OPS = {
        "capabilities": OpSpec(
            name="capabilities",
            summary="Report the CPU surface PC5 presents to a guest",
            outputs="isa, cores, threads, modes, privilege_levels, clock_policy, "
                    "documented_leaves, unknowns",
            errors=(),
            invariants=("cores and threads are positive",
                        "every entry in unknowns is also UNKNOWN in the contract"),
            claim_ids=("C-CPU-01", "C-CPU-02")),
        "cpuid": OpSpec(
            name="cpuid",
            summary="Return the four architectural registers for a CPUID leaf",
            inputs=("leaf", "subleaf"),
            outputs="eax, ebx, ecx, edx",
            errors=(Status.INVALID_ARGUMENT, Status.UNSUPPORTED),
            invariants=("a returned leaf carries all four registers",
                        "an uncovered leaf returns UNSUPPORTED and no values"),
            claim_ids=("C-CPU-03",)),
        "msr_read": OpSpec(
            name="msr_read",
            summary="Read one MSR from the exposed set",
            inputs=("address",),
            outputs="value",
            errors=(Status.INVALID_ARGUMENT, Status.UNSUPPORTED, Status.DENIED),
            invariants=("security-state MSRs are DENIED, never zero",
                        "unknown MSRs are UNSUPPORTED"),
            claim_ids=("C-CPU-04",)),
        "exception": OpSpec(
            name="exception",
            summary="Describe how one architectural exception is delivered",
            inputs=("vector",),
            outputs="name, error_code, pc5_action",
            errors=(Status.INVALID_ARGUMENT, Status.UNSUPPORTED),
            invariants=("vectors outside 0..31 are INVALID_ARGUMENT",
                        "vectors with no public description are UNSUPPORTED"),
            claim_ids=("C-CPU-05",)),
        "virtualisation": OpSpec(
            name="virtualisation",
            summary="Report whether AMD SVM is presented, and to whom",
            outputs="svm_presented, nested, guest_usage",
            errors=(),
            invariants=("guest_usage is 'unknown' - the contract does not know it",
                        "svm_presented is a PC5 choice, not a console fact"),
            claim_ids=("C-CPU-06",)),
        "reset": OpSpec(
            name="reset",
            summary="Return the double to its power-on state",
            outputs="reset_epoch",
            errors=(),
            invariants=("mutable state is cleared", "the epoch increments"),
            decision_ids=("D-PSM-13",)),
        "execute_payload": OpSpec(
            name="execute_payload",
            summary="Run a PC5-authored conformance payload (M11 deliverable)",
            inputs=("payload",),
            outputs="none",
            errors=(Status.UNSUPPORTED,),
            unsupported=True,
            invariants=("returns UNSUPPORTED until M11 builds the execution model",),
            claim_ids=("C-CPU-07",)),
    }

    def __init__(self, cores: int = 8, threads_per_core: int = 2) -> None:
        super().__init__()
        self._cores = cores
        self._threads_per_core = threads_per_core
        self._epoch = 0
        self._cpuid_calls = 0

    # ------------------------------------------------------------- operations
    def op_capabilities(self) -> Any:
        return ok({
            "isa": "x86-64",
            "cores": self._cores,
            "threads": self._cores * self._threads_per_core,
            "modes": ("real", "protected-32", "long-64"),
            "privilege_levels": ("ring0-kernel", "ring3-user"),
            "clock_policy": "variable; the model never assumes a fixed frequency",
            "documented_leaves": [hex(leaf) for leaf in DOCUMENTED_LEAVES],
            "unknowns": ("console-specific CPUID leaves", "MSR behaviour outside the exposed set",
                         "whether the guest enables SVM"),
        })

    def op_cpuid(self, leaf: int, subleaf: int) -> Any:
        leaf = int(leaf)
        subleaf = int(subleaf)
        if leaf < 0 or leaf > 0xFFFFFFFF or subleaf < 0 or subleaf > 0xFFFFFFFF:
            raise PsmError(Status.INVALID_ARGUMENT, "leaf and subleaf are 32-bit")
        if leaf not in DOCUMENTED_LEAVES:
            raise PsmError(Status.UNSUPPORTED,
                           f"CPUID leaf {leaf:#x} is not in the contract; the console's "
                           f"leaf space is UNKNOWN to PC5")
        self._cpuid_calls += 1
        # Values are PC5's presentation, derived from the public family/model and
        # the documented leaves - not a copy of any machine's registers.
        table = {
            0x0: (0x0000000D, 0x68747541, 0x444D4163, 0x69746E65),  # max leaf, "AuthenticAMD"
            0x1: (0x00A00F11, 0x02100800, 0x7ED8320B, 0x178BFBFF),
            0x80000000: (0x80000008, 0x68747541, 0x444D4163, 0x69746E65),
            0x80000001: (0x00A00F11, 0x00000000, 0x75C237FF, 0x2FD3FBFF),
        }
        eax, ebx, ecx, edx = table.get(leaf, (0, 0, 0, 0))
        return ok({"eax": eax, "ebx": ebx, "ecx": ecx, "edx": edx})

    def op_msr_read(self, address: int) -> Any:
        address = int(address)
        if address in RESTRICTED_MSRS:
            raise PsmError(Status.DENIED,
                           f"MSR {address:#x} is security state ({RESTRICTED_MSRS[address]}); "
                           f"PC5 does not model it (ADR-007)")
        if address not in EXPOSED_MSRS:
            raise PsmError(Status.UNSUPPORTED,
                           f"MSR {address:#x} is not in the exposed set; its behaviour on the "
                           f"console is UNKNOWN")
        return ok({"address": address, "value": self._epoch, "semantics": EXPOSED_MSRS[address]})

    def op_exception(self, vector: int) -> Any:
        vector = int(vector)
        if not 0 <= vector <= 31:
            raise PsmError(Status.INVALID_ARGUMENT, "architectural vectors are 0..31")
        name = EXCEPTION_NAMES.get(vector)
        if name is None:
            raise PsmError(Status.UNSUPPORTED,
                           f"vector {vector} has no public description in the contract")
        error_code = vector in (8, 10, 11, 12, 13, 14, 17, 21)
        return ok({"name": name, "error_code": error_code,
                   "pc5_action": "deliver to the guest's handler, or halt with a diagnostic "
                                 "if the guest has none"})

    def op_virtualisation(self) -> Any:
        return ok({"svm_presented": True, "nested": False, "guest_usage": "unknown"})

    def op_reset(self) -> Any:
        self._epoch += 1
        self._cpuid_calls = 0
        return ok({"reset_epoch": self._epoch})
