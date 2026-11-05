# Offline only; run from repository with .venv-linux/bin/python /tmp/p3_news_review.py
import sys
from pathlib import Path
sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / 'tests')]
from test_p3_news import *
from ff14_core.delivery import dispatch_pending

def expired_update():
    t=NewsWorkflowTests();t.setUp()
    try:
        t.article(text='A');t.flow.plan_due(now=NOW+timedelta(days=4))
        t.article(text='B',at=NOW+timedelta(days=4,seconds=1))
        actual=t.tick(NOW+timedelta(days=4,seconds=2))
        print('recent change to expired initial announcement: expected 1, actual',actual)
    finally:t.doCleanups()

def frozen_duplicate():
    t=NewsWorkflowTests();t.setUp()
    try:
        t.store.set_announcement_policy(t.target.target_id,replace(t.policy,mode='digest'),now=NOW.isoformat())
        for i in range(5):t.article(str(i),text=('Value '+str(i)+' ')*35)
        t.flow.plan_due(now=NOW+timedelta(seconds=2))
        pending=t.store.list_pending_notifications(stream='announcement')
        claim=t.store.claim_notification(pending[0][0],now=NOW+timedelta(seconds=3))
        t.store.finish_delivery_attempt(claim.attempt_id,SendResult(True,provider_message_id='first'))
        with t.store._connection() as c:
            vid=c.execute('SELECT version_id FROM news_member WHERE notification_id=?',(pending[1][0],)).fetchone()[0]
        v=t.store.get_document_version(vid)
        doc=SourceDocument(v.source_id,v.document_id,v.title,v.url,v.published_at,None,v.category,None,None)
        t.store.record_document_version(doc,'<div style="color:red">'+v.content_html+'</div>',now=(NOW+timedelta(seconds=4)).isoformat())
        t.flow.plan_due(now=NOW+timedelta(seconds=5),recovery=True)
        asyncio.run(dispatch_pending(t.store,t.messaging,stream='announcement',now=NOW+timedelta(seconds=5)))
        actual=sum(v.url in msg for _,msg in t.messaging.sent)
        print('unchanged frozen article deliveries: expected 1, actual',actual)
    finally:t.doCleanups()

expired_update()
frozen_duplicate()
