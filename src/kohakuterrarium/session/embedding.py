"""Create local or API embedding providers for session memory search."""

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


def create_embedder(config: dict[str, Any] | None = None) -> BaseEmbedder:
    """Create an embedder from provider, model, dimension, and device settings.

    ``auto`` selects the best installed local provider. ``none`` or missing config
    returns :class:`NullEmbedder`; API providers may load their key from an
    environment variable.
    """
    if not config:
        return NullEmbedder()

    provider = config.get("provider", "auto")

    if provider == "auto":
        provider = _detect_best_provider()

    match provider:
        case "model2vec":
            model = config.get("model", DEFAULT_M2V_MODEL)
            preset = _resolve_preset(model, MODEL2VEC_PRESETS)
            if preset:
                model = preset["model"]
            return Model2VecEmbedder(model_name=model)

        case "sentence-transformer":
            model = config.get("model", DEFAULT_ST_MODEL)
            dims = config.get("dimensions")
            device = config.get("device", "cpu")
            preset = _resolve_preset(model, ST_PRESETS)
            if preset:
                model = preset["model"]
                dims = dims or preset.get("dimensions")
            return SentenceTransformerEmbedder(
                model_name=model, dimensions=dims, device=device
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
