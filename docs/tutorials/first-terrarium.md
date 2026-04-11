# First Terrarium

This tutorial walks through building a small two-creature terrarium.

By the end, you will have:

- two creature configs
- one terrarium config
- queue and broadcast channels
- a runnable multi-agent system

## What you are building

You will build:

- a `worker` creature that receives tasks
- a `reviewer` creature that reviews results
- a terrarium that wires them together

This is a simple but very useful pattern because it shows the split between creature behavior and terrarium topology.

## Step 1: create the folders

```text
my-team/
  terrarium.yaml
  creatures/
    worker/
      config.yaml
      prompts/
        system.md
    reviewer/
      config.yaml
      prompts/
        system.md
```

## Step 2: create the worker creature

`creatures/worker/config.yaml`

```yaml
name: worker
base_config: "@kt-defaults/creatures/swe"
system_prompt_file: prompts/system.md
```

`creatures/worker/prompts/system.md`

```markdown
# Worker

You focus on doing the task and reporting useful work product.
```

## Step 3: create the reviewer creature

`creatures/reviewer/config.yaml`

```yaml
name: reviewer
base_config: "@kt-defaults/creatures/reviewer"
system_prompt_file: prompts/system.md
```

`creatures/reviewer/prompts/system.md`

```markdown
# Reviewer

You focus on reviewing the worker's output carefully and giving actionable feedback.
```

## Step 4: create `terrarium.yaml`

```yaml
terrarium:
  name: my_team

  creatures:
    - name: worker
      config: ./creatures/worker
      channels:
        listen: [tasks, feedback]
        can_send: [review, team_chat]

    - name: reviewer
      config: ./creatures/reviewer
      channels:
        listen: [review]
        can_send: [feedback, team_chat]

  channels:
    tasks:
      type: queue
      description: Incoming work for the worker
    review:
      type: queue
      description: Work product for review
    feedback:
      type: queue
      description: Reviewer feedback to the worker
    team_chat:
      type: broadcast
      description: Shared status updates
```

## Step 5: run the terrarium

```bash
kt terrarium run path/to/my-team
```

If you prefer, run the shipped team first to compare the experience:

```bash
kt terrarium run @kt-defaults/terrariums/swe_team
```

## Step 6: understand the split

This is the key lesson.

### The creatures define

- role behavior
- prompts
- tools
- internal agent logic

### The terrarium defines

- who can hear what
- who can send where
- queue vs broadcast topology
- collaboration structure

That means the terrarium is not replacing the creatures. It is wiring them.

## Step 7: understand the channels

### `tasks`

A queue channel for incoming work.

### `review`

A queue channel for passing work product to the reviewer.

### `feedback`

A queue channel for sending review feedback back to the worker.

### `team_chat`

A broadcast channel for shared updates.

This combination is common:

- queue channels for work handoff
- broadcast channels for shared awareness

## What you learned

You just learned the core terrarium model:

1. creatures stay standalone agents
2. the terrarium wires them through channels
3. topology belongs in the terrarium, not in creature internals

## Next steps

- [Terrariums](../guides/terrariums.md)
- [Channels](../concepts/channels.md)
- [Terrariums Concept](../concepts/terrariums.md)
