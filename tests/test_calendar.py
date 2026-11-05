from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ff14_core.calendar import (
    ActivityDigestRenderer,
    ActivityDigestWorkflow,
    CalendarQueryService,
)
from ff14_core.messaging import OutboundMessage, SendResult, TargetRef
from ff14_core.source_models import (
    ActivityDigestPolicy,
    ChinaNewsDetail,
    ClassifiedEventCandidate,
    EventCandidate,
    SourceDocument,
)
from ff14_core.storage import StateStore


class FakeMessaging:
    def __init__(self, results: dict[str, SendResult] | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.results = results or {}

    async def reply(self, request, message: OutboundMessage) -> SendResult:
        raise AssertionError("reply is not used by the digest workflow")

    async def send_proactive(self, target: TargetRef, message: OutboundMessage) -> SendResult:
        self.sent.append((target.provider_target_id, message.text))
        return self.results.get(
            target.provider_target_id,
            SendResult(success=True, provider_message_id="fixture"),
        )


class RaisingMessaging(FakeMessaging):
    def __init__(self, failing_group: str) -> None:
        super().__init__()
        self.failing_group = failing_group

    async def send_proactive(self, target: TargetRef, message: OutboundMessage) -> SendResult:
        if target.provider_target_id == self.failing_group:
            raise RuntimeError("provider disconnected")
        return await super().send_proactive(target, message)


class CalendarWorkflowTests(unittest.TestCase):
    def test_query_renderer_and_idempotent_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            target = store.save_onebot_group("group-test", enabled=True)
            store.set_activity_digest_policy(
                target.target_id,
                ActivityDigestPolicy(
                    enabled=True,
                    digest_hour=20,
                    digest_minute=0,
                    upcoming_window_minutes=7 * 24 * 60,
                    ending_window_minutes=60,
                    timezone="UTC",
                ),
            )
            self._record_entries(store)

            now = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
            digest = CalendarQueryService(store).query(target, now=now)
            self.assertEqual([entry.title for entry in digest.ongoing], ["进行中活动"])
            self.assertEqual([entry.title for entry in digest.upcoming], ["即将开始活动"])
            self.assertEqual([entry.title for entry in digest.ending], ["即将结束活动"])

            rendered = ActivityDigestRenderer().render(digest)
            self.assertIn("【正在进行的活动】", rendered)
            self.assertIn("C-进行中活动", rendered)
            self.assertIn("【即将开始的活动】", rendered)

            workflow = ActivityDigestWorkflow(store, CalendarQueryService(store))
            delayed_tick = now.replace(second=30)
            self.assertEqual(workflow.plan_due(now=delayed_tick), 1)
            self.assertEqual(workflow.plan_due(now=delayed_tick), 0)
            messaging = FakeMessaging()
            self.assertEqual(asyncio.run(workflow.dispatch_pending(messaging, now=now)), 1)
            self.assertEqual(asyncio.run(workflow.dispatch_pending(messaging, now=now)), 0)
            self.assertEqual(len(messaging.sent), 1)

    def test_retryable_target_failure_does_not_block_other_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            first = store.save_onebot_group("group-fails", enabled=True)
            second = store.save_onebot_group("group-succeeds", enabled=True)
            policy = ActivityDigestPolicy(enabled=True, digest_hour=20, timezone="UTC")
            store.set_activity_digest_policy(first.target_id, policy)
            store.set_activity_digest_policy(second.target_id, policy)
            workflow = ActivityDigestWorkflow(store, CalendarQueryService(store))
            now = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
            self.assertEqual(workflow.plan_due(now=now), 2)
            messaging = FakeMessaging(
                {
                    "group-fails": SendResult(success=False, retryable=True, error="offline")
                }
            )
            self.assertEqual(asyncio.run(workflow.dispatch_pending(messaging, now=now)), 2)
            self.assertEqual(len(messaging.sent), 2)
            self.assertEqual(len(store.list_pending_notifications()), 1)
            self.assertEqual(asyncio.run(workflow.dispatch_pending(FakeMessaging(), now=now)), 1)
            self.assertEqual(len(store.list_pending_notifications()), 0)

    def test_provider_exception_does_not_block_other_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            first = store.save_onebot_group("group-raises", enabled=True)
            second = store.save_onebot_group("group-succeeds", enabled=True)
            policy = ActivityDigestPolicy(enabled=True, digest_hour=20, timezone="UTC")
            store.set_activity_digest_policy(first.target_id, policy)
            store.set_activity_digest_policy(second.target_id, policy)
            workflow = ActivityDigestWorkflow(store, CalendarQueryService(store))
            now = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
            self.assertEqual(workflow.plan_due(now=now), 2)

            messaging = RaisingMessaging("group-raises")
            self.assertEqual(asyncio.run(workflow.dispatch_pending(messaging, now=now)), 2)
            self.assertEqual(len(messaging.sent), 1)
            self.assertEqual(len(store.list_pending_notifications()), 0)
            connection = sqlite3.connect(store._database_path)
            attempts = connection.execute(
                "SELECT success, retryable, error FROM delivery_attempt ORDER BY attempt_id"
            ).fetchall()
            connection.close()
            self.assertEqual(attempts[0][0:2], (0, 0))
            self.assertIn("发送结果未知", attempts[0][2])

    def test_schedule_uses_local_day_and_survives_workflow_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            target = store.save_onebot_group("group-test", enabled=True)
            store.set_activity_digest_policy(
                target.target_id,
                ActivityDigestPolicy(
                    enabled=True,
                    digest_hour=20,
                    digest_minute=0,
                    timezone="Asia/Shanghai",
                ),
            )
            workflow = ActivityDigestWorkflow(store, CalendarQueryService(store))
            before = datetime(2026, 8, 16, 11, 59, tzinfo=timezone.utc)
            due = datetime(2026, 8, 16, 12, 1, tzinfo=timezone.utc)
            next_day = datetime(2026, 8, 17, 12, 1, tzinfo=timezone.utc)
            self.assertEqual(workflow.plan_due(now=before), 0)
            self.assertEqual(workflow.plan_due(now=due), 1)
            restarted = ActivityDigestWorkflow(store, CalendarQueryService(store))
            self.assertEqual(restarted.plan_due(now=due), 0)
            self.assertEqual(restarted.plan_due(now=next_day), 0)  # 同一持续故障只提示一次

    def test_pending_digest_candidate_updates_before_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            target = store.save_onebot_group("group-test", enabled=True)
            business_key = "activity_digest:%s:2026-08-16T20:00:0" % target.target_id
            self.assertIsNotNone(
                store.create_notification_candidate(
                    business_key=business_key,
                    target_id=target.target_id,
                    stream="activity_digest",
                    window_key="2026-08-16T20:00",
                    message_text="旧摘要",
                )
            )
            self.assertIsNone(
                store.create_notification_candidate(
                    business_key=business_key,
                    target_id=target.target_id,
                    stream="activity_digest",
                    window_key="2026-08-16T20:00",
                    message_text="活动时间调整后的摘要",
                )
            )
            pending = store.list_pending_notifications()
            self.assertEqual(pending[0][2], "活动时间调整后的摘要")

    def test_non_retryable_delivery_is_recorded_as_failed_not_sent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            target = store.save_onebot_group("group-test", enabled=True)
            notification_id = store.create_notification_candidate(
                business_key="activity_digest:terminal-failure",
                target_id=target.target_id,
                stream="activity_digest",
                window_key="2026-08-16T20:00",
                message_text="摘要",
            )
            self.assertIsNotNone(notification_id)
            store.set_activity_digest_policy(target.target_id, ActivityDigestPolicy(enabled=True))
            claim = store.claim_notification(notification_id)
            store.finish_delivery_attempt(claim.attempt_id, SendResult(
                success=False,
                retryable=False,
                provider_message_id=None,
                error="不支持的投递目标",
            ))

            connection = sqlite3.connect(store._database_path)
            status, last_error = connection.execute(
                "SELECT status, last_error FROM notification_candidate WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
            connection.close()
            self.assertEqual(status, "failed")
            self.assertEqual(last_error, "不支持的投递目标")
            self.assertEqual(store.list_pending_notifications(), ())

    def test_removing_group_preserves_delivery_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            target = store.save_onebot_group("group-test", enabled=True)
            notification_id = store.create_notification_candidate(
                business_key="activity_digest:deletion",
                target_id=target.target_id,
                stream="activity_digest",
                window_key="2026-08-16T20:00",
                message_text="摘要",
            )
            store.set_activity_digest_policy(target.target_id, ActivityDigestPolicy(enabled=True))
            claim = store.claim_notification(notification_id)
            store.finish_delivery_attempt(claim.attempt_id, SendResult(
                success=True,
                retryable=False,
                provider_message_id="provider-1",
                error=None,
            ))

            self.assertTrue(store.delete_onebot_group("group-test"))
            connection = sqlite3.connect(store._database_path)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM delivery_attempt WHERE notification_id = ?",
                    (notification_id,),
                ).fetchone()[0],
                1,
            )
            connection.close()

    def test_stale_source_is_visible_in_digest_when_policy_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            target = store.save_onebot_group("group-test", enabled=True)
            store.set_activity_digest_policy(
                target.target_id,
                ActivityDigestPolicy(enabled=True, timezone="UTC"),
            )
            store.record_source_success(
                source_id="china_news_api",
                display_name="fixture",
                http_status=200,
                raw_payload=b"fixture",
                documents=(),
            )
            connection = sqlite3.connect(store._database_path)
            connection.execute(
                "UPDATE source_state SET last_success_at = ? WHERE source_id = ?",
                ("2020-01-01T00:00:00+00:00", "china_news_api"),
            )
            connection.commit()
            connection.close()
            digest = CalendarQueryService(
                store, source_stale_after=timedelta(hours=1)
            ).query(target, now=datetime(2026, 8, 16, tzinfo=timezone.utc))
            rendered = ActivityDigestRenderer().render(digest)
            self.assertIn("数据源数据已超过允许新鲜度", rendered)
            self.assertIn("无法确认是否没有活动", rendered)

    def test_group_digest_is_independent_of_global_calendar_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / 'state.sqlite3')
            store.initialize()
            target = store.save_onebot_group('fixture-group', enabled=True)
            self._record_entries(store)
            store.set_calendar_category_included('季节活动', False)
            digest = CalendarQueryService(store).query(target, now=datetime(2026, 8, 16, 20, tzinfo=timezone.utc))
            self.assertTrue(digest.ongoing or digest.ending)
            self.assertEqual(store.get_calendar_entries('2026-08-01T00:00:00+00:00', '2026-09-01T00:00:00+00:00'), ())

    def test_activity_start_is_inclusive_when_future_window_is_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / 'state.sqlite3')
            store.initialize()
            target = store.save_onebot_group('fixture-group', enabled=True)
            self._record_entries(store)
            store.set_activity_digest_policy(target.target_id, ActivityDigestPolicy(upcoming_window_minutes=0, ending_window_minutes=0))
            digest = CalendarQueryService(store).query(target, now=datetime(2026, 8, 16, 18, tzinfo=timezone.utc))
            self.assertEqual({item.document_id for item in digest.ongoing}, {'ongoing', 'ending'})

    def _record_entries(self, store: StateStore) -> None:
        documents = []
        entries = (
            ("ongoing", "进行中活动", "2026-08-16T18:00:00+00:00", "2026-08-17T18:00:00+00:00"),
            ("upcoming", "即将开始活动", "2026-08-16T21:00:00+00:00", "2026-08-17T21:00:00+00:00"),
            ("ending", "即将结束活动", "2026-08-16T18:00:00+00:00", "2026-08-16T21:00:00+00:00"),
        )
        for document_id, title, start_at, end_at in entries:
            documents.append(
                SourceDocument(
                    source_id="china_news_api",
                    document_id=document_id,
                    title=title,
                    url=f"https://example.invalid/{document_id}",
                    published_at="2026-08-15T00:00:00+00:00",
                    updated_at=None,
                    category="5310",
                    summary=None,
                    image_url=None,
                )
            )
        store.record_source_success(
            source_id="china_news_api",
            display_name="fixture",
            http_status=200,
            raw_payload=b"fixture",
            documents=tuple(documents),
        )
        for document_id, title, start_at, end_at in entries:
            candidate = EventCandidate(
                source_id="china_news_api",
                document_id=document_id,
                title=title,
                candidate_id="candidate",
                kind="activity",
                label="活动时间",
                evidence_text=f"{start_at} 至 {end_at}",
                start_at=start_at,
                end_at=end_at,
                timezone="UTC",
                confidence="high",
                region="cn",
            )
            store.record_china_detail_success(
                ChinaNewsDetail(
                    source_id="china_news_api",
                    document_id=document_id,
                    title=title,
                    published_at=None,
                    content_html="<p>fixture</p>",
                    content_text="fixture",
                ),
                (
                    ClassifiedEventCandidate(
                        candidate=candidate,
                        classification="seasonal",
                        classification_label="季节活动",
                        routing="calendar_candidate",
                        routing_label="日历候选",
                        reason="fixture",
                    ),
                ),
                "fixture",
            )


if __name__ == "__main__":
    unittest.main()
