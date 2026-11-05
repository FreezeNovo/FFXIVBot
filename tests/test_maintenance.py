from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ff14_core.maintenance import MaintenanceQueryService, MaintenanceRenderer, MaintenanceWorkflow
from ff14_core.messaging import SendResult, TargetRef
from ff14_core.source_models import ClassifiedEventCandidate, ChinaNewsDetail, EventCandidate, MaintenancePolicy, SourceDocument
from ff14_core.storage import StateStore


class FakeMaintenanceMessaging:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_proactive(self, target: TargetRef, message) -> SendResult:
        self.sent.append(message.text)
        return SendResult(success=True, provider_message_id="maintenance-fixture")


class MaintenanceWorkflowTests(unittest.TestCase):
    def _store(self):
        temporary_directory = tempfile.TemporaryDirectory()
        store = StateStore(Path(temporary_directory.name) / "state.sqlite3")
        store.initialize()
        target = store.save_onebot_group("group-maintenance", enabled=True)
        store.set_maintenance_policy(
            target.target_id,
            MaintenancePolicy(enabled=True, timezone="UTC", reminder_window_minutes=24 * 60),
        )
        return temporary_directory, store, target

    def _record_maintenance(self, store: StateStore, *, start: datetime) -> None:
        document = SourceDocument(
            source_id="china_news_api",
            document_id="maintenance-1",
            title="全区维护公告",
            url="https://example.invalid/maintenance-1",
            published_at=None,
            updated_at=None,
            category=None,
            summary=None,
            image_url=None,
        )
        store.record_source_success(
            source_id="china_news_api",
            display_name="fixture",
            http_status=200,
            raw_payload=b"maintenance",
            documents=(document,),
        )
        candidate = EventCandidate(
            source_id="china_news_api",
            document_id=document.document_id,
            title=document.title,
            candidate_id="maintenance-version-1",
            kind="maintenance",
            label="维护时间",
            evidence_text="维护时间：明确时间",
            start_at=start.isoformat(),
            end_at=(start + timedelta(hours=4)).isoformat(),
            timezone="UTC",
            confidence="high",
        )
        store.record_china_detail_success(
            ChinaNewsDetail(
                source_id="china_news_api",
                document_id=document.document_id,
                title=document.title,
                published_at=None,
                content_html="<p>fixture</p>",
                content_text="fixture",
            ),
            (
                ClassifiedEventCandidate(
                    candidate=candidate,
                    classification="maintenance",
                    classification_label="维护",
                    routing="maintenance_candidate",
                    routing_label="维护候选",
                    reason="fixture",
                ),
            ),
            "fixture",
        )

    def test_new_maintenance_sends_immediate_without_duplicate_near_start_reminder(self) -> None:
        temporary_directory, store, target = self._store()
        self.addCleanup(temporary_directory.cleanup)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._record_maintenance(store, start=now + timedelta(hours=2))
        workflow = MaintenanceWorkflow(store, MaintenanceQueryService(store))
        self.assertEqual(workflow.plan_due(now=now), 1)
        messaging = FakeMaintenanceMessaging()
        self.assertEqual(asyncio.run(workflow.dispatch_pending(messaging)), 1)
        self.assertEqual(len(messaging.sent), 1)
        self.assertIn("发现新的维护公告", messaging.sent[0])
        self.assertEqual(workflow.plan_due(now=now), 0)

    def test_time_update_supersedes_pending_old_version(self) -> None:
        temporary_directory, store, target = self._store()
        self.addCleanup(temporary_directory.cleanup)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._record_maintenance(store, start=now + timedelta(hours=2))
        workflow = MaintenanceWorkflow(store, MaintenanceQueryService(store))
        self.assertEqual(workflow.plan_due(now=now), 1)
        # Replace the candidate with a new parser version/time while both old
        # notifications are still pending.
        store.record_china_detail_success(
            ChinaNewsDetail(
                source_id="china_news_api",
                document_id="maintenance-1",
                title="全区维护公告",
                published_at=None,
                content_html="<p>changed</p>",
                content_text="changed",
            ),
            (
                ClassifiedEventCandidate(
                    candidate=EventCandidate(
                        source_id="china_news_api",
                        document_id="maintenance-1",
                        title="全区维护公告",
                        candidate_id="maintenance-version-2",
                        kind="maintenance",
                        label="维护时间",
                        evidence_text="维护时间：调整后",
                        start_at=(now + timedelta(hours=3)).isoformat(),
                        end_at=(now + timedelta(hours=7)).isoformat(),
                        timezone="UTC",
                        confidence="high",
                    ),
                    classification="maintenance",
                    classification_label="维护",
                    routing="maintenance_candidate",
                    routing_label="维护候选",
                    reason="fixture",
                ),
            ),
            "fixture",
        )
        self.assertEqual(workflow.plan_due(now=now), 1)
        pending = store.list_pending_notifications(stream="maintenance")
        self.assertEqual(len(pending), 1)
        self.assertIn("维护时间发生变更", pending[0][2])

    def test_preview_is_separate_from_activity_digest(self) -> None:
        temporary_directory, store, target = self._store()
        self.addCleanup(temporary_directory.cleanup)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._record_maintenance(store, start=now + timedelta(hours=2))
        preview = MaintenanceWorkflow(store, MaintenanceQueryService(store)).preview(target, now=now)
        self.assertIn("FF14 国服维护", preview)
        self.assertIn("即将维护", preview)
        self.assertIn("全区维护公告", preview)

    def test_announcement_toggle_does_not_disable_reminder(self) -> None:
        temporary_directory, store, target = self._store()
        self.addCleanup(temporary_directory.cleanup)
        store.set_maintenance_policy(
            target.target_id,
            MaintenancePolicy(
                enabled=True,
                timezone="UTC",
                notify_announcements=False,
                reminder_window_minutes=24 * 60,
            ),
        )
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._record_maintenance(store, start=now + timedelta(hours=2))
        workflow = MaintenanceWorkflow(store, MaintenanceQueryService(store))
        self.assertEqual(workflow.plan_due(now=now), 1)
        pending = store.list_pending_notifications(stream="maintenance")
        self.assertIn("维护即将开始", pending[0][2])
        self.assertNotIn("发现新的维护公告", pending[0][2])

    def test_early_announcement_does_not_suppress_later_reminder(self):
        temporary, store, target = self._store()
        self.addCleanup(temporary.cleanup)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._record_maintenance(store, start=now + timedelta(hours=48))
        workflow = MaintenanceWorkflow(store, MaintenanceQueryService(store))
        port = FakeMaintenanceMessaging()
        self.assertEqual(asyncio.run(workflow.tick(port, now=now)), 1)
        self.assertEqual(asyncio.run(workflow.tick(port, now=now + timedelta(hours=25))), 1)
        self.assertIn('维护即将开始', port.sent[-1])

    def test_pending_reminder_is_not_sent_after_maintenance_starts(self):
        temporary, store, target = self._store()
        self.addCleanup(temporary.cleanup)
        store.set_maintenance_policy(target.target_id, MaintenancePolicy(enabled=True, notify_announcements=False))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._record_maintenance(store, start=now + timedelta(hours=2))
        workflow = MaintenanceWorkflow(store, MaintenanceQueryService(store))
        workflow.plan_due(now=now)
        port = FakeMaintenanceMessaging()
        asyncio.run(workflow.tick(port, now=now + timedelta(hours=3)))
        self.assertEqual(port.sent, [])

    def test_planner_interruption_after_observation_does_not_lose_announcement(self):
        temporary, store, target = self._store()
        self.addCleanup(temporary.cleanup)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._record_maintenance(store, start=now + timedelta(hours=48))
        candidate = store.get_maintenance_candidates()[0].candidate
        store.observe_maintenance_candidate(target_id=target.target_id, candidate=candidate,
            watermark_at=store.get_maintenance_policy(target.target_id).watermark_at)
        # Equivalent to interruption after observation commits, before candidate creation.
        restarted = MaintenanceWorkflow(store, MaintenanceQueryService(store))
        self.assertEqual(restarted.plan_due(now=now), 1)
        self.assertIn('发现新的维护公告', store.list_pending_notifications()[0][2])

    def test_delayed_announcement_covers_near_start_reminder(self):
        temporary, store, target = self._store()
        self.addCleanup(temporary.cleanup)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._record_maintenance(store, start=now + timedelta(hours=48))
        workflow = MaintenanceWorkflow(store, MaintenanceQueryService(store))
        workflow.plan_due(now=now)
        port = FakeMaintenanceMessaging()
        asyncio.run(workflow.tick(port, now=now + timedelta(hours=25)))
        self.assertEqual(len(port.sent), 1)
        self.assertIn('发现新的维护公告', port.sent[0])
        self.assertEqual(workflow.plan_due(now=now + timedelta(hours=26)), 0)

    def test_rejected_announcement_does_not_suppress_independent_reminder(self):
        temporary, store, target = self._store()
        self.addCleanup(temporary.cleanup)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._record_maintenance(store, start=now + timedelta(hours=2))
        workflow = MaintenanceWorkflow(store, MaintenanceQueryService(store))
        class Rejected:
            async def send_proactive(self, target, message):
                return SendResult(success=False, retryable=False, error='fixture rejection')
        asyncio.run(workflow.tick(Rejected(), now=now))
        port = FakeMaintenanceMessaging()
        asyncio.run(workflow.tick(port, now=now + timedelta(minutes=1)))
        self.assertEqual(len(port.sent), 1)
        self.assertIn('维护即将开始', port.sent[0])

    def test_parser_revision_without_time_change_does_not_repeat_notice(self):
        temporary, store, target = self._store()
        self.addCleanup(temporary.cleanup)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._record_maintenance(store, start=now + timedelta(hours=2))
        workflow = MaintenanceWorkflow(store, MaintenanceQueryService(store))
        port = FakeMaintenanceMessaging()
        asyncio.run(workflow.tick(port, now=now))
        item = store.get_maintenance_candidates()[0]
        store.record_china_detail_success(
            ChinaNewsDetail('china_news_api', 'maintenance-1', '全区维护公告', None, '<p>same times</p>', 'same times'),
            (replace(item, candidate=replace(item.candidate, candidate_id='parser-revision-2')),), 'parser-revision-2')
        asyncio.run(workflow.tick(port, now=now))
        self.assertEqual(len(port.sent), 1)


if __name__ == "__main__":
    unittest.main()
