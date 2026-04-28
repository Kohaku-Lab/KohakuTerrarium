"""CLI model management commands — list, default, show LLM profiles.

Thin dispatcher routing ``kt model {list,default,show}`` to the
``studio.identity.llm_default`` / ``identity_llm`` operations. The
historical ``kt model`` verb stays in place for back-compat.
"""

import argparse

from kohakuterrarium.cli.identity_llm import (
    default_cli as _llm_default_cli,
)
from kohakuterrarium.cli.identity_llm import list_cli as _llm_list_cli
from kohakuterrarium.cli.identity_llm import show_cli as _llm_show_cli


def model_cli(args: argparse.Namespace) -> int:
    sub = getattr(args, "model_command", None) or "list"
    name = getattr(args, "name", None)
    match sub:
        case "list":
            return _llm_list_cli(include_builtins=False)
        case "show":
            if not name:
                print("name required")
                return 1
            return _llm_show_cli(name)
        case "default":
            return _llm_default_cli(name)
    print("Usage: kt model {list|show|default}")
    return 1
