from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('windows_package', ROOT / 'scripts' / 'build-windows-package.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class WindowsPackageTests(unittest.TestCase):
    def test_payload_hashes_exclusions_and_windows_script_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'package.zip'
            builder.build(ROOT, destination)
            with zipfile.ZipFile(destination) as archive:
                self.assertIsNone(archive.testzip())
                prefix = archive.namelist()[0].split('/')[0] + '/'
                manifest = json.loads(archive.read(prefix + 'PACKAGE-MANIFEST.json'))
                names = {name.removeprefix(prefix) for name in archive.namelist()}
                self.assertEqual(names, set(manifest['files']) | {'PACKAGE-MANIFEST.json'})
                for name, expected in manifest['files'].items():
                    self.assertEqual(sha256(archive.read(prefix + name)).hexdigest(), expected)
                    self.assertFalse(set(Path(name).parts) & {'data', 'logs', '.git', '.scratch', '.venv', '.venv-linux', '__pycache__'})
                self.assertNotIn('.env', names)
                self.assertNotIn('config.yaml', names)
                self.assertTrue({'Start-FFXIVBot.cmd', 'scripts/start-windows.ps1', 'ff14_app/windows_deploy.py', 'tests/fixtures/p3_schema_v16.sql'} <= names)
                ps = archive.read(prefix + 'scripts/start-windows.ps1')
                self.assertTrue(ps.startswith(b'\xef\xbb\xbf'))
                self.assertIn(b'\r\n', archive.read(prefix + 'Start-FFXIVBot.cmd'))
            self.assertEqual(destination.with_suffix('.zip.sha256').read_text().split()[0], sha256(destination.read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                builder.build(ROOT, destination)
