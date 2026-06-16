#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Content Forwarder — Gradio UI v2.1
==========================================
تحسينات عن النسخة الأولى:
  1. شريط تقدم حقيقي يتحدث لحظياً (Generator-based)
  2. دعم SESSION_STRING من HuggingFace Secrets (لا ملف .session)
  3. زر تصدير الجلسة كـ string
  4. عرض معلومات القناة قبل بدء النقل
  5. إصلاح bug: state_connected لم يكن يُستخدم فعلياً
  6. معالجة أفضل لأخطاء API ID/Hash
  7. إضافة tab "الإحصائيات" لعرض نتائج آخر عملية
  8. تحذير عند اختيار تأخير < 1 ثانية

تحسينات v2.1:
  9. تبويب "الإحصائيات" مع عرض مفصّل (ألبومات / منفردة / أخطاء)
  10. زر "تنظيف المجلد المؤقت" للصيانة
  11. عرض معلومات القناة المصدر والوجهة قبل النقل
  12. تحسينات بصرية ورسائل أوضح
"""

import os
import sys
import asyncio
import shutil
import threading
import gradio as gr
from typing import Optional, Generator
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from forwarder import (
    TelegramForwarder, ForwardConfig, ForwardResult,
    create_forwarder, TEMP_DIR,
)

# ─── Persistent Event Loop (fixes "event loop must not change") ───

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return a single persistent event loop running in a daemon thread."""
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            t = threading.Thread(target=_loop.run_forever, daemon=True)
            t.start()
        return _loop


def _run(coro):
    """Run a coroutine on the persistent event loop (Telethon-safe)."""
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=60)


# ─── Global State ─────────────────────────────────────────────

forwarder: Optional[TelegramForwarder] = None
last_result: Optional[ForwardResult]   = None

# ─── CSS ──────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap');

* { font-family: 'Tajawal', sans-serif !important; }
.gradio-container { direction: rtl; }

.header {
    text-align: center; padding: 24px;
    background: linear-gradient(135deg, #1a73e8 0%, #6c3fc5 100%);
    color: white; border-radius: 16px; margin-bottom: 20px;
}
.header h1 { margin: 0; font-size: 1.9em; font-weight: 700; }
.header p  { margin: 8px 0 0; opacity: .85; font-size: .95em; }

.warn-box {
    background: #fff3cd; color: #856404;
    border: 1px solid #ffc107; border-radius: 10px;
    padding: 12px 16px; margin: 8px 0; font-size: .9em;
}
.success-box {
    background: #d1e7dd; color: #0a3622;
    border: 1px solid #badbcc; border-radius: 10px;
    padding: 12px 16px; margin: 8px 0;
}
.error-box {
    background: #f8d7da; color: #58151c;
    border: 1px solid #f1aeb5; border-radius: 10px;
    padding: 12px 16px; margin: 8px 0;
}
.info-box {
    background: #cfe2ff; color: #052c65;
    border: 1px solid #9ec5fe; border-radius: 10px;
    padding: 12px 16px; margin: 8px 0;
}

.stat-card {
    display: inline-block;
    background: #f8f9fa;
    border: 1px solid #dee2e6;
    border-radius: 10px;
    padding: 16px 24px;
    margin: 6px;
    text-align: center;
    min-width: 120px;
}
.stat-card .stat-number {
    font-size: 2em;
    font-weight: 700;
    color: #1a73e8;
}
.stat-card .stat-label {
    font-size: .85em;
    color: #6c757d;
    margin-top: 4px;
}
"""

# ─── Helpers ──────────────────────────────────────────────────

# _run() moved above — persistent loop version


def _status_html(text: str, kind: str = "info") -> str:
    return f'<div class="{kind}-box">{text}</div>'


# ─── Event Handlers ───────────────────────────────────────────

def do_send_code(api_id_val, api_hash_val, phone_val, session_str_val):
    """إرسال كود التحقق أو تسجيل الدخول عبر session string."""
    global forwarder

    api_id_val    = str(api_id_val).strip()
    api_hash_val  = str(api_hash_val).strip()
    phone_val     = str(phone_val).strip()
    session_str_val = str(session_str_val).strip() if session_str_val else ""

    _hide_col = gr.Column(visible=False)

    if not api_id_val or not api_hash_val:
        return (
            _hide_col,
            _status_html("❌ أدخل API ID و API Hash أولاً", "error"),
            _hide_col,
        )

    try:
        api_id = int(api_id_val)
    except ValueError:
        return (
            _hide_col,
            _status_html("❌ API ID يجب أن يكون رقماً", "error"),
            _hide_col,
        )

    # قطع الاتصال القديم
    if forwarder:
        try:
            _run(forwarder.disconnect())
        except Exception:
            pass

    forwarder = create_forwarder(
        api_id, api_hash_val,
        session_string=session_str_val if session_str_val else None,
    )
    _run(forwarder.create_client())

    # هل الجلسة مفعّلة بالفعل؟
    _show_col = gr.Column(visible=True)

    if _run(forwarder.is_authorized()):
        return (
            _hide_col,
            _status_html("✅ متصل بنجاح (جلسة محفوظة)!", "success"),
            _show_col,
        )

    # أرسل الكود
    if not phone_val or not phone_val.startswith("+"):
        return (
            _hide_col,
            _status_html("❌ أدخل رقم هاتف صالح يبدأ بـ + (مثال: +963XXXXXXXXX)", "error"),
            _hide_col,
        )

    try:
        _run(forwarder.send_code(phone_val))
        return (
            _show_col,
            _status_html("📱 تم إرسال الكود — تحقق من تطبيق Telegram", "info"),
            _hide_col,
        )
    except Exception as e:
        return (
            _hide_col,
            _status_html(f"❌ {e}", "error"),
            _hide_col,
        )


def do_verify_code(code_val, password_val):
    """تأكيد كود التحقق."""
    global forwarder

    if not forwarder:
        return _status_html("❌ أعد إرسال الكود أولاً", "error"), gr.Column(visible=False)

    code_val = str(code_val).strip()
    if not code_val:
        return _status_html("❌ أدخل الكود", "error"), gr.Column(visible=False)

    try:
        _run(forwarder.verify_code(code_val, password_val or None))
        return (
            _status_html("✅ تم تسجيل الدخول بنجاح!", "success"),
            gr.Column(visible=True),
        )
    except ValueError as e:
        if "2FA_PASSWORD_REQUIRED" in str(e):
            return _status_html("🔐 أدخل كلمة مرور التحقق الثنائي أعلاه ثم اضغط تأكيد", "warn"), gr.Column(visible=False)
        return _status_html(f"❌ {e}", "error"), gr.Column(visible=False)
    except Exception as e:
        return _status_html(f"❌ {e}", "error"), gr.Column(visible=False)


def do_export_session():
    """تصدير الجلسة كـ string للحفظ في HF Secrets."""
    global forwarder
    if not forwarder:
        return _status_html("❌ غير متصل", "error"), ""
    try:
        s = _run(forwarder.export_session_string())
        return (
            _status_html("✅ انسخ الـ string أدناه واحفظه في HF Secrets باسم SESSION_STRING", "success"),
            s,
        )
    except Exception as e:
        return _status_html(f"❌ {e}", "error"), ""


def do_disconnect():
    global forwarder
    if forwarder:
        _run(forwarder.disconnect())
        forwarder = None
    return (
        gr.Column(visible=False),
        _status_html("🔌 تم قطع الاتصال", "warn"),
        gr.Column(visible=False),
    )


def do_refresh():
    """تحديث قائمة القنوات."""
    global forwarder
    _empty_dd = gr.Dropdown(choices=[])
    if not forwarder or not _run(forwarder.is_authorized()):
        return _empty_dd, _empty_dd, _status_html("❌ غير متصل — سجل دخول أولاً", "error")

    try:
        dialogs = _run(forwarder.get_dialogs())
        choices = []
        for d in dialogs:
            badge = "🔒 " if d.get("protected") else ""
            label = f"{badge}{d['title']} ({d['type']})"
            choices.append((label, str(d["id"])))

        _dd = gr.Dropdown(choices=choices, value=None, interactive=True)
        return (
            _dd,
            _dd,
            _status_html(f"✅ تم تحميل {len(choices)} قناة/مجموعة", "success"),
        )
    except Exception as e:
        return _empty_dd, _empty_dd, _status_html(f"❌ {e}", "error")


def do_channel_info(channel_id):
    """عرض معلومات قناة."""
    global forwarder
    if not channel_id or not forwarder:
        return ""
    try:
        info = _run(forwarder.get_channel_info(channel_id))
        protected = "🔒 نعم" if info.get("protected") else "✅ لا"
        restricted = "⛔ نعم" if info.get("restricted") else "✅ لا"
        members = info.get("participants_count", 0) or "غير معروف"
        username = info.get("username") or "بدون @username"
        return (
            f"**{info['title']}**\n\n"
            f"| الخاصية | القيمة |\n"
            f"|---|---|\n"
            f"| الأعضاء | {members} |\n"
            f"| @username | @{username} |\n"
            f"| محتوى محمي | {protected} |\n"
            f"| مقيد | {restricted} |"
        )
    except Exception:
        return ""


def do_pre_forward_info(source_val, dest_val, source_manual_val, dest_manual_val):
    """عرض ملخص القنوات قبل بدء النقل."""
    global forwarder
    if not forwarder or not _run(forwarder.is_authorized()):
        return _status_html("❌ غير متصل", "error")

    source = (source_manual_val or "").strip() or source_val
    dest   = (dest_manual_val or "").strip() or dest_val

    if not source or not dest:
        return _status_html("❌ اختر القناة المصدر والوجهة أولاً", "error")

    try:
        src_info = _run(forwarder.get_channel_info(source))
        dst_info = _run(forwarder.get_channel_info(dest))

        src_protected = "🔒 محمي" if src_info.get("protected") else "✅ عادي"
        dst_protected = "🔒 محمي" if dst_info.get("protected") else "✅ عادي"

        return _status_html(
            f"📥 **المصدر**: {src_info['title']} ({src_info.get('participants_count', '?')} عضو, {src_protected})<br>"
            f"📤 **الوجهة**: {dst_info['title']} ({dst_info.get('participants_count', '?')} عضو, {dst_protected})<br>"
            f"{'⚠️ سيتم استخدام تقنية Download-Upload لتجاوز حماية المحتوى' if src_info.get('protected') else '✅ المحتوى غير محمي — النقل عادي'}",
            "info"
        )
    except Exception as e:
        return _status_html(f"❌ تعذّر جلب المعلومات: {e}", "error")


def do_forward(
    limit_val, delay_val, start_id_val, end_id_val,
    media_only_val, text_only_val, skip_forwards_val,
    filter_text_val, source_val, dest_val,
    source_manual_val, dest_manual_val,
    send_caption_val, reverse_val,
):
    """بدء النقل — Generator لتحديث التقدم لحظياً."""
    global forwarder, last_result

    if not forwarder or not _run(forwarder.is_authorized()):
        yield _status_html("❌ غير متصل — سجل دخول أولاً", "error"), 0, "{}"
        return

    source = (source_manual_val or "").strip() or source_val
    dest   = (dest_manual_val   or "").strip() or dest_val

    if not source or not dest:
        yield _status_html("❌ اختر قناة المصدر والوجهة", "error"), 0, "{}"
        return

    if source == dest:
        yield _status_html("❌ القناة المصدر والوجهة لا يمكن أن تكونا نفسهما", "error"), 0, "{}"
        return

    if float(delay_val) < 1.0:
        yield _status_html("⚠️ تأخير أقل من ثانية — خطر حظر! سيُستخدم 1.0 ثانية", "warn"), 0, "{}"
        delay_val = 1.0

    config = ForwardConfig(
        source_channel = source,
        dest_channel   = dest,
        limit          = int(limit_val),
        delay          = float(delay_val),
        media_only     = bool(media_only_val),
        text_only      = bool(text_only_val),
        skip_forwards  = bool(skip_forwards_val),
        filter_text    = str(filter_text_val).strip() or None,
        start_id       = int(start_id_val) if int(start_id_val) > 0 else None,
        end_id         = int(end_id_val)   if int(end_id_val)   > 0 else None,
        send_caption   = bool(send_caption_val),
        reverse_order  = bool(reverse_val),
    )

    yield _status_html("⏳ جارٍ الاتصال بالقنوات…", "info"), 0, "{}"

    # Progress tracking عبر asyncio.Queue
    loop = _get_loop()
    progress_queue = asyncio.Queue()

    async def progress_cb(result: ForwardResult, pct: int):
        await progress_queue.put((result, pct))

    async def run_forward():
        try:
            result = await forwarder.forward_content(config, progress_callback=progress_cb)
            await progress_queue.put(("DONE", result))
        except Exception as e:
            await progress_queue.put(("ERROR", str(e)))

    # Run forward_content on the persistent loop
    future = asyncio.run_coroutine_threadsafe(run_forward(), loop)

    # استقبال التحديثات وإرسالها للـ UI
    import time as _time
    while not future.done() or not progress_queue.empty():
        try:
            item = progress_queue.get_nowait()
        except Exception:
            _time.sleep(0.5)
            continue

        if isinstance(item, tuple) and item[0] == "DONE":
            r: ForwardResult = item[1]
            last_result = r
            status = _status_html(
                f"✅ اكتملت العملية — نجح: {r.success} | فشل: {r.failed} | تخطى: {r.skipped} | الوقت: {r.elapsed}",
                "success"
            )
            yield status, 100, str(r.to_dict())
            return

        elif isinstance(item, tuple) and item[0] == "ERROR":
            yield _status_html(f"❌ {item[1]}", "error"), 0, "{}"
            return

        elif isinstance(item, tuple) and len(item) == 2:
            r, pct = item
            status = _status_html(
                f"⏳ جارٍ النقل — "
                f"نجح: {r.success} | فشل: {r.failed} | تخطى: {r.skipped} | "
                f"الألبومات: {r.albums} | "
                f"الإجمالي: {r.total}/{config.limit}",
                "info"
            )
            yield status, int(pct), str(r.to_dict())

    # Wait for completion if loop ended before queue drained
    try:
        future.result(timeout=5)
    except Exception:
        pass


def do_cancel():
    global forwarder
    if forwarder:
        forwarder.cancel()
    return _status_html("⛔ تم إرسال أمر الإلغاء…", "warn")


def do_clear_temp():
    """تنظيف المجلد المؤقت."""
    try:
        if TEMP_DIR.exists():
            count = sum(1 for _ in TEMP_DIR.rglob("*") if _.is_file())
            shutil.rmtree(str(TEMP_DIR), ignore_errors=True)
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            if count > 0:
                return _status_html(f"🧹 تم حذف {count} ملف مؤقت", "success")
            return _status_html("🧹 المجلد المؤقت نظيف بالفعل", "info")
        return _status_html("🧹 المجلد المؤقت غير موجود", "info")
    except Exception as e:
        return _status_html(f"❌ فشل التنظيف: {e}", "error")


def do_show_stats():
    """عرض إحصائيات آخر عملية نقل."""
    global last_result
    if not last_result:
        return (
            _status_html("لا توجد عمليات سابقة", "info"),
            "{}",
        )

    r = last_result
    status_text = f"**نتيجة آخر عملية نقل**\n\n"
    status_text += f"| المقياس | القيمة |\n"
    status_text += f"|---|---|\n"
    status_text += f"| الإجمالي | {r.total} |\n"
    status_text += f"| نجح | ✅ {r.success} |\n"
    status_text += f"| فشل | ❌ {r.failed} |\n"
    status_text += f"| تخطّى | ⏭️ {r.skipped} |\n"
    status_text += f"| ألبومات | 🖼️ {r.albums} |\n"
    status_text += f"| رسائل منفردة | 📄 {r.singles} |\n"
    status_text += f"| الوقت | ⏱️ {r.elapsed} |\n"

    if r.cancelled:
        status_text += f"| الحالة | ⛔ مُلغاة |\n"
    else:
        status_text += f"| الحالة | ✅ مكتملة |\n"

    return status_text, str(r.to_dict())


# ─── UI ───────────────────────────────────────────────────────

def build_app():
    with gr.Blocks(css=CSS, title="📨 Telegram Forwarder", theme=gr.themes.Soft()) as app:

        gr.HTML("""
        <div class="header">
            <h1>📨 Telegram Content Forwarder</h1>
            <p>نقل محتوى القنوات المقيدة باستخدام Userbot — v2.1</p>
        </div>
        """)

        with gr.Tabs():

            # ══════════════════════════════════════════════
            # TAB 1: تسجيل الدخول
            # ══════════════════════════════════════════════
            with gr.Tab("🔐 تسجيل الدخول"):

                gr.HTML("""<div class="info-box">
                    📌 احصل على API ID و API Hash من
                    <a href="https://my.telegram.org" target="_blank">my.telegram.org</a>
                    → API development tools
                </div>""")

                with gr.Row():
                    api_id   = gr.Number(label="API ID",   value=0, precision=0, minimum=1)
                    api_hash = gr.Textbox(label="API Hash", type="password", placeholder="abc123def456...")

                phone = gr.Textbox(
                    label="رقم الهاتف (مع كود الدولة)",
                    placeholder="+963XXXXXXXXX",
                )

                # Session String بديل عن ملف .session
                with gr.Accordion("🔑 تسجيل دخول بـ Session String (موصى به لـ HuggingFace)", open=False):
                    gr.HTML("""<div class="info-box">
                        إذا كنت قد سجّلت الدخول من قبل وصدّرت الجلسة،
                        الصقها هنا لتجنّب الحاجة للكود في كل مرة.
                        <br>أو احفظها في HF Secrets باسم <code>SESSION_STRING</code>.
                    </div>""")
                    session_str = gr.Textbox(
                        label="Session String (اختياري)",
                        placeholder="1BVtsOK...",
                        type="password",
                        value=os.environ.get("SESSION_STRING", ""),
                    )

                with gr.Row():
                    send_code_btn = gr.Button("📱 إرسال كود التحقق", variant="primary")
                    disconnect_btn = gr.Button("🔌 قطع الاتصال", variant="secondary")

                # كود التحقق (مخفي حتى إرسال الكود)
                with gr.Column(visible=False) as code_section:
                    gr.HTML('<div class="info-box">📲 تحقق من تطبيق Telegram — أدخل الكود المُرسَل</div>')
                    login_code = gr.Textbox(label="كود التحقق", placeholder="12345")
                    two_fa     = gr.Textbox(label="كلمة مرور التحقق الثنائي (اختياري)", type="password")
                    verify_btn = gr.Button("✅ تأكيد وتسجيل الدخول", variant="primary")

                login_status = gr.HTML(_status_html("غير متصل 🔴", "warn"))

                # تصدير الجلسة
                with gr.Column(visible=False) as export_section:
                    gr.HTML('<div class="info-box">💾 انسخ هذا الـ string واحفظه في HF Secrets لتجنّب تسجيل الدخول مجدداً</div>')
                    export_btn    = gr.Button("📤 تصدير الجلسة كـ String", variant="secondary")
                    export_status = gr.HTML()
                    session_out   = gr.Textbox(label="Session String", interactive=False)

            # ══════════════════════════════════════════════
            # TAB 2: اختيار القنوات
            # ══════════════════════════════════════════════
            with gr.Tab("📡 اختيار القنوات"):

                refresh_btn = gr.Button("🔄 تحديث قائمة القنوات", variant="secondary")
                refresh_status = gr.HTML()

                with gr.Row():
                    with gr.Column():
                        gr.Markdown("#### 📥 القناة المصدر")
                        source_list   = gr.Dropdown(choices=[], label="اختر من القائمة", interactive=True)
                        source_manual = gr.Textbox(label="أو أدخل يدوياً (@username أو ID)", placeholder="@channel_name")
                        source_info   = gr.Markdown()

                    with gr.Column():
                        gr.Markdown("#### 📤 القناة الوجهة")
                        dest_list   = gr.Dropdown(choices=[], label="اختر من القائمة", interactive=True)
                        dest_manual = gr.Textbox(label="أو أدخل يدوياً", placeholder="@my_channel")
                        dest_info   = gr.Markdown()

                # ملخص قبل النقل
                pre_forward_info = gr.HTML()
                check_info_btn = gr.Button("🔍 عرض معلومات القنوات", variant="secondary")

                gr.HTML("""<div class="warn-box">
                    ⚠️ القنوات المُشار إليها بـ 🔒 لديها محتوى محمي —
                    ستعمل تقنية Download-Upload على تجاوز هذا القيد.
                </div>""")

            # ══════════════════════════════════════════════
            # TAB 3: إعدادات النقل
            # ══════════════════════════════════════════════
            with gr.Tab("⚙️ إعدادات النقل"):

                with gr.Row():
                    with gr.Column():
                        limit = gr.Slider(1, 5000, value=100, step=1, label="عدد الرسائل")
                        delay = gr.Slider(1.0, 60.0, value=2.0, step=0.5, label="التأخير بين الرسائل (ثانية)")

                    with gr.Column():
                        start_id = gr.Number(label="من رسالة رقم (0 = من البداية)", value=0, precision=0)
                        end_id   = gr.Number(label="إلى رسالة رقم (0 = حتى النهاية)", value=0, precision=0)

                with gr.Row():
                    media_only     = gr.Checkbox(label="🖼️ وسائط فقط",               value=False)
                    text_only      = gr.Checkbox(label="📝 نص فقط",                  value=False)
                    skip_forwards  = gr.Checkbox(label="⏭️ تخطّ الرسائل المُعاد توجيهها", value=True)
                    send_caption   = gr.Checkbox(label="💬 أرفق النص مع الوسائط",    value=True)
                    reverse_order  = gr.Checkbox(label="🔃 ترتيب تصاعدي (الأقدم أولاً)", value=False)

                filter_text = gr.Textbox(
                    label="تصفية حسب النص (اختياري — فقط الرسائل التي تحتوي هذا النص)",
                    placeholder="كلمة أو عبارة للبحث…",
                )

                gr.HTML("""<div class="warn-box">
                    ⚠️ تأخير أقل من 2 ثانية قد يؤدي إلى حظر مؤقت أو دائم من Telegram.
                    الحد الأدنى الموصى به هو <b>2 ثانية</b>.
                </div>""")

            # ══════════════════════════════════════════════
            # TAB 4: بدء النقل
            # ══════════════════════════════════════════════
            with gr.Tab("🚀 بدء النقل"):

                with gr.Row():
                    start_btn  = gr.Button("▶️ بدء النقل",  variant="primary",  scale=3)
                    cancel_btn = gr.Button("⛔ إيقاف",       variant="stop",     scale=1)

                forward_status = gr.HTML(_status_html("في انتظار بدء النقل…", "info"))
                progress_bar   = gr.Slider(0, 100, value=0, label="التقدم %", interactive=False)
                stats_out      = gr.Code(label="آخر نتيجة (JSON)", language="json", value="{}")

            # ══════════════════════════════════════════════
            # TAB 5: الإحصائيات
            # ══════════════════════════════════════════════
            with gr.Tab("📊 الإحصائيات"):

                gr.Markdown("### نتائج آخر عملية نقل")

                with gr.Row():
                    show_stats_btn = gr.Button("📊 عرض الإحصائيات", variant="secondary")
                    clear_temp_btn = gr.Button("🧹 تنظيف المجلد المؤقت", variant="secondary")

                stats_display = gr.Markdown("لا توجد عمليات سابقة")
                stats_json    = gr.Code(label="بيانات JSON كاملة", language="json", value="{}")
                maintenance_status = gr.HTML()

            # ══════════════════════════════════════════════
            # TAB 6: المساعدة
            # ══════════════════════════════════════════════
            with gr.Tab("❓ المساعدة"):
                gr.Markdown("""
## كيفية الاستخدام

### الخطوة 1 — تسجيل الدخول
1. اذهب إلى [my.telegram.org](https://my.telegram.org)
2. سجل دخول بحسابك → **API development tools** → أنشئ تطبيقاً
3. أدخل **API ID** و **API Hash** في التبويب الأول
4. أدخل رقم هاتفك واضغط **إرسال كود التحقق**
5. أدخل الكود المُرسَل إلى Telegram واضغط **تأكيد**

### الخطوة 2 — حفظ الجلسة (مهم لـ HuggingFace)
بعد تسجيل الدخول، اضغط **تصدير الجلسة كـ String** واحفظ النتيجة في:
`Settings → Secrets → SESSION_STRING`
هذا يمنع الحاجة لإعادة تسجيل الدخول في كل مرة.

### الخطوة 3 — اختيار القنوات
- اضغط **تحديث قائمة القنوات**
- اختر المصدر (القناة المقيدة) والوجهة (قناتك الخاصة)
- اضغط **عرض معلومات القنوات** للتأكد

### الخطوة 4 — ضبط الإعدادات والنقل
- حدد عدد الرسائل والتأخير (2 ثانية موصى بها)
- اضغط **بدء النقل**

---

### كيف يعمل؟
بدلاً من **Forward** العادي الذي يُمنع بواسطة "Restrict Saving Content"،
يستخدم التطبيق تقنية **Download-Upload**:
1. يُنزّل الوسائط إلى ذاكرة مؤقتة
2. يُعيد رفعها كرسائل جديدة
3. يحذف الملفات المؤقتة فوراً

### دعم الألبومات (جديد v2.1)
التطبيق يكتشف تلقائياً الرسائل المجمّعة (Albums/MediaGroups) وينقلها كألبوم واحد
بدل إرسال كل صورة/فيديو منفصلاً.

### نصائح للاستخدام الآمن
- استخدم تأخيراً لا يقل عن **2 ثانية**
- لا تنقل أكثر من **500 رسالة** في الجلسة الواحدة
- استرح بين العمليات المتكررة
- **لا تُشارك Session String** مع أي أحد
- نظّف المجلد المؤقت بانتظام من تبويب الإحصائيات
                """)

        # ── Wire Events ───────────────────────────────────────

        # Login
        send_code_btn.click(
            do_send_code,
            inputs=[api_id, api_hash, phone, session_str],
            outputs=[code_section, login_status, export_section],
        )
        verify_btn.click(
            do_verify_code,
            inputs=[login_code, two_fa],
            outputs=[login_status, export_section],
        )
        disconnect_btn.click(
            do_disconnect,
            outputs=[code_section, login_status, export_section],
        )
        export_btn.click(
            do_export_session,
            outputs=[export_status, session_out],
        )

        # Channels
        refresh_btn.click(
            do_refresh,
            outputs=[source_list, dest_list, refresh_status],
        )
        source_list.change(do_channel_info, inputs=[source_list], outputs=[source_info])
        dest_list.change(do_channel_info,   inputs=[dest_list],   outputs=[dest_info])

        # Pre-forward info
        check_info_btn.click(
            do_pre_forward_info,
            inputs=[source_list, dest_list, source_manual, dest_manual],
            outputs=[pre_forward_info],
        )

        # Forward
        start_btn.click(
            do_forward,
            inputs=[
                limit, delay, start_id, end_id,
                media_only, text_only, skip_forwards, filter_text,
                source_list, dest_list, source_manual, dest_manual,
                send_caption, reverse_order,
            ],
            outputs=[forward_status, progress_bar, stats_out],
        )
        cancel_btn.click(do_cancel, outputs=[forward_status])

        # Statistics
        show_stats_btn.click(
            do_show_stats,
            outputs=[stats_display, stats_json],
        )
        clear_temp_btn.click(
            do_clear_temp,
            outputs=[maintenance_status],
        )

    return app


# ─── Launch ───────────────────────────────────────────────────

app = build_app()

if __name__ == "__main__":
    app.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", 7860)),
        show_error=True,
    )