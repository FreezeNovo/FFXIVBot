import asyncio
from dataclasses import replace
from datetime import timedelta
from threading import Event
import unittest
from unittest.mock import patch
from ff14_core.application import CoreApplication
from ff14_core.config import AppConfig
from ff14_app.workers import ApplicationWorkers
from ff14_core.lodestone import LodestoneDetailService
from ff14_core.source_models import ActivityDigestPolicy
import test_p3_na_freshness as _fixtures
from test_p3_na_freshness import NOW,body
from test_calendar import FakeMessaging
from ff14_core.sources import HttpResponse


class PreflightTests(unittest.TestCase):
    setUp=_fixtures.NAFreshnessTests.setUp
    refresh=_fixtures.NAFreshnessTests.refresh

    def test_preflight_merges_groups_does_not_wait_at_deadline_and_late_result_no_second(self):
        self.refresh(NOW-timedelta(hours=47))
        second=self.store.save_onebot_group('second-fixture-group',enabled=True)
        policy=self.store.get_activity_digest_policy(self.target.target_id)
        self.store.set_activity_digest_policy(second.target_id,policy)
        app=CoreApplication.create(AppConfig(self.store._database_path));app.store=self.store
        entered=Event();release=Event();calls=[]
        class Blocking:
            def get(_self,url):
                calls.append(url);entered.set()
                if not release.wait(4):raise TimeoutError('test synchronization timed out')
                return HttpResponse(200,('<div class="news__detail__wrapper">'+body()+'</div>').encode())
        app.lodestone_detail_service=LodestoneDetailService(self.store,Blocking())
        messaging=FakeMessaging();worker=ApplicationWorkers(app,messaging)
        async def scenario():
            worker.reviews.preflight(now=NOW-timedelta(minutes=5))
            # Event loop checkpoints, without real waiting for a schedule interval.
            await asyncio.to_thread(entered.wait,2)
            self.assertEqual(len(calls),1)
            self.assertEqual(len(worker.reviews.tasks),1)
            await worker.deliver(now=NOW)
            self.assertEqual(len(messaging.sent),2)
            self.assertIn('本次复查尚未完成',messaging.sent[0][1])
            release.set()
            await asyncio.gather(*worker.reviews.tasks.values())
            await worker.deliver(now=NOW+timedelta(seconds=30))
            self.assertEqual(len(messaging.sent),2)
            await worker.reviews.stop()
        try:asyncio.run(scenario())
        finally:release.set()

    def test_stop_discards_thread_response(self):
        self.refresh()
        previous=self.store.get_event_verification('lodestone_topics','1')['last_success_at']
        app=CoreApplication.create(AppConfig(self.store._database_path));app.store=self.store
        entered=Event();release=Event()
        class Blocking:
            def get(_self,url):
                entered.set();release.wait(4)
                return HttpResponse(200,b'<div class="news__detail__wrapper">late changed response</div>')
        app.lodestone_detail_service=LodestoneDetailService(self.store,Blocking())
        worker=ApplicationWorkers(app,FakeMessaging())
        async def scenario():
            worker.reviews.schedule(self.document,now=NOW)
            await asyncio.to_thread(entered.wait,2)
            await worker.stop()
            release.set()
        try:asyncio.run(scenario())
        finally:release.set()
        self.assertEqual(self.store.get_event_verification('lodestone_topics','1')['last_success_at'],previous)
        self.assertNotEqual(self.store.get_document_versions('lodestone_topics','1')[0].content_html,'late changed response')

    def test_restart_recovers_reserved_window_and_schedule_change_rechecks(self):
        from datetime import datetime
        self.refresh(NOW-timedelta(hours=47))
        app=CoreApplication.create(AppConfig(self.store._database_path));app.store=self.store;app.lodestone_detail_service=self.service
        calls=[]
        original=self.transport.get
        def get(url):calls.append(url);return original(url)
        self.transport.get=get
        window=(NOW+timedelta(hours=8)).replace(tzinfo=__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).isoformat()
        self.store.reserve_preflight(self.target.target_id,window,self.document.source_id,self.document.document_id,now=NOW-timedelta(minutes=5))
        worker=ApplicationWorkers(app,FakeMessaging())
        async def scenario():
            with patch('ff14_core.storage.utc_now_text',return_value=(NOW-timedelta(minutes=5)).isoformat()):
                worker.reviews.preflight(now=NOW-timedelta(minutes=5))
                await asyncio.gather(*worker.reviews.tasks.values())
            self.assertEqual(len(calls),1)
            self.store.set_activity_digest_policy(self.target.target_id,replace(self.store.get_activity_digest_policy(self.target.target_id),digest_hour=21))
            with patch('ff14_core.storage.utc_now_text',return_value=(NOW+timedelta(minutes=55)).isoformat()):
                worker.reviews.preflight(now=NOW+timedelta(minutes=55))
                await asyncio.gather(*worker.reviews.tasks.values())
            self.assertEqual(len(calls),2)
            await worker.reviews.stop()
        asyncio.run(scenario())
