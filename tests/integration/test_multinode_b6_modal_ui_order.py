"""B6 — failing test: in NewCreatureModal/NewTerrariumModal the "Run on X" node
selector must appear ABOVE the "Working directory" input.

Static template-ordering check — the Vue template is the authoritative source
for visual order, so we parse the .vue file as plain text and compare line
numbers of two anchor strings:

* The ``<SitePicker>`` element bound to ``cluster.spawn.label`` (renders the
  "Run on" label, see ``utils/i18n/locales/en.js``).
* The ``Working directory`` literal label.

The selected node decides which filesystem the working-dir path resolves on
(B5), so the picker MUST be the field the user fills first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODALS_DIR = (
    REPO_ROOT
    / "src"
    / "kohakuterrarium-frontend"
    / "src"
    / "components"
    / "shell"
    / "modals"
)

# Anchor strings we look for. The SitePicker anchor is the i18n key for the
# "Run on" label — robust against renaming the visible English text.
SITE_PICKER_ANCHOR = "cluster.spawn.label"
WORKING_DIR_ANCHOR = "Working directory"


def _find_line(path: Path, needle: str) -> int:
    """Return 1-based line number of the first line containing ``needle``."""
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            return idx
    raise AssertionError(f"{path.name}: anchor not found: {needle!r}")


class TestModalFieldOrder:
    """B6: 'Run on' node selector must precede 'Working directory' input."""

    @pytest.mark.parametrize(
        "modal_file",
        ["NewCreatureModal.vue", "NewTerrariumModal.vue"],
    )
    def test_run_on_appears_before_working_dir(self, modal_file: str) -> None:
        path = MODALS_DIR / modal_file
        assert path.is_file(), f"missing modal: {path}"

        site_line = _find_line(path, SITE_PICKER_ANCHOR)
        pwd_line = _find_line(path, WORKING_DIR_ANCHOR)

        assert site_line < pwd_line, (
            f"{modal_file}: 'Run on' SitePicker is at line {site_line} but "
            f"'Working directory' is at line {pwd_line}; SitePicker must "
            "appear ABOVE 'Working directory' (it determines which node the "
            "path resolves on)."
        )

    # Kept as named convenience aliases so failure output names the modal.
    def test_new_creature_modal_run_on_before_working_dir(self) -> None:
        self.test_run_on_appears_before_working_dir("NewCreatureModal.vue")

    def test_new_terrarium_modal_run_on_before_working_dir(self) -> None:
        self.test_run_on_appears_before_working_dir("NewTerrariumModal.vue")
