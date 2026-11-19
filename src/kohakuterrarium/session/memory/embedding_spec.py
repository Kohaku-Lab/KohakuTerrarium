"""Pure embedding-spec equivalence across SessionStore and model factories.

This module deliberately does not import NumPy, HTTP clients, or model runtimes.
"""

from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

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


def canonical_embedding_spec(
    config: dict[str, Any] | None,
    *,
    anchor: dict[str, Any] | None = None,
    selected_provider: str | None = None,
    selected_model: str | None = None,
) -> dict[str, Any]:
    """Resolve aliases/defaults without loading an embedding model.

    An existing Session's persisted effective spec is authoritative for
    *unspecified* provider/model fields. Explicit model changes remain distinct.
    Normalizing each side makes short config, preset, and concrete-name
    representations comparable without importing embedding runtimes into Store.
    """
    if not config:
        return {}
    spec = {k: v for k, v in config.items() if v is not None}
    provider = selected_provider or spec.get("provider", "auto")
    if provider == "auto" and anchor:
        provider = anchor.get("provider") or provider
    spec["provider"] = provider

    if provider in ("model2vec", "sentence-transformer"):
        default = DEFAULT_M2V_MODEL if provider == "model2vec" else DEFAULT_ST_MODEL
        presets = MODEL2VEC_PRESETS if provider == "model2vec" else ST_PRESETS
        model = selected_model or spec.get("model")
        if not model:
            if anchor and anchor.get("provider") == provider:
                model = anchor.get("model")
            model = model or default
        if not isinstance(model, str):
            raise ValueError("Embedding model must be a string")
        preset = _resolve_preset(model, presets)
        if preset:
            model = preset["model"]
            if provider == "sentence-transformer" and "dimensions" not in spec:
                dims = preset.get("dimensions")
                if dims is not None:
                    spec["dimensions"] = dims
        spec["model"] = model
    elif provider == "api":
        spec["model"] = (
            selected_model
            or spec.get("model")
            or (
                anchor.get("model")
                if anchor and anchor.get("provider") == provider
                else None
            )
            or "text-embedding-3-small"
        )
        if (
            not spec.get("base_url")
            and anchor
            and anchor.get("provider") == "api"
            and anchor.get("base_url")
        ):
            spec["base_url"] = anchor["base_url"]
    return spec
