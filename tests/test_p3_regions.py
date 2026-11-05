from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from ff14_core.storage import StateStore
from ff14_web.app import create_app,ActivityPolicyUpdate,MaintenancePolicyUpdate
from ff14_web.p3 import AnnouncementUpdate
from ff14_core.news import AnnouncementPolicy
from test_p3_na_freshness import NOW,doc,body
from ff14_core.na_events import parse_na_candidates
from ff14_core.source_models import ChinaNewsDetail
from ff14_core.calendar import CalendarQueryService,ActivityDigestRenderer,ActivityDigestWorkflow
from ff14_core.maintenance import MaintenanceWorkflow,MaintenanceQueryService


class RegionPolicyTests(unittest.TestCase):
    def test_ui_three_independent_sets_and_claim_removes_region(self):
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(Path(directory)/'test.sqlite3');store.initialize()
            target=store.save_onebot_group('12345',enabled=True)
            routes={(r.path,next(iter(r.methods))):r.endpoint for r in create_app(store).routes if hasattr(r,'methods')}
            a=routes[('/api/access-settings/groups/{group_id}/activity-policy','PUT')]
            m=routes[('/api/access-settings/groups/{group_id}/maintenance-policy','PUT')]
            n=routes[('/api/news/{group_id}','PUT')]
            self.assertEqual(a('12345',ActivityPolicyUpdate(enabled=True,regions=['cn','global-na']))['regions'],['cn','global-na'])
            m('12345',MaintenancePolicyUpdate(enabled=True,regions=['global-na']))
            n('12345',AnnouncementUpdate(enabled=True,regions=['cn']))
            self.assertEqual(store.get_maintenance_policy(target.target_id).selected_regions,('global-na',))
            self.assertEqual(store.get_announcement_policy(target.target_id).regions,('cn',))
            d=doc();store.record_source_success(source_id=d.source_id,display_name='NA',http_status=200,raw_payload=b'fixture',documents=(d,))
            candidates=parse_na_candidates(d,body())
            store.record_china_detail_success(ChinaNewsDetail(d.source_id,d.document_id,d.title,d.published_at,body(),'fixture'),candidates,'na-time-v1')
            query=CalendarQueryService(store);text=ActivityDigestRenderer().render(query.query(target,now=NOW))
            self.assertIn('【区域 NA】',text);self.assertIn('NA-Official English Title',text)
            flow=ActivityDigestWorkflow(store,query);flow.plan_due(now=NOW)
            pending=store.list_pending_notifications(stream='activity_digest')
            self.assertTrue(pending)
            a('12345',ActivityPolicyUpdate(enabled=True,regions=['cn']))
            self.assertIsNone(store.claim_notification(pending[0][0],now=NOW))
            self.assertFalse(query.query(target,now=NOW).ongoing)
            a('12345',ActivityPolicyUpdate(enabled=True,regions=[]))
            self.assertFalse(store.get_activity_digest_policy(target.target_id).selected_regions)
            with self.assertRaises(Exception):a('12345',ActivityPolicyUpdate(enabled=True,regions=['global-jp']))

    def test_provider_same_document_id_has_distinct_maintenance_observation(self):
        from ff14_core.source_models import SourceDocument,MaintenancePolicy,ClassifiedEventCandidate
        from unittest.mock import patch
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(Path(directory)/'test.sqlite3');store.initialize()
            target=store.save_onebot_group('fixture',enabled=True)
            with patch('ff14_core.storage.utc_now_text',return_value=(NOW-timedelta(minutes=1)).isoformat()):
                store.set_maintenance_policy(target.target_id,MaintenancePolicy(enabled=True,regions=('cn','global-na')))
            for source,region,category,title in [('china_news_api','cn','5310','国服维护'),('lodestone_atom','global-na','Maintenance','NA Maintenance')]:
                document=SourceDocument(source,'same-id',title,'https://example.invalid/'+source,NOW.isoformat(),None,category,None,None)
                store.record_source_success(source_id=source,display_name=source,http_status=200,raw_payload=b'fixture',documents=(document,))
                base=parse_na_candidates(doc(category='Maintenance'),body(start='November 5, 2026 1:00 a.m.'))[0]
                candidate=replace(base.candidate,source_id=source,region=region,document_id='same-id',title=title)
                with patch('ff14_core.storage.utc_now_text',return_value=NOW.isoformat()):
                    store.record_china_detail_success(ChinaNewsDetail(source,'same-id',title,NOW.isoformat(),body(),'fixture'),(replace(base,candidate=candidate),),'fixture-parser')
            flow=MaintenanceWorkflow(store,MaintenanceQueryService(store));self.assertEqual(flow.plan_due(now=NOW),2)
            with store._connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM maintenance_observation').fetchone()[0],2)
            previews=flow.preview(target,now=NOW);self.assertIn('C-国服维护',previews);self.assertIn('NA-NA Maintenance',previews)
