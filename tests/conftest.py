import os

import pytest

from magisk_boot_patcher.errors import OfflineError
from magisk_boot_patcher.magisk_apk import MagiskApk, resolve_apk


def pytest_collection_modifyitems(config, items):
    if os.environ.get("MBP_REFERENCE_ROM"):
        return
    skip = pytest.mark.skip(reason="MBP_REFERENCE_ROM not set")
    for item in items:
        if "reference" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def tmp_workdir(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    return workdir


def resolve_or_skip(spec: str, cache_dir) -> MagiskApk:
    """resolve_apk(), skipping the test when GitHub is unreachable or rate-limited."""
    try:
        return resolve_apk(spec, cache_dir)
    except OfflineError as e:
        pytest.skip(f"Magisk APK unavailable ({e}); set MBP_TEST_APK to a local APK")


@pytest.fixture(scope="session")
def magisk_apk(tmp_path_factory) -> MagiskApk:
    """The Magisk APK used by the patch tests.

    ``MBP_TEST_APK`` (path to a local Magisk APK) makes them run offline; otherwise the
    latest release is downloaded once per session, and the tests are skipped (not failed)
    when GitHub is unreachable or rate-limited.
    """
    local = os.environ.get("MBP_TEST_APK")
    if local:
        return resolve_apk(local, tmp_path_factory.mktemp("cache"))
    return resolve_or_skip("latest", tmp_path_factory.mktemp("cache"))


@pytest.fixture
def magisk_args(magisk_apk) -> list[str]:
    """``patch`` arguments selecting the test APK (a local path: no network in the CLI)."""
    return ["--magisk", str(magisk_apk.path)]
