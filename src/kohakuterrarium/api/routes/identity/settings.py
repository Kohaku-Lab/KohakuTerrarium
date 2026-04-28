"""Identity generic settings router (currently empty - no HTTP endpoints).

The legacy ``routes/settings.py`` did not expose generic show/path/edit
endpoints; those operations are CLI-only via ``kt config show/path/edit``.
This router is mounted to reserve the namespace for future expansion.
"""

from fastapi import APIRouter

router = APIRouter()
