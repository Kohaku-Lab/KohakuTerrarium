"""Provider-native image generation (Codex built-in ``image_generation``).

This is a **provider-native** tool and is **opt-out** — every
creature that runs on Codex gets it automatically. The tool runner
never executes it; the provider translates its presence into a
wire-format tool spec, captures the returned image from the
response stream, and surfaces it as structured assistant content.

Supported providers:

* ``codex`` — via ``CodexOAuthProvider``, which maps this tool to
  the Codex Responses API built-in
  ``{"type":"image_generation", "output_format": ...}``.

### Default behaviour

- Codex-backed creatures: ``image_gen`` is auto-registered with
  provider defaults (PNG, auto size, auto quality). No YAML needed.
- Non-Codex creatures: ``image_gen`` simply isn't available. No
  error, no prompt noise.

### Opt-out

Add the tool name to the creature's ``disable_provider_tools``
list to suppress the auto-injection::

    disable_provider_tools:
      - image_gen

### Custom knobs

Wire the tool explicitly if you want non-default knobs. The
explicit entry wins over auto-injection::

    tools:
      - name: image_gen
        type: builtin
        output_format: png        # png | webp | jpeg
        size: 1024x1024           # 1024x1024 | 1024x1536 | 1536x1024 | auto
        quality: high             # low | medium | high | auto
        action: auto              # generate | edit | auto
        background: auto          # transparent | opaque | auto
"""

from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.core.native_tool_validation import validate_native_tool_options
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult


@register_builtin("image_gen")
class ImageGenTool(BaseTool):
    """Codex-native image generation (text-to-image + image edit)."""

    # Provider-native hooks — see BaseTool for full contract.
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
                # Free-form with runtime validation: newer image models
                # may accept sizes not listed below, but values still
                # must be "auto" or WIDTHxHEIGHT within sane bounds.
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
        # Capture explicit kwargs separately so a later
        # :meth:`refresh_native_options` re-read of ``ToolConfig.extra``
        # (used when the bootstrap merges profile-level options after
        # construction) keeps honoring them as the highest-priority
        # source.
        self._explicit_kwargs: dict[str, Any] = {
            "output_format": output_format,
            "action": action,
            "size": size,
            "quality": quality,
            "background": background,
        }
        self.refresh_native_options()

    def refresh_native_options(self) -> None:
        """Re-read ``ToolConfig.extra`` into instance fields.

        Called by the bootstrap layer after profile-level options are
        merged into ``self.config.extra``. The merge order is::

            explicit kwargs > config.extra > schema defaults > "png"
        """
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
        return (
            "Generate or edit an image. When the user asks for an image "
            "(draw / sketch / picture / render / edit this image), use "
            "this tool — the provider's built-in image backend produces "
            "the final PNG. Attached input images become editing targets."
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        # Provider-native tools never actually execute through the
        # tool runner, but we still declare DIRECT so any accidental
        # code path that dispatches us doesn't treat us like a
        # long-running background job.
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Unreachable — the base class's ``execute`` short-circuits
        provider-native tools before they reach ``_execute``."""
        return ToolResult(
            error=(
                "image_gen is provider-native; if you see this error the "
                "tool runner dispatched it by accident. Filter provider-"
                "native tools before execution."
            )
        )

    def provider_native_options(self) -> dict[str, Any]:
        """Return the subset of per-tool knobs the provider should
        merge into its wire-format tool spec. Omits ``None`` values
        so each provider can keep its own defaults."""
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
