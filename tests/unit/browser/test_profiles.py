"""Unit tests for ``kohakuterrarium.browser.profiles``: one browser per persistent profile."""

import asyncio
import time

import pytest

from kohakuterrarium.browser import profiles
from kohakuterrarium.browser.errors import BrowserUnavailableError
from kohakuterrarium.browser.profiles import claim_profile, profile_key


class Process:
    """A launched browser as the claim sees it: running until ``exit``."""

    def __init__(self) -> None:
        self.running = True

    def exited(self) -> bool:
        return not self.running


@pytest.fixture(autouse=True)
def short_wait(monkeypatch):
    monkeypatch.setattr(profiles, "RELEASE_WAIT_S", 0.2)
    monkeypatch.setattr(profiles, "POLL_S", 0.01)


async def test_a_free_profile_is_claimed_at_once(tmp_path):
    started = time.perf_counter()
    claim = await claim_profile(str(tmp_path / "p"))
    assert time.perf_counter() - started < 0.1
    claim.drop()


async def test_a_running_browser_keeps_its_profile_until_it_exits(tmp_path):
    path = str(tmp_path / "p")
    first = await claim_profile(path)
    browser = Process()
    first.held_by(browser)
    started = time.perf_counter()
    with pytest.raises(BrowserUnavailableError) as info:
        await claim_profile(path)
    assert time.perf_counter() - started >= 0.2
    assert str(tmp_path / "p") in str(info.value)
    assert "could not be confirmed" in str(info.value)
    browser.running = False
    second = await claim_profile(path)
    assert profiles._claims[profile_key(path)] == [second]
    second.drop()


async def test_a_browser_that_exits_while_waited_for_frees_the_profile(tmp_path):
    path = str(tmp_path / "p")
    first = await claim_profile(path)
    browser = Process()
    first.held_by(browser)
    asyncio.get_running_loop().call_later(0.05, setattr, browser, "running", False)
    second = await claim_profile(path)
    second.drop()


async def test_a_launch_in_progress_holds_the_profile_until_dropped(tmp_path):
    path = str(tmp_path / "p")
    pending = await claim_profile(path)
    with pytest.raises(BrowserUnavailableError):
        await claim_profile(path)
    pending.drop()
    (await claim_profile(path)).drop()


async def test_concurrent_claims_admit_exactly_one(tmp_path):
    path = str(tmp_path / "p")
    results = await asyncio.gather(
        claim_profile(path), claim_profile(path), return_exceptions=True
    )
    won = [r for r in results if not isinstance(r, BaseException)]
    lost = [r for r in results if isinstance(r, BrowserUnavailableError)]
    assert len(won) == 1 and len(lost) == 1
    won[0].drop()


async def test_two_spellings_of_one_directory_are_one_profile(tmp_path):
    (tmp_path / "p").mkdir()
    first = await claim_profile(str(tmp_path / "p"))
    browser = Process()
    first.held_by(browser)
    with pytest.raises(BrowserUnavailableError):
        await claim_profile(str(tmp_path / "p" / ".." / "p"))
    other = await claim_profile(str(tmp_path / "q"))
    other.drop()
    browser.running = False
    profiles.forget_if_released(first)
    for name in ("p", "q"):
        assert profile_key(str(tmp_path / name)) not in profiles._claims


async def test_forget_drops_an_exited_claim_and_keeps_a_running_one(tmp_path):
    path = str(tmp_path / "p")
    claim = await claim_profile(path)
    browser = Process()
    claim.held_by(browser)
    profiles.forget_if_released(claim)
    assert profiles._claims[profile_key(path)] == [claim]
    browser.running = False
    profiles.forget_if_released(claim)
    assert profile_key(path) not in profiles._claims
