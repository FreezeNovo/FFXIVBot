"""官方文章发现、国服详情抓取与活动候选解析。

文章发现与详情解析分为独立服务；文章发布时间绝不被当作活动开始或结束时间。
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import threading
from dataclasses import replace
from dataclasses import dataclass, field
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from xml.etree import ElementTree

from .china_classification import classify_event_candidate
from .china_events import PARSER_VERSION, extract_plain_text, parse_event_candidates
from .source_models import ChinaNewsDetail, SourceDocument, SourceHealth
from .storage import StateStore
from .diagnostics import safe_reason, diagnostic


CHINA_NEWS_CENTER_URL = "https://ff.web.sdo.com/web8/index.html#/newstab/newslist"
CHINA_NEWS_DETAIL_PAGE_URL = "https://ff.web.sdo.com/web8/index.html#/newstab/newscont/{document_id}"
CHINA_NEWS_PAGE_SIZE = 20
DEFAULT_CHINA_PAGE_COUNT = 5
CHINA_NEWS_CATEGORY_CODES = "8324,8325,8326,8327,5309,5310,5311,5312,5313"
LODESTONE_TOPICS_URL = "https://na.finalfantasyxiv.com/lodestone/news/topics.xml"
LODESTONE_NEWS_URL = "https://na.finalfantasyxiv.com/lodestone/news/news.xml"
CHINA_NEWS_DETAIL_URL = "https://cqnews.web.sdo.com/api/news/newsDetail?gameCode=ff&id={document_id}"
ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"


def china_news_list_url(
    page_index: int,
    page_size: int = CHINA_NEWS_PAGE_SIZE,
) -> str:
    """Build the observed official JSON list endpoint for one page."""

    return (
        "https://cqnews.web.sdo.com/api/news/newsList?gameCode=ff&"
        f"CategoryCode={CHINA_NEWS_CATEGORY_CODES}&"
        f"pageIndex={page_index}&pageSize={page_size}"
    )


def china_news_detail_page_url(document_id: str) -> str:
    """Build the user-facing official article page, not the list view."""

    return CHINA_NEWS_DETAIL_PAGE_URL.format(document_id=document_id)


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    def get(self, url: str) -> HttpResponse: ...


class SourceTransportError(RuntimeError):
    """可分类的来源传输失败。"""

    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class UrllibHttpTransport:
    """带超时、代理、条件请求和有限退避的标准库 HTTP 传输层。"""

    def __init__(
        self,
        timeout_seconds: float = 20.0,
        *,
        proxy_url: str | None = None,
        max_attempts: int = 3,
        user_agent: str = "FFXIVBot/0.3.0 official-source",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._user_agent = user_agent
        self._opener = build_opener(
            ProxyHandler({"http": proxy_url, "https": proxy_url})
            if proxy_url
            else ProxyHandler()
        )

    def get(self, url: str) -> HttpResponse:
        return self.get_with_headers(url)

    def get_with_headers(
        self, url: str, headers: Mapping[str, str] | None = None
    ) -> HttpResponse:
        request_headers = {
            "Accept": "application/json, application/atom+xml, application/xml;q=0.9, */*;q=0.1",
            "User-Agent": self._user_agent,
        }
        if headers:
            request_headers.update(headers)
        for attempt in range(self._max_attempts):
            request = Request(url, headers=request_headers)
            try:
                with self._opener.open(request, timeout=self._timeout_seconds) as response:
                    return HttpResponse(
                        status_code=int(response.status),
                        body=response.read(),
                        headers={str(k).lower(): str(v) for k, v in response.headers.items()},
                    )
            except HTTPError as error:
                if error.code == 304:
                    return HttpResponse(
                        status_code=304,
                        body=b"",
                        headers={str(k).lower(): str(v) for k, v in error.headers.items()},
                    )
                if not _retryable_status(error.code) or attempt + 1 >= self._max_attempts:
                    raise SourceTransportError(
                        f"HTTP {error.code}",
                        retryable=_retryable_status(error.code),
                        status_code=error.code,
                    ) from error
            except (TimeoutError, URLError, OSError) as error:
                if attempt + 1 >= self._max_attempts:
                    raise SourceTransportError(
                        f"{type(error).__name__}: {error}", retryable=True
                    ) from error
            _sleep_with_jitter(attempt)
        raise AssertionError("unreachable")


@dataclass(frozen=True)
class SourceFetchResult:
    source_id: str
    display_name: str
    status_code: int
    raw_payload: bytes
    documents: tuple[SourceDocument, ...]
    page_count: int = 1
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None


class OfficialSourceAdapter(Protocol):
    source_id: str
    display_name: str

    def fetch(self, transport: HttpTransport) -> SourceFetchResult: ...


class _ConditionalTransport:
    """给一个来源的第一请求附加 validators，兼容现有 fixture transport。"""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        self._transport = transport
        self._headers = {
            key: value
            for key, value in (
                ("If-None-Match", etag),
                ("If-Modified-Since", last_modified),
            )
            if value
        }
        self._used = False

    def get(self, url: str) -> HttpResponse:
        if self._used or not self._headers:
            return self._transport.get(url)
        self._used = True
        getter = getattr(self._transport, "get_with_headers", None)
        if callable(getter):
            return getter(url, self._headers)
        return self._transport.get(url)


class ChinaNewsApiSource:
    source_id = "china_news_api"
    display_name = "国服官方新闻"

    def __init__(
        self,
        page_count: int = DEFAULT_CHINA_PAGE_COUNT,
        page_size: int = CHINA_NEWS_PAGE_SIZE,
    ) -> None:
        if page_count < 1:
            raise ValueError("page_count must be at least 1")
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        self._page_count = page_count
        self._page_size = page_size

    def fetch(self, transport: HttpTransport) -> SourceFetchResult:
        documents_by_id: dict[str, SourceDocument] = {}
        raw_payloads: list[bytes] = []
        for page_index in range(self._page_count):
            response = transport.get(china_news_list_url(page_index, self._page_size))
            if response.status_code == 304:
                if page_index != 0:
                    raise ValueError("国服新闻分页返回了无法独立处理的 304")
                return SourceFetchResult(
                    source_id=self.source_id,
                    display_name=self.display_name,
                    status_code=304,
                    raw_payload=b"",
                    documents=(),
                    page_count=self._page_count,
                    not_modified=True,
                    etag=_header(response, "etag"),
                    last_modified=_header(response, "last-modified"),
                )
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")

            payload = json.loads(response.body.decode("utf-8"))
            if str(payload.get("Code")) != "0":
                raise ValueError("国服新闻接口返回非成功 Code")
            records = payload.get("Data")
            if not isinstance(records, list):
                raise ValueError("国服新闻接口缺少 Data 列表")
            if not records:
                raise ValueError("国服新闻接口 Data 为空，视为来源退化")
            if any(not isinstance(record, dict) or record.get("Id") is None for record in records):
                raise ValueError("国服新闻接口 Data 含缺少 Id 的记录")
            raw_payloads.append(response.body)

            for record in records:
                document_id = str(record["Id"])
                if document_id in documents_by_id:
                    continue
                out_link = str(record.get("OutLink") or "").strip()
                documents_by_id[document_id] = SourceDocument(
                    source_id=self.source_id,
                    document_id=document_id,
                    title=str(record.get("Title") or "未命名公告"),
                    url=out_link or china_news_detail_page_url(document_id),
                    published_at=_optional_text(record.get("PublishDate")),
                    updated_at=None,
                    category=_optional_text(record.get("CategoryCode")),
                    summary=_optional_text(record.get("Summary")),
                    image_url=_optional_text(record.get("HomeImagePath")),
                )
        return SourceFetchResult(
            source_id=self.source_id,
            display_name=self.display_name,
            status_code=200,
            raw_payload=b"\n".join(raw_payloads),
            documents=tuple(documents_by_id.values()),
            page_count=self._page_count,
            etag=_header(response, "etag"),
            last_modified=_header(response, "last-modified"),
        )


class ChinaNewsDetailApi:
    """国服新闻详情只按已发现的文章 ID 请求，不扫描网页或专题页。"""

    source_id = "china_news_api"

    def fetch(self, transport: HttpTransport, document_id: str) -> ChinaNewsDetail:
        response = transport.get(CHINA_NEWS_DETAIL_URL.format(document_id=document_id))
        if response.status_code == 304:
            raise ValueError("国服新闻详情不支持 304 条件响应")
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        payload = json.loads(response.body.decode("utf-8"))
        if str(payload.get("Code")) != "0" or not isinstance(payload.get("Data"), dict):
            raise ValueError("国服新闻详情接口返回非成功响应")
        data = payload["Data"]
        content_html = str(data.get("Content") or "")
        if not content_html.strip():
            raise ValueError("国服新闻详情缺少 Content")
        return ChinaNewsDetail(
            source_id=self.source_id,
            document_id=str(data.get("Id") or document_id),
            title=str(data.get("Title") or "未命名公告"),
            published_at=_optional_text(data.get("PublishDate")),
            content_html=content_html,
            content_text=extract_plain_text(content_html),
        )


class LodestoneAtomSource:
    source_id = "lodestone_atom"
    display_name = "Lodestone Atom"

    def __init__(self, *, topics=False):
        self.url = LODESTONE_TOPICS_URL if topics else LODESTONE_NEWS_URL
        self.source_id = "lodestone_topics" if topics else "lodestone_atom"
        self.display_name = "Lodestone Topics" if topics else "Lodestone Atom"

    def fetch(self, transport: HttpTransport) -> SourceFetchResult:
        response = transport.get(self.url)
        if response.status_code == 304:
            return SourceFetchResult(
                source_id=self.source_id,
                display_name=self.display_name,
                status_code=304,
                raw_payload=b"",
                documents=(),
                not_modified=True,
                etag=_header(response, "etag"),
                last_modified=_header(response, "last-modified"),
            )
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")

        root = ElementTree.fromstring(response.body)
        if root.tag != f"{{{ATOM_NAMESPACE}}}feed":
            raise ValueError("Lodestone Atom 根节点不是 Atom feed")
        namespace = {"atom": ATOM_NAMESPACE}
        documents: list[SourceDocument] = []
        for entry in root.findall("atom:entry", namespace):
            atom_id = _element_text(entry, "atom:id", namespace)
            if not atom_id:
                raise ValueError("Lodestone Atom entry缺少稳定id")
            if not _element_text(entry,"atom:title",namespace) or _alternate_link(entry)==LODESTONE_NEWS_URL:
                raise ValueError("Lodestone Atom entry缺少标题或官方详情链接")
            documents.append(
                SourceDocument(
                    source_id=self.source_id,
                    document_id=atom_id,
                    title=_element_text(entry, "atom:title", namespace) or "Untitled",
                    url=_alternate_link(entry),
                    published_at=_element_text(entry, "atom:published", namespace),
                    updated_at=_element_text(entry, "atom:updated", namespace),
                    category=_category_term(entry),
                    summary=_element_text(entry, "atom:summary", namespace),
                    image_url=_enclosure_link(entry),
                    content_html=_element_text(entry, "atom:content", namespace),
                )
            )
        if not documents:
            raise ValueError("Lodestone Atom 未发现 entry，视为来源退化")
        return SourceFetchResult(
            source_id=self.source_id,
            display_name=self.display_name,
            status_code=response.status_code,
            raw_payload=response.body,
            documents=tuple(documents),
            etag=_header(response, "etag"),
            last_modified=_header(response, "last-modified"),
        )


@dataclass(frozen=True)
class SourceRefreshResult:
    source_id: str
    display_name: str
    success: bool
    document_count: int
    page_count: int = 1
    error: str | None = None
    not_modified: bool = False


class OfficialSourceService:
    """协调独立官方源；单个源失败不会影响其他源和旧快照。"""

    def __init__(
        self,
        store: StateStore,
        transport: HttpTransport | None = None,
        adapters: tuple[OfficialSourceAdapter, ...] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport or UrllibHttpTransport()
        self._adapters = adapters or (ChinaNewsApiSource(), LodestoneAtomSource(), LodestoneAtomSource(topics=True))
        self._lock = threading.Lock()
        self.stopped = threading.Event()

    def refresh(self) -> tuple[SourceRefreshResult, ...]:
        with self._lock:
            return self._refresh()

    def _refresh(self) -> tuple[SourceRefreshResult, ...]:
        results: list[SourceRefreshResult] = []
        for adapter in self._adapters:
            if self.stopped.is_set():break
            try:
                etag, last_modified = self._store.get_source_validators(adapter.source_id)
                fetched = adapter.fetch(
                    _ConditionalTransport(
                        self._transport,
                        etag=etag,
                        last_modified=last_modified,
                    )
                )
                if self.stopped.is_set():
                    return tuple(results)
                not_modified = bool(getattr(fetched, "not_modified", False))
                if not_modified:
                    self._store.record_source_not_modified(
                        source_id=fetched.source_id,
                        display_name=fetched.display_name,
                        etag=getattr(fetched, "etag", None),
                        last_modified=getattr(fetched, "last_modified", None),
                    )
                else:
                    self._store.record_source_success(
                        source_id=fetched.source_id,
                        display_name=fetched.display_name,
                        http_status=fetched.status_code,
                        raw_payload=fetched.raw_payload,
                        documents=fetched.documents,
                        etag=getattr(fetched, "etag", None),
                        last_modified=getattr(fetched, "last_modified", None),
                    )
                if not not_modified:
                    for document in fetched.documents:
                        if document.source_id in {"lodestone_atom","lodestone_topics"} and not document.content_html:
                            self._store.record_detail_parse_failure(source_id=document.source_id,document_id=document.document_id,error="Atom entry缺少正文，保留最后有效版本并等待详情复查")
                            diagnostic(stage="atom_body",source_id=document.source_id,document_id=document.document_id,action="preserve_last_valid",error="missing_content")
                        if document.content_html:
                            version_id=self._store.record_document_version(document, document.content_html,ready=False)
                            if document.source_id in {"lodestone_atom","lodestone_topics"}:
                                from .na_events import parse_na_candidates, date_only_evidence, PARSER_VERSION as NA_VERSION
                                self._store.record_time_evidence(version_id,date_only_evidence(document,document.content_html))
                                detail=ChinaNewsDetail(document.source_id,document.document_id,document.title,document.published_at,document.content_html,extract_plain_text(document.content_html))
                                self._store.record_china_detail_success(detail,parse_na_candidates(document,document.content_html),NA_VERSION)
                results.append(
                    SourceRefreshResult(
                        source_id=fetched.source_id,
                        display_name=fetched.display_name,
                        success=True,
                        document_count=(
                            len(fetched.documents)
                            if not not_modified
                            else self._document_count(adapter.source_id)
                        ),
                        page_count=getattr(fetched, "page_count", 1),
                        not_modified=not_modified,
                    )
                )
            except Exception as error:
                if self.stopped.is_set():break
                message = _error_summary(error)
                diagnostic(stage="discovery",source_id=adapter.source_id,action="preserve_snapshot",error=message)
                self._store.record_source_failure(
                    source_id=adapter.source_id,
                    display_name=adapter.display_name,
                    error=message,
                    http_status=getattr(error,"status_code",None),
                )
                results.append(
                    SourceRefreshResult(
                        source_id=adapter.source_id,
                        display_name=adapter.display_name,
                        success=False,
                        document_count=0,
                        error=message,
                    )
                )
        return tuple(results)

    def render_refresh(self, results: tuple[SourceRefreshResult, ...]) -> str:
        lines = ["官方数据源刷新"]
        for result in results:
            if result.success:
                page_suffix = f"（最近 {result.page_count} 页）" if result.page_count > 1 else ""
                unchanged_suffix = "，内容未变化" if result.not_modified else ""
                lines.append(
                    f"{result.display_name}：成功{unchanged_suffix}，发现 {result.document_count} 篇官方文章{page_suffix}"
                )
            else:
                lines.append(f"{result.display_name}：失败，已保留最后有效快照")
        return "\n".join(lines)

    def _document_count(self, source_id: str) -> int:
        health = next(
            (item for item in self._store.get_source_health() if item.source_id == source_id),
            None,
        )
        return health.document_count if health is not None else 0

    def render_health(self) -> str:
        health_records = self._store.get_source_health()
        if not health_records:
            return "官方数据源状态\n尚未同步。发送 ff14 数据源 开始手动同步。"

        lines = ["官方数据源状态"]
        for health in health_records:
            if health.last_success_at:
                state = "正常" if health.consecutive_failures == 0 else "降级"
                lines.append(
                    f"{health.display_name}：{state}，最近成功 {health.last_success_at}，"
                    f"缓存 {health.document_count} 篇"
                )
                if health.last_document_at:
                    lines.append(f"  最新文章时间：{health.last_document_at}")
                if health.last_error:
                    lines.append(f"  最近错误：{health.last_error}")
            else:
                lines.append(f"{health.display_name}：未成功同步")
        lines.append("文章发布时间不等同于活动时间。")
        return "\n".join(lines)


@dataclass(frozen=True)
class ChinaDetailFailure:
    document_id: str
    title: str
    error: str


@dataclass(frozen=True)
class ChinaDetailRefreshResult:
    attempted_count: int
    success_count: int
    candidate_count: int
    failure_count: int
    failures: tuple[ChinaDetailFailure, ...] = ()


class ChinaNewsDetailService:
    """手动解析已缓存的国服文章详情，生成待审核活动候选。"""

    source_id = "china_news_detail"
    display_name = "国服新闻详情解析"

    def __init__(
        self,
        store: StateStore,
        transport: HttpTransport | None = None,
        detail_api: ChinaNewsDetailApi | None = None,
    ) -> None:
        self._store = store
        self._transport = transport or UrllibHttpTransport()
        self._detail_api = detail_api or ChinaNewsDetailApi()
        self._document_locks = {}
        self._document_locks_guard = threading.Lock()
        self._lock = threading.Lock()
        self.stopped = threading.Event()

    def refresh_cached_documents(
        self,
        limit: int = 10,
        pause_seconds: float = 0.0,
    ) -> ChinaDetailRefreshResult:
        with self._lock:
            return self._refresh_cached_documents(limit, pause_seconds)

    def _refresh_cached_documents(
        self,
        limit: int = 10,
        pause_seconds: float = 0.0,
    ) -> ChinaDetailRefreshResult:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if pause_seconds < 0:
            raise ValueError("pause_seconds cannot be negative")
        documents = self._store.get_documents_needing_detail_review(
            "china_news_api", limit=limit, parser_version=PARSER_VERSION
        )
        successes = 0
        failures = 0
        candidate_count = 0
        last_error: str | None = None
        detail_failures: list[ChinaDetailFailure] = []
        for index, document in enumerate(documents):
            if index and pause_seconds:
                time.sleep(pause_seconds)
            try:
                candidate_count += self.refresh_document(document)
                successes += 1
            except Exception as error:
                if self.stopped.is_set():break
                failures += 1
                last_error = _error_summary(error)
                self._store.record_detail_parse_failure(
                    source_id=document.source_id,
                    document_id=document.document_id,
                    error=last_error,
                )
                detail_failures.append(
                    ChinaDetailFailure(
                        document_id=document.document_id,
                        title=document.title,
                        error=last_error,
                    )
                )

        if successes:
            self._store.record_source_probe_success(
                source_id=self.source_id,
                display_name=self.display_name,
                document_count=successes,
            )
        elif documents:
            self._store.record_source_failure(
                source_id=self.source_id,
                display_name=self.display_name,
                error=last_error or "所有国服新闻详情请求失败",
            )
        return ChinaDetailRefreshResult(
            attempted_count=len(documents),
            success_count=successes,
            candidate_count=candidate_count,
            failure_count=failures,
            failures=tuple(detail_failures),
        )

    def refresh_document(self, document):
        with self._document_locks_guard:
            lock = self._document_locks.setdefault((document.source_id, document.document_id), threading.Lock())
        with lock:
            if self.stopped.is_set():return 0
            return self._refresh_document(document)

    def _refresh_document(self, document):
        self._store.record_verification_attempt(document.source_id,document.document_id)
        detail = self._detail_api.fetch(self._transport, document.document_id)
        if self.stopped.is_set():
            return 0
        full_document = replace(document, title=detail.title, published_at=detail.published_at)
        self._store.record_document_version(full_document, detail.content_html,ready=False)
        content_hash = hashlib.sha256(
            detail.content_html.encode("utf-8")
        ).hexdigest()
        if (
            self._store.get_detail_content_hash(
                detail.source_id, detail.document_id
            )
            == content_hash
            and self._store.get_detail_parser_version(
                detail.source_id, detail.document_id
            )
            == PARSER_VERSION
        ):
            self._store.record_verification_success(detail.source_id,detail.document_id,content_hash)
            record=self._store.get_event_verification(detail.source_id,detail.document_id)
            if record and record["error_kind"]=="running":
                self._store.record_verification_attempt(detail.source_id,detail.document_id,error="当前正文没有可核实的活动时间",error_kind="parse_zero")
            self._store.finish_article_observation(document.source_id,document.document_id)
            return 0
        parsed_candidates = parse_event_candidates(
            source_id=detail.source_id,
            document_id=detail.document_id,
            title=detail.title,
            content_text=detail.content_text,
        )
        candidates = tuple(classify_event_candidate(item) for item in parsed_candidates)
        self._store.record_china_detail_success(detail, candidates, PARSER_VERSION)
        return len(candidates)

    def render_refresh(self, result: ChinaDetailRefreshResult) -> str:
        lines = [
            "国服活动解析",
            f"已解析 {result.success_count}/{result.attempted_count} 篇官方新闻详情，"
            f"发现 {result.candidate_count} 条明确时间候选。",
        ]
        if result.failure_count:
            lines.append(f"{result.failure_count} 篇详情获取失败，已保留旧快照。")
            for failure in result.failures[:3]:
                lines.append(
                    f"失败详情：{failure.title}（ID {failure.document_id}）：{failure.error}"
                )
            if len(result.failures) > 3:
                lines.append(f"另有 {len(result.failures) - 3} 篇失败详情未显示。")
        lines.append("本次刷新更新来源与候选；已启用策略由独立调度和投递账本处理。")
        return "\n".join(lines)

    def render_candidates(self) -> str:
        candidates = self._store.get_event_candidates("china_news_api")
        if not candidates:
            return "国服活动候选\n暂无明确时间候选。先发送 ff14 数据源 国服详情。"
        lines = ["国服活动候选"]
        for candidate in candidates[:5]:
            event = candidate.candidate
            lines.append(
                f"[{candidate.classification_label} / {candidate.routing_label}] {event.title}\n"
                f"{event.start_at} 至 {event.end_at}\n"
                f"依据：{candidate.reason}\n"
                f"证据：{event.evidence_text}"
            )
        if len(candidates) > 5:
            lines.append(f"另有 {len(candidates) - 5} 条候选未显示。")
        lines.append("所有时间均按 Asia/Shanghai 解析，需在后续合并前核验。")
        return "\n".join(lines)

def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _element_text(element: ElementTree.Element, path: str, namespace: dict[str, str]) -> str | None:
    child = element.find(path, namespace)
    return _optional_text(child.text if child is not None else None)


def _alternate_link(entry: ElementTree.Element) -> str:
    for link in entry.findall(f"{{{ATOM_NAMESPACE}}}link"):
        if link.get("rel") == "alternate" and link.get("href"):
            return str(link.get("href"))
    return LODESTONE_NEWS_URL


def _enclosure_link(entry: ElementTree.Element) -> str | None:
    for link in entry.findall(f"{{{ATOM_NAMESPACE}}}link"):
        if link.get("rel") == "enclosure" and link.get("href"):
            return str(link.get("href"))
    return None


def _category_term(entry: ElementTree.Element) -> str | None:
    category = entry.find(f"{{{ATOM_NAMESPACE}}}category")
    return _optional_text(category.get("term") if category is not None else None)


def _error_summary(error: Exception) -> str:
    return safe_reason(f"{type(error).__name__}: {error}")


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429} or 500 <= status_code <= 599


def _sleep_with_jitter(attempt: int) -> None:
    # Upper bound remains small so one source cannot stall the whole poll cycle.
    time.sleep(min(2.0, 0.25 * (2**attempt)) + random.uniform(0.0, 0.15))


def _header(response: HttpResponse, name: str) -> str | None:
    if not response.headers:
        return None
    value = response.headers.get(name.lower())
    return str(value).strip() if value else None
