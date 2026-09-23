"""Conservative operating presets for locally borrowed Antigravity accounts."""

PRESETS = {
    ("google-antigravity", name): {
        "model": name,
        "max_context": 120000,
        "max_output": 8192,
        "provider_native_tools": [],
    }
    for name in ("gemini-3-flash", "claude-sonnet-4-6")
}
