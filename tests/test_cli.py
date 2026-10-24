import json
import os

import pytest

from magisk_boot_patcher.cli import main

from .helpers import DEFAULT_FSTAB, make_block_ota_zip, make_boot_image

CACHE_FSTAB = DEFAULT_FSTAB + "/dev/block/by-name/cache /cache ext4 noatime wait,check\n"


def test_inspect_prints_report(tmp_path, capsys):
    zip_path = make_block_ota_zip(tmp_path / "rom", fstab=CACHE_FSTAB)
    assert main(["inspect", str(zip_path)]) == 0
    out = capsys.readouterr().out
    assert "PREINITDEVICE" in out and "cache" in out
    assert "Magisk:" not in out


def test_inspect_json(tmp_path, capsys):
    zip_path = make_block_ota_zip(tmp_path / "rom")
    assert main(["inspect", str(zip_path), "--json", "--abi", "x86_64"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["magisk"] is None
    assert rep["settings"]["device_abi"]["value"] == "x86_64"
    assert rep["settings"]["device_abi"]["source"] == "--abi"


def test_inspect_override_only_set_options(tmp_path, capsys):
    zip_path = make_block_ota_zip(tmp_path / "rom")
    assert main(["inspect", str(zip_path), "--json", "--no-keep-verity"]) == 0
    s = json.loads(capsys.readouterr().out)["settings"]
    assert s["KEEPVERITY"]["source"] == "--no-keep-verity"
    assert s["KEEPFORCEENCRYPT"]["source"] != "--keep-force-encrypt"


def test_inspect_payload_is_user_error(tmp_path, capsys):
    import zipfile

    z = tmp_path / "ab.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("payload.bin", b"x")
    assert main(["inspect", str(z)]) == 1
    err = capsys.readouterr().err
    assert "payload.bin" in err and "Traceback" not in err


def test_missing_file_is_user_error(tmp_path, capsys):
    assert main(["inspect", str(tmp_path / "nope.zip")]) == 1


def test_inspect_relative_path_from_cwd(tmp_path, monkeypatch, capsys):
    make_block_ota_zip(tmp_path / "rom", name="rel.zip")
    monkeypatch.chdir(tmp_path / "rom")
    assert main(["inspect", "rel.zip"]) == 0


def test_tool_error_exit_2(tmp_path, monkeypatch, capsys):
    from magisk_boot_patcher import cli
    from magisk_boot_patcher.errors import ToolError

    def boom(*a, **k):
        raise ToolError("kaboom", ["x"], "log text")

    monkeypatch.setattr(cli, "detect_settings", boom)
    zip_path = make_block_ota_zip(tmp_path / "rom")
    assert main(["inspect", str(zip_path)]) == 2
    err = capsys.readouterr().err
    assert "kaboom" in err and "log text" in err and "Traceback" not in err


def test_workdir_removed(tmp_path, monkeypatch):
    zip_path = make_block_ota_zip(tmp_path / "rom")
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp))
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", None)
    assert main(["inspect", str(zip_path)]) == 0
    assert list(tmp.iterdir()) == []


@pytest.mark.network
def test_patch_zip(tmp_path, capsys, monkeypatch, magisk_args):
    monkeypatch.setenv("MBP_CACHE_DIR", str(tmp_path / "cache"))
    zip_path = make_block_ota_zip(tmp_path / "rom")
    out = tmp_path / "out"
    rc = main(["patch", str(zip_path), "--out", str(out), "--preinit-device", "metadata", *magisk_args])
    assert rc == 0
    imgs = list(out.glob("boot-magisk-v*.img"))
    assert len(imgs) == 1
    stem = imgs[0].stem
    rep = json.loads((out / f"{stem}.report.json").read_text())
    assert rep["settings"]["PREINITDEVICE"]["source"] == "--preinit-device"
    assert rep["sha256"]["patched"]
    assert (out / f"{stem}.report.txt").exists()
    assert oct(imgs[0].stat().st_mode & 0o777) == "0o644"
    assert not list(out.glob("*.zip"))


@pytest.mark.network
def test_patch_repack_zip(tmp_path, capsys, monkeypatch, magisk_args):
    monkeypatch.setenv("MBP_CACHE_DIR", str(tmp_path / "cache"))
    zip_path = make_block_ota_zip(tmp_path / "rom")
    out = tmp_path / "out"
    assert main(["patch", str(zip_path), "--out", str(out), "--repack-zip", *magisk_args]) == 0
    zips = list(out.glob("rom-magisk-v*-unsigned.zip"))
    assert len(zips) == 1
    assert oct(zips[0].stat().st_mode & 0o777) == "0o644"
    assert "Unsigned zip" in capsys.readouterr().err


@pytest.mark.network
def test_patch_bare_init_boot_naming(tmp_path, monkeypatch, magisk_args):
    monkeypatch.setenv("MBP_CACHE_DIR", str(tmp_path / "cache"))
    img = make_boot_image(tmp_path / "init_boot.img", header_version=4)
    out = tmp_path / "out"
    assert main(["patch", str(img), "--out", str(out), *magisk_args]) == 0
    assert list(out.glob("init_boot-magisk-v*.img"))


@pytest.mark.network
@pytest.mark.skipif(os.geteuid() != 0, reason="chown needs root")
def test_outputs_chowned_to_dir_owner(tmp_path, monkeypatch, magisk_args):
    monkeypatch.setenv("MBP_CACHE_DIR", str(tmp_path / "cache"))
    zip_path = make_block_ota_zip(tmp_path / "rom")
    base = tmp_path / "base"
    base.mkdir()
    os.chown(base, 1234, 1234)
    out = base / "out"
    assert main(["patch", str(zip_path), "--out", str(out), "--repack-zip", *magisk_args]) == 0
    files = list(out.iterdir())
    assert len(files) == 4
    for p in [out, *files]:
        assert p.stat().st_uid == 1234 and p.stat().st_gid == 1234


def test_unexpected_exception_exit_2(tmp_path, monkeypatch, capsys):
    from magisk_boot_patcher import cli

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(cli, "detect_settings", boom)
    zip_path = make_block_ota_zip(tmp_path / "rom")
    assert main(["inspect", str(zip_path)]) == 2
    err = capsys.readouterr().err
    assert "disk full" in err and "Traceback" not in err
    with pytest.raises(OSError):
        main(["inspect", str(zip_path), "--debug"])


def test_workdir_removed_on_error(tmp_path, monkeypatch):
    import tempfile

    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp))
    monkeypatch.setattr(tempfile, "tempdir", None)
    assert main(["inspect", str(tmp_path / "nope.zip")]) == 1
    zip_path = make_block_ota_zip(tmp_path / "rom")
    from magisk_boot_patcher import cli

    monkeypatch.setattr(cli, "detect_settings", lambda *a: (_ for _ in ()).throw(OSError("x")))
    assert main(["inspect", str(zip_path)]) == 2
    assert list(tmp.iterdir()) == []


def test_repack_zip_with_bare_image_rejected_early(tmp_path, monkeypatch, capsys):
    from magisk_boot_patcher import cli

    def no_download(*a, **k):
        raise AssertionError("must not resolve the APK")

    monkeypatch.setattr(cli, "resolve_apk", no_download)
    img = make_boot_image(tmp_path / "boot.img")
    out = tmp_path / "out"
    assert main(["patch", str(img), "--out", str(out), "--repack-zip"]) == 1
    assert "--repack-zip" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.network
@pytest.mark.skipif(os.geteuid() != 0, reason="chown needs root")
def test_outputs_finalized_when_repack_fails(tmp_path, monkeypatch, magisk_args):
    from magisk_boot_patcher import cli

    monkeypatch.setenv("MBP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(cli, "repack_zip", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    zip_path = make_block_ota_zip(tmp_path / "rom")
    base = tmp_path / "base"
    base.mkdir()
    os.chown(base, 1234, 1234)
    out = base / "out"
    assert main(["patch", str(zip_path), "--out", str(out), "--repack-zip", *magisk_args]) == 2
    files = list(out.iterdir())
    assert files
    for p in [out, *files]:
        assert p.stat().st_uid == 1234
    assert all(oct(p.stat().st_mode & 0o777) == "0o644" for p in files)


@pytest.mark.network
def test_patch_config_mismatch_is_tool_error(tmp_path, monkeypatch, capsys, magisk_args):
    from magisk_boot_patcher import cli

    real = cli.verify_patched

    def tampered(*a, **k):
        return {**real(*a, **k), "KEEPVERITY": "false"}

    monkeypatch.setattr(cli, "verify_patched", tampered)
    zip_path = make_block_ota_zip(tmp_path / "rom")
    out = tmp_path / "out"
    assert main(["patch", str(zip_path), "--out", str(out), *magisk_args]) == 2
    assert "KEEPVERITY" in capsys.readouterr().err
    assert not out.exists()


def test_inspect_bare_init_boot_target(tmp_path, capsys):
    img = make_boot_image(tmp_path / "init_boot.img", header_version=4)
    assert main(["inspect", str(img), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["target_image"] == "init_boot.img"
    sources = [s["source"] for s in rep["settings"].values()]
    assert "init_boot.img:ramdisk_size" in sources
    assert not [s for s in sources if s.startswith("boot.img:")]
