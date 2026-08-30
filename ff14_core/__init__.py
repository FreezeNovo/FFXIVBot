"""与消息平台无关的 FF14 核心服务。"""

from .config import AppConfig
from .status_service import StatusService

__all__ = ["AppConfig", "StatusService"]
