"""讀取已存在的 .txt（本工具的固定格式），解析回 Record。"""
import codecs
import re

from .models import Record

URL_RE = re.compile(r"^https?://\S+$", re.I)
EP_RE = re.compile(r"^(\d+)\s*集\s*([\(（]\s*未完結\s*[\)）])?$")
VER_RE = re.compile(r"^v(\d+)$", re.I)
CJK_RE = re.compile(r"[\u3400-\u9fff]")
END_PUNCT = ("。", ".", "!", "?", "！", "？", "…")


def read_text_file(path) -> str:
    raw = open(path, "rb").read()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp950"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _split_blocks(text: str):
    """以空行切成區塊，回傳 [(行列表, 前面空行數)]。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks, cur, gap = [], [], 0
    for line in text.split("\n"):
        if not line.strip():
            if cur:
                blocks.append((cur, gap_before))
                cur = []
                gap = 0
            gap += 1
        else:
            if not cur:
                gap_before = gap
                gap = 0
            cur.append(line.strip())
    if cur:
        blocks.append((cur, gap_before))
    return blocks


def _is_url(block) -> bool:
    return len(block) == 1 and bool(URL_RE.match(block[0]))


def _parse_versions(lines):
    out, cur = [], None
    for ln in lines:
        if VER_RE.match(ln):
            if cur is not None:
                out.append("\n".join(cur).strip())
            cur = []
        else:
            if cur is None:
                cur = []
            cur.append(ln)
    if cur is not None:
        out.append("\n".join(cur).strip())
    return out


def _taglike(item) -> bool:
    kind, val = item
    return (kind == "single" and len(val.splitlines()) == 1 and len(val) <= 60
            and not val.rstrip().endswith(END_PUNCT))


def _as_list(item):
    return item[1] if item[0] == "versions" else [item[1]]


def _as_text(item):
    return "\n".join(item[1]) if item[0] == "versions" else item[1]


def _parse_group(blocks):
    if not blocks:
        return None
    rec = Record()
    extra_notes = []
    i = 0
    if not _is_url(blocks[0]):                       # 片名區塊
        zh = en = 0
        for ln in blocks[0]:
            if CJK_RE.search(ln):
                if not rec.zh_title:
                    rec.zh_title = ln
                elif not rec.zh_title2:
                    rec.zh_title2 = ln
                else:
                    extra_notes.append(ln)
            else:
                if not rec.en_title:
                    rec.en_title = ln
                elif not rec.en_title2:
                    rec.en_title2 = ln
                else:
                    extra_notes.append(ln)
        i = 1
    if i < len(blocks) and _is_url(blocks[i]):
        rec.url = blocks[i][0]
        i += 1
    rest = blocks[i:]

    ep_idx = next((k for k, b in enumerate(rest) if len(b) == 1 and EP_RE.match(b[0])), None)
    if ep_idx is not None:
        m = EP_RE.match(rest[ep_idx][0])
        rec.episodes, rec.ongoing = m.group(1), bool(m.group(2))
        pre, after = rest[:ep_idx], rest[ep_idx + 1:]
        if pre:
            rec.company = "\n".join(pre[0])
            extra_notes += ["\n".join(b) for b in pre[1:]]
    else:
        after = rest

    # 切成項目：單一文字 或 多版本(v1/v2…)
    items = []
    for b in after:
        m = VER_RE.match(b[0])
        if m and m.group(1) == "1":
            items.append(("versions", _parse_versions(b)))
        elif m and items and items[-1][0] == "versions":
            items[-1][1].extend(_parse_versions(b))
        else:
            items.append(("single", "\n".join(b)))

    n = len(items)
    head = []
    if n == 1:
        if _taglike(items[0]):
            rec.tags = items[0][1]
        else:
            rec.synopsis = _as_list(items[0])
    elif n == 2:
        if _taglike(items[0]):
            rec.tags, rec.synopsis = items[0][1], _as_list(items[1])
        else:
            rec.synopsis, rec.plot = _as_list(items[0]), _as_list(items[1])
    elif n >= 3:
        rec.plot, rec.synopsis = _as_list(items[-1]), _as_list(items[-2])
        head = items[:-2]
        if _taglike(head[0]):
            rec.tags, head = head[0][1], head[1:]
    notes = extra_notes + [_as_text(h) for h in head]
    rec.notes = "\n".join(notes)
    rec.synopsis = rec.synopsis or [""]
    rec.plot = rec.plot or [""]
    return rec


def _split_by_gap(seg):
    """一段區塊內，若出現連續兩個以上空行，視為新的一筆作品。"""
    out, cur = [], []
    for b, gap in seg:
        if cur and gap >= 2:
            out.append(cur)
            cur = []
        cur.append(b)
    if cur:
        out.append(cur)
    return out


def parse_txt(text: str):
    blocks = _split_blocks(text)
    url_idx = [i for i, (b, _) in enumerate(blocks) if _is_url(b)]
    groups = []
    if url_idx:                       # 以「連結」區塊定位每筆作品，再依兩個以上空行補切無連結的作品
        starts = [max(i - 1, 0) for i in url_idx]
        for n, s in enumerate(starts):
            e = starts[n + 1] if n + 1 < len(starts) else len(blocks)
            groups += _split_by_gap(blocks[s:e])
    else:
        groups = _split_by_gap(blocks)
    recs = [_parse_group(g) for g in groups]
    return [r for r in recs if r]
