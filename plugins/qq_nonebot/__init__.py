"""FFXIVBot 私有 NoneBot2 网关；不包含新闻、活动或市场业务逻辑。"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import GROUP, Bot, GroupMessageEvent, Message
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
                message.text,
                at_sender=False,
            )
        except Exception as error:
            return SendResult(
                success=False,
                retryable=True,
                error=f"{type(error).__name__}: {error}",
            )
        return SendResult(
            success=True,
            provider_message_id=_message_id(result),
        )

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
                message=message.text,
            )
        except Exception as error:
            return SendResult(
                success=False,
                retryable=True,
                error=f"{type(error).__name__}: {error}",
            )
        return SendResult(success=True, provider_message_id=_message_id(result))


REGISTRY = BotConnectionRegistry()
MESSAGING = NoneBotMessagingPort(REGISTRY)
BACKGROUND_TASKS: set[asyncio.Task[None]] = set()
SOURCE_POLL_INTERVAL_SECONDS = 6 * 60 * 60
DIGEST_TICK_INTERVAL_SECONDS = 30


class NoneBotGateway:
    """只负责 OneBot 事件、Core 请求和消息端口之间的转换。"""

    def __init__(self, application: CoreApplication, messaging: MessagingPort) -> None:
        self._application = application
        self._messaging = messaging

    async def handle_group_command(
        self, bot: Bot, event: GroupMessageEvent, args: Message
    ) -> None:
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
    BACKGROUND_TASKS.update(
        {
            asyncio.create_task(_source_poll_loop()),
            asyncio.create_task(_digest_loop()),
        }
    )
    LOGGER.info("FFXIVBot Core 已由 NoneBot2 网关启动")


@DRIVER.on_shutdown
async def _on_shutdown() -> None:
    tasks = tuple(BACKGROUND_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    BACKGROUND_TASKS.clear()


@DRIVER.on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    REGISTRY.connect(bot)
    LOGGER.info("OneBot Bot 已连接：%s", bot.self_id)


@DRIVER.on_bot_disconnect
async def _on_bot_disconnect(bot: Bot) -> None:
    REGISTRY.disconnect(bot)
    LOGGER.warning("OneBot Bot 已断开：%s", bot.self_id)


@FF14_COMMAND.handle()
async def _handle_ff14_command(
    bot: Bot,
    event: GroupMessageEvent,
    args: Message = CommandArg(),
) -> None:
    if isinstance(event, GroupMessageEvent):
        await GATEWAY.handle_group_command(bot, event, args)


def _message_id(result: object) -> str | None:
    if isinstance(result, dict) and result.get("message_id") is not None:
        return str(result["message_id"])
    return None


async def _source_poll_loop() -> None:
    await asyncio.sleep(5)
    while True:
        try:
            await asyncio.to_thread(APPLICATION.source_service.refresh)
            await asyncio.to_thread(
                APPLICATION.china_detail_service.refresh_cached_documents,
                40,
                0.15,
            )
        except Exception:
            LOGGER.exception("自动来源轮询失败；保留最后有效快照")
        await asyncio.sleep(SOURCE_POLL_INTERVAL_SECONDS)


async def _digest_loop() -> None:
    while True:
        try:
            await APPLICATION.digest_workflow.tick(MESSAGING)
            await APPLICATION.maintenance_workflow.tick(MESSAGING)
        except Exception:
            LOGGER.exception("活动摘要或维护提醒调度/投递失败")
        await asyncio.sleep(DIGEST_TICK_INTERVAL_SECONDS)
