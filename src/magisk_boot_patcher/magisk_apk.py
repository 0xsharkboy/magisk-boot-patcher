"""Resolve, download, verify, cache and stage the official Magisk APK."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import OfflineError, UserError

API = "https://api.github.com/repos/topjohnwu/Magisk/releases"
MIN_VERSION_CODE = 28000
KNOWN_FLAGS = {"KEEPVERITY", "KEEPFORCEENCRYPT", "PATCHVBMETAFLAG", "RECOVERYMODE", "LEGACYSAR"}
TIMEOUT = 30
_FLAG_RE = re.compile(r"^\[ -z \$(\w+) \] && \1=")
_VERSION_RE = re.compile(r"^MAGISK_VER='?([^'\s]+)'?\s*$", re.M)
_CODE_RE = re.compile(r"^MAGISK_VER_CODE=['\"]?(\d+)", re.M)


@dataclass
class MagiskApk:
    path: Path
    version: str
    version_code: int
    sha256: str


def _github_fetch(url: str) -> bytes:
    headers = {"User-Agent": "magisk-boot-patcher", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        return resp.read()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_member(z: zipfile.ZipFile, name: str) -> str:
    try:
        return z.read(name).decode("utf-8", "replace").replace("\r", "")
    except KeyError:
        raise UserError(f"not a Magisk APK: {name} is missing") from None


def _inspect(path: Path) -> MagiskApk:
    try:
        with zipfile.ZipFile(path) as z:
            util = _read_member(z, "assets/util_functions.sh")
    except (zipfile.BadZipFile, OSError) as e:
        raise UserError(f"{path} is not a valid APK: {e}") from None
    ver, code = _VERSION_RE.search(util), _CODE_RE.search(util)
    if not ver or not code:
        raise UserError(f"{path}: cannot read MAGISK_VER / MAGISK_VER_CODE from util_functions.sh")
    version = ver.group(1)
    if not re.fullmatch(r"[\w.+-]+", version):  # it ends up in output file names
        raise UserError(f"{path}: invalid MAGISK_VER ({version!r})")
    return MagiskApk(path, version, int(code.group(1)), _sha256_file(path))


def _vkey(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", version))


# Cache sidecar ``Magisk-v<ver>.apk.sha256``: "<sha256> <release|prerelease>". An entry
# without the second word (older cache) has an unknown channel.
_RELEASE, _PRERELEASE = "release", "prerelease"


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def _write_sidecar(path: Path, sha256: str, prerelease: bool) -> None:
    _sidecar(path).write_text(f"{sha256} {_PRERELEASE if prerelease else _RELEASE}\n")


def _cached(cache: Path, version: str, *, stable_only: bool = False) -> MagiskApk | None:
    """Return the cached APK for `version` if present and intact.

    With `stable_only`, an APK not recorded as a stable release (canary, or unknown
    channel) is ignored.
    """
    path = cache / f"Magisk-v{version}.apk"
    try:
        record = _sidecar(path).read_text().split()
        if stable_only and record[1:2] != [_RELEASE]:
            return None
        if _sha256_file(path) != record[0]:
            return None
        return _inspect(path)
    except (OSError, IndexError, UserError):
        return None


def _newest_cached(cache: Path, *, stable_only: bool) -> MagiskApk | None:
    versions = [
        m.group(1)
        for p in cache.glob("Magisk-v*.apk")
        if (m := re.fullmatch(r"Magisk-v(.+)\.apk", p.name))
    ]
    for v in sorted(versions, key=_vkey, reverse=True):
        if (apk := _cached(cache, v, stable_only=stable_only)):
            return apk
    return None


def _rate_limited(err: Exception) -> bool:
    return isinstance(err, urllib.error.HTTPError) and err.code in (403, 429)


_TOKEN_HINT = (
    "set GITHUB_TOKEN (e.g. docker run -e GITHUB_TOKEN …) to raise the limit"
)


def _offline(spec: str, cache: Path, err: Exception) -> MagiskApk:
    apk = None
    if spec in ("latest", "canary"):
        apk = _newest_cached(cache, stable_only=spec == "latest")
    else:
        apk = _cached(cache, spec.removeprefix("v"))
    limited = _rate_limited(err)
    if apk is None:
        if limited:
            raise OfflineError(
                f"GitHub API rate limit reached ({err}) and no Magisk APK "
                f"cached for '{spec}'; {_TOKEN_HINT}, or pass the path of a local .apk"
            )
        raise OfflineError(
            f"cannot reach GitHub ({err}) and no cached Magisk APK for '{spec}'; "
            "go online once or pass a local .apk path"
        )
    if limited:
        print(
            f"Warning: GitHub API rate limit reached ({err}); "
            f"Magisk v{apk.version} taken from the cache; {_TOKEN_HINT}",
            file=sys.stderr,
        )
    else:
        print(
            f"warning: offline ({err}); using cached Magisk v{apk.version}",
            file=sys.stderr,
        )
    return apk


def _select_release(spec: str, fetch: Callable[[str], bytes]) -> dict:
    if spec in ("latest", "canary"):
        releases = json.loads(fetch(API))
        for r in releases:
            if spec == "canary" or not r.get("prerelease"):
                return r
        raise UserError("no matching Magisk release found on GitHub")
    tag = "v" + spec.removeprefix("v")
    try:
        return json.loads(fetch(f"{API}/tags/{tag}"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UserError(f"Magisk release {tag} does not exist") from None
        raise


def resolve_apk(
    spec: str, cache_dir: Path, *, fetch: Callable[[str], bytes] = _github_fetch
) -> MagiskApk:
    local = Path(spec)
    if spec.lower().endswith(".apk") or local.is_file():
        if not local.is_file():
            raise UserError(f"APK not found: {spec}")
        return _inspect(local)

    cache = Path(cache_dir) / "apk"
    try:
        release = _select_release(spec, fetch)
    except (OSError, ValueError) as e:  # URLError, timeouts, bad JSON are OSError/ValueError
        if isinstance(e, ValueError):
            e = OSError(f"invalid GitHub response: {e}")
        return _offline(spec, cache, e)

    version = release["tag_name"].removeprefix("v")
    name = f"Magisk-v{version}.apk"
    asset = next((a for a in release.get("assets", []) if a.get("name") == name), None)
    if asset is None:
        raise UserError(f"release v{version} has no asset {name}")
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise UserError(
            f"GitHub provides no sha256 digest for {name}; refusing an unverified download "
            "(download it yourself and pass the local .apk path)"
        )
    expected = digest.removeprefix("sha256:").lower()

    prerelease = bool(release.get("prerelease"))
    if (apk := _cached(cache, version)) and apk.sha256 == expected:
        _write_sidecar(apk.path, apk.sha256, prerelease)  # (re)record the channel
        return apk

    try:
        data = fetch(asset["browser_download_url"])
    except OSError as e:
        return _offline(spec, cache, e)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise UserError(f"sha256 mismatch for {name}: expected {expected}, got {actual}")

    cache.mkdir(parents=True, exist_ok=True)
    final = cache / name
    fd, tmp = tempfile.mkstemp(dir=cache, prefix=name + ".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        _inspect(Path(tmp))  # must really be a Magisk APK before it enters the cache
        os.replace(tmp, final)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _write_sidecar(final, actual, prerelease)
    return _inspect(final)


def host_abi() -> str:
    machine = platform.machine()
    if machine == "x86_64":
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "arm64-v8a"
    raise UserError(f"unsupported host architecture: {machine}")


def stage_apk(apk: MagiskApk, workdir: Path, host_abi: str, device_abi: str) -> Path:
    if apk.version_code < MIN_VERSION_CODE:
        raise UserError(
            f"Magisk {apk.version} (code {apk.version_code}) is too old; "
            f"need MAGISK_VER_CODE >= {MIN_VERSION_CODE}"
        )
    workdir = Path(workdir)
    # (member in the APK, destination name)
    members = [
        ("assets/boot_patch.sh", "boot_patch.sh"),
        ("assets/util_functions.sh", "util_functions.sh"),
        (f"lib/{host_abi}/libmagiskboot.so", "magiskboot"),
        (f"lib/{host_abi}/libbusybox.so", "busybox"),
        (f"lib/{device_abi}/libmagiskinit.so", "magiskinit"),
        (f"lib/{device_abi}/libmagisk.so", "magisk"),
        (f"lib/{device_abi}/libinit-ld.so", "init-ld"),
    ]
    try:
        with zipfile.ZipFile(apk.path) as z:
            names = set(z.namelist())
            missing = [m for m, _ in members if m not in names]
            if missing:
                raise UserError("Magisk APK is missing: " + ", ".join(missing))
            if "assets/stub.apk" in names:
                members.append(("assets/stub.apk", "stub.apk"))

            script = _read_member(z, "assets/boot_patch.sh")
            unknown = sorted(
                {m.group(1) for line in script.split("\n") if (m := _FLAG_RE.match(line))}
                - KNOWN_FLAGS
            )
            if unknown:
                raise UserError(
                    "boot_patch.sh of this Magisk version uses unknown flag variable(s): "
                    + ", ".join(unknown)
                    + "; this tool does not support it yet"
                )

            workdir.mkdir(parents=True, exist_ok=True)
            for member, dest in members:
                target = workdir / dest
                if target.is_symlink() or target.exists():
                    target.unlink()
                with z.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                target.chmod(0o755)
    except (zipfile.BadZipFile, OSError) as e:
        raise UserError(f"cannot read Magisk APK {apk.path}: {e}") from None
    return workdir
