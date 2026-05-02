---
name: response
description: Generate user-facing responses
category: subagent
tags: [output, response, generation]
---

# response

Output sub-agent for generating user-facing responses.

## WHEN TO USE

- Controller wants to delegate response generation
- Need longer, more detailed output
- Separating orchestration from output generation
- Response needs specific formatting or style

## WHEN NOT TO USE

- Simple, short responses
- Controller can respond directly
- Internal processing/decisions

## HOW TO USE

```
tool call: response(
what to communicate
)
```

## Arguments

| Arg  | Type    | Description                     |
| ---- | ------- | ------------------------------- |
| body | content | What to communicate to the user |

## Examples

```
tool call: response(
Explain how the authentication system works
)
```

```
tool call: response(
Present the analysis results in a clear format
)
```

```
tool call: response(
Greet the user as the character
)
```

## OUTPUT ROUTING

The response sub-agent is an **output sub-agent**:

- `output_to: external` - Output streams directly to user
- Does NOT return to controller
- Designed for user-facing content

## CAPABILITIES

The response sub-agent:

- Receives context from controller
- Generates formatted responses
- Can maintain character/style consistency
- Streams output directly to user

## USE CASES

1. **RP Agent**: Generate in-character dialogue
2. **Assistant**: Format detailed explanations
3. **Report Generation**: Create structured output

## LIMITATIONS

- Output-only (cannot use tools)
- Cannot interact with controller after starting
- Single-turn output generation
