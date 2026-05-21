-- Initial schema — auth.db v1.
-- See ../schema.sql for the canonical reference.

CREATE TABLE IF NOT EXISTS schema_version (
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
CREATE INDEX idx_users_username ON users(username);

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

CREATE TABLE invitations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    created_by  INTEGER REFERENCES users(id),
    role        TEXT NOT NULL DEFAULT 'user',
    expires_at  TEXT,
    used_by     INTEGER REFERENCES users(id),
    used_at     TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_invitations_token_hash ON invitations(token_hash);
