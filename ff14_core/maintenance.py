"""国服维护查询、独立提醒规划和持久投递工作流。"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .messaging import MessagingPort, TargetRef
from .delivery import dispatch_pending
from .calendar import _region_prefix
from .source_models import ClassifiedEventCandidate, MaintenanceDigest
from .storage import ManagedTarget, StateStore


STREAM = "maintenance"


class MaintenanceQueryService:
    """查询尚未结束的国服维护，不复用活动摘要策略。"""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    def query(
        self, target: TargetRef, *, now: datetime | None = None
    ) -> MaintenanceDigest:
        policy = self._store.get_maintenance_policy(target.target_id)
        current = _require_aware(now or datetime.now(timezone.utc))
        local_now = current.astimezone(ZoneInfo(policy.timezone))
        ongoing: list[ClassifiedEventCandidate] = []
        upcoming: list[ClassifiedEventCandidate] = []
        for item in (item for region in policy.selected_regions for item in self._store.get_maintenance_candidates(region=region)):
            start = datetime.fromisoformat(item.candidate.start_at).astimezone(local_now.tzinfo)
            end = datetime.fromisoformat(item.candidate.end_at).astimezone(local_now.tzinfo)
            if start <= local_now < end:
                ongoing.append(item)
            elif local_now < start:
                upcoming.append(item)
        ongoing.sort(key=lambda item: item.candidate.start_at)
        upcoming.sort(key=lambda item: item.candidate.start_at)
        warnings: list[str] = []
        all_health = {item.source_id:item for item in self._store.get_source_health()}
        for region in policy.selected_regions:
            for source in (("china_news_detail",) if region=="cn" else ("lodestone_atom","lodestone_topics")):
                health=all_health.get(source)
                if health is None or health.last_success_at is None:
                    warnings.append(f"{_region_prefix(region)}维护数据源尚未成功同步")
                elif health.consecutive_failures:
                    warnings.append(f"{_region_prefix(region)}维护数据源最近同步失败 {health.consecutive_failures} 次")
        return MaintenanceDigest(
            target_id=target.target_id,
            generated_at=local_now.isoformat(),
            ongoing=tuple(_to_entry(item) for item in ongoing),
            upcoming=tuple(_to_entry(item) for item in upcoming),
            warnings=tuple(warnings),
        )


class MaintenanceRenderer:
    """渲染维护预览和单次提醒，保持与 QQ 无关。"""

    def render(self, digest: MaintenanceDigest) -> str:
        regions={e.region for e in (*digest.ongoing,*digest.upcoming)}
        lines = ["FF14 国服维护" if not regions or regions=={"cn"} else "FF14 维护"]
        lines.extend(f"数据源提示：{warning}" for warning in digest.warnings)
        if digest.ongoing:
            lines.append("【正在维护】")
            for entry in digest.ongoing[:8]:
                lines.extend(_entry_lines(entry, "结束时间"))
        if digest.upcoming:
            lines.append("【即将维护】")
            for entry in digest.upcoming[:8]:
                lines.extend(_entry_lines(entry, "开始时间"))
        if not digest.ongoing and not digest.upcoming:
            lines.append("当前没有已知的进行中或即将开始维护。")
        return "\n".join(lines)

    def render_event(
        self,
        item: ClassifiedEventCandidate,
        *,
        reason: str,
    ) -> str:
        candidate = item.candidate
        start = datetime.fromisoformat(candidate.start_at).astimezone(ZoneInfo("Asia/Shanghai"))
        end = datetime.fromisoformat(candidate.end_at).astimezone(ZoneInfo("Asia/Shanghai"))
        lines = [
            f"FF14 {'国服' if candidate.region=='cn' else _region_prefix(candidate.region)}维护：{reason}",
            f"{_region_prefix(candidate.region)}-{candidate.title}",
            f"开始时间: {start:%Y/%m/%d %H:%M}",
            f"结束时间: {end:%Y/%m/%d %H:%M}",
            f"依据：{candidate.evidence_text}",
        ]
        if candidate.url:
            lines.append(f"详情：{candidate.url}")
        return "\n".join(lines)


class MaintenanceWorkflow:
    """将维护策略转换为立即/提前提醒通知，并独立投递。"""

    def __init__(
        self,
        store: StateStore,
        query_service: MaintenanceQueryService,
        renderer: MaintenanceRenderer | None = None,
    ) -> None:
        self._store = store
        self._query_service = query_service
        self._renderer = renderer or MaintenanceRenderer()

    def preview(self, target: TargetRef, *, now: datetime | None = None) -> str:
        return self._renderer.render(self._query_service.query(target, now=now))

    def plan_due(self, *, now: datetime | None = None) -> int:
        current = _require_aware(now or datetime.now(timezone.utc))
        created = 0
        for managed_target, policy in self._store.list_enabled_maintenance_targets():
            candidates = tuple(item for region in policy.selected_regions for item in self._store.get_maintenance_candidates(region=region))
            for item in candidates:
                candidate = item.candidate
                start = _parse(candidate.start_at)
                end = _parse(candidate.end_at)
                if end <= current.astimezone(timezone.utc):
                    continue
                observation = self._store.observe_maintenance_candidate(
                    target_id=managed_target.target.target_id,
                    candidate=candidate,
                    watermark_at=self._store.get_maintenance_watermark(managed_target.target.target_id,candidate.region),
                )
                event_prefix = self._event_prefix(managed_target, candidate.document_id if candidate.source_id=="china_news_api" else candidate.source_id+":"+candidate.document_id)
                version_prefix = f"{event_prefix}{observation.version_id}:"
                if observation.transition == "changed":
                    # A changed start/end must replace unsent old-version
                    # reminders even when the optional update notice is off.
                    self._store.supersede_pending_notifications(
                        target_id=managed_target.target.target_id,
                        stream=STREAM,
                        event_prefix=event_prefix,
                        current_version_prefix=version_prefix,
                    )
                reason = ""
                if observation.transition == "new" and policy.notify_announcements:
                    reason = "发现新的维护公告"
                elif observation.transition == "changed" and policy.notify_updates:
                    reason = "维护时间发生变更"
                business_key = f"{version_prefix}announcement"
                if reason and not self._store.notification_exists(business_key):
                    if self._store.create_notification_candidate(
                        business_key=business_key,
                        target_id=managed_target.target.target_id,
                        stream=STREAM,
                        window_key=observation.version_id,
                        message_text=self._renderer.render_event(item, reason=reason),
                        expires_at=end.isoformat(),
                        kind="update" if observation.transition == "changed" else "announcement",
                        region=candidate.region,
                    ):
                        created += 1
                    continue

                if start <= current:
                    continue
                if policy.reminder_window_minutes <= 0:
                    continue
                minutes_until_start = (start - current).total_seconds() / 60
                if minutes_until_start > policy.reminder_window_minutes:
                    continue
                if self._store.maintenance_announcement_covers_reminder(
                    f"{version_prefix}announcement", start=start, lead_minutes=policy.reminder_window_minutes,
                ):
                    continue
                business_key = f"{version_prefix}reminder"
                if self._store.create_notification_candidate(
                    business_key=business_key,
                    target_id=managed_target.target.target_id,
                    stream=STREAM,
                    window_key=candidate.start_at,
                    message_text=self._renderer.render_event(item, reason="维护即将开始"),
                    expires_at=start.isoformat(),
                    kind="reminder",
                    region=candidate.region,
                ):
                    created += 1
        return created

    async def dispatch_pending(
        self, messaging: MessagingPort, *, now: datetime | None = None
    ) -> int:
        return await dispatch_pending(self._store, messaging, stream=STREAM, now=now)

    async def tick(
        self, messaging: MessagingPort, *, now: datetime | None = None
    ) -> int:
        self.plan_due(now=now)
        return await self.dispatch_pending(messaging, now=now)

    @staticmethod
    def _event_prefix(managed_target: ManagedTarget, document_id: str) -> str:
        return f"{STREAM}:{managed_target.target.target_id}:{document_id}:"


def _to_entry(item: ClassifiedEventCandidate):
    candidate = item.candidate
    from .source_models import CalendarEntry

    return CalendarEntry(
        document_id=candidate.document_id,
        title=candidate.title,
        url=candidate.url or "",
        classification_label=item.classification_label,
        routing_label=item.routing_label,
        start_at=candidate.start_at,
        end_at=candidate.end_at,
        evidence_text=candidate.evidence_text,
        region=candidate.region,
        classification=item.classification,
    )


def _entry_lines(entry, time_label: str) -> tuple[str, ...]:
    start = datetime.fromisoformat(entry.start_at)
    end = datetime.fromisoformat(entry.end_at)
    return (
        f"{_region_prefix(entry.region)}-{entry.title}",
        f"开始时间: {start:%Y/%m/%d %H:%M}",
        f"结束时间: {end:%Y/%m/%d %H:%M}",
    )


def _parse(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("维护时间必须包含时区")
    return moment.astimezone(timezone.utc)


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    return value
