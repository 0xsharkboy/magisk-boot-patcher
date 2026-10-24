import zipfile

import pytest

from magisk_boot_patcher.bootimg import read_header
from magisk_boot_patcher.errors import UserError
from magisk_boot_patcher.fsimage import detect_fs, read_file
from magisk_boot_patcher.rom.block_ota import BlockOtaZip
from magisk_boot_patcher.rom.boot_only import BootImageOnly
from magisk_boot_patcher.rom.source import open_source
from tests.helpers import DEFAULT_FSTAB, make_block_ota_zip, make_boot_image


@pytest.mark.parametrize("fs", ["erofs", "ext4"])
@pytest.mark.parametrize("br", [False, True])
def test_vendor_partition(tmp_path, tmp_workdir, fs, br):
    src = open_source(make_block_ota_zip(tmp_path / "z", vendor_fs=fs, brotli=br))
    assert isinstance(src, BlockOtaZip)
    assert src.extracted_partitions() == ["vendor"]
    img = src.partition_image("vendor", tmp_workdir)
    assert detect_fs(img) == fs
    assert read_file(img, "/etc/fstab.fake").decode() == DEFAULT_FSTAB


def test_target_image_name(tmp_path):
    assert open_source(make_block_ota_zip(tmp_path / "a")).target_image_name() == "boot.img"
    src = open_source(make_block_ota_zip(tmp_path / "b", with_init_boot=True))
    assert src.target_image_name() == "init_boot.img"


def test_extract_target_and_header(tmp_path):
    src = open_source(make_block_ota_zip(tmp_path / "z"))
    out = src.extract_target(tmp_path / "boot.img")
    h = read_header(out)
    assert (h.version, h.os_version) == (1, (16, 0, 0))
    assert h.ramdisk_size > 0


def test_members(tmp_path):
    src = open_source(make_block_ota_zip(tmp_path / "z"))
    assert src.has_file("vbmeta.img")
    assert not src.has_file("nope")
    assert b"ota-type" in src.read_member("META-INF/com/android/metadata")
    assert src.read_member("nope") is None
    nodyn = open_source(make_block_ota_zip(tmp_path / "y", with_vbmeta=False, dynamic=False))
    assert not nodyn.has_file("vbmeta.img")
    assert nodyn.read_member("dynamic_partitions_op_list") is None


def test_partition_image_only_vendor(tmp_path, tmp_workdir):
    src = open_source(make_block_ota_zip(tmp_path / "z"))
    with pytest.raises(ValueError):
        src.partition_image("system", tmp_workdir)


def test_missing_vendor_returns_none(tmp_path, tmp_workdir):
    p = tmp_path / "z.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.write(make_boot_image(tmp_path / "boot.img"), "boot.img")
    src = open_source(p)
    assert src.partition_image("vendor", tmp_workdir) is None
    assert src.extracted_partitions() == []


def test_vendor_img_in_zip(tmp_path, tmp_workdir):
    from tests.helpers import make_vendor_image
    p = tmp_path / "z.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.write(make_boot_image(tmp_path / "boot.img"), "boot.img")
        z.write(make_vendor_image(tmp_path / "v.img", "ext4"), "vendor.img")
    src = open_source(p)
    img = src.partition_image("vendor", tmp_workdir)
    assert read_file(img, "/build.prop") is not None


def test_payload_bin_rejected(tmp_path):
    p = make_block_ota_zip(tmp_path / "z", extra_members={"payload.bin": b"CrAU"})
    with pytest.raises(UserError, match="payload.bin"):
        open_source(p)


def test_zip_without_boot_rejected(tmp_path):
    p = tmp_path / "z.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("foo", "bar")
    with pytest.raises(UserError):
        open_source(p)


def test_vendor_boot_rejected(tmp_path):
    p = make_boot_image(tmp_path / "vendor_boot.img")
    with pytest.raises(UserError):
        open_source(p)


def test_boot_image_only(tmp_path, tmp_workdir):
    p = make_boot_image(tmp_path / "my-boot.img")
    src = open_source(p)
    assert isinstance(src, BootImageOnly)
    assert src.target_image_name() == "boot.img"
    assert src.extract_target(tmp_path / "out.img").read_bytes() == p.read_bytes()
    assert src.read_member("anything") is None
    assert not src.has_file("vbmeta.img")
    assert src.partition_image("vendor", tmp_workdir) is None
    assert src.extracted_partitions() == []
    with pytest.raises(ValueError):
        src.partition_image("system", tmp_workdir)


def test_non_boot_file_rejected(tmp_path):
    p = tmp_path / "x.img"
    p.write_bytes(b"\0" * 4096)
    with pytest.raises(UserError):
        open_source(p)


def _broken_central_directory(tmp_path):
    z = make_block_ota_zip(tmp_path / "rom", name="broken.zip")
    data = z.read_bytes()
    cd = data.rindex(b"PK\x01\x02")  # a central directory header; the EOCD stays intact
    z.write_bytes(data[:cd] + b"XX" + data[cd + 2:])
    assert zipfile.is_zipfile(z)
    return z


def test_broken_central_directory_is_user_error(tmp_path):
    z = _broken_central_directory(tmp_path)
    with pytest.raises(UserError):
        open_source(z)
    with pytest.raises(UserError):
        BlockOtaZip(z)


def test_broken_zip_exit_code_1(tmp_path, capsys):
    from magisk_boot_patcher.cli import main

    z = _broken_central_directory(tmp_path)
    assert main(["inspect", str(z)]) == 1
    assert "Internal error" not in capsys.readouterr().err


def test_bare_init_boot_target_name(tmp_path):
    src = open_source(make_boot_image(tmp_path / "init_boot.img", header_version=4))
    assert src.target_image_name() == "init_boot.img"
    assert open_source(make_boot_image(tmp_path / "init_boot-old.img")).target_image_name() == "boot.img"
