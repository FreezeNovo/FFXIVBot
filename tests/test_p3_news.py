"""公告资格、净变化、独立账本和补发批次的离线行为。"""
import asyncio
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from ff14_core.news import AnnouncementPolicy, AnnouncementWorkflow
from ff14_core.source_models import SourceDocument
from ff14_core.storage import StateStore
from ff14_core.messaging import SendResult
from test_calendar import FakeMessaging

NOW=datetime(2026,11,4,12,tzinfo=timezone.utc)


class NewsWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=StateStore(Path(self.directory.name)/'test.sqlite3');self.store.initialize()
        self.target=self.store.save_onebot_group('fixture-group',enabled=True)
        self.policy=AnnouncementPolicy(enabled=True)
        self.store.set_announcement_policy(self.target.target_id,self.policy,now=NOW.isoformat())
        self.flow=AnnouncementWorkflow(self.store)
        self.messaging=FakeMessaging()

    def article(self,id='1',text='Value 1',published=None,at=None,title='Fixture announcement'):
        stamp=at or NOW+timedelta(seconds=1)
        document=SourceDocument('china_news_api',id,title,'https://example.invalid/article/'+id,published or (NOW+timedelta(seconds=1)).isoformat(),None,'5310',None,None)
        return self.store.record_document_version(document,'<p>'+text+'</p>',now=stamp.isoformat())

    def tick(self,now=None,online=True):
        return asyncio.run(self.flow.tick(self.messaging,now=now or NOW+timedelta(seconds=10),online=online))

    def test_new_old_missing_publication_baseline_and_restart(self):
        self.article();self.article('old',published=(NOW-timedelta(days=5)).isoformat());
        doc=SourceDocument('china_news_api','unknown','Unknown','https://example.invalid',None,None,'5310',None,None)
        self.store.record_document_version(doc,'<p>A</p>',now=(NOW+timedelta(seconds=1)).isoformat())
        self.assertEqual(self.tick(),1);self.assertEqual(len(self.messaging.sent),1)
        self.flow=AnnouncementWorkflow(self.store);self.assertEqual(self.tick(),0)
        self.article('old','new old text',published=(NOW-timedelta(days=5)).isoformat(),at=NOW+timedelta(seconds=20))
        self.assertEqual(self.tick(NOW+timedelta(seconds=30)),1)
        self.assertIn('文章内容已变动',self.messaging.sent[-1][1])

    def test_pending_versions_net_change_and_return_after_success(self):
        self.article(text='A');self.tick()
        self.article(text='B',at=NOW+timedelta(seconds=20));self.flow.plan_due(now=NOW+timedelta(seconds=21))
        self.article(text='A',at=NOW+timedelta(seconds=22));self.assertEqual(self.tick(NOW+timedelta(seconds=23)),0)
        self.article(text='B',at=NOW+timedelta(seconds=24));self.assertEqual(self.tick(NOW+timedelta(seconds=25)),1)
        self.article(text='A',at=NOW+timedelta(seconds=26));self.assertEqual(self.tick(NOW+timedelta(seconds=27)),1)
        self.assertIn('B → A',self.messaging.sent[-1][1])

    def test_disable_reenable_scope_and_uncertain_no_repack(self):
        self.article();self.flow.plan_due(now=NOW+timedelta(seconds=2))
        self.store.set_announcement_policy(self.target.target_id,replace(self.policy,enabled=False),now=(NOW+timedelta(seconds=3)).isoformat())
        self.assertEqual(self.tick(),0)
        self.store.set_announcement_policy(self.target.target_id,self.policy,now=(NOW+timedelta(seconds=11)).isoformat())
        self.assertEqual(self.tick(NOW+timedelta(seconds=12)),0)
        self.article(text='B',at=NOW+timedelta(seconds=13))
        self.messaging=FakeMessaging({'fixture-group':SendResult(success=False,uncertain=True,error='unknown')})
        self.assertEqual(self.tick(NOW+timedelta(seconds=14)),1)
        self.assertEqual(self.tick(NOW+timedelta(hours=1)),0)
        self.assertEqual(self.store.list_deliveries()[0].status,'uncertain')

    def test_digest_empty_and_recovery_72h_inclusive(self):
        self.store.set_announcement_policy(self.target.target_id,replace(self.policy,mode='digest'),now=NOW.isoformat())
        self.article();self.assertEqual(self.tick(NOW+timedelta(seconds=10)),1) # 20:00 Beijing
        self.assertEqual(self.tick(NOW+timedelta(days=1)),0)
        # New long-enabled target, items on each side of the recovery horizon.
        self.store.set_announcement_policy(self.target.target_id,self.policy,now=NOW.isoformat())
        for i,seconds in [('inside',1),('edge',0),('outside',-1)]:
            self.article(i,at=NOW+timedelta(seconds=seconds),published=(NOW+timedelta(seconds=max(seconds,0))).isoformat())
        self.messaging=FakeMessaging({'fixture-group':SendResult(success=False,retryable=True,error='offline')})
        self.tick(NOW+timedelta(seconds=20),online=False)
        self.messaging=FakeMessaging();self.tick(NOW+timedelta(hours=72))
        content='\n'.join(text for _,text in self.messaging.sent)
        self.assertIn('离线补发汇总',content);self.assertIn('/inside',content);self.assertIn('/edge',content);self.assertNotIn('/outside',content)
        self.assertEqual(self.tick(NOW+timedelta(hours=72,seconds=1)),0)

    def test_partial_batch_unknown_restart_and_policy_removes_one_region(self):
        self.store.set_announcement_policy(self.target.target_id,replace(self.policy,mode='digest'),now=NOW.isoformat())
        for index in range(5):self.article(str(index),text=('Value '+str(index)+' ')*35)
        self.flow.plan_due(now=NOW+timedelta(seconds=2))
        pending=self.store.list_pending_notifications(stream='announcement');self.assertGreater(len(pending),1)
        first=self.store.claim_notification(pending[0][0],now=NOW+timedelta(seconds=3))
        self.store.finish_delivery_attempt(first.attempt_id,SendResult(True,provider_message_id='fixture-first'))
        second=self.store.claim_notification(pending[1][0],now=NOW+timedelta(seconds=3))
        self.assertIsNotNone(second)
        self.store.recover_interrupted_deliveries()
        self.flow=AnnouncementWorkflow(self.store)
        self.tick(NOW+timedelta(seconds=4))
        statuses=[d.status for d in self.store.list_deliveries()]
        self.assertIn('uncertain',statuses);self.assertEqual(statuses.count('uncertain'),1)
        self.assertFalse(any(text==pending[0][2] or text==pending[1][2] for _,text in self.messaging.sent))
        self.assertEqual(self.tick(NOW+timedelta(seconds=5)),0)

    def test_added_scope_keeps_original_watermark_and_maintenance_separate(self):
        before=self.store.get_announcement_policy(self.target.target_id)
        self.article();self.tick()
        with self.store._connection() as c:
            original=dict(c.execute('SELECT category,watermark_at FROM news_scope WHERE region="cn"'))
        newer=replace(before,regions=('cn','global-na'))
        self.store.set_announcement_policy(self.target.target_id,newer,now=(NOW+timedelta(hours=1)).isoformat())
        with self.store._connection() as c:self.assertEqual(dict(c.execute('SELECT category,watermark_at FROM news_scope WHERE region="cn"')),original)
        maintenance=SourceDocument('lodestone_atom','na-maint','Maintenance title','https://example.invalid/maintenance',(NOW+timedelta(hours=2)).isoformat(),None,'Maintenance',None,None)
        self.store.record_document_version(maintenance,'<p>Maintenance details</p>',now=(NOW+timedelta(hours=2)).isoformat())
        self.assertEqual(self.tick(NOW+timedelta(hours=2)),0)

    def test_normalizer_upgrade_does_not_notify_but_later_content_does(self):
        self.article(text='A');self.tick()
        original=self.store.get_document_versions('china_news_api','1')[0]
        document=SourceDocument(original.source_id,original.document_id,original.title,original.url,original.published_at,None,original.category,None,None)
        self.store.record_document_version(document,'<p>A</p>',now=(NOW+timedelta(seconds=20)).isoformat(),normalizer_version='article-v2')
        self.assertEqual(self.tick(NOW+timedelta(seconds=21)),0)
        self.store.record_document_version(document,'<p>B</p>',now=(NOW+timedelta(seconds=22)).isoformat(),normalizer_version='article-v2')
        self.assertEqual(self.tick(NOW+timedelta(seconds=23)),1)

    def test_daily_digest_is_one_window_and_late_articles_wait_next_day(self):
        self.store.set_announcement_policy(self.target.target_id,replace(self.policy,mode='digest'),now=NOW.isoformat())
        self.article();self.assertEqual(self.tick(),1)
        self.article('later',at=NOW+timedelta(minutes=15),published=(NOW+timedelta(minutes=15)).isoformat())
        self.assertEqual(self.tick(NOW+timedelta(minutes=16)),0)
        self.assertEqual(self.tick(NOW+timedelta(days=1)),1)
        self.assertIn('/later',self.messaging.sent[-1][1])

    def test_incomplete_observation_is_recoverable_and_known_maintenance_is_separate(self):
        from ff14_core.source_models import ChinaNewsDetail
        from ff14_core.china_events import parse_event_candidates
        from ff14_core.china_classification import classify_event_candidate
        document=SourceDocument('china_news_api','pending','Fixture article','https://example.invalid/pending',NOW.isoformat(),None,'5310',None,None)
        html='<p>活动时间：2026年11月4日20:00至2026年11月5日20:00</p>'
        self.store.record_document_version(document,html,now=NOW.isoformat(),ready=False)
        self.assertEqual(self.tick(),0)
        self.assertEqual(self.store.get_documents_needing_detail_review('china_news_api'),()) # Direct fixture has no list metadata.
        self.store.finish_article_observation('china_news_api','pending')
        self.assertEqual(self.tick(),1)
        # The title alone is insufficient for routing; stable candidate kind owns maintenance.
        maintenance=replace(document,document_id='maintenance',title='全区例行更新')
        html='维护时间：2026年11月5日20:00至2026年11月5日22:00'
        self.store.record_document_version(maintenance,html,now=NOW.isoformat())
        candidates=tuple(classify_event_candidate(x) for x in parse_event_candidates(source_id=maintenance.source_id,document_id=maintenance.document_id,title=maintenance.title,content_text=html))
        self.store.record_china_detail_success(ChinaNewsDetail(maintenance.source_id,maintenance.document_id,maintenance.title,maintenance.published_at,html,html),candidates,'fixture')
        self.assertEqual(self.tick(),0)
