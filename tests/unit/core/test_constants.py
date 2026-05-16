"""Pin the framework constants — anyone changing these should
update every place that consumes them."""

from kohakuterrarium.core import constants


def test_tool_output_preview_chars_value():
    # The job-status preview field is capped at exactly 200 chars — this
    # is the documented value in constants.py and is load-bearing for
    # every consumer that slices a preview.
    assert constants.TOOL_OUTPUT_PREVIEW_CHARS == 200
