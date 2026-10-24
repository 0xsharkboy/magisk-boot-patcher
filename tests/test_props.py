from pathlib import Path

from magisk_boot_patcher.model import Finding, Settings
from magisk_boot_patcher.props import parse_props

FIXTURE = Path(__file__).parent / "fixtures" / "vendor_build.prop"


def test_parse_props_fixture():
    props = parse_props(FIXTURE.read_text())
    assert props["ro.vendor.build.version.sdk"] == "36"
    assert props["ro.vendor.product.cpu.abilist"] == "arm64-v8a,armeabi-v7a,armeabi"
    assert not any(k.startswith("import") for k in props)


def test_last_value_wins_and_comments_ignored():
    assert parse_props("# a=1\na=1\nb=x=y\na=2\n") == {"a": "2", "b": "x=y"}


def _settings(preinit="cache", warn=False):
    f = lambda v: Finding(v, "src", "why", warn)  # noqa: E731
    return Settings(
        keep_verity=f(True),
        keep_force_encrypt=f(True),
        patch_vbmeta_flag=f(False),
        legacy_sar=f(False),
        recovery_mode=f(False),
        preinit_device=Finding(preinit, "src", "why", warn),
        device_abi=f("arm64-v8a"),
        sdk=f(36),
    )


def test_to_env():
    assert _settings().to_env() == {
        "KEEPVERITY": "true",
        "KEEPFORCEENCRYPT": "true",
        "PATCHVBMETAFLAG": "false",
        "RECOVERYMODE": "false",
        "LEGACYSAR": "false",
        "PREINITDEVICE": "cache",
    }


def test_to_env_omits_none_preinit():
    assert "PREINITDEVICE" not in _settings(preinit=None).to_env()


def test_warnings():
    assert _settings().warnings() == []
    w = _settings(warn=True).warnings()
    assert len(w) == 8 and all(isinstance(s, str) for s in w)
