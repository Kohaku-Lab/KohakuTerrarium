# Manual checks for the desktop shell

Automated tests and the headless self-check cover the shell's logic and its wiring. The items below need a human at a real desktop; run them on a nightly installer before a release.

Each section names the milestone that added it and the surface to look at.

## Native dialogs (M1)
- New session -> Browse...: the operating system's folder dialog opens, starts in the path typed in the field, and the chosen folder lands in the field.
- Cancelling the dialog leaves the field unchanged and shows no second picker.
- With a remote host selected, or a remote worker node, Browse... still shows the in-page picker.

## Tray and taskbar (M2)
- Start a long turn: the taskbar button shows a moving (indeterminate) bar; the tray tooltip says "1 running".
- Make a session ask you something: the bar turns yellow, the tray icon gets a red dot, the tooltip says "waiting for you".
- Cause a turn error: the bar turns red and the dot is amber while nothing is waiting; viewing the tab clears it.
- Right-click the tray icon: "Waiting for you" lists the waiting session first, "Sessions" lists the others, and choosing one opens its tab and raises the window.
- Tray "New session" opens the new-session dialog; "Open folder as session..." opens the OS folder dialog and then the dialog in that folder.

## Session windows (M3)
- Right-click a session tab -> "Move to new window": a window opens with that session and the tab closes in the main window.
- Right-click a session in the left rail -> "Open in new window": the window opens and the rail item stays.
- Drag a session tab out of the main window and release it over the desktop: a window opens under the pointer and the tab closes.
- Drop a tab on another tab: it reorders as before and no window opens.
- In a session window, "Return to main window" closes it and the session shows in the main window.
- Quit with a session window open, start again: the window comes back; close it first and it does not.

## Finding the desktop settings (M4)
- First start of a fresh install: a card "Set up the desktop app" appears bottom right with close-to-tray and start-at-login switches; "Done" removes it for good, "All desktop settings" opens Settings scrolled to the desktop section.
- Inside a session, the status bar shows a "Desktop" chip with a dot until the card was dismissed; hovering lists the current values; clicking opens the desktop section.
- Settings -> Desktop app is grouped under Window, Startup, Shortcuts, Updates and Integration, and "Reopen session windows" is there.

## Backend control, repair and diagnostics (M5, M6)
- Settings -> Desktop app -> Backend shows running, memory, uptime and restarts; "Restart backend" shows a banner, then the window reloads on a fresh backend.
- End the backend's python process in Task Manager: a "Restarting the backend" banner appears and the window reloads by itself. Do it four times within two minutes: the fourth time a "backend stopped" banner with Restart backend and Open logs stays.
- Tray menu shows "Backend: running - N MB"; "Restart backend" and "Export diagnostics..." work from there.
- Settings -> Runtime shows the active and the shipped release; "Repair runtime" asks for a second click, then the window restarts on the shipped release. With a stale runtime (active older than shipped) the warning line is shown.
- Settings -> Diagnostics -> Export: a save dialog opens; the zip holds info.json, app.log, runtime/, shell-settings.json and doctor.txt, and a search for your API key in them finds nothing.
- Break the runtime (rename `~/.kohakuterrarium/runtime/versions/<active>` while the app is closed) and start: the loading page shows the error with Restart backend, Repair runtime, Export diagnostics and Open logs; Repair runtime brings the app up.
- Command palette: "Desktop: settings / restart backend / export diagnostics / open backend log".

## Operating-system integration (M7)
- Settings -> Desktop app -> "Keep the computer awake": with "While agents work" start a long turn and check `powercfg /requests` (Windows) lists the app under SYSTEM; it disappears about 20 seconds after the turn ends. "Never" holds nothing; on battery with "on mains power" it holds nothing.
- Double-click a `.kohakutr` file, then right-click the taskbar icon: it appears under "Recent" in the jump list.
- Settings -> Explorer: turn "Open folders from Explorer" on; right-click a folder (Windows 11: "Show more options") -> "Open with KohakuTerrarium" opens the new-session dialog in it; turn it off and the entry is gone. After uninstalling with the NSIS installer the entry is gone too.
- In a terminal with the `kt` command installed: `kt open <folder>` raises the running app on that folder; `kt open file.txt` refuses.
- Open `kt://new?path=<folder>` and `kt://live/<scope>` from the Run dialog; `kt://install/<name>` shows a confirmation naming the package and installs nothing until "Install" is pressed; `kt://install/https%3A%2F%2Fx` does nothing.

## Window frame (M10)
- Windows 11: the title bar and window border take the colour at the top of the page (warm beige in light mode, dark brown in dark mode) and change when the theme is switched; the title text stays readable. On Windows 10 the frame stays the system one.

## Settings (all shells)
- Settings -> Desktop: every toggle survives closing and reopening the app.
