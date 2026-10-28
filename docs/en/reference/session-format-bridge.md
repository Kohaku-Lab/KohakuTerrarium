# Session format write admission

Before a session archive is opened for writing, its format marker and exact
archive-family companions are checked. Missing markers in an existing metadata
table are treated as version 1. Invalid markers, unreadable metadata, uncertain
handle release, and versions newer than this installation are rejected. A
standalone legacy memory archive without a metadata table remains supported
only when the native constructor raises exactly `KohakuVaultError` with
`SQLite error: no such table: meta` and its schema matches the known memory
layout on a private copy. Errors during later reads do not trigger that exception.
The gate does not apply to `SessionStore(..., read_only=True)`. The existing v2
format is unchanged; this adds no migration. Read-only version discovery is
best-effort and descriptive, not writable admission; every writable migration
and store or memory open performs the strict check independently.

Admission uses a native read-only handle without an immutable snapshot, so it
can observe committed WAL data. Opening that handle may create or update SQLite
WAL/SHM bookkeeping; it does not intentionally change logical session data.
General metadata readers retain their existing independent policies. This gate
protects writes by versions containing it; older binaries do not gain protection.
Format detection and a later writable open are not a cross-process transaction.
