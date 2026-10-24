"""Minimal Android boot image header reader (v0-v4)."""

import struct
from dataclasses import dataclass
from pathlib import Path

from .errors import UserError

BOOT_MAGIC = b"ANDROID!"


@dataclass
class BootHeader:
    version: int
    ramdisk_size: int
    os_version: tuple[int, int, int] | None
    cmdline: str


def _decode_os_version(value: int) -> tuple[int, int, int] | None:
    v = value >> 11
    if v == 0:
        return None
    return (v >> 14) & 0x7F, (v >> 7) & 0x7F, v & 0x7F


def _cstr(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode(errors="replace")


def read_header(path: Path) -> BootHeader:
    with open(path, "rb") as f:
        head = f.read(1632)
    if len(head) < 1632 - 1536 + 44 or head[:8] != BOOT_MAGIC:
        raise UserError(f"{path}: not an Android boot image")
    version = struct.unpack_from("<I", head, 40)[0]
    if version <= 2:
        ramdisk_size, = struct.unpack_from("<I", head, 16)
        os_raw, = struct.unpack_from("<I", head, 44)
        cmdline = _cstr(head[64:64 + 512])
    elif version <= 4:
        ramdisk_size, os_raw = struct.unpack_from("<II", head, 12)
        cmdline = _cstr(head[44:44 + 1536])
    else:
        raise UserError(f"{path}: unsupported boot image header version {version}")
    return BootHeader(version, ramdisk_size, _decode_os_version(os_raw), cmdline)
