# Deferred multi-node lifecycle failure

Status: confirmed on the original baseline `b03a2853`; deliberately deferred
from the Responses WebSocket recovery and test-fixture changes.

## Resume after closing a worker-hosted session

Run:

```sh
pytest tests/e2e/test_multinode_journey.py -q
```

The journey restores its saved cluster on the original workers, closes the
returned runtime, then restores the same cluster again. The final `CF-6`
assertion requires HTTP 200. The current implementation can instead return:

```text
502: lab transport error: terrarium.files write_begin failed:
operation refuses an active session store
```

An isolated baseline investigation also reproduced this sequence with one
resumed creature. After the public close returned 200, the worker had no live
graphs or creatures, but `_session_stores` still held the removed graph's store
at `config://resume/<saved-session>.kohakutr`.

The remote stop path removes creatures through the worker runtime adapter.
`Terrarium.remove_creature` removes the last creature's graph and checkpoint
bookkeeping but leaves its store registered. Host-side stop cleanup does not
release that worker registry entry. The next transfer targets the same saved
file, and the active-store guard rejects the stale registration.

Relevant code:

- `src/kohakuterrarium/studio/sessions/stop.py`: remote teardown and host cleanup.
- `src/kohakuterrarium/laboratory/adapters/terrarium_runtime.py`: remove-creature dispatch.
- `src/kohakuterrarium/terrarium/engine.py`: last-creature removal.
- `src/kohakuterrarium/laboratory/adapters/terrarium_files.py`: active-store protection.
- `src/kohakuterrarium/api/routes/persistence/remote_resume_transfer.py`: repeated transfer destination.

The separate fix must release worker store registrations and resources with
correct ownership and checkpoint semantics. Retain the protection against
overwriting genuinely attached stores, and preserve persisted conversation
contents. A domain conflict should not be disguised as a generic transport
failure.

The journey intentionally remains a failing regression for this behavior.
Do not accept 502, skip the step, or mark the entire journey as expected failure:
that would hide unrelated regressions. After the lifecycle repair, verify the
whole restore-close-restore sequence, cluster membership, and saved contents.
