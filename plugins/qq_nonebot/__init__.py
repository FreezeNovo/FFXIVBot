"""FFXIVBot 私有 NoneBot2 网关；不包含新闻、活动或市场业务逻辑。"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import GROUP, Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.params import CommandArg

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from ff14_core.application import CoreApplication
from ff14_core.config import AppConfig
from ff14_core.messaging import (
    CommandRequest,
    MessagingPort,
    OutboundMessage,
    SendResult,
    TargetRef,
)

LOGGER = logging.getLogger(__name__)
DRIVER = get_driver()
CONFIG = AppConfig.for_workspace(WORKSPACE_ROOT)
APPLICATION = CoreApplication.create(CONFIG)


@dataclass(frozen=True)
class _NoneBotReplyContext:
    bot: Bot
    event: GroupMessageEvent


class BotConnectionRegistry:
    """记录当前已连接的 Bot，便于主动发送和连接状态测试。"""

    def __init__(self) -> None:
        self._bots: dict[str, Bot] = {}

    def connect(self, bot: Bot) -> None:
        self._bots[str(bot.self_id)] = bot

    def disconnect(self, bot: Bot) -> None:
        self._bots.pop(str(bot.self_id), None)

    def snapshot(self) -> tuple[str, ...]:
        return tuple(sorted(self._bots))

    def first(self) -> Bot | None:
        return next(iter(self._bots.values()), None)


class NoneBotMessagingPort(MessagingPort):
    """将 Core 消息 DTO 转换为 OneBot V11 API 调用。"""

    def __init__(self, registry: BotConnectionRegistry) -> None:
        self._registry = registry

    def is_available(self):
        return self._registry.first() is not None

    async def reply(
        self, request: CommandRequest, message: OutboundMessage
    ) -> SendResult:
        context = request.reply_context
        if not isinstance(context, _NoneBotReplyContext):
            return SendResult(
                success=False,
                retryable=False,
                error="NoneBot reply context 不可用",
            )
        try:
            result = await context.bot.send(
                context.event,
                MessageSegment.text(message.text),
                at_sender=False,
            )
        except ActionFailed as error:
            code = getattr(error,"retcode",None)
            return SendResult(success=False, error=f"OneBot 明确拒绝发送 retcode={code if isinstance(code,int) else 'unknown'}")
        except Exception as error:
            return SendResult(success=False, uncertain=True, error=f"{type(error).__name__}: OneBot 发送结果未知")
        return _send_result(result)

    async def send_proactive(
        self, target: TargetRef, message: OutboundMessage
    ) -> SendResult:
        if target.provider != "onebot" or target.target_type != "group":
            return SendResult(
                success=False,
                retryable=False,
                error="不支持的 OneBot 投递目标",
            )
        bot = self._registry.first()
        if bot is None:
            return SendResult(
                success=False,
                retryable=True,
                error="当前没有已连接的 OneBot Bot",
            )
        try:
            result = await bot.send_msg(
                group_id=int(target.provider_target_id),
                message=MessageSegment.text(message.text),
            )
        except ActionFailed as error:
            code = getattr(error,"retcode",None)
            return SendResult(success=False, error=f"OneBot 明确拒绝发送 retcode={code if isinstance(code,int) else 'unknown'}")
        except Exception as error:
            return SendResult(success=False, uncertain=True, error=f"{type(error).__name__}: OneBot 发送结果未知")
        return _send_result(result)


REGISTRY = BotConnectionRegistry()
MESSAGING = NoneBotMessagingPort(REGISTRY)
from ff14_app.workers import ApplicationWorkers
WORKERS = ApplicationWorkers(APPLICATION, MESSAGING)
BACKGROUND_TASKS = WORKERS.tasks


class NoneBotGateway:
    """只负责 OneBot 事件、Core 请求和消息端口之间的转换。"""

    def __init__(self, application: CoreApplication, messaging: MessagingPort) -> None:
        self._application = application
        self._messaging = messaging

    async def handle_group_command(
        self, bot: Bot, event: GroupMessageEvent, args: Message
    ) -> None:
        if not isinstance(event, GroupMessageEvent):
            return
        target = TargetRef.for_onebot_group(str(event.group_id))
        request = CommandRequest(
            target=target,
            actor_id=str(event.user_id),
            actor_role=None,
            text=args.extract_plain_text(),
            reply_context=_NoneBotReplyContext(bot=bot, event=event),
        )
        result = await self._application.command_service.handle(request)
        if result.message is not None:
            await self._messaging.reply(request, result.message)


GATEWAY = NoneBotGateway(APPLICATION, MESSAGING)
FF14_COMMAND = on_command(
    "ff14",
    priority=10,
    block=True,
    force_whitespace=True,
    permission=GROUP,
)


@DRIVER.on_startup
async def _on_startup() -> None:
    APPLICATION.start()
    WORKERS.start()
    LOGGER.info("FFXIVBot Core 已由 NoneBot2 网关启动")


@DRIVER.on_shutdown
async def _on_shutdown() -> None:
    await WORKERS.stop()

@DRIVER.on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    REGISTRY.connect(bot)
    LOGGER.info("OneBot Bot 已连接")


@DRIVER.on_bot_disconnect
async def _on_bot_disconnect(bot: Bot) -> None:
    REGISTRY.disconnect(bot)
    LOGGER.warning("OneBot Bot 已断开")


@FF14_COMMAND.handle()
async def _handle_ff14_command(
    bot: Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
) -> None:
    if isinstance(event, GroupMessageEvent):
        await GATEWAY.handle_group_command(bot, event, args)


def _send_result(result: object) -> SendResult:
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if isinstance(message_id, (int, str)) and not isinstance(message_id, bool) and str(message_id).strip() not in {"", "0"}:
        return SendResult(success=True, provider_message_id=str(message_id))
    return SendResult(success=False, uncertain=True, error="OneBot 未返回有效消息 ID，发送结果未知")
