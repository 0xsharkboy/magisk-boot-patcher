import os
from pathlib import Path

import pytest

from magisk_boot_patcher.detect import detect_settings
from magisk_boot_patcher.rom.source import open_source


@pytest.mark.reference
def test_reference_detection(tmp_path):
    src = open_source(Path(os.environ["MBP_REFERENCE_ROM"]))
    s = detect_settings(src, tmp_path)
    for name, f in vars(s).items():
        print(f"{name} = {f.value!r}  [{f.source}] {f.reason}{' (WARNING)' if f.warning else ''}")
    assert (
        s.keep_verity.value,
        s.keep_force_encrypt.value,
        s.patch_vbmeta_flag.value,
        s.legacy_sar.value,
        s.preinit_device.value,
        s.device_abi.value,
    ) == (True, True, False, False, "cache", "arm64-v8a")
    assert s.recovery_mode.value is False
    assert s.sdk.value == 36
    assert s.warnings() == []
    assert src.extracted_partitions() == ["vendor"]


@pytest.mark.reference
@pytest.mark.network
def test_reference_patch(tmp_path, magisk_apk):
    from magisk_boot_patcher.magisk_apk import host_abi, stage_apk
    from magisk_boot_patcher.patcher import patch_boot, verify_patched

    src = open_source(Path(os.environ["MBP_REFERENCE_ROM"]))
    s = detect_settings(src, tmp_path)
    boot = src.extract_target(tmp_path / "rom-boot")
    staged = stage_apk(magisk_apk, tmp_path / "staged", host_abi(), s.device_abi.value)
    res = patch_boot(boot, s, staged)
    print(res.log)
    cfg = verify_patched(res.image, staged)
    print(cfg)
    assert cfg["PREINITDEVICE"] == "cache"
    assert cfg["KEEPVERITY"] == "true" and cfg["RECOVERYMODE"] == "false"
