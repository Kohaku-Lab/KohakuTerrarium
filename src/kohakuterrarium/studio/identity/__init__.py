"""Studio identity — configuration state (LLM, keys, MCP, codex, prefs).

This package centralizes every read/write against KohakuTerrarium's
identity configuration files. The CLI and HTTP route layers delegate
to these modules — no direct YAML/JSON parsing in those layers.
"""
