from pathlib import Path

import pytest

from magisk_boot_patcher.detect import (
    apply_overrides,
    detect_settings,
    encryption_type,
    find_preinit_device,
)
from magisk_boot_patcher.fstab import FstabEntry, parse_fstab
from magisk_boot_patcher.rom.source import open_source
from tests.helpers import make_block_ota_zip, make_boot_image

FIXTURES = Path(__file__).parent / "fixtures"
REF_FSTAB = (FIXTURES / "fstab.exynos9820").read_text()
REF_PROPS = (FIXTURES / "vendor_build.prop").read_text()

DATA_EXT4_PLAIN = "/dev/block/by-name/userdata /data ext4 noatime wait,check\n"
METADATA_EXT4 = "/dev/block/by-name/omr /metadata ext4 noatime wait,first_stage_mount\n"
DATA_F2FS_FBE = (
    "/dev/block/by-name/userdata /data f2fs noatime "
    "wait,check,fileencryption=aes-256-xts:aes-256-cts:v2\n"
)


def _entries(text: str) -> list[FstabEntry]:
    return parse_fstab(text)


def _data(text: str) -> FstabEntry | None:
    return next((e for e in parse_fstab(text) if e.mount_point == "/data"), None)


# --- encryption_type -------------------------------------------------------


def test_encryption_type_reference_is_metadata():
    assert encryption_type(_data(REF_FSTAB)) == "metadata"


@pytest.mark.parametrize(
    "flags, expected",
    [
        ("wait,fileencryption=aes-256-xts", "file"),
        ("wait,forceencrypt=footer", "block"),
        ("wait,encryptable=footer", "block"),
        ("wait,forcefdeorfbe=footer", "block"),
        ("wait,check", "none"),
    ],
)
def test_encryption_type_flags(flags, expected):
    entry = _data(f"/dev/block/by-name/userdata /data ext4 noatime {flags}\n")
    assert encryption_type(entry) == expected


def test_encryption_type_keydirectory_is_metadata():
    entry = _data(
        "/dev/block/by-name/userdata /data f2fs noatime "
        "wait,fileencryption=aes-256-xts:aes-256-cts:v2,keydirectory=/metadata/vold/metadata_encryption\n"
    )
    assert encryption_type(entry) == "metadata"


def test_preinit_keydirectory_excludes_data():
    text = (
        "/dev/block/by-name/userdata /data ext4 noatime "
        "wait,fileencryption=aes-256-xts,keydirectory=/metadata/vold/metadata_encryption\n"
        "/dev/block/by-name/md /metadata f2fs noatime wait\n"
    )
    entries = _entries(text)
    assert find_preinit_device(entries, encryption_type(_data(text))) == "md"


def test_detect_keydirectory_reason(tmp_path):
    fstab = REF_FSTAB.replace("metadata_encryption=aes-256-xts,", "")
    assert "metadata_encryption=" not in fstab
    s = _detect(tmp_path, fstab=fstab, vendor_build_prop=REF_PROPS)
    assert s.keep_force_encrypt.value is True
    assert "metadata" in s.keep_force_encrypt.reason
    assert "keydirectory=" in s.keep_force_encrypt.reason
    assert s.preinit_device.value == "cache"


def test_encryption_type_without_data():
    assert encryption_type(None) == "none"


# --- find_preinit_device ---------------------------------------------------


def test_preinit_reference_is_cache():
    assert find_preinit_device(_entries(REF_FSTAB), "metadata") == "cache"


def test_preinit_without_cache_is_omr():
    text = "\n".join(l for l in REF_FSTAB.splitlines() if " /cache " not in l)
    assert find_preinit_device(_entries(text), "metadata") == "omr"


def test_preinit_unencrypted_ext4_data():
    entries = _entries(DATA_EXT4_PLAIN + METADATA_EXT4)
    assert find_preinit_device(entries, "none") == "userdata"


def test_preinit_ext4_beats_f2fs():
    entries = _entries(DATA_F2FS_FBE + METADATA_EXT4)
    assert find_preinit_device(entries, "file") == "omr"


def test_preinit_block_encryption_excludes_data():
    assert find_preinit_device(_entries(DATA_EXT4_PLAIN), "block") is None


def test_preinit_no_candidate():
    text = (
        "system /system erofs ro wait,logical,first_stage_mount\n"
        "/dev/block/by-name/efs /mnt/vendor/efs ext4 noatime wait\n"
        "/dev/block/by-name/cache /cache vfat noatime wait\n"
        "/dev/block/by-name/persist /persist ext4 ro wait\n"
        "/dev/block/by-name/klog /klogdump ext4 noatime wait,logical\n"
        "/dev/sda5 /metadata ext4 noatime wait\n"
    )
    assert find_preinit_device(_entries(text), "metadata") is None


def test_preinit_metadata_f2fs_beats_persist_ext4():
    # Magisk quirk: Metadata vs an ext4 partition is compared by enum order only.
    text = (
        "/dev/block/bootdevice/by-name/persist /mnt/vendor/persist ext4 noatime wait\n"
        "/dev/block/by-name/md /metadata f2fs noatime wait\n"
    )
    assert find_preinit_device(_entries(text), "metadata") == "md"


def test_preinit_mnt_vendor_persist():
    text = (
        "/dev/block/bootdevice/by-name/persist /mnt/vendor/persist ext4 noatime wait\n"
        "/dev/block/by-name/userdata /data f2fs noatime wait,fileencryption=aes\n"
    )
    assert find_preinit_device(_entries(text), "file") == "persist"


# --- detect_settings -------------------------------------------------------


def _detect(tmp_path, **kw):
    src = open_source(make_block_ota_zip(tmp_path / "z", **kw))
    work = tmp_path / "work"
    work.mkdir()
    return detect_settings(src, work)


def _values(s):
    return (
        s.keep_verity.value,
        s.keep_force_encrypt.value,
        s.patch_vbmeta_flag.value,
        s.legacy_sar.value,
        s.preinit_device.value,
        s.device_abi.value,
    )


def test_detect_reference_like_zip(tmp_path):
    s = _detect(tmp_path, fstab=REF_FSTAB, vendor_build_prop=REF_PROPS)
    assert _values(s) == (True, True, False, False, "cache", "arm64-v8a")
    assert s.recovery_mode.value is False
    assert s.sdk.value == 36
    assert s.sdk.source == "vendor:/build.prop:ro.vendor.build.version.sdk"
    assert s.keep_force_encrypt.source == "vendor:/etc/fstab.fake:/data"
    assert s.preinit_device.source == "vendor:/etc/fstab.fake:/cache"
    assert s.device_abi.source == "vendor:/build.prop:ro.vendor.product.cpu.abilist64"
    assert s.patch_vbmeta_flag.source == "zip:vbmeta.img"
    assert s.warnings() == []


def test_detect_default_props_fall_back_with_warnings(tmp_path):
    s = _detect(tmp_path, fstab=REF_FSTAB)
    assert _values(s) == (True, True, False, False, "cache", "arm64-v8a")
    assert s.sdk.value == 36
    assert s.sdk.source == "boot.img:os_version"
    assert s.device_abi.warning
    assert any(w.startswith("device_abi:") for w in s.warnings())


def test_detect_sdk_from_ota_metadata(tmp_path):
    s = _detect(
        tmp_path,
        fstab=REF_FSTAB,
        vendor_build_prop=REF_PROPS,
        metadata="ota-type=BLOCK\npost-sdk-level=35\n",
    )
    assert s.sdk.value == 35
    assert s.sdk.source == "zip:META-INF/com/android/metadata:post-sdk-level"


def test_multiple_fstabs_picks_data(tmp_path):
    s = _detect(
        tmp_path,
        fstab=None,
        vendor_build_prop=REF_PROPS,
        vendor_files={
            "etc/fstab.aaa": "system /system erofs ro wait,logical\n",
            "etc/fstab.zzz": REF_FSTAB,
        },
    )
    assert s.keep_force_encrypt.source == "vendor:/etc/fstab.zzz:/data"
    assert s.preinit_device.value == "cache"


def test_fstab_fallback_to_ramdisk(tmp_path):
    s = _detect(
        tmp_path,
        fstab=None,
        vendor_build_prop=REF_PROPS,
        boot_ramdisk_files={"init": b"x", "first_stage_ramdisk/fstab.test": REF_FSTAB.encode()},
    )
    assert s.keep_force_encrypt.value is True
    assert s.keep_force_encrypt.source == "boot.img:ramdisk:first_stage_ramdisk/fstab.test:/data"
    assert s.preinit_device.value == "cache"
    assert s.warnings() == []


def test_fstab_fallback_to_ramdisk_system_etc(tmp_path):
    # Layout of the reference boot ramdisk (fs_mgr also looks in system/etc/).
    s = _detect(
        tmp_path,
        fstab=None,
        vendor_build_prop=REF_PROPS,
        boot_ramdisk_files={"init": b"x", "system/etc/fstab.exynos9820": REF_FSTAB.encode()},
    )
    assert s.keep_force_encrypt.source == "boot.img:ramdisk:system/etc/fstab.exynos9820:/data"
    assert s.preinit_device.value == "cache"


def test_no_fstab_warns(tmp_path):
    s = _detect(tmp_path, fstab=None, vendor_build_prop=REF_PROPS)
    assert s.warnings()
    assert s.keep_force_encrypt.value is True
    assert s.keep_force_encrypt.warning
    assert s.preinit_device.value is None
    assert s.preinit_device.warning


def test_ramdisk_unsupported_compression_warns(tmp_path):
    s = _detect(tmp_path, fstab=None, vendor_build_prop=REF_PROPS, boot_ramdisk_compression="lz4")
    assert s.keep_force_encrypt.warning
    assert "lz4" in s.keep_force_encrypt.reason or "compress" in s.keep_force_encrypt.reason


def test_unencrypted_fstab(tmp_path):
    s = _detect(tmp_path, fstab=DATA_EXT4_PLAIN + METADATA_EXT4, vendor_build_prop=REF_PROPS)
    assert s.keep_force_encrypt.value is False
    assert s.preinit_device.value == "userdata"


def test_no_vbmeta_sets_patch_flag(tmp_path):
    s = _detect(tmp_path, fstab=REF_FSTAB, vendor_build_prop=REF_PROPS, with_vbmeta=False)
    assert s.patch_vbmeta_flag.value is True
    assert s.patch_vbmeta_flag.source == "zip:vbmeta.img (missing)"
    assert not s.patch_vbmeta_flag.warning


def test_updater_script_flashing_vbmeta(tmp_path):
    script = b'package_extract_file("vb.img", "/dev/block/by-name/vbmeta");\n'
    s = _detect(
        tmp_path,
        fstab=REF_FSTAB,
        vendor_build_prop=REF_PROPS,
        with_vbmeta=False,
        extra_members={"META-INF/com/google/android/updater-script": script},
    )
    assert s.patch_vbmeta_flag.value is False
    assert s.patch_vbmeta_flag.source == "zip:META-INF/com/google/android/updater-script"


@pytest.mark.parametrize("target", ["vbmeta_a", "vbmeta_b"])
def test_updater_script_flashing_vbmeta_slot(tmp_path, target):
    script = f'package_extract_file("vb.img", "/dev/block/by-name/{target}");\n'.encode()
    s = _detect(
        tmp_path,
        fstab=REF_FSTAB,
        vendor_build_prop=REF_PROPS,
        with_vbmeta=False,
        extra_members={"META-INF/com/google/android/updater-script": script},
    )
    assert s.patch_vbmeta_flag.value is False


def test_updater_script_vbmeta_system_only(tmp_path):
    script = b'package_extract_file("vbs.img", "/dev/block/by-name/vbmeta_system");\n'
    s = _detect(
        tmp_path,
        fstab=REF_FSTAB,
        vendor_build_prop=REF_PROPS,
        with_vbmeta=False,
        extra_members={"META-INF/com/google/android/updater-script": script},
    )
    assert s.patch_vbmeta_flag.value is True


def test_legacy_sar_without_ramdisk(tmp_path):
    props = "ro.vendor.build.version.sdk=29\nro.vendor.product.cpu.abilist64=arm64-v8a\n"
    s = _detect(
        tmp_path,
        fstab=REF_FSTAB,
        vendor_build_prop=props,
        dynamic=False,
        boot_cmdline="console=null",
        boot_ramdisk_compression="empty",
    )
    assert s.keep_verity.value is True
    assert s.legacy_sar.value is True


def test_not_sar_old_sdk(tmp_path):
    props = "ro.vendor.build.version.sdk=28\nro.vendor.product.cpu.abilist64=arm64-v8a\n"
    s = _detect(
        tmp_path,
        fstab=REF_FSTAB,
        vendor_build_prop=props,
        dynamic=False,
        boot_cmdline="console=null",
    )
    assert s.keep_verity.value is False
    assert s.legacy_sar.value is False


def test_dynamic_from_vendor_prop(tmp_path):
    props = (
        "ro.vendor.build.version.sdk=28\nro.boot.dynamic_partitions=true\n"
        "ro.vendor.product.cpu.abilist64=arm64-v8a\n"
    )
    s = _detect(
        tmp_path, fstab=REF_FSTAB, vendor_build_prop=props, dynamic=False, boot_cmdline="x=y"
    )
    assert s.keep_verity.value is True


def test_dynamic_from_boot_cmdline(tmp_path):
    props = "ro.vendor.build.version.sdk=28\nro.vendor.product.cpu.abilist64=arm64-v8a\n"
    s = _detect(tmp_path, fstab=REF_FSTAB, vendor_build_prop=props, dynamic=False)
    assert s.keep_verity.value is True


def test_abi_fallback_product_cpu_abi(tmp_path):
    props = "ro.vendor.build.version.sdk=36\nro.product.cpu.abi=armeabi-v7a\n"
    s = _detect(tmp_path, fstab=REF_FSTAB, vendor_build_prop=props)
    assert s.device_abi.value == "armeabi-v7a"
    assert not s.device_abi.warning


def test_boot_image_only(tmp_path):
    boot = make_boot_image(tmp_path / "boot.img", {"init": b"x", "fstab.test": REF_FSTAB.encode()})
    work = tmp_path / "work"
    work.mkdir()
    s = detect_settings(open_source(boot), work)
    assert s.patch_vbmeta_flag.value is False
    assert s.patch_vbmeta_flag.warning
    assert s.sdk.value == 36
    assert s.keep_force_encrypt.source == "boot.img:ramdisk:fstab.test:/data"
    assert s.preinit_device.value == "cache"
    assert s.device_abi.warning


def test_missing_vendor_warns(tmp_path):
    import zipfile

    p = tmp_path / "z.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.write(make_boot_image(tmp_path / "boot.img"), "boot.img")
        z.writestr("vbmeta.img", b"AVB0")
    work = tmp_path / "work"
    work.mkdir()
    s = detect_settings(open_source(p), work)
    assert s.keep_force_encrypt.warning
    assert "vendor" in s.keep_force_encrypt.reason
    assert s.device_abi.warning


def test_apply_overrides():
    from magisk_boot_patcher.model import Finding, Settings

    f = Finding(True, "x", "y")
    s = Settings(f, f, f, f, Finding(False, "default", "z"), Finding("cache", "x", "y"),
                 Finding("arm64-v8a", "x", "y"), Finding(36, "x", "y"))
    o = apply_overrides(s, {"preinit_device": "metadata", "keep_verity": False})
    assert o.preinit_device.value == "metadata"
    assert o.preinit_device.source == "--preinit-device"
    assert o.preinit_device.reason == "forced on the command line"
    assert o.keep_verity.value is False
    assert o.keep_verity.source == "--no-keep-verity"
    assert apply_overrides(s, {"legacy_sar": True}).legacy_sar.source == "--legacy-sar"
    assert s.preinit_device.value == "cache"
    with pytest.raises(ValueError):
        apply_overrides(s, {"nope": 1})


@pytest.mark.parametrize(
    "fstab",
    [
        None,
        "/dev/block/by-name/userdata /data f2fs noatime "
        "wait,fileencryption=aes-256-xts,keydirectory=/metadata/vold/metadata_encryption\n",
    ],
)
def test_undetected_preinit_warning_explains_consequence(tmp_path, fstab):
    s = _detect(tmp_path, fstab=fstab, vendor_build_prop=REF_PROPS)
    assert s.preinit_device.value is None and s.preinit_device.warning
    assert "not pick one at boot" in s.preinit_device.reason
    assert "--preinit-device" in s.preinit_device.reason
