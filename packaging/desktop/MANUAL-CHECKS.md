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

## Settings (all shells)
- Settings -> Desktop: every toggle survives closing and reopening the app.
