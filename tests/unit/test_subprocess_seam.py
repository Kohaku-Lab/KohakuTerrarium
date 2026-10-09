"""Unit tests for :mod:`kohakuterrarium.testing.subprocess_seam`."""

import json

from kohakuterrarium.testing import subprocess_seam as seam
from kohakuterrarium.testing.llm import ScriptEntry


def test_script_entries_may_be_strings_or_entry_fields(tmp_path):
    path = tmp_path / "script.json"
    path.write_text(
        json.dumps(
            {
                "script": [
                    "plain",
                    {"response": "gated", "match": "key", "delay_per_chunk": 0.5},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert seam._load_script(path) == [
        "plain",
        ScriptEntry("gated", match="key", delay_per_chunk=0.5),
    ]


def test_unreadable_or_malformed_scripts_fall_back_to_ok(tmp_path):
    assert seam._load_script(tmp_path / "missing.json") == ["OK"]
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert seam._load_script(bad) == ["OK"]
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"script": "nope"}), encoding="utf-8")
    assert seam._load_script(wrong) == ["OK"]
