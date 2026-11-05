from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ff14_app.migration_preview import preview_migration
from ff14_core.storage import SCHEMA_VERSION


class MigrationPreviewTests(unittest.TestCase):
    def test_preview_migrates_only_copy_and_reports_legacy_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'original.sqlite3'
            destination = Path(directory) / 'preview.sqlite3'
            with closing(sqlite3.connect(source)) as connection:
                connection.executescript((Path(__file__).parent / 'fixtures/delivery_schema_v15.sql').read_text())
            checksum = sha256(source.read_bytes()).digest()
            report = preview_migration(source, destination)
            self.assertEqual(report, {'previous_schema':'15', 'preview_schema':SCHEMA_VERSION,
                                     'sent':1, 'uncertain':1, 'superseded':1})
            self.assertEqual(sha256(source.read_bytes()).digest(), checksum)
            self.assertFalse(source.with_name(source.name + '.pre-migration.bak').exists())
            with self.assertRaises(FileExistsError):
                preview_migration(source, source)
            with self.assertRaises(FileExistsError):
                preview_migration(source, destination)
            self.assertEqual(sha256(source.read_bytes()).digest(), checksum)

    def test_missing_source_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'missing.sqlite3'
            destination = Path(directory) / 'preview.sqlite3'
            with self.assertRaises(FileNotFoundError):
                preview_migration(source, destination)
            self.assertFalse(source.exists())
            self.assertFalse(destination.exists())
