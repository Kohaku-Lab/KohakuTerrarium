"""Unit tests for the /branch command helpers."""

from kohakuterrarium.builtins.user_commands.branch import _rebuild_conv
from kohakuterrarium.core.conversation import Conversation


def test_rebuild_conv_preserves_user_turn_metadata():
    # /branch rebuilds the live conversation from replay; the replayed user
    # messages carry turn identity (include_metadata=True) that must survive
    # the rebuild so edit targeting stays deterministic after a branch switch.
    msgs = [
        {
            "role": "user",
            "content": "U1",
            "metadata": {"event_id": 1, "turn_index": 1, "branch_id": 1},
        },
        {"role": "assistant", "content": "R1"},
    ]
    conv = _rebuild_conv(msgs, Conversation)
    out = conv.to_messages(include_metadata=True)
    assert out[0]["metadata"] == {"event_id": 1, "turn_index": 1, "branch_id": 1}
    assert out[1] == {"role": "assistant", "content": "R1"}
