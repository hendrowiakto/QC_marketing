"""webview_app.py - pywebview GUI layer untuk QC Marketing Bot.

Embed `QC Marketing Bot.html` via pywebview. Layer ini cuma:
  1. Launch native window yg load HTML
  2. Expose `BotAPI` ke JS (toggle bot, force scan, edit prompt, settings, dll)
  3. Push state (logs, status, stats, last_card, settings) ke JS setiap 500ms
"""

import os
import sys
import re
import json
import time
import threading
import webbrowser
import subprocess

import webview

from shared import (
    SCRIPT_DIR, LOG_DIR, PROMPT_FILE, REVIEWS_DIR, TRELLO_FILE,
    open_in_default_editor,
)


# ===================== ICON & WINDOW HELPERS =====================
def _resolve_path(fname):
    """Find file in dev OR frozen (PyInstaller _MEIPASS)."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, fname))
    candidates.append(os.path.join(SCRIPT_DIR, fname))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, fname))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0]


HTML_PATH = _resolve_path("QC Marketing Bot.html")
ICON_PATH = _resolve_path("icon.ico") if os.path.isfile(_resolve_path("icon.ico")) else None


# AppUserModelID: kasih taskbar group identity sendiri biar Windows pakai icon kita.
if sys.platform == "win32":
    try:
        import ctypes as _ct
        _ct.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GameMarket.QCMarketingBot")
    except Exception:
        pass


def _apply_window_icon(title, ico_path):
    """Set window + taskbar icon lewat Win32. macOS/Linux: no-op."""
    if sys.platform != "win32" or not ico_path or not os.path.isfile(ico_path):
        return
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return
    user32 = ctypes.windll.user32
    WM_SETICON = 0x0080
    ICON_SMALL = 0
    ICON_BIG = 1
    GCLP_HICON = -14
    GCLP_HICONSM = -34

    def _load_icon_scaled(w, h):
        try:
            comctl = ctypes.windll.comctl32
            comctl.LoadIconWithScaleDown.argtypes = [
                wintypes.HINSTANCE, wintypes.LPCWSTR,
                ctypes.c_int, ctypes.c_int, ctypes.POINTER(wintypes.HICON)
            ]
            comctl.LoadIconWithScaleDown.restype = ctypes.c_long
            h_out = wintypes.HICON()
            hr = comctl.LoadIconWithScaleDown(None, ico_path, w, h, ctypes.byref(h_out))
            if hr == 0 and h_out.value:
                return h_out.value
        except Exception:
            pass
        return 0

    hicon_small = _load_icon_scaled(16, 16)
    hicon_big = _load_icon_scaled(32, 32)
    if not hicon_small and not hicon_big:
        return
    try:
        user32.SetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.SetClassLongPtrW.restype = ctypes.c_void_p
    except Exception:
        pass
    for _ in range(20):
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            try:
                if hicon_small:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
                    try: user32.SetClassLongPtrW(hwnd, GCLP_HICONSM, hicon_small)
                    except Exception: pass
                if hicon_big:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
                    try: user32.SetClassLongPtrW(hwnd, GCLP_HICON, hicon_big)
                    except Exception: pass
            except Exception:
                pass
            return
        time.sleep(0.2)


# ===================== LOG PARSE =====================
_LOG_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s*\[([A-Z]+)\]\s*(.*)$")
_KNOWN_LEVELS = {"APP", "TRELLO", "CLAUDE", "REVIEW", "ERR", "OK", "WARN", "SYS"}


def parse_log_line(raw):
    m = _LOG_RE.match(raw)
    if not m:
        return {"time": time.strftime("%H:%M:%S"), "level": "SYS", "msg": raw}
    ts, level, msg = m.group(1), m.group(2), m.group(3)
    if level not in _KNOWN_LEVELS:
        level = "SYS"
    return {"time": ts, "level": level, "msg": msg}


def _js_escape(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


# ===================== BOT API (exposed to JS) =====================
class BotAPI:
    """Methods exposed to JS via `window.pywebview.api.<name>()`."""

    def __init__(self, ctx, window_ref):
        self.ctx = ctx
        self._window_ref = window_ref

    def _log(self, msg):
        self.ctx.logger.app(msg)

    # ---------- Bot toggle ----------
    def toggle_bot(self, enabled):
        self.ctx.bot_enabled = bool(enabled)
        self._log(f"Bot {'dinyalakan' if enabled else 'dimatikan'}")
        return {"ok": True, "enabled": self.ctx.bot_enabled}

    def force_scan(self):
        self.ctx.force_scan = True
        self._log("Force Scan: skip wait, scan sekarang")
        return {"ok": True}

    # ---------- Connection test ----------
    def test_connection(self):
        result = {
            "trello": False, "trello_msg": "",
            "claude": False, "claude_msg": "",
            "gemini": False, "gemini_msg": "",
            "active_provider": "",
        }
        try:
            me = self.ctx.trello.whoami()
            result["trello"] = True
            result["trello_msg"] = f"connected as {me.get('username', '?')}"
            self._log(f"[Test] Trello OK: {me.get('username', '?')}")
        except Exception as e:
            result["trello_msg"] = str(e)[:160]
            self.ctx.logger.err(f"[Test] Trello FAIL: {result['trello_msg']}")

        # Test Claude (kalau client init)
        if self.ctx.claude is None:
            result["claude_msg"] = "tidak terinit (cek API Claude.txt)"
        else:
            try:
                self.ctx.claude.client.messages.create(
                    model="claude-haiku-4-5-20251001",  # paling murah utk ping
                    max_tokens=10,
                    messages=[{"role": "user", "content": "ping"}],
                )
                result["claude"] = True
                result["claude_msg"] = "API OK"
                self._log("[Test] Claude OK")
            except Exception as e:
                result["claude_msg"] = str(e)[:160]
                self.ctx.logger.err(f"[Test] Claude FAIL: {result['claude_msg']}")

        # Test Gemini
        if self.ctx.gemini is None:
            result["gemini_msg"] = "tidak terinit (cek API Gemini.txt)"
        else:
            try:
                resp = self.ctx.gemini.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents="ping",
                )
                result["gemini"] = True
                result["gemini_msg"] = "API OK"
                self._log("[Test] Gemini OK")
            except Exception as e:
                result["gemini_msg"] = str(e)[:160]
                self.ctx.logger.err(f"[Test] Gemini FAIL: {result['gemini_msg']}")

        # Active provider berdasar config saat ini
        active = self.ctx.config.get("CLAUDE_MODEL", "")
        if active.startswith("gemini-"):
            result["active_provider"] = f"Gemini ({active})"
        elif active.startswith("claude-"):
            result["active_provider"] = f"Claude ({active})"
        return result

    # ---------- Open external ----------
    def open_log_folder(self):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(LOG_DIR)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", LOG_DIR])
            else:
                subprocess.Popen(["xdg-open", LOG_DIR])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_reviews_folder(self):
        try:
            os.makedirs(REVIEWS_DIR, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(REVIEWS_DIR)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", REVIEWS_DIR])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_trello_board(self):
        url = f"https://trello.com/b/{self.ctx.creds.get('BOARD_ID', '')}"
        # Pakai shortLink lebih stabil — coba dari config
        try:
            webbrowser.open("https://trello.com/b/OMl5PJIA/gamemarket-team-marketing")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_card_url(self, url):
        if not url:
            return {"ok": False}
        try:
            webbrowser.open(url)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def edit_prompt(self):
        ok = open_in_default_editor(PROMPT_FILE)
        if ok:
            self._log("Prompt.txt dibuka di editor default. Save lalu tunggu scan berikutnya.")
        return {"ok": ok}

    def edit_trello_credentials(self):
        ok = open_in_default_editor(TRELLO_FILE)
        if ok:
            self._log("Trello.txt dibuka di editor. Restart bot setelah save.")
        return {"ok": ok}

    # ---------- Settings ----------
    def get_settings(self):
        c = self.ctx.config
        return {
            "threshold": c.get_int("THRESHOLD", 75),
            "model": c.get("CLAUDE_MODEL", "claude-opus-4-7"),
            "web_search": c.get_bool("WEB_SEARCH_ENABLED", True),
            "polling_base": c.get_int("POLLING_BASE_SEC", 30),
            "polling_max": c.get_int("POLLING_MAX_SEC", 50),
            "polling_step": c.get_int("POLLING_STEP_SEC", 10),
        }

    def set_threshold(self, value):
        try:
            v = max(0, min(100, int(value)))
        except Exception:
            return {"ok": False, "error": "invalid int"}
        self.ctx.config.set("THRESHOLD", v)
        self._log(f"Threshold diubah ke {v}")
        return {"ok": True, "value": v}

    def set_model(self, model):
        allowed = (
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
        )
        if model not in allowed:
            return {"ok": False, "error": f"unknown model: {model}"}
        self.ctx.config.set("CLAUDE_MODEL", model)
        self._log(f"AI model diubah ke {model}")
        return {"ok": True, "value": model}

    def set_web_search(self, enabled):
        self.ctx.config.set("WEB_SEARCH_ENABLED", "true" if enabled else "false")
        self._log(f"Web search {'ON' if enabled else 'OFF'}")
        return {"ok": True, "value": bool(enabled)}

    # ---------- Window controls ----------
    def set_always_on_top(self, enabled):
        w = self._window()
        if not w:
            return {"ok": True, "value": bool(enabled)}
        val = bool(enabled)
        if sys.platform == "win32":
            try:
                from webview.platforms.winforms import BrowserView
                from System import Action
                form = BrowserView.instances.get(w.uid)
                if form is None:
                    return {"ok": False, "error": "form not found"}
                def _apply(): form.TopMost = val
                form.Invoke(Action(_apply))
                try: w._Window__on_top = val
                except Exception: pass
                return {"ok": True, "value": val}
            except Exception as e:
                return {"ok": False, "error": str(e)[:160]}
        else:
            try:
                w.on_top = val
                return {"ok": True, "value": val}
            except Exception as e:
                return {"ok": False, "error": str(e)[:160]}

    def window_minimize(self):
        w = self._window()
        if w:
            try: w.minimize()
            except Exception: pass
        return {"ok": True}

    def window_close(self):
        w = self._window()
        if w:
            try: w.destroy()
            except Exception: pass
        try: self.ctx.stop_event.set()
        except Exception: pass
        return {"ok": True}

    def _window(self):
        if self._window_ref and self._window_ref[0]:
            return self._window_ref[0]
        return None

    # ---------- Stats ----------
    def get_stats(self):
        return self.ctx.stats.snapshot()

    def get_app_info(self):
        return {"version": "1.0", "releaseDate": "2026-05-10"}


# ===================== STATE BRIDGE =====================
class StateBridge:
    """Polls BotContext setiap tick_ms dan push snapshot ke JS via evaluate_js."""

    def __init__(self, ctx, window_ref, tick_ms=500):
        self.ctx = ctx
        self._window_ref = window_ref
        self.tick = max(0.1, tick_ms / 1000.0)
        self._last_total = 0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        time.sleep(0.8)  # wait HTML load
        while not self._stop.is_set():
            try:
                self._push_tick()
            except Exception:
                pass
            self._stop.wait(self.tick)

    def _push_tick(self):
        window = self._window_ref[0] if self._window_ref else None
        if window is None:
            return

        # ---------- 1) Push log diff ----------
        try:
            messages, total = self.ctx.logger.snapshot()
        except Exception:
            messages, total = [], self._last_total

        if total != self._last_total:
            delta = total - self._last_total
            if delta > 0 and messages:
                new_lines = messages[-delta:] if delta <= len(messages) else messages
                entries = [parse_log_line(ln) for ln in new_lines]
                if entries:
                    self._safe_eval(f"window.pushLogBatch({_js_escape(entries)})")
            self._last_total = total

        # ---------- 2) Push state snapshot ----------
        snap = self._build_state()
        self._safe_eval(f"window.setAppState({_js_escape(snap)})")

    def _build_state(self):
        c = self.ctx
        stats = c.stats.snapshot()
        return {
            "connected": c.bot_enabled,  # bot ON = connected (semantic dari Bot_Poster_v2)
            "bot": {
                "enabled": c.bot_enabled,
                "status": c.current_status,
                "next_scan_in": int(c.next_scan_in),
                "current_wait": int(c.current_wait),
            },
            "last_card": c.last_card,
            "stats": stats,
            "settings": {
                "threshold": c.config.get_int("THRESHOLD", 75),
                "model": c.config.get("CLAUDE_MODEL", "claude-opus-4-7"),
                "web_search": c.config.get_bool("WEB_SEARCH_ENABLED", True),
            },
        }

    def _safe_eval(self, code):
        w = self._window_ref[0] if self._window_ref else None
        if not w:
            return
        try:
            w.evaluate_js(code)
        except Exception:
            pass


# ===================== WEBVIEW APP =====================
class WebviewApp:
    def __init__(self, ctx):
        self.ctx = ctx
        self._window_ref = [None]
        self.api = BotAPI(ctx, self._window_ref)
        self.bridge = StateBridge(ctx, self._window_ref, tick_ms=500)

    def run(self):
        title = "QC Marketing Bot"
        if not os.path.isfile(HTML_PATH):
            print(f"FATAL: HTML tidak ditemukan: {HTML_PATH}")
            return

        window = webview.create_window(
            title=title,
            url=HTML_PATH,
            js_api=self.api,
            width=1100,
            height=720,
            min_size=(900, 600),
            background_color="#080a10",
            on_top=True,
            text_select=True,
        )
        self._window_ref[0] = window

        def _on_shown():
            _apply_window_icon(title, ICON_PATH)
            t = threading.Thread(target=self.bridge.run, name="state-bridge", daemon=True)
            t.start()

        def _on_closed():
            self.bridge.stop()
            try: self.ctx.stop_event.set()
            except Exception: pass

        window.events.shown += _on_shown
        window.events.closed += _on_closed

        webview.start(debug=False)
