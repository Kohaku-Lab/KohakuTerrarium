"""Identity UI preferences — theme/zoom/layout state."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from kohakuterrarium.studio.identity.ui_prefs import load_prefs, save_prefs

router = APIRouter()


class UIPrefsUpdateRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("/ui-prefs")
async def get_ui_prefs():
    return {"values": load_prefs()}


@router.post("/ui-prefs")
async def update_ui_prefs(req: UIPrefsUpdateRequest):
    return {"values": save_prefs(req.values or {})}
