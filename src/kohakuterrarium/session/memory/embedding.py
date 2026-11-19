"""Create local or API embedding providers for session memory search."""

import hashlib
import json
import os
from abc import ABC, abstractmethod
from typing import Any

import httpx
import numpy as np

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


MODEL2VEC_PRESETS: dict[str, dict[str, Any]] = {
    # Compact static models trade retrieval quality for size and latency.
    "tiny": {"model": "minishlab/potion-base-2M"},  # 64 dimensions, about 2 MB
    "base": {"model": "minishlab/potion-base-8M"},  # 256 dimensions, about 8 MB
    "retrieval": {"model": "minishlab/potion-retrieval-32M"},  # 512 dimensions
    # Matryoshka dimensions support smaller indexes without changing models.
    "best": {
        "model": "sentence-transformers/static-retrieval-mrl-en-v1",
    },
    "multilingual": {
        "model": "minishlab/potion-multilingual-128M",  # 101 languages
    },
    "multilingual-best": {
        "model": "sentence-transformers/static-similarity-mrl-multilingual-v1",
    },  # 51 languages
    "science": {"model": "minishlab/potion-science-32M"},  # arXiv and PubMed
}

ST_PRESETS: dict[str, dict[str, Any]] = {
    "tiny": {
        "model": "ibm-granite/granite-embedding-30m-english",  # 512-token context
    },
    "small": {
        "model": "ibm-granite/granite-embedding-small-english-r2",  # 8K context
    },
    "base": {
        "model": "Alibaba-NLP/gte-modernbert-base",  # 8K context
    },
    "nomic": {
        "model": "nomic-ai/nomic-embed-text-v1.5",  # 8K Matryoshka model
    },
    "gemma": {
        "model": "google/embeddinggemma-300m",  # multilingual, 2K context
    },
    "multilingual": {
        "model": "Alibaba-NLP/gte-multilingual-base",  # 70+ languages, 8K context
    },
}

DEFAULT_M2V_MODEL = "sentence-transformers/static-similarity-mrl-multilingual-v1"
DEFAULT_ST_MODEL = "Alibaba-NLP/gte-modernbert-base"


def _resolve_preset(
    model: str, presets: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Resolve an ``@preset`` reference, returning ``None`` when unknown."""
    if model.startswith("@"):
        name = model[1:]
        if name in presets:
            return presets[name]
        logger.warning("Unknown embedding preset", preset=name)
    return None


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
    """Resolve a restorable embedding spec, not just the optional user input.

    Drop unset fields before any consumer constructs a model. Freeze provider,
    model defaults and presets at persistence time so future default changes
    cannot silently redirect an already-built index to a different model.
    A built-in embedder supplies its actual selected provider/model.
    """
    if not config:
        return {}
    result = {key: value for key, value in config.items() if value is not None}
    provider = result.get("provider", "auto")
    if provider == "auto" and isinstance(embedder, Model2VecEmbedder):
        provider = "model2vec"
    elif provider == "auto" and isinstance(embedder, SentenceTransformerEmbedder):
        provider = "sentence-transformer"
    elif provider == "auto" and isinstance(embedder, APIEmbedder):
        provider = "api"
    elif provider == "auto" and isinstance(embedder, NullEmbedder):
        provider = "none"
    elif provider == "auto":
        provider = _detect_best_provider()
    result["provider"] = provider

    if provider in ("model2vec", "sentence-transformer"):
        default = DEFAULT_M2V_MODEL if provider == "model2vec" else DEFAULT_ST_MODEL
        presets = MODEL2VEC_PRESETS if provider == "model2vec" else ST_PRESETS
        model = getattr(embedder, "_model_name", None) or result.get("model") or default
        if not isinstance(model, str):
            raise ValueError("Embedding model must be a string")
        preset = _resolve_preset(model, presets)
        if preset:
            model = preset["model"]
            if provider == "sentence-transformer" and "dimensions" not in result:
                dimensions = preset.get("dimensions")
                if dimensions is not None:
                    result["dimensions"] = dimensions
        result["model"] = model
    elif provider == "api":
        result["model"] = getattr(embedder, "_model", None) or result.get(
            "model", "text-embedding-3-small"
        )
        if isinstance(embedder, APIEmbedder):
            result["base_url"] = embedder._base_url
            if embedder._request_dims is not None:
                result["dimensions"] = embedder._request_dims
    return result


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
