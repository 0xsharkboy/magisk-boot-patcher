"""Block-based OTA zip (``*.new.dat(.br)`` + ``*.transfer.list``), streamed, never fully extracted."""

import shutil
import zipfile
from pathlib import Path

from ..errors import UserError
from ..fsimage import unsparse_if_needed
from ..sdat import open_new_dat, sdat_to_img
from .source import check_partition_name


class BlockOtaZip:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.name = self.path.name
        try:
            with zipfile.ZipFile(self.path) as z:
                self._names = set(z.namelist())
        except zipfile.BadZipFile as e:
            raise UserError(f"{self.path}: corrupt zip ({e})") from None

    def target_image_name(self) -> str:
        return "init_boot.img" if "init_boot.img" in self._names else "boot.img"

    def extract_target(self, dest: Path) -> Path:
        with zipfile.ZipFile(self.path) as z, z.open(self.target_image_name()) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        return Path(dest)

    def has_file(self, name: str) -> bool:
        return name in self._names

    def read_member(self, name: str) -> bytes | None:
        if name not in self._names:
            return None
        with zipfile.ZipFile(self.path) as z:
            return z.read(name)

    def _vendor_dat(self) -> str | None:
        for cand in ("vendor.new.dat.br", "vendor.new.dat"):
            if cand in self._names and "vendor.transfer.list" in self._names:
                return cand
        return None

    def extracted_partitions(self) -> list[str]:
        if self._vendor_dat() or "vendor.img" in self._names:
            return ["vendor"]
        return []

    def partition_image(self, name: str, workdir: Path) -> Path | None:
        check_partition_name(name)
        workdir = Path(workdir)
        dat = self._vendor_dat()
        out = workdir / "vendor.img"
        with zipfile.ZipFile(self.path) as z:
            if dat:
                tl = workdir / "vendor.transfer.list"
                tl.write_bytes(z.read("vendor.transfer.list"))
                with z.open(dat) as raw, open_new_dat(dat, raw) as stream:
                    sdat_to_img(tl, stream, out)
            elif "vendor.img" in self._names:
                with z.open("vendor.img") as src, open(out, "wb") as f:
                    shutil.copyfileobj(src, f, 1 << 20)
            else:
                return None
        return unsparse_if_needed(out)
