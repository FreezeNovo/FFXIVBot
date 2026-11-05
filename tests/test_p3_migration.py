from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from ff14_core.storage import StateStore,SCHEMA_VERSION
from ff14_app.migration_preview import preview_migration

FIXTURE=Path(__file__).parent/'fixtures'/'p3_schema_v16.sql'


class P3MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'original.sqlite3'
        with closing(sqlite3.connect(self.path)) as c:c.executescript(FIXTURE.read_text())

    def test_schema16_preview_preserves_state_and_restore_then_repeat(self):
        checksum=sha256(self.path.read_bytes()).hexdigest()
        preview=Path(self.directory.name)/'preview.sqlite3'
        report=preview_migration(self.path,preview)
        self.assertEqual(report,{'previous_schema':'16','preview_schema':SCHEMA_VERSION,'sent':1,'uncertain':1})
        self.assertEqual(sha256(self.path.read_bytes()).hexdigest(),checksum)
        store=StateStore(preview);store.initialize();store.validate_integrity()
        self.assertEqual(store.get_owner_user_id(),'fixture-owner')
        activity=store.get_activity_digest_policy('fixture-target')
        self.assertEqual(activity.selected_regions,('cn',));self.assertTrue(activity.enabled)
        self.assertEqual((activity.digest_minute,activity.upcoming_window_minutes,activity.ending_window_minutes),(3,60,30))
        self.assertEqual(store.get_manual_query_policy('fixture-target').activity_enabled,True)
        self.assertFalse(store.get_announcement_policy('fixture-target').enabled)
        self.assertIsNone(store.get_event_verification('china_news_api','fixture-article'))
        self.assertTrue(store.digest_window_started('fixture-target','2026-11-04'))
        self.assertEqual(len(store.get_source_documents('china_news_api',10)),1)
        candidates=store.get_event_candidates('china_news_api')
        self.assertEqual(len(candidates),1);self.assertEqual(candidates[0].candidate.candidate_id,'fixture-event')
        self.assertEqual(candidates[0].candidate.precision,'unknown')
        with store._connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM source_snapshot').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM source_document_detail_version').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT outcome FROM delivery_attempt ORDER BY attempt_id').fetchall(),[('sent',),('uncertain',)])
            self.assertEqual(c.execute('SELECT archived FROM target WHERE target_id="fixture-archived"').fetchone()[0],1)
        backup=preview.with_name(preview.name+f'.pre-v16-to-v{SCHEMA_VERSION}.bak')
        restored=Path(self.directory.name)/'restored.sqlite3';restored.write_bytes(backup.read_bytes())
        recovered=StateStore(restored);recovered.initialize();recovered.validate_integrity()
        self.assertEqual(len(recovered.list_deliveries()),2)

    def test_intermediate_versions_retry_and_original_backup_immutable(self):
        for version in ('17','18','19','20','21'):
            with self.subTest(version=version):
                path=Path(self.directory.name)/f'v{version}.sqlite3'
                path.write_bytes(self.path.read_bytes())
                with closing(sqlite3.connect(path)) as c:
                    c.execute('UPDATE app_meta SET value=? WHERE key="schema_version"',(version,));c.commit()
                store=StateStore(path)
                with patch.object(store,'_ensure_column',side_effect=RuntimeError('synthetic migration interruption')):
                    with self.assertRaises(RuntimeError):store.initialize()
                backup=path.with_name(path.name+f'.pre-v{version}-to-v{SCHEMA_VERSION}.bak')
                checksum=sha256(backup.read_bytes()).digest()
                store.initialize();store.initialize();store.validate_integrity()
                self.assertEqual(sha256(backup.read_bytes()).digest(),checksum)
                self.assertEqual(store.get_meta('schema_version'),SCHEMA_VERSION)
                self.assertFalse(store.get_announcement_policy('fixture-target').enabled)

    def test_real_structural_stages_upgrade_continuously_16_through_22(self):
        expected={17:'runtime_settings',18:'article_version',19:'news_batch',20:'maintenance_scope',21:'event_verification',22:'digest_incident'}
        for version,table in expected.items():
            with self.subTest(version=version),patch('ff14_core.storage.SCHEMA_VERSION',str(version)):
                store=StateStore(self.path);store.initialize();store.initialize();store.validate_integrity()
                self.assertEqual(store.get_meta('schema_version'),str(version))
                with store._connection() as c:
                    self.assertTrue(c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone())
                    if version<22:
                        self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='digest_incident'").fetchone())
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM delivery_attempt').fetchone()[0],2)
                self.assertEqual(store.get_owner_user_id(),'fixture-owner')
        store=StateStore(self.path)
        self.assertEqual(store.get_activity_digest_policy('fixture-target').selected_regions,('cn',))
        self.assertIsNone(store.get_event_verification('china_news_api','fixture-article'))
