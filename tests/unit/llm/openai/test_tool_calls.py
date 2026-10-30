"""Tool-call identity aliases and provider metadata remain response-local."""

from copy import deepcopy

import pytest

from kohakuterrarium.llm.openai.tool_calls import ToolCallAccumulator


def test_indexless_batch_positions_survive_id_addressed_singleton():
    stream = ToolCallAccumulator()
    for position, identity in enumerate(("a", "b")):
        stream.append(
            {"id": identity, "function": {"name": "read", "arguments": '{"path":'}},
            position,
            2,
        )
    stream.append(
        {"id": "b", "extra_content": {"google": {"thought_signature": "b-signature"}}},
        0,
        1,
    )
    stream.append({"function": {"arguments": '"a.txt"}'}}, 0, 2)
    stream.append({"function": {"arguments": '"b.txt"}'}}, 1, 2)
    assert [c["arguments"] for c in stream.calls.values()] == [
        '{"path":"a.txt"}',
        '{"path":"b.txt"}',
    ]
    assert "extra_content" not in stream.calls[0]
    assert stream.calls[1]["extra_content"] == {
        "google": {"thought_signature": "b-signature"}
    }


def test_shared_index_batched_continuations_use_positions():
    stream = ToolCallAccumulator()
    for position, identity in enumerate(("a", "b")):
        stream.append(
            {
                "id": identity,
                "index": 0,
                "function": {"name": "read", "arguments": '{"path":'},
            },
            position,
            2,
        )
    for position, path in enumerate(("a.txt", "b.txt")):
        stream.append(
            {"index": 0, "function": {"arguments": f'"{path}"}}'}}, position, 2
        )
    assert [c["arguments"] for c in stream.calls.values()] == [
        '{"path":"a.txt"}',
        '{"path":"b.txt"}',
    ]


def test_ambiguous_anonymous_singleton_is_rejected_not_merged():
    stream = ToolCallAccumulator()
    for position, identity in enumerate(("a", "b")):
        stream.append(
            {"id": identity, "function": {"name": "read", "arguments": '{"path":'}},
            position,
            2,
        )
    with pytest.raises(ValueError, match="Ambiguous tool-call delta"):
        stream.append({"function": {"arguments": '"unknown.txt"}'}}, 0, 1)
    assert [c["arguments"] for c in stream.calls.values()] == ['{"path":', '{"path":']


def test_late_ids_sparse_indexes_and_nested_metadata():
    stream = ToolCallAccumulator()
    for index in (7, 2):
        stream.append(
            {"index": index, "function": {"name": "read", "arguments": ""}}, 0, 1
        )
    first = {
        "index": 7,
        "id": "a",
        "extra_content": {"google": {"thought_signature": "sig-a"}, "tags": ["first"]},
    }
    original = deepcopy(first)
    assert stream.append(first, 0, 1) == 0
    stream.append(
        {
            "index": 2,
            "id": "b",
            "extra_content": {"google": {"thought_signature": "sig-b"}},
        },
        0,
        1,
    )
    stream.append(
        {
            "index": 7,
            "extra_content": {"google": {"other": "a-only"}},
            "function": {"arguments": "{}"},
        },
        0,
        1,
    )
    stream.append({"index": 2, "function": {"arguments": "{}"}}, 0, 1)
    assert first == original
    first["extra_content"]["tags"].append("mutated")
    assert stream.calls == {
        0: {
            "id": "a",
            "name": "read",
            "arguments": "{}",
            "extra_content": {
                "google": {"thought_signature": "sig-a", "other": "a-only"},
                "tags": ["first"],
            },
        },
        1: {
            "id": "b",
            "name": "read",
            "arguments": "{}",
            "extra_content": {"google": {"thought_signature": "sig-b"}},
        },
    }
    assert ToolCallAccumulator().calls == {}
