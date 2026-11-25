"""Unit tests for ``kohakuterrarium.browser.downloads``: safe names, unique paths, saving."""

import asyncio
import os

import pytest

from kohakuterrarium.browser import downloads as downloads_module
from kohakuterrarium.browser.downloads import (
    MAX_NAME_CHARS,
    Downloads,
    reserve_path,
    safe_filename,
)
from tests.helpers.browser_driver import ScriptedDownload


@pytest.mark.parametrize(
    "suggested,expected",
    [
        ("report.csv", "report.csv"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\boot.ini", "boot.ini"),
        ("C:\\Windows\\evil.exe", "evil.exe"),
        ('a<b>c:d"e|f?g*h.txt', "a_b_c_d_e_f_g_h.txt"),
        ("line\nbreak\x00.txt", "line_break_.txt"),
        ("...", "download"),
        ("", "download"),
        (".bashrc", "bashrc"),
        ("CON", "download_CON"),
        ("nul.txt", "download_nul.txt"),
        ("trailing. ", "trailing"),
    ],
)
def test_safe_filename(suggested, expected):
    assert safe_filename(suggested) == expected


def test_long_names_keep_their_extension():
    name = safe_filename("x" * 500 + ".pdf")
    assert len(name) == MAX_NAME_CHARS and name.endswith(".pdf")
    odd = safe_filename("y" * 300 + "." + "z" * 40)
    assert len(odd) == MAX_NAME_CHARS


def test_reserve_path_never_overwrites(tmp_path):
    folder = tmp_path / "new" / "dir"
    first = reserve_path(folder, "a.txt")
    first.write_text("keep")
    second = reserve_path(folder, "a.txt")
    third = reserve_path(folder, "a.txt")
    assert [p.name for p in (first, second, third)] == [
        "a.txt",
        "a (2).txt",
        "a (3).txt",
    ]
    assert first.read_text() == "keep"


async def test_saves_reports_once_and_counts_failures(tmp_path):
    downloads = Downloads(tmp_path)
    downloads.start(ScriptedDownload("a.csv", b"1"))
    downloads.start(ScriptedDownload("a.csv", b"2"))
    downloads.start(ScriptedDownload("bad.bin", error="disk full"))
    await downloads.wait(1.0)
    finished, failed = downloads.drain()
    assert sorted(path for _, path in finished) == sorted(
        [str(tmp_path / "a.csv"), str(tmp_path / "a (2).csv")]
    )
    assert failed == [("bad.bin", "disk full")]
    assert not (tmp_path / "bad.bin").exists()
    assert downloads.drain() == ([], [])
    assert downloads.started == 3


class SlowDownload(ScriptedDownload):
    async def save_as(self, path):
        await asyncio.Event().wait()


async def test_in_progress_timeout_and_clear(tmp_path):
    downloads = Downloads(tmp_path, save_timeout=0.05)
    downloads.start(SlowDownload("slow.bin"))
    await downloads.wait(0.01)
    assert downloads.in_progress() == ["slow.bin"]
    await downloads.wait(1.0)
    finished, failed = downloads.drain()
    assert finished == [] and failed[0][0] == "slow.bin"
    assert list(tmp_path.iterdir()) == []
    downloads.start(SlowDownload("again.bin"))
    await asyncio.sleep(0.01)
    downloads.clear()
    await asyncio.sleep(0.01)
    assert downloads.in_progress() == [] and list(tmp_path.iterdir()) == []


def test_reserve_path_probes_a_bounded_number_of_names(tmp_path, monkeypatch):
    for number in range(1, 200):
        name = "a.txt" if number == 1 else f"a ({number}).txt"
        (tmp_path / name).write_text("x")
    attempts = []
    real = downloads_module._create

    def counting(path):
        attempts.append(path)
        return real(path)

    monkeypatch.setattr(downloads_module, "_create", counting)
    path = reserve_path(tmp_path, "a.txt")
    assert path.exists() and path.read_text() == ""
    assert path.name.startswith("a-") and path.suffix == ".txt"
    assert len(attempts) <= 8


class CancellableDownload(ScriptedDownload):
    cancelled = False

    async def cancel(self):
        self.cancelled = True


async def test_count_cap_refuses_and_cancels_later_downloads(tmp_path):
    downloads = Downloads(tmp_path, max_count=2)
    items = [CancellableDownload(f"f{i}.bin") for i in range(3)]
    for item in items:
        downloads.start(item)
    await downloads.wait(1.0)
    await asyncio.sleep(0.01)
    finished, failed = downloads.drain()
    assert [name for name, _ in finished] == ["f0.bin", "f1.bin"]
    assert failed == [("f2.bin", "refused: the limit of 2 downloads per session")]
    assert items[2].cancelled and items[2].saved_to is None


async def test_byte_cap_deletes_what_goes_over(tmp_path):
    downloads = Downloads(tmp_path, max_bytes=10)
    downloads.start(ScriptedDownload("small.bin", b"12345"))
    await downloads.wait(1.0)
    downloads.start(ScriptedDownload("big.bin", b"123456789"))
    await downloads.wait(1.0)
    finished, failed = downloads.drain()
    assert [name for name, _ in finished] == ["small.bin"]
    assert failed == [("big.bin", "refused: over the 10-byte total download limit")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["small.bin"]


async def test_caps_hold_across_a_relaunch(tmp_path):
    downloads = Downloads(tmp_path, max_count=2, max_bytes=10)
    downloads.start(ScriptedDownload("small.bin", b"12345"))
    await downloads.wait(1.0)
    downloads.clear()
    downloads.start(ScriptedDownload("nine.bin", b"123456789"))
    await downloads.wait(1.0)
    item = CancellableDownload("third.bin")
    downloads.start(item)
    await downloads.wait(1.0)
    await asyncio.sleep(0.01)
    finished, failed = downloads.drain()
    assert finished == []
    assert failed == [
        ("nine.bin", "refused: over the 10-byte total download limit"),
        ("third.bin", "refused: the limit of 2 downloads per session"),
    ]
    assert item.cancelled and item.saved_to is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["small.bin"]


async def test_a_save_that_times_out_cancels_the_download(tmp_path):
    downloads = Downloads(tmp_path, save_timeout=0.05)
    item = SlowDownload("slow.bin")
    downloads.start(item)
    await downloads.wait(1.0)
    finished, failed = downloads.drain()
    assert finished == []
    assert failed == [("slow.bin", "timed out after 0.05 s")]
    assert item.cancelled
    assert list(tmp_path.iterdir()) == []


def test_reserve_path_gives_up_when_every_probe_is_taken(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads_module, "_create", lambda path: False)
    with pytest.raises(FileExistsError, match="no free file name"):
        reserve_path(tmp_path, "a.txt")


class StubbornDownload(ScriptedDownload):
    async def cancel(self):
        raise RuntimeError("cannot cancel")


class VanishingDownload(ScriptedDownload):
    async def save_as(self, path):
        await super().save_as(path)
        os.remove(path)


class TestWriteCheck:
    async def test_a_refused_download_writes_nothing_and_is_cancelled(self, tmp_path):
        target = tmp_path / "downloads"
        asked = []

        def refuse(path):
            asked.append(path)
            return "fs_write=deny"

        downloads = Downloads(target, write_check=refuse)
        item = CancellableDownload("report.csv")
        downloads.start(item)
        await downloads.wait(1.0)
        await asyncio.sleep(0.01)
        finished, failed = downloads.drain()
        assert finished == []
        assert failed == [
            ("report.csv", "refused by the file-write policy: fs_write=deny")
        ]
        assert item.cancelled and item.saved_to is None
        assert not target.exists(), "the directory was created before the check"
        assert asked == [target]

    async def test_every_probed_name_is_checked_before_it_is_created(self, tmp_path):
        (tmp_path / "a.txt").write_text("taken")
        asked = []

        def deny_second(path):
            asked.append(path.name)
            return "path is denied" if path.name == "a (2).txt" else None

        downloads = Downloads(tmp_path, write_check=deny_second)
        downloads.start(CancellableDownload("a.txt"))
        await downloads.wait(1.0)
        await asyncio.sleep(0.01)
        finished, failed = downloads.drain()
        assert finished == [] and failed[0][1].endswith("path is denied")
        assert asked == [tmp_path.name, "a.txt", "a (2).txt"]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]

    async def test_a_check_that_raises_refuses(self, tmp_path):
        def broken(path):
            raise RuntimeError("policy gone")

        downloads = Downloads(tmp_path / "d", write_check=broken)
        downloads.start(CancellableDownload("x.bin"))
        await downloads.wait(1.0)
        await asyncio.sleep(0.01)
        _, failed = downloads.drain()
        assert failed == [
            (
                "x.bin",
                "refused by the file-write policy: the check failed (RuntimeError)",
            )
        ]
        assert not (tmp_path / "d").exists()

    async def test_an_allowing_check_saves_as_before(self, tmp_path):
        downloads = Downloads(tmp_path, write_check=lambda path: None)
        downloads.start(ScriptedDownload("ok.csv", b"1"))
        await downloads.wait(1.0)
        finished, failed = downloads.drain()
        assert finished == [("ok.csv", str(tmp_path / "ok.csv"))] and failed == []

    async def test_the_directory_is_read_when_a_download_starts(self, tmp_path):
        current = [tmp_path / "first"]
        downloads = Downloads(lambda: current[0])
        downloads.start(ScriptedDownload("a.bin", b"1"))
        await downloads.wait(1.0)
        current[0] = tmp_path / "second"
        assert downloads.directory == tmp_path / "second"
        downloads.start(ScriptedDownload("b.bin", b"2"))
        await downloads.wait(1.0)
        finished, _ = downloads.drain()
        assert finished == [
            ("a.bin", str(tmp_path / "first" / "a.bin")),
            ("b.bin", str(tmp_path / "second" / "b.bin")),
        ]


async def test_cancel_failures_and_vanished_files_are_survived(tmp_path):
    downloads = Downloads(tmp_path, max_count=1, max_bytes=10)
    downloads.start(VanishingDownload("gone.bin", b"1" * 50))
    downloads.start(StubbornDownload("extra.bin"))
    await downloads.wait(1.0)
    await asyncio.sleep(0.01)
    finished, failed = downloads.drain()
    assert [name for name, _ in finished] == ["gone.bin"]
    assert [name for name, _ in failed] == ["extra.bin"]


async def test_without_a_directory_every_download_is_refused_and_cancelled(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    downloads = Downloads(None)
    items = [CancellableDownload(f"f{i}.bin") for i in range(3)]
    for item in items:
        downloads.start(item)
    await asyncio.sleep(0.05)
    finished, failed = downloads.drain()
    assert finished == [] and downloads.in_progress() == []
    assert failed == [
        (f"f{i}.bin", "refused: this browser session keeps no downloads")
        for i in range(3)
    ]
    assert all(item.cancelled and item.saved_to is None for item in items)
    assert downloads.started == 3 and list(tmp_path.iterdir()) == []
