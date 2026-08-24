"""Tests for TUI tool argument formatting."""

from kohakuterrarium.builtins.tui.tool_args import (
    format_args_detail,
    format_args_preview,
)


def test_tool_argument_preview_and_safe_detail_formatting():
    long_prompt = "describe-the-scene-" * 8
    args = {
        "prompt": long_prompt,
        "count": 2,
        "content": "hidden file content",
        "_session_token": "hidden internal value",
    }

    assert format_args_preview("custom_tool", {"prompt": "short"}) == ("prompt=short")
    assert format_args_preview("custom_tool", args) == f"prompt={long_prompt}"
    assert format_args_detail("custom_tool", args) == (f"prompt={long_prompt}\ncount=2")
    assert long_prompt in format_args_detail("custom_tool", args)
    assert "hidden file content" not in format_args_detail("custom_tool", args)
    assert "hidden internal value" not in format_args_detail("custom_tool", args)

    assert (
        format_args_detail(
            "send_message",
            {"channel": "review", "message": "private payload"},
        )
        == "channel=review"
    )
    assert (
        format_args_detail(
            "write",
            {
                "file_path": "/tmp/result.txt",
                "content": "private file body",
                "mode": "overwrite",
            },
        )
        == "file_path=/tmp/result.txt"
    )
