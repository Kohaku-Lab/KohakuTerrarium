"""Unit tests for :mod:`kohakuterrarium.session.embedding`.

Note: The provider-specific classes (Model2Vec / SentenceTransformer / API)
depend on third-party libraries or the network. Those paths are exercised
via mock injection; the real model loading is out of scope per the
``CLAUDE.md`` policy on 3rd-party deterministic-test exceptions.
"""

import numpy as np
import pytest

from kohakuterrarium.session import embedding as emb_mod
from kohakuterrarium.session.embedding import (
    APIEmbedder,
    BaseEmbedder,
    DEFAULT_M2V_MODEL,
    DEFAULT_ST_MODEL,
    MODEL2VEC_PRESETS,
    Model2VecEmbedder,
    NullEmbedder,
    SentenceTransformerEmbedder,
    ST_PRESETS,
    _detect_best_provider,
    _resolve_preset,
    create_embedder,
    list_embedding_presets,
)

# ── _resolve_preset ───────────────────────────────────────────────


class TestResolvePreset:
    def test_known_preset(self):
        out = _resolve_preset("@retrieval", MODEL2VEC_PRESETS)
        # The "@name" form resolves to the registered preset dict.
        assert out is MODEL2VEC_PRESETS["retrieval"]
        assert isinstance(out["model"], str) and out["model"]

    def test_unknown_preset_returns_none(self):
        assert _resolve_preset("@not-real", MODEL2VEC_PRESETS) is None

    def test_non_preset_returns_none(self):
        assert _resolve_preset("plain-model-name", MODEL2VEC_PRESETS) is None


# ── _detect_best_provider ─────────────────────────────────────────


class TestDetectBestProvider:
    def test_prefers_model2vec(self, monkeypatch):
        # If model2vec is importable (the common case), it's the pick.
        import sys

        # Force its presence:
        monkeypatch.setitem(sys.modules, "model2vec", object())
        assert _detect_best_provider() == "model2vec"

    def test_falls_back_to_sentence_transformer(self, monkeypatch):
        import builtins
        import sys

        # Remove model2vec from sys.modules and force its import to fail.
        sys.modules.pop("model2vec", None)
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "model2vec":
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.setitem(sys.modules, "sentence_transformers", object())
        assert _detect_best_provider() == "sentence-transformer"

    def test_no_libs_returns_none(self, monkeypatch):
        import builtins
        import sys

        sys.modules.pop("model2vec", None)
        sys.modules.pop("sentence_transformers", None)
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("model2vec", "sentence_transformers"):
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert _detect_best_provider() == "none"


# ── list_embedding_presets ────────────────────────────────────────


class TestListPresets:
    def test_returns_both_groups(self):
        out = list_embedding_presets()
        assert "model2vec" in out
        assert "sentence-transformer" in out
        assert out["model2vec"] is MODEL2VEC_PRESETS
        assert out["sentence-transformer"] is ST_PRESETS


# ── NullEmbedder ──────────────────────────────────────────────────


class TestNullEmbedder:
    def test_encode_raises(self):
        emb = NullEmbedder()
        assert emb.dimensions == 0
        with pytest.raises(RuntimeError, match="No embedding model"):
            emb.encode(["x"])


# ── BaseEmbedder.encode_one ───────────────────────────────────────


class _StubEmbedder(BaseEmbedder):
    dimensions = 3

    def encode(self, texts):
        return np.array([[1.0, 2.0, 3.0]] * len(texts), dtype=np.float32)


class TestBaseEmbedderHelpers:
    def test_encode_one_returns_1d(self):
        e = _StubEmbedder()
        vec = e.encode_one("hi")
        # encode_one unwraps the batch dim and returns the single vector.
        assert vec.shape == (3,)
        assert list(vec) == [1.0, 2.0, 3.0]


# ── create_embedder ───────────────────────────────────────────────


class TestCreateEmbedder:
    def test_none_config_returns_null(self):
        emb = create_embedder(None)
        assert isinstance(emb, NullEmbedder)

    def test_empty_dict_returns_null(self):
        emb = create_embedder({})
        assert isinstance(emb, NullEmbedder)

    def test_explicit_none_provider(self):
        emb = create_embedder({"provider": "none"})
        assert isinstance(emb, NullEmbedder)

    def test_unknown_provider_falls_back_to_null(self):
        emb = create_embedder({"provider": "magic"})
        assert isinstance(emb, NullEmbedder)

    def test_api_provider_missing_key_raises(self, monkeypatch):
        # Both api_key absent and the env var unset.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="api_key"):
            create_embedder({"provider": "api"})

    def test_api_provider_with_explicit_key(self):
        emb = create_embedder(
            {
                "provider": "api",
                "api_key": "sk-test",
                "model": "embed-X",
                "base_url": "http://localhost:1234/v1",
                "dimensions": 128,
            }
        )
        assert isinstance(emb, APIEmbedder)
        assert emb._model == "embed-X"
        assert emb.dimensions == 128

    def test_api_provider_uses_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "from-env")
        emb = create_embedder({"provider": "api", "api_key_env": "MY_KEY"})
        assert isinstance(emb, APIEmbedder)
        # The key named by api_key_env was read from the environment
        # and wired into the HTTP client's auth header.
        assert emb._client.headers["Authorization"] == "Bearer from-env"

    def test_model2vec_uses_preset(self, monkeypatch):
        # Stub the Model2VecEmbedder __init__ to avoid loading a real model.
        captured = {}

        def fake_init(self, model_name=DEFAULT_M2V_MODEL):
            captured["model"] = model_name
            self.dimensions = 64
            self._model = None

        monkeypatch.setattr(Model2VecEmbedder, "__init__", fake_init)
        emb = create_embedder({"provider": "model2vec", "model": "@retrieval"})
        assert isinstance(emb, Model2VecEmbedder)
        # Preset expanded.
        assert captured["model"] == MODEL2VEC_PRESETS["retrieval"]["model"]

    def test_sentence_transformer_uses_preset(self, monkeypatch):
        captured = {}

        def fake_init(self, model_name=DEFAULT_ST_MODEL, dimensions=None, device="cpu"):
            captured["model"] = model_name
            captured["dimensions"] = dimensions
            captured["device"] = device
            self.dimensions = dimensions or 768
            self._model = None
            self._truncate_dim = dimensions

        monkeypatch.setattr(SentenceTransformerEmbedder, "__init__", fake_init)
        emb = create_embedder(
            {
                "provider": "sentence-transformer",
                "model": "@gemma",
                "device": "cpu",
            }
        )
        assert isinstance(emb, SentenceTransformerEmbedder)
        assert captured["model"] == ST_PRESETS["gemma"]["model"]
        assert captured["device"] == "cpu"

    def test_auto_resolves_to_available(self, monkeypatch):
        monkeypatch.setattr(emb_mod, "_detect_best_provider", lambda: "none")
        emb = create_embedder({"provider": "auto"})
        assert isinstance(emb, NullEmbedder)


# ── Model2VecEmbedder (real, deterministic — model2vec is a dep) ──


class TestModel2VecEmbedder:
    def test_real_encode_round_trip(self):
        # model2vec ships as a project dependency and is fully
        # deterministic — load the small default model and verify the
        # encode contract: (n, dims) float32, dims matches the property.
        emb = Model2VecEmbedder()
        assert emb.dimensions > 0
        vecs = emb.encode(["hello world", "second text"])
        assert vecs.shape == (2, emb.dimensions)
        assert vecs.dtype == np.float32
        # encode_one unwraps the batch dim.
        one = emb.encode_one("solo")
        assert one.shape == (emb.dimensions,)

    def test_import_error_when_model2vec_absent(self, monkeypatch):
        # If model2vec can't be imported the constructor raises a clear
        # ImportError naming the install command.
        import builtins

        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "model2vec":
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match="model2vec"):
            Model2VecEmbedder()


# ── SentenceTransformerEmbedder (import-guard only — broken in env) ─


class TestSentenceTransformerEmbedder:
    def test_import_error_when_sentence_transformers_absent(self, monkeypatch):
        import builtins

        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match="sentence-transformers"):
            SentenceTransformerEmbedder()


# ── APIEmbedder.encode (mocked HTTP) ─────────────────────────────


class TestAPIEmbedderEncode:
    def test_encode_round_trip(self, monkeypatch):
        # Mock httpx.Client.post to return a fake embedding response.
        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": [
                        {"index": 1, "embedding": [0.1, 0.2]},
                        {"index": 0, "embedding": [0.3, 0.4]},
                    ]
                }

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def post(self, url, json):
                return _Resp()

        monkeypatch.setattr(emb_mod.httpx, "Client", _Client)
        emb = APIEmbedder(api_key="k", model="m", dimensions=2)
        vecs = emb.encode(["a", "b"])
        # Sorted by index ascending; float32 precision so use approx.
        assert vecs[0] == pytest.approx([0.3, 0.4], abs=1e-5)
        assert vecs[1] == pytest.approx([0.1, 0.2], abs=1e-5)

    def test_encode_auto_detects_dimensions(self, monkeypatch):
        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": [
                        {"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]},
                    ]
                }

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def post(self, url, json):
                return _Resp()

        monkeypatch.setattr(emb_mod.httpx, "Client", _Client)
        emb = APIEmbedder(api_key="k", model="m")
        # Force the "auto-detect" path by zeroing dimensions first.
        emb.dimensions = 0
        vecs = emb.encode(["x"])
        assert vecs.shape == (1, 4)
        assert emb.dimensions == 4
