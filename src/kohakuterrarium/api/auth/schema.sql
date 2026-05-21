-- Auth schema — applied by migrations/001_initial.sql.
-- Kept here as a single readable reference; the migration runner
-- consumes the .sql files under migrations/ in lexical order.

CREATE TABLE schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);
-- Case-insensitive uniqueness applied at migration 002 — see
-- 002_uniqueness_and_invitations_fk.sql.  Sqlite's BINARY collation
-- on a plain ``UNIQUE`` column treats "Alice"/"alice" as distinct;
-- the index below makes the case-insensitive guard
-- ``LOWER(username) = LOWER(?)`` race-free.
CREATE UNIQUE INDEX idx_users_username_nocase
    ON users(username COLLATE NOCASE);

CREATE TABLE sessions (
    session_id  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    user_agent  TEXT,
    last_seen   TEXT
);
CREATE INDEX idx_sessions_user_id  ON sessions(user_id);
CREATE INDEX idx_sessions_expires  ON sessions(expires_at);

CREATE TABLE api_tokens (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    last_used_at  TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX idx_api_tokens_user_id ON api_tokens(user_id);

-- ``created_by`` / ``used_by`` ON DELETE SET NULL applied at
-- migration 002 (originally created without ``ON DELETE`` which
-- defaulted to RESTRICT, blocking admin deletion when the admin had
-- ever issued an invitation).  The dataclass models both fields as
-- ``int | None`` — the schema now matches.
CREATE TABLE invitations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    role        TEXT NOT NULL DEFAULT 'user',
    expires_at  TEXT,
    used_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    used_at     TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_invitations_token_hash ON invitations(token_hash);
