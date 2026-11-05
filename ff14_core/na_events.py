"""经过固定Lodestone样本约束的时间区间提取；不解析任意网页。"""
from __future__ import annotations
from html import unescape
from html.parser import HTMLParser
from datetime import datetime,timedelta,timezone
import hashlib
import re
from .documents import ContentParser
from .source_models import EventCandidate, ClassifiedEventCandidate

PARSER_VERSION='na-time-v1'
MONTHS={name.lower():index for index,names in enumerate((('January','Jan'),('February','Feb'),('March','Mar'),('April','Apr'),('May',),('June','Jun'),('July','Jul'),('August','Aug'),('September','Sep','Sept'),('October','Oct'),('November','Nov'),('December','Dec')),1) for name in names}
MONTH='(?:'+ '|'.join(sorted(MONTHS,key=len,reverse=True))+r')\.?'
DATE=rf'(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?{MONTH}\s+\d{{1,2}}(?:,\s*\d{{4}})?'
CLOCK=r'\d{1,2}(?::\d{2})?\s*(?:a\.m\.|p\.m\.)'
ZONE=r'\((?:PST|PDT|UTC|GMT)\)'
RANGE=re.compile(rf'(?P<start>{DATE}\s+(?:(?:at|from)\s+)?{CLOCK})\s*(?P<tz1>{ZONE})?\s*(?:to|through|until|–|-)\s*(?P<end>(?:{DATE}\s+(?:at\s+)?)?{CLOCK})\s*(?P<tz2>{ZONE})?',re.I)
DATE_PART=re.compile(rf'(?P<month>{MONTH})\s+(?P<day>\d{{1,2}})(?:,\s*(?P<year>\d{{4}}))?',re.I)
CLOCK_PART=re.compile(r'(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>a|p)\.m\.',re.I)
ALLOWED_LABELS=('Date & Time','Event Period','Campaign Period','Entry Period','Archive Availability','Sale Period','Live Stream Schedule','Schedule','Seasonal Event')


def _moment(text, zone, default=None, year=None):
    date=DATE_PART.search(text);clock=CLOCK_PART.search(text)
    if not clock:return None
    if date:
        y=int(date['year']) if date['year'] else (default.year if default else year)
        if y is None:return None
        month=MONTHS[date['month'].rstrip('.').lower()];day=int(date['day'])
    elif default:y,month,day=default.year,default.month,default.day
    else:return None
    hour=int(clock['hour'])
    if not 1<=hour<=12:return None
    hour=hour%12+(12 if clock['period'].lower()=='p' else 0)
    offset={'PST':-8,'PDT':-7,'UTC':0,'GMT':0}[zone]
    return datetime(y,month,day,hour,int(clock['minute'] or 0),tzinfo=timezone(timedelta(hours=offset)))


def parse_na_candidates(document, content_html):
    parser=ContentParser(document.url);parser.feed(content_html);text=parser.text
    # Publication may supply an omitted *year*, never the activity start date/time.
    try:publication=datetime.fromisoformat(document.published_at) if document.published_at else None
    except ValueError:publication=None
    candidates=[]
    for match in RANGE.finditer(text):
        prefix=text[max(0,match.start()-180):match.start()]
        if not any(label.lower() in text[:match.start()].lower() for label in ALLOWED_LABELS):continue
        if re.search(r'(?:around|approximately|sometime between)\s*$',prefix,re.I):continue
        zone1=(match['tz1'] or match['tz2'] or '').strip('()').upper()
        zone2=(match['tz2'] or match['tz1'] or '').strip('()').upper()
        if not zone1:continue
        try:
            start=_moment(match['start'],zone1,year=publication.year if publication else None)
            if start is None:continue
            end=_moment(match['end'],zone2,default=start)
            if end is None:continue
            end_date=DATE_PART.search(match['end'])
            if end<=start and end_date and not end_date['year']:
                end=end.replace(year=end.year+1)
            if end<=start:continue
            # A time-only end that precedes start is ambiguous, not an assumed next day.
        except (ValueError,KeyError):continue
        evidence=match.group(0)
        maintenance=document.category=='Maintenance'
        candidate=EventCandidate(document.source_id,document.document_id,document.title,hashlib.sha256((document.source_id+document.document_id+evidence).encode()).hexdigest()[:24],'maintenance' if maintenance else 'activity','Date & Time' if maintenance else 'Event Period',evidence,start.astimezone(timezone.utc).isoformat(),end.astimezone(timezone.utc).isoformat(),zone1 if zone1==zone2 else zone1+'→'+zone2,'explicit',region='global-na',url=document.url,time_assumption='显式PST/PDT固定offset；省略年份时使用官方文章年份；跨年结束补一年')
        candidates.append(ClassifiedEventCandidate(candidate,'maintenance' if maintenance else 'other_operational','维护' if maintenance else '其他运营活动','maintenance_candidate' if maintenance else 'calendar_candidate','维护候选' if maintenance else '日历候选','Lodestone正文明确区间；显式PST/PDT固定offset；省略年份时使用官方文章年份，跨年结束补一年。'))
    return tuple(candidates)


class LodestoneBodyParser(HTMLParser):
    """只捕获官网正文wrapper，不包含社区或导航。"""
    VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self):
        super().__init__(convert_charrefs=False);self.depth=0;self.parts=[];self.title_parts=[];self.in_title=False
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='h1':
            # The Topics page has a separate h1 for page chrome before the article h1.
            self.in_title='heading__title' not in values.get('class','').split()
            if self.in_title:self.title_parts=[]
        if self.depth:
            self.parts.append(self.get_starttag_text())
            if tag not in self.VOID:self.depth+=1
        elif 'news__detail__wrapper' in values.get('class','').split():self.depth=1
    def handle_endtag(self,tag):
        # HTMLParser invokes this after starttag for <img /> and <br /> too.
        if tag in self.VOID:return
        if tag=='h1':self.in_title=False
        if self.depth:
            self.depth-=1
            if self.depth:self.parts.append(f'</{tag}>')
    def handle_data(self,data):
        if self.depth:self.parts.append(data)
        if self.in_title:self.title_parts.append(data)
    def handle_entityref(self,name):
        if self.depth:self.parts.append('&'+name+';')
        if self.in_title:self.title_parts.append(unescape('&'+name+';'))
    def handle_charref(self,name):
        if self.depth:self.parts.append('&#'+name+';')
    @property
    def body(self):return ''.join(self.parts).strip()
    @property
    def title(self):return re.sub(r'^\[(Maintenance|Notice|Update|Status)\]\s*','',re.sub(r'\s+',' ',''.join(self.title_parts)).strip())


def date_only_evidence(document,content_html):
    """保留只有日期的证据，绝不转换为精确时刻候选。"""
    parser=ContentParser(document.url);parser.feed(content_html);text=parser.text
    pattern=re.compile(rf'(?P<start>{DATE})\s+(?:to|through|until)\s+(?P<end>{DATE})(?:\s*(?P<zone>{ZONE}))?',re.I)
    records=[]
    for match in pattern.finditer(text):
        if not any(label.lower() in text[:match.start()].lower() for label in ALLOWED_LABELS):continue
        first=DATE_PART.search(match['start']);last=DATE_PART.search(match['end'])
        if not first['year'] or not last['year']:continue
        try:
            from datetime import date
            start=date(int(first['year']),MONTHS[first['month'].rstrip('.').lower()],int(first['day']))
            end=date(int(last['year']),MONTHS[last['month'].rstrip('.').lower()],int(last['day']))
        except ValueError:continue
        if end<start:continue
        records.append({'precision':'day','start_date':start.isoformat(),'end_date':end.isoformat(),'timezone':match['zone'].strip('()') if match['zone'] else None,'evidence_text':match.group(0),'parser_version':PARSER_VERSION,'reminder_eligible':False})
    return tuple(records)
