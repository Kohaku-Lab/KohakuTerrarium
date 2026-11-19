"""Create local or API embedding providers for session memory search."""

import hashlib
import json
import os
from abc import ABC, abstractmethod
from typing import Any

import httpx
import numpy as np

from kohakuterrarium.session.memory.embedding_spec import (
    DEFAULT_M2V_MODEL as DEFAULT_M2V_MODEL,
    DEFAULT_ST_MODEL as DEFAULT_ST_MODEL,
    MODEL2VEC_PRESETS,
    ST_PRESETS,
    _resolve_preset as _resolve_preset,
    canonical_embedding_spec,
)

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class BaseEmbedder(ABC):
    """Abstract embedding provider."""

    dimensions: int = 0

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode texts as a float32 matrix with one row per input."""
        ...

    def encode_one(self, text: str) -> np.ndarray:
        """Encode a single text. Returns 1D vector."""
        return self.encode([text])[0]


def embedder_identity(embedder: BaseEmbedder) -> str:
    """Stable model identity for vector index checkpoints.

    Include the concrete class and its initialized scalar configuration, not
    mutable runtime buffers or HTTP clients. Custom embedders with opaque
    configuration may supply an explicit index_identity string.
    """
    explicit = getattr(embedder, "index_identity", None)
    config = (
        {"identity": explicit}
        if isinstance(explicit, str) and explicit
        else {
            key: value
            for key, value in vars(embedder).items()
            if type(value) in (str, int, float, bool, type(None))
        }
    )
    descriptor = {
        "class": f"{type(embedder).__module__}.{type(embedder).__qualname__}",
        "dimensions": embedder.dimensions,
        "config": config,
    }
    return hashlib.sha256(
        json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class Model2VecEmbedder(BaseEmbedder):
    """Provide lightweight CPU-only static embeddings through Model2Vec."""

    def __init__(self, model_name: str = "minishlab/potion-base-8M"):
        try:
            from model2vec import StaticModel
        except ImportError:
            raise ImportError(
                "model2vec is required for Model2Vec embeddings. "
                "Install: pip install model2vec"
            )

        logger.info("Loading Model2Vec", model=model_name)
        self._model_name = model_name
        self._model = StaticModel.from_pretrained(model_name)
        self.dimensions = self._model.dim
        logger.info("Model2Vec loaded", model=model_name, dimensions=self.dimensions)

    def encode(self, texts: list[str]) -> np.ndarray:
        return self._model.encode(texts).astype(np.float32)


class SentenceTransformerEmbedder(BaseEmbedder):
    """Provide configurable SentenceTransformer embeddings."""

    def __init__(
        self,
        model_name: str = "google/embeddinggemma-300m",
        dimensions: int | None = None,
        device: str = "cpu",
    ):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers is required. "
                "Install: pip install sentence-transformers"
            )

        logger.info("Loading SentenceTransformer", model=model_name)
        self._model_name = model_name
        # Retrieval-capable Jina models require a default task; others ignore it.
        self._model = SentenceTransformer(
            model_name,
            device=device,
            trust_remote_code=True,
            model_kwargs={"default_task": "retrieval"},
        )
        self._truncate_dim = dimensions
        # Probe models whose dimension accessor is unavailable.
        auto_dim = self._model.get_sentence_embedding_dimension()
        if auto_dim is None:
            probe = self._model.encode(["test"])
            auto_dim = probe.shape[1]
        self.dimensions = dimensions or auto_dim
        logger.info(
            "SentenceTransformer loaded",
            model=model_name,
            dimensions=self.dimensions,
        )

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, normalize_embeddings=True)
        if self._truncate_dim and vecs.shape[1] > self._truncate_dim:
            vecs = vecs[:, : self._truncate_dim]
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1
            vecs = vecs / norms
        return vecs.astype(np.float32)


class APIEmbedder(BaseEmbedder):
    """Use an OpenAI-compatible HTTP embeddings endpoint."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        dimensions: int | None = None,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        self._model = model
        self._base_url = base_url
        self.dimensions = dimensions or 1536
        self._request_dims = dimensions

    def encode(self, texts: list[str]) -> np.ndarray:
        body: dict[str, Any] = {
            "input": texts,
            "model": self._model,
        }
        if self._request_dims:
            body["dimensions"] = self._request_dims

        resp = self._client.post("/embeddings", json=body)
        resp.raise_for_status()
        data = resp.json()

        vecs = []
        for item in sorted(data["data"], key=lambda x: x["index"]):
            vecs.append(item["embedding"])

        result = np.array(vecs, dtype=np.float32)
        if self.dimensions == 0:
            self.dimensions = result.shape[1]
        return result


class NullEmbedder(BaseEmbedder):
    """Represent sessions where only keyword search is available."""

    dimensions = 0

    def encode(self, texts: list[str]) -> np.ndarray:
        raise RuntimeError(
            "No embedding model configured. "
            "Use keyword search (mode='fts') or configure an embedding provider."
        )


def _detect_best_provider() -> str:
    """Detect the best available embedding provider.

    Prefer Model2Vec, then SentenceTransformers, then keyword-only search.
    """
    try:
        import model2vec  # noqa: F401

        return "model2vec"
    except ImportError:
        pass
    try:
        import sentence_transformers  # noqa: F401

        return "sentence-transformer"
    except ImportError:
        pass
    return "none"


def list_embedding_presets() -> dict[str, dict[str, dict[str, Any]]]:
    """Return all available presets grouped by provider."""
    return {"model2vec": MODEL2VEC_PRESETS, "sentence-transformer": ST_PRESETS}


def effective_embedding_config(
    config: dict[str, Any] | None,
    embedder: BaseEmbedder | None = None,
) -> dict[str, Any]:
    """Persist concrete model identity while sharing pure spec normalization."""
    if not config:
        return {}
    provider = config.get("provider", "auto")
    if provider == "auto":
        if isinstance(embedder, Model2VecEmbedder):
            provider = "model2vec"
        elif isinstance(embedder, SentenceTransformerEmbedder):
            provider = "sentence-transformer"
        elif isinstance(embedder, APIEmbedder):
            provider = "api"
        elif isinstance(embedder, NullEmbedder):
            provider = "none"
        else:
            provider = _detect_best_provider()
    spec = canonical_embedding_spec(
        config,
        selected_provider=provider,
        selected_model=(
            embedder._model
            if isinstance(embedder, APIEmbedder)
            else getattr(embedder, "_model_name", None)
        ),
    )
    if isinstance(embedder, APIEmbedder):
        spec["base_url"] = embedder._base_url
        if embedder._request_dims is not None:
            spec["dimensions"] = embedder._request_dims
    return spec


def create_embedder(config: dict[str, Any] | None = None) -> BaseEmbedder:
    """Create an embedder from provider, model, dimension, and device settings.

    ``auto`` selects the best installed local provider. ``none`` or missing config
    returns :class:`NullEmbedder`; API providers may load their key from an
    environment variable.
    """
    if not config:
        return NullEmbedder()
    config = effective_embedding_config(config)
    provider = config["provider"]

    match provider:
        case "model2vec":
            return Model2VecEmbedder(model_name=config["model"])

        case "sentence-transformer":
            return SentenceTransformerEmbedder(
                model_name=config["model"],
                dimensions=config.get("dimensions"),
                device=config.get("device", "cpu"),
            )

        case "api":
            api_key = config.get("api_key", "")
            if not api_key:
                api_key_env = config.get("api_key_env", "OPENAI_API_KEY")
                api_key = os.environ.get(api_key_env, "")
            if not api_key:
                raise ValueError("API embedding requires api_key or api_key_env")
            return APIEmbedder(
                api_key=api_key,
                model=config.get("model", "text-embedding-3-small"),
                base_url=config.get("base_url", "https://api.openai.com/v1"),
                dimensions=config.get("dimensions"),
            )

        case "none":
            return NullEmbedder()

        case _:
            logger.warning("Unknown embedding provider, using none", provider=provider)
            return NullEmbedder()
