"""Rebuild partition images from Android block-OTA .new.dat(.br) + transfer.list."""

import io
from pathlib import Path
from typing import BinaryIO

import brotli

from .errors import UserError

BLOCK_SIZE = 4096
_CHUNK = 1 << 20
_IGNORED = {"erase", "zero", "free", "stash"}
_INCREMENTAL = {"move", "bsdiff", "imgdiff"}


class _BrotliReader(io.RawIOBase):
    """Streaming brotli decompression exposing a read() interface."""

    def __init__(self, raw: BinaryIO):
        self._raw = raw
        self._dec = brotli.Decompressor()
        self._buf = b""
        self._eof = False

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        while not self._buf and not self._eof:
            chunk = self._raw.read(_CHUNK)
            if not chunk:
                self._eof = True
                break
            self._buf = self._dec.process(chunk)
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def open_new_dat(path_in_zip: str, raw: BinaryIO) -> BinaryIO:
    """Return a readable stream of the raw block data (brotli-decoded if ``.br``)."""
    if path_in_zip.endswith(".br"):
        return io.BufferedReader(_BrotliReader(raw), buffer_size=_CHUNK)
    return raw


def _parse_ranges(spec: str) -> list[tuple[int, int]]:
    nums = [int(x) for x in spec.split(",")]
    if not nums or nums[0] != len(nums) - 1 or nums[0] % 2:
        raise UserError(f"malformed range set in transfer.list: {spec!r}")
    return [(nums[i], nums[i + 1]) for i in range(1, len(nums), 2)]


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    parts = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            raise UserError("new.dat is shorter than transfer.list requires")
        parts.append(chunk)
        size -= len(chunk)
    return b"".join(parts)


def sdat_to_img(transfer_list: Path, new_dat: BinaryIO, out: Path) -> Path:
    """Write the partition image described by ``transfer_list`` to ``out`` (sparse file)."""
    lines = Path(transfer_list).read_text().splitlines()
    try:
        version = int(lines[0])
        total_blocks = int(lines[1])
    except (IndexError, ValueError) as e:
        raise UserError("malformed transfer.list header") from e
    if not 1 <= version <= 4:
        raise UserError(f"unsupported transfer.list version {version}")
    commands = lines[2:] if version == 1 else lines[4:]

    with open(out, "wb") as f:
        f.truncate(total_blocks * BLOCK_SIZE)
        for line in commands:
            parts = line.split()
            if not parts:
                continue
            cmd = parts[0]
            if cmd == "new":
                for start, end in _parse_ranges(parts[1]):
                    f.seek(start * BLOCK_SIZE)
                    remaining = (end - start) * BLOCK_SIZE
                    while remaining:
                        n = min(remaining, _CHUNK)
                        f.write(_read_exact(new_dat, n))
                        remaining -= n
            elif cmd in _INCREMENTAL:
                raise UserError(f"incremental OTA not supported (transfer.list command {cmd!r})")
            elif cmd not in _IGNORED:
                raise UserError(f"unknown transfer.list command {cmd!r}")
    return Path(out)
