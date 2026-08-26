from kohakuterrarium.cli import picker
from kohakuterrarium.cli.select import Runnable, RunnableGroup


def test_picker_records_catalog_ready(monkeypatch):
    milestones = []
    groups = [
        RunnableGroup(
            "local",
            creatures=(Runnable("a", "a", "creature", "local"),),
            terrariums=(Runnable("b", "b", "terrarium", "local"),),
        )
    ]
    from kohakuterrarium.cli import select_cli

    monkeypatch.setattr(picker, "enumerate_runnables", lambda: groups)
    monkeypatch.setattr(select_cli, "run_cli_picker", lambda _groups: "a")
    monkeypatch.setattr(
        picker,
        "mark_startup",
        lambda event, **fields: milestones.append((event, fields)),
    )

    assert picker.pick_runnable("cli") == "a"
    assert milestones == [
        (
            "picker_catalog_scanned",
            {"surface": "cli", "groups": 1, "entries": 2},
        )
    ]
