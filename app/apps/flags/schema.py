SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS feature_flags (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL,
    environment  TEXT NOT NULL CHECK (environment IN ('development', 'staging', 'production')),
    enabled      INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    updated_at   TEXT NOT NULL,
    updated_by   INTEGER REFERENCES users(id),
    UNIQUE (name, environment)
);
CREATE INDEX IF NOT EXISTS ix_flags_environment ON feature_flags (environment);
"""
