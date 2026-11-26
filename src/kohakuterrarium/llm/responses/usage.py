"""Token usage of a Responses-API response in KT's usage-dict shape."""

from typing import Any


def responses_usage(usage: Any) -> dict[str, int]:
    """Map a Responses ``usage`` object to KT's token-usage dict.

    ``reasoning_tokens`` is present only when the response reports it.
    """
    if not usage:
        return {}
    input_details = getattr(usage, "input_tokens_details", None)
    result = {
        "prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "output_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        "cached_tokens": getattr(input_details, "cached_tokens", 0) or 0,
    }
    output_details = getattr(usage, "output_tokens_details", None)
    reasoning = getattr(output_details, "reasoning_tokens", None)
    if reasoning is not None:
        result["reasoning_tokens"] = reasoning
    return result
