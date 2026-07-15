"""Expose workspace file operations through compatibility HTTP routes.

The handlers delegate filesystem policy and behavior to the Studio attachment
layer while preserving the URL shapes expected by existing frontend callers.
"""

from fastapi import APIRouter

from kohakuterrarium.api.schemas import FileDelete, FileMkdir, FileRename, FileWrite
from kohakuterrarium.studio.attach import workspace_files

router = APIRouter()


@router.get("/tree")
async def get_file_tree(root: str, depth: int = 1):
    """Return a bounded file tree for lazy branch expansion.

    The default depth of one lets callers request deeper branches only when the
    user expands them.
    """
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
