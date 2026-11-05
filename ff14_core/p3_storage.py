"""P3 存储增量，与 StateStore 共用连接、事务和备份机制。"""
from dataclasses import asdict
import json

from .runtime_settings import RuntimeSettings


def migrate_p3(connection, ensure_column, *, target_version=22):
    """按17–22的纵向切片重入，支持旧库和中断后的部分结构。"""
    if target_version >= 17:
        connection.execute("CREATE TABLE IF NOT EXISTS runtime_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS runtime_probe (name TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, result TEXT NOT NULL)")
    if target_version >= 18:
        connection.execute("""CREATE TABLE IF NOT EXISTS article_version (
            version_id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, document_id TEXT NOT NULL,
            observed_at TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, published_at TEXT, category TEXT NOT NULL,
            content_html TEXT NOT NULL, normalized TEXT NOT NULL, raw_hash TEXT NOT NULL, comparison_hash TEXT NOT NULL,
            normalizer_version TEXT NOT NULL, changed INTEGER NOT NULL)""")
        ensure_column(connection,"article_version","ready","INTEGER NOT NULL DEFAULT 1")
        connection.execute("CREATE INDEX IF NOT EXISTS article_identity ON article_version(source_id,document_id,version_id)")
        connection.execute("CREATE TABLE IF NOT EXISTS article_probe (source_id TEXT NOT NULL,document_id TEXT NOT NULL,attempted_at TEXT NOT NULL,error TEXT,PRIMARY KEY(source_id,document_id))")
    if target_version >= 19:
        connection.execute("CREATE TABLE IF NOT EXISTS announcement_policy(target_id TEXT PRIMARY KEY REFERENCES target(target_id),payload TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS news_scope(target_id TEXT NOT NULL REFERENCES target(target_id),region TEXT NOT NULL,category TEXT NOT NULL,watermark_at TEXT NOT NULL,epoch TEXT PRIMARY KEY,active INTEGER NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS news_observation(target_id TEXT NOT NULL,source_id TEXT NOT NULL,document_id TEXT NOT NULL,epoch TEXT NOT NULL,baseline_version INTEGER NOT NULL,last_sent_version INTEGER,PRIMARY KEY(target_id,source_id,document_id,epoch))")
        connection.execute("CREATE TABLE IF NOT EXISTS news_item(item_id TEXT PRIMARY KEY,target_id TEXT NOT NULL,source_id TEXT NOT NULL,document_id TEXT NOT NULL,epoch TEXT NOT NULL,version_id INTEGER NOT NULL REFERENCES article_version(version_id),eligible_at TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL,notification_id TEXT,UNIQUE(target_id,epoch,version_id))")
        connection.execute("CREATE TABLE IF NOT EXISTS news_batch(batch_id TEXT PRIMARY KEY,target_id TEXT NOT NULL,kind TEXT NOT NULL,signature TEXT NOT NULL,created_at TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS news_member(batch_id TEXT NOT NULL REFERENCES news_batch(batch_id),notification_id TEXT NOT NULL,item_id TEXT NOT NULL REFERENCES news_item(item_id),version_id INTEGER NOT NULL REFERENCES article_version(version_id),PRIMARY KEY(batch_id,item_id))")
    if target_version >= 20:
        for table in ('activity_digest_policy','maintenance_policy'):
            ensure_column(connection,table,'regions_json','TEXT')
            for target,region in connection.execute('SELECT target_id,region FROM '+table+' WHERE regions_json IS NULL').fetchall():
                connection.execute('UPDATE '+table+' SET regions_json=? WHERE target_id=?',(json.dumps([region]),target))
        connection.execute("CREATE TABLE IF NOT EXISTS maintenance_scope(target_id TEXT NOT NULL,region TEXT NOT NULL,watermark_at TEXT NOT NULL,PRIMARY KEY(target_id,region))")
    if target_version >= 21:
        connection.execute("CREATE TABLE IF NOT EXISTS event_verification(source_id TEXT NOT NULL,document_id TEXT NOT NULL,valid_hash TEXT,last_success_at TEXT,last_attempt_at TEXT,error_kind TEXT,error TEXT,PRIMARY KEY(source_id,document_id))")
        connection.execute("CREATE TABLE IF NOT EXISTS article_time_evidence(version_id INTEGER NOT NULL REFERENCES article_version(version_id),ordinal INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(version_id,ordinal))")
        ensure_column(connection,"event_candidate","precision","TEXT NOT NULL DEFAULT 'unknown'")
        ensure_column(connection,"event_candidate","time_assumption","TEXT NOT NULL DEFAULT ''")
        connection.execute("CREATE TABLE IF NOT EXISTS body_validator(source_id TEXT NOT NULL,document_id TEXT NOT NULL,etag TEXT,last_modified TEXT,version_id INTEGER NOT NULL,PRIMARY KEY(source_id,document_id))")
    if target_version >= 22:
        connection.execute("CREATE TABLE IF NOT EXISTS digest_incident(target_id TEXT PRIMARY KEY,episode TEXT NOT NULL,active INTEGER NOT NULL,started_at TEXT NOT NULL,recovered_at TEXT)")
        connection.execute("CREATE TABLE IF NOT EXISTS digest_preflight(target_id TEXT NOT NULL,window_key TEXT NOT NULL,source_id TEXT NOT NULL,document_id TEXT NOT NULL,attempted_at TEXT NOT NULL,PRIMARY KEY(target_id,window_key,source_id,document_id))")
        connection.execute("CREATE TABLE IF NOT EXISTS digest_member(notification_id TEXT NOT NULL,source_id TEXT NOT NULL,document_id TEXT NOT NULL,valid_hash TEXT,PRIMARY KEY(notification_id,source_id,document_id))")


class P3StoreMethods:
    def get_runtime_settings(self) -> RuntimeSettings:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM runtime_settings WHERE id=1").fetchone()
        return RuntimeSettings(**json.loads(row[0])) if row else RuntimeSettings()

    def set_runtime_settings(self, settings: RuntimeSettings) -> None:
        settings.validate()
        with self._connection() as connection:
            connection.execute("INSERT INTO runtime_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (json.dumps(asdict(settings)),))

    def record_runtime_probe(self, name: str, attempted_at: str, result: str) -> None:
        with self._connection() as connection:
            connection.execute("INSERT INTO runtime_probe VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET attempted_at=excluded.attempted_at,result=excluded.result", (name, attempted_at, result))

    def get_runtime_probes(self) -> dict:
        with self._connection() as connection:
            rows = connection.execute("SELECT name,attempted_at,result FROM runtime_probe").fetchall()
        return {name: {"attempted_at": timestamp, "result": result} for name, timestamp, result in rows}

    def record_document_version(self, document, content_html, *, now=None, normalizer_version=None, ready=True):
        from .documents import NORMALIZER_VERSION, normalize_document, fingerprint
        from .storage import utc_now_text
        stamp = now or utc_now_text()
        normalized = normalize_document(document.title, content_html, document.url)
        comparison_hash = fingerprint(normalized)
        raw_hash = fingerprint([document.title, content_html, document.url, document.published_at, document.category])
        normalizer = normalizer_version or NORMALIZER_VERSION
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE source_document SET title=?,url=? WHERE source_id=? AND document_id=?",(document.title,document.url,document.source_id,document.document_id))
            previous = connection.execute("SELECT version_id, raw_hash, comparison_hash, normalizer_version FROM article_version WHERE source_id=? AND document_id=? ORDER BY version_id DESC LIMIT 1", (document.source_id,document.document_id)).fetchone()
            if previous and previous[1] == raw_hash and previous[3] == normalizer:
                connection.execute("UPDATE article_probe SET attempted_at=?,error=NULL WHERE source_id=? AND document_id=?",(stamp,document.source_id,document.document_id))
                return previous[0]
            changed = bool(previous and previous[3] == normalizer and previous[2] != comparison_hash)
            cursor = connection.execute("INSERT INTO article_version(source_id,document_id,observed_at,title,url,published_at,category,content_html,normalized,raw_hash,comparison_hash,normalizer_version,changed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (document.source_id,document.document_id,stamp,document.title,document.url,document.published_at,document.category or "unknown",content_html,json.dumps(normalized,ensure_ascii=False),raw_hash,comparison_hash,normalizer,int(changed)))
            connection.execute("INSERT INTO article_probe VALUES(?,?,?,NULL) ON CONFLICT(source_id,document_id) DO UPDATE SET attempted_at=excluded.attempted_at,error=NULL", (document.source_id,document.document_id,stamp))
            if not ready:connection.execute("UPDATE article_version SET ready=0 WHERE version_id=?",(cursor.lastrowid,))
            return cursor.lastrowid

    def get_document_versions(self, source_id=None, document_id=None, *, limit=100):
        from .documents import DocumentVersion
        filters=[];parameters=[]
        for name,value in (("source_id",source_id),("document_id",document_id)):
            if value is not None:filters.append(name+"=?");parameters.append(value)
        with self._connection() as connection:
            rows=connection.execute("SELECT version_id,source_id,document_id,observed_at,title,url,published_at,category,content_html,normalized,comparison_hash,normalizer_version,changed FROM article_version"+(" WHERE "+" AND ".join(filters) if filters else "")+" ORDER BY version_id DESC LIMIT ?",(*parameters,limit)).fetchall()
        return tuple(DocumentVersion(*row[:9],json.loads(row[9]),*row[10:12],bool(row[12])) for row in rows)

    def get_document_version(self, version_id):
        from .documents import DocumentVersion
        with self._connection() as connection:
            row=connection.execute("SELECT version_id,source_id,document_id,observed_at,title,url,published_at,category,content_html,normalized,comparison_hash,normalizer_version,changed FROM article_version WHERE version_id=?",(version_id,)).fetchone()
        return DocumentVersion(*row[:9],json.loads(row[9]),*row[10:12],bool(row[12])) if row else None

    def get_announcement_policy(self,target_id):
        from .news import AnnouncementPolicy
        with self._connection() as c:
            row=c.execute('SELECT payload FROM announcement_policy WHERE target_id=?',(target_id,)).fetchone()
        if not row:return AnnouncementPolicy()
        data=json.loads(row[0]);data['regions']=tuple(data['regions']);data['categories']=tuple(data['categories'])
        return AnnouncementPolicy(**data)

    def set_announcement_policy(self,target_id,policy,*,now=None):
        from .storage import utc_now_text
        policy.validate();stamp=now or utc_now_text()
        with self._connection() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM target WHERE target_id=? AND archived=0',(target_id,)).fetchone():raise ValueError('未知投递目标')
            c.execute('INSERT INTO announcement_policy VALUES(?,?) ON CONFLICT(target_id) DO UPDATE SET payload=excluded.payload',(target_id,json.dumps(asdict(policy))))
            current={(r[0],r[1]):r[2] for r in c.execute('SELECT region,category,epoch FROM news_scope WHERE target_id=? AND active=1',(target_id,))}
            desired={(r,k) for r in policy.regions for k in policy.categories} if policy.enabled else set()
            for key,epoch in current.items():
                if key not in desired:c.execute('UPDATE news_scope SET active=0 WHERE target_id=? AND epoch=?',(target_id,epoch))
            from uuid import uuid4
            for region,category in desired-current.keys():
                c.execute('INSERT INTO news_scope VALUES(?,?,?,?,?,1)',(target_id,region,category,stamp,str(uuid4())))
            # Unsent pages are rechecked as a whole before any external call.
            if not policy.enabled:self._supersede_pending(c,target_id,stream='announcement',reason='公告订阅已关闭')

    @staticmethod
    def _news_claim_reason(c, notification_id, target_id, current):
        rows=c.execute('SELECT i.epoch,v.source_id,v.category,i.status,v.document_id,v.comparison_hash FROM news_member m JOIN news_item i ON i.item_id=m.item_id JOIN article_version v ON v.version_id=m.version_id WHERE m.notification_id=?',(notification_id,)).fetchall()
        if not rows:return '公告批次成员缺失'
        from .news import document_region
        frozen=c.execute("SELECT 1 FROM news_member own JOIN news_member peer ON peer.batch_id=own.batch_id JOIN delivery_attempt a ON a.notification_id=peer.notification_id WHERE own.notification_id=? AND a.outcome!='not_sent' LIMIT 1",(notification_id,)).fetchone()
        policy_row=c.execute('SELECT payload FROM announcement_policy WHERE target_id=?',(target_id,)).fetchone()
        if not policy_row:return '公告策略缺失'
        policy=json.loads(policy_row[0]);kind=c.execute('SELECT kind FROM notification_candidate WHERE notification_id=?',(notification_id,)).fetchone()[0]
        if not frozen:
            if kind in {'immediate','digest'} and policy['mode']!=kind:return '公告发送模式已改变'
            if kind=='digest':
                from zoneinfo import ZoneInfo
                local=current.astimezone(ZoneInfo('Asia/Shanghai'))
                if local.hour*60+local.minute<policy['digest_hour']*60+policy['digest_minute']:return '尚未到当前公告汇总时刻'
        for epoch,source,category,status,document,comparison_hash in rows:
            if not frozen:
                latest=c.execute('SELECT ready,comparison_hash FROM article_version WHERE source_id=? AND document_id=? ORDER BY version_id DESC LIMIT 1',(source,document)).fetchone()
                if not latest or not latest[0] or latest[1]!=comparison_hash:return '公告文章版本已改变或尚未处理完成'

            if status!='active' or not c.execute('SELECT 1 FROM news_scope WHERE target_id=? AND epoch=? AND active=1 AND region=? AND category=?',(target_id,epoch,document_region(source),category)).fetchone():return '公告订阅范围或文章版本已改变'
        return None

    @staticmethod
    def _finish_news(c,notification_id,status):
        if status!='sent':return
        rows=c.execute('SELECT i.target_id,i.source_id,i.document_id,i.epoch,m.version_id,i.item_id FROM news_member m JOIN news_item i ON i.item_id=m.item_id WHERE m.notification_id=?',(notification_id,)).fetchall()
        for target,source,document,epoch,version,item in rows:
            c.execute('UPDATE news_observation SET last_sent_version=? WHERE target_id=? AND source_id=? AND document_id=? AND epoch=?',(version,target,source,document,epoch))
            c.execute('UPDATE news_item SET status="sent" WHERE item_id=?',(item,))

    def get_maintenance_watermark(self,target_id,region):
        with self._connection() as c:
            row=c.execute('SELECT watermark_at FROM maintenance_scope WHERE target_id=? AND region=?',(target_id,region)).fetchone()
        return row[0] if row else self.get_maintenance_policy(target_id).watermark_at

    @staticmethod
    def _regions_allowed(stored,covered):
        if covered is None:return True
        requested=json.loads(covered) if covered.startswith('[') else [covered]
        return bool(requested) and set(requested)<=set(json.loads(stored))

    def get_event_verification(self,source_id,document_id):
        with self._connection() as c:
            row=c.execute('SELECT valid_hash,last_success_at,last_attempt_at,error_kind,error FROM event_verification WHERE source_id=? AND document_id=?',(source_id,document_id)).fetchone()
        return dict(zip(('valid_hash','last_success_at','last_attempt_at','error_kind','error'),row)) if row else None

    def record_verification_attempt(self,source_id,document_id,*,error=None,error_kind='running',now=None):
        from .storage import utc_now_text
        from .diagnostics import safe_reason
        with self._connection() as c:
            c.execute('INSERT INTO event_verification(source_id,document_id,last_attempt_at,error_kind,error) VALUES(?,?,?,?,?) ON CONFLICT(source_id,document_id) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,error_kind=excluded.error_kind,error=excluded.error',(source_id,document_id,now or utc_now_text(),error_kind,safe_reason(error or '本次复查尚未完成')))

    def record_verification_success(self,source_id,document_id,valid_hash,*,now=None):
        from .storage import utc_now_text
        stamp=now or utc_now_text()
        with self._connection() as c:
            valid=c.execute("SELECT 1 FROM source_document_detail WHERE source_id=? AND document_id=? AND parse_status='parsed' AND content_hash=?",(source_id,document_id,valid_hash)).fetchone()
            c.execute("UPDATE source_document_detail SET fetched_at=? WHERE source_id=? AND document_id=?",(stamp,source_id,document_id))
            if valid:
                c.execute('INSERT INTO event_verification VALUES(?,?,?,?,?,NULL,NULL) ON CONFLICT(source_id,document_id) DO UPDATE SET valid_hash=excluded.valid_hash,last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,error_kind=NULL,error=NULL',(source_id,document_id,valid_hash,stamp,stamp))

    def update_digest_incident(self,target_id,unavailable,*,now):
        from uuid import uuid4
        with self._connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT episode,active FROM digest_incident WHERE target_id=?',(target_id,)).fetchone()
            if unavailable:
                if row and row[1]:return row[0]
                from .diagnostics import diagnostic
                diagnostic(stage="digest",target_id=target_id,action="pause",error="全部过旧或来源不可用")
                episode=str(uuid4())
                c.execute('INSERT INTO digest_incident VALUES(?,?,1,?,NULL) ON CONFLICT(target_id) DO UPDATE SET episode=excluded.episode,active=1,started_at=excluded.started_at,recovered_at=NULL',(target_id,episode,now.isoformat()))
                return episode
            if row and row[1]:
                from .diagnostics import diagnostic
                diagnostic(stage="digest",target_id=target_id,action="recovered")
                c.execute('UPDATE digest_incident SET active=0,recovered_at=? WHERE target_id=?',(now.isoformat(),target_id))
                c.execute("UPDATE notification_candidate SET status='superseded',retirement_code='recovered',last_error='活动核实已恢复' WHERE target_id=? AND stream='activity_digest' AND kind='pause' AND status='pending'",(target_id,))
            return None

    def reserve_preflight(self,target_id,window_key,source_id,document_id,*,now):
        with self._connection() as c:
            return c.execute('INSERT OR IGNORE INTO digest_preflight VALUES(?,?,?,?,?)',(target_id,window_key,source_id,document_id,now.isoformat())).rowcount==1

    def record_time_evidence(self,version_id,evidence):
        with self._connection() as c:
            for index,record in enumerate(evidence):c.execute('INSERT OR IGNORE INTO article_time_evidence VALUES(?,?,?)',(version_id,index,json.dumps(record,ensure_ascii=False)))

    def get_time_evidence(self,version_id):
        with self._connection() as c:
            rows=c.execute('SELECT payload FROM article_time_evidence WHERE version_id=? ORDER BY ordinal',(version_id,)).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def list_event_verifications(self):
        with self._connection() as c:
            rows=c.execute('SELECT source_id,document_id,valid_hash,last_success_at,last_attempt_at,error_kind,error FROM event_verification ORDER BY last_attempt_at DESC LIMIT 200').fetchall()
        return tuple(dict(zip(('source_id','document_id','valid_hash','last_success_at','last_attempt_at','error_kind','error'),row)) for row in rows)

    def preview_normalization(self):
        from .documents import NORMALIZER_VERSION, normalize_document, fingerprint
        seen=set();results=[]
        for version in self.get_document_versions(limit=100000):
            key=(version.source_id,version.document_id)
            if key in seen:continue
            seen.add(key)
            normalized=normalize_document(version.title,version.content_html,version.url)
            results.append({'source_id':version.source_id,'document_id':version.document_id,'from_version':version.normalizer_version,'to_version':NORMALIZER_VERSION,'comparison_would_change':version.comparison_hash!=fingerprint(normalized),'action':'next_fetch_baseline' if version.normalizer_version!=NORMALIZER_VERSION else 'unchanged'})
        return tuple(results)

    def get_legacy_detail_versions(self,source_id=None,document_id=None):
        clauses=[];parameters=[]
        for key,value in [('source_id',source_id),('document_id',document_id)]:
            if value is not None:clauses.append(key+'=?');parameters.append(value)
        with self._connection() as c:
            return tuple(c.execute('SELECT source_id,document_id,content_hash,fetched_at,content_html FROM source_document_detail_version'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY fetched_at DESC LIMIT 100',parameters))

    def save_digest_members(self,business_key,entries):
        with self._connection() as c:
            row=c.execute('SELECT notification_id,status FROM notification_candidate WHERE business_key=?',(business_key,)).fetchone()
            if not row or row[1]!='pending':return
            c.execute('DELETE FROM digest_member WHERE notification_id=?',(row[0],))
            for entry in entries:
                verified=c.execute('SELECT valid_hash FROM event_verification WHERE source_id=? AND document_id=?',(entry.source_id,entry.document_id)).fetchone()
                c.execute('INSERT OR IGNORE INTO digest_member VALUES(?,?,?,?)',(row[0],entry.source_id,entry.document_id,verified[0] if verified else None))

    @staticmethod
    def _digest_claim_reason(c,notification_id,target_id,current):
        row=c.execute('SELECT window_key FROM notification_candidate WHERE notification_id=?',(notification_id,)).fetchone()
        frozen=c.execute("SELECT 1 FROM notification_candidate n JOIN delivery_attempt a ON a.notification_id=n.notification_id WHERE n.target_id=? AND n.stream='activity_digest' AND n.window_key=? AND a.outcome!='not_sent' LIMIT 1",(target_id,row[0])).fetchone()
        if frozen:return None
        settings=c.execute('SELECT payload FROM runtime_settings WHERE id=1').fetchone()
        limit=json.loads(settings[0])['stop_hours'] if settings else 72
        members=c.execute('SELECT m.valid_hash,v.valid_hash,v.last_success_at FROM digest_member m LEFT JOIN event_verification v ON v.source_id=m.source_id AND v.document_id=m.document_id WHERE m.notification_id=?',(notification_id,)).fetchall()
        from datetime import datetime
        if not members or any(not success or (current-datetime.fromisoformat(success)).total_seconds()>=limit*3600 or previous!=valid for previous,valid,success in members):return '活动核实或内容版本已改变，等待重新规划'
        return None

    def get_body_validators(self,source_id,document_id):
        with self._connection() as c:
            return c.execute('SELECT etag,last_modified,version_id FROM body_validator WHERE source_id=? AND document_id=?',(source_id,document_id)).fetchone()

    def set_body_validators(self,source_id,document_id,version_id,headers):
        headers={key.lower():value for key,value in headers.items()}
        with self._connection() as c:
            c.execute('INSERT INTO body_validator VALUES(?,?,?,?,?) ON CONFLICT(source_id,document_id) DO UPDATE SET etag=excluded.etag,last_modified=excluded.last_modified,version_id=excluded.version_id',(source_id,document_id,headers.get('etag'),headers.get('last-modified'),version_id))

    def finish_article_observation(self,source_id,document_id):
        with self._connection() as c:c.execute('UPDATE article_version SET ready=1 WHERE version_id=(SELECT MAX(version_id) FROM article_version WHERE source_id=? AND document_id=?)',(source_id,document_id))
