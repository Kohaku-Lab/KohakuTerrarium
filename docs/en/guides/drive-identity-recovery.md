# Recovering Drive identity after a session stop

Stopping a Studio session unloads its complete graph. It preserves the saved
creature IDs, graph ID, channels, Drive owner, scope, assignment, and lifecycle
state. Explicitly removing a creature still means deletion and retains the
normal orphan/unassignment rules. Studio and laboratory worker session attachment
also bind Drive storage before returning, including rows created in memory before
the session store was attached.

Worker session unload is retryable with the same graph ID and saved session path.
A timeout does not prove that a worker is still running: the dormant marker is
retained until the retry confirms unloading. Upgrade both controller and worker;
an older worker without the unload operation returns an error rather than falling
back to destructive per-creature removal.

## Existing orphaned goals

This fix prevents future identity loss; it does not guess which creature owns an
old orphan. A displayed owner-to-assignee arrow is not an identity migration log.
Matching names alone is insufficient, especially when several creatures share
a configuration. Establish the old and current IDs from the saved session and
its live-graph manifest before repairing anything.

The repository includes an offline, single-Drive repair utility. Run it from a
checkout installed in the current Python environment. First stop the session
using the fixed version and confirm no process holds its writer lock. Preview:

```sh
python scripts/repair_drive_identity.py --session /path/run.kohakutr \
  --drive-id DRIVE_ID --old-creature-id OLD_ID --creature-id CURRENT_ID
```

The preview is read-only. Check the owner, scope, assignment, status, and revision.
Owner transfer is separate: add `--transfer-owner` to both preview and apply only
when the old creature really owned this commitment. Apply the exact previewed
revision:

```sh
python scripts/repair_drive_identity.py --session /path/run.kohakutr \
  --drive-id DRIVE_ID --old-creature-id OLD_ID --creature-id CURRENT_ID \
  --transfer-owner --expected-revision REVISION --apply
```

Apply acquires the session writer lock and backs up both SQLite databases,
including committed WAL data, under `drive-repair-backups/`. It changes identity
fields in one transaction, fences obsolete deliveries with a new lifecycle epoch,
and records an audit entry. It preserves status and status reason, creator, and
origin scope. Repeating a completed repair makes no further record changes.

Blocked and paused goals remain blocked and paused. Review their prior attempts
before explicitly resuming them through the normal Drive controls: delivery is
at least once, and an admitted attempt may already have performed side effects.
The utility does not start an engine or execute a goal.

Terminal goals, missing targets, conflicting scopes/assignments, and mappings
whose old creature still exists are rejected. A legacy session without a manifest
must first be restored **as a copy**, with Drive delivery disabled; stop it with
the fixed version to persist its current manifest. Then verify the mapping again.
If evidence is insufficient or graph scope and assignment disagree, leave the
record untouched for manual investigation. Do not clear the blocked reason or
activate an orphan based only on its name.

For rollback, keep the session stopped and restore both files from the same
backup directory. Do not replace either database while a runtime has it open.
