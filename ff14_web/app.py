"""本地 WebUI：候选活动月视图、分类日历策略与待审核分页。"""

from __future__ import annotations

from calendar import month_name
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ff14_core.config import AppConfig
from ff14_core.source_models import (
    ActivityDigestPolicy,
    CalendarCategoryOption,
    CalendarEntry,
    MaintenancePolicy,
    ManualQueryPolicy,
    ReviewPage,
)
from ff14_core.storage import ManagedTarget, StateStore


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


class CalendarCategoryPolicyUpdate(BaseModel):
    included: bool


class OwnerUpdate(BaseModel):
    owner_user_id: str | None = None


class TargetUpdate(BaseModel):
    enabled: bool


class ManualQueryPolicyUpdate(BaseModel):
    activity_enabled: bool
    maintenance_enabled: bool


class DeliveryCloseRequest(BaseModel):
    confirm: bool


class ActivityPolicyUpdate(BaseModel):
    enabled: bool
    region: str = "cn"
    regions: list[str] | None = None
    classification_codes: list[str] = Field(default_factory=list)
    digest_hour: int = 20
    digest_minute: int = 0
    upcoming_window_minutes: int = 7 * 24 * 60
    ending_window_minutes: int = 1440


class MaintenancePolicyUpdate(BaseModel):
    enabled: bool
    region: str = "cn"
    regions: list[str] | None = None
    notify_announcements: bool = True
    reminder_window_minutes: int = 24 * 60
    notify_updates: bool = True


CLASSIFICATION_OPTIONS = (
    ("seasonal_event", "季节活动"),
    ("collaboration_event", "联动活动"),
    ("operational_campaign", "运营活动"),
    ("live_broadcast", "直播活动"),
    ("other_operational", "其他运营活动"),
)

UPCOMING_DIGEST_WINDOW_OPTIONS = (
    (0, "不显示"),
    (7 * 24 * 60, "7 天内"),
    (14 * 24 * 60, "14 天内"),
    (30 * 24 * 60, "30 天内"),
)

ENDING_DIGEST_WINDOW_OPTIONS = (
    (0, "不显示"),
    (60, "1 小时内"),
    (180, "3 小时内"),
    (360, "6 小时内"),
    (720, "12 小时内"),
    (1440, "24 小时内"),
    (2880, "48 小时内"),
)

MAINTENANCE_REMINDER_OPTIONS = (
    (0, "不提醒"),
    (60, "开始前 1 小时"),
    (360, "开始前 6 小时"),
    (24 * 60, "开始前 24 小时"),
)

MAINTENANCE_REMINDER_VALUES = frozenset(minutes for minutes, _ in MAINTENANCE_REMINDER_OPTIONS)

UPCOMING_DIGEST_WINDOW_VALUES = frozenset(minutes for minutes, _ in UPCOMING_DIGEST_WINDOW_OPTIONS)


@dataclass(frozen=True)
class CalendarMonth:
    first_day: date
    next_month: date

    @property
    def previous_month(self) -> date:
        return (self.first_day - timedelta(days=1)).replace(day=1)

    @property
    def label(self) -> str:
        return f"{self.first_day.year} 年 {self.first_day.month} 月"


def create_app(store: StateStore | None = None) -> FastAPI:
    if store is None:
        config = AppConfig.for_workspace(WORKSPACE_ROOT)
        store = StateStore(config.database_path)
        store.initialize()

    app = FastAPI(title="FF14 Bot 管理台", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def calendar_page(month: str | None = None) -> HTMLResponse:
        calendar_month = _parse_month(month)
        entries = store.get_calendar_entries(
            _day_start(calendar_month.first_day),
            _day_start(calendar_month.next_month),
        )
        options = store.get_calendar_category_options()
        return HTMLResponse(
            _page_shell(
                title="活动日历",
                body=_render_calendar(calendar_month, entries, options),
            )
        )

    @app.get("/review", response_class=HTMLResponse)
    def review_page(page: int = 1) -> HTMLResponse:
        try:
            review = store.get_review_page(page=page, page_size=12)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return HTMLResponse(
            _page_shell(title="待审核活动", body=_render_review(review))
        )

    @app.get("/access", response_class=HTMLResponse)
    def access_page() -> HTMLResponse:
        return HTMLResponse(
            _page_shell(
                title="群授权",
                body=_render_access_settings(
                    store, store.get_owner_user_id(), store.list_targets()
                ),
            )
        )

    @app.get("/deliveries", response_class=HTMLResponse)
    def deliveries_page() -> HTMLResponse:
        labels = {"pending": "待发送", "sending": "发送中", "uncertain": "结果未知，需核查",
                  "sent": "已确认成功", "failed": "未成功／已关闭", "superseded": "已停止投递"}
        entries = []
        for record in store.list_deliveries():
            action = (f'<button data-close-delivery="{escape(record.notification_id, quote=True)}">关闭记录（不重发）</button>'
                      if record.status == "uncertain" else "")
            entries.append(
                f'<article class="review-entry"><h2>{escape(record.target_name)} · '
                f'{escape(labels[record.status])}</h2><p>{escape(record.stream)} · {record.attempts} 次尝试</p>'
                f'<p>{escape(record.last_error or "")}</p><pre style="white-space:pre-wrap">'
                f'{escape(record.message_text)}</pre>{action}</article>'
            )
        body = ('<main class="app-shell"><header class="topbar"><h1>投递记录</h1>'
                '<nav class="topnav"><a href="/settings">运行设置</a><a href="/articles">文章版本</a><a href="/news">公告</a><a href="/deliveries">投递记录</a><a href="/access">群授权</a><a href="/">日历</a></nav></header>'
                '<p>最近 100 条记录，未知结果优先显示。请先核对群内消息；关闭记录会保留历史，'
                '不会重发，也不会将未知结果记为成功。</p>'
                + ("".join(entries) or '<p>暂无投递记录</p>') + '</main><script>' + _DELIVERY_SCRIPT + '</script>')
        return HTMLResponse(_page_shell("投递记录", body))

    @app.put("/api/deliveries/{notification_id}/close")
    def close_delivery(notification_id: str, update: DeliveryCloseRequest) -> dict[str, bool]:
        if not update.confirm:
            raise HTTPException(status_code=422, detail="需要明确确认关闭记录")
        if not store.close_uncertain_delivery(notification_id):
            raise HTTPException(status_code=409, detail="记录不存在或已不再是未知结果")
        return {"closed": True}

    @app.put("/api/access-settings/groups/{group_id}/query-policy")
    def update_query_policy(group_id: str, update: ManualQueryPolicyUpdate) -> dict[str, bool]:
        normalized = _normalize_numeric_id(group_id, label="QQ群号")
        target = next((item.target for item in store.list_targets()
                       if item.target.provider_target_id == normalized), None)
        if target is None:
            raise HTTPException(status_code=404, detail="未找到该服务群")
        policy = ManualQueryPolicy(update.activity_enabled, update.maintenance_enabled)
        store.set_manual_query_policy(target.target_id, policy)
        return {"activity_enabled": policy.activity_enabled, "maintenance_enabled": policy.maintenance_enabled}

    @app.get("/api/calendar-categories")
    def calendar_categories() -> list[dict[str, object]]:
        return [_option_json(option) for option in store.get_calendar_category_options()]

    @app.put("/api/calendar-categories/{classification_label}")
    def update_calendar_category(
        classification_label: str,
        update: CalendarCategoryPolicyUpdate,
    ) -> dict[str, object]:
        try:
            store.set_calendar_category_included(classification_label, update.included)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"classification_label": classification_label, "included": update.included}

    @app.get("/api/access-settings")
    def access_settings() -> dict[str, object]:
        return {
            "owner_user_id": store.get_owner_user_id(),
            "targets": [
                _target_json(
                    target,
                    store.get_activity_digest_policy(target.target.target_id),
                    store.get_maintenance_policy(target.target.target_id),
                    store.get_manual_query_policy(target.target.target_id),
                )
                for target in store.list_targets()
            ],
        }

    @app.put("/api/access-settings/owner")
    def update_owner(update: OwnerUpdate) -> dict[str, str | None]:
        owner_user_id = _normalize_numeric_id(update.owner_user_id, label="所有者 QQ 号")
        store.set_owner_user_id(owner_user_id)
        return {"owner_user_id": owner_user_id}

    @app.put("/api/access-settings/groups/{group_id}")
    def save_group(group_id: str, update: TargetUpdate) -> dict[str, object]:
        normalized_group_id = _normalize_numeric_id(group_id, label="QQ群号")
        if normalized_group_id is None:
            raise HTTPException(status_code=422, detail="QQ群号不能为空")
        target = store.save_onebot_group(normalized_group_id, enabled=update.enabled)
        return _target_json(
            ManagedTarget(target=target, enabled=update.enabled),
            store.get_activity_digest_policy(target.target_id),
            store.get_maintenance_policy(target.target_id),
            store.get_manual_query_policy(target.target_id),
        )

    @app.put("/api/access-settings/groups/{group_id}/activity-policy")
    def update_activity_policy(
        group_id: str, update: ActivityPolicyUpdate
    ) -> dict[str, object]:
        normalized_group_id = _normalize_numeric_id(group_id, label="QQ群号")
        if normalized_group_id is None:
            raise HTTPException(status_code=422, detail="QQ群号不能为空")
        if any(r not in {"cn", "global-na"} for r in (update.regions if update.regions is not None else [update.region])):
            raise HTTPException(status_code=422, detail="不支持的活动区域")
        if not 0 <= update.digest_hour <= 23 or not 0 <= update.digest_minute <= 59:
            raise HTTPException(status_code=422, detail="摘要时间无效")
        target = next(
            (
                item.target
                for item in store.list_targets()
                if item.target.provider_target_id == normalized_group_id
            ),
            None,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="未找到该服务群")
        if any(
            code not in {item[0] for item in CLASSIFICATION_OPTIONS}
            for code in update.classification_codes
        ):
            raise HTTPException(status_code=422, detail="存在不支持的活动分类")
        old = store.get_activity_digest_policy(target.target_id)
        if update.upcoming_window_minutes not in UPCOMING_DIGEST_WINDOW_VALUES and update.upcoming_window_minutes != old.upcoming_window_minutes:
            raise HTTPException(status_code=422, detail="开始前窗口必须为 0、7 天、14 天或 30 天")
        if update.ending_window_minutes < 0:
            raise HTTPException(status_code=422, detail="活动时间窗口不能为负数")
        old = store.get_activity_digest_policy(target.target_id)
        classification_codes = tuple(dict.fromkeys(update.classification_codes))
        if set(classification_codes) == {code for code, _ in CLASSIFICATION_OPTIONS}:
            classification_codes = ()
        policy = ActivityDigestPolicy(
            enabled=update.enabled,
            region=update.regions[0] if update.regions else update.region,
            regions=tuple(update.regions) if update.regions is not None else None,
            classification_codes=classification_codes,
            timezone=old.timezone,
            digest_hour=update.digest_hour,
            digest_minute=update.digest_minute,
            upcoming_window_minutes=update.upcoming_window_minutes,
            ending_window_minutes=update.ending_window_minutes,
        )
        store.set_activity_digest_policy(target.target_id, policy)
        return _policy_json(policy)

    @app.put("/api/access-settings/groups/{group_id}/maintenance-policy")
    def update_maintenance_policy(
        group_id: str, update: MaintenancePolicyUpdate
    ) -> dict[str, object]:
        normalized_group_id = _normalize_numeric_id(group_id, label="QQ群号")
        if normalized_group_id is None:
            raise HTTPException(status_code=422, detail="QQ群号不能为空")
        if any(r not in {"cn", "global-na"} for r in (update.regions if update.regions is not None else [update.region])):
            raise HTTPException(status_code=422, detail="不支持的维护区域")
        if update.reminder_window_minutes not in MAINTENANCE_REMINDER_VALUES:
            raise HTTPException(status_code=422, detail="维护提前提醒必须为 0、1 小时、6 小时或 24 小时")
        target = next(
            (
                item.target
                for item in store.list_targets()
                if item.target.provider_target_id == normalized_group_id
            ),
            None,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="未找到该服务群")
        old = store.get_maintenance_policy(target.target_id)
        policy = MaintenancePolicy(
            enabled=update.enabled,
            region=update.regions[0] if update.regions else update.region,
            regions=tuple(update.regions) if update.regions is not None else None,
            timezone=old.timezone,
            notify_announcements=update.notify_announcements,
            reminder_window_minutes=update.reminder_window_minutes,
            notify_updates=update.notify_updates,
            watermark_at=old.watermark_at,
        )
        store.set_maintenance_policy(target.target_id, policy)
        return _maintenance_policy_json(policy)

    @app.delete("/api/access-settings/groups/{group_id}")
    def delete_group(group_id: str) -> dict[str, bool]:
        normalized_group_id = _normalize_numeric_id(group_id, label="QQ群号")
        if normalized_group_id is None or not store.delete_onebot_group(normalized_group_id):
            raise HTTPException(status_code=404, detail="未找到该服务群")
        return {"deleted": True}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    from .p3 import install_p3_routes
    install_p3_routes(app, store, _page_shell)
    return app


def _parse_month(value: str | None) -> CalendarMonth:
    if value is None:
        today = date.today()
        first_day = today.replace(day=1)
    else:
        try:
            first_day = datetime.strptime(value, "%Y-%m").date().replace(day=1)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="month 必须为 YYYY-MM") from error
    next_month = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return CalendarMonth(first_day=first_day, next_month=next_month)


def _day_start(value: date) -> str:
    return f"{value.isoformat()}T00:00:00+08:00"


def _render_calendar(
    calendar_month: CalendarMonth,
    entries: tuple[CalendarEntry, ...],
    options: tuple[CalendarCategoryOption, ...],
) -> str:
    entries_by_day: dict[date, list[CalendarEntry]] = {}
    for entry in entries:
        event_start = datetime.fromisoformat(entry.start_at).date()
        event_end = datetime.fromisoformat(entry.end_at).date()
        visible_start = max(event_start, calendar_month.first_day)
        visible_end = min(event_end, calendar_month.next_month - timedelta(days=1))
        current_day = visible_start
        while current_day <= visible_end:
            entries_by_day.setdefault(current_day, []).append(entry)
            current_day += timedelta(days=1)

    first_visible_day = calendar_month.first_day - timedelta(days=calendar_month.first_day.weekday())
    last_month_day = calendar_month.next_month - timedelta(days=1)
    last_visible_day = last_month_day + timedelta(days=6 - last_month_day.weekday())
    days: list[str] = []
    current_day = first_visible_day
    while current_day <= last_visible_day:
        day_events = entries_by_day.get(current_day, [])
        event_html = "".join(_render_calendar_entry(entry, current_day) for entry in day_events)
        classes = "day-cell"
        if current_day.month != calendar_month.first_day.month:
            classes += " outside-month"
        if current_day == date.today():
            classes += " today"
        days.append(
            f'<section class="{classes}"><div class="day-number">{current_day.day}</div>{event_html}</section>'
        )
        current_day += timedelta(days=1)

    previous = calendar_month.previous_month.strftime("%Y-%m")
    following = calendar_month.next_month.strftime("%Y-%m")
    return f"""
      <main class="app-shell">
        <header class="topbar">
          <a class="brand" href="/">FF14 活动日历</a>
          <nav class="topnav"><a href="/settings">运行设置</a><a href="/articles">文章版本</a><a href="/news">公告</a><a href="/deliveries">投递记录</a><a href="/access">群授权</a></nav>
        </header>
        <div class="content-layout">
          <section class="calendar-panel">
            <header class="calendar-toolbar">
              <h1>{calendar_month.label}</h1>
              <div class="month-actions">
                <a class="icon-button" href="/?month={previous}" aria-label="上个月">&larr;</a>
                <a class="icon-button" href="/?month={following}" aria-label="下个月">&rarr;</a>
              </div>
            </header>
            <div class="weekday-row"><span>周一</span><span>周二</span><span>周三</span><span>周四</span><span>周五</span><span>周六</span><span>周日</span></div>
            <div class="month-grid">{''.join(days)}</div>
          </section>
          <aside class="policy-panel">
            <h2>月历显示分类</h2><p>仅影响本页显示；群摘要使用各群的推送分类。</p>
            <div class="category-list">{''.join(_render_category_option(option) for option in options) or '<p class="empty-state">暂无活动候选</p>'}</div>
          </aside>
        </div>
      </main>
      <script>{_POLICY_SCRIPT}</script>
    """


def _render_calendar_entry(entry: CalendarEntry, current_day: date) -> str:
    start_day = datetime.fromisoformat(entry.start_at).date()
    start_time = datetime.fromisoformat(entry.start_at).strftime("%H:%M")
    return (
        f'<a class="calendar-entry category-{_color_key(entry.classification_label)}" '
        f'href="{escape(entry.url, quote=True)}" target="_blank" rel="noreferrer" '
        f'title="{escape(entry.evidence_text, quote=True)}">'
        f'<span>{start_time if current_day == start_day else ""}</span>{escape(entry.title)}</a>'
    )


def _render_category_option(option: CalendarCategoryOption) -> str:
    option_id = _category_dom_id(option.classification_label)
    checked = " checked" if option.included else ""
    return (
        '<label class="category-option" for="' + option_id + '">'
        f'<input id="{option_id}" type="checkbox" data-category-label="{escape(option.classification_label, quote=True)}"{checked}>'
        f'<span class="category-swatch category-{_color_key(option.classification_label)}"></span>'
        f'<span class="category-name">{escape(option.classification_label)}</span>'
        f'<span class="category-count">{option.candidate_count}</span>'
        '</label>'
    )


def _category_dom_id(classification_label: str) -> str:
    """为分类控件生成稳定且不依赖颜色的唯一 DOM id。"""
    return f"category-{quote(classification_label, safe='')}"


def _render_review(review: ReviewPage) -> str:
    total_pages = max(1, (review.total_count + review.page_size - 1) // review.page_size)
    previous = max(1, review.page - 1)
    following = min(total_pages, review.page + 1)
    rows = "".join(_render_review_entry(entry) for entry in review.entries)
    if not rows:
        rows = '<p class="empty-state">暂无待审核活动</p>'
    return f"""
      <main class="app-shell review-shell">
        <header class="topbar">
          <a class="brand" href="/">FF14 活动日历</a>
          <nav class="topnav"><a href="/settings">运行设置</a><a href="/articles">文章版本</a><a href="/news">公告</a><a href="/deliveries">投递记录</a><a href="/">日历</a><a href="/access">群授权</a></nav>
        </header>
        <section class="review-panel">
          <header class="review-toolbar"><h1>待审核活动</h1><span>{review.total_count} 条</span></header>
          <div class="review-list">{rows}</div>
          <nav class="pagination" aria-label="待审核分页">
            <a href="/review?page={previous}"{' aria-disabled="true"' if review.page == 1 else ''}>&larr; 上一页</a>
            <span>{review.page} / {total_pages}</span>
            <a href="/review?page={following}"{' aria-disabled="true"' if review.page == total_pages else ''}>下一页 &rarr;</a>
          </nav>
        </section>
      </main>
    """


def _render_review_entry(entry: CalendarEntry) -> str:
    return f"""
      <article class="review-entry">
        <div class="review-meta"><span class="tag category-{_color_key(entry.classification_label)}">{escape(entry.classification_label)}</span><span>{escape(entry.routing_label)}</span></div>
        <h2><a href="{escape(entry.url, quote=True)}" target="_blank" rel="noreferrer">{escape(entry.title)}</a></h2>
        <time>{_format_range(entry.start_at, entry.end_at)}</time>
        <p>{escape(entry.evidence_text)}</p>
      </article>
    """


def _render_access_settings(
    store: StateStore,
    owner_user_id: str | None,
    targets: tuple[ManagedTarget, ...],
) -> str:
    owner_value = escape(owner_user_id or "", quote=True)
    target_rows = "".join(
        _render_target_row(
            target,
            store.get_activity_digest_policy(target.target.target_id),
            store.get_maintenance_policy(target.target.target_id),
            store.get_manual_query_policy(target.target.target_id),
        )
        for target in targets
    )
    if not target_rows:
        target_rows = '<p class="empty-state">尚未添加服务群</p>'
    return f"""
      <main class="app-shell access-shell">
        <header class="topbar">
          <a class="brand" href="/">FF14 活动日历</a>
          <nav class="topnav"><a href="/settings">运行设置</a><a href="/articles">文章版本</a><a href="/news">公告</a><a href="/deliveries">投递记录</a><a href="/">日历</a></nav>
        </header>
        <section class="access-panel">
          <header class="access-header"><h1>群授权</h1></header>
          <section class="setting-section">
            <h2>Bot 所有者</h2>
            <div class="setting-row">
              <label class="field-label" for="owner-user-id">QQ 号</label>
              <input id="owner-user-id" inputmode="numeric" autocomplete="off" value="{owner_value}">
              <button id="save-owner" type="button">保存</button>
            </div>
          </section>
          <section class="setting-section">
            <header class="section-toolbar"><h2>服务群白名单</h2></header><p>群员查询与主动推送独立配置。移除服务群会保留投递历史，并关闭该群策略。</p>
            <form id="add-group" class="setting-row">
              <label class="field-label" for="group-id">QQ群号</label>
              <input id="group-id" inputmode="numeric" autocomplete="off" required>
              <label class="inline-toggle" for="new-group-enabled"><input id="new-group-enabled" type="checkbox" checked> 启用</label>
              <button type="submit">添加</button>
            </form>
            <div class="target-list">{target_rows}</div>
          </section>
          <p id="access-message" class="access-message" role="status"></p>
        </section>
      </main>
      <script>{_ACCESS_SCRIPT}</script>
    """


def _render_target_row(
    managed_target: ManagedTarget,
    policy: ActivityDigestPolicy,
    maintenance_policy: MaintenancePolicy,
    query_policy: ManualQueryPolicy,
) -> str:
    group_id = managed_target.target.provider_target_id
    encoded_group_id = escape(group_id, quote=True)
    checked = " checked" if managed_target.enabled else ""
    digest_checked = " checked" if policy.enabled else ""
    maintenance_checked = " checked" if maintenance_policy.enabled else ""
    maintenance_announcements_checked = " checked" if maintenance_policy.notify_announcements else ""
    maintenance_updates_checked = " checked" if maintenance_policy.notify_updates else ""
    digest_time = f"{policy.digest_hour:02d}:{policy.digest_minute:02d}"
    region_options = "".join(f'<label><input type="checkbox" data-digest-region value="{r}"{" checked" if r in policy.selected_regions else ""}>{label}</label>' for r,label in (("cn","国服"),("global-na","国际服 NA")))
    classification_options = "".join(
        f'<label class="classification-toggle"><input type="checkbox" data-digest-classification value="{code}"'
        f'{" checked" if not policy.classification_codes or code in policy.classification_codes else ""}> {label}</label>'
        for code, label in CLASSIFICATION_OPTIONS
    )
    upcoming_options = _render_digest_window_options(
        policy.upcoming_window_minutes, UPCOMING_DIGEST_WINDOW_OPTIONS
    )
    ending_options = _render_digest_window_options(
        policy.ending_window_minutes, ENDING_DIGEST_WINDOW_OPTIONS
    )
    maintenance_region_options = "".join(f'<label><input type="checkbox" data-maintenance-region value="{r}"{" checked" if r in maintenance_policy.selected_regions else ""}>{label}</label>' for r,label in (("cn","国服"),("global-na","国际服 NA")))
    maintenance_reminder_options = _render_digest_window_options(
        maintenance_policy.reminder_window_minutes, MAINTENANCE_REMINDER_OPTIONS
    )
    return f"""
      <div class="target-row" data-group-id="{encoded_group_id}">
        <div class="target-summary">
          <span class="target-id">{escape(group_id)}</span>
          <label class="inline-toggle"><input type="checkbox" data-target-enabled{checked}> 启用服务</label>
          <label class="inline-toggle"><input type="checkbox" data-digest-enabled{digest_checked}> 推送活动摘要</label>
          <label class="inline-toggle"><input type="checkbox" data-maintenance-enabled{maintenance_checked}> 推送维护提醒</label>
          <label class="inline-toggle"><input type="checkbox" data-query-activity{" checked" if query_policy.activity_enabled else ""}> 群员活动查询</label>
          <label class="inline-toggle"><input type="checkbox" data-query-maintenance{" checked" if query_policy.maintenance_enabled else ""}> 群员维护查询</label>
          <button type="button" class="remove-target" title="移除服务群" aria-label="移除服务群">移除</button>
        </div>
        <div class="digest-settings">
          <label class="digest-field">活动区域（可多选）{region_options}</label>
          <label class="digest-field">发送时间<input type="time" data-digest-time value="{digest_time}"></label>
          <fieldset class="digest-field classification-fields"><legend>推送分类</legend><div class="classification-list">{classification_options}</div><small class="field-hint">全部勾选或全部取消都表示全部分类</small></fieldset>
          <label class="digest-field">开始前<select data-digest-upcoming>{upcoming_options}</select></label>
          <label class="digest-field">结束前<select data-digest-ending>{ending_options}</select></label>
        </div>
        <div class="digest-settings maintenance-settings">
          <label class="digest-field">维护区域（可多选）{maintenance_region_options}</label>
          <label class="inline-toggle"><input type="checkbox" data-maintenance-announcements{maintenance_announcements_checked}> 推送维护公告</label>
          <label class="digest-field">提前提醒<select data-maintenance-reminder>{maintenance_reminder_options}</select></label>
          <label class="inline-toggle"><input type="checkbox" data-maintenance-updates{maintenance_updates_checked}> 推送时间变更</label>
        </div>
      </div>
    """


def _render_digest_window_options(
    selected_minutes: int, options: tuple[tuple[int, str], ...]
) -> str:
    if selected_minutes not in {minutes for minutes, _ in options}:
        options = (*options, (selected_minutes, f"{selected_minutes} 分钟内"))
    return "".join(
        f'<option value="{minutes}"{" selected" if minutes == selected_minutes else ""}>{label}</option>'
        for minutes, label in options
    )


def _format_range(start_at: str, end_at: str) -> str:
    start = datetime.fromisoformat(start_at)
    end = datetime.fromisoformat(end_at)
    return f"{start:%Y-%m-%d %H:%M} 至 {end:%Y-%m-%d %H:%M}"


def _color_key(value: str) -> str:
    palette = ("coral", "teal", "gold", "rose", "green", "blue")
    return palette[sum(ord(character) for character in value) % len(palette)]


def _option_json(option: CalendarCategoryOption) -> dict[str, object]:
    return {
        "classification_label": option.classification_label,
        "candidate_count": option.candidate_count,
        "included": option.included,
    }


def _target_json(
    managed_target: ManagedTarget,
    policy: ActivityDigestPolicy,
    maintenance_policy: MaintenancePolicy,
    query_policy: ManualQueryPolicy,
) -> dict[str, object]:
    return {
        "provider": managed_target.target.provider,
        "target_type": managed_target.target.target_type,
        "group_id": managed_target.target.provider_target_id,
        "enabled": managed_target.enabled,
        "activity_digest": _policy_json(policy),
        "maintenance": _maintenance_policy_json(maintenance_policy),
        "manual_queries": {"activity_enabled": query_policy.activity_enabled,
                           "maintenance_enabled": query_policy.maintenance_enabled},
    }


def _policy_json(policy: ActivityDigestPolicy) -> dict[str, object]:
    return {
        "enabled": policy.enabled,
        "region": policy.region,
        "regions": list(policy.selected_regions),
        "digest_hour": policy.digest_hour,
        "digest_minute": policy.digest_minute,
        "timezone": policy.timezone,
        "classification_codes": list(policy.classification_codes),
        "upcoming_window_minutes": policy.upcoming_window_minutes,
        "ending_window_minutes": policy.ending_window_minutes,
    }


def _maintenance_policy_json(policy: MaintenancePolicy) -> dict[str, object]:
    return {
        "enabled": policy.enabled,
        "region": policy.region,
        "regions": list(policy.selected_regions),
        "timezone": policy.timezone,
        "notify_announcements": policy.notify_announcements,
        "reminder_window_minutes": policy.reminder_window_minutes,
        "notify_updates": policy.notify_updates,
    }


def _normalize_numeric_id(value: str | None, *, label: str) -> str | None:
    normalized = value.strip() if value is not None else ""
    if not normalized:
        return None
    if not normalized.isascii() or not normalized.isdecimal():
        raise HTTPException(status_code=422, detail=f"{label}必须为纯数字")
    return normalized


def _page_shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} | FF14 Bot</title>
  <style>{_STYLES}</style>
</head>
<body>{body}</body>
</html>"""


_POLICY_SCRIPT = """
document.querySelectorAll('[data-category-label]').forEach((input) => {
  input.addEventListener('change', async () => {
    input.disabled = true;
    try {
      const response = await fetch('/api/calendar-categories/' + encodeURIComponent(input.dataset.categoryLabel), {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({included: input.checked}),
      });
      if (!response.ok) throw new Error('update failed');
      window.location.reload();
    } catch (error) {
      input.checked = !input.checked;
      input.disabled = false;
    }
  });
});
"""


_ACCESS_SCRIPT = """
const message = document.querySelector('#access-message');
const showMessage = (text, isError = false) => {
  message.textContent = text;
  message.classList.toggle('error', isError);
};

document.querySelector('#save-owner')?.addEventListener('click', async () => {
  const button = document.querySelector('#save-owner');
  const ownerUserId = document.querySelector('#owner-user-id').value.trim();
  button.disabled = true;
  try {
    const response = await fetch('/api/access-settings/owner', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({owner_user_id: ownerUserId || null}),
    });
    if (!response.ok) throw new Error(await response.text());
    showMessage(ownerUserId ? '所有者已保存' : '所有者已清空');
  } catch (error) {
    showMessage('无法保存所有者，请检查 QQ 号', true);
  } finally {
    button.disabled = false;
  }
});

document.querySelector('#add-group')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const groupId = document.querySelector('#group-id').value.trim();
  const enabled = document.querySelector('#new-group-enabled').checked;
  try {
    const response = await fetch('/api/access-settings/groups/' + encodeURIComponent(groupId), {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled}),
    });
    if (!response.ok) throw new Error(await response.text());
    window.location.reload();
  } catch (error) {
    showMessage('无法保存服务群，请检查群号', true);
  }
});

document.querySelectorAll('[data-target-enabled]').forEach((input) => {
  input.addEventListener('change', async () => {
    const row = input.closest('[data-group-id]');
    input.disabled = true;
    try {
      const response = await fetch('/api/access-settings/groups/' + encodeURIComponent(row.dataset.groupId), {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled: input.checked}),
      });
      if (!response.ok) throw new Error(await response.text());
      showMessage(input.checked ? '服务群已启用' : '服务群已停用');
    } catch (error) {
      input.checked = !input.checked;
      showMessage('无法更新服务群状态', true);
    } finally {
      input.disabled = false;
    }
  });
});

document.querySelectorAll('[data-digest-enabled], [data-digest-region], [data-digest-time], [data-digest-classification], [data-digest-upcoming], [data-digest-ending]').forEach((input) => {
  input.addEventListener('change', async () => {
    const row = input.closest('[data-group-id]');
    const enabled = row.querySelector('[data-digest-enabled]').checked;
    const [hour, minute] = row.querySelector('[data-digest-time]').value.split(':').map(Number);
    const regions = [...row.querySelectorAll('[data-digest-region]:checked')].map(x=>x.value);
    const classification_codes = Array.from(row.querySelectorAll('[data-digest-classification]:checked')).map((checkbox) => checkbox.value);
    const upcoming_window_minutes = Number(row.querySelector('[data-digest-upcoming]').value);
    const ending_window_minutes = Number(row.querySelector('[data-digest-ending]').value);
    input.disabled = true;
    try {
      const response = await fetch('/api/access-settings/groups/' + encodeURIComponent(row.dataset.groupId) + '/activity-policy', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled, regions, classification_codes, digest_hour: hour, digest_minute: minute, upcoming_window_minutes, ending_window_minutes}),
      });
      if (!response.ok) throw new Error(await response.text());
      showMessage(enabled ? '活动摘要策略已启用' : '活动摘要策略已停用');
    } catch (error) {
      showMessage('无法更新活动摘要策略', true);
    } finally {
      input.disabled = false;
    }
  });
});

document.querySelectorAll('[data-maintenance-enabled], [data-maintenance-region], [data-maintenance-announcements], [data-maintenance-reminder], [data-maintenance-updates]').forEach((input) => {
  input.addEventListener('change', async () => {
    const row = input.closest('[data-group-id]');
    const enabled = row.querySelector('[data-maintenance-enabled]').checked;
    const regions = [...row.querySelectorAll('[data-maintenance-region]:checked')].map(x=>x.value);
    const notify_announcements = row.querySelector('[data-maintenance-announcements]').checked;
    const reminder_window_minutes = Number(row.querySelector('[data-maintenance-reminder]').value);
    const notify_updates = row.querySelector('[data-maintenance-updates]').checked;
    input.disabled = true;
    try {
      const response = await fetch('/api/access-settings/groups/' + encodeURIComponent(row.dataset.groupId) + '/maintenance-policy', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled, regions, notify_announcements, reminder_window_minutes, notify_updates}),
      });
      if (!response.ok) throw new Error(await response.text());
      showMessage(enabled ? '维护提醒策略已启用' : '维护提醒策略已停用');
    } catch (error) {
      showMessage('无法更新维护提醒策略', true);
    } finally {
      input.disabled = false;
    }
  });
});

document.querySelectorAll('[data-query-activity], [data-query-maintenance]').forEach((input) => {
  input.addEventListener('change', async () => {
    const row = input.closest('[data-group-id]');
    const controls = row.querySelectorAll('[data-query-activity], [data-query-maintenance]');
    controls.forEach((control) => { control.disabled = true; });
    try {
      const response = await fetch('/api/access-settings/groups/' + encodeURIComponent(row.dataset.groupId) + '/query-policy', {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({activity_enabled: row.querySelector('[data-query-activity]').checked,
                              maintenance_enabled: row.querySelector('[data-query-maintenance]').checked}),
      });
      if (!response.ok) throw new Error('save failed');
      showMessage('群员查询权限已保存');
    } catch (error) {
      input.checked = !input.checked;
      showMessage('无法保存群员查询权限', true);
    } finally {
      controls.forEach((control) => { control.disabled = false; });
    }
  });
});

document.querySelectorAll('.remove-target').forEach((button) => {
  button.addEventListener('click', async () => {
    const row = button.closest('[data-group-id]');
    button.disabled = true;
    try {
      const response = await fetch('/api/access-settings/groups/' + encodeURIComponent(row.dataset.groupId), {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error(await response.text());
      row.remove();
      showMessage('服务群已移除');
    } catch (error) {
      showMessage('无法移除服务群', true);
      button.disabled = false;
    }
  });
});
"""


_STYLES = """
:root { color: #202728; background: #f7f8f5; font-family: "Microsoft YaHei UI", system-ui, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; }
a { color: inherit; text-decoration: none; }
.app-shell { max-width: 1560px; margin: 0 auto; padding: 22px 28px 32px; }
.topbar { display: flex; align-items: center; justify-content: space-between; min-height: 46px; border-bottom: 1px solid #d9dfd8; padding-bottom: 14px; }
.brand { color: #173d39; font-size: 20px; font-weight: 700; }
.topnav { display: flex; gap: 16px; font-size: 14px; color: #475451; }
.topnav span { display: inline-grid; min-width: 20px; height: 20px; place-items: center; margin-left: 4px; border-radius: 50%; background: #df6d54; color: #fff; font-size: 12px; }
.content-layout { display: grid; grid-template-columns: minmax(0, 1fr) 276px; gap: 24px; padding-top: 22px; }
.calendar-toolbar, .review-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
h1, h2, p { margin-top: 0; }
h1 { margin-bottom: 0; font-size: 22px; color: #202b2a; }
h2 { font-size: 15px; color: #2e3a38; }
.month-actions { display: flex; gap: 6px; }
.icon-button { display: grid; width: 34px; height: 34px; place-items: center; border: 1px solid #cbd5cf; border-radius: 6px; color: #254b46; font-size: 20px; }
.weekday-row, .month-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); }
.weekday-row { border: 1px solid #d9dfd8; border-bottom: 0; background: #eef2ed; }
.weekday-row span { padding: 9px 10px; color: #596562; font-size: 13px; font-weight: 650; text-align: right; }
.month-grid { border-top: 1px solid #d9dfd8; border-left: 1px solid #d9dfd8; }
.day-cell { min-height: 126px; overflow: hidden; border-right: 1px solid #d9dfd8; border-bottom: 1px solid #d9dfd8; background: #fff; padding: 7px 6px; }
.day-cell.outside-month { background: #f4f6f2; }
.day-cell.today .day-number { background: #173d39; color: #fff; }
.day-number { display: grid; width: 25px; height: 25px; place-items: center; margin: 0 0 5px auto; border-radius: 50%; color: #41504d; font-size: 12px; }
.calendar-entry { display: block; overflow: hidden; margin: 3px 0; border-left: 3px solid; border-radius: 3px; background: #f4f5f2; padding: 3px 4px; color: #263432; font-size: 12px; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; }
.calendar-entry span { margin-right: 3px; font-variant-numeric: tabular-nums; }
.category-coral { border-color: #d96d55; }.category-teal { border-color: #218d86; }.category-gold { border-color: #c58d24; }.category-rose { border-color: #b25572; }.category-green { border-color: #5c9a62; }.category-blue { border-color: #4b7fc3; }
.policy-panel { align-self: start; border-top: 3px solid #173d39; padding-top: 12px; }
.policy-panel h2 { margin-bottom: 10px; }
.category-list { border-top: 1px solid #d9dfd8; }
.category-option { display: grid; grid-template-columns: 18px 10px minmax(0, 1fr) auto; gap: 8px; align-items: center; min-height: 44px; border-bottom: 1px solid #d9dfd8; color: #35423f; font-size: 14px; cursor: pointer; }
.category-option input { width: 16px; height: 16px; accent-color: #1d776f; }
.category-swatch { width: 9px; height: 9px; border-radius: 50%; }
.category-count { color: #71807b; font-size: 12px; }
.empty-state { color: #71807b; font-size: 14px; }
.review-shell { max-width: 1000px; }.review-panel { padding-top: 26px; }.review-toolbar { border-bottom: 1px solid #d9dfd8; padding-bottom: 13px; }.review-toolbar span { color: #687570; font-size: 14px; }
.review-list { display: grid; gap: 0; }.review-entry { border-bottom: 1px solid #d9dfd8; padding: 18px 0; }.review-meta { display: flex; gap: 9px; color: #6c7774; font-size: 12px; }.tag { border-left: 3px solid; background: #f0f3ef; padding: 2px 6px; border-radius: 3px; color: #3b4745; }.review-entry h2 { margin: 9px 0 6px; font-size: 17px; }.review-entry time { color: #356e67; font-size: 13px; font-variant-numeric: tabular-nums; }.review-entry p { margin: 9px 0 0; color: #596562; font-size: 13px; line-height: 1.5; }
.pagination { display: flex; align-items: center; justify-content: space-between; padding-top: 20px; color: #315f59; font-size: 14px; }.pagination a[aria-disabled="true"] { color: #9aa49f; pointer-events: none; }
.access-shell { max-width: 1100px; }.access-panel { padding-top: 26px; }.access-header { border-bottom: 1px solid #d9dfd8; padding-bottom: 13px; }.setting-section { padding: 20px 0; border-bottom: 1px solid #d9dfd8; }.setting-section h2 { margin-bottom: 12px; }.setting-row { display: flex; align-items: center; gap: 10px; }.field-label { min-width: 64px; color: #596562; font-size: 14px; }.setting-row input[type="text"], .setting-row input:not([type]) { width: min(280px, 100%); height: 36px; border: 1px solid #cbd5cf; border-radius: 5px; background: #fff; padding: 0 9px; color: #202b2a; font: inherit; }.setting-row button, .remove-target { min-height: 34px; border: 1px solid #315f59; border-radius: 5px; background: #315f59; padding: 0 12px; color: #fff; font: inherit; cursor: pointer; }.setting-row button:disabled, .remove-target:disabled { cursor: wait; opacity: .6; }.inline-toggle { display: inline-flex; align-items: center; gap: 5px; color: #475451; font-size: 14px; white-space: nowrap; }.inline-toggle input, .classification-toggle input { width: 16px; height: 16px; accent-color: #1d776f; }.target-list { border-top: 1px solid #d9dfd8; }.target-row { border-bottom: 1px solid #d9dfd8; padding: 13px 0; }.target-summary { display: flex; min-height: 34px; align-items: center; gap: 14px; }.target-id { flex: 1; color: #263432; font-variant-numeric: tabular-nums; }.digest-settings { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; margin: 11px 0 0; padding: 11px 0 0; border-top: 1px solid #edf0eb; }.digest-field { display: grid; gap: 5px; color: #596562; font-size: 12px; }.digest-field select, .digest-field input[type="time"] { min-width: 108px; min-height: 32px; border: 1px solid #cbd5cf; border-radius: 5px; background: #fff; padding: 0 6px; color: #35423f; font: inherit; }.classification-fields { min-width: 235px; margin: 0; border: 0; padding: 0; }.classification-fields legend { padding: 0; color: #596562; font-size: 12px; }.classification-list { display: flex; flex-wrap: wrap; gap: 6px 10px; padding-top: 5px; }.classification-toggle { display: inline-flex; align-items: center; gap: 4px; color: #475451; font-size: 13px; white-space: nowrap; }.remove-target { border-color: #b25572; background: #fff; color: #9a3452; }.access-message { min-height: 20px; margin-top: 15px; color: #356e67; font-size: 14px; }.access-message.error { color: #a34242; }
@media (max-width: 900px) { .app-shell { padding: 16px; }.content-layout { grid-template-columns: 1fr; }.policy-panel { order: -1; }.category-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }.category-option { border-right: 1px solid #d9dfd8; padding: 0 6px; }.day-cell { min-height: 104px; }.calendar-entry { font-size: 11px; } }
@media (max-width: 560px) { .app-shell { padding: 12px; }.brand { font-size: 18px; }.weekday-row span { padding: 7px 3px; font-size: 11px; }.day-cell { min-height: 82px; padding: 4px 3px; }.calendar-entry { padding: 2px; }.calendar-entry span { display: none; }.category-list { grid-template-columns: 1fr; }.topbar { align-items: flex-start; }.review-entry h2 { font-size: 16px; }.setting-row { flex-wrap: wrap; }.field-label { width: 100%; }.setting-row input[type="text"], .setting-row input:not([type]) { width: 100%; }.target-summary { align-items: flex-start; flex-wrap: wrap; gap: 9px; }.target-id { flex-basis: 100%; }.digest-settings { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }.classification-fields { grid-column: 1 / -1; min-width: 0; }.digest-field select, .digest-field input[type="time"] { min-width: 0; width: 100%; } }
"""


_DELIVERY_SCRIPT = """
document.querySelectorAll('[data-close-delivery]').forEach((button) => {
  button.addEventListener('click', async () => {
    if (!window.confirm('关闭这条未知结果记录？此操作保留历史，不会重发消息。')) return;
    button.disabled = true;
    try {
      const response = await fetch('/api/deliveries/' + encodeURIComponent(button.dataset.closeDelivery) + '/close', {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({confirm: true}),
      });
      if (!response.ok) throw new Error('close failed');
      window.location.reload();
    } catch (error) {
      window.alert('无法关闭记录，请刷新后重试。');
      button.disabled = false;
    }
  });
});
"""
