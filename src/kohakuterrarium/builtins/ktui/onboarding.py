"""First run: the focused creature has no model it can use (no provider set up, no key), so
before the first message is typed ktui offers to set one up here: sign in to Codex (a
ChatGPT subscription), add a provider with an API key, or pick another model, each with the
dialogs Settings uses. Skipped, it stays a notice; Settings and /login do the same later.
"""

from typing import TYPE_CHECKING

from kohakutui.ui import Picker

from kohakuterrarium.llm.deferred_provider import DeferredLLMProvider

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

CHOICES = [
    {
        "label": "sign in with Codex",
        "detail": "a ChatGPT subscription",
        "value": "codex",
    },
    {
        "label": "add a provider",
        "detail": "an API key (OpenAI, Anthropic, ...)",
        "value": "provider",
    },
    {"label": "pick a model", "detail": "one that is already set up", "value": "model"},
    {"label": "later", "detail": "Settings or /login any time", "value": "later"},
]


def needs_model(shell: "KtuiShell") -> str | None:
    """Why the focused creature cannot answer yet, or None when it has a model."""
    if getattr(shell.engine, "keeps_running", False):
        return None  # a server's creature: its model is the server's
    llm = getattr(shell.focused.agent, "llm", None)
    if isinstance(llm, DeferredLLMProvider):
        return llm.reason or "no model is set up"
    return None


async def first_run(shell: "KtuiShell") -> None:
    """Offers model setup when the focused creature has none."""
    reason = needs_model(shell)
    if reason is None:
        return
    choice = await shell.app.run_dialog_async(
        Picker(items=CHOICES, title=f"No model yet · {reason}"[:120])
    )
    entry = shell.focused
    match choice:
        case "codex":
            await shell.config_edit.login("codex")
            await shell.settings.pick_model(entry)
        case "provider":
            await shell.config_edit.act("provider:")
            await shell.settings.pick_model(entry)
        case "model":
            await shell.settings.pick_model(entry)
        case _:
            shell.app.notify(
                "no model yet: Settings (alt+5) or /login sets one up", "warning"
            )


__all__ = ["first_run", "needs_model"]
