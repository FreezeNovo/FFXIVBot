"""最小状态服务，供 QQ 适配层与未来 Web API 共用。"""

from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .messaging import TargetRef
from .storage import StateStore


@dataclass(frozen=True)
class StatusSnapshot:
    ready: bool
    target_enabled: bool
    schema_version: str | None


class StatusService:
    """管理最小实例状态，不处理任何 QQ 事件。"""

    def __init__(self, config: AppConfig, store: StateStore | None = None) -> None:
        self._config = config
        self._store = store or StateStore(config.database_path)
        self._started = False

    def start(self) -> None:
        self._store.initialize()
        self._started = True

    def is_target_enabled(self, target: TargetRef) -> bool:
        return self._started and self._store.is_target_enabled(target.target_id)

    def is_owner(self, user_id: str) -> bool:
        return self._started and self._store.get_owner_user_id() == user_id

    def can_query(self, target: TargetRef, feature: str) -> bool:
        policy = self._store.get_manual_query_policy(target.target_id)
        return policy.activity_enabled if feature == "activity" else policy.maintenance_enabled

    def snapshot_for_target(self, target: TargetRef) -> StatusSnapshot:
        return StatusSnapshot(
            ready=self._started,
            target_enabled=self.is_target_enabled(target),
            schema_version=self._store.get_meta("schema_version") if self._started else None,
        )

    def render_status(self, target: TargetRef) -> str:
        snapshot = self.snapshot_for_target(target)
        target_state = "已启用" if snapshot.target_enabled else "未启用"
        runtime_state = "已启动" if snapshot.ready else "未启动"
        return "\n".join(
            (
                "FF14 Bot 状态",
                f"运行状态：{runtime_state}",
                f"当前目标：{target.display_name or target.provider_target_id}（{target_state}）",
                "活动数据源：国服新闻与详情解析",
                "活动摘要与维护提醒：由 WebUI 按群配置",
                "市场查询：未启用",
            )
        )
