SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS refund_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id   TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL CHECK (amount_cents > 0),
    currency      TEXT NOT NULL DEFAULT 'USD',
    reason        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_by  INTEGER NOT NULL REFERENCES users(id),
    created_at    TEXT NOT NULL,
    decided_by    INTEGER REFERENCES users(id),
    decided_at    TEXT,
    decision_note TEXT,
    -- Database-level backstop for separation of duties (service layer checks first).
    CHECK (decided_by IS NULL OR decided_by <> requested_by)
);
CREATE INDEX IF NOT EXISTS ix_refunds_status ON refund_requests (status);
CREATE INDEX IF NOT EXISTS ix_refunds_customer ON refund_requests (customer_id);
"""
