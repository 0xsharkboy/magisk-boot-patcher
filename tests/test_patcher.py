import re
import shutil

import pytest

from magisk_boot_patcher.errors import UserError
from magisk_boot_patcher.magisk_apk import host_abi, stage_apk
from magisk_boot_patcher.model import Finding, Settings
from magisk_boot_patcher.patcher import patch_boot, verify_patched

from .helpers import make_boot_image


def make_settings() -> Settings:
    def f(v):
        return Finding(v, "test", "test")

    return Settings(
        keep_verity=f(True),
        keep_force_encrypt=f(True),
        patch_vbmeta_flag=f(False),
        legacy_sar=f(False),
        recovery_mode=f(False),
        preinit_device=f("cache"),
        device_abi=f("arm64-v8a"),
        sdk=f(36),
    )


@pytest.fixture(scope="module")
def staged_apk(tmp_path_factory, magisk_apk):
    wd = tmp_path_factory.mktemp("apk") / "staged"
    stage_apk(magisk_apk, wd, host_abi(), "arm64-v8a")
    return wd


@pytest.fixture
def staged(staged_apk, tmp_path):
    wd = tmp_path / "staged"
    shutil.copytree(staged_apk, wd, symlinks=True)
    return wd


EXPECTED = {
    "KEEPVERITY": "true",
    "KEEPFORCEENCRYPT": "true",
    "RECOVERYMODE": "false",
    "VENDORBOOT": "false",
    "PREINITDEVICE": "cache",
}


@pytest.mark.network
def test_patch_and_verify(staged, tmp_path):
    boot = make_boot_image(tmp_path / "boot.img")
    res = patch_boot(boot, make_settings(), staged)
    assert res.image.exists() and res.repatched is False
    assert "Stock boot image detected" in res.log
    cfg = verify_patched(res.image, staged)
    sha1 = cfg.pop("SHA1")
    assert re.fullmatch(r"[0-9a-f]{40}", sha1)
    assert cfg == EXPECTED


@pytest.mark.network
def test_repatch_magisk_boot(staged, tmp_path):
    boot = make_boot_image(tmp_path / "boot.img")
    first = patch_boot(boot, make_settings(), staged)
    cfg1 = verify_patched(first.image, staged)
    kept = tmp_path / "patched.img"
    shutil.copy(first.image, kept)
    second = patch_boot(kept, make_settings(), staged)
    assert second.repatched is True
    assert verify_patched(second.image, staged) == cfg1


@pytest.mark.network
def test_third_party_patched_boot_fails(staged, tmp_path):
    boot = make_boot_image(
        tmp_path / "boot.img",
        ramdisk_files={"init": b"x", "sbin/launch_daemonsu.sh": b"#!/bin/sh\n"},
    )
    with pytest.raises(UserError, match="original boot image"):
        patch_boot(boot, make_settings(), staged)


def test_patch_boot_refuses_unstaged_dir(tmp_path):
    victim = tmp_path / "home"
    victim.mkdir()
    (victim / "precious.txt").write_text("keep me")
    (victim / "magiskboot").write_text("")  # boot_patch.sh missing
    boot = make_boot_image(tmp_path / "boot.img")
    with pytest.raises(ValueError, match="staged"):
        patch_boot(boot, make_settings(), victim)
    assert (victim / "precious.txt").read_text() == "keep me"


def test_patch_boot_refuses_boot_inside_staged(tmp_path):
    staged = tmp_path / "staged"
    (staged / "out").mkdir(parents=True)
    for name in ("boot_patch.sh", "magiskboot"):
        (staged / name).write_text("")
    boot = make_boot_image(staged / "out" / "boot.img")
    with pytest.raises(ValueError, match="inside"):
        patch_boot(boot, make_settings(), staged)
    assert boot.exists()
