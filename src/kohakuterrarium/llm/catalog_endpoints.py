"""Inference endpoints with built-in model catalog support."""

import re

MIMO_ENDPOINT = "https://api.xiaomimimo.com/v1"

CATALOG_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    "opencode-go": "https://opencode.ai/inference/go/openai/v1",
    "minimax": "https://api.minimax.io/v1",
}

# These providers resolve environment keys through the configured backend record.
CATALOG_KEY_ENVS = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "opencode-go": "OPENCODE_GO_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}

_QWEN_WORKSPACE = re.compile(
    r"https://[a-z0-9-]+\.(?:cn-beijing|ap-southeast-1|cn-hongkong|ap-northeast-1|eu-central-1)"
    r"\.maas\.aliyuncs\.com/compatible-mode/v1"
)


def catalog_kind(base_url: str) -> str:
    """Identify a catalog by the configured inference endpoint, including aliases."""
    if not isinstance(base_url, str):
        return ""
    url = base_url.rstrip("/")
    if url == MIMO_ENDPOINT:
        return "mimo"
    if url == CATALOG_ENDPOINTS["deepseek"] + "/v1":
        return "deepseek"
    if _QWEN_WORKSPACE.fullmatch(url):
        return "qwen"
    return next(
        (name for name, endpoint in CATALOG_ENDPOINTS.items() if endpoint == url), ""
    )


def looks_like_bearer_endpoint(base_url: str) -> bool:
    lowered = (base_url or "").lower()
    return (
        "minimax" in lowered
        or "minimaxi" in lowered
        or lowered.rstrip("/") == "https://open.bigmodel.cn/api/anthropic"
    )
