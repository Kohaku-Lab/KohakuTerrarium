"""Identity UI preferences — theme/zoom/layout state.

When L4 (multi-user) is on, prefs are routed per-user via the
:func:`get_optional_user` dependency.  Anonymous callers (L4 off
or optional) read / write the shared ``<config_dir>/ui_prefs.json``
— preserves single-user behaviour.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from kohakuterrarium.api.auth import User, get_optional_user
from kohakuterrarium.studio.identity.ui_prefs import load_prefs, save_prefs

router = APIRouter()


class UIPrefsUpdateRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("/ui-prefs")
async def get_ui_prefs(user: User | None = Depends(get_optional_user)):
    return {"values": load_prefs(user_id=user.id if user else None)}


@router.post("/ui-prefs")
async def update_ui_prefs(
    req: UIPrefsUpdateRequest,
    user: User | None = Depends(get_optional_user),
):
    return {"values": save_prefs(req.values or {}, user_id=user.id if user else None)}
