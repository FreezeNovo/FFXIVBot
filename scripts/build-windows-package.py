"""Build a source deployment ZIP using an explicit allowlist, never local state."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
import zipfile


ROOT_FILES = {
    '.env.example', 'pyproject.toml', 'uv.lock', 'Start-FFXIVBot.cmd',
    'WINDOWS-QUICKSTART.md', 'WINDOWS-DEPLOYMENT.md', 'UV-USAGE.md', 'WEBUI.md',
    'GOAL.md', 'ARCHITECTURE.md', 'TODO.md', 'DATA-SOURCES.md', 'CHANGELOG.md',
    'MIGRATION.md', 'GLOSSARY.md', 'AGENTS.md', 'RESEARCH.md', 'ARCHIVE-KOISHI.md',
}
SCRIPT_FILES = {
    'scripts/start-windows.ps1', 'scripts/start-nonebot-dev.ps1',
    'scripts/start-webui.ps1', 'scripts/start-nonebot-dev.sh',
    'scripts/check-linux.sh', 'scripts/build-windows-package.py',
}


def package_files(root: Path) -> list[Path]:
    selected = {root / name for name in ROOT_FILES | SCRIPT_FILES}
    for directory in ('ff14_app', 'ff14_core', 'ff14_web', 'plugins', 'tests'):
        selected.update((root / directory).rglob('*.py'))
    for path in (root / 'tests' / 'fixtures').rglob('*'):
        if path.suffix in {'.json', '.sql', '.xml', '.html', '.md'}:
            selected.add(path)
    selected.update((root / 'docs').rglob('*.md'))
    for path in selected:
        if not path.is_file() or path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError(f'Missing file or unsafe symlink: {path}')
        if any(part in {'__pycache__', '.venv', '.venv-linux', 'data', 'logs', '.git', '.scratch'} for part in path.relative_to(root).parts):
            raise ValueError(f'Local state must never enter ZIP: {path}')
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def build(root: Path, destination: Path) -> Path:
    root = root.resolve()
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    folder = f'FFXIVBot-{version}-windows'
    payload = {}
    for path in package_files(root):
        name = path.relative_to(root).as_posix()
        data = path.read_bytes()
        # Windows PowerShell 5.1 requires a BOM for UTF-8 scripts with Unicode.
        if path.suffix == '.ps1':
            data = b'\xef\xbb\xbf' + data.decode('utf-8-sig').replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-8')
        elif path.suffix == '.cmd':
            data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        payload[name] = data
    revision = None
    if shutil.which('git') and (root / '.git').exists():
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, capture_output=True, check=False)
    manifest = {
        'version': version,
        'source_commit': revision.stdout.strip() if revision is not None and revision.returncode == 0 else None,
        'source_note': 'Packaged working-tree allowlist, including documented deployment changes; not a pristine tagged release.',
        'installation': 'Online current-user bootstrap; Python/uv/dependencies are NOT bundled.',
        'files': {name: sha256(data).hexdigest() for name, data in payload.items()},
    }
    payload['PACKAGE-MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids silently overwriting a previously delivered ZIP.
    with destination.open('xb') as output:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(payload.items()):
                info = zipfile.ZipInfo(f'{folder}/{name}', date_time=(2026, 11, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
    digest = sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + '.sha256').write_text(f'{digest}  {destination.name}\n', encoding='ascii')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build(Path(__file__).resolve().parents[1], args.output))
