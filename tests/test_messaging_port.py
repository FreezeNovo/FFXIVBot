from __future__ import annotations

import asyncio
import unittest

from ff14_core.messaging import (
    CommandRequest,
    MessagingPort,
    OutboundMessage,
    SendResult,
    TargetRef,
)


class FakeMessagingPort(MessagingPort):
    """模拟连接、平台错误和超时，供 Core 集成测试使用。"""

    def __init__(self) -> None:
        self.connected = False
        self.fail_next = False
        self.timeout_next = False
        self.replies: list[str] = []
        self.proactive: list[tuple[str, str]] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    async def reply(
        self, request: CommandRequest, message: OutboundMessage
    ) -> SendResult:
        if self.timeout_next:
            self.timeout_next = False
            raise TimeoutError("fake timeout")
        if not self.connected:
            return SendResult(success=False, retryable=True, error="offline")
        if self.fail_next:
            self.fail_next = False
            return SendResult(success=False, retryable=True, error="platform error")
        self.replies.append(message.text)
        return SendResult(success=True, provider_message_id="reply-1")

    async def send_proactive(
        self, target: TargetRef, message: OutboundMessage
    ) -> SendResult:
        if not self.connected:
            return SendResult(success=False, retryable=True, error="offline")
        self.proactive.append((target.target_id, message.text))
        return SendResult(success=True, provider_message_id="proactive-1")


class MessagingPortTests(unittest.TestCase):
    def test_fake_covers_connection_proactive_and_platform_failure(self) -> None:
        async def scenario() -> None:
            port = FakeMessagingPort()
            target = TargetRef.for_onebot_group("group-test")
            request = CommandRequest(
                target=target,
                actor_id="member",
                actor_role=None,
                text="状态",
            )

            self.assertEqual(
                (await port.reply(request, OutboundMessage("offline"))).error,
                "offline",
            )
            port.connect()
            self.assertTrue(
                (await port.send_proactive(target, OutboundMessage("digest"))).success
            )
            port.fail_next = True
            failed = await port.reply(request, OutboundMessage("retry"))
            self.assertTrue(failed.retryable)
            port.disconnect()
            self.assertFalse(port.connected)

        asyncio.run(scenario())

    def test_fake_timeout_is_not_a_success(self) -> None:
        async def scenario() -> None:
            port = FakeMessagingPort()
            port.connect()
            port.timeout_next = True
            with self.assertRaises(TimeoutError):
                await port.reply(
                    CommandRequest(
                        target=TargetRef.for_onebot_group("group-test"),
                        actor_id="member",
                        actor_role=None,
                        text="状态",
                    ),
                    OutboundMessage("timeout"),
                )
            self.assertEqual(port.replies, [])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
