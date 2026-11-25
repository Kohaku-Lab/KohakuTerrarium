"""Unit tests for ``kohakuterrarium.browser.processes``: ending a process tree at once."""

import subprocess
import sys
import time

from kohakuterrarium.browser import processes as processes_module
from kohakuterrarium.browser.processes import BrowserProcesses, kill_tree
from tests.helpers.browser_site import process_alive

PARENT = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(60)\n"
)


def gone_within(pids: list[int], seconds: float) -> list[int]:
    deadline = time.monotonic() + seconds
    alive = list(pids)
    while alive and time.monotonic() < deadline:
        alive = [pid for pid in alive if process_alive(pid)]
        time.sleep(0.05)
    return alive


def test_kill_tree_ends_a_process_and_its_children():
    extra = {} if sys.platform == "win32" else {"start_new_session": True}
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT], stdout=subprocess.PIPE, text=True, **extra
    )
    try:
        child = int(parent.stdout.readline())
        assert process_alive(parent.pid) and process_alive(child)
        kill_tree(parent.pid)
        assert gone_within([parent.pid, child], 10.0) == []
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(10)
        parent.stdout.close()


def test_kill_tree_of_a_missing_process_is_harmless():
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait(10)
    kill_tree(done.pid)
    kill_tree(0)
    kill_tree(-1)


def spawn_tree():
    extra = {} if sys.platform == "win32" else {"start_new_session": True}
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT], stdout=subprocess.PIPE, text=True, **extra
    )
    return parent, int(parent.stdout.readline())


def reap(parent) -> None:
    if parent.poll() is None:
        parent.kill()
    parent.wait(10)
    parent.stdout.close()


def test_held_processes_are_killed_with_their_tree():
    parent, child = spawn_tree()
    try:
        held = BrowserProcesses([parent.pid, child])
        held.kill()
        assert gone_within([parent.pid, child], 10.0) == []
        held.release()
    finally:
        reap(parent)


def test_a_root_that_already_exited_is_not_killed_again(monkeypatch):
    parent, child = spawn_tree()
    try:
        held = BrowserProcesses([parent.pid])
        parent.kill()
        parent.wait(10)
        trees = []
        monkeypatch.setattr(processes_module, "kill_tree", trees.append)
        held.kill()
        assert trees == [], "a pid that may have been reused was killed"
        held.release()
    finally:
        reap(parent)
        kill_tree(child)


def test_release_and_an_empty_hold_are_harmless():
    held = BrowserProcesses([])
    held.kill()
    held.release()
    held.release()
