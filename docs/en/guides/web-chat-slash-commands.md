# Web Chat goals and skill discovery

Type `/` at the start of an empty Web Chat composer to open the live Goal and skill menu for the selected creature. Continue typing to filter by name. Use Up/Down to move, Enter or Tab to select, Escape to close, or click an item.

The menu intentionally includes only the available `/goal` command and enabled skills that the creature may invoke. Other user commands are omitted because their Web equivalents already exist as controls, selectors, inspectors, or settings. For example, use the model selector instead of `/model`, the status panels instead of `/status`, and the composer button instead of `/compact`.

The complete command namespace still shadows colliding skill names and aliases, even though those commands are hidden from the menu. This prevents a suggestion from resolving differently on the server.

Selecting `/goal` uses the command endpoint and renders its structured result inside the chat. Selecting a skill inserts `/<skill>` and sends it as ordinary chat input so the creature can decide how to use the skill. Slash-prefixed text other than `/goal` is also ordinary chat input; it is not an alternate route to existing Web UI actions.

The inventory is cached briefly per session tab and refreshed after expiry. Changing sessions or tabs cannot apply a stale response to the new composer.

## API

- `GET /api/sessions/{session_id}/creatures/{creature_id}/command-inventory`
- `POST /api/sessions/{session_id}/creatures/{creature_id}/command`

## Screenshot instructions

No private/local screenshot file should be committed. For release notes or documentation screenshots:

1. Start Web Chat with a creature that has `/goal` and at least one enabled, invocable skill.
2. Select the creature tab and type `/` in an empty composer.
3. Capture the Goal and Skills menu, with one keyboard-selected option visible.
4. Type a filtering prefix and capture the narrowed menu.
5. Select `/goal` and capture its inline result, or select a skill and capture the streamed response.
6. Remove account names, tokens, filesystem paths, and private conversation content before publishing.
