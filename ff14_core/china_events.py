"""国服官方新闻正文的保守时间区间解析器。

只接受白名单语义标签后的明确起止时间。文章发布时间、标题中的日期、
“即日起”以及仅提供开始时间的公告都不能被推断为可提醒的活动区间。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from .source_models import EventCandidate


CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")
PARSER_VERSION = "china-time-v3"

# 这些标签来自当前缓存的国服官网正文。新标签必须先以真实文章 fixture 验证，
# 不使用宽泛的“任意 XX 时间”规则，避免把说明文字误认为活动时间。
_LABEL_KIND = {
    "预计维护时间": "maintenance",
    "维护时间": "maintenance",
    "停机维护时间": "maintenance",
    "活动时间": "activity",
    "活动开放时间": "activity",
    "专题页开放时间": "activity",
    "开放时间": "activity",
    "联动开启时间": "activity",
    "直播时间": "live_stream",
    "转播时间": "live_stream",
    "演出时间": "live_stream",
    "比赛时间": "live_stream",
    "报名时间": "operational",
    "领取优惠券": "operational",
    "领取时间": "operational",
    "兑换时间": "operational",
    "补款时间": "operational",
    "售卖时间": "operational",
    "购票时间": "operational",
    "抽选时间": "operational",
}
_LABEL_PATTERN = "|".join(
    re.escape(label) for label in sorted(_LABEL_KIND, key=len, reverse=True)
)

# 支持官网正文已经出现的中文格式，并兼容常见的 . / - 日期分隔符和“14点”。
_CLOCK_TEXT = r"(?:上午|下午|晚上|中午|凌晨)?\s*\d{1,2}(?:(?::|：)\d{2}|(?:点|时)\s*\d{0,2}\s*分?)"
_FULL_DATETIME_TEXT = (
    rf"\d{{4}}\s*(?:年|[./-])\s*\d{{1,2}}\s*(?:月|[./-])\s*\d{{1,2}}\s*日?\s*{_CLOCK_TEXT}"
)
_DATE_ONLY_TEXT = rf"\d{{4}}\s*(?:年|[./-])\s*\d{{1,2}}\s*(?:月|[./-])\s*\d{{1,2}}\s*日?"
_MONTH_DAY_DATETIME_TEXT = rf"\d{{1,2}}\s*月\s*\d{{1,2}}\s*日?\s*{_CLOCK_TEXT}"
_TIME_ONLY_TEXT = _CLOCK_TEXT
_RANGE_SEPARATOR = r"(?:～|~|至|到|—|－|-)"
_TIME_RANGE_PATTERN = re.compile(
    rf"(?P<label>{_LABEL_PATTERN})\s*[：:]\s*"
    rf"(?P<start>{_FULL_DATETIME_TEXT})\s*{_RANGE_SEPARATOR}\s*"
    rf"(?P<end>{_FULL_DATETIME_TEXT}|{_MONTH_DAY_DATETIME_TEXT}|{_TIME_ONLY_TEXT})"
)
_DATE_RANGE_PATTERN = re.compile(
    rf"(?P<label>{_LABEL_PATTERN})\s*[：:]\s*"
    rf"(?P<start>{_DATE_ONLY_TEXT})\s*{_RANGE_SEPARATOR}\s*"
    rf"(?P<end>{_DATE_ONLY_TEXT})"
)
_FULL_DATETIME_PATTERN = re.compile(
    r"(?P<year>\d{4})\s*(?:年|[./-])\s*"
    r"(?P<month>\d{1,2})\s*(?:月|[./-])\s*"
    r"(?P<day>\d{1,2})\s*日?\s*(?P<clock>.+)"
)
_DATE_ONLY_PATTERN = re.compile(
    r"(?P<year>\d{4})\s*(?:年|[./-])\s*"
    r"(?P<month>\d{1,2})\s*(?:月|[./-])\s*"
    r"(?P<day>\d{1,2})\s*日?"
)
_MONTH_DAY_DATETIME_PATTERN = re.compile(
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日?\s*(?P<clock>.+)"
)
_CLOCK_PATTERN = re.compile(
    r"(?P<period>上午|下午|晚上|中午|凌晨)?\s*(?P<hour>\d{1,2})"
    r"(?:(?::|：)(?P<minute_colon>\d{2})|(?:点|时)\s*(?P<minute_word>\d{0,2})\s*分?)"
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"br", "p", "div", "li", "tr"}:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "tr"}:
            self._parts.append(" ")

    def text(self) -> str:
        return re.sub(r"\s+", " ", unescape("".join(self._parts))).strip()


def extract_plain_text(content_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(content_html)
    parser.close()
    return parser.text()


def parse_event_candidates(
    *,
    source_id: str,
    document_id: str,
    title: str,
    content_text: str,
) -> tuple[EventCandidate, ...]:
    """从带白名单标签的完整起止时间中生成候选，不进行时间补全推断。"""

    candidates: list[EventCandidate] = []
    seen_candidate_ids: set[str] = set()
    for match in _TIME_RANGE_PATTERN.finditer(content_text):
        label = match.group("label")
        try:
            start = _parse_full_time(match.group("start"))
            end = _parse_end_time(match.group("end"), start)
        except ValueError:
            continue
        if end <= start:
            # 只有“16:00 ~ 02:00”这种同日省略日期的表达可以确定跨天。
            if _is_time_only(match.group("end")):
                end += timedelta(days=1)
            else:
                continue

        kind = _LABEL_KIND[label]
        evidence = match.group(0)
        candidate_id = hashlib.sha256(
            f"{PARSER_VERSION}|{kind}|{label}|{start.isoformat()}|{end.isoformat()}".encode("utf-8")
        ).hexdigest()[:20]
        if candidate_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(candidate_id)
        candidates.append(
            EventCandidate(
                source_id=source_id,
                document_id=document_id,
                title=title,
                candidate_id=candidate_id,
                kind=kind,
                label=label,
                evidence_text=evidence,
                start_at=start.isoformat(),
                end_at=end.isoformat(),
                timezone="Asia/Shanghai",
                confidence="high",
            )
        )
    for match in _DATE_RANGE_PATTERN.finditer(content_text):
        label = match.group("label")
        try:
            start = _parse_date_only(match.group("start"))
            end = _parse_date_only(match.group("end"), end_of_day=True)
        except ValueError:
            continue
        kind = _LABEL_KIND[label]
        evidence = match.group(0)
        candidate_id = hashlib.sha256(
            f"{PARSER_VERSION}|{kind}|{label}|{start.isoformat()}|{end.isoformat()}".encode("utf-8")
        ).hexdigest()[:20]
        if candidate_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(candidate_id)
        candidates.append(
            EventCandidate(
                source_id=source_id,
                document_id=document_id,
                title=title,
                candidate_id=candidate_id,
                kind=kind,
                label=label,
                evidence_text=evidence,
                start_at=start.isoformat(),
                end_at=end.isoformat(),
                timezone="Asia/Shanghai",
                confidence="high",
                precision="day",
                time_assumption="仅日期，按Asia/Shanghai日边界显示，不具备精确提醒资格",
            )
        )
    return tuple(candidates)


def _parse_full_time(value: str) -> datetime:
    match = _FULL_DATETIME_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"无法解析完整国服时间: {value}")
    hour, minute = _parse_clock(match.group("clock"))
    return datetime(
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
        hour,
        minute,
        tzinfo=CHINA_TIMEZONE,
    )


def _parse_date_only(value: str, *, end_of_day: bool = False) -> datetime:
    match = _DATE_ONLY_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"无法解析国服日期: {value}")
    return datetime(
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
        23 if end_of_day else 0,
        59 if end_of_day else 0,
        59 if end_of_day else 0,
        tzinfo=CHINA_TIMEZONE,
    )


def _parse_end_time(value: str, start: datetime) -> datetime:
    cleaned = value.strip()
    if _FULL_DATETIME_PATTERN.fullmatch(cleaned):
        return _parse_full_time(cleaned)
    month_day = _MONTH_DAY_DATETIME_PATTERN.fullmatch(cleaned)
    if month_day is not None:
        hour, minute = _parse_clock(month_day.group("clock"))
        end = datetime(
            start.year,
            int(month_day.group("month")),
            int(month_day.group("day")),
            hour,
            minute,
            tzinfo=CHINA_TIMEZONE,
        )
        if end < start and start.month == 12 and end.month == 1:
            return end.replace(year=end.year + 1)
        return end
    if _is_time_only(cleaned):
        hour, minute = _parse_clock(cleaned)
        return start.replace(hour=hour, minute=minute)
    raise ValueError(f"无法解析国服结束时间: {value}")


def _is_time_only(value: str) -> bool:
    return _CLOCK_PATTERN.fullmatch(value.strip()) is not None


def _parse_clock(value: str) -> tuple[int, int]:
    match = _CLOCK_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"无法解析国服时刻: {value}")
    hour = int(match.group("hour"))
    minute_text = match.group("minute_colon") or match.group("minute_word") or "0"
    minute = int(minute_text)
    period = match.group("period")
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    elif period == "凌晨" and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"国服时刻超出范围: {value}")
    return hour, minute
