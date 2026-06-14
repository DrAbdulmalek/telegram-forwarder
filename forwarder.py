#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Content Forwarder - Core Module
==========================================
Uses Telethon Userbot to bypass "Restrict Saving Content" by
using download-upload technique instead of native forwarding.
"""

import os
import re
import time
import asyncio
import logging
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass
from datetime import datetime

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, ChannelPrivateError, UserBannedInChannelError,
    MessageIdInvalidError, ChatWriteForbiddenError, SlowModeWaitError,
    SessionPasswordNeededError
)
from telethon.tl.types import Message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class ForwardConfig:
    """Configuration for forwarding operation."""
    source_channel: str
    dest_channel: str
    limit: int = 100
    delay: float = 2.0
    media_only: bool = False
    text_only: bool = False
    skip_forwards: bool = True
    filter_text: Optional[str] = None
    start_id: Optional[int] = None
    end_id: Optional[int] = None


class TelegramForwarder:
    """Main forwarder class using Telethon Userbot."""

    def __init__(self, api_id: int, api_hash: str, session_name: str = "forwarder"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.client: Optional[TelegramClient] = None
        self._cancelled = False
        self._progress_callback: Optional[Callable] = None

    async def create_client(self):
        """Create and connect TelegramClient without authorization."""
        self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)
        await self.client.connect()
        return self.client

    async def is_authorized(self) -> bool:
        """Check if the session is already authorized."""
        if not self.client:
            return False
        return await self.client.is_user_authorized()

    async def send_code(self, phone: str) -> dict:
        """Send verification code to phone number. Returns phone_code_hash."""
        if not self.client:
            await self.create_client()
        result = await self.client.send_code_request(phone)
        self._phone = phone
        self._phone_code_hash = result.phone_code_hash
        logger.info(f"Code sent to {phone}, hash: {result.phone_code_hash}")
        return {"phone_code_hash": result.phone_code_hash}

    async def verify_code(self, code: str, password: str = None) -> bool:
        """Verify the login code. Optionally provide 2FA password."""
        if not self.client or not self._phone_code_hash:
            raise RuntimeError("No pending code request. Call send_code first.")
        try:
            await self.client.sign_in(
                phone=self._phone,
                code=code,
                phone_code_hash=self._phone_code_hash
            )
        except SessionPasswordNeededError:
            if password:
                await self.client.sign_in(password=password)
            else:
                raise ValueError("2FA_PASSWORD_REQUIRED")
        logger.info("Signed in successfully")
        return True

    async def connect(self, phone: str = None, code_callback = None):
        """Connect to Telegram (legacy method for backward compatibility)."""
        await self.create_client()
        if await self.client.is_user_authorized():
            logger.info("Connected to Telegram successfully (existing session)")
            return True
        if phone and code_callback:
            await self.send_code(phone)
            code = await code_callback()
            await self.verify_code(code)
            return True
        raise ValueError("Phone and code callback required for first login")

    async def disconnect(self):
        """Disconnect from Telegram."""
        if self.client:
            await self.client.disconnect()
            logger.info("Disconnected from Telegram")

    def set_progress_callback(self, callback: Callable):
        """Set callback for progress updates."""
        self._progress_callback = callback

    def cancel(self):
        """Cancel ongoing operation."""
        self._cancelled = True

    async def get_dialogs(self) -> List[Dict]:
        """Get list of available dialogs (channels/groups)."""
        if not self.client:
            raise RuntimeError("Not connected")

        dialogs = []
        async for dialog in self.client.iter_dialogs():
            if dialog.is_channel or dialog.is_group:
                dialogs.append({
                    'id': dialog.id,
                    'title': dialog.title,
                    'username': dialog.entity.username if hasattr(dialog.entity, 'username') else None,
                    'type': 'channel' if dialog.is_channel else 'group',
                    'participants_count': getattr(dialog.entity, 'participants_count', 0)
                })
        return dialogs

    async def forward_content(self, config: ForwardConfig) -> Dict:
        """
        Forward content using download-upload technique.
        This bypasses 'Restrict Saving Content' by re-uploading as new message.
        """
        if not self.client:
            raise RuntimeError("Not connected")

        self._cancelled = False
        results = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'errors': []
        }

        try:
            # Resolve source and destination
            source = await self.client.get_entity(config.source_channel)
            dest = await self.client.get_entity(config.dest_channel)

            logger.info(f"Starting forward from {source.title} to {dest.title}")

            # Build message iterator
            kwargs = {'limit': config.limit}
            if config.start_id:
                kwargs['min_id'] = config.start_id - 1
            if config.end_id:
                kwargs['max_id'] = config.end_id + 1

            message_iter = self.client.iter_messages(source, **kwargs)

            async for message in message_iter:
                if self._cancelled:
                    logger.info("Operation cancelled by user")
                    break

                results['total'] += 1

                # Skip forwarded messages if configured
                if config.skip_forwards and message.fwd_from:
                    results['skipped'] += 1
                    continue

                # Filter by text content
                if config.filter_text and message.text:
                    if config.filter_text.lower() not in message.text.lower():
                        results['skipped'] += 1
                        continue

                # Filter by media type
                if config.media_only and not message.media:
                    results['skipped'] += 1
                    continue
                if config.text_only and message.media:
                    results['skipped'] += 1
                    continue

                try:
                    # Use download-upload technique to bypass restrictions
                    await self._copy_message(message, dest)
                    results['success'] += 1

                    # Progress callback
                    if self._progress_callback:
                        await self._progress_callback(results)

                except FloodWaitError as e:
                    wait_time = e.seconds
                    logger.warning(f"FloodWait: sleeping for {wait_time}s")
                    if self._progress_callback:
                        await self._progress_callback(results, f"Waiting {wait_time}s due to rate limit...")
                    await asyncio.sleep(wait_time)
                    # Retry once
                    try:
                        await self._copy_message(message, dest)
                        results['success'] += 1
                    except Exception as e2:
                        results['failed'] += 1
                        results['errors'].append(str(e2))

                except Exception as e:
                    results['failed'] += 1
                    results['errors'].append(str(e))
                    logger.error(f"Failed to copy message {message.id}: {e}")

                # Delay between messages
                if config.delay > 0:
                    await asyncio.sleep(config.delay)

            return results

        except ChannelPrivateError:
            raise RuntimeError("Cannot access source channel. Make sure you are a member.")
        except ChatWriteForbiddenError:
            raise RuntimeError("Cannot write to destination channel. Check permissions.")
        except Exception as e:
            raise RuntimeError(f"Forward failed: {e}")

    async def _copy_message(self, message: Message, dest):
        """Copy message using download-upload technique."""
        if message.media:
            # Download and re-upload media
            file_path = await message.download_media(file="temp/")
            if file_path:
                try:
                    await self.client.send_file(
                        dest,
                        file_path,
                        caption=message.text or "",
                        parse_mode='html'
                    )
                finally:
                    # Clean up temp file
                    if os.path.exists(file_path):
                        os.remove(file_path)
            else:
                # Fallback: send text only
                await self.client.send_message(dest, message.text or "")
        else:
            # Text-only message
            await self.client.send_message(dest, message.text or "")

    async def get_channel_info(self, channel_id: str) -> Dict:
        """Get information about a channel."""
        if not self.client:
            raise RuntimeError("Not connected")

        entity = await self.client.get_entity(channel_id)
        return {
            'id': entity.id,
            'title': entity.title,
            'username': entity.username,
            'participants_count': getattr(entity, 'participants_count', 0),
            'restricted': getattr(entity, 'restricted', False),
            'has_protected_content': getattr(entity, 'noforwards', False)
        }


# Helper functions for Gradio interface
def create_forwarder(api_id: int, api_hash: str) -> TelegramForwarder:
    """Create forwarder instance."""
    return TelegramForwarder(api_id, api_hash)