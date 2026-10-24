import io
import os
import subprocess

import brotli
import pytest

from magisk_boot_patcher.errors import UserError
from magisk_boot_patcher.sdat import open_new_dat, sdat_to_img

BS = 4096
TRANSFER = "4\n10\n0\n0\nnew 4,0,3,5,10\nerase 2,3,5\n"


def _blocks(n=10):
    return [os.urandom(BS) for _ in range(n)]


def _check(out, blocks):
    data = out.read_bytes()
    assert len(data) == 10 * BS
    for i in (0, 1, 2, 5, 6, 7, 8, 9):
        assert data[i * BS:(i + 1) * BS] == blocks[i]
    assert data[3 * BS:5 * BS] == b"\0" * (2 * BS)


def _payload(blocks):
    return b"".join(blocks[i] for i in (0, 1, 2, 5, 6, 7, 8, 9))


def test_sdat_roundtrip(tmp_path):
    blocks = _blocks()
    tl = tmp_path / "t.list"
    tl.write_text(TRANSFER)
    out = tmp_path / "out.img"
    with open_new_dat("vendor.new.dat", io.BytesIO(_payload(blocks))) as f:
        assert sdat_to_img(tl, f, out) == out
    _check(out, blocks)


def test_sdat_brotli(tmp_path):
    blocks = _blocks()
    tl = tmp_path / "t.list"
    tl.write_text(TRANSFER)
    out = tmp_path / "out.img"
    raw = io.BytesIO(brotli.compress(_payload(blocks)))
    with open_new_dat("vendor.new.dat.br", raw) as f:
        sdat_to_img(tl, f, out)
    _check(out, blocks)


def test_sdat_v1(tmp_path):
    blocks = _blocks()
    tl = tmp_path / "t.list"
    tl.write_text("1\n10\nnew 4,0,3,5,10\n")
    out = tmp_path / "out.img"
    sdat_to_img(tl, io.BytesIO(_payload(blocks)), out)
    _check(out, blocks)


def test_sdat_rejects_incremental(tmp_path):
    tl = tmp_path / "t.list"
    tl.write_text("4\n10\n0\n0\nbsdiff 0 5 a b 2,0,1 1 c\n")
    with pytest.raises(UserError):
        sdat_to_img(tl, io.BytesIO(b""), tmp_path / "o.img")


def test_sdat_truncated_data(tmp_path):
    tl = tmp_path / "t.list"
    tl.write_text(TRANSFER)
    with pytest.raises(UserError):
        sdat_to_img(tl, io.BytesIO(b"x" * BS), tmp_path / "o.img")
