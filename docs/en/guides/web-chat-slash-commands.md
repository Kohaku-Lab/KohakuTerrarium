# Web Chat slash commands and skills

Type `/` at the start of an empty Web Chat composer to open the live command and skill menu for the selected creature. Continue typing to filter by command name, command alias, or skill name. Use Up/Down to move, Enter or Tab to select, Escape to close, or click an item.

Commands are listed before skills. A command name or alias always wins if it collides with a skill name. Disabled skills are omitted and rejected by the server. A skill with model invocation blocked still appears for explicit user selection: that flag only prevents automatic model invocation.

Selecting a command uses the command endpoint and surfaces its typed result. Selecting a skill sends an explicit skill input through the normal creature turn queue with source `web:skill`, so the resulting answer streams and is recorded like other chat input.

The menu inventory is cached briefly per session tab and refreshed after expiry. Changing sessions or tabs cannot apply a stale response to the new composer.

## API

- `GET /api/sessions/{session_id}/creatures/{creature_id}/command-inventory`
- `POST /api/sessions/{session_id}/creatures/{creature_id}/skill-input`

The skill request body uses the existing slash payload shape:

```json
{"command": "review", "args": "focus on security"}
```

## Screenshot instructions

No private/local screenshot file should be committed. For release notes or documentation screenshots:

1. Start Web Chat with a creature that has at least one command and one enabled skill.
2. Select the creature tab and type `/` in an empty composer.
3. Capture the grouped Commands and Skills menu, with one keyboard-selected option visible.
4. Type a filtering prefix and capture the narrowed menu.
5. Select a skill and capture the streamed response in the same tab.
6. Remove account names, tokens, filesystem paths, and private conversation content before publishing.
