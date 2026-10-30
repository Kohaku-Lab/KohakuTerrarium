"""WinForms title strip for the Windows desktop host (requires its UI thread)."""

from System import Action, InvalidOperationException, ObjectDisposedException
from System.Drawing import Color, ColorTranslator, Font, Pen, Point, Size, SolidBrush
from System.Windows.Forms import Button, Control, FlatStyle, FormWindowState, Padding

from kohakuterrarium.serving.desktop.frame import NativeFrame
from kohakuterrarium.serving.desktop.palette import (
    BACKGROUND,
    DARK_BACKGROUND,
    DARK_FOREGROUND,
    FOREGROUND,
)


class DesktopTitleBar:
    """Native caption controls independent of WebView navigation and zoom."""

    def __init__(self, form, *, min_size: tuple[int, int]):
        self.form = form
        self._logical_minimum = tuple(min_size)
        self.frame = NativeFrame(form.Handle.ToInt64())
        self.buttons = []
        self._state = FormWindowState.Normal
        self.font = Font("Segoe UI", 9)
        self.foreground = ColorTranslator.FromHtml(FOREGROUND)
        self.background = ColorTranslator.FromHtml(BACKGROUND)
        form.BackColor = self.background
        for name, command in (
            ("Minimize", 0xF020),
            ("Maximize", None),
            ("Close", 0xF060),
        ):
            button = Button()
            button.AccessibleName = name
            button.TabStop = False
            button.FlatStyle = FlatStyle.Flat
            button.FlatAppearance.BorderSize = 0
            button.BackColor = self.background
            button.ForeColor = self.foreground
            button.FlatAppearance.MouseOverBackColor = (
                Color.FromArgb(232, 17, 35)
                if name == "Close"
                else Color.FromArgb(212, 205, 196)
            )
            button.Click += self._click(command)
            button.Paint += self._paint_button(name)
            form.Controls.Add(button)
            self.frame.attach_caption_control(button.Handle.ToInt64())
            button.BringToFront()
            self.buttons.append(button)
        form.Paint += self._paint
        form.Resize += self._layout
        form.TextChanged += self._invalidate
        form.DpiChanged += self._layout
        form.Disposed += self._dispose
        self.frame.dpi_changed = self._layout
        self.frame.appearance_changed = self._invalidate
        self._layout()
        self.frame.install()

    def set_dark(self, dark: bool, *, is_current=lambda: True) -> bool:
        if self.form.IsDisposed or not self.form.IsHandleCreated:
            return False

        applied = False

        def apply():
            nonlocal applied
            if not is_current():
                return
            self.background = ColorTranslator.FromHtml(
                DARK_BACKGROUND if dark else BACKGROUND
            )
            self.foreground = ColorTranslator.FromHtml(
                DARK_FOREGROUND if dark else FOREGROUND
            )
            self.form.BackColor = self.background
            for index, button in enumerate(self.buttons):
                button.BackColor = self.background
                button.ForeColor = self.foreground
                button.FlatAppearance.MouseOverBackColor = ColorTranslator.FromHtml(
                    "#e81123" if index == 2 else "#3a3632" if dark else "#d4cdc4"
                )
                button.FlatAppearance.MouseDownBackColor = ColorTranslator.FromHtml(
                    "#b80e1b" if index == 2 else "#45403a" if dark else "#c8beb2"
                )
                button.Invalidate()
            self.form.Invalidate()
            applied = True

        try:
            if self.form.InvokeRequired:
                self.form.Invoke(Action(apply))
            else:
                apply()
            return applied
        except (ObjectDisposedException, InvalidOperationException):
            return False

    def _click(self, command):
        def clicked(_sender, _event):
            self.frame.command(command or (0xF120 if self.frame.maximized else 0xF030))

        return clicked

    def _layout(self, *_):
        state = self.form.WindowState
        if state != self._state:
            self._state = state
            if state != FormWindowState.Normal:
                self.form.Size = self.form.RestoreBounds.Size
        scale = self.frame.dpi / 96
        minimum = Size(*(round(value * scale) for value in self._logical_minimum))
        if self.form.MinimumSize != minimum:
            self.form.MinimumSize = minimum
        height, width = round(32 * scale), round(46 * scale)
        self.frame.caption_height = height
        self.frame.controls_width = 3 * width
        self.form.Padding = Padding(0, height, 0, 0)
        for index, button in enumerate(self.buttons):
            button.Location = Point(self.form.ClientSize.Width - (3 - index) * width, 0)
            button.Size = Size(width, height)
            button.Invalidate()
        self.buttons[1].AccessibleName = (
            "Restore" if self.frame.maximized else "Maximize"
        )
        self.form.Invalidate()

    def _paint_button(self, name):
        def paint(button, event):
            scale = self.frame.dpi / 96
            size = round(10 * scale)
            x, y = (button.Width - size) // 2, (button.Height - size) // 2
            hover = button.ClientRectangle.Contains(
                button.PointToClient(Control.MousePosition)
            )
            color = Color.White if name == "Close" and hover else self.foreground
            pen = Pen(color, max(1, round(scale)))
            graphics = event.Graphics
            try:
                if name == "Minimize":
                    graphics.DrawLine(pen, x, y + size // 2, x + size, y + size // 2)
                elif name == "Close":
                    graphics.DrawLine(pen, x, y, x + size, y + size)
                    graphics.DrawLine(pen, x + size, y, x, y + size)
                elif self.frame.maximized:
                    offset = round(2 * scale)
                    graphics.DrawLine(pen, x + offset, y, x + size, y)
                    graphics.DrawLine(pen, x + size, y, x + size, y + size - offset)
                    graphics.DrawRectangle(
                        pen, x, y + offset, size - offset, size - offset
                    )
                else:
                    graphics.DrawRectangle(pen, x, y, size, size)
                self._paint_top_edge(graphics, button.Width)
            finally:
                pen.Dispose()

        return paint

    def _invalidate(self, *_):
        self.form.Invalidate(True)

    def _paint(self, _sender, event):
        scale = self.frame.dpi / 96
        brush = SolidBrush(self.foreground)
        try:
            event.Graphics.DrawString(
                self.form.Text, self.font, brush, 12 * scale, 8 * scale
            )
        finally:
            brush.Dispose()
        self._paint_top_edge(event.Graphics, self.form.ClientSize.Width)

    def _paint_top_edge(self, graphics, width):
        if self.frame.maximized:
            return
        hdc = graphics.GetHdc()
        try:
            self.frame.paint_top_edge(hdc.ToInt64(), width)
        finally:
            graphics.ReleaseHdc(hdc)

    def _dispose(self, *_):
        self.frame.close()
        self.font.Dispose()
