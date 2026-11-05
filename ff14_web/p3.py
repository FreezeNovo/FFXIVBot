"""本机P3管理页面，所有来源内容作为转义文本显示。"""
from dataclasses import asdict
from datetime import datetime, timedelta
from html import escape
from urllib.parse import quote, urlsplit
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from ff14_core.runtime_settings import RuntimeSettings
from ff14_core.documents import version_diff
from ff14_core.news import AnnouncementPolicy, AnnouncementWorkflow, ANNOUNCEMENT_CATEGORIES, SUPPORTED_REGIONS


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(strict=True)
    discovery_minutes: int = 15
    preflight_minutes: int = 5
    warning_hours: int = 48
    stop_hours: int = 72


class AnnouncementUpdate(BaseModel):
    model_config=ConfigDict(strict=True)
    enabled: bool = False
    regions: list[str] = ['cn']
    categories: list[str] = list(ANNOUNCEMENT_CATEGORIES)
    mode: str = 'immediate'
    digest_hour: int = 20
    digest_minute: int = 0


def safe_link(url):
    return escape(url,quote=True) if urlsplit(url).scheme in {'http','https'} else '#'


def install_p3_routes(app, store, page_shell):
    router = app

    @router.get('/api/runtime-settings')
    def settings():
        result = asdict(store.get_runtime_settings())
        result['probes'] = store.get_runtime_probes()
        previous = result['probes'].get('discovery')
        result['next_discovery_at'] = (datetime.fromisoformat(previous['attempted_at'])+timedelta(minutes=result['discovery_minutes'])).isoformat() if previous else None
        return result

    @router.put('/api/runtime-settings')
    def save_settings(update: SettingsUpdate):
        try:
            store.set_runtime_settings(RuntimeSettings(**update.model_dump()))
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        return settings()

    @router.get('/settings',response_class=HTMLResponse)
    def settings_page():
        values = asdict(store.get_runtime_settings())
        labels = {'discovery_minutes':'新闻列表间隔（分钟）','preflight_minutes':'摘要复查提前量（分钟）','warning_hours':'数据年龄警告（小时）','stop_hours':'停止主动展示（小时）'}
        fields=''.join(f'<label>{labels[key]} <input name="{key}" type="number" min="1" step="1" value="{value}" required></label><br>' for key,value in values.items())
        return HTMLResponse(page_shell('运行设置', f'''<main><nav><a href="/access">群授权</a> <a href="/sources">来源状态</a> <a href="/articles">文章版本</a></nav><h1>运行设置</h1><form id="settings">{fields}<button>保存</button></form><pre id="status">{escape(json.dumps(settings(),ensure_ascii=False,indent=2))}</pre><script>document.querySelector('#settings').onsubmit=async(e)=>{{e.preventDefault();const data=Object.fromEntries([...new FormData(e.target)].map(([k,v])=>[k,Number(v)]));const r=await fetch('/api/runtime-settings',{{method:'PUT',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}});document.querySelector('#status').textContent=await r.text();}};</script></main>'''))

    @router.get('/sources',response_class=HTMLResponse)
    def sources_page():
        rows=''.join(f'<tr><td>{escape(h.source_id)}</td><td>{escape(h.last_attempt_at or "未尝试")}</td><td>{escape(h.last_success_at or "未成功")}</td><td>{h.consecutive_failures}</td><td>{escape(h.last_error or "正常")}</td></tr>' for h in store.get_source_health())
        from ff14_core.freshness import evaluate_freshness
        from datetime import timezone
        verifications=[]
        for record in store.list_event_verifications():
            state=evaluate_freshness(record,datetime.now(timezone.utc),store.get_runtime_settings())
            query='source_id='+quote(record['source_id'],safe='')+'&document_id='+quote(record['document_id'],safe='')
            verifications.append(f'<tr><td><a href="/articles?{escape(query,quote=True)}">{escape(record["source_id"])} / {escape(record["document_id"])}</a></td><td>{escape(state.state)}</td><td>{escape(state.last_success_at or "未核实")}</td><td>{escape(state.last_attempt_at or "未知")}</td><td>{escape(state.warning or "有效")}</td></tr>')
        with store._connection() as c:
            incidents=c.execute('SELECT target_id,episode,active,started_at,recovered_at FROM digest_incident ORDER BY started_at DESC').fetchall()
        return HTMLResponse(page_shell('来源状态','<a href="/settings">运行设置</a><h1>来源状态</h1><table><tr><th>来源</th><th>最近尝试</th><th>最近成功</th><th>连续失败</th><th>结果</th></tr>'+rows+'</table><h2>逐活动核实</h2><table>'+''.join(verifications)+'</table><h2>摘要暂停周期</h2><pre>'+escape(json.dumps(incidents,ensure_ascii=False,indent=2))+'</pre>'))

    @router.get('/api/articles/normalization-preview')
    def normalization_preview():
        return store.preview_normalization()

    @router.get('/articles',response_class=HTMLResponse)
    def articles_page(source_id: str | None=None, document_id: str | None=None):
        versions=store.get_document_versions(source_id,document_id)
        body=['<a href="/access">群授权</a> <a href="/sources">来源状态</a><h1>文章版本</h1><p>仅比较图片引用；同地址下的图片内容替换无法检测。</p>']
        for version in versions:
            previous=next((v for v in versions if v.source_id==version.source_id and v.document_id==version.document_id and v.version_id<version.version_id),None)
            evidence=store.get_time_evidence(version.version_id)
            body.append(f'<article><h2>{escape(version.title)}</h2><p>{escape(version.source_id)} / {escape(version.document_id)} / v{version.version_id} / {escape(version.observed_at)} / {escape(version.normalizer_version)}</p><a href="{safe_link(version.url)}" rel="noreferrer">官方原文</a><pre>{escape(version_diff(previous,version))}</pre><pre>{escape(json.dumps(evidence,ensure_ascii=False,indent=2))}</pre><details><summary>原始HTML（文本）</summary><pre>{escape(version.content_html)}</pre></details></article>')
        body.append('<h2>旧详情原始版本</h2><p>旧记录的标题与比较字段未知，未用当前文章反填。以下为兼容详情快照；公告比较使用上方完整版本。</p>')
        for source,document,content_hash,fetched_at,html in store.get_legacy_detail_versions(source_id,document_id):
            body.append(f'<details><summary>{escape(source)} / {escape(document)} / {escape(fetched_at)} / {escape(content_hash)}</summary><pre>{escape(html)}</pre></details>')
        body.append('<a href="/api/articles/normalization-preview">规范化升级只读预览</a>')
        return HTMLResponse(page_shell('文章版本',''.join(body)))


    @router.get('/api/news/{group_id}')
    def news_policy(group_id: str):
        target=next((t.target for t in store.list_targets() if t.target.provider_target_id==group_id),None)
        if target is None:raise HTTPException(404,'未找到服务群')
        return asdict(store.get_announcement_policy(target.target_id))

    @router.put('/api/news/{group_id}')
    def save_news(group_id: str, update: AnnouncementUpdate):
        target=next((t.target for t in store.list_targets() if t.target.provider_target_id==group_id),None)
        if target is None:raise HTTPException(404,'未找到服务群')
        data=update.model_dump();data['regions']=tuple(data['regions']);data['categories']=tuple(data['categories'])
        try:store.set_announcement_policy(target.target_id,AnnouncementPolicy(**data))
        except ValueError as error:raise HTTPException(422,str(error)) from error
        return news_policy(group_id)

    @router.get('/news',response_class=HTMLResponse)
    def news_page():
        rows=[]
        for managed in store.list_targets():
            target=managed.target;policy=store.get_announcement_policy(target.target_id)
            regions=''.join(f'<label><input type="checkbox" name="regions" value="{r}"{" checked" if r in policy.regions else ""}>{label}</label>' for r,label in [('cn','国服'),('global-na','NA')])
            categories=''.join(f'<label><input type="checkbox" name="categories" value="{code}"{" checked" if code in policy.categories else ""}>{code}</label>' for code in ANNOUNCEMENT_CATEGORIES)
            preview=AnnouncementWorkflow(store).preview(target)
            rows.append(f'<form data-news-group="{escape(target.provider_target_id,quote=True)}"><h2>服务群 {escape(target.provider_target_id)}</h2><label><input type="checkbox" name="enabled"{" checked" if policy.enabled else ""}>公告订阅</label><fieldset><legend>区域（空选不订阅）</legend>{regions}</fieldset><fieldset><legend>分类（官方稳定代码）</legend>{categories}</fieldset><select name="mode"><option value="immediate"{" selected" if policy.mode=="immediate" else ""}>逐条即时</option><option value="digest"{" selected" if policy.mode=="digest" else ""}>每日汇总</option></select><label>北京时间 <input name="time" type="time" value="{policy.digest_hour:02}:{policy.digest_minute:02}" required></label><button>保存</button><output></output><details><summary>已知文章预览，不代表待发资格</summary><pre>{escape(preview)}</pre></details></form>')
        script="""<script>document.querySelectorAll('[data-news-group]').forEach(form=>{form.onsubmit=async e=>{e.preventDefault();const f=new FormData(form),t=f.get('time').split(':').map(Number);const body={enabled:f.has('enabled'),regions:f.getAll('regions'),categories:f.getAll('categories'),mode:f.get('mode'),digest_hour:t[0],digest_minute:t[1]};const r=await fetch('/api/news/'+encodeURIComponent(form.dataset.newsGroup),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});form.querySelector('output').textContent=r.ok?'已保存':await r.text();};});</script>"""
        return HTMLResponse(page_shell('公告订阅','<a href="/access">群授权</a> <a href="/deliveries">投递历史</a> <a href="/articles">文章版本</a><h1>公告订阅</h1><p>首次启用、重新开启及新增范围均建立生效点，不补发历史。维护使用独立流。</p>'+''.join(rows)+script))
