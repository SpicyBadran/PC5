"""PSM-STOR v0 — storage and I/O test double (contract subsystem ``storage``).

Standard NVMe behaviour is public and modelled here: queues, submission/completion
identifiers, flush, and explicit error completion. The console-specific parts -
the decompression block in the I/O path, and the layout the system software
expects on its own device - are ``UNKNOWN``, and the double answers
``UNSUPPORTED`` for them rather than wrapping a PC NVMe device and calling it a
PlayStation.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .psm import Interface, InterfaceInfo, OpSpec, PsmError, Status, ok

DEVICE_CLASSES = ("nvme", "ata", "usb")


class PsmStorage(Interface):
    info = InterfaceInfo(
        name="PSM-STOR", version="0.1.0", subsystem="storage", milestone="M13",
        note="PC5-authored test double for standard NVMe semantics; not a Sony device model.")

    OPS = {
        "present": OpSpec(
            name="present",
            summary="Present a block device with a capacity and queue depth",
            inputs=("device", "device_class", "capacity_bytes", "queue_depth"),
            outputs="device, queue_id, namespaces",
            errors=(Status.INVALID_ARGUMENT, Status.BAD_STATE),
            invariants=("presenting the same device twice is BAD_STATE",
                        "capacity and depth are positive"),
            claim_ids=("C-STO-01",)),
        "submit": OpSpec(
            name="submit",
            summary="Submit one read or write and get a completion tag",
            inputs=("device", "queue_id", "op", "lba", "sectors", "owner"),
            outputs="tag, completion",
            errors=(Status.NO_DEVICE, Status.INVALID_ARGUMENT, Status.BAD_STATE,
                    Status.OWNERSHIP),
            invariants=("submit before present is NO_DEVICE",
                        "an LBA past the namespace is INVALID_ARGUMENT",
                        "a completed tag is not reused"),
            claim_ids=("C-STO-02", "C-STO-03")),
        "complete": OpSpec(
            name="complete",
            summary="Fetch and retire a completion by tag",
            inputs=("device", "tag", "owner"),
            outputs="status, sectors, latency_ns",
            errors=(Status.NO_DEVICE, Status.BAD_STATE, Status.OWNERSHIP),
            invariants=("completing a tag twice is BAD_STATE",
                        "completing another owner's tag is OWNERSHIP"),
            claim_ids=("C-STO-03",)),
        "flush": OpSpec(
            name="flush",
            summary="Flush writes acknowledged before this call",
            inputs=("device", "queue_id"),
            outputs="flushed_sectors",
            errors=(Status.NO_DEVICE, Status.INVALID_ARGUMENT),
            invariants=("flush reports what it made durable, not what it hopes",),
            claim_ids=("C-STO-04",)),
        "health": OpSpec(
            name="health",
            summary="Report device health as the guest would see it",
            inputs=("device",),
            outputs="device, wear, errors, present",
            errors=(Status.NO_DEVICE,),
            invariants=("an absent device is NO_DEVICE",),
            claim_ids=("C-STO-05",)),
        "decompress": OpSpec(
            name="decompress",
            summary="Use the console's I/O decompression path",
            inputs=("device", "compressed", "algorithm"),
            outputs="none",
            errors=(Status.UNSUPPORTED,),
            unsupported=True,
            invariants=("returns UNSUPPORTED: the block's programming interface is UNKNOWN",),
            claim_ids=("C-STO-06",)),
        "guest_layout": OpSpec(
            name="guest_layout",
            summary="Present the on-disk layout the console's system software expects",
            outputs="none",
            errors=(Status.UNSUPPORTED,),
            unsupported=True,
            invariants=("returns UNSUPPORTED: the layout is UNKNOWN and is M21 work",),
            claim_ids=("C-STO-05",)),
    }

    def __init__(self) -> None:
        super().__init__()
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._tags: Dict[int, Dict[str, Any]] = {}
        self._next_tag = 1
        self._durable_lba: Dict[str, int] = {}

    def _device(self, device: str) -> Dict[str, Any]:
        entry = self._devices.get(device)
        if entry is None:
            raise PsmError(Status.NO_DEVICE, f"no device {device!r} is present")
        return entry

    def op_present(self, device: str, device_class: str, capacity_bytes: int,
                   queue_depth: int) -> Any:
        if not isinstance(device, str) or not device:
            raise PsmError(Status.INVALID_ARGUMENT, "a device needs a name")
        if device_class not in DEVICE_CLASSES:
            raise PsmError(Status.INVALID_ARGUMENT, f"device_class must be one of {DEVICE_CLASSES}")
        capacity_bytes, queue_depth = int(capacity_bytes), int(queue_depth)
        if capacity_bytes <= 0 or capacity_bytes % 512:
            raise PsmError(Status.INVALID_ARGUMENT, "capacity must be a positive multiple of 512")
        if not 1 <= queue_depth <= 1024:
            raise PsmError(Status.INVALID_ARGUMENT, "queue depth is 1..1024")
        if device in self._devices:
            raise PsmError(Status.BAD_STATE, f"device {device!r} is already present")
        self._devices[device] = {
            "device": device, "class": device_class, "capacity": capacity_bytes,
            "depth": queue_depth, "in_flight": 0, "completions": 0,
            "namespaces": [{"id": 1, "sectors": capacity_bytes // 512}],
        }
        self._durable_lba[device] = 0
        return ok({"device": device, "queue_id": 0, "namespaces": [1]})

    def op_submit(self, device: str, queue_id: int, op: str, lba: int, sectors: int,
                  owner: str) -> Any:
        entry = self._device(device)
        queue_id, lba, sectors = int(queue_id), int(lba), int(sectors)
        if op not in ("read", "write"):
            raise PsmError(Status.INVALID_ARGUMENT, "op must be 'read' or 'write'")
        if queue_id != 0:
            raise PsmError(Status.INVALID_ARGUMENT,
                           f"queue {queue_id} is not one of this device's queues (0)")
        if sectors <= 0:
            raise PsmError(Status.INVALID_ARGUMENT, "sectors must be positive")
        total = entry["capacity"] // 512
        if lba < 0 or lba + sectors > total:
            raise PsmError(Status.INVALID_ARGUMENT,
                           f"LBA range {lba}..{lba + sectors} is outside the namespace (0..{total})")
        if not isinstance(owner, str) or not owner:
            raise PsmError(Status.INVALID_ARGUMENT, "an owner token is required for the tag")
        if entry["in_flight"] >= entry["depth"]:
            raise PsmError(Status.BAD_STATE,
                           f"queue depth {entry['depth']} is reached: submission must be "
                           f"back-pressured, not queued without limit")
        tag = self._next_tag
        self._next_tag += 1
        entry["in_flight"] += 1
        self._tags[tag] = {"device": device, "tag": tag, "op": op, "lba": lba,
                           "sectors": sectors, "owner": owner, "retired": False}
        return ok({"tag": tag, "completion": "queued"})

    def op_complete(self, device: str, tag: int, owner: str) -> Any:
        entry = self._device(device)
        record = self._tags.get(int(tag))
        if record is None or record["device"] != device:
            raise PsmError(Status.NO_DEVICE, f"tag {tag} is not outstanding on {device!r}")
        if record["retired"]:
            raise PsmError(Status.BAD_STATE, f"tag {tag} was already retired")
        if record["owner"] != owner:
            raise PsmError(Status.OWNERSHIP,
                           f"tag {tag} belongs to {record['owner']!r}, not {owner!r}")
        record["retired"] = True
        entry["in_flight"] -= 1
        entry["completions"] += 1
        if record["op"] == "write":
            self._durable_lba[device] = max(self._durable_lba[device], record["lba"] + record["sectors"])
        return ok({"status": "success", "sectors": record["sectors"], "latency_ns": 0})

    def op_flush(self, device: str, queue_id: int) -> Any:
        self._device(device)
        if int(queue_id) != 0:
            raise PsmError(Status.INVALID_ARGUMENT, "queue must be 0")
        return ok({"flushed_sectors": self._durable_lba[device]})

    def op_health(self, device: str) -> Any:
        entry = self._device(device)
        return ok({"device": device, "wear": "unknown", "errors": 0,
                   "present": True, "completions": entry["completions"]})
