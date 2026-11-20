"""Provider catalogs through real profile stores and in-memory HTTP."""

import json
import sys
import time

import httpx
import pytest

from kohakuterrarium.llm.discovery import (
    DiscoveredModel,
    ModelDiscoveryError,
    discover_models,
    suggested_presets,
    supports_discovery,
)
from kohakuterrarium.llm.profiles.api_keys import save_api_key
from kohakuterrarium.llm.profiles.backends import load_backends
from kohakuterrarium.llm.profiles.resolve import save_backend
from kohakuterrarium.llm.profiles.types import LLMBackend
from kohakuterrarium.studio.identity.api_keys import get_existing_key
from kohakuterrarium.studio.identity.llm.backends import list_backends, remove_backend
from tests.unit._fakes.package_backend import (
    PROVIDER_SOURCE,
    echo_backend,
    install_backend_package,
    kt_home,  # noqa: F401
)


async def test_provider_failures_do_not_become_empty_results():
    with pytest.raises(ModelDiscoveryError, match="Unknown provider"):
        await discover_models("unknown")
    with pytest.raises(ModelDiscoveryError, match="unavailable"):
        await discover_models("google-antigravity")
    with pytest.raises(ModelDiscoveryError, match="API key"):
        await discover_models("deepseek")
    save_api_key("deepseek", "example")
    for status, data, reason in (
        (403, {"message": "private"}, "HTTP 403"),
        (200, [], "invalid model list"),
        (200, {}, "invalid model list"),
        (200, {"data": [{}]}, "without an ID"),
    ):
        with pytest.raises(ModelDiscoveryError, match=reason) as error:
            await discover_models(
                "deepseek",
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(status, json=data)
                ),
            )
        assert "private" not in str(error.value)

    def offline(request):
        raise httpx.ConnectError("private address")

    with pytest.raises(ModelDiscoveryError, match="Could not reach"):
        await discover_models("deepseek", transport=httpx.MockTransport(offline))
    assert (
        await discover_models(
            "deepseek",
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={"data": []})
            ),
        )
        == []
    )


async def test_qwen_region_and_paginated_text_models():
    backend = load_backends()["qwen"]
    backend.base_url = (
        "https://llm-team.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    save_backend(backend)
    save_api_key("qwen", "region-key")
    pages = []

    def respond(req):
        assert req.url.host == "llm-team.ap-southeast-1.maas.aliyuncs.com"
        assert req.url.path == "/api/v1/models"
        assert req.headers["authorization"] == "Bearer region-key"
        assert req.url.params["capabilities"] == "TG"
        page = int(req.url.params["page_no"])
        pages.append(page)
        rows = (
            [
                {
                    "model": "qwen-one",
                    "provider": "qwen",
                    "capabilities": ["TG"],
                    "model_info": {"context_window": 96000, "max_output_tokens": 4000},
                    "inference_metadata": {"response_modality": ["Text"]},
                },
                {"model": "embed", "capabilities": ["TR"]},
            ]
            if page == 1
            else [{"model": "other", "provider": "other"}, {"model": "qwen-two"}]
        )
        return httpx.Response(
            200, json={"output": {"page_no": page, "total": 4, "models": rows}}
        )

    assert await discover_models("qwen", transport=httpx.MockTransport(respond)) == [
        DiscoveredModel(
            "qwen-one", "qwen-one", 96000, 4000, output_modalities=("text",)
        ),
        DiscoveredModel("qwen-two", "qwen-two"),
    ]
    assert pages == [1, 2]
    with pytest.raises(ModelDiscoveryError, match="incomplete"):
        await discover_models(
            "qwen",
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200,
                    json={"output": {"total": 5, "models": [{"model": "duplicate"}]}},
                )
            ),
        )


async def test_go_public_and_workspace_catalogs_use_separate_credentials():
    requests = []

    def respond(req):
        assert str(req.url) == "https://opencode.ai/inference/go/v1/models"
        requests.append(req.headers.get("authorization"))
        row = {
            "id": "deepseek-go",
            "name": "DeepSeek",
            "type": "chat",
            "limit": {"context": 64000},
            "modalities": {"input": ["text"]},
            "capabilities": {"reasoning": {"effort": ["high"]}},
        }
        return httpx.Response(200, json={"data": [row, row, {"type": "image"}]})

    transport = httpx.MockTransport(respond)
    save_api_key("deepseek", "different-provider")
    assert await discover_models("opencode-go", transport=transport) == [
        DiscoveredModel(
            "deepseek-go",
            "DeepSeek",
            64000,
            input_modalities=("text",),
            reasoning_levels=("high",),
        )
    ]
    save_api_key("opencode-go", "workspace")
    await discover_models("opencode-go", transport=transport)
    assert requests == [None, "Bearer workspace"]


async def test_named_alias_reads_its_own_key_and_catalog():
    backend = load_backends()["deepseek"]
    backend.name = "work-deepseek"
    backend.api_key_env = "WORK_DEEPSEEK_KEY"
    save_backend(backend)
    save_api_key("work-deepseek", "work-only")

    def reply(request):
        assert request.headers["authorization"] == "Bearer work-only"
        return httpx.Response(200, json={"data": [{"id": "work-model"}]})

    assert await discover_models(
        "work-deepseek", transport=httpx.MockTransport(reply)
    ) == [DiscoveredModel("work-model", "work-model")]


async def test_mimo_lists_current_chat_models_using_its_api_key():
    with pytest.raises(ModelDiscoveryError, match="API key"):
        await discover_models("mimo")
    save_api_key("mimo", "mimo-only")

    def reply(req):
        assert str(req.url) == "https://api.xiaomimimo.com/v1/models"
        assert req.headers["api-key"] == "mimo-only"
        assert "authorization" not in req.headers
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": name}
                    for name in (
                        "mimo-v2.6-flash",
                        "mimo-v2.6-pro",
                        "mimo-v2.5-tts-voiceclone",
                        "mimo-v2.5-asr",
                        "future-chat-model",
                    )
                ]
            },
        )

    models = await discover_models("mimo", transport=httpx.MockTransport(reply))
    assert [model.id for model in models] == [
        "mimo-v2.6-flash",
        "mimo-v2.6-pro",
        "future-chat-model",
    ]


async def test_qwen_rejects_malformed_capabilities():
    save_api_key("qwen", "test")
    payload = {
        "output": {"total": 1, "models": [{"model": "broken", "capabilities": None}]}
    }
    with pytest.raises(ModelDiscoveryError, match="capabilities"):
        await discover_models(
            "qwen",
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json=payload)
            ),
        )


def test_existing_user_backend_named_deepseek_keeps_custom_provider_controls():
    from_record = load_backends()["deepseek"]
    from_record.base_url = "https://example.com/private/v1"
    from_record.api_key_env = "CUSTOM_KEY"
    save_backend(from_record)
    row = next(r for r in list_backends() if r["name"] == "deepseek")
    assert row["source"] == "user" and not row["built_in"]
    assert remove_backend("deepseek")
    assert load_backends()["deepseek"].base_url == "https://api.deepseek.com"


async def test_package_opt_in_and_scoped_snapshot_template(kt_home):
    module = install_backend_package(
        kt_home,
        "demo",
        [
            dict(
                echo_backend(credentials="external", model_discovery=True),
                **{"class": "CatalogProvider"},
            )
        ],
        source=PROVIDER_SOURCE + """
from kohakuterrarium.llm.discovery import DiscoveredModel
class CatalogProvider(EchoProvider):
    @classmethod
    async def discover_models(cls, backend):
        assert backend.name == "echo"
        return [DiscoveredModel("team-model-2026-11-01", max_context=16000)]
""",
        presets=[
            {
                "provider": "echo",
                "name": "team-model",
                "model": "team-model",
                "max_context": 64000,
                "max_output": 4000,
            },
            {"provider": "qwen", "name": "other", "model": "other", "max_output": 9000},
        ],
    )
    assert supports_discovery(load_backends()["echo"])
    assert module not in sys.modules
    models = await discover_models("echo")
    assert module in sys.modules
    snapshot, other = suggested_presets(
        "echo", [*models, DiscoveredModel("other-20261101")]
    )
    assert snapshot["model"] == models[0].id
    assert snapshot["max_context"] == 16000 and snapshot["max_output"] == 4000
    assert "max_output" not in other


@pytest.mark.parametrize(
    "line", ['raise RuntimeError("private secret")', "return [DiscoveredModel(123)]"]
)
async def test_package_discovery_failure_is_display_safe(kt_home, line):
    install_backend_package(
        kt_home,
        "demo",
        [dict(echo_backend(model_discovery=True), **{"class": "CatalogProvider"})],
        source=PROVIDER_SOURCE
        + "\nfrom kohakuterrarium.llm.discovery import DiscoveredModel\nclass CatalogProvider(EchoProvider):\n    @classmethod\n    async def discover_models(cls, backend):\n        "
        + line,
    )
    with pytest.raises(ModelDiscoveryError, match="package") as exc:
        await discover_models("echo")
    assert "secret" not in str(exc.value)


def test_empty_legacy_endpoint_keeps_provider_settings_available():
    backend = LLMBackend.from_dict(
        "local", {"backend_type": "openai", "base_url": None}
    )
    assert not supports_discovery(backend)


async def test_qwen_pagination_counts_provider_rows_before_deduplication():
    save_api_key("qwen", "region-key")
    pages = [
        [{"model": "same"}, {"model": "first"}],
        [{"model": "same"}, {"model": "second"}],
    ]

    def reply(request):
        n = int(request.url.params["page_no"])
        rows = pages[n - 1] if n <= len(pages) else []
        return httpx.Response(
            200, json={"output": {"total": 4, "page_no": n, "models": rows}}
        )

    models = await discover_models("qwen", transport=httpx.MockTransport(reply))
    assert [m.id for m in models] == ["same", "first", "second"]


def test_qwen_reads_only_the_credential_configured_for_its_backend(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "official")
    assert get_existing_key("qwen") == "official"
    backend = load_backends()["qwen"]
    backend.base_url = "https://example.invalid/v1"
    backend.api_key_env = "TEAM_KEY"
    save_backend(backend)
    assert not get_existing_key("qwen")
    assert not next(
        row["available"] for row in list_backends() if row["name"] == "qwen"
    )
    monkeypatch.setenv("TEAM_KEY", "custom")
    assert get_existing_key("qwen") == "custom"


async def test_catalog_expands_stored_key_reference(monkeypatch):
    monkeypatch.setenv("DS_LOCAL_KEY", "resolved")
    save_api_key("deepseek", "${DS_LOCAL_KEY}")

    def reply(req):
        assert req.headers["authorization"] == "Bearer resolved"
        return httpx.Response(200, json={"data": []})

    assert await discover_models("deepseek", transport=httpx.MockTransport(reply)) == []


async def test_custom_catalog_refresh_reads_only_configured_endpoint(monkeypatch):
    save_backend(
        LLMBackend("deepseek", "openai", "https://team.example/api/v1", "TEAM_KEY")
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unrelated-official")
    with pytest.raises(ModelDiscoveryError, match="API key"):
        await discover_models("deepseek")
    monkeypatch.setenv("TEAM_KEY", "team-only")
    pages = [["old", "step-asr"], ["new", "step-tts-mini"]]

    def respond(request):
        assert str(request.url) == "https://team.example/api/v1/models"
        assert request.headers["authorization"] == "Bearer team-only"
        return httpx.Response(
            200, json={"data": [{"id": model} for model in pages.pop(0)]}
        )

    transport = httpx.MockTransport(respond)
    for expected in ("old", "new"):
        assert [
            m.id for m in await discover_models("deepseek", transport=transport)
        ] == [expected]


async def test_anonymous_local_catalog_and_unsupported_endpoint(monkeypatch):
    save_backend(LLMBackend("local", "openai", "http://localhost:11434/v1"))
    monkeypatch.setenv("local", "irrelevant-environment-value")

    def respond(request):
        assert "authorization" not in request.headers
        return httpx.Response(404, json={"error": "private body"})

    with pytest.raises(
        ModelDiscoveryError, match="does not expose a model list"
    ) as err:
        await discover_models("local", transport=httpx.MockTransport(respond))
    assert "private" not in str(err.value)


async def test_anthropic_and_gemini_read_every_page():
    for name in ("anthropic", "gemini"):
        save_api_key(name, "private")
        seen = []

        def respond(req):
            seen.append(req.url)
            if name == "anthropic":
                assert (
                    req.headers["x-api-key"] == "private"
                    and "anthropic-version" in req.headers
                )
                assert req.url.path == "/v1/models"
                if len(seen) == 1:
                    return httpx.Response(
                        200,
                        json={
                            "data": [
                                {
                                    "id": "a",
                                    "display_name": "Alpha",
                                    "max_input_tokens": 10000,
                                }
                            ],
                            "has_more": True,
                            "last_id": "a",
                        },
                    )
                assert req.url.params["after_id"] == "a"
                return httpx.Response(
                    200, json={"data": [{"id": "b"}], "has_more": False}
                )
            assert (
                req.headers["x-goog-api-key"] == "private"
                and req.url.path == "/v1beta/models"
            )
            if len(seen) == 1:
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "models/a",
                                "displayName": "Alpha",
                                "inputTokenLimit": 10000,
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/embedding",
                                "supportedGenerationMethods": ["embedContent"],
                            },
                        ],
                        "nextPageToken": "opaque",
                    },
                )
            assert req.url.params["pageToken"] == "opaque"
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/b",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )

        models = await discover_models(name, transport=httpx.MockTransport(respond))
        assert [(m.id, m.name, m.max_context) for m in models] == [
            ("a", "Alpha", 10000),
            ("b", "b", None),
        ]
        assert len(seen) == 2
    with pytest.raises(ModelDiscoveryError, match="pagination"):
        await discover_models(
            "anthropic",
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200, json={"data": [], "has_more": True, "last_id": "same"}
                )
            ),
        )


async def test_openrouter_catalog_respects_account_and_output_modality():
    requests = []

    def respond(req):
        requests.append((req.url.path, req.headers.get("authorization")))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "org/new",
                        "name": "New",
                        "architecture": {"output_modalities": ["text"]},
                        "context_length": 12345,
                        "top_provider": {"max_completion_tokens": 1000},
                    },
                    {
                        "id": "org/image",
                        "architecture": {"output_modalities": ["image"]},
                    },
                ]
            },
        )

    for key in ("", "workspace"):
        save_api_key("openrouter", key)
        models = await discover_models(
            "openrouter", transport=httpx.MockTransport(respond)
        )
        assert models == [
            DiscoveredModel("org/new", "New", 12345, 1000, output_modalities=("text",))
        ]
    assert requests == [
        ("/api/v1/models", None),
        ("/api/v1/models/user", "Bearer workspace"),
    ]


async def test_subscription_catalog_uses_its_own_login(kt_home, monkeypatch):
    (kt_home / "codex-auth.json").write_text(
        json.dumps(
            {
                "access_token": "codex-login",
                "account_id": "account",
                "expires_at": time.time() + 3600,
            }
        )
    )

    def codex(req):
        assert (
            req.url.host == "chatgpt.com"
            and req.url.path == "/backend-api/codex/models"
        )
        assert (
            req.headers["authorization"] == "Bearer codex-login"
            and req.headers["ChatGPT-Account-ID"] == "account"
        )
        return httpx.Response(
            200,
            json={
                "models": [
                    {"slug": "off", "visibility": "hide"},
                    {
                        "slug": "new",
                        "display_name": "New",
                        "context_window": 8192,
                        "supported_reasoning_levels": [{"effort": "medium"}],
                    },
                ]
            },
        )

    assert await discover_models("codex", transport=httpx.MockTransport(codex)) == [
        DiscoveredModel("new", "New", 8192, reasoning_levels=("medium",))
    ]
    grok_file = kt_home / "grok-login.json"
    monkeypatch.setenv("OPENCODE_AUTH_FILE", str(grok_file))
    with pytest.raises(ModelDiscoveryError, match="Sign in"):
        await discover_models("grok-subscription")
    grok_file.write_text(json.dumps({"xai": {"access": "grok-login"}}))

    def grok(req):
        assert (
            str(req.url) == "https://api.x.ai/v1/models"
            and req.headers["authorization"] == "Bearer grok-login"
        )
        return httpx.Response(200, json={"data": [{"id": "future-grok"}]})

    assert [
        m.id
        for m in await discover_models(
            "grok-subscription", transport=httpx.MockTransport(grok)
        )
    ] == ["future-grok"]


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_grok_catalog_tries_alternate_login_only_on_401(
    kt_home, monkeypatch, status
):
    grok_home = kt_home / "grok"
    grok_home.mkdir()
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    (grok_home / "auth.json").write_text(
        json.dumps({"access_token": "first", "expires_at": time.time() + 86400})
    )
    opencode = kt_home / "opencode.json"
    monkeypatch.setenv("OPENCODE_AUTH_FILE", str(opencode))
    opencode.write_text(json.dumps({"xai": {"access": "second"}}))
    monkeypatch.setenv("PATH", "")
    hosts = []

    def reply(req):
        hosts.append(req.url.host)
        if req.url.host == "api.x.ai":
            assert req.headers["authorization"] == "Bearer second"
            return httpx.Response(200, json={"data": [{"id": "grok-test"}]})
        assert req.headers["authorization"] == "Bearer first"
        return httpx.Response(status)

    call = discover_models("grok-subscription", transport=httpx.MockTransport(reply))
    if status == 401:
        assert [m.id for m in await call] == ["grok-test"]
        assert hosts == ["cli-chat-proxy.grok.com", "api.x.ai"]
    else:
        with pytest.raises(ModelDiscoveryError, match=f"HTTP {status}"):
            await call
        assert hosts == ["cli-chat-proxy.grok.com"]
