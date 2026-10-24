import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from magisk_boot_patcher.errors import UserError
from magisk_boot_patcher.magisk_apk import (
    MagiskApk,
    host_abi,
    resolve_apk,
    stage_apk,
)

FLAGS = (
    "[ -z $KEEPVERITY ] && KEEPVERITY=false\n"
    "[ -z $KEEPFORCEENCRYPT ] && KEEPFORCEENCRYPT=false\n"
    "[ -z $PATCHVBMETAFLAG ] && PATCHVBMETAFLAG=false\n"
    "[ -z $RECOVERYMODE ] && RECOVERYMODE=false\n"
    "[ -z $LEGACYSAR ] && LEGACYSAR=false\n"
)
LIBS = ["busybox", "init-ld", "magisk", "magiskboot", "magiskinit", "magiskpolicy"]
ABIS = ["arm64-v8a", "armeabi-v7a", "x86", "x86_64"]


def make_apk(ver="30.7", code=30700, boot_patch=FLAGS, crlf=False, stub=True) -> bytes:
    util = f"MAGISK_VER='{ver}'\nMAGISK_VER_CODE={code}\n"
    if crlf:
        util = util.replace("\n", "\r\n")
        boot_patch = boot_patch.replace("\n", "\r\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("assets/util_functions.sh", util)
        z.writestr("assets/boot_patch.sh", boot_patch)
        if stub:
            z.writestr("assets/stub.apk", b"stub")
        for abi in ABIS:
            for lib in LIBS:
                z.writestr(f"lib/{abi}/lib{lib}.so", f"{abi}:{lib}".encode())
    return buf.getvalue()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class FakeGithub:
    def __init__(self, apks: dict[str, bytes], prerelease=("31.0",), digest=True):
        self.apks = apks
        self.calls: list[str] = []
        releases = []
        for ver, data in sorted(apks.items(), reverse=True):
            asset = {
                "name": f"Magisk-v{ver}.apk",
                "browser_download_url": f"https://dl.example/Magisk-v{ver}.apk",
            }
            if digest:
                asset["digest"] = "sha256:" + sha(data)
            releases.append(
                {
                    "tag_name": f"v{ver}",
                    "prerelease": ver in prerelease,
                    "assets": [asset, {"name": "app-debug.apk", "browser_download_url": "x"}],
                }
            )
        self.releases = releases

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url.endswith("/releases"):
            return json.dumps(self.releases).encode()
        if "/releases/tags/" in url:
            tag = url.rsplit("/", 1)[1]
            for r in self.releases:
                if r["tag_name"] == tag:
                    return json.dumps(r).encode()
            raise OSError("404")
        if url.startswith("https://dl.example/"):
            ver = url.rsplit("Magisk-v", 1)[1][: -len(".apk")]
            return self.apks[ver]
        raise AssertionError(url)


def offline(url):
    raise OSError("offline")


@pytest.fixture
def gh():
    return FakeGithub({"30.7": make_apk("30.7", 30700), "31.0": make_apk("31.0", 31000)})


def test_resolve_latest_skips_prerelease(gh, tmp_path):
    apk = resolve_apk("latest", tmp_path, fetch=gh)
    assert apk.version == "30.7" and apk.version_code == 30700
    assert apk.path == tmp_path / "apk" / "Magisk-v30.7.apk"
    assert apk.sha256 == sha(apk.path.read_bytes())
    assert (tmp_path / "apk" / "Magisk-v30.7.apk.sha256").read_text().strip() == apk.sha256


def test_resolve_canary_includes_prerelease(gh, tmp_path):
    assert resolve_apk("canary", tmp_path, fetch=gh).version == "31.0"


@pytest.mark.parametrize("spec", ["v30.7", "30.7"])
def test_resolve_explicit_version(gh, tmp_path, spec):
    assert resolve_apk(spec, tmp_path, fetch=gh).version == "30.7"


def test_resolve_unknown_version_is_user_error(gh, tmp_path):
    with pytest.raises(UserError):
        resolve_apk("v1.2", tmp_path, fetch=gh)


def test_bad_digest_rejected_and_not_cached(tmp_path):
    gh = FakeGithub({"30.7": make_apk()})
    gh.releases[0]["assets"][0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(UserError, match="sha"):
        resolve_apk("latest", tmp_path, fetch=gh)
    assert not list((tmp_path / "apk").glob("*")) if (tmp_path / "apk").exists() else True


def test_missing_digest_refused(tmp_path):
    gh = FakeGithub({"30.7": make_apk()}, digest=False)
    with pytest.raises(UserError, match="digest"):
        resolve_apk("latest", tmp_path, fetch=gh)


def test_cached_apk_not_redownloaded(gh, tmp_path):
    resolve_apk("latest", tmp_path, fetch=gh)
    gh.calls.clear()
    resolve_apk("latest", tmp_path, fetch=gh)
    assert not any(c.startswith("https://dl.example") for c in gh.calls)


def test_corrupt_cache_is_redownloaded(gh, tmp_path):
    apk = resolve_apk("latest", tmp_path, fetch=gh)
    apk.path.write_bytes(b"garbage")
    again = resolve_apk("latest", tmp_path, fetch=gh)
    assert again.sha256 == sha(make_apk("30.7", 30700))


def test_resolve_uses_cache_offline(gh, tmp_path, capsys):
    resolve_apk("latest", tmp_path, fetch=gh)
    resolve_apk("canary", tmp_path, fetch=gh)
    apk = resolve_apk("latest", tmp_path, fetch=offline)
    assert apk.version == "31.0"  # most recent cached
    assert "offline" in capsys.readouterr().err.lower()


def test_resolve_explicit_version_offline_cache(gh, tmp_path):
    resolve_apk("30.7", tmp_path, fetch=gh)
    assert resolve_apk("v30.7", tmp_path, fetch=offline).version == "30.7"


def test_resolve_offline_no_cache(tmp_path):
    with pytest.raises(UserError, match="offline|network"):
        resolve_apk("latest", tmp_path, fetch=offline)


def test_resolve_local_path(tmp_path):
    p = tmp_path / "my.apk"
    p.write_bytes(make_apk("30.7", 30700))
    apk = resolve_apk(str(p), tmp_path / "cache", fetch=offline)
    assert apk.path == p and apk.version == "30.7" and apk.sha256 == sha(p.read_bytes())


def test_resolve_local_garbage_is_user_error(tmp_path):
    p = tmp_path / "bad.apk"
    p.write_bytes(b"nope")
    with pytest.raises(UserError):
        resolve_apk(str(p), tmp_path, fetch=offline)


def test_version_parse_tolerates_crlf(tmp_path):
    p = tmp_path / "a.apk"
    p.write_bytes(make_apk(crlf=True))
    assert resolve_apk(str(p), tmp_path, fetch=offline).version_code == 30700


def _apk_obj(tmp_path, **kw) -> MagiskApk:
    p = tmp_path / "m.apk"
    p.write_bytes(make_apk(**kw))
    return resolve_apk(str(p), tmp_path, fetch=offline)


def test_stage_apk(tmp_path):
    apk = _apk_obj(tmp_path)
    wd = tmp_path / "wd"
    wd.mkdir()
    stage_apk(apk, wd, "x86_64", "arm64-v8a")
    expect = {
        "boot_patch.sh": None,
        "util_functions.sh": None,
        "stub.apk": b"stub",
        "magiskboot": b"x86_64:magiskboot",
        "busybox": b"x86_64:busybox",
        "magiskinit": b"arm64-v8a:magiskinit",
        "magisk": b"arm64-v8a:magisk",
        "init-ld": b"arm64-v8a:init-ld",
    }
    for name, content in expect.items():
        f = wd / name
        assert f.is_file() and not f.is_symlink(), name
        assert os.access(f, os.X_OK), name
        if content is not None:
            assert f.read_bytes() == content


def test_stage_without_stub(tmp_path):
    apk = _apk_obj(tmp_path, stub=False)
    wd = tmp_path / "wd"
    wd.mkdir()
    stage_apk(apk, wd, "x86_64", "arm64-v8a")
    assert not (wd / "stub.apk").exists()


def test_stage_old_magisk_refused(tmp_path):
    apk = _apk_obj(tmp_path, ver="27.0", code=27000)
    with pytest.raises(UserError, match="27000|28000"):
        stage_apk(apk, tmp_path, "x86_64", "arm64-v8a")


def test_stage_unknown_flag_refused(tmp_path):
    apk = _apk_obj(tmp_path, boot_patch=FLAGS + "[ -z $NEWFLAG ] && NEWFLAG=false\n")
    with pytest.raises(UserError, match="NEWFLAG"):
        stage_apk(apk, tmp_path, "x86_64", "arm64-v8a")


def test_stage_unknown_flag_with_crlf_refused(tmp_path):
    apk = _apk_obj(tmp_path, boot_patch=FLAGS + "[ -z $NEWFLAG ] && NEWFLAG=false\n", crlf=True)
    with pytest.raises(UserError, match="NEWFLAG"):
        stage_apk(apk, tmp_path, "x86_64", "arm64-v8a")


def test_stage_missing_lib_refused(tmp_path):
    apk = _apk_obj(tmp_path)
    with pytest.raises(UserError, match="mips"):
        stage_apk(apk, tmp_path, "mips", "arm64-v8a")


def test_host_abi(monkeypatch):
    import platform

    for machine, abi in [("x86_64", "x86_64"), ("aarch64", "arm64-v8a"), ("arm64", "arm64-v8a")]:
        monkeypatch.setattr(platform, "machine", lambda m=machine: m)
        assert host_abi() == abi
    monkeypatch.setattr(platform, "machine", lambda: "riscv64")
    with pytest.raises(UserError):
        host_abi()


@pytest.mark.network
def test_real_latest_apk(tmp_path):
    apk = resolve_apk("latest", tmp_path)
    assert apk.version_code >= 28000
    wd = tmp_path / "wd"
    wd.mkdir()
    stage_apk(apk, wd, "x86_64", "arm64-v8a")
    assert (wd / "magiskboot").stat().st_mode & 0o111
