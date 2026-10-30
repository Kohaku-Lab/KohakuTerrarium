# Resume Path Reuse Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce repeated ancestry processing during session resume without changing branch selection, compaction, or resumable work.

**Architecture:** Keep every derived cache local to one immutable event projection and selected view. Share validated path compatibility and parent indexes within that projection; rebuild after normalization or filtering changes the event sequence. Preserve existing behavior for malformed or custom Python inputs.

**Tech Stack:** Python 3.10+, pytest, KohakuVault.

## Task 1: Cache immutable path compatibility

**Files:** `src/kohakuterrarium/session/history_selection.py`, `tests/unit/session/test_history_selection.py`.

1. Add deterministic operation budgets for repeated immutable paths, including varying event turns, with compatible and incompatible ancestry. Preserve the existing differential tests for legacy, malformed, custom integer, and custom tuple inputs.
2. Run `.venv/bin/python -m pytest -n0 tests/unit/session/test_history_selection.py -q`; confirm the new budget fails on repeated validation.
3. Cache each exact built-in tuple by identity, retain its reference, and calculate the first conflicting parent turn once. Cache selected-branch comparisons for shared parent pairs. Answer compatibility as `conflict is None or conflict >= turn`. Keep noncanonical fallback evaluation unchanged.
4. Rerun the focused test file and audit cache lifetime, identity reuse, boolean aliases, and mutation handling.

## Task 2: Share parent indexes within replay

**Files:** `src/kohakuterrarium/session/branch_paths.py`, `src/kohakuterrarium/session/history.py`, `tests/unit/session/test_branch_paths.py`, `tests/unit/session/test_history.py`, `tests/unit/session/test_history_selection.py`.

Also share the raw projection's index in `src/kohakuterrarium/core/agent_message_history.py` (`replay_with_settled_tools` and `snapshot_needs_settled_tool_replay`), covered by `tests/unit/core/agent/history/test_message_history.py`. Normalization still creates a separate replay projection and therefore requires its own index.

1. Add a replay work budget requiring one parent index for the deduplicated event projection; assert exact branch and compaction output.
2. Run the focused file and confirm the budget fails.
3. Construct one index in `replay_conversation` and pass it to live selection and compact rule selection. Intern identical indexed paths in `branch_paths.py` and reuse legacy ancestry until an earlier branch maximum changes.
4. Rerun the focused files, including duplicate IDs, nested forks, legacy order dependence, and malformed metadata.

## Task 3: Workflow verification and audit

**Files:** `tests/integration/test_session.py` and the touched source/tests above.

1. Extend the existing session workflow with a nested branch resume and compacted-prefix check if its current assertions do not cover the changed consumers.
2. Run `.venv/bin/python -m pytest -n0 tests/integration/test_session.py -q` with one worker.
3. Run Black and Ruff on touched files, then the targeted file-size/dependency guards. Run full tiers, frontend, and fork CI before publication, within the machine's resource budget.
4. Audit clear, integrity, and behavior risks; any newly found defect gets a failing regression test before its fix. Obtain independent review and commit the branch with exact validation results.

## Tail-only startup investigation

Existing `SessionStore.get_events(since_event_id=...)` decodes every value before filtering. Physical per-agent sequence keys do not equal session-global event IDs; imported/mirrored IDs can be out of order. `inject_saved_state` also publishes normalized full history through `_pending_resume_events`, delivered by `Agent._drive_input` as an output `resume_batch`. This reaches custom `OutputModule.on_resume` implementations and the rich CLI/TUI; Studio's attach stream discards it. Snapshots alone do not prove that pre-watermark tools have settled or that pending placeholders contain every result. A safe tail optimization must preserve these contracts and use a validated read cursor or lazy source; this change must not assume monotonic mirrored IDs, silently discard old resume-output events, or add an unapproved persistent schema. Legacy/ambiguous inputs retain full replay.
