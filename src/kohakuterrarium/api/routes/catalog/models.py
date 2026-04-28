"""Catalog models — list available LLM profiles.

Replaces ``api.routes.configs.list_models``.
"""

from fastapi import APIRouter

from kohakuterrarium.llm.profiles import list_all as list_all_models

router = APIRouter()


@router.get("")
async def list_models():
    """List available LLM models/profiles with availability status."""
    return list_all_models()
