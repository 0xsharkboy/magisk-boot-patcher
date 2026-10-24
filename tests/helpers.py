"""Fixture factories shared by the test-suite (fake boot images, mini block-OTA zips)."""

import gzip
import io
import subprocess
import zipfile
from pathlib import Path

import brotli

BLOCK = 4096

DEFAULT_FSTAB = (
    "# fake vendor fstab\n"
    "system /system erofs ro wait,logical,first_stage_mount\n"
    "vendor /vendor erofs ro wait,logical,first_stage_mount\n"
    "/dev/block/by-name/userdata /data f2fs noatime,nosuid,nodev "
    "wait,check,fileencryption=aes-256-xts:aes-256-cts:v2,quota,reservedsize=128M\n"
)


def _cpio_newc(files: dict[str, bytes]) -> bytes:
    """Build a cpio newc archive (no external tool needed)."""
    out = io.BytesIO()
    ino = 1

    def entry(name: str, data: bytes, mode: int):
        nonlocal ino
        nm = name.encode() + b"\0"
        hdr = "070701" + "".join(
            f"{v:08X}" for v in (ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nm), 0)
        )
        out.write(hdr.encode() + nm)
        out.write(b"\0" * (-(110 + len(nm)) % 4))
        out.write(data)
        out.write(b"\0" * (-len(data) % 4))
        ino += 1

    for name, data in files.items():
        entry(name, data, 0o100755)
    entry("TRAILER!!!", b"", 0)
    return out.getvalue()


def make_boot_image(
    path: Path,
    ramdisk_files: dict[str, bytes] | None = None,
    header_version: int = 1,
    os_version: str = "16.0.0",
    os_patch_level: str = "2026-09",
    cmdline: str = " androidboot.super_partition=system",
) -> Path:
    """Create a fake boot image (dummy kernel, gzip'd cpio ramdisk) with mkbootimg."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    files = {"init": b"#!fake init\n"} if ramdisk_files is None else ramdisk_files
    kernel = path.with_name(path.name + ".kernel")
    ramdisk = path.with_name(path.name + ".ramdisk")
    kernel.write_bytes(b"FAKEKERNEL" * 100)
    ramdisk.write_bytes(gzip.compress(_cpio_newc(files), mtime=0))
    cmd = [
        "mkbootimg", "--header_version", str(header_version),
        "--kernel", str(kernel), "--ramdisk", str(ramdisk),
        "--os_version", os_version, "--os_patch_level", os_patch_level,
        "--cmdline", cmdline, "-o", str(path),
    ]
    if header_version < 3:
        cmd[1:1] = ["--pagesize", "2048"]
    subprocess.run(cmd, check=True, capture_output=True)
    kernel.unlink()
    ramdisk.unlink()
    return path


def make_vendor_image(path: Path, vendor_fs: str = "erofs", fstab: str = DEFAULT_FSTAB) -> Path:
    """Create a small vendor filesystem image holding etc/fstab.fake and build.prop."""
    path = Path(path)
    tree = path.with_name(path.name + ".tree")
    (tree / "etc").mkdir(parents=True, exist_ok=True)
    (tree / "etc" / "fstab.fake").write_text(fstab)
    (tree / "build.prop").write_text("ro.product.vendor.device=fake\n")
    if vendor_fs == "erofs":
        cmd = ["mkfs.erofs", str(path), str(tree)]
    elif vendor_fs == "ext4":
        cmd = ["mke2fs", "-q", "-t", "ext4", "-d", str(tree), str(path), "8M"]
    else:
        raise ValueError(vendor_fs)
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def make_block_ota_zip(
    tmp: Path,
    vendor_fs: str = "erofs",
    brotli: bool = False,
    with_init_boot: bool = False,
    with_vbmeta: bool = True,
    dynamic: bool = True,
    fstab: str = DEFAULT_FSTAB,
    name: str = "rom.zip",
    boot_ramdisk_files: dict[str, bytes] | None = None,
    header_version: int = 1,
    extra_members: dict[str, bytes] | None = None,
) -> Path:
    """Build a mini block-OTA zip: vendor.new.dat(.br)+transfer.list, boot.img, metadata."""
    brotli_mod = globals()["brotli"]
    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    vendor = make_vendor_image(tmp / "vendor.img", vendor_fs, fstab)
    data = vendor.read_bytes()
    assert len(data) % BLOCK == 0
    blocks = len(data) // BLOCK
    transfer = f"4\n{blocks}\n0\n0\nnew 2,0,{blocks}\n"
    boot = make_boot_image(tmp / "boot.img", boot_ramdisk_files, header_version)

    out = tmp / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/com/android/metadata", "ota-type=BLOCK\npost-timestamp=1\n")
        z.writestr("vendor.transfer.list", transfer)
        if brotli:
            z.writestr("vendor.new.dat.br", brotli_mod.compress(data))
        else:
            z.writestr("vendor.new.dat", data)
        z.write(boot, "boot.img")
        if with_init_boot:
            z.write(make_boot_image(tmp / "init_boot.img", boot_ramdisk_files, 4), "init_boot.img")
        if with_vbmeta:
            z.writestr("vbmeta.img", b"AVB0" + b"\0" * 252)
        if dynamic:
            z.writestr("dynamic_partitions_op_list", "remove_all_groups\nadd_group main 1000\n")
            z.writestr("super_empty.img", b"\0" * 4096)
        for member, content in (extra_members or {}).items():
            z.writestr(member, content)
    return out
