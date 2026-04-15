"""CLI model management commands — list, default, show LLM profiles."""

import argparse

from kohakuterrarium.cli.config import config_cli


def model_cli(args: argparse.Namespace) -> int:
    """Compatibility wrapper for legacy model management commands."""
    mapped = argparse.Namespace(**vars(args))
    mapped.config_command = "llm"
    mapped.config_llm_command = getattr(args, "model_command", None) or "list"
    return config_cli(mapped)
