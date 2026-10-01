"""Read ``general.size`` from a GGUF file header.

The parameter count lives in the metadata key-value block that follows
the 16-byte GGUF header — well inside the first megabytes of any real
weight file. We parse only as much of that block as the read window
covers and return ``None`` on anything unexpected: the stat is
first-class when the file says so, unknown otherwise. No guessing from
filenames.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

GGUF_MAGIC = b"GGUF"
# The general.* keys sit at the front of the kv block; a 4 MiB window
# covers them with room for long license blobs that come first in some
# layouts.
READ_WINDOW = 4 * 1024 * 1024

_VALUE_SIZES = {
    0: 1,    # uint8
    1: 1,    # int8
    2: 2,    # uint16
    3: 2,    # int16
    4: 4,    # uint32
    5: 4,    # int32
    6: 4,    # float32
    7: 1,    # bool
    8: -1,   # string: 8-byte length prefix + bytes
    9: -1,   # array: element type + count + elements
    10: 8,   # uint64
    11: 8,   # int64
    12: 8,   # float64
}

# Array element types and their fixed widths (used to skip arrays).
_ELEMENT_SIZES = {
    0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8,
}


class _Cursor:
    __slots__ = ("data", "offset")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def need(self, n: int) -> bool:
        return self.offset + n <= len(self.data)

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.offset)[0]
        self.offset += 4
        return value

    def u64(self) -> int:
        value = struct.unpack_from("<Q", self.data, self.offset)[0]
        self.offset += 8
        return value


def _skip_value(cursor: _Cursor, value_type: int) -> None:
    """Advance past one kv value. Raises ValueError past the window."""
    if value_type in _VALUE_SIZES and _VALUE_SIZES[value_type] > 0:
        size = _VALUE_SIZES[value_type]
        if not cursor.need(size):
            raise ValueError("value runs past the read window")
        cursor.offset += size
        return
    if value_type == 8:  # string
        if not cursor.need(8):
            raise ValueError("string length past the read window")
        length = cursor.u64()
        if length > len(cursor.data):
            raise ValueError("string runs past the read window")
        if not cursor.need(length):
            raise ValueError("string runs past the read window")
        cursor.offset += length
        return
    if value_type == 9:  # array
        element_type = cursor.u32()
        if element_type == 8:  # string array: per-element length prefix
            count = cursor.u64()
            for _ in range(count):
                if not cursor.need(8):
                    raise ValueError("array string length past the window")
                element_length = cursor.u64()
                if not cursor.need(element_length):
                    raise ValueError("array string runs past the window")
                cursor.offset += element_length
            return
        if element_type not in _ELEMENT_SIZES:
            raise ValueError("unknown array element type")
        count = cursor.u64()
        element_size = _ELEMENT_SIZES[element_type]
        total = count * element_size
        if total > len(cursor.data) or not cursor.need(total):
            raise ValueError("array runs past the read window")
        cursor.offset += total
        return
    raise ValueError("unknown GGUF value type")


def gguf_parameter_count(path: Path) -> int | None:
    """The declared parameter count (``general.size``), or None."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(READ_WINDOW)
    except OSError:
        return None
    if len(head) < 16 or head[:4] != GGUF_MAGIC:
        return None
    try:
        cursor = _Cursor(head)
        cursor.offset = 4
        version = cursor.u32()
        if version < 2:
            return None
        cursor.u64()  # tensor count
        kv_count = cursor.u64()
        if kv_count > 1_000_000:
            return None
        for _ in range(kv_count):
            if not cursor.need(8):
                return None
            key_length = cursor.u64()
            if key_length > 1_000_000 or not cursor.need(key_length):
                return None
            key = cursor.data[cursor.offset : cursor.offset + key_length]
            cursor.offset += key_length
            if not cursor.need(4):
                return None
            value_type = cursor.u32()
            if key == b"general.size":
                if value_type != 10:
                    return None
                return cursor.u64()
            _skip_value(cursor, value_type)
    except (struct.error, ValueError, IndexError):
        return None
    return None
