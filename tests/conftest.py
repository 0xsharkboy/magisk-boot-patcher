import os

import pytest


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
