---
name: send_message
description: Send a message to a named channel for agent-to-agent communication
category: builtin
tags: [communication, multi-agent]
---

# send_message

Send a message to a named channel. This is how you deliver results to
other team members in a terrarium. Other creatures CANNOT see your direct
text output -- you MUST use send_message to communicate with them.

## Arguments

| Arg          | Type   | Description                                     |
| ------------ | ------ | ----------------------------------------------- |
| channel      | string | Channel name to send to (required)              |
| message      | string | Message content (required)                      |
| metadata     | string | Optional JSON metadata object                   |
| channel_type | string | Channel type: "queue" (default) or "broadcast"  |
| reply_to     | string | Optional message ID to reply to (for threading) |

## When to Use

- **After completing work**: send your results to the designated output channel
- **For coordination**: send status updates to broadcast channels (e.g. team_chat)
- **To reach a specific creature**: send to their direct channel (channel name = creature name)

## Important

- Your text output is visible only to the observer/user, NOT to other creatures.
- If your workflow requires delivering results to another creature, you MUST
  call send_message. Just outputting text does nothing for the team.
- Queue channels deliver to one recipient. Broadcast channels deliver to all.

## Channel Types

- **queue** (default): Point-to-point. One consumer receives each message.
  Use for direct agent-to-agent communication via SubAgentChannel.
- **broadcast**: All subscribers receive every message. Use for status updates,
  events, or notifications via AgentChannel.

## Output

Returns confirmation with channel name and message ID.

## LIMITATIONS

- Fire-and-forget: no delivery confirmation beyond "message sent"
- No message persistence across restarts (in-memory queues)
- Metadata must be valid JSON if provided
- Broadcast channels require subscribers to be listening before the message is sent

## TIPS

- Channel names are arbitrary strings; both sender and receiver must agree on the name
- Channels are created automatically on first use (no setup needed)
- Messages are queued; the receiver picks them up at its own pace
- Use metadata to attach structured info (priority, source, tags) without polluting the message body
- The sender identity is set automatically from the agent context
- Each message gets a unique message_id that can be used for reply_to threading
- Use broadcast channels when multiple agents need to observe the same events
- Use queue channels for direct point-to-point communication
