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
    """Routes named-key + printable-char events to whichever overlay
    owns the keyboard at the moment.

    Expects the host class (``RichCLIApp``) to provide:
      - ``self.bus_overlay`` with ``visible`` / ``captures_input()`` /
        ``handle_key`` / ``handle_text``
      - ``self.model_picker``, ``self.module_picker``,
        ``self.settings_overlay`` with the same surface
      - optional ``self.agent_overlay``
      - ``self._invalidate()`` to schedule a redraw on consume
    """

    def _picker_handle_key(self, key: str) -> bool:
        """Forward a named-key event to whichever overlay is open.

        Composer bindings call this on every named key (``up``, ``enter``,
        ``escape``, ``tab``, ``backspace``, …). The first overlay that
        claims to own the keyboard (``visible``) gets the key; if it
        consumes it, the composer skips its own default handling.
        """
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
        if self.agent_overlay is not None and self.agent_overlay.visible:
            consumed = self.agent_overlay.handle_key(key)
            if consumed:
                self._invalidate()
            return consumed
        return False

    def _picker_handle_text(self, char: str) -> bool:
        """Forward a printable-character event to whichever overlay wants text.

        Invoked from the composer's ``Keys.Any`` binding which is
        conditionally active only when ``_picker_captures_input`` is
        True — so this runs only for forms inside the settings overlay.
        """
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
            # In list mode, ``t`` toggles current row. Consume any
            # other char so it doesn't leak into the textarea.
            consumed = self.module_picker.handle_text(char)
            if consumed:
                self._invalidate()
            return consumed
        if self.settings_overlay.visible:
            # Settings list mode wants ``d`` for delete (and silently
            # consumes other letters so they don't leak into the chat
            # textarea behind the overlay); form mode wants every
            # printable char as field input. Same handler covers both
            # — handle_text already routes by ``self.mode``.
            consumed = self.settings_overlay.handle_text(char)
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
        """True when an overlay is capturing printable characters.

        Drives the ``Condition`` filter on the composer's ``Keys.Any``
        binding — we only intercept text when an overlay genuinely wants
        it (form mode), so list-mode keystrokes still go through the
        normal ``handle_key`` path.
        """
        if self.bus_overlay.captures_input():
            return True
        if self.module_picker.visible:
            # Modal: consume both list-mode and form-mode chars so
            # nothing leaks into the chat textarea behind the
            # overlay.
            return True
        if self.settings_overlay.visible:
            # Settings is also modal — list mode reserves ``d`` for
            # delete and silently swallows the rest, form mode routes
            # printable chars into the active field. Either way the
            # composer's textarea must NOT receive these keystrokes,
            # so claim them unconditionally while the overlay is up.
            return True
        if self.agent_overlay is not None and self.agent_overlay.visible:
            # Topic 08 — printable chars go into the overlay's filter.
            return True
        return False
