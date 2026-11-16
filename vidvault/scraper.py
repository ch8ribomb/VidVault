"""依來源設定抓取網頁指定區塊。"""
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .urlclean import fetch_url

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
ONGOING_RE = re.compile(r"更新|連載|未完結|未完结|ongoing|updating", re.I)
_ATTR_RE = re.compile(r"^(.*)@([A-Za-z_:][\w:-]*)$", re.S)


def find_source(url: str, sources: dict):
    host = urlparse(url).netloc.lower().split(":")[0]
    best = None
    for key in sources:
        if key.startswith("_"):
            continue
        k = key.lower()
        if host == k or host.endswith("." + k):
            if best is None or len(k) > len(best):
                best = key
    if best is None and "_default" in sources:
        best = "_default"
    return best


def _extract(soup: BeautifulSoup, spec: str) -> list:
    if not spec or not spec.strip():
        return []
    attr = None
    m = _ATTR_RE.match(spec.strip())
    if m:
        spec, attr = m.group(1), m.group(2)
    out = []
    for node in soup.select(spec.strip()):
        val = node.get(attr) if attr else node.get_text(" ", strip=True)
        if isinstance(val, list):
            val = " ".join(val)
        val = (val or "").strip()
        if val and val not in out:
            out.append(val)
    return out


def parse_html(html: str, selectors: dict) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    fields, ongoing = {}, False
    for name, spec in selectors.items():
        texts = _extract(soup, spec)
        if not texts:
            continue
        if name == "tags":
            fields[name] = ", ".join(texts)
        elif name == "episodes":
            m = re.search(r"\d+", texts[0])
            if m:
                fields[name] = m.group()
            ongoing = bool(ONGOING_RE.search(" ".join(texts)))
        elif name == "notes":
            fields[name] = "\n".join(texts)
        else:                                   # 片名、公司、synopsis、劇情簡介：取第一筆
            fields[name] = texts[0]
    return {"fields": fields, "ongoing": ongoing}


def scrape(url: str, sources: dict) -> dict:
    key = find_source(url, sources)
    if key is None:
        raise ValueError("找不到此網址對應的來源設定，且沒有 _default 設定。")
    resp = requests.get(fetch_url(url), headers=HEADERS, timeout=20)
    resp.raise_for_status()
    if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    selectors = dict(sources.get("_default", {}).get("selectors", {}))   # 先套通用設定，再以來源設定覆蓋
    selectors.update(sources[key].get("selectors", {}))
    result = parse_html(resp.text, selectors)
    result["source"] = key
    return result
