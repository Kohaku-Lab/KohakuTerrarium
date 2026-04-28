"""CLI Codex OAuth — login flow."""

from kohakuterrarium.studio.identity.codex_oauth import run_login_blocking


def login_cli() -> int:
    """Run the Codex OAuth flow interactively."""
    return run_login_blocking()
