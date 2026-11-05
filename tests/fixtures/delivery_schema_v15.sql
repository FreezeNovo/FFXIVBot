-- Minimal schema-15 delivery snapshot. Synthetic identifiers only.
CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO app_meta VALUES ('schema_version', '15');
CREATE TABLE target (
    target_id TEXT PRIMARY KEY, provider TEXT NOT NULL, target_type TEXT NOT NULL,
    provider_target_id TEXT NOT NULL, display_name TEXT,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(provider,target_type,provider_target_id)
);
INSERT INTO target VALUES ('fixture-target','onebot','group','fixture-group',NULL,1,
                          '2026-08-01T00:00:00+00:00','2026-08-01T00:00:00+00:00');
CREATE TABLE notification_candidate (
    notification_id TEXT PRIMARY KEY, business_key TEXT NOT NULL UNIQUE,
    target_id TEXT NOT NULL REFERENCES target(target_id) ON DELETE CASCADE,
    stream TEXT NOT NULL, window_key TEXT NOT NULL, message_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','sent','failed','superseded')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error TEXT
);
CREATE TABLE delivery_attempt (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id TEXT NOT NULL REFERENCES notification_candidate(notification_id) ON DELETE CASCADE,
    attempted_at TEXT NOT NULL, success INTEGER NOT NULL CHECK(success IN (0,1)),
    retryable INTEGER NOT NULL CHECK(retryable IN (0,1)), provider_message_id TEXT, error TEXT
);
INSERT INTO notification_candidate VALUES
('fixture-sent','fixture-sent','fixture-target','activity_digest','fixture-window','sent text','sent','2026-08-01T00:00:00+00:00','2026-08-01T00:00:00+00:00',NULL),
('fixture-retry','fixture-retry','fixture-target','activity_digest','fixture-window','retry text','pending','2026-08-01T00:00:00+00:00','2026-08-01T00:00:00+00:00',NULL),
('fixture-pending','fixture-pending','fixture-target','maintenance','fixture-window','pending text','pending','2026-08-01T00:00:00+00:00','2026-08-01T00:00:00+00:00',NULL);
INSERT INTO delivery_attempt VALUES
(1,'fixture-sent','2026-08-01T00:00:00+00:00',1,0,'fixture-provider',NULL),
(2,'fixture-retry','2026-08-01T00:00:00+00:00',0,1,NULL,'fixture timeout');
