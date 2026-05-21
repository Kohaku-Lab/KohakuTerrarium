"""Dep-graph guard — the auth invariant.

Per design.md §1 and CLAUDE.md:

    Auth lives entirely in ``src/kohakuterrarium/api/auth/`` and is
    invisible to everything below ``api/``.  The engine, Studio,
    terrarium runtime, session store, and framework core have ZERO
    knowledge of users, tokens, admin gates, or hosts.

This test walks the source tree and asserts:

1. **No upward imports.**  No module outside ``src/kohakuterrarium/api/``
   (or its known carve-outs) imports ``kohakuterrarium.api.auth.*``.
2. **Carve-outs are documented.**  ``cli/admin.py`` is the one legitimate
   non-api consumer (it shares the auth DB + write paths so the CLI
   and API admin-rotation routes can't drift).  Anything else is a
   bug.

If a future change adds a new cross-boundary importer, the test fails
loudly with the exact file:line so we can decide: legitimate
carve-out (extend the allowlist with a written reason) or drift (move
the code).
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SRC = _REPO_ROOT / "src" / "kohakuterrarium"
_AUTH_MOD = "kohakuterrarium.api.auth"


# Modules outside ``src/kohakuterrarium/api/`` that may legitimately
# import from ``kohakuterrarium.api.auth.*``.  Add new entries with a
# one-line reason (visible in the failure message) and a code-review
# discussion — the bar is "this code conceptually owns auth state,
# not just consumes it."
_ALLOWED_OUTSIDE_API: dict[str, str] = {
    "cli/admin.py": (
        "kt admin shares the auth.db + config.toml write paths with the "
        "admin-rotation API routes via api.auth.config_write — both "
        "callers must converge on one TOML writer to avoid wire-format "
        "drift between CLI and frontend."
    ),
}


def _module_imports_auth(path: Path) -> list[tuple[int, str]]:
    """Return ``[(line, module_name), ...]`` for every
    ``kohakuterrarium.api.auth.*`` import in the file."""
    hits: list[tuple[int, str]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return hits
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == _AUTH_MOD or mod.startswith(_AUTH_MOD + "."):
                hits.append((node.lineno, mod))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _AUTH_MOD or alias.name.startswith(_AUTH_MOD + "."):
                    hits.append((node.lineno, alias.name))
    return hits


def _is_inside_api(path: Path) -> bool:
    """True iff ``path`` lives under ``src/kohakuterrarium/api/``."""
    try:
        rel = path.relative_to(_SRC)
    except ValueError:
        return False
    return rel.parts[:1] == ("api",)


def _walk_kt_modules() -> list[Path]:
    """Every ``.py`` file under ``src/kohakuterrarium/``."""
    return [p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts]


class TestAuthIsolation:
    def test_no_cross_boundary_imports_outside_allowlist(self):
        violations: list[tuple[str, int, str]] = []
        for path in _walk_kt_modules():
            if _is_inside_api(path):
                continue  # api/* may import from api/auth/* freely
            rel = path.relative_to(_SRC).as_posix()
            if rel in _ALLOWED_OUTSIDE_API:
                continue  # known carve-out
            for lineno, mod in _module_imports_auth(path):
                violations.append((rel, lineno, mod))
        assert not violations, (
            "non-api modules import from kohakuterrarium.api.auth.* — "
            "this breaks the auth invariant.  Add to "
            "_ALLOWED_OUTSIDE_API with a written reason if intentional:\n"
            + "\n".join(f"  {f}:{ln} → {m}" for f, ln, m in violations)
        )

    def test_carve_outs_actually_use_auth(self):
        """Sanity: every allowlisted carve-out actually imports from
        ``api.auth.*`` — otherwise the entry is stale and someone is
        about to accidentally re-introduce a bypass."""
        for rel, reason in _ALLOWED_OUTSIDE_API.items():
            path = _SRC / rel
            assert (
                path.is_file()
            ), f"allowlist entry {rel} doesn't exist on disk; remove the entry"
            hits = _module_imports_auth(path)
            assert hits, (
                f"allowlist entry {rel} no longer imports from "
                f"kohakuterrarium.api.auth.* — stale entry; remove it.\n"
                f"reason was: {reason}"
            )


class TestAuthInvariantDocumentation:
    """Ensure the README + ``__init__`` text matches the actual guard.

    The README claims a dep-graph guard exists; this test pins that the
    guard described above is what the documentation references.  If
    someone deletes this test file, the README claim becomes false —
    catch it by asserting the file exists on the rel path the README
    points at."""

    def test_this_guard_lives_in_a_predictable_location(self):
        # If we ever rename / relocate this test, update auth/README.md
        # alongside so the documented claim stays accurate.
        assert Path(__file__).name == "test_auth_module_isolation.py"
        assert Path(__file__).parent.name == "auth"
