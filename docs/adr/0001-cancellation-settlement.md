# ADR 0001: Cancellation Is a Request Before Settlement

- Status: Proposed
- Date: 2026-11-14
- Related issue: [#582](https://github.com/Kohaku-Lab/KohakuTerrarium/issues/582)
- Related PR: [#583](https://github.com/Kohaku-Lab/KohakuTerrarium/pull/583)

## Context

A cancellation request and a settled job represent two distinct runtime states. A cancellation request races with ongoing tool execution, cleanup routines, result collection, persistence, and the admission of subsequent turns. Treating an initial request as an immediate terminal outcome causes multiple independent code paths to publish conflicting terminal events, drops completed results when a collector is cancelled mid-flight, and allows subsequent provider requests to observe incomplete tool call histories.

This failure mode is especially pronounced for direct jobs, where executor callbacks, standard collectors, interrupt handlers, stop paths, and finalizers can all concurrently observe the same underlying task. Session history replay must also distinguish a tool result that arrived late within the same turn from a result belonging to a separate invocation.

## Decision

Cancellation follows a request-before-settlement contract:

1. `cancel()` requests cancellation; it does not by itself publish a terminal outcome.
2. Every direct job has exactly one terminal-settlement owner. Other paths may request cancellation, observe state changes, or hand off ownership, but terminal publication is strictly idempotent and owned.
3. The tool's actual terminal outcome remains authoritative whenever the tool suppresses cancellation or completes execution during cleanup.
4. A completed result remains recoverable until its designated settlement owner collects and persists it, and the settlement path must not publish duplicate terminal results.
5. Subsequent turns are admitted only after required interruption finalizers and result handoffs have settled. Any settlement failure fails closed rather than dispatching incomplete conversation history to a provider.
6. Replay may fold a late result back into its originating call only when turn identity, causal boundaries, job identity, and provider identity are fully unambiguous. Ambiguous results are not folded into earlier calls, while original recorded events remain available for raw history inspection.
7. Clean-resume optimizations must reject snapshots containing pending work or completed-but-undelivered results.

## Consequences

### Positive

- Cancellation requests, execution, cleanup, delivery, persistence, and next-turn admission have distinct, observable lifecycle semantics.
- Direct jobs cannot be terminally published by multiple competing paths.
- Tools that handle `CancelledError` retain their authentic success or error outcomes.
- Completed results survive collector interruption until their settlement owner can reliably deliver them.
- Providers never receive conversation history containing an unpaired native tool call due to an interrupted prior turn.
- Replay avoids unsafe cross-turn assumptions or misattributions from reused provider IDs.

### Costs and limits

- Next-turn admission can be delayed while an interrupted or cancelled tool completes asynchronous cleanup.
- A finalizer failure blocks subsequent provider admission until the session is explicitly recovered.
- Ambiguous historical results are not folded into earlier calls and remain visible only through raw event inspection rather than being heuristically repaired.
- Exactly-once external side effects and crash durability are not established by this decision.
- Bounded protocol tests cover selected state spaces and deterministic witnesses, rather than every possible asyncio scheduling interleaving.

## Alternatives considered

### Mark cancellation terminal immediately

Rejected. This conflates a cancellation request with cleanup and delivery, overwrites authentic tool execution outcomes, and allows incomplete history to reach subsequent provider requests.

### Let every observer publish its own terminal result

Rejected. Executor, collector, interrupt, and stop paths can all observe the same task; allowing independent publication creates duplicate terminal events and redundant tool result messages.

### Cancel the waiter and discard completed results

Rejected. A side-effecting tool may have already completed execution. Dropping its result prevents replay and recovery from distinguishing completed work from work that never ran.

### Guess ambiguous replay identity

Rejected. Job IDs and provider call IDs can be reused across turns. Guessing associations risks attaching results to the wrong invocation or branch; conservative omission is safer.

### Introduce a universal runtime state machine now

Deferred. While the underlying invariant is shared, the repository does not yet possess two independent production seams requiring identical state-machine machinery. Keeping ownership and settlement rules explicit within the current modules is preferred; extracting a deep module should wait until repeated, concrete reuse is demonstrated.

## Verification expectations

The implementation must be verified through behavioral testing rather than state-shape inspection alone. Required evidence includes:

- Deterministic two-job transition witnesses.
- Cancellation during execution, cleanup, collection, and finalization.
- Completed-before-collection recovery without duplicate terminal publication.
- Repeated cancellation and settlement failure handling.
- Exact provider request batching and the absence of premature next-turn admission.
- Persisted event identity and ordering.
- Replay and branch isolation.
- Fault injection covering duplicate, missing, swapped, reversed, and repeated deliveries.
- Clean-resume rejection for pending or undelivered work.

## Follow-up

- Keep provider-level Codex HTTP response validation in PR #579 separate.
- Keep deferred result handling and broader replay semantics as a separate design and implementation.
- Re-run cold-resume cross-regressions after any changes to snapshot closure, result delivery, or checkpoint eligibility.
- Promote this ADR from Proposed to Accepted only after the cancellation settlement implementation is reviewed and merged.
