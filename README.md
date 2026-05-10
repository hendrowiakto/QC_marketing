# QC Marketing Bot

Auto QC reviewer untuk konten marketing GameMarket.gg di Trello — Claude / Gemini multi-AI dengan vision (image + video frames) + Google Search grounding.

## ⚙️ Cara Kerja

1. **Polling adaptif** list `🧐 Review & QC` di Trello (30s → 40s → 50s → ... → 600s, reset ke 30s saat ada card baru).
2. Saat dapat card → download attachment (gambar atau video — frame extraction via ffmpeg per 5 detik) → kirim ke AI.
3. AI review pakai prompt di `prompt.txt` (skor 0-100 per kriteria + checklist critical fix).
4. Bot post hasil ke card:
   - Comment review lengkap
   - Title prefix `🔄` saat processing → `✅`/`⚠️` + `[SCORE:NN]` saat selesai (accumulate `[SCORE:65→78]` kalau re-review)
   - Trello checklist `🚨 Wajib Fix` dengan critical items
5. Move card:
   - Skor ≥ threshold (default 75) → `🚀 Ready to Publish`
   - Skor < threshold → `✂️ Editing`

## 📦 Setup

### Windows

1. **Install Python 3.10+** dari https://python.org (centang "Add to PATH" saat install).
2. **Install Git** dari https://git-scm.com/download/win.
3. **Clone repo**:
   ```
   git clone https://github.com/hendrowiakto/QC_marketing.git
   cd QC_marketing
   ```
4. **Run `setup_windows.bat`** (double-click) — akan install Python deps + ffmpeg portable.
5. **Buat 4 file kredensial** (copy dari template `.example`):
   - `API Claude.txt` — Anthropic API key (https://console.anthropic.com)
   - `API Gemini.txt` — Google AI key (https://aistudio.google.com/apikey)
   - `Trello.txt` — API key + token + board IDs (lihat panduan ambil token Trello di bawah)
   - `config.txt` — settings (threshold, polling, model)
6. **Run bot**: double-click `main.py` atau `python main.py`.

### Mac

1. **Double-click `setup_mac.command`** (klik kanan → Open kalau ada warning Gatekeeper).
   - Auto-install Homebrew + Python 3.13 + git + ffmpeg
   - Auto-clone repo ke `~/QC_marketing`
   - Auto-install Python deps
2. Buat 4 file kredensial (sama seperti Windows step 5) di `~/QC_marketing/`.
3. Run bot:
   ```
   cd ~/QC_marketing && python3 main.py
   ```

## 🔄 Update

### Windows
Double-click `update.bat`:
- Auto-stop bot
- `git pull`
- Upgrade pip dependencies
- Relaunch bot

### Mac
Double-click `update_mac.command` — flow sama dengan Windows.

## 🔑 Cara Ambil Trello API Key + Token

1. Login Trello → buka https://trello.com/power-ups/admin
2. Klik **"New"** → buat Power-Up: nama bebas, workspace yang berisi board target.
3. Setelah dibuat, klik Power-Up → tab **"API Key"** → klik **"Generate a new API key"**. Copy **API Key** (32 char hex).
4. Klik link **"Token"** di samping API Key → halaman authorize → klik **"Allow"**. Copy **Token** (format `ATTA...`, 76 char).
5. Untuk dapat `BOARD_ID` + `LIST_*` IDs, run di terminal:
   ```bash
   curl "https://api.trello.com/1/boards/<BOARD_SHORTLINK>?key=<KEY>&token=<TOKEN>&fields=id,name&lists=open"
   ```
   `<BOARD_SHORTLINK>` = bagian setelah `/b/` di URL board (misal `OMl5PJIA` dari `https://trello.com/b/OMl5PJIA/...`).

## 📁 File Structure

```
QC_marketing/
├── main.py                  # Entry point + orchestrator polling
├── shared.py                # Config, Trello/Claude/Gemini clients, helpers
├── webview_app.py           # pywebview GUI bridge
├── QC Marketing Bot.html    # GUI React (dark theme)
├── prompt.txt               # System prompt untuk AI (edit untuk tweak QC criteria)
├── requirements.txt         # Python deps
├── icon.ico, notif.wav      # GUI assets
├── install_ffmpeg.bat       # Auto-download ffmpeg portable
├── install_dependencies.bat # Pip install
├── setup_windows.bat        # First-time setup Windows
├── setup_mac.command        # First-time setup Mac
├── update.bat               # Windows auto-update
├── update_mac.command       # Mac auto-update
├── config.txt               # [GITIGNORED] Settings (threshold, model, polling)
├── API Claude.txt           # [GITIGNORED] Anthropic API key
├── API Gemini.txt           # [GITIGNORED] Google AI key
├── Trello.txt               # [GITIGNORED] Trello key + token + board IDs
├── state.json               # [GITIGNORED] Stats counter
├── log/                     # [GITIGNORED] Daily rotated log
├── reviews/                 # [GITIGNORED] Per-card review backup .md
└── tools/ffmpeg/            # [GITIGNORED] Portable ffmpeg (download via install_ffmpeg.bat)
```

## 🧠 Supported AI Models

| Model | Provider | Cost (in/out per 1M tokens) | Catatan |
|---|---|---|---|
| `claude-opus-4-7` | Anthropic | $15 / $75 | Smart, mahal |
| `claude-sonnet-4-6` | Anthropic | $3 / $15 | Recommended |
| `claude-haiku-4-5-20251001` | Anthropic | $1 / $5 | Cepat |
| `gemini-2.5-pro` | Google | $1.25 / $10 | Smart, native video understanding |
| `gemini-2.5-flash` | Google | $0.30 / $2.50 | Paling murah |

Switch model dari GUI (dropdown di Settings) atau edit `CLAUDE_MODEL` di `config.txt`. Tidak perlu restart bot.

## 🎬 Video Support

ffmpeg auto-detect dari `tools/ffmpeg/bin/` (portable, di-install via `install_ffmpeg.bat`) atau system PATH.

Behavior: video > 5s di-extract jadi frames per 5 detik (max 15 frames), kirim sebagai multi-image ke AI. Untuk video > 75s, interval auto-naik supaya total tetap 15 frames.

## 🛠️ Troubleshooting

- **"ffmpeg tidak terinstall"** saat ada video → run `install_ffmpeg.bat`.
- **"invalid key" Trello** → token mungkin expired, generate ulang dari Power-Up admin.
- **Bot tidak detect card baru** → cek list ID di `Trello.txt` masih valid (list di-rename atau di-delete tidak akan match).
- **Score parse failed** → cek folder `reviews/` ada file dengan suffix `_X.md` (debug output Claude). Adjust prompt agar Claude consistent output `**SKOR RATA-RATA**: NN`.

## 📜 License

Internal use — GameMarket.gg.
