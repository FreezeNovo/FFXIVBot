"""受控真实验收发现的回归；使用公开最小片段，完全离线。"""
from dataclasses import replace
from pathlib import Path
import unittest
from ff14_core.na_events import LodestoneBodyParser
from ff14_core.sources import HttpResponse
import test_p3_na_freshness as fixtures


class LiveFindingTests(unittest.TestCase):
    setUp=fixtures.NAFreshnessTests.setUp

    def test_topic_page_heading_is_not_part_of_official_article_title(self):
        header=(Path(__file__).parent/'fixtures/p3/na-topic-headings.html').read_text()
        expected='Presenting the Fan Festival 2026 in Tokyo Stage & Stream Schedule'
        body=fixtures.body()
        document=replace(self.document,title=expected)
        self.store.record_document_version(document,body)
        html=header+'<div class="news__detail__wrapper">'+body+'</div>'
        parser=LodestoneBodyParser();parser.feed(html)
        self.assertEqual(parser.title,expected)
        self.transport.responses[document.url]=HttpResponse(200,html.encode())
        self.service.refresh_document(document)
        version=self.store.get_document_versions(document.source_id,document.document_id)[0]
        self.assertEqual(version.title,expected)
        self.assertFalse(version.changed,'Page chrome must not manufacture a title change')
