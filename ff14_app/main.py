"""启动 NoneBot2、OneBot V11 Adapter 和 FFXIVBot 私有网关。"""

from __future__ import annotations

from pathlib import Path

import nonebot

from .runtime_lock import RuntimeLock
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from ff14_web.app import create_app


def main() -> None:
    with RuntimeLock(Path(__file__).resolve().parents[1] / "data" / "runtime.lock"):
        _run()


def _run() -> None:
    nonebot.init(command_start={"/", ""}, host="127.0.0.1")
    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)
    nonebot.load_plugin("plugins.qq_nonebot")
    from plugins.qq_nonebot import APPLICATION
    from .logging_setup import configure_logging
    configure_logging(APPLICATION.config.database_path.parent / "logs")

    # Adapter routes are registered before this mount, so OneBot paths remain intact
    # while the local management UI becomes the root application.
    nonebot.get_asgi().mount("/", create_app(APPLICATION.store))
    nonebot.run()


if __name__ == "__main__":
    main()
