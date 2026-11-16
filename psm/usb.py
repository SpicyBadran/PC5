"""PSM-USB v0 — USB and controller test double (contract subsystem ``usb``).

USB and HID are standard, and the PC side of them is ordinary hardware, so this
double models real semantics: device inventory, per-device ownership, HID report
length validation, interrupt-IN timeouts on the virtual clock, and hot-plug
removal invalidating handles. What the console does with a DualSense beyond the
standard HID reports is not public, and is not modelled.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .psm import Clock, Interface, InterfaceInfo, OpSpec, PsmError, Status, ok

DEVICE_CLASSES = ("hid", "audio", "storage", "hub")
REPORT_MAX = 64


class PsmUsb(Interface):
    info = InterfaceInfo(
        name="PSM-USB", version="0.1.0", subsystem="usb", milestone="M15",
        note="PC5-authored test double for standard xHCI/HID semantics; not a Sony device model.")

    OPS = {
        "attach": OpSpec(
            name="attach",
            summary="Attach a device at an address with a class and VID:PID",
            inputs=("address", "device_class", "vid_pid"),
            outputs="address, device_class",
            errors=(Status.INVALID_ARGUMENT, Status.BAD_STATE),
            invariants=("addresses are 1..127", "an occupied address is BAD_STATE"),
            claim_ids=("C-USB-01",)),
        "detach": OpSpec(
            name="detach",
            summary="Remove a device (hot-plug removal)",
            inputs=("address",),
            outputs="address",
            errors=(Status.NO_DEVICE,),
            invariants=("a detached address is free for reuse",),
            claim_ids=("C-USB-02",)),
        "enumerate": OpSpec(
            name="enumerate",
            summary="List the attached devices as a guest would see them",
            outputs="devices",
            errors=(),
            invariants=("every attached device appears exactly once",),
            claim_ids=("C-USB-01",)),
        "open": OpSpec(
            name="open",
            summary="Claim a device for an owner",
            inputs=("address", "owner"),
            outputs="handle",
            errors=(Status.NO_DEVICE, Status.OWNERSHIP),
            invariants=("a second owner cannot claim a claimed device",),
            claim_ids=("C-USB-03",)),
        "close": OpSpec(
            name="close",
            summary="Release a claimed device",
            inputs=("handle", "owner"),
            outputs="address",
            errors=(Status.OWNERSHIP,),
            invariants=("closing another owner's handle is OWNERSHIP",),
            claim_ids=("C-USB-03",)),
        "hid_report": OpSpec(
            name="hid_report",
            summary="Send a validated HID output report",
            inputs=("handle", "report"),
            outputs="bytes",
            errors=(Status.OWNERSHIP, Status.INVALID_ARGUMENT, Status.BAD_STATE),
            invariants=("an empty or oversized report is INVALID_ARGUMENT",
                        "a device removed while open is BAD_STATE"),
            claim_ids=("C-USB-02",)),
        "interrupt_in": OpSpec(
            name="interrupt_in",
            summary="Wait for an input report on the virtual clock",
            inputs=("handle", "timeout"),
            outputs="report, waited",
            errors=(Status.OWNERSHIP, Status.TIMEOUT, Status.INVALID_ARGUMENT),
            invariants=("no report within the timeout is TIMEOUT",),
            claim_ids=("C-USB-02",)),
        "inject_input": OpSpec(
            name="inject_input",
            summary="Deliver an input report to a claimed device (test affordance)",
            inputs=("handle", "report"),
            outputs="queued",
            errors=(Status.OWNERSHIP, Status.INVALID_ARGUMENT, Status.OVERFLOW),
            invariants=("the queue is bounded and overflow is explicit",),
            claim_ids=("C-USB-02",)),
        "dualsense_features": OpSpec(
            name="dualsense_features",
            summary="Use controller features beyond standard HID reports",
            inputs=("handle",),
            outputs="none",
            errors=(Status.UNSUPPORTED,),
            unsupported=True,
            invariants=("returns UNSUPPORTED: the console-specific behaviour is UNKNOWN",),
            claim_ids=("C-USB-03",)),
    }

    def __init__(self, clock: Clock | None = None, queue_depth: int = 8) -> None:
        super().__init__()
        self.clock = clock or Clock()
        self._depth = int(queue_depth)
        self._devices: Dict[int, Dict[str, Any]] = {}
        self._handles: Dict[int, Dict[str, Any]] = {}
        self._next_handle = 1

    def _handle(self, handle: int, owner: str) -> Dict[str, Any]:
        entry = self._handles.get(int(handle))
        if entry is None:
            raise PsmError(Status.OWNERSHIP, f"handle {handle} is not open")
        if entry["owner"] != owner:
            raise PsmError(Status.OWNERSHIP, f"handle {handle} belongs to {entry['owner']!r}")
        if entry["address"] not in self._devices:
            raise PsmError(Status.BAD_STATE,
                           f"the device behind handle {handle} was removed")
        return entry

    def op_attach(self, address: int, device_class: str, vid_pid: str) -> Any:
        address = int(address)
        if not 1 <= address <= 127:
            raise PsmError(Status.INVALID_ARGUMENT, "USB addresses are 1..127")
        if device_class not in DEVICE_CLASSES:
            raise PsmError(Status.INVALID_ARGUMENT, f"device_class must be one of {DEVICE_CLASSES}")
        if not isinstance(vid_pid, str) or len(vid_pid) < 5:
            raise PsmError(Status.INVALID_ARGUMENT, "vid_pid is a string like '054c:0ce6'")
        if address in self._devices:
            raise PsmError(Status.BAD_STATE, f"address {address} is occupied")
        self._devices[address] = {"address": address, "class": device_class,
                                  "vid_pid": vid_pid, "queue": [], "owner": None}
        return ok({"address": address, "device_class": device_class})

    def op_detach(self, address: int) -> Any:
        address = int(address)
        if address not in self._devices:
            raise PsmError(Status.NO_DEVICE, f"address {address} has no device")
        del self._devices[address]
        return ok({"address": address})

    def op_enumerate(self) -> Any:
        return ok({"devices": [
            {"address": entry["address"], "class": entry["class"], "vid_pid": entry["vid_pid"]}
            for entry in sorted(self._devices.values(), key=lambda item: item["address"])]})

    def op_open(self, address: int, owner: str) -> Any:
        address = int(address)
        entry = self._devices.get(address)
        if entry is None:
            raise PsmError(Status.NO_DEVICE, f"address {address} has no device")
        if entry["owner"] is not None:
            raise PsmError(Status.OWNERSHIP, f"address {address} is claimed by {entry['owner']!r}")
        handle = self._next_handle
        self._next_handle += 1
        entry["owner"] = owner
        self._handles[handle] = {"handle": handle, "address": address, "owner": owner}
        return ok({"handle": handle})

    def op_close(self, handle: int, owner: str) -> Any:
        entry = self._handle(handle, owner)
        self._devices[entry["address"]]["owner"] = None
        del self._handles[entry["handle"]]
        return ok({"address": entry["address"]})

    def op_hid_report(self, handle: int, report: bytes) -> Any:
        self._handle(handle, self._handles.get(int(handle), {}).get("owner", ""))
        if not isinstance(report, (bytes, bytearray)) or not report:
            raise PsmError(Status.INVALID_ARGUMENT, "a report is 1..64 bytes")
        if len(report) > REPORT_MAX:
            raise PsmError(Status.INVALID_ARGUMENT, f"reports are at most {REPORT_MAX} bytes")
        return ok({"bytes": len(report)})

    def op_interrupt_in(self, handle: int, timeout: int) -> Any:
        entry = self._handle(handle, self._handles.get(int(handle), {}).get("owner", ""))
        timeout = int(timeout)
        if timeout < 0:
            raise PsmError(Status.INVALID_ARGUMENT, "timeout cannot be negative")
        queue: List[bytes] = self._devices[entry["address"]]["queue"]
        if queue:
            return ok({"report": queue.pop(0), "waited": 0})
        self.clock.advance(timeout)
        raise PsmError(Status.TIMEOUT, f"no input report within {timeout} ns")

    def op_inject_input(self, handle: int, report: bytes) -> Any:
        entry = self._handle(handle, self._handles.get(int(handle), {}).get("owner", ""))
        if not isinstance(report, (bytes, bytearray)) or not report or len(report) > REPORT_MAX:
            raise PsmError(Status.INVALID_ARGUMENT, f"a report is 1..{REPORT_MAX} bytes")
        queue: List[bytes] = self._devices[entry["address"]]["queue"]
        if len(queue) >= self._depth:
            raise PsmError(Status.OVERFLOW,
                           f"the input queue ({self._depth}) is full; dropping a report would "
                           f"lose a button press silently")
        queue.append(bytes(report))
        return ok({"queued": len(queue)})
