"""真实 NoneBot 事件映射与发送 adapter；在子进程的临时工作区中测试。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class GatewayTests(unittest.TestCase):
    def test_real_event_permissions_and_send_outcomes(self):
        root = Path(__file__).resolve().parents[1]
        code = r'''
import asyncio
from pathlib import Path
from unittest.mock import patch
import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent, Message
from ff14_core.config import AppConfig
from ff14_core.messaging import OutboundMessage, TargetRef
nonebot.init(driver='~fastapi+~websockets', onebot_v11_ws_urls=[], host='127.0.0.1')
with patch.object(AppConfig, 'for_workspace', return_value=AppConfig(Path.cwd() / 'test.sqlite3')):
    import plugins.qq_nonebot as gateway
from ff14_core.source_models import ManualQueryPolicy
app = gateway.APPLICATION
app.start()
app.store.set_owner_user_id('12345')
target = app.store.save_onebot_group('67890', enabled=True)
class Replies:
    def __init__(self): self.messages = []
    async def reply(self, request, message): self.messages.append(message.text)
replies = Replies()
handler = gateway.NoneBotGateway(app, replies)
base = dict(time=1, self_id=99999, post_type='message', sub_type='normal', message_id=1,
    user_id=54321, message='ff14 状态', original_message='ff14 状态', raw_message='ff14 状态', font=0)
group = GroupMessageEvent(**base, message_type='group', group_id=67890, sender={'user_id':54321,'role':'owner'})
class FakeBot:
    self_id = '99999'
    def __init__(self): self.result = {}; self.error = None; self.messages = []
    async def send_msg(self, **kwargs):
        if self.error: raise self.error
        self.messages.append(kwargs['message'])
        return self.result
async def run():
    await handler.handle_group_command(None, group, Message('状态'))
    assert '仅限 Bot 所有者' in replies.messages[-1]
    await handler.handle_group_command(None, group.model_copy(update={'user_id':12345}), Message('状态'))
    assert 'FF14 Bot 状态' in replies.messages[-1]
    count = len(replies.messages)
    await handler.handle_group_command(None, group.model_copy(update={'group_id':11111}), Message('状态'))
    assert len(replies.messages) == count
    private = PrivateMessageEvent(**{**base, 'sub_type':'friend'}, message_type='private', sender={'user_id':54321})
    await handler.handle_group_command(None, private, Message('状态'))
    assert len(replies.messages) == count
    registry = gateway.BotConnectionRegistry()
    port = gateway.NoneBotMessagingPort(registry)
    offline = await port.send_proactive(target, OutboundMessage('fixture'))
    assert offline.retryable and not offline.uncertain
    bot = FakeBot(); registry.connect(bot)
    missing_id = await port.send_proactive(target, OutboundMessage('fixture'))
    assert not missing_id.success and missing_id.uncertain
    bot.error = TimeoutError('fixture')
    timeout = await port.send_proactive(target, OutboundMessage('fixture'))
    assert timeout.uncertain and not timeout.retryable
    from nonebot.adapters.onebot.v11.exception import ActionFailed
    bot.error = ActionFailed(retcode=100)
    rejected = await port.send_proactive(target, OutboundMessage('fixture'))
    assert not rejected.success and not rejected.uncertain and not rejected.retryable
    bot.error = None; bot.result = {'message_id':42}
    literal = '[CQ:at,qq=all] plain text'
    sent = await port.send_proactive(target, OutboundMessage(literal))
    assert sent.success and sent.provider_message_id == '42'
    assert not isinstance(bot.messages[-1], str)
    assert Message(bot.messages[-1]).extract_plain_text() == literal
with asyncio.Runner() as runner:
    # Windows may create an internal wakeup socket when constructing its loop.
    runner.get_loop()
    with patch('socket.socket.connect', side_effect=AssertionError('No external network')):
        runner.run(run())
'''
        with tempfile.TemporaryDirectory() as tmp:
            environment = {key: os.environ[key] for key in
                ('PATH', 'SYSTEMROOT', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
            environment.update({'PYTHONPATH': str(root), 'LANG': 'C.UTF-8',
                'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'ONEBOT_V11_WS_URLS': '[]'})
            result = subprocess.run([sys.executable, '-c', code], cwd=tmp, env=environment,
                                    capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, (result.stdout or '') + (result.stderr or ''))
