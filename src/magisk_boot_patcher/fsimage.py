"""Detect and read files from EROFS / ext4 images (including sparse images)."""

import struct
from pathlib import Path
from typing import Literal

from .errors import ToolError, UserError
from .tools import run_tool

SPARSE_MAGIC = 0xED26FF3A
EROFS_MAGIC = 0xE0F5E1E2
EXT4_MAGIC = 0xEF53


class UnsupportedImage(UserError):
    """The image is neither EROFS nor ext4."""


def _u32(f, offset: int) -> int | None:
    f.seek(offset)
    data = f.read(4)
    return struct.unpack("<I", data)[0] if len(data) == 4 else None


def unsparse_if_needed(img: Path) -> Path:
    """Convert an Android sparse image to raw (``<img>.raw``); raw images are returned as is."""
    img = Path(img)
    with open(img, "rb") as f:
        magic = _u32(f, 0)
    if magic != SPARSE_MAGIC:
        return img
    raw = img.with_name(img.name + ".raw")
    run_tool(["simg2img", img, raw])
    return raw


def detect_fs(img: Path) -> Literal["erofs", "ext4"]:
    with open(img, "rb") as f:
        if _u32(f, 1024) == EROFS_MAGIC:
            return "erofs"
        f.seek(1024 + 0x38)
        data = f.read(2)
        if len(data) == 2 and struct.unpack("<H", data)[0] == EXT4_MAGIC:
            return "ext4"
    raise UnsupportedImage(f"{img}: not an EROFS or ext4 image")


# Both dump.erofs and debugfs exit 0 on a missing path and only report it on stderr.
_MISSING_MARKERS = {
    "erofs": b"read inode failed",
    "ext4": b"File not found by ext2_lookup",
}


def _erofs_args(mode: str, img: Path, path: str) -> list:
    return ["dump.erofs", f"--{mode}", f"--path={path}", img]


def _debugfs_args(cmd: str, img: Path, path: str) -> list:
    return ["debugfs", "-R", f"{cmd} {path}", img]


def _query(img: Path, path: str, erofs_mode: str, debugfs_cmd: str) -> bytes | None:
    img = Path(img)
    fs = detect_fs(img)
    if fs == "erofs":
        args = _erofs_args(erofs_mode, img, path)
    else:
        args = _debugfs_args(debugfs_cmd, img, path)
    proc = run_tool(args)
    if _MISSING_MARKERS[fs] in proc.stderr:
        return None
    if fs == "ext4" and (b"while trying to open" in proc.stderr or b"Filesystem not open" in proc.stderr):
        raise ToolError("debugfs could not open the image", args, proc.stderr.decode(errors="replace"))
    return proc.stdout


def read_file(img: Path, path: str) -> bytes | None:
    """Return the content of ``path`` in the image, or None if it does not exist."""
    return _query(img, path, "cat", "cat")


def list_dir(img: Path, path: str) -> list[str]:
    """Return entry names (no ``.``/``..``) of directory ``path``; empty if missing."""
    img = Path(img)
    out = _query(img, path, "ls", "ls -p")
    if out is None:
        return []
    text = out.decode(errors="replace")
    names: list[str] = []
    if detect_fs(img) == "erofs":
        in_table = False
        for line in text.splitlines():
            if in_table:
                cols = line.split(None, 2)
                if len(cols) == 3 and cols[2] not in (".", ".."):
                    names.append(cols[2])
            elif line.split()[:3] == ["NID", "TYPE", "FILENAME"]:
                in_table = True
    else:
        for line in text.splitlines():
            fields = line.split("/")
            # /inode/mode/uid/gid/name/size/
            if len(fields) >= 7 and fields[5] not in ("", ".", ".."):
                names.append(fields[5])
    return names
