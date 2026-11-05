from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ff14_core.storage import StateStore, SCHEMA_VERSION


class DeliveryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'test.sqlite3'
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript((Path(__file__).parent / 'fixtures/delivery_schema_v15.sql').read_text())
        self.store = StateStore(self.path)

    def test_upgrade_preserves_attempts_holds_legacy_queue_and_restores_backup(self):
        self.store.initialize()
        self.store.validate_integrity()
        backup = self.path.with_name(self.path.name + f'.pre-v15-to-v{SCHEMA_VERSION}.bak')
        checksum = sha256(backup.read_bytes()).digest()
        with closing(sqlite3.connect(self.path)) as connection:
            statuses = dict(connection.execute('SELECT notification_id,status FROM notification_candidate'))
            self.assertEqual(statuses, {'fixture-sent':'sent', 'fixture-retry':'uncertain', 'fixture-pending':'superseded'})
            attempts = connection.execute('SELECT success,retryable,provider_message_id,error,outcome FROM delivery_attempt ORDER BY attempt_id').fetchall()
            self.assertEqual(attempts, [(1,0,'fixture-provider',None,'sent'), (0,1,None,'fixture timeout','uncertain')])
            connection.execute('PRAGMA foreign_keys = ON')
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM target WHERE target_id = 'fixture-target'")
        self.store.initialize()
        self.assertEqual(sha256(backup.read_bytes()).digest(), checksum)
        restored = self.path.parent / 'restore.sqlite3'
        StateStore(backup).backup_to(restored)
        self.assertEqual(StateStore(restored).get_meta('schema_version'), '15')
        StateStore(restored).initialize()
        StateStore(restored).validate_integrity()
        self.assertEqual(self.store.get_meta('schema_version'), SCHEMA_VERSION)
        self.assertFalse(self.store.get_manual_query_policy('fixture-target').activity_enabled)

    def test_failed_upgrade_retry_preserves_original_versioned_backup(self):
        original = self.store._ensure_column
        def fail_once(connection, table, column, definition):
            if table == 'target' and column == 'archived':
                raise RuntimeError('fixture interrupted upgrade')
            return original(connection, table, column, definition)
        with patch.object(self.store, '_ensure_column', side_effect=fail_once):
            with self.assertRaises(RuntimeError):
                self.store.initialize()
        backup = self.path.with_name(self.path.name + f'.pre-v15-to-v{SCHEMA_VERSION}.bak')
        checksum = sha256(backup.read_bytes()).digest()
        self.store.initialize()
        self.store.validate_integrity()
        self.assertEqual(sha256(backup.read_bytes()).digest(), checksum)
        self.assertEqual(len(self.store.list_deliveries()), 3)
