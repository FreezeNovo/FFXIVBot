"""独立公告流：群级基线、最新内容、持久分页与既有投递账本。"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import json
from zoneinfo import ZoneInfo

from .documents import version_diff, fingerprint
from .delivery import dispatch_pending

SUPPORTED_REGIONS = ('cn', 'global-na')
ANNOUNCEMENT_CATEGORIES = ('8324','8325','8326','8327','5309','5310','5311','5312','5313','Notices','Topics','Updates','Status','unknown')


def document_region(source_id):
    return {'china_news_api':'cn','lodestone_atom':'global-na','lodestone_topics':'global-na'}.get(source_id)


def published_moment(value, region):
    if not value:
        return None
    try:
        moment=datetime.fromisoformat(value.replace('/','-'))
        if moment.tzinfo is None:
            if region != 'cn':return None
            moment=moment.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        return moment.astimezone(timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class AnnouncementPolicy:
    enabled: bool = False
    regions: tuple[str,...] = ('cn',)
    categories: tuple[str,...] = ANNOUNCEMENT_CATEGORIES
    mode: str = 'immediate'
    digest_hour: int = 20
    digest_minute: int = 0

    def validate(self):
        if any(r not in SUPPORTED_REGIONS for r in self.regions) or len(set(self.regions)) != len(self.regions):
            raise ValueError('不支持或重复的公告区域')
        if any(c not in ANNOUNCEMENT_CATEGORIES for c in self.categories):
            raise ValueError('不支持的公告分类')
        if self.mode not in {'immediate','digest'} or not 0<=self.digest_hour<=23 or not 0<=self.digest_minute<=59:
            raise ValueError('公告模式或汇总时间无效')


class AnnouncementRenderer:
    def render_item(self, version, before, reason):
        region='C' if document_region(version.source_id)=='cn' else 'NA'
        stamp=published_moment(version.published_at,document_region(version.source_id))
        labels={'Notices':'公告','Topics':'专题','Updates':'更新','Status':'状态'}
        lines=[f'{region}-{version.title[:160]}',f'分类：{labels.get(version.category, "公告 / "+version.category)}',f'{reason}；官方发布时间：'+(stamp.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y/%m/%d %H:%M 北京时间') if stamp else '未知，待核查')]
        if before is not None:
            lines.append(version_diff(before,version)[:200])
        else:
            lines.append(version.normalized['text'][:200])
        lines.append('详情：'+version.url[:250])
        return '\n'.join(lines)

    def pages(self, items, kind):
        header={'immediate':'FF14 新公告','digest':'FF14 公告汇总','recovery':'FF14 离线补发汇总'}[kind]
        pages=[];members=[];text=header
        for item_id,rendered in items:
            if len(text)+len(rendered)+2>900 and members:
                pages.append((text,tuple(members)));text=header;members=[]
            text+='\n\n'+rendered;members.append(item_id)
        if members:pages.append((text,tuple(members)))
        return pages


class AnnouncementWorkflow:
    STREAM='announcement'

    def __init__(self,store,renderer=None):
        self._store=store
        self._renderer=renderer or AnnouncementRenderer()
        self._started=False

    def _observe(self, target_id, policy, current):
        store=self._store
        versions=store.get_document_versions(limit=100000)
        by_document={}
        for v in reversed(versions):
            by_document.setdefault((v.source_id,v.document_id),[]).append(v)
        with store._connection() as c:
            c.execute('BEGIN IMMEDIATE')
            scopes={(r[0],r[1]):(r[2],r[3]) for r in c.execute('SELECT region,category,watermark_at,epoch FROM news_scope WHERE target_id=? AND active=1',(target_id,))}
            for (source,document),history in by_document.items():
                latest=history[-1];region=document_region(source)
                if not c.execute("SELECT ready FROM article_version WHERE version_id=?",(latest.version_id,)).fetchone()[0]:continue
                scope=scopes.get((region,latest.category))
                if not scope:continue
                watermark,epoch=scope;start=datetime.fromisoformat(watermark)
                # Maintenance is owned by the independent stream, even when no exact interval exists.
                if latest.category=='Maintenance' or (source=='china_news_api' and '维护' in latest.title) or c.execute("SELECT 1 FROM event_candidate WHERE source_id=? AND document_id=? AND kind='maintenance' LIMIT 1",(source,document)).fetchone():continue
                row=c.execute('SELECT baseline_version,last_sent_version FROM news_observation WHERE target_id=? AND source_id=? AND document_id=? AND epoch=?',(target_id,source,document,epoch)).fetchone()
                if row is None:
                    before_start=[v for v in history if datetime.fromisoformat(v.observed_at)<start]
                    baseline=(before_start[-1] if before_start else history[0])
                    c.execute('INSERT INTO news_observation VALUES(?,?,?,?,?,NULL)',(target_id,source,document,epoch,baseline.version_id))
                    sent=None
                else:
                    baseline=store.get_document_version(row[0]);sent=store.get_document_version(row[1]) if row[1] else None
                # Normalizer-only changes establish a new comparison baseline without a notification.
                if baseline.normalizer_version!=latest.normalizer_version:
                    c.execute('UPDATE news_observation SET baseline_version=? WHERE target_id=? AND source_id=? AND document_id=? AND epoch=?',(latest.version_id,target_id,source,document,epoch))
                    baseline=latest
                prior_items=c.execute('SELECT i.item_id,i.version_id,i.eligible_at,i.reason,i.notification_id,n.status FROM news_item i LEFT JOIN notification_candidate n ON n.notification_id=i.notification_id WHERE i.target_id=? AND i.source_id=? AND i.document_id=? AND i.epoch=? AND i.status="active" ORDER BY i.version_id',(target_id,source,document,epoch)).fetchall()
                frozen=[];editable=[]
                for item in prior_items:
                    has_attempt=c.execute("SELECT 1 FROM news_member m JOIN news_member peer ON peer.batch_id=m.batch_id JOIN delivery_attempt a ON a.notification_id=peer.notification_id WHERE m.notification_id=? AND a.outcome!='not_sent' LIMIT 1",(item[4],)).fetchone() if item[4] else None
                    if has_attempt or item[5] in {'sent','sending','uncertain','failed'}:
                        frozen.append(item)
                    elif current-datetime.fromisoformat(item[2])>timedelta(hours=72):
                        c.execute('UPDATE news_item SET status="expired" WHERE item_id=?',(item[0],))
                    else:
                        editable.append(item)
                # Unknown/in-flight versions are never repackaged. Their operator review remains in the ledger.
                if any(item[5] in {'sending','uncertain'} for item in frozen):continue
                # An unconfirmed page in a started batch keeps its content frozen. Its
                # successor compares with that content, and waits for the page to resolve.
                unresolved=next((item for item in reversed(frozen) if item[5]=='pending'),None)
                compare=(store.get_document_version(unresolved[1]) if unresolved else
                         sent if sent and sent.normalizer_version==latest.normalizer_version else baseline)
                # Formatting observations share the eligibility of their comparison-content run.
                content_first=latest
                for version in reversed(history[:-1]):
                    if (version.comparison_hash,version.normalizer_version)!=(latest.comparison_hash,latest.normalizer_version):break
                    content_first=version
                published=published_moment(baseline.published_at,region)
                if published is None:
                    from .diagnostics import diagnostic
                    diagnostic(stage="announcement",source_id=source,document_id=document,target_id=target_id,action="hold_publication_unknown")
                is_new=(sent is None and unresolved is None and published is not None
                        and start<=published<=current and current-published<=timedelta(hours=72)
                        and datetime.fromisoformat(baseline.observed_at)>=start)
                change=(latest.comparison_hash!=compare.comparison_hash and datetime.fromisoformat(content_first.observed_at)>=start and latest.normalizer_version==compare.normalizer_version)
                desired=is_new or change
                if any(item[1]>=content_first.version_id for item in frozen):
                    # Only the current unchanged run is already covered. A→B→A is a new run,
                    # so an older failed A must not suppress a later genuine return to A.
                    desired=False
                same=next((item for item in editable if item[1]==latest.version_id),None)
                for item in editable:
                    if not desired or item[1]!=latest.version_id:
                        c.execute('UPDATE news_item SET status="superseded" WHERE item_id=?',(item[0],))
                if not desired or same:continue
                # Preserve the eligibility evidence through merges and retries.
                content_item=c.execute('SELECT i.eligible_at FROM news_item i JOIN article_version v ON v.version_id=i.version_id WHERE i.target_id=? AND i.epoch=? AND i.source_id=? AND i.document_id=? AND i.version_id>=? AND v.comparison_hash=? AND v.normalizer_version=? ORDER BY i.rowid LIMIT 1',(target_id,epoch,source,document,content_first.version_id,latest.comparison_hash,latest.normalizer_version)).fetchone()
                eligible=min((item[2] for item in editable),default=(published.isoformat() if is_new else content_first.observed_at))
                if content_item:eligible=min(eligible,content_item[0])
                item_id=str(uuid4());reason='新文章' if is_new else '文章内容已变动，请自行核实影响'
                c.execute('INSERT OR IGNORE INTO news_item(item_id,target_id,source_id,document_id,epoch,version_id,eligible_at,reason,status) VALUES(?,?,?,?,?,?,?,?,"active")',(item_id,target_id,source,document,epoch,latest.version_id,eligible,reason))

    def plan_due(self, *, now=None, recovery=False):
        current=now or datetime.now(timezone.utc);created=0
        if current.tzinfo is None:raise ValueError('now必须包含时区')
        for managed in self._store.list_targets():
            target_id=managed.target.target_id;policy=self._store.get_announcement_policy(target_id)
            if not managed.enabled or not policy.enabled:continue
            self._observe(target_id,policy,current)
            with self._store._connection() as c:
                c.execute('BEGIN IMMEDIATE')
                # Only batches with no possible external effect can be replaced.
                batches=c.execute('SELECT batch_id,signature FROM news_batch WHERE target_id=?',(target_id,)).fetchall()
                frozen_batches=set()
                for batch_id,signature in batches:
                    if c.execute("SELECT 1 FROM news_member m JOIN delivery_attempt a ON a.notification_id=m.notification_id WHERE m.batch_id=? AND a.outcome!='not_sent' LIMIT 1",(batch_id,)).fetchone():
                        frozen_batches.add(batch_id)
                rows=c.execute('SELECT item_id,version_id,eligible_at,reason,notification_id,source_id,document_id,epoch FROM news_item WHERE target_id=? AND status="active" ORDER BY eligible_at,item_id',(target_id,)).fetchall()
                available=[]
                for row in rows:
                    if row[4] and c.execute('SELECT 1 FROM news_member WHERE notification_id=? AND batch_id IN ('+','.join('?' for _ in frozen_batches)+')',(row[4],*frozen_batches)).fetchone():continue
                    # A real successor is durable, but cannot race its frozen predecessor.
                    if c.execute("SELECT 1 FROM news_item prior JOIN notification_candidate n ON n.notification_id=prior.notification_id JOIN news_member m ON m.notification_id=n.notification_id JOIN news_member peer ON peer.batch_id=m.batch_id JOIN delivery_attempt a ON a.notification_id=peer.notification_id WHERE prior.target_id=? AND prior.source_id=? AND prior.document_id=? AND prior.epoch=? AND prior.item_id!=? AND prior.status='active' AND n.status IN ('pending','sending','uncertain') AND a.outcome!='not_sent' LIMIT 1",(target_id,row[5],row[6],row[7],row[0])).fetchone():continue
                    latest_ready=c.execute("SELECT ready FROM article_version WHERE source_id=? AND document_id=? ORDER BY version_id DESC LIMIT 1",(row[5],row[6])).fetchone()
                    if latest_ready and not latest_ready[0]:continue
                    version=self._store.get_document_version(row[1])
                    active=c.execute('SELECT 1 FROM news_scope WHERE target_id=? AND epoch=? AND active=1 AND region=? AND category=?',(target_id,row[7],document_region(row[5]),version.category)).fetchone()
                    if not active or current-datetime.fromisoformat(row[2])>timedelta(hours=72):
                        c.execute('UPDATE news_item SET status="expired" WHERE item_id=?',(row[0],));continue
                    available.append(row)
                local=current.astimezone(ZoneInfo('Asia/Shanghai'))
                due=local.hour*60+local.minute>=policy.digest_hour*60+policy.digest_minute
                pending_recovery=c.execute("SELECT 1 FROM news_batch b JOIN news_member m ON m.batch_id=b.batch_id JOIN notification_candidate n ON n.notification_id=m.notification_id WHERE b.target_id=? AND b.kind='recovery' AND n.status='pending' LIMIT 1",(target_id,)).fetchone()
                target_recovery=recovery or bool(pending_recovery)
                kind='recovery' if target_recovery else policy.mode
                groups=[available] if (target_recovery or policy.mode=='digest') else [[row] for row in available]
                if kind=='digest':
                    prior=c.execute("SELECT b.batch_id,b.created_at FROM news_batch b WHERE b.target_id=? AND b.kind='digest'",(target_id,)).fetchall()
                    if any(datetime.fromisoformat(created_at).astimezone(ZoneInfo('Asia/Shanghai')).date()==local.date() and c.execute("SELECT 1 FROM news_member m JOIN delivery_attempt a ON a.notification_id=m.notification_id WHERE m.batch_id=? AND a.outcome!='not_sent' LIMIT 1",(batch_id,)).fetchone() for batch_id,created_at in prior):groups=[]
                desired_signatures=set()
                planned=[]
                if policy.mode=='immediate' or target_recovery or due:
                    for group in groups:
                        if not group:continue
                        window=local.date().isoformat() if kind=='digest' else ''
                        signature=fingerprint([kind,window,[(r[0],r[1]) for r in group]])
                        desired_signatures.add(signature);planned.append((signature,kind,group))
                for batch_id,signature in batches:
                    if batch_id in frozen_batches or signature in desired_signatures:continue
                    c.execute("UPDATE notification_candidate SET status='superseded',retirement_code='news_replanned',last_error='公告批次已由最新策略或版本替代' WHERE notification_id IN (SELECT notification_id FROM news_member WHERE batch_id=?) AND status='pending'",(batch_id,))
                for signature,kind,group in planned:
                    existing=next((bid for bid,sig in batches if sig==signature),None)
                    if existing and c.execute("SELECT 1 FROM news_member m JOIN notification_candidate n ON n.notification_id=m.notification_id WHERE m.batch_id=? AND n.status!='superseded'",(existing,)).fetchone():continue
                    batch_id=str(uuid4());c.execute('INSERT INTO news_batch VALUES(?,?,?,?,?)',(batch_id,target_id,kind,signature,current.isoformat()))
                    rendered=[]
                    for row in group:
                        v=self._store.get_document_version(row[1])
                        last=c.execute('SELECT last_sent_version FROM news_observation WHERE target_id=? AND source_id=? AND document_id=? AND epoch=?',(target_id,row[5],row[6],row[7])).fetchone()
                        before=self._store.get_document_version(last[0]) if last and last[0] else None
                        rendered.append((row[0],self._renderer.render_item(v,before,row[3])))
                    for index,(text,members) in enumerate(self._renderer.pages(rendered,kind)):
                        notification_id=str(uuid4());eligible=min(datetime.fromisoformat(row[2]) for row in group if row[0] in members)
                        # Inclusive 72-hour eligibility; one microsecond makes the generic deadline gate consistent.
                        expires=(eligible+timedelta(hours=72,microseconds=1)).isoformat()
                        covered=sorted({document_region(row[5]) for row in group if row[0] in members})
                        c.execute("INSERT INTO notification_candidate(notification_id,business_key,target_id,stream,window_key,message_text,status,created_at,updated_at,expires_at,kind,region) VALUES(?,?,?,'announcement',?,?,'pending',?,?,?,?,?)",(notification_id,f'announcement:{batch_id}:{index}',target_id,batch_id,text,current.isoformat(),current.isoformat(),expires,kind,json.dumps(covered)))
                        for item_id in members:
                            row=next(row for row in group if row[0]==item_id)
                            c.execute('INSERT INTO news_member VALUES(?,?,?,?)',(batch_id,notification_id,item_id,row[1]))
                            c.execute('UPDATE news_item SET notification_id=? WHERE item_id=?',(notification_id,item_id))
                        created+=1
        return created

    def preview(self,target,*,now=None):
        # A read-only rendering of known versions; no observation, candidate, or success writes.
        policy=self._store.get_announcement_policy(target.target_id)
        items=[]
        seen=set()
        for version in self._store.get_document_versions(limit=1000):
            identity=(version.source_id,version.document_id)
            if identity in seen:continue
            seen.add(identity)
            if document_region(version.source_id) in policy.regions and version.category in policy.categories:
                items.append(self._renderer.render_item(version,None,'预览（不代表待发资格）'))
        return 'FF14 公告预览\n'+('\n\n'.join(items[:10]) or '没有可预览文章。')

    async def tick(self,messaging,*,now=None,online=True):
        current=now or datetime.now(timezone.utc)
        previous=self._store.get_meta('news_last_tick')
        was_offline=self._store.get_meta('news_offline')=='1'
        recovery=online and (was_offline or (not self._started and previous is not None))
        self._started=True
        self.plan_due(now=current,recovery=recovery)
        count=await dispatch_pending(self._store,messaging,stream=self.STREAM,now=current)
        with self._store._connection() as c:
            for key,value in [('news_last_tick',current.isoformat()),('news_offline','0' if online else '1')]:
                c.execute('INSERT INTO app_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
        return count
