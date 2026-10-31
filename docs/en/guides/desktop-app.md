---
title: Desktop app
summary: The installed app (Windows and macOS) and `kt app`: what each contains, how the local server is protected, the terminal command, and first-run approval.
tags:
  - guides
  - desktop
  - install
---

# Desktop app

There are two ways to get the desktop window. They show the same interface and share
`~/.kohakuterrarium` (settings, sessions, installed packages), so you can switch
between them.

| | Installed app | `kt app` |
|---|---|---|
| Get it | `.msi` / `.exe` (Windows), `.dmg` (macOS) from the release page | `pip install kohakuterrarium` |
| Window | Tauri (system webview) | pywebview |
| Python | bundled with the app | your interpreter |
| Updates | new releases install through the launcher's update flow, without reinstalling ([App update](app-update.md)) | `pip install -U` |
| Platforms | Windows, macOS | Windows, macOS, Linux (best effort) |

## What the installed app contains

An interpreter, the launcher, and one release of KohakuTerrarium. On first start it
unpacks that release into `~/.kohakuterrarium/runtime` and shows the progress in
the window. Later starts reuse it; updates swap in a newer release without
reinstalling the app. Tools that start Python (the `python` tool, MCP servers,
package installs) use the bundled interpreter.

## Updates

Two things update separately. The Python runtime (the release tree) updates through
the launcher without reinstalling. The desktop shell itself (the window, tray and
native features) updates from **Settings → Preferences → Desktop app → App
updates**: **Check for updates** looks for a newer shell published with the latest
stable release, and **Install and restart** downloads it, verifies its signature
against the key built into the app, installs it and restarts. Shell updates are
rare; most releases only change the runtime.

## Terminal command

The installed app can put `kt`, `kt-cli` and `kt-tui` on your PATH. It is off by
default; see [Terminal commands](terminal-commands.md#installed-as-the-desktop-app).

## Tray and attention

The installed app puts an icon in the system tray (menu bar on macOS). The icon
shows a red dot while something is waiting for your input and a blue dot when
work has finished; its tooltip says how many, and the taskbar entry (Windows) or
Dock icon (macOS) carries the same indicator. Clicking the icon brings the window
back and opens the tab that needs you first.

**Settings → Desktop → Keep running in the tray** (off by default) makes closing the
window hide it instead of quitting, so agents keep working. Quit from the tray menu.
The setting is stored in `shell-settings.json` in the app's config folder.

## Opening files and folders

The installers register `.kohakutr` session files with the app. Opening one (double-click,
or `KohakuTerrarium <file>`) copies it into your saved sessions under a free name and
shows its history; if the app is already running, that window handles it. Passing a
folder (`KohakuTerrarium <folder>`) opens the new-session dialog with that folder as the
working directory. A file that is not a session is refused with a message.

## Summon shortcut

**Settings → Desktop → Summon shortcut** registers a system-wide key combination (off
until you set one). Pressing it from any app raises the window, even when it is hidden
in the tray, and puts the cursor in the message box (or opens the command palette when
no chat is showing). The shortcut needs at least one modifier key. If another app
already owns the combination, the setting says so and keeps the previous one.

## Dropping files

Drag files from your desktop onto a chat and their full paths are added to the message
box, one per line, ready for the agent to read. The chat highlights while the files are
over it; split views drop into whichever chat is under the pointer.

## Links

Links to other sites open in your default browser, never inside the app window.
`kt://session/<name>` links open that saved session's history in the app.

## How the local server is protected

The window talks to a server on `127.0.0.1`. Each launch generates a one-time
token that only the app knows; the server exchanges it for a cookie that the
window alone holds, and refuses every request without it, including requests from
web pages in your browser and from other programs. Requests whose `Host` or
`Origin` is not the local address are refused too. `kt serve` and `kt web` are
unchanged.

## First run on an unsigned build

Release builds are not signed with a paid developer certificate.

- **Windows:** SmartScreen may say "Windows protected your PC". Choose **More
  info → Run anyway**.
- **macOS:** the app is ad-hoc signed. If macOS blocks it, open **System Settings →
  Privacy & Security** and choose **Open Anyway** once.

Release assets list SHA-256 sums so you can verify the download.

## `kt app` on Linux

`kt app` needs a webview backend: GTK with WebKit2GTK, or Qt. Without one it opens
the interface in your default browser instead, and `kt doctor` names the missing
system packages.
