"""Session-summary settings: ``GET`` / ``PUT /api/settings/session-summary``."""

import os
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.studio.identity.session_summary import (
    SOURCE_ENV,
    SOURCES,
    load_settings,
    save_settings,
)

router = APIRouter()


def _view() -> dict[str, Any]:
    override = os.environ.get(SOURCE_ENV, "").strip()
    return {
        **asdict(load_settings(apply_env=False)),
        "sources": list(SOURCES),
        "source_override": override if override in SOURCES else None,
    }


@router.get("/session-summary")
async def get_session_summary_settings() -> dict[str, Any]:
    return _view()


@router.put("/session-summary", dependencies=[Depends(verify_admin_token)])
async def put_session_summary_settings(body: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        k: v for k, v in body.items() if k in ("source", "every_n_turns", "model")
    }
    try:
        save_settings(allowed)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _view()
