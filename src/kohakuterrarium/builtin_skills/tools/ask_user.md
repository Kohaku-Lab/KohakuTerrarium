---
name: ask_user
description: Ask the user a question and wait for response
category: builtin
tags: [interaction, human-in-the-loop]
---

# ask_user

Ask the user a question and wait for their response. Enables human-in-the-loop
patterns where the agent needs clarification, approval, or additional input.

## WHEN TO USE

`ask_user` is for **free-text replies** — the user types an answer.
For other interaction shapes, prefer the right tool:

| Need                                                             | Tool                         |
| ---------------------------------------------------------------- | ---------------------------- |
| Free-text answer (a name, a path, a description)                 | **ask_user** (this tool)     |
| Pick from N labelled options ("Approve / Edit / Reject")         | **show_card** with `actions` |
| Display structured info beautifully (plan preview, status panel) | **show_card** display-only   |

Use `ask_user` when:

- Requesting open-ended clarification ("which file did you mean?", "what should I name this?")
- Gathering missing free-form information mid-execution
- Asking for a description, reason, or note the user must type out

Use `show_card` instead when:

- The answer is one of a small set of choices → use `show_card` with
  buttons; the user clicks instead of typing.
- You want to display a structured summary the user should see
  prominently (e.g. "Migration plan ready" with file count, line
  count, risk level) — even without a reply.

## HOW TO USE

```
tool call: ask_user(
Your question here
)
```

The question text is passed as the content body.

## Arguments

| Arg      | Type    | Description                                    |
| -------- | ------- | ---------------------------------------------- |
| question | content | The question to present to the user (required) |

## Examples

Ask for clarification:

```
tool call: ask_user(
I found 3 potential approaches. Which should I use?
1. Refactor the existing module
2. Create a new module
3. Use a third-party library
)
```

Ask for approval:

```
tool call: ask_user(
Should I proceed with deleting the deprecated files? (yes/no)
)
```

Gather missing information:

```
tool call: ask_user(
What database host should I use for the staging environment?
)
```

## Output Format

Returns the user's raw text response as a string.

If the user provides an empty response, returns `(no response)`.

## LIMITATIONS

- Single free-text input only — for choice / button UIs use
  `show_card`.
- The tool waits forever by default. Pass `timeout_s` for a bounded
  wait if blocking the agent indefinitely is unacceptable.
- The CLI renders the question above the composer; the user types
  into the existing input field. TUI and web render a styled input
  box.

## TIPS

- Keep questions clear and concise
- Provide context so the user can make an informed decision
- Avoid asking unnecessary questions; prefer sensible defaults when possible
- **Don't list numbered options as text** — that's `show_card`'s job.
  ask_user's "options" only shape the answer; if the user reads a
  numbered list and types a number, you got a string back, not a
  structured choice. Use `show_card` with buttons for that pattern
  so each click returns a stable `action_id`.
