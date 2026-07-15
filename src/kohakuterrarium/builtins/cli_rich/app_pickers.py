"""Picker / overlay input routing for the rich CLI app.

Extracted from ``cli_rich/app.py`` to keep that file under the
hard 1000-line cap. Same surface as before — the three methods
``_picker_handle_key`` / ``_picker_handle_text`` / ``_picker_captures_input``
are mixed back into ``RichCLIApp`` via :class:`AppPickersMixin`.

The methods all share one shape: walk the overlay stack
(bus → model picker → module picker → settings → agent), forward the
event to the first overlay whose ``visible`` / ``captures_input``
guard claims ownership, and ``_invalidate()`` on consume so the
composer redraws. Splitting them out is a layout-only change — no
behaviour shifts.
"""


class AppPickersMixin:
    """Route keyboard input to the active modal overlay."""

    def _picker_handle_key(self, key: str) -> bool:
        """Forward a named key to the first visible overlay."""
        if self.bus_overlay.visible:
            consumed = self.bus_overlay.handle_key(key)
            if consumed:
                self._invalidate()
            return consumed
        if self.model_picker.visible:
            consumed = self.model_picker.handle_key(key)
            if consumed:
                self._invalidate()
            return consumed
        if self.module_picker.visible:
            consumed = self.module_picker.handle_key(key)
            if consumed:
                self._invalidate()
            return consumed
        if self.settings_overlay.visible:
            consumed = self.settings_overlay.handle_key(key)
            if consumed:
                self._invalidate()
            return consumed
        if self.drive_overlay.visible:
            consumed = self.drive_overlay.handle_key(key)
            if consumed:
                self._invalidate()
            return consumed
        if self.agent_overlay is not None and self.agent_overlay.visible:
            consumed = self.agent_overlay.handle_key(key)
            if consumed:
                self._invalidate()
            return consumed
        return False

    def _picker_handle_text(self, char: str) -> bool:
        """Forward printable input to the active modal overlay."""
        if self.bus_overlay.captures_input():
            consumed = self.bus_overlay.handle_text(char)
            if consumed:
                self._invalidate()
            return consumed
        if self.module_picker.visible and self.module_picker.is_capturing_text():
            consumed = self.module_picker.handle_text(char)
            if consumed:
                self._invalidate()
            return consumed
        if self.module_picker.visible:
            # List-mode shortcuts remain modal and must not reach the composer.
            consumed = self.module_picker.handle_text(char)
            if consumed:
                self._invalidate()
            return consumed
        if self.settings_overlay.visible:
            # One handler covers list shortcuts and form text entry.
            consumed = self.settings_overlay.handle_text(char)
            if consumed:
                self._invalidate()
            return consumed
        if self.drive_overlay.visible:
            consumed = self.drive_overlay.handle_text(char)
            if consumed:
                self._invalidate()
            return consumed
        if self.agent_overlay is not None and self.agent_overlay.visible:
            consumed = self.agent_overlay.handle_text(char)
            if consumed:
                self._invalidate()
            return consumed
        return False

    def _picker_captures_input(self) -> bool:
        """Return whether an overlay currently owns printable input."""
        if self.bus_overlay.captures_input():
            return True
        if self.module_picker.visible:
            return True
        if self.settings_overlay.visible:
            return True
        if self.drive_overlay.visible:
            return True
        if self.agent_overlay is not None and self.agent_overlay.visible:
            return True
        return False
