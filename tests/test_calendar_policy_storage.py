from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from ff14_core.source_models import (
    ActivityDigestPolicy,
    ChinaNewsDetail,
    ClassifiedEventCandidate,
    EventCandidate,
    SourceDocument,
)
from ff14_core.storage import StateStore, SCHEMA_VERSION


class CalendarPolicyStorageTests(unittest.TestCase):
    def test_notification_status_and_delivery_fk_are_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "state.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO app_meta VALUES ('schema_version', '13');
                CREATE TABLE target (
                    target_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    provider_target_id TEXT NOT NULL,
                    display_name TEXT,
                    enabled INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, target_type, provider_target_id)
                );
                INSERT INTO target VALUES (
                    'target-1', 'onebot', 'group', 'group-1', NULL, 1,
                    '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00'
                );
                CREATE TABLE notification_candidate (
                    notification_id TEXT PRIMARY KEY,
                    business_key TEXT NOT NULL UNIQUE,
                    target_id TEXT NOT NULL,
                    stream TEXT NOT NULL,
                    window_key TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'sent')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT,
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                );
                CREATE TABLE delivery_attempt (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    success INTEGER NOT NULL CHECK (success IN (0, 1)),
                    retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
                    provider_message_id TEXT,
                    error TEXT,
                    FOREIGN KEY (notification_id) REFERENCES notification_candidate(notification_id)
                );
                INSERT INTO notification_candidate VALUES
                    ('n-failed', 'failed-key', 'target-1', 'activity_digest', 'w', 'm',
                     'sent', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00', 'upstream'),
                    ('n-old', 'old-key', 'target-1', 'maintenance', 'w', 'm',
                     'sent', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00', '被新的维护时间版本替代');
                INSERT INTO delivery_attempt(
                    notification_id, attempted_at, success, retryable, error
                ) VALUES ('n-failed', '2026-08-01T00:00:00+00:00', 0, 0, 'upstream');
                """
            )
            connection.commit()
            connection.close()

            store = StateStore(database)
            store.initialize()
            connection = sqlite3.connect(database)
            statuses = dict(
                connection.execute(
                    "SELECT notification_id, status FROM notification_candidate"
                ).fetchall()
            )
            delivery_fk = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'delivery_attempt'"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(statuses, {"n-failed": "failed", "n-old": "superseded"})
            self.assertNotIn("ON DELETE CASCADE", delivery_fk.upper())
            self.assertEqual(store.get_meta("schema_version"), SCHEMA_VERSION)

    def test_old_upcoming_window_is_migrated_to_seven_days(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "state.sqlite3"
            store = StateStore(database)
            store.initialize()
            target = store.save_onebot_group("group-test", enabled=True)
            store.set_activity_digest_policy(
                target.target_id,
                ActivityDigestPolicy(
                    enabled=True,
                    upcoming_window_minutes=24 * 60,
                ),
            )
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE app_meta SET value = '9' WHERE key = 'schema_version'"
            )
            connection.commit()
            connection.close()
            StateStore(database).initialize()
            self.assertEqual(
                StateStore(database).get_activity_digest_policy(target.target_id).upcoming_window_minutes,
                7 * 24 * 60,
            )

    def test_v11_maintenance_policy_gains_independent_announcement_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "state.sqlite3"
            store = StateStore(database)
            store.initialize()
            target = store.save_onebot_group("group-test", enabled=True)
            connection = sqlite3.connect(database)
            connection.execute("DROP TABLE maintenance_policy")
            connection.execute(
                "CREATE TABLE maintenance_policy ("
                "target_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, region TEXT NOT NULL, "
                "timezone TEXT NOT NULL, reminder_window_minutes INTEGER NOT NULL, "
                "notify_updates INTEGER NOT NULL, watermark_at TEXT, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO maintenance_policy VALUES (?, 1, 'cn', 'Asia/Shanghai', 360, 0, NULL, '2026-08-24T00:00:00+00:00')",
                (target.target_id,),
            )
            connection.execute(
                "UPDATE app_meta SET value = '11' WHERE key = 'schema_version'"
            )
            connection.commit()
            connection.close()

            StateStore(database).initialize()
            policy = StateStore(database).get_maintenance_policy(target.target_id)
            self.assertTrue(policy.notify_announcements)
            self.assertEqual(policy.reminder_window_minutes, 360)
            self.assertFalse(policy.notify_updates)
            self.assertEqual(StateStore(database).get_meta("schema_version"), SCHEMA_VERSION)

    def test_category_policy_controls_calendar_and_preserves_review_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            documents = (
                SourceDocument(
                    source_id="china_news_api",
                    document_id="seasonal",
                    title="季节活动",
                    url="https://example.invalid/seasonal",
                    published_at="2026/08/01 00:00:00",
                    updated_at=None,
                    category="5310",
                    summary=None,
                    image_url=None,
                ),
                SourceDocument(
                    source_id="china_news_api",
                    document_id="live",
                    title="直播活动",
                    url="https://example.invalid/live",
                    published_at="2026/08/01 00:00:00",
                    updated_at=None,
                    category="5310",
                    summary=None,
                    image_url=None,
                ),
            )
            store.record_source_success(
                source_id="china_news_api",
                display_name="fixture",
                http_status=200,
                raw_payload=b"fixture",
                documents=documents,
            )
            self._record_candidate(
                store,
                document_id="seasonal",
                kind="activity",
                classification_label="季节活动",
                routing="calendar_candidate",
            )
            self._record_candidate(
                store,
                document_id="live",
                kind="live_stream",
                classification_label="直播或转播",
                routing="review",
            )

            options = {item.classification_label: item for item in store.get_calendar_category_options()}
            self.assertTrue(options["季节活动"].included)
            self.assertFalse(options["直播或转播"].included)
            self.assertEqual(len(store.get_calendar_entries("2026-08-01T00:00:00+08:00", "2026-09-01T00:00:00+08:00")), 1)

            store.set_calendar_category_included("直播或转播", True)
            calendar_entries = store.get_calendar_entries(
                "2026-08-01T00:00:00+08:00",
                "2026-09-01T00:00:00+08:00",
            )
            self.assertEqual({entry.document_id for entry in calendar_entries}, {"seasonal", "live"})

            review = store.get_review_page(page=1, page_size=12)
            self.assertEqual(review.total_count, 1)
            self.assertEqual(review.entries[0].document_id, "live")

            store.set_calendar_category_included("季节活动", False)
            calendar_entries = store.get_calendar_entries(
                "2026-08-01T00:00:00+08:00",
                "2026-09-01T00:00:00+08:00",
            )
            self.assertEqual([entry.document_id for entry in calendar_entries], ["live"])

    def _record_candidate(
        self,
        store: StateStore,
        *,
        document_id: str,
        kind: str,
        classification_label: str,
        routing: str,
    ) -> None:
        candidate = EventCandidate(
            source_id="china_news_api",
            document_id=document_id,
            title=document_id,
            candidate_id="candidate",
            kind=kind,
            label="活动时间",
            evidence_text="活动时间：2026年8月2日16:00~2026年8月3日16:00",
            start_at="2026-08-02T16:00:00+08:00",
            end_at="2026-08-03T16:00:00+08:00",
            timezone="Asia/Shanghai",
            confidence="high",
        )
        store.record_china_detail_success(
            ChinaNewsDetail(
                source_id="china_news_api",
                document_id=document_id,
                title=document_id,
                published_at=None,
                content_html="<p>fixture</p>",
                content_text="fixture",
            ),
            (
                ClassifiedEventCandidate(
                    candidate=candidate,
                    classification="fixture",
                    classification_label=classification_label,
                    routing=routing,
                    routing_label="日历候选" if routing == "calendar_candidate" else "待审核",
                    reason="fixture",
                ),
            ),
            "fixture",
        )


if __name__ == "__main__":
    unittest.main()
