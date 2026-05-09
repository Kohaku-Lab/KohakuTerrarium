"""
Read tool - read file contents (text and images).
"""

import base64
import io
import os
import time
from pathlib import Path
from typing import Any

import aiofiles

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
    resolve_tool_path,
)
from kohakuterrarium.utils.file_guard import is_binary_file
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@register_builtin("read")
class ReadTool(BaseTool):
    """
    Tool for reading file contents.

    Supports reading entire files or specific line ranges.
    """

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return "Read file contents: text, images, PDFs (required before write/edit)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Read file contents."""
        context = kwargs.get("context")

        path = args.get("path", "")
        if not path:
            return ToolResult(error="No path provided")

        # Resolve path
        file_path = resolve_tool_path(path, context)

        # Image files: return as multimodal content
        if _is_image_file(file_path):
            return await self._read_image(file_path, path)

        # PDF files: return text + rendered page images
        # offset/limit are reused as page offset/limit for PDFs
        if file_path.suffix.lower() == ".pdf":
            page_offset = int(args.get("offset", 0))
            page_limit = int(args.get("limit", 0))
            return await self._read_pdf(file_path, path, page_offset, page_limit)

        # Binary file guard (non-image binaries)
        if is_binary_file(file_path):
            return ToolResult(
                error=f"Binary file detected ({file_path.suffix}). "
                "Use bash with xxd, file, or other tools to inspect binary files."
            )

        # Path boundary guard
        if context and context.path_guard:
            msg = context.path_guard.check(str(file_path))
            if msg:
                return ToolResult(error=msg)

        if not file_path.exists():
            return ToolResult(error=f"File not found: {path}")

        if not file_path.is_file():
            return ToolResult(error=f"Not a file: {path}")

        # Get optional parameters
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", 0))

        # Configurable output truncation
        max_output_bytes = int(self.config.extra.get("max_output_bytes", 200000))

        try:
            async with aiofiles.open(
                file_path, encoding="utf-8", errors="replace"
            ) as f:
                content = await f.read()
            lines = content.splitlines(keepends=True)

            total_lines = len(lines)

            # Apply offset and limit
            if offset > 0:
                lines = lines[offset:]
            if limit > 0:
                lines = lines[:limit]

            # Format with line numbers
            output_lines = []
            start_line = offset + 1
            for i, line in enumerate(lines):
                line_num = start_line + i
                # Remove trailing newline for cleaner output
                line_content = line.rstrip("\n\r")
                # Truncate individual long lines
                if len(line_content) > 2000:
                    total_chars = len(line_content)
                    line_content = (
                        line_content[:2000]
                        + f" ... (line truncated, {total_chars} chars)"
                    )
                output_lines.append(f"{line_num:6}→{line_content}")

            output = "\n".join(output_lines)

            # Add truncation notice if applicable
            if limit > 0 and offset + limit < total_lines:
                output += f"\n\n... (showing lines {offset + 1}-{offset + len(lines)} of {total_lines})"

            # Truncate total output if it exceeds max bytes
            if max_output_bytes > 0 and len(output.encode("utf-8")) > max_output_bytes:
                output = output.encode("utf-8")[:max_output_bytes].decode(
                    "utf-8", errors="ignore"
                )
                output += f"\n\n[Output truncated at {max_output_bytes} bytes. Use offset/limit to read specific sections.]"

            logger.debug(
                "File read",
                file_path=str(file_path),
                lines_read=len(lines),
            )

            # Record read to file_read_state
            if context and context.file_read_state:
                mtime_ns = os.stat(file_path).st_mtime_ns
                partial = bool(args.get("offset") or args.get("limit"))
                context.file_read_state.record_read(
                    str(file_path), mtime_ns, partial, time.time()
                )

            return ToolResult(output=output, exit_code=0)

        except PermissionError:
            return ToolResult(error=f"Permission denied: {path}")
        except Exception as e:
            logger.error("Read failed", error=str(e))
            return ToolResult(error=str(e))

    async def _read_pdf(
        self,
        file_path: Path,
        original_path: str,
        page_offset: int,
        page_limit: int,
    ) -> ToolResult:
        """Read a PDF file: extract text + render page images.

        Uses offset/limit as page numbers (0-based offset, limit = page count).
        """
        try:
            import fitz  # pymupdf
        except ImportError:
            return ToolResult(
                error="PDF reading requires pymupdf. Install with: pip install pymupdf"
            )

        if not file_path.exists():
            return ToolResult(error=f"File not found: {original_path}")

        try:
            doc = fitz.open(file_path)
        except Exception as e:
            return ToolResult(error=f"Failed to open PDF: {e}")

        total_pages = len(doc)
        if total_pages == 0:
            doc.close()
            return ToolResult(output="Empty PDF (0 pages).", exit_code=0)

        # Apply offset/limit as page range
        start = min(page_offset, total_pages)
        end = total_pages
        if page_limit > 0:
            end = min(start + page_limit, total_pages)

        # Soft warning for large reads without explicit offset/limit
        warn_threshold = int(self.config.extra.get("pdf_page_warn", 20))
        if page_offset == 0 and page_limit == 0 and total_pages > warn_threshold:
            doc.close()
            return ToolResult(
                error=f"This PDF has {total_pages} pages. Reading all at once "
                f"will be very large. Please use offset and limit to read a "
                f"range of pages. For PDFs, offset is the starting page (0-based) "
                f"and limit is the number of pages to read. "
                f"Example: offset=0, limit={warn_threshold} for the first "
                f"{warn_threshold} pages. Or offset=0, limit={total_pages} "
                f"if you really want all pages."
            )

        # Extract text + render pages
        parts: list[TextPart | ImagePart] = []
        text_sections: list[str] = []

        dpi = int(self.config.extra.get("pdf_dpi", 100))
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)

        for page_num in range(start, end):
            page = doc[page_num]

            # Extract text with block sorting
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)[
                "blocks"
            ]
            blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

            page_lines = [f"\n--- Page {page_num + 1}/{total_pages} ---\n"]
            for block in blocks:
                if block["type"] == 0:  # Text block
                    for line in block.get("lines", []):
                        text_parts = []
                        for span in line.get("spans", []):
                            text = span.get("text", "")
                            if text.strip():
                                text_parts.append(text)
                        if text_parts:
                            page_lines.append("".join(text_parts))
            text_sections.extend(page_lines)

            # Render page image
            try:
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")
                b64 = base64.b64encode(img_data).decode("ascii")
                parts.append(
                    ImagePart(
                        url=f"data:image/png;base64,{b64}",
                        detail="auto",
                        source_type="pdf_page",
                        source_name=f"{file_path.name} p{page_num + 1}",
                    )
                )
            except Exception as e:
                logger.debug(
                    "PDF page render failed, text still available", error=str(e)
                )

        doc.close()

        # Combine text + images
        text_content = "\n".join(text_sections)
        if not text_content.strip():
            text_content = "(No extractable text — check the page images below.)"

        header = f"PDF: {original_path} ({total_pages} pages"
        if page_offset > 0 or page_limit > 0:
            header += f", showing pages {start + 1}-{end}"
        header += ")\n"

        parts.insert(0, TextPart(text=header + text_content))

        logger.info(
            "PDF read",
            file_path=str(file_path),
            pages=f"{start + 1}-{end}",
            total=total_pages,
            rendered=len(parts) - 1,
        )

        return ToolResult(output=parts, exit_code=0)

    async def _read_image(self, file_path: Path, original_path: str) -> ToolResult:
        """Read an image file and return as multimodal content.

        Only the four formats LLM providers reliably accept are
        supported: PNG, JPEG, WEBP, GIF. The bytes are decoded with
        Pillow before being base64-encoded so corrupt / mislabeled
        files fail cleanly here instead of at the provider boundary.
        """
        if not file_path.exists():
            return ToolResult(error=f"File not found: {original_path}")

        max_image_bytes = 20 * 1024 * 1024  # 20 MB limit
        file_size = file_path.stat().st_size

        if file_size > max_image_bytes:
            return ToolResult(
                error=f"Image too large ({file_size // 1024}KB). "
                f"Max: {max_image_bytes // (1024 * 1024)}MB."
            )

        suffix = file_path.suffix.lower()
        if suffix not in _IMAGE_MIME:
            return ToolResult(
                error=(
                    f"Unsupported image format {suffix!r}. "
                    f"Supported: {', '.join(sorted(_IMAGE_MIME))}."
                )
            )

        try:
            async with aiofiles.open(file_path, "rb") as f:
                data = await f.read()
        except Exception as e:
            return ToolResult(error=f"Failed to read image: {e}")

        # Verify the file actually decodes as a valid image of the
        # expected format. Pillow's verify() does a header-level
        # integrity check; we follow with a load on a fresh handle to
        # confirm the pixel data is intact too (verify() invalidates
        # the image afterwards, hence the second open).
        verified_format = _verify_image(data)
        if verified_format is None:
            return ToolResult(
                error=(
                    f"File {original_path} is not a valid image, or its format "
                    f"is unsupported (need PNG / JPEG / WEBP / GIF)."
                )
            )
        mime = _PIL_FORMAT_TO_MIME.get(verified_format)
        if mime is None:
            return ToolResult(
                error=(
                    f"Image decoded as {verified_format!r}, which is not in the "
                    f"supported set (PNG / JPEG / WEBP / GIF)."
                )
            )

        b64 = base64.b64encode(data).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"

        logger.info(
            "Image read",
            file_path=str(file_path),
            size_kb=len(data) // 1024,
            mime=mime,
        )

        return ToolResult(
            output=[
                TextPart(
                    text=f"Image: {original_path} ({len(data) // 1024}KB, {mime})"
                ),
                ImagePart(
                    url=data_url,
                    detail="auto",
                    source_type="file",
                    source_name=file_path.name,
                ),
            ],
            exit_code=0,
        )


# The only image formats the supported LLM providers (OpenAI Responses,
# Codex, Anthropic, Gemini) reliably accept. Anything else either gets
# rejected at the provider boundary or silently degraded — restricting
# here keeps the failure mode local and actionable.
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Image extensions that are still recognized as images by the dispatcher
# (so they don't fall through to the binary-file guard) but rejected
# with a clear "unsupported" message in ``_read_image``.
_UNSUPPORTED_IMAGE_EXTENSIONS = frozenset(
    {
        ".bmp",
        ".tiff",
        ".tif",
        ".ico",
        ".svg",
        ".heif",
        ".heic",
        ".avif",
    }
)

_PIL_FORMAT_TO_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


def _verify_image(data: bytes) -> str | None:
    """Decode-check ``data`` with Pillow; return the format name or ``None``.

    Returns the Pillow format string (e.g. ``"PNG"``, ``"JPEG"``) when
    the bytes parse cleanly, ``None`` when Pillow refuses them. Catches
    every exception class Pillow may raise — corrupt headers, truncated
    streams, decoder-not-available, etc.
    """
    # Optional-dep guard: Pillow is declared in pyproject but the
    # tool should still work in stripped-down installs. Skip
    # verification if missing — provider will surface any error.
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        logger.warning("Pillow not installed — skipping image verification")
        return "PNG"

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        # ``verify()`` leaves the image unusable; reopen for load().
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.load()
        return fmt
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as e:
        logger.debug("Image verification failed", error=str(e))
        return None


def _is_image_file(path: Path) -> bool:
    """Check if a file looks like an image (extension-based).

    Includes both the supported set and a small list of legacy formats
    we explicitly reject in ``_read_image`` — that routes them through
    the image branch and yields a "supported set is PNG/JPEG/WEBP/GIF"
    message instead of a generic "binary file detected".
    """
    suffix = path.suffix.lower()
    return suffix in _IMAGE_MIME or suffix in _UNSUPPORTED_IMAGE_EXTENSIONS
