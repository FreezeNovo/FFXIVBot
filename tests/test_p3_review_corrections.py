"""任务13：复审R1–R4的离线行为回归。"""
import asyncio
from collections import Counter
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import unittest

from ff14_app.reviews import ReviewCoordinator
from ff14_core.delivery import dispatch_pending
from ff14_core.na_events import LodestoneBodyParser
from ff14_core.source_models import SourceDocument
from ff14_core.sources import HttpResponse
from ff14_core.messaging import SendResult
import test_p3_na_freshness as na_fixture
import test_p3_news as news_fixture

NOW = news_fixture.NOW
SOURCES = ('china_news_api', 'lodestone_atom', 'lodestone_topics')


class ReviewFairnessTests(unittest.TestCase):
    def run_reviews(self, sizes):
        calls = []
        probes = []

        class Store:
            def get_documents_needing_detail_review(self, source, **kwargs):
                return tuple(SimpleNamespace(source_id=source, document_id=str(index))
                             for index in range(sizes[source]))

            def record_verification_attempt(self, *args, **kwargs):
                pass

            def record_source_probe_success(self, **kwargs):
                probes.append(kwargs)

            def record_source_failure(self, **kwargs):
                pass

        async def exercise():
            coordinator = ReviewCoordinator(SimpleNamespace(store=Store()))

            async def review(document):
                calls.append((document.source_id, document.document_id))
                return document.source_id != 'china_news_api'

            coordinator._review = review
            rounds = []
            for _ in range(3):
                offset = len(calls)
                await coordinator.background(now=NOW)
                rounds.append(calls[offset:])
            await coordinator.stop()
            return rounds

        return asyncio.run(exercise()), probes

    def test_failing_cn_backlog_does_not_starve_na_first_or_known_details(self):
        rounds, probes = self.run_reviews(dict(zip(SOURCES, (40, 2, 2))))
        for calls in rounds:
            self.assertLessEqual(len(calls), 40)
            self.assertEqual({identity for source, identity in calls if source == 'lodestone_atom'}, {'0', '1'})
            self.assertEqual({identity for source, identity in calls if source == 'lodestone_topics'}, {'0', '1'})
            self.assertTrue(any(source == 'china_news_api' for source, _ in calls))
        self.assertEqual({probe['source_id'] for probe in probes},
                         {'lodestone_atom_detail', 'lodestone_topics_detail'})

    def test_saturated_and_single_source_queues_keep_one_total_budget(self):
        for sizes in ((40, 40, 40), (0, 0, 40), (1, 0, 0)):
            with self.subTest(sizes=sizes):
                rounds, _ = self.run_reviews(dict(zip(SOURCES, sizes)))
                for calls in rounds:
                    self.assertEqual(len(calls), min(40, sum(sizes)))
                    counts = Counter(source for source, _ in calls)
                    if sizes == (40, 40, 40):
                        self.assertEqual(set(counts), set(SOURCES))
                        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)


class BodyVoidTagTests(unittest.TestCase):
    setUp = na_fixture.NAFreshnessTests.setUp
    refresh = na_fixture.NAFreshnessTests.refresh

    def test_equivalent_void_syntax_preserves_body_candidates_and_comparison(self):
        period = na_fixture.body()
        reference = None
        for image in ('<img src="fixture.png">', '<img src="fixture.png" />'):
            for br in ('<br>', '<br />', '<br></br>'):
                with self.subTest(image=image, br=br):
                    html = ('<div class="news__detail__wrapper">' + image + br
                            + '<section>' + period + '</section></div><p>Outside body</p>')
                    parser = LodestoneBodyParser()
                    parser.feed(html)
                    self.assertEqual(parser.depth, 0)
                    self.assertIn('Event Period', parser.body)
                    self.assertNotIn('Outside body', parser.body)
                    self.transport.responses[self.document.url] = HttpResponse(200, html.encode())
                    self.refresh()
                    version = self.store.get_document_versions(self.document.source_id, self.document.document_id)[0]
                    if reference is None:
                        reference = version.comparison_hash
                    self.assertEqual(version.comparison_hash, reference)
                    self.assertFalse(version.changed)
                    candidates = self.store.get_event_candidates(self.document.source_id)
                    self.assertEqual(len(candidates), 1)
                    self.assertEqual(candidates[0].candidate.start_at, '2026-11-04T09:00:00+00:00')


class AnnouncementReviewTests(unittest.TestCase):
    setUp = news_fixture.NewsWorkflowTests.setUp
    article = news_fixture.NewsWorkflowTests.article
    tick = news_fixture.NewsWorkflowTests.tick

    def items(self):
        with self.store._connection() as connection:
            return connection.execute('SELECT version_id,eligible_at,reason,status FROM news_item ORDER BY rowid').fetchall()

    def test_real_update_after_expired_first_publication_uses_observation_time(self):
        for expired_in_prior_wake in (True, False):
            with self.subTest(expired_in_prior_wake=expired_in_prior_wake):
                identity = str(expired_in_prior_wake)
                self.article(identity, text='A')
                self.flow.plan_due(now=NOW + timedelta(seconds=2))
                if expired_in_prior_wake:
                    self.flow.plan_due(now=NOW + timedelta(days=4))
                observed = NOW + timedelta(days=4, seconds=1)
                self.article(identity, text='B', at=observed)
                self.assertEqual(self.tick(observed + timedelta(seconds=1)), 1)
                self.assertIn('文章内容已变动', self.messaging.sent[-1][1])
                self.assertIn('/' + identity, self.messaging.sent[-1][1])
                active = self.items()[-1]
                self.assertEqual(active[1], observed.isoformat())
                self.assertEqual(self.tick(observed + timedelta(seconds=2)), 0)

    def test_update_merge_and_retry_do_not_extend_one_eligibility_window(self):
        self.article(text='A', published=(NOW - timedelta(days=5)).isoformat())
        self.flow.plan_due(now=NOW + timedelta(seconds=2))
        observed = NOW + timedelta(seconds=10)
        self.article(text='B', published=(NOW - timedelta(days=5)).isoformat(), at=observed)
        self.messaging=news_fixture.FakeMessaging({'fixture-group':SendResult(False,retryable=True,error='fixture offline')})
        self.assertEqual(self.tick(observed,online=False),1)
        self.article(text='C', published=(NOW - timedelta(days=5)).isoformat(), at=observed + timedelta(hours=1))
        self.flow.plan_due(now=observed + timedelta(hours=1))
        self.assertEqual(self.items()[-1][1], observed.isoformat())
        # The same latest content cannot regain eligibility through a retry or raw formatting change.
        self.flow.plan_due(now=observed + timedelta(hours=73))
        version = self.store.get_document_versions('china_news_api', '1')[0]
        self.style_version(version, observed + timedelta(hours=74))
        self.messaging=news_fixture.FakeMessaging()
        self.assertEqual(self.tick(observed + timedelta(hours=74)), 0)

    def freeze_batch(self):
        self.store.set_announcement_policy(self.target.target_id, replace(self.policy, mode='digest'), now=NOW.isoformat())
        for index in range(5):
            self.article(str(index), text='Value ' + str(index), title='Fixture announcement ' + 'title ' * 35)
        self.flow.plan_due(now=NOW + timedelta(seconds=2))
        pending = self.store.list_pending_notifications(stream='announcement')
        self.assertGreater(len(pending), 1)
        claim = self.store.claim_notification(pending[0][0], now=NOW + timedelta(seconds=3))
        self.store.finish_delivery_attempt(claim.attempt_id, SendResult(True, provider_message_id='first'))
        with self.store._connection() as connection:
            version_id = connection.execute('SELECT version_id FROM news_member WHERE notification_id=?', (pending[1][0],)).fetchone()[0]
        return self.store.get_document_version(version_id)

    def style_version(self, version, observed):
        document = SourceDocument(version.source_id, version.document_id, version.title, version.url,
                                  version.published_at, None, version.category, None, None)
        return self.store.record_document_version(document, '<div style="color:red">' + version.content_html + '</div>', now=observed.isoformat())

    def dispatch(self, now):
        return asyncio.run(dispatch_pending(self.store, self.messaging, stream='announcement', now=now))

    def test_frozen_page_format_change_does_not_duplicate_its_article(self):
        version = self.freeze_batch()
        self.style_version(version, NOW + timedelta(seconds=4))
        self.flow.plan_due(now=NOW + timedelta(seconds=5), recovery=True)
        self.dispatch(NOW + timedelta(seconds=5))
        self.assertEqual(sum(version.url in message for _, message in self.messaging.sent), 1)
        self.assertEqual(self.tick(NOW + timedelta(seconds=6)), 0)
        self.assertEqual(sum(version.url in message for _, message in self.messaging.sent), 1)

    def test_frozen_real_change_is_a_successor_after_original_confirmation(self):
        version = self.freeze_batch()
        observed = NOW + timedelta(seconds=4)
        self.article(version.document_id, text='Changed B', at=observed, title=version.title)
        self.flow.plan_due(now=observed, recovery=True)
        with self.store._connection() as connection:
            successor = connection.execute('SELECT eligible_at,reason,notification_id FROM news_item WHERE version_id=(SELECT MAX(version_id) FROM article_version WHERE document_id=?)', (version.document_id,)).fetchone()
        self.assertIsNotNone(successor)
        self.assertEqual(successor[0], observed.isoformat())
        self.assertIn('文章内容已变动', successor[1])
        self.assertIsNone(successor[2], 'A successor must wait while the frozen original page can still send')
        self.article(version.document_id,text='Changed C',at=observed+timedelta(microseconds=1),title=version.title)
        self.flow.plan_due(now=observed+timedelta(microseconds=1),recovery=True)
        self.assertEqual(self.items()[-1][1],observed.isoformat())
        self.dispatch(observed+timedelta(microseconds=1))
        self.assertEqual(sum(version.url in message for _, message in self.messaging.sent), 1)
        self.flow.plan_due(now=NOW + timedelta(seconds=5), recovery=True)
        self.dispatch(NOW + timedelta(seconds=5))
        matched = [message for _, message in self.messaging.sent if version.url in message]
        self.assertEqual(len(matched), 2)
        self.assertIn('Changed C', matched[-1])
        self.assertIn('正文：', matched[-1])
        self.assertEqual(self.tick(NOW + timedelta(seconds=6)), 0)


    def test_old_failed_content_does_not_suppress_a_genuine_return_after_success(self):
        self.article(text='A')
        self.messaging=news_fixture.FakeMessaging({'fixture-group':SendResult(False,error='fixture rejection')})
        self.assertEqual(self.tick(NOW+timedelta(seconds=2)),1)
        self.messaging=news_fixture.FakeMessaging()
        self.article(text='B',at=NOW+timedelta(seconds=10))
        self.assertEqual(self.tick(NOW+timedelta(seconds=11)),1)
        self.article(text='A',at=NOW+timedelta(seconds=20))
        self.assertEqual(self.tick(NOW+timedelta(seconds=21)),1)
        self.assertIn('B → A',self.messaging.sent[-1][1])


class ReviewFairnessIntegrationTests(unittest.TestCase):
    setUp = na_fixture.NAFreshnessTests.setUp
    refresh = na_fixture.NAFreshnessTests.refresh

    def test_repeated_cn_failures_allow_initial_and_old_na_body_verification(self):
        from unittest.mock import patch

        # One NA document has never had a body; another owns an old active snapshot.
        self.refresh(NOW-timedelta(days=4))
        first=SourceDocument('lodestone_atom','first','NA first body',
                             'https://na.finalfantasyxiv.com/lodestone/news/detail/first',
                             NOW.isoformat(),None,'Notices',None,None)
        self.store.record_source_success(source_id=first.source_id,display_name='NA News',http_status=200,
                                         raw_payload=b'fixture',documents=(first,))
        self.transport.responses[first.url]=HttpResponse(200,('<div class="news__detail__wrapper">'+na_fixture.body()+'</div>').encode())
        china=tuple(SourceDocument('china_news_api',str(index),'CN failing document',
                                   'https://example.invalid/cn/'+str(index),NOW.isoformat(),
                                   None,'5310',None,None) for index in range(40))
        self.store.record_source_success(source_id='china_news_api',display_name='CN',http_status=200,
                                         raw_payload=b'fixture',documents=china)
        calls=[]
        original_get=self.transport.get
        def get(url):
            calls.append(url)
            return original_get(url)
        self.transport.get=get

        class FailingChina:
            def refresh_document(_self, document):
                raise TimeoutError('fixture persistent failure')

        application=SimpleNamespace(store=self.store,china_detail_service=FailingChina(),
                                    lodestone_detail_service=self.service)
        async def exercise():
            coordinator=ReviewCoordinator(application)
            for _ in range(3):
                with patch('ff14_core.storage.utc_now_text',return_value=NOW.isoformat()):
                    await coordinator.background(now=NOW)
            await coordinator.stop()
        asyncio.run(exercise())
        self.assertIn(first.url,calls)
        self.assertIn(self.document.url,calls)
        self.assertEqual(self.store.get_event_verification(first.source_id,first.document_id)['last_success_at'],NOW.isoformat())
        self.assertEqual(self.store.get_event_verification(self.document.source_id,self.document.document_id)['last_success_at'],NOW.isoformat())
        self.assertEqual({item.candidate.document_id for item in self.store.get_event_candidates('lodestone_atom')},{'first'})
