from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from ff14_core.documents import normalize_document
from ff14_core.source_models import SourceDocument
from ff14_core.storage import StateStore
from ff14_web.app import create_app


class DocumentVersionTests(unittest.TestCase):
    def test_versions_format_title_images_links_return_and_normalizer_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(Path(directory)/'test.sqlite3');store.initialize()
            document=SourceDocument('fixture','42','Title','https://example.invalid/a',None,None,'Notices',None,None)
            a=store.record_document_version(document,'<p>Hello <b>world</b> 5!</p><img src="/a.png"><a href="/x?p=1">go</a>')
            repeated=store.record_document_version(document,'<p>Hello <b>world</b> 5!</p><img src="/a.png"><a href="/x?p=1">go</a>')
            self.assertEqual(a,repeated)
            b=store.record_document_version(document,'<p class="x"> Hello <em>world</em>  5! </p><img src="/a.png"><a href="/x?p=1">go</a>')
            self.assertFalse(store.get_document_version(b).changed)
            c=store.record_document_version(replace(document,title='New title'),'<p>Hello world 6!</p><img src="/b.png"><a href="/x?p=2">go</a>')
            self.assertTrue(store.get_document_version(c).changed)
            d=store.record_document_version(document,'<p>Hello <b>world</b> 5!</p><img src="/a.png"><a href="/x?p=1">go</a>')
            self.assertGreater(d,c);self.assertTrue(store.get_document_version(d).changed)
            upgrade=store.record_document_version(document,'<p>Hello <b>world</b> 5!</p><img src="/a.png"><a href="/x?p=1">go</a>',normalizer_version='article-v2')
            self.assertFalse(store.get_document_version(upgrade).changed)
            store.record_document_version(document,'<script>alert(1)</script><p>valid</p>')
            page=next(r.endpoint for r in create_app(store).routes if r.path=='/articles')()
            self.assertIn('&lt;script&gt;',page.body.decode());self.assertNotIn('<script>alert(1)',page.body.decode())
            self.assertEqual(normalize_document('t','<p>Hello</p><p>world</p>','https://example.invalid')['text'],'Hello world')
