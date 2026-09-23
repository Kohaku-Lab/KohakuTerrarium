"""Decode bounded Google SSE frames and accumulate one signed assistant turn."""

import copy
import json
from uuid import uuid4

from kohakuterrarium.llm.antigravity_auth import AntigravityError
from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.llm.turn_segments import TurnSegmentsBuilder


async def read_events(lines):
    """Decode complete SSE events from an asynchronous line iterator."""
    data, size = [], 0
    async for line in lines:
        if len(line) > 1_048_576:
            raise AntigravityError("oversized_sse_frame")
        if line == "":
            if data:
                raw = "\n".join(data)
                if raw == "[DONE]":
                    return
                try:
                    value = json.loads(raw)
                except ValueError:
                    raise AntigravityError("malformed_sse") from None
                if not isinstance(value, dict):
                    raise AntigravityError("malformed_sse")
                yield value
            data, size = [], 0
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
            size += len(line)
            if size > 1_048_576:
                raise AntigravityError("oversized_sse_frame")
    if data:
        raise AntigravityError("incomplete_stream")


class Turn:
    """Accumulate raw parts, canonical tool calls, reasoning and final usage."""

    def __init__(self):
        self.parts = []
        self.calls = []
        self.text = ""
        self.usage = {}
        self.finish_reason = ""
        self.committed = False
        self.segments = TurnSegmentsBuilder()

    def feed(self, event: dict) -> list[str]:
        response = event.get("response", event)
        if not isinstance(response, dict) or "error" in response or "error" in event:
            raise AntigravityError("upstream_stream_error")
        usage = response.get("usageMetadata")
        if isinstance(usage, dict):
            for target, source in (
                ("prompt_tokens", "promptTokenCount"),
                ("completion_tokens", "candidatesTokenCount"),
                ("total_tokens", "totalTokenCount"),
                ("reasoning_tokens", "thoughtsTokenCount"),
                ("cached_tokens", "cachedContentTokenCount"),
            ):
                if isinstance(usage.get(source), int):
                    self.usage[target] = usage[source]
        output = []
        candidates = response.get("candidates", [])
        if len(candidates) > 1:
            raise AntigravityError("multiple_candidates_unsupported")
        for candidate in candidates:
            for part in candidate.get("content", {}).get("parts", []):
                if not isinstance(part, dict):
                    raise AntigravityError("malformed_part")
                self.committed = True
                self.parts.append(copy.deepcopy(part))
                if "functionCall" in part:
                    call = part["functionCall"]
                    if (
                        not isinstance(call, dict)
                        or not isinstance(call.get("name"), str)
                        or not isinstance(call.get("args", {}), dict)
                    ):
                        raise AntigravityError("malformed_tool_call")
                    identifier = call.get("id") or "agy_" + uuid4().hex
                    if any(c.id == identifier for c in self.calls):
                        raise AntigravityError("duplicate_tool_call")
                    self.calls.append(
                        NativeToolCall(
                            identifier, call["name"], json.dumps(call.get("args", {}))
                        )
                    )
                    self.segments.append_tool_call_ref(identifier)
                elif "text" in part:
                    value = part["text"]
                    if not isinstance(value, str):
                        raise AntigravityError("malformed_part")
                    if part.get("thought"):
                        self.segments.append_reasoning(value, source="antigravity")
                    else:
                        self.text += value
                        self.segments.append_text(value)
                        if value:
                            output.append(value)
                elif "thoughtSignature" not in part:
                    raise AntigravityError("unsupported_output_part")
            reason = candidate.get("finishReason")
            if reason:
                if reason not in {"STOP", "MAX_TOKENS"}:
                    raise AntigravityError("generation_blocked")
                self.finish_reason = (
                    "tool_calls"
                    if self.calls
                    else ("stop" if reason == "STOP" else "length")
                )
        return output

    def finish(self):
        if not self.finish_reason:
            raise AntigravityError("incomplete_stream")

    def extra_fields(self) -> dict:
        return self.segments.inject_into({})
