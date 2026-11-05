"""持久投递协议：先登记，再调用 provider，未知结果留待核查。"""
from __future__ import annotations

import asyncio
from datetime import datetime

from .messaging import MessagingPort, OutboundMessage, SendResult
from .storage import StateStore
from .diagnostics import diagnostic,safe_reason


async def dispatch_pending(
    store: StateStore, messaging: MessagingPort, *, stream: str, now: datetime | None = None
) -> int:
    attempted = 0
    for notification_id, _, _ in store.list_pending_notifications(stream=stream):
        claim = store.claim_notification(notification_id, now=now)
        if claim is None:
            continue
        try:
            result = await messaging.send_proactive(claim.target, OutboundMessage(claim.message_text))
        except asyncio.CancelledError:
            store.finish_delivery_attempt(claim.attempt_id, SendResult(
                success=False, uncertain=True, error="发送期间任务中断，结果未知",
            ))
            raise
        except Exception as error:
            result = SendResult(success=False, uncertain=True, error=f"{type(error).__name__}: 发送结果未知")
        if not result.success:
            diagnostic(stage="delivery",target_id=claim.target.target_id,action="uncertain" if result.uncertain else "retry" if result.retryable else "failed",error=f"attempt={claim.attempt_id} "+safe_reason(result.error or "unknown"))
        store.finish_delivery_attempt(claim.attempt_id, result)
        attempted += 1
    return attempted
