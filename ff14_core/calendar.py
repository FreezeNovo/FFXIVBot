"""活动摘要查询、渲染和持久投递工作流。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .messaging import MessagingPort, OutboundMessage, SendResult, TargetRef
from .source_models import ActivityDigest, ActivityDigestPolicy, CalendarEntry
from .storage import StateStore


class CalendarQueryService:
    """按目标策略查询进行中、即将开始和即将结束的活动。"""

    def __init__(
        self,
        store: StateStore,
        *,
        source_stale_after: timedelta | None = None,
    ) -> None:
        if source_stale_after is not None and source_stale_after <= timedelta(0):
            raise ValueError("source_stale_after must be positive")
        self._store = store
        self._source_stale_after = source_stale_after

    def query(
        self, target: TargetRef, *, now: datetime | None = None
    ) -> ActivityDigest:
        policy = self._store.get_activity_digest_policy(target.target_id)
        current = _require_aware(now or datetime.now(timezone.utc))
        local_now = current.astimezone(ZoneInfo(policy.timezone))
        window_start = local_now - timedelta(minutes=policy.ending_window_minutes)
        window_end = local_now + timedelta(minutes=policy.upcoming_window_minutes)
        entries = self._store.get_calendar_entries(
            window_start.isoformat(),
            window_end.isoformat(),
            region=policy.region,
            classification_codes=policy.classification_codes,
        )
        ongoing: list[CalendarEntry] = []
        upcoming: list[CalendarEntry] = []
        ending: list[CalendarEntry] = []
        for entry in entries:
            start = datetime.fromisoformat(entry.start_at).astimezone(local_now.tzinfo)
            end = datetime.fromisoformat(entry.end_at).astimezone(local_now.tzinfo)
            if start <= local_now < end:
                if end <= local_now + timedelta(minutes=policy.ending_window_minutes):
                    ending.append(entry)
                else:
                    ongoing.append(entry)
            elif local_now < start <= local_now + timedelta(
                minutes=policy.upcoming_window_minutes
            ):
                upcoming.append(entry)
        return ActivityDigest(
            target_id=target.target_id,
            generated_at=local_now.isoformat(),
            ongoing=tuple(ongoing),
            upcoming=tuple(upcoming),
            ending=tuple(ending),
            warnings=self._source_warnings(policy.region, current),
        )

    def _source_warnings(self, region: str, now: datetime) -> tuple[str, ...]:
        source_ids = ("china_news_api", "china_news_detail") if region == "cn" else ("lodestone_atom",)
        health = {item.source_id: item for item in self._store.get_source_health()}
        warnings: list[str] = []
        for source_id in source_ids:
            item = health.get(source_id)
            if item is None or item.last_success_at is None:
                warnings.append("活动数据源尚未成功同步")
                continue
            if item.consecutive_failures:
                warnings.append(f"活动数据源最近同步失败 {item.consecutive_failures} 次")
                continue
            if self._source_stale_after is not None:
                last_success = datetime.fromisoformat(item.last_success_at)
                if now - last_success > self._source_stale_after:
                    warnings.append("活动数据源数据已超过允许新鲜度")
        return tuple(dict.fromkeys(warnings))


class ActivityDigestRenderer:
    """渲染与 QQ 无关的紧凑纯文本摘要。"""

    def render(self, digest: ActivityDigest) -> str:
        lines = ["FF14 活动摘要"]
        lines.extend(f"数据源提示：{warning}" for warning in digest.warnings)
        self._append_section(lines, "正在进行的活动", digest.ongoing, "进行至")
        self._append_section(lines, "即将开始的活动", digest.upcoming, "开始时间")
        self._append_section(lines, "即将结束的活动", digest.ending, "结束时间")
        if not digest.ongoing and not digest.upcoming and not digest.ending:
            lines.append("当前时间窗口内没有符合本群策略的活动。")
        return "\n".join(lines)

    @staticmethod
    def split(text: str, *, maximum_length: int = 900) -> tuple[str, ...]:
        if maximum_length < 80:
            raise ValueError("maximum_length must be at least 80")
        if len(text) <= maximum_length:
            return (text,)
        chunks: list[str] = []
        current = ""
        for line in text.splitlines():
            proposed = line if not current else f"{current}\n{line}"
            if current and len(proposed) > maximum_length:
                chunks.append(current)
                current = line
            else:
                current = proposed
        if current:
            chunks.append(current)
        return tuple(chunks)

    def _append_section(
        self,
        lines: list[str],
        heading: str,
        entries: tuple[CalendarEntry, ...],
        time_label: str,
    ) -> None:
        if not entries:
            return
        lines.append(f"【{heading}】")
        for entry in entries[:8]:
            timestamp = (
                entry.end_at if time_label in {"进行至", "结束时间"} else entry.start_at
            )
            moment = datetime.fromisoformat(timestamp)
            display_time = f"{moment.year}/{moment.month}/{moment.day} {moment:%H:%M}"
            lines.append(f"{_region_prefix(entry.region)}-{entry.title}")
            lines.append(f"{time_label}: {display_time}")
        if len(entries) > 8:
            lines.append(f"另有 {len(entries) - 8} 项未显示。")


class ActivityDigestWorkflow:
    """将每日群策略转成持久候选，并逐条投递。"""

    STREAM = "activity_digest"

    def __init__(
        self,
        store: StateStore,
        query_service: CalendarQueryService,
        renderer: ActivityDigestRenderer | None = None,
    ) -> None:
        self._store = store
        self._query_service = query_service
        self._renderer = renderer or ActivityDigestRenderer()

    def preview(self, target: TargetRef, *, now: datetime | None = None) -> str:
        return self._renderer.render(self._query_service.query(target, now=now))

    def plan_due(self, *, now: datetime | None = None) -> int:
        current = _require_aware(now or datetime.now(timezone.utc))
        created = 0
        for managed_target, policy in self._store.list_enabled_activity_digest_targets():
            local_now = current.astimezone(ZoneInfo(policy.timezone))
            scheduled = local_now.replace(
                hour=policy.digest_hour,
                minute=policy.digest_minute,
                second=0,
                microsecond=0,
            )
            if local_now < scheduled:
                continue
            message = self.preview(managed_target.target, now=current)
            pages = self._renderer.split(message)
            window_key = scheduled.strftime("%Y-%m-%dT%H:%M")
            for page_index, page in enumerate(pages):
                business_key = (
                    f"{self.STREAM}:{managed_target.target.target_id}:{window_key}:{page_index}"
                )
                if self._store.create_notification_candidate(
                    business_key=business_key,
                    target_id=managed_target.target.target_id,
                    stream=self.STREAM,
                    window_key=window_key,
                    message_text=page,
                ):
                    created += 1
        return created

    async def dispatch_pending(
        self, messaging: MessagingPort, *, stream: str | None = None
    ) -> int:
        attempted = 0
        for notification_id, target, message_text in self._store.list_pending_notifications(
            stream=stream or self.STREAM
        ):
            try:
                result = await messaging.send_proactive(
                    target, OutboundMessage(message_text)
                )
            except Exception as error:
                # A provider exception is an uncertain delivery outcome. Keep
                # the candidate pending and isolate the failure to this target.
                result = SendResult(
                    success=False,
                    retryable=True,
                    error=f"{type(error).__name__}: {error}",
                )
            self._store.record_delivery_attempt(
                notification_id=notification_id,
                success=result.success,
                retryable=result.retryable,
                provider_message_id=result.provider_message_id,
                error=result.error,
            )
            attempted += 1
        return attempted

    async def tick(self, messaging: MessagingPort, *, now: datetime | None = None) -> int:
        self.plan_due(now=now)
        return await self.dispatch_pending(messaging, stream=self.STREAM)


def _region_prefix(region: str) -> str:
    return {"cn": "C", "global-na": "NA", "global-eu": "EU", "global-jp": "JP", "global-oc": "OC"}.get(
        region, region.upper()
    )


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    return value
