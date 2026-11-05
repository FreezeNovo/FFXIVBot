import asyncio
from datetime import datetime,timezone
from types import SimpleNamespace
from ff14_app.reviews import ReviewCoordinator
class Store:
 def get_documents_needing_detail_review(self,source,**kwargs):
  return [SimpleNamespace(source_id=source,document_id=str(i)) for i in range(40 if source=='china_news_api' else 1)]
 def record_source_failure(self,**kwargs):pass
class Review(ReviewCoordinator):
 def schedule(self,document,**kwargs):
  async def failed():
   calls.append(document.source_id)
   return False
  return failed()
calls=[]
async def main():
 review=Review(SimpleNamespace(store=Store()))
 for _ in range(3):await review.background(now=datetime.now(timezone.utc))
 print({source:calls.count(source) for source in ('china_news_api','lodestone_atom','lodestone_topics')})
asyncio.run(main())
