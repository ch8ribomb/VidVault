"""資料模型與 .txt 輸出格式。"""
from dataclasses import dataclass, field, asdict, fields

BLOCK_SEP = "\n\n"        # 區塊之間：空一行
VERSION_SEP = "\n"        # v1 內容 與 v2 之間（若想空一行，改成 "\n\n"）
RECORD_SEP = "\n\n\n"     # 不同作品之間：空兩行


@dataclass
class Record:
    zh_title: str = ""
    en_title: str = ""
    zh_title2: str = ""
    en_title2: str = ""
    url: str = ""
    company: str = ""
    episodes: str = ""          # 只存數字（最後集數）
    ongoing: bool = False       # 未完結
    tags: str = ""
    notes: str = ""
    synopsis: list = field(default_factory=lambda: [""])
    plot: list = field(default_factory=lambda: [""])

    @classmethod
    def from_dict(cls, d: dict) -> "Record":
        names = {f.name for f in fields(cls)}
        r = cls(**{k: v for k, v in d.items() if k in names})
        for k in ("synopsis", "plot"):
            v = getattr(r, k)
            if isinstance(v, str):
                setattr(r, k, [v])
            elif not v:
                setattr(r, k, [""])
        return r

    def to_dict(self) -> dict:
        return asdict(self)

    def display_name(self) -> str:
        return self.zh_title or self.en_title or self.zh_title2 or self.en_title2 or "(未命名)"


def _versions(items) -> str:
    items = [s.strip() for s in (items or []) if s and s.strip()]
    if not items:
        return ""
    if len(items) == 1:                       # 只有一個：直接輸出內容
        return items[0]
    return VERSION_SEP.join(f"v{i}\n{t}" for i, t in enumerate(items, 1))


def format_record(r: Record) -> str:
    blocks = []

    titles = [r.zh_title, r.en_title, r.zh_title2, r.en_title2]
    titles = [t.strip() for t in titles if t and t.strip()]   # 沒有的行直接省略
    if titles:
        blocks.append("\n".join(titles))

    if r.url.strip():
        blocks.append(r.url.strip())
    if r.company.strip():
        blocks.append(r.company.strip())

    ep = (r.episodes or "").strip()
    if ep:
        ep_text = ep if "集" in ep else f"{ep}集"
        if r.ongoing:
            ep_text += "(未完結)"
        blocks.append(ep_text)

    if r.tags.strip():
        blocks.append(r.tags.strip())
    if r.notes.strip():
        blocks.append(r.notes.strip())

    for items in (r.synopsis, r.plot):
        text = _versions(items)
        if text:
            blocks.append(text)

    return BLOCK_SEP.join(blocks)


def format_records(records) -> str:
    return RECORD_SEP.join(format_record(r) for r in records) + "\n"
