-- Synthetic schema16 fixed snapshot. No real users, configuration, or credentials.
BEGIN TRANSACTION;
CREATE TABLE activity_digest_policy (
                    target_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    region TEXT NOT NULL,
                    classification_codes_json TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    digest_hour INTEGER NOT NULL CHECK (digest_hour BETWEEN 0 AND 23),
                    digest_minute INTEGER NOT NULL CHECK (digest_minute BETWEEN 0 AND 59),
                    upcoming_window_minutes INTEGER NOT NULL CHECK (upcoming_window_minutes >= 0),
                    ending_window_minutes INTEGER NOT NULL CHECK (ending_window_minutes >= 0),
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                );
INSERT INTO "activity_digest_policy" VALUES('fixture-target',1,'cn','["other_operational"]','Asia/Shanghai',20,3,60,30,'2026-10-01T04:00:00Z');
CREATE TABLE app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
INSERT INTO "app_meta" VALUES('schema_version','16');
INSERT INTO "app_meta" VALUES('first_started_at','2026-10-01T00:00:00+00:00');
INSERT INTO "app_meta" VALUES('last_started_at','2026-10-01T00:00:00+00:00');
INSERT INTO "app_meta" VALUES('owner_user_id','fixture-owner');
CREATE TABLE calendar_category_policy (
                    classification_label TEXT PRIMARY KEY,
                    included INTEGER NOT NULL CHECK (included IN (0, 1)),
                    updated_at TEXT NOT NULL
                );
CREATE TABLE "delivery_attempt" (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id TEXT NOT NULL REFERENCES "notification_candidate"(notification_id),
                attempted_at TEXT NOT NULL, success INTEGER NOT NULL CHECK(success IN (0,1)),
                retryable INTEGER NOT NULL CHECK(retryable IN (0,1)),
                provider_message_id TEXT, error TEXT,
                outcome TEXT NOT NULL CHECK(outcome IN ('in_flight','sent','not_sent','failed','uncertain')),
                completed_at TEXT
            );
INSERT INTO "delivery_attempt" VALUES(1,'notification-0','2026-11-04T12:00:00Z',1,0,'fixture-message',NULL,'sent','2026-11-04T12:00:00Z');
INSERT INTO "delivery_attempt" VALUES(2,'notification-1','2026-11-04T12:00:00Z',0,0,NULL,NULL,'uncertain','2026-11-04T12:00:00Z');
CREATE TABLE delivery_review (review_id INTEGER PRIMARY KEY AUTOINCREMENT, notification_id TEXT NOT NULL REFERENCES notification_candidate(notification_id), reviewed_at TEXT NOT NULL, decision TEXT NOT NULL);
CREATE TABLE event_candidate (
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    evidence_text TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT 'cn',
                    classification TEXT NOT NULL DEFAULT 'unclassified',
                    classification_label TEXT NOT NULL DEFAULT '未分类',
                    routing TEXT NOT NULL DEFAULT 'review',
                    routing_label TEXT NOT NULL DEFAULT '待审核',
                    classification_reason TEXT NOT NULL DEFAULT '',
                    parser_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, document_id, candidate_id)
                );
CREATE TABLE maintenance_observation (
                    target_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL, notification_kind TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (target_id, document_id),
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                );
CREATE TABLE maintenance_policy (
                    target_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    region TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    notify_announcements INTEGER NOT NULL DEFAULT 1 CHECK (notify_announcements IN (0, 1)),
                    reminder_window_minutes INTEGER NOT NULL CHECK (reminder_window_minutes >= 0),
                    notify_updates INTEGER NOT NULL CHECK (notify_updates IN (0, 1)),
                    watermark_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                );
INSERT INTO "maintenance_policy" VALUES('fixture-target',1,'cn','Asia/Shanghai',1,360,0,'2026-10-01T04:00:00Z','2026-10-01T04:00:00Z');
CREATE TABLE maintenance_reminder_suppression (
                    target_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (target_id, document_id, candidate_id),
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                );
CREATE TABLE "notification_candidate" (
                notification_id TEXT PRIMARY KEY, business_key TEXT NOT NULL UNIQUE,
                target_id TEXT NOT NULL REFERENCES target(target_id),
                stream TEXT NOT NULL, window_key TEXT NOT NULL, message_text TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','sending','uncertain','sent','failed','superseded')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error TEXT,
                expires_at TEXT, kind TEXT NOT NULL DEFAULT '', region TEXT
            , retirement_code TEXT NOT NULL DEFAULT '');
INSERT INTO "notification_candidate" VALUES('notification-0','activity_digest:fixture-target:2026-11-04:0','fixture-target','activity_digest','2026-11-04','fixture history','sent','2026-11-04T12:00:00Z','2026-11-04T12:00:00Z',NULL,NULL,'','cn','');
INSERT INTO "notification_candidate" VALUES('notification-1','activity_digest:fixture-target:2026-11-04:1','fixture-archived','activity_digest','2026-11-04','fixture history','uncertain','2026-11-04T12:00:00Z','2026-11-04T12:00:00Z',NULL,NULL,'','cn','');
CREATE TABLE schema_migration (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
INSERT INTO "schema_migration" VALUES('16','2026-11-04T13:08:49+00:00');
CREATE TABLE source_document (
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT,
                    updated_at TEXT,
                    category TEXT,
                    summary TEXT,
                    image_url TEXT,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, document_id)
                );
INSERT INTO "source_document" VALUES('china_news_api','fixture-article','Fixture event','https://example.invalid/article','2026/10/01 12:00:00',NULL,'5310',NULL,NULL,'2026-10-01T04:00:00Z');
CREATE TABLE source_document_detail (
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    content_html TEXT NOT NULL,
                    content_text TEXT NOT NULL, parse_status TEXT NOT NULL DEFAULT 'unknown', parser_error TEXT, candidate_count INTEGER NOT NULL DEFAULT 0, parser_version TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (source_id, document_id)
                );
INSERT INTO "source_document_detail" VALUES('china_news_api','fixture-article','body-hash','2026-10-01T04:00:00Z','<p>Fixture</p>','Fixture','parsed',NULL,1,'china-time-v3');
CREATE TABLE source_document_detail_version (
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    content_html TEXT NOT NULL,
                    content_text TEXT NOT NULL,
                    PRIMARY KEY (source_id, document_id, content_hash)
                );
INSERT INTO "source_document_detail_version" VALUES('china_news_api','fixture-article','body-hash','2026-10-01T04:00:00Z','<p>Fixture</p>','Fixture');
CREATE TABLE source_snapshot (
                    source_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (source_id, content_hash)
                );
INSERT INTO "source_snapshot" VALUES('china_news_api','fixture-hash','2026-10-01T04:00:00Z',200,X'66697874757265');
CREATE TABLE source_state (
                    source_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    last_attempt_at TEXT NOT NULL,
                    last_success_at TEXT,
                    last_http_status INTEGER,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    document_count INTEGER NOT NULL DEFAULT 0,
                    last_document_at TEXT,
                    etag TEXT,
                    last_modified TEXT
                );
CREATE TABLE target (
                    target_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    provider_target_id TEXT NOT NULL,
                    display_name TEXT,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, activity_query_enabled INTEGER NOT NULL DEFAULT 0, maintenance_query_enabled INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(provider, target_type, provider_target_id)
                );
INSERT INTO "target" VALUES('fixture-target','onebot','group','fixture-fixture-target',NULL,1,'2026-10-01T00:00:00Z','2026-10-01T00:00:00Z',0,1,0);
INSERT INTO "target" VALUES('fixture-archived','onebot','group','fixture-fixture-archived',NULL,1,'2026-10-01T00:00:00Z','2026-10-01T00:00:00Z',1,1,0);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('delivery_attempt',2);
INSERT INTO event_candidate VALUES('china_news_api','fixture-article','fixture-event','activity','活动时间','活动时间：2026年11月4日20:00至2026年11月6日20:00','2026-11-04T20:00:00+08:00','2026-11-06T20:00:00+08:00','Asia/Shanghai','high','cn','other_operational','其他运营活动','calendar_candidate','日历候选','fixed legacy evidence','china-time-v3','2026-10-01T04:00:00Z');
COMMIT;
