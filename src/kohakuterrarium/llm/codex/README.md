# llm/codex

Codex provider for ChatGPT-subscription accounts. The prefix `codex_` is
dropped from file names because the folder carries it.

```
codex/
├── provider.py        CodexOAuthProvider (BaseLLMProvider + CodexWSMixin)
├── ws.py              CodexWSMixin: WebSocket turn transport for the provider
├── stream.py          Responses event processing and WebSocket turn streaming
├── format.py          Responses-API message shapes, tool-call pairing fixes
├── auth.py            OAuth PKCE flows (browser, device code), token cache
├── claims.py          account id from the ID token
├── rate_limits.py     rate-limit / credit / usage snapshots and cache
├── web_search.py      subscription-backed web search backend
├── presets.py         model presets (pure data)
└── image/
    ├── budget.py      outbound image limits, image-count rejection recognition
    └── gen.py         image-generation request/response translation
```

Shared Responses-API transport (`responses_ws.py`, `responses_reasoning.py`,
`responses_tools.py`) stays in `llm/` because the OpenAI provider uses it too.

Tests mirror the tree under `tests/unit/llm/codex/`.
