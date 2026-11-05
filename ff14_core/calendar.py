"""活动摘要查询、渲染和持久投递工作流。"""

from __future__ import annotations

from dataclasses import replace
import json
from .freshness import evaluate_freshness
from .diagnostics import diagnostic
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .messaging import MessagingPort, TargetRef
from .delivery import dispatch_pending
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
        self, target: TargetRef, *, now: datetime | None = None, automatic: bool = False
    ) -> ActivityDigest:
        policy = self._store.get_activity_digest_policy(target.target_id)
        current = _require_aware(now or datetime.now(timezone.utc))
        local_now = current.astimezone(ZoneInfo(policy.timezone))
        window_start = local_now - timedelta(minutes=policy.ending_window_minutes)
        window_end = local_now + timedelta(minutes=policy.upcoming_window_minutes)
        entries = tuple(entry for region in policy.selected_regions for entry in self._store.get_calendar_entries(
            window_start.isoformat(), window_end.isoformat(), region=region,
            classification_codes=policy.classification_codes, use_global_categories=False, include_window_end=True,
        ))
        ongoing: list[CalendarEntry] = []
        upcoming: list[CalendarEntry] = []
        ending: list[CalendarEntry] = []
        excluded = 0
        settings = self._store.get_runtime_settings()
        source_warnings = tuple(w for region in policy.selected_regions for w in self._source_warnings(region,current))
        matched = 0
        for entry in entries:
            state = evaluate_freshness(self._store.get_event_verification(entry.source_id,entry.document_id),current,settings)
            entry = replace(entry,freshness_state=state.state,last_verified_at=state.last_success_at,age_seconds=state.age_seconds,verification_warning=state.warning)
            start = datetime.fromisoformat(entry.start_at).astimezone(local_now.tzinfo)
            end = datetime.fromisoformat(entry.end_at).astimezone(local_now.tzinfo)
            relevant = start <= local_now < end or local_now < start <= local_now+timedelta(minutes=policy.upcoming_window_minutes)
            if relevant:matched+=1
            if relevant and automatic and state.state in {'stale','unverified'}:
                excluded+=1
                diagnostic(stage="freshness",source_id=entry.source_id,document_id=entry.document_id,target_id=target.target_id,action="exclude",error=state.state)
                continue
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
            warnings=source_warnings,
            excluded_count=excluded,
            unavailable=(automatic and matched>0 and excluded==matched) or (matched==0 and bool(source_warnings)),
        )

    def _source_warnings(self, region: str, now: datetime) -> tuple[str, ...]:
        source_ids = ("china_news_api", "china_news_detail") if region == "cn" else ("lodestone_atom","lodestone_topics")
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
        return tuple(f"{_region_prefix(region)} {warning}" for warning in dict.fromkeys(warnings))


class ActivityDigestRenderer:
    """渲染与 QQ 无关的紧凑纯文本摘要。"""

    def render(self, digest: ActivityDigest) -> str:
        lines = ["FF14 活动摘要"]
        lines.extend(f"数据源提示：{warning}" for warning in digest.warnings)
        regions = tuple(dict.fromkeys(e.region for e in (*digest.ongoing,*digest.upcoming,*digest.ending)))
        for region in regions:
            lines.append(f"【区域 {_region_prefix(region)}】")
            self._append_section(lines, "正在进行的活动", tuple(e for e in digest.ongoing if e.region==region), "进行至")
            self._append_section(lines, "即将开始的活动", tuple(e for e in digest.upcoming if e.region==region), "开始时间")
            self._append_section(lines, "即将结束的活动", tuple(e for e in digest.ending if e.region==region), "结束时间")
        if not digest.ongoing and not digest.upcoming and not digest.ending:
            lines.append("当前数据尚未核实，无法确认是否没有活动。" if digest.unavailable else "当前时间窗口内没有符合本群策略的活动。")
        if digest.excluded_count:
            lines.append(f"另有 {digest.excluded_count} 项信息因过旧或未核实未展示。")
        return "\n".join(lines)

    @staticmethod
    def split(text: str, *, maximum_length: int = 900) -> tuple[str, ...]:
        if maximum_length < 80:
            raise ValueError("maximum_length must be at least 80")
        if len(text) <= maximum_length:
            return (text,)
        chunks: list[str] = []
        remaining = text
        while len(remaining) > maximum_length:
            boundary = remaining.rfind("\n", 0, maximum_length + 1)
            if boundary > 0:
                chunks.append(remaining[:boundary])
                remaining = remaining[boundary + 1:]
            else:
                chunks.append(remaining[:maximum_length])
                remaining = remaining[maximum_length:]
        if remaining:
            chunks.append(remaining)
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
            moment = datetime.fromisoformat(timestamp).astimezone(ZoneInfo("Asia/Shanghai"))
            display_time = f"{moment.year}/{moment.month}/{moment.day}" if entry.precision=="day" else f"{moment.year}/{moment.month}/{moment.day} {moment:%H:%M}"
            lines.append(f"{_region_prefix(entry.region)}-{entry.title}")
            lines.append(f"{time_label}: {display_time} 北京时间")
            if entry.verification_warning:
                lines.append("核实提示："+entry.verification_warning)
            elif entry.last_verified_at:
                lines.append("最近核实："+entry.last_verified_at)
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
            window_key = scheduled.date().isoformat()
            digest = self._query_service.query(managed_target.target,now=current,automatic=True)
            has_entries = bool(digest.ongoing or digest.upcoming or digest.ending)
            episode = self._store.update_digest_incident(managed_target.target.target_id,digest.unavailable,now=current)
            if self._store.digest_window_started(managed_target.target.target_id,window_key):
                continue
            if not has_entries and not digest.unavailable:
                self._store.prune_digest_pages(managed_target.target.target_id,window_key,0)
                continue
            if digest.unavailable:
                self._store.prune_digest_pages(managed_target.target.target_id,window_key,0)
                # One persistent key per continuous outage, including restarts and uncertain sends.
                message = "FF14 活动摘要暂停：全部候选过旧、未核实或来源不可用。恢复有效核实后自动恢复。"
                pause_key = "pause:"+str(episode)
            else:
                message = self._renderer.render(digest)
            regional_pages=[]
            displayed=(*digest.ongoing,*digest.upcoming,*digest.ending)
            if not digest.unavailable:
                for region in dict.fromkeys(e.region for e in displayed):
                    selected=tuple(e for e in displayed if e.region==region)
                    part=replace(digest,ongoing=tuple(e for e in digest.ongoing if e.region==region),upcoming=tuple(e for e in digest.upcoming if e.region==region),ending=tuple(e for e in digest.ending if e.region==region))
                    regional_pages.extend((text,(region,),selected) for text in self._renderer.split(self._renderer.render(part)))
            else:
                regional_pages.extend((text,policy.selected_regions,()) for text in self._renderer.split(message))
            pages=tuple(text for text,_,_ in regional_pages)
            if not digest.unavailable:
                self._store.prune_digest_pages(managed_target.target.target_id, window_key, len(pages))
            expires_at = None if digest.unavailable else (scheduled + timedelta(days=1)).replace(hour=0, minute=0).astimezone(timezone.utc).isoformat()
            for page_index, (page,covered_regions,covered_entries) in enumerate(regional_pages):
                business_key = f"{self.STREAM}:{managed_target.target.target_id}:{pause_key if digest.unavailable else window_key}:{page_index}"
                if self._store.create_notification_candidate(
                    business_key=business_key,
                    target_id=managed_target.target.target_id,
                    stream=self.STREAM,
                    window_key=window_key,
                    message_text=page,
                    expires_at=expires_at,
                    region=json.dumps(covered_regions),
                    kind="pause" if digest.unavailable else "digest",
                ):
                    created += 1
                if not digest.unavailable:
                    self._store.save_digest_members(business_key,covered_entries)
        return created

    async def dispatch_pending(
        self, messaging: MessagingPort, *, now: datetime | None = None, stream: str | None = None
    ) -> int:
        return await dispatch_pending(self._store, messaging, stream=stream or self.STREAM, now=now)

    async def tick(self, messaging: MessagingPort, *, now: datetime | None = None) -> int:
        self.plan_due(now=now)
        return await self.dispatch_pending(messaging, stream=self.STREAM, now=now)


def _region_prefix(region: str) -> str:
    return {"cn": "C", "global-na": "NA", "global-eu": "EU", "global-jp": "JP", "global-oc": "OC"}.get(
        region, region.upper()
    )


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    return value
