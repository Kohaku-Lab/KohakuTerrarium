"""Read and update shared or per-user UI preferences.

Authenticated multi-user requests use the user's preference store. Anonymous
and single-user requests retain the shared configuration-backed behavior.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from kohakuterrarium.api.auth import User, get_optional_user
from kohakuterrarium.studio.identity.ui_prefs import load_prefs, save_prefs

router = APIRouter()


class UIPrefsUpdateRequest(BaseModel):
    """Carry UI preference keys and replacement values."""

    values: dict[str, Any] = Field(default_factory=dict)


@router.get("/ui-prefs")
async def get_ui_prefs(user: User | None = Depends(get_optional_user)):
    """Return preferences from the current user or shared store."""
    return {"values": load_prefs(user_id=user.id if user else None)}


@router.post("/ui-prefs")
async def update_ui_prefs(
    req: UIPrefsUpdateRequest,
    user: User | None = Depends(get_optional_user),
):
    """Persist preferences to the current user or shared store."""
    return {"values": save_prefs(req.values or {}, user_id=user.id if user else None)}
