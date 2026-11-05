"""有界正文复查；预查与后台任务按文章合并，发送时不等待HTTP。"""
import asyncio
from itertools import islice, zip_longest
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from ff14_core.sources import PARSER_VERSION
from ff14_core.na_events import PARSER_VERSION as NA_PARSER_VERSION
from ff14_core.diagnostics import diagnostic,safe_reason


class ReviewCoordinator:
    def __init__(self,application):
        self.app=application;self.tasks={};self.semaphore=asyncio.Semaphore(4);self.stopped=False;self._scheduled_windows=set()

    def schedule(self,document,*,now):
        key=(document.source_id,document.document_id)
        previous=self.tasks.get(key)
        if previous and not previous.done():return previous
        self.app.store.record_verification_attempt(*key,now=now.isoformat())
        task=asyncio.create_task(self._review(document));self.tasks[key]=task
        return task

    async def _review(self,document):
        async with self.semaphore:
            if self.stopped:return
            service=self.app.china_detail_service if document.source_id=='china_news_api' else self.app.lodestone_detail_service
            try:
                await asyncio.to_thread(service.refresh_document,document)
                record=self.app.store.get_event_verification(document.source_id,document.document_id)
                return not record or record["error_kind"] not in {"detail_failed","parse_zero"}
            except Exception as error:
                if self.stopped:return
                reason=safe_reason(f'{type(error).__name__}: {error}')
                self.app.store.record_detail_parse_failure(source_id=document.source_id,document_id=document.document_id,error=reason)
                diagnostic(stage='detail_review',source_id=document.source_id,document_id=document.document_id,action='preserve_last_valid',error=reason)

    async def background(self,*,now):
        self.tasks={key:task for key,task in self.tasks.items() if not task.done()}
        queues=[]
        for source,parser in [('china_news_api',PARSER_VERSION),('lodestone_atom',NA_PARSER_VERSION),('lodestone_topics',NA_PARSER_VERSION)]:
            queues.append(self.app.store.get_documents_needing_detail_review(source,limit=40,parser_version=parser))
        # Interleave sources before applying the shared budget, keeping each source's review order.
        documents=list(islice((document for row in zip_longest(*queues)
                              for document in row if document is not None),40))
        # Only four network requests can be active. Queue length is bounded per pass.
        results=await asyncio.gather(*(self.schedule(d,now=now) for d in documents))
        for source in {d.source_id for d in documents}:
            outcomes=[result for d,result in zip(documents,results) if d.source_id==source]
            identity='china_news_detail' if source=='china_news_api' else source+'_detail'
            if any(outcomes):self.app.store.record_source_probe_success(source_id=identity,display_name=identity,document_count=sum(bool(x) for x in outcomes))
            if not all(outcomes):self.app.store.record_source_failure(source_id=identity,display_name=identity,error='部分正文复查失败；查看逐活动核实记录')

    def preflight(self,*,now):
        store=self.app.store;lead=timedelta(minutes=store.get_runtime_settings().preflight_minutes)
        for managed,policy in store.list_enabled_activity_digest_targets():
            local=now.astimezone(ZoneInfo(policy.timezone))
            scheduled=local.replace(hour=policy.digest_hour,minute=policy.digest_minute,second=0,microsecond=0)
            if local<scheduled-lead:continue
            window=scheduled.isoformat()
            if store.digest_window_started(managed.target.target_id,scheduled.date().isoformat()):continue
            digest=self.app.calendar_query_service.query(managed.target,now=scheduled)
            keys={(e.source_id,e.document_id) for e in (*digest.ongoing,*digest.upcoming,*digest.ending)}
            for source,document_id in keys:
                identity=(managed.target.target_id,window,source,document_id)
                if identity in self._scheduled_windows:continue
                self._scheduled_windows.add(identity)
                store.reserve_preflight(managed.target.target_id,window,source,document_id,now=now)
                # A shared recent result already verifies this window for another group.
                record=store.get_event_verification(source,document_id)
                if record and record['last_success_at'] and datetime.fromisoformat(record['last_success_at'])>=scheduled-lead:
                    continue
                document=next((d for d in store.get_source_documents(source,100000) if d.document_id==document_id),None)
                if document:self.schedule(document,now=now)

    async def stop(self):
        self.stopped=True
        for task in self.tasks.values():task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)
        self.tasks.clear()
