import pytest

from magisk_boot_patcher.bootimg import read_header
from magisk_boot_patcher.errors import UserError
from tests.helpers import make_boot_image


def test_read_header_v1(tmp_path):
    h = read_header(make_boot_image(tmp_path / "boot.img", header_version=1))
    assert h.version == 1
    assert h.ramdisk_size > 0
    assert h.os_version == (16, 0, 0)
    assert h.cmdline == " androidboot.super_partition=system"


def test_read_header_v4(tmp_path):
    h = read_header(make_boot_image(tmp_path / "init_boot.img", header_version=4, cmdline="a=b"))
    assert h.version == 4
    assert h.ramdisk_size > 0
    assert h.os_version == (16, 0, 0)
    assert h.cmdline == "a=b"


def test_read_header_rejects_garbage(tmp_path):
    p = tmp_path / "x.img"
    p.write_bytes(b"nope" * 1000)
    with pytest.raises(UserError):
        read_header(p)
