# Impl notes

Four specific implementation choices that deserve their own short
concept doc — not because a user has to know them to use KT, but
because they surface design decisions that recur elsewhere and are
useful mental models in their own right.

- [Non-blocking compaction](non-blocking-compaction.md) — how a
  creature summarises its own history without pausing the controller.
- [Stream parser](stream-parser.md) — why tools start before the LLM
  stops talking.
- [Prompt aggregation](prompt-aggregation.md) — how the final system
  prompt is assembled (base + tools + hints + topology + named
  outputs + plugins), and why `skill_mode` lets you choose "ship full
  docs" vs "load on demand."
- [Session persistence](session-persistence.md) — the dual-store model
  (append-only event log + conversation snapshots) that lets one
  `.kohakutr` serve resume, human search, and agent-side RAG.

Each doc follows the same shape: *problem → options considered → what
we do → invariants preserved → where it lives in the code.*
