#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Content Forwarder — Core Module v2.1
==============================================
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

تحسينات v2.1:
  11. دعم كامل لـ Albums/MediaGroups (جمع الرسائل وإرسالها كألبوم)
  12. asyncio.Semaphore للحد من التنزيلات المتزامنة (تجنب OOM)
  13. تصفية ذكية: تجاهل WebPage، دعم reverse_order
  14. ForwardResult منظّم مع to_dict()
  15. إضافة logging مفصّل للألبومات
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

    # ── Connection ────────────────────────────────────────────

    async def create_client(self) -> TelegramClient:
        """أنشئ Client واتصل بـ Telegram."""
        if self.session_string:
            # StringSession: أفضل لـ HuggingFace (لا يحتاج ملف)
            session = StringSession(self.session_string)
        else:
            session = self.session_name

        self.client = TelegramClient(session, self.api_id, self.api_hash)
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
        """جلب قائمة القنوات والمجموعات."""
        if not self.client:
            raise RuntimeError("Not connected")

        dialogs = []
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
        return dialogs

    def _resolve_entity_input(self, channel_id):
        """تحويل معرّف القناة إلى الصيغة المناسبة لـ get_entity.

        القائمة المنسدلة تُمرّر المعرّفات كنصوص مثل '-1001927197663'.
        Telethon يحتاجها كأرقام (int) ليعرف نوع الكيان.
        """
        if isinstance(channel_id, (int, float)):
            return int(channel_id)
        s = str(channel_id).strip()
        # إذا كان يبدأ بـ @ أو يحرف — مرّره كـ username
        if s.startswith('@') or not s.lstrip('-').isdigit():
            return s
        # معرّف رقمي مثل '-1001927197663' أو '1927197663'
        return int(s)

    async def get_channel_info(self, channel_id: str) -> Dict:
        """جلب معلومات قناة محددة."""
        if not self.client:
            raise RuntimeError("Not connected")
        entity = await self.client.get_entity(self._resolve_entity_input(channel_id))
        return {
            "id":                 entity.id,
            "title":              entity.title,
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

        try:
            source = await self.client.get_entity(self._resolve_entity_input(config.source_channel))
            dest   = await self.client.get_entity(self._resolve_entity_input(config.dest_channel))
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
                async with DOWNLOAD_SEMAPHORE:
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