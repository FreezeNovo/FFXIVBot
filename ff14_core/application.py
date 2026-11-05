"""FFXIVBot 应用服务；不依赖 NoneBot 或 FastAPI。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .calendar import ActivityDigestWorkflow, CalendarQueryService
from .news import AnnouncementWorkflow
from .lodestone import LodestoneDetailService
from .maintenance import MaintenanceQueryService, MaintenanceWorkflow
from .config import AppConfig
from .messaging import CommandRequest, CommandResult, OutboundMessage
from .sources import ChinaNewsDetailService, OfficialSourceService
from .status_service import StatusService
from .storage import StateStore

LOGGER = logging.getLogger(__name__)


@dataclass
class CoreApplication:
    """应用生命周期中共享的 Core 服务集合。"""

    config: AppConfig
    store: StateStore
    status_service: StatusService
    source_service: OfficialSourceService
    china_detail_service: ChinaNewsDetailService
    calendar_query_service: CalendarQueryService
    digest_workflow: ActivityDigestWorkflow
    maintenance_workflow: MaintenanceWorkflow
    command_service: "CommandService"
    announcement_workflow: AnnouncementWorkflow
    lodestone_detail_service: LodestoneDetailService

    @classmethod
    def create(cls, config: AppConfig) -> "CoreApplication":
        store = StateStore(config.database_path)
        status_service = StatusService(config, store=store)
        source_service = OfficialSourceService(store)
        china_detail_service = ChinaNewsDetailService(store)
        calendar_query_service = CalendarQueryService(store)
        digest_workflow = ActivityDigestWorkflow(store, calendar_query_service)
        maintenance_workflow = MaintenanceWorkflow(
            store, MaintenanceQueryService(store)
        )
        command_service = CommandService(
            config=config,
            status_service=status_service,
            source_service=source_service,
            china_detail_service=china_detail_service,
            digest_workflow=digest_workflow,
            maintenance_workflow=maintenance_workflow,
        )
        return cls(
            config=config,
            store=store,
            status_service=status_service,
            source_service=source_service,
            china_detail_service=china_detail_service,
            calendar_query_service=calendar_query_service,
            digest_workflow=digest_workflow,
            maintenance_workflow=maintenance_workflow,
            command_service=command_service,
            announcement_workflow=AnnouncementWorkflow(store),
            lodestone_detail_service=LodestoneDetailService(store),
        )

    def start(self) -> None:
        self.status_service.start()
        self.store.recover_interrupted_deliveries()


class CommandService:
    """FF14 命令分派；消息平台只负责把请求和响应接进来。"""

    def __init__(
        self,
        *,
        config: AppConfig,
        status_service: StatusService,
        source_service: OfficialSourceService,
        china_detail_service: ChinaNewsDetailService,
        digest_workflow: ActivityDigestWorkflow,
        maintenance_workflow: MaintenanceWorkflow | None = None,
    ) -> None:
        self._config = config
        self._status_service = status_service
        self._source_service = source_service
        self._china_detail_service = china_detail_service
        self._digest_workflow = digest_workflow
        self._maintenance_workflow = maintenance_workflow

    async def handle(self, request: CommandRequest) -> CommandResult:
        if request.target.target_type != "group" or not self._status_service.is_target_enabled(request.target):
            return CommandResult(handled=False)

        action = " ".join(request.text.strip().lower().split())
        if action in {"", "状态", "status"}:
            if not self._is_owner(request):
                return self._message("FF14 Bot：此诊断命令仅限 Bot 所有者使用。")
            return self._message(
                self._status_service.render_status(request.target)
            )

        if action in {"活动", "activity", "日历", "calendar"}:
            if not self._is_owner(request) and not self._status_service.can_query(request.target, "activity"):
                return self._message("FF14 Bot：本群尚未开启普通群员活动查询。")
            return self._message(self._digest_workflow.preview(request.target))

        if action in {"维护", "maintenance"}:
            if not self._is_owner(request) and not self._status_service.can_query(request.target, "maintenance"):
                return self._message("FF14 Bot：本群尚未开启普通群员维护查询。")
            if self._maintenance_workflow is None:
                return CommandResult(handled=False)
            return self._message(self._maintenance_workflow.preview(request.target))

        if action in {
            "数据源",
            "source",
            "数据源 状态",
            "source status",
            "数据源 国服详情",
            "source china details",
            "数据源 国服详情 扩展",
            "source china details extended",
            "国服活动候选",
            "china candidates",
        } and not self._is_owner(request):
            return self._message("FF14 Bot：此诊断命令仅限 Bot 所有者使用。")

        if action in {"数据源", "source"}:
            try:
                results = await asyncio.to_thread(self._source_service.refresh)
            except Exception:
                LOGGER.exception("官方数据源手动刷新失败")
                return self._message(
                    "官方数据源刷新失败，已记录日志并保留此前有效快照。"
                )
            china_source_succeeded = any(
                result.source_id == "china_news_api" and result.success
                for result in results
            )
            lines = [self._source_service.render_refresh(results)]
            if china_source_succeeded:
                try:
                    detail_result = await asyncio.to_thread(
                        self._china_detail_service.refresh_cached_documents,
                        40,
                        0.15,
                    )
                    lines.append(self._china_detail_service.render_refresh(detail_result))
                except Exception:
                    LOGGER.exception("官方数据源同步后的国服详情解析失败")
                    lines.append(
                        "国服活动解析失败，已保留此前有效快照；可稍后重试"
                        " ff14 数据源 国服详情。"
                    )
            return self._message("\n".join(lines))

        if action in {"数据源 状态", "source status"}:
            return self._message(self._source_service.render_health())

        if action in {
            "数据源 国服详情 扩展",
            "source china details extended",
        }:
            try:
                result = await asyncio.to_thread(
                    self._china_detail_service.refresh_cached_documents,
                    40,
                    0.15,
                )
            except Exception:
                LOGGER.exception("国服扩展活动详情解析失败")
                return self._message(
                    "国服扩展活动详情解析失败，已记录日志并保留此前有效快照。"
                )
            return self._message(self._china_detail_service.render_refresh(result))

        if action in {"数据源 国服详情", "source china details"}:
            try:
                result = await asyncio.to_thread(
                    self._china_detail_service.refresh_cached_documents
                )
            except Exception:
                LOGGER.exception("国服活动详情解析失败")
                return self._message(
                    "国服活动详情解析失败，已记录日志并保留此前有效快照。"
                )
            return self._message(self._china_detail_service.render_refresh(result))

        if action in {"国服活动候选", "china candidates"}:
            return self._message(self._china_detail_service.render_candidates())

        return CommandResult(handled=False)

    def _is_owner(self, request: CommandRequest) -> bool:
        return self._status_service.is_owner(
            request.actor_id
        )

    @staticmethod
    def _message(text: str) -> CommandResult:
        return CommandResult(handled=True, message=OutboundMessage(text=text))
