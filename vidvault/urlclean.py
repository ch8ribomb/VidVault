"""各來源網址的清理規則。clean_url() 回傳 (清理後網址, 來源代號或 None)。"""
import re
from urllib.parse import parse_qs, quote, urlsplit


def clean_url(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return raw, None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw
    p = urlsplit(raw)
    netloc = p.netloc.lower()
    host = netloc.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    path = p.path

    # dramatip：https://dramatip.net/series/<slug>/episode-1
    if host == "dramatip.net":
        m = re.match(r"^/series/([^/]+)", path)
        if m:
            return f"https://{netloc}/series/{m.group(1)}/episode-1", "dramatip"

    # TikTok 短劇：https://www.tiktok.com/shortdrama/episode/<id>/<集>
    if host == "tiktok.com":
        m = re.match(r"^/shortdrama/episode/(\d+)(?:/(\d+))?", path)
        if m:
            return f"https://{netloc}/shortdrama/episode/{m.group(1)}/{m.group(2) or 1}", "tiktok"

    # narto：/detail/watch/<slug>/1?lang=xx（其餘參數、後綴全部移除）
    if host in ("nartodrama.com", "narto-drama.com"):
        m = re.match(r"^/detail/watch/([^/]+)", path)
        if m:
            lang = parse_qs(p.query).get("lang", [None])[0]
            url = f"https://{netloc}/detail/watch/{m.group(1)}/1"
            return (f"{url}?lang={lang}" if lang else url), "narto"

    # anyshort：/<語系>/film/<名稱-編號>/watch
    if host == "anyshort.net":
        m = re.match(r"^(/[^/]+/film/[^/]+)", path)
        if m:
            return f"https://{netloc}{quote(m.group(1), safe='/%')}/watch", "anyshort"

    # 8movie：/movies/<id>/ → /play/<id>/1
    if host == "8movie.com":
        m = re.match(r"^/(?:movies|play)/(\d+)", path)
        if m:
            return f"https://{netloc}/play/{m.group(1)}/1", "8movies"

    return raw, None


def fetch_url(url: str) -> str:
    """抓取資訊時使用的網址：8movie 的播放頁改回作品頁（資訊在 /movies/ 頁）。"""
    p = urlsplit((url or "").strip())
    host = p.netloc.lower().split(":")[0].removeprefix("www.")
    if host == "8movie.com":
        m = re.match(r"^/play/(\d+)", p.path)
        if m:
            return f"https://{p.netloc}/movies/{m.group(1)}/"
    return url
