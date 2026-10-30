"""Materialize controller-owned upload bytes; filesystem operations require blocking."""

import hashlib
import re
import tempfile
from pathlib import Path


def materialize_upload(
    raw: bytes, name: str, directory: str, cached_path: str | None
) -> str:
    """Reuse an intact upload copy or create a fresh copy without overwriting files."""
    if cached_path:
        path = Path(cached_path)
        try:
            if not path.is_symlink() and path.read_bytes() == raw:
                return cached_path
        except OSError:
            pass

    name = Path(name.replace("\\", "/")).name
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "attachment"
    suffix = Path(safe_name).suffix[:20]
    stem = safe_name[: -len(suffix)] if suffix else safe_name
    safe_name = f"{stem[:100]}{suffix}"
    digest = hashlib.sha256(raw).hexdigest()
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=directory,
        prefix=f"{digest[:16]}-",
        suffix=f"-{safe_name}",
        delete=False,
    ) as upload:
        upload.write(raw)
        return upload.name
