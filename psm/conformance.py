"""Conformance suite for the PSM interfaces.

Each suite drives one interface through its documented operations and asserts the
contract's observable promises: valid operations succeed, invalid parameters are
refused rather than coerced, an operation the contract marks UNKNOWN is
``UNSUPPORTED`` with no value, ownership and state violations are named, resource
exhaustion leaves nothing behind, and timeouts are timeouts.

What a passing run proves: PC5's *own* interface behaves as PC5 specified it.
What it does not prove: that Sony's system software accepts, uses or even
resembles this interface. That requires genuine execution evidence, which does not
exist (see the contract, §7).

Run it directly with ``python3 -m psm.conformance``; ``make check`` runs it as
part of the test suite, and ``tests/test_psm_mutations.py`` runs it against
deliberately broken doubles to prove it can fail.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import audio, cpu, graphics, interrupts, memory, services, storage, usb
from .psm import Clock, Interface, Result, Status


@dataclass
class Check:
    interface: str
    name: str
    ok: bool
    detail: str = ""
    claim_ids: Sequence[str] = ()

    def __str__(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        extra = f"  ({self.detail})" if self.detail and not self.ok else ""
        return f"[{mark}] {self.interface}: {self.name}{extra}"


class Suite:
    """Collects checks for one interface."""

    def __init__(self, interface: Interface) -> None:
        self.interface = interface
        self.checks: List[Check] = []

    @property
    def name(self) -> str:
        return self.interface.info.name

    # ------------------------------------------------------------------ helpers
    def _record(self, name: str, ok: bool, detail: str = "",
                claim_ids: Sequence[str] = ()) -> bool:
        self.checks.append(Check(self.name, name, bool(ok), detail, tuple(claim_ids)))
        return bool(ok)

    def result(self, label: str, operation: str, expect: Status,
               claim_ids: Sequence[str] = (), **kwargs: Any) -> Result:
        """Call an operation and assert its status.

        The label is the check's name; operation arguments arrive as keywords, so a
        double whose operation takes an argument called ``name`` still works.
        """
        outcome = self.interface.call(operation, **kwargs)
        self._record(f"{label} → {expect.value}", outcome.status is expect,
                     f"got {outcome.status.value}: {outcome.detail}", claim_ids)
        if expect is not Status.OK:
            self._record(f"{label} carries no value on {expect.value}", outcome.value is None,
                         f"value={outcome.value!r}", claim_ids)
        return outcome

    def property(self, name: str, outcome: Result, predicate: Callable[[Any], bool],
                 detail: str = "", claim_ids: Sequence[str] = ()) -> bool:
        satisfied = bool(outcome.ok) and bool(predicate(outcome.value))
        return self._record(name, satisfied, detail or f"value={outcome.value!r}", claim_ids)


# --------------------------------------------------------------------- suites
def suite_cpu(iface: cpu.PsmCpu) -> Suite:
    suite = Suite(iface)
    caps = suite.result("capabilities", "capabilities", Status.OK, ("C-CPU-01",))
    suite.property("capabilities reports the published core count", caps,
                   lambda v: v["cores"] == 8 and v["threads"] == 16, claim_ids=("C-CPU-01",))
    suite.property("capabilities names what is unknown", caps,
                   lambda v: "console-specific CPUID leaves" in v["unknowns"],
                   claim_ids=("C-CPU-04",))

    leaf = suite.result("documented CPUID leaf", "cpuid", Status.OK, ("C-CPU-03",),
                        leaf=0x1, subleaf=0)
    suite.property("a CPUD leaf carries all four registers", leaf,
                   lambda v: set(v) == {"eax", "ebx", "ecx", "edx"})
    suite.result("undocumented CPUID leaf", "cpuid", Status.UNSUPPORTED, ("C-CPU-04",),
                 leaf=0x40000001, subleaf=0)
    suite.result("negative CPUID leaf", "cpuid", Status.INVALID_ARGUMENT, leaf=-1, subleaf=0)

    msr = suite.result("architectural MSR", "msr_read", Status.OK, ("C-CPU-05",), address=0xC0000080)
    suite.property("an exposed MSR names its semantics", msr,
                   lambda v: bool(v["semantics"]), claim_ids=("C-CPU-05",))
    suite.result("security-state MSR", "msr_read", Status.DENIED, ("C-CPU-05",), address=0xC0010114)
    suite.result("unknown MSR", "msr_read", Status.UNSUPPORTED, ("C-CPU-05",), address=0x1234)

    page_fault = suite.result("page-fault vector", "exception", Status.OK, ("C-CPU-03",), vector=14)
    suite.property("page fault carries an error code", page_fault, lambda v: v["error_code"] is True)
    suite.result("vector out of range", "exception", Status.INVALID_ARGUMENT, vector=32)
    suite.result("vector with no public description", "exception", Status.UNSUPPORTED, vector=21)

    virt = suite.result("virtualisation", "virtualisation", Status.OK, ("C-CPU-06",))
    suite.property("SVM use by the guest is reported unknown", virt,
                   lambda v: v["guest_usage"] == "unknown", claim_ids=("C-CPU-07",))

    suite.result("conformance payload", "execute_payload", Status.UNSUPPORTED, ("C-CPU-07",),
                 payload=b"pc5")
    suite.result("unknown operation", "cpuid_x", Status.UNSUPPORTED)
    suite.result("missing argument", "cpuid", Status.INVALID_ARGUMENT, leaf=0)
    first = iface.call("reset")
    second = iface.call("reset")
    suite._record("reset increments the epoch",
                  first.value["reset_epoch"] + 1 == second.value["reset_epoch"])
    return suite


def suite_memory(iface: memory.PsmMemory) -> Suite:
    suite = Suite(iface)
    topo = suite.result("topology", "topology", Status.OK, ("C-MEM-01", "C-MEM-02"))
    suite.property("topology reports one pool for every engine", topo,
                   lambda v: v["pool_bytes"] == 16 * 1024 ** 3 and len(v["engines"]) >= 2)

    small = suite.result("allocate aligned", "allocate", Status.OK, ("C-MEM-03",),
                         size=4096, alignment=4096, purpose="queue", engine="cpu")
    suite.property("the device address honours the alignment", small,
                   lambda v: v["device_address"] % 4096 == 0)

    suite.result("misaligned alignment request", "allocate", Status.INVALID_ARGUMENT,
                 size=4096, alignment=3000, purpose="queue", engine="cpu")
    suite.result("zero size", "allocate", Status.INVALID_ARGUMENT,
                 size=0, alignment=4096, purpose="queue", engine="cpu")

    before = iface.call("stats").value
    suite.result("allocation larger than the pool", "allocate", Status.EXHAUSTED, ("C-MEM-04",),
                 size=17 * 1024 ** 3, alignment=4096, purpose="guest", engine="cpu")
    after = iface.call("stats").value
    suite._record("a refused allocation leaves nothing behind",
                  before["allocated_bytes"] == after["allocated_bytes"],
                  f"{before} vs {after}", ("C-MEM-04",))

    second_block = suite.result("allocate a second block", "allocate", Status.OK,
                                ("C-MEM-03",), size=8192, alignment=8192, purpose="queue",
                                engine="cpu")
    suite._record("two live allocations do not overlap",
                  second_block.value["device_address"] + 8192
                  <= small.value["device_address"]
                  or second_block.value["device_address"]
                  >= small.value["device_address"] + 4096,
                  f"first={small.value['device_address']} "
                  f"second={second_block.value['device_address']}", ("C-MEM-03",))

    inside = suite.result("translate inside the allocation", "translate", Status.OK,
                          handle=small.value["handle"], offset=4095)
    suite.property("translation stays inside its allocation", inside,
                   lambda v: v["device_address"] == small.value["device_address"] + 4095)
    suite.result("translate past the end", "translate", Status.INVALID_ARGUMENT,
                 handle=small.value["handle"], offset=4096)
    suite.result("translate an unknown handle", "translate", Status.OWNERSHIP,
                 handle=9999, offset=0)

    shared = suite.result("share with the GPU engine", "share", Status.OK, ("C-MEM-06",),
                          handle=small.value["handle"], engine="gpu")
    again = suite.result("share twice", "share", Status.OK, handle=small.value["handle"], engine="gpu")
    suite.property("sharing is idempotent", again, lambda v: v["engines"] == shared.value["engines"])
    suite._record("sharing does not move the memory",
                  iface.call("translate", handle=small.value["handle"], offset=0).value["device_address"]
                  == small.value["device_address"], claim_ids=("C-MEM-02",))

    suite.result("release", "release", Status.OK, handle=small.value["handle"])
    suite.result("release twice", "release", Status.OWNERSHIP, ("C-MEM-05",),
                 handle=small.value["handle"])
    suite.result("protection domain", "protection_domain", Status.UNSUPPORTED, ("C-MEM-05",),
                 name="guest")
    suite.result("unknown engine when allocating", "allocate", Status.INVALID_ARGUMENT,
                 size=4096, alignment=4096, purpose="queue", engine="dsp")

    suite.result("release the second block", "release", Status.OK,
                 handle=second_block.value["handle"])
    stats = iface.call("stats").value
    suite._record("stats account for every live handle",
                  stats["allocated_bytes"] <= stats["pool_bytes"] and stats["live_handles"] == 0,
                  str(stats))
    return suite


def suite_irq_timer_ipc(irq: interrupts.PsmIrq, timer: interrupts.PsmTimer,
                        ipc: interrupts.PsmIpc) -> List[Suite]:
    irq_suite = Suite(irq)
    claim = irq_suite.result("claim a vector", "claim", Status.OK, ("C-IRQ-02",), vector=32)
    irq_suite.property("the claim names a slot", claim, lambda v: v["slot"] == 0)
    irq_suite.result("claim the same vector twice", "claim", Status.BAD_STATE, vector=32)
    irq_suite.result("raise an unclaimed vector", "raise", Status.BAD_STATE, vector=33)
    irq_suite.result("raise a claimed vector", "raise", Status.OK, vector=32)
    irq_suite.result("raise a second time", "raise", Status.OK, vector=32)
    first = irq_suite.result("take the first delivery", "take", Status.OK)
    irq_suite.property("deliveries are first-in-first-out", first, lambda v: v["vector"] == 32)
    irq_suite.result("take again", "take", Status.OK)
    irq_suite.result("take with nothing pending", "take", Status.NOT_READY, ("C-IRQ-02",))
    irq_suite.result("release an unclaimed vector", "release", Status.BAD_STATE, vector=40)
    irq_suite.result("claim vector 40", "claim", Status.OK, vector=40)
    irq_suite.result("raise vector 40", "raise", Status.OK, vector=40)
    dropped = irq_suite.result("release a vector with a delivery pending", "release", Status.OK,
                               vector=40, )
    irq_suite.property("a released vector's deliveries are counted", dropped,
                       lambda v: v["dropped"] == 1, claim_ids=("C-IRQ-03",))
    info = irq_suite.result("controller information", "controller_info", Status.OK, ("C-IRQ-01",))
    irq_suite.property("the controller is named as PC5's, not the console's", info,
                       lambda v: "PC5" in v["model"] and "not the console" in v["model"],
                       claim_ids=("C-IRQ-01",))

    timer_suite = Suite(timer)
    timer.bind_irq(irq)
    start = timer_suite.result("read the clock", "now", Status.OK, ("C-TMR-01",))
    timer_suite.result("schedule in the past", "schedule", Status.INVALID_ARGUMENT,
                       deadline=0, vector=32)
    timer_suite.result("schedule on an unclaimed vector", "schedule", Status.BAD_STATE,
                       deadline=10 ** 12, vector=99)
    timer_suite.result("schedule on a claimed vector", "schedule", Status.OK,
                       deadline=10 ** 9, vector=32)
    advance = timer_suite.result("advance past the deadline", "advance", Status.OK,
                                 nanoseconds=2 * 10 ** 9)
    timer_suite.property("the deadline fired and raised its vector", advance,
                         lambda v: v["fired"] == [32] and v["now"] == 2 * 10 ** 9,
                         claim_ids=("C-TMR-01",))
    timer_suite.result("advance backwards", "advance", Status.INVALID_ARGUMENT,
                       nanoseconds=-1)
    rtc = timer_suite.result("read the RTC", "rtc_read", Status.OK, ("C-TMR-02",))
    timer_suite.property("the RTC names its source", rtc,
                         lambda v: "not a read of the machine" in v["source"])
    timer_suite._record("the clock never went backwards",
                        timer.call("now").value["nanoseconds"] >= start.value["nanoseconds"])

    ipc_suite = Suite(ipc)
    ipc_suite.result("create a queue", "create", Status.OK, ("C-IPC-01",), name="svc", depth=2)
    ipc_suite.result("create the same queue again", "create", Status.BAD_STATE, name="svc", depth=2)
    ipc_suite.result("create with depth 0", "create", Status.INVALID_ARGUMENT, name="zero", depth=0)
    ipc_suite.result("send to an unknown queue", "send", Status.NO_DEVICE,
                     name="nope", message="x")
    ipc_suite.result("send the first message", "send", Status.OK, name="svc", message="a")
    ipc_suite.result("send the second message", "send", Status.OK, name="svc", message="b")
    ipc_suite.result("send past the queue depth", "send", Status.OVERFLOW, ("C-IPC-01",),
                     name="svc", message="c")
    got_a = ipc_suite.result("receive the first message", "recv", Status.OK,
                             name="svc", timeout=1000)
    ipc_suite.property("messages come out in order", got_a, lambda v: v["message"] == "a")
    ipc_suite.result("receive the second message", "recv", Status.OK, name="svc", timeout=1000)
    before = ipc.clock.now()
    ipc_suite.result("receive from an empty queue", "recv", Status.TIMEOUT,
                     name="svc", timeout=5000)
    ipc_suite._record("a timeout advances the clock by the timeout",
                      ipc.clock.now() - before == 5000,
                      f"clock moved {ipc.clock.now() - before} ns", ("C-IPC-01",))
    ipc_suite.result("send after draining", "send", Status.OK, name="svc", message="d")
    destroy = ipc_suite.result("destroy the queue", "destroy", Status.OK, name="svc")
    ipc_suite.property("a destroyed queue reports what it held", destroy,
                       lambda v: v["discarded"] == 1, claim_ids=("C-IPC-01",))
    ipc_suite.result("destroy it again", "destroy", Status.NO_DEVICE, name="svc")
    return [irq_suite, timer_suite, ipc_suite]


def suite_storage(iface: storage.PsmStorage) -> Suite:
    suite = Suite(iface)
    suite.result("submit before any device is present", "submit", Status.NO_DEVICE,
                 ("C-STO-02",), device="nvme0", queue_id=0, op="read", lba=0, sectors=1,
                 owner="pc5")
    present = suite.result("present a device", "present", Status.OK, ("C-STO-01",),
                           device="nvme0", device_class="nvme",
                           capacity_bytes=1024 * 1024 * 1024, queue_depth=2)
    suite.property("the device exposes one namespace", present, lambda v: v["namespaces"] == [1])
    suite.result("present the same device twice", "present", Status.BAD_STATE,
                 device="nvme0", device_class="nvme",
                 capacity_bytes=1024 * 1024 * 1024, queue_depth=2)
    suite.result("capacity that is not a multiple of 512", "present", Status.INVALID_ARGUMENT,
                 device="odd", device_class="nvme", capacity_bytes=1000, queue_depth=1)

    first = suite.result("submit a write", "submit", Status.OK, ("C-STO-02",),
                         device="nvme0", queue_id=0, op="write", lba=0, sectors=8, owner="pc5")
    tag = first.value["tag"]
    suite.result("LBA past the namespace", "submit", Status.INVALID_ARGUMENT,
                 device="nvme0", queue_id=0, op="read", lba=2 ** 21, sectors=1, owner="pc5")
    suite.result("unknown queue", "submit", Status.INVALID_ARGUMENT,
                 device="nvme0", queue_id=3, op="read", lba=0, sectors=1, owner="pc5")
    suite.result("complete with the wrong owner", "complete", Status.OWNERSHIP,
                 device="nvme0", tag=tag, owner="someone-else")
    done = suite.result("complete with the right owner", "complete", Status.OK,
                        ("C-STO-03",), device="nvme0", tag=tag, owner="pc5")
    suite.property("the completion reports the sectors written", done,
                   lambda v: v["status"] == "success" and v["sectors"] == 8)
    suite.result("complete the same tag twice", "complete", Status.BAD_STATE,
                 device="nvme0", tag=tag, owner="pc5")
    flush = suite.result("flush", "flush", Status.OK, ("C-STO-03",), device="nvme0", queue_id=0)
    suite.property("flush reports what was made durable", flush,
                   lambda v: v["flushed_sectors"] == 8, claim_ids=("C-STO-03",))

    suite.result("fill the queue", "submit", Status.OK,
                 device="nvme0", queue_id=0, op="read", lba=1, sectors=1, owner="pc5")
    suite.result("fill the queue again", "submit", Status.OK,
                 device="nvme0", queue_id=0, op="read", lba=2, sectors=1, owner="pc5")
    suite.result("submit past the queue depth", "submit", Status.BAD_STATE, ("C-STO-02",),
                 device="nvme0", queue_id=0, op="read", lba=3, sectors=1, owner="pc5")

    health = suite.result("health of a present device", "health", Status.OK, ("C-STO-01",),
                          device="nvme0")
    suite.property("health does not invent a wear figure", health, lambda v: v["wear"] == "unknown")
    suite.result("health of an absent device", "health", Status.NO_DEVICE, device="absent")
    suite.result("decompression block", "decompress", Status.UNSUPPORTED, ("C-STO-04",),
                 device="nvme0", compressed=b"x", algorithm="zlib")
    suite.result("guest on-disk layout", "guest_layout", Status.UNSUPPORTED, ("C-STO-05",))
    return suite


def suite_graphics(iface: graphics.PsmGraphics) -> Suite:
    suite = Suite(iface)
    info = suite.result("device information", "info", Status.OK, ("C-GFX-01",))
    suite.property("the engine description matches the public specification", info,
                   lambda v: "RDNA 2" in v["engine"] and v["ray_tracing"] is True)
    suite.property("the unknowns are named", info,
                   lambda v: "command encoding" in v["unknowns"], claim_ids=("C-GFX-03",))
    context = suite.result("create a context", "create_context", Status.OK, ("C-GFX-02",),
                           owner="pc5")
    ctx = context.value["context"]
    surface = context.value["surface"]
    suite.property("the context brings a scanout surface with it", context,
                   lambda v: v["surface"] == v["context"], claim_ids=("C-GFX-02",))
    fence = suite.result("create a fence", "create_fence", Status.OK, ("C-GFX-03",),
                         context=ctx, owner="pc5")
    fence_id = fence.value["fence"]
    suite.result("command submission", "submit", Status.UNSUPPORTED, ("C-GFX-03",),
                 context=ctx, commands=b"\x00", owner="pc5")
    suite.result("GPU firmware", "gpu_firmware", Status.UNSUPPORTED, ("C-GFX-04",),
                 message=b"\x00")
    suite.result("wait for an unsignalled fence", "wait_fence", Status.TIMEOUT,
                 fence=fence_id, timeout=1000)
    suite.result("signal the fence", "signal_fence", Status.OK, fence=fence_id, owner="pc5")
    suite.result("wait for the signalled fence", "wait_fence", Status.OK,
                 fence=fence_id, timeout=1000)
    suite.result("signal twice", "signal_fence", Status.BAD_STATE, fence=fence_id, owner="pc5")
    suite.result("signal another owner's fence", "signal_fence", Status.OWNERSHIP,
                 fence=fence_id, owner="intruder")
    suite.result("present an unknown surface", "present", Status.INVALID_ARGUMENT,
                 surface=42, owner="pc5")
    first = suite.result("present a PC5 surface", "present", Status.OK, ("C-GFX-02",),
                         surface=surface, owner="pc5")
    second = suite.result("present it again", "present", Status.OK, surface=surface, owner="pc5")
    suite.property("presentation numbers increase", second,
                   lambda v: v["sequence"] == first.value["sequence"] + 1)
    suite.result("destroy another owner's context", "destroy_context", Status.OWNERSHIP,
                 context=ctx, owner="intruder")
    released = suite.result("destroy the context", "destroy_context", Status.OK,
                            context=ctx, owner="pc5")
    suite.property("destruction releases the context's fences and surface", released,
                   lambda v: v["fences_released"] == 1 and v["surface_released"] is True)
    suite.result("present a destroyed context's surface", "present", Status.INVALID_ARGUMENT,
                 ("C-GFX-02",), surface=surface, owner="pc5")
    return suite


def suite_audio(iface: audio.PsmAudio) -> Suite:
    suite = Suite(iface)
    suite.result("rate out of range", "open_stream", Status.INVALID_ARGUMENT,
                 rate=4000, channels=2, format="pcm_s16le", latency_class="interactive",
                 owner="pc5")
    suite.result("unknown sample format", "open_stream", Status.INVALID_ARGUMENT,
                 rate=48000, channels=2, format="pcm_u8", latency_class="interactive",
                 owner="pc5")
    opened = suite.result("open a stream", "open_stream", Status.OK, ("C-AUD-02",),
                          rate=48000, channels=2, format="pcm_s16le",
                          latency_class="interactive", owner="pc5")
    stream = opened.value["stream"]
    suite.property("the buffer follows the latency class", opened,
                   lambda v: v["buffer_frames"] == 480 and v["latency_ns"] == 10 * 10 ** 6)
    suite.result("write more than the buffer holds", "write", Status.OVERFLOW, ("C-AUD-02",),
                 stream=stream, frames=481, owner="pc5")
    suite.result("write within the buffer", "write", Status.OK, stream=stream, frames=480,
                 owner="pc5")
    suite.result("write again when full", "write", Status.OVERFLOW, stream=stream, frames=1,
                 owner="pc5")
    drained = suite.result("drain", "drain", Status.OK, stream=stream)
    suite.property("draining consumes exactly what was queued", drained,
                   lambda v: v["drained_frames"] == 480)
    suite.result("write from another owner", "write", Status.OWNERSHIP, stream=stream,
                 frames=10, owner="intruder")
    suite.result("DSP program upload", "dsp_load", Status.UNSUPPORTED, ("C-AUD-03",),
                 program=b"\x00")
    suite.result("close with nothing queued", "close", Status.OK, stream=stream, owner="pc5")
    suite.result("write after close", "write", Status.OWNERSHIP, stream=stream, frames=1,
                 owner="pc5")
    for index in range(4):
        suite.result(f"open stream {index}", "open_stream", Status.OK, rate=44100,
                     channels=2, format="pcm_f32le", latency_class="balanced", owner="pc5")
    suite.result("open past the stream limit", "open_stream", Status.EXHAUSTED,
                 rate=44100, channels=2, format="pcm_f32le", latency_class="balanced",
                 owner="pc5")
    return suite


def suite_usb(iface: usb.PsmUsb) -> Suite:
    suite = Suite(iface)
    suite.result("attach at address 0", "attach", Status.INVALID_ARGUMENT,
                 address=0, device_class="hid", vid_pid="054c:0ce6")
    suite.result("attach a HID device", "attach", Status.OK, ("C-USB-01",),
                 address=3, device_class="hid", vid_pid="054c:0ce6")
    suite.result("attach at an occupied address", "attach", Status.BAD_STATE,
                 address=3, device_class="hid", vid_pid="054c:0ce6")
    listing = suite.result("enumerate", "enumerate", Status.OK, ("C-USB-01",))
    suite.property("enumerate lists the attached device once", listing,
                   lambda v: [d["address"] for d in v["devices"]] == [3])
    handle = suite.result("open the device", "open", Status.OK, ("C-USB-03",),
                          address=3, owner="pc5").value["handle"]
    suite.result("open it again", "open", Status.OWNERSHIP, address=3, owner="guest")
    suite.result("an empty HID report", "hid_report", Status.INVALID_ARGUMENT,
                 handle=handle, report=b"")
    suite.result("an oversized HID report", "hid_report", Status.INVALID_ARGUMENT,
                 handle=handle, report=b"\x00" * 65)
    suite.result("a valid HID report", "hid_report", Status.OK, handle=handle,
                 report=b"\x05\xff\x00")
    suite.result("wait for input with none queued", "interrupt_in", Status.TIMEOUT,
                 ("C-USB-02",), handle=handle, timeout=1000)
    suite.result("inject an input report", "inject_input", Status.OK, handle=handle,
                 report=b"\x01\x02")
    received = suite.result("read the injected report", "interrupt_in", Status.OK,
                            handle=handle, timeout=0)
    suite.property("the report is delivered unchanged", received,
                   lambda v: v["report"] == b"\x01\x02")
    suite.result("extended controller features", "dualsense_features", Status.UNSUPPORTED,
                 ("C-USB-03",), handle=handle)
    suite.result("close with the wrong owner", "close", Status.OWNERSHIP,
                 handle=handle, owner="guest")
    suite.result("detach the device while it is open", "detach", Status.OK, address=3)
    suite.result("use a handle after removal", "hid_report", Status.BAD_STATE, ("C-USB-02",),
                 handle=handle, report=b"\x01")
    suite.result("detach it again", "detach", Status.NO_DEVICE, address=3)
    return suite


def suite_services(iface: services.PsmServices) -> Suite:
    suite = Suite(iface)
    clock = suite.result("read the service clock", "clock_get", Status.OK, ("C-SVC-01",))
    suite.property("the clock names its source", clock, lambda v: "PC5" in v["source"])
    suite.result("sleep backwards", "sleep", Status.INVALID_ARGUMENT, nanoseconds=-5)
    suite.result("sleep forward", "sleep", Status.OK, nanoseconds=1000)
    suite.result("unknown log level", "log_write", Status.INVALID_ARGUMENT,
                 level="verbose", message="x")
    suite.result("write a record", "log_write", Status.OK, ("C-SVC-02",),
                 level="info", message="boot")
    suite.result("read zero records", "log_read", Status.INVALID_ARGUMENT, count=0)
    read = suite.result("read one record", "log_read", Status.OK, count=1)
    suite.property("the record is returned unchanged", read,
                   lambda v: v["records"][0]["message"] == "boot")
    suite.result("register a service", "service_register", Status.OK, ("C-SVC-01",),
                 name="pc5.boot", version="0.1.0")
    suite.result("register it twice", "service_register", Status.BAD_STATE,
                 name="pc5.boot", version="0.1.0")
    found = suite.result("look it up", "service_lookup", Status.OK, name="pc5.boot")
    suite.property("the lookup returns the registered version", found,
                   lambda v: v["version"] == "0.1.0")
    suite.result("look up an unknown service", "service_lookup", Status.NO_DEVICE,
                 ("C-SVC-01",), name="sony.system")
    for index in range(services.LOG_CAPACITY):
        iface.call("log_write", level="debug", message=f"filler {index}")
    suite.result("write past the log ring", "log_write", Status.OVERFLOW, ("C-SVC-02",),
                 level="debug", message="one too many")
    suite.result("panic", "panic", Status.OK, ("C-SVC-01",), reason="test")
    suite.result("call after panic", "clock_get", Status.BAD_STATE, ("C-SVC-01",))
    suite.result("unknown operation", "reboot", Status.UNSUPPORTED)
    return suite


# ------------------------------------------------------------------- running
def fresh_interfaces() -> Dict[str, Any]:
    """Build one configured instance of every double, sharing clocks where needed."""
    clock = Clock()
    irq = interrupts.PsmIrq()
    return {
        "cpu": cpu.PsmCpu(),
        "memory": memory.PsmMemory(),
        "irq": irq,
        "timer": interrupts.PsmTimer(clock=clock),
        "ipc": interrupts.PsmIpc(clock=clock),
        "storage": storage.PsmStorage(),
        "graphics": graphics.PsmGraphics(clock=clock),
        "audio": audio.PsmAudio(),
        "usb": usb.PsmUsb(clock=clock),
        "services": services.PsmServices(clock=clock),
    }


SUITE_ORDER = ("cpu", "memory", "irq", "timer", "ipc", "storage", "graphics",
               "audio", "usb", "services")


def run_all(instances: Optional[Dict[str, Any]] = None) -> List[Check]:
    """Run every suite and return the checks in a stable order."""
    made = instances or fresh_interfaces()
    checks: List[Check] = []
    checks.extend(suite_cpu(made["cpu"]).checks)
    checks.extend(suite_memory(made["memory"]).checks)
    for suite in suite_irq_timer_ipc(made["irq"], made["timer"], made["ipc"]):
        checks.extend(suite.checks)
    checks.extend(suite_storage(made["storage"]).checks)
    checks.extend(suite_graphics(made["graphics"]).checks)
    checks.extend(suite_audio(made["audio"]).checks)
    checks.extend(suite_usb(made["usb"]).checks)
    checks.extend(suite_services(made["services"]).checks)
    return checks


def transcript(instances: Dict[str, Any]) -> Dict[str, list]:
    """The recorded calls per interface: what deterministic replay compares."""
    return {name: [list(entry) for entry in iface.transcript]
            for name, iface in instances.items()}


def main(argv: Optional[List[str]] = None) -> int:
    checks = run_all()
    for check in checks:
        if not check.ok:
            print(check)
    failures = [check for check in checks if not check.ok]
    print(f"PSM conformance: {len(checks) - len(failures)}/{len(checks)} checks passed")
    if failures:
        print(f"{len(failures)} check(s) failed; this is the PC5-authored interface, "
              f"not the console's")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
