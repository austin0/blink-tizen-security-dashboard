"""Small MP4 box reader used to describe the live fMP4 stream to MSE.

Only the boxes needed for codec discovery are walked.  Keeping this local (and
small) avoids launching a second ffprobe against a live, one-shot Blink stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Box:
    kind: bytes
    start: int
    payload_start: int
    end: int


def boxes(data: bytes, start: int = 0, end: int | None = None) -> Iterator[Box]:
    """Yield complete ISO-BMFF boxes between *start* and *end*.

    Iteration stops at the first incomplete box.  That is intentional: the
    caller feeds this a growing pipe buffer and asks again when more bytes have
    arrived.
    """

    if end is None:
        end = len(data)
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = int.from_bytes(data[pos + 8 : pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield Box(kind, pos, pos + header, pos + size)
        pos += size


def moov_end(data: bytes) -> int | None:
    """Return the byte offset just past a complete ``moov`` box, if present."""

    for box in boxes(data):
        if box.kind == b"moov":
            return box.end
    return None


_CONTAINERS = {
    b"moov": 0,
    b"trak": 0,
    b"mdia": 0,
    b"minf": 0,
    b"stbl": 0,
    b"stsd": 8,  # version/flags + entry_count
    b"avc1": 78,  # visual sample entry fields
    b"mp4a": 28,  # audio sample entry version 0 (adjusted for version 1)
    b"wave": 0,
}


def _walk(data: bytes, start: int, end: int, depth: int = 0) -> Iterator[Box]:
    if depth > 8:
        return
    for box in boxes(data, start, end):
        yield box
        skip = _CONTAINERS.get(box.kind)
        if skip is None:
            continue
        if box.kind == b"mp4a":
            # SampleEntry version lives at payload + 8. Version 1 entries add
            # 16 bytes of extra fields before the child boxes.
            if box.payload_start + 10 <= box.end:
                version = int.from_bytes(
                    data[box.payload_start + 8 : box.payload_start + 10], "big"
                )
                skip = 44 if version == 1 else 28
        child_start = box.payload_start + skip
        if child_start < box.end:
            yield from _walk(data, child_start, box.end, depth + 1)


def _first(data: bytes, start: int, end: int, kind: bytes) -> Box | None:
    for box in _walk(data, start, end):
        if box.kind == kind:
            return box
    return None


def _descriptor(data: bytes, offset: int) -> tuple[int, bytes]:
    if offset >= len(data):
        raise ValueError("missing descriptor")
    tag = data[offset]
    offset += 1
    size = 0
    for _ in range(4):
        if offset >= len(data):
            raise ValueError("truncated descriptor size")
        value = data[offset]
        offset += 1
        size = (size << 7) | (value & 0x7F)
        if not value & 0x80:
            break
    else:
        raise ValueError("invalid descriptor size")
    if offset + size > len(data):
        raise ValueError("truncated descriptor")
    return tag, data[offset : offset + size]


def _aac_object_type(esds_payload: bytes) -> int | None:
    try:
        tag, es = _descriptor(esds_payload, 4)  # skip FullBox version/flags
        if tag != 3 or len(es) < 3:
            return None
        offset = 3
        flags = es[2]
        if flags & 0x80:
            offset += 2
        if flags & 0x40:
            if offset >= len(es):
                return None
            offset += 1 + es[offset]
        if flags & 0x20:
            offset += 2
        tag, decoder = _descriptor(es, offset)
        if tag != 4 or len(decoder) < 13:
            return None
        tag, config = _descriptor(decoder, 13)
        if tag != 5 or len(config) < 2 or decoder[0] != 0x40:
            return None
        audio_type = config[0] >> 3
        if audio_type == 31:
            audio_type = 32 + ((config[0] & 7) << 3) + (config[1] >> 5)
        return audio_type or None
    except (IndexError, ValueError):
        return None


def describe_init(data: bytes) -> dict[str, object] | None:
    """Describe the codecs in a complete fMP4 initialization segment.

    Returns ``None`` while the ``moov`` box is still incomplete.  A missing
    audio track is normal (the camera may not expose one, or ``-an`` was used).
    """

    end = moov_end(data)
    if end is None:
        return None

    video_codec = None
    audio_codec = None

    avcc = _first(data, 0, end, b"avcC")
    if avcc is not None and avcc.payload_start + 4 <= avcc.end:
        payload = data[avcc.payload_start : avcc.end]
        video_codec = "avc1.%02X%02X%02X" % (payload[1], payload[2], payload[3])

    esds = _first(data, 0, end, b"esds")
    if esds is not None:
        aot = _aac_object_type(data[esds.payload_start : esds.end])
        if aot is not None:
            audio_codec = f"mp4a.40.{aot}"
        else:
            # Blink sends AAC-LC.  The esds parser can fail on unusual encoders
            # while the track is still perfectly playable as LC.
            audio_codec = "mp4a.40.2"

    if video_codec is None:
        return None
    return {
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "moov_end": end,
    }
