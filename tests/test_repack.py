import zipfile

import pytest

from magisk_boot_patcher.errors import UserError
from magisk_boot_patcher.repack import repack_zip
from tests.helpers import make_block_ota_zip


def test_repack_replaces_member_only(tmp_path):
    src = make_block_ota_zip(tmp_path / "src")
    with zipfile.ZipFile(src, "a") as z:
        z.comment = b"signature"
        z.writestr(zipfile.ZipInfo("stored.bin", (2020, 1, 2, 3, 4, 6)), b"s" * 10,
                   compress_type=zipfile.ZIP_STORED)
    new = tmp_path / "new.img"
    new.write_bytes(b"NEWBOOT" * 1000)
    dest = tmp_path / "out" / "rom-unsigned.zip"
    dest.parent.mkdir()
    assert repack_zip(src, "boot.img", new, dest) == dest
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dest) as b:
        assert b.comment == b""
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
        for ia, ib in zip(a.infolist(), b.infolist()):
            assert ia.compress_type == ib.compress_type
            assert ia.date_time == ib.date_time
            assert ia.external_attr == ib.external_attr
            if ia.filename == "boot.img":
                assert b.read("boot.img") == new.read_bytes()
            else:
                assert a.read(ia.filename) == b.read(ib.filename)
    assert [p.name for p in dest.parent.iterdir()] == ["rom-unsigned.zip"]


def test_repack_missing_member(tmp_path):
    src = make_block_ota_zip(tmp_path / "src")
    new = tmp_path / "new.img"
    new.write_bytes(b"x")
    dest = tmp_path / "o.zip"
    with pytest.raises(UserError):
        repack_zip(src, "nope.img", new, dest)
    assert not dest.exists()
