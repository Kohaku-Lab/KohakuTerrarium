# KohakuTerrarium for VS Code

First-party VS Code workspace extension for creating and operating KohakuTerrarium Sessions from a sidebar.

## First-release scope

- Automatically discover a same-host KohakuTerrarium daemon from `~/.kohakuterrarium/run/web.json`.
- Fall back to a bounded probe of local KT ports when using foreground `kt web` or when daemon state is stale.
- Use the default loopback auth bypass without reading, requesting, storing, or sending a host token.
- Reuse a token from VS Code `SecretStorage` only when the local service explicitly disables loopback bypass; prompt once only when that strict service has no stored token.
- List live and dormant Sessions.
- Create a Session from `kohakuterrarium.defaultCreature` and the current workspace folder.
- Select a Creature by stable Creature ID.
- Reuse the production KohakuTerrarium chat store for history, streaming text, tool activity, interactive replies, and Stop Turn.
- Stop Session and resume Sessions.
- Relocate the selected Creature after graph merge/split events and fail closed when it disappears.
- Recover explicitly with Refresh after the KT service restarts; the extension does not run an infinite reconnect loop.

The first release supports Tunnel Browser plus a KohakuTerrarium service on the same host. Remote KT endpoints and multi-user auth are out of scope.

## Use

1. Start the local daemon:

   ```bash
   kt serve start
   ```

2. Set `kohakuterrarium.defaultCreature` to a trusted Creature path or installed `@package/...` reference if you want to create new Sessions. Existing Sessions can be viewed and resumed without this setting.
3. Open the KohakuTerrarium Activity Bar view.

The extension discovers the daemon URL and connects automatically. With the normal local KT defaults, there is no endpoint or token prompt.

If no daemon is running, start it and press **Refresh**. A foreground `kt web` process is also discovered on the bounded default local port range.

### Strict local auth

If the local service has host-token auth enabled and `loopback_bypass = false`, the extension reuses the token from VS Code `SecretStorage`. It asks for a token when none is saved, or once to replace a token rejected with HTTP 401. Network errors do not trigger replacement prompts.

When daemon state is unavailable or stale, automatic port discovery lists strict-auth candidates for you to select before reading or sending a stored token. Select only an endpoint you trust: public capabilities advertise an auth policy, not a verified service identity. The extension verifies authenticated KT diagnostics and the session connection before saving a new token. Canceling the selector sends no credentials and does not fall back to an old endpoint. No manual endpoint entry is required.

### Refresh lifecycle

Refresh reuses a healthy Host connection, runtime, and topology watcher, reconciling Sessions through the existing authenticated client instead of repeating discovery or token prompts. Each Refresh still starts a new operation epoch: old chat sockets, pending commands and image reads lose ownership. Reconciliation has a bounded deadline. Configuration changes or current-runtime failure release the connection; the next explicit Refresh discovers again. Backend mutations are never retried automatically.

### Unsent composer state

Within an open Webview, Refresh preserves text and files for the same runtime and Creature ID, including while the request is pending. Draft and attachment caches each retain up to 32 recently used conversations; older inactive buffers are evicted. This bounds retained conversation entries, not aggregate attachment bytes. Changing the service endpoint, changing connection configuration, or closing the Webview clears the caches. Unsent files are not persisted to disk.

### Goal commands

Pure-text `/goal ...` uses the selected Creature's command endpoint and shows the command result in the transcript. Text with attachments remains a normal chat message. No arbitrary command or target proxy is exposed. Drafts remain on failure; if a request times out or disconnects after dispatch, the command may still have executed. Check goal status before retrying a mutation.

### Notifications

Toast-surface events appear in the Webview with explicit severity text, a dismiss button, and ARIA status/alert semantics. Hover or keyboard focus suspends dismissal; leaving grants a full reading interval. Escape dismisses a focused notification. Up to five notifications are retained; configuration/endpoint changes and Webview disposal clear them. The existing shared store maps absent or zero `duration_ms` to four seconds, matching Dashboard behavior.

### Queued messages

Messages sent while a Creature is processing appear above the composer. The last three are shown initially, with a control to expand the rest. After the backend acknowledges queueing, edit the text (attachments are preserved) or cancel the message. Ctrl/Cmd+Enter saves an edit; Escape discards it. Changes require a connected socket and backend acknowledgement; a failed or timed-out write may have executed, so uncertain entries block retries until a matching acknowledgement arrives. A message that already entered processing cannot be changed.

This is a view of locally observed queued input, not a server queue snapshot. Refreshing, switching Creature, or closing the Webview clears the shared store's queue view but does not cancel queued backend input. Check server state before resending or assuming cancellation.

### Artifact images

PNG, JPEG, GIF and WebP artifact images in message parts or Markdown load through the authenticated Workspace Host. Webview networking remains disabled (`connect-src 'none'`); neither endpoint nor token is sent to the Webview. The Host accepts only artifact references observed in the current conversation and validates their namespace against the selected live runtime's server-provided saved name. Unknown namespaces, traversal, redirects, SVG/HTML and mismatched image signatures are rejected.

Each read is limited to 8 MiB and 10 seconds, with at most four Host reads in flight. The Webview deduplicates reads, limits pending work and its data-URI cache, and clears cached results across Refresh/selection/disposal. Unavailable images show a Retry button. A runtime without a trustworthy saved-name mapping—including unsupported multi-node or versioned-store mappings—fails closed rather than reading another session's artifacts. This bridge does not provide arbitrary file downloads or remote image fetching.

### Advanced override

Use **KohakuTerrarium: Configure Local Connection Override** only for a nonstandard local port that cannot be discovered. Return to the normal behavior with **KohakuTerrarium: Use Automatic Local Discovery**.

The Webview never receives the token, endpoint, Creature config reference, workspace path, or `pwd`. HTTP, WebSocket, filesystem-sensitive settings, discovery, and selection ownership stay in the Workspace Extension Host.

## Development

```bash
cd extensions/vscode
npm ci
npm test
npm run build
npm run package
```

The VSIX contains only the bundled Extension Host, bundled Webview, stylesheet, manifest, icon, license, and README. Source files, tests, source maps, scripts, dependencies, and lockfiles are excluded.
