import hashlib
import json
from pathlib import Path

from magisk_boot_patcher.magisk_apk import MagiskApk
from magisk_boot_patcher.model import Finding, Settings
from magisk_boot_patcher.report import build_report, render_text, write_report


def _settings() -> Settings:
    return Settings(
        keep_verity=Finding(True, "fstab", "avb flag"),
        keep_force_encrypt=Finding(True, "fstab", "fileencryption"),
        patch_vbmeta_flag=Finding(False, "vbmeta", "present"),
        legacy_sar=Finding(False, "cmdline", "no skip_initramfs"),
        recovery_mode=Finding(False, "default", "n/a"),
        preinit_device=Finding("cache", "fstab", "cache partition"),
        device_abi=Finding("arm64-v8a", "vendor build.prop", "ro.product.cpu.abi"),
        sdk=Finding(None, "not found", "missing", warning=True),
    )


def _build(tmp_path: Path, apk: MagiskApk | None = None) -> tuple[dict, Path, Path]:
    a, b = tmp_path / "a.img", tmp_path / "b.img"
    a.write_bytes(b"original")
    b.write_bytes(b"patched")
    rep = build_report(
        input_path=tmp_path / "rom.zip", target_name="boot.img", original=a, patched=b,
        settings=_settings(), apk=apk, extracted=["boot", "vendor"], tool_version="1.2.3",
    )
    return rep, a, b


def test_build_report(tmp_path):
    apk = MagiskApk(tmp_path / "m.apk", "30.0", 30000, "ab" * 32)
    rep, _, _ = _build(tmp_path, apk)
    assert rep["tool_version"] == "1.2.3"
    assert rep["target_image"] == "boot.img"
    assert rep["magisk"] == {"version": "30.0", "version_code": 30000, "apk_sha256": "ab" * 32}
    assert rep["settings"]["PREINITDEVICE"]["source"] == "fstab"
    assert rep["settings"]["PREINITDEVICE"]["value"] == "cache"
    assert rep["settings"]["sdk"]["warning"] is True
    assert rep["settings"]["device_abi"]["value"] == "arm64-v8a"
    assert rep["extracted_partitions"] == ["boot", "vendor"]
    assert rep["sha256"]["original"] == hashlib.sha256(b"original").hexdigest()
    assert rep["sha256"]["patched"] == hashlib.sha256(b"patched").hexdigest()
    assert len(rep["warnings"]) == 1 and "sdk" in rep["warnings"][0]
    json.dumps(rep)


def test_inspect_report(tmp_path):
    a = tmp_path / "a.img"
    a.write_bytes(b"x")
    rep = build_report(
        input_path=tmp_path / "rom.zip", target_name="boot.img", original=a, patched=None,
        settings=_settings(), apk=None, extracted=[], tool_version="1",
    )
    assert rep["magisk"] is None
    assert rep["sha256"]["patched"] is None


def test_render_text_and_write(tmp_path):
    rep, _, _ = _build(tmp_path)
    text = render_text(rep)
    assert "PREINITDEVICE" in text and "cache" in text
    assert "Warnings" in text
    j, t = write_report(rep, tmp_path / "out", "rom")
    assert j.name == "rom.report.json" and t.name == "rom.report.txt"
    assert json.loads(j.read_text()) == rep
    assert t.read_text() == text
