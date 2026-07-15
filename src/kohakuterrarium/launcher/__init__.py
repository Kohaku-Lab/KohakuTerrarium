"""Manage the standalone versioned runtime used by packaged launchers.

The launcher downloads prebuilt release trees, atomically selects the active
version, and hands execution to it without requiring package-install tooling.
Its dependency surface must remain usable before the full framework is on the
import path. This initializer intentionally re-exports nothing, avoiding eager
imports and circular initialization across launcher modules.
"""
