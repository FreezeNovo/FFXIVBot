"""NA已发现文章的直接正文复查，复用版本、候选与账本。"""
from dataclasses import replace
import threading
from .china_events import extract_plain_text
from .na_events import LodestoneBodyParser,parse_na_candidates,date_only_evidence,PARSER_VERSION
from .source_models import ChinaNewsDetail
from .sources import UrllibHttpTransport,ChinaDetailRefreshResult,ChinaDetailFailure


class LodestoneDetailService:
    def __init__(self,store,transport=None):
        self._store=store;self._transport=transport or UrllibHttpTransport(timeout_seconds=12,max_attempts=2)
        self.stopped=threading.Event();self._lock=threading.Lock()
        self._document_locks={};self._document_locks_guard=threading.Lock()

    def refresh_document(self, document):
        with self._document_locks_guard:
            lock=self._document_locks.setdefault((document.source_id,document.document_id),threading.Lock())
        with lock:
            if self.stopped.is_set():return False
            return self._refresh_document(document)

    def _refresh_document(self,document):
        from urllib.parse import urlsplit
        if urlsplit(document.url).hostname!='na.finalfantasyxiv.com' or not any(p in urlsplit(document.url).path for p in ('/lodestone/news/detail/','/lodestone/topics/detail/')):raise ValueError('非已核验NA正文地址')
        self._store.record_verification_attempt(document.source_id,document.document_id)
        validators=self._store.get_body_validators(document.source_id,document.document_id)
        headers={key:value for key,value in zip(('If-None-Match','If-Modified-Since'),validators[:2] if validators else ()) if value}
        getter=getattr(self._transport,'get_with_headers',None)
        conditional_sent=bool(headers and callable(getter))
        response=getter(document.url,headers) if conditional_sent else self._transport.get(document.url)
        if self.stopped.is_set():return False
        if response.status_code==304:
            if not conditional_sent or not validators:raise ValueError('304缺少已发送的逐篇条件验证器')
            version=self._store.get_document_version(validators[2])
            if version is None:raise ValueError('304缺少对应有效正文版本')
            full_document=replace(document,title=version.title);body=version.content_html
        elif response.status_code==200:
            parser=LodestoneBodyParser();parser.feed(response.body.decode('utf-8'))
            if not parser.body or parser.depth:raise ValueError('NA正文wrapper缺失或结构异常')
            body=parser.body;full_document=replace(document,title=parser.title or document.title)
        else:raise ValueError(f'HTTP {response.status_code}')
        version_id=self._store.record_document_version(full_document,body,ready=False)
        if response.status_code==200:self._store.set_body_validators(document.source_id,document.document_id,version_id,response.headers)
        candidates=parse_na_candidates(full_document,body)
        self._store.record_time_evidence(version_id,date_only_evidence(full_document,body))
        detail=ChinaNewsDetail(document.source_id,document.document_id,full_document.title,document.published_at,body,extract_plain_text(body))
        self._store.record_china_detail_success(detail,candidates,PARSER_VERSION)
        return True

    def refresh_cached_documents(self,limit=40):
        with self._lock:
            documents=tuple(d for source in ('lodestone_atom','lodestone_topics') for d in self._store.get_documents_needing_detail_review(source,limit=limit,parser_version=PARSER_VERSION))
            success=0;failures=[]
            for document in documents[:limit]:
                if self.stopped.is_set():break
                try:success+=int(self.refresh_document(document))
                except Exception as error:
                    if self.stopped.is_set():break
                    from .sources import _error_summary
                    message=_error_summary(error)
                    self._store.record_detail_parse_failure(source_id=document.source_id,document_id=document.document_id,error=message)
                    failures.append(ChinaDetailFailure(document.document_id,document.title,message))
            return ChinaDetailRefreshResult(len(documents[:limit]),success,0,len(failures),tuple(failures))
