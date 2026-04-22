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
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult


@register_builtin("image_gen")
class ImageGenTool(BaseTool):
    """Codex-native image generation (text-to-image + image edit)."""

    # Provider-native hooks — see BaseTool for full contract.
    is_provider_native = True
    provider_support = frozenset({"codex"})

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
        # The bootstrap path hands knobs through ``ToolConfig.extra``
        # (non-recognised YAML keys go there). Explicit kwargs take
        # precedence so programmatic construction stays readable.
        extra = getattr(self.config, "extra", {}) or {}

        def _pick(kwarg: Any, key: str) -> Any:
            if kwarg is not None:
                return kwarg
            return extra.get(key)

        self.output_format = _pick(output_format, "output_format") or "png"
        self.action = _pick(action, "action")
        self.size = _pick(size, "size")
        self.quality = _pick(quality, "quality")
        self.background = _pick(background, "background")

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
