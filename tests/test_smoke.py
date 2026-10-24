import shutil

import pytest


def test_import_and_tools():
    import magisk_boot_patcher

    assert magisk_boot_patcher.__version__ == "0.1.0"
    for tool in ("dump.erofs", "debugfs", "simg2img", "mkbootimg", "cpio"):
        assert shutil.which(tool) is not None, f"missing tool: {tool}"
