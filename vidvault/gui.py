"""Tkinter 主視窗。"""
import os
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from . import scraper, txtimport, urlclean
from .config import (
    APP_NAME, CONFIG_FILE, DATA_DIR, ICON_FILE, DEFAULT_HOTKEYS, DEFAULT_SOURCES, FIELD_LABELS, KNOWN_SOURCES,
    RECORDS_FILE, SOURCES_FILE, load_json, save_json,
)
from .hotkeys import HotkeyManager
from .models import Record, format_record, format_records

ENTRY_FIELDS = ["zh_title", "en_title", "zh_title2", "en_title2", "url", "company", "tags"]
ONGOING_RE = scraper.ONGOING_RE


def ask_preview(parent, title, message, preview, yes_text="確定", no_text="取消") -> bool:
    """顯示預覽內容並詢問是否繼續。"""
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.geometry("700x560")
    dlg.transient(parent)
    result = {"ok": False}
    ttk.Label(dlg, text=message, justify="left", wraplength=660).pack(anchor="w", padx=12, pady=8)
    txt = tk.Text(dlg, wrap="word")
    txt.insert("1.0", preview)
    txt.configure(state="disabled")
    txt.pack(fill="both", expand=True, padx=12)
    bar = ttk.Frame(dlg)
    bar.pack(pady=8)

    def done(v):
        result["ok"] = v
        dlg.destroy()

    ttk.Button(bar, text=yes_text, command=lambda: done(True)).pack(side="left", padx=6)
    ttk.Button(bar, text=no_text, command=lambda: done(False)).pack(side="left", padx=6)
    dlg.protocol("WM_DELETE_WINDOW", lambda: done(False))
    dlg.wait_visibility()
    dlg.grab_set()
    dlg.wait_window()
    return result["ok"]


class ImportDialog(tk.Toplevel):
    """讀取 .txt 後的預覽與勾選視窗；確認後才會入庫。"""

    def __init__(self, app, records, path):
        super().__init__(app)
        self.app, self.records = app, records
        self.title(f"匯入 .txt：{os.path.basename(path)}")
        self.geometry("1000x640")
        ttk.Label(self, text=f"解析到 {len(records)} 筆作品。請檢查右側內容後，選取要入庫的項目"
                             "（標示［重複］者預設不選取，若選取將覆蓋既有資料）。",
                  wraplength=960, justify="left").pack(anchor="w", padx=10, pady=8)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10)
        self.lb = tk.Listbox(body, selectmode="extended", exportselection=False, width=42)
        self.lb.pack(side="left", fill="y")
        self.lb.bind("<<ListboxSelect>>", self._show)
        self.preview = tk.Text(body, wrap="word")
        self.preview.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.dups = [app._find_dup(r) for r in records]
        for i, r in enumerate(records):
            self.lb.insert("end", ("［重複］" if self.dups[i] is not None else "") + r.display_name())
            if self.dups[i] is None:
                self.lb.selection_set(i)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=8)
        self.clean_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="入庫時清理連結", variable=self.clean_var).pack(side="left")
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bar, text="匯入所選", command=self._do_import).pack(side="right", padx=4)
        ttk.Button(bar, text="全選", command=lambda: self.lb.selection_set(0, "end")).pack(side="right", padx=4)
        if records:
            self.lb.activate(0)
            self._show_index(0)

    def _show(self, _e):
        sel = self.lb.curselection()
        if sel:
            self._show_index(sel[-1])

    def _show_index(self, i):
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", format_record(self.records[i]))

    def _do_import(self):
        sel = list(self.lb.curselection())
        if not sel:
            messagebox.showinfo("未選取", "請先選取要匯入的作品。", parent=self)
            return
        picked = []
        for i in sel:
            r = Record.from_dict(self.records[i].to_dict())
            if self.clean_var.get():
                r.url = urlclean.clean_url(r.url)[0]
            picked.append((r, self.dups[i]))
        new = sum(1 for _, d in picked if d is None)
        over = len(picked) - new
        msg = f"即將入庫 {len(picked)} 筆：新增 {new} 筆" + (f"、覆蓋既有 {over} 筆" if over else "") + "。\n確定要寫入資料庫嗎？"
        names = "\n".join(f"{'[覆蓋] ' if d is not None else '[新增] '}{r.display_name()}" for r, d in picked)
        if ask_preview(self, "確認匯入", msg, names, "確定入庫"):
            self.app.apply_import(picked)
            self.destroy()


class VersionedText(ttk.Frame):
    """多版本文字區（v1、v2…）。"""

    def __init__(self, master):
        super().__init__(master)
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="＋新增版本", command=self.add_version).pack(side="left")
        ttk.Button(bar, text="－刪除目前版本", command=self.remove_version).pack(side="left")
        self.texts = []
        self.add_version()

    def add_version(self, text=""):
        t = tk.Text(self.nb, height=6, wrap="word", undo=True)
        t.insert("1.0", text)
        self.texts.append(t)
        self.nb.add(t, text=f"v{len(self.texts)}")
        self.nb.select(t)
        return t

    def remove_version(self):
        if len(self.texts) <= 1:
            self.texts[0].delete("1.0", "end")
            return
        idx = self.nb.index(self.nb.select())
        t = self.texts.pop(idx)
        self.nb.forget(idx)
        t.destroy()
        for i in range(len(self.texts)):
            self.nb.tab(i, text=f"v{i + 1}")

    def get(self):
        return [t.get("1.0", "end").strip() for t in self.texts]

    def set(self, values):
        for t in self.texts:
            self.nb.forget(t)
            t.destroy()
        self.texts = []
        for v in (values or [""]):
            self.add_version(v)
        self.nb.select(0)

    def is_empty(self):
        return not any(self.get())

    def capture(self, text):
        """目前版本已有內容就新增下一版，否則填入目前版本。"""
        if text in self.get():
            return
        cur = self.texts[self.nb.index(self.nb.select())]
        if cur.get("1.0", "end").strip():
            cur = self.add_version()
        cur.insert("1.0", text)


class SourceDialog(tk.Toplevel):
    """各來源網站的擷取設定。"""

    def __init__(self, master, sources, on_save):
        super().__init__(master)
        self.title("來源設定（CSS 選擇器）")
        self.sources, self.on_save = sources, on_save
        self.geometry("620x520")
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="網域：").pack(side="left")
        self.combo = ttk.Combobox(top, values=list(sources.keys()), width=30)
        self.combo.pack(side="left", padx=4)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.load())
        ttk.Button(top, text="新增", command=self.add).pack(side="left", padx=2)
        ttk.Button(top, text="刪除", command=self.delete).pack(side="left")

        form = ttk.Frame(self)
        form.pack(fill="both", expand=True, padx=10)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="來源名稱").grid(row=0, column=0, sticky="w", pady=3)
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew")
        self.vars = {}
        for i, (k, label) in enumerate(FIELD_LABELS.items(), 1):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", pady=3)
            self.vars[k] = tk.StringVar()
            ttk.Entry(form, textvariable=self.vars[k]).grid(row=i, column=1, sticky="ew")
        tip = ("選擇器請用瀏覽器 F12 → 對目標右鍵 → Copy → Copy selector。\n"
               "要取屬性請寫成「選擇器@屬性」，例如 meta[property='og:title']@content。\n"
               "tag 會抓取所有符合的項目；集數會自動取出數字，含「更新/連載」則標示未完結。")
        ttk.Label(self, text=tip, justify="left", foreground="#555").pack(anchor="w", padx=10, pady=6)
        ttk.Button(self, text="儲存", command=self.save).pack(pady=6)
        if sources:
            self.combo.set(next(iter(sources)))
            self.load()

    def load(self):
        s = self.sources.get(self.combo.get(), {})
        self.name_var.set(s.get("name", ""))
        sel = s.get("selectors", {})
        for k, v in self.vars.items():
            v.set(sel.get(k, ""))

    def add(self):
        key = self.combo.get().strip().lower()
        if not key:
            return
        self.sources.setdefault(key, {"name": key, "selectors": {}})
        self.combo["values"] = list(self.sources.keys())
        self.load()

    def delete(self):
        key = self.combo.get()
        if key in self.sources and messagebox.askyesno("刪除", f"刪除來源設定 {key}？", parent=self):
            del self.sources[key]
            self.combo["values"] = list(self.sources.keys())
            self.combo.set("")
            self.on_save()

    def save(self):
        key = self.combo.get().strip().lower() if self.combo.get() not in self.sources else self.combo.get()
        if not key:
            return
        sel = {k: v.get().strip() for k, v in self.vars.items() if v.get().strip()}
        self.sources[key] = {"name": self.name_var.get().strip() or key, "selectors": sel}
        self.combo["values"] = list(self.sources.keys())
        self.on_save()
        messagebox.showinfo("已儲存", f"{key} 設定已儲存。", parent=self)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self._set_icon()
        self.geometry("1120x800")
        DATA_DIR.mkdir(exist_ok=True)
        self.records = [Record.from_dict(d) for d in load_json(RECORDS_FILE, [])]
        self.sources = load_json(SOURCES_FILE, DEFAULT_SOURCES)
        added = [k for k in KNOWN_SOURCES if k not in self.sources]
        for k in added:
            self.sources[k] = KNOWN_SOURCES[k]
        if added:
            save_json(SOURCES_FILE, self.sources)
        self.cfg = load_json(CONFIG_FILE, {"hotkeys": DEFAULT_HOTKEYS})
        self.current = None
        self.status = tk.StringVar(value="就緒")
        self.hk = HotkeyManager(self._on_capture)
        self._build()
        self._refresh_list()
        self.new_record()
        self._start_hotkeys()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _set_icon(self):
        try:
            if os.name == "nt":
                self.iconbitmap(default=str(ICON_FILE))
            else:                                   # macOS／Linux 不支援 .ico，改用內嵌的 PNG
                from PIL import Image, ImageTk
                img = Image.open(ICON_FILE)
                img = img.convert("RGBA").resize((128, 128))
                self._icon_img = ImageTk.PhotoImage(img)
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass                                    # 圖示失敗不影響程式運作

    # ---------- UI ----------
    def _build(self):
        left = ttk.Frame(self, width=270)
        left.pack(side="left", fill="y", padx=8, pady=8)
        left.pack_propagate(False)
        self.listbox = tk.Listbox(left, selectmode="extended", exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        for text, cmd in [
            ("新增作品", self.new_record), ("刪除所選", self.delete_selected),
            ("匯入 .txt", self.import_txt),
            ("匯出全部 .txt", lambda: self.export(False)),
            ("匯出所選 .txt", lambda: self.export(True)),
            ("來源設定", self.open_sources),
        ]:
            ttk.Button(left, text=text, command=cmd).pack(fill="x", pady=2)
        self.hk_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text="啟用全域快捷鍵", variable=self.hk_on,
                        command=self._toggle_hotkeys).pack(anchor="w", pady=4)

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        right.columnconfigure(1, weight=1)
        self.vars = {}
        r = 0
        for k in ["zh_title", "en_title", "zh_title2", "en_title2"]:
            r = self._entry_row(right, r, FIELD_LABELS[k], k)
        ttk.Label(right, text="連結").grid(row=r, column=0, sticky="w", pady=3)
        self.vars["url"] = tk.StringVar()
        ttk.Entry(right, textvariable=self.vars["url"]).grid(row=r, column=1, sticky="ew")
        btns = ttk.Frame(right)
        btns.grid(row=r, column=2, padx=4)
        ttk.Button(btns, text="從連結抓取", command=self.fetch).pack(side="left")
        ttk.Button(btns, text="清理連結", command=self.clean_url_now).pack(side="left", padx=2)
        r += 1
        r = self._entry_row(right, r, "出品公司", "company")

        ttk.Label(right, text="集數").grid(row=r, column=0, sticky="w", pady=3)
        ep = ttk.Frame(right)
        ep.grid(row=r, column=1, sticky="w")
        self.ep_var = tk.StringVar()
        ttk.Entry(ep, textvariable=self.ep_var, width=8).pack(side="left")
        ttk.Label(ep, text="集（最後集數）").pack(side="left", padx=4)
        self.ongoing_var = tk.BooleanVar()
        ttk.Checkbutton(ep, text="未完結", variable=self.ongoing_var).pack(side="left", padx=10)
        r += 1
        r = self._entry_row(right, r, "tag", "tags")

        ttk.Label(right, text="notes").grid(row=r, column=0, sticky="nw", pady=3)
        self.notes = tk.Text(right, height=3, wrap="word")
        self.notes.grid(row=r, column=1, columnspan=2, sticky="ew")
        r += 1
        self.vt = {}
        for k in ("synopsis", "plot"):
            ttk.Label(right, text=FIELD_LABELS[k]).grid(row=r, column=0, sticky="nw", pady=3)
            self.vt[k] = VersionedText(right)
            self.vt[k].grid(row=r, column=1, columnspan=2, sticky="nsew")
            right.rowconfigure(r, weight=1)
            r += 1
        ttk.Button(right, text="儲存此作品", command=self.save_record).grid(row=r, column=1, sticky="e", pady=6)
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w").pack(side="bottom", fill="x")

    def _entry_row(self, parent, r, label, key):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=3)
        self.vars[key] = tk.StringVar()
        ttk.Entry(parent, textvariable=self.vars[key]).grid(row=r, column=1, columnspan=2, sticky="ew")
        return r + 1

    # ---------- 表單 <-> Record ----------
    def _collect(self) -> Record:
        r = Record()
        for k in ENTRY_FIELDS:
            setattr(r, k, self.vars[k].get().strip())
        r.episodes = re.sub(r"\D", "", self.ep_var.get())
        r.ongoing = self.ongoing_var.get()
        r.notes = self.notes.get("1.0", "end").strip()
        r.synopsis = self.vt["synopsis"].get()
        r.plot = self.vt["plot"].get()
        return r

    def _fill(self, r: Record):
        for k in ENTRY_FIELDS:
            self.vars[k].set(getattr(r, k))
        self.ep_var.set(r.episodes)
        self.ongoing_var.set(r.ongoing)
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", r.notes)
        self.vt["synopsis"].set(r.synopsis)
        self.vt["plot"].set(r.plot)

    # ---------- 作品清單 ----------
    def _refresh_list(self):
        self.listbox.delete(0, "end")
        for r in self.records:
            self.listbox.insert("end", r.display_name())

    def _on_select(self, _e):
        sel = self.listbox.curselection()
        if len(sel) == 1:
            self.current = sel[0]
            self._fill(self.records[self.current])

    def new_record(self):
        self.current = None
        self.listbox.selection_clear(0, "end")
        self._fill(Record())

    def save_record(self):
        r = self._collect()
        if not (r.zh_title or r.en_title):
            messagebox.showwarning("缺少片名", "請至少填寫中文或英文片名。")
            return
        raw_url = r.url
        r.url, site = urlclean.clean_url(raw_url)
        action = "新增" if self.current is None else "更新"
        lines = []
        if raw_url and r.url != raw_url:
            lines.append(f"連結已清理：\n  原：{raw_url}\n  新：{r.url}")
        elif raw_url and site is None:
            lines.append("⚠ 無法辨識此連結的來源格式，將保持原樣。")
        dup = self._find_dup(r, exclude=self.current)
        if dup is not None:
            lines.append(f"⚠ 資料庫已有相同連結或片名的作品：{self.records[dup].display_name()}。")
        lines.append(f"確定要{action}入庫嗎？")
        if not ask_preview(self, f"確認{action}入庫", "\n\n".join(lines), format_record(r), f"{action}入庫"):
            self.status.set("已取消入庫")
            return
        self.vars["url"].set(r.url)
        if self.current is None:
            self.records.append(r)
            self.current = len(self.records) - 1
        else:
            self.records[self.current] = r
        self._persist()
        self._refresh_list()
        self.listbox.selection_set(self.current)
        self.status.set(f"已{action}入庫：{r.display_name()}")

    def clean_url_now(self):
        raw = self.vars["url"].get().strip()
        new, site = urlclean.clean_url(raw)
        self.vars["url"].set(new)
        self.status.set("連結已清理" if site else "無法辨識此連結的來源格式，保持原樣")

    def _find_dup(self, r, exclude=None):
        u = urlclean.clean_url(r.url)[0] if r.url else ""
        for i, x in enumerate(self.records):
            if i == exclude:
                continue
            xu = urlclean.clean_url(x.url)[0] if x.url else ""
            if u and xu and u == xu:
                return i
            if r.zh_title and r.zh_title == x.zh_title:
                return i
            if r.en_title and r.en_title.lower() == x.en_title.lower():
                return i
        return None

    def import_txt(self):
        path = filedialog.askopenfilename(filetypes=[("文字檔", "*.txt"), ("所有檔案", "*.*")])
        if not path:
            return
        try:
            recs = txtimport.parse_txt(txtimport.read_text_file(path))
        except Exception as e:
            messagebox.showerror("讀取失敗", str(e))
            return
        if not recs:
            messagebox.showinfo("沒有資料", "檔案中沒有解析到任何作品。")
            return
        ImportDialog(self, recs, path)

    def apply_import(self, picked):
        for r, dup in picked:
            if dup is None:
                self.records.append(r)
            else:
                self.records[dup] = r
        self._persist()
        self._refresh_list()
        self.new_record()
        self.status.set(f"已匯入 {len(picked)} 筆")

    def delete_selected(self):
        sel = list(self.listbox.curselection())
        if sel and messagebox.askyesno("刪除", f"確定刪除 {len(sel)} 筆？"):
            for i in reversed(sel):
                del self.records[i]
            self._persist()
            self._refresh_list()
            self.new_record()

    def _persist(self):
        save_json(RECORDS_FILE, [r.to_dict() for r in self.records])

    def export(self, only_selected):
        if only_selected:
            recs = [self.records[i] for i in self.listbox.curselection()]
        else:
            recs = self.records
        if not recs:
            messagebox.showinfo("沒有資料", "沒有可匯出的作品。")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("文字檔", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(format_records(recs))
            self.status.set(f"已匯出 {len(recs)} 筆 → {path}")

    # ---------- 欄位寫入（快捷鍵／抓取共用） ----------
    def _is_empty(self, field):
        if field in self.vars:
            return not self.vars[field].get().strip()
        if field == "episodes":
            return not self.ep_var.get().strip()
        if field == "notes":
            return not self.notes.get("1.0", "end").strip()
        return self.vt[field].is_empty()

    def apply_field(self, field, text):
        if field in self.vars:
            v = self.vars[field]
            if field == "tags" and v.get().strip():
                v.set(f"{v.get().strip()}, {text}")
            else:
                v.set(text)
        elif field == "episodes":
            m = re.search(r"\d+", text)
            if m:
                self.ep_var.set(m.group())
            if ONGOING_RE.search(text):
                self.ongoing_var.set(True)
        elif field == "notes":
            cur = self.notes.get("1.0", "end").strip()
            self.notes.delete("1.0", "end")
            self.notes.insert("1.0", f"{cur}\n{text}" if cur else text)
        else:
            self.vt[field].capture(text)

    # ---------- 快捷鍵 ----------
    def _on_capture(self, field, text):
        self.after(0, lambda: self._captured(field, text))

    def _captured(self, field, text):
        self.apply_field(field, text)
        self.status.set(f"已擷取到「{FIELD_LABELS.get(field, field)}」：{text[:40]}")

    def _start_hotkeys(self):
        try:
            self.hk.start(self.cfg.get("hotkeys", DEFAULT_HOTKEYS))
            self.status.set("全域快捷鍵已啟用（Ctrl+Alt+1~0，可在 config.json 修改）")
        except Exception as e:
            self.hk_on.set(False)
            self.status.set(f"快捷鍵無法啟用：{e}")

    def _toggle_hotkeys(self):
        if self.hk_on.get():
            self._start_hotkeys()
        else:
            self.hk.stop()
            self.status.set("全域快捷鍵已停用")

    # ---------- 抓取 ----------
    def fetch(self):
        url = self.vars["url"].get().strip()
        if not url:
            messagebox.showinfo("缺少連結", "請先輸入連結。")
            return
        self.status.set("抓取中…")

        def work():
            try:
                res = scraper.scrape(url, self.sources)
            except Exception as e:
                self.after(0, lambda: self.status.set(f"抓取失敗：{e}"))
                return
            self.after(0, lambda: self._fetch_done(res))

        threading.Thread(target=work, daemon=True).start()

    def _fetch_done(self, res):
        got = []
        for f, v in res["fields"].items():
            if f in ("synopsis", "plot"):
                self.vt[f].capture(v)            # 已有內容 → 自動新增 v2、v3…
                got.append(FIELD_LABELS[f])
            elif self._is_empty(f):              # 其他欄位只補空白，不覆蓋已填資料
                self.apply_field(f, v)
                got.append(FIELD_LABELS[f])
        if res["ongoing"]:
            self.ongoing_var.set(True)
        self.status.set(f"抓取完成（來源設定：{res['source']}）；已填入：{'、'.join(got) or '無'}")

    # ---------- 來源設定 ----------
    def open_sources(self):
        SourceDialog(self, self.sources, lambda: save_json(SOURCES_FILE, self.sources))

    def _close(self):
        self.hk.stop()
        self.destroy()


def main():
    App().mainloop()
