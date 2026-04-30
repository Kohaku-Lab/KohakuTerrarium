"""Pre-send content-part sanitisation and request-shape diagnostics
for the OpenAI-compatible provider.

Lifted out of :mod:`kohakuterrarium.llm.openai` to keep that module
under the 600-line soft cap. Imported only by ``openai.py``; tests
may import directly.

The sanitiser exists because strict OpenAI-compatible providers
(vLLM, SGLang, MiMo, …) drop or ignore content parts whose top-level
shape carries unknown keys. KohakuTerrarium's :class:`ImagePart`
serialises a ``meta`` field carrying chat-panel badge metadata
(filename, source kind); that field is invisible to the rendering
chain and breaks vision input on strict providers. We strip it at
the provider boundary so storage stays lossless and the wire stays
schema-clean.
"""

from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Allowed top-level keys per content-part type, per the OpenAI Chat
# Completions content-part schema. KT serializers (``ImagePart.to_dict``,
# ``TextPart.to_dict``) sometimes emit additional fields that carry
# UI-rendering hints (badge metadata, source filename, …). Those must
# never reach the provider — strict OpenAI-compatible servers reject or
# silently drop the entire part when they encounter unknown keys.
_OAI_PART_ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "text": frozenset({"type", "text"}),
    "image_url": frozenset({"type", "image_url"}),
}

# Inside the ``image_url`` object, only ``url`` and ``detail`` are
# documented. Everything else is dropped.
_OAI_IMAGE_URL_ALLOWED_KEYS: frozenset[str] = frozenset({"url", "detail"})


def strip_kt_extras(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Strip KohakuTerrarium-internal fields from content parts.

    The visible offender today is :class:`ImagePart`, whose ``to_dict``
    emits a top-level ``meta`` key with chat-panel badge metadata
    (``source_type``, ``source_name``). The OpenAI image_url part
    schema does not declare ``meta``; strict OpenAI-compatible
    providers ignore the entire part when they see unknown keys.

    Only messages with a *list* content (multimodal) are walked.
    Messages with string content are passed through unchanged. Within a
    list, parts of unknown ``type`` are also passed through — we only
    police the schemas we know.

    Allocates new dicts only when a part actually needs cleaning, and
    returns the original list (identity-preserved) when no message in
    the batch needed cleaning at all. The common-case "no images / no
    extras" path stays a no-op.
    """
    out: list[dict[str, Any]] = []
    any_changed = False
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        new_content: list[Any] = []
        msg_changed = False
        for part in content:
            if not isinstance(part, dict):
                new_content.append(part)
                continue
            ptype = part.get("type")
            allowed = _OAI_PART_ALLOWED_KEYS.get(ptype)
            if allowed is None:
                new_content.append(part)
                continue
            iu = part.get("image_url")
            iu_extra = (
                set(iu) - _OAI_IMAGE_URL_ALLOWED_KEYS
                if isinstance(iu, dict)
                else frozenset()
            )
            if set(part) <= allowed and not iu_extra:
                new_content.append(part)
                continue
            cleaned = {k: v for k, v in part.items() if k in allowed}
            if ptype == "image_url" and isinstance(iu, dict):
                cleaned["image_url"] = {
                    k: v for k, v in iu.items() if k in _OAI_IMAGE_URL_ALLOWED_KEYS
                }
            new_content.append(cleaned)
            msg_changed = True
        if msg_changed:
            out.append({**msg, "content": new_content})
            any_changed = True
        else:
            out.append(msg)
    return out if any_changed else messages


def strip_surrogates(text: str) -> str:
    """Drop invalid Unicode surrogate code points from provider text.

    Some streaming APIs occasionally emit lone surrogate characters.
    They are not valid Unicode scalar values and crash later UTF-8
    encoding paths, so provider boundaries should discard them.
    """
    return text.encode("utf-8", errors="ignore").decode("utf-8")


def log_request_shape(msg: str, model: str, messages: list[dict[str, Any]]) -> None:
    """One-line summary of an outgoing request, for diagnosis.

    Counts content-part types per multimodal message so the user can
    confirm at a glance that ``image_url`` parts are present in the
    payload — useful when a provider claims it sees no image. Always
    logs at INFO when any non-text part exists; otherwise DEBUG.
    """
    image_count = 0
    file_count = 0
    multimodal_msgs = 0
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        had_extra = False
        for p in content:
            if not isinstance(p, dict):
                continue
            ptype = p.get("type")
            if ptype == "image_url":
                image_count += 1
                had_extra = True
            elif ptype == "file":
                file_count += 1
                had_extra = True
        if had_extra:
            multimodal_msgs += 1

    if image_count or file_count:
        logger.info(
            msg,
            model=model,
            messages=len(messages),
            multimodal_messages=multimodal_msgs,
            image_parts=image_count,
            file_parts=file_count,
        )
    else:
        logger.debug(msg, model=model, messages=len(messages))
