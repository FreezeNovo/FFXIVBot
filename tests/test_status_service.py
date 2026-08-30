from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ff14_core.config import AppConfig
from ff14_core.messaging import TargetRef
from ff14_core.status_service import StatusService
from ff14_core.storage import StateStore


class StatusServiceTests(unittest.TestCase):
    def test_only_configured_group_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = TargetRef.for_onebot_group("group-test")
            config = AppConfig(database_path=Path(temporary_directory) / "state.sqlite3")
            store = StateStore(config.database_path)
            service = StatusService(config, store=store)
            service.start()
            store.save_onebot_group("group-test", enabled=True)

            self.assertTrue(service.is_target_enabled(target))
            self.assertFalse(
                service.is_target_enabled(TargetRef.for_onebot_group("other-group"))
            )

    def test_status_text_exposes_only_current_minimal_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = TargetRef.for_onebot_group("group-test", display_name="测试群")
            config = AppConfig(database_path=Path(temporary_directory) / "state.sqlite3")
            store = StateStore(config.database_path)
            service = StatusService(config, store=store)
            service.start()
            store.save_onebot_group("group-test", enabled=True)

            text = service.render_status(target)
            self.assertIn("FF14 Bot 状态", text)
            self.assertIn("当前目标：测试群（已启用）", text)
            self.assertIn("活动数据源：国服新闻与详情解析", text)
            self.assertIn("活动摘要与维护提醒：由 WebUI 按群配置", text)
            self.assertIn("市场查询：未启用", text)

    def test_owner_and_target_changes_take_effect_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = TargetRef.for_onebot_group("group-test")
            config = AppConfig(database_path=Path(temporary_directory) / "state.sqlite3")
            store = StateStore(config.database_path)
            service = StatusService(config, store=store)
            service.start()

            self.assertFalse(service.is_target_enabled(target))
            self.assertFalse(service.is_owner("owner"))

            store.save_onebot_group("group-test", enabled=True)
            store.set_owner_user_id("owner")

            self.assertTrue(service.is_target_enabled(target))
            self.assertTrue(service.is_owner("owner"))

            store.save_onebot_group("group-test", enabled=False)
            store.set_owner_user_id(None)

            self.assertFalse(service.is_target_enabled(target))
            self.assertFalse(service.is_owner("owner"))


if __name__ == "__main__":
    unittest.main()
