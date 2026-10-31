# Manual checks for the desktop shell

Automated tests and the headless self-check cover the shell's logic and its wiring. The items below need a human at a real desktop; run them on a nightly installer before a release.

Each section names the milestone that added it and the surface to look at.

## Native dialogs (M1)
- New session -> Browse...: the operating system's folder dialog opens, starts in the path typed in the field, and the chosen folder lands in the field.
- Cancelling the dialog leaves the field unchanged and shows no second picker.
- With a remote host selected, or a remote worker node, Browse... still shows the in-page picker.

## Settings (all shells)
- Settings -> Desktop: every toggle survives closing and reopening the app.
