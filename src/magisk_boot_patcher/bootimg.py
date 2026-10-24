"""Minimal Android boot image header reader (v0-v4) and ramdisk file reader."""

import struct
import zlib
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


GZIP_MAGIC = b"\x1f\x8b"
CPIO_NEWC_MAGIC = b"070701"
_S_IFMT = 0o170000
_S_IFREG = 0o100000


def _ramdisk_bytes(path: Path) -> bytes:
    with open(path, "rb") as f:
        head = f.read(48)
        if len(head) < 48 or head[:8] != BOOT_MAGIC:
            raise UserError(f"{path}: not an Android boot image")
        kernel_size, = struct.unpack_from("<I", head, 8)
        version, = struct.unpack_from("<I", head, 40)
        if version <= 2:
            ramdisk_size, = struct.unpack_from("<I", head, 16)
            page_size, = struct.unpack_from("<I", head, 36)
        elif version <= 4:
            ramdisk_size, = struct.unpack_from("<I", head, 12)
            page_size = 4096
        else:
            raise UserError(f"{path}: unsupported boot image header version {version}")
        if ramdisk_size == 0 or page_size == 0:
            return b""
        f.seek(page_size * (1 + -(-kernel_size // page_size)))
        return f.read(ramdisk_size)


def _gunzip(data: bytes) -> bytes:
    """Decompress one or more concatenated gzip members, ignoring zero padding."""
    out = []
    while data[:2] == GZIP_MAGIC:
        d = zlib.decompressobj(wbits=31)
        out.append(d.decompress(data))
        data = d.unused_data.lstrip(b"\0")
    return b"".join(out)


def _parse_cpio_newc(data: bytes) -> dict[str, bytes]:
    """Return regular files of (possibly concatenated) cpio newc archives."""
    files: dict[str, bytes] = {}
    pos = 0
    while True:
        while pos < len(data) and data[pos] == 0:
            pos += 1
        if data[pos:pos + 6] != CPIO_NEWC_MAGIC or pos + 110 > len(data):
            break
        try:
            fields = [int(data[pos + 6 + 8 * i:pos + 14 + 8 * i], 16) for i in range(13)]
        except ValueError:  # garbled header: keep what was parsed so far
            break
        mode, filesize, namesize = fields[1], fields[6], fields[11]
        name_start = pos + 110
        name = data[name_start:name_start + namesize].split(b"\0", 1)[0].decode(errors="replace")
        data_start = name_start + namesize
        data_start += -data_start % 4
        content = data[data_start:data_start + filesize]
        pos = data_start + filesize
        pos += -pos % 4
        if name == "TRAILER!!!":
            continue
        if mode & _S_IFMT == _S_IFREG:
            name = name.lstrip("/")
            if name.startswith("./"):
                name = name[2:]
            files[name] = content
    return files


def read_ramdisk_files(boot: Path) -> dict[str, bytes]:
    """Regular files of the boot image ramdisk, keyed by path without leading ``/``.

    Supports gzip and uncompressed cpio newc ramdisks; anything else (no ramdisk, lz4,
    xz...) returns ``{}``.
    """
    data = _ramdisk_bytes(Path(boot))
    if data[:2] == GZIP_MAGIC:
        try:
            data = _gunzip(data)
        except zlib.error:
            return {}
    if data[:6] != CPIO_NEWC_MAGIC:
        return {}
    return _parse_cpio_newc(data)


def ramdisk_format(boot: Path) -> str:
    """Short name of the ramdisk format: "none", "gzip", "cpio", "lz4", "xz", ... or "unknown"."""
    data = _ramdisk_bytes(Path(boot))[:6]
    if not data:
        return "none"
    for magic, name in (
        (GZIP_MAGIC, "gzip"),
        (CPIO_NEWC_MAGIC, "cpio"),
        (b"\x02\x21\x4c\x18", "lz4"),
        (b"\x04\x22\x4d\x18", "lz4"),
        (b"\xfd7zXZ", "xz"),
        (b"\x5d\x00\x00", "lzma"),
        (b"BZh", "bzip2"),
        (b"\x28\xb5\x2f\xfd", "zstd"),
    ):
        if data.startswith(magic):
            return name
    return "unknown"
