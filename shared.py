"""shared.py - foundation untuk QC Marketing Bot.

Berisi:
- Paths & constants
- Config loader (config.txt, Trello.txt, API Claude.txt, prompt.txt)
- Logger (file rotation + in-memory snapshot untuk UI bridge)
- TrelloClient (REST wrapper: list cards, comment, move, update title, attachment download)
- ClaudeClient (Anthropic SDK: vision multimodal + web_search tool)
- Stats (today / all-time counter)
- BotContext (holder)
"""

import os
import sys
import re
import json
import time
import base64
import shutil
import tempfile
import subprocess
import threading
from io import BytesIO
from datetime import datetime, timedelta

import requests
from PIL import Image

try:
    import anthropic
except ImportError:
    anthropic = None  # akan di-raise saat dipakai

try:
    from google import genai as _google_genai
    from google.genai import types as _google_genai_types
except ImportError:
    _google_genai = None
    _google_genai_types = None


# ===================== PATHS =====================
if getattr(sys, "frozen", False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE     = os.path.join(SCRIPT_DIR, "config.txt")
TRELLO_FILE     = os.path.join(SCRIPT_DIR, "Trello.txt")
CLAUDE_KEY_FILE = os.path.join(SCRIPT_DIR, "API Claude.txt")
GEMINI_KEY_FILE = os.path.join(SCRIPT_DIR, "API Gemini.txt")
PROMPT_FILE     = os.path.join(SCRIPT_DIR, "prompt.txt")
STATE_FILE      = os.path.join(SCRIPT_DIR, "state.json")
LOG_DIR         = os.path.join(SCRIPT_DIR, "log")
REVIEWS_DIR     = os.path.join(SCRIPT_DIR, "reviews")
TOOLS_DIR       = os.path.join(SCRIPT_DIR, "tools")
FFMPEG_LOCAL    = os.path.join(TOOLS_DIR, "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE_LOCAL   = os.path.join(TOOLS_DIR, "ffmpeg", "bin", "ffprobe.exe")

LOG_MAX_LINES   = 500


# ===================== CONFIG =====================
def _read_kv_file(path):
    """Read simple KEY=VALUE config file. Returns dict (string values, trimmed).
    Lines starting with # are comments. Empty/malformed lines are skipped."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _write_kv_file(path, data, header_lines=None):
    """Write KEY=VALUE file. Preserve header comment lines if path already has them."""
    existing_header = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("#") or not line.strip():
                    existing_header.append(line.rstrip("\n"))
                else:
                    break
    lines = []
    if header_lines is not None:
        lines.extend(header_lines)
    elif existing_header:
        lines.extend(existing_header)
    for k, v in data.items():
        lines.append(f"{k}={v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


class Config:
    """Mutable config wrapper with type-coerced getters."""

    def __init__(self, path=CONFIG_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._data = _read_kv_file(path)

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def get_int(self, key, default=0):
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key, default=False):
        v = self.get(key)
        if v is None:
            return default
        return v.strip().lower() in ("1", "true", "yes", "on")

    def set(self, key, value):
        with self._lock:
            self._data[key] = str(value)
            _write_kv_file(self.path, self._data)

    def all(self):
        with self._lock:
            return dict(self._data)


def load_trello_credentials():
    """Returns dict with API_KEY, API_TOKEN, BOARD_ID, LIST_REVIEW_QC, LIST_EDITING, LIST_READY_PUBLISH."""
    data = _read_kv_file(TRELLO_FILE)
    required = ("API_KEY", "API_TOKEN", "BOARD_ID",
                "LIST_REVIEW_QC", "LIST_EDITING", "LIST_READY_PUBLISH")
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise RuntimeError(f"Trello.txt missing keys: {', '.join(missing)}")
    return data


def load_claude_api_key():
    if not os.path.isfile(CLAUDE_KEY_FILE):
        raise RuntimeError(f"File tidak ditemukan: {CLAUDE_KEY_FILE}")
    with open(CLAUDE_KEY_FILE, "r", encoding="utf-8") as f:
        key = f.read().strip()
    if not key.startswith("sk-ant-"):
        raise RuntimeError("API Claude.txt tidak berisi Anthropic API key (harus mulai 'sk-ant-')")
    return key


def load_gemini_api_key():
    if not os.path.isfile(GEMINI_KEY_FILE):
        raise RuntimeError(f"File tidak ditemukan: {GEMINI_KEY_FILE}")
    with open(GEMINI_KEY_FILE, "r", encoding="utf-8") as f:
        key = f.read().strip()
    if not key.startswith("AIza"):
        raise RuntimeError("API Gemini.txt tidak berisi Google API key (harus mulai 'AIza')")
    return key


def is_claude_model(model_id):
    return (model_id or "").startswith("claude-")


def is_gemini_model(model_id):
    return (model_id or "").startswith("gemini-")


def load_prompt():
    if not os.path.isfile(PROMPT_FILE):
        raise RuntimeError(f"File tidak ditemukan: {PROMPT_FILE}")
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read()


# ===================== LOGGER =====================
class Logger:
    """File-rotated daily logger + in-memory ring buffer untuk UI bridge.

    Format line: [HH:MM:SS] [LEVEL] msg
    Level kategori: APP, TRELLO, CLAUDE, REVIEW, ERR, OK, WARN, SYS
    """

    def __init__(self, log_dir=LOG_DIR, max_lines=LOG_MAX_LINES):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._buffer = []
        self._total = 0  # monotonic counter (UI uses delta)
        self.max_lines = max_lines
        self._current_date = None
        self._fp = None

    def _ensure_file(self):
        today = time.strftime("%Y-%m-%d")
        if today != self._current_date:
            if self._fp:
                try: self._fp.close()
                except Exception: pass
            self._fp = open(os.path.join(self.log_dir, f"{today}.log"),
                            "a", encoding="utf-8")
            self._current_date = today

    def log(self, level, msg):
        ts = time.strftime("%H:%M:%S")
        level_up = (level or "SYS").upper()
        line = f"[{ts}] [{level_up}] {msg}"
        with self._lock:
            self._buffer.append(line)
            self._total += 1
            if len(self._buffer) > self.max_lines:
                self._buffer = self._buffer[-self.max_lines:]
            try:
                self._ensure_file()
                self._fp.write(line + "\n")
                self._fp.flush()
            except Exception:
                pass

    def app(self, msg):    self.log("APP", msg)
    def trello(self, msg): self.log("TRELLO", msg)
    def claude(self, msg): self.log("CLAUDE", msg)
    def review(self, msg): self.log("REVIEW", msg)
    def err(self, msg):    self.log("ERR", msg)
    def ok(self, msg):     self.log("OK", msg)
    def warn(self, msg):   self.log("WARN", msg)

    def snapshot(self):
        """Return (lines, total_counter). UI computes delta dari total."""
        with self._lock:
            return list(self._buffer), self._total

    def cleanup_old(self, retention_days):
        """Hapus log file yang lebih lama dari retention_days."""
        if retention_days <= 0:
            return
        cutoff = datetime.now() - timedelta(days=retention_days)
        try:
            for name in os.listdir(self.log_dir):
                if not name.endswith(".log"):
                    continue
                try:
                    d = datetime.strptime(name[:-4], "%Y-%m-%d")
                except ValueError:
                    continue
                if d < cutoff:
                    try: os.remove(os.path.join(self.log_dir, name))
                    except Exception: pass
        except Exception:
            pass


# ===================== TRELLO CLIENT =====================
class TrelloClient:
    BASE = "https://api.trello.com/1"

    def __init__(self, creds, logger):
        self.key = creds["API_KEY"]
        self.token = creds["API_TOKEN"]
        self.board_id = creds["BOARD_ID"]
        self.list_review_qc = creds["LIST_REVIEW_QC"]
        self.list_editing = creds["LIST_EDITING"]
        self.list_ready_publish = creds["LIST_READY_PUBLISH"]
        self.logger = logger
        self.session = requests.Session()
        # Untuk download attachment: butuh Authorization OAuth header
        self._auth_header = (
            f'OAuth oauth_consumer_key="{self.key}", oauth_token="{self.token}"'
        )

    def _auth_params(self, extra=None):
        p = {"key": self.key, "token": self.token}
        if extra: p.update(extra)
        return p

    def _request(self, method, path, params=None, data=None, timeout=30):
        url = self.BASE + path
        params = self._auth_params(params)
        r = self.session.request(method, url, params=params, data=data, timeout=timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Trello {method} {path} -> {r.status_code}: {r.text[:200]}")
        if r.text:
            try: return r.json()
            except ValueError: return r.text
        return None

    # ---------- Auth check ----------
    def whoami(self):
        return self._request("GET", "/members/me", params={"fields": "username,fullName"})

    # ---------- Cards in Review & QC list ----------
    def get_review_qc_cards(self):
        """Return list of cards in Review & QC, sorted by pos (top first).
        Each card includes: id, name, desc, pos, idAttachmentCover, customFieldItems, attachments."""
        return self._request(
            "GET",
            f"/lists/{self.list_review_qc}/cards",
            params={
                "attachments": "true",
                "attachment_fields": "id,name,url,mimeType,bytes,date,isUpload",
                "customFieldItems": "true",
                "fields": "id,name,desc,pos,shortUrl,idList",
                "filter": "open",
            },
        )

    # ---------- Custom field options (for "Tujuan Konten") ----------
    def get_custom_fields(self):
        return self._request("GET", f"/boards/{self.board_id}/customFields")

    # ---------- Card mutations ----------
    def update_card_name(self, card_id, new_name):
        # PUT pakai form-body untuk safety (title biasa pendek tapi defensive).
        return self._request("PUT", f"/cards/{card_id}", data={"name": new_name})

    def move_card(self, card_id, target_list_id, position="top"):
        return self._request(
            "PUT", f"/cards/{card_id}",
            params={"idList": target_list_id, "pos": position},
        )

    def add_comment(self, card_id, text):
        # POST /cards/{id}/actions/comments — kirim text sebagai form-body
        # (BUKAN query param), karena comment bisa >10KB dan kena HTTP 414 URI Too Long.
        return self._request(
            "POST", f"/cards/{card_id}/actions/comments",
            data={"text": text},
        )

    # ---------- Checklists ----------
    def get_checklists(self, card_id):
        """List checklists pada card. Return list of {id, name, ...}."""
        return self._request(
            "GET", f"/cards/{card_id}/checklists",
            params={"fields": "id,name"},
        ) or []

    def delete_checklist(self, checklist_id):
        return self._request("DELETE", f"/checklists/{checklist_id}")

    def create_checklist(self, card_id, name):
        """Create checklist di card. Return dict {id, name, ...}."""
        return self._request(
            "POST", "/checklists",
            data={"idCard": card_id, "name": name},
        )

    def add_checkitem(self, checklist_id, name, checked=False, pos="bottom"):
        return self._request(
            "POST", f"/checklists/{checklist_id}/checkItems",
            data={
                "name": name,
                "checked": "true" if checked else "false",
                "pos": pos,
            },
        )

    def replace_checklist_with_items(self, card_id, name, items):
        """Idempotent replace: hapus checklist existing dengan nama sama, bikin baru
        + isi items. No-op kalau items kosong (tidak bikin checklist kosong)."""
        if not items:
            return None
        # Find & delete existing checklists dengan nama persis sama
        try:
            existing = self.get_checklists(card_id)
            for cl in existing:
                if (cl.get("name") or "").strip() == name.strip():
                    try: self.delete_checklist(cl["id"])
                    except Exception: pass
        except Exception:
            pass
        # Create fresh
        new_cl = self.create_checklist(card_id, name)
        cl_id = new_cl["id"]
        for item in items:
            try: self.add_checkitem(cl_id, item)
            except Exception: pass
        return cl_id

    # ---------- Attachment download ----------
    def download_attachment_bytes(self, attachment_url, timeout=60):
        """Download attachment bytes pakai OAuth header (required untuk private boards)."""
        r = self.session.get(
            attachment_url,
            headers={"Authorization": self._auth_header},
            timeout=timeout,
            stream=True,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Download {attachment_url} -> {r.status_code}")
        return r.content


# ===================== CLAUDE CLIENT =====================
IMAGE_MIME_OK = ("image/jpeg", "image/png", "image/gif", "image/webp")
VIDEO_EXT_OK = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".3gp")


def is_image_attachment(att):
    mime = (att.get("mimeType") or "").lower()
    if mime in IMAGE_MIME_OK:
        return True
    # Fallback: cek extension dari URL/name
    name = (att.get("name") or att.get("url") or "").lower()
    return any(name.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"))


def guess_mime(att):
    mime = (att.get("mimeType") or "").lower()
    if mime in IMAGE_MIME_OK:
        return mime
    name = (att.get("name") or att.get("url") or "").lower()
    if name.endswith(".png"): return "image/png"
    if name.endswith(".gif"): return "image/gif"
    if name.endswith(".webp"): return "image/webp"
    return "image/jpeg"


def resize_image_for_claude(raw_bytes, max_dim=1568):
    """Resize ke max_dim sisi terpanjang. Return (mime, bytes_resized)."""
    img = Image.open(BytesIO(raw_bytes))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return "image/jpeg", buf.getvalue()


# ===================== VIDEO (FFMPEG) =====================
def is_video_attachment(att):
    mime = (att.get("mimeType") or "").lower()
    if mime.startswith("video/"):
        return True
    name = (att.get("name") or att.get("url") or "").lower()
    return any(name.endswith(ext) for ext in VIDEO_EXT_OK)


def find_ffmpeg():
    """Cari ffmpeg: prioritas tools/ffmpeg/bin/ (portable) > PATH. None kalau ndak ada."""
    if os.path.isfile(FFMPEG_LOCAL):
        return FFMPEG_LOCAL
    p = shutil.which("ffmpeg")
    return p if p else None


def find_ffprobe():
    if os.path.isfile(FFPROBE_LOCAL):
        return FFPROBE_LOCAL
    p = shutil.which("ffprobe")
    return p if p else None


def get_video_duration(video_path, ffprobe_path=None):
    """Return duration in seconds (float). None kalau gagal."""
    ff = ffprobe_path or find_ffprobe()
    if not ff:
        return None
    try:
        result = subprocess.run(
            [ff, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def extract_video_frames(video_bytes, interval_sec=5, max_frames=15, max_dim=1568):
    """Extract frames dari video bytes menggunakan ffmpeg.

    Strategy:
      1. Save video ke temp file
      2. Probe duration; kalau (duration / interval_sec) > max_frames,
         interval naik supaya total frames = max_frames (auto-spread).
      3. Loop: ffmpeg seek + ekstrak 1 frame ke temp jpg
      4. Resize ke max_dim, return list[(mime, bytes)]
      5. Cleanup semua temp file

    Returns: list of (mime, raw_jpeg_bytes). Empty kalau ffmpeg ndak ada / video corrupt.
    """
    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()
    if not ffmpeg or not ffprobe:
        return []

    # Save video ke temp
    tmpdir = tempfile.mkdtemp(prefix="qc_video_")
    video_path = os.path.join(tmpdir, "input.mp4")
    try:
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        duration = get_video_duration(video_path, ffprobe)
        if duration is None or duration <= 0:
            return []

        # Tentukan timestamps: interval default, scale up kalau >max_frames
        if duration / interval_sec > max_frames:
            interval = duration / max_frames
        else:
            interval = float(interval_sec)
        timestamps = []
        t = 0.0
        while t < duration and len(timestamps) < max_frames:
            timestamps.append(t)
            t += interval

        frames = []
        for i, ts in enumerate(timestamps):
            frame_path = os.path.join(tmpdir, f"f{i:03d}.jpg")
            try:
                # -ss before -i = fast seek (kurang akurat tapi cukup buat thumbnail)
                # -frames:v 1 = 1 frame saja
                # -q:v 2 = high quality jpeg
                subprocess.run(
                    [ffmpeg, "-y", "-ss", f"{ts:.2f}", "-i", video_path,
                     "-frames:v", "1", "-q:v", "2", "-loglevel", "error",
                     frame_path],
                    capture_output=True, timeout=30,
                )
                if os.path.isfile(frame_path):
                    with open(frame_path, "rb") as ff:
                        raw = ff.read()
                    if raw:
                        mime, resized = resize_image_for_claude(raw, max_dim=max_dim)
                        frames.append((mime, resized))
            except (subprocess.SubprocessError, OSError):
                continue

        return frames
    finally:
        try: shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception: pass


class ClaudeClient:
    def __init__(self, api_key, config, logger):
        if anthropic is None:
            raise RuntimeError("Library 'anthropic' belum terinstall. Run install_dependencies.bat")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.config = config
        self.logger = logger

    def review(self, system_prompt, user_text, images, model=None,
               web_search=True, max_tokens=8000, web_search_max_uses=5):
        """Call Claude. images = list[(mime, raw_bytes)] sudah resized.
        Return dict: {text, score, usage, raw_response}."""
        model = model or self.config.get("CLAUDE_MODEL", "claude-opus-4-7")

        content = []
        for mime, b in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.b64encode(b).decode("ascii"),
                },
            })
        content.append({"type": "text", "text": user_text})

        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )
        if web_search:
            kwargs["tools"] = [{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": int(web_search_max_uses),
            }]

        msg = self.client.messages.create(**kwargs)

        # Extract concatenated text dari blocks (skip tool_use, server_tool_use,
        # web_search_tool_result blocks — final synthesis ada di text blocks).
        text_parts = []
        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
        full_text = "\n".join(text_parts).strip()

        score = parse_average_score(full_text)
        return {
            "text": full_text,
            "score": score,
            "model": model,
            "stop_reason": getattr(msg, "stop_reason", None),
            "usage": _usage_dict(msg),
        }


def _usage_dict(msg):
    try:
        u = msg.usage
        return {
            "input_tokens": getattr(u, "input_tokens", 0),
            "output_tokens": getattr(u, "output_tokens", 0),
            "cache_read": getattr(u, "cache_read_input_tokens", 0),
            "cache_create": getattr(u, "cache_creation_input_tokens", 0),
            "server_tool_use": getattr(u, "server_tool_use", None),
        }
    except Exception:
        return {}


class GeminiClient:
    """Wrapper Google Gemini dengan interface mirip ClaudeClient.
    Return shape sama: {text, score, model, stop_reason, usage}.

    Web search: pakai Google Search grounding tool (built-in di Gemini 2.x).
    Cost: ~$35/1000 search queries (lebih mahal dari Anthropic web_search $10/1000).
    """

    def __init__(self, api_key, config, logger):
        if _google_genai is None or _google_genai_types is None:
            raise RuntimeError("Library 'google-genai' belum terinstall. Run install_dependencies.bat")
        self._genai = _google_genai
        self._types = _google_genai_types
        self.client = _google_genai.Client(api_key=api_key)
        self.config = config
        self.logger = logger

    def review(self, system_prompt, user_text, images, model=None,
               web_search=True, max_tokens=8000, web_search_max_uses=5):
        types = self._types
        model = model or self.config.get("CLAUDE_MODEL", "gemini-2.5-flash")

        # Multimodal contents — gambar dulu, teks terakhir
        parts = []
        for mime, b in images:
            parts.append(types.Part.from_bytes(data=b, mime_type=mime))
        parts.append(types.Part.from_text(text=user_text))

        # Config dengan system instruction + optional grounding tool
        config_kwargs = {
            "system_instruction": system_prompt,
            "max_output_tokens": int(max_tokens),
        }
        if web_search:
            try:
                config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
            except Exception:
                pass

        gen_config = types.GenerateContentConfig(**config_kwargs)

        response = self.client.models.generate_content(
            model=model,
            contents=parts,
            config=gen_config,
        )

        text = (getattr(response, "text", None) or "").strip()
        score = parse_average_score(text)

        # Usage metadata
        usage = {}
        try:
            um = response.usage_metadata
            usage = {
                "input_tokens": getattr(um, "prompt_token_count", 0) or 0,
                "output_tokens": getattr(um, "candidates_token_count", 0) or 0,
                "total": getattr(um, "total_token_count", 0) or 0,
            }
        except Exception:
            pass

        # Stop / finish reason
        stop_reason = None
        try:
            cands = getattr(response, "candidates", None) or []
            if cands:
                fr = getattr(cands[0], "finish_reason", None)
                stop_reason = str(fr) if fr is not None else None
        except Exception:
            pass

        return {
            "text": text,
            "score": score,
            "model": model,
            "stop_reason": stop_reason,
            "usage": usage,
        }


# ===================== SCORE PARSER & TITLE UPDATE =====================
# Cari skor rata-rata dari output Claude. Robust ke berbagai format:
#   "**SKOR RATA-RATA**: 73"
#   "| **SKOR RATA-RATA** | 73 |"
#   "Skor rata-rata = 73.5"
#   "Average Score: 73"
#   "AVG 73"
_SCORE_PATTERNS = [
    # Primary: "SKOR RATA-RATA" (paling reliable, diminta di prompt)
    re.compile(r"SKOR\s+RATA[-\s]*RATA[^\d]{0,80}(\d{1,3}(?:[.,]\d+)?)", re.IGNORECASE),
    # Bahasa Indonesia variants
    re.compile(r"(?:skor|nilai|rating)\s+rata[-\s]*rata[^\d]{0,60}(\d{1,3}(?:[.,]\d+)?)", re.IGNORECASE),
    # English fallback
    re.compile(r"average\s+(?:score|rating)[^\d]{0,40}(\d{1,3}(?:[.,]\d+)?)", re.IGNORECASE),
    # Last-resort: lone "AVG" + number
    re.compile(r"\bAVG\b[^\d]{0,30}(\d{1,3}(?:[.,]\d+)?)", re.IGNORECASE),
]


def parse_average_score(text):
    """Extract average score (int 0-100) dari output Claude. None jika gagal."""
    if not text:
        return None
    for pattern in _SCORE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            val = float(m.group(1).replace(",", "."))
            score = int(round(val))
            if 0 <= score <= 100:
                return score
        except (ValueError, TypeError):
            continue
    return None


# ===================== CRITICAL ITEMS PARSER =====================
# Cari section "### 🚨 WAJIB FIX" lalu ambil bullet items.
# Stop saat ketemu section header berikutnya (### atau ##) atau end-of-text.
_CRITICAL_SECTION_RE = re.compile(
    r"#+\s*🚨\s*WAJIB\s*FIX[^\n]*\n(.*?)(?=\n#+\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)
# Bullet styles: "- ", "* ", "• "
_BULLET_PREFIX = ("- ", "* ", "• ")
# Placeholder dari prompt yg harus di-skip (Claude kadang copy template)
_PLACEHOLDER_RE = re.compile(
    r"\[\s*masalah[^\]]*\]\s*[→\->]+\s*\[\s*fix[^\]]*\]",
    re.IGNORECASE,
)


def parse_critical_items(text, max_items=20, max_len=300):
    """Extract critical items dari section '🚨 WAJIB FIX' di output Claude.

    Skip placeholder bullets (literal "[masalah 1] → [fix singkat]" dari prompt template).
    Return list of strings (truncated jika terlalu panjang).
    Empty list kalau section tidak ada atau cuma placeholder.
    """
    if not text:
        return []
    m = _CRITICAL_SECTION_RE.search(text)
    if not m:
        return []
    block = m.group(1)
    items = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Cek bullet prefix
        content = None
        for prefix in _BULLET_PREFIX:
            if line.startswith(prefix):
                content = line[len(prefix):].strip()
                break
        if content is None:
            continue
        if not content:
            continue
        if _PLACEHOLDER_RE.search(content):
            continue
        # Strip markdown bold/italic (** atau *) di start/end
        content = content.strip("*_ ").strip()
        if content:
            items.append(content[:max_len])
        if len(items) >= max_items:
            break
    return items


_TITLE_SCORE_RE = re.compile(r"\s*\[SCORE:([0-9→]+)\]\s*$")
# 🔄 = processing marker (real-time indicator saat bot lagi review card)
_LEADING_ICON_RE = re.compile(r"^[✅⚠️🚨🔄]+\s*")
PROCESSING_ICON = "🔄"


def update_title_with_score(current_title, new_score, threshold):
    """Format: '✅ Title [SCORE:85]' atau accumulate '✅ Title [SCORE:78→85→90]'."""
    title = current_title or ""
    m = _TITLE_SCORE_RE.search(title)
    if m:
        history = m.group(1)
        base = title[: m.start()].rstrip()
        base = _LEADING_ICON_RE.sub("", base).strip()
        new_history = f"{history}→{new_score}"
    else:
        base = _LEADING_ICON_RE.sub("", title).strip()
        new_history = str(new_score)
    icon = "✅" if new_score >= threshold else "⚠️"
    return f"{icon} {base} [SCORE:{new_history}]"


def mark_title_processing(current_title):
    """Tambahkan 🔄 di depan title untuk indikator real-time 'sedang di-review'.
    Idempotent: leading icon (✅⚠️🚨🔄) di-strip dulu sebelum 🔄 dipasang."""
    title = current_title or ""
    base = _LEADING_ICON_RE.sub("", title).strip()
    return f"{PROCESSING_ICON} {base}".strip()


def clear_processing_marker(current_title):
    """Hapus 🔄 (dan icon lain) dari depan title — dipanggil saat error path
    agar card tidak tertinggal 🔄 stale di Trello."""
    title = current_title or ""
    return _LEADING_ICON_RE.sub("", title).strip()


# ===================== STATS =====================
class Stats:
    """Persistent counters untuk reviewed_today / reviewed_total / publish / editing."""

    def __init__(self, path=STATE_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            return {
                "today_date": "",
                "today": {"reviewed": 0, "publish": 0, "editing": 0, "error": 0,
                          "score_sum": 0, "score_count": 0},
                "all_time": {"reviewed": 0, "publish": 0, "editing": 0, "error": 0,
                             "score_sum": 0, "score_count": 0},
            }
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return self._load.__func__(self)  # fallback

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _ensure_today(self):
        today = time.strftime("%Y-%m-%d")
        if self._data.get("today_date") != today:
            self._data["today_date"] = today
            self._data["today"] = {"reviewed": 0, "publish": 0, "editing": 0, "error": 0,
                                    "score_sum": 0, "score_count": 0}

    def record(self, outcome, score=None):
        """outcome: 'publish' | 'editing' | 'error'. Score (0-100) optional."""
        with self._lock:
            self._ensure_today()
            for bucket in (self._data["today"], self._data["all_time"]):
                bucket["reviewed"] = bucket.get("reviewed", 0) + 1
                bucket[outcome] = bucket.get(outcome, 0) + 1
                if score is not None:
                    bucket["score_sum"] = bucket.get("score_sum", 0) + int(score)
                    bucket["score_count"] = bucket.get("score_count", 0) + 1
            self._save()

    def snapshot(self):
        with self._lock:
            self._ensure_today()
            return json.loads(json.dumps(self._data))


# ===================== BOT CONTEXT =====================
class BotContext:
    """Holder shared antara orchestrator + UI bridge."""

    def __init__(self):
        self.config = Config()
        self.logger = Logger()
        self.stats = Stats()
        self.creds = load_trello_credentials()
        self.trello = TrelloClient(self.creds, self.logger)
        # Claude client baru di-init kalau API key tersedia (defer error sampai dipakai)
        try:
            self.claude_api_key = load_claude_api_key()
            self.claude = ClaudeClient(self.claude_api_key, self.config, self.logger)
        except Exception as e:
            self.claude_api_key = None
            self.claude = None
            self.logger.err(f"Claude init: {e}")

        # Gemini client (optional — non-fatal kalau API Gemini.txt tidak ada)
        try:
            self.gemini_api_key = load_gemini_api_key()
            self.gemini = GeminiClient(self.gemini_api_key, self.config, self.logger)
        except Exception as e:
            self.gemini_api_key = None
            self.gemini = None
            self.logger.warn(f"Gemini init skipped: {e}")

        # Runtime flags
        self.bot_enabled = True         # default ON saat launch (user request 2026-05-10)
        self.force_scan = False         # set True dari UI "Scan Now"
        self.stop_event = threading.Event()

        # Live runtime state (dibaca UI bridge)
        self.next_scan_in = 0           # detik countdown ke scan berikutnya
        self.current_wait = self.config.get_int("POLLING_BASE_SEC", 30)
        self.current_status = "Idle"    # Idle / Standby / Scanning / Reviewing
        self.last_card = None           # dict {id, name, score, outcome, ts}

        os.makedirs(REVIEWS_DIR, exist_ok=True)
        self.logger.cleanup_old(self.config.get_int("LOG_RETENTION_DAYS", 120))


# ===================== PROMPT TXT EDIT FILE OPENER =====================
def open_in_default_editor(path):
    """Buka file di default editor Windows (Notepad untuk .txt)."""
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False
