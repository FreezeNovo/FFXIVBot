"""线上最小样本的离线契约；不在回归中抓取网络。"""
import json
from pathlib import Path
import unittest
from xml.etree import ElementTree
from ff14_core.sources import ChinaNewsApiSource, ChinaNewsDetailApi, LodestoneAtomSource, HttpResponse, china_news_list_url, LODESTONE_NEWS_URL
from test_sources import FakeTransport

FIXTURES=Path(__file__).parent/'fixtures'/'p3'


class P3ContractTests(unittest.TestCase):
    def test_cn_pinned_pages_and_body_contract(self):
        adapter=ChinaNewsApiSource(page_count=2,page_size=3)
        result=adapter.fetch(FakeTransport({china_news_list_url(i,3):HttpResponse(200,(FIXTURES/name).read_bytes()) for i,name in enumerate(('cn-list.json','cn-page1.json'))}))
        self.assertEqual(len(result.documents),10)
        self.assertEqual(result.documents[0].document_id,'394997')
        detail=ChinaNewsDetailApi().fetch(FakeTransport({'https://cqnews.web.sdo.com/api/news/newsDetail?gameCode=ff&id=395032':HttpResponse(200,(FIXTURES/'cn-detail.json').read_bytes())}),'395032')
        self.assertIn('2027',detail.content_text)

    def test_two_distinct_atom_feeds_have_body_and_stable_ids(self):
        ids=[]
        for name in ('na-news.xml','na-topics.xml'):
            data=(FIXTURES/name).read_bytes()
            result=LodestoneAtomSource().fetch(FakeTransport({LODESTONE_NEWS_URL:HttpResponse(200,data)}))
            ids.append({d.document_id for d in result.documents})
            root=ElementTree.fromstring(data)
            for entry in root.findall('{http://www.w3.org/2005/Atom}entry'):
                self.assertTrue(entry.findtext('{http://www.w3.org/2005/Atom}content'))
                self.assertTrue(entry.findtext('{http://www.w3.org/2005/Atom}published').endswith('Z'))
        self.assertFalse(ids[0]&ids[1])

    def test_partial_page_does_not_become_success(self):
        with self.assertRaises(TimeoutError):
            ChinaNewsApiSource(page_count=2,page_size=3).fetch(FakeTransport({china_news_list_url(0,3):HttpResponse(200,(FIXTURES/'cn-list.json').read_bytes()),china_news_list_url(1,3):TimeoutError('fixture timeout')}))
