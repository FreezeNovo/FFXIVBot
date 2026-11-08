"""Offline deployment checks: config, cancellation, locks and copy-only migration."""
from contextlib import closing
import base64
from hashlib import sha256
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ff14_app.runtime_lock import RuntimeLock
from ff14_app.windows_deploy import prepare_database, run, validate_configuration
from ff14_core.storage import StateStore


class WindowsDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / '.env').write_text('ONEBOT_V11_WS_URLS=[]\n', encoding='utf-8')
        for name in ('pyproject.toml', 'uv.lock'):
            (self.root / name).write_text('test fixture', encoding='utf-8')

    def test_disabled_and_configured_connections(self):
        self.assertFalse(validate_configuration(self.root))
        (self.root / '.env').write_text('ONEBOT_V11_WS_URLS=["ws://127.0.0.1:12345"]\nONEBOT_V11_ACCESS_TOKEN=fixture-token\n', encoding='utf-8-sig')
        self.assertTrue(validate_configuration(self.root))

    def test_invalid_config_fails_without_leaking_values(self):
        for urls in ('{}', '[123]', '["https://invalid.example"]', '["ws://<placeholder>:123"]', '["ws://127.0.0.1:bad"]', '["ws://user:private-secret@example.invalid"]'):
            with self.subTest(urls=urls):
                (self.root / '.env').write_text('ONEBOT_V11_WS_URLS=' + urls, encoding='utf-8')
                with self.assertRaises(ValueError) as caught:
                    validate_configuration(self.root)
                self.assertNotIn('private-secret', str(caught.exception))
        (self.root / '.env').write_text('ONEBOT_V11_WS_URLS=["ws://127.0.0.1:12345"]\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'token'):
            validate_configuration(self.root)

    def test_shadow_config_and_interpolation_rejected(self):
        (self.root / '.env.prod').touch()
        with self.assertRaisesRegex(ValueError, '额外'):
            validate_configuration(self.root)
        (self.root / '.env.prod').unlink()
        (self.root / '.env').write_text('ONEBOT_V11_WS_URLS=${OTHER_BOT}\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '插值'):
            validate_configuration(self.root)

    def test_copy_preview_preserves_original_and_backup(self):
        database = self.root / 'data' / 'ff14_bot.sqlite3'
        database.parent.mkdir()
        fixture = Path(__file__).parent / 'fixtures' / 'p3_schema_v16.sql'
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript(fixture.read_text(encoding='utf-8'))
        before = sha256(database.read_bytes()).digest()
        with RuntimeLock(database.parent / 'runtime.lock'):
            backup = prepare_database(self.root)
            second = prepare_database(self.root)
        self.assertNotEqual(backup, second)
        self.assertEqual(sha256(database.read_bytes()).digest(), before)
        self.assertEqual(StateStore(backup / 'before.sqlite3').get_meta('schema_version'), '16')
        self.assertEqual(StateStore(backup / 'preview.sqlite3').get_meta('schema_version'), '22')
        self.assertEqual((backup / 'connection.env').read_bytes(), (self.root / '.env').read_bytes())
        StateStore(backup / 'before.sqlite3').validate_integrity()

    def test_current_schema_and_new_install_do_not_create_backups(self):
        self.assertIsNone(prepare_database(self.root))
        database = self.root / 'data' / 'ff14_bot.sqlite3'
        StateStore(database).initialize()
        self.assertIsNone(prepare_database(self.root))
        self.assertFalse((database.parent / 'backups').exists())

    def test_newer_schema_or_corruption_stops_deployment(self):
        database = self.root / 'data' / 'ff14_bot.sqlite3'
        StateStore(database).initialize()
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE app_meta SET value='999' WHERE key='schema_version'")
            connection.commit()
        with self.assertRaisesRegex(ValueError, '降级'):
            prepare_database(self.root)
        database.write_bytes(b'corrupted fixture')
        with self.assertRaises(sqlite3.DatabaseError):
            prepare_database(self.root)

    def test_cancel_does_not_initialize_database_or_start_gateway(self):
        with patch('builtins.input', return_value='cancel'), patch('ff14_app.main._run') as gateway:
            run(self.root)
        gateway.assert_not_called()
        self.assertFalse((self.root / 'data' / 'ff14_bot.sqlite3').exists())

    def test_confirmed_start_retains_runtime_lock(self):
        def check_lock():
            with self.assertRaises(RuntimeError):
                with RuntimeLock(self.root / 'data' / 'runtime.lock'):
                    pass
        with patch('builtins.input', return_value='START'), patch('ff14_app.main._run', side_effect=check_lock) as gateway:
            run(self.root)
        gateway.assert_called_once()

    def test_failed_preview_cannot_start_gateway_or_modify_source(self):
        database = self.root / 'data' / 'ff14_bot.sqlite3'
        database.parent.mkdir()
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript((Path(__file__).parent / 'fixtures' / 'p3_schema_v16.sql').read_text(encoding='utf-8'))
        before = sha256(database.read_bytes()).digest()
        with patch('ff14_app.windows_deploy.preview_migration', side_effect=ValueError('preview failed')), patch('ff14_app.main._run') as gateway:
            with self.assertRaisesRegex(ValueError, 'preview failed'):
                run(self.root)
        gateway.assert_not_called()
        self.assertEqual(sha256(database.read_bytes()).digest(), before)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows .NET/msvcrt interop requires Windows')
    def test_powershell_installer_and_python_runtime_locks_interoperate(self):
        path = self.root / 'runtime.lock'
        literal_path = str(path).replace("'", "''")
        open_lock = ("$f=[IO.File]::Open('" + literal_path + "', [IO.FileMode]::OpenOrCreate, "
                     "[IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite); ")

        def powershell(code):
            encoded = base64.b64encode(code.encode('utf-16-le')).decode('ascii')
            return subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                                  capture_output=True, timeout=20)

        probe = open_lock + 'try {$f.Lock(0,1)} catch {exit 7} finally {$f.Dispose()}; exit 0'
        with RuntimeLock(path):
            self.assertEqual(powershell(probe).returncode, 7)
        self.assertEqual(powershell(probe).returncode, 0)
        child_code = ('from pathlib import Path; from ff14_app.runtime_lock import RuntimeLock\n'
                      'try:\n with RuntimeLock(Path(' + repr(str(path)) + ')): pass\n'
                      'except RuntimeError: raise SystemExit(0)\n'
                      'raise SystemExit(1)')
        invoke_python = "& '" + sys.executable.replace("'", "''") + "' -c '" + child_code.replace("'", "''") + "'; $result=$LASTEXITCODE"
        result = powershell(open_lock + 'try {$f.Lock(0,1); ' + invoke_python + '} finally {$f.Dispose()}; exit $result')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
