"""Patch report: structured dict, plain-text rendering and file output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .magisk_apk import MagiskApk
from .model import Settings

_ENV_NAMES = {
    "keep_verity": "KEEPVERITY",
    "keep_force_encrypt": "KEEPFORCEENCRYPT",
    "patch_vbmeta_flag": "PATCHVBMETAFLAG",
    "recovery_mode": "RECOVERYMODE",
    "legacy_sar": "LEGACYSAR",
    "preinit_device": "PREINITDEVICE",
    "device_abi": "device_abi",
    "sdk": "sdk",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_report(
    *,
    input_path: Path,
    target_name: str,
    original: Path,
    patched: Path | None,
    settings: Settings,
    apk: MagiskApk | None,
    extracted: list[str],
    tool_version: str,
) -> dict:
    return {
        "tool_version": tool_version,
        "input": str(input_path),
        "target_image": target_name,
        "magisk": None
        if apk is None
        else {"version": apk.version, "version_code": apk.version_code, "apk_sha256": apk.sha256},
        "settings": {
            _ENV_NAMES[attr]: {
                "value": f.value,
                "source": f.source,
                "reason": f.reason,
                "warning": f.warning,
            }
            for attr, f in vars(settings).items()
        },
        "warnings": settings.warnings(),
        "extracted_partitions": list(extracted),
        "sha256": {
            "original": _sha256(original),
            "patched": None if patched is None else _sha256(patched),
        },
    }


def render_text(report: dict) -> str:
    lines = [f"magisk-boot-patcher {report['tool_version']}", ""]
    lines.append(f"Input: {report['input']}")
    lines.append(f"Target image: {report['target_image']}")
    m = report["magisk"]
    if m:
        lines.append(f"Magisk: {m['version']} (code {m['version_code']}, sha256 {m['apk_sha256']})")
    if report["extracted_partitions"]:
        lines.append("Extracted partitions: " + ", ".join(report["extracted_partitions"]))
    lines.append(f"Original SHA-256: {report['sha256']['original']}")
    if report["sha256"]["patched"]:
        lines.append(f"Patched SHA-256: {report['sha256']['patched']}")
    lines += ["", "Settings", ""]
    rows = [("Setting", "Value", "Source", "Reason")]
    for name, s in report["settings"].items():
        value = "(not set)" if s["value"] is None else str(s["value"]).lower() \
            if isinstance(s["value"], bool) else str(s["value"])
        rows.append((name + (" (!)" if s["warning"] else ""), value, s["source"], s["reason"]))
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    for r in rows:
        lines.append(
            "  ".join(r[i].ljust(widths[i]) for i in range(3)) + "  " + r[3]
        )
    if report["warnings"]:
        lines += ["", "Warnings", ""]
        lines += [f"- {w}" for w in report["warnings"]]
    return "\n".join(lines) + "\n"


def write_report(report: dict, out_dir: Path, stem: str) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    j = out_dir / f"{stem}.report.json"
    t = out_dir / f"{stem}.report.txt"
    j.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    t.write_text(render_text(report), encoding="utf-8")
    return j, t
