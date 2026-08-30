"""国服活动候选的分类和 Web 日历路由规则。"""

from __future__ import annotations

from .source_models import ClassifiedEventCandidate, EventCandidate


COMMERCIAL_KEYWORDS = (
    "月卡",
    "商城",
    "优惠券",
    "特惠",
    "礼包",
    "礼赠",
    "积分",
    "换礼",
)


def classify_event_candidate(candidate: EventCandidate) -> ClassifiedEventCandidate:
    """将所有有明确时间的候选归入日历分类，不再产生待审核路由。"""

    title = candidate.title
    if candidate.kind == "maintenance":
        return _classified(
            candidate,
            classification="maintenance",
            classification_label="维护",
            routing="maintenance_candidate",
            routing_label="维护候选",
            reason="正文包含明确维护时间范围。",
        )

    if candidate.kind == "operational":
        return _classified(
            candidate,
            classification="operational_campaign",
            classification_label="运营活动",
            routing="calendar_candidate",
            routing_label="日历候选",
            reason=f"正文使用“{candidate.label}”标签，属于运营活动，进入日历候选。",
        )

    if candidate.kind == "live_stream":
        return _classified(
            candidate,
            classification="live_broadcast",
            classification_label="直播或转播",
            routing="calendar_candidate",
            routing_label="日历候选",
            reason=f"正文使用“{candidate.label}”标签，归入直播或转播日历。",
        )

    # 联动是独立于季节活动的分类，避免“季节联动活动”与“联动活动”重复。
    if candidate.label == "联动开启时间" or "联动" in title:
        return _classified(
            candidate,
            classification="collaboration_event",
            classification_label="联动活动",
            routing="calendar_candidate",
            routing_label="日历候选",
            reason="标题含“联动”，归入统一的联动活动分类。",
        )

    if "季节活动" in title:
        return _classified(
            candidate,
            classification="seasonal_event",
            classification_label="季节活动",
            routing="calendar_candidate",
            routing_label="日历候选",
            reason="标题含“季节活动”，归入季节活动分类。",
        )

    commercial_keyword = next((word for word in COMMERCIAL_KEYWORDS if word in title), None)
    if commercial_keyword:
        return _classified(
            candidate,
            classification="operational_campaign",
            classification_label="运营活动",
            routing="calendar_candidate",
            routing_label="日历候选",
            reason=f"标题含运营活动关键词“{commercial_keyword}”，归入运营活动分类。",
        )

    return _classified(
        candidate,
        classification="other_operational",
        classification_label="其他运营活动",
        routing="calendar_candidate",
        routing_label="日历候选",
        reason="正文存在明确时间，但标题未命中更具体分类，归入其他运营活动。",
    )


def _classified(
    candidate: EventCandidate,
    *,
    classification: str,
    classification_label: str,
    routing: str,
    routing_label: str,
    reason: str,
) -> ClassifiedEventCandidate:
    return ClassifiedEventCandidate(
        candidate=candidate,
        classification=classification,
        classification_label=classification_label,
        routing=routing,
        routing_label=routing_label,
        reason=reason,
    )
