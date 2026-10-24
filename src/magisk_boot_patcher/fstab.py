from dataclasses import dataclass


@dataclass(frozen=True)
class FstabEntry:
    source: str
    mount_point: str
    fs_type: str
    mnt_flags: tuple[str, ...]
    fs_mgr_flags: tuple[str, ...]


def _flags(field: str) -> tuple[str, ...]:
    return tuple(f for f in field.split(",") if f)


def parse_fstab(text: str) -> list[FstabEntry]:
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        source, mount_point, fs_type, mnt_flags, fs_mgr_flags = parts[:5]
        entries.append(
            FstabEntry(source, mount_point, fs_type, _flags(mnt_flags), _flags(fs_mgr_flags))
        )
    return entries
