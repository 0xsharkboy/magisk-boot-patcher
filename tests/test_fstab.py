from pathlib import Path

from magisk_boot_patcher.fstab import FstabEntry, parse_fstab

FIXTURE = Path(__file__).parent / "fixtures" / "fstab.exynos9820"


def test_data_entry():
    entries = parse_fstab(FIXTURE.read_text())
    data = [e for e in entries if e.mount_point == "/data"]
    assert len(data) == 1
    e = data[0]
    assert e.source.endswith("/by-name/userdata")
    assert e.fs_type == "f2fs"
    assert "fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized" in e.fs_mgr_flags
    assert "metadata_encryption=aes-256-xts" in e.fs_mgr_flags
    assert "inlinecrypt" in e.mnt_flags


def test_logical_entries():
    entries = parse_fstab(FIXTURE.read_text())
    vendor = [e for e in entries if e.mount_point == "/vendor"]
    assert vendor
    assert all("logical" in e.fs_mgr_flags for e in vendor)


def test_comments_and_blank_lines_ignored():
    entries = parse_fstab("# comment\n\n  \na /x ext4 ro wait\n")
    assert entries == [FstabEntry("a", "/x", "ext4", ("ro",), ("wait",))]


def test_short_lines_skipped():
    assert parse_fstab("a /x ext4\n") == []
