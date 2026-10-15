"""잘 된 글의 '뼈대'만 뽑기 (소제목, 표·목록·사진 개수, 글자 수). 본문은 베끼지 않도록 넘기지 않는다."""
from html.parser import HTMLParser

import requests

# 티스토리 스킨들이 본문을 담는 div 클래스
BODY_CLASSES = ("tt_article_useless_p_margin", "contents_style", "entry-content", "article-view")
HEADINGS = ("h2", "h3", "h4")


class _Outline(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0          # 본문 div 안에서의 div 깊이 (0 = 본문 밖)
        self.heading = None
        self.headings, self.counts, self.chars = [], {"table": 0, "ul": 0, "ol": 0, "img": 0}, 0
        self._skip = 0          # script/style 안

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if self.depth == 0:
            cls = dict(attrs).get("class") or ""
            if tag == "div" and any(c in cls for c in BODY_CLASSES):
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
        elif tag in HEADINGS:
            self.heading = [tag, ""]
        elif tag in self.counts:
            self.counts[tag] += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if self.depth == 0:
            return
        if tag == "div":
            self.depth -= 1
        elif tag in HEADINGS and self.heading:
            text = " ".join(self.heading[1].split())
            if text:
                self.headings.append(f"{self.heading[0]}: {text}")
            self.heading = None

    def handle_data(self, data):
        if self.depth == 0 or self._skip:
            return
        if self.heading:
            self.heading[1] += data
        self.chars += len("".join(data.split()))


def outline(url):
    """본문을 못 찾거나 받아오지 못하면 None."""
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (blog-growth report)"})
        r.raise_for_status()
    except requests.RequestException:
        return None
    p = _Outline()
    p.feed(r.text)
    if not p.chars:
        return None
    return {"headings": p.headings, "chars": p.chars, **p.counts}
