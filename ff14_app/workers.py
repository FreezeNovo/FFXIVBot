"""唯一应用的后台唤醒器，业务状态保存在 SQLite。"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
import logging
from .reviews import ReviewCoordinator

LOGGER = logging.getLogger("ffxivbot.runtime")


class ApplicationWorkers:
    def __init__(self, application, messaging):
        self.application = application
        self.messaging = messaging
        self.tasks = set()
        self._discovery_lock = asyncio.Lock()
        self._detail_lock = asyncio.Lock()
        self.reviews = ReviewCoordinator(application)

    def start(self):
        if self.tasks:
            return
        self.reviews.stopped=False
        self.application.source_service.stopped.clear()
        self.application.china_detail_service.stopped.clear()
        self.application.lodestone_detail_service.stopped.clear()
        self.tasks.update(asyncio.create_task(self._loop(work)) for work in (self.discover, self.review, self.deliver))

    async def stop(self):
        self.application.source_service.stopped.set()
        self.application.china_detail_service.stopped.set()
        self.application.lodestone_detail_service.stopped.set()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        await self.reviews.stop()

    async def _loop(self, work):
        while True:
            try:
                await work()
            except Exception:
                LOGGER.exception("后台阶段失败 stage=%s", work.__name__)
            await asyncio.sleep(1 if work == self.discover else 30)

    async def discover(self, *, now=None):
        if self._discovery_lock.locked():
            return
        async with self._discovery_lock:
            current = now or datetime.now(timezone.utc)
            store = self.application.store
            interval = store.get_runtime_settings().discovery_minutes
            previous = store.get_runtime_probes().get('discovery')
            if previous and current < datetime.fromisoformat(previous['attempted_at']) + timedelta(minutes=interval):
                return
            store.record_runtime_probe('discovery', current.isoformat(), 'running')
            results = await asyncio.to_thread(self.application.source_service.refresh)
            store.record_runtime_probe('discovery', current.isoformat(), '; '.join(f'{r.source_id}:{"ok" if r.success else r.error}' for r in results))

    async def review(self, *, now=None):
        if self._detail_lock.locked():
            return
        async with self._detail_lock:
            current = now or datetime.now(timezone.utc)
            store = self.application.store
            previous = store.get_runtime_probes().get('details')
            if previous and current < datetime.fromisoformat(previous['attempted_at']) + timedelta(minutes=1):
                return
            store.record_runtime_probe('details', current.isoformat(), 'running')
            await self.reviews.background(now=current)
            store.record_runtime_probe('details',current.isoformat(),'completed; see per-document verification')

    async def deliver(self, *, now=None):
        self.reviews.preflight(now=now or datetime.now(timezone.utc))
        await self.application.digest_workflow.tick(self.messaging, now=now)
        await self.application.maintenance_workflow.tick(self.messaging, now=now)
        await self.application.announcement_workflow.tick(self.messaging, now=now, online=getattr(self.messaging,"is_available",lambda:True)())
