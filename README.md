---
title: Telegram Content Forwarder
emoji: 📨
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# 📨 Telegram Content Forwarder

[![Hugging Face Spaces](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Spaces-blue)](https://huggingface.co/spaces/DrAbdulmalek/telegram-forwarder)
[![GitHub](https://img.shields.io/badge/GitHub-Repository-black?logo=github)](https://github.com/DrAbdulmalek/telegram-forwarder)

نسخ محتوى القنوات المقيدة من النسخ والتحويل باستخدام **Telethon Userbot** وتقنية **التحميل وإعادة الرفع** (Download-Upload).

## ⚠️ تحذيرات مهمة

- **التأخير المنخفض** (< 1 ثانية) قد يؤدي إلى **حظر مؤقت** من Telegram
- **الاستخدام المفرط** قد يؤدي إلى **حظر دائم**
- **احترم حقوق** منشئي المحتوى — استخدم للأغراض الشخصية فقط
- **لا تشارك** ملف الجلسة (`.session`) مع أي شخص
- هذا المشروع **للأغراض التعليمية** فقط

## 🚀 المميزات

| الميزة | الوصف |
|--------|-------|
| 🔓 تجاوز القيود | يتجاوز "Restrict Saving Content" بتقنية تحميل-إعادة-رفع |
| 📱 واجهة سهلة | واجهة Gradio عربية بالكامل |
| 🎛️ تحكم كامل | عدد الرسائل، التأخير، أنواع الوسائط، تصفية النص |
| ⏸️ إيقاف مؤقت | إمكانية الإلغاء في أي وقت |
| 🐳 Docker | جاهز للنشر على Hugging Face Spaces |

## 📦 التثبيت المحلي

```bash
# استنساخ المستودع
git clone https://github.com/DrAbdulmalek/telegram-forwarder.git
cd telegram-forwarder

# إنشاء بيئة افتراضية
python -m venv venv
source venv/bin/activate  # Linux/Mac
# أو: venv\Scripts\activate  # Windows

# تثبيت المكتبات
pip install -r requirements.txt

# تشغيل
python app.py
```

## 🔧 الاستخدام

### 1️⃣ الحصول على API credentials

1. اذهب إلى [my.telegram.org](https://my.telegram.org)
2. سجل دخول بحسابك
3. اذهب إلى **API development tools**
4. أنشئ تطبيق جديد
5. احفظ **API ID** و **API Hash**

### 2️⃣ تسجيل الدخول

- أدخل API ID و API Hash
- أدخل رقم هاتفك مع كود الدولة (مثال: `+966501234567`)
- سيصلك كود على Telegram — أدخله في التطبيق

### 3️⃣ اختيار القنوات

- اضغط "تحديث قائمة القنوات"
- اختر القناة المصدر (المحتوى المقيد)
- اختر القناة الوجهة (قناتك الخاصة)

### 4️⃣ بدء النقل

- حدد عدد الرسائل (1-5000)
- حدد التأخير بين الرسائل (2 ثوانٍ موصى بها)
- اضغط "بدء النقل"

## 🐳 النشر على Hugging Face Spaces

1. أنشئ Space جديد باسم `telegram-forwarder`
2. اختر **Docker** كـ SDK
3. اربطه بهذا المستودع أو ارفع الملفات مباشرة
4. اضغط **Build** — التطبيق يشتغل تلقائياً على بورت `7860`

## 📁 هيكل المشروع

```
telegram-forwarder/
├── app.py              # واجهة Gradio الرئيسية
├── forwarder.py        # منطق النقل الأساسي (Telethon)
├── requirements.txt    # المكتبات المطلوبة
├── Dockerfile          # للنشر على HF Spaces
├── .gitignore          # استبعاد الجلسات والمؤقتات
└── README.md           # هذا الملف
```

## ⚙️ كيف يعمل؟

بدلاً من "إعادة التوجيه" العادية (Forward) التي تُمنعها قيود الحفظ، يستخدم هذا التطبيق تقنية **التحميل وإعادة الرفع** (Download-Upload):

1. يحمل الوسائط (صور/فيديو/ملفات) إلى الجهاز مؤقتاً
2. يرفعها كرسائل جديدة في القناة الوجهة
3. يحذف الملفات المؤقتة تلقائياً

## 🛡️ الأمان

- **لا تُرفع** ملفات `.session` إلى GitHub أبداً
- **غيّر** API credentials بانتظام
- **استخدم** تأخير مناسب (2+ ثواني) لتجنب الحظر
- **راقب** سجلات النشاط في Telegram

## 📞 الدعم

- **Issues**: [github.com/DrAbdulmalek/telegram-forwarder/issues](https://github.com/DrAbdulmalek/telegram-forwarder/issues)
- **Pull Requests**: مرحب بها!

## 📄 الترخيص

هذا المشروع مرخص بموجب [MIT License](LICENSE).

---

**⚠️ إخلاء المسؤولية**: هذا المشروع للأغراض التعليمية فقط. المؤلف غير مسؤول عن أي استخدام غير قانوني أو انتهاك لشروط خدمة Telegram.