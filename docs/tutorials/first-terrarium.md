# First Terrarium

**Problem:** you want two creatures to cooperate — a writer produces
something, a reviewer critiques it — and you want to see the messages
flow between them.

**End state:** a terrarium config with two creatures and two channels,
running under the TUI, visibly passing messages from one to the other.

**Prerequisites:** [First Creature](first-creature.md). You should have
`kt-defaults` installed and be able to `kt run` a single creature.

A terrarium is a **pure wiring layer**: it owns channels and manages
creature lifecycles. It has no LLM of its own. The intelligence stays
inside each creature. See
[terrarium concept](../concepts/multi-agent/terrarium.md) for the full
contract.

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
      base_config: "@kt-defaults/creatures/general"
      system_prompt: |
        You are a concise writer. When you receive a message on
        `tasks`, write a short draft and send it to `review` using
        send_message. When you receive feedback, revise and resend.
      channels:
        listen:    [tasks, feedback]
        can_send:  [review]

    - name: reviewer
      base_config: "@kt-defaults/creatures/reviewer"
      system_prompt: |
        You critique drafts. When you receive a message on `review`,
        reply with one or two concrete improvement suggestions on
        `feedback` using send_message. If the draft is good, say so.
      channels:
        listen:    [review]
        can_send:  [feedback]

  channels:
    tasks:    { type: queue, description: "Incoming work for the writer" }
    review:   { type: queue, description: "Drafts sent to the reviewer" }
    feedback: { type: queue, description: "Review notes sent back" }
```

What the wiring does:

- `listen` registers a `ChannelTrigger` on the creature — when a message
  lands on one of those channels, the creature wakes up and sees it.
- `can_send` enumerates channels the creature's `send_message` tool is
  allowed to write to. A creature cannot reach channels that are not in
  this list.
- Channels are declared once in `channels:`. `queue` delivers each
  message to one consumer; `broadcast` delivers to all listeners.

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

## Step 5 — Understand the honest limit

Horizontal multi-agent has one characteristic failure mode: **progress
depends on each creature actually routing its output to the right
channel.** If a model forgets to call `send_message`, the channel stays
empty and the team stalls.

Two workarounds you can reach for today:

1. **Strong prompting.** Tell the creature very explicitly which
   channel to emit to and when. The inline prompts above do this.
2. **Add a root agent.** A root creature sits *outside* the terrarium
   and owns the terrarium-management tools. It receives user input,
   seeds the team, observes channels, and nudges creatures that stall.
   See `@kt-defaults/creatures/root` and the `swe_team` terrarium for a
   worked example. The [root agent concept](../concepts/multi-agent/root-agent.md)
   explains the pattern.

Example — add a root:

```yaml
terrarium:
  name: writer_team
  root:
    base_config: "@kt-defaults/creatures/root"
  # ... creatures and channels as before
```

Now the TUI mounts the root agent on its main tab and you talk to it
directly; it orchestrates the writer/reviewer through terrarium tools.

## Step 6 — Where terrariums are going

Auto-routing (a configurable "creature's last message always goes to
channel X"), root lifecycle observation, and dynamic creature
management are on the roadmap. Until they land, prefer explicit
prompting or a root creature for anything important. The full picture
is in the [ROADMAP](../../ROADMAP.md) terrarium section.

## What you learned

- A terrarium is wiring. It adds no intelligence.
- Creatures stay standalone; the terrarium tells them who can hear
  what and who can send where.
- Horizontal multi-agent is real but explicit — prompts drive the
  routing, and the current failure mode is stalls.
- A root creature is the practical answer when you want a user-facing
  orchestrator around the team.

## What to read next

- [Terrarium concept](../concepts/multi-agent/terrarium.md) — the
  contract and its boundaries.
- [Root agent concept](../concepts/multi-agent/root-agent.md) — the
  user-facing creature.
- [Terrariums guide](../guides/terrariums.md) — the practical how-to
  reference.
- [Channel concept](../concepts/modules/channel.md) — queue vs
  broadcast, observers, and where channels cross module lines.
