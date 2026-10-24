import pytest

from magisk_boot_patcher.bootimg import read_header, read_ramdisk_files
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


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("compression", ["gzip", "none"])
def test_read_ramdisk_files(tmp_path, version, compression):
    files = {"init": b"#!init\n", "first_stage_ramdisk/fstab.test": b"fstab\n"}
    boot = make_boot_image(
        tmp_path / "boot.img", files, header_version=version, ramdisk_compression=compression
    )
    assert read_ramdisk_files(boot) == files


def test_read_ramdisk_files_unsupported_compression(tmp_path):
    boot = make_boot_image(tmp_path / "boot.img", ramdisk_compression="lz4")
    assert read_ramdisk_files(boot) == {}


def test_read_ramdisk_files_no_ramdisk(tmp_path):
    boot = make_boot_image(tmp_path / "boot.img", ramdisk_compression="empty")
    assert read_header(boot).ramdisk_size == 0
    assert read_ramdisk_files(boot) == {}


def test_parse_cpio_newc_garbled_header_stops_gracefully():
    from magisk_boot_patcher.bootimg import _parse_cpio_newc
    from tests.helpers import _cpio_newc

    good = _cpio_newc({"init": b"x"})
    trailer = good.index(b"070701", 1)
    garbled = good[:trailer] + b"070701" + b"ZZZZZZZZ" * 13 + b"junk"
    assert _parse_cpio_newc(garbled) == {"init": b"x"}
    truncated = good[:trailer] + b"070701" + b"0000"
    assert _parse_cpio_newc(truncated) == {"init": b"x"}


def test_read_ramdisk_files_garbled_cpio(tmp_path):
    boot = make_boot_image(tmp_path / "boot.img", ramdisk_compression="none")
    data = bytearray(boot.read_bytes())
    pos = data.index(b"070701")
    data[pos + 6:pos + 14] = b"GGGGGGGG"
    boot.write_bytes(bytes(data))
    assert read_ramdisk_files(boot) == {}
