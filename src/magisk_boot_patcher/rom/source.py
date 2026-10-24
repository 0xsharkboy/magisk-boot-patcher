"""ROM source abstraction and detection."""

import zipfile
from pathlib import Path
from typing import Protocol

from ..errors import UserError

BOOT_MAGIC = b"ANDROID!"
REBUILDABLE = "vendor"


class RomSource(Protocol):
    name: str

    def target_image_name(self) -> str: ...

    def extract_target(self, dest: Path) -> Path: ...

    def has_file(self, name: str) -> bool: ...

    def read_member(self, name: str) -> bytes | None: ...

    def partition_image(self, name: str, workdir: Path) -> Path | None: ...

    def extracted_partitions(self) -> list[str]: ...


def check_partition_name(name: str) -> None:
    if name != REBUILDABLE:
        raise ValueError(f"only the {REBUILDABLE!r} partition can be rebuilt, not {name!r}")


def open_source(path: Path) -> RomSource:
    from .block_ota import BlockOtaZip
    from .boot_only import BootImageOnly

    path = Path(path)
    if not path.is_file():
        raise UserError(f"{path}: file not found")
    if zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile as e:
            raise UserError(f"{path}: corrupt zip ({e})") from None
        if "payload.bin" in names:
            raise UserError("payload.bin (A/B) not supported yet")
        if not names & {"boot.img", "init_boot.img"}:
            raise UserError(f"{path}: zip contains neither boot.img nor init_boot.img")
        return BlockOtaZip(path)
    if path.name.startswith("vendor_boot") and path.suffix == ".img":
        raise UserError("vendor_boot images are not supported")
    with open(path, "rb") as f:
        magic = f.read(8)
    if path.suffix == ".img" and magic == BOOT_MAGIC:
        return BootImageOnly(path)
    raise UserError(f"{path}: not a block-OTA zip nor an Android boot image")
