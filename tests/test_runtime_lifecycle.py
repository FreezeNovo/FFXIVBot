"""统一入口的离线生命周期检查：临时数据、无监听、禁止外部连接。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class RuntimeLifecycleTests(unittest.TestCase):
    def test_loopback_routes_startup_and_shutdown_without_external_connections(self):
        root = Path(__file__).resolve().parents[1]
        code = r'''
import asyncio
from pathlib import Path
from unittest.mock import patch
import nonebot
from ff14_core.config import AppConfig
from ff14_app.main import _run
with patch.object(AppConfig, 'for_workspace', return_value=AppConfig(Path.cwd() / 'smoke.sqlite3')):
    with patch.object(nonebot, 'run'):
        _run()
assert str(nonebot.get_driver().config.host) == '127.0.0.1'
from plugins.qq_nonebot import APPLICATION, BACKGROUND_TASKS, _on_startup, _on_shutdown
async def smoke():
    await _on_startup()
    await asyncio.sleep(0)
    assert len(BACKGROUND_TASKS) == 3
    for path in ('/', '/access', '/deliveries', '/review','/settings','/sources','/articles','/news'):
        messages = []
        async def receive(): return {'type':'http.request', 'body':b'', 'more_body':False}
        async def send(message): messages.append(message)
        await nonebot.get_asgi()({'type':'http', 'asgi':{'version':'3.0'}, 'http_version':'1.1',
            'method':'GET', 'scheme':'http', 'path':path, 'raw_path':path.encode(), 'query_string':b'',
            'root_path':'', 'headers':[], 'server':('test',80), 'client':('127.0.0.1',1)}, receive, send)
        assert messages[0]['status'] == 200, (path, messages[0])
    await _on_shutdown()
    assert not BACKGROUND_TASKS
    APPLICATION.store.validate_integrity()
    assert APPLICATION.store.get_meta('schema_version') == '22'
with patch('socket.socket.connect', side_effect=AssertionError('No external network')):
    asyncio.run(smoke())
print('schema22 lifecycle, loopback binding, 8 WebUI routes, shutdown and integrity: PASS')
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', code], cwd=directory,
                env={'PATH': os.environ['PATH'], 'PYTHONPATH': str(root), 'LANG': 'C.UTF-8',
                     'HOST': '0.0.0.0', 'DRIVER': '~fastapi+~websockets', 'ONEBOT_V11_WS_URLS': '[]'},
                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
