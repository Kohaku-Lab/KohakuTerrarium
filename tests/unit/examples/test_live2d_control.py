"""Semantic actions reject disabled, expired and incompatible requests."""

import json

from examples.packages.live2d_pet.kt_live2d.control import Live2DControl


async def test_live2d_action_lifecycle():
    tool = Live2DControl()
    assert (await tool.execute({"operation": "perform", "action": "listen"})).error
    tool.bind("lease", "dragon")
    assert (
        "celebrate"
        in json.loads((await tool.execute({"operation": "actions"})).output)["actions"]
    )
    for seconds in [True, float("nan"), float("inf"), 0, 16, "nan", "bad", "16"]:
        assert (
            await tool.execute(
                {"operation": "perform", "action": "listen", "seconds": seconds}
            )
        ).error
    assert (await tool.execute({"operation": "perform", "action": "missing"})).error
    result = await tool.execute(
        {"operation": "perform", "action": "listen", "seconds": "4"}
    )
    assert json.loads(result.output)["status"] == "dispatched"
    command = tool.events.get_nowait()
    assert command["action"] == "listen" and command["seconds"] == 4
    assert (await tool.execute({"operation": "perform", "action": "listen"})).error
    assert not (await tool.execute({"operation": "release"})).error
    assert tool.events.get_nowait()["operation"] == "release"
    tool.bind("lease", "deepseek", waiting=True)
    assert (await tool.execute({"operation": "perform", "action": "celebrate"})).error
    tool.until = 0
    assert (await tool.execute({"operation": "actions"})).error
    tool.revoke()
    assert tool.events.get_nowait()["operation"] == "release"
    tool.bind("first-ui", "dragon")
    old_queue = tool.events
    tool.bind("second-ui", "dragon")
    assert old_queue.get_nowait()["operation"] == "release"
    assert old_queue is not tool.events
    result = await tool.execute({"operation": "perform", "action": "nod"})
    assert not result.error
    assert tool.events.get_nowait()["action"] == "nod"
    assert old_queue.empty()
    inventory = json.loads((await tool.execute({"operation": "actions"})).output)
    assert "playback" not in inventory["actions"]["nod"]
    assert (await tool.execute({"operation": "perform", "action": {}})).error
    assert (await tool.execute({"operation": []})).error
