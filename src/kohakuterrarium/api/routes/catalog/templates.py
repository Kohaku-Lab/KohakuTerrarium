"""Templates route — list + render the scaffolding templates."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from kohakuterrarium.studio.editors.templates import render

router = APIRouter()


_TEMPLATES = [
    {
        "id": "tool-minimal",
        "kind": "tools",
        "label": "Minimal tool",
        "template": "tool.py.j2",
    },
    {
        "id": "creature-minimal",
        "kind": "creatures",
        "label": "Minimal creature",
        "template": "creature_config.yaml.j2",
    },
    {
        "id": "system-prompt",
        "kind": "creatures",
        "label": "System prompt starter",
        "template": "system_prompt.md.j2",
    },
]


class RenderBody(BaseModel):
    id: str
    context: dict = Field(default_factory=dict)


@router.get("")
async def list_templates() -> list[dict]:
    return _TEMPLATES


@router.post("/render")
async def render_template(body: RenderBody) -> dict:
    t = next((t for t in _TEMPLATES if t["id"] == body.id), None)
    if t is None:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"no template with id {body.id!r}",
            },
        )
    try:
        out = render(t["template"], **body.context)
    except Exception as e:
        raise HTTPException(
            400,
            detail={
                "code": "render_failed",
                "message": str(e),
            },
        )
    return {"id": body.id, "source": out}
