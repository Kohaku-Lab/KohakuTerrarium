"""Opt-in live Antigravity text and signed-tool matrix with redacted reports."""

import argparse
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from kohakuterrarium.llm.antigravity_auth import AntigravityError
from kohakuterrarium.llm.antigravity_client import make_client
from kohakuterrarium.llm.antigravity_presets import MODELS, model_settings
from kohakuterrarium.llm.antigravity_provider import AntigravityProvider
from kohakuterrarium.llm.base import ToolSchema

CASES = tuple(
    (model, effort)
    for model, spec in MODELS.items()
    for effort in (spec.efforts or ("",))
)
EXPECTED = "AGY_MATRIX_OK"
TEXT_PROMPT = "Reply exactly AGY_MATRIX_OK. Do not add any other text."
TOOL_PROMPT = "Call probe_echo exactly once with value AGY_MATRIX_OK. After receiving the tool result, reply exactly with its value. Do not answer before calling the tool."
TOOL = ToolSchema(
    "probe_echo",
    "Return the supplied value without side effects.",
    {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    },
)


class ProductionTransport(httpx.AsyncBaseTransport):
    """Use the production client's environment, proxy and timeout configuration."""

    def __init__(self):
        self.client = make_client()

    async def handle_async_request(self, request):
        return await self.client.send(request, stream=True)

    async def aclose(self):
        await self.client.aclose()


class MatrixTransport(httpx.AsyncBaseTransport):
    """Enforce the approved destination, one attempt per step, and total call budget."""

    def __init__(self, inner, *, max_requests):
        self.inner = inner
        self.max_requests = max_requests
        self.inference_requests = 0
        self.start_step()

    def start_step(self):
        self.step_requests = 0
        self.last_status = None
        self.last_operation = ""
        self.last_diagnostic = {}

    async def handle_async_request(self, request):
        allowed = {"/v1internal:loadCodeAssist", "/v1internal:streamGenerateContent"}
        if (
            request.method != "POST"
            or request.url.scheme != "https"
            or request.url.host != "daily-cloudcode-pa.googleapis.com"
            or request.url.port not in (None, 443)
            or request.url.path not in allowed
        ):
            raise AntigravityError("destination_not_allowed")
        operation = request.url.path.split(":")[-1]
        if operation == "streamGenerateContent":
            if self.inference_requests >= self.max_requests:
                raise AntigravityError("inference_budget_exhausted")
            if self.step_requests:
                raise AntigravityError("step_retry_blocked")
            self.inference_requests += 1
            self.step_requests += 1
        self.last_operation = operation
        response = await self.inner.handle_async_request(request)
        self.last_status = response.status_code
        if response.status_code != 200:
            self.last_diagnostic = await error_diagnostic(response, request)
            await response.aclose()
            raise AntigravityError("http_" + str(response.status_code))
        return response

    async def aclose(self):
        """Leave the shared inner transport open until the matrix owner closes it."""


async def error_diagnostic(response, request):
    """Extract a bounded error message after removing credentials and account data."""
    raw = bytearray()
    async for chunk in response.aiter_bytes():
        raw.extend(chunk)
        if len(raw) > 8192:
            return {"status": "oversized_error"}
    try:
        error = json.loads(raw).get("error", {})
        message = error.get("message", "")
        if not isinstance(message, str):
            return {}
        body = json.loads(request.content)
        for secret in (
            body.get("project", ""),
            request.headers.get("authorization", "").removeprefix("Bearer "),
        ):
            if isinstance(secret, str) and secret:
                message = message.replace(secret, "[redacted]")
        message = re.sub(
            r"https?://\S+|[\w.+-]+@[\w.-]+|[A-Za-z0-9_+/=-]{24,}",
            "[redacted]",
            message,
        )
        status = error.get("status", "")
        return {
            "status": (
                status
                if status
                in {
                    "INVALID_ARGUMENT",
                    "NOT_FOUND",
                    "PERMISSION_DENIED",
                    "UNAUTHENTICATED",
                    "RESOURCE_EXHAUSTED",
                    "INTERNAL",
                    "UNAVAILABLE",
                }
                else "unknown"
            ),
            "message": message[:600],
        }
    except (ValueError, TypeError, AttributeError):
        return {}


def case_plan(model, effort):
    settings = model_settings(model, effort)
    cap = max(2048, settings.thinking_config.get("thinkingBudget", 0) + 1024)
    return {
        "model": model,
        "effort": effort or "fixed",
        "wire_model": settings.wire_model,
        "thinking": settings.thinking_config,
        "max_output_tokens": cap,
    }


async def run_case(
    model, effort, transport, stages=("text", "tool_call", "tool_result")
):
    result = {**case_plan(model, effort), "steps": [], "passed": False}
    provider = AntigravityProvider(
        model,
        reasoning_effort=effort,
        max_tokens=result["max_output_tokens"],
        retry_policy={"max_retries": 0},
        transport=transport,
    )
    messages = [{"role": "user", "content": TEXT_PROMPT}]
    for stage in stages:
        transport.start_step()
        started = time.monotonic()
        step = {"stage": stage, "passed": False}
        try:
            if stage == "tool_call":
                messages = [{"role": "user", "content": TOOL_PROMPT}]
            response = await asyncio.wait_for(
                provider.chat_complete(
                    messages, tools=None if stage == "text" else [TOOL]
                ),
                timeout=90,
            )
            step.update(
                {
                    "finish_reason": response.finish_reason,
                    "text_characters": len(response.content),
                    "tool_calls": len(provider.last_tool_calls),
                    "usage": {
                        key: value
                        for key, value in response.usage.items()
                        if type(value) is int
                    },
                }
            )
            if stage == "tool_call":
                step["part_shapes"] = [
                    {
                        "thought": bool(part.get("thought")),
                        "text_characters": len(part.get("text", "")),
                        "signature_characters": len(part.get("thoughtSignature", "")),
                        "function_call": "functionCall" in part,
                        "has_call_id": bool(part.get("functionCall", {}).get("id")),
                    }
                    for part in provider.last_assistant_extra_fields.get(
                        "_kt_antigravity_content", {}
                    ).get("parts", [])[:40]
                ]
                calls = provider.last_tool_calls
                step["passed"] = (
                    len(calls) == 1
                    and calls[0].name == "probe_echo"
                    and json.loads(calls[0].arguments) == {"value": EXPECTED}
                    and response.finish_reason == "tool_calls"
                )
                if step["passed"]:
                    call = calls[0]
                    messages += [
                        {
                            "role": "assistant",
                            "content": response.content,
                            "tool_calls": [
                                {
                                    "id": call.id,
                                    "type": "function",
                                    "function": {
                                        "name": call.name,
                                        "arguments": call.arguments,
                                    },
                                }
                            ],
                            **provider.last_assistant_extra_fields,
                        },
                        {"role": "tool", "tool_call_id": call.id, "content": EXPECTED},
                    ]
            else:
                step["passed"] = (
                    response.content.strip() == EXPECTED
                    and not provider.last_tool_calls
                    and response.finish_reason == "stop"
                )
        except AntigravityError as exc:
            step["error"] = (
                exc.code
                if re.fullmatch(r"[a-z0-9_]{1,80}", exc.code)
                else "provider_error"
            )
        except Exception as exc:
            step["error"] = type(exc).__name__
        step.update(
            {
                "http_status": transport.last_status,
                "operation": transport.last_operation,
                "duration_seconds": round(time.monotonic() - started, 2),
            }
        )
        if transport.last_diagnostic:
            step["diagnostic"] = transport.last_diagnostic
        result["steps"].append(step)
        if not step["passed"]:
            return result
    result["passed"] = True
    return result


async def run_matrix(selected, output, stages):
    transport = MatrixTransport(
        ProductionTransport(), max_requests=len(stages) * len(selected)
    )
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "planned_inference_limit": transport.max_requests,
        "results": [],
    }
    try:
        for model, effort in selected:
            result = await run_case(model, effort, transport, stages)
            report["results"].append(result)
            report["inference_requests"] = transport.inference_requests
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await transport.inner.aclose()
    return all(result["passed"] for result in report["results"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Make explicitly authorized live requests"
    )
    parser.add_argument(
        "--output", type=Path, help="Write the credential-free JSON report"
    )
    parser.add_argument(
        "--case", action="append", choices=[m + ":" + (e or "fixed") for m, e in CASES]
    )
    parser.add_argument("--stages", choices=["all", "text", "tools"], default="all")
    args = parser.parse_args()
    stages = {
        "all": ("text", "tool_call", "tool_result"),
        "text": ("text",),
        "tools": ("tool_call", "tool_result"),
    }[args.stages]
    selected = [
        (m, e)
        for m, e in CASES
        if not args.case or m + ":" + (e or "fixed") in args.case
    ]
    if not args.run:
        print(
            json.dumps(
                {
                    "max_inference_requests": len(stages) * len(selected),
                    "cases": [case_plan(m, e) for m, e in selected],
                },
                indent=2,
            )
        )
        return 0
    if args.output is None or not args.output.parent.is_dir():
        parser.error("--run requires --output with an existing parent directory")
    return 0 if asyncio.run(run_matrix(selected, args.output, stages)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
