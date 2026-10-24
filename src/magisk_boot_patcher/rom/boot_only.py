"""A bare boot.img with no ROM around it."""

import shutil
from pathlib import Path

from .source import check_partition_name


class BootImageOnly:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.name = self.path.name

    def target_image_name(self) -> str:
        return "boot.img"

    def extract_target(self, dest: Path) -> Path:
        shutil.copyfile(self.path, dest)
        return Path(dest)

    def has_file(self, name: str) -> bool:
        return False

    def read_member(self, name: str) -> bytes | None:
        return None

    def partition_image(self, name: str, workdir: Path) -> Path | None:
        check_partition_name(name)
        return None

    def extracted_partitions(self) -> list[str]:
        return []
