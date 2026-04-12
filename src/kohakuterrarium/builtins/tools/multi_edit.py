"""Atomic or policy-driven multi-edit tool for ordered search/replace edits."""

from pathlib import Path
from typing import Any

import aiofiles

from kohakuterrarium.builtins.tools.edit import (
    build_result_diff,
    check_edit_guards,
    update_edit_read_state,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
    resolve_tool_path,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class MultiEditFailure(Exception):
    def __init__(self, index: int, reason: str):
        self.index = index
        self.reason = reason
        super().__init__(reason)


def _normalize_mode(strict: bool, best_effort: bool) -> str:
    if strict and best_effort:
        raise ValueError("best_effort=true cannot be used together with strict=true")
    if best_effort:
        return "best_effort"
    if strict:
        return "strict"
    return "partial"


def _validate_edits(edits: Any) -> list[dict[str, Any]]:
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must be a non-empty array")

    normalized: list[dict[str, Any]] = []
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ValueError(f"edit[{i}] must be an object")
        if "old" not in edit:
            raise ValueError(f"edit[{i}] is missing required field: old")
        if "new" not in edit:
            raise ValueError(f"edit[{i}] is missing required field: new")
        old = edit["old"]
        new = edit["new"]
        replace_all = edit.get("replace_all", False)
        if not isinstance(old, str):
            raise ValueError(f"edit[{i}].old must be a string")
        if not isinstance(new, str):
            raise ValueError(f"edit[{i}].new must be a string")
        if old == "":
            raise ValueError(f"edit[{i}].old must not be empty")
        if not isinstance(replace_all, bool):
            raise ValueError(f"edit[{i}].replace_all must be a boolean")
        normalized.append({"old": old, "new": new, "replace_all": replace_all})
    return normalized


def _apply_single_edit(
    content: str, edit: dict[str, Any], index: int
) -> tuple[str, str, int]:
    old = edit["old"]
    new = edit["new"]
    replace_all = edit["replace_all"]

    count = content.count(old)
    if count == 0:
        raise MultiEditFailure(index, "old not found in file after prior edits")
    if count > 1 and not replace_all:
        raise MultiEditFailure(
            index,
            f"found {count} occurrences of old; set replace_all=true or provide more context",
        )

    if replace_all:
        updated = content.replace(old, new)
        replaced = count
    else:
        updated = content.replace(old, new, 1)
        replaced = 1

    if updated == content:
        return updated, "ok: no change (old equals new)", 0
    noun = "replacement" if replaced == 1 else "replacements"
    return updated, f"ok: {replaced} {noun}", replaced


def _build_output(
    file_path: Path,
    mode: str,
    statuses: list[str],
    original: str,
    final_content: str,
    applied: int,
    failed: int,
    skipped: int,
    file_changed: bool,
) -> str:
    header = []
    if file_changed:
        header.append(f"Edited {file_path}")
    else:
        header.append(f"No changes made to {file_path}")
    header.append(f"mode: {mode}")
    header.append(f"applied: {applied}")
    header.append(f"failed: {failed}")
    header.append(f"skipped: {skipped}")
    header.append("")
    header.extend(statuses)

    diff_text = build_result_diff(file_path, original, final_content)
    if diff_text:
        header.extend(["", diff_text])
    return "\n".join(header)


@register_builtin("multi_edit")
class MultiEditTool(BaseTool):
    """Apply multiple ordered search/replace edits to a single file."""

    needs_context = True
    require_manual_read = True

    @property
    def tool_name(self) -> str:
        return "multi_edit"

    @property
    def description(self) -> str:
        return (
            "Apply multiple ordered search/replace edits to one file with strict, partial, "
            "or best_effort policies. Use info(multi_edit) first."
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")
        path = args.get("path", "")
        if not path:
            return ToolResult(error="path is required")

        strict = args.get("strict", True)
        best_effort = args.get("best_effort", False)
        if not isinstance(strict, bool):
            return ToolResult(error="strict must be a boolean")
        if not isinstance(best_effort, bool):
            return ToolResult(error="best_effort must be a boolean")

        try:
            mode = _normalize_mode(strict, best_effort)
            edits = _validate_edits(args.get("edits"))
        except ValueError as e:
            return ToolResult(error=str(e))

        file_path = resolve_tool_path(path, context)
        guard = check_edit_guards(file_path, context)
        if guard:
            return guard

        if not file_path.exists():
            return ToolResult(error=f"File not found: {path}")
        if not file_path.is_file():
            return ToolResult(error=f"Not a file: {path}")

        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                original = await f.read()

            current = original
            statuses: list[str] = []
            applied = 0
            failed = 0
            skipped = 0
            encountered_failure = False

            for i, edit in enumerate(edits):
                if mode == "partial" and encountered_failure:
                    statuses.append(f"edit[{i}]: skipped")
                    skipped += 1
                    continue

                try:
                    current, status, replaced = _apply_single_edit(current, edit, i)
                    statuses.append(f"edit[{i}]: {status}")
                    if replaced >= 0:
                        applied += 1
                except MultiEditFailure as e:
                    encountered_failure = True
                    failed += 1
                    statuses.append(f"edit[{e.index}]: error: {e.reason}")
                    if mode == "strict":
                        for j in range(i + 1, len(edits)):
                            statuses.append(f"edit[{j}]: skipped")
                            skipped += 1
                        output = _build_output(
                            file_path,
                            mode,
                            statuses,
                            original,
                            original,
                            applied,
                            failed,
                            skipped,
                            file_changed=False,
                        )
                        return ToolResult(
                            output=output,
                            error=(
                                f"multi_edit failed in strict mode at edit[{e.index}] "
                                "(file unchanged)"
                            ),
                        )
                    if mode == "partial":
                        for j in range(i + 1, len(edits)):
                            statuses.append(f"edit[{j}]: skipped")
                            skipped += 1
                        break

            file_changed = current != original
            if file_changed:
                async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                    await f.write(current)
                update_edit_read_state(file_path, context)

            output = _build_output(
                file_path,
                mode,
                statuses,
                original,
                current,
                applied,
                failed,
                skipped,
                file_changed=file_changed,
            )

            if failed:
                return ToolResult(
                    output=output,
                    error=f"multi_edit completed with {failed} failed edit(s)",
                )

            logger.debug(
                "File multi-edited",
                file_path=str(file_path),
                mode=mode,
                edits=len(edits),
                changed=file_changed,
            )
            return ToolResult(output=output, exit_code=0)

        except PermissionError:
            return ToolResult(error=f"Permission denied: {path}")
        except Exception as e:
            logger.error("Multi-edit failed", error=str(e))
            return ToolResult(error=str(e))
