"""Command line: ``inspect`` and ``patch``."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from . import __version__
from .detect import apply_overrides, detect_settings
from .errors import ToolError, UserError
from .magisk_apk import host_abi, resolve_apk, stage_apk
from .model import Settings
from .patcher import check_config, patch_boot, verify_patched
from .repack import repack_zip
from .report import build_report, render_text, write_report
from .rom.source import RomSource, open_source
from .rom.boot_only import BootImageOnly

UNSIGNED_WARNING = "Unsigned zip: requires a recovery that does not verify signatures"

_BOOL_OVERRIDES = {
    "keep_verity": "--keep-verity",
    "keep_force_encrypt": "--keep-force-encrypt",
    "patch_vbmeta_flag": "--patch-vbmeta-flag",
    "legacy_sar": "--legacy-sar",
    "recovery_mode": "--recovery-mode",
}


def _add_overrides(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("forced settings (override detection)")
    for dest, flag in _BOOL_OVERRIDES.items():
        g.add_argument(flag, dest=dest, action=argparse.BooleanOptionalAction, default=None)
    g.add_argument("--preinit-device", dest="preinit_device", metavar="NAME", default=None)
    g.add_argument("--abi", dest="device_abi", metavar="ABI", default=None,
                   help="device ABI (e.g. arm64-v8a)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="magisk-boot-patcher")
    parser.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--debug", action="store_true", help="show error tracebacks")
    sub = parser.add_subparsers(dest="command", required=True)

    ins = sub.add_parser("inspect", parents=[common], help="show the detected settings")
    ins.add_argument("input")
    ins.add_argument("--json", action="store_true", help="print the report as JSON")
    _add_overrides(ins)

    pat = sub.add_parser("patch", parents=[common], help="patch the boot image with Magisk")
    pat.add_argument("input")
    pat.add_argument("--magisk", default="latest", metavar="SPEC",
                     help="latest, canary, a version or an .apk path (default: latest)")
    pat.add_argument("--out", default="out", metavar="DIR")
    pat.add_argument("--repack-zip", action="store_true",
                     help="also rebuild the ROM zip (unsigned)")
    _add_overrides(pat)
    return parser


def _cache_dir() -> Path:
    env = os.environ.get("MBP_CACHE_DIR")
    return Path(env) if env else Path.home() / ".cache" / "magisk-boot-patcher"


def _overrides(args: argparse.Namespace) -> dict[str, object]:
    names = [*_BOOL_OVERRIDES, "preinit_device", "device_abi"]
    return {n: getattr(args, n) for n in names if getattr(args, n) is not None}


def _detect(src: RomSource, args: argparse.Namespace, work: Path) -> Settings:
    (work / "detect").mkdir(exist_ok=True)
    settings = detect_settings(src, work / "detect")
    overrides = _overrides(args)
    if not overrides:
        return settings
    settings = apply_overrides(settings, overrides)
    if "device_abi" in overrides:  # the user-facing option is --abi
        f = settings.device_abi
        settings.device_abi = dataclasses.replace(f, source="--abi")
    return settings


def _extract_original(src: RomSource, work: Path) -> Path:
    orig = work / "original"
    orig.mkdir(exist_ok=True)
    return src.extract_target(orig / src.target_image_name())


def _image_stem(src: RomSource, input_path: Path) -> str:
    if isinstance(src, BootImageOnly):
        return "init_boot" if input_path.stem == "init_boot" else "boot"
    return Path(src.target_image_name()).stem


def _inspect(args: argparse.Namespace, work: Path) -> int:
    input_path = Path(args.input).resolve()
    src = open_source(input_path)
    settings = _detect(src, args, work)
    report = build_report(
        input_path=input_path, target_name=src.target_image_name(),
        original=_extract_original(src, work), patched=None, settings=settings,
        apk=None, extracted=src.extracted_partitions(), tool_version=__version__,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_text(report),
          end="" if not args.json else "\n")
    return 0


def _chown_like_parent(out: Path, paths: list[Path]) -> None:
    if os.geteuid() != 0:
        return
    st = out.parent.stat()
    for p in paths:
        os.chown(p, st.st_uid, st.st_gid)


def _patch(args: argparse.Namespace, work: Path) -> int:
    input_path = Path(args.input).resolve()
    out = Path(args.out).resolve()
    src = open_source(input_path)
    if args.repack_zip and not zipfile.is_zipfile(input_path):
        raise UserError("--repack-zip requires a ROM zip as input")
    settings = _detect(src, args, work)
    for w in settings.warnings():
        print(f"Warning: {w}", file=sys.stderr)

    apk = resolve_apk(args.magisk, _cache_dir())
    staged = stage_apk(apk, work / "staged", host_abi(), settings.device_abi.value)
    original = _extract_original(src, work)
    result = patch_boot(original, settings, staged)
    check_config(verify_patched(result.image, staged), settings)
    if result.repatched:
        print("Boot image already patched by Magisk: it was patched again.", file=sys.stderr)

    stem = f"{_image_stem(src, input_path)}-magisk-v{apk.version}"
    created = not out.exists()
    out.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    try:
        patched = out / f"{stem}.img"
        shutil.copyfile(result.image, patched)
        outputs.append(patched)

        report = build_report(
            input_path=input_path, target_name=f"{_image_stem(src, input_path)}.img",
            original=original, patched=patched, settings=settings, apk=apk,
            extracted=src.extracted_partitions(), tool_version=__version__,
        )
        outputs.extend(write_report(report, out, stem))

        if args.repack_zip:
            zip_out = out / f"{input_path.stem}-magisk-v{apk.version}-unsigned.zip"
            repack_zip(input_path, src.target_image_name(), patched, zip_out)
            outputs.append(zip_out)
            print(f"Warning: {UNSIGNED_WARNING}", file=sys.stderr)
    finally:
        # runs even when a later step failed, for the files written so far
        for p in outputs:
            p.chmod(0o644)
        if created:
            out.chmod(0o755)
            outputs.append(out)
        _chown_like_parent(out, outputs)

    print(render_text(report), end="")
    for p in outputs:
        if p is not out:
            print(f"Written: {p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="mbp-") as tmp:
            work = Path(tmp)
            return (_inspect if args.command == "inspect" else _patch)(args, work)
    except UserError as e:
        if args.debug:
            raise
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ToolError as e:
        if args.debug:
            raise
        print(f"Internal error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - keep the 0/1/2 exit-code contract
        if args.debug:
            raise
        print(f"Internal error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
