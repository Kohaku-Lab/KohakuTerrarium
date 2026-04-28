"""Workspace files HTTP route shell.

Thin parse-and-call wrapper over
:mod:`kohakuterrarium.studio.attach.workspace_files`. Mounted at
``/api/files/*`` from ``api/app.py`` so the legacy ``filesAPI``
frontend callers (``filesAPI.browseDirectories``, ``getTree``,
``readFile``, ``writeFile``, etc.) keep their existing URL shapes.
"""

from fastapi import APIRouter

from kohakuterrarium.api.schemas import FileDelete, FileMkdir, FileRename, FileWrite
from kohakuterrarium.studio.attach import workspace_files

router = APIRouter()


@router.get("/tree")
async def get_file_tree(root: str, depth: int = 3):
    """Return a nested file tree starting from the given root directory."""
    return await workspace_files.get_file_tree(root, depth)


@router.get("/browse")
async def browse_directories(path: str | None = None):
    """Return browsable directories under the local filesystem."""
    return await workspace_files.browse_directories(path)


@router.get("/read")
async def read_file(path: str):
    """Read a file and return its content with metadata."""
    return await workspace_files.read_file(path)


@router.post("/write")
async def write_file(req: FileWrite):
    """Write content to a file, creating parent directories if needed."""
    return await workspace_files.write_file(req.path, req.content)


@router.post("/rename")
async def rename_file(req: FileRename):
    """Rename or move a file/directory."""
    return await workspace_files.rename_file(req.old_path, req.new_path)


@router.post("/delete")
async def delete_file(req: FileDelete):
    """Delete a file or empty directory."""
    return await workspace_files.delete_file(req.path)


@router.post("/mkdir")
async def make_directory(req: FileMkdir):
    """Create a directory, including parent directories."""
    return await workspace_files.make_directory(req.path)
