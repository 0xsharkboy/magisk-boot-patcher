import subprocess

import pytest

from magisk_boot_patcher.errors import ToolError, UserError
from magisk_boot_patcher.fsimage import (
    UnsupportedImage,
    detect_fs,
    list_dir,
    read_file,
    unsparse_if_needed,
)


@pytest.fixture
def tree(tmp_path):
    d = tmp_path / "tree"
    (d / "etc").mkdir(parents=True)
    (d / "etc" / "fstab.test").write_text("fstab content\n")
    (d / "build.prop").write_text("ro.x=1\n")
    return d


def _check(img):
    assert read_file(img, "/etc/fstab.test") == b"fstab content\n"
    assert read_file(img, "/build.prop") == b"ro.x=1\n"
    assert read_file(img, "/nope") is None
    assert read_file(img, "/etc/nope") is None
    assert "fstab.test" in list_dir(img, "/etc")
    assert "build.prop" in list_dir(img, "/")


def test_read_file_erofs(tmp_path, tree):
    img = tmp_path / "e.img"
    subprocess.run(["mkfs.erofs", str(img), str(tree)], check=True, capture_output=True)
    assert detect_fs(img) == "erofs"
    _check(img)


def test_read_file_ext4(tmp_path, tree):
    img = tmp_path / "x.img"
    subprocess.run(["mke2fs", "-q", "-t", "ext4", "-d", str(tree), str(img), "8M"],
                   check=True, capture_output=True)
    assert detect_fs(img) == "ext4"
    _check(img)


def test_detect_fs_rejects_garbage(tmp_path):
    img = tmp_path / "g.img"
    img.write_bytes(b"\x01" * 8192)
    with pytest.raises(UnsupportedImage):
        detect_fs(img)
    assert issubclass(UnsupportedImage, UserError)


def test_detect_fs_rejects_short_file(tmp_path):
    img = tmp_path / "s.img"
    img.write_bytes(b"abc")
    with pytest.raises(UnsupportedImage):
        detect_fs(img)


def test_unsparse_noop_for_raw(tmp_path):
    img = tmp_path / "r.img"
    img.write_bytes(b"\0" * 2048)
    assert unsparse_if_needed(img) == img


def test_unsparse_sparse(tmp_path, tree):
    raw = tmp_path / "x.img"
    subprocess.run(["mke2fs", "-q", "-t", "ext4", "-d", str(tree), str(raw), "8M"],
                   check=True, capture_output=True)
    sparse = tmp_path / "x.simg"
    subprocess.run(["img2simg", str(raw), str(sparse)], check=True, capture_output=True)
    out = unsparse_if_needed(sparse)
    assert out != sparse
    assert out.read_bytes() == raw.read_bytes()


def test_tool_failure_raises_tool_error(tmp_path):
    from magisk_boot_patcher.tools import run_tool

    with pytest.raises(ToolError) as ei:
        run_tool(["dump.erofs", "--cat", "--path=/x", str(tmp_path / "missing.img")])
    assert "missing.img" in str(ei.value)
    assert ei.value.stderr
    assert ei.value.exit_code == 2
