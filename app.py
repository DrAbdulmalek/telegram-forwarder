#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Content Forwarder - Gradio Web Interface
=================================================
Ready for Hugging Face Spaces deployment.
"""

import os
import sys
import json
import asyncio
import gradio as gr
from typing import Optional

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from forwarder import TelegramForwarder, ForwardConfig

# Global state
forwarder_instance: Optional[TelegramForwarder] = None

# CSS for RTL Arabic support
custom_css = """
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700&display=swap');

body, .gradio-container {
    font-family: 'Tajawal', sans-serif;
    direction: rtl;
}

.header {
    text-align: center;
    padding: 20px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    border-radius: 15px;
    margin-bottom: 20px;
}

.header h1 {
    margin: 0;
    font-size: 2em;
}

.header p {
    margin: 10px 0 0 0;
    opacity: 0.9;
}

.tab-content {
    padding: 20px;
}

.status-box {
    border-radius: 10px;
    padding: 15px;
    margin: 10px 0;
}

.success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
.warning { background: #fff3cd; color: #856404; border: 1px solid #ffeeba; }
.error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
.info { background: #d1ecf1; color: #0c5460; border: 1px solid #bee5eb; }

.progress-bar {
    width: 100%;
    height: 25px;
    background: #e9ecef;
    border-radius: 12px;
    overflow: hidden;
    margin: 10px 0;
}

.progress-fill {
    height: 100%;
    background: linear-gradient(90deg, #667eea, #764ba2);
    transition: width 0.3s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-weight: bold;
}

.btn-primary {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    color: white !important;
    border: none !important;
    padding: 12px 30px !important;
    border-radius: 25px !important;
    font-size: 1.1em !important;
    cursor: pointer;
    transition: transform 0.2s;
}

.btn-primary:hover {
    transform: scale(1.05);
}

.btn-danger {
    background: #dc3545 !important;
    color: white !important;
    border: none !important;
    padding: 12px 30px !important;
    border-radius: 25px !important;
}

.channel-card {
    border: 1px solid #dee2e6;
    border-radius: 10px;
    padding: 15px;
    margin: 10px 0;
    background: #f8f9fa;
    cursor: pointer;
    transition: all 0.2s;
}

.channel-card:hover {
    background: #e9ecef;
    border-color: #667eea;
}

.channel-card.selected {
    background: #667eea;
    color: white;
    border-color: #667eea;
}
"""


def render_header():
    """Render app header."""
    return gr.HTML("""
    <div class="header">
        <h1>Telegram Content Forwarder</h1>
        <p>نسخ محتوى القنوات المقيدة باستخدام Userbot</p>
    </div>
    """)


# ============== Main App ==============
def create_app():
    """Create Gradio app."""

    with gr.Blocks(css=custom_css, title="Telegram Content Forwarder", theme=gr.themes.Soft()) as app:
        render_header()

        # State variables
        state_connected = gr.State(False)

        with gr.Tabs():

            # ============== TAB 1: Login ==============
            with gr.Tab("تسجيل الدخول", id="login"):
                gr.Markdown("""
                ### الخطوة 1: أدخل بيانات API
                1. اذهب إلى [my.telegram.org](https://my.telegram.org)
                2. سجل دخول بحسابك
                3. اذهب إلى API development tools
                4. أنشئ تطبيق جديد واحصل على **API ID** و **API Hash**
                """)

                with gr.Row():
                    api_id = gr.Number(label="API ID", value=0, precision=0)
                    api_hash = gr.Textbox(label="API Hash", type="password")

                phone = gr.Textbox(label="رقم الهاتف (مع كود الدولة)", placeholder="+966XXXXXXXXX")

                with gr.Row():
                    send_code_btn = gr.Button("ارسال كود التحقق", variant="primary")
                    disconnect_btn = gr.Button("فصل", variant="secondary")

                # Step 2: Code verification (hidden initially)
                with gr.Column(visible=False) as code_step:
                    gr.Markdown("### الخطوة 2: أدخل كود التحقق")
                    gr.Markdown("تم إرسال كود إلى تطبيق Telegram الخاص بك")

                    login_code = gr.Textbox(label="كود التحقق", placeholder="أدخل الكود المكون من 5 أرقام")
                    two_fa_pass = gr.Textbox(label="كلمة مرور التحقق الثنائي (اختياري)", type="password",
                                              visible=False, placeholder="إذا كنت مفعل التحقق الثنائي")

                    verify_btn = gr.Button("تأكيد وتسجيل الدخول", variant="primary")

                login_status = gr.Textbox(label="الحالة", interactive=False, value="غير متصل")

            # ============== TAB 2: Channel Selection ==============
            with gr.Tab("اختيار القنوات", id="channels"):
                gr.Markdown("### اختر القناة المصدر والوجهة")

                refresh_btn = gr.Button("تحديث قائمة القنوات", variant="secondary")

                with gr.Row():
                    with gr.Column():
                        gr.Markdown("#### القناة المصدر")
                        source_list = gr.Dropdown(
                            choices=[],
                            label="اختر القناة المصدر",
                            interactive=True
                        )
                        source_manual = gr.Textbox(
                            label="أو أدخل يدوياً (@username أو ID)",
                            placeholder="@channel_name"
                        )

                    with gr.Column():
                        gr.Markdown("#### القناة الوجهة")
                        dest_list = gr.Dropdown(
                            choices=[],
                            label="اختر القناة الوجهة",
                            interactive=True
                        )
                        dest_manual = gr.Textbox(
                            label="أو أدخل يدوياً (@username أو ID)",
                            placeholder="@my_channel"
                        )

                channel_info = gr.JSON(label="معلومات القناة", visible=False)

            # ============== TAB 3: Forward Settings ==============
            with gr.Tab("بدء النقل", id="forward"):
                gr.Markdown("### إعدادات النقل")

                with gr.Row():
                    with gr.Column():
                        limit = gr.Slider(
                            minimum=1, maximum=5000, value=100, step=1,
                            label="عدد الرسائل"
                        )
                        delay = gr.Slider(
                            minimum=0.5, maximum=60, value=2.0, step=0.5,
                            label="التأخير بين الرسائل (ثانية)"
                        )

                    with gr.Column():
                        start_id = gr.Number(
                            label="من رسالة رقم (اختياري)",
                            value=0, precision=0
                        )
                        end_id = gr.Number(
                            label="إلى رسالة رقم (اختياري)",
                            value=0, precision=0
                        )

                with gr.Row():
                    media_only = gr.Checkbox(label="وسائط فقط", value=False)
                    text_only = gr.Checkbox(label="نص فقط", value=False)
                    skip_forwards = gr.Checkbox(label="تخطى المعاد توجيهها", value=True)

                filter_text = gr.Textbox(
                    label="تصفية حسب النص (اختياري)",
                    placeholder="أدخل كلمة للبحث..."
                )

                with gr.Row():
                    start_btn = gr.Button("بدء النقل", variant="primary", elem_classes=["btn-primary"])
                    cancel_btn = gr.Button("إيقاف", variant="stop", elem_classes=["btn-danger"])

                # Progress section
                with gr.Column(visible=False) as progress_section:
                    progress_bar = gr.Slider(minimum=0, maximum=100, value=0, label="التقدم", interactive=False)
                    progress_text = gr.Textbox(label="التفاصيل", interactive=False)
                    stats_json = gr.JSON(label="الاحصائيات")

            # ============== TAB 4: Help ==============
            with gr.Tab("المساعدة", id="help"):
                gr.Markdown("""
                ## كيفية الاستخدام

                ### 1 - تسجيل الدخول
                - أدخل **API ID** و **API Hash** من [my.telegram.org](https://my.telegram.org)
                - أدخل رقم هاتفك مع كود الدولة (مثال: +966501234567)
                - اضغط "ارسال كود التحقق" — سيصلك كود على Telegram
                - أدخل الكود في الحقل واضغط "تأكيد وتسجيل الدخول"
                - إذا كنت مفعل التحقق الثنائي، أدخل كلمة المرور أيضاً

                ### 2 - اختيار القنوات
                - اضغط "تحديث قائمة القنوات" لعرض القنوات التي انضممت إليها
                - اختر القناة المصدر (المحتوى المقيد)
                - اختر القناة الوجهة (قناتك الخاصة)

                ### 3 - بدء النقل
                - حدد عدد الرسائل (1-5000)
                - حدد التأخير بين الرسائل (2 ثوانٍ موصى بها)
                - اضغط "بدء النقل"

                ### تحذيرات مهمة
                - **التأخير المنخفض** (< 1 ثانية) قد يؤدي إلى حظر مؤقت من Telegram
                - **الاستخدام المفرط** قد يؤدي إلى حظر دائم
                - **احترم حقوق** منشئي المحتوى — استخدم للأغراض الشخصية فقط
                - **لا تشارك** ملف الجلسة (`.session`) مع أي شخص

                ### كيف يعمل؟
                بدلاً من "إعادة التوجيه" العادية (Forward) التي تُمنعها قيود الحفظ،
                يستخدم هذا التطبيق تقنية **التحميل وإعادة الرفع** (Download-Upload):

                1. يحمل الوسائط (صور/فيديو/ملفات) إلى الجهاز مؤقتاً
                2. يرفعها كرسائل جديدة في القناة الوجهة
                3. يحذف الملفات المؤقتة تلقائياً

                ### الدعم
                - GitHub: [github.com/DrAbdulmalek/telegram-forwarder](https://github.com/DrAbdulmalek/telegram-forwarder)
                - Issues: افتح issue في المستودع للمشاكل
                """)

        # ============== Event Handlers ==============

        async def do_send_code(api_id_val, api_hash_val, phone_val):
            """Step 1: Send verification code."""
            global forwarder_instance

            if not api_id_val or not api_hash_val:
                return gr.update(visible=False), "أدخل API ID و API Hash", False, gr.update(visible=False)

            if not phone_val or not phone_val.startswith("+"):
                return gr.update(visible=False), "أدخل رقم هاتف صالح مع كود الدولة (مثال: +966...)", False, gr.update(visible=False)

            try:
                # Disconnect existing instance if any
                if forwarder_instance:
                    try:
                        await forwarder_instance.disconnect()
                    except Exception:
                        pass

                forwarder_instance = TelegramForwarder(int(api_id_val), str(api_hash_val))
                await forwarder_instance.create_client()

                # Check if already authorized (has saved session)
                if await forwarder_instance.is_authorized():
                    return (
                        gr.update(visible=False),
                        "متصل بنجاح (جلسة محفوظة)",
                        True,
                        gr.update(visible=False)
                    )

                # Send code
                await forwarder_instance.send_code(str(phone_val))
                return (
                    gr.update(visible=True),   # show code step
                    "تم إرسال الكود! تحقق من تطبيق Telegram",
                    False,                      # not fully connected yet
                    gr.update(visible=False)    # 2FA hidden by default
                )

            except Exception as e:
                return gr.update(visible=False), f"فشل: {str(e)}", False, gr.update(visible=False)

        async def do_verify_code(code_val, password_val):
            """Step 2: Verify code and complete login."""
            global forwarder_instance

            if not forwarder_instance:
                return "خطأ: أعد إرسال الكود أولاً", False

            if not code_val:
                return "أدخل كود التحقق", False

            try:
                await forwarder_instance.verify_code(
                    code=str(code_val).strip(),
                    password=password_val if password_val else None
                )
                return "متصل بنجاح!", True
            except ValueError as e:
                if "2FA_PASSWORD_REQUIRED" in str(e):
                    return "حسابك مفعل التحقق الثنائي — أدخل كلمة المرور أعلاه", False
                return f"خطأ: {str(e)}", False
            except Exception as e:
                return f"فشل التحقق: {str(e)}", False

        async def do_disconnect():
            """Handle disconnect button."""
            global forwarder_instance
            if forwarder_instance:
                await forwarder_instance.disconnect()
                forwarder_instance = None
            return (
                gr.update(visible=False),  # hide code step
                "غير متصل",
                False,
                gr.update(visible=False)
            )

        async def do_refresh():
            """Refresh channel list."""
            global forwarder_instance
            if not forwarder_instance or not await forwarder_instance.is_authorized():
                return gr.update(choices=[]), gr.update(choices=[]), "غير متصل — سجل دخول أولاً"

            try:
                dialogs = await forwarder_instance.get_dialogs()
                choices = [(f"{d['title']} ({d['type']})", str(d['id'])) for d in dialogs]
                return gr.update(choices=choices), gr.update(choices=choices), f"تم تحميل {len(dialogs)} قناة"
            except Exception as e:
                return gr.update(choices=[]), gr.update(choices=[]), f"خطأ: {str(e)}"

        async def do_forward(limit_val, delay_val, start_id_val, end_id_val,
                            media_only_val, text_only_val, skip_forwards_val,
                            filter_text_val, source_val, dest_val,
                            source_manual_val, dest_manual_val):
            """Handle forward button."""
            global forwarder_instance

            if not forwarder_instance:
                return {"error": "غير متصل"}, "غير متصل", 0

            # Determine source and dest
            source = source_manual_val or source_val
            dest = dest_manual_val or dest_val

            if not source or not dest:
                return {"error": "اختر القنوات المصدر والوجهة"}, "اختر القنوات", 0

            config = ForwardConfig(
                source_channel=source,
                dest_channel=dest,
                limit=int(limit_val),
                delay=float(delay_val),
                media_only=media_only_val,
                text_only=text_only_val,
                skip_forwards=skip_forwards_val,
                filter_text=filter_text_val if filter_text_val else None,
                start_id=int(start_id_val) if start_id_val > 0 else None,
                end_id=int(end_id_val) if end_id_val > 0 else None
            )

            try:
                results = await forwarder_instance.forward_content(config)
                return results, f"تم: {results['success']} | فشل: {results['failed']} | تخطى: {results['skipped']}", 100
            except Exception as e:
                return {"error": str(e)}, f"خطأ: {str(e)}", 0

        async def do_cancel():
            """Handle cancel button."""
            global forwarder_instance
            if forwarder_instance:
                forwarder_instance.cancel()
            return "تم الإلغاء"

        # ============== Wire Events ==============

        # Step 1: Send code
        send_code_btn.click(
            do_send_code,
            inputs=[api_id, api_hash, phone],
            outputs=[code_step, login_status, state_connected, two_fa_pass]
        )

        # Step 2: Verify code
        verify_btn.click(
            do_verify_code,
            inputs=[login_code, two_fa_pass],
            outputs=[login_status, state_connected]
        )

        # Disconnect
        disconnect_btn.click(
            do_disconnect,
            outputs=[code_step, login_status, state_connected, two_fa_pass]
        )

        # Refresh channels
        refresh_btn.click(
            do_refresh,
            outputs=[source_list, dest_list, login_status]
        )

        # Start forward
        start_btn.click(
            do_forward,
            inputs=[limit, delay, start_id, end_id, media_only, text_only,
                   skip_forwards, filter_text, source_list, dest_list,
                   source_manual, dest_manual],
            outputs=[stats_json, progress_text, progress_bar]
        )

        # Cancel
        cancel_btn.click(do_cancel, outputs=[progress_text])

    return app


# Create and launch
app = create_app()

if __name__ == "__main__":
    app.launch(server_name="0.0.0.0", server_port=7860)