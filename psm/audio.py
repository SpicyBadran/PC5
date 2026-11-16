"""PSM-AUD v0 — audio test double (contract subsystem ``audio``).

Public: the console presents a "Tempest" 3D audio engine. Not public: what that
engine's programming interface looks like. So this double models what a PC5
output path can honestly model - PCM stream format negotiation, buffer
accounting, latency classes, underrun and overflow reporting - and refuses the
DSP operations the contract marks UNKNOWN.
"""

from __future__ import annotations

from typing import Any, Dict

from .psm import Interface, InterfaceInfo, OpSpec, PsmError, Status, ok

FORMATS = ("pcm_s16le", "pcm_f32le")
LATENCY_CLASSES = ("interactive", "balanced", "throughput")
RATE_RANGE = (8000, 192000)
CHANNEL_RANGE = (1, 8)


class PsmAudio(Interface):
    info = InterfaceInfo(
        name="PSM-AUD", version="0.1.0", subsystem="audio", milestone="M15",
        note="PC5-authored test double; the console's audio DSP contract is UNKNOWN.")

    OPS = {
        "open_stream": OpSpec(
            name="open_stream",
            summary="Negotiate a PCM output stream",
            inputs=("rate", "channels", "format", "latency_class", "owner"),
            outputs="stream, buffer_frames, latency_ns",
            errors=(Status.INVALID_ARGUMENT, Status.EXHAUSTED),
            invariants=("rate and channel counts are range-checked",
                        "the buffer size follows from the latency class, not from a guess",
                        "the stream limit is enforced with EXHAUSTED"),
            claim_ids=("C-AUD-01", "C-AUD-02")),
        "write": OpSpec(
            name="write",
            summary="Queue PCM frames for output",
            inputs=("stream", "frames", "owner"),
            outputs="accepted, free_frames",
            errors=(Status.OWNERSHIP, Status.BAD_STATE, Status.INVALID_ARGUMENT,
                    Status.OVERFLOW),
            invariants=("writing more than the buffer holds is OVERFLOW, not a silent drop",
                        "a closed stream is BAD_STATE"),
            claim_ids=("C-AUD-03",)),
        "drain": OpSpec(
            name="drain",
            summary="Advance the virtual clock until the buffer empties",
            inputs=("stream",),
            outputs="drained_frames, now",
            errors=(Status.OWNERSHIP,),
            invariants=("draining consumes exactly the queued frames",),
            claim_ids=("C-AUD-03",)),
        "close": OpSpec(
            name="close",
            summary="Close a stream and report what was still queued",
            inputs=("stream", "owner"),
            outputs="discarded_frames",
            errors=(Status.OWNERSHIP,),
            invariants=("queued frames are counted, never silently lost",),
            claim_ids=("C-AUD-03",)),
        "dsp_load": OpSpec(
            name="dsp_load",
            summary="Load a program onto the console's 3D audio DSP",
            inputs=("program",),
            outputs="none",
            errors=(Status.UNSUPPORTED,),
            unsupported=True,
            invariants=("returns UNSUPPORTED: the DSP interface is UNKNOWN",),
            claim_ids=("C-AUD-03",)),
    }

    def __init__(self, streams: int = 4) -> None:
        super().__init__()
        self._limit = int(streams)
        self._streams: Dict[int, Dict[str, Any]] = {}
        self._next_stream = 1
        self._now = 0

    def _stream(self, stream: int, owner: str) -> Dict[str, Any]:
        entry = self._streams.get(int(stream))
        if entry is None:
            raise PsmError(Status.OWNERSHIP, f"stream {stream} does not exist")
        if entry["owner"] != owner:
            raise PsmError(Status.OWNERSHIP, f"stream {stream} belongs to {entry['owner']!r}")
        return entry

    def op_open_stream(self, rate: int, channels: int, format: str, latency_class: str,
                       owner: str) -> Any:
        rate, channels = int(rate), int(channels)
        if not RATE_RANGE[0] <= rate <= RATE_RANGE[1]:
            raise PsmError(Status.INVALID_ARGUMENT, f"rate is {RATE_RANGE[0]}..{RATE_RANGE[1]} Hz")
        if not CHANNEL_RANGE[0] <= channels <= CHANNEL_RANGE[1]:
            raise PsmError(Status.INVALID_ARGUMENT,
                           f"channels are {CHANNEL_RANGE[0]}..{CHANNEL_RANGE[1]}")
        if format not in FORMATS:
            raise PsmError(Status.INVALID_ARGUMENT, f"format must be one of {FORMATS}")
        if latency_class not in LATENCY_CLASSES:
            raise PsmError(Status.INVALID_ARGUMENT, f"latency_class must be one of {LATENCY_CLASSES}")
        if not isinstance(owner, str) or not owner:
            raise PsmError(Status.INVALID_ARGUMENT, "a stream needs an owner")
        if len(self._streams) >= self._limit:
            raise PsmError(Status.EXHAUSTED, f"the stream limit ({self._limit}) is reached")
        # Buffer depth is a PC5 decision, stated as milliseconds per class.
        ms = {"interactive": 10, "balanced": 40, "throughput": 120}[latency_class]
        buffer_frames = rate * ms // 1000
        stream = self._next_stream
        self._next_stream += 1
        self._streams[stream] = {"stream": stream, "owner": owner, "rate": rate,
                                 "channels": channels, "format": format,
                                 "buffer": buffer_frames, "queued": 0, "closed": False}
        return ok({"stream": stream, "buffer_frames": buffer_frames,
                   "latency_ns": ms * 1_000_000})

    def op_write(self, stream: int, frames: int, owner: str) -> Any:
        entry = self._stream(stream, owner)
        frames = int(frames)
        if frames <= 0:
            raise PsmError(Status.INVALID_ARGUMENT, "frames must be positive")
        if entry["closed"]:
            raise PsmError(Status.BAD_STATE, f"stream {stream} is closed")
        if entry["queued"] + frames > entry["buffer"]:
            raise PsmError(Status.OVERFLOW,
                           f"stream {stream} has {entry['buffer'] - entry['queued']} frames free; "
                           f"overwriting would be a silent audio glitch")
        entry["queued"] += frames
        return ok({"accepted": frames, "free_frames": entry["buffer"] - entry["queued"]})

    def op_drain(self, stream: int) -> Any:
        entry = self._streams.get(int(stream))
        if entry is None:
            raise PsmError(Status.OWNERSHIP, f"stream {stream} does not exist")
        drained = entry["queued"]
        # Playing the buffer takes its own duration on the virtual clock.
        self._now += (drained * 1_000_000_000) // entry["rate"] if drained else 0
        entry["queued"] = 0
        return ok({"drained_frames": drained, "now": self._now})

    def op_close(self, stream: int, owner: str) -> Any:
        entry = self._stream(stream, owner)
        discarded = entry["queued"]
        entry["closed"] = True
        entry["queued"] = 0
        del self._streams[entry["stream"]]
        return ok({"discarded_frames": discarded})
