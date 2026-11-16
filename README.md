# VidVault

短劇／影片資料庫工具

記錄短劇或影片的中英文片名、連結、出品公司、集數、tag、notes、Synopsis、劇情簡介，
並依固定格式匯出 `.txt`。

## 安裝與執行

```bash
pip install -r requirements.txt
python main.py
```

需要 Python 3.9+（Tkinter 為內建；Linux 請另外安裝 `python3-tk`）。

## 功能

- 視窗操作，資料存放於 `data/records.json`
- **依來源設定自動抓取**：貼上連結 → 「從連結抓取」，程式依網域套用 `sources.json` 的 CSS 選擇器
  - 其他欄位只補空白；Synopsis／劇情簡介已有內容時，會自動新增為 v2、v3…
- **全域快捷鍵擷取**：在瀏覽器用滑鼠選取文字 → 按快捷鍵 → 自動複製並填入欄位

| 快捷鍵 | 欄位 | 快捷鍵 | 欄位 |
|---|---|---|---|
| Ctrl+Alt+1 | 中文片名 | Ctrl+Alt+6 | 集數 |
| Ctrl+Alt+2 | 英文片名 | Ctrl+Alt+7 | tag（累加） |
| Ctrl+Alt+3 | 中文片名2 | Ctrl+Alt+8 | Synopsis（已有內容則新增版本） |
| Ctrl+Alt+4 | 英文片名2 | Ctrl+Alt+9 | 劇情簡介（同上） |
| Ctrl+Alt+5 | 出品公司 | Ctrl+Alt+0 | notes（累加） |

快捷鍵可編輯 `config.json`（首次執行後產生）的 `hotkeys`，格式為 pynput 寫法，如 `"<ctrl>+<alt>+q"`。
macOS 需在「系統設定 → 隱私權與安全性 → 輔助使用／輸入監控」授權終端機或打包後的程式。

## 來源設定（sources.json）

在視窗按「來源設定」編輯，或直接改 `sources.json`：

```json
{
  "example.com": {
    "name": "範例",
    "selectors": {
      "zh_title": "h1.title",
      "company": ".studio a",
      "episodes": ".episode-count",
      "tags": ".genre a",
      "synopsis": "meta[property='og:description']@content"
    }
  }
}
```

- 選擇器取得方式：瀏覽器 F12 → 對目標元素右鍵 → Copy → Copy selector
- `選擇器@屬性` 可取屬性值（如 `@content`、`@href`）
- 網域含子網域會自動比對；找不到時使用 `_default`
- 若網站內容由 JavaScript 動態產生，requests 抓不到，請改用快捷鍵擷取


## 連結清理規則（存入資料庫前自動套用）

| 來源 | 清理後格式 |
|---|---|
| dramatip | `https://dramatip.net/series/<名稱>/episode-1` |
| TikTok 短劇 | `https://www.tiktok.com/shortdrama/episode/<編號>/<集>`（無集數時補 `/1`） |
| Narto（nartodrama.com / narto-drama.com） | `.../detail/watch/<名稱>/1?lang=zh-TW`（或 `en-US`；其他參數全部移除） |
| AnyShort | `https://anyshort.net/<語系>/film/<名稱-編號>/watch` |
| 8movie（八影） | `https://8movie.com/movies/<編號>/` → `https://8movie.com/play/<編號>/1` |

- 按「儲存此作品」時，程式會顯示清理後的連結與完整預覽，**確認後才入庫**；也可先按「清理連結」。
- 資料庫已有相同連結或片名時會提示。
- 「從連結抓取」時，8movie 的 `/play/` 連結會自動改用 `/movies/` 作品頁取得資訊。

## 匯入既有 .txt

按「匯入 .txt」選擇檔案（支援 UTF-8、UTF-16、Big5）：

1. 程式依固定區塊解析（片名 → 連結 → 公司 → 集數 → tag → notes → Synopsis → 劇情簡介），支援 `v1`、`v2` 多版本。
2. 跳出預覽視窗，可逐筆檢查、勾選；標示［重複］者預設不選取，若選取將覆蓋既有資料。
3. 按「匯入所選」後會再次確認，才寫入資料庫。

解析限制：以「連結」區塊定位每筆作品；沒有連結的作品需與前一筆相隔兩個空行。
公司、tag、notes 缺漏時依內容特徵判斷（例如 tag 為單行短文字），請以預覽視窗確認。

## 匯出格式

```
中文片名
英文片名
中文片名2
英文片名2

連結

出品公司

40集(未完結)

tag

notes

v1
Synopsis 第一版
v2
Synopsis 第二版

v1
劇情簡介第一版
v2
劇情簡介第二版
```

- 沒有的片名行會直接省略；沒有 notes 等空欄位整個區塊省略
- 只有一個 Synopsis／劇情簡介時不加 `v1`
- 區塊間空一行；不同作品之間空兩行（可在 `vidvault/models.py` 調整 `RECORD_SEP`、`VERSION_SEP`）

## 打包成 exe

圖示檔放在 `assets/Video_Library.ico`，打包時一併帶入：

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name VidVault --icon assets/Video_Library.ico --add-data "assets/Video_Library.ico;assets" main.py
```

macOS／Linux 的 `--add-data` 分隔符號請改用冒號：`"assets/Video_Library.ico:assets"`。
打包後 `data/`、`sources.json`、`config.json` 會建立在 exe 同一個資料夾。
