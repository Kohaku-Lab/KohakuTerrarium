# modules/trigger/

Trigger module protocol and implementations. Triggers produce `TriggerEvent`
objects without user input, enabling autonomous agent behavior. Each trigger
runs as an async task that waits for its condition and fires events into the
controller's event queue.

## Files

| File          | Description                                                                          |
| ------------- | ------------------------------------------------------------------------------------ |
| `__init__.py` | Re-exports protocol, base class, and all trigger implementations                     |
| `base.py`     | `TriggerModule` protocol (`start`, `stop`, `wait_for_trigger`) and `BaseTrigger` ABC |
| `timer.py`    | `TimerTrigger`: fire at fixed intervals with optional immediate first fire           |
| `context.py`  | `ContextUpdateTrigger`: fire when external context changes (with debounce)           |
| `channel.py`  | `ChannelTrigger`: fire when a named channel receives a message                       |

## Dependencies

- `kohakuterrarium.core.events` (TriggerEvent, EventType)
- `kohakuterrarium.core.channel` (AgentChannel, ChannelRegistry, ChannelSubscription)
- `kohakuterrarium.core.session` (get_channel_registry)
- `kohakuterrarium.utils.logging`
