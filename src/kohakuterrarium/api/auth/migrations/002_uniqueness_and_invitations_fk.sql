-- Migration 002 — audit fixes.
--
-- 1. Case-insensitive username uniqueness via UNIQUE INDEX on
--    ``LOWER(username)``.  Migration 001's schema declared
--    ``username TEXT NOT NULL UNIQUE``, which sqlite's BINARY collation
--    treats as case-sensitive — so "Alice" and "alice" could both insert
--    even though the application-level guard tried to reject the second.
--    Two concurrent ``POST /auth/register`` calls could race past the
--    pre-insert SELECT and both win.
--
-- 2. ``invitations.created_by`` was declared without ``ON DELETE``,
--    which sqlite defaults to RESTRICT.  Deleting an admin who ever
--    issued an invitation would have failed the FK with
--    ``PRAGMA foreign_keys = ON``.  The dataclass already models the
--    field as ``int | None`` — make the schema match by switching to
--    ``ON DELETE SET NULL``.
--
-- sqlite has no ``ALTER COLUMN`` for these — the standard
-- recreate-and-rename pattern is the only way.  Both changes are
-- wrapped in a single transaction so a partial apply rolls back cleanly.

-- Username uniqueness ----------------------------------------------------
-- NOTE: migration 001 declared ``username TEXT NOT NULL UNIQUE`` on the
-- column itself, which sqlite enforces with BINARY collation.  That
-- column-level constraint stays in place after this migration —
-- a deliberate redundancy because we can't drop the constraint without
-- a full table recreate (sqlite has no ``DROP CONSTRAINT``), and the
-- case-insensitive index below is the stricter one anyway.  Two
-- INSERTs of ``Alice`` and ``ALICE`` will fail at the case-insensitive
-- index first; two INSERTs of ``Alice`` would have failed under either.
DROP INDEX IF EXISTS idx_users_username;
CREATE UNIQUE INDEX idx_users_username_nocase
    ON users(username COLLATE NOCASE);

-- Invitations FK ---------------------------------------------------------
CREATE TABLE invitations_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    role        TEXT NOT NULL DEFAULT 'user',
    expires_at  TEXT,
    used_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    used_at     TEXT,
    created_at  TEXT NOT NULL
);
INSERT INTO invitations_new
    (id, token_hash, created_by, role, expires_at, used_by, used_at, created_at)
SELECT id, token_hash, created_by, role, expires_at, used_by, used_at, created_at
FROM invitations;
DROP TABLE invitations;
ALTER TABLE invitations_new RENAME TO invitations;
CREATE INDEX idx_invitations_token_hash ON invitations(token_hash);
