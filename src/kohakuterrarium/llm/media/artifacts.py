"""Resolve provider media references using explicit sessions or Studio defaults."""

from functools import partial
from pathlib import Path
from typing import Any

from kohakuterrarium.llm.media.reference import (
    artifact_reference,
    current_media_session_path,
    prepare_image_urls,
    resolve_media_url,
    session_media_path,
)
from kohakuterrarium.llm.media.reference import (
    file_reference_path as file_reference_path,
)
from kohakuterrarium.session.store.records.artifacts import resolve_artifact_file
from kohakuterrarium.studio.persistence.artifacts import resolve_artifacts_dir
from kohakuterrarium.studio.persistence.store import _session_dir


def _local_media_path(url: str) -> Path | None:
    """Locate a file reference or a Studio-default artifact."""
    file_path = file_reference_path(url)
    if file_path is not None:
        return file_path
    reference = artifact_reference(url)
    if reference is None:
        return None
    namespace, filename = reference
    return resolve_artifact_file(
        resolve_artifacts_dir(namespace, _session_dir()), filename
    )


def resolve_artifact_url(url: str, *, session_path: str | Path | None = None) -> str:
    """Inline a local reference, preserving its original URL on failure."""
    if session_path is None:
        session_path = current_media_session_path()
    paths = (
        _local_media_path
        if session_path is None
        else partial(session_media_path, session_path=Path(session_path))
    )
    return resolve_media_url(url, paths)


def resolve_message_image_urls(
    messages: list[dict[str, Any]], *, session_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """Prepare effective image blocks, rejecting unavailable required local media."""
    resolver = (
        resolve_artifact_url
        if session_path is None
        else partial(resolve_artifact_url, session_path=session_path)
    )
    return prepare_image_urls(messages, resolver)
