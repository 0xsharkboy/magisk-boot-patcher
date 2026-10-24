"""Run the official Magisk boot_patch.sh on the host and verify the result."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import ToolError, UserError
from .model import Settings
from .tools import run_tool

# Files put in the working directory by stage_apk; everything else is leftover.
_STAGED = {
    "boot_patch.sh", "util_functions.sh", "stub.apk", "magiskboot", "busybox",
    "magiskinit", "magisk", "init-ld",
}
_OUT_DIR = "out"
_VERIFY_DIR = "verify"


@dataclass
class PatchResult:
    image: Path
    log: str
    repatched: bool


def _clean(staged: Path) -> None:
    for entry in staged.iterdir():
        if entry.name in _STAGED:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _getprop_script(settings: Settings) -> str:
    sdk = settings.sdk.value
    return (
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  ro.product.cpu.abi) echo '{settings.device_abi.value}' ;;\n"
        f"  ro.build.version.sdk) echo '{'' if sdk is None else sdk}' ;;\n"
        "  *) echo '' ;;\n"
        "esac\n"
    )


def patch_boot(boot: Path, settings: Settings, staged: Path) -> PatchResult:
    staged = Path(staged).resolve()
    # _clean() deletes everything else in `staged`: make sure it really is a stage_apk dir
    # and that the input does not live in it.
    if not all((staged / n).is_file() for n in ("boot_patch.sh", "magiskboot")):
        raise ValueError(f"{staged} is not a staged Magisk directory")
    if Path(boot).resolve().is_relative_to(staged):
        raise ValueError(f"boot image {boot} is inside the staged directory {staged}")
    _clean(staged)
    shutil.copyfile(boot, staged / "boot.img")
    bindir = staged / "bin"
    bindir.mkdir()
    run_tool([staged / "busybox", "--install", "-s", bindir], cwd=staged)
    getprop = bindir / "getprop"
    if getprop.is_symlink() or getprop.exists():
        getprop.unlink()
    getprop.write_text(_getprop_script(settings))
    getprop.chmod(0o755)

    env = {"PATH": str(bindir), "BOOTMODE": "true", **settings.to_env()}
    try:
        proc = subprocess.run(
            [str(staged / "busybox"), "sh", "boot_patch.sh", "boot.img"],
            cwd=staged, env=env, capture_output=True, text=True, errors="replace",
        )
    except OSError as e:
        raise ToolError(f"cannot run boot_patch.sh: {e}") from e
    log = proc.stdout + proc.stderr
    if "Boot image patched by unsupported programs" in log:
        raise UserError("boot image already modified by another tool, provide the original boot image")
    new_boot = staged / "new-boot.img"
    if proc.returncode != 0 or not new_boot.exists():
        raise ToolError(
            f"boot_patch.sh failed with exit code {proc.returncode}", ["boot_patch.sh"], log
        )
    out = staged / _OUT_DIR
    out.mkdir()
    image = out / "new-boot.img"
    shutil.move(new_boot, image)
    return PatchResult(image, log, "Magisk patched boot image detected" in log)


def verify_patched(image: Path, staged: Path) -> dict[str, str]:
    staged = Path(staged).resolve()
    magiskboot = staged / "magiskboot"
    work = staged / _VERIFY_DIR
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    img = Path(image).resolve()
    run_tool([magiskboot, "unpack", img], cwd=work)
    test = run_tool([magiskboot, "cpio", "ramdisk.cpio", "test"], check=False, cwd=work)
    if test.returncode != 1:
        raise ToolError(
            f"patched boot is not recognised as Magisk-patched (cpio test={test.returncode})"
        )
    run_tool([magiskboot, "cpio", "ramdisk.cpio", "extract .backup/.magisk cfg"], cwd=work)
    cfg = work / "cfg"
    os.chmod(cfg, 0o644)
    result = {}
    for line in cfg.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k] = v
    return result
