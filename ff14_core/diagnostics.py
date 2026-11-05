"""安全诊断摘要；不保留任意provider响应或连接地址。"""
import logging
import re

LOGGER=logging.getLogger('ffxivbot')


def safe_reason(value):
    text=str(value).replace('\n',' ')
    text=re.sub(r'(?i)\b(?:https?|wss?)://[^\s<>]+','[URI已隐藏]',text)
    text=re.sub(r"(?i)(?:authorization|bearer|(?:access_)?token|cookie|secret|password|(?:group|user|qq)_?id)[\"']?\s*[:= ]\s*[\"']?[^\s,;\"'}]+",'[敏感字段已隐藏]',text)
    text=re.sub(r'(?<![\w-])\d{5,12}(?![\w-])','[标识已隐藏]',text)
    return text[:300]


_counts={}

def diagnostic(*,stage,source_id=None,document_id=None,target_id=None,action,error=None):
    source_id=safe_reason(source_id) if source_id else None
    document_id=str(document_id) if source_id=='china_news_api' and str(document_id).isdigit() else safe_reason(document_id) if document_id else None
    target_id=safe_reason(target_id) if target_id else None
    key=(stage,source_id,document_id,target_id,action,safe_reason(error or '-'))
    count=_counts.get(key,0)+1
    if len(_counts)>2048:_counts.clear()
    _counts[key]=count
    if count>1 and count%10:return
    LOGGER.warning('count=%s stage=%s source=%s document=%s target=%s action=%s reason=%s',count,stage,source_id or '-',document_id or '-',target_id or '-',action,safe_reason(error or '-'))
