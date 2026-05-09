---
title: Your first terrarium
summary: Compose two creatures with channels and output wiring, then add a root for an interactive surface.
tags:
  - tutorials
  - terrarium
  - multi-agent
---

# First Terrarium

**Problem:** you want two creatures to cooperate — a writer produces
something, a reviewer critiques it — and you want to see the messages
flow between them.

**End state:** a terrarium config with two creatures and two channels,
running under the TUI, visibly passing messages from one to the other.

**Prerequisites:** [First Creature](first-creature.md). You should have
`kt-biome` installed and be able to `kt run` a single creature.

A terrarium is the **runtime engine**: it owns the channel graph,
creature lifecycles, output wiring, and the topology + session
bookkeeping that follows graph changes. It runs no LLM and has no
reasoning loop — the LLMs and the reasoning live in the creatures
inside it. See [terrarium concept](../concepts/multi-agent/terrarium.md)
for the full contract.

## Step 1 — Create the folder

```bash
mkdir -p terrariums
```

You can put the terrarium config anywhere; the convention is a
`terrariums/` folder next to your creatures.

## Step 2 — Write the terrarium config

`terrariums/writer-team.yaml`:

```yaml
# Writer + reviewer team.
#   tasks    -> writer  -> review  -> reviewer
#                       <- feedback <- reviewer

terrarium:
  name: writer_team

  creatures:
    - name: writer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You are a concise writer. When you receive a message on
        `tasks`, write a short draft and send it to `review` using
        send_message. When you receive feedback, revise and resend.
      channels:
        listen:    [tasks, feedback]
        can_send:  [review]

    - name: reviewer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You critique drafts. When you receive a message on `review`,
        reply with one or two concrete improvement suggestions on
        `feedback` using send_message. If the draft is good, say so.
      channels:
        listen:    [review]
        can_send:  [feedback]

  channels:
    tasks:    "Incoming work for the writer"
    review:   "Drafts sent to the reviewer"
    feedback: "Review notes sent back"
```

What the wiring does:

- `listen` registers a `ChannelTrigger` on the creature — when a message
  lands on one of those channels, the creature wakes up and sees it.
- `can_send` enumerates channels the creature's `send_message` tool is
  allowed to write to. A creature cannot reach channels that are not in
  this list.
- Channels are declared once in `channels:` with an optional one-line
  description. All channels are broadcast: every listener sees every
  send.

Inline `system_prompt:` is appended to the inherited base prompt. Do
that here to keep the tutorial self-contained; prefer
`system_prompt_file:` for real use.

## Step 3 — Inspect the topology (optional)

```bash
kt terrarium info terrariums/writer-team.yaml
```

Prints the creatures, their listen/send channel sets, and the channel
definitions. Good sanity check before running.

## Step 4 — Run it

```bash
kt terrarium run terrariums/writer-team.yaml --mode tui --seed "write a one-paragraph product description for a smart kettle" --seed-channel tasks
```

The TUI opens with a tab per creature plus a tab per channel. `--seed`
injects your prompt onto the `seed-channel` (default `seed`; we override
to `tasks`) at startup. The writer wakes up, drafts, and sends to
`review`. The reviewer wakes up, reviews, sends to `feedback`. The
writer wakes up again, revises.

You can watch the channel tabs for raw message flow and the creature
tabs for each one's reasoning.

## Step 5 — Make the handoff reliable with output wiring

Channels are the right answer for conditional / optional / broadcast
traffic — the reviewer's "approve vs. revise" decision is a genuine
choice that should live on a channel. But the writer → reviewer edge
is **deterministic**: every time the writer finishes a turn, the
reviewer should see it. Relying on the writer's LLM to remember
`send_message("review", ...)` is the old failure mode.

The framework offers a direct alternative: **output wiring**. Declare
the pipeline edge in the creature's config, and the runtime emits a
`creature_output` event straight into the target's event queue at
turn-end — no `send_message` required on either side.

Update `terrariums/writer-team.yaml`:

```yaml
terrarium:
  name: writer_team
  creatures:
    - name: writer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You write short product copy. You receive a brief on `tasks`
        and a critique on `feedback`. When you receive feedback, revise
        your draft based on it.
      output_wiring:
        - reviewer                # every writer turn-end → reviewer
      channels:
        listen: [tasks, feedback]
        can_send: []              # no longer needs to send on `review`
    - name: reviewer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You are a strict reviewer. The writer's draft will arrive as a
        creature_output event. If the draft is good, send "APPROVED:
        <draft>" on `feedback`. If not, send specific revision requests
        on `feedback`.
      channels:
        listen: []                # receives writer's output via wiring
        can_send: [feedback]      # reviewer's decision is conditional — keep on channel
  channels:
    tasks:    "Incoming work for the writer"
    feedback: "Review notes sent back"
```

What changed:

- Writer's `output_wiring: [reviewer]` replaces the need for the
  writer to emit on a `review` channel.
- The `review` channel itself is gone — wiring carries the edge.
- Reviewer still uses `feedback` (channel) because "approve vs.
  revise" is a conditional branch that wiring can't express.

Re-run and the ratchet completes without the writer ever having to
remember to call `send_message` — wiring fires regardless.

## Step 6 — Add a root for interactive use (optional)

Channels + wiring give you a headless cooperative team. If you want a
single conversational surface — the user talks to one agent and that
agent drives the team — add a **root**:

```yaml
terrarium:
  name: writer_team
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md   # team-specific delegation prompt
  creatures:
    - ...
```

Create `prompts/root.md` next to the terrarium yaml — it only needs
to carry delegation style; the framework auto-generates the topology
awareness section listing the team's creatures and channels, and
force-injects the [group tools](../concepts/glossary.md#group-tools)
(`group_add_node`, `group_status`, `group_channel`, `group_wire`, …)
that let root manage the graph from inside.

The TUI mounts root on its main tab; you talk to root, root talks to
the team. See [privileged node concept](../concepts/multi-agent/privileged-node.md)
for more.

## What you learned

- A terrarium is the runtime engine. It owns structure (the channel
  graph, topology, sessions, output wiring) and runs no LLM of its
  own.
- Creatures stay standalone; the engine tells them who can hear
  what, who can send where, and where their turn-end output flows.
- Two cooperation mechanisms compose freely:
  - **Channels** — conditional, optional, broadcast. The creature
    chooses whether and where to send.
  - **Output wiring** — deterministic pipeline edges. Fires on every
    turn-end regardless of what the creature does.
- Root is optional. Skip it for headless workflows; add it when you
  want a single conversational surface.

## What to read next

- [Terrarium concept](../concepts/multi-agent/terrarium.md) — the
  contract and its boundaries.
- [Privileged node concept](../concepts/multi-agent/privileged-node.md)
  — the user-facing privileged node designated by `root:`.
- [Terrariums guide](../guides/terrariums.md) — the practical how-to
  reference.
- [Channel concept](../concepts/modules/channel.md) — broadcast
  semantics, observers, and where channels cross module lines.
