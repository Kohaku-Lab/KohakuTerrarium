"""On-demand model discovery for one configured backend, independent of chat."""

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from kohakuterrarium import __version__
from kohakuterrarium.llm.catalog_endpoints import (
    catalog_kind,
    looks_like_bearer_endpoint,
)
from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.llm.codex.auth import valid_subscription_tokens
from kohakuterrarium.llm.codex.claims import account_id_from_id_token
from kohakuterrarium.llm.codex.image.budget import CODEX_BASE_URL
from kohakuterrarium.llm.grok.auth import GrokTokens
from kohakuterrarium.llm.presets.catalog import get_all_presets
from kohakuterrarium.llm.profiles.api_keys import backend_api_key
from kohakuterrarium.llm.profiles.backends import load_backends
from kohakuterrarium.llm.profiles.backend_registry import (
    load_backend_class,
    package_backend,
)
from kohakuterrarium.llm.profiles.types import LLMBackend
from kohakuterrarium.utils.env_interp import interpolate_env_vars


class ModelDiscoveryError(ValueError):
    """A display-safe catalog failure."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class _CatalogUnauthorized(ModelDiscoveryError):
    """Credential rejected. A subscription adapter may try its next login."""


@dataclass(frozen=True)
class DiscoveredModel:
    """Provider-reported metadata. Absent limits and capabilities stay unknown."""

    id: str
    name: str = ""
    max_context: int | None = None
    max_output: int | None = None
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()
    reasoning_levels: tuple[str, ...] = ()


def _catalog_kind(backend: LLMBackend, base_url: str) -> str:
    protocol = backend.backend_type
    if protocol == "grok-subscription" or (
        protocol == "codex" and not backend.base_url
    ):
        return protocol
    if protocol in {"openai", "anthropic", "codex"}:
        try:
            url = urlsplit(base_url or "")
            if (
                url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username
                or url.query
                or url.fragment
            ):
                return ""
            url.port  # Validate an explicitly configured port.
        except ValueError:
            return ""
        if protocol == "anthropic":
            return "anthropic"
        endpoint = base_url.rstrip("/")
        if endpoint == "https://generativelanguage.googleapis.com/v1beta/openai":
            return "gemini"
        if endpoint == "https://openrouter.ai/api/v1":
            return "openrouter"
        return catalog_kind(endpoint) or "openai-compatible"
    descriptor = package_backend(backend.backend_type)
    return "package" if descriptor and descriptor.model_discovery else ""


def supports_discovery(backend: LLMBackend) -> bool:
    return bool(_catalog_kind(backend, interpolate_env_vars(backend.base_url)))


def _positive(value) -> int | None:
    return value if type(value) is int and value > 0 else None


def _strings(value) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.lower() for item in value if isinstance(item, str) and item)


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


async def _get(client, url, **params) -> dict:
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {404, 405, 501}:
            raise ModelDiscoveryError(
                f"The configured endpoint does not expose a model list (HTTP {status})."
            ) from None
        if status in {401, 403}:
            error = _CatalogUnauthorized if status == 401 else ModelDiscoveryError
            raise error(
                f"The provider rejected catalog access (HTTP {status}). Check this provider’s credentials and permissions."
            ) from None
        raise ModelDiscoveryError(
            f"The provider refused the model list request (HTTP {exc.response.status_code})."
        ) from None
    except httpx.RequestError:
        raise ModelDiscoveryError("Could not reach the provider. Try again.") from None
    except ValueError:
        raise ModelDiscoveryError(
            "The provider returned an invalid model list."
        ) from None
    if not isinstance(payload, dict):
        raise ModelDiscoveryError("The provider returned an invalid model list.")
    return payload


def _rows(payload, key):
    rows = payload.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ModelDiscoveryError("The provider returned an invalid model list.")
    return rows


def _model_id(row, key="id"):
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ModelDiscoveryError("The provider returned a model without an ID.")
    return value


async def _qwen_models(client, base_url):
    endpoint = urlsplit(base_url)
    if endpoint.path != "/compatible-mode/v1" or endpoint.query or endpoint.fragment:
        raise ModelDiscoveryError("Configure a Qwen compatible-mode/v1 endpoint first.")
    url = endpoint._replace(path="/api/v1/models").geturl()
    models = []
    received = 0
    previous_page = None
    for page in range(1, 201):
        data = await _get(
            client,
            url,
            providers="qwen",
            capabilities="TG",
            page_no=page,
            page_size=100,
        )
        if data.get("success") is False:
            raise ModelDiscoveryError("Qwen refused the model list request.")
        result = _object(data.get("output"))
        rows = _rows(result, "models")
        total = result.get("total")
        if type(total) is not int or total < 0 or result.get("page_no", page) != page:
            raise ModelDiscoveryError("Qwen returned invalid pagination metadata.")
        ids = [_model_id(row, "model") for row in rows]
        if total and (not ids or ("page_no" not in result and ids == previous_page)):
            raise ModelDiscoveryError(
                "Qwen returned an incomplete model list. Refresh to retry."
            )
        previous_page = ids
        received += len(rows)
        for row in rows:
            capabilities = row.get("capabilities", ["TG"])
            if not isinstance(capabilities, list):
                raise ModelDiscoveryError("Qwen returned invalid model capabilities.")
            if row.get("provider", "qwen") != "qwen" or "TG" not in capabilities:
                continue
            info = _object(row.get("model_info"))
            modalities = _object(row.get("inference_metadata"))
            models.append(
                DiscoveredModel(
                    id=row["model"],
                    name=row.get("name") or row["model"],
                    max_context=_positive(info.get("context_window")),
                    max_output=_positive(info.get("max_output_tokens")),
                    input_modalities=_strings(modalities.get("request_modality")),
                    output_modalities=_strings(modalities.get("response_modality")),
                )
            )
        if received >= total:
            return models
    raise ModelDiscoveryError("Qwen exceeded the model catalog page limit.")


async def _fetch_catalog(client, kind, base_url):
    if kind in {"anthropic", "gemini"}:
        return await _paged_models(client, kind, base_url)
    if kind == "codex":
        # Version of the Codex catalog protocol this adapter understands.
        data = await _get(client, base_url + "/models", client_version="0.169.0")
        return [
            DiscoveredModel(
                id=_model_id(row, "slug"),
                name=row.get("display_name") or row["slug"],
                max_context=_positive(row.get("context_window")),
                input_modalities=_strings(row.get("input_modalities")),
                reasoning_levels=tuple(
                    level["effort"]
                    for level in row.get("supported_reasoning_levels") or []
                    if isinstance(level, dict) and isinstance(level.get("effort"), str)
                ),
            )
            for row in _rows(data, "models")
            if row.get("visibility", "list") == "list"
        ]
    if kind == "openrouter":
        path = "/models/user" if "authorization" in client.headers else "/models"
        payload = await _get(client, base_url + path)
        models = []
        for row in _rows(payload, "data"):
            architecture = _object(row.get("architecture"))
            outputs = _strings(architecture.get("output_modalities"))
            if outputs and "text" not in outputs:
                continue
            models.append(
                DiscoveredModel(
                    id=_model_id(row),
                    name=row.get("name") or row["id"],
                    max_context=_positive(row.get("context_length")),
                    max_output=_positive(
                        _object(row.get("top_provider")).get("max_completion_tokens")
                    ),
                    input_modalities=_strings(architecture.get("input_modalities")),
                    output_modalities=outputs,
                )
            )
        return models
    if kind == "qwen":
        return await _qwen_models(client, base_url)
    if kind == "opencode-go":
        payload = await _get(client, "https://opencode.ai/inference/go/v1/models")
        models = []
        for row in _rows(payload, "data"):
            if row.get("type", "chat") != "chat":
                continue
            limits = _object(row.get("limit"))
            modalities = _object(row.get("modalities"))
            reasoning = _object(_object(row.get("capabilities")).get("reasoning"))
            models.append(
                DiscoveredModel(
                    id=_model_id(row),
                    name=row.get("name") or row["id"],
                    max_context=_positive(limits.get("context")),
                    max_output=_positive(limits.get("output")),
                    input_modalities=_strings(modalities.get("input")),
                    output_modalities=_strings(modalities.get("output")),
                    reasoning_levels=_strings(reasoning.get("effort")),
                )
            )
        return models
    payload = await _get(client, base_url + "/models")
    rows = _rows(payload, "data")
    if base_url == "https://api.openai.com/v1":
        # /models includes dedicated media, embedding and legacy completion APIs.
        rows = [row for row in rows if not _NON_CHAT_OPENAI.search(_model_id(row))]
    rows = [row for row in rows if _text_model(row)]
    return [
        DiscoveredModel(
            id=_model_id(row),
            name=row.get("name") or row["id"],
            max_context=_positive(row.get("context_window")),
            max_output=_positive(row.get("max_output_tokens")),
            input_modalities=_strings(row.get("input_modalities")),
            output_modalities=_strings(row.get("output_modalities")),
            reasoning_levels=_strings(
                _object(row.get("effort")).get("supported_levels")
            ),
        )
        for row in rows
    ]


_NON_CHAT_OPENAI = re.compile(
    r"(?:^|[-:])(embedding|moderation|whisper|tts|transcribe|realtime|dall-e|image|sora|babbage|davinci)(?:[-:]|$)"
)


def _text_model(row):
    """Prefer declared modalities. Some compatible APIs only return an ID."""
    outputs = _strings(row.get("output_modalities"))
    if outputs:
        return "text" in outputs
    dedicated = {"tts", "asr", "embedding", "embeddings", "rerank", "reranker"}
    return not dedicated.intersection(re.split(r"[-_/:.]", _model_id(row).lower()))


async def _paged_models(client, kind, base_url):
    gemini = kind == "gemini"
    if gemini:
        url = base_url.removesuffix("/openai") + "/models"
    else:
        url = base_url + ("/models" if base_url.endswith("/v1") else "/v1/models")
    params = {"pageSize": 100} if gemini else {"limit": 100}
    seen = set()
    models = []
    for _ in range(200):
        payload = await _get(client, url, **params)
        if payload.get("success") is False:
            raise ModelDiscoveryError(
                "The configured endpoint refused the model list request."
            )
        for row in _rows(payload, "models" if gemini else "data"):
            if gemini:
                if "generateContent" not in (
                    row.get("supportedGenerationMethods") or []
                ):
                    continue
                model = _model_id(row, "name").removeprefix("models/")
                if not model:
                    raise ModelDiscoveryError(
                        "The provider returned a model without an ID."
                    )
                models.append(
                    DiscoveredModel(
                        model,
                        row.get("displayName") or model,
                        _positive(row.get("inputTokenLimit")),
                        _positive(row.get("outputTokenLimit")),
                    )
                )
            else:
                model = _model_id(row)
                models.append(
                    DiscoveredModel(
                        model,
                        row.get("display_name") or row.get("name") or model,
                        _positive(row.get("max_input_tokens")),
                        _positive(row.get("max_tokens")),
                    )
                )
        if gemini:
            cursor = payload.get("nextPageToken")
            if cursor is None or cursor == "":
                return models
        else:
            if not payload.get("has_more"):
                return models
            cursor = payload.get("last_id")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise ModelDiscoveryError(
                "The provider returned incomplete model list pagination."
            )
        seen.add(cursor)
        params["pageToken" if gemini else "after_id"] = cursor
    raise ModelDiscoveryError("The provider exceeded the model catalog page limit.")


async def _catalog_connection(backend, kind, base_url):
    headers = {"User-Agent": f"KohakuTerrarium/{__version__}"}
    if kind == "codex":
        try:
            tokens = await valid_subscription_tokens()
        except LLMNotConfiguredError as exc:
            raise ModelDiscoveryError(str(exc), 409) from None
        headers["Authorization"] = f"Bearer {tokens.access_token}"
        account = tokens.account_id or account_id_from_id_token(tokens.id_token)
        if account:
            headers["ChatGPT-Account-ID"] = account
        return CODEX_BASE_URL, headers
    key = await asyncio.to_thread(backend_api_key, backend.name, backend.api_key_env)
    optional_key = kind in {"opencode-go", "openrouter"} or (
        kind in {"openai-compatible", "anthropic"} and not backend.api_key_env
    )
    if not key and not optional_key:
        raise ModelDiscoveryError(
            f"Configure an API key for {backend.name} first.", 409
        )
    if kind == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    if key:
        if kind == "mimo":
            headers["api-key"] = key
        elif kind == "gemini":
            headers["x-goog-api-key"] = key
        elif (
            kind == "anthropic"
            and not looks_like_bearer_endpoint(base_url)
            and base_url != "https://api.kimi.com/coding"
        ):
            headers["x-api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
    return base_url, headers


async def _read_catalog(kind, base_url, headers, transport):
    async with httpx.AsyncClient(
        transport=transport, timeout=15, follow_redirects=False, headers=headers
    ) as client:
        return await _fetch_catalog(client, kind, base_url)


async def _grok_catalog(transport):
    failure = ModelDiscoveryError("Sign in to Grok first.", 409)
    async for token in GrokTokens.request_candidates(set()):
        headers = {
            "User-Agent": f"KohakuTerrarium/{__version__}",
            **token.extra_headers,
            "Authorization": f"Bearer {token.access_token}",
        }
        try:
            return await _read_catalog(
                "grok-subscription", token.base_url, headers, transport
            )
        except _CatalogUnauthorized as exc:
            failure = exc
    raise failure


async def discover_models(
    backend_name: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[DiscoveredModel]:
    """Read one catalog without sending a prompt or modifying a profile."""
    backend = (await asyncio.to_thread(load_backends)).get(backend_name)
    if backend is None:
        raise ModelDiscoveryError(f"Unknown provider: {backend_name}", 404)
    base_url = interpolate_env_vars(backend.base_url) or ""
    kind = _catalog_kind(backend, base_url)
    if not kind:
        raise ModelDiscoveryError(
            f"Model discovery is unavailable for {backend_name}.", 400
        )
    if kind == "package":
        try:
            cls = await asyncio.to_thread(load_backend_class, backend.backend_type)
            models = await cls.discover_models(backend)
        except Exception:
            raise ModelDiscoveryError(
                "The package could not read its model list."
            ) from None
        if not isinstance(models, list) or not all(
            isinstance(model, DiscoveredModel)
            and isinstance(model.id, str)
            and model.id.strip()
            for model in models
        ):
            raise ModelDiscoveryError("The package returned an invalid model list.")
    elif kind == "grok-subscription":
        models = await _grok_catalog(transport)
    else:
        base_url, headers = await _catalog_connection(
            backend, kind, base_url.rstrip("/")
        )
        models = await _read_catalog(kind, base_url, headers, transport)
    unique = {}
    for model in models:
        unique.setdefault(model.id, model)
    return list(unique.values())


def suggested_presets(provider: str, models: list[DiscoveredModel]) -> list[dict]:
    """Suggest drafts in catalog order, using curated limits for dated snapshots."""
    templates = {}
    for (owner, name), data in get_all_presets().items():
        if owner == provider and data.get("model") == name:
            templates[name] = data
    backend = load_backends().get(provider)
    kind = (
        catalog_kind(interpolate_env_vars(backend.base_url))
        if backend and backend.backend_type == "openai"
        else ""
    )
    return [_preset_fields(provider, model, kind, templates) for model in models]


def _preset_fields(provider, model, kind, templates):
    fields = {
        "provider": provider,
        "name": re.sub(r"[/@]", "-", model.id),
        "model": model.id,
    }
    family = re.fullmatch(r"(.+)-(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{8})", model.id)
    template = templates.get(family[1], {}) if family else {}
    for key in ("max_context", "max_output"):
        limit = getattr(model, key) or _positive(template.get(key))
        if limit is not None:
            fields[key] = limit
    if kind == "deepseek" and model.reasoning_levels:
        fields["variation_groups"] = {
            "reasoning": {
                level: {"extra_body.reasoning_effort": level}
                for level in model.reasoning_levels
            }
        }
    return fields
