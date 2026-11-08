"""部署预检；确认前不加载网关、不迁移原库、不访问网络。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values

from ff14_app.migration_preview import preview_migration
from ff14_app.runtime_lock import RuntimeLock
from ff14_core.storage import SCHEMA_VERSION, StateStore


def validate_configuration(root: Path) -> bool:
    """只验证本地配置，不在错误中包含地址或 token。"""
    if not (root / '.env').is_file():
        raise ValueError('缺少 .env，请先运行 Start-FFXIVBot.cmd')
    # NoneBot normally overlays .env.prod; do not silently use a second config.
    if any(path.name != '.env.example' for path in root.glob('.env.*')):
        raise ValueError('发现额外 .env.* 配置；请先合并到 .env 并另行保存该文件，避免连接配置被覆盖')
    values = dotenv_values(root / '.env', interpolate=False, encoding='utf-8-sig')
    if any('${' in (value or '') for value in values.values()):
        raise ValueError('部署配置不支持变量插值，请在 .env 中填写直接值')
    try:
        urls = json.loads(values.get('ONEBOT_V11_WS_URLS') or '')
        if not isinstance(urls, list):
            raise ValueError
        for url in urls:
            if not isinstance(url, str) or '<' in url or '>' in url:
                raise ValueError
            parsed = urlsplit(url)
            if parsed.scheme not in {'ws', 'wss'} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError
            _ = parsed.port
    except (ValueError, TypeError):
        raise ValueError('ONEBOT_V11_WS_URLS 必须为有效 ws/wss 地址的 JSON 数组；请替换模板占位符') from None
    token = values.get('ONEBOT_V11_ACCESS_TOKEN') or ''
    if urls and (not token.strip() or '<' in token or '>' in token):
        raise ValueError('启用 QQ 连接时必须填写既有 NapCat 的有效 token')
    return bool(urls)


def prepare_database(root: Path) -> Path | None:
    """调用方持有运行锁；只对独立副本迁移，原库版本不变。"""
    source = root / 'data' / 'ff14_bot.sqlite3'
    if not source.exists():
        return None
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as connection:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_meta'").fetchone():
            row = connection.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
            version = row[0] if row else None
        else:
            version = None
    if version is not None and int(version) > int(SCHEMA_VERSION):
        raise ValueError('数据库来自更高 schema，禁止自动降级')
    if version == SCHEMA_VERSION:
        StateStore(source).validate_integrity()
        return None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    backup = root / 'data' / 'backups' / ('windows-deploy-' + stamp)
    backup.mkdir(parents=True, exist_ok=False)
    # Original, unmodified backup remains separate from the migrated preview.
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(backup / 'before.sqlite3')) as destination:
            original.backup(destination)
    shutil.copy2(root / '.env', backup / 'connection.env')
    for name in ('pyproject.toml', 'uv.lock'):
        shutil.copy2(root / name, backup / name)
    report = preview_migration(backup / 'before.sqlite3', backup / 'preview.sqlite3')
    (backup / 'preview.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'迁移副本检查通过，原库尚未升级。备份与预演：{backup}')
    print(f"schema: {report['previous_schema']} -> {report['preview_schema']}")
    return backup


def run(root: Path) -> None:
    with RuntimeLock(root / 'data' / 'runtime.lock'):
        connected = validate_configuration(root)
        prepare_database(root)
        print('启动后将访问官方来源并运行后台调度；已有库可能立即恢复有效期内的投递。')
        print('QQ 连接已配置。请确认旧实例停止、目标群与各流策略已获准。' if connected
              else 'QQ 连接未启用：仅本机管理与来源轮询，仍会写入本地数据库。')
        print('管理页面：http://127.0.0.1:20036/ （修改端口时使用配置端口）')
        if input('输入 START 确认启动，其他输入取消：').strip() != 'START':
            print('已取消，未启动应用或迁移原库。')
            return
        # Delay importing the entry point until after explicit confirmation.
        from ff14_app.main import _run
        _run()


if __name__ == '__main__':
    run(Path(__file__).resolve().parents[1])
