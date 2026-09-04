"""Describe Codex-native image generation to the provider layer.

The provider executes this opt-out tool directly; the ordinary tool runner
never invokes it. Explicit configuration overrides provider defaults.
"""

from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.core.native_tool_validation import validate_native_tool_options
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult


@register_builtin("image_gen")
class ImageGenTool(BaseTool):
    """Codex-native image generation (text-to-image + image edit)."""

    is_provider_native = True
    provider_support = frozenset({"codex"})

    @classmethod
    def provider_native_option_schema(cls) -> dict[str, dict[str, Any]]:
        return {
            "output_format": {
                "type": "enum",
                "values": ["png", "webp", "jpeg"],
                "default": "png",
                "label": "Output format",
                "description": "Image file format the provider returns.",
            },
            "size": {
                # Suggestions are non-exhaustive; runtime validation permits future
                # dimensions while retaining bounded WIDTHxHEIGHT syntax.
                "type": "string",
                "suggestions": [
                    "auto",
                    "1024x1024",
                    "1024x1536",
                    "1536x1024",
                    "2048x2048",
                ],
                "default": "auto",
                "placeholder": "auto or WIDTHxHEIGHT",
                "label": "Size",
                "max_length": 32,
                "description": "Output dimensions; auto lets the provider choose.",
            },
            "quality": {
                "type": "enum",
                "values": ["auto", "low", "medium", "high"],
                "default": "auto",
                "label": "Quality",
                "description": "Render quality; higher costs more.",
            },
            "background": {
                "type": "enum",
                "values": ["auto", "transparent", "opaque"],
                "default": "auto",
                "label": "Background",
                "description": "Transparent only applies to PNG/WebP.",
            },
            "action": {
                "type": "enum",
                "values": ["auto", "generate", "edit"],
                "default": "auto",
                "label": "Action",
                "description": "Force generate vs edit; auto routes by attached input image.",
            },
        }

    def __init__(
        self,
        *,
        output_format: str | None = None,
        action: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        background: str | None = None,
        config: Any = None,
    ) -> None:
        super().__init__(config=config)
        # Explicit constructor values must remain highest priority after profile
        # options are merged into ``ToolConfig.extra``.
        self._explicit_kwargs: dict[str, Any] = {
            "output_format": output_format,
            "action": action,
            "size": size,
            "quality": quality,
            "background": background,
        }
        self.refresh_native_options()

    def refresh_native_options(self) -> None:
        """Resolve validated native options using configuration precedence."""
        extra = getattr(self.config, "extra", {}) or {}
        kwargs = self._explicit_kwargs

        def _pick(key: str) -> Any:
            if kwargs.get(key) is not None:
                return kwargs[key]
            return extra.get(key)

        raw_options: dict[str, Any] = {"output_format": _pick("output_format") or "png"}
        for key in ("action", "size", "quality", "background"):
            value = _pick(key)
            if value not in (None, ""):
                raw_options[key] = value
        validated = validate_native_tool_options(
            "image_gen", raw_options, self.provider_native_option_schema()
        )
        self.output_format = validated.get("output_format") or "png"
        self.action = validated.get("action")
        self.size = validated.get("size")
        self.quality = validated.get("quality")
        self.background = validated.get("background")

    @property
    def tool_name(self) -> str:
        return "image_gen"

    @property
    def description(self) -> str:
        return "Generate or edit a raster image. Not for diagrams from code - write the source and render it with bash."

    @property
    def execution_mode(self) -> ExecutionMode:
        # A defensive dispatch must remain synchronous rather than becoming a job.
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Report an invariant violation if provider-native dispatch leaks here."""
        return ToolResult(
            error=(
                "image_gen is provider-native; if you see this error the "
                "tool runner dispatched it by accident. Filter provider-"
                "native tools before execution."
            )
        )

    def provider_native_options(self) -> dict[str, Any]:
        """Return configured wire options while preserving provider defaults."""
        opts: dict[str, Any] = {"output_format": self.output_format}
        if self.action:
            opts["action"] = self.action
        if self.size:
            opts["size"] = self.size
        if self.quality:
            opts["quality"] = self.quality
        if self.background:
            opts["background"] = self.background
        return opts
