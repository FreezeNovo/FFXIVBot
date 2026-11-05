"""本机应用设置；连接凭据仍由环境配置管理。"""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class RuntimeSettings:
    discovery_minutes: int = 15
    preflight_minutes: int = 5
    warning_hours: int = 48
    stop_hours: int = 72

    def validate(self) -> None:
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError("运行参数必须为正整分钟/小时")
        if self.warning_hours >= self.stop_hours:
            raise ValueError("警告阈值必须小于停发阈值")
