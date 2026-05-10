"""main.py - QC Marketing Bot entry point.

Orchestrator pattern: single thread polling Trello list "Review & QC" dengan
adaptive interval (30s -> 40s -> 50s -> reset ke 30s saat ada kerja).
Process card paling atas, panggil Claude Opus untuk review (vision + web search),
parse skor dari output, update title + comment + move list.
"""

import os
import sys
import time
import threading
import traceback
from datetime import datetime

# Windows console default cp1252 — paksa UTF-8 supaya emoji ndak crash print.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from shared import (
    BotContext, REVIEWS_DIR,
    is_image_attachment, is_video_attachment, guess_mime, resize_image_for_claude,
    extract_video_frames, find_ffmpeg,
    update_title_with_score, mark_title_processing, clear_processing_marker,
    parse_critical_items,
    is_claude_model, is_gemini_model,
    load_prompt,
)

CHECKLIST_NAME = "🚨 Wajib Fix"


def get_active_ai_client(ctx, model_id):
    """Route ke ClaudeClient atau GeminiClient berdasar prefix model.
    Raise RuntimeError kalau model dipilih tapi client-nya gagal init."""
    if is_gemini_model(model_id):
        if ctx.gemini is None:
            raise RuntimeError(
                f"Model '{model_id}' butuh Gemini, tapi client tidak terinit. "
                "Pastikan 'API Gemini.txt' valid dan google-genai terinstall."
            )
        return ctx.gemini
    if is_claude_model(model_id):
        if ctx.claude is None:
            raise RuntimeError(
                f"Model '{model_id}' butuh Claude, tapi client tidak terinit. "
                "Cek 'API Claude.txt'."
            )
        return ctx.claude
    raise RuntimeError(f"Model '{model_id}' tidak dikenal (harus prefix claude- atau gemini-)")
from webview_app import WebviewApp


# ===================== HELPERS =====================
def _safe_filename(s, max_len=80):
    out = []
    for c in s or "":
        if c.isalnum() or c in (" ", "-", "_"):
            out.append(c)
        else:
            out.append("_")
    return "".join(out).strip().replace("  ", " ")[:max_len] or "untitled"


def build_user_message(card, custom_field_map):
    """Susun text user message ke Claude. custom_field_map: dict {field_id: name+options}.

    NOTE: Title card SENGAJA TIDAK dimasukkan ke prompt — title sering kontain
    metadata bot (🔄 / [SCORE:NN]) yang bisa nyasar ke output review.
    Konten yang di-review = description + custom field + image attachments.
    """
    lines = []
    lines.append("# KONTEN UNTUK DI-REVIEW")
    lines.append("")
    desc = (card.get("desc") or "").strip()
    if desc:
        lines.append("**Description / Caption**:")
        lines.append(desc)
        lines.append("")
    else:
        lines.append("**Description / Caption**: (kosong)")
        lines.append("")

    # Custom field "Tujuan Konten"
    cfis = card.get("customFieldItems") or []
    tujuan_lines = []
    for cfi in cfis:
        cf_id = cfi.get("idCustomField")
        meta = custom_field_map.get(cf_id)
        if not meta:
            continue
        name = meta.get("name", "")
        # type 'list': value is in idValue -> lookup option label
        if meta.get("type") == "list":
            opt_id = cfi.get("idValue")
            for opt in meta.get("options", []):
                if opt.get("id") == opt_id:
                    label = (opt.get("value") or {}).get("text", "")
                    if label:
                        tujuan_lines.append(f"- **{name}**: {label}")
                    break
        else:
            val = cfi.get("value") or {}
            text_val = val.get("text") or val.get("number") or val.get("date") or ""
            if text_val:
                tujuan_lines.append(f"- **{name}**: {text_val}")

    if tujuan_lines:
        lines.append("**Custom Fields**:")
        lines.extend(tujuan_lines)
        lines.append("")

    # Image / video count
    attachments = card.get("attachments") or []
    img_count = sum(1 for a in attachments if is_image_attachment(a))
    vid_count = sum(1 for a in attachments if is_video_attachment(a))
    notes = []
    if img_count > 0:
        notes.append(f"{img_count} gambar")
    if vid_count > 0:
        notes.append(
            f"{vid_count} video (di-extract jadi frames per ~5 detik, "
            "urutan kiri→kanan = flow video — JANGAN review per-frame, "
            "evaluasi sebagai 1 video utuh)"
        )
    if notes:
        lines.append(f"**Visual**: {' + '.join(notes)} dilampirkan di bawah.")
    else:
        lines.append("**Visual**: tidak ada attachment visual — review berdasarkan teks saja.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Tolong review konten di atas sesuai panduan di system prompt. "
                 "Lakukan web search untuk verifikasi klaim faktual yang relevan.")
    return "\n".join(lines)


def save_review_backup(card, score, outcome, review_text):
    """Simpan markdown backup di reviews/."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_name = _safe_filename(card.get("name", "untitled"))
    fname = f"{ts}_{safe_name}_{score if score is not None else 'X'}.md"
    path = os.path.join(REVIEWS_DIR, fname)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Review: {card.get('name', '')}\n\n")
            f.write(f"- **Card ID**: {card.get('id', '')}\n")
            f.write(f"- **Card URL**: {card.get('shortUrl', '')}\n")
            f.write(f"- **Score**: {score}\n")
            f.write(f"- **Outcome**: {outcome}\n")
            f.write(f"- **Timestamp**: {datetime.now().isoformat(timespec='seconds')}\n\n")
            f.write("---\n\n")
            f.write(review_text or "")
    except Exception:
        pass


def _build_bot_signature_comment(score, outcome_label, review_text, model):
    """Comment text untuk Trello dengan signature bot di awal."""
    icon = "✅" if outcome_label == "publish" else "⚠️"
    header = (
        f"🤖 **QC Bot Review** — {datetime.now().strftime('%d %b %Y %H:%M')}\n"
        f"Model: `{model}` | Score rata-rata: **{score}** | Verdict: {icon} "
        f"{'SIAP PUBLISH' if outcome_label == 'publish' else 'REVISI'}\n\n"
        f"---\n\n"
    )
    return header + (review_text or "")


def _build_error_comment(card_name, error_msg, retry_wait):
    """Comment kalau error — kirim ke Editing dengan instruksi retry."""
    return (
        f"🤖 **QC Bot — ERROR**\n"
        f"Time: {datetime.now().strftime('%d %b %Y %H:%M')}\n\n"
        f"⚠️ Bot tidak bisa review card ini: \n"
        f"```\n{error_msg[:500]}\n```\n\n"
        f"**Action**: Tim marketing tolong cek konten card-nya, "
        f"lalu pindahkan kembali ke 🧐 **Review & QC** untuk retry. "
        f"Bot akan polling list itu setiap {retry_wait}s."
    )


# ===================== ORCHESTRATOR =====================
def process_card(ctx, card, system_prompt, custom_field_map):
    """Process 1 card dari Review & QC. Raise exception saat fatal."""
    card_id = card["id"]
    card_name = card.get("name", "")
    ctx.current_status = "Reviewing"
    ctx.logger.review(f"Mulai review: \"{card_name[:80]}\" (id={card_id[:8]})")

    # ---------- Set 🔄 processing marker di title (visible di Trello real-time) ----------
    try:
        processing_title = mark_title_processing(card_name)
        if processing_title != card_name:
            ctx.trello.update_card_name(card_id, processing_title)
            ctx.logger.trello(f"Title -> 🔄 marker ({processing_title[:60]})")
    except Exception as e:
        ctx.logger.warn(f"Gagal set 🔄 marker: {str(e)[:120]}")

    # ---------- Download images + extract video frames ----------
    images = []
    attachments = card.get("attachments") or []
    max_dim = ctx.config.get_int("IMAGE_MAX_DIMENSION", 1568)
    interval_sec = ctx.config.get_int("VIDEO_FRAME_INTERVAL_SEC", 5)
    max_frames = ctx.config.get_int("VIDEO_MAX_FRAMES", 15)
    max_video_bytes = ctx.config.get_int("VIDEO_MAX_BYTES", 200 * 1024 * 1024)

    for att in attachments:
        url = att.get("url")
        name = att.get("name", "?")
        if not url:
            continue

        if is_image_attachment(att):
            try:
                ctx.logger.trello(f"Download image: {name}")
                raw = ctx.trello.download_attachment_bytes(url)
                mime, resized = resize_image_for_claude(raw, max_dim=max_dim)
                images.append((mime, resized))
            except Exception as e:
                ctx.logger.warn(f"Gagal download/resize image '{name}': {str(e)[:160]}")
        elif is_video_attachment(att):
            ffmpeg_path = find_ffmpeg()
            if not ffmpeg_path:
                ctx.logger.warn(
                    f"Skip video '{name}': ffmpeg tidak terinstall. Run install_ffmpeg.bat untuk enable video review."
                )
                continue
            try:
                size_bytes = att.get("bytes") or 0
                if size_bytes and size_bytes > max_video_bytes:
                    ctx.logger.warn(
                        f"Skip video '{name}': size {size_bytes/1024/1024:.1f}MB > "
                        f"limit {max_video_bytes/1024/1024:.0f}MB"
                    )
                    continue
                ctx.logger.trello(f"Download video: {name} ({size_bytes/1024/1024:.1f}MB)")
                video_bytes = ctx.trello.download_attachment_bytes(url)
                if len(video_bytes) > max_video_bytes:
                    ctx.logger.warn(f"Skip video '{name}': downloaded size exceeds limit")
                    continue
                ctx.logger.app(f"Extract frames dari '{name}' (1/{interval_sec}s, max {max_frames})...")
                frames = extract_video_frames(
                    video_bytes,
                    interval_sec=interval_sec,
                    max_frames=max_frames,
                    max_dim=max_dim,
                )
                if not frames:
                    ctx.logger.warn(f"Video '{name}': 0 frames extracted (corrupt/format unsupported?)")
                else:
                    ctx.logger.app(f"Video '{name}': {len(frames)} frames extracted")
                images.extend(frames)
            except Exception as e:
                ctx.logger.warn(f"Gagal proses video '{name}': {str(e)[:160]}")
        # else: non-image non-video → skip (e.g., PDF, doc)

    if not images and not (card.get("desc") or "").strip():
        raise RuntimeError("Card kosong (tidak ada description, gambar, atau video attachment)")

    # ---------- Build user message ----------
    user_msg = build_user_message(card, custom_field_map)

    # ---------- Call AI (route ke Claude atau Gemini) ----------
    web_search = ctx.config.get_bool("WEB_SEARCH_ENABLED", True)
    max_uses = ctx.config.get_int("WEB_SEARCH_MAX_USES", 5)
    max_tokens = ctx.config.get_int("MAX_OUTPUT_TOKENS", 8000)
    model = ctx.config.get("CLAUDE_MODEL", "claude-opus-4-7")
    ai_client = get_active_ai_client(ctx, model)
    provider_label = "Gemini" if is_gemini_model(model) else "Claude"

    ctx.logger.claude(f"Calling {model} via {provider_label} (images={len(images)}, web_search={'ON' if web_search else 'OFF'})...")
    t0 = time.time()
    result = ai_client.review(
        system_prompt=system_prompt,
        user_text=user_msg,
        images=images,
        model=model,
        web_search=web_search,
        max_tokens=max_tokens,
        web_search_max_uses=max_uses,
    )
    dt = time.time() - t0
    usage = result.get("usage") or {}
    ctx.logger.claude(
        f"{provider_label} selesai dalam {dt:.1f}s | tokens in={usage.get('input_tokens', '?')} "
        f"out={usage.get('output_tokens', '?')} | stop={result.get('stop_reason')}"
    )

    score = result.get("score")
    text = result.get("text") or ""
    if score is None:
        # Save full output for debug
        save_review_backup(card, None, "parse-error", text)
        raise RuntimeError(
            "Tidak bisa parse skor rata-rata dari output Claude. "
            "Cek folder reviews/ untuk full output, lalu adjust prompt.txt jika perlu."
        )

    threshold = ctx.config.get_int("THRESHOLD", 75)
    outcome = "publish" if score >= threshold else "editing"
    target_list = ctx.trello.list_ready_publish if outcome == "publish" else ctx.trello.list_editing

    # ---------- Save backup FIRST (resilience: kalau Trello mutation gagal,
    # review tetap aman di disk dan bisa di-recover manual). ----------
    save_review_backup(card, score, outcome, text)

    # ---------- Trello: comment dulu (paling rawan fail karena size besar),
    # baru checklist + title + move. ----------
    comment_text = _build_bot_signature_comment(score, outcome, text, model)
    ctx.logger.trello(f"Post comment ({len(comment_text)} chars)")
    ctx.trello.add_comment(card_id, comment_text)

    # ---------- Checklist '🚨 Wajib Fix' (replace existing kalau ada) ----------
    critical_items = parse_critical_items(text)
    if critical_items:
        try:
            ctx.logger.trello(f"Replace checklist '{CHECKLIST_NAME}' ({len(critical_items)} items)")
            ctx.trello.replace_checklist_with_items(card_id, CHECKLIST_NAME, critical_items)
        except Exception as e:
            ctx.logger.warn(f"Gagal create checklist: {str(e)[:160]}")
    else:
        # No critical items — clean up old checklist kalau ada (re-review yg tadinya
        # ada 🚨 sekarang clean — checklist lama harus dihapus biar ndak misleading).
        try:
            existing = ctx.trello.get_checklists(card_id) or []
            for cl in existing:
                if (cl.get("name") or "").strip() == CHECKLIST_NAME.strip():
                    ctx.trello.delete_checklist(cl["id"])
                    ctx.logger.trello(f"Hapus checklist '{CHECKLIST_NAME}' lama (no critical items)")
                    break
        except Exception:
            pass

    new_title = update_title_with_score(card_name, score, threshold)
    ctx.logger.trello(f"Update title: \"{new_title[:80]}\"")
    ctx.trello.update_card_name(card_id, new_title)

    ctx.logger.trello(f"Move card -> {'🚀 Ready to Publish' if outcome == 'publish' else '✂️ Editing'}")
    ctx.trello.move_card(card_id, target_list)

    # ---------- Stats ----------
    ctx.stats.record(outcome, score=score)
    ctx.last_card = {
        "id": card_id,
        "name": card_name,
        "new_title": new_title,
        "score": score,
        "outcome": outcome,
        "ts": datetime.now().strftime("%H:%M:%S"),
        "url": card.get("shortUrl", ""),
    }
    icon = "✅" if outcome == "publish" else "⚠️"
    ctx.logger.ok(f"{icon} \"{card_name[:60]}\" SCORE={score} -> {outcome.upper()}")


def handle_card_error(ctx, card, err_msg, retry_wait):
    """Comment error + clear 🔄 marker + move ke Editing."""
    # Strip 🔄 dari title kalau sempat dipasang (defensive — selalu force set
    # ke cleaned base supaya ndak tertinggal stale di Trello).
    try:
        cleaned = clear_processing_marker(card.get("name", "") or "")
        if cleaned:
            try: ctx.trello.update_card_name(card["id"], cleaned)
            except Exception: pass
    except Exception: pass
    try:
        comment = _build_error_comment(card.get("name", ""), err_msg, retry_wait)
        ctx.trello.add_comment(card["id"], comment)
        ctx.trello.move_card(card["id"], ctx.trello.list_editing)
        ctx.logger.warn(f"Card '{card.get('name', '')[:50]}' di-bounce ke Editing dengan error comment")
    except Exception as e:
        ctx.logger.err(f"Gagal handle error untuk card {card.get('id')}: {str(e)[:160]}")
    ctx.stats.record("error")


def fetch_custom_field_map(ctx):
    """Pre-fetch board custom fields, build {field_id: meta} dict."""
    try:
        data = ctx.trello.get_custom_fields() or []
        return {cf["id"]: cf for cf in data}
    except Exception as e:
        ctx.logger.warn(f"Gagal fetch custom fields: {str(e)[:160]}")
        return {}


def orchestrator(ctx):
    """Main loop polling adaptif."""
    base = ctx.config.get_int("POLLING_BASE_SEC", 30)
    step = ctx.config.get_int("POLLING_STEP_SEC", 10)
    cap = ctx.config.get_int("POLLING_MAX_SEC", 50)

    wait = base
    ctx.current_wait = wait
    ctx.logger.app("Orchestrator started.")

    # Cache custom field map (re-fetch sekali per N cycle, jarang berubah)
    cf_map = fetch_custom_field_map(ctx)
    cf_refresh_counter = 0

    while not ctx.stop_event.is_set():
        # Bot disabled -> idle wait
        if not ctx.bot_enabled:
            ctx.current_status = "Stopped"
            ctx.next_scan_in = 0
            time.sleep(0.5)
            continue

        # ---------- Countdown wait ----------
        ctx.current_status = "Standby"
        ctx.current_wait = wait
        countdown = wait
        while countdown > 0 and not ctx.stop_event.is_set() and ctx.bot_enabled:
            if ctx.force_scan:
                ctx.logger.app(f"Force Scan triggered, skip wait (was {countdown}s remaining)")
                ctx.force_scan = False
                break
            ctx.next_scan_in = countdown
            time.sleep(1)
            countdown -= 1
        ctx.next_scan_in = 0
        if ctx.stop_event.is_set() or not ctx.bot_enabled:
            continue

        # ---------- Refresh prompt + custom fields tiap 5 cycle ----------
        cf_refresh_counter += 1
        if cf_refresh_counter >= 5:
            cf_map = fetch_custom_field_map(ctx)
            cf_refresh_counter = 0

        # Reload prompt fresh setiap scan (user mungkin baru edit di Notepad)
        try:
            system_prompt = load_prompt()
        except Exception as e:
            ctx.logger.err(f"Gagal load prompt.txt: {e}")
            time.sleep(2)
            continue

        # ---------- Scan list Review & QC ----------
        ctx.current_status = "Scanning"
        try:
            cards = ctx.trello.get_review_qc_cards() or []
        except Exception as e:
            ctx.logger.err(f"Trello scan error: {str(e)[:200]}")
            wait = min(wait + step, cap)
            continue

        if not cards:
            ctx.logger.app(f"List '🧐 Review & QC' kosong. Next scan in {min(wait + step, cap)}s")
            wait = min(wait + step, cap)
            continue

        # Sort by pos asc (top first)
        cards.sort(key=lambda c: c.get("pos", 0))
        target_card = cards[0]
        ctx.logger.app(
            f"Found {len(cards)} card(s) di Review & QC. "
            f"Process top-1: \"{target_card.get('name', '')[:60]}\""
        )

        try:
            process_card(ctx, target_card, system_prompt, cf_map)
        except Exception as e:
            err_msg = str(e)
            tb = traceback.format_exc()
            ctx.logger.err(f"Process card gagal: {err_msg[:300]}")
            ctx.logger.err(tb.splitlines()[-1] if tb else "")
            handle_card_error(ctx, target_card, err_msg, base)

        # Reset wait ke base (ada kerja ditemukan walau pun fail)
        wait = base

    ctx.logger.app("Orchestrator stopped.")


# ===================== ENTRY =====================
def main():
    print("[QC Marketing Bot] Booting...")
    try:
        ctx = BotContext()
    except Exception as e:
        print(f"FATAL: {e}")
        traceback.print_exc()
        input("Press Enter to exit...")
        return 1

    ctx.logger.app(f"QC Marketing Bot v1.0 starting...")
    ctx.logger.app(f"Board: {ctx.creds['BOARD_ID']}")

    # Quick connectivity probe (non-fatal)
    try:
        me = ctx.trello.whoami()
        ctx.logger.ok(f"Trello: connected as {me.get('username', '?')} ({me.get('fullName', '')})")
    except Exception as e:
        ctx.logger.err(f"Trello probe gagal: {str(e)[:160]}")

    # Start orchestrator daemon
    t = threading.Thread(target=orchestrator, args=(ctx,), name="orchestrator", daemon=True)
    t.start()

    # Launch GUI (blocking until window closed)
    app = WebviewApp(ctx)
    try:
        app.run()
    finally:
        ctx.stop_event.set()
        ctx.logger.app("Shutdown signal sent.")
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
