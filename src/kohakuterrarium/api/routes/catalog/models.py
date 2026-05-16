"""Catalog models — list available LLM profiles.

Replaces ``api.routes.configs.list_models``.
"""

from fastapi import APIRouter

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.llm.profiles import list_all as list_all_models

router = APIRouter()


@router.get("")
async def list_models():
    """List available LLM models/profiles with availability status.

    Off-loaded to the shared I/O executor — ``list_all`` reads
    ``llm_profiles.yaml`` + ``api_keys.yaml`` + ``codex-auth.json``
    once per preset to fill the ``available`` flag, and the file
    reads happen on every request (no in-memory cache).  Running on
    the event loop blocked the Model Switcher modal at "Loading
    models" whenever a concurrent route was using a ``to_thread``
    slot.
    """
    return await run_in_io_executor(list_all_models)
