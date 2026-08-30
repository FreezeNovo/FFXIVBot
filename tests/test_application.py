from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from ff14_core.calendar import ActivityDigestWorkflow, CalendarQueryService
from ff14_core.maintenance import MaintenanceQueryService, MaintenanceWorkflow
from ff14_core.application import CommandService
from ff14_core.config import AppConfig
from ff14_core.messaging import CommandRequest, TargetRef
from ff14_core.sources import (
    ChinaNewsDetailService,
    ChinaDetailRefreshResult,
    OfficialSourceService,
    SourceFetchResult,
)
from ff14_core.status_service import StatusService
from ff14_core.storage import StateStore


class ApplicationCommandTests(unittest.TestCase):
    def _service(self, owner_user_id: str | None = None):
        temporary_directory = tempfile.TemporaryDirectory()
        target = TargetRef.for_onebot_group("group-test")
        config = AppConfig(database_path=Path(temporary_directory.name) / "state.sqlite3")
        store = StateStore(config.database_path)
        status = StatusService(config, store=store)
        status.start()
        store.save_onebot_group("group-test", enabled=True)
        store.set_owner_user_id(owner_user_id)
        service = CommandService(
            config=config,
            status_service=status,
            source_service=OfficialSourceService(store),
            china_detail_service=ChinaNewsDetailService(store),
            digest_workflow=ActivityDigestWorkflow(store, CalendarQueryService(store)),
            maintenance_workflow=MaintenanceWorkflow(store, MaintenanceQueryService(store)),
        )
        return temporary_directory, target, service

    def test_status_command_is_framework_independent(self) -> None:
        temporary_directory, target, service = self._service()
        self.addCleanup(temporary_directory.cleanup)

        result = asyncio.run(
            service.handle(
                CommandRequest(
                    target=target,
                    actor_id="member",
                    actor_role=None,
                    text="状态",
                )
            )
        )

        self.assertTrue(result.handled)
        self.assertIsNotNone(result.message)
        self.assertIn("FF14 Bot 状态", result.message.text)

    def test_diagnostic_command_requires_owner(self) -> None:
        temporary_directory, target, service = self._service(owner_user_id="owner")
        self.addCleanup(temporary_directory.cleanup)

        result = asyncio.run(
            service.handle(
                CommandRequest(
                    target=target,
                    actor_id="member",
                    actor_role=None,
                    text="数据源 状态",
                )
            )
        )

        self.assertTrue(result.handled)
        self.assertIn("仅限 Bot 所有者", result.message.text)

    def test_source_status_command_renders_for_owner(self) -> None:
        temporary_directory, target, service = self._service(owner_user_id="owner")
        self.addCleanup(temporary_directory.cleanup)

        result = asyncio.run(
            service.handle(
                CommandRequest(
                    target=target,
                    actor_id="owner",
                    actor_role=None,
                    text="数据源 状态",
                )
            )
        )

        self.assertTrue(result.handled)
        self.assertIsNotNone(result.message)
        self.assertIn("官方数据源状态", result.message.text)

    def test_source_command_also_refreshes_china_details(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        config = AppConfig(database_path=Path(temporary_directory.name) / "state.sqlite3")
        store = StateStore(config.database_path)
        status = StatusService(config, store=store)
        status.start()
        store.save_onebot_group("group-test", enabled=True)
        store.set_owner_user_id("owner")

        class FixtureSource:
            source_id = "china_news_api"
            display_name = "国服官方新闻"

            def fetch(self, transport):
                return SourceFetchResult(
                    source_id=self.source_id,
                    display_name=self.display_name,
                    status_code=200,
                    raw_payload=b"fixture",
                    documents=(),
                    page_count=1,
                )

        class FixtureDetails:
            def __init__(self):
                self.calls = []

            def refresh_cached_documents(self, limit=10, pause_seconds=0.0):
                self.calls.append((limit, pause_seconds))
                return ChinaDetailRefreshResult(
                    attempted_count=1,
                    success_count=1,
                    candidate_count=2,
                    failure_count=0,
                )

            def render_refresh(self, result):
                return f"国服活动解析\n发现 {result.candidate_count} 条明确时间候选。"

        details = FixtureDetails()
        service = CommandService(
            config=config,
            status_service=status,
            source_service=OfficialSourceService(
                store,
                adapters=(FixtureSource(),),
            ),
            china_detail_service=details,
            digest_workflow=ActivityDigestWorkflow(store, CalendarQueryService(store)),
        )

        result = asyncio.run(
            service.handle(
                CommandRequest(
                    target=TargetRef.for_onebot_group("group-test"),
                    actor_id="owner",
                    actor_role=None,
                    text="数据源",
                )
            )
        )

        self.assertTrue(result.handled)
        self.assertIn("国服活动解析", result.message.text)
        self.assertIn("发现 2 条明确时间候选", result.message.text)
        self.assertEqual(details.calls, [(40, 0.15)])

    def test_unknown_command_is_silent(self) -> None:
        temporary_directory, target, service = self._service()
        self.addCleanup(temporary_directory.cleanup)

        result = asyncio.run(
            service.handle(
                CommandRequest(
                    target=target,
                    actor_id="member",
                    actor_role=None,
                    text="未知命令",
                )
            )
        )

        self.assertFalse(result.handled)
        self.assertIsNone(result.message)

    def test_maintenance_command_is_framework_independent(self) -> None:
        temporary_directory, target, service = self._service()
        self.addCleanup(temporary_directory.cleanup)

        result = asyncio.run(
            service.handle(
                CommandRequest(
                    target=target,
                    actor_id="member",
                    actor_role=None,
                    text="维护",
                )
            )
        )

        self.assertTrue(result.handled)
        self.assertIsNotNone(result.message)
        self.assertIn("FF14 国服维护", result.message.text)


if __name__ == "__main__":
    unittest.main()
