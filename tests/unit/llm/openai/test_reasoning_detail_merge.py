"""Streamed ``reasoning_details`` must replay as the model sent them (signed blocks included)."""

from kohakuterrarium.llm.openai.helpers import merge_reasoning_detail_stream


def _merged(pieces: list[dict]) -> list[dict]:
    acc: list[dict] = []
    for piece in pieces:
        merge_reasoning_detail_stream(acc, piece)
    return acc


def _encrypted(call_id: str, data: str) -> dict:
    return {
        "type": "reasoning.encrypted",
        "index": 0,
        "id": call_id,
        "data": data,
        "format": "google-gemini-v1",
    }


def test_signed_blocks_of_different_tool_calls_stay_separate():
    merged = _merged([_encrypted("call_a", "SIG-A"), _encrypted("call_b", "SIG-B")])
    assert [(d["id"], d["data"]) for d in merged] == [
        ("call_a", "SIG-A"),
        ("call_b", "SIG-B"),
    ]


def test_blocks_are_never_merged_across_a_different_block():
    text = {"type": "reasoning.text", "index": 0, "text": "a", "format": "x"}
    merged = _merged([text, _encrypted("call_a", "SIG"), {**text, "text": "b"}])
    assert [d["type"] for d in merged] == [
        "reasoning.text",
        "reasoning.encrypted",
        "reasoning.text",
    ]
    assert [d.get("text") for d in merged if d["type"] == "reasoning.text"] == [
        "a",
        "b",
    ]


def test_fragments_of_one_block_still_merge():
    base = {"type": "reasoning.text", "index": 0, "id": "r1", "format": "x"}
    merged = _merged(
        [{**base, "text": "ab"}, {**base, "text": "cd"}, {**base, "signature": "S"}]
    )
    assert len(merged) == 1
    assert (merged[0]["text"], merged[0]["signature"]) == ("abcd", "S")
