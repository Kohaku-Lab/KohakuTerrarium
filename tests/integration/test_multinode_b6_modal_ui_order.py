"""B6: in the new-session dialog the "Run on" node selector must appear ABOVE
the working-directory input.

Static template-ordering check — the Vue template is the authoritative source
for visual order, so we parse the .vue file as plain text and compare line
numbers of two anchor strings:

* The ``<SitePicker>`` element bound to ``cluster.spawn.label`` (renders the
  "Run on" label, see ``utils/i18n/locales/en.js``).
* The working-directory field bound to ``lab.new.pwd``.

The selected node decides which filesystem the working-dir path resolves on
(B5), so the picker MUST be the field the user fills first.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIALOG = (
    REPO_ROOT
    / "src"
    / "kohakuterrarium-frontend"
    / "src"
    / "components"
    / "shell"
    / "newSession"
    / "NewSessionDialog.vue"
)

# Anchor strings are i18n keys — robust against renaming the visible text.
SITE_PICKER_ANCHOR = "cluster.spawn.label"
WORKING_DIR_ANCHOR = "lab.new.pwd"


def _find_line(path: Path, needle: str) -> int:
    """Return 1-based line number of the first line containing ``needle``."""
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            return idx
    raise AssertionError(f"{path.name}: anchor not found: {needle!r}")


class TestModalFieldOrder:
    """B6: 'Run on' node selector must precede the working-directory input."""

    def test_run_on_appears_before_working_dir(self) -> None:
        assert DIALOG.is_file(), f"missing dialog: {DIALOG}"
        site_line = _find_line(DIALOG, SITE_PICKER_ANCHOR)
        pwd_line = _find_line(DIALOG, WORKING_DIR_ANCHOR)
        assert site_line < pwd_line, (
            f"'Run on' SitePicker is at line {site_line} but the working "
            f"directory is at line {pwd_line}; SitePicker must appear ABOVE it "
            "(it determines which node the path resolves on)."
        )
