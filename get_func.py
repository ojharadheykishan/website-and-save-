# safe_repo

import asyncio
import time
import os
import re
import subprocess
import json
import cv2
from datetime import datetime as dt
import logging
from telethon import events, Button
from pyrogram import filters
from pyrogram.errors import ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid, PeerIdInvalid
from pyrogram.enums import MessageMediaType
from pyrogram.types import Message
from safe_repo import app
from safe_repo.core.func import (
    progress_bar, video_metadata, screenshot, humanbytes, parse_telegram_link,
    clean_link, choose_progress_style, choose_theme, choose_spinner,
    choose_status, choose_extra_text, convert, get_link, get_seconds,
    new_progress_session,
)
from safe_repo.core.mongo import db
from config import LOG_GROUP, CLONE_LOG_CHANNEL

# Configure logging
logger = logging.getLogger(__name__)
DOWNLOAD_CREDIT = "Download by Rk 👍😍❤️"


def add_download_credit(caption):
    caption = (caption or "").rstrip()
    if DOWNLOAD_CREDIT.casefold() in caption.casefold():
        return caption
    if len(caption) + len(DOWNLOAD_CREDIT) + 2 > 1024:
        return caption
    return f"{caption}\n\n{DOWNLOAD_CREDIT}" if caption else DOWNLOAD_CREDIT


async def delete_message_after_1h(chat_id, message_id):
    """Delete a message after 1 hour."""
    await asyncio.sleep(3600)  # 1 hour = 3600 seconds
    try:
        await app.delete_message(chat_id, message_id)
    except Exception as e:
        logger.error(f"Failed to delete message {message_id}: {e}")


async def log_clone_operation(client, message, operation, user_id, file_path=None, caption=None):
    """Log clone operations to the clone log channel."""
    try:
        if file_path and os.path.exists(file_path):
            # Send file copy to clone log channel
            if message.media == MessageMediaType.PHOTO:
                await app.send_photo(
                    chat_id=CLONE_LOG_CHANNEL,
                    photo=file_path,
                    caption=f"📁 **Clone Operation:** {operation}\n👤 **User ID:** {user_id}\n📄 **File:** `{os.path.basename(file_path)}`\n📦 **Size:** {humanbytes(os.path.getsize(file_path))}\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096"
                )
            elif message.media == MessageMediaType.DOCUMENT or message.media == MessageMediaType.VIDEO:
                await app.send_document(
                    chat_id=CLONE_LOG_CHANNEL,
                    document=file_path,
                    caption=f"📁 **Clone Operation:** {operation}\n👤 **User ID:** {user_id}\n📄 **File:** `{os.path.basename(file_path)}`\n📦 **Size:** {humanbytes(os.path.getsize(file_path))}\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096"
                )
            else:
                await app.send_message(
                    chat_id=CLONE_LOG_CHANNEL,
                    text=f"📁 **Clone Operation:** {operation}\n👤 **User ID:** {user_id}\n📄 **File:** `{os.path.basename(file_path)}`\n📦 **Size:** {humanbytes(os.path.getsize(file_path))}\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096"
                )
        elif caption:
            await app.send_message(
                chat_id=CLONE_LOG_CHANNEL,
                text=f"📁 **Clone Operation:** {operation}\n👤 **User ID:** {user_id}\n📄 **Content:** {caption[:200]}...\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096"
            )
    except Exception as e:
        logger.error(f"Failed to log clone operation: {e}")


async def send_alert(client, message, alert_type, details):
    """Send alert to clone log channel."""
    try:
        await app.send_message(
            chat_id=CLONE_LOG_CHANNEL,
            text=f"🚨 **ALERT: {alert_type}**\n👤 **User ID:** {details.get('user_id', 'Unknown')}\n📄 **Details:** {details.get('message', 'N/A')}\n⏰ **Time:** {dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096"
        )
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")


def thumbnail(sender):
    return f'{sender}.jpg' if os.path.exists(f'{sender}.jpg') else None


def media_duration(message):
    video = getattr(message, "video", None)
    if video:
        return getattr(video, "duration", 0) or 0

    document = getattr(message, "document", None)
    for attribute in getattr(document, "attributes", ()):
        duration = getattr(attribute, "duration", None)
        if duration is not None:
            return duration
    return 0


def apply_rename_tag(file_path, user_id):
    if not file_path or not os.path.exists(file_path):
        return file_path
    tag = get_user_rename_preference(user_id)
    if not tag or tag == "safe_repo":
        return file_path
    safe_tag = re.sub(r'[\\/:*?"<>|]+', "_", tag).strip(" .")
    if not safe_tag:
        return file_path
    directory, filename = os.path.split(file_path)
    renamed_path = os.path.join(directory, f"{safe_tag}_{filename}")
    os.replace(file_path, renamed_path)
    return renamed_path


async def get_msg(
    userbot,
    sender,
    edit_id,
    msg_link,
    i,
    message,
    is_batch=False,
    destination_chat_id=None,
    destination_thread_id=None):
    edit = ""
    chat = ""
    round_message = False

    # Strip ?single fragment (kept for backwards compatibility)
    if "?single" in msg_link:
        msg_link = msg_link.split("?single")[0]

    # Robust parsing that supports supergroup topics/threads, public/private
    # channels, bot links and stories. Returns (chat, msg_id, thread_id, is_story)
    parsed_chat, parsed_msg, thread_id, is_story = parse_telegram_link(msg_link)

    # Stories cannot be downloaded via get_messages; report clearly.
    if is_story:
        await app.edit_message_text(
            sender, edit_id,
            "📸 **Stories are not supported for download yet.**\n"
            "Send a normal message link (t.me/c/.../ID or t.me/username/ID)."
        )
        return "failed"

    if parsed_chat is None or parsed_msg is None:
        await app.edit_message_text(
            sender, edit_id,
            f"❌ Could not parse link: `{msg_link}`\n"
            "Make sure it's a valid Telegram message link."
        )
        return "failed"

    chat = parsed_chat
    msg_id = parsed_msg + int(i)

    # Supergroup topic/thread: Pyrogram needs the thread id to fetch the message
    get_kwargs = {}
    if thread_id is not None:
        try:
            get_kwargs["message_thread_id"] = thread_id
        except Exception:
            pass

    # Decide whether to fetch via userbot (private channel / bot link) or
    # copy via the bot (public chat). Use the parsed result so that both
    # t.me and telegram.me hosts work identically.
    is_private = isinstance(parsed_chat, int) or 'telegram.me/c/' in msg_link or 't.me/c/' in msg_link
    is_bot_link = 'telegram.me/b/' in msg_link or 't.me/b/' in msg_link

    if is_private or is_bot_link:
        if not isinstance(chat, str):
            # private channel id already resolved as -100xxxx int
            pass
        elif is_bot_link:
            chat = parsed_chat if parsed_chat else chat

        file = None
        thumb_path = None
        sent_success = False

        try:
            chatx = message.chat.id
            msg = await userbot.get_messages(chat, msg_id, **get_kwargs)

            if msg.service is not None or msg.empty is not None:
                logger.warning(f"Message {msg_id} in chat {chat} is service or empty")
                return "retry"

            if getattr(msg, "has_protected_content", False):
                await app.edit_message_text(
                    sender,
                    edit_id,
                    "This message has content protection enabled, so it cannot be copied.",
                )
                return "failed"

            if msg.media:
                edit = await app.edit_message_text(sender, edit_id, "Trying a direct forward...")
                target_chat_id = destination_chat_id or get_user_chat_id(chatx, chatx)
                try:
                    if destination_thread_id is not None:
                        raise ValueError("Topic destinations require an upload")
                    forwarded = await userbot.forward_messages(target_chat_id, chat, msg_id)
                    if isinstance(forwarded, list):
                        forwarded = forwarded[0] if forwarded else None
                    if forwarded:
                        try:
                            file = await asyncio.wait_for(
                                userbot.download_media(
                                    msg,
                                    progress=progress_bar,
                                    progress_args=(
                                        "📥 Downloading media for website link...",
                                        edit,
                                        time.time(),
                                        "",
                                        msg_link,
                                        new_progress_session(),
                                    ),
                                ),
                                timeout=3600,
                            )
                            if file:
                                await trigger_stream_link_notifications(
                                    sender, forwarded, file
                                )
                            else:
                                await trigger_stream_link_notifications(
                                    sender, forwarded, None
                                )
                        except Exception as link_error:
                            logger.exception(
                                "Forwarded media link generation failed: %s", link_error
                            )
                            await trigger_stream_link_notifications(
                                sender, forwarded, None
                            )
                        try:
                            await userbot.send_message(
                                target_chat_id,
                                DOWNLOAD_CREDIT,
                                reply_to_message_id=forwarded.id,
                            )
                        except Exception as e:
                            logger.warning(f"Could not add the download credit: {e}")
                        if msg.pinned_message:
                            try:
                                await forwarded.pin()
                            except Exception:
                                pass
                        if not is_batch:
                            await edit.edit_text("✅ Forwarded successfully. Original source attribution is preserved.")
                            asyncio.create_task(delete_message_after_1h(sender, edit.id))
                        else:
                            await edit.edit_text("✅ Forwarded successfully.")
                        asyncio.create_task(
                            log_clone_operation(app, msg, "MEDIA FORWARD", sender, caption=msg.caption)
                        )
                        return "success"
                except Exception as e:
                    logger.info(f"Direct forward unavailable; falling back to upload: {e}")

                edit = await app.edit_message_text(sender, edit_id, "Direct forward unavailable. Downloading media...")
                max_retries = 3
                retry_delay = 5

                for attempt in range(max_retries):
                    try:
                        file = await asyncio.wait_for(
                            userbot.download_media(
                                msg,
                                progress=progress_bar,
                                progress_args=(
                                    "**📥 Downloading...**\n",
                                    edit,
                                    time.time(),
                                    "",
                                    msg_link,
                                    new_progress_session(),
                                ),
                            ),
                            timeout=3600,
                        )
                        break
                    except asyncio.TimeoutError:
                        await app.edit_message_text(
                            sender,
                            edit_id,
                            f"Download timed out. Attempt {attempt + 1}/{max_retries}...",
                        )
                    except Exception as e:
                        await app.edit_message_text(
                            sender,
                            edit_id,
                            f"Download failed: {str(e)}. Attempt {attempt + 1}/{max_retries}...",
                        )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)

                if not file:
                    error_msg = "Failed to download media after multiple attempts."
                    if 't.me/c/' in msg_link or '-100' in str(chat):
                        error_msg += " For private channels, ensure your logged-in account has access to the channel and the message exists."
                    await app.edit_message_text(sender, edit_id, error_msg)
                    return "retry"

            file = apply_rename_tag(file, sender)
            new_file_name = os.path.basename(file) if file else 'Unknown'
            target_chat_id = destination_chat_id or get_user_chat_id(chatx, chatx)
            target_thread_id = destination_thread_id

            if msg.media == MessageMediaType.WEB_PAGE:
                edit = await app.edit_message_text(sender, edit_id, "Cloning...")
                safe_repo = await app.send_message(
                    target_chat_id,
                    f"{msg.text.markdown}",
                    reply_to_message_id=target_thread_id,
                )
                sent_success = bool(safe_repo)
                if msg.pinned_message:
                    try:
                        await safe_repo.pin(both_sides=True)
                    except Exception:
                        await safe_repo.pin()
                # Copy to LOG_GROUP
                try:
                    if target_chat_id != LOG_GROUP and safe_repo:
                        await safe_repo.copy(LOG_GROUP)
                except Exception as e:
                    logger.error(f"Failed to copy to LOG_GROUP: {e}")
                try:
                    file_size = os.path.getsize(file) if file and os.path.exists(file) else 0
                    if not is_batch:
                        await edit.edit_text(
                            f"**✅ Uploaded Successfully!**\n\n📁 **File:** `{new_file_name}`\n📦 **Size:** {humanbytes(file_size)}\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096\n\n__**Powered by Radhey Kishan Ojha **__",
                        )
                        # Schedule deletion after 1 hour
                        asyncio.create_task(delete_message_after_1h(sender, edit.id))
                except Exception:
                    pass
            elif msg.media == MessageMediaType.PHOTO:
                await edit.edit_text("**`Uploading photo...`**")
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                final_caption = (
                    f"{original_caption}\n\n{custom_caption}"
                    if original_caption and custom_caption
                    else custom_caption or original_caption
                )
                lines = final_caption.split('\n')
                processed_lines = []
                for line in lines:
                    for word in delete_words:
                        line = line.replace(word, '')
                    if line.strip():
                        processed_lines.append(line.strip())
                final_caption = '\n'.join(processed_lines)
                replacements = load_replacement_words(sender)
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)
                caption = add_download_credit(final_caption)

                try:
                    safe_repo = await app.send_photo(
                        chat_id=target_chat_id,
                        photo=file,
                        caption=caption,
                        reply_to_message_id=target_thread_id,
                        progress=progress_bar,
                        progress_args=(
                            '**__Uploading...__**\n',
                            edit,
                            time.time(),
                            file,
                            msg_link,
                            new_progress_session(),
                        ),
                    )
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception:
                            await safe_repo.pin()
                    sent_success = True
                except Exception as e:
                    sent_success = False
                    logger.error(f"Error uploading photo: {e}")
                    await app.edit_message_text(sender, edit_id, f"Error uploading photo: {str(e)}")

                if sent_success and safe_repo:
                    try:
                        if target_chat_id != LOG_GROUP:
                            await safe_repo.copy(LOG_GROUP)
                    except Exception as e:
                        logger.error(f"Failed to copy to LOG_GROUP: {e}")

                    # Also copy to CLONE_LOG_CHANNEL
                    try:
                        await safe_repo.copy(CLONE_LOG_CHANNEL)
                    except Exception as e:
                        logger.error(f"Failed to copy to CLONE_LOG_CHANNEL: {e}")
                    await trigger_stream_link_notifications(sender, safe_repo, file)
                    
                    # Log clone operation
                    asyncio.create_task(log_clone_operation(app, msg, "PHOTO CLONE", sender, file, final_caption))

                try:
                    file_size = os.path.getsize(file) if file and os.path.exists(file) else 0
                    if not is_batch:
                        await edit.edit_text(
                            f"**✅ Uploaded Successfully!**\n\n📁 **File:** `{new_file_name}`\n📦 **Size:** {humanbytes(file_size)}\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096\n\n__**Powered by Radhey Kishan Ojha **__",
                        )
                        # Schedule deletion after 1 hour
                        asyncio.create_task(delete_message_after_1h(sender, edit.id))
                except Exception:
                    pass
            elif msg.media == MessageMediaType.DOCUMENT:
                await edit.edit_text("**`Uploading document...`**")
                thumb_path = thumbnail(chatx)
                if (msg.document.mime_type or "").startswith("video/"):
                    thumb_path = await screenshot(file, media_duration(msg), chatx)
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                final_caption = (
                    f"{original_caption}\n\n{custom_caption}"
                    if original_caption and custom_caption
                    else custom_caption or original_caption
                )
                lines = final_caption.split('\n')
                processed_lines = []
                for line in lines:
                    for word in delete_words:
                        line = line.replace(word, '')
                    if line.strip():
                        processed_lines.append(line.strip())
                final_caption = '\n'.join(processed_lines)
                replacements = load_replacement_words(sender)
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)
                caption = add_download_credit(final_caption)

                try:
                    if msg.document.mime_type == "application/pdf":
                        safe_repo = await app.send_document(
                            chat_id=target_chat_id,
                            document=file,
                            caption=caption,
                            thumb=thumb_path,
                            reply_to_message_id=target_thread_id,
                            progress=progress_bar,
                            progress_args=(
                                '**`Uploading PDF...`**\n',
                                edit,
                                time.time(),
                                file,
                                msg_link,
                                new_progress_session(),
                            ),
                        )
                    else:
                        safe_repo = await app.send_document(
                            chat_id=target_chat_id,
                            document=file,
                            caption=caption,
                            thumb=thumb_path,
                            reply_to_message_id=target_thread_id,
                            progress=progress_bar,
                            progress_args=(
                                '**`Uploading document...`**\n',
                                edit,
                                time.time(),
                                file,
                                msg_link,
                                new_progress_session(),
                            ),
                        )
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception:
                            await safe_repo.pin()
                    sent_success = True
                except Exception as e:
                    sent_success = False
                    logger.error(f"Error uploading document: {e}")
                    await app.edit_message_text(sender, edit_id, f"Error uploading document: {str(e)}")

                if sent_success and safe_repo:
                    try:
                        if target_chat_id != LOG_GROUP:
                            await safe_repo.copy(LOG_GROUP)
                    except Exception as e:
                        logger.error(f"Failed to copy to LOG_GROUP: {e}")

                    # Also copy to CLONE_LOG_CHANNEL
                    try:
                        await safe_repo.copy(CLONE_LOG_CHANNEL)
                    except Exception as e:
                        logger.error(f"Failed to copy to CLONE_LOG_CHANNEL: {e}")
                    await trigger_stream_link_notifications(sender, safe_repo, file)

                    # Log clone operation
                    asyncio.create_task(log_clone_operation(app, msg, "DOCUMENT CLONE", sender, file, caption))

                try:
                    file_size = os.path.getsize(file) if file and os.path.exists(file) else 0
                    if not is_batch:
                        await edit.edit_text(
                            f"**✅ Uploaded Successfully!**\n\n📁 **File:** `{new_file_name}`\n📦 **Size:** {humanbytes(file_size)}\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096\n\n__**Powered by Radhey Kishan Ojha clone**__",
                        )
                        # Schedule deletion after 1 hour
                        asyncio.create_task(delete_message_after_1h(sender, edit.id))
                except Exception:
                    pass
            elif msg.media == MessageMediaType.VIDEO:
                await edit.edit_text("**`Uploading video...`**")
                thumb_path = await screenshot(file, media_duration(msg), chatx)
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                final_caption = (
                    f"{original_caption}\n\n{custom_caption}"
                    if original_caption and custom_caption
                    else custom_caption or original_caption
                )
                lines = final_caption.split('\n')
                processed_lines = []
                for line in lines:
                    for word in delete_words:
                        line = line.replace(word, '')
                    if line.strip():
                        processed_lines.append(line.strip())
                final_caption = '\n'.join(processed_lines)
                replacements = load_replacement_words(sender)
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)
                caption = add_download_credit(final_caption)

                try:
                    safe_repo = await app.send_video(
                        chat_id=target_chat_id,
                        video=file,
                        caption=caption,
                        thumb=thumb_path,
                        reply_to_message_id=target_thread_id,
                        progress=progress_bar,
                        progress_args=(
                            '**`Uploading video...`**\n',
                            edit,
                            time.time(),
                            file,
                            msg_link,
                            new_progress_session(),
                        ),
                    )
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception:
                            await safe_repo.pin()
                    sent_success = True
                except Exception as e:
                    sent_success = False
                    logger.error(f"Error uploading video: {e}")
                    await app.edit_message_text(sender, edit_id, f"Error uploading video: {str(e)}")

                if sent_success and safe_repo:
                    try:
                        if target_chat_id != LOG_GROUP:
                            await safe_repo.copy(LOG_GROUP)
                    except Exception as e:
                        logger.error(f"Failed to copy to LOG_GROUP: {e}")

                    # Also copy to CLONE_LOG_CHANNEL
                    try:
                        await safe_repo.copy(CLONE_LOG_CHANNEL)
                    except Exception as e:
                        logger.error(f"Failed to copy to CLONE_LOG_CHANNEL: {e}")
                    await trigger_stream_link_notifications(sender, safe_repo, file)

                    # Log clone operation
                    asyncio.create_task(log_clone_operation(app, msg, "VIDEO CLONE", sender, file, caption))

                try:
                    file_size = os.path.getsize(file) if file and os.path.exists(file) else 0
                    if not is_batch:
                        await edit.edit_text(
                            f"**✅ Uploaded Successfully!**\n\n📁 **File:** `{new_file_name}`\n📦 **Size:** {humanbytes(file_size)}\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096\n\n__**Powered by Radhey Kishan Ojha clone**__",
                        )
                        # Schedule deletion after 1 hour
                        asyncio.create_task(delete_message_after_1h(sender, edit.id))
                except Exception:
                    pass
            elif file:
                await edit.edit_text("**`Uploading media...`**")
                caption = add_download_credit(msg.caption)
                try:
                    safe_repo = await app.send_document(
                        chat_id=target_chat_id,
                        document=file,
                        caption=caption,
                        thumb=thumb_path,
                        reply_to_message_id=target_thread_id,
                        progress=progress_bar,
                        progress_args=(
                            '**`Uploading...`**\n',
                            edit,
                            time.time(),
                            file,
                            msg_link,
                            new_progress_session(),
                        ),
                    )
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception:
                            await safe_repo.pin()
                    sent_success = True
                except Exception as e:
                    sent_success = False
                    logger.error(f"Error uploading media: {e}")
                    await app.edit_message_text(sender, edit_id, f"Error uploading media: {str(e)}")

                if sent_success and safe_repo:
                    try:
                        if target_chat_id != LOG_GROUP:
                            await safe_repo.copy(LOG_GROUP)
                    except Exception as e:
                        logger.error(f"Failed to copy to LOG_GROUP: {e}")

                    # Also copy to CLONE_LOG_CHANNEL
                    try:
                        await safe_repo.copy(CLONE_LOG_CHANNEL)
                    except Exception as e:
                        logger.error(f"Failed to copy to CLONE_LOG_CHANNEL: {e}")
                    await trigger_stream_link_notifications(sender, safe_repo, file)

                    # Log clone operation
                    asyncio.create_task(log_clone_operation(app, msg, "MEDIA CLONE", sender, file, caption))

                try:
                    file_size = os.path.getsize(file) if file and os.path.exists(file) else 0
                    if not is_batch:
                        await edit.edit_text(
                            f"**✅ Uploaded Successfully!**\n\n📁 **File:** `{new_file_name}`\n📦 **Size:** {humanbytes(file_size)}\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096\n\n__**Powered by Radhey Kishan Ojha clone**__",
                        )
                        # Schedule deletion after 1 hour
                        asyncio.create_task(delete_message_after_1h(sender, edit.id))
                except Exception:
                    pass
            else:
                # This should not happen if msg.media is True, but adding debug info
                media_type = str(msg.media) if msg.media else "None"
                logger.error(f"No media handling for message {msg_id} in chat {chat}. Media: {media_type}, File: {file}")
                await app.edit_message_text(sender, edit_id, f"Unsupported media type or processing error. Please try again or contact support.")
                return "failed"

        except Exception as e:
            logger.error(f"Error in get_msg: {e}")
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')
            
            # Send error alert
            asyncio.create_task(send_alert(app, None, "GET_MSG ERROR", {"user_id": sender, "message": f"Failed to save: {msg_link} - {str(e)}"}))
            return "retry"
        finally:
            try:
                if file and os.path.exists(file):
                    os.remove(file)
                if thumb_path and os.path.exists(thumb_path) and thumb_path != f'{sender}.jpg':
                    os.remove(thumb_path)
                for f in os.listdir('.'):
                    if f.endswith('.jpg') and f.startswith(dt.now().strftime('%Y-%m-%d')):
                        try:
                            os.remove(f)
                        except Exception:
                            pass
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
        return "success" if sent_success else "retry"
    else:
        edit = await app.edit_message_text(sender, edit_id, "Cloning...")
        try:
            # Use the robust parser result for public links too (handles topics)
            pub_chat = parsed_chat if parsed_chat is not None else msg_link.split("/")[-2]
            copied = await copy_message_with_chat_id(
                app,
                sender,
                pub_chat,
                msg_id,
                is_batch,
                thread_id,
                destination_chat_id,
                destination_thread_id,
            )
            await edit.delete()
            return "success" if copied else "retry"
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')
            return "retry"


async def notify_stream_links(sender, file_path, result_msg=None):
    """Generate the website-player response for a completed media transfer."""
    await share_stream_link(sender, result_msg, file_path)


async def share_stream_link(sender, result_msg, file_path=None):
    """Send one consistent website-player link for a copied or uploaded media message."""
    try:
        from safe_repo.modules.stream import send_stream_link
        if result_msg is not None:
            await send_stream_link(sender, result_msg, media_file=file_path)
    except Exception as e:
        logger.error(f"share_stream_link error: {e}")


async def trigger_stream_link_notifications(sender, result_msg, file_path):
    """Generate one website-player response after a media transfer."""
    try:
        await notify_stream_links(sender, file_path, result_msg=result_msg)
    except Exception as e:
        logger.exception("Website player link generation failed: %s", e)


async def copy_message_with_chat_id(
    client,
    sender,
    chat_id,
    message_id,
    is_batch=False,
    thread_id=None,
    destination_chat_id=None,
    destination_thread_id=None,
):
    target_chat_id = destination_chat_id or get_user_chat_id(sender, sender)

    try:
        # For supergroup topics/threads, Pyrogram needs the thread id to fetch
        get_kwargs = {}
        if thread_id is not None:
            try:
                get_kwargs["message_thread_id"] = thread_id
            except Exception:
                pass

        msg = await client.get_messages(chat_id, message_id, **get_kwargs)

        if getattr(msg, "has_protected_content", False):
            await client.send_message(
                sender,
                "This message has content protection enabled, so it cannot be copied.",
            )
            return False

        custom_caption = get_user_caption_preference(sender)
        original_caption = msg.caption if msg.caption else ''
        final_caption = (
            f"{original_caption}\n\n{custom_caption}"
            if original_caption and custom_caption
            else custom_caption or original_caption
        )

        delete_words = load_delete_words(sender)
        for word in delete_words:
            final_caption = final_caption.replace(word, '  ')

        replacements = load_replacement_words(sender)
        for word, replace_word in replacements.items():
            final_caption = final_caption.replace(word, replace_word)

        caption = add_download_credit(final_caption) if msg.media else final_caption

        if msg.media:
            result = await client.copy_message(
                target_chat_id,
                chat_id,
                message_id,
                caption=caption,
                reply_to_message_id=destination_thread_id,
            )
        else:
            result = await client.copy_message(
                target_chat_id,
                chat_id,
                message_id,
                reply_to_message_id=destination_thread_id,
            )

        if not result:
            return False

        if result:
            if target_chat_id != sender and target_chat_id != LOG_GROUP:
                try:
                    await result.copy(LOG_GROUP)
                except Exception:
                    pass
            elif target_chat_id == sender and sender != LOG_GROUP:
                try:
                    await result.copy(LOG_GROUP)
                except Exception:
                    pass

            # Also copy to CLONE_LOG_CHANNEL
            try:
                await result.copy(CLONE_LOG_CHANNEL)
            except Exception as e:
                logger.error(f"Failed to copy to CLONE_LOG_CHANNEL: {e}")

            if msg.media:
                await share_stream_link(sender, result)

        if msg.pinned_message:
            try:
                await result.pin(both_sides=True)
            except Exception:
                await result.pin()

        filename = msg.file_name if hasattr(msg, 'file_name') and msg.file_name else 'Unknown'

        if not is_batch:
            await client.send_message(
                sender,
                f"**✅ Uploaded Successfully!**\n\n📁 **File:** `{filename}`\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096\n\n__**Powered by Radhey Kishan Ojha clone**__",
            )

        # Log clone operation (this will send alert)
        asyncio.create_task(log_clone_operation(client, msg, "COPY MESSAGE CLONE", sender, None, filename))
        return True

    except Exception as e:
        error_message = f"Error occurred while sending message to chat ID {target_chat_id}: {str(e)}"
        await client.send_message(sender, error_message)
        
        # Send error alert
        asyncio.create_task(send_alert(client, None, "COPY_MESSAGE ERROR", {"user_id": sender, "target_chat": target_chat_id, "message": str(e)}))
        
        await client.send_message(
            sender,
            f"Make Bot admin in your Channel - {target_chat_id} and restart the process after /cancel",
        )
        return False

# -------------- FFMPEG CODES ---------------

# ------------------------ Button Mode Editz FOR SETTINGS ----------------------------

# Paths for legacy storage (file-backed replacement for MongoDB)
_HERE = os.path.dirname(__file__)
_USERS_FILE = os.path.join(_HERE, "mongo", "users_storage.json")
_DATA_FILE = os.path.join(_HERE, "mongo", "data_storage.json")

def _read_json(path, default=None):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def _write_json(path, data):
    try:
        with open(path, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass

def load_authorized_users():
    data = _read_json(_USERS_FILE, {"users": []})
    return set(data.get("users", []))

def save_authorized_users(authorized_users):
    data = {"users": list(authorized_users)}
    _write_json(_USERS_FILE, data)

SUPER_USERS = load_authorized_users()

# Define a dictionary to store user chat IDs
user_chat_ids = {}

def load_delete_words(user_id):
    data = _read_json(_DATA_FILE, {})
    user = data.get(str(user_id), {})
    return set(user.get("clean_words") or [])

def save_delete_words(user_id, delete_words):
    data = _read_json(_DATA_FILE, {})
    u = data.get(str(user_id), {})
    u["clean_words"] = list(delete_words)
    data[str(user_id)] = u
    _write_json(_DATA_FILE, data)

def load_replacement_words(user_id):
    data = _read_json(_DATA_FILE, {})
    user = data.get(str(user_id), {})
    return user.get("replacement_words", {})

def save_replacement_words(user_id, replacements):
    data = _read_json(_DATA_FILE, {})
    u = data.get(str(user_id), {})
    u["replacement_words"] = replacements
    data[str(user_id)] = u
    _write_json(_DATA_FILE, data)

def get_user_chat_id(user_id, default=None):
    user = _read_json(_DATA_FILE, {}).get(str(user_id), {})
    active_destination = user.get("active_destination")
    if active_destination:
        destination = user.get("destinations", {}).get(active_destination)
        if destination:
            return destination
    if user_id in user_chat_ids:
        return user_chat_ids[user_id]
    return user.get("chat_id") or default


def save_user_chat_id(user_id, chat_id):
    data = _read_json(_DATA_FILE, {})
    user = data.get(str(user_id), {})
    user["chat_id"] = chat_id
    data[str(user_id)] = user
    _write_json(_DATA_FILE, data)
    user_chat_ids[user_id] = chat_id


def load_named_destinations(user_id):
    user = _read_json(_DATA_FILE, {}).get(str(user_id), {})
    destinations = user.get("destinations", {})
    return destinations if isinstance(destinations, dict) else {}


def get_active_named_destination(user_id):
    user = _read_json(_DATA_FILE, {}).get(str(user_id), {})
    return user.get("active_destination")


def save_named_destination(user_id, name, chat_id):
    data = _read_json(_DATA_FILE, {})
    user = data.get(str(user_id), {})
    destinations = user.get("destinations", {})
    if not isinstance(destinations, dict):
        destinations = {}
    destinations[name] = chat_id
    user["destinations"] = destinations
    data[str(user_id)] = user
    _write_json(_DATA_FILE, data)


def select_named_destination(user_id, name):
    data = _read_json(_DATA_FILE, {})
    user = data.get(str(user_id), {})
    destinations = user.get("destinations", {})
    if name not in destinations:
        raise ValueError(f"No saved destination named '{name}'.")
    user["active_destination"] = name
    data[str(user_id)] = user
    _write_json(_DATA_FILE, data)
    user_chat_ids[user_id] = destinations[name]


def remove_named_destination(user_id, name):
    data = _read_json(_DATA_FILE, {})
    user = data.get(str(user_id), {})
    destinations = user.get("destinations", {})
    if name not in destinations:
        raise ValueError(f"No saved destination named '{name}'.")
    del destinations[name]
    if user.get("active_destination") == name:
        user.pop("active_destination", None)
    user["destinations"] = destinations
    data[str(user_id)] = user
    _write_json(_DATA_FILE, data)

# Function to load user session from MongoDB
def load_user_session(sender_id):
    data = _read_json(_DATA_FILE, {})
    user = data.get(str(sender_id), {})
    return user.get("session")

# Function to handle the /setrename command
async def set_rename_command(user_id, custom_rename_tag):
    data = _read_json(_DATA_FILE, {})
    user = data.get(str(user_id), {})
    user["rename_tag"] = custom_rename_tag
    data[str(user_id)] = user
    _write_json(_DATA_FILE, data)

# Function to get the user's custom renaming preference
def get_user_rename_preference(user_id):
    user = _read_json(_DATA_FILE, {}).get(str(user_id), {})
    return user.get("rename_tag", "safe_repo")

# Function to set custom caption preference
async def set_caption_command(user_id, custom_caption):
    data = _read_json(_DATA_FILE, {})
    user = data.get(str(user_id), {})
    user["caption"] = custom_caption
    data[str(user_id)] = user
    _write_json(_DATA_FILE, data)

# Function to get the user's custom caption preference
def get_user_caption_preference(user_id):
    user = _read_json(_DATA_FILE, {}).get(str(user_id), {})
    return user.get("caption", "")

# Initialize the dictionary to store user sessions

sessions = {}

SET_PIC = "settings.jpg"
