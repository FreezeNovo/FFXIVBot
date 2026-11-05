from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from pathlib import Path

from ff14_core.application import CoreApplication
from ff14_core.config import AppConfig
from ff14_core.messaging import CommandRequest, SendResult
from ff14_core.source_models import ActivityDigestPolicy, MaintenancePolicy, ManualQueryPolicy


class RecordingMessaging:
    def __init__(self, *, lose_response=False):
        self.sent = []
        self.lose_response = lose_response

    async def send_proactive(self, target, message):
        self.sent.append(message.text)
        if self.lose_response:
            raise TimeoutError('fixture: provider accepted, response lost')
        return SendResult(success=True, provider_message_id='fixture-message')


class ReliabilityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'test.sqlite3'
        self.app = CoreApplication.create(AppConfig(self.path))
        self.app.start()
        self.store = self.app.store
        self.target = self.store.save_onebot_group('fixture-group', enabled=True)
        self.store.set_owner_user_id('fixture-owner')
        self.store.set_activity_digest_policy(self.target.target_id, ActivityDigestPolicy(enabled=True))
        self.store.set_maintenance_policy(self.target.target_id, MaintenancePolicy(enabled=True))

    def candidate(self, stream='activity_digest'):
        return self.store.create_notification_candidate(
            business_key=f'{stream}:fixture', target_id=self.target.target_id,
            stream=stream, window_key='fixture-window', message_text='fixture message',
        )

    def rows(self, query):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(query).fetchall()

    def test_accepted_send_with_lost_response_is_not_retried(self):
        self.candidate()
        port = RecordingMessaging(lose_response=True)
        asyncio.run(self.app.digest_workflow.dispatch_pending(port))
        asyncio.run(self.app.digest_workflow.dispatch_pending(port))
        self.assertEqual(len(port.sent), 1)
        self.assertEqual(self.rows('SELECT status FROM notification_candidate'), [('uncertain',)])

    def test_disabled_activity_policy_stops_pending_messages(self):
        self.candidate()
        self.store.set_activity_digest_policy(self.target.target_id, ActivityDigestPolicy(enabled=False))
        port = RecordingMessaging()
        asyncio.run(self.app.digest_workflow.dispatch_pending(port))
        self.assertEqual(port.sent, [])

    def test_disabled_maintenance_policy_stops_pending_messages(self):
        self.candidate('maintenance')
        self.store.set_maintenance_policy(self.target.target_id, MaintenancePolicy(enabled=False))
        port = RecordingMessaging()
        asyncio.run(self.app.maintenance_workflow.dispatch_pending(port))
        self.assertEqual(port.sent, [])

    def test_removing_group_preserves_delivery_history(self):
        self.candidate()
        asyncio.run(self.app.digest_workflow.dispatch_pending(RecordingMessaging()))
        self.assertTrue(self.store.delete_onebot_group('fixture-group'))
        self.assertEqual(self.store.list_targets(), ())
        self.assertEqual(self.rows('SELECT status FROM notification_candidate'), [('sent',)])
        self.assertEqual(self.rows('SELECT success FROM delivery_attempt'), [(1,)])

    def test_status_and_aliases_require_configured_owner(self):
        for action in ('', '状态', 'status'):
            for role in (None, 'owner', 'admin'):
                with self.subTest(action=action, role=role):
                    result = asyncio.run(self.app.command_service.handle(CommandRequest(
                        target=self.target, actor_id='fixture-member', actor_role=role, text=action,
                    )))
                    self.assertIn('仅限 Bot 所有者', result.message.text)

    def test_interrupted_attempt_is_persisted_and_held_on_restart(self):
        notification = self.candidate()
        claim = self.store.claim_notification(notification)
        self.assertIsNotNone(claim)
        self.assertEqual(self.rows("SELECT outcome FROM delivery_attempt"), [('in_flight',)])
        restarted = CoreApplication.create(AppConfig(self.path))
        restarted.start()
        port = RecordingMessaging()
        asyncio.run(restarted.digest_workflow.dispatch_pending(port))
        self.assertEqual(port.sent, [])
        self.assertEqual(self.rows("SELECT status FROM notification_candidate"), [('uncertain',)])
        self.assertTrue(self.store.close_uncertain_delivery(notification))
        self.assertFalse(self.store.close_uncertain_delivery(notification))
        self.assertEqual(self.rows("SELECT outcome FROM delivery_attempt"), [('uncertain',)])
        self.assertEqual(len(self.rows("SELECT * FROM delivery_review")), 1)

    def test_concurrent_wakeups_only_claim_once(self):
        self.candidate()
        port = RecordingMessaging()
        async def exercise():
            await asyncio.gather(*(self.app.digest_workflow.dispatch_pending(port) for _ in range(2)))
        asyncio.run(exercise())
        self.assertEqual(len(port.sent), 1)

    def test_readding_archived_group_preserves_history_and_resets_subscriptions(self):
        self.candidate()
        asyncio.run(self.app.digest_workflow.dispatch_pending(RecordingMessaging()))
        self.store.set_manual_query_policy(self.target.target_id, ManualQueryPolicy(True, True))
        self.store.delete_onebot_group('fixture-group')
        self.assertFalse(self.store.delete_onebot_group('fixture-group'))
        target = self.store.save_onebot_group('fixture-group', enabled=True)
        self.assertEqual(target.target_id, self.target.target_id)
        self.assertFalse(self.store.get_activity_digest_policy(target.target_id).enabled)
        self.assertFalse(self.store.get_maintenance_policy(target.target_id).enabled)
        self.assertEqual(self.store.get_manual_query_policy(target.target_id), ManualQueryPolicy())
        self.assertEqual(len(self.rows('SELECT * FROM delivery_attempt')), 1)

    def test_member_query_permissions_are_independent_and_take_effect_immediately(self):
        def request(action, actor='fixture-member', target=None):
            return asyncio.run(self.app.command_service.handle(CommandRequest(
                target=target or self.target, actor_id=actor, actor_role=None, text=action,
            )))
        self.assertIn('尚未开启', request('活动').message.text)
        self.store.set_activity_digest_policy(self.target.target_id, ActivityDigestPolicy(enabled=False))
        self.store.set_manual_query_policy(self.target.target_id, ManualQueryPolicy(True, False))
        self.assertIn('FF14 活动摘要', request('活动').message.text)
        self.assertIn('尚未开启', request('维护').message.text)
        self.assertIn('FF14 国服维护', request('维护', 'fixture-owner').message.text)
        self.store.set_manual_query_policy(self.target.target_id, ManualQueryPolicy())
        self.assertIn('尚未开启', request('calendar').message.text)
        self.assertFalse(request('状态', target=replace(self.target, target_type='private')).handled)
        self.store.save_onebot_group('fixture-group', enabled=False)
        self.assertFalse(request('状态', 'fixture-owner').handled)

    def test_expired_candidates_do_not_send(self):
        now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.store.create_notification_candidate(
            business_key='deadline', target_id=self.target.target_id, stream='activity_digest',
            window_key='deadline', message_text='old', expires_at=now.isoformat(),
        )
        port = RecordingMessaging()
        asyncio.run(self.app.digest_workflow.dispatch_pending(port, now=now))
        self.assertEqual(port.sent, [])
        self.assertEqual(self.rows('SELECT status FROM notification_candidate'), [('superseded',)])

    def test_schedule_edit_does_not_send_second_daily_digest(self):
        now = datetime(2026, 10, 30, 20, tzinfo=timezone.utc)
        policy = ActivityDigestPolicy(enabled=True, timezone='UTC', digest_hour=19)
        self.store.set_activity_digest_policy(self.target.target_id, policy)
        port = RecordingMessaging()
        asyncio.run(self.app.digest_workflow.tick(port, now=now))
        self.store.set_activity_digest_policy(self.target.target_id, replace(policy, digest_hour=20))
        asyncio.run(self.app.digest_workflow.tick(port, now=now))
        self.assertEqual(len(port.sent), 1)

    def test_digest_does_not_replay_previous_day(self):
        now = datetime(2026, 10, 30, 20, tzinfo=timezone.utc)
        self.store.set_activity_digest_policy(self.target.target_id, ActivityDigestPolicy(enabled=True, timezone='UTC'))
        self.app.digest_workflow.plan_due(now=now)
        port = RecordingMessaging()
        asyncio.run(self.app.digest_workflow.tick(port, now=now + timedelta(days=1)))
        self.assertEqual(len(port.sent), 1)

    def test_renderer_splits_a_single_oversized_line(self):
        from ff14_core.calendar import ActivityDigestRenderer
        text = '活动' * 1000
        chunks = ActivityDigestRenderer.split(text, maximum_length=100)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))
        self.assertEqual(''.join(chunks), text)

    def test_shrinking_and_growing_pending_digest_pages_does_not_send_old_pages(self):
        now = datetime(2026, 10, 30, 20, tzinfo=timezone.utc)
        self.store.set_activity_digest_policy(self.target.target_id, ActivityDigestPolicy(enabled=True, timezone='UTC'))
        workflow = self.app.digest_workflow
        with patch.object(workflow._renderer, 'split', return_value=('old-a', 'old-b')):
            workflow.plan_due(now=now)
        with patch.object(workflow._renderer, 'split', return_value=('short',)):
            workflow.plan_due(now=now)
        self.assertEqual([row[2] for row in self.store.list_pending_notifications()], ['short'])
        with patch.object(workflow._renderer, 'split', return_value=('new-a', 'new-b')):
            workflow.plan_due(now=now)
        port = RecordingMessaging()
        asyncio.run(workflow.dispatch_pending(port, now=now))
        self.assertEqual(port.sent, ['new-a', 'new-b'])

    def test_schema_from_newer_application_is_not_downgraded(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE app_meta SET value = '999' WHERE key = 'schema_version'")
            connection.commit()
        with self.assertRaises(ValueError):
            self.store.initialize()
        self.assertEqual(self.store.get_meta('schema_version'), '999')

    def test_disabling_during_unsent_attempt_prevents_replay_on_reenable(self):
        self.candidate()
        store, target = self.store, self.target
        class DisableDuringAttempt:
            async def send_proactive(self, target, message):
                store.set_activity_digest_policy(target.target_id, ActivityDigestPolicy(enabled=False))
                return SendResult(success=False, retryable=True, error='fixture definitely not sent')
        asyncio.run(self.app.digest_workflow.dispatch_pending(DisableDuringAttempt()))
        store.set_activity_digest_policy(target.target_id, ActivityDigestPolicy(enabled=True))
        port = RecordingMessaging()
        asyncio.run(self.app.digest_workflow.dispatch_pending(port))
        self.assertEqual(port.sent, [])

    def test_legacy_successful_daily_window_is_not_sent_again(self):
        now = datetime(2026, 10, 30, 20, tzinfo=timezone.utc)
        self.store.create_notification_candidate(business_key='legacy:2026-10-30T19:00:0',
            target_id=self.target.target_id, stream='activity_digest', window_key='2026-10-30T19:00', message_text='fixture')
        port = RecordingMessaging()
        asyncio.run(self.app.digest_workflow.dispatch_pending(port, now=now))
        self.store.set_activity_digest_policy(self.target.target_id, ActivityDigestPolicy(enabled=True, timezone='UTC'))
        asyncio.run(self.app.digest_workflow.tick(port, now=now))
        self.assertEqual(len(port.sent), 1)


if __name__ == '__main__':
    unittest.main()
