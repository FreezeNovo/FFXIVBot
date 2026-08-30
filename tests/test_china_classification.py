from __future__ import annotations

import unittest

from ff14_core.china_classification import classify_event_candidate
from ff14_core.source_models import EventCandidate


def candidate(title: str, kind: str = "activity") -> EventCandidate:
    return EventCandidate(
        source_id="china_news_api",
        document_id="1",
        title=title,
        candidate_id="candidate",
        kind=kind,
        label="活动时间",
        evidence_text="活动时间：2026年8月1日16:00～2026年8月2日16:00",
        start_at="2026-08-01T16:00:00+08:00",
        end_at="2026-08-02T16:00:00+08:00",
        timezone="Asia/Shanghai",
        confidence="high",
    )


def labeled_candidate(title: str, label: str) -> EventCandidate:
    result = candidate(title)
    return EventCandidate(
        source_id=result.source_id,
        document_id=result.document_id,
        title=result.title,
        candidate_id=result.candidate_id,
        kind=result.kind,
        label=label,
        evidence_text=result.evidence_text,
        start_at=result.start_at,
        end_at=result.end_at,
        timezone=result.timezone,
        confidence=result.confidence,
    )


class ChinaClassificationTests(unittest.TestCase):
    def test_collaboration_time_label_uses_collaboration_category(self) -> None:
        result = classify_event_candidate(
            labeled_candidate("开始涂鸦！狂热糖潮", "联动开启时间")
        )

        self.assertEqual(result.classification, "collaboration_event")
        self.assertEqual(result.classification_label, "联动活动")

    def test_collaboration_uses_one_calendar_category(self) -> None:
        result = classify_event_candidate(candidate("【季节活动】妖怪手表联动再启"))

        self.assertEqual(result.classification, "collaboration_event")
        self.assertEqual(result.classification_label, "联动活动")
        self.assertEqual(result.routing, "calendar_candidate")

    def test_maintenance_is_not_a_calendar_event(self) -> None:
        result = classify_event_candidate(candidate("全区全服临时维护公告", kind="maintenance"))

        self.assertEqual(result.classification, "maintenance")
        self.assertEqual(result.routing, "maintenance_candidate")

    def test_monthly_card_campaign_is_a_calendar_candidate(self) -> None:
        result = classify_event_candidate(candidate("月卡礼赠！限时赠送"))

        self.assertEqual(result.classification, "operational_campaign")
        self.assertEqual(result.classification_label, "运营活动")
        self.assertEqual(result.routing, "calendar_candidate")

    def test_unknown_timed_announcement_is_other_operational(self) -> None:
        result = classify_event_candidate(candidate("限时开放公告"))

        self.assertEqual(result.classification, "other_operational")
        self.assertEqual(result.classification_label, "其他运营活动")
        self.assertEqual(result.routing, "calendar_candidate")

    def test_operational_window_is_a_calendar_candidate(self) -> None:
        result = classify_event_candidate(candidate("时装灵感季", kind="operational"))

        self.assertEqual(result.classification, "operational_campaign")
        self.assertEqual(result.routing, "calendar_candidate")

    def test_live_stream_is_a_calendar_candidate(self) -> None:
        result = classify_event_candidate(candidate("官方直播", kind="live_stream"))

        self.assertEqual(result.classification_label, "直播或转播")
        self.assertEqual(result.routing, "calendar_candidate")


if __name__ == "__main__":
    unittest.main()
