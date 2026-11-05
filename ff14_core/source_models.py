"""官方文章、活动与三类消息策略的框架无关数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDocument:
    """来自官方源的一篇文章，不等同于已经确认的活动。"""

    source_id: str
    document_id: str
    title: str
    url: str
    published_at: str | None
    updated_at: str | None
    category: str | None
    summary: str | None
    image_url: str | None
    content_html: str | None = None


@dataclass(frozen=True)
class SourceHealth:
    source_id: str
    display_name: str
    last_attempt_at: str | None
    last_success_at: str | None
    last_http_status: int | None
    consecutive_failures: int
    last_error: str | None
    document_count: int
    last_document_at: str | None = None
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class ChinaNewsDetail:
    """国服官方新闻详情 API 返回的已验证字段。"""

    source_id: str
    document_id: str
    title: str
    published_at: str | None
    content_html: str
    content_text: str


@dataclass(frozen=True)
class EventCandidate:
    """正文存在明确时间证据时生成的候选；投递资格由独立工作流决定。"""

    source_id: str
    document_id: str
    title: str
    candidate_id: str
    kind: str
    label: str
    evidence_text: str
    start_at: str
    end_at: str
    timezone: str
    confidence: str
    region: str = "cn"
    created_at: str | None = None
    url: str | None = None
    precision: str = "minute"
    time_assumption: str = ""


@dataclass(frozen=True)
class ClassifiedEventCandidate:
    """候选的可解释分类与后续处理路由。"""

    candidate: EventCandidate
    classification: str
    classification_label: str
    routing: str
    routing_label: str
    reason: str


@dataclass(frozen=True)
class CalendarCategoryOption:
    """一个分类标签是否整体进入推送日历的管理员策略。"""

    classification_label: str
    candidate_count: int
    included: bool


@dataclass(frozen=True)
class CalendarEntry:
    """用于 Web 日历显示的候选活动，不代表已经启用 QQ 推送。"""

    document_id: str
    title: str
    url: str
    classification_label: str
    routing_label: str
    start_at: str
    end_at: str
    evidence_text: str
    region: str = "cn"
    classification: str = "unclassified"
    source_id: str = "china_news_api"
    precision: str = "minute"
    freshness_state: str = "unverified"
    last_verified_at: str | None = None
    age_seconds: float | None = None
    verification_warning: str | None = None


@dataclass(frozen=True)
class ReviewPage:
    """待审核候选的稳定分页结果。"""

    entries: tuple[CalendarEntry, ...]
    page: int
    page_size: int
    total_count: int


@dataclass(frozen=True)
class ActivityDigestPolicy:
    """单个目标的活动摘要意图，不包含调度器运行状态。"""

    enabled: bool = False
    region: str = "cn"
    classification_codes: tuple[str, ...] = ()
    timezone: str = "Asia/Shanghai"
    digest_hour: int = 20
    digest_minute: int = 0
    upcoming_window_minutes: int = 7 * 24 * 60
    ending_window_minutes: int = 1440

    regions: tuple[str, ...] | None = None

    @property
    def selected_regions(self) -> tuple[str, ...]:
        return self.regions if self.regions is not None else (self.region,)


@dataclass(frozen=True)
class ActivityDigest:
    """一次活动摘要查询的结构化结果。"""

    target_id: str
    generated_at: str
    ongoing: tuple[CalendarEntry, ...]
    upcoming: tuple[CalendarEntry, ...]
    ending: tuple[CalendarEntry, ...]
    warnings: tuple[str, ...] = ()
    excluded_count: int = 0
    unavailable: bool = False


@dataclass(frozen=True)
class MaintenancePolicy:
    """单个目标的国服维护提醒策略。"""

    enabled: bool = False
    region: str = "cn"
    timezone: str = "Asia/Shanghai"
    notify_announcements: bool = True
    reminder_window_minutes: int = 24 * 60
    notify_updates: bool = True
    watermark_at: str | None = None

    regions: tuple[str, ...] | None = None

    @property
    def selected_regions(self) -> tuple[str, ...]:
        return self.regions if self.regions is not None else (self.region,)


@dataclass(frozen=True)
class MaintenanceDigest:
    """一次维护状态查询的结构化结果。"""

    target_id: str
    generated_at: str
    ongoing: tuple[CalendarEntry, ...]
    upcoming: tuple[CalendarEntry, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManualQueryPolicy:
    """普通群员的手动查询授权，独立于主动订阅。"""

    activity_enabled: bool = False
    maintenance_enabled: bool = False


@dataclass(frozen=True)
class MaintenanceObservation:
    transition: str
    version_id: str
