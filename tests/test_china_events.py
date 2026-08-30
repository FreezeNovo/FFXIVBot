from __future__ import annotations

import unittest

from ff14_core.china_events import extract_plain_text, parse_event_candidates


class ChinaEventParserTests(unittest.TestCase):
    def test_parses_date_only_collaboration_window(self) -> None:
        candidates = parse_event_candidates(
            source_id="china_news_api",
            document_id="392085",
            title="开始涂鸦！联动活动",
            content_text="联动开启时间：2026年8月18日至2026年12月31日",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].kind, "activity")
        self.assertEqual(candidates[0].start_at, "2026-08-18T00:00:00+08:00")
        self.assertEqual(candidates[0].end_at, "2026-12-31T23:59:59+08:00")

    def test_parses_explicit_seasonal_event_range(self) -> None:
        text = extract_plain_text(
            "<p>活动时间：2026年8月12日16:00～2026年8月26日22:59</p>"
        )
        candidates = parse_event_candidates(
            source_id="china_news_api",
            document_id="391661",
            title="【季节活动】红莲节与海边的嫩叶们",
            content_text=text,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].kind, "activity")
        self.assertEqual(candidates[0].start_at, "2026-08-12T16:00:00+08:00")
        self.assertEqual(candidates[0].end_at, "2026-08-26T22:59:00+08:00")

    def test_parses_same_day_maintenance_range(self) -> None:
        candidates = parse_event_candidates(
            source_id="china_news_api",
            document_id="391743",
            title="临时维护公告",
            content_text="预计维护时间：2026年8月7日14:00-18:00",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].kind, "maintenance")
        self.assertEqual(candidates[0].end_at, "2026-08-07T18:00:00+08:00")

    def test_does_not_infer_start_time_from_immediately(self) -> None:
        candidates = parse_event_candidates(
            source_id="china_news_api",
            document_id="391650",
            title="时装灵感季",
            content_text="搭配师报名：即日起 ~ 2026年9月4日14:00",
        )

        self.assertEqual(candidates, ())

    def test_parses_cross_month_explicit_opening_window(self) -> None:
        candidates = parse_event_candidates(
            source_id="china_news_api",
            document_id="389245",
            title="时装灵感季开启",
            content_text="专题页开放时间：2026年6月15日 14:00 ~ 7月9日 14:00",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].kind, "activity")
        self.assertEqual(candidates[0].end_at, "2026-07-09T14:00:00+08:00")

    def test_parses_operational_window_but_marks_its_kind(self) -> None:
        candidates = parse_event_candidates(
            source_id="china_news_api",
            document_id="391650",
            title="时装灵感季第二期",
            content_text="领取优惠券：2026年8月7日 14:00 ~ 2026年9月4日 14:00",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].kind, "operational")

    def test_parses_clock_words_and_cross_year_end_date(self) -> None:
        candidates = parse_event_candidates(
            source_id="china_news_api",
            document_id="fixture",
            title="跨年活动",
            content_text="活动时间：2026年12月31日晚上11点～1月1日凌晨1点30分",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].start_at, "2026-12-31T23:00:00+08:00")
        self.assertEqual(candidates[0].end_at, "2027-01-01T01:30:00+08:00")

    def test_does_not_create_range_when_only_start_time_is_explicit(self) -> None:
        candidates = parse_event_candidates(
            source_id="china_news_api",
            document_id="389539",
            title="LIVE直播特别篇",
            content_text="直播时间：2026年6月18日19:00~",
        )

        self.assertEqual(candidates, ())


if __name__ == "__main__":
    unittest.main()
