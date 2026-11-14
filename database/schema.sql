PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    signup_at TEXT NOT NULL,
    acquisition_channel TEXT NOT NULL,
    region TEXT NOT NULL,
    plan_type TEXT NOT NULL CHECK (plan_type IN ('free', 'premium'))
);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    device_type TEXT NOT NULL,
    bound_at TEXT NOT NULL,
    current_status TEXT NOT NULL CHECK (current_status IN ('online', 'offline')),
    last_seen_at TEXT NOT NULL,
    firmware_version TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    device_id TEXT,
    event_time TEXT NOT NULL,
    event_name TEXT NOT NULL,
    feature_name TEXT,
    session_id TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (device_id) REFERENCES devices(device_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    feedback_text TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    paid_at TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    currency TEXT NOT NULL,
    plan_type TEXT NOT NULL,
    payment_status TEXT NOT NULL CHECK (payment_status IN ('success', 'failed', 'refunded')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_events_user_time ON events(user_id, event_time);
CREATE INDEX IF NOT EXISTS idx_events_name_time ON events(event_name, event_time);
CREATE INDEX IF NOT EXISTS idx_feedback_user_time ON feedback(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_payments_user_time ON payments(user_id, paid_at);
CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);
