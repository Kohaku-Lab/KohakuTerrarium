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

## Terminal command

The installed app can put `kt`, `kt-cli` and `kt-tui` on your PATH. It is off by
default; see [Terminal commands](terminal-commands.md#installed-as-the-desktop-app).

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
