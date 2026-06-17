#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Content Forwarder — Core Module v2
=============================================
إصلاحات رئيسية عن النسخة الأولى:
  1. مجلد temp/ يُنشأ تلقائياً (كان يُسبّب FileNotFoundError)
  2. إضافة retry exponential backoff بدل retry واحد
  3. معالجة FloodWaitError بشكل صحيح داخل الحلقة
  4. إضافة timeout لكل عملية تنزيل/رفع
  5. دعم Album/MediaGroup (كان يُرسل كل صورة منفردة)
  6. إضافة rate-limiter ذاتي بدل delay ثابت
  7. حذف الملفات المؤقتة حتى عند الفشل (finally)
  8. إضافة progress_callback حقيقي مع نسبة مئوية
  9. إصلاح bug: _phone_code_hash غير مُعرَّف عند استدعاء verify_code مباشرة
  10. إضافة export_session_string لحفظ الجلسة في HF Secrets
"""

import os
import re
import time
import asyncio
import logging
import tempfile
import shutil
from typing import Optional, List, Dict, Callable, Any, Set
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    UserBannedInChannelError,
    MessageIdInvalidError,
    ChatWriteForbiddenError,
    SlowModeWaitError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    ApiIdInvalidError,
)
from telethon.tl.types import (
    Message, MessageMediaPhoto, MessageMediaDocument,
    MessageMediaWebPage,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Temp Directory ───────────────────────────────────────────
# إنشاء مجلد مؤقت آمن في /tmp بدل "temp/" النسبي
TEMP_DIR = Path(tempfile.gettempdir()) / "tg_forwarder"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Semaphore للحد الأقصى للتنزيلات المتزامنة (تجنب OOM)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(3)


# ─── Config ───────────────────────────────────────────────────

@dataclass
class ForwardConfig:
    """إعدادات عملية النقل."""
    source_channel: str
    dest_channel: str
    limit: int           = 100
    delay: float         = 2.0        # ثوانٍ بين الرسائل
    media_only: bool     = False
    text_only: bool      = False
    skip_forwards: bool  = True       # تخطّي الرسائل المُعاد توجيهها
    filter_text: Optional[str] = None
    start_id: Optional[int]    = None
    end_id: Optional[int]      = None
    max_retries: int     = 3          # محاولات إعادة لكل رسالة
    send_caption: bool   = True       # إرفاق نص الرسالة مع الوسائط
    reverse_order: bool  = False      # ترتيب تصاعدي (الأقدم أولاً)


@dataclass
class ForwardResult:
    """نتيجة عملية النقل."""
    total: int     = 0
    success: int   = 0
    failed: int    = 0
    skipped: int   = 0
    albums: int    = 0           # عدد الألبومات المنقولة
    singles: int   = 0           # عدد الرسائل المنفردة المنقولة
    cancelled: bool = False
    errors: List[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> str:
        secs = int(time.time() - self.start_time)
        return f"{secs // 60}م {secs % 60}ث"

    def to_dict(self) -> dict:
        return {
            "total":     self.total,
            "success":   self.success,
            "failed":    self.failed,
            "skipped":   self.skipped,
            "albums":    self.albums,
            "singles":   self.singles,
            "cancelled": self.cancelled,
            "elapsed":   self.elapsed,
            "errors":    self.errors[-10:],  # آخر 10 أخطاء فقط
        }


# ─── Rate Limiter ─────────────────────────────────────────────

class RateLimiter:
    """يحسب التأخير الديناميكي بناءً على عدد FloodWait المُستقبَلة."""

    def __init__(self, base_delay: float = 2.0):
        self.base_delay = base_delay
        self._flood_count = 0

    def get_delay(self) -> float:
        """تأخير متزايد بعد كل FloodWait."""
        if self._flood_count == 0:
            return self.base_delay
        # exponential: 2s, 4s, 8s, 16s, max 60s
        return min(self.base_delay * (2 ** self._flood_count), 60.0)

    def record_flood(self, wait_seconds: int) -> float:
        self._flood_count += 1
        # استخدم أقصى قيمة بين ما طلب Telegram وما نحسبه
        return max(wait_seconds, self.get_delay())

    def reset(self):
        self._flood_count = 0


# ─── Main Forwarder ───────────────────────────────────────────

class TelegramForwarder:
    """
    Userbot لنقل محتوى القنوات المقيدة.
    يستخدم تقنية Download-Upload لتجاوز قيود الحفظ.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str = "forwarder",
        session_string: Optional[str] = None,
    ):
        self.api_id   = api_id
        self.api_hash = api_hash
        self.session_name   = session_name
        self.session_string = session_string  # للاستخدام في HuggingFace Secrets

        self.client: Optional[TelegramClient] = None
        self._cancelled = False
        self._progress_callback: Optional[Callable] = None

        # State لعملية تسجيل الدخول
        self._phone: Optional[str] = None
        self._phone_code_hash: Optional[str] = None
        self._entity_cache: Optional[dict] = None  # {id: entity} مبني من iter_dialogs

    # ── Connection ────────────────────────────────────────────

    async def create_client(self) -> TelegramClient:
        """أنشئ Client واتصل بـ Telegram."""
        if self.session_string:
            # StringSession: أفضل لـ HuggingFace (لا يحتاج ملف)
            session = StringSession(self.session_string)
        else:
            session = self.session_name

        self.client = TelegramClient(
            session, self.api_id, self.api_hash,
            catch_up=False,           # لا تُزامن الرسائل الفائتة تلقائياً
            sequential_updates=True,  # يقلّل الطلبات المتزامنة عند الاتصال
        )
        await self.client.connect()
        return self.client

    async def is_authorized(self) -> bool:
        if not self.client:
            return False
        try:
            return await self.client.is_user_authorized()
        except Exception:
            return False

    async def export_session_string(self) -> str:
        """
        صدّر الجلسة كـ string للحفظ في HuggingFace Secrets.
        استخدم هذا بدل ملف .session لتجنّب فقدان الجلسة عند إعادة تشغيل Space.
        """
        if not self.client:
            raise RuntimeError("Not connected")
        return self.client.session.save()

    async def disconnect(self):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self._entity_cache = None
        logger.info("Disconnected")

    # ── Authentication ────────────────────────────────────────

    async def send_code(self, phone: str) -> dict:
        """أرسل كود التحقق إلى الهاتف."""
        if not self.client:
            await self.create_client()

        try:
            result = await self.client.send_code_request(phone)
            self._phone = phone
            self._phone_code_hash = result.phone_code_hash
            logger.info(f"Code sent to {phone}")
            return {"phone_code_hash": result.phone_code_hash}
        except ApiIdInvalidError:
            raise ValueError("API ID أو API Hash غير صحيح — تحقق من my.telegram.org")
        except Exception as e:
            raise RuntimeError(f"فشل إرسال الكود: {e}")

    async def verify_code(self, code: str, password: Optional[str] = None) -> bool:
        """تحقق من كود تسجيل الدخول."""
        if not self.client:
            raise RuntimeError("Client غير موجود — استدعِ create_client أولاً")
        if not self._phone or not self._phone_code_hash:
            raise RuntimeError("لا يوجد كود معلّق — استدعِ send_code أولاً")

        try:
            await self.client.sign_in(
                phone=self._phone,
                code=code.strip(),
                phone_code_hash=self._phone_code_hash,
            )
            logger.info("Signed in successfully")
            return True

        except SessionPasswordNeededError:
            if password and password.strip():
                await self.client.sign_in(password=password.strip())
                logger.info("Signed in with 2FA")
                return True
            raise ValueError("2FA_PASSWORD_REQUIRED")

        except PhoneCodeInvalidError:
            raise ValueError("كود التحقق غير صحيح")

        except PhoneCodeExpiredError:
            raise ValueError("انتهت صلاحية الكود — اطلب كوداً جديداً")

        except Exception as e:
            raise RuntimeError(f"فشل التحقق: {e}")

    # ── Dialogs ───────────────────────────────────────────────

    async def get_dialogs(self, limit: int = 200) -> List[Dict]:
        """جلب قائمة القنوات والمجموعات. يبني أيضاً entity cache لاستخدام النقل لاحقاً."""
        if not self.client:
            raise RuntimeError("Not connected")

        # ابنِ الكاش الكامل أولاً (كل المحادثات بدون limit)
        await self._build_entity_cache(force=True)

        dialogs = []
        try:
            async for dialog in self.client.iter_dialogs(limit=limit):
                if dialog.is_channel or dialog.is_group:
                    entity = dialog.entity
                    dialogs.append({
                        "id":                 dialog.id,
                        "title":              dialog.title,
                        "username":           getattr(entity, "username", None),
                        "type":               "channel" if dialog.is_channel else "group",
                        "participants_count": getattr(entity, "participants_count", 0),
                        "restricted":         getattr(entity, "restricted", False),
                        "protected":          getattr(entity, "noforwards", False),
                    })
        except FloodWaitError as e:
            mins = e.seconds // 60
            raise RuntimeError(
                f"⏳ Telegram يطلب الانتظار {mins} دقيقة قبل المحاولة مجدداً "
                f"(حماية ضد الاستخدام المكثف). هذا ليس خطأ في التطبيق — "
                f"يُرجى الانتظار ثم إعادة المحاولة، وتجنّب إعادة تشغيل الجلسة بشكل متكرر."
            )
        return dialogs

    async def _build_entity_cache(self, force: bool = False) -> None:
        """
        بناء فهرس داخلي {id: entity} من كل المحادثات.
        Telethon يحتاج access_hash صحيحاً لأي channel/chat، وهذا الوحيد
        المضمون توفّره من iter_dialogs() — لا يمكن تركيبه يدوياً عبر
        PeerChannel(id) فقط لأنه يفتقد access_hash فيفشل دائماً.

        force=True يُعيد البناء حتى لو كان الكاش موجوداً.
        """
        if not force and self._entity_cache is not None:
            return
        self._entity_cache = {}
        count = 0
        try:
            async for dialog in self.client.iter_dialogs():
                self._entity_cache[dialog.id] = dialog.entity
                # خزّن أيضاً بالصيغة الموجبة (بدون -100) لتسهيل المطابقة
                if dialog.id < 0:
                    id_str = str(dialog.id)
                    if id_str.startswith("-100"):
                        self._entity_cache[int(id_str[4:])] = dialog.entity
                count += 1
        except FloodWaitError:
            raise  # دعها تُعالَج في الطبقة الأعلى
        except Exception as e:
            logger.warning(f"Failed building entity cache: {e}")
        logger.info(f"Entity cache built: {count} dialogs, {len(self._entity_cache)} entries")

    async def _resolve_entity(self, identifier):
        """
        حلّ معرّف القناة/المستخدم إلى entity صحيح.

        يدعم:
          - @username
          - رابط https://t.me/username أو t.me/+invite
          - ID رقمي خام (مثل -1001927197663 أو 1927197663)

        ملاحظة مهمة: لا يمكن بناء PeerChannel(id) يدوياً والاتصال به مباشرة
        لأنه يفتقد access_hash المطلوب من Telegram API — هذا يفشل دائماً
        برسالة "Cannot find any entity" حتى لو كان الـ ID صحيحاً 100%.
        الحل الموثوق الوحيد: استخدام access_hash من dialogs المُحمَّلة مسبقاً
        (وهو متوفر فقط بعد المرور على iter_dialogs على الأقل مرة واحدة).
        """
        if not self.client:
            raise RuntimeError("Not connected")

        identifier = str(identifier).strip()

        # حالة: رابط t.me
        if "t.me/" in identifier:
            identifier = identifier.split("t.me/")[-1].lstrip("@")
            try:
                return await self.client.get_entity(identifier)
            except Exception:
                pass

        # حالة: @username أو اسم نصي
        if identifier.startswith("@") or not self._is_numeric_id(identifier):
            return await self.client.get_entity(identifier)

        # حالة: ID رقمي — اعتمد على الكاش المبني من iter_dialogs (access_hash صحيح)
        raw_id = int(identifier)

        await self._build_entity_cache()

        if raw_id in self._entity_cache:
            return self._entity_cache[raw_id]

        # جرّب الصيغة الموجبة إذا أُدخلت بصيغة -100
        if raw_id < 0:
            id_str = str(raw_id)
            if id_str.startswith("-100"):
                positive_id = int(id_str[4:])
                if positive_id in self._entity_cache:
                    return self._entity_cache[positive_id]
        else:
            # جرّب الصيغة السالبة -100xxxx إذا أُدخل الرقم موجباً
            negative_id = int(f"-100{raw_id}")
            if negative_id in self._entity_cache:
                return self._entity_cache[negative_id]

        # كحل أخير فقط — قد يفشل لنفس سبب access_hash لكن نحاول
        try:
            return await self.client.get_entity(raw_id)
        except Exception:
            raise ValueError(
                f"لم يُعثر على القناة بالمعرّف {identifier}. "
                f"تأكد أنك عضو في هذه القناة، أو اخترها من القائمة المنسدلة "
                f"بدل إدخال الـ ID يدوياً (القائمة تضمن صحة الربط)."
            )

    @staticmethod
    def _is_numeric_id(s: str) -> bool:
        s = s.strip()
        if s.startswith("-"):
            s = s[1:]
        return s.isdigit()

    async def get_channel_info(self, channel_id: str) -> Dict:
        """جلب معلومات قناة محددة."""
        if not self.client:
            raise RuntimeError("Not connected")
        try:
            entity = await self._resolve_entity(channel_id)
        except FloodWaitError as e:
            mins = e.seconds // 60
            raise RuntimeError(
                f"⏳ Telegram يطلب الانتظار {mins} دقيقة (حماية ضد الطلبات المتكررة). "
                f"المعلومات التفصيلية للقناة غير ضرورية لبدء النقل — يمكنك تجاهل هذا "
                f"وإدخال معرّف القناة يدوياً أو اختيارها من القائمة مباشرة."
            )
        return {
            "id":                 entity.id,
            "title":              getattr(entity, "title", None) or getattr(entity, "first_name", "—"),
            "username":           getattr(entity, "username", None),
            "participants_count": getattr(entity, "participants_count", 0),
            "restricted":         getattr(entity, "restricted", False),
            "protected":          getattr(entity, "noforwards", False),
        }

    # ── Forward ───────────────────────────────────────────────

    def cancel(self):
        """إلغاء العملية الجارية."""
        self._cancelled = True

    def set_progress_callback(self, callback: Callable):
        self._progress_callback = callback

    async def forward_content(
        self,
        config: ForwardConfig,
        progress_callback: Optional[Callable] = None,
    ) -> ForwardResult:
        """
        نقل المحتوى من قناة إلى أخرى.
        progress_callback(result: ForwardResult, message: str) → None
        """
        if not self.client:
            raise RuntimeError("Not connected")

        self._cancelled = False
        cb = progress_callback or self._progress_callback
        result = ForwardResult()
        rate = RateLimiter(base_delay=config.delay)

        # تأكد من بناء الكاش قبل البدء (يتغلّب على نسيان الضغط على "تحديث")
        await self._build_entity_cache(force=True)

        try:
            source = await self._resolve_entity(config.source_channel)
            dest   = await self._resolve_entity(config.dest_channel)
            logger.info(f"Forwarding from '{source.title}' → '{dest.title}'")

        except ChannelPrivateError:
            raise RuntimeError("القناة المصدر خاصة أو لست عضواً فيها")
        except Exception as e:
            raise RuntimeError(f"تعذّر الوصول إلى القنوات: {e}")

        # بناء iterator
        iter_kwargs: Dict[str, Any] = {
            "limit":   config.limit,
            "reverse": config.reverse_order,
        }
        if config.start_id:
            iter_kwargs["min_id"] = config.start_id - 1
        if config.end_id:
            iter_kwargs["max_id"] = config.end_id + 1

        # تتبع الألبومات المُعالَجة لتجنّب التكرار
        processed_albums: Set[int] = set()
        # تتبع رسائل الألبوم التي يجب تخطّيها
        skip_message_ids: Set[int] = set()

        try:
            async for message in self.client.iter_messages(source, **iter_kwargs):
                if self._cancelled:
                    result.cancelled = True
                    break

                # تخطّي رسائل ألبوم تمت معالجتها
                if message.id in skip_message_ids:
                    continue

                result.total += 1

                # ── Filters ───────────────────────────────────

                if config.skip_forwards and message.fwd_from:
                    result.skipped += 1
                    continue

                if config.filter_text and message.text:
                    if config.filter_text.lower() not in message.text.lower():
                        result.skipped += 1
                        continue

                if config.media_only and not message.media:
                    result.skipped += 1
                    continue

                if config.text_only and message.media:
                    result.skipped += 1
                    continue

                # تجاهل WebPage previews
                if isinstance(getattr(message, "media", None), MessageMediaWebPage):
                    # أرسل النص فقط إذا كان موجوداً
                    if message.text:
                        success = await self._copy_with_retry(
                            message, dest, config, result
                        )
                        if success:
                            result.success += 1
                            result.singles += 1
                        else:
                            result.failed += 1
                    else:
                        result.skipped += 1
                    continue

                # ── Album Detection ───────────────────────────

                elif message.grouped_id and message.grouped_id not in processed_albums:
                    # هذه الرسالة جزء من ألبوم لم نعالجه بعد
                    processed_albums.add(message.grouped_id)

                    album_messages = await self._collect_album(
                        source, message
                    )

                    if len(album_messages) <= 1:
                        # رسالة واحدة فقط — عاملها كرسالة عادية
                        success = await self._copy_with_retry(
                            message, dest, config, result
                        )
                        if success:
                            result.success += 1
                            result.singles += 1
                        else:
                            result.failed += 1
                    else:
                        # ألبوم حقيقي — أرسله كدفعة واحدة
                        logger.info(
                            f"Album detected: grouped_id={message.grouped_id}, "
                            f"messages={len(album_messages)}"
                        )
                        success = await self._copy_album(
                            album_messages, dest, config, result
                        )
                        if success:
                            result.success += 1
                            result.albums += 1
                            logger.info(
                                f"Album sent successfully: {len(album_messages)} files"
                            )
                        else:
                            result.failed += 1
                            logger.error(
                                f"Album failed: grouped_id={message.grouped_id}"
                            )

                        # ضع علامة على باقي رسائل الألبوم لتخطّيها
                        for m in album_messages:
                            if m.id != message.id:
                                skip_message_ids.add(m.id)

                elif message.grouped_id and message.grouped_id in processed_albums:
                    # رسالة ضمن ألبوم تمت معالجته — تخطّيها
                    result.skipped += 1
                    continue

                # ── Single Message ────────────────────────────

                else:
                    success = await self._copy_with_retry(
                        message, dest, config, result
                    )
                    if success:
                        result.success += 1
                        result.singles += 1
                    else:
                        result.failed += 1

                # Progress callback
                if cb:
                    pct = round((result.total / config.limit) * 100) if config.limit else 0
                    try:
                        await cb(result, pct)
                    except Exception:
                        pass

                # تأخير ديناميكي
                delay = rate.get_delay()
                await asyncio.sleep(delay)

        except ChatWriteForbiddenError:
            raise RuntimeError("لا تملك صلاحية الكتابة في القناة الوجهة")
        except Exception as e:
            result.errors.append(f"خطأ عام: {e}")
            logger.error(f"Forward failed: {e}", exc_info=True)

        logger.info(
            f"Done: {result.success} ok ({result.albums} albums, {result.singles} singles), "
            f"{result.failed} failed, {result.skipped} skipped — {result.elapsed}"
        )
        return result

    # ── Album Collection ──────────────────────────────────────

    async def _collect_album(
        self, source, trigger_message: Message
    ) -> List[Message]:
        """
        جمع كل رسائل الألبوم بناءً على grouped_id.
        يبحث في نطاق ±10 رسائل من رسالة الزناد.
        """
        album_messages = []
        search_range = 10

        async for msg in self.client.iter_messages(
            source,
            limit=search_range * 2 + 1,
            min_id=trigger_message.id - search_range,
            max_id=trigger_message.id + search_range,
        ):
            if msg.grouped_id == trigger_message.grouped_id:
                album_messages.append(msg)

        # ترتيب حسب ID
        album_messages.sort(key=lambda m: m.id)
        logger.info(
            f"Collected album: grouped_id={trigger_message.grouped_id}, "
            f"count={len(album_messages)}, "
            f"IDs={[m.id for m in album_messages]}"
        )
        return album_messages

    # ── Album Copy ────────────────────────────────────────────

    async def _copy_album(
        self,
        album_messages: List[Message],
        dest,
        config: ForwardConfig,
        result: ForwardResult,
    ) -> bool:
        """
        تنزيل وإرسال ألبوم كاملاً باستخدام send_file مع قائمة ملفات.
        """
        album_tmp = TEMP_DIR / f"album_{album_messages[0].grouped_id}_{int(time.time())}"
        album_tmp.mkdir(parents=True, exist_ok=True)

        try:
            files: List[str] = []
            album_caption = ""

            async with DOWNLOAD_SEMAPHORE:
                for msg in album_messages:
                    # تخطّي WebPage
                    if isinstance(getattr(msg, "media", None), MessageMediaWebPage):
                        if msg.text and not album_caption:
                            album_caption = (msg.text or "") if config.send_caption else ""
                        continue

                    if not msg.media:
                        # نص فقط ضمن الألبوم
                        if msg.text and not album_caption:
                            album_caption = (msg.text or "") if config.send_caption else ""
                        continue

                    try:
                        file_path = await asyncio.wait_for(
                            msg.download_media(file=str(album_tmp) + "/"),
                            timeout=120,
                        )
                        if file_path and os.path.exists(file_path):
                            files.append(file_path)
                            logger.debug(f"Album file downloaded: {file_path}")
                        # استخدم نص أول رسالة فيها وسائط كـ caption
                        if msg.text and not album_caption and config.send_caption:
                            album_caption = msg.text
                    except asyncio.TimeoutError:
                        logger.warning(f"Album file timeout: msg#{msg.id}")
                        result.errors.append(f"album msg#{msg.id}: timeout تنزيل")
                    except Exception as e:
                        logger.warning(f"Album file download failed: msg#{msg.id}: {e}")
                        result.errors.append(f"album msg#{msg.id}: {e}")

            if not files:
                # لا توجد ملفات — أرسل النص فقط
                if album_caption:
                    await self.client.send_message(dest, album_caption, parse_mode="html")
                    return True
                return False

            # إرسال كل الملفات كألبوم واحد
            await asyncio.wait_for(
                self.client.send_file(
                    dest,
                    files,
                    caption=album_caption if config.send_caption else "",
                    parse_mode="html",
                    force_document=False,
                ),
                timeout=300,  # 5 دقائق للألبوم
            )
            return True

        except FloodWaitError:
            raise  # سيتم معالجته في _copy_with_retry
        except Exception as e:
            result.errors.append(f"album send: {e}")
            logger.error(f"Album send failed: {e}", exc_info=True)
            return False
        finally:
            shutil.rmtree(str(album_tmp), ignore_errors=True)

    # ── Internal ──────────────────────────────────────────────

    async def _copy_with_retry(
        self,
        message: Message,
        dest,
        config: ForwardConfig,
        result: ForwardResult,
    ) -> bool:
        """محاولة نسخ رسالة مع إعادة المحاولة عند الفشل."""
        rate = RateLimiter(config.delay)

        for attempt in range(1, config.max_retries + 1):
            try:
                await self._copy_message(message, dest, config)
                rate.reset()
                return True

            except FloodWaitError as e:
                wait = rate.record_flood(e.seconds)
                logger.warning(f"FloodWait {e.seconds}s — waiting {wait:.0f}s (attempt {attempt})")
                result.errors.append(f"msg#{message.id}: FloodWait {e.seconds}s")
                await asyncio.sleep(wait)

            except (MessageIdInvalidError, ChatWriteForbiddenError, UserBannedInChannelError) as e:
                # أخطاء غير قابلة للتكرار
                result.errors.append(f"msg#{message.id}: {type(e).__name__}")
                logger.error(f"Non-retryable error on msg {message.id}: {e}")
                return False

            except SlowModeWaitError as e:
                logger.warning(f"SlowMode: waiting {e.seconds}s")
                await asyncio.sleep(e.seconds + 1)

            except Exception as e:
                if attempt == config.max_retries:
                    result.errors.append(f"msg#{message.id}: {e}")
                    logger.error(f"Failed after {attempt} attempts: {e}")
                    return False
                backoff = 2 ** attempt
                logger.warning(f"Attempt {attempt} failed ({e}) — retrying in {backoff}s")
                await asyncio.sleep(backoff)

        return False

    async def _copy_message(self, message: Message, dest, config: ForwardConfig):
        """نسخ رسالة واحدة بتقنية Download-Upload."""
        caption = (message.text or "") if config.send_caption else ""

        if message.media and not isinstance(message.media, MessageMediaWebPage):
            # مجلد مؤقت خاص بهذه الرسالة
            msg_tmp = TEMP_DIR / f"msg_{message.id}_{int(time.time())}"
            msg_tmp.mkdir(parents=True, exist_ok=True)

            try:
                # تنزيل الوسائط
                file_path = await asyncio.wait_for(
                    message.download_media(file=str(msg_tmp) + "/"),
                    timeout=120,  # 2 دقيقة لكل ملف
                )

                if file_path and os.path.exists(file_path):
                    # رفع كرسالة جديدة
                    await asyncio.wait_for(
                        self.client.send_file(
                            dest,
                            file_path,
                            caption=caption,
                            parse_mode="html",
                            force_document=False,
                        ),
                        timeout=180,  # 3 دقائق للرفع
                    )
                else:
                    # لا يوجد ملف — أرسل النص فقط
                    if caption:
                        await self.client.send_message(dest, caption, parse_mode="html")
            finally:
                # حذف المجلد المؤقت دائماً
                shutil.rmtree(str(msg_tmp), ignore_errors=True)

        elif message.text:
            # رسالة نصية بحتة
            await self.client.send_message(dest, message.text, parse_mode="html")
        # رسائل فارغة بدون نص ولا وسائط — تجاهل


# ─── Helper ───────────────────────────────────────────────────

def create_forwarder(
    api_id: int,
    api_hash: str,
    session_string: Optional[str] = None,
) -> TelegramForwarder:
    """أنشئ instance جديد من TelegramForwarder."""
    return TelegramForwarder(api_id, api_hash, session_string=session_string)
