"""不可变原始文章版本与机械比较，不推断变动的重要性。"""
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin

NORMALIZER_VERSION = "article-v1"


class ContentParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts = []
        self.images = []
        self.links = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append(" ")
        if tag == "img":
            for key in ("src", "srcset"):
                if values.get(key):
                    self.images.append(urljoin(self.base_url, values[key]) if key == "src" else values[key])
        if tag == "a" and values.get("href"):
            self.links.append(urljoin(self.base_url, values["href"]))

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)

    @property
    def text(self):
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


def normalize_document(title, content_html, url):
    parser = ContentParser(url)
    parser.feed(content_html)
    return {"title": re.sub(r"\s+", " ", title).strip(), "text": parser.text,
            "images": parser.images, "links": parser.links}


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class DocumentVersion:
    version_id: int
    source_id: str
    document_id: str
    observed_at: str
    title: str
    url: str
    published_at: str | None
    category: str
    content_html: str
    normalized: dict
    comparison_hash: str
    normalizer_version: str
    changed: bool


def version_diff(before, after):
    if before is None:
        return "首次完整内容基线"
    changes = []
    for key, label in (("title", "标题"), ("text", "正文"), ("images", "图片引用"), ("links", "链接地址")):
        if before.normalized[key] != after.normalized[key]:
            changes.append(f"{label}：{before.normalized[key]} → {after.normalized[key]}")
    return "\n".join(changes) or "机械内容未变化"
