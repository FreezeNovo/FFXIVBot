"""逐文章的有效活动核实证据与新鲜度判定。"""
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone


@dataclass(frozen=True)
class EventFreshness:
    state: str
    last_success_at: str | None
    last_attempt_at: str | None
    age_seconds: float | None
    error: str | None

    @property
    def warning(self):
        if self.state=='valid':return None
        age='未核实' if self.age_seconds is None else f'{self.age_seconds/3600:.1f}小时'
        return f'最后成功核实：{self.last_success_at or "未知"}；数据年龄 {age}；{self.error or self.state}'


def evaluate_freshness(record,now,settings):
    if now.tzinfo is None:raise ValueError('now必须带时区')
    if not record or not record['last_success_at']:
        return EventFreshness('unverified',None,record['last_attempt_at'] if record else None,None,record['error'] if record else '无可信逐活动核实记录')
    age=max(0,(now-datetime.fromisoformat(record['last_success_at'])).total_seconds())
    state='stale' if age>=settings.stop_hours*3600 else ('warning' if record['error'] or age>=settings.warning_hours*3600 else 'valid')
    return EventFreshness(state,record['last_success_at'],record['last_attempt_at'],age,record['error'])
