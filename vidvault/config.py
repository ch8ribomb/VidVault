"""路徑、預設設定與 JSON 讀寫。"""
import json
import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):          # PyInstaller 打包後
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(rel: str) -> Path:
    """取得隨程式打包的資源（如圖示）；PyInstaller 打包後位於 sys._MEIPASS。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


APP_NAME = "VidVault"
ICON_FILE = resource_path("assets/Video_Library.ico")
APP_DIR = app_dir()
DATA_DIR = APP_DIR / "data"
RECORDS_FILE = DATA_DIR / "records.json"
SOURCES_FILE = APP_DIR / "sources.json"
CONFIG_FILE = APP_DIR / "config.json"

# 欄位代號 -> 顯示名稱
FIELD_LABELS = {
    "zh_title": "中文片名",
    "en_title": "英文片名",
    "zh_title2": "中文片名2",
    "en_title2": "英文片名2",
    "company": "出品公司",
    "episodes": "集數",
    "tags": "tag",
    "notes": "notes",
    "synopsis": "Synopsis",
    "plot": "劇情簡介",
}

# 全域快捷鍵（選取網頁文字後按下，會自動複製並填入對應欄位）
DEFAULT_HOTKEYS = {
    "<ctrl>+<alt>+1": "zh_title",
    "<ctrl>+<alt>+2": "en_title",
    "<ctrl>+<alt>+3": "zh_title2",
    "<ctrl>+<alt>+4": "en_title2",
    "<ctrl>+<alt>+5": "company",
    "<ctrl>+<alt>+6": "episodes",
    "<ctrl>+<alt>+7": "tags",
    "<ctrl>+<alt>+8": "synopsis",
    "<ctrl>+<alt>+9": "plot",
    "<ctrl>+<alt>+0": "notes",
}

# 各來源網站的擷取設定。key 為網域（含子網域會自動比對）。
# 選擇器為 CSS selector；若要取屬性，寫成 "選擇器@屬性"，例如 "meta[property='og:title']@content"
DEFAULT_SOURCES = {
    "_default": {
        "name": "通用（Open Graph）",
        "selectors": {
            "zh_title": "meta[property='og:title']@content",
            "synopsis": "meta[property='og:description']@content",
        },
    },
    "example.com": {
        "name": "範例來源（請改成實際網域與選擇器）",
        "selectors": {
            "zh_title": "h1.title",
            "en_title": "h2.subtitle",
            "company": ".studio a",
            "episodes": ".episode-count",
            "tags": ".genre a",
            "synopsis": ".synopsis",
            "plot": ".plot",
        },
    },
}


# 常用來源（首次啟動時自動加入來源設定清單，選擇器請在「來源設定」補上）
KNOWN_SOURCES = {
    d: {"name": n, "selectors": {}}
    for d, n in [
        ("nartodrama.com", "Narto"),
        ("narto-drama.com", "Narto"),
        ("dramatip.net", "Dramatip"),
        ("anyshort.net", "AnyShort"),
        ("8movie.com", "8movies（八影）"),
        ("tiktok.com", "TikTok 短劇"),
    ]
}


def load_json(path: Path, default):
    path = Path(path)
    if not path.exists():
        save_json(path, default)
        return json.loads(json.dumps(default))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(default))


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
