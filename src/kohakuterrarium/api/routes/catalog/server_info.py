"""Catalog server-info — runtime environment introspection.

Replaces ``api.routes.configs.server_info``.
"""

import os
import sys

from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def server_info():
    """Return server environment info (cwd, platform, etc.)."""
    return {
        "cwd": os.getcwd(),
        "platform": sys.platform,
    }
