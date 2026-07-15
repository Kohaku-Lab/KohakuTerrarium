"""List configured LLM profiles and their availability."""

from fastapi import APIRouter

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.llm.profiles import list_all as list_all_models

router = APIRouter()


@router.get("")
async def list_models():
    """Load profile availability without blocking the event loop on config reads."""
    return await run_in_io_executor(list_all_models)
