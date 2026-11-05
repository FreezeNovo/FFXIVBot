"""与 QQ 平台无关的目标、命令和消息端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5


class FFXIVBotError(Exception):
    """通道无关的应用错误基类。"""


class CommandError(FFXIVBotError):
    """命令请求或权限错误。"""


class MessagingError(FFXIVBotError):
    """消息发送端口错误。"""


@dataclass(frozen=True)
class TargetRef:
    """应用内部使用的稳定投递目标身份。"""

    target_id: str
    provider: str
    target_type: str
    provider_target_id: str
    display_name: str | None = None

    @classmethod
    def for_onebot_group(
        cls, provider_target_id: str, display_name: str | None = None
    ) -> "TargetRef":
        provider_target_id = str(provider_target_id).strip()
        if not provider_target_id:
            raise ValueError("provider_target_id 不能为空")
        stable_id = str(
            uuid5(
                NAMESPACE_URL,
                f"ffxivbot:onebot:group:{provider_target_id}",
            )
        )
        return cls(
            target_id=stable_id,
            provider="onebot",
            target_type="group",
            provider_target_id=provider_target_id,
            display_name=display_name,
        )


@dataclass(frozen=True)
class CommandRequest:
    """网关转换后的命令请求，不暴露平台事件类型。"""

    target: TargetRef
    actor_id: str
    actor_role: str | None
    text: str
    # 仅由 adapter 创建和解释，Core 不读取其平台成员。
    reply_context: Any | None = None


@dataclass(frozen=True)
class OutboundMessage:
    """通道无关的出站消息；当前只承诺纯文本。"""

    text: str


@dataclass(frozen=True)
class SendResult:
    success: bool
    provider_message_id: str | None = None
    retryable: bool = False
    error: str | None = None
    # True means the provider may have accepted the message; never auto-retry.
    uncertain: bool = False


class MessagingPort(Protocol):
    """应用向消息 provider 发送消息的最小端口。"""

    async def reply(
        self, request: CommandRequest, message: OutboundMessage
    ) -> SendResult: ...

    async def send_proactive(
        self, target: TargetRef, message: OutboundMessage
    ) -> SendResult: ...


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    message: OutboundMessage | None = None
