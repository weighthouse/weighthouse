"""GGUF header reading: the parameter count comes from the file's own
``general.size`` metadata — parsed, never guessed from the filename.
Anything unreadable or malformed reports unknown (None), never a guess.
"""

from __future__ import annotations

import struct
from pathlib import Path

from app.gguf import gguf_parameter_count


def _kv_string(key: str, value: str) -> bytes:
    key_bytes = key.encode()
    value_bytes = value.encode()
    return (
        struct.pack("<Q", len(key_bytes)) + key_bytes
        + struct.pack("<I", 8)  # string
        + struct.pack("<Q", len(value_bytes)) + value_bytes
    )


def _kv_uint64(key: str, value: int) -> bytes:
    key_bytes = key.encode()
    return (
        struct.pack("<Q", len(key_bytes)) + key_bytes
        + struct.pack("<I", 10)  # uint64
        + struct.pack("<Q", value)
    )


def _kv_array(key: str, values: list[str]) -> bytes:
    body = b"".join(struct.pack("<Q", len(v.encode())) + v.encode() for v in values)
    key_bytes = key.encode()
    return (
        struct.pack("<Q", len(key_bytes)) + key_bytes
        + struct.pack("<I", 9)  # array
        + struct.pack("<I", 8)  # element: string
        + struct.pack("<Q", len(values))
        + body
    )


def _build_file(path: Path, kvs: list[bytes], magic: bytes = b"GGUF", version: int = 3) -> None:
    header = (
        magic
        + struct.pack("<I", version)
        + struct.pack("<Q", 0)  # tensor count
        + struct.pack("<Q", len(kvs))
    )
    path.write_bytes(header + b"".join(kvs))


def test_parameter_count_is_read_from_general_size(tmp_path: Path):
    path = tmp_path / "model.gguf"
    _build_file(
        path,
        [
            _kv_string("general.architecture", "llama"),
            _kv_uint64("general.size", 70_557_022_720),
            _kv_string("general.name", "Llama-3.3-70B"),
        ],
    )
    assert gguf_parameter_count(path) == 70_557_022_720


def test_keys_before_the_count_are_skipped(tmp_path: Path):
    path = tmp_path / "model.gguf"
    _build_file(
        path,
        [
            _kv_string("general.license", "LICENSE-TEXT" * 200),
            _kv_array("general.tags", ["llama", "instruct", "gguf"]),
            _kv_uint64("general.size", 1_249_479_168),
        ],
    )
    assert gguf_parameter_count(path) == 1_249_479_168


def test_wrong_magic_is_unknown(tmp_path: Path):
    path = tmp_path / "not-gguf.bin"
    _build_file(path, [_kv_uint64("general.size", 5)], magic=b"XXXX")
    assert gguf_parameter_count(path) is None


def test_truncated_header_is_unknown(tmp_path: Path):
    path = tmp_path / "short.gguf"
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 4)
    assert gguf_parameter_count(path) is None


def test_truncated_value_is_unknown(tmp_path: Path):
    path = tmp_path / "truncated.gguf"
    header = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 1)
    kv = struct.pack("<Q", 12) + b"general.size" + struct.pack("<I", 10) + b"\x01\x02"
    path.write_bytes(header + kv)
    assert gguf_parameter_count(path) is None


def test_wrong_value_type_is_unknown(tmp_path: Path):
    path = tmp_path / "typed.gguf"
    _build_file(path, [_kv_string("general.size", "70B")])
    assert gguf_parameter_count(path) is None


def test_version_one_is_unknown(tmp_path: Path):
    path = tmp_path / "v1.gguf"
    _build_file(path, [_kv_uint64("general.size", 42)], version=1)
    assert gguf_parameter_count(path) is None


def test_missing_file_is_unknown(tmp_path: Path):
    assert gguf_parameter_count(tmp_path / "absent.gguf") is None
