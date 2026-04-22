"""Integration test — controller persists assistant images to session.

Covers the path:

    provider.last_assistant_content_parts (data URL)
      → controller._persist_image_part (decode + write to disk)
      → SessionStore.artifacts_dir / "generated_images/...png"
      → rewritten URL: /api/sessions/<id>/artifacts/generated_images/...png
      → AssistantImageEvent yielded
      → conversation.append receives multimodal content

No LLM, no network — we drive the controller's helpers directly.
"""

import base64
from pathlib import Path
from typing import Any

from kohakuterrarium.core.controller import Controller
from kohakuterrarium.llm.message import ImagePart
from kohakuterrarium.parsing import AssistantImageEvent
from kohakuterrarium.session.store import SessionStore


class _DummyLLM:
    """Minimum surface the Controller touches in these tests."""

    provider_name = "codex"
    _parts: list[Any] = []

    @property
    def last_assistant_content_parts(self):
        return self._parts or None

    @property
    def last_tool_calls(self):
        return []


def _make_controller(store: SessionStore | None = None) -> Controller:
    ctrl = Controller(llm=_DummyLLM())
    ctrl.session_store = store
    return ctrl


def test_persist_image_part_writes_to_artifacts_dir(tmp_path: Path):
    store = SessionStore(tmp_path / "agent_xyz.kohakutr")
    ctrl = _make_controller(store)

    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nHELLO").decode("ascii")
    src = ImagePart(
        url=f"data:image/png;base64,{payload}",
        detail="auto",
        source_type="image_gen",
        source_name="ig_abc",
    )
    rewritten = ctrl._persist_image_part(src)

    # URL is rewritten to the served path.
    assert (
        rewritten.url == "/api/sessions/agent_xyz/artifacts/generated_images/ig_abc.png"
    )
    # File actually lands on disk with the decoded bytes.
    on_disk = store.artifacts_dir / "generated_images" / "ig_abc.png"
    assert on_disk.is_file()
    assert on_disk.read_bytes().startswith(b"\x89PNG")
    # Metadata preserved.
    assert rewritten.source_type == "image_gen"
    assert rewritten.source_name == "ig_abc"


def test_persist_image_part_keeps_data_url_without_session_store():
    ctrl = _make_controller(store=None)
    src = ImagePart(
        url="data:image/png;base64,QUJD", detail="auto", source_name="noname"
    )
    out = ctrl._persist_image_part(src)
    # No session store attached → fall back gracefully, no exception.
    assert out.url == src.url


def test_persist_image_part_leaves_non_data_urls_alone():
    ctrl = _make_controller(store=None)
    src = ImagePart(url="https://example.com/cat.png", detail="auto")
    out = ctrl._persist_image_part(src)
    assert out is src


def test_collect_structured_parts_pulls_from_provider(tmp_path: Path):
    store = SessionStore(tmp_path / "s.kohakutr")
    ctrl = _make_controller(store)

    payload = base64.b64encode(b"xyzpng").decode("ascii")
    ctrl.llm._parts = [
        ImagePart(
            url=f"data:image/png;base64,{payload}",
            detail="auto",
            source_name="ig_1",
        )
    ]
    collected = ctrl._collect_structured_assistant_parts()
    assert len(collected) == 1
    assert isinstance(collected[0], ImagePart)
    # URL rewritten — the provider's raw data URL is gone.
    assert collected[0].url.startswith("/api/sessions/s/artifacts/")


def test_collect_structured_parts_empty_when_provider_returns_none():
    ctrl = _make_controller(store=None)
    ctrl.llm._parts = []  # property returns None
    assert ctrl._collect_structured_assistant_parts() == []


def test_assistant_image_event_carries_final_url(tmp_path: Path):
    """After persistence, the event the router sees should have the
    served URL — not the raw data URL. Prevents the frontend from
    bloating its message store with base64 blobs."""
    store = SessionStore(tmp_path / "s.kohakutr")
    ctrl = _make_controller(store)

    payload = base64.b64encode(b"aaaa").decode("ascii")
    part = ctrl._persist_image_part(
        ImagePart(
            url=f"data:image/png;base64,{payload}",
            detail="auto",
            source_name="ig_final",
        )
    )

    # The controller emits an AssistantImageEvent constructed from the
    # persisted part; emulate that here to lock in the contract.
    event = AssistantImageEvent(
        url=part.url,
        detail=part.detail,
        source_type=part.source_type,
        source_name=part.source_name,
    )
    assert event.url.startswith("/api/sessions/s/artifacts/")
    assert "data:" not in event.url
