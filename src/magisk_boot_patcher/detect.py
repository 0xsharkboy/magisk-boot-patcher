"""Detect Magisk patch settings from ROM contents (offline port of Magisk's own logic).

Ported from ``util_functions.sh`` (``mount_partitions``, ``get_flags``) and
``native/src/core/mount.rs`` (``find_preinit_device``); see spec §5.2.
"""

import dataclasses
import functools
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .bootimg import BootHeader, ramdisk_format, read_header, read_ramdisk_files
from .fsimage import UnsupportedImage, list_dir, read_file
from .fstab import FstabEntry, parse_fstab
from .model import Finding, Settings
from .props import parse_props
from .rom.boot_only import BootImageOnly
from .rom.source import RomSource

Encryption = Literal["none", "block", "file", "metadata"]

# Order of Magisk's PartId enum (derives Ord): Data < Cache < Klogdump < Metadata < Persist.
DATA, CACHE, KLOGDUMP, METADATA, PERSIST = range(5)
_PART_IDS = {
    "/data": DATA,
    "/cache": CACHE,
    "/klogdump": KLOGDUMP,
    "/metadata": METADATA,
    "/persist": PERSIST,
    "/mnt/vendor/persist": PERSIST,
}

OTA_METADATA = "META-INF/com/android/metadata"
UPDATER_SCRIPT = "META-INF/com/google/android/updater-script"
_VBMETA_WRITE = re.compile(r"by-name/vbmeta(?![\w.-])")

# Ramdisk directories where fs_mgr (GetFstabPath) looks for the first-stage fstab.
_RAMDISK_FSTAB_DIRS = ("", "first_stage_ramdisk", "system/etc", "first_stage_ramdisk/system/etc")

# Android major version (boot header os_version) -> SDK level.
_OS_TO_SDK = {10: 29, 11: 30, 12: 31, 13: 33, 14: 34, 15: 35, 16: 36}


# --- fstab-level rules -------------------------------------------------------


def encryption_type(data: FstabEntry | None) -> Encryption:
    """Encryption type of ``/data`` from its fs_mgr flags."""
    if data is None:
        return "none"
    flags = data.fs_mgr_flags

    def has(prefix: str) -> bool:
        return any(f.startswith(prefix) for f in flags)

    if has("metadata_encryption="):
        return "metadata"
    if has("fileencryption="):
        return "file"
    if has("forceencrypt=") or has("encryptable=") or has("forcefdeorfbe="):
        return "block"
    return "none"


def _cmp(a: tuple[int, str], b: tuple[int, str]) -> int:
    """Exact port of the comparator used by Magisk's ``find_preinit_device``."""
    a_ext4, b_ext4 = a[1] == "ext4", b[1] == "ext4"
    if (a[0] == METADATA and b_ext4) or (b[0] == METADATA and a_ext4):
        return a[0] - b[0]
    if a_ext4 and not b_ext4:
        return -1
    if b_ext4 and not a_ext4:
        return 1
    return a[0] - b[0]


def _preinit_candidates(entries: list[FstabEntry], encrypt: Encryption) -> list[FstabEntry]:
    out = []
    for e in entries:
        part = _PART_IDS.get(e.mount_point)
        if part is None:
            continue
        if part == DATA and encrypt not in ("none", "file"):
            continue
        if e.fs_type not in ("ext4", "f2fs"):
            continue
        if "logical" in e.fs_mgr_flags or "ro" in e.mnt_flags:
            continue
        if "/by-name/" not in e.source and "/block/" not in e.source:
            continue
        out.append(e)
    return out


def _preinit_entry(entries: list[FstabEntry], encrypt: Encryption) -> FstabEntry | None:
    cands = _preinit_candidates(entries, encrypt)
    if not cands:
        return None

    def key(e: FstabEntry) -> tuple[int, str]:
        return _PART_IDS[e.mount_point], e.fs_type

    # select_nth_unstable_by(0, cmp) == the minimum according to cmp.
    return min(cands, key=functools.cmp_to_key(lambda a, b: _cmp(key(a), key(b))))


def find_preinit_device(entries: list[FstabEntry], encrypt: Encryption) -> str | None:
    """Name of the partition Magisk would pick as pre-init device, or None."""
    e = _preinit_entry(entries, encrypt)
    return posixpath.basename(e.source) if e else None


# --- overrides ---------------------------------------------------------------


def apply_overrides(s: Settings, overrides: dict[str, object]) -> Settings:
    """Return a copy of ``s`` where each overridden setting comes from the command line."""
    names = {f.name for f in dataclasses.fields(Settings)}
    changes = {}
    for name, value in overrides.items():
        if name not in names:
            raise ValueError(f"unknown setting {name!r}")
        changes[name] = Finding(value, "--" + name.replace("_", "-"), "forced on the command line")
    return dataclasses.replace(s, **changes)


# --- ROM-level detection -----------------------------------------------------


@dataclass
class _Fstab:
    label: str
    entries: list[FstabEntry]

    @property
    def data(self) -> FstabEntry | None:
        return next((e for e in self.entries if e.mount_point == "/data"), None)


def _pick_fstab(found: list[_Fstab]) -> tuple[_Fstab | None, str]:
    """Pick the fstab having a /data entry; returns (fstab, ambiguity note)."""
    with_data = [f for f in found if f.data is not None]
    if not with_data:
        return None, ""
    chosen = with_data[0]
    note = ""
    if len(with_data) > 1:
        def outcome(f: _Fstab):
            enc = encryption_type(f.data)
            return enc, find_preinit_device(f.entries, enc)

        if any(outcome(f) != outcome(chosen) for f in with_data[1:]):
            others = ", ".join(f.label for f in with_data[1:])
            note = f"several fstabs with /data giving different results ({others})"
    return chosen, note


class _Rom:
    """Lazily gathered ROM facts with a human-readable status for each missing piece."""

    def __init__(self, src: RomSource, workdir: Path):
        self.src = src
        self.workdir = Path(workdir)
        self.target = src.target_image_name()
        self.boot = src.extract_target(self.workdir / self.target)
        self.header: BootHeader = read_header(self.boot)
        self.vendor: Path | None = None
        self.vendor_status = ""
        self._load_vendor()
        self.props: dict[str, str] = {}
        if self.vendor is not None:
            raw = read_file(self.vendor, "/build.prop")
            if raw is None:
                self.vendor_status = "vendor:/build.prop missing"
            else:
                self.props = parse_props(raw.decode(errors="replace"))

    def _load_vendor(self) -> None:
        if isinstance(self.src, BootImageOnly):
            self.vendor_status = "boot image only, no vendor partition"
            return
        if "vendor" not in self.src.extracted_partitions():
            self.vendor_status = "no vendor partition in the ROM"
            return
        img = self.src.partition_image("vendor", self.workdir)
        if img is None:
            self.vendor_status = "vendor partition cannot be rebuilt"
            return
        try:
            list_dir(img, "/")
        except UnsupportedImage:
            self.vendor_status = "unrecognized vendor file system"
            return
        self.vendor = img

    def vendor_fstabs(self) -> list[_Fstab]:
        if self.vendor is None:
            return []
        out = []
        for name in sorted(n for n in list_dir(self.vendor, "/etc") if n.startswith("fstab.")):
            raw = read_file(self.vendor, f"/etc/{name}")
            if raw is not None:
                out.append(_Fstab(f"vendor:/etc/{name}", parse_fstab(raw.decode(errors="replace"))))
        return out

    def ramdisk_fstabs(self) -> tuple[list[_Fstab], str]:
        fmt = ramdisk_format(self.boot)
        if fmt == "none":
            return [], f"{self.target} has no ramdisk"
        files = read_ramdisk_files(self.boot)
        if not files:
            return [], f"ramdisk of {self.target} unreadable (compression {fmt} not supported)"
        out = []
        for name in sorted(files):
            base = posixpath.basename(name)
            if base.startswith("fstab.") and posixpath.dirname(name) in _RAMDISK_FSTAB_DIRS:
                text = files[name].decode(errors="replace")
                out.append(_Fstab(f"{self.target}:ramdisk:{name}", parse_fstab(text)))
        return out, "" if out else f"no fstab in the ramdisk of {self.target}"

    def find_fstab(self) -> tuple[_Fstab | None, str, str]:
        """Return (fstab, why-missing, ambiguity note)."""
        fstab, note = _pick_fstab(self.vendor_fstabs())
        if fstab:
            return fstab, "", note
        vendor_why = self.vendor_status or "no fstab with /data in vendor:/etc"
        found, ramdisk_why = self.ramdisk_fstabs()
        fstab, note = _pick_fstab(found)
        if fstab:
            return fstab, "", note
        if found and not ramdisk_why:
            ramdisk_why = f"no fstab with /data in the ramdisk of {self.target}"
        return None, f"{vendor_why}; {ramdisk_why}", ""


def _sdk(rom: _Rom) -> Finding[int | None]:
    meta = rom.src.read_member(OTA_METADATA)
    if meta is not None:
        value = parse_props(meta.decode(errors="replace")).get("post-sdk-level", "")
        if value.isdigit():
            return Finding(int(value), f"zip:{OTA_METADATA}:post-sdk-level", "OTA target SDK")
    value = rom.props.get("ro.vendor.build.version.sdk", "")
    if value.isdigit():
        source = "vendor:/build.prop:ro.vendor.build.version.sdk"
        return Finding(int(value), source, "vendor SDK")
    osv = rom.header.os_version
    if osv is not None:
        major = osv[0]
        src = f"{rom.target}:os_version"
        if major in _OS_TO_SDK:
            sdk = _OS_TO_SDK[major]
            if major == 12:
                return Finding(sdk, src, "Android 12: SDK 31 or 32 (12L), 31 chosen", warning=True)
            return Finding(sdk, src, f"inferred from Android {major}")
        if major > 16:
            return Finding(major + 20, src, f"extrapolated from Android {major}", warning=True)
        if major < 10:
            return Finding(None, src, f"Android {major}: SDK not inferred", warning=True)
    why = "SDK not found (OTA metadata, vendor, boot header)"
    return Finding(None, "not found", why, warning=True)


def _dynamic(rom: _Rom) -> tuple[bool, str]:
    for member in ("dynamic_partitions_op_list", "super_empty.img"):
        if rom.src.has_file(member):
            return True, f"zip:{member}"
    if rom.props.get("ro.boot.dynamic_partitions") == "true":
        return True, "vendor:/build.prop:ro.boot.dynamic_partitions"
    if "androidboot.super_partition" in rom.header.cmdline:
        return True, f"{rom.target}:cmdline:androidboot.super_partition"
    return False, "no sign of dynamic partitions"


def _patch_vbmeta_flag(rom: _Rom) -> Finding[bool]:
    if isinstance(rom.src, BootImageOnly):
        return Finding(False, "default", "boot image only: vbmeta presence unknown", warning=True)
    if rom.src.has_file("vbmeta.img"):
        return Finding(False, "zip:vbmeta.img", "the ROM ships vbmeta.img")
    script = rom.src.read_member(UPDATER_SCRIPT)
    if script is not None and _VBMETA_WRITE.search(script.decode(errors="replace")):
        return Finding(False, f"zip:{UPDATER_SCRIPT}", "the installer script writes vbmeta")
    return Finding(True, "zip", "the ROM ships no vbmeta")


def _device_abi(rom: _Rom) -> Finding[str]:
    for prop in ("ro.vendor.product.cpu.abilist64", "ro.product.cpu.abi", "ro.product.cpu.abilist"):
        value = rom.props.get(prop, "").split(",")[0].strip()
        if value:
            return Finding(value, f"vendor:/build.prop:{prop}", "device ABI")
    why = rom.vendor_status or "ABI missing from vendor:/build.prop"
    return Finding("arm64-v8a", "default", f"{why}; arm64-v8a by default", warning=True)


def _system_as_root(sdk: Finding[int | None], dynamic: bool, dyn_source: str) -> Finding[bool]:
    if sdk.value is not None and sdk.value >= 29:
        return Finding(True, sdk.source, f"system-as-root (SDK {sdk.value} ≥ 29)")
    if dynamic:
        return Finding(True, dyn_source, "system-as-root (dynamic partitions)")
    level = sdk.value if sdk.value is not None else "unknown"
    why = f"SDK {level} without dynamic partitions: SAR assumed false"
    return Finding(False, sdk.source, why, warning=True)


def _fstab_findings(rom: _Rom) -> tuple[Finding[bool], Finding[str | None]]:
    """Return (keep_force_encrypt, preinit_device)."""
    fstab, missing, note = rom.find_fstab()
    if fstab is None:
        why = f"no fstab ({missing})"
        return (
            Finding(True, "default", f"{why}; Magisk's safe default", True),
            Finding(None, "not found", why, True),
        )
    enc = encryption_type(fstab.data)
    suffix = f"; {note}" if note else ""
    encrypt = Finding(
        enc != "none", f"{fstab.label}:/data", f"/data encryption: {enc}{suffix}", bool(note)
    )
    entry = _preinit_entry(fstab.entries, enc)
    if entry is None:
        return encrypt, Finding(None, fstab.label, "no candidate partition in the fstab", True)
    cands = _preinit_candidates(fstab.entries, enc)
    mount_points = [e.mount_point for e in cands]
    duplicated = len(set(mount_points)) != len(mount_points)
    why = f"encryption {enc}; candidates " + ", ".join(
        f"{posixpath.basename(e.source)}({e.mount_point}, {e.fs_type})" for e in cands
    )
    if duplicated:
        why += "; several entries for the same mount point"
    preinit = Finding(
        posixpath.basename(entry.source),
        f"{fstab.label}:{entry.mount_point}",
        why + suffix,
        duplicated or bool(note),
    )
    return encrypt, preinit


def detect_settings(src: RomSource, workdir: Path) -> Settings:
    """Detect every boot_patch.sh setting from the ROM, each with its source and reason."""
    rom = _Rom(src, workdir)
    sdk = _sdk(rom)
    dynamic, dyn_source = _dynamic(rom)
    sar = _system_as_root(sdk, dynamic, dyn_source)
    ramdisk_size = rom.header.ramdisk_size
    legacy_sar = Finding(
        sar.value and not dynamic and ramdisk_size == 0,
        f"{rom.target}:ramdisk_size",
        f"SAR={sar.value}, dynamic={dynamic}, ramdisk of {ramdisk_size} bytes",
        sar.warning,
    )
    keep_force_encrypt, preinit = _fstab_findings(rom)
    return Settings(
        keep_verity=sar,
        keep_force_encrypt=keep_force_encrypt,
        patch_vbmeta_flag=_patch_vbmeta_flag(rom),
        legacy_sar=legacy_sar,
        recovery_mode=Finding(False, "default", "Magisk installed in the boot image, not in recovery"),
        preinit_device=preinit,
        device_abi=_device_abi(rom),
        sdk=sdk,
    )
