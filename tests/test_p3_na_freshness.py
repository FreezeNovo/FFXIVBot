import asyncio
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ff14_core.source_models import SourceDocument,ActivityDigestPolicy,MaintenancePolicy
from ff14_core.sources import LodestoneAtomSource,HttpResponse,OfficialSourceService,LODESTONE_NEWS_URL
from ff14_core.lodestone import LodestoneDetailService
from ff14_core.na_events import parse_na_candidates,LodestoneBodyParser
from ff14_core.calendar import CalendarQueryService,ActivityDigestWorkflow
from ff14_core.maintenance import MaintenanceWorkflow,MaintenanceQueryService
from ff14_core.freshness import evaluate_freshness
from ff14_core.runtime_settings import RuntimeSettings
from ff14_core.storage import StateStore
from test_sources import FakeTransport
from test_calendar import FakeMessaging

FIXTURES=Path(__file__).parent/'fixtures'/'p3'
NOW=datetime(2026,11,4,12,tzinfo=timezone.utc)


def doc(id='1',category='Topics'):
    return SourceDocument('lodestone_topics',id,'Official English Title','https://na.finalfantasyxiv.com/lodestone/topics/detail/'+id,'2026-11-01T00:00:00Z',None,category,None,None)


def body(start='November 4, 2026 1:00 a.m.',end='November 12, 2026 2:00 a.m.',zone='PST'):
    return f'<h4>Event Period</h4><p>{start} to {end} ({zone})</p>'


class NAFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=StateStore(Path(self.directory.name)/'test.sqlite3');self.store.initialize()
        self.target=self.store.save_onebot_group('fixture-group',enabled=True)
        self.store.set_activity_digest_policy(self.target.target_id,ActivityDigestPolicy(enabled=True,regions=('global-na',)))
        self.document=doc()
        self.transport=FakeTransport({self.document.url:HttpResponse(200,('<h1>Official English Title</h1><div class="news__detail__wrapper">'+body()+'</div>').encode())})
        self.service=LodestoneDetailService(self.store,self.transport)
        self.store.record_source_success(source_id='lodestone_topics',display_name='NA Topics',http_status=200,raw_payload=b'fixture',documents=(self.document,))

    def refresh(self,at=NOW):
        with patch('ff14_core.storage.utc_now_text',return_value=at.isoformat()):self.service.refresh_document(self.document)

    def test_real_maintenance_body_crossmonth_and_feed_topics(self):
        feed=LodestoneAtomSource().fetch(FakeTransport({LODESTONE_NEWS_URL:HttpResponse(200,(FIXTURES/'na-news.xml').read_bytes())}))
        maintenance=feed.documents[0]
        candidates=parse_na_candidates(maintenance,maintenance.content_html)
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0].candidate.start_at,'2026-11-02T07:00:00+00:00')
        self.assertEqual(candidates[0].candidate.end_at,'2026-11-02T08:00:00+00:00')
        parser=LodestoneBodyParser();parser.feed((FIXTURES/'na-detail.html').read_text())
        self.assertIn('Date & Time',parser.body);self.assertNotIn('news__footer',parser.body)
        self.assertNotIn('[Maintenance]',parser.title)

    def test_synthetic_parser_boundaries_no_time_timezone_date_only_and_dst(self):
        # Synthetic variants, not represented as live source evidence.
        for html in ('<p>No event time</p>',body(zone='PT'),body().replace('(PST)',''),'<h4>Event Period</h4><p>November 4, 2026 to November 5, 2026</p>',body().replace('1:00 a.m.','around 1:00 a.m.')):
            self.assertFalse(parse_na_candidates(self.document,html),html)
        multi=body('December 31, 2026 11:00 p.m.','January 1, 2027 1:00 a.m.')+body('November 1, 2026 1:00 a.m.','November 1, 2026 1:30 a.m.','PDT')+body('November 1, 2026 1:00 a.m.','November 1, 2026 1:30 a.m.','PST')
        result=parse_na_candidates(self.document,multi)
        self.assertEqual(len(result),3)
        self.assertEqual(result[0].candidate.end_at,'2027-01-01T09:00:00+00:00')
        self.assertEqual(datetime.fromisoformat(result[2].candidate.start_at)-datetime.fromisoformat(result[1].candidate.start_at),timedelta(hours=1))

    def test_unchanged_success_zero_parse_failure_and_recovery(self):
        self.refresh();self.refresh(NOW+timedelta(hours=1))
        record=self.store.get_event_verification('lodestone_topics','1')
        self.assertEqual(record['last_success_at'],(NOW+timedelta(hours=1)).isoformat())
        self.transport.responses[self.document.url]=HttpResponse(200,b'<div class="news__detail__wrapper">No times</div>')
        self.refresh(NOW+timedelta(hours=2))
        record=self.store.get_event_verification('lodestone_topics','1')
        self.assertEqual(record['last_success_at'],(NOW+timedelta(hours=1)).isoformat());self.assertEqual(record['error_kind'],'parse_zero')
        self.assertEqual(len(self.store.get_event_candidates('lodestone_topics')),1)
        query=CalendarQueryService(self.store).query(self.target,now=NOW+timedelta(hours=2))
        self.assertEqual(query.ongoing[0].freshness_state,'warning')
        self.assertIn('保留最后有效',query.ongoing[0].verification_warning)
        # 304 only validates the content when that exact known version parses successfully.
        self.transport.responses[self.document.url]=HttpResponse(304,b'')
        with self.assertRaisesRegex(ValueError,"304缺少已发送"):
            self.refresh(NOW+timedelta(hours=3))
        self.assertEqual(self.store.get_event_verification('lodestone_topics','1')['last_success_at'],record['last_success_at'])

    def test_48_72_boundaries_manual_retains_and_once_pause_recovers(self):
        self.refresh()
        query=CalendarQueryService(self.store)
        for age,state in [(48-1/3600,'valid'),(48,'warning'),(72-1/3600,'warning'),(72,'stale')]:
            self.assertEqual(query.query(self.target,now=NOW+timedelta(hours=age)).ongoing[0].freshness_state,state)
        manual=query.query(self.target,now=NOW+timedelta(hours=72));self.assertTrue(manual.ongoing)
        automatic=query.query(self.target,now=NOW+timedelta(hours=72),automatic=True);self.assertTrue(automatic.unavailable);self.assertEqual(automatic.excluded_count,1)
        messaging=FakeMessaging();flow=ActivityDigestWorkflow(self.store,query)
        at=NOW+timedelta(hours=72)
        asyncio.run(flow.tick(messaging,now=at));self.assertIn('暂停',messaging.sent[0][1])
        asyncio.run(ActivityDigestWorkflow(self.store,query).tick(messaging,now=at+timedelta(days=1)));self.assertEqual(len(messaging.sent),1)
        self.refresh(at+timedelta(days=1,minutes=1))
        asyncio.run(flow.tick(messaging,now=at+timedelta(days=2)));self.assertEqual(len(messaging.sent),2);self.assertIn('Official English Title',messaging.sent[1][1])

    def test_normal_empty_does_not_send_but_manual_explains(self):
        with self.store._connection() as c:
            # Both feed discoveries succeeded; no event intervals exist in this fixture.
            c.execute("INSERT INTO source_state(source_id,display_name,last_attempt_at,last_success_at) VALUES('lodestone_atom','NA',?,?)",(NOW.isoformat(),NOW.isoformat()))
        query=CalendarQueryService(self.store);flow=ActivityDigestWorkflow(self.store,query)
        messaging=FakeMessaging();asyncio.run(flow.tick(messaging,now=NOW))
        self.assertEqual(messaging.sent,[]);self.assertIn('没有符合本群策略',flow.preview(self.target,now=NOW))

    def test_date_only_is_auditable_without_precise_candidate(self):
        from ff14_core.na_events import date_only_evidence
        html='<h4>Event Period</h4><p>December 31, 2026 to January 2, 2027 (PST)</p>'
        self.assertFalse(parse_na_candidates(self.document,html))
        records=date_only_evidence(self.document,html)
        self.assertEqual(records[0]['precision'],'day');self.assertFalse(records[0]['reminder_eligible'])
        self.assertEqual(records[0]['end_date'],'2027-01-02')
        self.transport.responses[self.document.url]=HttpResponse(200,('<div class="news__detail__wrapper">'+html+'</div>').encode())
        self.refresh()
        version=self.store.get_document_versions('lodestone_topics','1')[0]
        self.assertEqual(self.store.get_time_evidence(version.version_id)[0]['precision'],'day')

    def test_display_is_beijing_even_with_other_host_timezone(self):
        from zoneinfo import ZoneInfo
        self.refresh()
        foreign_zone = ZoneInfo('Pacific/Honolulu')

        class ForeignHostDatetime(datetime):
            # Model a foreign host's implicit timezone conversion on every OS.
            # A renderer that accidentally uses astimezone() must show Honolulu
            # and fail the Beijing expectation, without changing the OS timezone.
            def astimezone(self, tz=None):
                return super().astimezone(foreign_zone if tz is None else tz)

        with patch('ff14_core.calendar.datetime', ForeignHostDatetime):
            text=ActivityDigestWorkflow(self.store,CalendarQueryService(self.store)).preview(
                self.target,now=NOW.astimezone(foreign_zone))
        self.assertIn('北京时间',text)
        self.assertIn('2026/11/12 18:00',text)

    def test_detail_304_requires_corresponding_validator_and_refreshes_only_its_event(self):
        class ConditionalTransport:
            headers=None
            def get(_self,url):return HttpResponse(200,('<div class="news__detail__wrapper">'+body()+'</div>').encode(),{'ETag':'fixture-etag'})
            def get_with_headers(_self,url,headers):
                _self.headers=headers
                return HttpResponse(304,b'')
        transport=ConditionalTransport();self.service=LodestoneDetailService(self.store,transport)
        self.refresh()
        self.refresh(NOW+timedelta(hours=1))
        self.assertEqual(transport.headers,{'If-None-Match':'fixture-etag'})
        self.assertEqual(self.store.get_event_verification('lodestone_topics','1')['last_success_at'],(NOW+timedelta(hours=1)).isoformat())
        self.assertIsNone(self.store.get_event_verification('lodestone_topics','other'))

    def test_unstarted_delayed_digest_rechecks_age_before_claim(self):
        self.refresh(NOW-timedelta(hours=71))
        flow=ActivityDigestWorkflow(self.store,CalendarQueryService(self.store));flow.plan_due(now=NOW)
        pending=self.store.list_pending_notifications(stream='activity_digest');self.assertTrue(pending)
        self.assertIsNone(self.store.claim_notification(pending[0][0],now=NOW+timedelta(hours=1)))
        self.assertEqual(self.store.list_deliveries()[0].status,'superseded')
        messaging=FakeMessaging();asyncio.run(flow.tick(messaging,now=NOW+timedelta(hours=1)))
        self.assertEqual(len(messaging.sent),1);self.assertIn('暂停',messaging.sent[0][1])

    def test_unchanged_reparse_preserves_first_observation_time(self):
        self.refresh()
        before=self.store.get_event_candidates('lodestone_topics')[0].candidate.created_at
        self.refresh(NOW+timedelta(hours=1))
        after=self.store.get_event_candidates('lodestone_topics')[0].candidate.created_at
        self.assertEqual(after,before)
        self.assertEqual(self.store.get_event_verification('lodestone_topics','1')['last_success_at'],(NOW+timedelta(hours=1)).isoformat())
