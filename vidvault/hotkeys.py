"""全域快捷鍵：在瀏覽器選取文字 -> 按快捷鍵 -> 自動複製並填入對應欄位。"""
import platform
import threading
import time

import pyperclip
from pynput import keyboard

IS_MAC = platform.system() == "Darwin"


class HotkeyManager:
    def __init__(self, on_capture):
        self.on_capture = on_capture          # on_capture(field, text)
        self._listener = None
        self._ctl = keyboard.Controller()
        self._busy = threading.Lock()

    def start(self, mapping: dict):
        self.stop()
        bindings = {
            combo: (lambda f=field: self._trigger(f)) for combo, field in mapping.items()
        }
        self._listener = keyboard.GlobalHotKeys(bindings)
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None

    def _trigger(self, field):
        threading.Thread(target=self._capture, args=(field,), daemon=True).start()

    def _capture(self, field):
        if not self._busy.acquire(blocking=False):
            return
        try:
            try:
                old = pyperclip.paste()
            except Exception:
                old = ""
            pyperclip.copy("")
            time.sleep(0.35)                   # 等使用者放開快捷鍵
            for k in (keyboard.Key.alt, keyboard.Key.shift):
                try:
                    self._ctl.release(k)
                except Exception:
                    pass
            mod = keyboard.Key.cmd if IS_MAC else keyboard.Key.ctrl
            with self._ctl.pressed(mod):
                self._ctl.press("c")
                self._ctl.release("c")
            time.sleep(0.2)
            text = (pyperclip.paste() or "").strip()
            try:
                pyperclip.copy(old)            # 還原原本的剪貼簿
            except Exception:
                pass
            if text:
                self.on_capture(field, text)
        finally:
            self._busy.release()
