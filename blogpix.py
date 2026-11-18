#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlogPix v3.1 — 網頁貼文圖片批次下載（視窗版）

  • 搭配瀏覽器腳本 BlogPix.user.js：滑鼠移到圖片上點「⬇ BlogPix」，
    或框選貼文區塊後按全域快捷鍵，即可下載整篇圖片（不需要網址）
  • 也可貼上網址下載（備用；微博為精準解析，其他網站為盡力解析）
  • 儲存位置：每次詢問 / 固定預設資料夾 / 沿用上次位置
  • 檔名：網路原名 / 批量重新命名 / 批量重新命名並檢查目的資料夾是否已有該批圖片
  • 格式：沿用原始格式 / 轉換為指定格式

需求： pip install requests Pillow
"""
import hashlib
import ipaddress
import json
import os
import queue
import re
import socket
import sys
import threading
import time
from datetime import datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

try:
    from PIL import Image, ImageOps
    HAVE_PIL = True
except Exception:  # 沒有 Pillow 時仍可下載，只是不能轉檔
    HAVE_PIL = False

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception:  # 讓邏輯部分可在無 tkinter 環境測試
    tk = None

__version__ = "3.1"
APP_NAME = "BlogPix"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
WEIBO_REFERER = "https://weibo.com/"

# ======================================================================
# 設定檔
# ======================================================================
DEFAULT_CFG = {
    "folder_mode": "fixed",        # ask 每次詢問 / fixed 固定預設資料夾 / last 沿用上次
    "default_folder": "",
    "last_folder": "",
    "name_mode": "original",       # original 原名 / rename 批量重新命名 / rename_check 重新命名並檢查
    "orig_exists": "skip",         # 原名模式下檔案已存在：skip 略過 / suffix 另存為 (1)
    "name_template": "{post}_{n}",
    "name_start": 1,
    "name_digits": 2,
    "format": "original",          # original 或 JPG / PNG / WEBP / BMP / TIFF / GIF
    "quality": 95,
    "hotkey_enabled": True,
    "hotkey": "Ctrl+Alt+D",
    "page_button": True,           # 網頁圖片上顯示「⬇ BlogPix」按鈕
    "cookie": "",
    "port": 8765,
}


def config_dir():
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


class Config:
    def __init__(self, path=None):
        self.path = Path(path) if path else config_dir() / "config.json"
        self.data = dict(DEFAULT_CFG)
        self.lock = threading.Lock()
        self.load()

    def load(self):
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            for k in DEFAULT_CFG:
                if k in saved:
                    self.data[k] = saved[k]
        except Exception:
            # 沿用舊版（v1/v2）記住的資料夾
            for old in (".blogpix_config.json", ".weibo_dl_config.json"):
                try:
                    f = json.loads((Path.home() / old).read_text(encoding="utf-8")).get("folder")
                    if f:
                        self.data["default_folder"] = f
                        self.data["last_folder"] = f
                        break
                except Exception:
                    continue

    def save(self):
        with self.lock:
            try:
                self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
            except Exception:
                pass

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    def snapshot(self):
        return dict(self.data)


# ======================================================================
# 圖片格式
# ======================================================================
FORMATS = {  # 顯示名稱 -> (Pillow 格式, 副檔名)
    "JPG": ("JPEG", ".jpg"), "PNG": ("PNG", ".png"), "WEBP": ("WEBP", ".webp"),
    "BMP": ("BMP", ".bmp"), "TIFF": ("TIFF", ".tif"), "GIF": ("GIF", ".gif"),
}
LABEL_EXT = {"JPG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp", "BMP": ".bmp",
             "TIFF": ".tif", "AVIF": ".avif", "HEIC": ".heic"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".heic"}
EXT_LABEL = {".jpg": "JPG", ".jpeg": "JPG", ".png": "PNG", ".gif": "GIF", ".webp": "WEBP",
             ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF", ".avif": "AVIF", ".heic": "HEIC"}


def sniff(data):
    """依檔案內容判斷格式，不依賴副檔名。"""
    if data[:3] == b"\xff\xd8\xff":
        return "JPG"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if data[:4] == b"GIF8":
        return "GIF"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[:2] == b"BM":
        return "BMP"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "TIFF"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"avif", b"avis"):
            return "AVIF"
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1"):
            return "HEIC"
    return None


class KeepOriginal(Exception):
    """此圖不轉檔，保留原始格式（附原因）。"""


def convert_bytes(data, target, quality):
    if not HAVE_PIL:
        raise KeepOriginal("未安裝 Pillow，無法轉檔")
    fmt, _ = FORMATS[target]
    im = Image.open(BytesIO(data))
    if getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1:
        raise KeepOriginal("動態圖，保留原格式")
    im.load()
    try:
        im = ImageOps.exif_transpose(im)  # 保留 JPEG 的拍攝方向
    except Exception:
        pass
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if fmt in ("JPEG", "BMP"):
        if has_alpha:
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
    elif fmt == "GIF":
        im = im.convert("RGBA" if has_alpha else "RGB").convert("P", palette=Image.ADAPTIVE)
    else:
        if im.mode not in ("RGB", "RGBA", "L", "LA"):
            im = im.convert("RGBA" if has_alpha else "RGB")
    buf = BytesIO()
    kw = {}
    if fmt == "JPEG":
        kw = {"quality": quality, "optimize": True}
    elif fmt == "WEBP":
        kw = {"quality": quality}
    im.save(buf, format=fmt, **kw)
    return buf.getvalue()


# ======================================================================
# 檔名工具
# ======================================================================
def safe_name(s):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip().strip(".")
    return s[:150]


def render_template(tpl, post, n, digits, orig_stem, date=None):
    if "{n}" not in tpl:
        tpl += "_{n}"
    mapping = {"post": post, "n": str(n).zfill(digits), "orig": orig_stem,
               "date": date or datetime.now().strftime("%Y%m%d")}
    out = re.sub(r"\{(post|n|orig|date)\}", lambda m: mapping[m.group(1)], tpl)
    return safe_name(out) or f"{post}_{n}"


def url_basename(u):
    return safe_name(unquote(os.path.basename(urlparse(u).path)))


def src_key(u):
    """同一張圖的穩定識別碼：微博用圖片 ID，其他網站用網址雜湊。"""
    p = urlparse(u)
    if p.hostname and p.hostname.endswith("sinaimg.cn"):
        return os.path.splitext(os.path.basename(p.path))[0]
    return hashlib.sha1((p.netloc + p.path).encode("utf-8")).hexdigest()[:16]


def unique_path(folder, stem, ext):
    p = folder / f"{stem}{ext}"
    n = 1
    while p.exists():
        p = folder / f"{stem} ({n}){ext}"
        n += 1
    return p


class FolderIndex:
    """記錄此資料夾內「來源圖片 → 存檔名」，供模式 3 檢查該批圖片是否已存在。"""
    NAME = ".blogpix_index.json"

    def __init__(self, folder):
        self.path = folder / self.NAME
        self.items = {}
        try:
            self.items = json.loads(self.path.read_text(encoding="utf-8")).get("items", {})
        except Exception:
            pass

    def get(self, key):
        return self.items.get(key)

    def set(self, key, name):
        self.items[key] = name

    def save(self):
        try:
            self.path.write_text(json.dumps({"v": 1, "items": self.items}, ensure_ascii=False),
                                 encoding="utf-8")
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(self.path), 0x2)  # 隱藏
        except Exception:
            pass


# ======================================================================
# 網址解析：取得整篇貼文的圖片清單
# ======================================================================
class Source:
    def __init__(self, label, urls, referer="", site=""):
        self.label = safe_name(label) or "post"
        self.urls = urls
        self.referer = referer
        self.site = site


def new_session(cookie=""):
    s = requests.Session()
    s.headers["User-Agent"] = UA
    if cookie:
        s.headers["Cookie"] = cookie
    return s


def weibo_id(url):
    for p in (r"weibo\.com/\d+/([A-Za-z0-9]+)", r"weibo\.com/detail/(\w+)",
              r"m\.weibo\.cn/(?:detail|status)/(\w+)", r"weibo\.cn/comment/(\w+)"):
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _weibo_pc(session, pid):
    r = session.get("https://weibo.com/ajax/statuses/show",
                    params={"id": pid, "locale": "zh-CN"},
                    headers={"Referer": WEIBO_REFERER}, timeout=20)
    d = r.json()
    post = d
    if not post.get("pic_ids") and post.get("retweeted_status"):
        post = post["retweeted_status"]
    infos = post.get("pic_infos", {})
    urls = []
    for pic_id in post.get("pic_ids", []):
        info = infos.get(pic_id, {})
        u = None
        for k in ("original", "largest", "mw2000", "large"):
            if info.get(k, {}).get("url"):
                u = info[k]["url"]
                break
        urls.append(u or f"https://wx1.sinaimg.cn/oslarge/{pic_id}.jpg")
    return urls


def _weibo_mobile(session, pid):
    r = session.get("https://m.weibo.cn/statuses/show", params={"id": pid},
                    headers={"Referer": f"https://m.weibo.cn/detail/{pid}"}, timeout=20)
    data = r.json().get("data") or {}
    post = data
    if not post.get("pics") and post.get("retweeted_status"):
        post = post["retweeted_status"]
    return [u for u in ((p.get("large") or {}).get("url") or p.get("url")
                        for p in post.get("pics", [])) if u]


def resolve_weibo(session, pid):
    for fn in (_weibo_pc, _weibo_mobile):
        try:
            urls = fn(session, pid)
            if urls:
                return Source(pid, urls, WEIBO_REFERER, "weibo.com")
        except Exception:
            continue
    raise RuntimeError("找不到圖片（貼文無圖、已刪除，或需要登入 → 請到「快捷鍵與進階」填入 Cookie）")


SKIP_IMG = re.compile(r"(sprite|logo|icon|avatar|favicon|emoji|loading|blank|pixel|spacer|1x1|"
                      r"badge|\.svg(\?|$)|\.ico(\?|$))", re.I)


class _ImgParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.urls = []

    def _add(self, u):
        if not u or u.startswith(("data:", "blob:", "javascript:")):
            return
        u = urljoin(self.base, u.strip())
        if urlparse(u).scheme in ("http", "https") and not SKIP_IMG.search(u) and u not in self.urls:
            self.urls.append(u)

    @staticmethod
    def _largest(srcset):
        best, bw = None, -1
        for part in srcset.split(","):
            bits = part.strip().split()
            if not bits:
                continue
            w = 0
            if len(bits) > 1:
                try:
                    w = float(bits[1].rstrip("wx"))
                except ValueError:
                    pass
            if w >= bw:
                best, bw = bits[0], w
        return best

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("property") in ("og:image", "twitter:image")
                              or a.get("name") in ("twitter:image", "og:image")):
            self._add(a.get("content"))
        elif tag in ("img", "source"):
            for k in ("data-original", "data-src", "data-lazy-src", "data-actualsrc"):
                if a.get(k):
                    self._add(a[k])
                    return
            if a.get("srcset") or a.get("data-srcset"):
                self._add(self._largest(a.get("srcset") or a.get("data-srcset")))
            elif a.get("src"):
                self._add(a["src"])
        elif tag == "a":
            h = a.get("href") or ""
            if os.path.splitext(urlparse(h).path)[1].lower() in IMG_EXTS:
                self._add(h)


def resolve_generic(session, url):
    r = session.get(url, headers={"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}, timeout=25)
    r.raise_for_status()
    if "html" not in r.headers.get("Content-Type", "html"):
        if sniff(r.content[:16]):
            return Source(src_key(url), [url], url, urlparse(url).hostname or "")
    p = _ImgParser(r.url)
    p.feed(r.text)
    if not p.urls:
        raise RuntimeError("此網頁解析不到圖片（可能由 JavaScript 動態載入，建議改用瀏覽器快捷鍵下載）")
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:6]
    return Source(f"{urlparse(url).hostname}_{h}", p.urls, url, urlparse(url).hostname or "")


def resolve_url(url, cookie=""):
    s = new_session(cookie)
    wid = weibo_id(url) if re.search(r"weibo\.(com|cn)", url) else None
    if wid:
        return resolve_weibo(s, wid)
    return resolve_generic(s, url)


def url_allowed(u):
    """瀏覽器腳本送來的網址：只允許公開網路，避免被利用來存取區域網路。"""
    try:
        p = urlparse(u)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved \
                    or ip.is_multicast or ip.is_unspecified:
                return False
        return True
    except Exception:
        return False


# ======================================================================
# 下載引擎
# ======================================================================
def fetch(session, url, referer, retries=3):
    last = None
    for _ in range(retries):
        try:
            r = session.get(url, headers={"Referer": referer or url}, timeout=40)
            r.raise_for_status()
            return r.content
        except Exception as e:
            last = e
            time.sleep(1.2)
    raise last


def run_download(src, folder, cfg, log, progress, session=None):
    """依設定下載一篇貼文的所有圖片。回傳統計 dict。"""
    session = session or new_session()
    folder.mkdir(parents=True, exist_ok=True)
    index = FolderIndex(folder)
    mode = cfg["name_mode"]
    target = cfg["format"]                       # "original" 或格式名稱
    tpl, start, digits = cfg["name_template"], int(cfg["name_start"]), int(cfg["name_digits"])
    quality = int(cfg["quality"])
    try:
        existing = {Path(f).stem.lower() for f in os.listdir(folder)
                    if Path(f).suffix.lower() in IMG_EXTS}
    except OSError:
        existing = set()

    stat = {"saved": 0, "skipped": 0, "failed": 0, "orig": {}, "converted": 0}
    total = len(src.urls)
    log(f"貼文 {src.label}：共 {total} 張 → {folder}")
    progress(0, total)
    for i, u in enumerate(src.urls):
        n = start + i
        key = src_key(u)
        orig_name = url_basename(u) or f"{src.label}_{i + 1:02d}"
        orig_stem, url_ext = os.path.splitext(orig_name)
        if url_ext.lower() not in IMG_EXTS:
            orig_stem, url_ext = orig_name, ""
        tag = f"[{i + 1}/{total}]"

        # ---- 下載前先判斷是否已存在（可免下載）----
        if mode == "original":
            pred_ext = LABEL_EXT.get(target, "") if target != "original" else url_ext
            pred = folder / f"{orig_stem}{pred_ext or '.jpg'}"
            if pred.exists() and cfg["orig_exists"] == "skip":
                log(f"  {tag} {pred.name}  已存過，略過")
                stat["skipped"] += 1
                progress(i + 1, total)
                continue
        elif mode == "rename_check":
            prev = index.get(key)
            stem_new = render_template(tpl, src.label, n, digits, orig_stem)
            # 舊版以原名存的檔案也視為已存在（僅限夠長、不易重名的檔名，如微博圖片 ID）
            legacy = len(orig_stem) >= 20 and orig_stem.lower() in existing
            if (prev and (folder / prev).exists()) or stem_new.lower() in existing or legacy:
                log(f"  {tag} {prev or stem_new}  該圖已存在，略過")
                stat["skipped"] += 1
                progress(i + 1, total)
                continue

        # ---- 下載 ----
        try:
            data = fetch(session, u, src.referer)
        except Exception as e:
            log(f"  {tag} 下載失敗：{e}")
            stat["failed"] += 1
            progress(i + 1, total)
            continue

        label = sniff(data) or EXT_LABEL.get(url_ext.lower(), "?")
        stat["orig"][label] = stat["orig"].get(label, 0) + 1

        # ---- 轉檔（若有指定）----
        ext = LABEL_EXT.get(label) or url_ext or ".jpg"
        note = ""
        if target != "original" and label != target:
            try:
                data = convert_bytes(data, target, quality)
                ext = LABEL_EXT[target]
                stat["converted"] += 1
                note = f"（{label}→{target}）"
            except KeepOriginal as e:
                note = f"（{e}）"
            except Exception as e:
                note = f"（轉檔失敗，保留原格式：{e}）"

        # ---- 決定檔名 ----
        if mode == "original":
            stem = orig_stem
        else:
            stem = render_template(tpl, src.label, n, digits, orig_stem)
        dest = folder / f"{stem}{ext}"
        if dest.exists():
            if mode == "original" and cfg["orig_exists"] == "skip":
                log(f"  {tag} {dest.name}  已存過，略過")
                stat["skipped"] += 1
                progress(i + 1, total)
                continue
            dest = unique_path(folder, stem, ext)
        try:
            dest.write_bytes(data)
        except OSError as e:
            log(f"  {tag} 寫入失敗：{e}")
            stat["failed"] += 1
            progress(i + 1, total)
            continue
        index.set(key, dest.name)
        existing.add(dest.stem.lower())
        stat["saved"] += 1
        log(f"  {tag} {dest.name}{note}")
        progress(i + 1, total)
        time.sleep(0.15)

    index.save()
    fm = "、".join(f"{k}×{v}" for k, v in stat["orig"].items()) or "—"
    log(f"完成：新存 {stat['saved']} 張、略過 {stat['skipped']} 張、失敗 {stat['failed']} 張　原始格式：{fm}\n")
    return stat


# ======================================================================
# 全域快捷鍵（Windows）與衝突檢查
# ======================================================================
MOD_BITS = {"Alt": 0x1, "Ctrl": 0x2, "Shift": 0x4, "Win": 0x8}
MOD_ORDER = ["Ctrl", "Alt", "Shift", "Win"]
VK_NAMED = {"Space": 0x20, "PageUp": 0x21, "PageDown": 0x22, "End": 0x23, "Home": 0x24,
            "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28, "Insert": 0x2D, "Delete": 0x2E}
KEY_CHOICES = ([chr(c) for c in range(ord("A"), ord("Z") + 1)] + [str(i) for i in range(10)]
               + [f"F{i}" for i in range(1, 13)] + list(VK_NAMED))


def normalize_combo(s):
    parts = [p.strip() for p in s.split("+") if p.strip()]
    mods = [m for m in MOD_ORDER if m in parts]
    key = [p for p in parts if p not in MOD_BITS]
    return "+".join(mods + key[:1])


def parse_combo(s):
    parts = s.split("+")
    key = parts[-1]
    mods = 0
    for p in parts[:-1]:
        mods |= MOD_BITS[p]
    if len(key) == 1:
        vk = ord(key.upper())
    elif re.fullmatch(r"F\d{1,2}", key):
        vk = 0x70 + int(key[1:]) - 1
    else:
        vk = VK_NAMED[key]
    return mods, vk


_KNOWN_RAW = {
    # Windows 系統
    "Win+D": "Windows：顯示桌面", "Win+E": "Windows：檔案總管", "Win+L": "Windows：鎖定電腦",
    "Win+R": "Windows：執行", "Win+S": "Windows：搜尋", "Win+I": "Windows：設定",
    "Win+V": "Windows：剪貼簿記錄", "Win+C": "Windows：Copilot", "Win+Tab": "Windows：工作檢視",
    "Win+Shift+S": "Windows：螢幕擷取", "Alt+F4": "Windows：關閉視窗", "Alt+Tab": "Windows：切換視窗",
    "Ctrl+Alt+Delete": "Windows：安全性選項", "Alt+Space": "Windows：視窗選單",
    "Ctrl+Space": "輸入法：中英切換", "Ctrl+Shift+Space": "輸入法", "Alt+Shift+Tab": "Windows：切換視窗",
    # 瀏覽器（Edge / Chrome）
    "Ctrl+D": "瀏覽器：加入書籤", "Ctrl+T": "瀏覽器：新分頁", "Ctrl+W": "瀏覽器：關閉分頁",
    "Ctrl+N": "瀏覽器：新視窗", "Ctrl+Shift+N": "瀏覽器：無痕視窗", "Ctrl+Shift+T": "瀏覽器：還原分頁",
    "Ctrl+L": "瀏覽器：網址列", "Ctrl+R": "瀏覽器：重新整理", "Ctrl+F": "瀏覽器：尋找",
    "Ctrl+S": "瀏覽器：儲存網頁", "Ctrl+P": "瀏覽器：列印", "Ctrl+H": "瀏覽器：記錄",
    "Ctrl+J": "瀏覽器：下載項目", "Ctrl+U": "瀏覽器：檢視原始碼", "Ctrl+Shift+S": "Edge：網頁擷取",
    "Alt+Shift+S": "Edge：視覺搜尋", "Ctrl+Shift+D": "瀏覽器：全部分頁加入書籤",
    "Ctrl+Shift+B": "瀏覽器：書籤列", "Ctrl+Shift+I": "瀏覽器：開發人員工具",
    "Ctrl+Shift+Delete": "瀏覽器：清除瀏覽資料", "Ctrl+Shift+O": "瀏覽器：書籤管理員",
    "Ctrl+Shift+W": "瀏覽器：關閉視窗", "Ctrl+Shift+A": "瀏覽器：搜尋分頁",
    "Alt+D": "瀏覽器：網址列", "Alt+Left": "瀏覽器：上一頁", "Alt+Right": "瀏覽器：下一頁",
    "F5": "瀏覽器：重新整理", "F11": "瀏覽器：全螢幕", "F12": "瀏覽器：開發人員工具",
    # 常見編輯 / 通訊軟體
    "Ctrl+C": "複製", "Ctrl+V": "貼上", "Ctrl+X": "剪下", "Ctrl+Z": "復原", "Ctrl+Y": "取消復原",
    "Ctrl+A": "全選", "Alt+A": "微信/QQ：截圖", "Ctrl+Alt+A": "微信/QQ：截圖",
    "Ctrl+Alt+Z": "QQ：顯示視窗", "Ctrl+Alt+W": "微信：顯示視窗", "Alt+Z": "NVIDIA GeForce：遊戲內浮層",
}
KNOWN_SHORTCUTS = {normalize_combo(k): v for k, v in _KNOWN_RAW.items()}

ERR_HOTKEY_TAKEN = 1409


def check_hotkey(hk, combo):
    """回傳 (level, 訊息)；level: ok / warn（可自行決定）/ block（不可用）。"""
    combo = normalize_combo(combo)
    parts = combo.split("+")
    mods = [p for p in parts if p in MOD_BITS]
    if not mods:
        return "block", "必須搭配 Ctrl、Alt、Shift 或 Win 其中至少一個，否則會影響平常打字。"
    if "Shift" in mods and not any(m in mods for m in ("Ctrl", "Alt", "Win")):
        return "block", "只用 Shift 會影響大寫輸入，請再加上 Ctrl 或 Alt。"
    if combo in KNOWN_SHORTCUTS:
        return "warn", f"與常見快捷鍵重複：{KNOWN_SHORTCUTS[combo]}。啟用後，該功能在所有程式都會失效。"
    if hk is not None and hk.supported:
        ok, err = hk.test(combo)
        if not ok:
            if err == ERR_HOTKEY_TAKEN:
                return "block", "此組合已被 Windows 或其他程式註冊為全域快捷鍵，無法使用。"
            return "block", f"無法註冊此組合（Windows 錯誤碼 {err}）。"
        return "ok", "未偵測到衝突。\n（可偵測 Windows 與其他程式註冊的全域快捷鍵，以及常見瀏覽器／系統快捷鍵；各程式內部自有的快捷鍵無法完全偵測。）"
    return "ok", "此系統不支援全域快捷鍵偵測，僅以常見快捷鍵清單比對，未發現重複。"


class HotkeyManager:
    def __init__(self, callback):
        self.cb = callback
        self.cmds = queue.Queue()
        self.supported = sys.platform == "win32"
        self.thread = None
        self.active = None

    def start(self):
        if self.supported and not self.thread:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def _call(self, *cmd):
        if not self.supported:
            return False, -1
        self.start()
        rq = queue.Queue()
        self.cmds.put((cmd, rq))
        try:
            return rq.get(timeout=3)
        except queue.Empty:
            return False, -2

    def register(self, combo):
        ok, err = self._call("reg", 1, combo)
        self.active = combo if ok else None
        return ok, err

    def unregister(self):
        self._call("unreg", 1)
        self.active = None

    def test(self, combo):
        if combo == self.active:
            return True, 0
        ok, err = self._call("reg", 2, combo)
        if ok:
            self._call("unreg", 2)
        return ok, err

    def _loop(self):
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        u.RegisterHotKey.restype = wintypes.BOOL
        u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT, wintypes.UINT]
        msg = wintypes.MSG()
        while True:
            try:
                cmd, rq = self.cmds.get_nowait()
            except queue.Empty:
                cmd = None
            if cmd:
                if cmd[0] == "reg":
                    u.UnregisterHotKey(None, cmd[1])
                    mods, vk = parse_combo(cmd[2])
                    ok = u.RegisterHotKey(None, cmd[1], mods | 0x4000, vk)  # MOD_NOREPEAT
                    rq.put((bool(ok), 0 if ok else ctypes.get_last_error()))
                else:
                    u.UnregisterHotKey(None, cmd[1])
                    rq.put((True, 0))
                continue
            while u.PeekMessageW(ctypes.byref(msg), None, 0x312, 0x312, 1):  # WM_HOTKEY
                if msg.wParam == 1:
                    try:
                        self.cb()
                    except Exception:
                        pass
            time.sleep(0.03)


# ======================================================================
# 視窗程式
# ======================================================================
def resource_path(name):
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


class App:
    def __init__(self):
        self.cfg = Config()
        self.ui_q = queue.Queue()
        self.job_q = queue.Queue()
        self.trigger_id = 0
        self.trigger_t = 0.0
        self.last_poll = 0.0
        self.last_orig = "—"
        self.server = None
        self.hk = HotkeyManager(self._on_hotkey)

        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{__version__}")
        try:
            self.root.iconbitmap(resource_path("BlogPix.ico"))
        except Exception:
            pass
        self.root.minsize(700, 660)
        self.root.geometry("720x700")
        self._setup_style()
        self._build_ui()
        self._start_server()
        threading.Thread(target=self._worker, daemon=True).start()
        self._startup_hotkey()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(80, self._pump)
        self.root.after(1000, self._tick)

    # ---------- 樣式 ----------
    def _setup_style(self):
        import tkinter.font as tkfont
        families = set(tkfont.families())
        for fam in ("Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans CJK TC", "PingFang TC"):
            if fam in families:
                self.root.option_add("*Font", (fam, 10))
                ttk.Style().configure(".", font=(fam, 10))
                break
        self.mono = "Consolas" if "Consolas" in families else "TkFixedFont"

    # ---------- 介面 ----------
    def _build_ui(self):
        c = self.cfg
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=10)
        t1, t2, t3 = ttk.Frame(self.nb, padding=12), ttk.Frame(self.nb, padding=12), ttk.Frame(self.nb, padding=12)
        self.nb.add(t1, text="  下載  ")
        self.nb.add(t2, text="  儲存位置與檔名  ")
        self.nb.add(t3, text="  快捷鍵與進階  ")

        # ===== 分頁 1：下載 =====
        self.v_url = tk.StringVar()
        how = ttk.LabelFrame(t1, text="網頁點選下載（不需要網址）", padding=10)
        how.pack(fill="x")
        ttk.Label(how, justify="left",
                  text="① 在瀏覽器把滑鼠移到貼文的圖片上，點出現的橘色「⬇ BlogPix」按鈕\n"
                       "② 或用滑鼠框選想下載的貼文區塊（框到圖片就只下載選到的），再按快捷鍵"
                  ).pack(anchor="w")
        self.l_howkey = ttk.Label(how, font=(self.mono, 10, "bold"))
        self.l_howkey.pack(anchor="w", pady=(2, 0))
        self.l_status = ttk.Label(t1, text="", foreground="#333")
        self.l_status.pack(anchor="w", pady=(8, 0))
        self.l_summary = ttk.Label(t1, text="", foreground="#666")
        self.l_summary.pack(anchor="w", pady=(2, 6))
        self.pb = ttk.Progressbar(t1, mode="determinate")
        self.pb.pack(fill="x")

        box = ttk.Frame(t1)
        box.pack(fill="both", expand=True, pady=8)
        self.txt = tk.Text(box, height=10, wrap="word", state="disabled", font=(self.mono, 9),
                           relief="solid", borderwidth=1)
        sb = ttk.Scrollbar(box, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        bar = ttk.Frame(t1)
        bar.pack(fill="x")
        ttk.Button(bar, text="開啟儲存資料夾", command=self._open_folder).pack(side="left")
        ttk.Button(bar, text="複製記錄", command=self._copy_log).pack(side="left", padx=6)
        ttk.Button(bar, text="清除記錄", command=self._clear_log).pack(side="left")

        alt = ttk.LabelFrame(t1, text="備用：直接貼上網址", padding=8)
        alt.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(alt)
        row.pack(fill="x")
        ent = ttk.Entry(row, textvariable=self.v_url)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self._download_clicked())
        ttk.Button(row, text="貼上", width=6, command=self._paste).pack(side="left", padx=6)
        ttk.Button(row, text="下載", command=self._download_clicked).pack(side="left")

        # ===== 分頁 2：儲存位置與檔名 =====
        self.v_folder_mode = tk.StringVar(value=c["folder_mode"])
        self.v_default_folder = tk.StringVar(value=c["default_folder"])
        self.v_name_mode = tk.StringVar(value=c["name_mode"])
        self.v_orig_exists = tk.StringVar(value=c["orig_exists"])
        self.v_template = tk.StringVar(value=c["name_template"])
        self.v_start = tk.StringVar(value=str(c["name_start"]))
        self.v_digits = tk.StringVar(value=str(c["name_digits"]))
        cur = c["format"]
        self.v_fmt_mode = tk.StringVar(value="original" if cur == "original" else "convert")
        self.v_target = tk.StringVar(value=cur if cur in FORMATS else "JPG")
        self.v_quality = tk.StringVar(value=str(c["quality"]))

        f1 = ttk.LabelFrame(t2, text="儲存位置", padding=10)
        f1.pack(fill="x")
        for val, txt in (("ask", "每次下載前詢問要存到哪個資料夾"),
                         ("fixed", "固定使用預設資料夾（不詢問）"),
                         ("last", "沿用上次使用的位置")):
            ttk.Radiobutton(f1, text=txt, value=val, variable=self.v_folder_mode).pack(anchor="w")
        r = ttk.Frame(f1)
        r.pack(fill="x", pady=(6, 0))
        ttk.Label(r, text="預設資料夾").pack(side="left")
        ttk.Entry(r, textvariable=self.v_default_folder).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(r, text="選擇…", command=self._pick_default).pack(side="left")
        self.l_last = ttk.Label(f1, foreground="#666")
        self.l_last.pack(anchor="w", pady=(4, 0))

        f2 = ttk.LabelFrame(t2, text="檔名", padding=10)
        f2.pack(fill="x", pady=10)
        ttk.Radiobutton(f2, text="使用網路上的原始檔名", value="original",
                        variable=self.v_name_mode).pack(anchor="w")
        r = ttk.Frame(f2)
        r.pack(anchor="w", padx=(24, 0))
        ttk.Label(r, text="原名檔案已存在時：").pack(side="left")
        self.cb_exists = ttk.Combobox(r, state="readonly", width=16,
                                      values=["略過（視為已存過）", "另存為 名稱 (1)"])
        self.cb_exists.pack(side="left")
        self.cb_exists.current(0 if c["orig_exists"] == "skip" else 1)
        self.cb_exists.bind("<<ComboboxSelected>>", self._collect)
        ttk.Radiobutton(f2, text="批量重新命名", value="rename",
                        variable=self.v_name_mode).pack(anchor="w", pady=(6, 0))
        ttk.Radiobutton(f2, text="批量重新命名，並檢查目的資料夾是否已存在該批圖片（已存在的圖不重複下載）",
                        value="rename_check", variable=self.v_name_mode).pack(anchor="w")
        self.f_tpl = ttk.Frame(f2)
        self.f_tpl.pack(fill="x", padx=(24, 0), pady=(4, 0))
        ttk.Label(self.f_tpl, text="命名格式").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.f_tpl, textvariable=self.v_template).grid(row=0, column=1, columnspan=5,
                                                                  sticky="ew", padx=6)
        ttk.Label(self.f_tpl, text="起始編號").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Spinbox(self.f_tpl, from_=0, to=99999, width=7, textvariable=self.v_start
                    ).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(self.f_tpl, text="編號位數").grid(row=1, column=2, sticky="e")
        ttk.Spinbox(self.f_tpl, from_=1, to=6, width=4, textvariable=self.v_digits
                    ).grid(row=1, column=3, sticky="w", padx=6)
        self.l_preview = ttk.Label(self.f_tpl, foreground="#0a5")
        self.l_preview.grid(row=1, column=4, columnspan=2, sticky="w")
        ttk.Label(self.f_tpl, foreground="#666",
                  text="可用：{post} 貼文代碼　{n} 序號　{orig} 原檔名　{date} 日期"
                  ).grid(row=2, column=0, columnspan=6, sticky="w")
        self.f_tpl.columnconfigure(5, weight=1)

        f3 = ttk.LabelFrame(t2, text="檔案格式", padding=10)
        f3.pack(fill="x")
        ttk.Radiobutton(f3, text="沿用原始格式（不轉換）", value="original",
                        variable=self.v_fmt_mode).pack(anchor="w")
        r = ttk.Frame(f3)
        r.pack(anchor="w")
        ttk.Radiobutton(r, text="轉換為：", value="convert", variable=self.v_fmt_mode).pack(side="left")
        self.cb_target = ttk.Combobox(r, state="readonly", width=8, values=list(FORMATS),
                                      textvariable=self.v_target)
        self.cb_target.pack(side="left")
        ttk.Label(r, text="　品質").pack(side="left")
        self.sp_q = ttk.Spinbox(r, from_=1, to=100, width=5, textvariable=self.v_quality)
        self.sp_q.pack(side="left", padx=4)
        ttk.Label(r, text="（JPG／WEBP）", foreground="#666").pack(side="left")
        self.l_orig = ttk.Label(f3, foreground="#0a5")
        self.l_orig.pack(anchor="w", pady=(6, 0))
        if not HAVE_PIL:
            ttk.Label(f3, foreground="#c00", text="未安裝 Pillow，無法轉換格式。").pack(anchor="w")

        # ===== 分頁 3：快捷鍵與進階 =====
        self.v_hk_on = tk.BooleanVar(value=c["hotkey_enabled"])
        self.v_btn_on = tk.BooleanVar(value=c["page_button"])
        f0 = ttk.LabelFrame(t3, text="網頁按鈕", padding=10)
        f0.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(f0, text="滑鼠移到網頁圖片上時，顯示「⬇ BlogPix」下載按鈕",
                        variable=self.v_btn_on).pack(anchor="w")
        f4 = ttk.LabelFrame(t3, text="快捷鍵（滑鼠移到貼文上，或框選區塊後按下，即可下載整篇圖片）", padding=10)
        f4.pack(fill="x")
        ttk.Checkbutton(f4, text="啟用全域快捷鍵（取消勾選則只用「貼上網址」下載）",
                        variable=self.v_hk_on, command=self._toggle_hotkey).pack(anchor="w")
        r = ttk.Frame(f4)
        r.pack(fill="x", pady=6)
        ttk.Label(r, text="目前快捷鍵：").pack(side="left")
        self.l_hk = ttk.Label(r, font=(self.mono, 11, "bold"))
        self.l_hk.pack(side="left")
        ttk.Button(r, text="變更並檢查衝突…", command=self._hotkey_dialog).pack(side="left", padx=12)
        self.l_hk_state = ttk.Label(f4, foreground="#666", wraplength=620, justify="left")
        self.l_hk_state.pack(anchor="w")
        if not self.hk.supported:
            ttk.Label(f4, foreground="#c00", text="此系統不是 Windows，全域快捷鍵不可用。").pack(anchor="w")

        f5 = ttk.LabelFrame(t3, text="瀏覽器腳本", padding=10)
        f5.pack(fill="x", pady=10)
        self.l_script = ttk.Label(f5, wraplength=620, justify="left")
        self.l_script.pack(anchor="w")
        ttk.Label(f5, foreground="#666", wraplength=620, justify="left",
                  text="快捷鍵需搭配瀏覽器腳本（BlogPix.user.js，以 Tampermonkey 安裝）。腳本負責判斷滑鼠所在的貼文並取得圖片清單，"
                       "存檔位置、檔名與格式一律依本程式的設定。").pack(anchor="w", pady=(4, 0))

        f6 = ttk.LabelFrame(t3, text="進階（選填）", padding=10)
        f6.pack(fill="x")
        self.v_cookie = tk.StringVar(value=c["cookie"])
        r = ttk.Frame(f6)
        r.pack(fill="x")
        ttk.Label(r, text="微博 Cookie").pack(side="left")
        ttk.Entry(r, textvariable=self.v_cookie, show="•").pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(f6, foreground="#666", wraplength=620, justify="left",
                  text="僅在「貼上網址」下載、且貼文需登入才看得到時使用；以快捷鍵下載則直接使用瀏覽器登入狀態，不需要填。"
                       "Cookie 等同登入憑證，只存在本機設定檔，請勿分享。").pack(anchor="w", pady=(4, 0))

        # 綁定變更事件（放最後，避免初始化時觸發）
        for v in (self.v_folder_mode, self.v_default_folder, self.v_name_mode, self.v_template,
                  self.v_start, self.v_digits, self.v_fmt_mode, self.v_target, self.v_quality,
                  self.v_cookie, self.v_btn_on):
            v.trace_add("write", self._collect)
        self._collect()

    # ---------- 設定收集 ----------
    @staticmethod
    def _int(s, default, lo, hi):
        try:
            return max(lo, min(hi, int(str(s).strip())))
        except (ValueError, TypeError):
            return default

    def _collect(self, *_):
        c = self.cfg
        c["folder_mode"] = self.v_folder_mode.get()
        c["default_folder"] = self.v_default_folder.get().strip()
        c["name_mode"] = self.v_name_mode.get()
        c["orig_exists"] = "skip" if self.cb_exists.current() == 0 else "suffix"
        c["name_template"] = self.v_template.get() or "{post}_{n}"
        c["name_start"] = self._int(self.v_start.get(), 1, 0, 99999)
        c["name_digits"] = self._int(self.v_digits.get(), 2, 1, 6)
        c["format"] = "original" if self.v_fmt_mode.get() == "original" else self.v_target.get()
        c["quality"] = self._int(self.v_quality.get(), 95, 1, 100)
        c["cookie"] = self.v_cookie.get().strip()
        c["page_button"] = bool(self.v_btn_on.get())
        c.save()
        self._refresh_states()

    def _refresh_states(self):
        c = self.cfg
        renaming = c["name_mode"] in ("rename", "rename_check")
        for w in self.f_tpl.winfo_children():
            try:
                w.configure(state="normal" if renaming else "disabled")
            except tk.TclError:
                pass
        self.cb_exists.configure(state="readonly" if c["name_mode"] == "original" else "disabled")
        conv = c["format"] != "original"
        self.cb_target.configure(state="readonly" if conv else "disabled")
        self.sp_q.configure(state="normal" if conv and c["format"] in ("JPG", "WEBP") else "disabled")
        ex = render_template(c["name_template"], "Qabc123", c["name_start"], c["name_digits"], "0088skurly1ij2")
        ext = LABEL_EXT.get(c["format"], ".jpg") if conv else ".jpg"
        self.l_preview.configure(text=f"範例：{ex}{ext}")
        self.l_last.configure(text=f"上次使用位置：{c['last_folder'] or '（尚無）'}")
        self.l_orig.configure(text=f"最近一篇貼文的原始格式：{self.last_orig}")
        self.l_hk.configure(text=c["hotkey"] if c["hotkey_enabled"] else "（已停用）")
        self.l_howkey.configure(text=(f"目前快捷鍵：{c['hotkey']}" if c["hotkey_enabled"] else "快捷鍵已停用（仍可點網頁上的按鈕）")
                                + ("" if c["page_button"] else "　｜　網頁按鈕已關閉"))

    def _pick_default(self):
        d = filedialog.askdirectory(parent=self.root, title="選擇預設資料夾",
                                    initialdir=self.cfg["default_folder"] or None)
        if d:
            self.v_default_folder.set(os.path.normpath(d))

    # ---------- 記錄 / 狀態 ----------
    def log(self, text):
        self.ui_q.put(("log", text))

    def set_progress(self, done, total):
        self.ui_q.put(("progress", done, total))

    def run_on_main(self, fn):
        ev, box = threading.Event(), {}
        self.ui_q.put(("call", fn, ev, box))
        ev.wait()
        return box.get("r")

    def _pump(self):
        try:
            while True:
                item = self.ui_q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.txt.configure(state="normal")
                    for line in str(item[1]).split("\n"):
                        stamp = datetime.now().strftime("%H:%M:%S ")
                        self.txt.insert("end", (stamp if line.strip() and not line.startswith("  ") else "         ") + line + "\n")
                    self.txt.see("end")
                    self.txt.configure(state="disabled")
                elif kind == "progress":
                    self.pb.configure(maximum=max(item[2], 1), value=item[1])
                elif kind == "orig":
                    self.last_orig = item[1]
                    self._refresh_states()
                elif kind == "call":
                    try:
                        item[3]["r"] = item[1]()
                    finally:
                        item[2].set()
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _tick(self):
        c = self.cfg
        connected = time.time() - self.last_poll < 4
        if self.server is None:
            self.l_status.configure(text="⚠ 網頁連線埠被占用（BlogPix 可能已開啟），瀏覽器功能暫不可用；仍可貼上網址下載。")
        elif c["hotkey_enabled"] and self.hk.active:
            self.l_status.configure(text=f"待命中　快捷鍵 {self.hk.active}　瀏覽器腳本：" +
                                    ("已連線 ✓" if connected else "未偵測到（請確認已安裝並開啟網頁）"))
        elif c["hotkey_enabled"]:
            self.l_status.configure(text="快捷鍵未啟用（註冊失敗，請到「快捷鍵與進階」更換）　仍可點網頁上的按鈕下載")
        else:
            self.l_status.configure(text="待命中　瀏覽器腳本：" + ("已連線 ✓" if connected else "未偵測到（請確認已安裝並開啟網頁）"))
        self.l_script.configure(text="腳本連線狀態：" + ("已連線 ✓" if connected else "尚未偵測到（請在瀏覽器開啟任一網頁，並確認腳本已安裝）"))
        fm = {"ask": "每次詢問", "fixed": "固定預設資料夾", "last": "沿用上次位置"}[c["folder_mode"]]
        nm = {"original": "網路原名", "rename": "批量重新命名", "rename_check": "重新命名＋檢查已存在"}[c["name_mode"]]
        ft = "原始格式" if c["format"] == "original" else f"轉為 {c['format']}"
        self.l_summary.configure(text=f"位置：{fm}　檔名：{nm}　格式：{ft}")
        self.root.after(1000, self._tick)

    def _clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def _copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.txt.get("1.0", "end"))

    def _open_folder(self):
        d = self.cfg["last_folder"] or self.cfg["default_folder"]
        if d and os.path.isdir(d):
            try:
                os.startfile(d)
            except AttributeError:
                messagebox.showinfo(APP_NAME, d)
        else:
            messagebox.showinfo(APP_NAME, "尚未有儲存資料夾。")

    def _paste(self):
        try:
            self.v_url.set(self.root.clipboard_get().strip())
        except tk.TclError:
            pass

    def _download_clicked(self):
        text = self.v_url.get().strip()
        if not text:
            self._paste()
            text = self.v_url.get().strip()
        urls = [u for u in re.split(r"\s+", text) if u]
        if not urls:
            return
        for u in urls:
            if not re.match(r"https?://", u):
                self.log(f"不是有效網址，已略過：{u}")
                continue
            self.job_q.put(("url", u, None, None))
        self.v_url.set("")

    # ---------- 工作執行緒 ----------
    def _worker(self):
        while True:
            kind, payload, ev, box = self.job_q.get()
            try:
                res = self._do_job(kind, payload)
            except Exception as e:
                self.log(f"錯誤：{e}\n")
                res = {"ok": False, "error": str(e)}
            if box is not None:
                box.update(res)
            if ev is not None:
                ev.set()

    def _do_job(self, kind, payload):
        cfg = self.cfg.snapshot()
        if kind == "url":
            self.log(f"解析網址：{payload}")
            src = resolve_url(payload, cfg["cookie"])
            session = new_session(cfg["cookie"] if src.site.endswith("weibo.com") else "")
        else:
            src = payload
            session = new_session()
        exts = {}
        for u in src.urls:
            lab = EXT_LABEL.get(os.path.splitext(urlparse(u).path)[1].lower())
            if lab:
                exts[lab] = exts.get(lab, 0) + 1
        if exts:
            self.ui_q.put(("orig", "、".join(f"{k}×{v}" for k, v in exts.items()) + "（依網址推測，下載後以實際內容為準）"))

        folder = self._choose_folder(len(src.urls), src.label, cfg)
        if folder is None:
            self.log("已取消（未選擇資料夾）\n")
            return {"ok": True, "cancelled": True}
        stat = run_download(src, folder, cfg, self.log, self.set_progress, session)
        self.cfg["last_folder"] = str(folder)
        if not self.cfg["default_folder"]:
            self.cfg["default_folder"] = str(folder)  # 第一次使用：順便設為預設資料夾
            self.ui_q.put(("call", lambda: self.v_default_folder.set(str(folder)), threading.Event(), {}))
        self.cfg.save()
        if stat["orig"]:
            self.ui_q.put(("orig", "、".join(f"{k}×{v}" for k, v in stat["orig"].items())))
        return {"ok": True, "saved": stat["saved"], "skipped": stat["skipped"],
                "failed": stat["failed"], "folder": str(folder)}

    def _choose_folder(self, count, label, cfg):
        mode = cfg["folder_mode"]
        d = None
        if mode == "fixed":
            d = cfg["default_folder"]
        elif mode == "last":
            d = cfg["last_folder"] or cfg["default_folder"]
        if d:
            return Path(d)

        def ask():
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
            return filedialog.askdirectory(
                parent=self.root, title=f"選擇下載資料夾（貼文 {label}，共 {count} 張）",
                initialdir=cfg["last_folder"] or cfg["default_folder"] or None)
        d = self.run_on_main(ask)
        return Path(d) if d else None

    # ---------- 內建網頁伺服器（供瀏覽器腳本使用）----------
    def _start_server(self):
        app = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self):
                # 自訂標頭：一般網頁無法跨站偽造，避免被其他網站濫用
                if self.headers.get("X-BlogPix") != "1":
                    self._send(403, {"ok": False, "error": "forbidden"})
                    return False
                return True

            def do_GET(self):
                if not self._auth():
                    return
                app.last_poll = time.time()
                self._send(200, {"ok": True, "version": __version__, "trigger": app.trigger_id,
                                 "age": round(time.time() - app.trigger_t, 2),
                                 "button": bool(app.cfg["page_button"])})

            def do_POST(self):
                if not self._auth():
                    return
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(n).decode("utf-8"))
                except Exception:
                    return self._send(400, {"ok": False, "error": "bad request"})
                app.last_poll = time.time()
                urls = [u for u in data.get("urls", []) if isinstance(u, str)][:300]
                hosts_ok = {}
                good = []
                for u in urls:
                    h = urlparse(u).hostname
                    if h not in hosts_ok:
                        hosts_ok[h] = url_allowed(u)
                    if hosts_ok[h]:
                        good.append(u)
                if not good:
                    return self._send(400, {"ok": False, "error": "no valid images"})
                ref = data.get("referer") or ""
                if not re.match(r"https?://", ref):
                    ref = ""
                src = Source(str(data.get("post", "post")), good, ref, str(data.get("site", "")))
                ev, box = threading.Event(), {}
                app.job_q.put(("source", src, ev, box))
                ev.wait(timeout=900)
                self._send(200, box or {"ok": False, "error": "timeout"})

        try:
            srv = ThreadingHTTPServer(("127.0.0.1", int(self.cfg["port"])), H)
            srv.daemon_threads = True
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.server = srv
        except OSError:
            self.server = None

    # ---------- 快捷鍵 ----------
    def _on_hotkey(self):
        self.trigger_id += 1
        self.trigger_t = time.time()
        if time.time() - self.last_poll > 5:
            self.log("快捷鍵已觸發，但沒有偵測到瀏覽器腳本回應。請確認 Tampermonkey 已安裝 BlogPix.user.js，且目前停留在網頁上。")
        else:
            self.log("快捷鍵已觸發，等待瀏覽器取得貼文圖片…")

    def _startup_hotkey(self):
        c = self.cfg
        if not c["hotkey_enabled"] or not self.hk.supported:
            return
        ok, err = self.hk.register(c["hotkey"])
        if ok:
            self.l_hk_state.configure(text="已啟用。")
        else:
            self.l_hk_state.configure(text=f"註冊失敗（錯誤碼 {err}），多半是被其他程式占用。請按「變更並檢查衝突」更換。")
            self.log(f"快捷鍵 {c['hotkey']} 註冊失敗（可能被其他程式占用），請到「快捷鍵與進階」更換。")

    def _toggle_hotkey(self):
        self.cfg["hotkey_enabled"] = self.v_hk_on.get()
        self.cfg.save()
        if self.v_hk_on.get():
            ok, err = self.hk.register(self.cfg["hotkey"])
            self.l_hk_state.configure(text="已啟用。" if ok else f"註冊失敗（錯誤碼 {err}），請更換快捷鍵。")
        else:
            self.hk.unregister()
            self.l_hk_state.configure(text="已停用，請用「貼上網址」下載。")
        self._refresh_states()

    def _hotkey_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("設定快捷鍵")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        try:
            dlg.iconbitmap(resource_path("BlogPix.ico"))
        except Exception:
            pass
        cur = normalize_combo(self.cfg["hotkey"]).split("+")
        vs = {m: tk.BooleanVar(value=m in cur) for m in MOD_ORDER}
        v_key = tk.StringVar(value=cur[-1] if cur[-1] not in MOD_BITS else "D")
        body = ttk.Frame(dlg, padding=14)
        body.pack(fill="both", expand=True)
        r = ttk.Frame(body)
        r.pack(anchor="w")
        for m in MOD_ORDER:
            ttk.Checkbutton(r, text=m, variable=vs[m]).pack(side="left", padx=(0, 8))
        ttk.Label(r, text="＋ 按鍵").pack(side="left")
        cb = ttk.Combobox(r, state="readonly", width=9, values=KEY_CHOICES, textvariable=v_key)
        cb.pack(side="left", padx=6)
        l_combo = ttk.Label(body, font=(self.mono, 12, "bold"))
        l_combo.pack(anchor="w", pady=(10, 4))
        l_res = ttk.Label(body, wraplength=440, justify="left")
        l_res.pack(anchor="w", pady=4)
        state = {"level": None}

        def combo():
            return normalize_combo("+".join([m for m in MOD_ORDER if vs[m].get()] + [v_key.get()]))

        def refresh(*_):
            l_combo.configure(text=combo())
            state["level"] = None
            l_res.configure(text="請按「檢查衝突」。", foreground="#666")

        def do_check():
            level, msg = check_hotkey(self.hk, combo())
            state["level"] = level
            icon = {"ok": "✓ ", "warn": "⚠ ", "block": "✗ "}[level]
            l_res.configure(text=icon + msg, foreground={"ok": "#0a5", "warn": "#b60", "block": "#c00"}[level])
            return level

        def apply():
            level = state["level"] or do_check()
            if level == "block":
                return
            if level == "warn" and not messagebox.askyesno(
                    APP_NAME, "此組合與常見快捷鍵重複，啟用後那個功能會失效。\n仍要使用嗎？", parent=dlg):
                return
            new = combo()
            old = self.cfg["hotkey"]
            if self.hk.supported and self.v_hk_on.get():
                ok, err = self.hk.register(new)
                if not ok:
                    self.hk.register(old)
                    messagebox.showerror(APP_NAME, f"註冊失敗（錯誤碼 {err}），已保留原快捷鍵。", parent=dlg)
                    return
            self.cfg["hotkey"] = new
            self.cfg.save()
            self.l_hk_state.configure(text="已更新。")
            self._refresh_states()
            dlg.destroy()

        for v in vs.values():
            v.trace_add("write", refresh)
        v_key.trace_add("write", refresh)
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="檢查衝突", command=do_check).pack(side="left")
        ttk.Button(bar, text="取消", command=dlg.destroy).pack(side="right")
        ttk.Button(bar, text="套用", command=apply).pack(side="right", padx=6)
        refresh()

    # ---------- 結束 ----------
    def _close(self):
        try:
            self.cfg.save()
            self.hk.unregister()
            if self.server:
                self.server.shutdown()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.log(f"BlogPix v{__version__} 已啟動。")
        self.root.mainloop()


def main():
    if tk is None:
        sys.exit("此環境缺少 tkinter，無法開啟視窗。")
    try:
        App().run()
    except Exception as e:  # 視窗版沒有主控台，把錯誤顯示出來並寫入記錄檔
        try:
            (config_dir() / "crash.log").write_text(repr(e), encoding="utf-8")
            messagebox.showerror(APP_NAME, f"程式發生錯誤：\n{e}")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
