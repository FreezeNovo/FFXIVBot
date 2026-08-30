from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ff14_core.source_models import (
    ChinaNewsDetail,
    ClassifiedEventCandidate,
    EventCandidate,
    SourceDocument,
)
from ff14_core.sources import (
    ChinaNewsApiSource,
    ChinaDetailFailure,
    ChinaDetailRefreshResult,
    ChinaNewsDetailApi,
    ChinaNewsDetailService,
    HttpResponse,
    LodestoneAtomSource,
    OfficialSourceService,
    SourceFetchResult,
    china_news_detail_page_url,
    china_news_list_url,
)
from ff14_core.storage import StateStore


class FakeTransport:
    def __init__(self, responses: dict[str, HttpResponse | Exception]) -> None:
        self.responses = responses

    def get(self, url: str) -> HttpResponse:
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class SourceTests(unittest.TestCase):
    def test_saved_official_source_fixtures_match_contracts(self) -> None:
        fixture_root = Path(__file__).parent / "fixtures"
        list_payload = (fixture_root / "china_news_list.json").read_bytes()
        detail_payload = (fixture_root / "china_news_detail_392085.json").read_bytes()
        atom_payload = (fixture_root / "lodestone_news.xml").read_bytes()

        china_list = ChinaNewsApiSource(page_count=1).fetch(
            FakeTransport({china_news_list_url(0): HttpResponse(200, list_payload)})
        )
        self.assertEqual(china_list.documents[0].document_id, "392085")

        detail = ChinaNewsDetailApi().fetch(
            FakeTransport({
                "https://cqnews.web.sdo.com/api/news/newsDetail?gameCode=ff&id=392085": HttpResponse(
                    200, detail_payload
                )
            }),
            "392085",
        )
        self.assertIn("联动开启时间", detail.content_text)

        lodestone = LodestoneAtomSource().fetch(
            FakeTransport({"https://na.finalfantasyxiv.com/lodestone/news/news.xml": HttpResponse(200, atom_payload)})
        )
        self.assertEqual(lodestone.documents[0].document_id, "tag:example.invalid,2026:/fixture")

    def test_conditional_304_keeps_cached_documents(self) -> None:
        document = SourceDocument(
            source_id="fixture_source",
            document_id="1",
            title="Fixture",
            url="https://example.invalid/fixture",
            published_at="2026/08/20 00:00:00",
            updated_at=None,
            category=None,
            summary=None,
            image_url=None,
        )

        class FixtureSource:
            source_id = "fixture_source"
            display_name = "Fixture"

            def fetch(self, transport):
                response = transport.get("fixture://source")
                if response.status_code == 304:
                    return SourceFetchResult(
                        source_id=self.source_id,
                        display_name=self.display_name,
                        status_code=304,
                        raw_payload=b"",
                        documents=(),
                        not_modified=True,
                        etag=response.headers.get("etag"),
                    )
                return SourceFetchResult(
                    source_id=self.source_id,
                    display_name=self.display_name,
                    status_code=200,
                    raw_payload=response.body,
                    documents=(document,),
                    etag=response.headers.get("etag"),
                )

        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            first = OfficialSourceService(
                store,
                FakeTransport({
                    "fixture://source": HttpResponse(200, b"first", {"etag": "v1"})
                }),
                (FixtureSource(),),
            )
            second = OfficialSourceService(
                store,
                FakeTransport({
                    "fixture://source": HttpResponse(304, b"", {"etag": "v1"})
                }),
                (FixtureSource(),),
            )
            self.assertTrue(first.refresh()[0].success)
            unchanged = second.refresh()[0]
            self.assertTrue(unchanged.not_modified)
            self.assertEqual(unchanged.document_count, 1)
            self.assertEqual(store.get_source_documents("fixture_source", 10)[0].document_id, "1")
            self.assertEqual(store.get_source_health()[0].last_http_status, 304)

    def test_china_news_schema_drift_is_rejected(self) -> None:
        response = HttpResponse(
            status_code=200,
            body=b'{"Code":"0","Data":[{"Title":"missing id"}]}',
        )
        with self.assertRaisesRegex(ValueError, "缺少 Id"):
            ChinaNewsApiSource(page_count=1).fetch(
                FakeTransport({china_news_list_url(0): response})
            )

    def test_lodestone_schema_drift_is_rejected(self) -> None:
        response = HttpResponse(
            status_code=200,
            body=b'<html><body>not an atom feed</body></html>',
        )
        with self.assertRaisesRegex(ValueError, "Atom feed"):
            LodestoneAtomSource().fetch(
                FakeTransport({"https://na.finalfantasyxiv.com/lodestone/news/news.xml": response})
            )

    def test_sqlite_backup_and_wal_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "state.sqlite3"
            backup = Path(temporary_directory) / "backup.sqlite3"
            store = StateStore(database)
            store.initialize()
            store.set_owner_user_id("owner")
            store.backup_to(backup)
            store.validate_integrity()
            self.assertTrue(backup.exists())
            self.assertEqual(StateStore(backup).get_meta("owner_user_id"), "owner")
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            connection.close()

    def test_zero_candidate_parse_preserves_last_known_candidate(self) -> None:
        document = SourceDocument(
            source_id="china_news_api",
            document_id="stable",
            title="Stable event",
            url="https://example.invalid/stable",
            published_at=None,
            updated_at=None,
            category=None,
            summary=None,
            image_url=None,
        )
        candidate = EventCandidate(
            source_id="china_news_api",
            document_id="stable",
            title="Stable event",
            candidate_id="candidate",
            kind="activity",
            label="活动时间",
            evidence_text="活动时间：2026年8月1日00:00~2026年8月2日00:00",
            start_at="2026-08-01T00:00:00+08:00",
            end_at="2026-08-02T00:00:00+08:00",
            timezone="Asia/Shanghai",
            confidence="high",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            store.record_source_success(
                source_id="china_news_api",
                display_name="fixture",
                http_status=200,
                raw_payload=b"fixture",
                documents=(document,),
            )
            store.record_china_detail_success(
                ChinaNewsDetail(
                    source_id="china_news_api",
                    document_id="stable",
                    title="Stable event",
                    published_at=None,
                    content_html="<p>old</p>",
                    content_text="old",
                ),
                (
                    ClassifiedEventCandidate(
                        candidate=candidate,
                        classification="seasonal_event",
                        classification_label="季节活动",
                        routing="calendar_candidate",
                        routing_label="日历候选",
                        reason="fixture",
                    ),
                ),
                "parser-v1",
            )
            store.record_china_detail_success(
                ChinaNewsDetail(
                    source_id="china_news_api",
                    document_id="stable",
                    title="Stable event",
                    published_at=None,
                    content_html="<p>changed layout</p>",
                    content_text="changed layout",
                ),
                (),
                "parser-v2",
            )
            self.assertEqual(store.get_event_candidates("china_news_api")[0].candidate.candidate_id, "candidate")
            connection = sqlite3.connect(store._database_path)
            status = connection.execute(
                "SELECT parse_status, candidate_count, parser_version FROM source_document_detail "
                "WHERE source_id = 'china_news_api' AND document_id = 'stable'"
            ).fetchone()
            connection.close()
            self.assertEqual(status, ("empty", 0, "parser-v2"))

    def test_activity_time_update_replaces_old_candidate(self) -> None:
        document = SourceDocument(
            source_id="china_news_api",
            document_id="updated",
            title="活动时间调整公告",
            url="https://example.invalid/updated",
            published_at=None,
            updated_at=None,
            category=None,
            summary=None,
            image_url=None,
        )
        old_candidate = EventCandidate(
            source_id="china_news_api",
            document_id="updated",
            title=document.title,
            candidate_id="old-time",
            kind="activity",
            label="活动时间",
            evidence_text="活动时间：2026年8月1日00:00~2026年8月2日00:00",
            start_at="2026-08-01T00:00:00+08:00",
            end_at="2026-08-02T00:00:00+08:00",
            timezone="Asia/Shanghai",
            confidence="high",
        )
        new_candidate = replace(
            old_candidate,
            candidate_id="new-time",
            evidence_text="活动时间：2026年8月3日00:00~2026年8月4日00:00",
            start_at="2026-08-03T00:00:00+08:00",
            end_at="2026-08-04T00:00:00+08:00",
        )
        classified = lambda candidate: (
            ClassifiedEventCandidate(
                candidate=candidate,
                classification="seasonal_event",
                classification_label="季节活动",
                routing="calendar_candidate",
                routing_label="日历候选",
                reason="fixture",
            )
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            store.record_source_success(
                source_id="china_news_api",
                display_name="fixture",
                http_status=200,
                raw_payload=b"fixture",
                documents=(document,),
            )
            for candidate, html in ((old_candidate, "<p>old time</p>"), (new_candidate, "<p>new time</p>")):
                store.record_china_detail_success(
                    ChinaNewsDetail(
                        source_id="china_news_api",
                        document_id="updated",
                        title=document.title,
                        published_at=None,
                        content_html=html,
                        content_text=html,
                    ),
                    (classified(candidate),),
                    "parser-v1",
                )
            candidates = store.get_event_candidates("china_news_api")
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].candidate.candidate_id, "new-time")
            self.assertEqual(candidates[0].candidate.start_at, "2026-08-03T00:00:00+08:00")

    def test_empty_detail_content_is_rejected(self) -> None:
        response = HttpResponse(
            status_code=200,
            body=b'{"Code":"0","Data":{"Id":392112,"Title":"Fixture","Content":""}}',
        )
        with self.assertRaisesRegex(ValueError, "缺少 Content"):
            ChinaNewsDetailApi().fetch(
                FakeTransport({
                    "https://cqnews.web.sdo.com/api/news/newsDetail?gameCode=ff&id=392112": response
                }),
                "392112",
            )

    def test_first_detail_failure_is_persisted(self) -> None:
        document = SourceDocument(
            source_id="china_news_api",
            document_id="392112",
            title="Fixture",
            url="https://example.invalid/392112",
            published_at=None,
            updated_at=None,
            category=None,
            summary=None,
            image_url=None,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            store.record_source_success(
                source_id="china_news_api",
                display_name="fixture",
                http_status=200,
                raw_payload=b"fixture",
                documents=(document,),
            )
            store.record_detail_parse_failure(
                source_id="china_news_api",
                document_id="392112",
                error="ValueError: 国服新闻详情缺少 Content",
            )
            connection = sqlite3.connect(store._database_path)
            row = connection.execute(
                "SELECT parse_status, parser_error FROM source_document_detail "
                "WHERE source_id = ? AND document_id = ?",
                ("china_news_api", "392112"),
            ).fetchone()
            connection.close()
            self.assertEqual(row, ("degraded", "ValueError: 国服新闻详情缺少 Content"))
            self.assertEqual(
                store.get_documents_needing_detail_review("china_news_api")[0].document_id,
                "392112",
            )

    def test_parser_version_change_reopens_unchanged_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            store.record_source_success(
                source_id="china_news_api",
                display_name="fixture",
                http_status=200,
                raw_payload=b"fixture",
                documents=(
                    SourceDocument(
                        source_id="china_news_api",
                        document_id="392085",
                        title="Fixture",
                        url="https://example.invalid/392085",
                        published_at=None,
                        updated_at=None,
                        category=None,
                        summary=None,
                        image_url=None,
                    ),
                ),
            )
            store.record_china_detail_success(
                ChinaNewsDetail(
                    source_id="china_news_api",
                    document_id="392085",
                    title="Fixture",
                    published_at=None,
                    content_html="<p>fixture</p>",
                    content_text="fixture",
                ),
                (),
                "old-parser",
            )
            self.assertEqual(
                store.get_documents_needing_detail_review(
                    "china_news_api", parser_version="new-parser"
                )[0].document_id,
                "392085",
            )

    def test_detail_refresh_renders_failure_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            text = ChinaNewsDetailService(store).render_refresh(
                ChinaDetailRefreshResult(
                    attempted_count=1,
                    success_count=0,
                    candidate_count=0,
                    failure_count=1,
                    failures=(
                        ChinaDetailFailure(
                            document_id="392112",
                            title="Fixture",
                            error="ValueError: 国服新闻详情缺少 Content",
                        ),
                    ),
                )
            )
            self.assertIn("ID 392112", text)
            self.assertIn("缺少 Content", text)

    def test_china_news_api_parses_official_article_metadata(self) -> None:
        response = HttpResponse(
            status_code=200,
            body=(
                b'{"Code":"0","Data":[{"Id":391897,"CategoryCode":5310,'
                b'"Title":"\xe6\xb4\xbb\xe5\x8a\xa8\xe6\xb5\x8b\xe8\xaf\x95",'
                b'"Summary":"\xe6\x91\x98\xe8\xa6\x81",'
                b'"PublishDate":"2026/08/12 16:09:55",'
                b'"HomeImagePath":"https://example.invalid/image.jpg","OutLink":""}]}'
            ),
        )
        result = ChinaNewsApiSource(page_count=1).fetch(FakeTransport({
            china_news_list_url(0): response
        }))

        self.assertEqual(result.documents[0].document_id, "391897")
        self.assertEqual(result.documents[0].url, china_news_detail_page_url("391897"))
        self.assertEqual(result.documents[0].title, "活动测试")
        self.assertEqual(result.documents[0].published_at, "2026/08/12 16:09:55")

    def test_china_news_api_merges_pages_and_deduplicates_article_ids(self) -> None:
        page_zero = HttpResponse(
            status_code=200,
            body=(
                b'{"Code":"0","Data":[{"Id":1,"Title":"One"},'
                b'{"Id":2,"Title":"Two"}]}'
            ),
        )
        page_one = HttpResponse(
            status_code=200,
            body=(
                b'{"Code":"0","Data":[{"Id":2,"Title":"Duplicate"},'
                b'{"Id":3,"Title":"Three"}]}'
            ),
        )
        result = ChinaNewsApiSource(page_count=2, page_size=2).fetch(
            FakeTransport({
                china_news_list_url(0, 2): page_zero,
                china_news_list_url(1, 2): page_one,
            })
        )

        self.assertEqual(result.page_count, 2)
        self.assertEqual([item.document_id for item in result.documents], ["1", "2", "3"])
        self.assertEqual(result.documents[1].title, "Two")

    def test_lodestone_atom_parses_article_metadata(self) -> None:
        response = HttpResponse(
            status_code=200,
            body=b'''<?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom"><entry>
              <title>Seasonal Event</title>
              <link rel="alternate" href="https://example.invalid/article" />
              <link rel="enclosure" href="https://example.invalid/image.png" />
              <id>tag:example.invalid,2026:/article</id>
              <published>2026-08-14T09:10:00Z</published>
              <updated>2026-08-14T10:10:00Z</updated>
              <summary>Official summary</summary><category term="Topics" />
            </entry></feed>''',
        )
        result = LodestoneAtomSource().fetch(FakeTransport({
            "https://na.finalfantasyxiv.com/lodestone/news/news.xml": response
        }))

        self.assertEqual(result.documents[0].title, "Seasonal Event")
        self.assertEqual(result.documents[0].category, "Topics")
        self.assertEqual(result.documents[0].url, "https://example.invalid/article")

    def test_failed_refresh_keeps_last_successful_document(self) -> None:
        document = SourceDocument(
            source_id="fixture_source",
            document_id="1",
            title="Fixture",
            url="https://example.invalid/fixture",
            published_at=None,
            updated_at=None,
            category=None,
            summary=None,
            image_url=None,
        )

        class FixtureSource:
            source_id = "fixture_source"
            display_name = "Fixture"

            def fetch(self, transport):
                response = transport.get("fixture://source")
                return type("Fetched", (), {
                    "source_id": self.source_id,
                    "display_name": self.display_name,
                    "status_code": response.status_code,
                    "raw_payload": response.body,
                    "documents": (document,),
                })()

        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            source = FixtureSource()
            success_service = OfficialSourceService(
                store,
                FakeTransport({"fixture://source": HttpResponse(200, b"first")}),
                (source,),
            )
            failure_service = OfficialSourceService(
                store,
                FakeTransport({"fixture://source": RuntimeError("offline")}),
                (source,),
            )

            self.assertTrue(success_service.refresh()[0].success)
            self.assertFalse(failure_service.refresh()[0].success)
            health = store.get_source_health()[0]
            self.assertEqual(health.document_count, 1)
            self.assertEqual(health.consecutive_failures, 1)
            self.assertIsNotNone(health.last_success_at)


if __name__ == "__main__":
    unittest.main()
